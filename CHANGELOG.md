# Changelog

## 0.1.1 — public alpha

- Added the owner-selected MIT license and tested dependency constraints.
- Serialized shared Git metadata mutations across processes and profiles.
- Covered prompt delivery, output draining and cancellation with process deadlines;
  bounded worker logs to 8 MiB and cleaned up on input/callback errors.
- Preserved prior worker/verification logs across feedback and reruns.
- Refreshed live output and elapsed time without waiting for a lifecycle change.
- Included new, untracked implementations in the review diff and file list.
- Required explicit outside-terminal review for binary or truncated changes.
- Pinned remote approvals to the source commit, destination repository and exact
  draft request title/body; displayed those details before confirmation.
- Added a reproducible Textual demo walkthrough covering approvals, dependency
  blocking, feedback, continuation and restart persistence.

## 0.1.0 — private evaluation

Initial local-first package, onboarding, profiles, Jira adapters, deterministic
planning, human approvals, worktrees, supervised workers, verification and review.
