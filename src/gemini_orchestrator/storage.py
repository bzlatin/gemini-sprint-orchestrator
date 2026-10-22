"""Bounded disk accounting and explicit historical-log retention."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from .models import OrchestratorError


def usage(root: Path, max_bytes: int, max_files: int) -> dict[str, int]:
    total = count = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                count += 1
                total += info.st_size if stat.S_ISREG(info.st_mode) else 0
                if total > max_bytes or count > max_files:
                    raise OrchestratorError(
                        "Storage limit exceeded; inspect disk usage and retain/archive historical data before continuing"
                    )
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
    return {"bytes": total, "files": count}


def old_logs(runtime: Path, days: int, current: set[str]) -> list[dict[str, str | int]]:
    cutoff = time.time() - days * 86400
    result: list[dict[str, str | int]] = []
    for p in sorted((runtime / "logs").glob("*.log")):
        info = p.lstat()
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_mtime < cutoff
            and str(p) not in current
        ):
            result.append(
                {
                    "name": p.name,
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "inode": info.st_ino,
                }
            )
    return result
