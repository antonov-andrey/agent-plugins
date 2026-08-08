# Manual Linear Development Workflow

## Contract Owners

- Root `DESIGN.md`, section `Goal Brainstorm И Linear Task Workflow`, owns stable Project identity, task identity, synchronization, lifecycle, Review, handoff, validation and concurrency policy.
- `plugins/linear-agent-tools/lib/task_graph/issue-contract.md` owns the visible issue-card schema and conditional sections.
- Each `linear-agent-tools` skill owns its operation-specific preconditions, evidence and terminal boundary.
- This manual owns only the shared operational sequence. It does not restate stable policy.

## Setup And Graph Entry

1. Run `linear-agent-tools:workflow-configure` once for the exact workspace, team and GitHub repositories.
2. On each supported merge host, run the `task-merge` transport provision operation once with the operating-system user's standard `HOME` and no `CODEX_HOME`.
3. Use the currently installed `linear-agent-tools:task-graph-create` skill for graph publication. The `AND-47` cutover replaces that skill and this step atomically with `task-graph-sync`. Follow the cutover sequence in root `DESIGN.md`. Do not invoke the replacement name before installation readback.
4. Review the complete provider preview before its mutation boundary.
5. Open one fresh Codex thread for one ready issue and invoke the skill that matches its role and current status.

## Guarded Attempt Sequence

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the canonical multi-repository checkout container. Keep the standard `HOME` and leave `CODEX_HOME` unset.
2. Acquire the exact issue process-lifetime attempt guard before dispatch or provider/Git mutation. Hold it through nested attempt cleanup and final Linear readback. Release it only by process exit.
3. Fully read the authenticated destination, issue, relations, comments, source, repository state and delivery-applicable external state.
4. Save one transient complete snapshot and run the owning dispatch or transition validator before each status mutation.
5. Run attempt cleanup before `Todo` or `Rework` enters `In Progress`. Standard targets are derived from issue/repository identity; nested cleanup reuses the live attempt guard and creates no receipt or cleanup-progress state.
6. When an owning attempt invokes its exact Project self-migration, pass its live issue guard to synchronization under the caller-guard rule in root `DESIGN.md`. The nested owner does not reacquire or release that guard.
7. Mutate one provider boundary, fully read its semantic result, then continue with only the next permitted phase.
8. Run targeted checks after each coherent owner slice. Run the required complete deterministic checks on the frozen result.
9. Perform the required fresh semantic owner reread after the last fix.
10. Run attempt cleanup before every result handoff or status transition.
11. Render the one minimal human-first handoff through the shared evidence owner, publish it once, fully paginate comments, parse the provider marker and semantically require only the fields consumed by the next transition.
12. Validate the result transition, mutate Linear, fully reread the issue and preserve or retire the workspace as the role skill requires.
13. Exit only after final provider readback so the process-lifetime guard releases at the correct boundary.

## Code Delivery Sequence

1. Prepare or adopt only the deterministic issue worktrees and branches declared by the card's code delivery section.
2. On `Rework`, preserve the existing worktree, branch and current open pull request. Do not reset them.
3. Use `agent-workflows:git-commit` for logical commits and push each green commit immediately.
4. Create or adopt the exact pull request through the task implementation owner and reread the official Linear integration attachment.
5. Wait for configured checks through the harness-native background terminal and native resume path.
6. Stop implementation in `Review` after the handoff and readback. A separate fresh `task-review` attempt owns the decision.
7. If the candidate changes its own Review/lifecycle provider, use a fresh generic `gpt-5.6-sol` max thread with one complete prompt and branch-local contracts/diff; neither the implementation thread nor its installed plugin cache reviews the candidate.

## Codex Child Boundary

1. A task thread performs its owned scope directly; do not create a nested agent unless the task names a genuinely independent parallel owner.
2. When the harness launches a Codex child, use the operating-system user's standard `HOME`, remove `CODEX_HOME`, pass the complete prompt and close stdin in that same launch operation, and never forward Linear credentials.
3. Use harness-native background execution and native wait/resume. Do not add model polling, a supervisor, timeouts, arbitrary thresholds, an alternate home or copied authentication.
4. When a merged issue changes the installed lifecycle provider, its retained reviewed task worktree runs branch-local `task-merge/scripts/provider_install.py` after terminal merge readback. That fixed boundary discovers the configured local marketplace, fast-forwards its exact clean base worktree from the reviewed old base to the exact merged commit, performs only an incomplete normal standard-home installation phase, and returns the complete fresh-discovery prompt and expected result. From the canonical non-Git `LINEAR_AGENT_WORKSPACE_ROOT`, the outer harness launches generic max `codex exec --skip-git-repo-check` directly with that prompt and closed stdin, waits natively, and requires exact installed source/manifest and skill discovery before `Done`; no launcher wrapper, alternate home, cache authority or install state file is created.

## Cleanup Sequence

1. Derive standard worktree, branch, private baseline and exact PR targets from the canonical issue and participating repository identities.
2. Accept a non-standard resource only when current Project, issue and repository state declares its natural identity and the installed closed provider registry consumes its typed handler and lifetime. Repository-owned boundaries declare the handler key in their bootstrap manifest; Project-owned resources may be declared by their current owner issue. The registry currently owns only the development-infrastructure acceptance-base branch and workflow-infrastructure development environment handlers. Otherwise leave the resource untouched and stop. Never execute shell text, arbitrary argv or a fingerprint from issue prose.
3. Invoke the owning `task-cleanup` operation for the current boundary and follow its live-state terminal sequence.
4. Every cleanup/recovery Git mutation uses the provider's closed minimal standard-user environment with global/system and invocation Git config ignored, hooks disabled, exact transport credentials retained, and explicit destination/lease validation before remote deletion or canonical-base fast-forward.
