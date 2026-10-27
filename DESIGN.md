---
name: Gemini Sprint Orchestrator
description: A human-controlled terminal operating surface for sprint work and review evidence.
colors:
  background: "#101820"
  panel: "#182630"
  foreground: "#edf2f4"
  muted: "#b7c8cf"
  accent: "#6de0cb"
  warning: "#ffd28a"
  divider: "#334b59"
typography:
  body:
    fontFamily: "monospace"
components:
  screen:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
  header:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.accent}"
  confirmation:
    backgroundColor: "{colors.panel}"
---

# Design System: Gemini Sprint Orchestrator

## Overview

**Creative North Star: "The Terminal Workbench"**

This is a Python Textual operating surface for repeated keyboard use on macOS and Linux, including SSH and tmux. A dense ticket table anchors the workspace; dedicated tabs expose plans, worker activity, approval history and implementation review. Human control and durable evidence determine hierarchy: the operator can inspect context before approving each consequential step.

This document records the implemented system in `src/gemini_orchestrator/dashboard.py`, `session_widgets.py` and `session_ui.py`, with session coordination in `sessions.py`. Terminal character cells govern sizing. The host terminal supplies the font and rendering; this is not a web or mobile design system. The descriptive North Star names the existing interface and introduces no new visual direction. The shared conversation extension preserves this identity in both the dashboard and editor terminal.

**Key Characteristics:**

- Persistent textual lifecycle labels and literal selection markers.
- Named actions, explicit approval previews and conservative modal focus.
- Scrollable evidence and compact geometry that preserve access to actions.
- Flat, dark work areas divided by terminal borders and tonal surfaces.

Current walkthrough evidence consists of five native Rich/Textual SVG captures and locally rendered PNGs in `docs/demo/`: `01-selection`, `02-plan`, `03-workers-and-blockers`, `04-review`, and `05-accepted-continuation`. These 120×40 synthetic states were raster-inspected in the finish review. `docs/demo/report.json` records the actual scripted application interactions, real local worktree execution, separate approvals, prerequisite acceptance, continuation and persistence checks. The walkthrough does not contact Jira, invoke a model account or publish remotely.

Earlier native captures `docs/dashboard-120.svg`, `docs/dashboard-80.svg`, `docs/planner-120.svg`, and `docs/planner-80.svg` retain ticket/plan geometry evidence at 120×40 and 80×24. The compact-size application test covers selection markers, focusable details, table height and separate plan approval without worker launch; it is not raster evidence for the full current workflow at 80×24. Source and behavioral tests establish dynamic approval/refresh behavior beyond a still capture. Full SSH/tmux operation, screen-reader behavior and enterprise integration remain target-system validation work. Capture-export font settings are not application typography tokens.

Shared-session evidence is separate: `docs/demo/sessions/report.json` records confirmed dashboard and editor-terminal follow-ups, rejection preserving a draft, the same branch/worktree, passing verification and six conversation entries surviving restart. Five final native SVG/PNG capture pairs in that directory cover `dashboard-session-120`, `dashboard-session-80`, `editor-session-120`, `editor-session-80`, and `session-confirmation-120`. Both terminal views were exercised at 120×40 and 80×24. The final implementation pass reports 116 full tests passed and exact native UUID resumption tested with a sandboxed synthetic CLI. The capture/report set is from the passing shared-session walkthrough run demo-06. VS Code GUI launch was stubbed because Code is not installed; generated workspace/process-task configuration and the real shared Textual client were exercised. Real Gemini authentication and provider history/cache behavior remain target-system validation. The final finish review inspected the composer-contrast recaptures and records ship specifically for its two-item fix list: composer contrast and Sessions design persistence.

## Colors

The palette is cool and restrained, with a mint accent for identity and input focus and a warm notice color for feedback. The frontmatter contains the exact application overrides; Textual's inherited widget theme still supplies button variants, tab states, table cursor and zebra stripes.

### Primary

- **Accent:** header text, focused input borders, focused conversation composer border/title and the confirmation boundary.

### Secondary

- **Warning:** the persistent notice line, including demo/connected mode and action feedback. Errors are also reported through this notice; there is no separate application error-color override.

### Neutral

- **Background:** screen and ticket table work area.
- **Panel:** header, footer and confirmation surface.
- **Foreground:** default screen text.
- **Muted:** profile, sprint and operational counts in the overview; session status and the unfocused conversation composer border/title.
- **Divider:** the horizontal boundary above ticket details.

### Readiness

- The Readiness label uses the terminal's own green (`Ready`) and dim red (`Waiting on KEY`, blocked reasons), plus a yellow `· check KEY` suffix for acceptance criteria that mention an unconfirmed ticket. These are the only state colors in the ticket table, and the label text still names the state in full.

**The Text Carries State Rule.** Selection, lifecycle, approvals and errors must remain understandable without color. Preserve full state labels and explicit messages.

## Typography

The application uses the terminal's monospaced font and cell metrics. It does not select a font family, point size, letter spacing or browser type scale. The `monospace` frontmatter value describes that terminal constraint. The native SVG evidence uses `Menlo, Consolas, monospace` at 20px with a 24.4px line height for capture presentation; external web-font declarations are removed and no network font is required. These export settings are not runtime requirements.

Hierarchy comes from placement and labels: application header, two-line operational overview, tab names, table column headers, section labels and body evidence. Plan content separates groups, blocked reasons, dependency/conflict edges, branch/worktree paths and verification commands with blank lines. Review packages use readable labeled sections with actual diff text, check results and command argument vectors. Ticket values use literal Rich `Text`; details, notices, plan and review text disable markup so bracketed content is preserved.

## Layout

The vertical shell is header → overview → eight-tab workspace → notice → footer. Tabs appear in this order: Tickets, Plan, Agents, Sessions, Approvals, Review, Activity, Settings. The workspace and content switcher fill remaining height and allow a zero minimum height so fixed chrome does not force the whole app past the viewport. The editor terminal uses header → shared session panel → notice → footer, without the dashboard's overview or tabs.

All measurements below are Textual cells: horizontal quantities are columns and vertical quantities are rows. Textual padding pairs mean vertical then horizontal.

| Element | Implemented geometry |
| --- | --- |
| Tab pane | Padding 1 cell on each side; remaining height; overflow hidden |
| Overview | Height 3; horizontal padding 2 |
| Filter row | Height 3; search fills remaining width; priority and status each width 22 |
| Ticket table | Remaining height; minimum 3 rows; row cursor; zebra stripes |
| Ticket details viewport | Height 5; horizontal padding 1; top divider; focusable vertical scroll |
| Details content | Automatic height; minimum 2 rows |
| Evidence document | Padding 1 row / 2 columns; remaining height; vertical scroll |
| Plan, agent, review, approval, activity content | Automatic height; minimum 4 rows |
| Action row | Automatic height; minimum 3 rows; horizontal layout and horizontal overflow scrolling |
| Buttons | Minimum width 12; right margin 1 |
| Notice | Height 2; left padding 2 |
| Settings editor | Remaining height; minimum 10 rows |
| Confirmation | Width 78; maximum width 95%; automatic height; maximum height 90%; padding 1 row / 2 columns; vertical scroll |
| Confirmation content/actions | Message bottom margin 1; action row height 3 |
| Session panel | Remaining height; minimum height 0; dashboard loader or fixed-ticket editor row height 3 |
| Session status | Height 3; horizontal margin 1; muted text |
| Session history viewport | Remaining height; minimum 2 rows; horizontal padding 1; focusable vertical scroll |
| Session location, history and recent output | Automatic height; literal text |
| Session composer | Height 5; minimum 3 rows; solid muted border/title normally, solid accent border/title on focus |
| Session actions | Separate horizontal action row below composer; shared terminal fixes height at 3 |

At **120×40**, the normal overview, details and notice heights apply. Tickets and details stack vertically within the Tickets tab; the table receives remaining height. Plan evidence scrolls above its fixed action row.

At **80×24**, the height breakpoint (`height < 30`) reduces overview to 2 rows, details viewport to 3 and notice to 1. It does not replace tabs with another navigation model or reflow fixed-width filters. Table columns may extend beyond the visible width and long content requires scrolling. Action rows retain horizontal overflow; keyboard focus can reach controls outside the initial horizontal view. Document scrolling preserves access to longer plan and review evidence while action rows remain separate.

The session composer is 5 rows at 120×40 and 3 rows at 80×24 in both hosts. The dashboard also reduces session status from 3 to 2 rows at the compact breakpoint; the editor terminal currently retains its 3-row status. Session history scrolls in the remaining area while the composer and actions stay outside that scrolling viewport. The editor terminal notice compresses from 2 to 1 row. These are terminal cell behaviors, not browser breakpoints.

**The Remaining Space Rule.** Let the table and evidence documents consume available space; shrink compact chrome and scroll content before losing operator actions.

## Elevation & Depth

There are no application shadows. Panel tone, the details divider and terminal borders separate work areas. Confirmation is centered over a modal background using Textual's `$background` at 75%; its solid accent border identifies the active decision surface. The modal itself scrolls when its evidence exceeds the available height.

## Shapes

The interface uses cell-aligned rectangles and terminal border glyphs. The details boundary and confirmation use solid borders; focused inputs use a tall accent border. The conversation composer uses a solid border in both states, with an explicit muted border and title normally and accent border and title on focus. No pixel radius scale or custom card system is defined. Buttons, tabs, table focus and other unmodified shapes follow Textual's theme.

## Components

### Ticket workspace and keyboard focus

The table columns are Pick, Ticket, Lifecycle, Priority, Summary, Jira status. Pick renders the literal strings `[x]` and `[ ]`; the row cursor identifies the inspected ticket independently of selection. Row highlighting updates ticket key, summary, lifecycle, description, acceptance criteria and blockers in the focusable details scroll area. Filtering matches key/summary, priority and status without case sensitivity; priority sorting uses Highest through Lowest.

On mount, focus starts in the table. Application bindings are Space to toggle a ticket, P to propose a plan, R to refresh, Ctrl+S to save selection and Q to quit. Textual supplies navigation between focusable controls. Proposing a plan activates Plan and focuses Approve plan; successful launch activates Agents and focuses its ticket-key input. Updates refresh the inspected ticket's lifecycle in details as well as table lifecycle cells, preserving the table cursor and active filters. Equivalent CLI commands remain available; terminal assistive-technology behavior is not established by the captures.

### Evidence and progress

The overview reports profile, mode, board, sprint, selected/total tickets, running/concurrency, blocked and review counts. There are no invented completion percentages. Plans show identity, base and fingerprint, execution groups/slots, blockers, edges, paths and exact verification argument vectors. Agents show lifecycle, PID, branch, worktree and recent text from the recorded active log. Active workers and verification show elapsed seconds when a start time is available; the half-second update loop refreshes logs and elapsed time even when no lifecycle or approval record changes. Source and the live-log behavioral test establish that dynamic behavior.

Review shows ticket/summary, branch/worktree, verification status, check names and pass/fail outcomes, exit codes, durations, exact commands and log paths. Labeled sections present changed files including new files, the actual implementation diff, acceptance criteria, worker summary, review warnings, known risks, feedback history and the loaded fingerprint. This readable evidence is scrollable and renders literally. Approvals show action, subject, actor or pending status, and fingerprint. Activity lists timestamped events.

### Human confirmation gates

Every confirmation presents **Confirm human approval** and **Go back**, with initial focus on **Go back**. Only an affirmative modal result runs the callback. The gate covers saving the exact selected keys (including empty selection), deferring a ticket through selection saving, approving a plan, rejecting a plan, launching workers, accepting changes, requesting changes with feedback, rerunning verification, deferring review, pausing/canceling a worker, sending a session follow-up and saving execution settings.

Planning approval and launch approval are separate decisions. The plan preview names its fingerprint; launch preview names queued tickets, concurrency and verification commands. Acceptance states that it creates a local commit on the worker branch. Pause/cancel previews state that worktree contents are preserved; deferring review states that work is preserved. Settings saving invalidates current plan approvals and is rejected while workers or manual verification execute. Push and draft-request approvals are exposed through the CLI, as the Approvals tab explains.

### Review and verification binding

Loading a review records the normalized ticket key together with the package fingerprint. Acceptance requires the current input key to match that loaded key, reloads the package and rejects a changed fingerprint. Its confirmation includes that fingerprint, and `engine.accept` receives it as `expected_fingerprint` for the final operation. Loading one ticket's evidence cannot authorize acceptance of another ticket or changed evidence.

Verify again previews the full current check definitions as JSON and the verification-context fingerprint. On confirmation, the callback compares the current fingerprint to the previewed value before recording approval and starting verification. Changed context requires another preview. Approval therefore covers the exact commands and context shown, rather than an unqualified permission to execute whatever settings exist later.

**The Inspected Evidence Rule.** Bind consequential approval to the ticket and evidence or command context actually shown. Preserve the mismatch errors and require a fresh preview when that context changes.

### Shared worker conversation

Agents adds **Open session** and **Open VS Code** for the entered worker ticket key. Open session activates Sessions, loads that ticket's validated branch/worktree and focuses its composer. Activating the Sessions tab by keyboard with a loaded session also explicitly focuses the composer through the screen's focus API; the real-key regression verifies that `p`, `q`, `r` and Space remain message text while composing. `SessionPanel` shares textual lifecycle/branch and readiness status, a focusable scrolling region containing continuity label, exact worktree path, recent timestamped conversation and recent worker output, and the **Message to worker** composer. The dashboard supplies a ticket-key input and **Load session**, preserving per-ticket drafts while switching; a changed input key must be loaded before acting. The editor terminal is fixed to its opened ticket and worktree: its static heading reads **KEY · Conversation for this worktree** (for example, **DEMO-1 · Conversation for this worktree**) beside **Refresh**, with no ticket selector. Its draft therefore belongs to that single conversation. **Send follow-up** and **Pause worker** stay below the composer in both hosts; the dashboard also offers **Open VS Code**. Ctrl+Q closes the terminal client. Go back preserves a declined message in either host.

Conversation entries show role, UTC timestamp and literal message text. Full history persists in private profile SQLite storage, while the visible/context window is bounded; the panel caps rendered history at 100,000 characters. New messages follow the bottom only when the reader was already there, preserving a scrolled reading position. This is the explicitly selected shared conversation view in both hosts. It uses supervised headless coding turns, not Gemini's native interactive terminal UI.

Send is disabled while the batch is busy and requires an eligible completed or paused ticket with prior approved scope. The exact confirmation names ticket, branch, worktree, continuity mode, message, check commands, required/optional flags and timeouts. A question also authorizes a coding turn. Approval binds the message to the history revision, prior worker turn, lifecycle, branch/worktree/tree fingerprint, native session ID, approved plan/scope, configuration and checks. Both views revalidate that context; stale previews and replayed consumed approvals are rejected. Changed scope, configuration or prerequisites require a fresh approved plan. A turn reuses the recorded worktree, invalidates prior acceptance and affected downstream review evidence, reruns checks and returns to separate Review for fresh acceptance. Pause preserves the worker's worktree and conversation; remaining workers or checks must finish before sending.

Continuity is explicitly labeled: synthetic demo, exact native Gemini UUID when available, or **Continuing from saved task context; native Gemini session ID unavailable**. Resume uses that UUID only when the installed CLI supports it; it does not guess another session. Missing capability and mismatched returned IDs surface errors. Saved-context continuation remains visibly distinct from native provider-session resumption.

### Editor worktree and process ownership

Open VS Code validates the recorded worktree, branch and shared Git metadata, then invokes `code --new-window` on a private generated `.code-workspace`. Its folder and process-task working directory are the existing worker checkout; it changes no main-repository branch and writes no source `.vscode` settings. The **Gemini session: KEY** process task opens the shared terminal client fixed to that workspace's ticket, subject to VS Code trust and automatic-task permission. The documented fallback is Terminal → Run Task; trust checks remain in force.

The main dashboard owns model processes and a private Unix-socket coordinator. Editor clients share its snapshots, messages, confirmations and pause controls; opening a client does not start a second independent model process. The main dashboard must stay running. Closing an editor client leaves workers running; closing the dashboard stops supervised workers and the bridge. Connection failures disable send/pause and explain that the dashboard must be reopened before reloading. SSH/editor terminals must reach that same filesystem and socket.

### Finish-review disposition

The preceding core-terminal finish review recorded **ship**, with no remaining material fixes, at its evidence scope. The final shared-conversation finish review records **SHIP at two-fix-list scope**: unfocused composer contrast is resolved by the visible muted border/title and distinct mint focus treatment in the final captures; Sessions design persistence is resolved by this document. This verdict closes those two findings only; it does not certify the whole product, installed VS Code GUI behavior or real Gemini account/history compatibility. The earlier five material findings remain resolved:

| Material fix | Recorded disposition | Implementation evidence |
| --- | --- | --- |
| Compact viewport reachability | Resolved | Height compression, scrollable documents and separate overflow action rows |
| Keyboard access to full ticket details | Resolved | Focusable `VerticalScroll` for details |
| Reliable selection display | Resolved | Literal `[x]` / `[ ]` rendered with Rich `Text` |
| Acceptance bound to inspected review | Resolved | Loaded key/fingerprint checks and expected fingerprint passed to acceptance |
| Verification bound to exact approved commands | Resolved | Full command preview and verification fingerprint recheck before execution |

The core pass also hardened readable review evidence, active log/elapsed refresh, inspected-ticket lifecycle refresh and settings exclusion during manual verification. Source and behavioral tests support those dynamic guarantees; the five core 120×40 PNGs support visual review of their captured states. Shared-session evidence adds exercised dashboard/editor-terminal views at both sizes with the launch and provider limits described above. This records supplied review/test evidence; it does not certify enterprise integration or replace final code validation.

## Do's and Don'ts

- Do preserve textual state, literal bracketed content and visible action names.
- Do keep long details and evidence keyboard-accessible through scrolling.
- Do retain the 120×40 and 80×24 terminal capture cases when changing geometry.
- Do preview the exact subject and context of consequential actions, with Go back initially focused.
- Do preserve exact ticket, branch, worktree and continuity labels in both conversation views, with scrolling history and separate composer/actions.
- Do preserve the draft on a declined follow-up and require fresh confirmation after session context changes.
- Don't collapse planning, launch, verification and implementation acceptance into one approval.
- Don't treat a conversation message as read-only or let a second client create an independent model process.
- Don't accept a different ticket or changed evidence under a previously loaded review.
- Don't substitute color or invented progress percentages for durable evidence.
- Don't introduce browser font, pixel-radius or mobile-platform assumptions into this terminal system.
