---
name: instruction-writer
description: Use when the user explicitly requests the `instruction-writer` skill or a project-local instruction-writing workflow.
---

# Instruction Writer

## Goal
Create, delete, restructure, or update repository instruction artifacts while preserving the owner model defined by `AGENTS.md`.

## When To Use
- Use this skill only when the user explicitly requests the `instruction-writer` skill or a project-local instruction-writing workflow.
- Do not trigger this skill only because a task changes one `AGENTS.md`, provider or project-local `Skill`, plugin support contract, role contract, or harness configuration.

## Canonical Sources
- `AGENTS.md`
- `agent-workflows:instruction-audit`
- `pytest`

## Inputs
- User-requested instruction-artifact scope or focus notes.
- Current repository state and current instruction-artifact changes.
- Any explicit semantic authority path or interpretation supplied by the user.

## Outputs
- Updated instruction artifacts.
- Updated direct references, code-contract tests, or project documentation artifacts when the instruction change requires them.
- Final response that states outcome, verification, and unresolved problems.

## Hard Rules
- This skill owns instruction-writing workflow only.
- Semantic audit of instruction artifacts is owned by `agent-workflows:instruction-audit`.
- Instruction artifacts are edited directly at their runtime paths; parallel template sources for generated instruction artifacts are forbidden.
- Inspect current repository state directly instead of trusting prior conversation state.
- Before changing normative instruction text, identify the concrete problem, generalized rule, and canonical owner.
- Broad instruction rewrites are forbidden unless the current issue framing identifies a concrete failure mode.
- Resolve each confirmed issue with the minimal instruction change required for that issue.
- Prefer the shortest wording that preserves the same semantics.
- When changed instruction text depends on details owned elsewhere, reference that owner instead of restating foreign-owner detail unless one owner-local trigger, boundary, or transition must stay explicit.
- In workflow text, keep steps limited to local actions and transitions; repeated requirements belong in the owning contract instead of the step text.
- When changing one instruction artifact, update direct references and behavior tests that enforce or reference that artifact. Update an executable checker only when it still decides one independently normative closed predicate completely; delete it when the changed contract exposes heuristic, selected-example, partial, or semantic inference.
- If the instruction change affects project documentation requirements, update the owning documentation artifact in the same run.

## Role-Prompt Contract
- Follow `project-standards:project-instruction-developer` and the `agent-workflows` plugin support owner `lib/subagent-role-contract.md`.
- Keep one role contract limited to mission, scope, limits, evidence requirements, handoff rules, and necessary workflow-specific execution details.
- Do not override repository-wide rules, duplicate provider standards, or copy canonical algorithms into one role prompt.
- Provider-owned Markdown role contracts or templates are canonical; consumer-local named-agent TOML is forbidden as a cross-harness dependency.

## Workflow
1. Inventory the requested instruction scope and current changed instruction artifacts.
2. Resolve the applicable owners and direct references from `AGENTS.md`.
3. Frame each intended instruction change as `Problem`, `Generalized rule`, and `Canonical owner`.
4. Apply the smallest direct artifact edit that resolves the framed problem.
5. Update any direct references, code-contract checks, tests, or project documentation artifacts required by the instruction change.
6. Run verification required by `AGENTS.md` for the changed artifact types.
7. Independently derive every applicable normative requirement from the complete affected owner closure, then semantically review the current artifacts and owner placement against each requirement. Do not derive or narrow this scope from scripts, tests, changed files, previous findings, or the implementation plan. If the review finds one problem or uncovered requirement, return to the applicable owner-resolution or edit step; after every fix rerun affected verification and restart this complete semantic review. Completion requires one fresh post-fix review with no findings and creates no separate evidence artifact.
8. Use `instruction-audit` only when the user explicitly requests the structured instruction-audit skill or one validated instruction-audit report.
9. Report outcome, verification, and unresolved problems.

## Verification
- Instruction-only changes require the complete direct semantic review in step 7 and direct checks for the changed artifact type.
- Mechanical and executable checks prove only their exact closed contracts and MUST NOT replace, seed, narrow, or close the semantic review.
- Changes to an executable instruction checker or tool require behavior tests at that checker's owning provider or project-local test boundary. Consumer `test/code/**` is used only for one exact consumer-specific integration contract, never as a copied reusable checker owner.
- Handoff verification MUST be selected from `Evidence And Verification Rules` in `AGENTS.md` for the changed artifact types.

## Anti-Patterns
- Automatically invoking the structured, report-producing `instruction-audit` workflow without an explicit user request; the direct artifact-free semantic review in step 7 remains mandatory.
- Treating passing mechanical checks as semantic audit.
- Adding broad rules without current evidence and a concrete failure mode.
- Duplicating repository-wide rules in one skill or role prompt instead of referencing `AGENTS.md`.
- Leaving direct references, tests, or project documentation artifacts stale after changing instruction contracts.
