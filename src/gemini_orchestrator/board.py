from __future__ import annotations

import json
from pathlib import Path

from .config import atomic_write
from .models import Profile
from .store import Store


def md(value: object) -> str:
    return str(value or "—").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_board(store: Store, profile: Profile, runtime: Path) -> Path:
    lines = [
        "# Gemini Sprint Orchestrator",
        "",
        f"User: {md(profile.display_name)} · Team: {md(profile.team)}",
        f"Board: {md(profile.board_id)} · Sprint: {md(profile.sprint.name if profile.sprint else None)}",
        f"Mode: **{profile.mode}**",
        "",
        "| Ticket | Summary | State | Agent PID | Branch | Worktree | Dependencies | Verification | Review |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in store.rows():
        ticket = store.ticket(row["key"])
        verification = json.loads(row["verification"] or "{}")
        lines.append(
            "| "
            + " | ".join(
                md(value)
                for value in [
                    ticket.key,
                    ticket.summary,
                    row["state"],
                    row["pid"],
                    row["branch"],
                    row["worktree"],
                    ", ".join(ticket.dependencies),
                    verification.get("status", "Pending"),
                    "Human accepted" if row["state"] in {"APPROVED", "MR_READY"} else "Pending",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Pending actions", ""])
    for approval in store.approvals():
        lines.append(
            f"- #{approval['id']} {md(approval['action'])} / {md(approval['subject'])}: {'granted' if approval['granted'] else 'human approval required'}"
        )
    lines.extend(["", "## Activity", ""])
    lines.extend(
        f"- {event['created']} · {md(event['ticket'])} · {md(event['message'])}"
        for event in store.events(30)
    )
    lines.extend(
        [
            "",
            "## Next steps",
            "",
            "Select tickets → inspect plan → approve plan and launch → inspect diff and verification → accept or request changes.",
            "",
            "Remote push and draft request creation each require a separate exact-commit approval. Jira is never modified.",
            "",
        ]
    )
    path = runtime / "TASK_BOARD.md"
    atomic_write(path, "\n".join(lines))
    return path
