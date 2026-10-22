# Integration compatibility

All real enterprise integrations require validation on an approved target system. Personal development uses synthetic data and mocked external responses.

## Gemini CLI

The adapter runs `gemini --version` and `gemini --help`, plus `gemini mcp --help`, before using advertised capabilities. MCP inventory uses `gemini mcp list` only when advertised. Coding requires `--prompt`, `--output-format` and `--admin-policy`. Headless output is captured in full; exit codes and process completion come from Python, not model self-reports. No `--yolo` is used.

The worker tool policy denies all tools by default and allows only built-in local read/edit tools. MCP, shell execution, web access and subagent delegation remain denied. Verification runs separately under Python after human launch approval. macOS sandbox-exec or Linux bubblewrap additionally confines filesystem writes to the assigned worktree and its task-specific runtime directory. Shared Git metadata, controller state and other worktrees are unavailable to workers. Central admin policies can override supplemental policies; the adapter refuses such configurations until an equivalent administrator-approved path is implemented/validated. It does not silently weaken restrictions.

Gemini runs with an isolated per-ticket HOME and a clean environment. Use an approved model API key or authenticate inside that dedicated home; the ordinary personal home is not inherited. Auth/cache/resume compatibility must be validated on the approved target system. See [hardening](hardening.md). Custom agent/skill reuse is disabled because arbitrary tools could bypass the restricted inventory. Existing GEMINI.md and README roadmap are included subject to policy. Native continuation uses the exact captured UUID when advertised; read-only questions never resume a coding session.

References checked during implementation: [headless mode](https://geminicli.com/docs/cli/headless/), [configuration](https://geminicli.com/docs/reference/configuration/), [policy engine](https://geminicli.com/docs/reference/policy-engine/), [MCP configuration](https://geminicli.com/docs/tools/mcp-server/). Runtime capability detection remains authoritative.

## Existing Jira MCP connection

The preferred adapter reads the user-selected Gemini `settings.json` MCP server definition in place. Credentials are not copied into profiles. It connects with the MCP SDK over stdio, HTTPS Streamable HTTP or HTTPS SSE, reusing environment references and compatible headers. Stdio helpers are trusted configured executables, not ticket-supplied commands. The application enforces Gemini's explicit include/exclude/server allowlists. Project-level settings must be selected explicitly rather than guessing merge precedence.

There is no supported general-purpose Gemini CLI interface for invoking arbitrary MCP tools directly as structured RPC. The application therefore reuses the underlying connection definition via MCP, rather than asking an unrestricted LLM to fetch Jira data. Extension-only definitions, Gemini-managed OAuth sessions, IAP service-account configurations and unstructured text results are rejected with compatibility guidance. This can require REST fallback; existing OAuth credentials are never scraped from Gemini's secure stores.

Supported operations use an explicit known-name semantic catalog (see `jira.OPERATIONS`). Missing annotation hints are accepted only for those exact known read operations, to support legacy community servers. Explicit `readOnlyHint=false`, `destructiveHint=true` or malformed hint values reject the tool. `includeTools` only narrows access; it cannot approve unknown names or override unsafe hints. An arbitrary tool cannot become allowed by claiming a read-only annotation. JSON Schema validates arguments before each call. No model chooses or invokes Jira tools. Mutation operations do not exist in the adapter.

Supported families include common `jira_get_current_user`, `jira_search` / `jira_search_issues`, `jira_get_issue`, `jira_get_all_projects` / `jira_get_projects`, `jira_get_agile_boards`, `jira_get_sprints_from_board`, plus listed Atlassian equivalents. Tools with different input/result schemas need explicit compatibility adapters and tests. Unsupported project/board/sprint discovery offers manual stable-ID selection; identity and search are mandatory. Account identifiers must match the saved confirmed identity on every discovery. JQL targets that identity and sprint; local filtering checks assignee, sprint, selected projects and completion status.

### Jira Server / Data Center through MCP

Identity and issue assignees use the same first non-empty field: `accountId`, `account_id`, `name`, then `key`. The confirmed identifier is rechecked before every ticket search; changing users requires setup confirmation again. A Server username can change, so it is not described as immutable. Numeric sprint IDs are unquoted literals (`sprint = 123`); names remain escaped quoted values. Search may return an `issues` object or a JSON issue list. List pages request 50 results; a full page requires offset support, repeated pages abort, and servers with other paging conventions need an explicit adapter. This is compatibility with supported schemas, not a claim that every community server works.

Setup lists server names from the selected local file and rejects blank or unknown names. If the file is unavailable, choose another file or enter `later` to save local settings without a verified identity. Cloud ID is normally blank for Server/Data Center; supply one only if the chosen tool requires it. Missing project, board or sprint tools default to manual stable IDs and manual sprint behavior. The wizard does not guess a board or sprint from its display name.

The MCP server remains a trusted integration: names and [protocol annotation hints](https://modelcontextprotocol.io/specification/2025-11-25/schema) do not enforce its internal behavior. Use approved read-only Jira credentials. Tests use synthetic Server-shaped payloads and real local stdio; your deployed server still needs target validation.

Use `jira test --enable-connected` after setup on the target system. It discovers allowed capabilities, checks exact identity, validates sprint scope and retrieves scoped issues using read operations only. Read-only account permissions remain the deployment requirement; annotations alone cannot establish that a malicious server really implements read semantics.

## Jira Cloud REST fallback

Configure HTTPS `base_url`, `token_env` and optional `email_env`. A token plus email uses Basic authentication; token without email uses bearer authentication. Tokens are read from the process environment at runtime and never saved. Only allowlisted GET endpoints are possible, with TLS verification, timeouts, bounded retries, redirect rejection, pagination limits and repeated-token detection. Cloud v3 enhanced search `/rest/api/3/search/jql` uses `nextPageToken`; board/sprint/project discovery uses startAt pagination. Direct Jira Server/Data Center REST endpoints are not implemented; use a compatible MCP connection described above.

ADF descriptions/comments are normalized to text. Optional `acceptance_field` names the instance-specific custom field. Embedded comments may be limited by Jira; separate all-comment pagination is not claimed. Jira team APIs vary, so team is explicitly configured as a label; it is never used as an inferred board identifier.

Reference: [Jira Cloud search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/).

## GitHub / GitLab / local

Local-only is the default. GitHub uses `gh`, GitLab uses `glab`; authentication remains managed by those tools. Read-only request listing is provided by `Hosting.read_requests`. `profile remote-writes --enabled` enables remote features after confirmation; `push KEY` and `draft KEY TARGET` each require separate exact-action approval. A draft checks that origin has the exact accepted commit. No force-push, merge, deployment, Jira update or automatic remote repository creation exists in the application.

Request titles/descriptions link the ticket key with implementation and verification context. GitHub passes a body file; GitLab passes one literal description argument. There is no shell interpolation. Hosting adapters are covered with command mocks, not claims of live enterprise validation.

Remote action previews now show the exact SHA, destination repository and draft title/body. Pushes use the approved SHA and URL rather than mutable HEAD/origin names; draft creation uses an explicit --repo target. CLI options were checked against the official [GitHub](https://cli.github.com/manual/gh_pr_create) and [GitLab](https://docs.gitlab.com/cli/mr/create/) command references. Real enterprise hosting operations still require target validation.
