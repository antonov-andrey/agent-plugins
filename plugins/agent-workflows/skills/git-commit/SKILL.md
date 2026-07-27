---
name: git-commit
description: Use when asked to commit or push; inventory worktrees, split logical commits, and publish submodules first.
---

# Git Commit

## Commit Contract

- Inventory the entire current repository worktree before choosing commit boundaries.
- Treat the full visible change set as in scope unless the user explicitly excludes paths.
- Split the in-scope worktree into logical commits, separating governance-only instruction changes from Product behavior changes.
- Push committed changes by default unless the user explicitly requests commit-only behavior or forbids `push`.
- Do not stop for per-change confirmation only because some changes predate the current task.
- Commit each in-scope dirty submodule before creating a superproject commit that records its gitlink. When push is enabled, publish each submodule commit before the referencing superproject commit.
- Never leave a superproject commit pointing at uncommitted or unpublished submodule state.

## Workflow

1. Inventory root-repository changes, submodule status, untracked files, and branch state.
2. Apply explicit exclusions and partition the remaining change set into logical commits.
3. For each in-scope dirty submodule, inspect it, create its logical commits, and push them unless push is disabled.
4. Refresh superproject status so every gitlink points at the intended committed submodule revision.
5. Create the logical superproject commits.
6. Push the superproject only after every referenced submodule commit is reachable on its remote, unless push is disabled.

## Handoff

Confirm the final committed state and each recorded submodule revision. Report commit ids, push targets, explicit exclusions, and every remaining dirty path.
