from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Static, TextArea


class SessionPanel(Vertical):
    DEFAULT_CSS = """
    SessionPanel { height: 1fr; min-height: 0; }
    #session-key { width: 1fr; }
    #session-fixed-key { width: 1fr; height: 3; content-align: left middle; }
    #session-status { height: 3; color: #b7c8cf; margin: 0 1; }
    #session-scroll { height: 1fr; min-height: 2; padding: 0 1; }
    #session-location, #session-history, #session-log { height: auto; }
    #session-message { height: 5; min-height: 3; border: solid #b7c8cf; border-title-color: #b7c8cf; }
    #session-message:focus { border: solid #6de0cb; border-title-color: #6de0cb; }
    """

    def __init__(self, *, show_editor: bool = True, fixed_key: str = ""):
        super().__init__()
        self.show_editor = show_editor
        self.fixed_key = fixed_key
        self.last_messages = ""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="filters"):
            if self.fixed_key:
                yield Static(
                    f"{self.fixed_key} · Conversation for this worktree",
                    id="session-fixed-key",
                    markup=False,
                )
                yield Button("Refresh", id="load-session")
            else:
                yield Input(placeholder="Worker ticket key, e.g. DEMO-1", id="session-key")
                yield Button("Load session", id="load-session")
        yield Static(
            "Load a launched worker to see its conversation and worktree.",
            id="session-status",
            markup=False,
        )
        with VerticalScroll(id="session-scroll", can_focus=True):
            yield Static(id="session-location", markup=False)
            yield Static(
                "Messages and replies will appear here. Sending a follow-up runs another supervised turn and verification; implementation acceptance stays separate.",
                id="session-history",
                markup=False,
            )
            yield Static(id="session-log", markup=False)
        editor = TextArea(id="session-message")
        editor.border_title = "Message to worker"
        yield editor
        with Horizontal(classes="actions"):
            yield Button("Send follow-up", id="session-send", variant="primary", disabled=True)
            yield Button("Pause worker", id="session-pause", disabled=True)
            if self.show_editor:
                yield Button("Open VS Code", id="session-code", disabled=True)

    def show_snapshot(self, snapshot: dict[str, Any]) -> None:
        continuity = (
            "Synthetic demo session"
            if snapshot["mode"] == "demo"
            else f"Gemini session {snapshot['session_id']}"
            if snapshot["session_id"]
            else "Continuing from saved task context; native Gemini session ID unavailable"
        )
        activity = (
            "Wait for the batch or pause its workers before sending."
            if snapshot["busy"]
            else "Confirm follow-up → verification → human review."
        )
        self.query_one("#session-status", Static).update(
            f"{snapshot['key']} · {snapshot['state']} · {snapshot['branch']}\n{activity}"
        )
        self.query_one("#session-location", Static).update(
            f"{continuity}\nWorktree: {snapshot['worktree']}\n\n"
        )
        messages = "\n\n".join(
            f"{item['role'].title()} · {item['created']} UTC\n{item['text']}"
            for item in snapshot["messages"]
        )[-100000:]
        if messages != self.last_messages:
            scroll = self.query_one("#session-scroll", VerticalScroll)
            follow = scroll.is_vertical_scroll_end
            self.last_messages = messages
            self.query_one("#session-history", Static).update(
                messages
                or "This older worker has no conversation transcript yet. Its next follow-up will be recorded here."
            )
            if follow:
                self.call_after_refresh(scroll.scroll_end, animate=False)
        self.query_one("#session-log", Static).update(
            "\n\nRecent worker output:\n" + snapshot["recent_log"] if snapshot["recent_log"] else ""
        )
        self.query_one("#session-send", Button).disabled = snapshot["busy"] or snapshot[
            "state"
        ] not in {
            "READY_FOR_REVIEW",
            "CHANGES_REQUESTED",
            "APPROVED",
            "MR_READY",
            "PAUSED",
            "INTERRUPTED",
            "FAILED",
            "CANCELLED",
        }
        self.query_one("#session-pause", Button).disabled = not snapshot.get("can_pause", False)
        if self.show_editor:
            self.query_one("#session-code", Button).disabled = False


def confirmation_text(preview: dict[str, Any]) -> str:
    continuity = (
        f"Resume Gemini session: {preview['session_id']}"
        if preview["session_id"]
        else "Continue using saved conversation and task context"
    )
    checks = (
        "\n".join(
            f"{check['name']} · {'required' if check['required'] else 'optional'} · timeout {check['timeout']}s\n{json.dumps(check['argv'], ensure_ascii=False)}"
            for check in preview["checks"]
        )
        or "No automated checks; manual verification required."
    )
    return f"Send this follow-up to {preview['key']}?\nBranch: {preview['branch']}\nWorktree: {preview['worktree']}\n{continuity}\n\nMessage:\n{preview['message']}\n\nVerification commands:\n{checks}\n\nThis authorizes another coding turn within the approved ticket scope. Prior acceptance is invalidated; verification and fresh human review follow."
