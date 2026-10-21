from __future__ import annotations

import fcntl
import hashlib
import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import OrchestratorError, Profile, digest


def command(argv: list[str], cwd: Path | None = None, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OrchestratorError(f"Could not run {argv[0]}: {exc}") from exc
    if result.returncode:
        raise OrchestratorError(f"{argv[0]} failed: {result.stderr.strip()[:1000]}")
    return result.stdout.strip()


class Git:
    def __init__(self, repository: Path):
        self.root = repository.resolve()

    def run(self, *args: str) -> str:
        return command(["git", "-C", str(self.root), *args])

    def validate(self) -> dict[str, str]:
        if not self.root.is_dir():
            raise OrchestratorError("Repository path does not exist")
        top = Path(self.run("rev-parse", "--show-toplevel")).resolve()
        if top != self.root:
            raise OrchestratorError(f"Select the repository root: {top}")
        head = self.run("rev-parse", "--verify", "HEAD")
        branch = self.run("symbolic-ref", "--short", "HEAD")
        self.run("worktree", "list", "--porcelain")
        common = Path(self.run("rev-parse", "--path-format=absolute", "--git-common-dir"))
        if not os.access(common, os.R_OK | os.W_OK | os.X_OK):
            raise OrchestratorError("Git metadata is not writable by this user")
        return {"head": head, "branch": branch}

    def clean(self) -> bool:
        return not self.run("status", "--porcelain", "--untracked-files=all")

    def branch_exists(self, branch: str) -> bool:
        return bool(self.run("for-each-ref", "--format=%(refname)", f"refs/heads/{branch}"))

    @contextmanager
    def mutation_lock(self) -> Iterator[None]:
        """Serialize shared Git metadata writes across profiles and processes."""
        common = Path(self.run("rev-parse", "--path-format=absolute", "--git-common-dir"))
        fd = os.open(common / "gso-mutations.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def create(self, profile: Profile, branch: str, path: Path, base: str) -> None:
        with self.mutation_lock():
            self.validate()
            command(["git", "check-ref-format", "--branch", branch])
            if branch in profile.protected_branches or not branch.startswith("gso/"):
                raise OrchestratorError("Worker branches must use gso/ and must not be protected")
            expected = profile.worktrees.resolve()
            if path.parent.resolve() != expected or path.is_symlink():
                raise OrchestratorError("Worktree path escapes configured directory")
            if expected.is_relative_to(profile.repository):
                raise OrchestratorError("Worktrees cannot be inside the main checkout")
            if self.branch_exists(branch) or path.exists():
                raise OrchestratorError(
                    f"Branch or worktree collision: {branch}; inspect existing work first"
                )
            if not re.fullmatch(r"[0-9a-f]{40,64}", base):
                raise OrchestratorError("A resolved commit hash is required")
            expected.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.run(
                "-c", "core.hooksPath=/dev/null", "worktree", "add", "-b", branch, str(path), base
            )

    def review(self, base: str) -> dict[str, Any]:
        files = self.run("diff", "--name-status", base)
        patch = self.run("diff", "--no-ext-diff", "--no-textconv", base, "--")
        listing = subprocess.run(
            ["git", "-C", str(self.root), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
        ).stdout
        untracked = [os.fsdecode(name) for name in listing.split(b"\0") if name]
        warnings: list[str] = []
        for name in filter(None, untracked):
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.root),
                    "diff",
                    "--no-index",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--",
                    "/dev/null",
                    name,
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
            if result.returncode not in {0, 1}:
                raise OrchestratorError(f"Could not generate untracked diff for {name}")
            files += f"\nA\t{name}"
            patch += "\n" + result.stdout
        if re.search(r"^(Binary files .+ differ|GIT binary patch)$", patch, re.MULTILINE):
            warnings.append("Binary changes require inspection in the worktree")
        truncated = len(patch) > 1_000_000
        if truncated:
            warnings.append(
                "Diff exceeds 1 MB; inspect the full changes in the worktree before accepting"
            )
            patch = patch[:1_000_000] + "\n[Diff truncated: inspect full changes in worktree]\n"
        return {
            "head": self.run("rev-parse", "HEAD"),
            "status": self.run("status", "--short"),
            "files": files.strip(),
            "diff": patch,
            "review_warnings": warnings,
            "diff_truncated": truncated,
        }

    def fingerprint(self, *, content_only: bool = False) -> str:
        entries: dict[str, str] = {
            "head": self.run("rev-parse", "HEAD"),
            "status": self.run("status", "--porcelain=v1", "--untracked-files=all"),
        }
        if content_only:
            entries = {}
        raw = subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
        ).stdout
        for name in raw.decode().split("\0"):
            if not name:
                continue
            path = self.root / name
            if path.is_symlink():
                entries[name] = "symlink:" + os.readlink(path)
            elif path.is_file():
                h = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                entries[name] = f"{path.stat().st_mode}:{h.hexdigest()}"
            elif path.exists():
                raise OrchestratorError(
                    "Submodules are not supported by worker approval fingerprints"
                )
            else:
                entries[name] = "deleted"
        return digest(entries)

    def checkpoint(self, key: str) -> str:
        with self.mutation_lock():
            # Only orchestrator code commits; Gemini has no shell or writable shared Git metadata.
            self.run("-c", "core.hooksPath=/dev/null", "add", "--all")
            if self.run("diff", "--cached", "--name-only"):
                self.run(
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "user.name=Gemini Orchestrator",
                    "-c",
                    "user.email=orchestrator@localhost",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    f"Implement {key}",
                )
            return self.run("rev-parse", "HEAD")

    def ancestor(self, older: str, newer: str) -> bool:
        return (
            subprocess.run(
                ["git", "-C", str(self.root), "merge-base", "--is-ancestor", older, newer],
                capture_output=True,
            ).returncode
            == 0
        )
