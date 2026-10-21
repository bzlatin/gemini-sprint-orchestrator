# Validation evidence — 0.1.1

Release checks used synthetic data only. No company service or real model account was contacted. The owner selected MIT and approved source upload to the private [GitHub repository](https://github.com/bzlatin/gemini-sprint-orchestrator). Public visibility remains a separate owner decision.

| Check | Actual result |
|---|---|
| Complete Pytest suite, macOS / Python 3.13 | 98 passed in 61.37 seconds |
| Live dashboard output without lifecycle transition | Passed with a real supervised synthetic process |
| Ruff lint and formatting | Passed, 46 Python files |
| Mypy | Passed, 19 source files |
| Wheel and source distribution | Built version 0.1.1 |
| Fresh virtual environment / installed wheel | Installed with runtime constraints; pip check, CLI self-test and headless demo passed |
| Full Textual walkthrough | Passed: selection, separate plan/launch gates, concurrent worktrees, blocked dependencies, verification, acceptance, feedback, continuation and saved-profile restart |
| Native captures | Five current 120×40 SVG/PNG captures inspected; existing 80×24 capture and compact interaction tests retained |
| Runtime dependency audit | pip-audit found no known vulnerabilities in all 40 pinned runtime dependencies |
| Existing Git history | Three revisions scanned offline with detect-secrets; zero candidates |
| Candidate tracked source | Zero candidates across all tracked release files |
| GitHub Actions | Candidate matrix pending upload and execution |

The suite covers profile isolation/onboarding, exact identity checks, mocked REST pagination/errors, known-read MCP capability checks, a real local stdio MCP server, deterministic scheduling/cycles/conflicts, approval invalidation, real isolated worktrees, multiprocess Git mutation, concurrent subprocesses, prompt/output deadlines, bounded logs, cancellation/crash cleanup, persistent recovery, verification snapshots, feedback continuation, new/binary-file review, review fingerprints, Markdown reporting, Textual interactions and mocked hosting operations. Six concurrent processes exercise shared Git metadata locking.

The connected worker code path is tested using a synthetic Gemini executable under the OS sandbox. A separate macOS test checks permitted worktree writes and denied outside writes. Tests requiring OS process access run outside the nested tool sandbox; unavailable facilities produce explicit skips. Real Gemini authentication and company credentials are not part of this evidence.

The [scripted walkthrough report](demo/report.json) records eleven granted approvals and final accepted states for DEMO-1/2/3. Native Textual SVG exports were rendered locally without external font requests and the PNGs visually inspected. The interface finish review recorded ship with no remaining material fixes. Live SSH/tmux, screen-reader behavior and real enterprise integrations remain target-system validation work.

The [CycloneDX file](security/runtime-sbom.json) is a dependency inventory generated from the runtime constraints; it does not claim a complete dependency-edge graph. Version pins are constraints rather than artifact hashes. Vulnerability and secret scans provide bounded evidence, not a security certification.

Before work use, validate the actual Gemini version/auth/cache behavior, compatible Jira MCP schemas/OAuth, hosting CLIs and the worker sandbox on the approved work system. See [enterprise deployment](enterprise.md), [integration compatibility](integrations.md) and [limitations](limitations.md).
