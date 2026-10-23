# Scripted synthetic demo walkthrough

This walkthrough ran the actual Textual application with scripted test-operator button/key interactions. It did not contact Jira, invoke a model account or write to a remote repository. It uses the same durable approval and execution paths as the interactive demo; the harness is a developer test, not a product API for agents to approve work.

The [machine-readable report](report.json) records the actual steps and final states:

1. Discovered six synthetic tickets and selected four through the explorer; the Readiness column showed DEMO-3 waiting on DEMO-1 and DEMO-6 waiting on the unavailable DEMO-99.
2. Pressed **Start selected** and confirmed once: readiness, dependencies, template branch names and checks.
3. Gemini (synthetic) drafted read-only implementation plans for DEMO-1 and DEMO-2; their worktrees stayed unchanged. DEMO-3 and DEMO-6 stayed blocked.
4. Commented on the DEMO-1 plan; a revised plan addressed the comment. Approved both plans, which implemented them in real worktrees and ran verification.
5. Loaded and accepted the verified prerequisite implementations through review confirmations.
6. Started dependent DEMO-3, approved its plan, then verified and accepted it.
7. Requested a change to DEMO-2 and started it again. Its approved plan was kept, so it continued implementing in the original worktree without re-planning; earlier logs were preserved.
8. Accepted the continuation, closed the app, reloaded the saved profile and reopened SQLite state. Accepted states and feedback survived.

The run recorded 19 granted approval records. DEMO-1/2/3 finished APPROVED. DEMO-4/5 remained unselected. DEMO-6 was deselected by the later, narrower starts; its earlier blocked state is recorded in the report.

![Selection](01-selection.png)

![Gemini implementation plan awaiting review](02-plan.png)

![Workers and held dependencies](03-workers-and-blockers.png)

![Review with verification and actual new-file diff](04-review.png)

![Accepted continuation](05-accepted-continuation.png)

Captures are native Textual SVG exports, rendered to PNG with headless Chrome using a system monospaced font. The capture sanitizer replaces the capturing account's home folder name. External web-font declarations were removed. Content is synthetic, including identities, ticket keys and temporary paths. PNG captures were visually inspected; full SSH/tmux and assistive-technology behavior remains target-system validation work.

Reproduce with `python scripts/demo_walkthrough.py --output /tmp/new-synthetic-demo-directory` after installing development dependencies. The output directory must be new so old evidence is preserved. The normal user demo remains `gemini-orchestrator demo` with interactive confirmations.
