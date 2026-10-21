# Contributing

Use synthetic fixtures and temporary repositories. Never commit Jira data, company paths, runtime state, credentials, logs or generated task boards. Do not connect enterprise services during personal development.

Install `.[dev]`, run pytest, Ruff and Mypy, and build the wheel. Add behavior tests for security boundaries and lifecycle changes. Keep integrations read-only unless an existing exact-action human approval explicitly authorizes a remote operation. Do not add automatic merges, deployments or Jira mutations.

This project uses the MIT license. Keep contributions generic and include regression tests for behavior or security changes.
