# Goal Brainstorm Document Contract

## Table Of Contents

- [Document Owners](#document-owners)
- [Document Selection](#document-selection)
- [Coordination Repository](#coordination-repository)
- [Task Directory And Identity](#task-directory-and-identity)
- [Implementation Specification](#implementation-specification)
- [Verification Design](#verification-design)
- [Goal File](#goal-file)
- [Terminal Completion Audit](#terminal-completion-audit)
- [Lifecycle](#lifecycle)
- [Semantic Review](#semantic-review)

## Document Owners

- Applicable `AGENTS.md` files own durable project instructions and engineering constraints for their path scope.
- Root `DESIGN.md` owns stable architecture and serves as its canonical entry point.
- `design/**.md` owns detailed stable contracts for distinct architecture or domain areas when one `DESIGN.md` is insufficient.
- `docs/**` owns user, operational, and other maintained documentation that is not a stable design contract.
- `project-goals/<common-prefix>/spec.md` owns one temporary task-specific implementation contract.
- `project-goals/<common-prefix>/goal.md` owns one concise executable objective and exact references to its paired specification and approved stable source contracts.
- `project-goals/<common-prefix>/checkpoint.yaml` owns published cross-repository closing-commit snapshots and accepted-checkpoint identity; `agent-workflows:goal-checkpoint` owns its mutation contract.
- `plugins/agent-workflows/skills/goal-brainstorm/references/worktree-contract.md` owns reusable task-worktree preparation, bootstrap, isolation, revision, sealing, and activation semantics.
- `agent-workflows:goal-merge` owns checkpoint merge and primary-environment acceptance; `agent-workflows:goal-delete` owns explicitly authorized synchronized deletion.

A goal is not a second design or specification owner. A specification must not copy durable instructions or architecture already owned elsewhere. Task artifacts are tracked coordination state, not project documentation or Product source. Their Git history is retained, and current task-directory deletion requires an explicit user request through `agent-workflows:goal-delete`.

## Document Selection

Use **Direct Owner Update** when the change is a small amendment to existing instructions or design, or when even a substantial change belongs completely and naturally to one or two existing owner documents. Update those owners. The task specification identifies the outcome, affected owners, task boundary, and verification without copying their requirements.

Use **Dedicated Implementation Specification** when the task has a substantial standalone brief, spans several owners, needs one task-level migration or rollout contract, carries shared acceptance criteria across components, or would pollute stable owner documents with implementation-specific constraints. Existing owner documents may still change, but the specification owns only the integrating task-specific requirements.

If user intent conflicts with a current owner, change that owner after approval rather than hiding the conflict in a new specification. Trivial work that does not need a persistent goal creates no task directory. Every successfully completed brainstorm that prepares a persistent goal creates `spec.md`, `goal.md`, and an initial `checkpoint.yaml`.

## Coordination Repository

The canonical coordination repository is `project-goals`. The caller supplies its exact local root, the skill shows that root before authoring, and the provider binds its Git common directory and origin identity; it must not select a nearby repository by name or directory scan. Project repositories create no new `.spec/`, task-artifact link, or task-artifact copy. Pre-cutover ignored `.spec` files are inert historical artifacts: this workflow neither reads nor deletes them without the separate explicit authorization owned by their legacy task lifecycle.

The `project-goals` repository uses only its canonical `main` checkout. It has no task branch, linked worktree, or bootstrap manifest. After each approved task-contract authoring phase, the applicable command commits and pushes only the exact task-directory delta through one workspace-global serialized direct-main transaction. The transaction starts from clean synchronized `main`, uses compare-and-swap fast-forward publication, and returns the checkout to clean synchronized state before releasing its lock. No approved revision may remain only in a working tree or side branch.

If unrelated coordination changes advance `main`, the transaction may replay the exact unchanged task-directory delta on the new tip. If the same task directory changed concurrently, it stops for semantic conflict resolution. `seal` publishes any exact final task-directory delta, then binds the resulting `project-goals/main` commit and task-file fingerprints.

A sealed coordination commit means only that the candidate is internally consistent and available for review. It is not user approval, goal activation, a checkpoint, or permission to merge Product source.

## Task Directory And Identity

Create one directory whose common prefix is the creation date plus a stable semantic name:

```text
project-goals/
  YYYY-MM-DD-<semantic-name>/
    checkpoint.yaml
    goal.md
    spec.md
```

The common prefix is one filesystem basename, passes `git check-ref-format --branch` unchanged, and contains no path separator. Reuse a prefix only for the same inactive task. Do not rename a task while continuing the same objective; create a current-date prefix for a distinct or completed follow-up objective.

The same common prefix identifies every participating implementation task branch and `.worktree/<common-prefix>` basename. It does not identify a `project-goals` branch or worktree. `checkpoint.yaml` is created with the closed initial value defined by `project-goals/DESIGN.md`; `goal-brainstorm` does not append or accept checkpoints.

## Implementation Specification

Use a structure shaped by the task rather than a mandatory heading template. Include every applicable semantic element:

- required outcome and problem;
- verified current state;
- scope and non-goals;
- approved decisions and their rationale;
- target behavior;
- public interfaces, models, and data owners;
- state transitions for stateful behavior;
- failure handling and recovery;
- migration and compatibility;
- changes by repository or owner component;
- verification obligations and observable acceptance criteria required by `Verification Design`.

The approved specification describes the final steady state. It must not retain rejected alternatives, open questions, `TODO` markers, placeholders, compatibility bridges that are not part of the target, or a step-by-step implementation plan. For a direct owner update, keep it concise and reference the exact stable owners instead of restating them.

## Verification Design

Design verification before approving each changed observable behavior. Apply each affected project's existing test and verification contracts by reference instead of restating framework, placement, fixtures, or command rules. For every changed observable behavior, the approved owner documents identify:

- the observable contract or outcome;
- the verification owner and the appropriate unit, integration, workflow, migration, semantic, or operational level;
- the success path, primary contract-defining failure path, and critical new edge cases;
- required data, environment, or external dependencies and an exact stable command when one exists.

For changed executable behavior, require automated behavior tests whenever they are direct evidence of correctness. Existing tests count only when the design identifies how they exercise every changed contract branch. Name a concrete test file only when that path is itself stable; otherwise identify the owning test family or verification boundary.

Tests verify observable behavior and public contracts rather than private call order, incidental class or file layout, or mocked interactions used instead of a required boundary. Instruction, design, and task artifacts are verified through semantic reread or audit, never pytest assertions over prose, headings, examples, file presence, or placement. When automation is inappropriate, state the exact semantic or operational verification and why it is sufficient.

## Goal File

Keep `goal.md` materially below the persistent-objective limit and use this shape:

```markdown
# <Result name>

## Outcome

<Concrete final state.>

## Source Contracts

- `spec.md`: <document role>
- `<repository>/<stable-owner-path>`: <exact section or document role>

## Constraints

<Only task-specific boundaries not already expressed by source references.>

## Verification

<Verifiable completion definition or exact references to its owners.>
```

The goal states the outcome, essential constraints, and verification while giving the implementing agent exact file context and freedom to build and revise its working plan. It must not copy source contracts, predict a brittle implementation-file list, or split one multi-repository objective into several goals.

Cross-repository references identify the canonical repository and root-relative path without a user-specific workspace root. The persistent objective names the tracked goal path in `project-goals`, treats it, `spec.md`, and their stable source contracts as the completion contract, binds work to the sealed task roots, requires full applicable verification, and requires `Terminal Completion Audit`.

## Terminal Completion Audit

After implementation appears complete, audit the whole current task scope again from scratch against `goal.md`, `spec.md`, every referenced stable owner, and current external state owned by the task. The audit must not be limited to the plan, changed files, completed checklist entries, passing verification, or findings from an earlier audit.

If any unfinished, contradictory, missing, stale, or unverified requirement is found, keep the goal active, fix every finding, rerun affected verification, and start another complete audit from scratch. Repeat until one new full audit performed after the last fix finds no unfinished requirement. A fixed iteration limit, a partial recheck, or completion because remaining work is expensive is forbidden. A genuine external blocker follows the harness goal-blocking contract.

This cycle uses current task contracts and system state directly. It must not require a separately generated completion ledger, evidence document, completion report, or other persistent proof artifact.

## Lifecycle

1. Inspect persistent goal state before changing an existing task directory or creating another directory for the same objective.
2. After design approval, create or update `spec.md` through the serialized `project-goals/main` transaction.
3. Prepare every participating implementation worktree through `Preparation Lifecycle` in `worktree-contract.md`.
4. Update approved implementation-repository stable owners only in recorded task roots. Apply an approved `project-goals` stable-owner change only through its direct-main transaction. Run `contracts-authored`, apply `Semantic Review`, and resolve every finding.
5. Prepare the approved goal input. `seal` creates the initial closed `checkpoint.yaml` when absent and publishes both files through the direct-main transaction.
6. Validate the complete set, seal and publish any exact final task-directory delta to `project-goals/main`, bind its commit and fingerprints, then show the task directory, stable-source changes, and task-root diff before asking separately whether to activate. `goal_ready` records consistency, not approval.
7. Before activation, treat every user correction or scope addition as an ordinary revision of the same task. Run `revise`, preserve existing task worktrees and content, extend participants when required, and repeat authoring, review, and seal. Do not create a prerequisite or replacement goal merely because an earlier candidate was sealed or published.
8. Activate only after separate explicit user approval and a fresh sealed validation. Active `spec.md`, `goal.md`, task identity, and participant set are immutable.
9. Before presumed completion, move every durable resulting rule into its stable owner and confirm the task artifacts are not the only owner of a current durable contract.
10. Run `Terminal Completion Audit` to its zero-finding fixed point.
11. Completion, abandonment, checkpointing, or merge never deletes the task directory. Deletion belongs only to an explicitly invoked `agent-workflows:goal-delete` operation and remains visible in prior Git history.

## Semantic Review

Before sealing, reread all changed and directly affected documents as one contract set. This pre-activation review does not replace the terminal audit. Confirm that:

- each requirement has one owner;
- references identify exact source documents or sections without duplicating content;
- public interfaces and ownership boundaries are explicit;
- state transitions, failure behavior, and recovery are complete when applicable;
- coordination repository identity, task prefix, participating roots, bootstrap ownership, and isolation recovery agree with `worktree-contract.md`;
- verification design covers every changed observable behavior;
- no open decision, contradiction, unnecessary wrapper, duplicated carrier, or transition-only target remains;
- task artifacts contain no durable rule that belongs in `AGENTS.md`, `DESIGN.md`, `design/**`, `docs/**`, code, or a public interface.
