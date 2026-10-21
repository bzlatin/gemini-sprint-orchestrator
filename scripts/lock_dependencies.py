"""Write tested dependency versions without editable paths or environment secrets."""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

root = Path(__file__).resolve().parents[1]
project = tomllib.loads((root / "pyproject.toml").read_text())["project"]


def closure(requirements: list[str]) -> list[str]:
    pending = [Requirement(value) for value in requirements]
    versions: dict[str, str] = {}
    processed: set[tuple[str, tuple[str, ...]]] = set()
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(requirement.name)
        variant = (name, tuple(sorted(requirement.extras)))
        if variant in processed:
            continue
        processed.add(variant)
        distribution = importlib.metadata.distribution(name)
        versions[name] = distribution.version
        for value in distribution.requires or []:
            child = Requirement(value)
            if child.marker is None or any(
                child.marker.evaluate({"extra": extra}) for extra in requirement.extras | {""}
            ):
                child.marker = None
                pending.append(child)
    return [f"{name}=={version}" for name, version in sorted(versions.items())]


for filename, requirements in {
    "requirements.lock": project["dependencies"],
    "requirements-dev.lock": project["dependencies"] + project["optional-dependencies"]["dev"],
}.items():
    (root / filename).write_text(
        "# Tested versions. Regenerate with python scripts/lock_dependencies.py.\n"
        + "\n".join(closure(requirements))
        + "\n"
    )
