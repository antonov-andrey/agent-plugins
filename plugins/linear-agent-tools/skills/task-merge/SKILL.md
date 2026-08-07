---
name: task-merge
description: Merge exactly one independently reviewed Linear code task in Merging, enforcing current PR heads, declared bases, required checks, branch protection, ordered recovery and final links without hidden fix-forward.
---

# Merge Linear Task

Run only in a fresh thread for one code implementation already moved by an independent zero-finding Codex review from `Review` to `Merging`. Read `../../references/manual-workflow.md`, the exact provider-read review handoff, PR links, repository bases and ordered merge plan. No per-PR human approval is required by this workflow; repository branch protection remains mandatory.

Every preview or handoff MUST state that one canonical-root issue guard is acquired before dispatch/mutation, held continuously across ordered recovery, nested cleanup and final provider readback, and released only by process exit afterward.

1. Set canonical `LINEAR_AGENT_WORKSPACE_ROOT`, start `../../lib/task_workspace/tool/attempt.py hold`, require `status=held`, and keep that process alive through the boundary.
2. Fully reread Linear and run dispatch. Require active Project, exact issue `Merging`, implementation/code role, exact assignment and no blockers.
3. Parse the latest byte-for-byte provider-read `review-passed` handoff and take its complete PR URL/head map as direct review evidence. For every PR run `scripts/pull_request.py inspect --base-branch <base> --head-branch <head> --reviewed-head-commit <commit>`. Separately prove current exact head, base/head, integration link, non-draft state, provider mergeability, required checks and branch protection. An already merged exact reviewed head is accepted only for crash recovery.
4. If source, generated output, base update or PR head changed after review, publish and byte-for-byte reread a `rework-required` semantic handoff with direct stale-state evidence, then move `Merging -> Rework`. Do not fix or rebase in merge.
5. Merge in declared repository order with `scripts/pull_request.py merge ... --reviewed-head-commit <commit> --merge-method <method>`. Retry only transient provider operations that do not change reviewed state. Never hide or roll back a partial cross-repository merge; publish completed merges and exact remaining recovery order.
6. Use native background-terminal waiting for provider operations. Do not create model polling, a Project supervisor, command timeouts, arbitrary thresholds or an alternate Codex home.
7. After all merges are directly read back, reconcile attempt resources under the live guard and publish/reread one `merged` semantic handoff with reviewed PR heads, final commits, evidence URLs and only directly exposed exact Codex usage counters. Validate `Merging -> Done`, mutate/reread Linear, then run issue-owned cleanup under the same guard. Do not complete downstream graph review or acceptance automatically.

No verification receipt, candidate fingerprint or generic invalidation gate is created. Delete transient inputs only after final provider readback; process exit releases the guard.
