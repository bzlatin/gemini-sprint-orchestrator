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
from .isolation import environment, scratch, wrap
from .jira import JiraConnection
from .models import (
    TRANSITIONS,
    Edge,
    OrchestratorError,
    Plan,
    Profile,
    State,
    Ticket,
    digest,
)
from .planning import create_plan, mentioned_tickets
from .process import Supervisor
from .store import Store


def profile_scope(profile: Profile) -> str:
    """Stable local runtime scope, also used by clients that do not own the engine lock."""
    return digest(
        {
            "mode": profile.mode,
            "identity": profile.identity.account_id,
            "repository": str(profile.repository),
            "board": profile.board_id,
            "sprint": profile.sprint.id if profile.sprint else None,
        }
    )[:16]


def synthetic_plan(ticket: Ticket, comments: list[str]) -> str:
    lines = [
        f"## Implementation plan for {ticket.key}: {ticket.summary}",
        "",
        "### Understanding",
        ticket.description or ticket.summary,
        "",
        "### Files to change",
        *(f"- `{path}`" for path in ticket.files or ["(to be determined)"]),
        "",
        "### Steps",
        *(f"{index}. {criterion}" for index, criterion in enumerate(ticket.acceptance, 1)),
        "",
        "### Tests",
        "- Run the configured verification checks.",
        "",
        "### Risks and open questions",
        "- Synthetic demo plan; no model was contacted.",
    ]
    if comments:
        lines += ["", "### Revisions", *(f"- Addressed: {comment}" for comment in comments)]
    return "\n".join(lines)


class Engine:
    def __init__(self, profile: Profile, runtime: Path):
        self.profile = profile
        # Changing Jira identity, sprint, board or checkout creates a separate state scope.
        scope = profile_scope(profile)
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
        self.supervisor.storage_limits = (
            profile.isolation.max_worktree_bytes,
            profile.isolation.max_files,
        )
        self.executing = False
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.stop_states: dict[str, State] = {}
        # Tickets a human re-queued while the scheduler runs; it may start them again.
        self.requeued: set[str] = set()
        self.wakeup: asyncio.Event | None = None
        self.recover()

    def recover(self) -> None:
        for row in self.store.rows():
            if row["state"] in {State.PLANNING, State.IN_PROGRESS, State.VERIFYING}:
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

    def propose(
        self,
        advisory: list[Edge] | None = None,
        *,
        branches: dict[str, str] | None = None,
        plan_id: str | None = None,
    ) -> Plan:
        if self.executing:
            raise OrchestratorError("Wait for workers before changing a plan")
        rows = [
            row
            for row in self.store.rows()
            if row["state"] in {State.SELECTED, State.AWAITING_PLAN_APPROVAL}
        ]
        plan = self.preview(
            [row["key"] for row in rows], branches=branches, plan_id=plan_id, rows=rows
        )
        if advisory:
            plan.edges.extend(advisory)
        self.store.save_plan(plan)
        for row in rows:
            if row["state"] == State.SELECTED:
                self.store.transition(row["key"], State.AWAITING_PLAN_APPROVAL)
        self.board()
        return plan

    def preview(
        self,
        keys: list[str],
        *,
        branches: dict[str, str] | None = None,
        plan_id: str | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> Plan:
        """Compute a schedule without recording it, so a human can review it first."""
        rows = rows if rows is not None else [self.store.row(key) for key in keys]
        plan = create_plan(
            self.profile,
            [self.store.ticket(row["key"]) for row in rows],
            plan_id=plan_id,
            branches={
                key: name
                for key, name in (branches or {}).items()
                if not self.store.row(key)["worktree"]
            },
            accepted={
                row["key"]
                for row in self.store.rows()
                if row["state"] in {State.APPROVED, State.MR_READY}
            },
        )
        root = Git(self.profile.repository)
        # Existing work is continued explicitly on its existing branch/worktree.
        for row in rows:
            if row["worktree"]:
                self.validate_worktree(row)
                plan.branches[row["key"]] = row["branch"]
                plan.worktrees[row["key"]] = row["worktree"]
                plan.context[f"continuation:{row['key']}"] = Git(
                    Path(row["worktree"])
                ).fingerprint()
            elif root.branch_exists(plan.branches[row["key"]]):
                raise OrchestratorError(
                    f"Branch {plan.branches[row['key']]} already exists; choose another name for {row['key']}"
                )
        return plan

    def start(self, keys: list[str], actor: str, expected: Plan) -> Plan:
        """One human approval: select, schedule and authorize exactly the reviewed preview."""
        self.select(keys, actor)
        plan = self.propose(branches=expected.branches, plan_id=expected.id)
        fields = ("groups", "blocked", "branches", "worktrees", "checks")
        if any(getattr(plan, name) != getattr(expected, name) for name in fields):
            raise OrchestratorError("The schedule changed while you reviewed it; review it again")
        self.approve_plan(actor)
        self.approve_launch(actor)
        return plan

    def readiness(self, key: str) -> str:
        """Whether a ticket can start now or waits on prerequisites that are not yet accepted."""
        known = {row["key"]: row["state"] for row in self.store.rows()}
        waiting = [
            dep
            for dep in self.store.ticket(key).dependencies
            if known.get(dep) not in {State.APPROVED, State.MR_READY}
        ]
        return "Ready" if not waiting else "Waiting on " + ", ".join(waiting)

    def advisories(self, key: str) -> list[str]:
        """Tickets the acceptance criteria mention without a confirmed dependency."""
        return mentioned_tickets(self.store.ticket(key))

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
        order = [key for group in plan.groups for key in group]
        conflicts = {
            frozenset((edge.prerequisite, edge.dependent))
            for edge in plan.edges
            if edge.kind == "potential conflict"
        }
        running: dict[str, asyncio.Task[None]] = {}
        attempted: set[str] = set()
        noted: set[str] = set()
        self.wakeup = asyncio.Event()
        self.requeued.clear()
        try:
            while True:
                # Fill free worker slots in schedule order; a human decision or acceptance wakes us.
                for key in order:
                    if len(running) >= self.profile.gemini.concurrency:
                        break
                    if key in running or key in plan.blocked:
                        continue
                    if key in attempted and key not in self.requeued:
                        continue
                    state = self.store.row(key)["state"]
                    if state not in {State.QUEUED, State.BLOCKED}:
                        continue
                    ticket = self.store.ticket(key)
                    if any(
                        self.store.row(dep)["state"] not in {State.APPROVED, State.MR_READY}
                        for dep in ticket.dependencies
                    ):
                        if state == State.QUEUED:
                            self.store.transition(key, State.BLOCKED)
                        if key not in noted:
                            noted.add(key)
                            self.store.event(
                                "Waiting for human acceptance of prerequisites; it starts once they are accepted",
                                key,
                            )
                        continue
                    if any(frozenset((key, other)) in conflicts for other in running):
                        continue
                    if state == State.BLOCKED:
                        self.store.transition(key, State.QUEUED)
                    attempted.add(key)
                    self.requeued.discard(key)
                    task = asyncio.create_task(self.run_ticket(key, plan))
                    self.tasks[key] = task
                    running[key] = task
                if not running:
                    break
                self.wakeup.clear()
                waiter = asyncio.create_task(self.wakeup.wait())
                try:
                    # Pausing one worker must not abandon supervision of the rest of its batch.
                    await asyncio.wait(
                        [*running.values(), waiter], return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    waiter.cancel()
                for key, task in list(running.items()):
                    if task.done():
                        running.pop(key)
                        self.tasks.pop(key, None)
        finally:
            # A cancelled scheduler still owns its workers; stop them rather than orphan them.
            for task in running.values():
                task.cancel()
            if running:
                await asyncio.gather(*running.values(), return_exceptions=True)
            self.executing = False
            self.wakeup = None
            self.requeued.clear()
            self.tasks.clear()
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
            from .storage import usage

            usage(
                self.runtime,
                self.profile.isolation.max_runtime_bytes,
                self.profile.isolation.max_files,
            )
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
            approved_plan = self.approved_implementation(key)
            if self.profile.gemini.plan_first and approved_plan is None:
                await self.plan_ticket(key, git)
                return
            self.store.transition(key, State.IN_PROGRESS)
            ticket = self.store.ticket(key)
            feedback = json.loads(self.store.row(key)["feedback"])
            session_id = self.worker_session_id(key)
            if not self.conversation(key):
                self.store.run_record(
                    key,
                    "conversation",
                    {
                        "role": "user",
                        "text": f"Implement {key}: {ticket.summary}\n"
                        + "\n".join(ticket.acceptance),
                    },
                )
            elif (
                approved_plan is not None
                and not self.store.db.execute(
                    "SELECT 1 FROM runs WHERE ticket=? AND kind='worker'", (key,)
                ).fetchone()
            ):
                self.store.run_record(
                    key,
                    "conversation",
                    {"role": "user", "text": f"Implement the approved plan for {key}"},
                )
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
                        session_id=session_id,
                        conversation=self.conversation(key),
                        approved_plan=approved_plan or "",
                    )
                result = await self.model_turn(
                    key, git, argv, prompt, "worker", attempt, session_id
                )
                session_id = str(result["session_id"])
                if self.profile.mode == "demo" and result["passed"]:
                    result["summary"] = "Synthetic worker completed in the same worktree." + (
                        f" Follow-up: {feedback[-1]}"
                        if feedback
                        else " Inspect its implementation and verification before accepting."
                    )
                self.store.run_record(key, "worker", result)
                if result["passed"]:
                    self.store.run_record(
                        key,
                        "conversation",
                        {"role": "assistant", "text": str(result["summary"])[:32000]},
                    )
                    break
                self.store.event(
                    f"Worker attempt {attempt + 1} failed; bounded retry limit {self.profile.gemini.retries}",
                    key,
                )
            self.store.patch(key, pid=None, result=json.dumps(result))
            if not result["passed"]:
                self.record_failure(key, result)
                return
            self.store.transition(key, State.VERIFYING)
            await self.verify(key, approved_launch=True)
        except asyncio.CancelledError:
            row = self.store.row(key)
            if row["state"] in {
                State.QUEUED,
                State.PLANNING,
                State.IN_PROGRESS,
                State.VERIFYING,
            }:
                self.store.transition(key, self.stop_states.pop(key, State.CANCELLED))
            self.store.patch(key, pid=None)
            raise
        except Exception as exc:
            row = self.store.row(key)
            self.store.event(str(exc), key)
            self.store.run_record(key, "conversation", {"role": "system", "text": str(exc)[:8000]})
            if row["state"] in {State.PLANNING, State.IN_PROGRESS, State.VERIFYING}:
                self.store.transition(key, State.FAILED)
            elif row["state"] == State.QUEUED:
                self.store.transition(key, State.BLOCKED)
            self.store.patch(key, pid=None)
        finally:
            self.board()

    async def model_turn(
        self,
        key: str,
        git: Git,
        argv: list[str],
        prompt: str | None,
        kind: str,
        attempt: int,
        session_id: str,
    ) -> dict[str, Any]:
        """Run one supervised model process and validate its structured connected output."""
        log = self.runtime / "logs" / f"{key}-{kind}-{uuid.uuid4().hex[:12]}-{attempt}.log"
        started = datetime.now(UTC).isoformat()

        def on_start(pid: int, token: str) -> None:
            self.store.patch(
                key,
                pid=pid,
                process_token=token,
                result=json.dumps(
                    {
                        "log": str(log),
                        "started_at": started,
                        "attempt": attempt + 1,
                        "session_id": session_id,
                    }
                ),
            )

        self.supervisor.storage_roots[key] = [scratch(self.runtime, key)]
        result = await self.supervisor.run(
            key,
            argv,
            git.root,
            self.profile.gemini.timeout,
            log,
            on_start,
            prompt,
            env=environment(scratch(self.runtime, key), model=self.profile.mode == "connected")
            | (
                {
                    "GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(self.runtime / "worker-settings.json"),
                    "GEMINI_CLI_SYSTEM_DEFAULTS_PATH": str(self.runtime / "worker-settings.json"),
                }
                if self.profile.mode == "connected"
                else {}
            ),
        )
        result["started_at"] = started
        result["session_id"] = session_id
        if self.profile.mode == "connected" and result["passed"]:
            try:
                output = json.loads(log.read_text())
                if output.get("error") or not isinstance(output.get("response"), str):
                    raise ValueError("Missing response or Gemini error")
                result["summary"] = output["response"]
                returned_id = output.get("session_id", "")
                if returned_id:
                    returned_id = str(uuid.UUID(returned_id))
                    if session_id and returned_id != session_id:
                        raise ValueError("Gemini resumed a different session")
                    result["session_id"] = returned_id
            except (ValueError, OSError) as exc:
                result["passed"] = False
                result["failure"] = f"Invalid structured Gemini result: {exc}"
        return result

    def record_failure(self, key: str, result: dict[str, Any]) -> None:
        self.store.run_record(
            key,
            "conversation",
            {
                "role": "system",
                "text": str(
                    result.get("failure")
                    or result.get("storage_error")
                    or "Worker failed or timed out. Inspect the log before retrying."
                ),
            },
        )
        self.store.transition(key, State.FAILED)

    async def plan_ticket(self, key: str, git: Git) -> None:
        """Read-only model turn: draft an implementation plan for human review."""
        self.store.transition(key, State.PLANNING)
        ticket = self.store.ticket(key)
        comments = self.plan_comments(key) + json.loads(self.store.row(key)["feedback"])
        session_id = self.worker_session_id(key)
        if not self.conversation(key):
            self.store.run_record(
                key,
                "conversation",
                {
                    "role": "user",
                    "text": f"Plan {key}: {ticket.summary}\n{ticket.description}"[:16000],
                },
            )
        before = git.fingerprint()
        result: dict[str, Any] = {}
        for attempt in range(self.profile.gemini.retries + 1):
            if self.profile.mode == "demo":
                text = synthetic_plan(ticket, comments)
                argv, prompt = [sys.executable, "-c", f"print({text!r})"], None
            else:
                argv, prompt = await asyncio.to_thread(
                    Gemini(self.profile.gemini.executable).worker,
                    self.profile,
                    ticket,
                    git.root,
                    self.runtime,
                    comments,
                    session_id=session_id,
                    conversation=self.conversation(key),
                    planning=True,
                )
            result = await self.model_turn(key, git, argv, prompt, "plan", attempt, session_id)
            if self.profile.mode == "demo" and result["passed"]:
                result["summary"] = await asyncio.to_thread(Path(str(result["log"])).read_text)
            self.store.run_record(key, "planning", result)
            if result["passed"]:
                session_id = str(result["session_id"])
                break
            self.store.event(
                f"Planning attempt {attempt + 1} failed; bounded retry limit {self.profile.gemini.retries}",
                key,
            )
        self.store.patch(key, pid=None, result=json.dumps(result))
        if result["passed"] and git.fingerprint() != before:
            result["passed"] = False
            result["failure"] = "Worktree changed during read-only planning; inspect it first"
        if not result["passed"] or not str(result.get("summary", "")).strip():
            result.setdefault("failure", "Planning returned no plan; inspect the log")
            self.record_failure(key, result)
            return
        text = str(result["summary"]).strip()[:32000]
        fingerprint = digest({"key": key, "plan": text, "ticket": digest(ticket), "tree": before})
        with self.store.transaction():
            self.store.run_record(
                key,
                "implementation_plan",
                {
                    "text": text,
                    "fingerprint": fingerprint,
                    "ticket": digest(ticket),
                    "tree": before,
                    "session_id": session_id,
                },
            )
            self.store.run_record(
                key, "conversation", {"role": "assistant", "text": text, "mode": "plan"}
            )
            self.store.request("implementation-plan", key, fingerprint)
            self.store.transition(key, State.PLAN_REVIEW)
            self.store.event("Implementation plan ready; approve, comment or reject it", key)

    def implementation_plan(self, key: str) -> dict[str, Any] | None:
        """The latest proposed plan with the human decision on it, if any."""
        self.store.ticket(key)
        latest: dict[str, Any] | None = None
        for record in self.store.db.execute(
            "SELECT id,kind,data,created FROM runs WHERE ticket=? AND kind IN ('implementation_plan','plan_decision') ORDER BY id",
            (key,),
        ):
            data = json.loads(record["data"])
            if record["kind"] == "implementation_plan":
                latest = {**data, "id": record["id"], "created": record["created"]}
                latest["decision"] = None
            elif latest is not None and data["fingerprint"] == latest["fingerprint"]:
                latest["decision"] = data
        return latest

    def plan_comments(self, key: str) -> list[str]:
        comments = []
        for record in self.store.db.execute(
            "SELECT data FROM runs WHERE ticket=? AND kind='plan_decision' ORDER BY id", (key,)
        ):
            data = json.loads(record[0])
            if data["comment"]:
                comments.append(f"{data['actor']} ({data['decision']}): {data['comment']}")
        return comments[-10:]

    def approved_implementation(self, key: str) -> str | None:
        plan = self.implementation_plan(key)
        if (
            plan
            and plan["decision"]
            and plan["decision"]["decision"] == "approve"
            and plan["ticket"] == digest(self.store.ticket(key))
        ):
            return str(plan["text"])
        return None

    def decide_plan(
        self, key: str, decision: str, actor: str, fingerprint: str, comment: str = ""
    ) -> bool:
        """Record a human approve/comment/reject decision on a proposed implementation plan.

        Returns True when the caller should start execution to continue the ticket.
        """
        if decision not in {"approve", "comment", "reject"}:
            raise OrchestratorError("Choose approve, comment or reject")
        comment = comment.strip()
        if decision == "comment" and not comment:
            raise OrchestratorError("Write a comment for Gemini to address in its revised plan")
        if len(comment) > 8000 or "\x00" in comment:
            raise OrchestratorError("Comments must be at most 8000 characters")
        self.store.require_human(actor)
        row = self.store.row(key)
        plan = self.implementation_plan(key)
        if row["state"] != State.PLAN_REVIEW or plan is None or plan["decision"]:
            raise OrchestratorError(f"{key} has no implementation plan awaiting review")
        if plan["fingerprint"] != fingerprint:
            raise OrchestratorError("The plan changed; load and read the current plan first")
        schedule = self.store.plan()
        if decision != "reject":
            if key not in schedule.tickets:
                raise OrchestratorError(f"Select {key} and start it again to continue")
            self.validate_plan(schedule)
            self.store.require("plan", schedule.id, schedule.fingerprint)
        if decision == "approve":
            if plan["ticket"] != digest(self.store.ticket(key)):
                raise OrchestratorError(
                    "The Jira ticket changed since planning; comment to re-plan"
                )
            if self.validate_worktree(row).fingerprint() != plan["tree"]:
                raise OrchestratorError("The worktree changed since planning; comment to re-plan")
        with self.store.transaction():
            if decision == "approve":
                self.store.grant(self.store.request("implementation-plan", key, fingerprint), actor)
                self.store.require("implementation-plan", key, fingerprint, consume=True)
            else:
                self.store.db.execute(
                    "UPDATE approvals SET consumed=1 WHERE action='implementation-plan' AND subject=?",
                    (key,),
                )
            self.store.run_record(
                key,
                "plan_decision",
                {
                    "decision": decision,
                    "comment": comment,
                    "actor": actor,
                    "fingerprint": fingerprint,
                },
            )
            message = {
                "approve": "Approved the plan; implement it.",
                "comment": comment,
                "reject": "Rejected the plan." + (f" {comment}" if comment else ""),
            }[decision]
            self.store.run_record(
                key, "conversation", {"role": "user", "text": message, "mode": "plan"}
            )
            self.store.transition(
                key, State.CANCELLED if decision == "reject" else State.QUEUED, human=actor
            )
            self.store.event(
                {
                    "approve": f"Human {actor} approved the implementation plan",
                    "comment": f"Human {actor} commented; Gemini will revise the plan",
                    "reject": f"Human {actor} rejected the plan; worktree preserved",
                }[decision],
                key,
            )
        self.board()
        if decision == "reject":
            return False
        if self.executing:
            self.requeued.add(key)
            if self.wakeup:
                self.wakeup.set()
            return False
        # Approving or commenting is the human authorization to continue this schedule.
        self.store.grant(
            self.store.request("launch", schedule.id, self.launch_fingerprint(schedule)), actor
        )
        return True

    def conversation(self, key: str) -> list[dict[str, Any]]:
        self.store.ticket(key)
        records = self.store.db.execute(
            "SELECT id,data,created FROM runs WHERE ticket=? AND kind='conversation' ORDER BY id DESC LIMIT 100",
            (key,),
        ).fetchall()
        recent = []
        remaining = 60000
        for row in records:
            item = json.loads(row["data"])
            text = str(item["text"])[-min(16000, remaining) :]
            recent.append({**item, "text": text, "id": row["id"], "created": row["created"]})
            remaining -= len(text)
            if remaining <= 0:
                break
        return list(reversed(recent))

    def worker_session_id(self, key: str) -> str:
        result = json.loads(self.store.row(key)["result"] or "{}")
        if result.get("session_id"):
            return str(uuid.UUID(result["session_id"]))
        records = self.store.db.execute(
            "SELECT data FROM runs WHERE ticket=? AND kind='worker' ORDER BY id DESC LIMIT 100",
            (key,),
        )
        for record in records:
            value = json.loads(record[0]).get("session_id")
            if value:
                return str(uuid.UUID(value))
        return ""

    def session_snapshot(self, key: str) -> dict[str, Any]:
        row = self.store.row(key)
        if not row["worktree"]:
            raise OrchestratorError(
                "This ticket has no worker worktree yet; approve and launch it first"
            )
        git = self.validate_worktree(row)
        worker = json.loads(row["result"] or "{}")
        recent = ""
        log = Path(worker["log"]) if worker.get("log") else None
        if (
            log
            and not log.is_symlink()
            and log.is_file()
            and log.parent.resolve() == (self.runtime / "logs").resolve()
        ):
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 3000))
                recent = stream.read().decode(errors="replace")
        return {
            "key": key,
            "summary": self.store.ticket(key).summary,
            "state": row["state"],
            "branch": row["branch"],
            "worktree": str(git.root),
            "session_id": self.worker_session_id(key),
            "mode": self.profile.mode,
            "messages": self.conversation(key),
            "recent_log": recent,
            "busy": self.executing or bool(self.supervisor.running),
            "can_pause": row["state"] in {State.IN_PROGRESS, State.VERIFYING} and key in self.tasks,
            "verification": json.loads(row["verification"] or "{}"),
        }

    def session_plan(self, key: str) -> Plan:
        row = self.store.row(key)
        # Follow-ups reuse a previously human-approved ticket scope, never an unapproved plan.
        for record in self.store.db.execute(
            "SELECT p.data FROM plans p WHERE EXISTS (SELECT 1 FROM approvals a WHERE a.action='plan' AND a.subject=p.id AND a.granted=1) ORDER BY p.rowid DESC"
        ):
            plan = Plan.model_validate_json(record[0])
            if (
                plan.config_hash == digest(self.profile)
                and plan.tickets.get(key) == digest(self.store.ticket(key))
                and plan.branches.get(key) == row["branch"]
            ):
                return plan
        raise OrchestratorError(
            "Ticket or settings changed, or no approved scope exists. Generate and approve a fresh plan first"
        )

    def session_preview(self, key: str, message: str) -> dict[str, Any]:
        if not message.strip() or len(message) > 8000 or "\x00" in message:
            raise OrchestratorError("Enter a message between 1 and 8000 characters")
        if self.executing or self.supervisor.running or self.tasks:
            raise OrchestratorError(
                "Wait for the current batch to finish, or pause its workers, before sending a follow-up"
            )
        row = self.store.row(key)
        if row["state"] not in {
            State.READY_FOR_REVIEW,
            State.CHANGES_REQUESTED,
            State.APPROVED,
            State.MR_READY,
            State.PAUSED,
            State.INTERRUPTED,
            State.FAILED,
            State.CANCELLED,
        }:
            raise OrchestratorError(
                "Choose a completed or paused worker session before sending a follow-up"
            )
        git = self.validate_worktree(row)
        plan = self.session_plan(key)
        expected_base = self.prerequisite_base(key, plan)
        if not Git(self.profile.repository).ancestor(expected_base, git.run("rev-parse", "HEAD")):
            raise OrchestratorError(
                "Prerequisites changed; integrate them and approve a fresh plan first"
            )
        return {
            "key": key,
            "message": message.strip(),
            "history": digest(row["feedback"]),
            "conversation_id": self.store.db.execute(
                "SELECT COALESCE(MAX(id),0) FROM runs WHERE ticket=? AND kind='conversation'",
                (key,),
            ).fetchone()[0],
            "turn": self.store.db.execute(
                "SELECT COALESCE(MAX(id),0) FROM runs WHERE ticket=? AND kind='worker'", (key,)
            ).fetchone()[0],
            "state": row["state"],
            "branch": row["branch"],
            "worktree": str(git.root),
            "tree": git.fingerprint(),
            "session_id": self.worker_session_id(key),
            "plan": plan.id,
            "scope": plan.fingerprint,
            "config": digest(self.profile),
            "checks": [check.model_dump() for check in self.profile.checks],
        }

    def approve_session_turn(self, key: str, message: str, actor: str, fingerprint: str) -> None:
        if digest(self.session_preview(key, message)) != fingerprint:
            raise OrchestratorError("Session context changed; preview the follow-up again")
        self.store.grant(self.store.request("session-turn", key, fingerprint), actor)

    def launch_session_turn(
        self, key: str, message: str, *, connected_enabled: bool = False
    ) -> asyncio.Task[None]:
        if self.profile.mode == "connected" and not connected_enabled:
            raise OrchestratorError(
                "Connected session messages require --enable-connected in an approved environment"
            )
        preview = self.session_preview(key, message)
        self.store.require("session-turn", key, digest(preview), consume=True)
        actor = self.profile.display_name
        plan = self.session_plan(key)
        with self.store.transaction():
            if self.store.row(key)["state"] in {
                State.READY_FOR_REVIEW,
                State.APPROVED,
                State.MR_READY,
            }:
                self.feedback(key, preview["message"], actor)
            else:
                history = json.loads(self.store.row(key)["feedback"])
                history.append(f"{actor}: {preview['message']}")
                self.store.patch(key, feedback=json.dumps(history))
                self.invalidate_descendants(self.descendants(key))
            self.store.db.execute(
                "UPDATE approvals SET consumed=1 WHERE subject=? OR action='launch'", (key,)
            )
            self.store.patch(key, verification=None)
            self.store.transition(key, State.QUEUED)
            self.store.run_record(key, "conversation", {"role": "user", "text": preview["message"]})
            self.store.event(
                "Human approved session follow-up; prior implementation acceptance invalidated", key
            )
        self.executing = True

        async def turn() -> None:
            try:
                await self.run_ticket(key, plan)
            finally:
                self.executing = False
                self.tasks.pop(key, None)
                self.board()

        task = asyncio.create_task(turn())
        self.tasks[key] = task
        self.board()
        return task

    async def ask(self, key: str, question: str, *, connected_enabled: bool = False) -> str:
        preview = self.session_preview(key, question)
        self.store.require("ask", key, digest(preview), consume=True)
        if self.profile.mode == "connected" and not connected_enabled:
            raise OrchestratorError("Read-only model questions require --enable-connected")
        git = self.validate_worktree(self.store.row(key))
        before = git.fingerprint()
        self.executing = True
        try:
            if self.profile.mode == "demo":
                answer = "Synthetic read-only answer: inspect the recorded implementation and verification. No files were changed."
            else:
                argv, prompt = await asyncio.to_thread(
                    Gemini(self.profile.gemini.executable).worker,
                    self.profile,
                    self.store.ticket(key),
                    git.root,
                    self.runtime,
                    [question],
                    conversation=self.conversation(key),
                    read_only=True,
                )
                home = scratch(self.runtime, key, "ask")
                env = environment(home, model=True) | {
                    "GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(self.runtime / "worker-settings.json"),
                    "GEMINI_CLI_SYSTEM_DEFAULTS_PATH": str(self.runtime / "worker-settings.json"),
                }
                log = self.runtime / "logs" / f"{key}-ask-{uuid.uuid4().hex[:12]}.log"
                self.supervisor.storage_roots[f"{key}:ask"] = [home]
                result = await self.supervisor.run(
                    f"{key}:ask",
                    argv,
                    git.root,
                    self.profile.gemini.timeout,
                    log,
                    stdin=prompt,
                    env=env,
                )
                self.store.run_record(key, "question", result)
                if not result["passed"]:
                    raise OrchestratorError("Read-only question failed; inspect its bounded log")
                output = json.loads(log.read_text())
                if output.get("error") or not isinstance(output.get("response"), str):
                    raise OrchestratorError("Invalid read-only question response")
                answer = output["response"][:32000]
            if git.fingerprint() != before:
                raise OrchestratorError(
                    "Worktree changed during the read-only question; reverify and review it"
                )
            self.store.run_record(
                key, "conversation", {"role": "user", "text": question, "mode": "ask"}
            )
            self.store.run_record(
                key, "conversation", {"role": "assistant", "text": answer, "mode": "ask"}
            )
            return answer
        finally:
            self.executing = False
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
            argv, env = wrap(self.profile, check.argv, git.root, self.runtime, key)
            self.supervisor.storage_roots[f"{key}:verify"] = [
                scratch(self.runtime, key, "verification")
            ]
            result = await self.supervisor.run(
                f"{key}:verify",
                argv,
                git.root,
                check.timeout,
                self.runtime / "logs" / f"{key}-check-{uuid.uuid4().hex[:12]}-{index}.log",
                env=env,
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
        if self.wakeup:
            self.wakeup.set()
        self.board()

    def descendants(self, key: str) -> set[str]:
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
        return descendants

    def invalidate_descendants(self, descendants: set[str]) -> None:
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

    def feedback(self, key: str, message: str, actor: str) -> None:
        if not message.strip() or not actor.strip():
            raise OrchestratorError("Provide human feedback before requesting changes")
        descendants = self.descendants(key)
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
        self.invalidate_descendants(descendants)
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
