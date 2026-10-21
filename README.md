# Gemini Sprint Orchestrator

A local, human-controlled sprint control center for Gemini CLI. Select assigned Jira tickets, inspect dependency plans, run isolated workers, verify changes and review them before any optional remote publication.

**Status:** local implementation with an offline demo and mocked integration coverage. Real Gemini/Jira/hosting compatibility must be validated on the approved target system. Source is published in the owner-approved [private GitHub repository](https://github.com/bzlatin/gemini-sprint-orchestrator). No software license has been selected.

## Install

Python 3.11+ and Git are required on macOS or Linux.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
gemini-orchestrator demo
```

The demo needs no Gemini, Jira, company credentials or network. It creates synthetic tickets and a sample repository under `~/.local/share/gemini-orchestrator/runtime/demo/`. `GSO_HOME` or `--home` selects another private local root. Demo does not change your active connected profile.

## Demo workflow

1. Select tickets with Space; choose **Save selection** and confirm.
2. Choose **Propose plan**, inspect dependencies/branches/checks, then **Approve plan**.
3. Choose **Approve launch** and confirm. Independent mock processes run concurrently in real worktrees.
4. Inspect review evidence in **Review**, then accept or request changes. Acceptance creates a local commit on the worker branch.
5. Approve another launch to continue dependencies after prerequisite review. DEMO-6 remains blocked by an unavailable external prerequisite.

## Connected setup

```sh
gemini-orchestrator setup
gemini-orchestrator doctor
# Only on an approved work system, after reviewing configuration:
gemini-orchestrator jira test --enable-connected
gemini-orchestrator start --enable-connected
```

Setup can save local settings without connecting to Jira. It detects Gemini capabilities, confirms the authenticated immutable identity, distinguishes team/project/board, discovers active sprints when supported, validates Git read-only, configures worker limits and verification, and keeps remote writes off.

## CLI workflow

Global options precede the subcommand: `gemini-orchestrator --profile personal status`.

```sh
gemini-orchestrator profile list
gemini-orchestrator profile create
gemini-orchestrator profile switch personal
gemini-orchestrator profile edit
gemini-orchestrator tickets --refresh --enable-connected
gemini-orchestrator select DEMO-1 DEMO-2
gemini-orchestrator plan --approve
gemini-orchestrator run                         # demo profile
# connected run also requires --enable-connected
gemini-orchestrator review DEMO-1
gemini-orchestrator review DEMO-1 --accept
gemini-orchestrator status
gemini-orchestrator board
gemini-orchestrator approvals
gemini-orchestrator test
```

For a demo CLI workflow, create it with `demo --headless`, then use `--profile demo` on each command. Every consequential action still asks for explicit human confirmation. Use `retry KEY` after feedback or interruption to propose continuation in the same worktree.

## Development

```sh
python -m pytest
ruff check .
ruff format --check .
mypy src/gemini_orchestrator
python -m build --no-isolation
```

See [architecture](docs/architecture.md), [user guide](docs/guide.md), [security](docs/security.md), [integration compatibility](docs/integrations.md), [enterprise deployment](docs/enterprise.md), and [limitations](docs/limitations.md). No Jira mutation, automatic merge, deployment, telemetry or automatic worktree deletion is implemented.

Native terminal captures from synthetic data: [wide dashboard](docs/dashboard-120.svg), [compact dashboard](docs/dashboard-80.svg), [execution plan](docs/planner-120.svg). Reproduce them with `python scripts/capture_dashboard.py`.
