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
from .git import Git
from .jira import Jira, JiraConnection
from .models import (
    Check,
    GeminiSettings,
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
        default = (
            profile.jira.server
            if profile.jira.server in names
            else names[0]
            if len(names) == 1
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
    suggestions = tooling(profile.repository)
    console.print("Detected suggestions (not executed): " + (", ".join(suggestions) or "none"))
    console.print(
        "Verification commands run only after plan and worker-launch approval. Enter executable arguments; no shell expansion."
    )
    checks: list[Check] = []
    while Confirm.ask("Configure a verification check?", default=False):
        name = Prompt.ask(
            "Check name (unit / integration / lint / types / build / smoke / reviewer)"
        )
        argv = shlex.split(Prompt.ask("Command"))
        check = Check(
            name=name,
            argv=argv,
            required=Confirm.ask("Required?", default=True),
            timeout=typer.prompt("Timeout seconds", default=300, type=int),
        )
        checks.append(check)
    profile.checks = checks


def wizard(
    profiles: Profiles, name: str | None = None, *, existing: Profile | None = None
) -> Profile:
    console.print("Welcome to Gemini Sprint Orchestrator.\nSetup never launches coding agents.")
    name = name or Prompt.ask("Profile name", default=existing.name if existing else "personal")
    assert name is not None
    if not existing and name in profiles.names():
        raise OrchestratorError("Profile exists; use profile edit")
    display_name = Prompt.ask(
        "What should we call you?", default=existing.display_name if existing else "Developer"
    )
    email = Prompt.ask("Optional email address", default=(existing.email or "") if existing else "")
    repository = (
        Path(
            Prompt.ask(
                "Existing local Git repository",
                default=str(existing.repository) if existing else str(Path.cwd()),
            )
        )
        .expanduser()
        .resolve()
    )
    Git(repository).validate()
    default_worktrees = repository.parent / f"{repository.name}-worktrees"
    worktrees = Path(
        Prompt.ask(
            "Worktree directory (outside checkout)",
            default=str(existing.worktrees if existing else default_worktrees),
        )
    ).expanduser()
    profile = (
        existing.model_copy(deep=True)
        if existing
        else Profile(
            name=name, display_name=display_name, repository=repository, worktrees=worktrees
        )
    )
    profile.display_name, profile.email = display_name, email or None
    profile.repository, profile.worktrees = repository, worktrees.resolve()
    profile.mode = "connected"
    profile.team = Prompt.ask("Team name (separate from board/project)", default=profile.team)
    executable = Prompt.ask(
        "Gemini executable", default=shutil.which("gemini") or profile.gemini.executable
    )
    try:
        gemini = Gemini(executable)
        console.print(json.dumps(gemini.inspect(), indent=2))
    except OrchestratorError as exc:
        console.print(
            f"Gemini unavailable: {exc}. Install/authenticate Gemini CLI on the approved target system."
        )
    profile.gemini = GeminiSettings(
        executable=executable,
        concurrency=typer.prompt(
            "Maximum parallel agents", default=profile.gemini.concurrency, type=int
        ),
        timeout=typer.prompt("Worker timeout seconds", default=profile.gemini.timeout, type=int),
        retries=typer.prompt("Bounded worker retries", default=profile.gemini.retries, type=int),
        behavior=Prompt.ask("Preferred worker behavior", default=profile.gemini.behavior),
        use_instructions=Confirm.ask("Use existing GEMINI.md?", default=True),
        reuse_skills=False,
    )
    console.print(
        "Custom skills/agents are detected by Gemini itself, but reuse is disabled until their permissions can be safely restricted."
    )
    adapter = Prompt.ask("Jira connection", choices=["mcp", "rest", "later"], default="later")
    if adapter != "later":
        profile.jira.adapter = "rest" if adapter == "rest" else "mcp"
        if adapter == "mcp":
            console.print(
                "MCP connectivity can contact configured services. Use this only in an approved environment."
            )
            configured = configure_mcp_settings(profile)
            if configured and Confirm.ask(
                "Discover MCP servers now in this approved environment?", default=False
            ):
                try:
                    console.print(Gemini(executable).servers())
                except OrchestratorError as exc:
                    console.print(str(exc))
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
            "Verify Jira identity and discover resources now? This accesses the configured service.",
            default=False,
        ):
            try:
                asyncio.run(configure_jira(profile))
                break
            except Exception as exc:
                console.print(
                    f"Jira verification failed: {exc}. Retry or continue configuring local settings."
                )
    if not profile.identity.account_id:
        console.print(
            "Jira identity remains unverified. Ticket retrieval is disabled until setup confirms authentication."
        )
    configure_checks(profile)
    profile.hosting = Prompt.ask(
        "Git hosting", choices=["local", "github", "gitlab"], default=profile.hosting
    )
    tool = {"github": "gh", "gitlab": "glab"}.get(profile.hosting)
    if tool:
        console.print(f"{tool}: {shutil.which(tool) or 'not installed'}")
    profile.remote_writes = False
    while True:
        console.print(profile.model_dump_json(indent=2))
        field = Prompt.ask(
            "Edit a setting before save",
            choices=[
                "save",
                "identity",
                "team",
                "jira",
                "repository",
                "gemini",
                "checks",
                "hosting",
            ],
            default="save",
        )
        if field == "save":
            break
        if field == "identity":
            profile.display_name = Prompt.ask("Display name", default=profile.display_name)
        elif field == "team":
            profile.team = Prompt.ask("Team", default=profile.team)
        elif field == "jira":
            if Confirm.ask("Connect to Jira now in an approved environment?", default=False):
                asyncio.run(configure_jira(profile))
        elif field == "repository":
            profile.repository = Path(Prompt.ask("Repository path")).expanduser().resolve()
            Git(profile.repository).validate()
            profile.worktrees = Path(Prompt.ask("Worktree directory")).expanduser().resolve()
        elif field == "gemini":
            profile.gemini.concurrency = typer.prompt("Concurrency", default=2, type=int)
            profile.gemini.timeout = typer.prompt("Worker timeout", default=1800, type=int)
        elif field == "checks":
            configure_checks(profile)
        elif field == "hosting":
            profile.hosting = Prompt.ask("Hosting", choices=["local", "github", "gitlab"])
    profile = Profile.model_validate(profile.model_dump())
    if not Confirm.ask("Save this local configuration?", default=True):
        raise OrchestratorError("Configuration was not saved")
    profiles.save(profile)
    profiles.switch(profile.name)
    console.print(f"Saved profile {profile.name} with restrictive local permissions.")
    if Confirm.ask("Run local diagnostics now? (No Jira network access)", default=True):
        for name, status in diagnostics(profile).items():
            console.print(f"{name}: {status}")
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
