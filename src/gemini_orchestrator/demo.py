from __future__ import annotations

import sys

from .config import Profiles, atomic_write
from .git import command
from .models import Check, Identity, Profile, Resource, Ticket


def demo_tickets() -> list[Ticket]:
    specs = [
        ("DEMO-1", "Add accessible command help", [], ["src/help.py"]),
        ("DEMO-2", "Document local installation", [], ["docs/install.md"]),
        ("DEMO-3", "Add examples to command help", ["DEMO-1"], ["src/help.py"]),
        ("DEMO-4", "Improve help error messages", [], ["src/help.py"]),
        ("DEMO-5", "Add diagnostic output", [], ["src/diagnostics.py"]),
        ("DEMO-6", "Connect unavailable identity provider", ["DEMO-99"], ["src/auth.py"]),
    ]
    return [
        Ticket(
            key=key,
            summary=summary,
            description=f"Synthetic task: {summary}. No enterprise systems are used.",
            acceptance=[
                f"Implement {summary.lower()} with a small documented example.",
                "Keep the sample application compilable.",
            ],
            assignee="demo-alex",
            sprint_id="101",
            project_id="100",
            priority="High" if key in {"DEMO-1", "DEMO-6"} else "Medium",
            dependencies=dependencies,
            files=files,
        )
        for key, summary, dependencies, files in specs
    ]


def create_demo(profiles: Profiles, name: str = "demo") -> Profile:
    if name in profiles.names():
        existing = profiles.load(name)
        if existing.mode != "demo":
            from .models import OrchestratorError

            raise OrchestratorError(
                "This profile name belongs to connected mode; choose another demo name"
            )
        return existing
    runtime = profiles.runtime(name)
    repository = runtime / "sample-repository"
    repository.mkdir(mode=0o700)
    command(["git", "init", "-b", "main", str(repository)])
    (repository / "src").mkdir()
    (repository / "docs").mkdir()
    (repository / "README.md").write_text(
        "# Synthetic sample\n\n## Roadmap\nBuild command help, diagnostics and documentation.\n"
    )
    (repository / "GEMINI.md").write_text(
        "Keep changes focused. Do not contact external systems.\n"
    )
    (repository / "src/__init__.py").write_text('"""Synthetic sample application."""\n')
    (repository / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    command(["git", "-C", str(repository), "add", "."])
    command(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Demo User",
            "-c",
            "user.email=demo@localhost",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-m",
            "Initialize synthetic demo",
        ]
    )
    profile = Profile(
        name=name,
        mode="demo",
        display_name="Alex Morgan (synthetic)",
        identity=Identity(account_id="demo-alex", display_name="Alex Morgan"),
        team="Demo Platform Team",
        projects=[Resource(id="100", name="Synthetic Platform")],
        boards=[Resource(id="10", name="Demo Sprint Board")],
        board_id="10",
        sprint=Resource(id="101", name="Synthetic Sprint 1", board_id="10"),
        repository=repository,
        worktrees=runtime / "worktrees",
        checks=[
            Check(
                name="Sample Python compilation",
                argv=[sys.executable, "-m", "compileall", "-q", "src"],
            )
        ],
    )
    profiles.save(profile)
    # Demo deliberately does not switch the user's connected active profile.
    atomic_write(
        runtime / "DEMO.txt",
        "All ticket data, identities and code in this directory are synthetic.\n",
    )
    return profile
