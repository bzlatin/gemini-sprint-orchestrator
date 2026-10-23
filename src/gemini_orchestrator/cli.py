from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from .config import Profiles
from .dashboard import Dashboard
from .demo import create_demo
from .engine import Engine, profile_scope
from .hosting import perform_draft, perform_push, remote_fingerprint, remote_preview
from .jira import JiraConnection, validate_identity
from .models import OrchestratorError, Plan, Profile, State
from .setup import diagnostics, wizard

CORE = "Core workflow"
ADVANCED = "Advanced"
app = typer.Typer(
    help="Pick Jira tickets, let Gemini plan each one, approve the plan, then review the code. "
    "Start with demo or setup.",
    no_args_is_help=False,
    pretty_exceptions_enable=False,
)
profile_app = typer.Typer(help="Manage local, isolated configuration profiles.")
jira_app = typer.Typer(help="Read-only Jira diagnostics. Requires explicit connected access.")
app.add_typer(profile_app, name="profile", rich_help_panel=ADVANCED)
app.add_typer(jira_app, name="jira", rich_help_panel=ADVANCED)
console = Console(markup=False)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    profile: Annotated[
        str | None, typer.Option("--profile", help="Profile name; otherwise use the active profile")
    ] = None,
    home: Annotated[
        Path | None, typer.Option("--home", help="Local configuration/runtime root (or GSO_HOME)")
    ] = None,
) -> None:
    ctx.obj = {"profiles": Profiles(home), "profile": profile}
    if ctx.invoked_subcommand is None:
        profiles = ctx.obj["profiles"]
        try:
            profiles.load(profile)
        except OrchestratorError:
            if Confirm.ask(
                "No valid configuration. Start the interactive setup wizard?", default=True
            ):
                wizard(profiles, profile)
            return
        console.print(ctx.get_help())


def registry(ctx: typer.Context) -> Profiles:
    return ctx.obj["profiles"]  # type: ignore[no-any-return]


def configured(ctx: typer.Context) -> Profile:
    return registry(ctx).load(ctx.obj["profile"])


@contextmanager
def session(ctx: typer.Context) -> Iterator[Engine]:
    profile = configured(ctx)
    engine = Engine(profile, registry(ctx).runtime(profile.name))
    try:
        yield engine
    finally:
        engine.close()


ALLOW = "Contact Jira/Gemini for this command (or allow once: profile connected --enable)"


def connected_access(profile: Profile, flag: bool) -> bool:
    """Connected profiles contact Jira and Gemini only with one-time profile consent or the flag."""
    if profile.mode != "connected":
        return flag
    if not (flag or profile.connected_access):
        raise OrchestratorError(
            "This profile may not contact Jira or Gemini yet. On your approved work system, run "
            "`gemini-orchestrator profile connected --enable` once (or pass --enable-connected)"
        )
    return True


def human_confirm(message: str) -> None:
    if not Confirm.ask(message, default=False):
        raise OrchestratorError("Human action declined; no approval granted")


def printable(text: str) -> str:
    """Model and Jira text may carry terminal escape sequences; show them inert."""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "�", text)


def next_step(engine: Engine, row: dict[str, object]) -> str:
    key, state = str(row["key"]), row["state"]
    if state == State.PLAN_REVIEW:
        return f"plan {key}"
    if state == State.READY_FOR_REVIEW:
        return f"review {key}"
    if state in {State.BLOCKED, State.NOT_SELECTED, State.SELECTED}:
        readiness = engine.readiness(key)
        return f"work {key}" if readiness == "Ready" and state == State.BLOCKED else readiness
    if state in {State.FAILED, State.INTERRUPTED, State.CHANGES_REQUESTED, State.CANCELLED}:
        return f"retry {key}"
    return "—"


def show_status(engine: Engine) -> None:
    table = Table(title=f"{engine.profile.name} · {engine.profile.mode}")
    for column in ("Ticket", "Summary", "Lifecycle", "Branch", "Next"):
        table.add_column(column)
    for row in engine.store.rows():
        table.add_row(
            row["key"],
            printable(engine.store.ticket(row["key"]).summary),
            row["state"],
            row["branch"] or "—",
            next_step(engine, row),
        )
    console.print(table)
    console.print(f"Local board: {engine.board()}")


def show_schedule(engine: Engine, plan: Plan) -> None:
    table = Table(title="Schedule")
    for column in ("Ticket", "Readiness", "Branch", "Worktree"):
        table.add_column(column)
    for key in plan.tickets:
        table.add_row(
            key,
            plan.blocked.get(key) or engine.readiness(key),
            plan.branches[key],
            plan.worktrees[key],
        )
    console.print(table)
    for edge in plan.edges:
        console.print(f"{edge.prerequisite} → {edge.dependent} [{edge.kind}] {edge.reason}")
    console.print(
        "Verification: "
        + (json.dumps([check.argv for check in plan.checks]) if plan.checks else "manual")
    )


def parse_branches(values: list[str]) -> dict[str, str]:
    branches = {}
    for value in values:
        key, separator, name = value.partition("=")
        if not separator or not name.strip():
            raise OrchestratorError("Use --branch KEY=branch-name")
        branches[key.strip().upper()] = name.strip()
    return branches


def begin(ctx: typer.Context, keys: list[str], branches: list[str], enable_connected: bool) -> None:
    keys = [key.strip().upper() for key in keys]
    with session(ctx) as engine:
        enable_connected = connected_access(engine.profile, enable_connected)
        preview = engine.preview(
            keys, branches=parse_branches(branches), plan_id=uuid.uuid4().hex[:12]
        )
        show_schedule(engine, preview)
        step = (
            "Gemini drafts a read-only implementation plan for each ready ticket and waits for your approval before editing code"
            if engine.profile.gemini.plan_first
            else "Gemini implements each ready ticket in its worktree"
        )
        human_confirm(f"Start {', '.join(keys)} with these branches? {step}.")
        engine.start(keys, engine.profile.display_name, preview)
        asyncio.run(engine.execute(connected_enabled=enable_connected))
        show_status(engine)


@app.command(rich_help_panel=CORE)
def setup(ctx: typer.Context, name: str | None = None) -> None:
    """Interactive onboarding; remote access is optional and explicitly confirmed."""
    wizard(registry(ctx), name or ctx.obj["profile"])


@app.command(rich_help_panel=CORE)
def demo(ctx: typer.Context, name: str = "demo", headless: bool = False) -> None:
    """Launch the synthetic demo dashboard; no external integrations or credentials."""
    profiles = registry(ctx)
    profile = create_demo(profiles, name)
    engine = Engine(profile, profiles.runtime(name))
    try:
        if not engine.store.rows():
            asyncio.run(engine.discover())
        if headless:
            show_status(engine)
        else:
            Dashboard(engine, profiles).run()
    finally:
        engine.close()


@app.command(rich_help_panel=CORE)
def start(
    ctx: typer.Context, enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False
) -> None:
    """Open the connected dashboard in an approved environment."""
    with session(ctx) as engine:
        if engine.profile.mode != "connected":
            raise OrchestratorError("Use demo for synthetic profiles")
        connected_access(engine.profile, enable_connected)
        asyncio.run(engine.discover(connected_enabled=True))
        Dashboard(engine, registry(ctx), connected_enabled=True).run()


@app.command("worker-home", rich_help_panel=ADVANCED)
def worker_home(ctx: typer.Context, key: str) -> None:
    """Print this ticket's isolated provider home for approved authentication; no credentials copied."""
    from .isolation import scratch

    with session(ctx) as engine:
        engine.store.ticket(key)
        console.print(str(scratch(engine.runtime, key)))


@app.command(rich_help_panel=ADVANCED)
def ask(
    ctx: typer.Context,
    key: str,
    question: str,
    enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False,
) -> None:
    """Ask about a completed worker without granting coding or shell tools. Close the dashboard first."""
    from .models import digest

    with session(ctx) as engine:
        enable_connected = connected_access(engine.profile, enable_connected)
        preview = engine.session_preview(key, question)
        console.print(json.dumps(preview, indent=2))
        human_confirm(
            "Send this read-only question to the model? No coding turn or native-session resume is authorized"
        )
        engine.store.grant(
            engine.store.request("ask", key, digest(preview)), engine.profile.display_name
        )
        console.print(asyncio.run(engine.ask(key, question, connected_enabled=enable_connected)))


@app.command(rich_help_panel=ADVANCED)
def storage(
    ctx: typer.Context, days: Annotated[int, typer.Option(min=1)] = 30, prune_logs: bool = False
) -> None:
    """Show bounded disk usage; optionally confirm removal of old non-current logs only."""
    from .storage import old_logs, usage

    with session(ctx) as engine:
        limits = engine.profile.isolation
        try:
            console.print(usage(engine.runtime, limits.max_runtime_bytes, limits.max_files))
        except OrchestratorError as exc:
            console.print(str(exc))
        current: set[str] = set()
        for row in engine.store.rows():
            for column in ("result", "verification"):
                value = json.loads(row[column] or "{}")
                for record in [value, *value.get("results", [])]:
                    if record.get("log"):
                        current.add(record["log"])
        candidates = old_logs(engine.runtime, days, current)
        console.print(json.dumps(candidates, indent=2))
        if prune_logs and candidates:
            human_confirm(
                "Delete exactly these historical logs? Current logs, database, messages and worktrees are preserved"
            )
            if old_logs(engine.runtime, days, current) != candidates:
                raise OrchestratorError("Log candidates changed; review storage again")
            for item in candidates:
                (engine.runtime / "logs" / str(item["name"])).unlink()
            engine.store.event(
                f"Human removed {len(candidates)} historical logs older than {days} days"
            )


@app.command(rich_help_panel=CORE)
def doctor(ctx: typer.Context) -> None:
    """Check local tooling and profile configuration without contacting Jira."""
    console.print(json.dumps(diagnostics(configured(ctx)), indent=2, default=str))


@jira_app.command("test")
def jira_test(
    ctx: typer.Context, enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False
) -> None:
    """Validate read-only capabilities and exact authenticated identity."""
    profile = configured(ctx)
    if not connected_access(profile, enable_connected):
        raise OrchestratorError(
            "Jira access requires --enable-connected on an approved work system"
        )

    async def check() -> None:
        async with JiraConnection(profile) as jira:
            identity = await validate_identity(jira, profile)
            console.print(identity.model_dump_json(indent=2))
            console.print(f"Read-only discovery: {len(await jira.issues(profile))} scoped issues")

    asyncio.run(check())


@app.command(rich_help_panel=CORE)
def tickets(
    ctx: typer.Context,
    refresh: bool = False,
    enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False,
    priority: str = "",
    status: str = "",
    issue_type: str = "",
    as_json: Annotated[bool, typer.Option("--json", help="Print full ticket data")] = False,
) -> None:
    """List your sprint tickets and whether each is ready or waiting on prerequisites."""
    with session(ctx) as engine:
        if refresh:
            asyncio.run(
                engine.discover(
                    connected_enabled=connected_access(engine.profile, enable_connected)
                )
            )
        tickets = [
            ticket
            for ticket in (engine.store.ticket(row["key"]) for row in engine.current_rows())
            if (not priority or ticket.priority.casefold() == priority.casefold())
            and (not status or ticket.status.casefold() == status.casefold())
            and (not issue_type or ticket.issue_type.casefold() == issue_type.casefold())
        ]
        if as_json:
            console.print(json.dumps([ticket.model_dump() for ticket in tickets], indent=2))
            return
        table = Table(title="Sprint tickets")
        for column in ("Ticket", "Readiness", "Lifecycle", "Priority", "Summary"):
            table.add_column(column)
        for ticket in tickets:
            table.add_row(
                ticket.key,
                engine.readiness(ticket.key),
                engine.store.row(ticket.key)["state"],
                ticket.priority,
                printable(ticket.summary),
            )
        console.print(table)
        console.print("Start ready work with: gemini-orchestrator work KEY [KEY...]")


@app.command(rich_help_panel=CORE)
def work(
    ctx: typer.Context,
    keys: list[str],
    branch: Annotated[
        list[str], typer.Option("--branch", help="Override a branch name: KEY=name")
    ] = [],  # noqa: B006
    enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False,
) -> None:
    """Start tickets: review branches and readiness once, then Gemini plans each ready ticket."""
    begin(ctx, keys, branch, enable_connected)


@app.command("plan", rich_help_panel=CORE)
def implementation_plan(
    ctx: typer.Context,
    key: str,
    approve: bool = False,
    comment: Annotated[str, typer.Option(help="Ask Gemini to revise the plan")] = "",
    reject: bool = False,
    enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False,
) -> None:
    """Read Gemini's implementation plan, then approve it, comment to refine it, or reject it."""
    key = key.strip().upper()
    if approve and (comment or reject):
        raise OrchestratorError("Approve, comment or reject; choose one")
    with session(ctx) as engine:
        plan = engine.implementation_plan(key)
        if plan is None:
            raise OrchestratorError(f"{key} has no implementation plan yet; start it with work")
        console.print(f"{key} · {engine.store.row(key)['state']} · plan #{plan['id']}\n")
        console.print(printable(str(plan["text"])))
        decision = plan["decision"]
        if decision:
            console.print(f"\nDecision: {decision['decision']} by {decision['actor']}")
        if not (approve or comment or reject):
            if engine.store.row(key)["state"] == State.PLAN_REVIEW:
                console.print(
                    f"\nNext: plan {key} --approve | --comment TEXT | --reject [--comment REASON]"
                )
            return
        choice = "reject" if reject else "approve" if approve else "comment"
        if choice != "reject":
            enable_connected = connected_access(engine.profile, enable_connected)
        human_confirm(
            {
                "approve": f"Approve this plan and let Gemini implement {key} in its worktree?",
                "comment": f"Send this comment and have Gemini revise the {key} plan?\n{comment}",
                "reject": f"Reject this plan and stop {key}? Its worktree is preserved.",
            }[choice]
        )
        if engine.decide_plan(
            key, choice, engine.profile.display_name, str(plan["fingerprint"]), comment
        ):
            asyncio.run(engine.execute(connected_enabled=enable_connected))
        show_status(engine)


@app.command("select", rich_help_panel=ADVANCED)
def select_tickets(ctx: typer.Context, keys: list[str]) -> None:
    """Explicitly select ticket keys, persisting the human selection approval."""
    with session(ctx) as engine:
        human_confirm("Select exactly these tickets: " + ", ".join(keys) + "?")
        engine.select(keys, engine.profile.display_name)
        show_status(engine)


@app.command(rich_help_panel=ADVANCED)
def schedule(
    ctx: typer.Context,
    approve: bool = False,
    semantic: bool = False,
    enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False,
) -> None:
    """Generate the dependency schedule for selected tickets. --approve adds a human gate."""
    with session(ctx) as engine:
        proposed = (
            asyncio.run(
                engine.propose_semantic(
                    connected_enabled=connected_access(engine.profile, enable_connected)
                )
            )
            if semantic
            else engine.propose()
        )
        console.print(proposed.model_dump_json(indent=2))
        if approve:
            human_confirm(f"Approve plan {proposed.id} ({proposed.fingerprint})?")
            engine.approve_plan(engine.profile.display_name)


@app.command(rich_help_panel=ADVANCED)
def dependencies(
    ctx: typer.Context,
    key: str,
    requires: list[str] = typer.Option([], "--requires"),
    conflicts: list[str] = typer.Option([], "--conflicts"),
    files: list[str] = typer.Option([], "--file"),
) -> None:
    """Explicitly edit local confirmed dependencies, conflicts and relevant paths."""
    with session(ctx) as engine:
        console.print(
            json.dumps(
                {"ticket": key, "requires": requires, "conflicts": conflicts, "files": files},
                indent=2,
            )
        )
        human_confirm("Save this local planning context and invalidate prior plan approvals?")
        engine.set_dependencies(key, requires, conflicts, files, engine.profile.display_name)


@app.command(rich_help_panel=ADVANCED)
def run(
    ctx: typer.Context, enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False
) -> None:
    """Approve and launch an already-approved plan's queued workers."""
    with session(ctx) as engine:
        plan = engine.store.plan()
        console.print(plan.model_dump_json(indent=2))
        human_confirm(
            f"Launch workers for plan {plan.id} and run exactly its verification commands?"
        )
        engine.approve_launch(engine.profile.display_name)
        asyncio.run(
            engine.execute(connected_enabled=connected_access(engine.profile, enable_connected))
        )
        show_status(engine)


@app.command(rich_help_panel=CORE)
def status(ctx: typer.Context) -> None:
    """Show persisted task states and regenerate the Markdown board."""
    with session(ctx) as engine:
        show_status(engine)


@app.command("session", rich_help_panel=ADVANCED)
def worker_session(ctx: typer.Context, key: str) -> None:
    """Open a shared worker conversation while its main dashboard is running."""
    from .session_ui import SessionConsole

    profile = configured(ctx)
    descriptor = (
        registry(ctx).runtime(profile.name)
        / "sessions"
        / profile_scope(profile)
        / "conversation-bridge.json"
    )
    if not descriptor.is_file():
        raise OrchestratorError(
            "Start the demo or connected dashboard first, then open its worker session"
        )
    SessionConsole(descriptor, key.strip().upper()).run()


@app.command(rich_help_panel=ADVANCED)
def board(ctx: typer.Context) -> None:
    """Regenerate TASK_BOARD.md atomically in this profile's runtime directory."""
    with session(ctx) as engine:
        console.print(str(engine.board()))


@app.command(rich_help_panel=ADVANCED)
def approvals(ctx: typer.Context) -> None:
    """List durable approval requests and their action fingerprints."""
    with session(ctx) as engine:
        console.print(json.dumps(engine.store.approvals(), indent=2))


@app.command(rich_help_panel=CORE)
def review(
    ctx: typer.Context,
    key: str,
    accept: bool = False,
    request_changes: str = "",
    verify: bool = False,
    manual_verified: bool = False,
    full_diff_reviewed: bool = False,
) -> None:
    """Inspect review evidence, rerun checks, accept, or request changes."""
    if sum([accept, bool(request_changes), verify]) > 1:
        raise OrchestratorError("Choose one review action at a time")
    with session(ctx) as engine:
        package = engine.review(key)
        console.print(json.dumps(package, indent=2))
        if verify:
            console.print(
                json.dumps([check.model_dump() for check in engine.profile.checks], indent=2)
            )
            human_confirm("Execute exactly these verification commands for this implementation?")
            engine.approve_verification(key, engine.profile.display_name)
            asyncio.run(engine.verify(key))
        elif request_changes:
            human_confirm(f"Request changes for {key}: {request_changes}?")
            engine.feedback(key, request_changes, engine.profile.display_name)
        elif accept:
            if manual_verified:
                human_confirm(
                    "Attest that you manually verified this implementation despite missing/failed automated checks?"
                )
            if full_diff_reviewed:
                human_confirm(
                    "Attest that you inspected all binary/large changes in the worktree outside the abbreviated terminal diff?"
                )
            human_confirm(
                f"Accept this exact {key} implementation and create a local branch commit?"
            )
            engine.accept(
                key,
                engine.profile.display_name,
                manual_verified=manual_verified,
                expected_fingerprint=package["fingerprint"],
                full_diff_reviewed=full_diff_reviewed,
            )


@app.command(rich_help_panel=CORE)
def retry(
    ctx: typer.Context,
    key: str,
    enable_connected: Annotated[bool, typer.Option(help=ALLOW)] = False,
) -> None:
    """Continue failed, interrupted or change-requested work in its existing worktree."""
    begin(ctx, [key], [], enable_connected)


@app.command(rich_help_panel=CORE)
def cancel(ctx: typer.Context, key: str) -> None:
    """Cancel queued work, preserving its branch and worktree."""
    with session(ctx) as engine:
        human_confirm(f"Cancel {key}? Its work will be preserved.")
        asyncio.run(engine.cancel(key))


@app.command(rich_help_panel=ADVANCED)
def push(ctx: typer.Context, key: str) -> None:
    """Approve a specific accepted branch push; remote writes must be enabled."""
    with session(ctx) as engine:
        fingerprint = remote_fingerprint(engine, key, "push")
        console.print(json.dumps(remote_preview(engine, key, "push"), indent=2))
        console.print(f"Approval fingerprint {fingerprint}")
        human_confirm("Push this exact accepted commit to this repository's origin?")
        engine.store.grant(
            engine.store.request("push", key, fingerprint), engine.profile.display_name
        )
        perform_push(engine, key)


@app.command(rich_help_panel=ADVANCED)
def draft(ctx: typer.Context, key: str, target: str) -> None:
    """Separately approve creation of a draft MR/PR after an exact-commit push."""
    with session(ctx) as engine:
        fingerprint = remote_fingerprint(engine, key, "draft", target)
        console.print(json.dumps(remote_preview(engine, key, "draft", target), indent=2))
        console.print(f"Fingerprint {fingerprint}")
        human_confirm("Create the ticket-linked draft merge/pull request?")
        engine.store.grant(
            engine.store.request("draft", key, fingerprint), engine.profile.display_name
        )
        console.print(perform_draft(engine, key, target))


@profile_app.command("create")
def profile_create(ctx: typer.Context, name: str | None = None) -> None:
    """Create a separate local configuration using interactive onboarding."""
    wizard(registry(ctx), name)


@profile_app.command("list")
def profile_list(ctx: typer.Context) -> None:
    """List profile names without printing credential or integration details."""
    profiles = registry(ctx)
    try:
        active = profiles.active()
    except OrchestratorError:
        active = ""
    for name in profiles.names():
        console.print(f"{'*' if name == active else ' '} {name}")


@profile_app.command("switch")
def profile_switch(ctx: typer.Context, name: str) -> None:
    """Change the default local profile."""
    registry(ctx).switch(name)
    console.print(f"Active profile: {name}")


@profile_app.command("edit")
def profile_edit(ctx: typer.Context, name: str | None = None) -> None:
    """Edit an existing profile through the setup wizard."""
    existing = registry(ctx).load(name or ctx.obj["profile"])
    wizard(registry(ctx), existing.name, existing=existing)


@profile_app.command("connected")
def profile_connected(
    ctx: typer.Context,
    enable: Annotated[bool, typer.Option("--enable/--disable")] = True,
) -> None:
    """Allow or stop this profile contacting Jira and Gemini, so commands need no flag."""
    profile = configured(ctx)
    if profile.mode != "connected":
        raise OrchestratorError("Demo profiles never contact Jira or Gemini")
    if enable:
        human_confirm(
            f"Allow profile {profile.name} to contact Jira and Gemini from this machine without "
            "--enable-connected? Every consequential action still asks for your approval."
        )
    profile.connected_access = enable
    registry(ctx).save(profile)
    console.print(
        f"Profile {profile.name}: Jira and Gemini access "
        + ("allowed on this machine." if enable else "off; commands need --enable-connected.")
    )


@profile_app.command("remote-writes")
def profile_remote(ctx: typer.Context, enabled: bool = False) -> None:
    """Enable/disable optional remote writes; each write still needs separate approval."""
    profile = configured(ctx)
    if enabled:
        human_confirm(
            f"Enable remote-write features for profile {profile.name}? Each push and draft request remains separately gated."
        )
    profile.remote_writes = enabled
    registry(ctx).save(profile)


@app.command("test", rich_help_panel=ADVANCED)
def validate_application() -> None:
    """Offline validation: real Git, synthetic discovery, planning, SQLite and board."""
    with tempfile.TemporaryDirectory(prefix="gso-validation-") as directory:
        profiles = Profiles(Path(directory))
        profile = create_demo(profiles)
        engine = Engine(profile, profiles.runtime(profile.name))
        try:
            asyncio.run(engine.discover())
            assert len(engine.store.rows()) >= 6
            assert GitValidation(profile)
            console.print(
                "PASS: package imports, synthetic discovery, profile persistence, Git worktrees capability, SQLite and Markdown generation.\nFor the full lifecycle/security suite: python -m pytest"
            )
        finally:
            engine.close()


def GitValidation(profile: Profile) -> bool:
    from .git import Git

    return bool(Git(profile.repository).validate())


def main() -> None:
    try:
        app()
    except (OrchestratorError, ValueError, OSError) as exc:
        console.print(f"Error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
