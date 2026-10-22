# Gemini Sprint Orchestrator

A local, human-controlled sprint control center for Gemini CLI. Select assigned Jira tickets, inspect dependency plans, run isolated workers, verify changes and review them before optional remote publication.

**Version 0.4.0 is an MIT-licensed public alpha.** The offline demo is the supported starting point. Connected Gemini/Jira/hosting configurations require validation on the approved target system; this is not an enterprise security certification. See [release readiness](docs/release-readiness.md) and [known limitations](docs/limitations.md).

![Synthetic ticket explorer](docs/demo/01-selection.png)

## Install and try the offline demo

Python 3.11+ and Git are required on macOS or Linux. From this source checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c requirements.lock -e .
gemini-orchestrator demo
```

The demo needs no Gemini, Jira, company credentials or network. It creates synthetic tickets and a sample repository under `~/.local/share/gemini-orchestrator/runtime/demo/`. `GSO_HOME` or the global `--home` option selects another private local root. Demo does not change your active connected profile.

1. Select tickets with Space; choose **Save selection** and confirm.
2. Choose **Propose plan**, inspect dependencies/branches/checks, then **Approve plan**.
3. Separately choose **Approve launch**. Independent mock processes run concurrently in real worktrees.
4. Load a ticket in **Review**. Inspect actual file changes, including new files, verification and diff; accept or request changes. Acceptance creates a local worker-branch commit.
5. Approve another launch for dependent work after prerequisite acceptance. DEMO-6 stays blocked while its unavailable prerequisite is selected for planning.

The maintainer's [recorded walkthrough](docs/demo/README.md) exercised this lifecycle, change requests, continuation and restart recovery through the real Textual controls. To reproduce it in a new disposable directory:

```sh
python -m pip install -c requirements-dev.lock -e '.[dev]'
python scripts/demo_walkthrough.py --output /tmp/my-synthetic-sprint-demo
```

## Worktree editor and shared conversations

After launching a worker, open **Agents**, enter its ticket key, and choose **Open session** or **Open VS Code**. The **Sessions** tab also loads workers directly.

- **Open VS Code** opens a separate window rooted in that ticket's recorded worktree and branch. It leaves the main checkout on its existing branch. A private generated workspace includes a **Gemini session: KEY** terminal task that connects to the same running coordinator.
- Allow that workspace's automatic task when VS Code asks, or choose **Terminal → Run Task → Gemini session: KEY**. Workspace Trust and automatic-task preferences remain under your control. Install the `code` CLI on PATH if needed; the usual macOS application location is also detected. With Remote SSH, run the orchestrator in the same remote environment as the VS Code terminal.
- In either view, inspect recent messages and output, type a follow-up, and confirm its exact ticket, worktree and verification commands. A follow-up starts another supervised coding turn, invalidates previous acceptance, reruns verification, and returns to human review.
- Follow-ups run when the current batch is idle. Pause workers or let the batch finish first. This is conversation at turn boundaries; it does not inject keystrokes into an active headless CLI process.
- When the installed CLI supports resume and returns a native session UUID, the next turn passes that exact UUID to Gemini. Older runs without an ID use clearly labelled saved conversation/task context. Resume failures remain visible and require attention.
- Keep the main dashboard running. Closing the editor conversation view leaves its worker running; quitting the main dashboard stops its supervised workers. Session messages and replies persist in the profile's private runtime data.

You can open the same conversation in another terminal:

```sh
gemini-orchestrator --profile demo session DEMO-1
```

See the [shared-session walkthrough](docs/demo/sessions/README.md) and [session guide](docs/sessions.md) for evidence and limits.

## Isolated checks and read-only questions

Version 0.3.0 sandboxes verification, removes inherited secrets, isolates provider homes, protects editor/agent execution configuration, and binds editor credentials to one ticket. Linux needs working bubblewrap even for the demo. Use `ask KEY QUESTION` for an approved read-only model turn and `storage` to inspect disk use or explicitly prune historical logs. Existing profiles need fresh plan approval because the isolation settings change their configuration fingerprint. See the [hardening guide](docs/hardening.md) for authentication changes, limits and the target-machine validation harness.

## Connected setup

On an approved work system:

```sh
gemini-orchestrator setup
gemini-orchestrator doctor
gemini-orchestrator jira test --enable-connected
gemini-orchestrator start --enable-connected
```

Version 0.4.0 adds Jira Server/Data Center compatibility through supported MCP tools, including legacy account names, missing annotation hints, alternate tool names and list search responses. Direct REST remains Jira Cloud only. See [integration compatibility](docs/integrations.md).

Setup can save local settings without connecting to Jira. It detects Gemini capabilities, confirms the authenticated account identifier, distinguishes team/project/board, discovers active sprints when supported, validates Git without changing the checkout, configures limits/checks, and keeps remote writes off. Do not transfer enterprise runtime data to this generic source repository or a personal machine.

## CLI workflow

Global options precede the subcommand: `gemini-orchestrator --profile personal status`.

```sh
gemini-orchestrator profile list
gemini-orchestrator profile create
gemini-orchestrator profile switch personal
gemini-orchestrator profile edit
gemini-orchestrator tickets --refresh --enable-connected
gemini-orchestrator select APP-1 APP-2
gemini-orchestrator plan --approve
gemini-orchestrator run --enable-connected
gemini-orchestrator review APP-1
gemini-orchestrator review APP-1 --accept
gemini-orchestrator status
gemini-orchestrator board
```

For a demo CLI workflow, use `demo --headless`, then `--profile demo` on each command; omit `--enable-connected`. Every consequential action still asks for explicit confirmation. Use `retry KEY` after feedback/interruption to propose continuation in the same worktree. Remote pushes and draft requests are disabled by default and each have a separate exact-commit/destination approval.

## Development and validation

```sh
python -m pip install -c requirements-dev.lock -e '.[dev]'
python -m pytest
ruff check .
ruff format --check .
mypy src/gemini_orchestrator
python -m build --no-isolation
gemini-orchestrator test
```

Runtime and development constraints record tested versions. [Security policy](SECURITY.md), [dependency inventory](docs/security/runtime-sbom.json), [validation evidence](docs/validation.md), and [changelog](CHANGELOG.md) describe the release scope. The project adds no telemetry, Jira mutations, automatic merges/deployments, or automatic worktree deletion. Gemini and configured external tools retain their own approved settings and policies.

See [architecture](docs/architecture.md), [user guide](docs/guide.md), [integration compatibility](docs/integrations.md), and [enterprise deployment](docs/enterprise.md).
