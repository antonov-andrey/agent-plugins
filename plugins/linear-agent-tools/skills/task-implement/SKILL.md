---
name: task-implement
description: "Implement one dispatchable Linear task: reconstruct fresh context, adopt or create exact issue worktrees, change only its scope, verify, commit and push logical slices, open linked PRs, wait for required CI, and stop at Human Review."
---

# Implement Linear Task

Execute only one `task:implementation` issue. Every `Todo` or `Rework` attempt starts in a fresh Codex thread; previous chat history is never execution state.

Read `../../references/manual-workflow.md` and the complete issue, relations, comments, exact published source revision and relevant sections, repository instructions, Git/PR state and current verification evidence.

## Start

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the explicit user workspace. Start `../../lib/task_workspace/tool/attempt.py hold --issue-identifier <exact-identifier>` as a long-lived process, require its initial `status=held` JSON, and keep it alive through the complete attempt, including attempt-resource cleanup and final Linear read-back. A nonzero exit means another local attempt owns the issue; do not continue. Stop the guard only after the attempt boundary is complete; process death releases the kernel lock.
2. Reread the exact authenticated Linear destination and issue. Save the complete transient task snapshot and run `../../lib/linear_boundary/tool/task.py dispatch`; require exit `0`, role `task:implementation` and delivery `code` or `evidence`.
3. For code delivery, build a transient repository request from issue origins/bases and recorded workspace baselines, then run `scripts/workspace.py prepare`. For first dispatch, build the exact `workspace-baseline` input from the returned branch/repository commits plus the source fingerprint and render it through `../../lib/verification/tool/evidence.py workspace-baseline`. Fully paginate issue comments: create the provider-marked baseline only when absent, accept exactly one byte-identical comment, and stop on duplicate or conflicting baseline comments. Reread it before Product mutation. A crash before the comment is recovered from private Git-admin state; on later attempts require that exact comment to match the adopted private state.
4. On `Rework`, adopt the same branches, worktrees and PRs. Never reset user work or silently change baseline. A base update/rebase is a candidate mutation requiring a new approval cycle.
5. Validate the exact `Todo`/`Rework -> In Progress` proof with `../../lib/linear_boundary/tool/task.py transition`, then mutate and reread Linear. Evidence-only probes create no fake branch or PR.
6. Before recovering a prior attempt, invoke `task-cleanup` for every declared `attempt`-lifetime resource using its exact recorded identity; absence is success. That nested cleanup reuses this attempt's live guard rather than acquiring a second lock.

## Delivery

- Apply every applicable project standard and implement only the bounded issue outcome.
- Complete one coherent owner slice before verification and publication. Use `agent-workflows:git-commit` for logical commits and push each commit immediately; do not emit symptom-by-symptom intermediate commits that are not independently green.
- Run targeted checks after each completed owner slice, the applicable full suite on the frozen candidate, live acceptance only for that exact deployed candidate, and a fresh semantic audit after the last fix.
- Build receipts from the exact source fingerprint, argv, working directory, exact verification repository URL, applicable repository commits, that repository's recursive submodule commits, dependency locks and environment/release identity. A source-independent evidence probe may have an empty verification repository and repository set, but never an absent source fingerprint. Use `scripts/receipt.py`; reuse only exact passed inputs.
- For code delivery, push the deterministic `linear/<lowercase-issue-identifier>` branch and run `scripts/pull_request.py` to create or accept PRs whose title and branch contain the exact Linear identifier. Fully reread the Linear issue and require the official integration-created GitHub PR attachment or diff for that exact PR URL. A title, branch or manually added URL is not link evidence. Wait for applicable required CI without frequent polling.

## Handoff

Before publishing the attempt result, invoke `task-cleanup` for all current `attempt`-lifetime resources. Build the exact code or evidence candidate fingerprint with `../../lib/verification/tool/evidence.py candidate`. Build the concise structured attempt comment with its `attempt` operation: attempt ID, role, delivery kind, UTC start/end, outcome, delivery-applicable commit set, receipt hit/miss counts, external wait duration, token usage only when directly exposed, candidate fingerprint, PR/CI links and bounded evidence. Publish that exact comment to Linear. Exclude prompts, secrets and raw logs; delete transient input files.

Validate the exact `In Progress -> Human Review` proof with the shared transition tool. Move only when result, verification, commit/push/PR, required CI and evidence are complete, then reread Linear. Stop the agent and preserve the workspace. Any later candidate mutation requires `Rework`, a fresh thread and a new fingerprint.
