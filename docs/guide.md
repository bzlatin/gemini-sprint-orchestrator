# User guide

## Installation and first run

Install Python 3.11+ and Git, create a virtual environment, then `pip install -c requirements.lock -e .` from this source checkout (or use the Gemini CLI install prompt in the README). `gemini-orchestrator` offers setup when no valid profile exists. `--help` lists the core workflow first; global `--home` and `--profile` go before the subcommand. See the README's *Updating* section for upgrades.

The wizard asks only for your name, the repository (default: the current Git checkout), branch naming, the Jira connection and verification checks. It detects the Gemini CLI, places worktrees next to the repository and defaults to the single Jira-named server in your Gemini MCP settings. Email, team, hosting, concurrency, timeouts, retries, worker behavior and plan-first are editable from the summary before saving. A display name never substitutes for a Jira account ID. Only a confirmed authenticated identity enables ticket discovery. You may configure local settings now and connect later on a work system.

MCP discovery can connect to configured servers, so it has a separate explicit prompt. Choose the exact Jira server when there is more than one. Project and board names are separate resources. Setup lists stable IDs, supports name search for longer lists, manual IDs when discovery is unsupported, multiple projects/boards, and an explicitly chosen board/sprint. Refresh or edit the profile when a sprint changes. Active sprint behavior rejects a stale sprint when the adapter can discover it; manual behavior uses the confirmed configured ID.

The final summary lets you edit identity, team, Jira resources, repository, Gemini limits, verification and hosting before saving. Initial diagnostics do not launch agents or contact Jira.

## Profiles

`profile create`, `profile list`, `profile switch NAME`, `profile edit [NAME]`. Use `--profile NAME` for a single invocation. Files live in `$GSO_HOME/profiles`, defaulting to `~/.local/share/gemini-orchestrator/profiles`. Configuration files are mode 0600; directories are 0700. Keep separate profiles for different repositories or teams. Demo profiles cannot be used to open enterprise adapters.

Changing identity, repository, board or sprint starts a distinct state scope under `runtime/PROFILE/sessions/SCOPE`; previous databases, worktrees and reviews remain available. Demo creation does not replace the active connected profile.

## Dashboard

Tickets: type a key/summary search or priority/status filter. Space or **Toggle ticket** changes the pending selection. *Readiness* shows `Ready` or `Waiting on KEY` (a prerequisite not yet accepted). Optional branch names go in the branch field as `KEY=name, KEY=name`. **Start selected** (`s`) shows one confirmation with readiness, dependencies, conflicts, branch names and checks; confirming selects, schedules and authorizes exactly that preview. If anything changed while you read it, it is refused and you review again.

Plans: tickets waiting for your decision are listed. Enter a key and **Load plan** to read Gemini's implementation plan. **Approve plan** lets Gemini implement exactly that plan; **Send comment** asks Gemini to revise it in the same session; **Reject plan** cancels the ticket and preserves its worktree. A decision is refused unless the loaded plan is still the current one. Dependent tickets start planning only after you accept their prerequisites.

Agents: see actual lifecycle, PID, branch, worktree and recent worker output. Pause/cancel stops owned subprocess groups and preserves files. There is no fabricated progress percentage. The approval queue lists every outstanding gate. Activity records transitions and decisions.

Review: enter a ticket key and load its package. Inspect diff, file list, untracked files, checks, logs, feedback and known risks. Accept after review, request changes with feedback, rerun verification or defer review. Use CLI `review KEY --accept --manual-verified` only when explicitly attesting manual verification after unavailable/failed checks. Accepting an implementation makes a local branch commit; it does not push or merge.

Settings: edit concurrency, timeout and checks as structured JSON. Saving invalidates execution approvals. For identity, board, sprint, paths or hosting, close the dashboard and use `profile edit`. The CLI remains available for screen-reader workflows and very small terminals.

## Implementation plans

With `gemini.plan_first` (the default), every ticket's first Gemini turn is planning only. Gemini receives the full Jira ticket as untrusted data and must reply with its understanding, files to change, numbered steps, tests, risks and open questions. The turn uses read-only tools, the same isolated worker home as implementation, and an OS sandbox that denies worktree writes; any worktree change fails planning. CLI equivalents:

```sh
gemini-orchestrator plan APP-1                    # read the plan and its decision
gemini-orchestrator plan APP-1 --comment "TEXT"   # Gemini revises the plan
gemini-orchestrator plan APP-1 --approve          # Gemini implements the approved plan
gemini-orchestrator plan APP-1 --reject [--comment REASON]
```

Approval is bound to the exact plan text, ticket data and worktree state. The implementation turn resumes the planning session when Gemini supports it and also receives the approved plan explicitly. Set `plan_first` to `false` in the profile to implement directly after starting.

## Worktrees and continuation

Each started ticket gets a branch from the profile's `branch_template` (default `{type}/{key}-{slug}`; fields `{key}`, `{slug}`, `{type}`, `{id}`) or the name you entered, plus an external worktree path. Invalid, duplicate, protected and existing branch names are refused before anything starts. Main checkout must be clean when planning. Collisions fail without deleting or resetting anything. A single accepted prerequisite becomes the dependent worktree base. Multiple prerequisite commits must already form an ancestor chain; otherwise manually integrate on a human-controlled branch and replan. Protected branches are never worker targets.

After feedback, use `retry KEY` (or **Start selected**). The approved implementation plan, existing worktree, feedback and task context are retained, so Gemini continues implementing without re-planning. Native Gemini sessions are not required. Interrupted and failed work uses this same continuation path after inspection.

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
- MCP compatibility: select the exact server name in the chosen settings file. Known read tools may omit annotation hints; unknown or explicitly unsafe tools remain blocked. Use manual stable resource IDs when discovery is unavailable. See integrations.md for Server/Data Center support.
- Worktree collision: inspect existing branch/worktree, preserve files and select a new plan; no force reset is provided.
- Worktree changed after verification: rerun verification and inspect a fresh review package.
- Session already open: close the other coordinator for that profile/scope. Do not remove its lock file while it runs.
- Gemini blocked by sandbox/policy/auth cache: see integrations and enterprise instructions. An unavailable safe worker is a real error, not mock success.

## Semantic planning and manual dependency corrections

On the approved connected system, `schedule --semantic --enable-connected` asks Gemini for advisory recommendations using bounded README/GEMINI/manifest context, declared relevant source files, the tracked source tree and local branches. A deny-all tool policy prevents the analysis from invoking MCP, shell or edit tools. The model cannot mark its guesses confirmed or schedule work. Ticket references found by deterministic text matching are labeled unknown, not AI-inferred.

Inspect every inferred dependency. To promote/correct a relationship explicitly, use:

```sh
gemini-orchestrator dependencies APP-12 --requires APP-11 --file src/example.py
# Multiple --requires / --conflicts / --file options are supported.
gemini-orchestrator work APP-11 APP-12
```

This edits local planning metadata only; Jira is unchanged. It returns the previous queued plan to selection and revokes all previous plan/launch grants while retaining their audit records. Repeated discovery may replace manual context with current Jira data, so review the next plan.

Manual verification reruns have their own durable approval, bound to the exact command list and current implementation. Changing checks or files invalidates that grant. In the dashboard, acceptance is bound to the loaded review key and fingerprint; changing the input key or implementation requires a new loaded review.

Review includes new, untracked source files in the patch. Binary or diffs larger than 1 MB require inspecting the complete worktree changes outside the abbreviated terminal view, then `review KEY --accept --full-diff-reviewed` with a separate explicit attestation. Worker and verification logs retain unique paths across reruns and each process log is capped at 8 MiB.

## Shared conversations and editor windows

Use Agents → Open session or Open VS Code for a launched ticket. The Sessions tab provides the same message/confirmation workflow used by the editor terminal. See [the session guide](sessions.md).
