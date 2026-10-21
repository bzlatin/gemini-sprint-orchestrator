# Moving to an approved enterprise environment

1. Obtain approval to use Gemini CLI, Jira read APIs/MCP and this application on the work system. Validate the code and dependencies under your organization's policy.
2. Transfer only the generic source repository or built wheel. Do not transfer your personal `GSO_HOME`, profiles, databases, logs, dashboards, worktrees, shell history or credential files. Conversely, never transfer enterprise runtime data back to your personal machine or personal GitHub repository.
3. Create a virtual environment with Python 3.11+. Install the reviewed wheel or source plus approved dependencies. Install Git and the organization's Gemini CLI distribution. Authenticate Gemini using the existing approved mechanism. On Linux install bubblewrap; macOS uses sandbox-exec.
4. Run `gemini-orchestrator setup` on that system. Choose the actual local enterprise checkout, external worktree root, exact Jira MCP settings/server and team/project/board/sprint. Keep remote writes off during evaluation.
5. If using a compatible MCP stdio helper or header-based connection, let setup verify the authenticated immutable identity and confirm it. Do not copy token values into profile JSON. For Gemini-managed OAuth/extension-only configuration, use a validated server adapter or approved GET-only REST authentication through environment variables.
6. Run `doctor`, inspect version/capability results and manually validate the worker policy/sandbox against the installed CLI. Run `jira test --enable-connected` and confirm that only your selected sprint's assigned incomplete issues appear. Compare keys and blockers against Jira manually.
7. Configure explicit verification commands for a safe test repository first. Inspect scripts before running them. Launch a small non-sensitive ticket through every human gate. Confirm isolation, timeout/cancel, review diff, actual test logs and fresh approval after edits. Validate denied shell/MCP tools and attempted outside-worktree writes with a controlled test.
8. Confirm target-system handling of Gemini auth/cache writes under confinement. A failure is a compatibility error; do not remove the sandbox or tool policy to make the demonstration pass.
9. Test Git hosting on a dedicated approved test repository. Enable writes only when needed, then separately approve an exact branch push and a draft request. Verify target branch and remote commit. Never automate merge or deployment.
10. Set local retention/backups/access permissions according to company policy. Keep state on approved storage and use tmux for SSH. Record versions and validation evidence before normal use.

## Existing Jira MCP validation checklist

- `gemini --version`, `gemini --help`, `gemini mcp --help`: verify current supported interfaces.
- After approval to contact the configured services, `gemini mcp list`: identify the exact Jira server. Multiple servers require explicit selection.
- Inspect the selected `settings.json` definition locally; never paste credentials into issues or personal chats. Confirm transport and existing secure environment references.
- Run setup and confirm authenticated account ID, project IDs, board ID and sprint ID; team names alone are insufficient.
- `gemini-orchestrator jira test --enable-connected`: verify supported read-only tools/annotations, schema compatibility, exact identity and scoped pagination.
- Check the server exposes no required mutation path and credentials are read-only. Unsupported capability/auth combinations must remain disabled.
- Compare returned issues with Jira UI. Check completed/other-assignee exclusion, blockers, descriptions, custom acceptance field and comments.
- Treat all real connection results as enterprise-only runtime artifacts. Only generic compatibility code and synthetic tests may return to the reusable source.
