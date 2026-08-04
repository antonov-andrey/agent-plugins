---
name: task-review
description: Perform one independent fresh-thread semantic review issue after its implementation blockers close under an exact process-lifetime host-local issue guard held from before dispatch through nested cleanup and final provider readback; report zero findings or create exact remediation blockers, and never hide Product fixes in the review task.
---

# Review Linear Task

Review one `task:review` / `evidence` issue in a fresh thread. Read `../../references/manual-workflow.md`, the entire issue graph slice, exact published source revision, current repository/PR/merged state and external evidence.

Begin every preview and handoff with the attempt boundary: acquire the exact issue process-lifetime host-local attempt guard before dispatch or any mutation, hold the same guard through nested attempt cleanup and final provider read-back so no second local attempt overlaps it, and release it only by process exit after that boundary.

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the explicit user workspace. Start `../../lib/task_workspace/tool/attempt.py hold --issue-identifier <exact-identifier>` as the exact issue process-lifetime host-local guard, require its initial `status=held` JSON, and keep that same process alive from before dispatch or mutation through nested attempt cleanup and final Linear provider read-back. A nonzero exit means another local attempt owns the issue; do not continue. Process exit after final read-back releases the kernel lock.
2. Save a complete fresh Linear snapshot and run `../../lib/linear_boundary/tool/task.py dispatch`. Require exit `0`, Project `In Progress`, issue `Todo`, `In Progress` or `Rework`, exact `task:review` role and `evidence` delivery. Validate every requested status mutation with the same tool's `transition` operation and reread Linear.
3. Use read-only candidate commits, PR refs, merged main commits and environment identities. Do not create a fake review branch.
4. Derive review coverage afresh from every applicable source/stable owner and current external state. Do not scope review from the implementation plan, prior findings, changed-file list or passing tests.
5. Do not modify Product code. For every finding against unfinished work, return the owning implementation issue to `Rework` when appropriate. For a finding against merged/`Done` work, create or link a bounded `task:implementation` remediation issue with its own branch, make it a blocker of this review, preserve downstream blockers and return review to `Todo`.
6. After remediation closes, start a new fresh-thread full review from scratch. A partial recheck is not acceptance.
7. Reconcile all exact `attempt`-lifetime resources through `task-cleanup`, reusing this attempt's live guard, then on one fresh zero-finding pass build the exact evidence fingerprint and structured attempt comment with `../../lib/verification/tool/evidence.py`, publish both with concise scope, candidate identities and evidence, move the issue to `Human Review` and stop. A remediation finding also cleans its attempt resources and publishes an attempt comment with an empty candidate fingerprint. The human may approve the unchanged non-code fingerprint directly to `Done` or request `Rework`. Delete transient evidence inputs.
