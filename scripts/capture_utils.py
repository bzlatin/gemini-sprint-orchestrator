"""Keep native terminal captures portable and free of external font requests."""

from __future__ import annotations

import re
from pathlib import Path


def sanitize_svg(path: Path) -> None:
    source = re.sub(r"\s*@font-face\s*\{[^}]*\}", "", path.read_text())
    source = source.replace(
        "font-family: Fira Code, monospace", "font-family: Menlo, Consolas, monospace"
    )
    path.write_text("\n".join(line.rstrip() for line in source.splitlines()) + "\n")
