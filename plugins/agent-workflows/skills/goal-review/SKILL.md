---
name: goal-review
description: Run an explicitly requested non-interactive Codex review of uncommitted changes or a branch diff.
---

# Goal Review

## Boundary

Use only non-interactive `codex review`; manual code audits, anti-pattern audits, instruction audits, and interactive `/review` flows are separate workflows. Inspect `codex review --help` instead of inventing uncertain flags.

## Target Selection

1. If the user explicitly asks to review uncommitted changes, select uncommitted review.
2. If the user explicitly asks to review relative to another branch, select branch-relative review with that branch.
3. If there are no visible uncommitted changes and the current branch is not `main`, select branch-relative review with base branch `main`.
4. If there are visible uncommitted changes and the current branch is `main`, select uncommitted review.
5. In all other states, ask the user whether to review uncommitted changes or review relative to a named branch.

## Review Loop

1. Inventory branch and dirty state with `git status --short --branch --untracked-files=all`.
2. Resolve the review target through `Target Selection`.
3. If branch-relative review was selected, verify the base branch exists with `git rev-parse --verify <base_branch>`.
4. Before branch-relative review, use `agent-workflows:git-commit` for any visible uncommitted changes, rerun the inventory command, and do not run the review until the worktree is clean.
5. Run `codex review --uncommitted` or `codex review --base <base_branch>` for the selected target. Pass user focus notes as the prompt or through stdin.
6. Treat any finding or comment as a failed review even when the command exits successfully.
7. Fix every finding, run the required verification for changed files, and use `git-commit` again when branch-relative fixes make the worktree dirty.
8. Rerun the same selected review until one fresh run after the last fix reports no findings.

## Handoff

Report the selected mode, target, every review command, fixes made from feedback, and the clean final result. If `codex review` itself fails, report its command and failure output.
