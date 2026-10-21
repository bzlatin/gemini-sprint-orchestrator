# Validation evidence

Local validation on macOS with Python 3.13, using synthetic data only. No Jira/company service was contacted and no remote branch/repository was created or pushed.

| Check | Actual result |
|---|---|
| Complete Pytest suite | 77 passed |
| Dashboard suite after final priority-sort regression check | 8 passed |
| Ruff lint | Passed |
| Ruff formatting | Passed |
| Mypy | Passed, 19 source files |
| Wheel and source distribution build | Passed |
| Installed-wheel import and CLI offline validation | Passed |
| CLI `gemini-orchestrator test` | Passed |
| Native Textual captures | Generated at 120×40 and 80×24 |
| Independent dashboard fix review | Five material fixes scored resolved; ship at fix-list scope |

The complete suite includes profile isolation/onboarding, exact identity checks, mocked REST pagination and errors, known-read MCP capability checks, a real local stdio MCP server, deterministic dependency/cycle/conflict scheduling, exact-action approvals and invalidation, real worktree isolation, concurrent mock subprocesses, timeouts/cancellation/pause, actual coordinator crash cleanup, persistent recovery, verification, feedback continuation, review-fingerprint invalidation, Markdown reporting, Textual approval interactions and mocked GitHub/GitLab commands.

The real connected worker code path was exercised with a synthetic Gemini executable under the OS sandbox. A separate actual sandbox test confirmed a permitted worktree write and a denied outside write. These tests require running outside an already restrictive nested process sandbox; the ordinary nested test run may skip them with an explicit reason. No test used a real model account or enterprise credentials.

Textual SVG captures and source/geometry review support terminal layout and interaction claims. Raster visual fidelity and actual SSH/tmux sessions were not independently inspected. The reviewer scored only its five reported fixes; this is not a whole-product security certification.

Live enterprise Gemini authentication/cache compatibility, Jira MCP schemas/OAuth behavior, Jira Server/Data Center, hosting CLI behavior, Linux execution and the remote GitHub Actions matrix remain target-system validation work. See [enterprise deployment](enterprise.md), [integration compatibility](integrations.md) and [limitations](limitations.md).

Publishing and software license selection remain explicit owner approval steps. The authenticated GitHub integration reported account `bzlatin`; no repository URL is asserted before publication.
