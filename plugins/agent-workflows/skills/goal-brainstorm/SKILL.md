---
name: goal-brainstorm
description: Clarify implementation ideas or design changes, prepare an isolated task worktree, create or inspect harness-neutral .spec task pairs, and prepare a persistent goal.
---

# Goal Brainstorm

Turn an implementation idea into approved project contracts, one isolated task worktree set, and one harness-neutral temporary specification and goal pair. Do not start production implementation during this workflow.

**REQUIRED REFERENCES:** Read both references before selecting or changing any instruction, design, specification, goal, or task-worktree state:

- `references/specification-contract.md`
- `references/worktree-contract.md`

## Workflow

1. Inspect the coordinating repository, every affected repository, applicable `AGENTS.md` files, relevant design/specification/goal documents, and the code and tests needed to verify current facts. Inspect persistent goal state before changing an existing task artifact pair. If goal status cannot be inspected, stop before editing that pair or creating a replacement and resume only after status can be verified. Keep this discovery read-only except for the ignored physical task specification allowed after design approval.
2. Identify the real requirement owners and participating repository boundaries. Before editing, show the user which existing documents will change, whether the paired specification uses direct-owner or dedicated mode, the exact common prefix, physical pair location, task branch, and `.worktree/` paths, and why each choice follows the required references.
3. Resolve the applicable outcome, scope, non-goals, ownership, public interfaces, data and state ownership, failure and recovery behavior, compatibility or migration, and verification design. Ask zero or more short questions, one at a time, only while material ambiguities remain. Do not ask for facts that can be established from the project. Surface contradictions and hidden assumptions as concrete decisions.
4. Offer alternatives only when a real design decision exists. State the recommended minimal option first and explain its material tradeoff.
5. Present the proposed design and its verification obligations in coherent sections sized for review. Obtain explicit approval before writing each corresponding contract change.
6. Create or update the approved specification in the coordinating main worktree's physical `.spec/`, then use one unchanged common prefix for both task-artifact filenames, every task branch, and every `.worktree/<common-prefix>` basename while applying `Goal Brainstorm Worktree Contract` to reach `repository_prepared` for every participating repository before any tracked owner change. Diagnose validation failures and apply every safe deterministic repair; stop only for a remaining ambiguity, unavailable dependency, or unauthorized destructive or publication action.
7. Bind every subsequent repository command to the exact prepared task roots. Update approved owner documents only inside those task worktrees, and update the physical specification only through their `.spec` link. Revalidate isolation before and after each tracked authoring phase, then run `contracts-authored` to validate the complete repository set and explicitly record `contracts_authored`. Do not begin semantic review, create the paired goal, or call `seal` while state remains `repository_prepared`.
8. After `contracts_authored` is recorded, apply `Semantic Review` from `references/specification-contract.md` to every changed and directly affected contract, then resolve every finding in the task worktrees.
9. When semantic review passes, create or update the paired dated goal file defined by `Goal File` in `references/specification-contract.md` through the `.spec` link. Its verification contract and the persistent objective MUST require `Terminal Completion Audit` from that reference. Semantically reread both task artifacts with their source contracts, run complete worktree validation, seal the task artifacts, then show the resulting document set and task-worktree diff. Do not create an implementation plan, commit, push, merge, or begin implementation.
10. Ask separately whether to activate the displayed sealed goal version. On explicit confirmation, inspect persistent goal state again and rerun complete sealed worktree validation, including every deterministic repair, before following this capability and state matrix:
   - If goal status cannot be inspected, do not call a creation tool. Report that current state is unverified and provide the exact `/goal` command with an explicit no-unfinished-goal precondition.
   - If status shows an unfinished goal, report it and wait for the user to resolve it. Do not issue an activation command. Inspect status again after resolution.
   - If status shows no unfinished goal and a goal-creation tool is available, create the goal from the goal file, immediately record `active` through the worktree script, and require a fresh `active` validation before implementation continues.
   - If status shows no unfinished goal but no goal-creation tool is available, report only that automatic activation is unavailable and provide the exact `/goal` command.

## Mandatory Identity Handoff

Before sending either a proposed-design handoff or a sealed-goal handoff, assemble one contiguous identity block. Do not scatter these facts across workflow steps or refer back to an earlier list:

```text
Specification: .spec/<common-prefix>-spec.md
Goal: .spec/<common-prefix>-goal.md
Branch: <common-prefix>
Task roots:
- <repository-one>/.worktree/<common-prefix>
- <repository-two>/.worktree/<common-prefix>
Specification links: every task-root .spec is one relative link to the coordinating main worktree's physical .spec/ directory.
Execution boundary: every subsequent repository command stays in the exact task roots above until an explicitly authorized merge.
```

List every participating task root in that same block. Use exact resolved repository paths when they are known. A summary such as “the pair above,” facts split among numbered steps, or a handoff that omits one field does not satisfy this requirement.

## Terminal Rules

- Preserve unrelated user changes.
- If an owner conflict remains unresolved, stop without conflicting edits or a task artifact pair and report the exact decision still required.
- Follow the `.spec` retention and deletion authorization owned by `Lifecycle` in `references/specification-contract.md`; never infer deletion authority from goal or task state.
- Follow `Diagnosis And Repair` in `references/worktree-contract.md` before treating wrong-root execution, baseline drift, broken links, submodule drift, resource drift, or private-state damage as a blocker.
- If this agent wrote a task patch into a participating main worktree, invoke `recover-main-leak` for the exact known paths and revalidate without asking the user. Never infer agent provenance from matching bytes alone.
- If committed main drift overlaps task paths, preserve both states and ask only whether the exact reported owner root, commit, and complete owner-relative path set are independent work to accept. After confirmation, invoke `accept-main-commit-drift` with that exact identity; never edit private state, cross a delegated submodule boundary, or infer the attestation.
- Once `repository_prepared` is reached, all tracked task work, verification, and Git inspection MUST remain bound to the recorded task roots until an explicitly authorized merge.
- Every proposed-design handoff and every sealed-goal handoff MUST include the complete contiguous `Mandatory Identity Handoff` block. Reread that block field by field immediately before responding.
- Goal activation MUST NOT weaken `Terminal Completion Audit` into one final checklist pass, one test run, one implementation self-review, or one request for additional completion evidence artifacts.
- For either `/goal` fallback, print this exact semantic command in the user's language:

```text
/goal When no other goal is unfinished, implement the objective in <goal-path>. Treat that file, its paired specification, and their source contracts as the complete completion contract. Perform all repository work only in the exact prepared task-worktree roots returned by sealed state until an explicit merge. After presumed completion, repeatedly audit the complete current scope from scratch and fix every incomplete finding until a new full audit finds none; only then finish the goal.
```
