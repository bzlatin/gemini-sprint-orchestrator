from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import atomic_write
from .git import command
from .models import OrchestratorError, State, digest


def repository_slug(remote: str) -> str:
    """Normalize supported origin URLs to an explicit host/owner/repository target."""
    if remote.startswith("git@"):
        match = re.fullmatch(
            r"git@([A-Za-z0-9.-]+):([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", remote
        )
        if not match:
            raise OrchestratorError("Unsupported SSH origin; use git@host:owner/repository.git")
        return f"{match[1]}/{match[2]}"
    parsed = urlparse(remote)
    if (
        parsed.scheme not in {"https", "ssh"}
        or not parsed.hostname
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "https" and parsed.username)
    ):
        raise OrchestratorError(
            "Use an HTTPS/SSH origin without embedded credentials, query or fragment"
        )
    if parsed.port is not None or parsed.username not in {None, "git"}:
        raise OrchestratorError("Custom SSH users/ports need a validated hosting adapter")
    path = parsed.path.removeprefix("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", path):
        raise OrchestratorError("Origin must identify a hosting owner and repository")
    return f"{parsed.hostname}/{path}"


class Hosting:
    def __init__(self, provider: str, repository: Path, target: str | None = None):
        self.provider = provider
        self.repository = repository
        self.target = target

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
        # Actual mutation paths pass the exact approved repository, never a mutable cwd origin.
        explicit = ["--repo", self.target] if self.target else []
        if self.provider == "github":
            return command(
                [
                    "gh",
                    "pr",
                    "create",
                    *explicit,
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
            return command(
                [
                    "glab",
                    "mr",
                    "create",
                    *explicit,
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


def remote_preview(engine: Any, key: str, action: str, target: str = "") -> dict[str, Any]:
    row = engine.store.row(key)
    if row["state"] not in {State.APPROVED, State.MR_READY}:
        raise OrchestratorError("Human implementation acceptance is required first")
    git = engine.validate_worktree(row)
    result = json.loads(row["result"])
    commit = git.run("rev-parse", "HEAD")
    if (
        not git.clean()
        or result.get("accepted_fingerprint") != git.fingerprint()
        or result.get("accepted_commit") != commit
    ):
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
    slug = repository_slug(remote)
    if action == "draft":
        command(["git", "check-ref-format", "--branch", target])
    ticket = engine.store.ticket(key)
    title = f"{key}: {ticket.summary}"
    body = f"## {title}\n\n{ticket.description}\n\nVerification: {verification.get('status', 'manual required')}\n\nHuman reviewed implementation. See local review package for detailed evidence.\n"
    return {
        "action": action,
        "key": key,
        "branch": row["branch"],
        "commit": commit,
        "remote": remote,
        "repository": slug,
        "provider": engine.profile.hosting,
        "target": target,
        "title": title,
        "body": body,
    }


def remote_fingerprint(engine: Any, key: str, action: str, target: str = "") -> str:
    return digest(remote_preview(engine, key, action, target))


def perform_push(engine: Any, key: str) -> None:
    preview = remote_preview(engine, key, "push")
    engine.store.require("push", key, digest(preview), consume=True)
    git = engine.validate_worktree(engine.store.row(key))
    # Pin the source SHA and destination URL. Neither HEAD nor origin may redirect
    # an already-approved push if another process changes them after the gate.
    git.run(
        "-c",
        "core.hooksPath=/dev/null",
        "push",
        preview["remote"],
        f"{preview['commit']}:refs/heads/{preview['branch']}",
    )
    engine.store.event("Human-approved exact branch pushed", key)
    engine.board()


def perform_draft(engine: Any, key: str, target: str) -> str:
    preview = remote_preview(engine, key, "draft", target)
    engine.store.require("draft", key, digest(preview), consume=True)
    row = engine.store.row(key)
    git = engine.validate_worktree(row)
    remote_line = git.run("ls-remote", preview["remote"], f"refs/heads/{preview['branch']}")
    if not remote_line or remote_line.split()[0] != preview["commit"]:
        raise OrchestratorError("Push this exact accepted commit before creating a draft request")
    body_file = engine.runtime / "reviews" / f"{key}-request.md"
    atomic_write(body_file, preview["body"])
    url = Hosting(
        engine.profile.hosting, Path(row["worktree"]), preview["repository"]
    ).create_draft(preview["branch"], target, preview["title"], body_file)
    if row["state"] == State.APPROVED:
        engine.store.transition(key, State.MR_READY)
    engine.store.event(f"Human-approved draft request created: {url}", key)
    engine.board()
    return url
