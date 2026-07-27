---
name: goal-review
description: Run an explicitly requested non-interactive Codex review of uncommitted changes or a branch diff.
---

# Goal Review

## Goal
Run non-interactive `codex review` against the correct git target, fix every review finding, and repeat until a fresh review returns no findings.

## When To Use
- Use this skill when the user explicitly requests `goal-review`.
- Use this skill when the user asks to run a Codex CLI review and the target may be uncommitted changes or another branch.
- Do not use this skill for manual code-audit reports, anti-pattern audits, instruction audits, or interactive `/review` flows.

## Canonical Sources
- `AGENTS.md`
- `agent-workflows:git-commit`
- `codex review --help`

## Inputs
- Optional explicit instruction to review uncommitted changes.
- Optional explicit base branch for branch-relative review.
- Optional user focus notes for the review prompt.

## Outputs
- One clean non-interactive `codex review` result with no findings.
- A final response that states the selected review mode, the reviewed target, every review command that ran, fixes made from review feedback, and the clean final review result.

## Hard Rules
- Inspect current repository state directly before selecting the review target.
- Use `git status --short --branch --untracked-files=all` to identify the current branch and visible dirty state.
- Use only non-interactive `codex review`; do not use interactive `/review`.
- For uncommitted review, run `codex review --uncommitted`.
- For branch-relative review, run `codex review --base <base_branch>`.
- If the user provides custom review focus notes, pass them as the `codex review` prompt or through stdin with `codex review ... -`.
- If branch-relative review is selected and visible uncommitted changes exist, first use `git-commit` to commit and push the visible change set, then refresh repository state and run `codex review --base <base_branch>`.
- Do not run branch-relative review from a dirty worktree.
- Do not silently choose a review target in an ambiguous state.
- Treat any `codex review` output with findings or comments as a failed review, even when the command exits successfully.
- After any failed review, fix the reported issues, rerun required verification for changed files, and then rerun `codex review` against the same selected target.
- Continue the review/fix/review loop until one fresh `codex review` returns no findings.
- Do not hand off after only applying review feedback; the final review in the same loop MUST be clean.

## Target Selection
1. If the user explicitly asks to review uncommitted changes, select uncommitted review.
2. If the user explicitly asks to review relative to another branch, select branch-relative review with that branch.
3. If there are no visible uncommitted changes and the current branch is not `main`, select branch-relative review with base branch `main`.
4. If there are visible uncommitted changes and the current branch is `main`, select uncommitted review.
5. In all other states, ask the user whether to review uncommitted changes or review relative to a named branch.

## Workflow
1. Inventory branch and dirty state with `git status --short --branch --untracked-files=all`.
2. Resolve the review target through `Target Selection`.
3. If branch-relative review was selected, verify the base branch exists with `git rev-parse --verify <base_branch>`.
4. If branch-relative review was selected and the worktree is dirty, run `git-commit`, then rerun the inventory command.
5. Run the selected non-interactive review command.
6. If the review reports findings, fix them.
7. Run required verification for any files changed while fixing review feedback.
8. If branch-relative review is selected and fixes created visible uncommitted changes, run `git-commit`, then rerun the inventory command.
9. Rerun the same selected non-interactive review command.
10. Repeat steps 6-9 until a fresh review reports no findings.
11. Report the selected mode, reviewed target, every review command, fixes made from review feedback, and the clean final review result.

## Verification
- The selected command MUST match the resolved review target.
- Branch-relative review MUST start only from a clean worktree.
- If `git-commit` was required, the final reviewed branch state MUST include the committed visible change set.
- Completion requires one fresh `codex review` run after the last fix, and that run MUST report no findings.
- If `codex review` fails, report the command and failure output instead of replacing it with a generic review failure.

## Anti-Patterns
- Running `codex review --base <branch>` while uncommitted changes are present.
- Reviewing `main` with no explicit target and no uncommitted changes.
- Treating an uncommitted review result as a branch-relative review result.
- Stopping after applying review feedback without running a clean follow-up review.
- Reporting review completion while the latest `codex review` still contains findings.
- Running tests, formatters, or audits as a substitute for `codex review`.
- Inventing Codex CLI flags instead of checking `codex review --help` when command syntax is uncertain.
