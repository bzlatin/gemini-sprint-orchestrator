from __future__ import annotations

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
from gemini_orchestrator.models import State
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
            "alex@example.invalid",
            str(demo.repository),
            str(demo.repository.parent / "new-worktrees"),
            "Platform",
            "missing-gemini",
            "Focused implementation",
            "later",
            "local",
            "save",
        ]
    )
    with (
        patch("gemini_orchestrator.setup.Prompt.ask", side_effect=lambda *a, **kw: next(answers)),
        patch(
            "gemini_orchestrator.setup.Confirm.ask",
            side_effect=lambda *a, **kw: a[0].startswith(("Use existing", "Save this")),
        ),
        patch("gemini_orchestrator.setup.typer.prompt", side_effect=[2, 120, 1]),
    ):
        profile = wizard(profiles, "personal")
    assert profile.display_name == "Alex"
    assert profile.identity.account_id == ""
    assert not profile.remote_writes
    assert profiles.active() == "personal"


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
        assert table.get_row_at(0)[3].plain == "High"
        assert table.size.height >= 5  # at least three visible rows with header/scrollbar
        assert app.query_one("#details-scroll").can_focus
        await pilot.press("space")
        assert "DEMO-1" in app.selected
        assert table.get_row("DEMO-1")[0].plain == "[x]"
        app.action_save_selection()
        await pilot.pause()
        assert isinstance(app.screen, Confirmation)
        await pilot.click("#confirm")
        await pilot.pause()
        assert engine.store.row("DEMO-1")["state"] == State.SELECTED
        app.action_plan()
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "planner", str(
            app.query_one("#notice", Static).render()
        )
        assert engine.store.row("DEMO-1")["state"] == State.AWAITING_PLAN_APPROVAL
        await pilot.click("#approve-plan")
        await pilot.pause()
        await pilot.click("#confirm")
        await pilot.pause()
        assert engine.store.row("DEMO-1")["state"] == State.QUEUED
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
        engine.select(["DEMO-1", "DEMO-2"], "Alex")
        app.action_plan()
        engine.approve_plan("Alex")
        await pilot.pause()
        await pilot.click("#launch")
        await pilot.pause()
        await pilot.click("#confirm")
        await pilot.pause(2)
        assert engine.store.row("DEMO-1")["state"] == State.READY_FOR_REVIEW
        assert engine.store.row("DEMO-2")["state"] == State.READY_FOR_REVIEW
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
