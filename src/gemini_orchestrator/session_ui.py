"""Shared-session terminal for VS Code; execution stays in the main coordinator."""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Footer, Header, Static, TextArea

from .dashboard import Confirmation
from .session_widgets import SessionPanel, confirmation_text
from .sessions import SessionRemote


class SessionConsole(App[None]):
    TITLE = "Gemini worker conversation"
    CSS = """
    Screen { background: #101820; color: #edf2f4; }
    Header, Footer { background: #182630; color: #6de0cb; }
    .filters { height: 3; }
    .actions { height: 3; overflow-x: auto; }
    Button { min-width: 12; margin-right: 1; }
    #notice { height: 2; color: #ffd28a; }
    Input:focus { border: tall #6de0cb; }
    """
    BINDINGS = [Binding("ctrl+q", "quit", "Close session")]

    def __init__(self, descriptor: Path, key: str):
        super().__init__()
        self.remote = SessionRemote(descriptor)
        self.loaded_key = key
        self.refreshing = False
        self.pending = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionPanel(show_editor=False, fixed_key=self.loaded_key)
        yield Static(
            "Connected to the main orchestrator. Closing this view leaves the worker running.",
            id="notice",
            markup=False,
        )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_session()
        self.set_interval(1, self.refresh_session)
        self.query_one("#session-message", TextArea).focus()
        self.on_resize()

    def on_resize(self, event: events.Resize | None = None) -> None:
        if self.query("#session-message"):
            self.query_one("#session-message").styles.height = 3 if self.size.height < 30 else 5
            self.query_one("#notice").styles.height = 1 if self.size.height < 30 else 2

    def notice(self, value: object) -> None:
        if self.query("#notice"):
            self.query_one("#notice", Static).update(str(value))

    @work(group="refresh-session")
    async def refresh_session(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        key = self.loaded_key
        try:
            snapshot = await self.remote.request("snapshot", key)
            if key != self.loaded_key or not self.query("#session-send"):
                return
            self.query_one(SessionPanel).show_snapshot(snapshot)
        except Exception as exc:
            if not self.query("#session-send"):
                return
            self.query_one("#session-send", Button).disabled = True
            self.query_one("#session-pause", Button).disabled = True
            self.notice(exc)
        finally:
            self.refreshing = False

    @work(group="session-action")
    async def action_request(self, action: str) -> None:
        if self.pending:
            return
        self.pending = True
        try:
            key = self.loaded_key
            message = self.query_one("#session-message", TextArea).text
            response = await self.remote.request(
                "preview" if action == "send" else "preview-pause", key, message=message
            )
            prompt = (
                confirmation_text(response["preview"])
                if action == "send"
                else f"Pause {key}? Its worktree and conversation are preserved. Other workers may still be running."
            )

            def decision(approved: bool | None) -> None:
                self.pending = False
                if approved:
                    self.perform(action, key, message, response["fingerprint"])

            self.push_screen(Confirmation(prompt), decision)
        except Exception as exc:
            self.pending = False
            self.notice(exc)

    @work(group="session-action")
    async def perform(self, action: str, key: str, message: str, fingerprint: str) -> None:
        try:
            await self.remote.request(action, key, message=message, fingerprint=fingerprint)
            if not self.query("#session-message"):
                return
            if (
                action == "send"
                and self.loaded_key == key
                and self.query_one("#session-message", TextArea).text == message
            ):
                self.query_one("#session-message", TextArea).clear()
            self.notice(
                "Follow-up started; verification and human review follow."
                if action == "send"
                else "Worker paused. Wait for the rest of the batch before sending a follow-up."
            )
            self.refresh_session()
        except Exception as exc:
            self.notice(exc)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        try:
            if event.button.id == "load-session":
                self.query_one("#session-send", Button).disabled = True
                self.query_one("#session-pause", Button).disabled = True
                self.query_one("#session-status", Static).update(f"Loading {self.loaded_key}…")
                self.refresh_session()
            elif event.button.id == "session-send":
                self.action_request("send")
            elif event.button.id == "session-pause":
                self.action_request("pause")
        except Exception as exc:
            self.notice(exc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True, help="Encoded local connection-file path")
    parser.add_argument("--ticket", required=True)
    args = parser.parse_args()
    descriptor = Path(os.fsdecode(base64.urlsafe_b64decode(args.connection)))
    SessionConsole(descriptor, args.ticket.upper()).run()


if __name__ == "__main__":
    main()
