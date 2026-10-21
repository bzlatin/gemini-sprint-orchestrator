from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import OrchestratorError, Profile


def private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise OrchestratorError(f"Refusing symlink directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def atomic_write(path: Path, data: str) -> None:
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Profiles:
    def __init__(self, home: Path | None = None):
        self.home = (
            (
                home
                or Path(
                    os.environ.get("GSO_HOME", Path.home() / ".local/share/gemini-orchestrator")
                )
            )
            .expanduser()
            .resolve()
        )
        private_dir(self.home)
        private_dir(self.home / "profiles")

    def path(self, name: str) -> Path:
        if not __import__("re").fullmatch(r"[a-z][a-z0-9-]{0,47}", name):
            raise OrchestratorError("Invalid profile name")
        return self.home / "profiles" / f"{name}.json"

    def names(self) -> list[str]:
        return sorted(p.stem for p in (self.home / "profiles").glob("*.json"))

    def save(self, profile: Profile) -> None:
        atomic_write(self.path(profile.name), profile.model_dump_json(indent=2))

    def active(self) -> str:
        path = self.home / "active"
        if not path.exists():
            raise OrchestratorError("No active profile. Run `gemini-orchestrator setup` or `demo`.")
        return path.read_text().strip()

    def load(self, name: str | None = None) -> Profile:
        path = self.path(name or self.active())
        try:
            return Profile.model_validate(json.loads(path.read_text()))
        except (OSError, ValueError) as exc:
            raise OrchestratorError(
                f"Invalid profile {path.name}; run profile edit or setup: {exc}"
            ) from exc

    def switch(self, name: str) -> None:
        self.load(name)
        atomic_write(self.home / "active", name)

    def runtime(self, name: str) -> Path:
        self.path(name)
        return private_dir(self.home / "runtime" / name)
