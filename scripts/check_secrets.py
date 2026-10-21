"""Offline scan of tracked source. Print only finding locations, never secret values."""

from __future__ import annotations

import json
import subprocess
import sys

paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
result = subprocess.run(
    [
        sys.executable,
        "-m",
        "detect_secrets",
        "scan",
        "--no-verify",
        "--all-files",
        "--",
        *filter(None, paths),
    ],
    capture_output=True,
    text=True,
    check=True,
)
report = json.loads(result.stdout)
findings = [
    (name, hit["line_number"], hit["type"])
    for name, hits in report["results"].items()
    for hit in hits
]
for name, line, kind in findings:
    print(f"{name}:{line}: {kind}")
print(f"Secret scan: {len(findings)} candidates in tracked files")
sys.exit(bool(findings))
