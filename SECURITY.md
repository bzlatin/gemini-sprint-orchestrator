# Security policy

Version 0.1.x is an experimental local development tool. The supported entry point
for initial evaluation is the offline synthetic demo. Connected use requires a
trusted Gemini CLI/MCP installation, approved model-provider access and validation
on the target system. Worktree write confinement is not a general hostile-code or
confidentiality sandbox. See [the security model](docs/security.md).

To report a suspected vulnerability, use GitHub's private vulnerability reporting
for this repository when available. Do not put credentials, company ticket data,
worktree contents or exploit details in public issues. If private reporting is not
available, open an issue requesting a private reporting channel without sensitive
details. This project does not offer a guaranteed response time.

Dependencies are recorded in `requirements.lock`; development/CI versions are in
`requirements-dev.lock`. Re-run vulnerability checks when updating either file.
Security scans are evidence at a point in time, not a guarantee that the software
contains no vulnerabilities.
