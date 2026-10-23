# Isolated execution and target validation (0.3.0)

Workers, read-only model questions, advisory analysis and verification now use filesystem isolation. A missing sandbox is an error, including for verification; there is no permissive fallback. Linux requires bubblewrap with working user namespaces. The CI jobs install it and fail if required boundary tests are skipped.

## Files, environment and configuration

Verification can persist host writes only in its assigned worktree and dedicated scratch home. Linux also supplies an ephemeral private `/tmp`, which is not the host temporary filesystem. It has no network access; on macOS Mach service lookup and other-process inspection are also denied. Workers retain network access for the approved model provider, with a local read/edit tool allowlist. Questions have both a read-only tool policy and OS write denial for the worktree. Advisory analysis denies every model tool and mounts the source read-only.

The host home, controller databases, bridge credentials, other worktrees, SSH agents and arbitrary shell environment variables are excluded. Standard OS tooling, the application's Python environment and resolved Node installation are readable; macOS linked libraries outside those roots are granted as individual files. These tool installations must be trusted and separate from runtime data. Explicit additional toolchain paths can be configured through `isolation.tool_read_paths`; exposing controller/worktree roots is refused. Generic `python`/`python3` checks use the application's Python interpreter; use an explicit executable path for another installed version.

Gemini receives a dedicated per-ticket home. Only `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` and `GOOGLE_GENAI_USE_VERTEXAI` are forwarded from the model environment. Verification receives none of these. Proxy variables, shell startup flags, PYTHONPATH, NODE_OPTIONS and unrelated credential variables are not inherited. Existing personal Gemini settings/credentials are no longer automatically reused. An approved API key or authentication in the isolated worker home is required:

```sh
gemini-orchestrator --profile NAME worker-home KEY
```

This prints the private home path without reading or copying credentials. If your organization uses OAuth, authenticate the approved CLI with that HOME on the work machine, from a safe directory; do not copy normal settings, extensions or credentials from a personal machine. Read-only questions use a separate `ask` home and need their own approved authentication or API key. The provider credential required by the model process is available to that trusted process; this is not a defense against a malicious Gemini binary or an approved toolchain containing secrets.

Coordinator-owned system settings disable hooks, skills and local dotenv loading. Existing central organization settings or overriding central policies cause launch refusal, requiring administrator validation rather than silently replacing organization controls. Generated execution-config directories (`.vscode`, `.gemini`, `.agents`, `.claude`) are write-protected during worker/check execution. On Linux, empty directory mount points are created if absent; empty directories do not change Git content. Existing project agent configuration is hidden from workers. The VS Code action refuses nonempty or symlinked execution-config directories and root `.code-workspace` files. Review these manually before opening the generated editor workspace; it never silently trusts or deletes them.

Editor bridge credentials are bound to one ticket and rotate when the dashboard restarts. They cannot be reused for another ticket. The general terminal entry point retains same-account authority. These controls isolate worker processes; they do not authenticate a human against malicious software already running as the same unsandboxed OS user.

## Read-only questions

Close the dashboard, then run:

```sh
gemini-orchestrator --profile demo ask DEMO-1 'Explain this implementation'
# On the approved model system:
gemini-orchestrator --profile NAME ask KEY 'Explain this implementation' --enable-connected
```

The CLI previews the question and exact worktree and asks for approval. It starts a fresh read-only model turn, with no native coding-session resume. It records the reply in shared history and preserves implementation acceptance when the worktree is unchanged. Questions need an idle, previously approved ticket scope. A coding follow-up still requires fresh coding approval and verification. Question history invalidates older conversation previews.

## Storage and retention

Defaults are 512 MiB and 50,000 entries per worktree/scratch directory, with a 2 GiB controller-runtime preflight limit. These are configurable `isolation` settings and therefore affect plan approval fingerprints. The supervisor checks disk growth approximately every half second and again at process completion. These are soft watchdog limits, not filesystem quotas; a fast writer can overshoot before termination. Each process log also retains its existing 8 MiB cap.

```sh
gemini-orchestrator --profile NAME storage --days 30
# Preview, then explicitly confirm deletion:
gemini-orchestrator --profile NAME storage --days 30 --prune-logs
```

Close the dashboard first. Only old, regular, single-link, non-current process logs are candidates. The exact candidate metadata is checked again after confirmation. Databases, messages, current worker/check logs and all worktrees are preserved. No automatic deletion is enabled. Move/archive worktrees and runtime data through your organization's reviewed retention process if these limits are reached.

## Approved target-machine exercise

Gemini and the VS Code GUI are not installed on the development machine. The new harness makes the outstanding real-provider exercise reproducible while keeping company repositories, Jira and hosting out of it:

```sh
python scripts/validate_target.py --profile NAME --enable-connected --output /tmp/gso-target-check --editor
```

Choose a new output directory outside your checkout. The script copies only the configured Gemini/toolchain settings into a synthetic profile, creates its own repository, prompts separately for the schedule, the read-only planning turn, approval of Gemini's implementation plan (the coding turn), the exact-session follow-up and the read-only question, and verifies checks and native UUID continuity. It fails if live output lacks a native session ID. Real mode consumes approved model usage. API authentication must be valid for the isolated homes. `--synthetic` exercises the harness without any provider access and reports that distinction.

With `--editor`, use Agents → DEMO-1 → Open VS Code. Verify the branch/worktree, task permission, shared history, closing just the editor, dashboard restart/reconnection and Remote SSH if applicable. Record the installed CLI/editor versions and results locally. The harness deliberately reports GUI validation as manual. Run `jira test --enable-connected` and hosting compatibility checks separately only after organization approval.

The Gemini settings controls follow the official [settings](https://geminicli.com/docs/reference/configuration/) and [settings merge implementation](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/settings.ts). Capability detection and target validation remain necessary for the installed version. Weekly GitHub Actions dependency/secret audits and Dependabot update proposals provide ongoing checks; updates are reviewed and tested rather than automatically merged.
