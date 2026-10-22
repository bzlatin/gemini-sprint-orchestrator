from __future__ import annotations

import asyncio
import json
import tempfile
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
from .models import OrchestratorError, Profile
from .setup import diagnostics, wizard

app = typer.Typer(
    help="Local, human-controlled sprint orchestration. Start with demo or setup.",
    no_args_is_help=False,
    pretty_exceptions_enable=False,
)
profile_app = typer.Typer(help="Manage local, isolated configuration profiles.")
jira_app = typer.Typer(help="Read-only Jira diagnostics. Requires explicit connected access.")
app.add_typer(profile_app, name="profile")
app.add_typer(jira_app, name="jira")
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


def human_confirm(message: str) -> None:
    if not Confirm.ask(message, default=False):
        raise OrchestratorError("Human action declined; no approval granted")


def show_status(engine: Engine) -> None:
    table = Table(title=f"{engine.profile.name} · {engine.profile.mode}")
    for column in ("Ticket", "Summary", "Lifecycle", "Branch"):
        table.add_column(column)
    for row in engine.store.rows():
        table.add_row(
            row["key"], engine.store.ticket(row["key"]).summary, row["state"], row["branch"] or "—"
        )
    console.print(table)
    console.print(f"Local board: {engine.board()}")


@app.command()
def setup(ctx: typer.Context, name: str | None = None) -> None:
    """Interactive onboarding; remote access is optional and explicitly confirmed."""
    wizard(registry(ctx), name or ctx.obj["profile"])


@app.command()
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


@app.command()
def start(ctx: typer.Context, enable_connected: bool = False) -> None:
    """Open the connected dashboard in an approved environment."""
    with session(ctx) as engine:
        if engine.profile.mode != "connected":
            raise OrchestratorError("Use demo for synthetic profiles")
        if not enable_connected:
            raise OrchestratorError(
                "Company access is opt-in: run start --enable-connected on your approved work system"
            )
        asyncio.run(engine.discover(connected_enabled=True))
        Dashboard(engine, registry(ctx), connected_enabled=True).run()


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check local tooling and profile configuration without contacting Jira."""
    console.print(json.dumps(diagnostics(configured(ctx)), indent=2, default=str))


@jira_app.command("test")
def jira_test(ctx: typer.Context, enable_connected: bool = False) -> None:
    """Validate read-only capabilities and exact authenticated identity."""
    if not enable_connected:
        raise OrchestratorError(
            "Jira access requires --enable-connected on an approved work system"
        )
    profile = configured(ctx)

    async def check() -> None:
        async with JiraConnection(profile) as jira:
            identity = await validate_identity(jira, profile)
            console.print(identity.model_dump_json(indent=2))
            console.print(f"Read-only discovery: {len(await jira.issues(profile))} scoped issues")

    asyncio.run(check())


@app.command()
def tickets(
    ctx: typer.Context,
    refresh: bool = False,
    enable_connected: bool = False,
    priority: str = "",
    status: str = "",
    issue_type: str = "",
) -> None:
    """List cached assigned tickets; optionally refresh approved Jira access."""
    with session(ctx) as engine:
        if refresh:
            asyncio.run(engine.discover(connected_enabled=enable_connected))
        tickets = [engine.store.ticket(row["key"]) for row in engine.current_rows()]
        console.print(
            json.dumps(
                [
                    ticket.model_dump()
                    for ticket in tickets
                    if (not priority or ticket.priority.casefold() == priority.casefold())
                    and (not status or ticket.status.casefold() == status.casefold())
                    and (not issue_type or ticket.issue_type.casefold() == issue_type.casefold())
                ],
                indent=2,
            )
        )


@app.command("select")
def select_tickets(ctx: typer.Context, keys: list[str]) -> None:
    """Explicitly select ticket keys, persisting the human selection approval."""
    with session(ctx) as engine:
        human_confirm("Select exactly these tickets: " + ", ".join(keys) + "?")
        engine.select(keys, engine.profile.display_name)
        show_status(engine)


@app.command()
def plan(
    ctx: typer.Context,
    approve: bool = False,
    semantic: bool = False,
    enable_connected: bool = False,
) -> None:
    """Generate/revise an execution plan. --approve adds a separate human gate."""
    with session(ctx) as engine:
        proposed = (
            asyncio.run(engine.propose_semantic(connected_enabled=enable_connected))
            if semantic
            else engine.propose()
        )
        console.print(proposed.model_dump_json(indent=2))
        if approve:
            human_confirm(f"Approve plan {proposed.id} ({proposed.fingerprint})?")
            engine.approve_plan(engine.profile.display_name)


@app.command()
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


@app.command()
def run(ctx: typer.Context, enable_connected: bool = False) -> None:
    """Approve and launch an already-approved plan's queued workers."""
    with session(ctx) as engine:
        plan = engine.store.plan()
        console.print(plan.model_dump_json(indent=2))
        human_confirm(
            f"Launch workers for plan {plan.id} and run exactly its verification commands?"
        )
        engine.approve_launch(engine.profile.display_name)
        asyncio.run(engine.execute(connected_enabled=enable_connected))
        show_status(engine)


@app.command()
def status(ctx: typer.Context) -> None:
    """Show persisted task states and regenerate the Markdown board."""
    with session(ctx) as engine:
        show_status(engine)


@app.command("session")
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


@app.command()
def board(ctx: typer.Context) -> None:
    """Regenerate TASK_BOARD.md atomically in this profile's runtime directory."""
    with session(ctx) as engine:
        console.print(str(engine.board()))


@app.command()
def approvals(ctx: typer.Context) -> None:
    """List durable approval requests and their action fingerprints."""
    with session(ctx) as engine:
        console.print(json.dumps(engine.store.approvals(), indent=2))


@app.command()
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


@app.command()
def retry(ctx: typer.Context, key: str) -> None:
    """Reselect failed/interrupted/change-requested work for a fresh approved plan."""
    with session(ctx) as engine:
        human_confirm(f"Continue {key} in its existing worktree after a fresh plan?")
        engine.select([key], engine.profile.display_name)
        proposed = engine.propose()
        console.print(proposed.model_dump_json(indent=2))


@app.command()
def cancel(ctx: typer.Context, key: str) -> None:
    """Cancel queued work, preserving its branch and worktree."""
    with session(ctx) as engine:
        human_confirm(f"Cancel {key}? Its work will be preserved.")
        asyncio.run(engine.cancel(key))


@app.command()
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


@app.command()
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


@app.command("test")
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
