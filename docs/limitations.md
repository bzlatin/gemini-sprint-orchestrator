# Known limitations

The demo and automated tests validate the local lifecycle. They do not establish compatibility with a particular enterprise Gemini, Jira MCP, GitLab or GitHub installation.

- Jira Cloud REST is supported. Jira Server/Data Center is supported through the listed MCP schemas and identity fields; direct Server/Data Center REST is not implemented. Teams are manual labels because Jira team APIs differ by deployment.
- MCP must expose known read-only semantic names, no explicit unsafe annotation hints, compatible schemas and structured JSON. Managed Gemini OAuth/extension-only/IAP connections cannot be transparently reused. Unknown tools are disabled, not guessed.
- Active sprint validation requires sprint discovery; manually configured unsupported MCP resources rely on explicit human confirmation. Ticket comments embedded by Jira may be truncated; full comment pagination is not implemented.
- Worker tool restrictions intentionally prevent shell/MCP/custom-agent use. Python runs approved verification commands separately. Gemini versions needing unrestricted cache/auth writes may fail under OS confinement; target-system validation is required.
- Declared issue dependencies and overlapping declared paths inform deterministic scheduling. Full semantic repository conflict detection is advisory, not proof that independent workers cannot conflict. There is no automatic merge of divergent prerequisites.
- Parent guardian/process recovery is implemented for macOS/Linux. Subprocesses that deliberately escape their process group are outside the trusted-project-command model. Native Gemini continuation requires an advertised --resume flag and a captured session UUID; otherwise the shared conversation uses saved task context. Actual auth/history/cache compatibility still requires target-system validation.
- SQLite schema 1 rejects incompatible versions. There is no automatic schema migration or worktree/database archival deletion. Each process log is capped at 8 MiB; historical logs have no automatic rotation. The storage command can explicitly prune non-current logs after preview/confirmation; soft watchdog limits are not filesystem quotas.
- One coordinator per profile/scope is allowed. Some CLI operations require closing the active dashboard to acquire the same session lock.
- Remote adapter behavior is mocked in tests; actual CLI versions and auth require target validation. Draft creation failures consume the grant so retries need fresh confirmation.
- The source is licensed under MIT. The public repository is: [bzlatin/gemini-sprint-orchestrator](https://github.com/bzlatin/gemini-sprint-orchestrator).

Optional Gemini semantic analysis (`schedule --semantic --enable-connected`) is advisory and may miss conflicts. Source excerpts are bounded and limited to declared relevant paths; it does not claim a complete semantic codebase index. Request/branch retrieval adapters exist, but live remote MR/PR context is not automatically included in planning.

Shared session clients use a private local Unix socket and require the dashboard to stay running on the same machine or Remote SSH environment. VS Code must be installed; automatic terminal tasks require the user's workspace trust/task permission. Follow-ups wait for the batch to become idle and remain bounded by the existing approved ticket scope. The conversation view retains a recent window (up to 100 records / 60,000 characters); full recorded entries remain in private SQLite runtime data.

Version 0.3.0 deliberately stops inheriting the normal Gemini home. Existing profiles require fresh plan approval; approved authentication must be provisioned for isolated worker/ask homes. Verification is network-free. The editor shortcut refuses execution-bearing agent/editor configuration. See [hardening](hardening.md) for migration and target checks.
