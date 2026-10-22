# 0.3.0 hardening walkthrough evidence

Both reports were produced using the installed 0.3.0 wheel in a separate virtual environment. The shared-session walkthrough exercised actual dashboard/editor-terminal controls, reused the same branch/worktree and reran verification under the new sandbox. VS Code's executable was stubbed; the GUI was not launched.

The target harness used `--synthetic`: it tested initial launch, follow-up, verification and read-only question flow in a new disposable repository. Its report explicitly does not claim a real native-provider resume or GUI validation. No company repository, Jira, hosting operation or real provider account was accessed.

- [Shared-session report](shared-session-report.json)
- [Synthetic target-harness report](target-report.json)
- [Run the approved real-provider/editor exercise](../../hardening.md#approved-target-machine-exercise)
