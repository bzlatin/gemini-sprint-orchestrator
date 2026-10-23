# Gemini Sprint Orchestrator

Pick your assigned Jira tickets, see which are ready and which wait on others, and let Gemini CLI work on each one in its own Git worktree — **planning first, coding only after you approve the plan**.

```
tickets ──► work ──► Gemini plans (read-only) ──► you approve / comment / reject ──► Gemini implements ──► you review & accept
```

**Version 0.5.0 is an MIT-licensed public alpha.** The offline demo is the supported starting point. Connected Gemini/Jira configurations need validation on your approved work system; this is not an enterprise security certification. See [release readiness](docs/release-readiness.md) and [known limitations](docs/limitations.md).

![Synthetic ticket explorer](docs/demo/01-selection.png)

## Try it in one minute (offline demo)

Python 3.11+ and Git on macOS or Linux (Linux also needs `bubblewrap`):

```sh
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -c requirements.lock -e .
gemini-orchestrator demo
```

The demo uses synthetic tickets and a sample repository; no Gemini, Jira, credentials or network.

1. **Tickets** — Space selects tickets. The *Readiness* column shows `Ready` or `Waiting on KEY`. Optionally type branch names (`KEY=name`) in the branch field.
2. **Start selected** — one confirmation shows every branch name, dependency and check. Gemini then drafts a read-only implementation plan for each ready ticket.
3. **Plans** — load a plan and read it. **Approve plan** lets Gemini implement it; **Send comment** asks Gemini to revise it; **Reject plan** stops the ticket and keeps its worktree.
4. **Review** — inspect the real diff and verification, then accept (a local commit on the ticket branch) or request changes.
5. Tickets waiting on a prerequisite become `Ready` once you accept it; start them the same way.

## The same flow from the CLI

```sh
gemini-orchestrator tickets                         # readiness of your sprint tickets
gemini-orchestrator work APP-1 APP-2 --branch APP-1=feature/login
gemini-orchestrator plan APP-1                      # read Gemini's plan
gemini-orchestrator plan APP-1 --comment "Reuse the existing session store"
gemini-orchestrator plan APP-1 --approve            # Gemini implements the approved plan
gemini-orchestrator review APP-1 --accept
gemini-orchestrator status                          # every ticket's next step
```

Use `--profile demo` with the demo. Connected profiles add `--enable-connected` to commands that contact Jira or Gemini. `gemini-orchestrator --help` lists the core workflow first; everything else is under *Advanced*.

## Branch names

Branches come from a template you set during `setup` (default `{type}/{key}-{slug}`, e.g. `feature/APP-12-add-login` or `fix/APP-7-crash-on-empty-input`). Fields: `{key}`, `{slug}` (summary), `{type}` (`fix` for bugs, otherwise `feature`), `{id}` (short schedule id). You see and approve every name before work starts, and can override any of them. Protected and existing branches are refused.

## Safety model

- **You approve every consequential step:** starting work, each implementation plan, accepting code, and any optional push or draft PR. Approvals are bound to exact fingerprints; if the plan, ticket, worktree or settings change, approval is required again.
- **Planning cannot edit code.** The planning turn runs with read-only tools and an OS sandbox that denies writes to the worktree; a changed worktree fails planning.
- **Workers are sandboxed** (`sandbox-exec` on macOS, `bubblewrap` on Linux): only local read/edit tools, no shell, MCP, web or delegation; scrubbed environment; isolated provider home; one worktree per ticket.
- **Jira is read-only.** Only exact known read tools are allowed; ticket content is passed to Gemini as untrusted data.
- **Nothing leaves your machine without a separate approval.** No telemetry, Jira writes, automatic merges, pushes, deployments or worktree deletion.

Details: [security](docs/security.md), [hardening](docs/hardening.md), [security policy](SECURITY.md).

## Connected setup

On an approved work system:

```sh
gemini-orchestrator setup          # profile, Jira MCP server, repo, branch naming, checks
gemini-orchestrator doctor
gemini-orchestrator jira test --enable-connected
gemini-orchestrator start --enable-connected
```

Jira Cloud, Server and Data Center work through supported MCP tools; direct REST is Jira Cloud only. See [integration compatibility](docs/integrations.md) and [enterprise deployment](docs/enterprise.md). Gemini runs with an isolated home; authenticate it once per ticket home as described in [hardening](docs/hardening.md).

## More

- [User guide](docs/guide.md) · [sessions and VS Code](docs/sessions.md) · [architecture](docs/architecture.md)
- [Recorded demo walkthrough](docs/demo/README.md) · [changelog](CHANGELOG.md)

## Development

```sh
python -m pip install -c requirements-dev.lock -e '.[dev]'
python -m pytest
ruff check . && ruff format --check .
mypy src/gemini_orchestrator
python scripts/demo_walkthrough.py --output /tmp/new-walkthrough-dir
```
