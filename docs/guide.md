# User guide

## Installation and first run

Install Python 3.11+ and Git, create a virtual environment, then `pip install -e '.[dev]'` from this source checkout. `gemini-orchestrator` offers setup when no valid profile exists. `--help` lists all commands; global `--home` and `--profile` go before the subcommand.

The wizard asks for a preferred name, optional email, team, Git checkout and worktree directory, Gemini executable/limits, integration choice, verification commands and hosting. A display name never substitutes for a Jira account ID. Only a confirmed authenticated identity enables ticket discovery. You may configure local settings now and connect later on a work system.

MCP discovery can connect to configured servers, so it has a separate explicit prompt. Choose the exact Jira server when there is more than one. Project and board names are separate resources. Setup lists stable IDs, supports name search for longer lists, manual IDs when discovery is unsupported, multiple projects/boards, and an explicitly chosen board/sprint. Refresh or edit the profile when a sprint changes. Active sprint behavior rejects a stale sprint when the adapter can discover it; manual behavior uses the confirmed configured ID.

The final summary lets you edit identity, team, Jira resources, repository, Gemini limits, verification and hosting before saving. Initial diagnostics do not launch agents or contact Jira.

## Profiles

`profile create`, `profile list`, `profile switch NAME`, `profile edit [NAME]`. Use `--profile NAME` for a single invocation. Files live in `$GSO_HOME/profiles`, defaulting to `~/.local/share/gemini-orchestrator/profiles`. Configuration files are mode 0600; directories are 0700. Keep separate profiles for different repositories or teams. Demo profiles cannot be used to open enterprise adapters.

Changing identity, repository, board or sprint starts a distinct state scope under `runtime/PROFILE/sessions/SCOPE`; previous databases, worktrees and reviews remain available. Demo creation does not replace the active connected profile.

## Dashboard

Tickets: type a key/summary search or priority/status filter. Space or **Toggle ticket** changes the pending selection. **Save selection** records human intent after confirmation. **Defer ticket** saves a selection without that ticket. The priority sort action sorts the table; selection and lifecycle persist across restarts.

Plan: inspect groups, worker slots, blockers, dependency reasons, branches, worktree paths and verification. Confirm plan approval, then separately confirm worker launch. Reselect tickets and propose another plan to edit; prior grants become invalid. Launching downstream work waits for human acceptance of prerequisite commits. After review, approve a fresh launch for blocked downstream work.

Agents: see actual lifecycle, PID, branch, worktree and recent worker output. Pause/cancel stops owned subprocess groups and preserves files. There is no fabricated progress percentage. The approval queue lists every outstanding gate. Activity records transitions and decisions.

Review: enter a ticket key and load its package. Inspect diff, file list, untracked files, checks, logs, feedback and known risks. Accept after review, request changes with feedback, rerun verification or defer review. Use CLI `review KEY --accept --manual-verified` only when explicitly attesting manual verification after unavailable/failed checks. Accepting an implementation makes a local branch commit; it does not push or merge.

Settings: edit concurrency, timeout and checks as structured JSON. Saving invalidates execution approvals. For identity, board, sprint, paths or hosting, close the dashboard and use `profile edit`. The CLI remains available for screen-reader workflows and very small terminals.

## Worktrees and continuation

Each approved ticket gets a `gso/KEY-PLAN` branch and an external worktree path. Main checkout must be clean when planning. Collisions fail without deleting or resetting anything. A single accepted prerequisite becomes the dependent worktree base. Multiple prerequisite commits must already form an ancestor chain; otherwise manually integrate on a human-controlled branch and replan. Protected branches are never worker targets.

After feedback, use `retry KEY`, inspect the new plan, approve it and run. Existing worktree, feedback and task context are retained. Native Gemini sessions are not required. Interrupted and failed work uses this same continuation path after inspection.

## Verification

Setup detects suggestions from common manifest files but never executes them. Add unit/integration/lint/type/build/smoke/reviewer commands as executable argument arrays. No shell expansion occurs. For shell syntax, configure an explicit shell executable only after reviewing its script; it is an approved project command with your account's authority. Launch approval displays the exact commands. Unknown/no checks produce "manual required", not success. Failed checks stay visible in review. Rerun verification after editing implementation. Worker failures have bounded retries; verification failure does not trigger an unapproved automatic repair loop.

## Local board and retention

`board` writes `TASK_BOARD.md` atomically in the active scope's runtime directory. It includes identity, team, sprint, states, PIDs, branches, worktrees, dependencies, checks, approvals and activity. It can contain sensitive work data; keep it on the approved system. Runtime logs/reviews never belong in this generic source repository.

For retention, stop all dashboard/workers, inspect `git worktree list` and each worktree's status, archive any needed commits/files, then manually remove only data you explicitly choose. There is intentionally no automatic deletion command. Never delete dirty worktrees or SQLite WAL files while a process is active.

## SSH and tmux

Run `tmux new -s sprint`, activate the virtual environment and open the dashboard. Detach with the normal tmux keybinding; the application keeps running. Reconnect with `tmux attach -t sprint`. A terminal disconnect without tmux may interrupt the process; restart shows INTERRUPTED rather than assuming workers survived. Recommended size is 120×40; 80×24 uses scrollable panels and the CLI is an equivalent alternative.

## Troubleshooting

- No active profile: run setup, demo, or `profile switch NAME`.
- Jira identity differs: confirm authentication on the correct approved account and rerun setup. Do not edit account IDs to impersonate someone.
- Sprint expired: refresh resources in profile edit and confirm the correct sprint; old state remains isolated.
- MCP read semantics missing: use a compatible server or GET-only REST fallback. Never weaken annotations or tool allowlists.
- Worktree collision: inspect existing branch/worktree, preserve files and select a new plan; no force reset is provided.
- Worktree changed after verification: rerun verification and inspect a fresh review package.
- Session already open: close the other coordinator for that profile/scope. Do not remove its lock file while it runs.
- Gemini blocked by sandbox/policy/auth cache: see integrations and enterprise instructions. An unavailable safe worker is a real error, not mock success.

## Semantic planning and manual dependency corrections

On the approved connected system, `plan --semantic --enable-connected` asks Gemini for advisory recommendations using bounded README/GEMINI/manifest context, declared relevant source files, the tracked source tree and local branches. A deny-all tool policy prevents the analysis from invoking MCP, shell or edit tools. The model cannot mark its guesses confirmed or schedule work. Ticket references found by deterministic text matching are labeled unknown, not AI-inferred.

Inspect every inferred dependency. To promote/correct a relationship explicitly, use:

```sh
gemini-orchestrator dependencies APP-12 --requires APP-11 --file src/example.py
# Multiple --requires / --conflicts / --file options are supported.
gemini-orchestrator plan --approve
```

This edits local planning metadata only; Jira is unchanged. It returns the previous queued plan to selection and revokes all previous plan/launch grants while retaining their audit records. Repeated discovery may replace manual context with current Jira data, so review the next plan.

Manual verification reruns have their own durable approval, bound to the exact command list and current implementation. Changing checks or files invalidates that grant. In the dashboard, acceptance is bound to the loaded review key and fingerprint; changing the input key or implementation requires a new loaded review.

Review includes new, untracked source files in the patch. Binary or diffs larger than 1 MB require inspecting the complete worktree changes outside the abbreviated terminal view, then `review KEY --accept --full-diff-reviewed` with a separate explicit attestation. Worker and verification logs retain unique paths across reruns and each process log is capped at 8 MiB.

## Shared conversations and editor windows

Use Agents → Open session or Open VS Code for a launched ticket. The Sessions tab provides the same message/confirmation workflow used by the editor terminal. See [the session guide](sessions.md).
