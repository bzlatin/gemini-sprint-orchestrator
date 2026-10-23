from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import Button, DataTable, Input, Static, TabbedContent
from typer.testing import CliRunner

from gemini_orchestrator.cli import app
from gemini_orchestrator.config import Profiles
from gemini_orchestrator.dashboard import Confirmation, Dashboard
from gemini_orchestrator.demo import create_demo
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.models import OrchestratorError, State
from gemini_orchestrator.setup import configure_connected_resources, select_resource, wizard

runner = CliRunner()


def test_cli_help_and_first_run_offer(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--home", str(tmp_path), "--help"])
    assert result.exit_code == 0
    assert "setup" in result.output and "demo" in result.output
    result = runner.invoke(app, ["--home", str(tmp_path)], input="n\n")
    assert result.exit_code == 0
    assert "Start the interactive setup" in result.output


def test_cli_demo_and_profile_switch(profiles: Profiles) -> None:
    result = runner.invoke(app, ["--home", str(profiles.home), "demo", "--headless"])
    assert result.exit_code == 0, result.output
    assert "DEMO-6" in result.output
    switched = runner.invoke(app, ["--home", str(profiles.home), "profile", "switch", "demo"])
    assert switched.exit_code == 0
    status = runner.invoke(app, ["--home", str(profiles.home), "status"])
    assert status.exit_code == 0
    assert "NOT_SELECTED" in status.output


def test_onboarding_offline_identity_not_assumed(profiles: Profiles) -> None:
    demo = create_demo(profiles)
    answers = iter(
        [
            "Alex",
            str(demo.repository),
            "missing-gemini",
            "{bad}/{key}",
            "{key}-work",
            "later",
            "save",
        ]
    )
    with (
        patch("gemini_orchestrator.setup.Prompt.ask", side_effect=lambda *a, **kw: next(answers)),
        patch("gemini_orchestrator.setup.Confirm.ask", return_value=False),
        patch("gemini_orchestrator.setup.shutil.which", return_value=None),
    ):
        profile = wizard(profiles, "personal")
    assert profile.display_name == "Alex"
    assert profile.branch_template == "{key}-work"
    assert profile.worktrees == demo.repository.parent / "sample-repository-worktrees"
    assert profile.gemini.executable == "missing-gemini" and profile.gemini.plan_first
    assert profile.hosting == "local" and profile.email is None and not profile.checks
    assert profile.identity.account_id == ""
    assert not profile.remote_writes
    assert profiles.active() == "personal"
    with pytest.raises(StopIteration):
        next(answers)


def test_onboarding_accepts_suggested_checks_and_can_cancel(profiles: Profiles) -> None:
    demo = create_demo(profiles)
    (demo.repository / "pyproject.toml").write_text("[project]\nname = 'sample'\n")
    answers = iter(["Alex", str(demo.repository), "missing-gemini", "{key}", "later", "save"])
    with (
        patch("gemini_orchestrator.setup.Prompt.ask", side_effect=lambda *a, **kw: next(answers)),
        patch(
            "gemini_orchestrator.setup.Confirm.ask",
            side_effect=lambda prompt, **kw: prompt.startswith("Use these checks"),
        ),
        patch("gemini_orchestrator.setup.shutil.which", return_value=None),
    ):
        profile = wizard(profiles, "checks")
    # Only programs that exist are suggested; python is always available.
    assert [check.argv for check in profile.checks] == [["python", "-m", "pytest"]]
    cancel = iter(["Alex", str(demo.repository), "missing-gemini", "{key}", "later", "cancel"])
    with (
        patch("gemini_orchestrator.setup.Prompt.ask", side_effect=lambda *a, **kw: next(cancel)),
        patch("gemini_orchestrator.setup.Confirm.ask", return_value=False),
        patch("gemini_orchestrator.setup.shutil.which", return_value=None),
        pytest.raises(OrchestratorError, match="not saved"),
    ):
        wizard(profiles, "cancelled")
    assert "cancelled" not in profiles.names()


def test_manual_resource_selection_is_explicit() -> None:
    with patch(
        "gemini_orchestrator.setup.Prompt.ask", side_effect=["manual", "42", "Shared Board"]
    ):
        resource = select_resource("Board", [])
    assert resource.id == "42"
    assert resource.name == "Shared Board"


async def test_authenticated_identity_board_sprint_confirmation(engine: Engine) -> None:
    from gemini_orchestrator.models import Identity, Resource

    class Resources:
        async def identity(self):
            return Identity(account_id="immutable-account", display_name="Alex")

        async def projects(self):
            return [Resource(id="p1", name="Platform")]

        async def boards(self):
            return [Resource(id="b1", name="Platform"), Resource(id="b2", name="Shared")]

        async def sprints(self, board_id):
            assert board_id == "b2"
            return [Resource(id="s1", name="Active", board_id="b2")]

    answers = iter(["p1", "b2", "s1", "active"])
    with (
        patch("gemini_orchestrator.setup.Prompt.ask", side_effect=lambda *a, **kw: next(answers)),
        patch(
            "gemini_orchestrator.setup.Confirm.ask",
            side_effect=lambda prompt, **kw: prompt.startswith(("Is this", "Use ")),
        ),
    ):
        await configure_connected_resources(engine.profile, Resources())
    assert engine.profile.identity.account_id == "immutable-account"
    assert engine.profile.board_id == "b2"
    assert engine.profile.sprint.board_id == "b2"


@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
async def test_dashboard_selection_plan_and_approval_gates(
    engine: Engine, profiles: Profiles, size: tuple[int, int]
) -> None:
    await engine.discover()
    app = Dashboard(engine, profiles)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        table = app.query_one("#tickets", DataTable)
        assert table.row_count == 6
        app.query_one("#sort-priority", Button).press()
        await pilot.pause()
        assert table.get_row_at(0)[4].plain == "High"
        assert table.size.height >= 5  # at least three visible rows with header/scrollbar
        assert app.query_one("#details-scroll").can_focus
        await pilot.press("space")
        assert "DEMO-1" in app.selected
        assert table.get_row("DEMO-1")[0].plain == "[x]"
        assert "Selected 1 / 6" in str(app.query_one("#overview", Static).render())
        assert table.get_row("DEMO-3")[3].plain == "Waiting on DEMO-1"
        app.query_one("#branch-overrides", Input).value = "DEMO-1=feature/accessible-help"
        app.action_start()
        await pilot.pause()
        assert isinstance(app.screen, Confirmation)
        assert "feature/accessible-help" in app.screen.message
        # Going back records nothing: no selection, schedule or launch approval.
        await pilot.click("#back")
        await pilot.pause()
        assert engine.store.row("DEMO-1")["state"] == State.NOT_SELECTED
        assert not engine.store.approvals()
        assert not engine.supervisor.running
        await app.action_quit()


async def test_dashboard_search_and_full_execution(engine: Engine, profiles: Profiles) -> None:
    await engine.discover()
    app = Dashboard(engine, profiles)
    async with app.run_test(size=(140, 45)) as pilot:
        app.query_one("#search", Input).value = "diagnostic"
        await pilot.pause()
        assert app.query_one("#tickets", DataTable).row_count == 1
        app.query_one("#search", Input).value = ""
        app.selected = {"DEMO-1", "DEMO-2"}
        await pilot.click("#start")
        await pilot.pause()
        await pilot.click("#confirm")
        async with asyncio.timeout(20):
            while any(
                engine.store.row(key)["state"] != State.READY_FOR_REVIEW
                for key in ("DEMO-1", "DEMO-2")
            ):
                await pilot.pause(0.1)
        assert engine.store.row("DEMO-1")["branch"] == "feature/DEMO-1-add-accessible-command-help"
        app.query_one(TabbedContent).active = "review"
        app.call_after_refresh(app.query_one("#review-key", Input).focus)
        await pilot.pause()
        app.query_one("#review-key", Input).value = "DEMO-1"
        app.load_review()
        assert "passed" in str(app.query_one("#review-text", Static).render())
        app.query_one("#review-key", Input).value = "DEMO-2"
        await pilot.pause()
        await pilot.click("#accept")
        await pilot.pause()
        assert not isinstance(app.screen, Confirmation)
        assert "Load and inspect" in str(app.query_one("#notice", Static).render())
        await app.action_quit()


async def test_dashboard_plan_review_comment_and_approve(
    profiles: Profiles,
) -> None:
    profile = create_demo(profiles)
    assert profile.gemini.plan_first
    engine = Engine(profile, profiles.runtime(profile.name))
    try:
        await engine.discover()
        app = Dashboard(engine, profiles)
        async with app.run_test(size=(120, 40)) as pilot:
            app.selected = {"DEMO-1"}
            app.action_start()
            await pilot.pause()
            await pilot.click("#confirm")
            async with asyncio.timeout(20):
                while engine.store.row("DEMO-1")["state"] != State.PLAN_REVIEW:
                    await pilot.pause(0.1)
            assert not (Path(engine.store.row("DEMO-1")["worktree"]) / "src/help.py").exists()
            await pilot.pause(0.6)
            assert "DEMO-1" in str(app.query_one("#plan-text", Static).render())
            # Deciding without reading the plan is refused.
            app.query_one("#plan-key", Input).value = "DEMO-1"
            app.query_one("#approve-plan", Button).press()
            await pilot.pause()
            assert not isinstance(app.screen, Confirmation)
            app.load_plan("DEMO-1")
            assert "Implementation plan for DEMO-1" in str(
                app.query_one("#plan-text", Static).render()
            )
            app.query_one("#plan-comment").load_text("Mention keyboard shortcuts")
            app.query_one("#comment-plan", Button).press()
            await pilot.pause()
            await pilot.click("#confirm")
            async with asyncio.timeout(20):
                while engine.store.row("DEMO-1")["state"] != State.PLAN_REVIEW or (
                    "keyboard shortcuts" not in str(engine.implementation_plan("DEMO-1")["text"])
                ):
                    await pilot.pause(0.1)
            app.load_plan("DEMO-1")
            app.query_one("#approve-plan", Button).press()
            await pilot.pause()
            await pilot.click("#confirm")
            async with asyncio.timeout(20):
                while engine.store.row("DEMO-1")["state"] != State.READY_FOR_REVIEW:
                    await pilot.pause(0.1)
            assert (Path(engine.store.row("DEMO-1")["worktree"]) / "src/help.py").exists()
            await app.action_quit()
    finally:
        engine.close()


async def test_live_logs_refresh_without_a_lifecycle_change(
    engine: Engine, profiles: Profiles
) -> None:
    import asyncio
    import sys

    await engine.discover()
    original_run = engine.supervisor.run

    async def observed_worker(key, argv, *args, **kwargs):
        if key == "DEMO-1":
            script = "import time; print('phase one',flush=True); time.sleep(1); print('phase two',flush=True); time.sleep(2)"
            argv = [sys.executable, "-u", "-c", script]
        return await original_run(key, argv, *args, **kwargs)

    app = Dashboard(engine, profiles)
    with patch.object(engine.supervisor, "run", side_effect=observed_worker):
        async with app.run_test(size=(120, 40)) as pilot:
            app.selected = {"DEMO-1"}
            app.query_one("#start", Button).press()
            await pilot.pause()
            await pilot.click("#confirm")
            async with asyncio.timeout(10):
                while "phase two" not in str(app.query_one("#agent-text", Static).render()):
                    await pilot.pause(0.1)
            assert engine.store.row("DEMO-1")["state"] == State.IN_PROGRESS
            assert "elapsed" in str(app.query_one("#agent-text", Static).render())
            await app.action_quit()


def test_mcp_settings_rejects_blank_unknown_and_resets_identity(
    engine: Engine, tmp_path: Path
) -> None:
    from gemini_orchestrator.setup import configure_mcp_settings

    settings = tmp_path / "settings.json"
    settings.write_text('{"mcpServers": {"jira-server": {"command": "not-launched"}}}')
    with patch(
        "gemini_orchestrator.setup.Prompt.ask",
        side_effect=[str(settings), " ", "wrong", "jira-server", ""],
    ):
        assert configure_mcp_settings(engine.profile)
    assert engine.profile.jira.server == "jira-server"
    assert engine.profile.jira.settings_path == settings
    assert engine.profile.jira.cloud_id == ""
    assert engine.profile.identity.account_id == ""


def test_mcp_settings_defaults_to_the_single_jira_server(engine: Engine, tmp_path: Path) -> None:
    from gemini_orchestrator.setup import configure_mcp_settings

    settings = tmp_path / "settings.json"
    settings.write_text(
        '{"mcpServers": {"github": {"command": "a"}, "company-jira": {"command": "b"}}}'
    )
    defaults = []

    def ask(prompt, default=None, **kw):
        defaults.append(default)
        return {0: str(settings), 1: default, 2: ""}[len(defaults) - 1]

    with patch("gemini_orchestrator.setup.Prompt.ask", side_effect=ask):
        assert configure_mcp_settings(engine.profile)
    assert engine.profile.jira.server == "company-jira" and defaults[1] == "company-jira"


@pytest.mark.parametrize(
    "contents", ["{}", "[]", '{"mcpServers": []}', '{"mcpServers": {}}', "invalid JSON"]
)
def test_mcp_settings_unavailable_can_defer(engine: Engine, tmp_path: Path, contents: str) -> None:
    from gemini_orchestrator.setup import configure_mcp_settings

    settings = tmp_path / "settings.json"
    settings.write_text(contents)
    with patch("gemini_orchestrator.setup.Prompt.ask", side_effect=[str(settings), "later"]):
        assert not configure_mcp_settings(engine.profile)
    assert not engine.profile.identity.account_id


async def test_missing_discovery_defaults_to_manual_ids(engine: Engine) -> None:
    from gemini_orchestrator.models import Identity, OrchestratorError

    class ManualResources:
        async def identity(self):
            return Identity(account_id="server-user", display_name="Alex")

        async def projects(self):
            raise OrchestratorError("No project tool")

        async def boards(self):
            raise OrchestratorError("No board tool")

        async def sprints(self, board_id):
            raise OrchestratorError("No sprint tool")

    ids = iter(["", "100", "42", "101"])

    def answer(prompt, **kwargs):
        if prompt.startswith("Stable "):
            return next(ids)
        if prompt.endswith("display name"):
            return "Synthetic resource"
        assert kwargs["default"] == "manual"
        return kwargs["default"]

    with (
        patch("gemini_orchestrator.setup.Prompt.ask", side_effect=answer),
        patch(
            "gemini_orchestrator.setup.Confirm.ask",
            side_effect=lambda prompt, **kwargs: prompt.startswith("Is this"),
        ),
    ):
        await configure_connected_resources(engine.profile, ManualResources())
    assert engine.profile.identity.account_id == "server-user"
    assert engine.profile.projects[0].id == "100"
    assert engine.profile.board_id == "42"
    assert engine.profile.sprint.id == "101"
    assert engine.profile.sprint.board_id == "42"
    assert engine.profile.sprint_behavior == "manual"
