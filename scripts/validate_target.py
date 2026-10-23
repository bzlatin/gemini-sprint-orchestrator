"""Opt-in target-machine smoke test using synthetic tickets and a disposable repository.

Real mode contacts Gemini only. It never connects to Jira or hosting and never uses
an enterprise checkout. --synthetic validates this harness without a model account.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from rich.prompt import Confirm

from gemini_orchestrator.config import Profiles, atomic_write
from gemini_orchestrator.dashboard import Dashboard
from gemini_orchestrator.demo import create_demo, demo_tickets
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.isolation import scratch
from gemini_orchestrator.models import OrchestratorError, State, digest


def confirm(message: str, synthetic: bool) -> None:
    if not synthetic and not Confirm.ask(message, default=False):
        raise OrchestratorError("Target validation declined")


async def validate(output: Path, template: str | None, synthetic: bool) -> dict:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=False)
    profiles = Profiles(output / "local-data")
    profile = create_demo(profiles, "target-validation")
    if not synthetic:
        configured = Profiles().load(template)
        profile.mode = "connected"
        profile.gemini = configured.gemini.model_copy(deep=True)
        profile.isolation = configured.isolation.model_copy(deep=True)
        profile.gemini.retries = 0
    profiles.save(profile)
    engine = Engine(profile, profiles.runtime(profile.name))
    try:
        engine.store.upsert(demo_tickets())
        engine.select(["DEMO-1"], "Target validation operator")
        plan = engine.propose()
        print(plan.model_dump_json(indent=2))
        confirm("Approve this synthetic ticket plan?", synthetic)
        engine.approve_plan("Target validation operator")
        if not synthetic:
            home = scratch(engine.runtime, "DEMO-1")
            print(
                f"Isolated Gemini HOME: {home}. Use an approved model API key in the environment, or authenticate Gemini in this dedicated home before continuing. Do not copy your normal settings/extensions."
            )
        confirm(
            "Approve one read-only Gemini planning turn in this disposable repository?",
            synthetic,
        )
        engine.approve_launch("Target validation operator")
        await engine.execute(connected_enabled=not synthetic)
        proposal = engine.implementation_plan("DEMO-1")
        if engine.store.row("DEMO-1")["state"] != State.PLAN_REVIEW or proposal is None:
            raise OrchestratorError("Read-only planning failed; inspect private runtime logs")
        print(proposal["text"])
        confirm(
            "Approve this implementation plan: one Gemini coding turn and isolated verification?",
            synthetic,
        )
        engine.decide_plan(
            "DEMO-1", "approve", "Target validation operator", str(proposal["fingerprint"])
        )
        await engine.execute(connected_enabled=not synthetic)
        row = engine.store.row("DEMO-1")
        if (
            row["state"] != State.READY_FOR_REVIEW
            or json.loads(row["verification"] or "{}").get("status") != "passed"
        ):
            raise OrchestratorError(
                "Initial target worker/verification failed; inspect private runtime logs"
            )
        native_id = engine.worker_session_id("DEMO-1")
        if not synthetic and not native_id:
            raise OrchestratorError(
                "CLI returned no native session UUID; target resume validation is incomplete"
            )
        message = "Add a short explanatory comment to the command help implementation. Keep the acceptance criteria and code compilable."
        preview = engine.session_preview("DEMO-1", message)
        print(json.dumps(preview, indent=2))
        confirm("Approve this follow-up in the exact same native session and worktree?", synthetic)
        engine.approve_session_turn(
            "DEMO-1", message, "Target validation operator", digest(preview)
        )
        await engine.launch_session_turn("DEMO-1", message, connected_enabled=not synthetic)
        after = engine.store.row("DEMO-1")
        assert after["worktree"] == row["worktree"] and after["branch"] == row["branch"]
        assert after["state"] == State.READY_FOR_REVIEW
        assert json.loads(after["verification"])["status"] == "passed"
        assert engine.worker_session_id("DEMO-1") == native_id
        question = "Explain the implementation without changing any files."
        preview = engine.session_preview("DEMO-1", question)
        confirm("Approve one read-only model question with OS write denial?", synthetic)
        engine.store.grant(
            engine.store.request("ask", "DEMO-1", digest(preview)), "Target validation operator"
        )
        await engine.ask("DEMO-1", question, connected_enabled=not synthetic)
        report = {
            "mode": "synthetic harness" if synthetic else "real Gemini / synthetic task",
            "read_only_planning_and_approval": "passed",
            "initial_worker_and_checks": "passed",
            "same_branch_and_worktree": True,
            "native_uuid_resume": "synthetic mode; not tested" if synthetic else "passed",
            "read_only_question": "passed",
            "jira_or_hosting_contacted": False,
            "vscode_gui": "manual validation required",
        }
        atomic_write(output / "report.json", json.dumps(report, indent=2))
        return report
    finally:
        await engine.shutdown()
        engine.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New private directory outside your source checkout",
    )
    parser.add_argument(
        "--profile", help="Approved local profile supplies Gemini/tooling settings only"
    )
    parser.add_argument("--enable-connected", action="store_true")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument(
        "--editor",
        action="store_true",
        help="After checks, open dashboard for manual Open VS Code validation",
    )
    args = parser.parse_args()
    if not args.synthetic and not args.enable_connected:
        parser.error("Real model validation requires --enable-connected on an approved machine")
    report = asyncio.run(validate(args.output.resolve(), args.profile, args.synthetic))
    print(json.dumps(report, indent=2))
    if args.editor:
        profiles = Profiles(args.output.resolve() / "local-data")
        profile = profiles.load("target-validation")
        engine = Engine(profile, profiles.runtime(profile.name))
        try:
            print(
                "Agents → DEMO-1 → Open VS Code. Verify the branch, shared messages, task permissions, and reconnect after dashboard restart. Closing only the editor must leave the dashboard running."
            )
            Dashboard(engine, profiles, connected_enabled=not args.synthetic).run()
        finally:
            engine.close()


if __name__ == "__main__":
    main()
