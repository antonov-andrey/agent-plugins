---
name: git-commit
description: Use when asked to commit or push; inventory worktrees, split logical commits, and publish submodules first.
---

# Git Commit

## Goal
Commit and push the full current repository change set by default with correct commit boundaries and correct submodule ordering.

## When To Use
- The user explicitly asks to:
  - `commit`
  - `push`
- The user asks to publish the current repository state to git remotes.

## Canonical Sources
- `AGENTS.md`

## Inputs
- The full current repository worktree, including root repository changes and submodule changes.
- Any user-provided exclusions.
- Any user-provided commit message constraints or branch constraints.

## Outputs
- One or more logical commits that cover the required repository change set.
- Pushed commits by default unless the user explicitly requests commit-only behavior or explicitly forbids `push`.
- A final statement that identifies what was committed and pushed.

## Hard Rules
- Inventory the entire current repository worktree before choosing commit boundaries.
- Split the repository worktree into logical commits.
- When the in-scope worktree includes governance-only instruction edits and product behavior edits, commit them in separate logical commits.
- Commit the full repository change set by default.
- The only default exception is an explicit user exclusion of some paths.
- Push the committed repository change set by default.
- The only default exception to `push` is an explicit user request for commit-only behavior or an explicit user prohibition on `push`.
- Do not stop for per-change confirmation only because some changes predate the current task.
- If dirty submodules are part of the required change set, commit submodule changes before creating the superproject commit that records their gitlinks.
- When submodules and the superproject are pushed, push submodule commits before pushing the superproject commit that points at them.
- Do not leave the superproject commit pointing at uncommitted or unpublished submodule state.

## Workflow
1. Inventory root-repository changes, submodule status, untracked files, and branch state.
2. Determine whether the user requested exclusions; if not, treat the full visible change set as in scope.
3. Partition the in-scope worktree into logical commits.
   - If governance-only instruction edits and product behavior edits are both in scope, place them in separate logical commits.
4. For each dirty in-scope submodule:
   - inspect its worktree;
   - create the required logical commit set inside the submodule;
   - push those commits first unless the user explicitly requested commit-only behavior or explicitly forbade `push`.
5. Refresh superproject status so recorded submodule gitlinks point at the committed submodule revisions.
6. Create the required logical commit set in the superproject.
7. Push the superproject commits after every referenced submodule commit is already reachable on its remote, unless the user explicitly requested commit-only behavior or explicitly forbade `push`.
8. Report commit ids, push targets, and any explicit exclusions.

## Verification
- The final superproject worktree reflects the intended committed state.
- Every committed submodule gitlink in the superproject resolves to the intended committed submodule revision.
- When governance-only instruction edits and product behavior edits are both in scope, they are committed in separate logical commits.
- Unless the user explicitly requested commit-only behavior or explicitly forbade `push`, referenced submodule commits are pushed before the superproject commit that points at them.

## Anti-Patterns
- Committing only the current task files while ignoring unrelated in-scope dirty paths without an explicit user exclusion.
- Creating the superproject commit before the referenced submodule commits exist.
- Pushing the superproject before the referenced submodule commits are pushed.
- Stopping for confirmation solely because some dirty changes predate the current request.
- Mixing governance-only instruction edits and product behavior edits inside one logical commit when both are in scope.
