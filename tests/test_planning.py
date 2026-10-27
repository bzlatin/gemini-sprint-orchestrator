from __future__ import annotations

import asyncio
import json
import platform
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gemini_orchestrator.cli import app
from gemini_orchestrator.config import Profiles
from gemini_orchestrator.demo import create_demo, demo_tickets
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.git import Git
from gemini_orchestrator.isolation import scratch
from gemini_orchestrator.models import OrchestratorError, Profile, State, Ticket
from gemini_orchestrator.planning import branch_name, mentioned_tickets

runner = CliRunner()


@pytest.fixture
def planner(profiles: Profiles) -> Iterator[Engine]:
    profile = create_demo(profiles)
    assert profile.gemini.plan_first
    engine = Engine(profile, profiles.runtime(profile.name))
    yield engine
    engine.close()


async def start(engine: Engine, keys: list[str], **branches: str) -> None:
    await engine.discover()
    engine.start(keys, "Alex", engine.preview(keys, branches=branches or None))
    await engine.execute()


def plan_text(engine: Engine, key: str) -> str:
    plan = engine.implementation_plan(key)
    assert plan is not None
    return str(plan["text"])


async def test_plan_first_waits_for_human_and_never_edits(planner: Engine) -> None:
    await start(planner, ["DEMO-1"])
    row = planner.store.row("DEMO-1")
    assert row["state"] == State.PLAN_REVIEW
    assert "Implementation plan for DEMO-1" in plan_text(planner, "DEMO-1")
    assert Git(Path(row["worktree"])).clean()
    pending = [a for a in planner.store.approvals() if a["action"] == "implementation-plan"]
    assert len(pending) == 1 and not pending[0]["granted"]
    # Nothing implements until a human approves this exact plan.
    assert planner.approved_implementation("DEMO-1") is None
    with pytest.raises(OrchestratorError, match="launch"):
        await planner.execute()


async def test_approve_implements_the_approved_plan(planner: Engine) -> None:
    await start(planner, ["DEMO-1"])
    fingerprint = str(planner.implementation_plan("DEMO-1")["fingerprint"])
    with pytest.raises(OrchestratorError, match="changed"):
        planner.decide_plan("DEMO-1", "approve", "Alex", "stale")
    with pytest.raises(OrchestratorError, match="human"):
        planner.decide_plan("DEMO-1", "approve", "gemini", fingerprint)
    assert planner.decide_plan("DEMO-1", "approve", "Alex", fingerprint)
    assert planner.store.row("DEMO-1")["state"] == State.QUEUED
    with pytest.raises(OrchestratorError, match="no implementation plan awaiting"):
        planner.decide_plan("DEMO-1", "approve", "Alex", fingerprint)
    await planner.execute()
    row = planner.store.row("DEMO-1")
    assert row["state"] == State.READY_FOR_REVIEW
    assert (Path(row["worktree"]) / "src/help.py").exists()
    assert planner.approved_implementation("DEMO-1") == plan_text(planner, "DEMO-1")
    texts = [message["text"] for message in planner.conversation("DEMO-1")]
    assert any("Approved the plan" in text for text in texts)
    assert any("Implement the approved plan" in text for text in texts)


async def test_comment_revises_and_reject_preserves_worktree(planner: Engine) -> None:
    await start(planner, ["DEMO-1"])
    first = planner.implementation_plan("DEMO-1")
    with pytest.raises(OrchestratorError, match="comment"):
        planner.decide_plan("DEMO-1", "comment", "Alex", str(first["fingerprint"]), "  ")
    assert planner.decide_plan(
        "DEMO-1", "comment", "Alex", str(first["fingerprint"]), "Cover screen readers"
    )
    await planner.execute()
    revised = planner.implementation_plan("DEMO-1")
    assert planner.store.row("DEMO-1")["state"] == State.PLAN_REVIEW
    assert revised["id"] != first["id"] and "Cover screen readers" in str(revised["text"])
    assert not planner.decide_plan("DEMO-1", "reject", "Alex", str(revised["fingerprint"]))
    row = planner.store.row("DEMO-1")
    assert row["state"] == State.CANCELLED
    assert await asyncio.to_thread(Path(row["worktree"]).is_dir)
    assert planner.approved_implementation("DEMO-1") is None


async def test_worktree_change_after_planning_blocks_approval(planner: Engine) -> None:
    await start(planner, ["DEMO-1"])
    (Path(planner.store.row("DEMO-1")["worktree"]) / "unreviewed.txt").write_text("edit")
    fingerprint = str(planner.implementation_plan("DEMO-1")["fingerprint"])
    with pytest.raises(OrchestratorError, match="worktree changed"):
        planner.decide_plan("DEMO-1", "approve", "Alex", fingerprint)
    assert planner.store.row("DEMO-1")["state"] == State.PLAN_REVIEW


async def test_planning_interruption_is_recovered(planner: Engine, profiles: Profiles) -> None:
    await planner.discover()
    planner.start(["DEMO-1"], "Alex", planner.preview(["DEMO-1"]))
    planner.store.transition("DEMO-1", State.PLANNING)
    planner.close()
    reopened = Engine(planner.profile, profiles.runtime(planner.profile.name))
    try:
        assert reopened.store.row("DEMO-1")["state"] == State.INTERRUPTED
    finally:
        reopened.close()
        planner.lock = (reopened.runtime / "session.lock").open("a+")
        planner.store = type(reopened.store)(reopened.runtime / "state.db")


async def test_approval_while_running_is_scheduled(planner: Engine) -> None:
    await start(planner, ["DEMO-1", "DEMO-2"])
    plans = {
        key: str(planner.implementation_plan(key)["fingerprint"]) for key in ("DEMO-1", "DEMO-2")
    }
    assert planner.decide_plan("DEMO-1", "approve", "Alex", plans["DEMO-1"])
    execution = asyncio.create_task(planner.execute())
    for _ in range(200):
        if planner.store.row("DEMO-1")["state"] == State.IN_PROGRESS:
            break
        await asyncio.sleep(0.05)
    # The running scheduler picks up a decision without another launch approval.
    assert not planner.decide_plan("DEMO-2", "approve", "Alex", plans["DEMO-2"])
    await asyncio.wait_for(execution, 20)
    assert planner.store.row("DEMO-1")["state"] == State.READY_FOR_REVIEW
    assert planner.store.row("DEMO-2")["state"] == State.READY_FOR_REVIEW


async def test_dependent_plans_after_prerequisite_acceptance(planner: Engine) -> None:
    await start(planner, ["DEMO-1", "DEMO-3"])
    assert planner.store.row("DEMO-3")["state"] == State.BLOCKED
    assert planner.readiness("DEMO-3") == "Waiting on DEMO-1"
    plan = planner.implementation_plan("DEMO-1")
    assert planner.decide_plan("DEMO-1", "approve", "Alex", str(plan["fingerprint"]))
    await planner.execute()
    planner.accept("DEMO-1", "Alex")
    assert planner.readiness("DEMO-3") == "Ready"
    preview = planner.preview(["DEMO-3"])
    assert "DEMO-3" not in preview.blocked
    planner.start(["DEMO-3"], "Alex", preview)
    await planner.execute()
    assert planner.store.row("DEMO-3")["state"] == State.PLAN_REVIEW


def test_branch_template_rendering_and_validation(engine: Engine) -> None:
    bug = Ticket(
        key="APP-7",
        summary="Fix: crash on  empty input!",
        assignee="a",
        sprint_id="1",
        issue_type="Bug",
    )
    assert (
        branch_name("{type}/{key}-{slug}", bug, "abcdef99") == "fix/APP-7-fix-crash-on-empty-input"
    )
    assert branch_name("users/alex/{key}-{id}", bug, "abcdef99") == "users/alex/APP-7-abcdef"
    for template in ("{slug}", "{key}-{unknown}", "{key"):
        with pytest.raises(ValueError):
            Profile.model_validate({**engine.profile.model_dump(), "branch_template": template})


async def test_branch_names_are_approved_and_checked(engine: Engine) -> None:
    await engine.discover()
    preview = engine.preview(["DEMO-1", "DEMO-2"], branches={"DEMO-1": "feature/help"})
    assert preview.branches == {
        "DEMO-1": "feature/help",
        "DEMO-2": "feature/DEMO-2-document-local-installation",
    }
    for bad, message in [
        ("main", "protected"),
        ("bad..name", "not valid"),
        ("feature/help", "distinct"),
    ]:
        with pytest.raises(OrchestratorError, match=message):
            engine.preview(["DEMO-1", "DEMO-2"], branches={"DEMO-1": "feature/help", "DEMO-2": bad})
    Git(engine.profile.repository).run("branch", "feature/taken")
    with pytest.raises(OrchestratorError, match="already exists"):
        engine.preview(["DEMO-1"], branches={"DEMO-1": "feature/taken"})
    engine.start(["DEMO-1"], "Alex", preview := engine.preview(["DEMO-1"]))
    assert engine.store.plan().branches == preview.branches
    # A preview that no longer matches what a human approved is refused.
    stale = engine.preview(["DEMO-2"])
    stale.groups = []
    with pytest.raises(OrchestratorError, match="review it again"):
        engine.start(["DEMO-2"], "Alex", stale)


def test_cli_work_plan_and_decisions(profiles: Profiles) -> None:
    home = ["--home", str(profiles.home), "--profile", "demo"]
    assert runner.invoke(app, [*home, "demo", "--headless"]).exit_code == 0
    tickets = runner.invoke(app, [*home, "tickets"])
    assert "Waiting on DEMO-1" in tickets.output
    declined = runner.invoke(app, [*home, "work", "DEMO-1"], input="n\n")
    assert declined.exit_code == 1 and "declined" in str(declined.exception)
    started = runner.invoke(
        app, [*home, "work", "demo-1", "--branch", "DEMO-1=feature/help"], input="y\n"
    )
    assert started.exit_code == 0, started.output
    assert "feature/help" in started.output and "plan DEMO-1" in started.output
    shown = runner.invoke(app, [*home, "plan", "DEMO-1"])
    assert "Implementation plan for DEMO-1" in shown.output and "--approve" in shown.output
    both = runner.invoke(app, [*home, "plan", "DEMO-1", "--approve", "--reject"])
    assert both.exit_code == 1
    revised = runner.invoke(
        app, [*home, "plan", "DEMO-1", "--comment", "Add an example"], input="y\n"
    )
    assert revised.exit_code == 0, revised.output
    assert "Add an example" in runner.invoke(app, [*home, "plan", "DEMO-1"]).output
    approved = runner.invoke(app, [*home, "plan", "DEMO-1", "--approve"], input="y\n")
    assert approved.exit_code == 0, approved.output
    assert "review DEMO-1" in approved.output


def test_cli_plan_output_neutralizes_terminal_escapes() -> None:
    from gemini_orchestrator.cli import printable

    assert "\x1b" not in printable("safe\x1b]52;c;Y2xpcGJvYXJk\x07 text\nnext")
    assert printable("line\n\tindent") == "line\n\tindent"


async def test_connected_planning_is_read_only_and_resumed(planner: Engine, tmp_path: Path) -> None:
    if not shutil.which("sandbox-exec" if platform.system() == "Darwin" else "bwrap"):
        pytest.skip("A supported OS sandbox is required for connected workers")
    executable = tmp_path / "synthetic-gemini"
    executable.write_text(f"""#!{sys.executable}
import sys,json,os
from pathlib import Path
if "--version" in sys.argv: print("synthetic-2")
elif "--help" in sys.argv: print("list --prompt --output-format --admin-policy --resume")
else:
    payload = json.loads(sys.stdin.read())
    policy = Path(sys.argv[sys.argv.index("--admin-policy") + 1])
    resumed = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else ""
    record = {{"policy": policy.name, "resumed": resumed, "home": os.environ["HOME"],
              "approved": payload.get("approved_plan", "")}}
    if policy.name == "planning-policy.toml":
        assert "write_file" not in policy.read_text() and "replace" not in policy.read_text()
        try:
            Path("planning-wrote-this").write_text("bad"); record["wrote"] = True
        except OSError:
            record["wrote"] = False
        response = "1. Edit src/help.py\\n2. Add a test"
    else:
        Path("implementation.txt").write_text("done")
        response = "Implemented the approved plan"
    with open(Path(os.environ["HOME"]) / "calls.jsonl", "a") as stream:
        stream.write(json.dumps(record) + "\\n")
    print(json.dumps({{"response": response, "session_id": "4b1c2f2e-2a51-4f43-8f2e-0d6c1f3b9a11"}}))
""")
    executable.chmod(0o700)
    planner.profile.mode = "connected"
    planner.profile.gemini.executable = str(executable)
    planner.profile.gemini.retries = 0
    planner.store.upsert(demo_tickets())
    planner.start(["DEMO-1"], "Alex", planner.preview(["DEMO-1"]))
    await planner.execute(connected_enabled=True)
    row = planner.store.row("DEMO-1")
    result = json.loads(row["result"] or "{}")
    if (
        row["state"] == State.FAILED
        and result.get("log")
        and "sandbox_apply: Operation not permitted"
        in await asyncio.to_thread(Path(result["log"]).read_text)
    ):
        pytest.skip("Host sandbox cannot nest sandbox-exec")
    assert row["state"] == State.PLAN_REVIEW, planner.store.events(10)
    assert plan_text(planner, "DEMO-1") == "1. Edit src/help.py\n2. Add a test"
    fingerprint = str(planner.implementation_plan("DEMO-1")["fingerprint"])
    assert planner.decide_plan("DEMO-1", "approve", "Alex", fingerprint)
    await planner.execute(connected_enabled=True)
    assert planner.store.row("DEMO-1")["state"] == State.READY_FOR_REVIEW
    calls = scratch(planner.runtime, "DEMO-1") / "calls.jsonl"
    planning, implementing = [json.loads(line) for line in calls.read_text().splitlines()]
    assert planning["policy"] == "planning-policy.toml" and planning["wrote"] is False
    assert implementing["policy"] == "worker-policy.toml"
    # One isolated home and one native session span planning and implementation.
    assert implementing["home"] == planning["home"]
    assert implementing["resumed"] == "4b1c2f2e-2a51-4f43-8f2e-0d6c1f3b9a11"
    assert implementing["approved"] == "1. Edit src/help.py\n2. Add a test"


def test_connected_access_is_one_time_profile_consent(profiles: Profiles) -> None:
    from gemini_orchestrator.cli import connected_access
    from gemini_orchestrator.models import digest

    demo = create_demo(profiles)
    work = Profile.model_validate({**demo.model_dump(), "name": "work", "mode": "connected"})
    profiles.save(work)
    home = ["--home", str(profiles.home), "--profile", "work"]
    refused = runner.invoke(app, [*home, "work", "DEMO-1"])
    assert refused.exit_code == 1 and "profile connected --enable" in str(refused.exception)
    assert connected_access(work, True)
    assert not connected_access(demo, False)
    declined = runner.invoke(app, [*home, "profile", "connected", "--enable"], input="n\n")
    assert declined.exit_code == 1 and not profiles.load("work").connected_access
    allowed = runner.invoke(app, [*home, "profile", "connected", "--enable"], input="y\n")
    assert allowed.exit_code == 0, allowed.output
    saved = profiles.load("work")
    assert saved.connected_access and connected_access(saved, False)
    # Consent is not part of the configuration fingerprint, so approvals survive toggling it.
    assert digest(saved) == digest(work)
    runner.invoke(app, [*home, "profile", "connected", "--disable"])
    assert not profiles.load("work").connected_access
    demo_home = ["--home", str(profiles.home), "--profile", "demo"]
    assert runner.invoke(app, [*demo_home, "profile", "connected", "--enable"]).exit_code == 1
    with pytest.raises(ValueError, match="Demo profiles"):
        Profile.model_validate({**demo.model_dump(), "connected_access": True})


def test_setup_records_connected_access_once(profiles: Profiles) -> None:
    from unittest.mock import patch

    from gemini_orchestrator.setup import wizard

    demo = create_demo(profiles)
    answers = iter(["Alex", str(demo.repository), "missing-gemini", "{key}", "later", "save"])
    with (
        patch("gemini_orchestrator.setup.Prompt.ask", side_effect=lambda *a, **kw: next(answers)),
        patch(
            "gemini_orchestrator.setup.Confirm.ask",
            side_effect=lambda prompt, **kw: prompt.startswith("Allow this profile"),
        ),
        patch("gemini_orchestrator.setup.shutil.which", return_value=None),
    ):
        profile = wizard(profiles, "consented")
    assert profile.connected_access and profiles.load("consented").connected_access


def test_acceptance_mentions_are_advisory_only() -> None:
    ticket = Ticket(
        key="APP-4",
        summary="Use the new API",
        assignee="a",
        sprint_id="1",
        acceptance=["Calls APP-2 and APP-3 endpoints", "Matches APP-2 schema; see APP-4"],
        dependencies=["APP-3"],
    )
    assert mentioned_tickets(ticket) == ["APP-2"]
