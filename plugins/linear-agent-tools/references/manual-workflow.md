# Manual Linear Development Workflow

## Contract Owners

- Root `DESIGN.md`, section `Goal Brainstorm И Linear Task Workflow`, owns stable Project identity, task identity, synchronization, lifecycle, Review, handoff, validation and concurrency policy.
- `plugins/linear-agent-tools/lib/task_graph/issue-contract.md` owns the visible issue-card schema and conditional sections.
- Each `linear-agent-tools` skill owns its operation-specific preconditions, evidence and terminal boundary.
- This manual owns only the shared operational sequence. It does not restate stable policy.

## Setup And Graph Entry

1. Run `linear-agent-tools:workflow-configure` once for the exact workspace, team and GitHub repositories.
2. On each supported merge host, run the `task-merge` transport provision operation once with the operating-system user's standard `HOME` and no `CODEX_HOME`.
3. Use the currently installed `linear-agent-tools:task-graph-create` skill for graph publication. The `AND-47` cutover replaces that skill and this step atomically with `task-graph-sync`; do not invoke the replacement name before that cutover lands.
4. Review the complete provider preview before its mutation boundary.
5. Open one fresh Codex thread for one ready issue and invoke the skill that matches its role and current status.

## Guarded Attempt Sequence

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the canonical multi-repository checkout container. Keep the standard `HOME` and leave `CODEX_HOME` unset.
2. Acquire the exact issue process-lifetime attempt guard before dispatch or provider/Git mutation. Hold it through nested attempt cleanup and final Linear readback. Release it only by process exit.
3. Fully read the authenticated destination, issue, relations, comments, source, repository state and delivery-applicable external state.
4. Save one transient complete snapshot and run the owning dispatch or transition validator before each status mutation.
5. Reconcile the exact attempt-lifetime resources before `Todo` or `Rework` enters `In Progress`. Nested cleanup reuses the live attempt guard.
6. Mutate one provider boundary, fully read its semantic result, then continue with only the next permitted phase.
7. Run targeted checks after each coherent owner slice. Run the required complete deterministic checks on the frozen result.
8. Perform the required fresh semantic owner reread after the last fix.
9. Reconcile attempt-lifetime resources before every result handoff or status transition.
10. Render the one minimal human-first handoff through the shared evidence owner, publish it once and require byte-identical provider readback.
11. Validate the result transition, mutate Linear, fully reread the issue and preserve or retire the workspace as the role skill requires.
12. Exit only after final provider readback so the process-lifetime guard releases at the correct boundary.

## Code Delivery Sequence

1. Prepare or adopt only the deterministic issue worktrees and branches declared by the card's code delivery section.
2. On `Rework`, preserve the existing worktree, branch and current open pull request. Do not reset them.
3. Use `agent-workflows:git-commit` for logical commits and push each green commit immediately.
4. Create or adopt the exact pull request through the task implementation owner and reread the official Linear integration attachment.
5. Wait for configured checks through the harness-native background terminal and native resume path.
6. Stop implementation in `Review` after the handoff and readback. A separate fresh `task-review` attempt owns the decision.

## Cleanup Sequence

1. Read each optional typed resource declaration from the issue-card schema.
2. Resolve its provider cleanup-handler key under root `DESIGN.md`.
3. Invoke the owning `task-cleanup` operation for the current boundary and follow that skill's terminal sequence.
