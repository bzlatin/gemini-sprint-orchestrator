# Worker conversations and VS Code

From **Agents**, enter a launched ticket key and choose **Open session**. The **Sessions** tab shows the recorded branch, worktree, recent messages and recent worker output. Each ticket keeps its own draft when you switch sessions. The full conversation is stored in the profile's private SQLite data; the UI/context window is bounded. Timestamps are UTC.

Write a message and choose **Send follow-up**. The confirmation shows the exact ticket, worktree, message and verification commands. Go back preserves the draft. Confirming runs another coding turn within that ticket's previously approved scope, using the same worktree. It invalidates earlier acceptance and affected downstream review evidence. Checks run again and the implementation returns to Review for fresh human acceptance. Sending a question also authorizes a coding turn; this is not a read-only query mode.

A batch must finish or be paused before a follow-up starts. **Pause worker** preserves its worktree and conversation; remaining workers stay supervised. Wait for any remaining workers or checks to finish. A changed ticket, configuration or prerequisite needs a fresh approved plan. Other users' tickets and company services are not discovered by opening a conversation.

## Open the editor and conversation together

Choose **Open VS Code** in Agents or a loaded session. The application validates the stored worktree, branch and shared Git metadata, then invokes `code --new-window` on a generated private `.code-workspace`. The folder is the worker's existing checkout. Opening is refused for nonempty/symlinked editor or agent execution-configuration directories and root workspace files; inspect these manually first. No branch switch occurs in the main repository and no `.vscode/tasks.json` is written into your code.

The workspace includes a process task named **Gemini session: KEY**, with the worktree as its working directory. This starts Python in isolated mode so worktree files cannot shadow the installed client package. It runs the same conversation client available through `gemini-orchestrator --profile NAME session KEY`. It connects back to the existing dashboard; it does not launch a second independent Gemini process. Message/reply history, confirmations, output and pause are shared between the two views. The editor terminal is fixed to the ticket whose worktree that window opened; use the main dashboard to switch tickets or open another editor window. Refresh reconnects the fixed session without discarding the draft. A confirmation in one view becomes stale if another view changes the task first.

VS Code may ask for Workspace Trust and permission to run automatic tasks. Respect those prompts. If the task does not start, choose **Terminal → Run Task → Gemini session: KEY**. Do not disable trust checks. Install the `code` CLI on PATH; the standard macOS application path is detected as a fallback. On Linux/SSH, use a VS Code or Remote SSH environment whose terminal can reach the same local filesystem and Unix socket as the dashboard. Generic SSH cannot open a separate computer's GUI by itself.

Closing the editor terminal leaves the coordinator and worker running. Closing the main dashboard stops supervised workers and closes the bridge. Reopen the dashboard and load the conversation to reconnect; a workspace's descriptor path remains stable within the profile scope. Keep runtime directories and generated workspaces out of source control.

## Native Gemini continuity

The orchestrator captures a valid `session_id` from successful Gemini JSON output. When a later turn has that ID and the installed CLI advertises resume, it passes `--resume` with the exact UUID. It never guesses `latest` or a session index. A mismatched returned ID fails the turn. If a historical run has no native ID, the UI explicitly says it is continuing from saved task/conversation context.

The conversation client is an orchestrator view, not Gemini's native interactive terminal UI. It sends supervised headless turns, waits for the reply, and retains the existing restricted tool policy, timeout and logging rules. Real Gemini authentication and native history/cache persistence under OS write confinement require validation on the approved target system. Missing capability or failed resume is surfaced rather than silently selecting another conversation.

The compatibility design follows the official [Gemini session reference](https://geminicli.com/docs/cli/session-management/), [headless output reference](https://geminicli.com/docs/cli/headless/), [JSON formatter source](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/output/json-formatter.ts), [VS Code CLI reference](https://code.visualstudio.com/docs/configure/command-line) and [automatic task controls](https://code.visualstudio.com/docs/debugtest/tasks#_control-automatic-task-execution).

For isolated authentication homes, read-only `ask`, storage controls and target-machine checks added in 0.3.0, see [hardening](hardening.md).
