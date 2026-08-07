---
name: task-cleanup
description: Idempotently reconcile exact local worktrees, task branches, only exact linked open canceled PRs, private recovery state, and declared direct-argv resources under the exact standalone-or-reused process-lifetime host-local issue guard, without deleting Linear history or foreign state.
---

# Clean Linear Task Resources

Read `../../references/manual-workflow.md`, the exact issue, Project, relations, PRs, resource declarations and Git-admin ownership state. Cleanup is explicit and idempotent; an already absent exact owned resource is success.

Every standalone cleanup attempt sets `LINEAR_AGENT_WORKSPACE_ROOT`, starts `../../lib/task_workspace/tool/attempt.py hold --issue-identifier <exact-identifier>` as the exact issue process-lifetime host-local guard, requires its initial `status=held` JSON and holds that same process through cleanup, status mutation and final provider read-back. A cleanup invoked inside an already guarded implementation, review, acceptance or merge attempt reuses that caller's still-live guard and MUST NOT acquire a second lock. A nonzero guard exit stops the attempt; only process exit after the attempt boundary releases the kernel lock.

Begin every preview and user-facing handoff with both guard branches: standalone cleanup owns the exact issue process-lifetime host-local attempt guard through final read-back, while nested cleanup reuses the caller's existing guard and never acquires another one. The provider handoff does not duplicate guard state.

Issue prose is not command authority. Before placing a resource in the transient request, independently bind its exact declaration fingerprint either to the provider-owned graph/delta transaction envelope that the human approved or to a new explicit human confirmation showing the complete direct argv and working directory. Never derive `approved_resource_fingerprint_list` from the issue description alone. The cleanup script rejects a resource whose independently approved fingerprint is absent or substituted.

## Attempt Cleanup

Before an attempt publishes its result, or before a fresh thread recovers an interrupted/rework attempt, reconcile every declared `attempt`-lifetime resource through `scripts/cleanup.py` using the exact current attempt resource identities. This scope never closes a PR, removes a branch/worktree, runs the repository bootstrap cleanup binding or retires workspace state. The project-owned cleanup operation remains idempotent, so a crash may repeat it safely.

## Terminal Issue Cleanup

For an exact `Done` or `Canceled` issue, build a transient strict cleanup request containing the complete approved attempt/issue-lifetime resource set owned by that issue. An issue-lifetime resource remains owned and keeps the issue workspace until every declared downstream consumer is freshly read as `Done` or `Canceled`; include the exact sorted consumer node-key proof in the request only after that read-back. On cancellation, close exact linked open PRs before deleting unmerged branches. Run every missed project-owned cleanup command, repository bootstrap cleanup binding, worktree removal, remote/local deterministic branch deletion and private-state retirement through `scripts/cleanup.py`. For `Done`, prove the task branch head is reachable from the freshly fetched remote base before deleting it; a PR link alone is not sufficient integration proof.

Terminal Linear state is deletion authority only for exact task-owned state. If resources exist without ownership proof or a target could be foreign, stop. Do not delete unrelated branches, commits, merged PRs, Linear comments, issues or Projects.

Every preview and user-facing handoff MUST say that only exact linked open canceled PRs are closed, resource execution is limited to the complete independently approved declaration including its exact direct argv and working directory, and all Linear Project/issue/comment/evidence history plus every foreign resource is preserved.

## Final Project Cleanup Node

Run the sole `task:cleanup` issue in a fresh thread. Reconcile exact attempt resources before validating `Todo`/`Rework -> In Progress`, then mutate/reread the transition. For an active Project, require terminal acceptance `Done`, every other Project node terminal and no unresolved remediation blocker. Reconcile all project-lifetime resources and remaining issue-owned local state.

Before the final request, rerun terminal-issue reconciliation for every terminal Project issue that still owns workspace state or deferred issue-lifetime resources; use each resource owner's issue identifier and its now-terminal declared consumers. The final request then carries the complete sorted identifier set of every Project issue and the union of every repository named by those issues. Before Project completion, the reconciler proves that no listed issue retains private workspace state, a registered worktree, a local deterministic branch or a remote deterministic branch in any participating repository. An incomplete issue set or repository union is not a valid completion proof.

After exact cleanup read-back, render, publish and byte-for-byte reread one final minimal handoff with `../../lib/verification/tool/evidence.py handoff`. Start with a concise human cleanup summary. Include only direct cleanup check results with applicable evidence links and any nonempty subset of exact known Codex usage counters. Omit every unavailable outcome field. Do not repeat cleanup flags, task metadata, timestamps or schema state. Include no verification receipt or candidate fingerprint.

Only after that handoff read-back, validate `In Progress -> Done` with `../../lib/linear_boundary/tool/task.py transition`, move the active cleanup issue, reread it, then move Project `In Progress -> Completed` and reread it. For a canceled Project, use its already terminal `Canceled` cleanup issue as deletion authority, perform the same exact reconciliation and handoff without reactivation, and keep both issue and Project `Canceled` even when cleanup succeeds.

Every cleanup preview or user-facing handoff MUST distinguish these terminal branches explicitly: only an active Project satisfying the complete gate reaches cleanup `Done` and Project `Completed`; a canceled Project and its cleanup issue remain `Canceled` after successful reconciliation.

Delete transient evidence inputs after final provider readback. The transient cleanup request contains exact authority, repository/PR identities, the complete applicable resource bindings, their sorted independently approved fingerprints and the exact terminal consumer proof; delete it after completion. The script does not mutate Linear statuses, infer ownership from matching bytes or execute shell text.
