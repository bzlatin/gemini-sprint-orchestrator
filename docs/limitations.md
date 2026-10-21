# Known limitations

The demo and automated tests validate the local lifecycle. They do not establish compatibility with a particular enterprise Gemini, Jira MCP, GitLab or GitHub installation.

- Jira Cloud REST is supported; Jira Server/Data Center differences need a dedicated adapter. Teams are manual labels because Jira team APIs differ by deployment.
- MCP must expose known read-only semantic names, explicit safety annotations, compatible schemas and structured JSON. Managed Gemini OAuth/extension-only/IAP connections cannot be transparently reused. Unknown tools are disabled, not guessed.
- Active sprint validation requires sprint discovery; manually configured unsupported MCP resources rely on explicit human confirmation. Ticket comments embedded by Jira may be truncated; full comment pagination is not implemented.
- Worker tool restrictions intentionally prevent shell/MCP/custom-agent use. Python runs approved verification commands separately. Gemini versions needing unrestricted cache/auth writes may fail under OS confinement; target-system validation is required.
- Declared issue dependencies and overlapping declared paths inform deterministic scheduling. Full semantic repository conflict detection is advisory, not proof that independent workers cannot conflict. There is no automatic merge of divergent prerequisites.
- Parent guardian/process recovery is implemented for macOS/Linux. Subprocesses that deliberately escape their process group are outside the trusted-project-command model. No native Gemini session resumption is assumed.
- SQLite schema 1 rejects incompatible versions. There is no automatic schema migration or built-in archival deletion. Each process log is capped at 8 MiB; historical logs have no automatic rotation; apply a reviewed retention policy and monitor free disk space.
- One coordinator per profile/scope is allowed. Some CLI operations require closing the active dashboard to acquire the same session lock.
- Remote adapter behavior is mocked in tests; actual CLI versions and auth require target validation. Draft creation failures consume the grant so retries need fresh confirmation.
- The source is licensed under MIT. Repository visibility remains an owner decision: [bzlatin/gemini-sprint-orchestrator](https://github.com/bzlatin/gemini-sprint-orchestrator).

Optional Gemini semantic analysis (`plan --semantic --enable-connected`) is advisory and may miss conflicts. Source excerpts are bounded and limited to declared relevant paths; it does not claim a complete semantic codebase index. Request/branch retrieval adapters exist, but live remote MR/PR context is not automatically included in planning.
