# Validation evidence

Pre-publication local validation on macOS with Python 3.13 used synthetic data only. No Jira/company service was contacted. Remote publication was deferred until explicit owner approval.

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

The owner subsequently approved private publication. Both the GitHub integration and authenticated GitHub CLI identified account `bzlatin`. Repository creation returned [bzlatin/gemini-sprint-orchestrator](https://github.com/bzlatin/gemini-sprint-orchestrator), and the GitHub API confirmed `isPrivate: true` before source upload. Software license selection remains undecided.
