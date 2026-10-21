from __future__ import annotations

import re
import uuid
from pathlib import Path

from .git import Git
from .models import Edge, OrchestratorError, Plan, Profile, Ticket, digest


def repository_context(repository: Path) -> dict[str, str]:
    git = Git(repository)
    context = {
        "branches": git.run(
            "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"
        )[:12000]
    }
    for name in (
        "README.md",
        "GEMINI.md",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
    ):
        path = repository / name
        if path.is_file() and not path.is_symlink():
            context[name] = path.read_text(errors="replace")[:16000]
    context["source_tree"] = git.run("ls-files")[:20000]
    return context


def create_plan(profile: Profile, tickets: list[Ticket]) -> Plan:
    if not tickets:
        raise OrchestratorError("Select at least one ticket")
    git = Git(profile.repository)
    base = git.validate()["head"]
    if not git.clean():
        raise OrchestratorError(
            "Main checkout is dirty; commit or stash your own changes before planning"
        )
    selected = {ticket.key: ticket for ticket in tickets}
    edges: list[Edge] = []
    blocked: dict[str, str] = {}
    dependencies: dict[str, set[str]] = {}
    conflicts: set[frozenset[str]] = set()
    for ticket in tickets:
        dependencies[ticket.key] = set(ticket.dependencies) & selected.keys()
        missing = set(ticket.dependencies) - selected.keys()
        if missing:
            blocked[ticket.key] = "Prerequisite outside selection: " + ", ".join(sorted(missing))
        for dep in ticket.dependencies:
            edges.append(
                Edge(
                    prerequisite=dep,
                    dependent=ticket.key,
                    reason=ticket.dependency_reasons.get(dep, "Jira blocks/is blocked by link"),
                )
            )
        # Textual recommendations remain advisory and never change the dependency DAG.
        for dep in re.findall(r"\b[A-Z][A-Z0-9_]*-\d+\b", " ".join(ticket.acceptance)):
            if dep != ticket.key and dep not in ticket.dependencies:
                edges.append(
                    Edge(
                        prerequisite=dep,
                        dependent=ticket.key,
                        kind="unknown",
                        reason="Acceptance criteria mention this ticket; confirm before changing dependencies",
                    )
                )
        for other in tickets:
            overlap = set(ticket.files) & set(other.files)
            if ticket.key < other.key and (
                overlap or other.key in ticket.conflicts or ticket.key in other.conflicts
            ):
                conflicts.add(frozenset((ticket.key, other.key)))
                edges.append(
                    Edge(
                        prerequisite=ticket.key,
                        dependent=other.key,
                        kind="potential conflict",
                        reason="Shared source paths: " + ", ".join(sorted(overlap))
                        if overlap
                        else "Explicit conflict metadata",
                    )
                )
    remaining = set(selected)
    finished: set[str] = set()
    groups: list[list[str]] = []
    while remaining:
        ready = sorted(key for key in remaining if dependencies[key] <= finished)
        if not ready:
            for key in remaining:
                blocked[key] = "Dependency cycle or dependency on a cycle"
            break
        group: list[str] = []
        for key in ready:
            if any(dep in blocked for dep in dependencies[key]):
                blocked[key] = "Prerequisite is blocked"
            if (
                key not in blocked
                and len(group) < profile.gemini.concurrency
                and not any(frozenset((key, active)) in conflicts for active in group)
            ):
                group.append(key)
        excluded = [key for key in ready if key in blocked]
        if group:
            groups.append(group)
        processed = set(group + excluded)
        remaining -= processed
        finished |= processed
    context = repository_context(profile.repository)
    for ticket in tickets:
        for relative in ticket.files:
            candidate = (profile.repository / relative).resolve()
            if (
                candidate.is_relative_to(profile.repository)
                and candidate.is_file()
                and not (profile.repository / relative).is_symlink()
            ):
                context[f"source:{relative}"] = candidate.read_text(errors="replace")[:16000]
    plan_id = uuid.uuid4().hex[:12]
    branches = {key: f"gso/{key.lower()}-{plan_id[:6]}" for key in selected}
    return Plan(
        id=plan_id,
        base=base,
        config_hash=digest(profile),
        tickets={key: digest(ticket) for key, ticket in selected.items()},
        groups=groups,
        blocked=blocked,
        edges=edges,
        branches=branches,
        worktrees={
            key: str(profile.worktrees / f"{key.lower()}-{plan_id[:6]}") for key in selected
        },
        checks=profile.checks,
        context=context,
    )
