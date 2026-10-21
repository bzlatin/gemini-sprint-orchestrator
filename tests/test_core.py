from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from gemini_orchestrator.config import Profiles, atomic_write
from gemini_orchestrator.demo import create_demo, demo_tickets
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.git import Git
from gemini_orchestrator.models import Check, OrchestratorError, Resource, State
from gemini_orchestrator.planning import create_plan


def test_profiles_create_switch_isolation(profiles: Profiles) -> None:
    first = create_demo(profiles, "one")
    second = create_demo(profiles, "two")
    profiles.switch("one")
    assert profiles.load().name == "one"
    profiles.switch("two")
    assert profiles.load().name == "two"
    assert first.repository != second.repository
    assert profiles.names() == ["one", "two"]
    assert profiles.path("one").stat().st_mode & 0o777 == 0o600
    assert profiles.runtime("one").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("name", ["../oops", "/tmp/x", "One", "a/b", "a;b", "", "a" * 50])
def test_unsafe_profile_names(profiles: Profiles, name: str) -> None:
    with pytest.raises(OrchestratorError):
        profiles.path(name)


def test_atomic_files_and_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "private" / "board.md"
    atomic_write(path, "first")
    atomic_write(path, "second")
    assert path.read_text() == "second"
    assert path.stat().st_mode & 0o777 == 0o600
    link = tmp_path / "linked"
    link.symlink_to(path.parent)
    with pytest.raises(OrchestratorError):
        atomic_write(link / "oops", "bad")


@pytest.mark.parametrize("state", [State.NOT_SELECTED, State.SELECTED, State.READY_FOR_REVIEW])
def test_no_worker_self_approval(engine: Engine, state: State) -> None:
    engine.store.upsert(demo_tickets())
    engine.store.db.execute("UPDATE tickets SET state=? WHERE key='DEMO-1'", (state,))
    with pytest.raises(OrchestratorError):
        engine.store.transition("DEMO-1", State.APPROVED)


def test_approval_exact_action_one_use(engine: Engine) -> None:
    approval = engine.store.request("push", "DEMO-1", "commit-a")
    with pytest.raises(OrchestratorError):
        engine.store.grant(approval, "worker")
    engine.store.grant(approval, "Alex")
    for action, subject, fingerprint in [
        ("draft", "DEMO-1", "commit-a"),
        ("push", "DEMO-2", "commit-a"),
        ("push", "DEMO-1", "commit-b"),
    ]:
        with pytest.raises(OrchestratorError):
            engine.store.require(action, subject, fingerprint)
    engine.store.require("push", "DEMO-1", "commit-a", consume=True)
    with pytest.raises(OrchestratorError):
        engine.store.require("push", "DEMO-1", "commit-a")


def test_transaction_rolls_back(engine: Engine) -> None:
    engine.store.upsert(demo_tickets())
    with pytest.raises(RuntimeError), engine.store.transaction():
        engine.store.db.execute("UPDATE tickets SET state='FAILED'")
        raise RuntimeError("crash")
    assert all(row["state"] == State.NOT_SELECTED for row in engine.store.rows())


def test_dag_conflicts_cycles_and_blockers(engine: Engine) -> None:
    tickets = demo_tickets()
    plan = create_plan(engine.profile, tickets)
    assert "DEMO-6" in plan.blocked
    assert any(edge.kind == "potential conflict" for edge in plan.edges)
    assert all(not {"DEMO-1", "DEMO-4"} <= set(group) for group in plan.groups)
    assert next(i for i, group in enumerate(plan.groups) if "DEMO-1" in group) < next(
        i for i, group in enumerate(plan.groups) if "DEMO-3" in group
    )
    tickets[0].dependencies = ["DEMO-3"]
    cycle = create_plan(engine.profile, tickets)
    assert "cycle" in cycle.blocked["DEMO-1"]
    assert "cycle" in cycle.blocked["DEMO-3"]


def test_inferred_dependencies_not_scheduled_as_confirmed(engine: Engine) -> None:
    tickets = demo_tickets()[:2]
    tickets[1].acceptance = ["Consider DEMO-1"]
    plan = create_plan(engine.profile, tickets)
    assert len(plan.groups[0]) == 2
    assert any(edge.kind == "unknown" for edge in plan.edges)


def test_readonly_repo_validation(engine: Engine) -> None:
    git = Git(engine.profile.repository)
    before = git.fingerprint()
    git.validate()
    assert before == git.fingerprint()


def test_worktree_isolation_collision_and_path_escape(engine: Engine) -> None:
    git = Git(engine.profile.repository)
    base = git.validate()["head"]
    path = engine.profile.worktrees / "first"
    git.create(engine.profile, "gso/first", path, base)
    (path / "unique.txt").write_text("isolated")
    assert not (engine.profile.repository / "unique.txt").exists()
    with pytest.raises(OrchestratorError, match="collision"):
        git.create(engine.profile, "gso/first", path, base)
    with pytest.raises(OrchestratorError, match="escapes"):
        git.create(engine.profile, "gso/escape", path.parent.parent / "escape", base)
    with pytest.raises(OrchestratorError, match="protected"):
        git.create(engine.profile, "main", engine.profile.worktrees / "main", base)


def test_untracked_and_symlink_changes_invalidate_fingerprint(engine: Engine) -> None:
    git = Git(engine.profile.repository)
    before = git.fingerprint()
    path = engine.profile.repository / "new.txt"
    path.write_text("first")
    first = git.fingerprint()
    path.write_text("second")
    assert len({before, first, git.fingerprint()}) == 3
    path.unlink()
    path.symlink_to("README.md")
    assert git.fingerprint() != before


def test_profile_path_and_command_validation(engine: Engine) -> None:
    raw = engine.profile.model_dump()
    raw["worktrees"] = engine.profile.repository / "nested"
    with pytest.raises(ValidationError):
        type(engine.profile).model_validate(raw)
    with pytest.raises(ValidationError):
        Check(name="bad", argv=[])


async def test_plan_and_launch_approval_enforcement(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1", "DEMO-2"], "Alex")
    plan = engine.propose()
    with pytest.raises(OrchestratorError, match="plan"):
        await engine.execute()
    engine.approve_plan("Alex")
    with pytest.raises(OrchestratorError, match="launch"):
        await engine.execute()
    engine.approve_launch("Alex")
    engine.profile.gemini.concurrency = 3
    with pytest.raises(OrchestratorError, match="Configuration"):
        await engine.execute()
    engine.profile.gemini.concurrency = 2
    engine.select(["DEMO-1"], "Alex")
    revised = engine.propose()
    assert revised.fingerprint != plan.fingerprint
    with pytest.raises(OrchestratorError):
        engine.store.require("plan", plan.id, plan.fingerprint)


async def test_scope_changed_sprint_isolated(engine: Engine, profiles: Profiles) -> None:
    await engine.discover()
    other = engine.profile.model_copy(deep=True)
    other.sprint = Resource(id="102", name="New", board_id="10")
    second = Engine(other, profiles.runtime(other.name))
    try:
        assert not second.store.rows()
        assert second.runtime != engine.runtime
    finally:
        second.close()


async def test_duplicate_profile_session_rejected(engine: Engine, profiles: Profiles) -> None:
    with pytest.raises(OrchestratorError, match="another process"):
        Engine(engine.profile, profiles.runtime(engine.profile.name))


async def test_full_demo_lifecycle_review_feedback_and_continuation(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-6"], "Alex")
    plan = engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    for key in ("DEMO-1", "DEMO-2"):
        assert engine.store.row(key)["state"] == State.READY_FOR_REVIEW, engine.store.events(30)
        package = engine.review(key)
        assert package["verification"]["status"] == "passed"
        assert package["worktree"] != str(engine.profile.repository)
    assert engine.store.row("DEMO-3")["state"] == State.BLOCKED
    engine.accept("DEMO-1", "Alex")
    engine.accept("DEMO-2", "Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    assert engine.store.row("DEMO-3")["state"] == State.READY_FOR_REVIEW
    ancestor = engine.store.row("DEMO-1")
    descendant = engine.store.row("DEMO-3")
    assert Git(engine.profile.repository).ancestor(
        json.loads(ancestor["result"])["accepted_commit"],
        Git(Path(descendant["worktree"])).run("rev-parse", "HEAD"),
    )
    engine.accept("DEMO-3", "Alex")
    engine.feedback("DEMO-1", "Include a clearer example", "Alex")
    assert engine.store.row("DEMO-3")["state"] == State.CHANGES_REQUESTED
    original = engine.store.row("DEMO-1")["worktree"]
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    assert engine.store.row("DEMO-1")["worktree"] == original
    assert "clearer example" in engine.store.row("DEMO-1")["feedback"]
    assert "DEMO-6" in plan.blocked
    assert "READY_FOR_REVIEW" in engine.board().read_text()


async def test_changed_review_requires_reverification(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    row = engine.store.row("DEMO-1")
    (Path(row["worktree"]) / "src/help.py").write_text("# changed after tests\n")
    with pytest.raises(OrchestratorError, match="changed after verification"):
        engine.accept("DEMO-1", "Alex")
    engine.approve_verification("DEMO-1", "Alex")
    await engine.verify("DEMO-1")
    engine.accept("DEMO-1", "Alex")
    assert engine.store.row("DEMO-1")["state"] == State.APPROVED


async def test_no_checks_or_failed_checks_need_manual_attestation(engine: Engine) -> None:
    engine.profile.checks = [Check(name="failure", argv=["false"])]
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    with pytest.raises(OrchestratorError, match="Required checks"):
        engine.accept("DEMO-1", "Alex")
    engine.accept("DEMO-1", "Alex", manual_verified=True)


async def test_crash_recovery_requires_fresh_plan(engine: Engine, profiles: Profiles) -> None:
    await engine.discover()
    engine.store.db.execute(
        "UPDATE tickets SET state='IN_PROGRESS',pid=987654321 WHERE key='DEMO-1'"
    )
    # Release this fixture's engine, replace its connection/lock so fixture cleanup stays valid.
    profile, runtime = engine.profile, profiles.runtime(engine.profile.name)
    engine.close()
    recovered = Engine(profile, runtime)
    assert recovered.store.row("DEMO-1")["state"] == State.INTERRUPTED
    assert recovered.store.row("DEMO-1")["pid"] is None
    engine.__dict__.update(recovered.__dict__)


async def test_actual_parallel_workers(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1", "DEMO-2"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    execution = asyncio.create_task(engine.execute())
    maximum = 0
    while not execution.done():
        maximum = max(maximum, len(engine.supervisor.running))
        await asyncio.sleep(0.02)
    await execution
    assert maximum == 2


async def test_refresh_hides_reassigned_tickets_preserves_history(engine: Engine) -> None:
    from unittest.mock import patch

    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    with patch("gemini_orchestrator.engine.demo_tickets", return_value=demo_tickets()[1:]):
        await engine.discover()
    assert "DEMO-1" not in {row["key"] for row in engine.current_rows()}
    assert engine.store.row("DEMO-1")["state"] == State.NOT_SELECTED
    with pytest.raises(OrchestratorError, match="latest"):
        engine.select(["DEMO-1"], "Alex")
    assert not [a for a in engine.store.approvals() if a["action"] == "plan"]


async def test_verification_settings_change_requires_new_evidence(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    engine.profile.checks = []
    with pytest.raises(OrchestratorError, match="settings changed"):
        engine.accept("DEMO-1", "Alex", manual_verified=True)


async def test_local_dependency_edits_invalidate_approvals(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1", "DEMO-2"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.set_dependencies("DEMO-2", ["DEMO-1"], [], ["README.md"], "Alex")
    assert not [a for a in engine.store.approvals() if a["action"] == "plan"]
    plan = engine.propose()
    assert plan.groups == [["DEMO-1"], ["DEMO-2"]]
    assert any(edge.reason == "Confirmed manually by Alex" for edge in plan.edges)
    with pytest.raises(OrchestratorError):
        engine.set_dependencies("DEMO-1", [], [], ["../outside"], "Alex")


async def test_pause_stops_process_preserves_worktree(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    execution = asyncio.create_task(engine.execute())
    for _ in range(100):
        if "DEMO-1" in engine.supervisor.running:
            break
        await asyncio.sleep(0.01)
    await engine.cancel("DEMO-1", pause=True)
    await asyncio.gather(execution, return_exceptions=True)
    row = engine.store.row("DEMO-1")
    assert row["state"] == State.PAUSED
    assert await asyncio.to_thread(Path(row["worktree"]).exists)


async def test_rerun_verification_requires_exact_command_approval(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    with pytest.raises(OrchestratorError, match="verify"):
        await engine.verify("DEMO-1")
    engine.approve_verification("DEMO-1", "Alex")
    engine.profile.checks = [Check(name="new command", argv=["true"])]
    with pytest.raises(OrchestratorError, match="verify"):
        await engine.verify("DEMO-1")
    engine.approve_verification("DEMO-1", "Alex")
    assert (await engine.verify("DEMO-1"))["status"] == "passed"


async def test_accept_rejects_changed_loaded_review(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    fingerprint = engine.review("DEMO-1")["fingerprint"]
    path = Path(engine.store.row("DEMO-1")["worktree"]) / "src/help.py"
    path.write_text("# revised\n")
    engine.approve_verification("DEMO-1", "Alex")
    await engine.verify("DEMO-1")
    with pytest.raises(OrchestratorError, match="Review preview changed"):
        engine.accept("DEMO-1", "Alex", expected_fingerprint=fingerprint)


def test_git_metadata_mutations_are_serialized_across_processes(
    engine: Engine, tmp_path: Path
) -> None:
    import concurrent.futures
    import subprocess
    import sys

    profile_json = engine.profile.model_dump_json()
    base = Git(engine.profile.repository).validate()["head"]

    def create_one(index: int) -> subprocess.CompletedProcess[str]:
        script = (
            "from gemini_orchestrator.models import Profile; from gemini_orchestrator.git import Git; from pathlib import Path; p=Profile.model_validate_json("
            + repr(profile_json)
            + "); Git(p.repository).create(p,"
            + repr(f"gso/concurrent-{index}")
            + ", p.worktrees/"
            + repr(f"concurrent-{index}")
            + ","
            + repr(base)
            + ")"
        )
        return subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(create_one, range(6)))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert (
        len(
            Git(engine.profile.repository).run("worktree", "list", "--porcelain").split("worktree ")
        )
        == 8
    )


async def test_review_includes_untracked_implementation_content(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    package = engine.review("DEMO-1")
    assert "src/help.py" in package["files"]
    assert "+def example()" in package["diff"]
    assert not package["review_warnings"]


async def test_reruns_preserve_previous_logs(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    original = Path(json.loads(engine.store.row("DEMO-1")["result"])["log"])
    content = await asyncio.to_thread(original.read_text)
    engine.feedback("DEMO-1", "Make a clearer example", "Alex")
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    current = Path(json.loads(engine.store.row("DEMO-1")["result"])["log"])
    assert current != original
    assert await asyncio.to_thread(original.read_text) == content


async def test_checks_changed_during_verification_cannot_be_claimed_passed(engine: Engine) -> None:
    from unittest.mock import patch

    from gemini_orchestrator.models import digest

    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    old_hash = digest(engine.profile.checks)
    engine.approve_verification("DEMO-1", "Alex")
    original_run = engine.supervisor.run

    async def change_configuration(*args, **kwargs):
        result = await original_run(*args, **kwargs)
        engine.profile.checks = [Check(name="Unexecuted changed command", argv=["false"])]
        return result

    with patch.object(engine.supervisor, "run", side_effect=change_configuration):
        result = await engine.verify("DEMO-1")
    assert result["checks_hash"] == old_hash
    assert result["checks_hash"] != digest(engine.profile.checks)
    with pytest.raises(OrchestratorError, match="settings changed"):
        engine.accept("DEMO-1", "Alex")


async def test_binary_changes_require_explicit_full_review_attestation(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    worktree = Path(engine.store.row("DEMO-1")["worktree"])
    (worktree / "asset.bin").write_bytes(b"\x00binary asset")
    engine.approve_verification("DEMO-1", "Alex")
    await engine.verify("DEMO-1")
    assert engine.review("DEMO-1")["review_warnings"]
    with pytest.raises(OrchestratorError, match="Full diff inspection"):
        engine.accept("DEMO-1", "Alex")
    engine.accept("DEMO-1", "Alex", full_diff_reviewed=True)
