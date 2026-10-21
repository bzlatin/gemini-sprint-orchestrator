from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .git import command
from .models import OrchestratorError, State, digest


class Hosting:
    def __init__(self, provider: str, repository: Path):
        self.provider = provider
        self.repository = repository

    def read_requests(self) -> list[dict[str, Any]]:
        if self.provider == "github":
            return list(
                json.loads(
                    command(
                        ["gh", "pr", "list", "--json", "number,title,headRefName,url"],
                        self.repository,
                    )
                )
            )
        if self.provider == "gitlab":
            return list(
                json.loads(command(["glab", "mr", "list", "--output", "json"], self.repository))
            )
        return []

    def create_draft(self, branch: str, target: str, title: str, body_file: Path) -> str:
        if self.provider == "github":
            return command(
                [
                    "gh",
                    "pr",
                    "create",
                    "--draft",
                    "--head",
                    branch,
                    "--base",
                    target,
                    "--title",
                    title,
                    "--body-file",
                    str(body_file),
                ],
                self.repository,
            )
        if self.provider == "gitlab":
            # glab accepts the description as one argument, without shell interpretation.
            return command(
                [
                    "glab",
                    "mr",
                    "create",
                    "--draft",
                    "--source-branch",
                    branch,
                    "--target-branch",
                    target,
                    "--title",
                    title,
                    "--description",
                    body_file.read_text(),
                    "--yes",
                ],
                self.repository,
            )
        raise OrchestratorError("Local-only profiles cannot create remote requests")


def remote_fingerprint(engine: Any, key: str, action: str, target: str = "") -> str:
    row = engine.store.row(key)
    if row["state"] not in {State.APPROVED, State.MR_READY}:
        raise OrchestratorError("Human implementation acceptance is required first")
    git = engine.validate_worktree(row)
    result = json.loads(row["result"])
    if not git.clean() or result.get("accepted_fingerprint") != git.fingerprint():
        raise OrchestratorError("Accepted implementation changed; verify and review again")
    if not engine.profile.remote_writes or engine.profile.hosting == "local":
        raise OrchestratorError(
            "Remote writes are disabled; explicitly enable them in profile settings"
        )
    verification = json.loads(row["verification"] or "{}")
    if verification.get("checks_hash") != digest(engine.profile.checks):
        raise OrchestratorError(
            "Verification settings changed; reverify and re-review before remote operations"
        )
    remote = git.run("remote", "get-url", "--push", "origin")
    # Do not execute remote helper/local transport commands from untrusted repository config.
    if not (
        remote.startswith("https://") or remote.startswith("ssh://") or remote.startswith("git@")
    ) or any(c.isspace() for c in remote):
        raise OrchestratorError("Only explicit HTTPS/SSH origin remotes are supported")
    return digest(
        {
            "action": action,
            "key": key,
            "branch": row["branch"],
            "commit": git.run("rev-parse", "HEAD"),
            "remote": remote,
            "provider": engine.profile.hosting,
            "target": target,
            "ticket": engine.store.ticket(key).model_dump(),
        }
    )


def perform_push(engine: Any, key: str) -> None:
    fingerprint = remote_fingerprint(engine, key, "push")
    engine.store.require("push", key, fingerprint, consume=True)
    row = engine.store.row(key)
    git = engine.validate_worktree(row)
    git.run("-c", "core.hooksPath=/dev/null", "push", "origin", f"HEAD:refs/heads/{row['branch']}")
    engine.store.event("Human-approved exact branch pushed", key)
    engine.board()


def perform_draft(engine: Any, key: str, target: str) -> str:
    fingerprint = remote_fingerprint(engine, key, "draft", target)
    engine.store.require("draft", key, fingerprint, consume=True)
    row = engine.store.row(key)
    git = engine.validate_worktree(row)
    remote_line = git.run("ls-remote", "origin", f"refs/heads/{row['branch']}")
    if not remote_line or remote_line.split()[0] != git.run("rev-parse", "HEAD"):
        raise OrchestratorError("Push this exact accepted commit before creating a draft request")
    ticket = engine.store.ticket(key)
    body = engine.runtime / "reviews" / f"{key}-request.md"
    from .config import atomic_write

    verification = json.loads(row["verification"] or "{}")
    atomic_write(
        body,
        f"## {key}: {ticket.summary}\n\n{ticket.description}\n\nVerification: {verification.get('status', 'manual required')}\n\nHuman reviewed implementation. See local review package for detailed evidence.\n",
    )
    url = Hosting(engine.profile.hosting, Path(row["worktree"])).create_draft(
        row["branch"], target, f"{key}: {ticket.summary}", body
    )
    if row["state"] == State.APPROVED:
        engine.store.transition(key, State.MR_READY)
    engine.store.event(f"Human-approved draft request created: {url}", key)
    engine.board()
    return url
