---
name: task-accept
description: Verify the complete current multi-task outcome in a fresh thread after all blockers close, create remediation blockers for any finding, and stop at Human Review only after a new whole-scope zero-finding pass.
---

# Accept Linear Outcome

Accept one `task:acceptance` / `evidence` issue. Read `../../references/manual-workflow.md`, the complete active Project graph, exact published source revision, every resulting stable owner, merged commits, current external state and verification evidence.

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the explicit user workspace. Start `../../lib/task_workspace/tool/attempt.py hold --issue-identifier <exact-identifier>` as a long-lived process, require its initial `status=held` JSON, and keep it alive through attempt cleanup and final Linear read-back. A nonzero exit means another local attempt owns the issue; do not continue. Stop it only after the attempt boundary is complete.
2. Save a complete fresh Linear snapshot and run `../../lib/linear_boundary/tool/task.py dispatch`. Require exit `0`, Project `In Progress`, issue `Todo`, `In Progress` or `Rework`, exact `task:acceptance` role and `evidence` delivery. Validate every requested status mutation with the same tool's `transition` operation and reread Linear.
3. Reconstruct the required outcome from source and current owners. Audit the entire current scope from scratch; do not reduce it to completed checklists, changed files, prior reports or cached partial checks.
4. Run all required final automated, integration, live and semantic acceptance on exact current candidate/environment identities. Reuse only exact verification receipts; never cache away the final whole-outcome pass.
5. Do not fix Product code inside acceptance. A finding creates or links a bounded remediation implementation issue, becomes this issue's blocker, preserves final-cleanup dependency, and returns acceptance to `Todo`.
6. After every fix, start another fresh-thread full acceptance pass. Continue until one new pass after the last change finds nothing incomplete, contradictory, stale or unverified.
7. Reconcile all exact `attempt`-lifetime resources through `task-cleanup`, reusing this attempt's live guard, then build the exact evidence fingerprint and structured attempt comment with `../../lib/verification/tool/evidence.py`, publish concise acceptance scope and exact commit/environment identities, move to `Human Review` and stop. A finding also cleans its attempt resources and publishes an attempt comment with an empty candidate fingerprint. Human approval of the unchanged non-code fingerprint moves it to `Done`; any mutation or rejection moves it to `Rework`. Delete transient evidence inputs.
8. When the issue explicitly owns local-workflow baseline acceptance, derive queue, startup, execution, review and merge durations from exact Linear/GitHub history and render the complete baseline through the same tool's `baseline` operation. Do not estimate missing phases or parse private chat logs.

Every preview or handoff response MUST state that one exact issue process-lifetime host-local attempt guard is acquired before dispatch or status mutation, held continuously through nested attempt cleanup and the final Linear provider readback, and released only by process exit after that boundary.
