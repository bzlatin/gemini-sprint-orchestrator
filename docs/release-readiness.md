# Public alpha release readiness — 0.2.0

This release is prepared for public evaluation of the local application and synthetic demo. Connected enterprise deployment is a separate approval and compatibility exercise. It is not advertised as production-certified automation.

| Gate | Evidence |
|---|---|
| License | Owner selected MIT; LICENSE and package metadata included |
| Shared sessions | Private local bridge, exact-context one-use follow-ups, fixed-ticket editor client, same worktree, re-verification and fresh review |
| Editor opening | Recorded branch/worktree validated; private process task; isolated client imports; no main checkout switch |
| Offline lifecycle | Full Textual walkthrough completed with real Git worktrees and persistent approval gates |
| Review integrity | New files included in diffs; loaded fingerprint rechecked; binary/truncated changes need explicit full-review attestation |
| Process reliability | Prompt delivery and output are within deadlines; cancellation cleanup; 8 MiB per-log cap; prior logs retained |
| Parallel Git | Shared metadata mutation lock with six-process regression test |
| Remote control | Exact source SHA, destination repository and request content bound to approval |
| Dependencies | Tested constraints and runtime inventory; pip-audit found no known vulnerabilities in the pinned runtime set |
| Source disclosure | Existing history and current tracked source scanned offline; synthetic credential fixtures individually annotated |
| CI | [0.2.0 validation](https://github.com/bzlatin/gemini-sprint-orchestrator/actions/runs/39707440075) passed all five jobs: macOS 116 passed; Ubuntu 113 passed / 3 platform skips, on Python 3.11 and 3.13 |
| Package | 0.2.0 wheel/sdist and installed-wheel checks recorded in validation.md |

The public-use entry point is the synthetic demo. Before connecting company repositories, validate the actual Gemini version, auth/cache writes, MCP identity/search safety, sprint scope, sandbox behavior and hosting credentials on the approved work system. The existing worker OS policy restricts writes; the trusted Gemini process still has local read/model-provider access. Configured verification scripts run with your account's authority after command approval.

Remaining intentional limits include no managed-OAuth MCP credential scraping, custom agent/skill reuse disabled, no Jira Server/Data Center adapter, no automatic merge of divergent prerequisites, no automatic database migrations, and manual retention of historical runtime data. See limitations.md and enterprise.md.

The owner authorized public repository visibility after the release checks passed. GitHub confirmed the repository is public on 2026-10-21. Package-registry publication is a separate operation. No enterprise data was introduced during this development work.
