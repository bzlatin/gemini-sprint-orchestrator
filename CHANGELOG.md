# Changelog

## 0.5.0 — plan first, simpler flow

- Every ticket's first Gemini turn now drafts a detailed implementation plan from the full Jira ticket, with read-only tools and OS write denial. Coding starts only after a human approves that exact plan; comments make Gemini revise it in the same session, and rejecting stops the ticket and keeps its worktree.
- New `work KEY...` and `plan KEY [--approve|--comment|--reject]` commands; the dashboard has **Start selected** and a **Plans** tab. One confirmation replaces the separate select, plan-approval and launch-approval steps.
- Branch names come from a configurable `branch_template` (default `{type}/{key}-{slug}`) and can be overridden per ticket; every name is shown before work starts. Branches no longer need the `gso/` prefix; protected and existing branches are still refused.
- Ticket lists and `status` show readiness (`Ready` / `Waiting on KEY`) and each ticket's next step. Accepted prerequisites no longer block a newly started dependent.
- The scheduler fills free worker slots as plans are approved or prerequisites accepted, instead of running fixed groups.
- The old dependency `plan` command is now `schedule`; less common commands are listed under *Advanced* in `--help`.
- Existing profiles gain `plan_first` and `branch_template`, which changes their configuration fingerprint, so start work again after upgrading.

## 0.4.0 — Jira MCP compatibility

- Added Jira Server/Data Center account-name/key normalization and common search/project tool aliases.
- Accepted missing annotation hints only for exact known read operations; explicit unsafe hints, unknown tools and include/exclude restrictions remain enforced.
- Rendered numeric sprint IDs as JQL literals while escaping nonnumeric values.
- Supported list and object search results, with malformed/repeated-page rejection.
- Validated local MCP settings and non-empty exact server selection before optional connectivity.
- Defaulted missing resource discovery to manual stable IDs, including manual sprint behavior.
- Direct REST remains Cloud v3; live enterprise MCP compatibility requires target validation.

## 0.3.0 — execution isolation

- Sandboxed verification with network disabled, clean environments and restricted host reads; no unsafe fallback.
- Isolated per-ticket provider homes, read-only questions and advisory analysis.
- Protected worker/editor configuration and ticket-bound rotating editor bridge credentials.
- Added soft storage watchdogs, runtime preflight limits and explicit historical-log retention.
- Added a synthetic/opt-in real-provider target validation harness and mandatory Linux sandbox CI coverage.
- Scheduled weekly dependency/secret auditing and Dependabot update proposals.
- Authentication no longer inherits the personal Gemini home; existing profiles require fresh plan approval.

## 0.2.0 — shared worker conversations

- Added a Sessions tab with persisted messages, replies, output, drafts and explicit follow-up approval.
- Added Open session and Open VS Code controls for recorded worker worktrees.
- Generated private VS Code workspaces with a shared conversation terminal task; no checkout switching or repository task-file edits.
- Added a private Unix-socket bridge and `session KEY` client, with exact-context confirmation and replay rejection.
- Captured and resumed exact native Gemini session UUIDs when supported, with labelled saved-context continuation for older runs.
- Preserved supervision of other workers when pausing one member of a batch.
- Reran verification and invalidated acceptance/downstream review when continuing a session.

## 0.1.1 — public alpha

- Added the owner-selected MIT license and tested dependency constraints.
- Serialized shared Git metadata mutations across processes and profiles.
- Covered prompt delivery, output draining and cancellation with process deadlines;
  bounded worker logs to 8 MiB and cleaned up on input/callback errors.
- Preserved prior worker/verification logs across feedback and reruns.
- Refreshed live output and elapsed time without waiting for a lifecycle change.
- Included new, untracked implementations in the review diff and file list.
- Required explicit outside-terminal review for binary or truncated changes.
- Pinned remote approvals to the source commit, destination repository and exact
  draft request title/body; displayed those details before confirmation.
- Added a reproducible Textual demo walkthrough covering approvals, dependency
  blocking, feedback, continuation and restart persistence.

## 0.1.0 — private evaluation

Initial local-first package, onboarding, profiles, Jira adapters, deterministic
planning, human approvals, worktrees, supervised workers, verification and review.
