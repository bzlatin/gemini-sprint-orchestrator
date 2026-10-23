"""Exercise the actual Textual demo with scripted synthetic human interactions.

No enterprise access, model calls or remote writes. This is a developer validation
harness, not an unattended approval interface in the product.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from capture_utils import sanitize_svg
from textual.widgets import Button, DataTable, Input, TabbedContent

from gemini_orchestrator.config import Profiles
from gemini_orchestrator.dashboard import Confirmation, Dashboard
from gemini_orchestrator.demo import create_demo
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.models import State


async def walkthrough(directory: Path) -> dict[str, object]:
    profiles = Profiles(directory / "local-data")
    profile = create_demo(profiles, "walkthrough")
    profile.checks[0].argv[0] = "python3"
    profiles.save(profile)
    engine = Engine(profile, profiles.runtime(profile.name))
    captures = directory / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {"mode": "synthetic demo", "steps": []}
    steps: list[str] = []
    app = Dashboard(engine, profiles)
    await engine.discover()
    async with app.run_test(size=(120, 40)) as pilot:

        async def press(identifier: str) -> None:
            app.query_one(f"#{identifier}", Button).press()
            await pilot.pause()

        async def confirm() -> None:
            assert isinstance(app.screen, Confirmation)
            await pilot.click("#confirm")
            await pilot.pause()

        async def tab(identifier: str, focus: str) -> None:
            app.query_one(TabbedContent).active = identifier
            app.call_after_refresh(app.query_one(f"#{focus}").focus)
            await pilot.pause()

        async def wait_state(key: str, expected: State) -> None:
            async with asyncio.timeout(20):
                while engine.store.row(key)["state"] != expected:
                    row = engine.store.row(key)
                    if row["state"] in {State.FAILED, State.CANCELLED}:
                        raise AssertionError(engine.store.events(20))
                    await pilot.pause(0.1)

        def capture(name: str) -> None:
            app.save_screenshot(name + ".svg", path=str(captures))

        async def load_plan(key: str) -> None:
            await tab("planner", "plan-key")
            app.query_one("#plan-key", Input).value = key
            await press("load-plan")

        async def approve_plan(key: str) -> None:
            await wait_state(key, State.PLAN_REVIEW)
            await load_plan(key)
            await press("approve-plan")
            await confirm()

        async def load_review(key: str) -> None:
            await tab("review", "review-key")
            app.query_one("#review-key", Input).value = key
            await press("load-review")

        async def start(keys: set[str]) -> None:
            await tab("explorer", "tickets")
            app.selected = set(keys)
            app.render_tickets()
            await press("start")
            await confirm()

        # Select through the explorer keyboard, then confirm branches and readiness once.
        table = app.query_one("#tickets", DataTable)
        table.focus()
        for index in range(6):
            if index in {0, 1, 2, 5}:
                await pilot.press("space")
            if index < 5:
                await pilot.press("down")
        assert app.selected == {"DEMO-1", "DEMO-2", "DEMO-3", "DEMO-6"}
        capture("01-selection")
        await press("start")
        assert isinstance(app.screen, Confirmation)
        await confirm()
        steps.append("Selected four tickets; one confirmation approved readiness and branch names")

        await wait_state("DEMO-1", State.PLAN_REVIEW)
        await wait_state("DEMO-2", State.PLAN_REVIEW)
        assert engine.store.row("DEMO-3")["state"] == State.BLOCKED
        assert engine.store.row("DEMO-6")["state"] == State.BLOCKED
        assert not (Path(engine.store.row("DEMO-1")["worktree"]) / "src/help.py").exists()
        await load_plan("DEMO-1")
        capture("02-plan")
        app.query_one("#plan-comment").load_text("Mention keyboard shortcuts (synthetic comment)")
        await press("comment-plan")
        await confirm()
        async with asyncio.timeout(20):
            while "keyboard shortcuts" not in str(engine.implementation_plan("DEMO-1")["text"]):
                await pilot.pause(0.1)
        steps.append(
            "Gemini planned ready tickets read-only; a human comment produced a revised plan"
        )

        await approve_plan("DEMO-1")
        await approve_plan("DEMO-2")
        await wait_state("DEMO-1", State.READY_FOR_REVIEW)
        await wait_state("DEMO-2", State.READY_FOR_REVIEW)
        summary["blocked_after_first_batch"] = {
            key: engine.store.row(key)["state"] for key in ("DEMO-3", "DEMO-6")
        }
        await tab("agents", "agent-key")
        capture("03-workers-and-blockers")
        steps.append(
            "Approved plans were implemented in real worktrees; downstream and external blockers held"
        )

        for key in ("DEMO-1", "DEMO-2"):
            await load_review(key)
            assert engine.review(key)["verification"]["status"] == "passed"
            if key == "DEMO-1":
                capture("04-review")
            await press("accept")
            await confirm()
            assert engine.store.row(key)["state"] == State.APPROVED
        steps.append("Inspected actual review evidence and accepted both prerequisites")

        await start({"DEMO-3"})
        await approve_plan("DEMO-3")
        await wait_state("DEMO-3", State.READY_FOR_REVIEW)
        await load_review("DEMO-3")
        await press("accept")
        await confirm()
        steps.append(
            "Dependent work was planned and implemented only after prerequisite acceptance"
        )

        # Exercise feedback and continuation in the same worktree.
        original_worktree = engine.store.row("DEMO-2")["worktree"]
        await load_review("DEMO-2")
        app.query_one(
            "#feedback", Input
        ).value = "Include a clearer installation example (synthetic feedback)"
        await press("request-changes")
        await confirm()
        await start({"DEMO-2"})
        await wait_state("DEMO-2", State.READY_FOR_REVIEW)
        assert engine.store.row("DEMO-2")["worktree"] == original_worktree
        await load_review("DEMO-2")
        await press("accept")
        await confirm()
        capture("05-accepted-continuation")
        steps.append(
            "Requested changes; the approved plan was continued in the original worktree and accepted"
        )
        await app.action_quit()

    final = {row["key"]: row["state"] for row in engine.store.rows()}
    approvals = engine.store.db.execute(
        "SELECT count(*) FROM approvals WHERE granted=1"
    ).fetchone()[0]
    engine.close()
    profile = profiles.load(profile.name)
    reopened = Engine(profile, profiles.runtime(profile.name))
    try:
        assert {row["key"]: row["state"] for row in reopened.store.rows()} == final
        assert "synthetic feedback" in reopened.store.row("DEMO-2")["feedback"]
        steps.append(
            "Closed and reopened persisted SQLite state; feedback and accepted implementations survived"
        )
    finally:
        reopened.close()
    summary.update(
        {
            "steps": steps,
            "final_states": final,
            "recorded_approval_grants": approvals,
            "captures": sorted(path.name for path in captures.glob("*.svg")),
        }
    )
    for path in captures.glob("*.svg"):
        sanitize_svg(path)
    (directory / "demo-report.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New disposable synthetic-data directory"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new directory so previous evidence is preserved")
    args.output.mkdir(parents=True, mode=0o700)
    print(json.dumps(asyncio.run(walkthrough(args.output)), indent=2))
