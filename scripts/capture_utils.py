"""Keep native terminal captures portable and free of external font requests."""

from __future__ import annotations

import re
from pathlib import Path


def sanitize_svg(path: Path) -> None:
    source = re.sub(r"\s*@font-face\s*\{[^}]*\}", "", path.read_text())
    source = source.replace(
        "font-family: Fira Code, monospace", "font-family: Menlo, Consolas, monospace"
    )
    # Interpreter and tool paths can reveal the capturing account; keep line widths unchanged.
    home = Path.home()
    neutral = home.parent / ("synthetic" * 4)[: len(home.name)]
    source = source.replace(str(home), str(neutral))
    path.write_text("\n".join(line.rstrip() for line in source.splitlines()) + "\n")
