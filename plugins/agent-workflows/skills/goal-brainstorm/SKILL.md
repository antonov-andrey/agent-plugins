---
name: goal-brainstorm
description: Use when a user wants to clarify an implementation idea, revise project design requirements, create an implementation specification, or prepare a persistent Codex goal before implementation.
---

# Goal Brainstorm

Turn an implementation idea into approved project contracts and one harness-neutral temporary specification and goal pair. Do not start production implementation during this workflow.

**REQUIRED REFERENCE:** Read `references/specification-contract.md` before selecting or changing any instruction, design, specification, or goal document.

## Workflow

1. Inspect the coordinating repository, every affected repository, applicable `AGENTS.md` files, relevant design/specification/goal documents, and the code and tests needed to verify current facts. Inspect persistent goal state before changing an existing task artifact pair. If goal status cannot be inspected, stop before editing that pair or creating a replacement and resume only after status can be verified.
2. Identify the real requirement owners. Before editing, show the user which existing documents will change, whether the paired specification uses direct-owner or dedicated mode, where the pair will live, and why each choice follows the reference contract.
3. Resolve the applicable outcome, scope, non-goals, ownership, public interfaces, data and state ownership, failure and recovery behavior, compatibility or migration, and verification design. Ask zero or more short questions, one at a time, only while material ambiguities remain. Do not ask for facts that can be established from the project. Surface contradictions and hidden assumptions as concrete decisions.
4. Offer alternatives only when a real design decision exists. State the recommended minimal option first and explain its material tradeoff.
5. Present the proposed design and its verification obligations in coherent sections sized for review. Obtain explicit approval before writing each corresponding contract change.
6. Update the approved owner documents. Create or update the paired specification in the mode selected through the reference contract.
7. Apply `Semantic Review` from the required reference to every changed and directly affected contract, then resolve every finding.
8. When semantic review passes, create or update the paired dated goal file defined by `Goal File` in the required reference. Its verification contract and the persistent objective MUST require `Terminal Completion Audit` from the reference. Semantically reread both task artifacts with their source contracts, then show the resulting document set and diff. Do not create an implementation plan, commit, push, or begin implementation.
9. Ask separately whether to activate the displayed goal version. On explicit confirmation, follow this capability and state matrix:
   - If goal status cannot be inspected, do not call a creation tool. Report that current state is unverified and provide the exact `/goal` command with an explicit no-unfinished-goal precondition.
   - If status shows an unfinished goal, report it and wait for the user to resolve it. Do not issue an activation command. Inspect status again after resolution.
   - If status shows no unfinished goal and a goal-creation tool is available, create the goal from the goal file.
   - If status shows no unfinished goal but no goal-creation tool is available, report only that automatic activation is unavailable and provide the exact `/goal` command.

## Terminal Rules

- Preserve unrelated user changes.
- If an owner conflict remains unresolved, stop without conflicting edits or a task artifact pair and report the exact decision still required.
- Follow the `.spec` retention and deletion authorization owned by `Lifecycle` in the required reference; never infer deletion authority from goal or task state.
- Goal activation MUST NOT weaken `Terminal Completion Audit` into one final checklist pass, one test run, one implementation self-review, or one request for additional completion evidence artifacts.
- For either `/goal` fallback, print this exact semantic command in the user's language:

```text
/goal When no other goal is unfinished, implement the objective in <goal-path>. Treat that file, its paired specification, and their source contracts as the complete completion contract. After presumed completion, repeatedly audit the complete current scope from scratch and fix every incomplete finding until a new full audit finds none; only then finish the goal.
```
