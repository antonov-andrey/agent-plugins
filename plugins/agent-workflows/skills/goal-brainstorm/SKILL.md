---
name: goal-brainstorm
description: Clarify a coherent implementation idea or architecture change, author or revise its project-goals goal.md and spec.md source pair, and publish that pair for a later Linear handoff without starting implementation.
---

# Goal Brainstorm

Turn one coherent outcome into a reviewable source contract. This workflow ends at a published `goal.md`/`spec.md` pair; it does not create a task graph, task workspace, branch, persistent harness goal, checkpoint, or implementation change.

Read `references/specification-contract.md` completely before changing a specification, goal, instruction, or design contract.

## Workflow

1. Inspect the exact affected repositories, applicable `AGENTS.md`, current design, code and tests needed to distinguish verified facts from proposals. Inspect the canonical `project-goals` checkout and any existing pair for the chosen common prefix.
2. Establish one coherent outcome, stable owners, source scope, non-goals, interfaces, state transitions, failure and recovery behavior, compatibility, and observable verification. The outcome may span repositories and decompose into independently executable tasks; do not force it into one implementation boundary.
3. Choose direct owner updates or a dedicated implementation specification according to the reference. During brainstorming, proposed stable-owner changes are source requirements for later Linear tasks, not implementation edits in Product repositories.
4. Present the complete proposed design and verification contract in reviewable sections. Ask only about material choices that cannot be discovered and obtain explicit user approval before publication.
5. Author both complete temporary input files and reread them together with every directly affected stable owner. Resolve every semantic finding, open question, contradiction, placeholder, duplicate owner and unverified observable behavior.
6. Run `scripts/source.py write` with the explicit canonical `project-goals` root, common prefix and both input paths. The command publishes exactly the pair through one serialized direct-main transaction and pushes it immediately.
7. Run `scripts/source.py validate`, report the exact source commit, fingerprint and root-relative paths, and stop. Recommend a fresh thread with `linear-agent-tools:task-graph-create` for handoff.

## Revision Boundary

Before successful Linear handoff, a correction is an ordinary revision of the same pair. Repeat semantic review and `write` with both complete files. There is no seal, active state or special revision command.

After handoff, the Linear Project and its issues own operational changes. The dispatched source remains the exact historical Git commit even if `project-goals/main` later advances. Do not use `goal-brainstorm` to mutate an active Linear graph; use a Linear-native graph delta or create a new independent source.

## Publication Boundary

- `project-goals` uses only its canonical clean `main` checkout and has no goal branch, linked worktree, `.spec`, bootstrap manifest, checkpoint or unpublished source state.
- Initial authoring and revision always publish the complete pair atomically. A spec-only directory or one locally approved but unpushed revision is invalid.
- The skill may commit and push only the exact approved `project-goals/<common-prefix>/goal.md` and `spec.md` paths. It does not commit, push or mutate implementation repositories.
- Preserve existing historical goal directories. Do not add compatibility state or reinterpret historical `checkpoint.yaml` files as current workflow input.

## Required Handoff

Report one contiguous source identity block:

```text
Coordination repository: <exact-project-goals-root>
Source directory: <exact-project-goals-root>/<common-prefix>
Goal: <common-prefix>/goal.md
Specification: <common-prefix>/spec.md
Source commit: <full-git-commit>
Source fingerprint: <sha256>
Next owner: linear-agent-tools:task-graph-create in a fresh Codex thread
```

Do not claim that publication activated implementation. Linear handoff occurs only after the next workflow has fully published, reread and activated one Linear Project graph.
