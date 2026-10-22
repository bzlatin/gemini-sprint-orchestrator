# Validation evidence

Release checks used synthetic data only. No company service or real model account was contacted. The owner selected MIT, approved the initial private source upload and subsequently authorized public visibility after CI passed. On 2026-10-21 the GitHub API confirmed `private: false` and `visibility: public` for the [repository](https://github.com/bzlatin/gemini-sprint-orchestrator).

## 0.3.0 — execution isolation

The release adds restricted filesystem reads/writes and clean environments for workers/checks, network-free verification, protected execution configuration, scoped editor credentials, approved read-only questions, soft disk limits and manual log retention. The runtime dependency set is unchanged.

| Check | Actual result |
|---|---|
| Local regression suite | 133 passed in 119.09 seconds; final host-process/supervision/model checks: 29 passed; synchronized dashboard lifecycle test passed |
| macOS process boundaries | Real synthetic processes denied controller/sibling-worktree/symlink reads, localhost networking in checks, question-mode source writes and host-process environment inspection |
| Configuration and credentials | Editor execution configuration refused; worker configuration writes denied; wrong-ticket and stale-generation credentials rejected |
| Question continuity | Synthetic connected CLI used a separate read-only tool policy, did not resume coding, and preserved implementation evidence |
| Disk controls | Oversized process stopped; symlink/current logs excluded from retention candidates |
| Static checks | Ruff and Mypy passed for 24 source files |
| Package | 0.3.0 wheel/sdist built; installed wheel passed pip check, CLI self-test, full shared-session UI walkthrough and target harness in synthetic mode |
| GitHub Actions / macOS and Ubuntu, Python 3.11 and 3.13 | 134 passed on each platform/version; zero skips; static checks, required boundary guard, CLI self-test and build passed |
| GitHub Actions / security | Runtime dependency audit and offline secret scan passed |

The complete [0.3.0 validation run](https://github.com/bzlatin/gemini-sprint-orchestrator/actions/runs/39779803913) passed all five jobs for commit `39b870ba649a3ccb2ed670c78aa18e07d4a9d7d9`. All four JUnit artifacts recorded 134 tests, zero failures/errors and zero skips. Subsequent evidence updates change documentation only.

The [recorded reports](demo/hardening/README.md) contain synthetic data only. The new [target validation harness](hardening.md#approved-target-machine-exercise) is available for approved real-provider/editor tests. Gemini and VS Code are absent here; their real authentication, GUI and Remote SSH behavior remain unvalidated. The Linux CI configuration now installs bubblewrap and rejects skipped boundary tests instead of counting them as coverage. Watchdog disk limits can overshoot between polls and are not kernel quotas. Same-account unsandboxed clients remain trusted.

## 0.2.0 — shared worker conversations

The extension adds exact-worktree editor opening and the same supervised conversation in the dashboard/editor terminal. The checks below record the release candidate and its bounded synthetic evidence.

| Check | Actual result |
|---|---|
| Local complete suite | 116 passed in 114.07 seconds; macOS / Python 3.13 |
| Static checks | Ruff lint/format and Mypy passed; 22 source files |
| Synthetic shared-session walkthrough | Passed through real dashboard and editor-terminal controls at 120×40 and 80×24 |
| Persistence | Six conversation records survived restart; original worktree/branch preserved; checks reran |
| Native continuity | Real sandboxed synthetic CLI asserted exact UUID passed to --resume |
| Editor launch | Workspace/task/branch/cwd verified with stubbed code executable; actual terminal client exercised |
| Editor isolation | Python -I prevented a worker-created package from shadowing the installed client |
| Human control | Stale/replayed previews rejected; declined draft preserved; new turn invalidated acceptance/downstream review |
| Failure/pause | Startup errors visible in chat; pausing verification or one batch member preserves work and supervision |
| Interface review | Both listed material fixes scored resolved; ship at that two-fix-list scope |
| Package and installed-wheel workflow | Wheel/sdist 0.2.0 built; installed site-packages copy passed pip check, CLI self-test and the complete shared-session walkthrough |
| GitHub Actions / macOS Python 3.11 and 3.13 | 116 passed on each; static checks, CLI self-test and build passed |
| GitHub Actions / Ubuntu Python 3.11 and 3.13 | 113 passed, 3 platform skips on each; static checks, CLI self-test and build passed |
| GitHub Actions / security | Dependency audit and offline secret scan passed |

The complete [0.2.0 CI run](https://github.com/bzlatin/gemini-sprint-orchestrator/actions/runs/39707440075) passed all five jobs for commit `784bfd16dffe03772cbaa8526c4b649c863f370f`. Its test artifacts confirm the counts above. The three Linux skips require a supported OS sandbox; the macOS jobs ran those tests successfully.

[Walkthrough evidence](demo/sessions/README.md) distinguishes the real Textual applications from the stubbed VS Code GUI launch. VS Code is not installed on this development machine. The Gemini executable in the connected test is synthetic: no real provider account, credentials or company service were used. Actual VS Code/Remote SSH and native provider auth/history behavior remain target-system checks. Runtime dependencies are unchanged from the previously audited pinned set; the inventory root version is updated to 0.2.0.

## 0.1.1 — public alpha baseline

| Check | Actual result |
|---|---|
| Complete Pytest suite, macOS / Python 3.13 | 98 passed in 61.37 seconds |
| Live dashboard output without lifecycle transition | Passed with a real supervised synthetic process |
| Ruff lint and formatting | Passed, 46 Python files |
| Mypy | Passed, 19 source files |
| Wheel and source distribution | Built version 0.1.1 |
| Fresh virtual environment / installed wheel | Installed with runtime constraints; pip check, CLI self-test, headless demo and full Textual walkthrough passed |
| Full Textual walkthrough | Passed: selection, separate plan/launch gates, concurrent worktrees, blocked dependencies, verification, acceptance, feedback, continuation and saved-profile restart |
| Native captures | Five current 120×40 SVG/PNG captures inspected; existing 80×24 capture and compact interaction tests retained |
| Runtime dependency audit | pip-audit found no known vulnerabilities in all 40 pinned runtime dependencies |
| Existing Git history | Three revisions scanned offline with detect-secrets; zero candidates |
| Candidate tracked source | Zero candidates across all tracked release files |
| GitHub Actions / Ubuntu Python 3.11 | 96 passed, 2 platform skips; lint, types, CLI self-test and build passed |
| GitHub Actions / Ubuntu Python 3.13 | 96 passed, 2 platform skips; lint, types, CLI self-test and build passed |
| GitHub Actions / security | Dependency audit and offline secret scan passed |
| GitHub Actions / macOS Python 3.11 | 98 passed; lint, types, CLI self-test and build passed |
| GitHub Actions / macOS Python 3.13 | 98 passed; lint, types, CLI self-test and build passed |

The complete [GitHub Actions run](https://github.com/bzlatin/gemini-sprint-orchestrator/actions/runs/39665272473) passed all five jobs for implementation commit `a66fbc0f3b46e884539364a9fd85ff5716b5867f`. Subsequent evidence and publication-status updates change documentation only.

The suite covers profile isolation/onboarding, exact identity checks, mocked REST pagination/errors, known-read MCP capability checks, a real local stdio MCP server, deterministic scheduling/cycles/conflicts, approval invalidation, real isolated worktrees, multiprocess Git mutation, concurrent subprocesses, prompt/output deadlines, bounded logs, cancellation/crash cleanup, persistent recovery, verification snapshots, feedback continuation, new/binary-file review, review fingerprints, Markdown reporting, Textual interactions and mocked hosting operations. Six concurrent processes exercise shared Git metadata locking.

The connected worker code path is tested using a synthetic Gemini executable under the OS sandbox. A separate macOS test checks permitted worktree writes and denied outside writes. Tests requiring OS process access run outside the nested tool sandbox; unavailable facilities produce explicit skips. The Linux runner lacks a supported sandbox executable, so its two OS-boundary/connected-worker tests were skipped; mocked Linux policy checks passed. Real Gemini authentication and company credentials are not part of this evidence.

The [scripted walkthrough report](demo/report.json) records eleven granted approvals and final accepted states for DEMO-1/2/3. Native Textual SVG exports were rendered locally without external font requests and the PNGs visually inspected. The interface finish review recorded ship with no remaining material fixes. Live SSH/tmux, screen-reader behavior and real enterprise integrations remain target-system validation work.

The [CycloneDX file](security/runtime-sbom.json) is a dependency inventory generated from the runtime constraints; it does not claim a complete dependency-edge graph. Version pins are constraints rather than artifact hashes. Vulnerability and secret scans provide bounded evidence, not a security certification.

Before work use, validate the actual Gemini version/auth/cache behavior, compatible Jira MCP schemas/OAuth, hosting CLIs and the worker sandbox on the approved work system. See [enterprise deployment](enterprise.md), [integration compatibility](integrations.md) and [limitations](limitations.md).
