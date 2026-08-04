---
name: task-merge
description: Merge exactly one Human Review-approved Linear code candidate in Merging, enforcing PR head, bases, required checks, branch protection, ordered cross-repository recovery, final links, and no hidden fix-forward.
---

# Merge Linear Task

Run only in a fresh thread for one code-delivery issue already moved by a human from `Human Review` to `Merging`. Read `../../references/manual-workflow.md`, exact approval fingerprint, PR links, repository bases and ordered merge plan.

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the explicit user workspace. Start `../../lib/task_workspace/tool/attempt.py hold --issue-identifier <exact-identifier>` as a long-lived process, require its initial `status=held` JSON, and keep it alive through attempt cleanup and final Linear read-back. A nonzero exit means another local attempt owns the issue; do not continue. Stop it only after the attempt boundary is complete.
2. Save a complete fresh Linear snapshot and run `../../lib/linear_boundary/tool/task.py dispatch`. Require exit `0`, Project `In Progress`, exact issue status `Merging`, role `task:implementation`, code delivery, exact assignee and no blockers.
3. For every approved PR, run `scripts/pull_request.py inspect --base-branch <exact-base> --head-branch <exact-head> --approved-head-commit <exact-head-commit>`. The command requires the exact approved head, intended base/head, integration-compatible Linear identifier, non-draft state, branch-protection mergeability and all required checks passing; an already merged exact candidate is accepted only as crash recovery. Independently reread the Linear issue and require its official integration-created attachment or diff for the exact PR URL. Require the repository-approved `merge`, `squash` or `rebase` method from the issue contract before mutation.
4. If source, generated artifacts, base update or head changed after approval, do not fix it here. Move the issue to `Rework`, record the stale fingerprint and require a fresh implementation/verification/Human Review cycle.
5. Merge in the issue's explicit repository order using `scripts/pull_request.py merge --base-branch <exact-base> --head-branch <exact-head> --approved-head-commit <exact-head-commit> --merge-method <approved-method>`. Retry only transient provider operations that do not change the candidate.
6. After each merge, reread the PR, final commit and Linear/GitHub link. Never hide or automatically roll back a partial cross-repository merge; record exact completed merges and create bounded recovery evidence for the remaining order.
7. After the complete set is proven merged, reconcile all exact `attempt`-lifetime resources, reusing this attempt's live guard, render and publish one structured attempt comment with `../../lib/verification/tool/evidence.py attempt`, preserving the human-approved candidate fingerprint and exact final commits. Validate `Merging -> Done` with the shared transition tool, mutate and reread Linear, then invoke `task-cleanup` for issue-owned local state under the same guard. Do not complete downstream review or acceptance automatically; delete transient evidence inputs.
