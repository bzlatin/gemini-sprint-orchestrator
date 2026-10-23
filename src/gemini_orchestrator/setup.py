from __future__ import annotations

import asyncio
import json
import shlex
import shutil
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt

from .config import Profiles
from .gemini import Gemini
from .git import Git, command
from .jira import Jira, JiraConnection
from .models import (
    Check,
    Identity,
    OrchestratorError,
    Profile,
    Resource,
)

console = Console(markup=False)


def select_resource(
    label: str, resources: list[Resource], *, board_id: str | None = None
) -> Resource:
    filtered = resources
    if len(resources) > 8:
        query = Prompt.ask(f"Search {label} by name (blank shows all)", default="")
        filtered = [
            resource for resource in resources if query.casefold() in resource.name.casefold()
        ]
    for resource in filtered:
        console.print(f"  {resource.id}: {resource.name}")
    if not resources:
        console.print(f"No {label.lower()} discovery available; enter a stable ID manually.")
    default = filtered[0].id if len(filtered) == 1 else "manual" if not resources else None
    value = Prompt.ask(f"{label} ID (or 'manual')", default=default)
    if value == "manual":
        identifier = ""
        while not identifier:
            identifier = Prompt.ask(f"Stable {label} ID").strip()
            if not identifier:
                console.print("A non-empty stable ID is required.")
        return Resource(
            id=identifier,
            name=Prompt.ask(f"{label} display name"),
            board_id=board_id,
        )
    for resource in resources:
        if resource.id == value:
            if Confirm.ask(f"Use {resource.name} ({resource.id})?", default=True):
                return resource
            return select_resource(label, resources, board_id=board_id)
    console.print("Choose a listed identifier, or enter manual.")
    return select_resource(label, resources, board_id=board_id)


async def configure_jira(profile: Profile) -> None:
    async with JiraConnection(profile) as jira:
        await configure_connected_resources(profile, jira)


async def configure_connected_resources(profile: Profile, jira: Jira) -> None:
    identity = await jira.identity()
    if not identity.account_id:
        raise OrchestratorError("The integration did not return a stable account identity")
    console.print(
        f"Connected Jira account: {identity.display_name} / {identity.email or identity.account_id}"
    )
    if not Confirm.ask("Is this your Jira account?", default=False):
        raise OrchestratorError("Identity not confirmed; Jira discovery stopped")
    profile.identity = identity
    try:
        projects = await jira.projects()
    except OrchestratorError as exc:
        console.print(str(exc))
        projects = []
    profile.projects = [select_resource("Project", projects)]
    while Confirm.ask("Add another project?", default=False):
        resource = select_resource("Project", projects)
        if resource.id not in {p.id for p in profile.projects}:
            profile.projects.append(resource)
    try:
        boards = await jira.boards()
    except OrchestratorError as exc:
        console.print(str(exc))
        boards = []
    chosen = select_resource("Board", boards)
    profile.boards = [chosen]
    profile.board_id = chosen.id
    while Confirm.ask("Add another board to this profile?", default=False):
        resource = select_resource("Board", boards)
        if resource.id not in {b.id for b in profile.boards}:
            profile.boards.append(resource)
    while True:
        try:
            sprints = await jira.sprints(chosen.id)
        except OrchestratorError as exc:
            console.print(str(exc))
            sprints = []
        profile.sprint = select_resource("Sprint", sprints, board_id=chosen.id)
        profile.sprint.board_id = chosen.id
        if not Confirm.ask("Refresh sprint discovery?", default=False):
            break
    sprint_choice = Prompt.ask(
        "Sprint behavior", choices=["active", "manual"], default="active" if sprints else "manual"
    )
    profile.sprint_behavior = "manual" if sprint_choice == "manual" else "active"


def configure_mcp_settings(profile: Profile) -> bool:
    """Validate local settings before any optional service connection."""
    while True:
        value = Prompt.ask(
            "Gemini MCP settings file (or 'later')", default=str(profile.jira.settings_path)
        ).strip()
        if value == "later":
            profile.identity = Identity()
            return False
        path = Path(value).expanduser()
        try:
            cfg = json.loads(path.read_text())
            servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
            if not isinstance(servers, dict):
                raise ValueError("Missing mcpServers object")
            names = [
                name
                for name, server in servers.items()
                if name.strip() and isinstance(server, dict) and server
            ]
            if not names:
                raise ValueError("No configured MCP servers")
        except (OSError, ValueError) as exc:
            console.print(f"Settings unavailable: {exc}. Choose another file, or enter later.")
            continue
        console.print("Configured server names: " + ", ".join(names))
        jira_like = [n for n in names if any(word in n.lower() for word in ("jira", "atlassian"))]
        default = (
            profile.jira.server
            if profile.jira.server in names
            else names[0]
            if len(names) == 1
            else jira_like[0]
            if len(jira_like) == 1
            else None
        )
        while True:
            server = (Prompt.ask("Exact Jira MCP server name", default=default) or "").strip()
            if server in names:
                break
            console.print("Choose a non-empty exact server name from the list above.")
        if path != profile.jira.settings_path or server != profile.jira.server:
            profile.identity = Identity()
        profile.jira.settings_path, profile.jira.server = path, server
        profile.jira.cloud_id = Prompt.ask(
            "Cloud ID if required by tools (leave blank for Server/Data Center)",
            default=profile.jira.cloud_id,
        ).strip()
        return True


def tooling(repository: Path) -> list[str]:
    hints: list[str] = []
    for filename, commands in {
        "pyproject.toml": ["python -m pytest", "ruff check .", "mypy src"],
        "package.json": ["npm test", "npm run build"],
        "Cargo.toml": ["cargo test", "cargo clippy"],
        "go.mod": ["go test ./..."],
    }.items():
        if (repository / filename).exists():
            hints.extend(commands)
    return hints


def configure_checks(profile: Profile) -> None:
    # Only suggest commands whose program exists; nothing runs during setup.
    suggestions = [
        hint
        for hint in tooling(profile.repository)
        if shlex.split(hint)[0] in {"python", "python3"} or shutil.which(shlex.split(hint)[0])
    ]
    checks: list[Check] = []
    if suggestions:
        console.print("Suggested verification checks (not run now):")
        for hint in suggestions:
            console.print(f"  {hint}")
        if Confirm.ask(
            "Use these checks? They run sandboxed, without network, after you approve work",
            default=True,
        ):
            checks = [Check(name=hint, argv=shlex.split(hint)) for hint in suggestions]
    while Confirm.ask("Add a custom verification check?", default=False):
        name = Prompt.ask("Check name (unit / lint / types / build / smoke)")
        argv = shlex.split(Prompt.ask("Command (arguments only; no shell expansion)"))
        checks.append(
            Check(
                name=name,
                argv=argv,
                required=Confirm.ask("Required?", default=True),
                timeout=typer.prompt("Timeout seconds", default=300, type=int),
            )
        )
    profile.checks = checks


def git_setting(key: str) -> str:
    try:
        return command(["git", "config", "--get", key])
    except OrchestratorError:
        return ""


def current_repository() -> Path:
    try:
        return Path(command(["git", "rev-parse", "--show-toplevel"], cwd=Path.cwd()))
    except OrchestratorError:
        return Path.cwd()


def configure_gemini(profile: Profile) -> None:
    executable = shutil.which(profile.gemini.executable) or shutil.which("gemini")
    if not executable:
        executable = Prompt.ask(
            "Gemini CLI was not found on PATH. Path to the gemini executable", default="gemini"
        )
    profile.gemini.executable = executable
    try:
        capabilities = Gemini(executable).inspect()
        missing = [flag for flag in ("prompt", "json", "admin_policy") if not capabilities[flag]]
        console.print(
            f"Gemini CLI {capabilities['version']} at {executable}"
            + (f"; missing worker support: {', '.join(missing)}" if missing else "")
        )
    except OrchestratorError as exc:
        console.print(
            f"Gemini unavailable: {exc}. Install and sign in to Gemini CLI, then run doctor."
        )


def configure_branches(profile: Profile) -> None:
    while True:
        template = Prompt.ask(
            "Branch naming ({key} {slug} {type} {id}; you approve every name before work starts)",
            default=profile.branch_template,
        )
        try:
            profile.branch_template = Profile.known_branch_fields(template)
            return
        except ValueError as exc:
            console.print(str(exc))


def connect_jira(profile: Profile) -> None:
    default = "mcp" if profile.jira.settings_path.is_file() else "later"
    adapter = Prompt.ask("Jira connection", choices=["mcp", "rest", "later"], default=default)
    if adapter == "later":
        return
    profile.jira.adapter = "rest" if adapter == "rest" else "mcp"
    if adapter == "mcp":
        console.print("Choose the exact Jira MCP server from your Gemini settings.")
        configured = configure_mcp_settings(profile)
    else:
        configured = True
        profile.jira.base_url = Prompt.ask("Jira HTTPS base URL", default=profile.jira.base_url)
        console.print(
            "Credentials stay in environment variables; this wizard never asks for tokens."
        )
        profile.jira.token_env = Prompt.ask(
            "Token environment variable", default=profile.jira.token_env
        )
        profile.jira.email_env = Prompt.ask(
            "Email environment variable (optional for bearer)", default=profile.jira.email_env
        )
    while configured and Confirm.ask(
        "Verify your Jira identity and choose your board and sprint now? This contacts Jira.",
        default=True,
    ):
        try:
            asyncio.run(configure_jira(profile))
            break
        except Exception as exc:
            console.print(f"Jira verification failed: {exc}. Retry, or continue and connect later.")


def configure_access(profile: Profile) -> None:
    profile.connected_access = Confirm.ask(
        "Allow this profile to contact Jira and Gemini from this machine, so commands need no "
        "--enable-connected flag? Every consequential action still asks for your approval",
        default=bool(profile.identity.account_id),
    )


def summary(profile: Profile) -> str:
    jira = (
        f"{profile.jira.adapter} {profile.jira.server or profile.jira.base_url or ''}".strip()
        if profile.identity.account_id or profile.jira.server or profile.jira.base_url
        else "not connected"
    )
    identity = profile.identity.display_name or profile.identity.account_id
    return "\n".join(
        [
            f"Profile {profile.name}",
            f"  You:         {profile.display_name}"
            + (f" · team {profile.team}" if profile.team else ""),
            f"  Repository:  {profile.repository}",
            f"  Worktrees:   {profile.worktrees}",
            f"  Gemini:      {profile.gemini.executable} · {profile.gemini.concurrency} parallel · "
            f"{profile.gemini.timeout}s timeout · "
            + ("plan first" if profile.gemini.plan_first else "implement directly"),
            f"  Branches:    {profile.branch_template}",
            f"  Jira:        {jira}"
            + (f" · verified as {identity}" if identity else " · identity not verified")
            + (f" · board {profile.board_id}" if profile.board_id else "")
            + (f" · sprint {profile.sprint.name}" if profile.sprint else ""),
            "  Access:      "
            + (
                "may contact Jira and Gemini on this machine"
                if profile.connected_access
                else "off; commands need --enable-connected"
            ),
            "  Checks:      " + (", ".join(c.name for c in profile.checks) or "manual review only"),
            f"  Hosting:     {profile.hosting} (remote writes off)",
        ]
    )


def wizard(
    profiles: Profiles, name: str | None = None, *, existing: Profile | None = None
) -> Profile:
    console.print(
        "Welcome to Gemini Sprint Orchestrator. A few questions; everything else has a safe "
        "default you can change before saving. Setup never launches coding agents."
    )
    name = name or Prompt.ask("Profile name", default=existing.name if existing else "personal")
    assert name is not None
    if not existing and name in profiles.names():
        raise OrchestratorError("Profile exists; use profile edit")
    display_name = Prompt.ask(
        "What should we call you?",
        default=existing.display_name if existing else git_setting("user.name") or "Developer",
    )
    repository = (
        Path(
            Prompt.ask(
                "Git repository to work in",
                default=str(existing.repository if existing else current_repository()),
            )
        )
        .expanduser()
        .resolve()
    )
    Git(repository).validate()
    worktrees = (
        existing.worktrees
        if existing and existing.repository == repository
        else repository.parent / f"{repository.name}-worktrees"
    )
    profile = (
        existing.model_copy(deep=True)
        if existing
        else Profile(
            name=name, display_name=display_name, repository=repository, worktrees=worktrees
        )
    )
    profile.display_name = display_name
    profile.repository, profile.worktrees = repository, worktrees.resolve()
    profile.mode = "connected"
    profile.gemini.reuse_skills = False
    configure_gemini(profile)
    configure_branches(profile)
    connect_jira(profile)
    if not profile.identity.account_id:
        console.print(
            "Jira identity is not verified yet; ticket retrieval stays off until you connect."
        )
    if not profile.connected_access:
        configure_access(profile)
    configure_checks(profile)
    profile.remote_writes = False
    while True:
        console.print(summary(profile))
        field = Prompt.ask(
            "Save, or change a setting",
            choices=[
                "save",
                "cancel",
                "identity",
                "team",
                "jira",
                "access",
                "repository",
                "gemini",
                "branches",
                "checks",
                "hosting",
            ],
            default="save",
        )
        if field == "save":
            break
        if field == "cancel":
            raise OrchestratorError("Configuration was not saved")
        if field == "identity":
            profile.display_name = Prompt.ask("Display name", default=profile.display_name)
            profile.email = (
                Prompt.ask("Optional email address", default=profile.email or "") or None
            )
        elif field == "team":
            profile.team = Prompt.ask("Team (separate from board/project)", default=profile.team)
        elif field == "jira":
            connect_jira(profile)
        elif field == "access":
            configure_access(profile)
        elif field == "repository":
            profile.repository = Path(Prompt.ask("Repository path")).expanduser().resolve()
            Git(profile.repository).validate()
            profile.worktrees = (
                Path(
                    Prompt.ask(
                        "Worktree directory (outside the checkout)",
                        default=str(
                            profile.repository.parent / f"{profile.repository.name}-worktrees"
                        ),
                    )
                )
                .expanduser()
                .resolve()
            )
        elif field == "gemini":
            configure_gemini(profile)
            profile.gemini.concurrency = typer.prompt(
                "Maximum parallel agents", default=profile.gemini.concurrency, type=int
            )
            profile.gemini.timeout = typer.prompt(
                "Worker timeout seconds", default=profile.gemini.timeout, type=int
            )
            profile.gemini.retries = typer.prompt(
                "Bounded worker retries", default=profile.gemini.retries, type=int
            )
            profile.gemini.behavior = Prompt.ask(
                "Preferred worker behavior", default=profile.gemini.behavior
            )
            profile.gemini.use_instructions = Confirm.ask(
                "Follow the repository's GEMINI.md?", default=profile.gemini.use_instructions
            )
            profile.gemini.plan_first = Confirm.ask(
                "Plan first and wait for approval before coding?",
                default=profile.gemini.plan_first,
            )
        elif field == "branches":
            configure_branches(profile)
        elif field == "checks":
            configure_checks(profile)
        elif field == "hosting":
            profile.hosting = Prompt.ask(
                "Git hosting", choices=["local", "github", "gitlab"], default=profile.hosting
            )
            tool = {"github": "gh", "gitlab": "glab"}.get(profile.hosting)
            if tool:
                console.print(f"{tool}: {shutil.which(tool) or 'not installed'}")
    profile = Profile.model_validate(profile.model_dump())
    profiles.save(profile)
    profiles.switch(profile.name)
    console.print(f"Saved profile {profile.name} with restrictive local permissions.")
    # Local diagnostics only: no Jira or model access.
    for item, status in diagnostics(profile).items():
        console.print(f"{item}: {status}")
    console.print(
        "Next: gemini-orchestrator start"
        + ("" if profile.connected_access else " --enable-connected")
    )
    return profile


def diagnostics(profile: Profile) -> dict[str, Any]:
    result: dict[str, Any] = {
        "profile": profile.name,
        "mode": profile.mode,
        "jira_identity": "configured; live validation requires explicit connected mode"
        if profile.identity.account_id
        else "missing; run setup",
        "sprint": profile.sprint.name if profile.sprint else "missing",
        "checks": len(profile.checks),
        "remote_writes": profile.remote_writes,
    }
    try:
        result["git"] = Git(profile.repository).validate()
    except OrchestratorError as exc:
        result["git"] = str(exc)
    try:
        result["gemini"] = Gemini(profile.gemini.executable).inspect()
    except OrchestratorError as exc:
        result["gemini"] = str(exc)
    import platform

    result["sandbox"] = (
        shutil.which("sandbox-exec" if platform.system() == "Darwin" else "bwrap")
        or "missing; connected workers disabled"
    )
    return result
