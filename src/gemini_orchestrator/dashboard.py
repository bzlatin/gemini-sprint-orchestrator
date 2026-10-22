from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from .config import Profiles
from .engine import Engine
from .models import Check, OrchestratorError, State, digest
from .session_widgets import SessionPanel, confirmation_text
from .sessions import SessionBridge, open_vscode


class Confirmation(ModalScreen[bool]):
    CSS = """
    Confirmation { align: center middle; background: $background 75%; }
    #confirmation { width: 78; max-width: 95%; height: auto; max-height: 90%; padding: 1 2; border: solid #6de0cb; background: #182630; }
    #confirmation Static { height: auto; margin-bottom: 1; }
    #confirmation Horizontal { height: 3; }
    """

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="confirmation"):
            yield Static(self.message, markup=False)
            with Horizontal():
                yield Button("Confirm human approval", variant="primary", id="confirm")
                yield Button("Go back", id="back")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def on_mount(self) -> None:
        self.query_one("#back", Button).focus()


class Dashboard(App[None]):
    TITLE = "Gemini Sprint Orchestrator"
    SUB_TITLE = "Human-controlled development"
    ENABLE_COMMAND_PALETTE = True
    CSS = """
    Screen { background: #101820; color: #edf2f4; }
    Header { background: #182630; color: #6de0cb; }
    Footer { background: #182630; }
    TabbedContent { height: 1fr; min-height: 0; }
    ContentSwitcher { height: 1fr; min-height: 0; }
    #overview { height: 3; padding: 0 2; color: #b7c8cf; }
    #tickets { height: 1fr; min-height: 3; }
    #search { width: 1fr; }
    #priority, #status-filter { width: 22; }
    .filters { height: 3; }
    .actions { height: auto; min-height: 3; layout: horizontal; overflow-x: auto; }
    Button { margin-right: 1; min-width: 12; }
    #details-scroll { height: 5; padding: 0 1; border-top: solid #334b59; }
    #details { height: auto; min-height: 2; }
    .document { padding: 1 2; height: 1fr; min-height: 0; overflow-y: auto; }
    #plan-text, #agent-text, #review-text, #activity-text, #approval-text { height: auto; min-height: 4; }
    #notice { height: 2; padding-left: 2; color: #ffd28a; }
    #settings-json { height: 1fr; min-height: 10; }
    #feedback, #review-key { width: 1fr; }
    TabPane { padding: 1; height: 1fr; min-height: 0; overflow: hidden; }
    DataTable { background: #101820; }
    Input:focus { border: tall #6de0cb; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "toggle_ticket", "Select ticket"),
        Binding("p", "plan", "Plan"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+s", "save_selection", "Save selection"),
    ]

    def __init__(self, engine: Engine, profiles: Profiles, connected_enabled: bool = False):
        super().__init__()
        self.engine = engine
        self.profiles = profiles
        self.connected_enabled = connected_enabled
        self.selected: set[str] = {
            row["key"] for row in engine.store.rows() if row["state"] != State.NOT_SELECTED
        }
        self.current_key = ""
        self.execution_task: asyncio.Task[None] | None = None
        self.last_view = ""
        self.loaded_review: tuple[str, str] | None = None
        self.loaded_session = ""
        self.session_drafts: dict[str, str] = {}
        self.bridge = SessionBridge(engine, connected_enabled=connected_enabled)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="overview", markup=False)
        with TabbedContent():
            with TabPane("Tickets", id="explorer"):
                with Horizontal(classes="filters"):
                    yield Input(placeholder="Search key or summary", id="search")
                    yield Input(placeholder="Priority", id="priority")
                    yield Input(placeholder="Status", id="status-filter")
                yield DataTable(id="tickets", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="details-scroll", can_focus=True):
                    yield Static(
                        "Select a row to inspect it. Space toggles selection.",
                        id="details",
                        markup=False,
                    )
                with Horizontal(classes="actions"):
                    yield Button("Toggle ticket", id="toggle")
                    yield Button("Save selection", id="save-selection", variant="primary")
                    yield Button("Propose plan", id="propose")
                    yield Button("Refresh", id="refresh")
                    yield Button("Sort priority", id="sort-priority")
                    yield Button("Defer ticket", id="defer")
            with TabPane("Plan", id="planner"):
                with VerticalScroll(classes="document"):
                    yield Static(
                        "Select tickets, then propose a plan.", id="plan-text", markup=False
                    )
                with Horizontal(classes="actions"):
                    yield Button("Approve plan", id="approve-plan", variant="primary")
                    yield Button("Approve launch", id="launch", variant="success")
                    yield Button("Reject plan", id="reject-plan")
            with TabPane("Agents", id="agents"):
                with VerticalScroll(classes="document"):
                    yield Static(id="agent-text", markup=False)
                yield Input(placeholder="Worker ticket key", id="agent-key")
                with Horizontal(classes="actions"):
                    yield Button("Open session", id="agent-session", variant="primary")
                    yield Button("Open VS Code", id="agent-code")
                    yield Button("Pause worker", id="pause")
                    yield Button("Cancel worker", id="cancel", variant="warning")
            with TabPane("Sessions", id="sessions"):
                yield SessionPanel()
            with TabPane("Approvals", id="approvals"):
                with VerticalScroll(classes="document"):
                    yield Static(id="approval-text", markup=False)
            with TabPane("Review", id="review"):
                with Horizontal(classes="filters"):
                    yield Input(placeholder="Ticket key, e.g. DEMO-1", id="review-key")
                    yield Button("Load review", id="load-review")
                with VerticalScroll(classes="document"):
                    yield Static(
                        "Choose a review-ready ticket. Inspect the full diff and verification before accepting.",
                        id="review-text",
                        markup=False,
                    )
                yield Input(placeholder="Feedback for requesting changes", id="feedback")
                with Horizontal(classes="actions"):
                    yield Button("Accept changes", id="accept", variant="success")
                    yield Button("Request changes", id="request-changes")
                    yield Button("Verify again", id="verify")
                    yield Button("Defer review", id="defer-review")
            with TabPane("Activity", id="activity"):
                with VerticalScroll(classes="document"):
                    yield Static(id="activity-text", markup=False)
            with TabPane("Settings", id="settings"):
                yield Label(
                    "Edit concurrency, timeout and verification checks. Commands require approval through a new plan."
                )
                yield TextArea(
                    json.dumps(
                        {
                            "concurrency": self.engine.profile.gemini.concurrency,
                            "timeout": self.engine.profile.gemini.timeout,
                            "checks": [c.model_dump() for c in self.engine.profile.checks],
                        },
                        indent=2,
                    ),
                    id="settings-json",
                )
                yield Button("Save settings", id="save-settings", variant="primary")
                yield Label(
                    "Identity, board, sprint, repository and hosting: use profile edit after closing this dashboard."
                )
        yield Static(
            "Synthetic demo"
            if self.engine.profile.mode == "demo"
            else "Connected mode explicitly enabled",
            id="notice",
            markup=False,
        )
        yield Footer()

    def on_resize(self, event: events.Resize | None = None) -> None:
        if self.query("#details-scroll"):
            compact = self.size.height < 30
            self.query_one("#details-scroll").styles.height = 3 if compact else 5
            self.query_one("#overview").styles.height = 2 if compact else 3
            self.query_one("#notice").styles.height = 1 if compact else 2
            self.query_one("#session-message").styles.height = 3 if compact else 5
            self.query_one("#session-status").styles.height = 2 if compact else 3

    async def on_mount(self) -> None:
        self.on_resize()
        table = self.query_one("#tickets", DataTable)
        table.add_columns("Pick", "Ticket", "Lifecycle", "Priority", "Summary", "Jira status")
        self.render_tickets()
        self.update_views()
        self.set_interval(0.5, self.update_views)
        table.focus()
        try:
            await self.bridge.start()
        except (OSError, ValueError) as exc:
            self.notice(
                f"VS Code session connection unavailable: {exc}. Dashboard conversations still work."
            )

    async def on_unmount(self) -> None:
        await self.bridge.close()

    def load_session(self, key: str | None = None) -> None:
        key = key or self.query_one("#session-key", Input).value.strip().upper()
        snapshot = self.engine.session_snapshot(key)
        if self.loaded_session:
            self.session_drafts[self.loaded_session] = self.query_one(
                "#session-message", TextArea
            ).text
        self.loaded_session = key
        self.query_one("#session-key", Input).value = key
        self.query_one("#session-message", TextArea).load_text(self.session_drafts.get(key, ""))
        self.query_one(SessionPanel).show_snapshot(snapshot)
        self.query_one(TabbedContent).active = "sessions"
        self.call_after_refresh(
            self.screen.set_focus,
            self.query_one("#session-message", TextArea),
            scroll_visible=False,
        )

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "sessions" and self.loaded_session:
            self.call_after_refresh(
                self.screen.set_focus,
                self.query_one("#session-message", TextArea),
                scroll_visible=False,
            )

    def checked_session_key(self) -> str:
        if (
            not self.loaded_session
            or self.query_one("#session-key", Input).value.strip().upper() != self.loaded_session
        ):
            raise OrchestratorError("Load the ticket session before sending or opening its editor")
        return self.loaded_session

    @work(group="session-turn")
    async def wait_session_turn(self, task: asyncio.Task[None]) -> None:
        try:
            await asyncio.shield(task)
            self.notice(
                "Follow-up finished. Inspect the conversation, verification and implementation review."
            )
        except Exception as exc:
            self.notice(exc)
        finally:
            self.update_views(force=True)

    @work(group="editor")
    async def open_editor(self, key: str) -> None:
        try:
            await open_vscode(self.engine, key, self.bridge)
            self.notice(
                f"Opened {key} in VS Code. Allow its session task, or use Terminal → Run Task → Gemini session: {key}."
            )
        except Exception as exc:
            self.notice(exc)

    def notice(self, value: object) -> None:
        if self.query("#notice"):
            self.query_one("#notice", Static).update(str(value))

    def render_tickets(self) -> None:
        table = self.query_one("#tickets", DataTable)
        table.clear()
        query = self.query_one("#search", Input).value.casefold()
        priority = self.query_one("#priority", Input).value.casefold()
        status = self.query_one("#status-filter", Input).value.casefold()
        for row in self.engine.current_rows():
            ticket = self.engine.store.ticket(row["key"])
            if (
                query not in f"{ticket.key} {ticket.summary}".casefold()
                or priority not in ticket.priority.casefold()
                or status not in ticket.status.casefold()
            ):
                continue
            table.add_row(
                *(
                    Text(value)
                    for value in [
                        "[x]" if ticket.key in self.selected else "[ ]",
                        ticket.key,
                        row["state"],
                        ticket.priority,
                        ticket.summary,
                        ticket.status,
                    ]
                ),
                key=ticket.key,
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"search", "priority", "status-filter"}:
            self.render_tickets()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value:
            self.current_key = str(event.row_key.value)
            ticket = self.engine.store.ticket(self.current_key)
            self.query_one("#details", Static).update(
                f"{ticket.key} · {ticket.summary} · {self.engine.store.row(ticket.key)['state']}\n{ticket.description}\nAcceptance: {'; '.join(ticket.acceptance)}\nBlockers: {', '.join(ticket.dependencies) or 'none'}"
            )

    def action_toggle_ticket(self) -> None:
        if not self.current_key:
            return
        if self.current_key in self.selected:
            self.selected.remove(self.current_key)
        else:
            self.selected.add(self.current_key)
        cursor = self.query_one("#tickets", DataTable).cursor_row
        self.render_tickets()
        self.query_one("#tickets", DataTable).move_cursor(row=cursor)

    def action_save_selection(self) -> None:
        keys = sorted(self.selected)
        self.confirm(
            "Save this exact ticket selection?\n" + (", ".join(keys) or "Empty selection"),
            lambda: self.engine.select(keys, self.engine.profile.display_name),
        )

    def confirm(self, message: str, callback: Callable[[], Any]) -> None:
        def done(approved: bool | None) -> None:
            if approved:
                try:
                    callback()
                    self.update_views(force=True)
                    self.notice("Human action recorded.")
                except Exception as exc:
                    self.notice(exc)

        self.push_screen(Confirmation(message), done)

    def action_plan(self) -> None:
        try:
            plan = self.engine.propose()
            text = (
                f"Plan {plan.id} · base {plan.base[:12]} · fingerprint {plan.fingerprint[:16]}\n\n"
            )
            for index, group in enumerate(plan.groups, 1):
                text += (
                    f"Group {index}: "
                    + ", ".join(f"slot {slot}: {key}" for slot, key in enumerate(group, 1))
                    + "\n"
                )
            text += "\nBlocked:\n" + (
                "\n".join(f"{key}: {reason}" for key, reason in plan.blocked.items()) or "None"
            )
            text += "\n\nDependencies and conflicts:\n" + (
                "\n".join(
                    f"{edge.prerequisite} → {edge.dependent} [{edge.kind}] {edge.reason}"
                    for edge in plan.edges
                )
                or "Independent selected tasks; code conflicts remain unknown without semantic analysis."
            )
            text += "\n\nBranches / worktrees:\n" + "\n".join(
                f"{key}\n  {plan.branches[key]}\n  {plan.worktrees[key]}" for key in plan.tickets
            )
            text += "\n\nVerification:\n" + (
                "\n".join(f"{c.name}: {c.argv}" for c in plan.checks)
                or "Manual verification required"
            )
            text += "\n\nDependent work waits for human acceptance of prerequisites. Reselect tickets to edit this plan; every material change invalidates approval."
            self.query_one("#plan-text", Static).update(text)
            self.query_one(TabbedContent).active = "planner"
            self.call_after_refresh(self.query_one("#approve-plan", Button).focus)
            self.update_views(force=True)
        except Exception as exc:
            self.notice(exc)

    @work(exclusive=True, group="refresh")
    async def action_refresh(self) -> None:
        try:
            await self.engine.discover(connected_enabled=self.connected_enabled)
            self.selected &= {row["key"] for row in self.engine.current_rows()}
            self.render_tickets()
            self.notice("Tickets refreshed.")
        except Exception as exc:
            self.notice(exc)

    @work(exclusive=True, group="execution")
    async def run_workers(self) -> None:
        try:
            await self.engine.execute(connected_enabled=self.connected_enabled)
            self.notice("Batch finished. Review implementations before launching dependent work.")
        except Exception as exc:
            self.notice(exc)
        finally:
            self.update_views(force=True)

    @work(group="review-verification")
    async def rerun_verification(self, key: str) -> None:
        try:
            await self.engine.verify(key)
            self.load_review()
        except Exception as exc:
            self.notice(exc)

    @work(group="cancel")
    async def stop_worker(self, key: str, pause: bool) -> None:
        try:
            await self.engine.cancel(key, pause=pause)
        except Exception as exc:
            self.notice(exc)

    def load_review(self) -> None:
        key = self.query_one("#review-key", Input).value.strip().upper()
        package = self.engine.review(key)
        self.loaded_review = (key, package["fingerprint"])
        ticket = package["ticket"]
        verification = package["verification"]
        checks = "\n".join(
            f"  {item['name']}: {'PASS' if item['passed'] else 'FAIL'} · exit {item['exit_code']} · {item['duration']}s\n    {item['argv']}\n    Log: {item['log']}"
            for item in verification.get("results", [])
        )
        rendered = (
            f"{key} · {ticket['summary']}\nBranch: {package['branch']}\nWorktree: {package['worktree']}\nVerification: {verification.get('status', 'manual required')}\n{checks}\n\nFiles changed (including new files):\n{package['files'] or 'No changes'}\n\nImplementation diff:\n{package['diff'] or 'No diff'}\n\nAcceptance criteria:\n"
            + "\n".join(f"- {criterion}" for criterion in ticket["acceptance"])
        )
        rendered += "\n\nWorker summary:\n" + package["worker"].get(
            "summary", "Synthetic worker completed; inspect its actual diff above."
        )
        rendered += "\n\nReview warnings:\n" + (
            "\n".join(package.get("review_warnings", [])) or "None"
        )
        rendered += "\n\nKnown risks:\n" + "\n".join(package["risks"])
        rendered += "\n\nFeedback history:\n" + ("\n".join(package["feedback"]) or "None")
        rendered += f"\n\nLoaded review fingerprint: {package['fingerprint']}"
        self.query_one("#review-text", Static).update(rendered)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        actor = self.engine.profile.display_name
        try:
            if button == "toggle":
                self.action_toggle_ticket()
            elif button == "save-selection":
                self.action_save_selection()
            elif button == "propose":
                self.action_plan()
            elif button == "agent-session":
                self.load_session(
                    self.query_one("#agent-key", Input).value.strip().upper() or self.current_key
                )
            elif button == "load-session":
                self.load_session()
            elif button in {"agent-code", "session-code"}:
                key = (
                    self.checked_session_key()
                    if button == "session-code"
                    else self.query_one("#agent-key", Input).value.strip().upper()
                    or self.current_key
                )
                self.open_editor(key)
            elif button == "session-send":
                key = self.checked_session_key()
                message = self.query_one("#session-message", TextArea).text
                preview = self.engine.session_preview(key, message)
                fingerprint = digest(preview)

                def send() -> None:
                    self.engine.approve_session_turn(key, message, actor, fingerprint)
                    task = self.engine.launch_session_turn(
                        key, message, connected_enabled=self.connected_enabled
                    )
                    if self.query_one("#session-message", TextArea).text == message:
                        self.query_one("#session-message", TextArea).clear()
                    self.wait_session_turn(task)

                self.confirm(confirmation_text(preview), send)
            elif button == "session-pause":
                key = self.checked_session_key()
                self.confirm(
                    f"Pause {key}? Worktree and conversation are preserved.",
                    lambda: self.stop_worker(key, True),
                )
            elif button == "refresh":
                self.action_refresh()
            elif button == "sort-priority":
                self.query_one("#tickets", DataTable).sort(
                    list(self.query_one("#tickets", DataTable).columns)[3],
                    key=lambda value: {
                        "Highest": 0,
                        "High": 1,
                        "Medium": 2,
                        "Low": 3,
                        "Lowest": 4,
                    }.get(str(value), 5),
                )
            elif button == "defer":
                self.selected.discard(self.current_key)
                self.action_save_selection()
            elif button == "approve-plan":
                plan = self.engine.store.plan()
                self.confirm(
                    f"Approve plan {plan.id}?\nFingerprint: {plan.fingerprint}\nThis records planning approval. Worker launch requires its own approval.",
                    lambda: self.engine.approve_plan(actor),
                )
            elif button == "reject-plan":
                self.confirm(
                    "Reject the current plan and return its tickets to selection?",
                    lambda: self.engine.select(sorted(self.selected), actor),
                )
            elif button == "launch":
                plan = self.engine.store.plan()

                def launch() -> None:
                    self.engine.approve_launch(actor)
                    self.run_workers()
                    self.query_one(TabbedContent).active = "agents"
                    self.call_after_refresh(self.query_one("#agent-key", Input).focus)

                self.confirm(
                    f"Launch queued workers for plan {plan.id}?\n{', '.join(key for group in plan.groups for key in group)}\nConcurrency: {self.engine.profile.gemini.concurrency}\nVerification commands: {[c.argv for c in plan.checks]}",
                    launch,
                )
            elif button == "load-review":
                self.load_review()
            elif button == "accept":
                key = self.query_one("#review-key", Input).value.strip().upper()
                if self.loaded_review is None or self.loaded_review[0] != key:
                    raise OrchestratorError(
                        "Load and inspect this ticket's review package before accepting"
                    )
                package = self.engine.review(key)
                if self.loaded_review[1] != package["fingerprint"]:
                    raise OrchestratorError(
                        "Loaded review changed; reload and inspect the new diff before accepting"
                    )
                self.confirm(
                    f"Accept {key} after inspecting its diff and verification?\nFingerprint: {package['fingerprint']}\nAcceptance creates a local commit on this worker branch.",
                    lambda: self.engine.accept(
                        key, actor, expected_fingerprint=package["fingerprint"]
                    ),
                )
            elif button == "request-changes":
                key = self.query_one("#review-key", Input).value.strip().upper()
                feedback = self.query_one("#feedback", Input).value
                self.confirm(
                    f"Request changes for {key}?\n{feedback}",
                    lambda: self.engine.feedback(key, feedback, actor),
                )
            elif button == "verify":
                key = self.query_one("#review-key", Input).value.strip().upper()
                commands = [check.model_dump() for check in self.engine.profile.checks]
                fingerprint = self.engine.verification_fingerprint(key)

                def verify() -> None:
                    if fingerprint != self.engine.verification_fingerprint(key):
                        raise OrchestratorError(
                            "Verification context changed; preview the current commands again"
                        )
                    self.engine.approve_verification(key, actor)
                    self.rerun_verification(key)

                self.confirm(
                    f"Run these exact verification commands for {key}?\n{json.dumps(commands, indent=2)}\nApproval: {fingerprint}",
                    verify,
                )
            elif button == "defer-review":
                key = self.query_one("#review-key", Input).value.strip().upper()
                self.confirm(
                    f"Defer review of {key}? Work is preserved.",
                    lambda: self.engine.store.transition(key, State.PAUSED),
                )
            elif button in {"pause", "cancel"}:
                key = self.query_one("#agent-key", Input).value.strip().upper()
                self.confirm(
                    f"{button.title()} {key}? Worktree contents will be preserved.",
                    lambda: self.stop_worker(key, button == "pause"),
                )
            elif button == "save-settings":
                raw = json.loads(self.query_one("#settings-json", TextArea).text)

                def save() -> None:
                    if self.engine.executing or self.engine.supervisor.running:
                        raise OrchestratorError(
                            "Wait for workers and verification before editing settings"
                        )
                    profile = self.engine.profile.model_copy(deep=True)
                    profile.gemini = type(profile.gemini).model_validate(
                        {
                            **profile.gemini.model_dump(),
                            "concurrency": raw["concurrency"],
                            "timeout": raw["timeout"],
                        }
                    )
                    profile.checks = [Check.model_validate(check) for check in raw["checks"]]
                    self.profiles.save(profile)
                    self.engine.profile = profile
                    self.engine.store.db.execute(
                        "UPDATE approvals SET consumed=1 WHERE action IN ('plan','launch')"
                    )
                    self.engine.store.event("Human edited execution settings; regenerate plan")
                    self.engine.board()

                self.confirm(
                    "Save these execution settings and invalidate the current plan approvals?", save
                )
        except Exception as exc:
            self.notice(exc)

    def update_views(self, force: bool = False) -> None:
        store = self.engine.store
        rows = store.rows()
        signature = json.dumps([rows, store.approvals(), store.events(30)], default=str)
        active = any(row["state"] in {State.IN_PROGRESS, State.VERIFYING} for row in rows)
        if signature == self.last_view and not force and not active:
            return
        self.last_view = signature
        if self.loaded_session:
            try:
                self.query_one(SessionPanel).show_snapshot(
                    self.engine.session_snapshot(self.loaded_session)
                )
            except Exception as exc:
                self.query_one("#session-send", Button).disabled = True
                self.query_one("#session-code", Button).disabled = True
                self.notice(exc)
        p = self.engine.profile
        running = sum(row["state"] in {State.IN_PROGRESS, State.VERIFYING} for row in rows)
        review = sum(row["state"] == State.READY_FOR_REVIEW for row in rows)
        blocked = sum(row["state"] == State.BLOCKED for row in rows)
        self.query_one("#overview", Static).update(
            f"{p.display_name} · {p.mode.upper()} · Board {p.board_id or 'unconfigured'} · {p.sprint.name if p.sprint else 'No sprint'}\nSelected {len(self.selected)} / {len(rows)}    Running {running}/{p.gemini.concurrency}    Blocked {blocked}    Review {review}"
        )
        agents = []
        for row in rows:
            if row["worktree"] or row["state"] != State.NOT_SELECTED:
                worker = json.loads(row["result"] or "{}")
                log = Path(worker["log"]) if worker.get("log") else None
                recent = ""
                elapsed = ""
                if row["state"] in {State.IN_PROGRESS, State.VERIFYING} and worker.get(
                    "started_at"
                ):
                    seconds = max(
                        0,
                        int(
                            (
                                datetime.now(UTC) - datetime.fromisoformat(worker["started_at"])
                            ).total_seconds()
                        ),
                    )
                    elapsed = f" · elapsed {seconds}s"
                if (
                    log is not None
                    and log.is_file()
                    and not log.is_symlink()
                    and log.parent.resolve() == (self.engine.runtime / "logs").resolve()
                ):
                    with log.open("rb") as stream:
                        stream.seek(max(0, log.stat().st_size - 1500))
                        recent = stream.read().decode(errors="replace")
                agents.append(
                    f"{row['key']} · {row['state']} · PID {row['pid'] or '—'}{elapsed}\nBranch {row['branch'] or 'pending'}\nWorktree {row['worktree'] or 'pending'}\n{recent}"
                )
        self.query_one("#agent-text", Static).update(
            "\n\n".join(agents) or "No workers launched. Select work and approve a plan first."
        )
        self.query_one("#approval-text", Static).update(
            "\n\n".join(
                f"#{a['id']} · {a['action']} · {a['subject']}\n{'Granted by ' + str(a['actor']) if a['granted'] else 'Awaiting explicit human action'}\nFingerprint {a['fingerprint']}"
                for a in store.approvals()
            )
            + "\n\nUse Plan for planning/launch approval; use Review for implementation acceptance. Push and draft-request approvals use the CLI."
        )
        self.query_one("#activity-text", Static).update(
            "\n".join(
                f"{event['created']}  {event['ticket'] or 'sprint'}  {event['message']}"
                for event in store.events(100)
            )
        )
        if self.current_key:
            ticket = store.ticket(self.current_key)
            self.query_one("#details", Static).update(
                f"{ticket.key} · {ticket.summary} · {store.row(ticket.key)['state']}\n{ticket.description}\nAcceptance: {'; '.join(ticket.acceptance)}\nBlockers: {', '.join(ticket.dependencies) or 'none'}"
            )
        # Update lifecycle cells without moving the user's cursor or resetting filters.
        table = self.query_one("#tickets", DataTable)
        for row in rows:
            if row["key"] in table.rows:
                table.update_cell(row["key"], list(table.columns)[2], row["state"])

    async def action_quit(self) -> None:
        await self.bridge.close()
        await self.engine.shutdown()
        self.exit()
