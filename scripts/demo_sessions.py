"""Demonstrate shared conversations using synthetic workers and real Textual controls."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from capture_utils import sanitize_svg
from textual.widgets import Button, Input, Static, TabbedContent, TextArea

from gemini_orchestrator.config import Profiles
from gemini_orchestrator.dashboard import Confirmation, Dashboard
from gemini_orchestrator.demo import create_demo
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.models import State
from gemini_orchestrator.session_ui import SessionConsole
from gemini_orchestrator.sessions import open_vscode


async def demo(output: Path) -> None:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=False)
    profiles = Profiles(output / "local-data")
    profile = create_demo(profiles)
    profile.checks[0].argv[0] = "python3"
    profiles.save(profile)
    engine = Engine(profile, profiles.runtime(profile.name))
    captures = output / "captures"
    captures.mkdir()
    report: dict[str, object] = {
        "mode": "synthetic shared-session walkthrough",
        "steps": [],
        "vscode_application_launched": False,
    }
    try:
        await engine.discover()
        engine.select(["DEMO-1", "DEMO-2"], "Synthetic operator")
        engine.propose()
        engine.approve_plan("Synthetic operator")
        engine.approve_launch("Synthetic operator")
        await engine.execute()
        # Plan first: approve each read-only plan, then the approved work is implemented.
        for key in ("DEMO-1", "DEMO-2"):
            proposal = engine.implementation_plan(key)
            assert proposal is not None, engine.store.events(10)
            engine.decide_plan(key, "approve", "Synthetic operator", str(proposal["fingerprint"]))
        await engine.execute()
        original_path = engine.store.row("DEMO-1")["worktree"]
        original_branch = engine.store.row("DEMO-1")["branch"]
        app = Dashboard(engine, profiles)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one(TabbedContent).active = "agents"
            await pilot.pause()
            app.query_one("#agent-key", Input).value = "DEMO-1"
            app.query_one("#agent-session", Button).press()
            await pilot.pause(0.5)
            app.query_one("#session-message", TextArea).load_text(
                "Include a clearer command example and explain the change."
            )
            app.save_screenshot("dashboard-session-120.svg", path=str(captures))
            app.query_one("#session-send", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, Confirmation)
            app.save_screenshot("session-confirmation-120.svg", path=str(captures))
            app.screen.query_one("#back", Button).press()
            await pilot.pause()
            assert not engine.executing
            assert app.query_one("#session-message", TextArea).text
            app.query_one("#session-send", Button).press()
            await pilot.pause()
            app.screen.query_one("#confirm", Button).press()
            await pilot.pause()
            async with asyncio.timeout(15):
                while engine.executing:
                    await pilot.pause(0.1)
            assert engine.store.row("DEMO-1")["state"] == State.READY_FOR_REVIEW
            app.update_views(force=True)
            assert "clearer command example" in str(
                app.query_one("#session-history", Static).render()
            )
            calls = []
            with (
                patch("gemini_orchestrator.sessions.shutil.which", return_value="/synthetic/code"),
                patch(
                    "gemini_orchestrator.sessions.command",
                    side_effect=lambda argv, **kw: calls.append(argv) or "",
                ),
            ):
                workspace = await open_vscode(engine, "DEMO-1", app.bridge)
            document = json.loads(workspace.read_text())
            assert document["folders"][0]["path"] == original_path
            assert document["tasks"]["tasks"][0]["options"]["cwd"] == original_path
            assert original_branch in document["folders"][0]["name"]
            report["editor_workspace"] = {
                "branch_matches": True,
                "worktree_matches": True,
                "process_task_uses_same_coordinator": True,
                "launch_argument_count": len(calls[0]),
            }
            # Exercise the exact terminal app launched by the generated VS Code task.
            for size in [(120, 40), (80, 24)]:
                remote = SessionConsole(app.bridge.descriptor, "DEMO-1")
                async with remote.run_test(size=size) as client:
                    async with asyncio.timeout(10):
                        while remote.query_one("#session-send", Button).disabled:
                            await client.pause(0.1)
                    if size[0] == 120:
                        remote.query_one("#session-message", TextArea).load_text(
                            "Add one more example from the editor conversation."
                        )
                        remote.query_one("#session-send", Button).press()
                        async with asyncio.timeout(10):
                            while not isinstance(remote.screen, Confirmation):
                                await client.pause(0.1)
                        remote.screen.query_one("#confirm", Button).press()
                        await client.pause()
                        async with asyncio.timeout(15):
                            while engine.executing:
                                await client.pause(0.1)
                        remote.refresh_session()
                        await client.pause(0.5)
                    remote.save_screenshot(f"editor-session-{size[0]}.svg", path=str(captures))
                    assert remote.query_one("#session-send", Button).region.bottom <= size[1] - 1
            app.update_views(force=True)
            assert "editor conversation" in str(app.query_one("#session-history", Static).render())
            await app.action_quit()
        compact = Dashboard(engine, profiles)
        async with compact.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            compact.load_session("DEMO-1")
            await pilot.pause(0.5)
            compact.query_one("#load-session", Button).focus()
            await pilot.pause()
            compact.save_screenshot("dashboard-session-80.svg", path=str(captures))
            assert compact.query_one("#session-send", Button).region.bottom <= 23
            await compact.action_quit()
        assert engine.store.row("DEMO-1")["worktree"] == original_path
        assert engine.store.row("DEMO-1")["branch"] == original_branch
        report["steps"] = [
            "Launched two synthetic workers with separate plan and launch approvals",
            "Declined a dashboard message and preserved its draft",
            "Confirmed a dashboard follow-up; reused the worktree and reran verification",
            "Validated the generated VS Code workspace and shared-terminal process task; editor executable mocked",
            "Sent and confirmed a follow-up through the actual editor terminal app",
            "Observed the editor message and reply in the main dashboard",
            "Confirmed compact controls remain visible in both views",
        ]
        report["conversation_roles"] = [item["role"] for item in engine.conversation("DEMO-1")]
        report["final_state"] = engine.store.row("DEMO-1")["state"]
        report["same_branch_and_worktree"] = True
        report["verification"] = json.loads(engine.store.row("DEMO-1")["verification"])["status"]
        report["captures"] = sorted(path.name for path in captures.glob("*.svg"))
        for path in captures.glob("*.svg"):
            sanitize_svg(path)
    finally:
        await engine.shutdown()
        engine.close()
    reopened = Engine(profiles.load(profile.name), profiles.runtime(profile.name))
    try:
        # Plan request, plan and approval, then the implementation turn and two follow-ups.
        assert len(reopened.conversation("DEMO-1")) == 9
        report["conversation_survived_restart"] = True
    finally:
        reopened.close()
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(demo(parser.parse_args().output.resolve()))
