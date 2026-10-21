from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .board import render_board
from .config import atomic_write
from .demo import demo_tickets
from .gemini import Gemini
from .git import Git
from .jira import JiraConnection
from .models import TRANSITIONS, Edge, OrchestratorError, Plan, Profile, State, digest
from .planning import create_plan
from .process import Supervisor
from .store import Store


class Engine:
    def __init__(self, profile: Profile, runtime: Path):
        self.profile = profile
        # Changing Jira identity, sprint, board or checkout creates a separate state scope.
        scope = digest(
            {
                "mode": profile.mode,
                "identity": profile.identity.account_id,
                "repository": str(profile.repository),
                "board": profile.board_id,
                "sprint": profile.sprint.id if profile.sprint else None,
            }
        )[:16]
        from .config import private_dir

        runtime = private_dir(runtime / "sessions" / scope)
        self.runtime = runtime
        self.lock = (runtime / "session.lock").open("a+")
        os.chmod(runtime / "session.lock", 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close()
            raise OrchestratorError(
                "This profile is open in another process; close it before starting a second session"
            ) from exc
        self.store = Store(runtime / "state.db")
        self.supervisor = Supervisor()
        self.executing = False
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.stop_states: dict[str, State] = {}
        self.recover()

    def recover(self) -> None:
        for row in self.store.rows():
            if row["state"] in {State.IN_PROGRESS, State.VERIFYING}:
                self.store.transition(row["key"], State.INTERRUPTED)
                self.store.event(
                    "Recovered after interruption; no process assumed alive. Inspect worktree before retry.",
                    row["key"],
                )
                self.store.patch(row["key"], pid=None)
        self.board()

    def close(self) -> None:
        self.store.close()
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    def board(self) -> Path:
        return render_board(self.store, self.profile, self.runtime)

    async def discover(self, *, connected_enabled: bool = False) -> None:
        if self.executing:
            raise OrchestratorError("Wait for running workers before refreshing tickets")
        if self.profile.mode == "demo":
            tickets = demo_tickets()
        else:
            if not connected_enabled:
                raise OrchestratorError(
                    "Connected Jira access requires --enable-connected in an approved environment"
                )
            async with JiraConnection(self.profile) as jira:
                tickets = await jira.issues(self.profile)
        # Scope filtering in the adapter is mandatory; this final guard prevents a bad adapter bypass.
        for ticket in tickets:
            if (
                ticket.assignee != self.profile.identity.account_id
                or not self.profile.sprint
                or ticket.sprint_id != self.profile.sprint.id
            ):
                raise OrchestratorError(
                    "Adapter returned an issue outside this user's sprint; refresh aborted"
                )
        self.store.upsert(tickets)
        current = [ticket.key for ticket in tickets]
        with self.store.transaction():
            self.store.db.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('discovered_keys',?)",
                (json.dumps(current),),
            )
            removed = [row for row in self.store.rows() if row["key"] not in current]
            if removed:
                self.store.db.execute(
                    "UPDATE approvals SET consumed=1 WHERE action IN ('plan','launch')"
                )
            for row in removed:
                if row["state"] in {
                    State.SELECTED,
                    State.AWAITING_PLAN_APPROVAL,
                    State.QUEUED,
                    State.BLOCKED,
                }:
                    self.store.transition(row["key"], State.NOT_SELECTED)
                self.store.event(
                    "Absent from latest assigned discovery; hidden from ticket explorer, history preserved",
                    row["key"],
                )
        self.board()

    def current_rows(self) -> list[dict[str, Any]]:
        saved = self.store.db.execute(
            "SELECT value FROM meta WHERE key='discovered_keys'"
        ).fetchone()
        keys = set(json.loads(saved[0])) if saved else {row["key"] for row in self.store.rows()}
        return [row for row in self.store.rows() if row["key"] in keys]

    def select(self, keys: list[str], actor: str) -> None:
        if self.executing:
            raise OrchestratorError("Cannot change selection while workers run")
        if len(set(keys)) != len(keys):
            raise OrchestratorError("Duplicate ticket selection")
        for key in keys:
            if key not in {r["key"] for r in self.current_rows()}:
                raise OrchestratorError(
                    f"{key} is outside the latest assigned sprint discovery; refresh first"
                )
            self.store.ticket(key)
        fingerprint = self.store.selection_hash(keys)
        approval = self.store.request("selection", "tickets", fingerprint)
        self.store.grant(approval, actor)
        with self.store.transaction():
            self.store.require("selection", "tickets", fingerprint, consume=True)
            self.store.db.execute(
                "UPDATE approvals SET consumed=1 WHERE action IN ('plan','launch')"
            )
            for row in self.store.rows():
                state = State(row["state"])
                if state in {
                    State.SELECTED,
                    State.AWAITING_PLAN_APPROVAL,
                    State.QUEUED,
                    State.BLOCKED,
                }:
                    target = State.SELECTED if row["key"] in keys else State.NOT_SELECTED
                    if state != target:
                        self.store.transition(row["key"], target)
                elif row["key"] in keys:
                    if State.SELECTED not in TRANSITIONS[state]:
                        raise OrchestratorError(
                            f"{row['key']} must be reviewed, deferred or cancelled before reselection"
                        )
                    self.store.transition(row["key"], State.SELECTED)
            self.store.event("Human selected: " + (", ".join(keys) or "none"))
        self.board()

    def propose(self, advisory: list[Edge] | None = None) -> Plan:
        if self.executing:
            raise OrchestratorError("Wait for workers before changing a plan")
        rows = [
            row
            for row in self.store.rows()
            if row["state"] in {State.SELECTED, State.AWAITING_PLAN_APPROVAL}
        ]
        plan = create_plan(self.profile, [self.store.ticket(row["key"]) for row in rows])
        if advisory:
            plan.edges.extend(advisory)
        # Existing work is continued explicitly on its existing branch/worktree.
        for row in rows:
            if row["worktree"]:
                self.validate_worktree(row)
                plan.branches[row["key"]] = row["branch"]
                plan.worktrees[row["key"]] = row["worktree"]
                plan.context[f"continuation:{row['key']}"] = Git(
                    Path(row["worktree"])
                ).fingerprint()
        self.store.save_plan(plan)
        for row in rows:
            if row["state"] == State.SELECTED:
                self.store.transition(row["key"], State.AWAITING_PLAN_APPROVAL)
        self.board()
        return plan

    async def propose_semantic(self, *, connected_enabled: bool = False) -> Plan:
        if self.profile.mode != "connected" or not connected_enabled:
            raise OrchestratorError(
                "Gemini reasoning requires explicit connected mode in an approved environment"
            )
        from .semantic import recommendations

        rows = [
            row
            for row in self.store.rows()
            if row["state"] in {State.SELECTED, State.AWAITING_PLAN_APPROVAL}
        ]
        tickets = [self.store.ticket(row["key"]) for row in rows]
        draft = create_plan(self.profile, tickets)
        edges = await recommendations(
            self.profile, draft, [t.model_dump() for t in tickets], self.runtime
        )
        self.store.event(
            f"Gemini supplied {len(edges)} advisory recommendations; scheduling remains deterministic"
        )
        return self.propose(advisory=edges)

    def set_dependencies(
        self, key: str, dependencies: list[str], conflicts: list[str], files: list[str], actor: str
    ) -> None:
        if self.executing:
            raise OrchestratorError("Cannot edit dependencies while workers run")
        ticket = self.store.ticket(key)
        for dep in dependencies + conflicts:
            if dep == key:
                raise OrchestratorError("A ticket cannot depend on or conflict with itself")
            # Validate syntax without requiring external prerequisites to be selected.
            type(ticket).model_validate({**ticket.model_dump(), "key": dep})
        for path in files:
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise OrchestratorError("Relevant source paths must stay inside the repository")
        ticket.dependencies, ticket.conflicts, ticket.files = dependencies, conflicts, files
        ticket.dependency_reasons = {dep: f"Confirmed manually by {actor}" for dep in dependencies}
        state = State(self.store.row(key)["state"])
        if state not in {
            State.NOT_SELECTED,
            State.SELECTED,
            State.AWAITING_PLAN_APPROVAL,
            State.QUEUED,
            State.BLOCKED,
        }:
            raise OrchestratorError(
                "Return this ticket to selection before changing its dependencies"
            )
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE tickets SET data=? WHERE key=?", (ticket.model_dump_json(), key)
            )
            for row in self.store.rows():
                if (
                    row["state"] in {State.AWAITING_PLAN_APPROVAL, State.QUEUED, State.BLOCKED}
                    or row["key"] == key
                ):
                    if row["state"] != State.SELECTED:
                        self.store.transition(row["key"], State.SELECTED)
            self.store.db.execute(
                "UPDATE approvals SET consumed=1 WHERE action IN ('plan','launch')"
            )
            self.store.event(
                f"Human {actor} edited dependency/conflict context; regenerate plan", key
            )
        self.board()

    def approve_plan(self, actor: str) -> None:
        plan = self.store.plan()
        self.validate_plan(plan)
        self.store.grant(self.store.request("plan", plan.id, plan.fingerprint), actor)
        for key in plan.tickets:
            row = self.store.row(key)
            if row["state"] == State.AWAITING_PLAN_APPROVAL:
                self.store.transition(key, State.BLOCKED if key in plan.blocked else State.QUEUED)
        self.store.request("launch", plan.id, self.launch_fingerprint(plan))
        self.board()

    def approve_launch(self, actor: str) -> None:
        plan = self.store.plan()
        self.validate_plan(plan)
        self.store.require("plan", plan.id, plan.fingerprint)
        self.store.grant(
            self.store.request("launch", plan.id, self.launch_fingerprint(plan)), actor
        )
        self.board()

    def launch_fingerprint(self, plan: Plan) -> str:
        evidence = {}
        for key in plan.tickets:
            row = self.store.row(key)
            evidence[key] = {
                "state": row["state"],
                "tree": Git(Path(row["worktree"])).fingerprint() if row["worktree"] else None,
            }
        return digest({"plan": plan.fingerprint, "evidence": evidence})

    def validate_plan(self, plan: Plan) -> None:
        if plan.config_hash != digest(self.profile):
            raise OrchestratorError("Configuration changed; generate and approve a new plan")
        git = Git(self.profile.repository)
        if git.validate()["head"] != plan.base or not git.clean():
            raise OrchestratorError("Base repository changed; generate and approve a new plan")
        for key, fingerprint in plan.tickets.items():
            if digest(self.store.ticket(key)) != fingerprint:
                raise OrchestratorError(f"{key} changed; regenerate plan")
            continuation = plan.context.get(f"continuation:{key}")
            if continuation and self.store.row(key)["state"] in {
                State.AWAITING_PLAN_APPROVAL,
                State.QUEUED,
            }:
                if Git(Path(plan.worktrees[key])).fingerprint() != continuation:
                    raise OrchestratorError(
                        f"{key} worktree changed since planning; regenerate plan"
                    )

    def validate_worktree(self, row: dict[str, Any]) -> Git:
        path = Path(row["worktree"])
        if path.is_symlink() or path.parent.resolve() != self.profile.worktrees.resolve():
            raise OrchestratorError("Stored worktree is outside the configured directory")
        git = Git(path)
        if (
            git.validate()["branch"] != row["branch"]
            or row["branch"] in self.profile.protected_branches
        ):
            raise OrchestratorError("Worker branch changed or is protected")
        expected_common = Git(self.profile.repository).run(
            "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        actual_common = git.run("rev-parse", "--path-format=absolute", "--git-common-dir")
        if Path(expected_common).resolve() != Path(actual_common).resolve():
            raise OrchestratorError("Worktree Git metadata changed; inspect it manually")
        return git

    async def execute(self, *, connected_enabled: bool = False) -> None:
        if self.executing:
            raise OrchestratorError("Execution is already active")
        if self.profile.mode == "connected" and not connected_enabled:
            raise OrchestratorError("Connected worker launch requires explicit --enable-connected")
        plan = self.store.plan()
        self.validate_plan(plan)
        self.store.require("plan", plan.id, plan.fingerprint)
        self.store.require("launch", plan.id, self.launch_fingerprint(plan))
        self.executing = True
        # Launch grant is one-use for the approved plan's queued batch.
        self.store.require("launch", plan.id, self.launch_fingerprint(plan), consume=True)
        try:
            for group in plan.groups:
                pending = [
                    key
                    for key in group
                    if self.store.row(key)["state"] in {State.QUEUED, State.BLOCKED}
                    and key not in plan.blocked
                ]
                tasks = []
                for key in pending:
                    ticket = self.store.ticket(key)
                    if any(
                        self.store.row(dep)["state"] not in {State.APPROVED, State.MR_READY}
                        for dep in ticket.dependencies
                    ):
                        if self.store.row(key)["state"] == State.QUEUED:
                            self.store.transition(key, State.BLOCKED)
                        self.store.event(
                            "Waiting for human acceptance of prerequisites; approve a fresh launch after review",
                            key,
                        )
                        continue
                    if self.store.row(key)["state"] == State.BLOCKED:
                        self.store.transition(key, State.QUEUED)
                    task = asyncio.create_task(self.run_ticket(key, plan))
                    self.tasks[key] = task
                    tasks.append(task)
                if tasks:
                    await asyncio.gather(*tasks)
                self.tasks.clear()
        finally:
            self.executing = False
            self.board()

    def prerequisite_base(self, key: str, plan: Plan) -> str:
        heads: list[str] = []
        root = Git(self.profile.repository)
        for dep in self.store.ticket(key).dependencies:
            row = self.store.row(dep)
            git = self.validate_worktree(row)
            result = json.loads(row["result"] or "{}")
            if result.get("accepted_fingerprint") != git.fingerprint():
                raise OrchestratorError(
                    f"Accepted prerequisite {dep} changed; re-review it and revalidate downstream work"
                )
            heads.append(git.run("rev-parse", "HEAD"))
        if not heads:
            return plan.base
        for candidate in heads:
            if all(root.ancestor(other, candidate) for other in heads):
                return candidate
        raise OrchestratorError(
            "Prerequisites diverge; manually integrate them and generate a new plan. No automatic merge is performed"
        )

    async def run_ticket(self, key: str, plan: Plan) -> None:
        try:
            row = self.store.row(key)
            if row["worktree"]:
                git = self.validate_worktree(row)
                expected_base = self.prerequisite_base(key, plan)
                if not Git(self.profile.repository).ancestor(
                    expected_base, git.run("rev-parse", "HEAD")
                ):
                    raise OrchestratorError(
                        "Prerequisite commit changed; manually integrate it in this continuation worktree before replanning"
                    )
            else:
                base = self.prerequisite_base(key, plan)
                path = Path(plan.worktrees[key])
                creation = asyncio.create_task(
                    asyncio.to_thread(
                        Git(self.profile.repository).create,
                        self.profile,
                        plan.branches[key],
                        path,
                        base,
                    )
                )
                try:
                    await asyncio.shield(creation)
                except asyncio.CancelledError:
                    # A thread cannot be cancelled. Reconcile its real Git result before
                    # recording cancellation so no successful worktree is lost from state.
                    await creation
                    self.store.patch(key, worktree=str(path), branch=plan.branches[key], base=base)
                    raise
                self.store.patch(key, worktree=str(path), branch=plan.branches[key], base=base)
                git = Git(path)
            self.store.transition(key, State.IN_PROGRESS)
            ticket = self.store.ticket(key)
            feedback = json.loads(self.store.row(key)["feedback"])
            for attempt in range(self.profile.gemini.retries + 1):
                if self.profile.mode == "demo":
                    # Genuine subprocesses exercise the same monitor, timeout and cancellation path.
                    relative = ticket.files[0]
                    if relative.endswith(".py"):
                        content = f'"""{ticket.summary}. Synthetic demo implementation."""\n\ndef example() -> str:\n    return "{key}"\n'
                    else:
                        content = f"# {ticket.summary}\n\nSynthetic example for {key}.\n"
                    script = (
                        "import pathlib,time; time.sleep(0.35); p=pathlib.Path("
                        + repr(relative)
                        + "); p.parent.mkdir(parents=True,exist_ok=True); p.write_text("
                        + repr(content)
                        + "); print('Synthetic implementation complete')"
                    )
                    argv, prompt = [sys.executable, "-c", script], None
                else:
                    argv, prompt = await asyncio.to_thread(
                        Gemini(self.profile.gemini.executable).worker,
                        self.profile,
                        ticket,
                        git.root,
                        self.runtime,
                        feedback,
                    )
                log = self.runtime / "logs" / f"{key}-worker-{uuid.uuid4().hex[:12]}-{attempt}.log"
                started = datetime.now(UTC).isoformat()

                def on_start(
                    pid: int,
                    token: str,
                    log_path: Path = log,
                    started_at: str = started,
                    try_number: int = attempt + 1,
                ) -> None:
                    self.store.patch(
                        key,
                        pid=pid,
                        process_token=token,
                        result=json.dumps(
                            {"log": str(log_path), "started_at": started_at, "attempt": try_number}
                        ),
                    )

                result = await self.supervisor.run(
                    key,
                    argv,
                    git.root,
                    self.profile.gemini.timeout,
                    log,
                    on_start,
                    prompt,
                )
                result["started_at"] = started
                if self.profile.mode == "connected" and result["passed"]:
                    try:
                        output = json.loads(log.read_text())
                        if output.get("error") or not isinstance(output.get("response"), str):
                            raise ValueError("Missing response or Gemini error")
                        result["summary"] = output["response"]
                    except (ValueError, OSError) as exc:
                        result["passed"] = False
                        result["failure"] = f"Invalid structured Gemini result: {exc}"
                self.store.run_record(key, "worker", result)
                if result["passed"]:
                    break
                self.store.event(
                    f"Worker attempt {attempt + 1} failed; bounded retry limit {self.profile.gemini.retries}",
                    key,
                )
            self.store.patch(key, pid=None, result=json.dumps(result))
            if not result["passed"]:
                self.store.transition(key, State.FAILED)
                return
            self.store.transition(key, State.VERIFYING)
            await self.verify(key, approved_launch=True)
        except asyncio.CancelledError:
            row = self.store.row(key)
            if row["state"] in {State.QUEUED, State.IN_PROGRESS, State.VERIFYING}:
                self.store.transition(key, self.stop_states.pop(key, State.CANCELLED))
            self.store.patch(key, pid=None)
            raise
        except Exception as exc:
            row = self.store.row(key)
            self.store.event(str(exc), key)
            if row["state"] in {State.IN_PROGRESS, State.VERIFYING}:
                self.store.transition(key, State.FAILED)
            elif row["state"] == State.QUEUED:
                self.store.transition(key, State.BLOCKED)
            self.store.patch(key, pid=None)
        finally:
            self.board()

    def verification_fingerprint(self, key: str) -> str:
        row = self.store.row(key)
        git = self.validate_worktree(row)
        return digest(
            {
                "ticket": key,
                "tree": git.fingerprint(),
                "checks": [check.model_dump() for check in self.profile.checks],
            }
        )

    def approve_verification(self, key: str, actor: str) -> None:
        fingerprint = self.verification_fingerprint(key)
        self.store.grant(self.store.request("verify", key, fingerprint), actor)
        self.board()

    async def verify(self, key: str, *, approved_launch: bool = False) -> dict[str, Any]:
        row = self.store.row(key)
        checks = [check.model_copy(deep=True) for check in self.profile.checks]
        if not approved_launch:
            self.store.require("verify", key, self.verification_fingerprint(key), consume=True)
        if row["state"] != State.VERIFYING:
            if row["state"] not in {State.READY_FOR_REVIEW, State.APPROVED, State.MR_READY}:
                raise OrchestratorError("Task is not ready for verification")
            self.store.transition(key, State.VERIFYING)
        git = self.validate_worktree(row)
        before = git.fingerprint()
        results = []
        for index, check in enumerate(checks):
            result = await self.supervisor.run(
                f"{key}:verify",
                check.argv,
                git.root,
                check.timeout,
                self.runtime / "logs" / f"{key}-check-{uuid.uuid4().hex[:12]}-{index}.log",
            )
            result.update({"name": check.name, "required": check.required})
            results.append(result)
            self.store.run_record(key, "verification", result)
        # Verification can legitimately generate tracked build output; bind to post-check state.
        fingerprint = git.fingerprint()
        status = (
            "manual required"
            if not results
            else "failed"
            if any(not r["passed"] and r["required"] for r in results)
            else "passed"
        )
        verification = {
            "status": status,
            "results": results,
            "fingerprint": fingerprint,
            "precheck_fingerprint": before,
            "checks_hash": digest(checks),
        }
        self.store.patch(key, verification=json.dumps(verification))
        self.store.transition(key, State.READY_FOR_REVIEW)
        self.store.request("accept", key, fingerprint)
        self.store.event(f"Verification {status}; human review required", key)
        self.board()
        return verification

    def review(self, key: str) -> dict[str, Any]:
        row = self.store.row(key)
        if not row["worktree"]:
            raise OrchestratorError("No implementation worktree yet")
        git = self.validate_worktree(row)
        result = {
            "ticket": self.store.ticket(key).model_dump(),
            "branch": row["branch"],
            "worktree": row["worktree"],
            **git.review(row["base"]),
            "fingerprint": git.fingerprint(),
            "worker": json.loads(row["result"] or "{}"),
            "verification": json.loads(row["verification"] or "{}"),
            "feedback": json.loads(row["feedback"]),
            "risks": [
                "Generated implementation requires human inspection",
                "Verification covers only configured commands; inspect untracked files in worktree",
            ],
        }
        atomic_write(self.runtime / "reviews" / f"{key}.json", json.dumps(result, indent=2))
        return result

    def accept(
        self,
        key: str,
        actor: str,
        *,
        manual_verified: bool = False,
        expected_fingerprint: str | None = None,
        full_diff_reviewed: bool = False,
    ) -> None:
        row = self.store.row(key)
        if row["state"] != State.READY_FOR_REVIEW:
            raise OrchestratorError("Only review-ready implementations can be accepted")
        git = self.validate_worktree(row)
        fingerprint = git.fingerprint()
        if expected_fingerprint is not None and fingerprint != expected_fingerprint:
            raise OrchestratorError(
                "Review preview changed; load and inspect a fresh review package"
            )
        verification = json.loads(row["verification"] or "{}")
        if verification.get("checks_hash") != digest(self.profile.checks):
            raise OrchestratorError("Verification settings changed; rerun verification")
        if verification.get("fingerprint") != fingerprint:
            raise OrchestratorError("Implementation changed after verification; rerun verification")
        if verification.get("status") != "passed" and not manual_verified:
            raise OrchestratorError(
                "Required checks failed or are unavailable; rerun verification or explicitly attest manual verification"
            )
        for dep in self.store.ticket(key).dependencies:
            prerequisite = self.store.row(dep)
            prereq_git = self.validate_worktree(prerequisite)
            accepted = json.loads(prerequisite["result"] or "{}")
            if (
                prerequisite["state"] not in {State.APPROVED, State.MR_READY}
                or accepted.get("accepted_fingerprint") != prereq_git.fingerprint()
                or not Git(self.profile.repository).ancestor(
                    prereq_git.run("rev-parse", "HEAD"), git.run("rev-parse", "HEAD")
                )
            ):
                raise OrchestratorError(
                    "Prerequisite changed or is not accepted; integrate current accepted changes and reverify downstream work"
                )
        if git.review(row["base"])["review_warnings"] and not full_diff_reviewed:
            raise OrchestratorError(
                "Full diff inspection required for binary/truncated changes; inspect the worktree, then explicitly attest --full-diff-reviewed"
            )
        # Approval binds both reviewed tree and resulting commit. No Git hook is executed.
        self.store.grant(self.store.request("accept", key, fingerprint), actor)
        self.store.transition(key, State.APPROVED, human=actor, fingerprint=fingerprint)
        try:
            reviewed_content = git.fingerprint(content_only=True)
            git.checkpoint(key)
            if git.fingerprint(content_only=True) != reviewed_content:
                raise OrchestratorError(
                    "Implementation changed during acceptance; inspect the branch and re-review"
                )
        except Exception:
            self.store.transition(key, State.CHANGES_REQUESTED)
            raise
        result = json.loads(row["result"] or "{}")
        result["accepted_fingerprint"] = git.fingerprint()
        result["accepted_commit"] = git.run("rev-parse", "HEAD")
        result["manual_attestation"] = manual_verified
        result["accepted_config"] = digest(self.profile)
        self.store.patch(key, result=json.dumps(result))
        self.board()

    def feedback(self, key: str, message: str, actor: str) -> None:
        if not message.strip() or not actor.strip():
            raise OrchestratorError("Provide human feedback before requesting changes")
        descendants: set[str] = set()
        changed = {key}
        while changed:
            next_changed = (
                {
                    row["key"]
                    for row in self.store.rows()
                    if set(self.store.ticket(row["key"]).dependencies) & changed
                }
                - descendants
                - {key}
            )
            descendants |= next_changed
            changed = next_changed
        if any(
            self.store.row(dep)["state"] in {State.IN_PROGRESS, State.VERIFYING}
            for dep in descendants
        ):
            raise OrchestratorError(
                "Pause running downstream workers before requesting changes to this prerequisite"
            )
        self.store.transition(key, State.CHANGES_REQUESTED, human=actor)
        history = json.loads(self.store.row(key)["feedback"])
        history.append(f"{actor}: {message}")
        self.store.patch(key, feedback=json.dumps(history), verification=None)
        self.store.db.execute(
            "UPDATE approvals SET consumed=1 WHERE subject=? OR action='launch'", (key,)
        )
        # All already-created descendants need fresh verification/review after prerequisite changes.
        for row in self.store.rows():
            if row["key"] in descendants and row["state"] in {
                State.APPROVED,
                State.MR_READY,
                State.READY_FOR_REVIEW,
            }:
                self.store.transition(row["key"], State.CHANGES_REQUESTED)
                self.store.patch(row["key"], verification=None)
                self.store.event(
                    "Prerequisite changed; revalidate downstream implementation", row["key"]
                )
        self.board()

    async def cancel(self, key: str, *, pause: bool = False) -> None:
        task = self.tasks.get(key)
        if task:
            self.stop_states[key] = State.PAUSED if pause else State.CANCELLED
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            self.store.transition(key, State.PAUSED if pause else State.CANCELLED)
        self.board()

    async def shutdown(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.supervisor.shutdown()
        self.board()
