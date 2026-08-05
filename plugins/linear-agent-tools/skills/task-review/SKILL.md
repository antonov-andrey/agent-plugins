---
name: task-review
description: Perform one independent fresh-thread semantic review issue after its implementation blockers close under an exact process-lifetime host-local issue guard held from before dispatch through nested cleanup and final provider readback; report zero findings or hand merged findings to the active-Project delta workflow as exact remediation blockers, and never hide Product fixes in the review task.
---

# Review Linear Task

Review one `task:review` / `evidence` issue in a fresh thread. Read `../../references/manual-workflow.md`, the entire issue graph slice, exact published source revision, current repository/PR/merged state and external evidence.

Every preview or handoff response MUST state that one exact issue process-lifetime host-local attempt guard is acquired before dispatch or status mutation, held continuously through nested attempt cleanup and the final Linear provider readback so no second local attempt can overlap, and released only by process exit after that boundary.

Each preview and handoff MUST state that complete coverage is derived afresh from all current applicable sources and stable owners. It MUST state that prior reports, implementation artifacts, changed-file lists, and passing tests do not define review scope.

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the explicit user workspace. Start `../../lib/task_workspace/tool/attempt.py hold --issue-identifier <exact-identifier>` as the exact issue process-lifetime host-local guard, require its initial `status=held` JSON, and keep that same process alive from before dispatch or mutation through nested attempt cleanup and final Linear provider read-back. A nonzero exit means another local attempt owns the issue; do not continue. Process exit after final read-back releases the kernel lock.
2. Save a complete fresh Linear snapshot and run `../../lib/linear_boundary/tool/task.py dispatch`. Require exit `0`, Project `In Progress`, issue `Todo`, `In Progress` or `Rework`, exact `task:review` role and `evidence` delivery. Validate every requested status mutation with the same tool's `transition` operation and reread Linear.
3. Use read-only candidate commits, PR refs, merged main commits and environment identities. Do not create a fake review branch.
4. Derive review coverage afresh from every applicable source/stable owner and current external state. Do not scope review from the implementation plan, prior findings, changed-file list or passing tests.
5. Do not modify Product code. For every finding against unfinished work, return the owning implementation issue to `Rework` when appropriate. For a finding against merged/`Done` work, use `linear-agent-tools:task-graph-create` to preview and reconcile an approved active-Project delta: add a bounded `task:implementation` remediation issue with its own branch, make it a blocker of this review, preserve downstream blockers, and return only this review to blocked `Todo` before the new remediation issue receives activation metadata or its final `Todo` transition.
6. After remediation closes, start a new fresh-thread full review from scratch. A partial recheck is not acceptance.
7. Reconcile all exact `attempt`-lifetime resources through `task-cleanup`, reusing this attempt's live guard. A fresh zero-finding pass is incomplete until it builds and publishes both the exact evidence fingerprint and one structured attempt comment with `../../lib/verification/tool/evidence.py`; only then move the issue to `Human Review` and stop. A remediation finding also cleans its attempt resources and publishes an attempt comment with an empty candidate fingerprint. The human may approve the unchanged non-code fingerprint directly to `Done` or request `Rework`. Delete transient evidence inputs.
