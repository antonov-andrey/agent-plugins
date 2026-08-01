---
name: goal-brainstorm
description: Clarify implementation ideas or design changes, prepare central project-goals task contracts and isolated task worktrees, revise an inactive candidate, and prepare a persistent goal without starting implementation.
---

# Goal Brainstorm

Turn an implementation idea into approved stable contracts, one tracked `project-goals` task directory, one isolated implementation-worktree set, and one persistent-goal candidate. Do not start implementation, checkpoint, merge, or delete a task during this workflow.

**REQUIRED REFERENCES:** Read both references completely before changing any instruction, design, specification, goal, or task-worktree state:

- `references/specification-contract.md`
- `references/worktree-contract.md`

## Workflow

1. Inspect persistent goal state, the canonical `project-goals` repository, every affected repository, applicable `AGENTS.md`, relevant design/task artifacts, and enough current code and tests to verify facts. If goal state cannot be inspected, stop before changing an existing candidate or creating a replacement for the same objective.
2. Identify the real requirement owners and complete participant set. Before editing, show the user the direct-owner or dedicated-specification choice, common prefix, central task directory, implementation task branch, every planned implementation worktree, and why each owner belongs.
3. Resolve outcome, scope, non-goals, ownership, interfaces, data and state transitions, failure and recovery, compatibility, and verification. Ask only about material decisions that cannot be discovered. Offer alternatives only when a real choice remains and recommend one with its tradeoff.
4. Present the design and verification obligations in reviewable sections. Obtain explicit approval before writing the corresponding contracts.
5. Create or revise `project-goals/<common-prefix>/spec.md` through the serialized direct-main transaction, then prepare every participating implementation repository through `Preparation Lifecycle` in `worktree-contract.md`. Reach `repository_prepared` everywhere before tracked stable-owner authoring.
6. Bind every implementation-repository command to the recorded task roots and update its approved stable owners only there. Apply an approved `project-goals` stable-owner change only through the direct-main transaction. Revalidate isolation before and after each authoring phase, then run `contracts-authored`; do not begin semantic review or seal while state is `repository_prepared`.
7. Apply `Semantic Review` from `specification-contract.md` to the complete changed contract set and resolve every finding. Prepare the approved goal input; `seal` creates the initial `checkpoint.yaml` and publishes both files through the direct-main transaction. Require `Terminal Completion Audit` in the goal and persistent objective.
8. Run complete validation and `seal`. Sealing commits and pushes only the exact final task-directory delta to `project-goals/main`, binds the published commit and fingerprints, and never publishes implementation repositories. Show the published candidate, stable-source changes, and task-root diff. `goal_ready` means internally consistent review candidate, not user approval.
9. Before activation, handle every correction or scope addition as an ordinary revision of the same task. Inspect goal state, run `revise`, preserve task content and worktrees, extend participants through `prepare` when needed, then repeat authoring, `contracts-authored`, semantic review, and `seal`. Never create a prerequisite or replacement goal merely because a candidate was sealed.
10. Ask separately whether to activate the displayed candidate. After explicit confirmation, inspect goal state and rerun sealed validation:
    - If an unfinished goal exists, do not activate another one.
    - If no unfinished goal exists and a goal-creation tool is available, create the persistent goal, record `active`, and require fresh `active` validation.
    - If state cannot be inspected or automatic creation is unavailable, report the exact limitation and provide the semantic `/goal` command below.

Checkpoint publication belongs to `agent-workflows:goal-checkpoint`, merge and primary acceptance to `agent-workflows:goal-merge`, and deletion to `agent-workflows:goal-delete`. Goal completion alone authorizes none of them.

## Mandatory Identity Handoff

Before a proposed-design or sealed-candidate handoff, provide one contiguous block:

```text
Coordination repository: <project-goals-root>
Task directory: <project-goals-root>/<common-prefix>
Specification: <common-prefix>/spec.md
Goal: <common-prefix>/goal.md
Checkpoint: <common-prefix>/checkpoint.yaml
Implementation branch: <common-prefix>
Task roots:
- <repository-one>/.worktree/<common-prefix>
- <repository-two>/.worktree/<common-prefix>
Execution boundary: every implementation-repository command stays in the exact task roots above until an explicitly invoked goal-merge operation.
```

List every participant in that block with exact resolved paths when known. Do not scatter identity facts or refer back to an earlier list.

## Terminal Rules

- Preserve unrelated user work and diagnose deterministic repairs before declaring a blocker.
- Never infer participants from workspace proximity, repository names, or dirty state.
- If this agent leaked a task patch into a participating main worktree, use recorded caller provenance and `recover-main-leak`; matching bytes alone are not provenance.
- Accept overlapping committed main drift only after explicit user confirmation of the exact owner, commit, and complete overlap set.
- Once prepared, all tracked implementation work and verification stay in recorded task roots until `goal-merge`.
- A sealed inactive candidate changes only after `revise`; an active task identity and `spec.md`/`goal.md` are immutable.
- `goal-brainstorm` may commit and push only its exact approved coordination path set directly to `project-goals/main` but never publishes, merges, or deletes implementation source.
- Reread the identity block field by field before every required handoff.
- Never weaken `Terminal Completion Audit` into one checklist, test run, self-review, or separately generated evidence artifact.

For a `/goal` fallback, print this semantic command in the user's language:

```text
/goal When no other goal is unfinished, implement the objective in <project-goals-goal-path>. Treat that file, its sibling spec.md, and their source contracts as the complete completion contract. Perform implementation work only in the exact sealed task-worktree roots until an explicit goal-merge operation. After presumed completion, repeatedly audit the complete current scope from scratch and fix every incomplete finding until a new full audit finds none; only then finish the goal.
```
