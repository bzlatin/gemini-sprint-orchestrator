# Security model

## Boundaries

The coordinator runs locally as the developer. It is not a multi-user service, an authentication proxy or a general hostile-code sandbox. Keep enterprise configuration and runtime data on the approved enterprise machine. Demo uses only generated data and never instantiates enterprise adapters.

Connected data access requires `--enable-connected` or an explicit setup confirmation. Remote writes are disabled by default and have separate push/draft approvals. Jira mutation operations do not exist. No automatic merge, deployment or destructive cleanup is provided. The owner selected MIT. Changing repository visibility or publishing releases remains a separate owner decision.

Approvals bind exact action, ticket identities, plan ID, configuration hash, base commit and relevant implementation fingerprints. Launch approval is one-use and includes current worktree evidence. Changed plans invalidate grants. Acceptance fingerprints content after verification, including untracked content and symlink targets, and requires an explicit human actor. Missing/failed checks require a separate manual attestation. Agents have no approval interface.

## Worker confinement

Workers have a deny-by-default Gemini admin tool policy with a small local-read/edit allowlist. No shell, remote tools, MCP or delegated agents are allowed. OS write confinement is mandatory: sandbox-exec on macOS, bubblewrap on Linux. Worktrees isolate branches; they are not themselves a security sandbox. The OS policy keeps common Git metadata read-only to the worker; Python performs Git operations. No unsafe fallback exists when restrictions are unavailable. Central Gemini admin policies that override supplemental restrictions cause launch refusal.

Gemini itself needs network for its model API and reads existing credentials/configuration. The worker process therefore still has model-provider access and read access to the local machine. This is an approved-system trusted-tool boundary, not a claim that arbitrary malicious Gemini binaries or MCP servers can be contained. Do not run it with untrusted CLI executables. Existing skills/agents with arbitrary tool requirements are deliberately disabled.

Jira ticket descriptions, acceptance criteria, comments, source files and README content are untrusted model inputs. They are serialized as data, never interpolated into shell commands. Prompt text states boundaries, but code-level tool restrictions and OS write confinement provide enforcement. Stdio MCP server commands and verification scripts are user-configured trusted programs, never issue-controlled strings. They run with the developer's permissions after explicit connection/launch approval. Review them before configuration.

## Jira and credentials

MCP tool calls use a known semantic name catalog plus explicit read-only/non-destructive annotations and schema checks. Unknown/mutating operations cannot be selected by a model. Deployment still requires least-privilege credentials and a trusted server; protocol annotations are not proof of a server's internal implementation. Incompatible identity/search semantics disable access. Managed Gemini OAuth is never scraped or silently replaced.

REST permits only allowlisted GET endpoints, verifies TLS, rejects redirects, bounds retries/pagination and retrieves credentials from environment variables. Profiles never contain token values. Avoid credential-bearing URLs or environment dumps. HTTP error messages omit response bodies that might contain sensitive data.

## Local data

Configuration, SQLite, logs, review packages, worktree paths and TASK_BOARD.md may contain sensitive ticket data. Profile directories are 0700, files 0600. State is isolated by profile and identity/repository/board/sprint. Git ignores runtime data, tokens, databases, logs and generated dashboards. The generic product source must be reviewed before commit/publication; `.gitignore` is a guard, not a classifier.

Atomic writes prevent partial dashboards/configuration. WAL-backed transactions preserve transitions. Advisory locks prevent duplicate supervision. Guardian processes terminate child groups when the parent disappears. Recovery never assumes a saved PID still belongs to a live worker and never kills an unrelated process from a stale PID.

There is no telemetry service. External traffic is limited to explicitly enabled integrations/model execution, installation and owner-approved publication. Process logs are local and capped at 8 MiB each; there is no automatic historical retention/rotation; operators must monitor disk space and apply their retention policy after stopping processes.
