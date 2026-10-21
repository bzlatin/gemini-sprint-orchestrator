# Public alpha release readiness — 0.1.1

This release is prepared for public evaluation of the local application and synthetic demo. Connected enterprise deployment is a separate approval and compatibility exercise. It is not advertised as production-certified automation.

| Gate | Evidence |
|---|---|
| License | Owner selected MIT; LICENSE and package metadata included |
| Offline lifecycle | Full Textual walkthrough completed with real Git worktrees and persistent approval gates |
| Review integrity | New files included in diffs; loaded fingerprint rechecked; binary/truncated changes need explicit full-review attestation |
| Process reliability | Prompt delivery and output are within deadlines; cancellation cleanup; 8 MiB per-log cap; prior logs retained |
| Parallel Git | Shared metadata mutation lock with six-process regression test |
| Remote control | Exact source SHA, destination repository and request content bound to approval |
| Dependencies | Tested constraints and runtime inventory; pip-audit found no known vulnerabilities in the pinned runtime set |
| Source disclosure | Existing history and current tracked source scanned offline; synthetic credential fixtures individually annotated |
| CI | [All five jobs passed](https://github.com/bzlatin/gemini-sprint-orchestrator/actions/runs/39665272473): macOS/Linux, Python 3.11/3.13 and security |
| Package | 0.1.1 wheel/sdist built; fresh installed wheel passed CLI validation and the full Textual walkthrough |

The public-use entry point is the synthetic demo. Before connecting company repositories, validate the actual Gemini version, auth/cache writes, MCP identity/search safety, sprint scope, sandbox behavior and hosting credentials on the approved work system. The existing worker OS policy restricts writes; the trusted Gemini process still has local read/model-provider access. Configured verification scripts run with your account's authority after command approval.

Remaining intentional limits include no managed-OAuth MCP credential scraping, custom agent/skill reuse disabled, no Jira Server/Data Center adapter, no automatic merge of divergent prerequisites, no automatic database migrations, and manual retention of historical runtime data. See limitations.md and enterprise.md.

Repository visibility and public release publication remain explicit owner decisions. No enterprise data was introduced during this development work.
