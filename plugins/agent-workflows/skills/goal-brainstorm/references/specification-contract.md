# Goal Brainstorm Document Contract

## Document Owners

- Applicable `AGENTS.md` files own durable project instructions and engineering constraints for their path scope.
- Root `DESIGN.md` owns the stable architecture and serves as its canonical entry point.
- `design/**.md` owns detailed stable contracts for distinct architecture or domain areas when one `DESIGN.md` is insufficient.
- `docs/**` owns user, operational, and other documentation that is not a stable design contract.
- `.spec/*-spec.md` owns one temporary task-specific implementation contract.
- `.spec/*-goal.md` owns one concise executable objective and exact references to its paired specification and approved stable source contracts.

A goal is not a second design or specification owner. A specification must not copy durable instructions or architecture already owned elsewhere. Completed or abandoned task artifacts are not project documentation and must not remain in `.spec/`.

## Document Selection

A multi-repository change has one coordinating repository that owns its specification and goal pair. Coordination ownership must follow the current project contracts or an explicit user decision.

Use **Direct Owner Update** when the change is a small amendment to existing instructions or design, or when even a substantial change belongs completely and naturally to one or two existing owner documents. Update those owners. The paired specification identifies the outcome, affected owners, task boundary, and verification without copying their requirements.

Use **Dedicated Implementation Specification** when the task has a substantial standalone brief, spans several owners, needs one task-level migration or rollout contract, carries shared acceptance criteria across components, or would pollute stable owner documents with implementation-specific constraints. Existing owner documents may still change, but the specification owns only the integrating task-specific requirements.

If user intent conflicts with a current owner, change that owner after approval rather than hiding the conflict in a new specification.

Trivial work that does not need a persistent goal creates neither file. Every successfully completed brainstorm that prepares a persistent goal creates both the specification and goal.

## Artifact Directory

Task artifacts live under the harness-neutral root directory `.spec/`. Before creating them, ensure that the coordinating repository has the exact root-level ignore rule:

```gitignore
/.spec/
```

The directory contains ordinary Markdown only. Vendor-specific frontmatter, harness session state, lock files, caches, and project-global durable rules are forbidden there.

The directory may be absent when no task artifacts are active. Do not add `.gitkeep` or another tracked placeholder.

## File Names

Create one pair with the same creation date and stable semantic prefix:

```text
.spec/YYYY-MM-DD-<semantic-name>-spec.md
.spec/YYYY-MM-DD-<semantic-name>-goal.md
```

Reuse a same-day prefix only for the same task. Choose a more precise semantic name for a different task instead of adding a numeric suffix.

Do not rename an existing specification while continuing the same task. Update an existing pair only while continuing the same inactive objective. Create a current-date pair for a new objective or a follow-up to a completed objective.

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

The approved specification describes the final steady state. It must not retain rejected alternatives, open questions, `TODO` markers, placeholders, compatibility bridges that are not part of the target, or a step-by-step implementation plan.

For a direct owner update, keep the specification concise and reference the exact stable owners instead of restating them.

## Verification Design

Design verification before approving each changed observable behavior. Apply the affected project's existing test and verification contracts by reference instead of restating their framework, placement, fixtures, or command rules. For every changed observable behavior, the approved owner documents must identify:

- the observable contract or outcome;
- the verification owner and the appropriate unit, integration, workflow, migration, semantic, or operational level;
- the success path, primary contract-defining failure path, and critical new edge cases;
- required data, environment, or external dependencies and an exact stable command when one exists.

For changed executable behavior, the approved owner documents must require adding or updating automated behavior tests whenever those tests are direct evidence of correctness. Existing tests satisfy this obligation only when the verification design identifies how they already exercise every changed contract branch. Name a concrete test file only when that path is itself a stable owner; otherwise identify the owning test family or verification boundary.

Automated tests must verify executable behavior and public contracts rather than private call sequences, incidental class or file layout, or mocked interactions used as a substitute for required boundary behavior.

Instruction, design, and specification artifacts are verified through semantic reread or semantic audit. Never design pytest assertions over their prose, headings, examples, file presence, or placement.

When automated testing is not appropriate, specify the exact semantic or operational verification and why it is sufficient. If a requirement cannot be observed unambiguously, refine its interface or acceptance criteria before approval. The brainstorm writes no test code and must not turn verification design into a step-by-step implementation plan.

## Goal File

Keep the goal materially below the persistent objective limit and use this shape:

```markdown
# <Result name>

## Outcome

<Concrete final state.>

## Source Contracts

- `<paired-spec-path>`: `<document role>`
- `<stable-owner-path>`: `<exact section or document role>`

## Constraints

<Only task-specific boundaries not already expressed by the source references.>

## Verification

<Verifiable completion definition or exact references to its owners.>
```

The `Verification` section must reference the approved verification obligations and every applicable project test-contract owner without copying their rules.

The goal states the outcome, essential constraints, and verification while giving an agent exact file context and freedom to build and revise its working plan. It must not copy source contracts, predict a brittle implementation-file list, or split one multi-repository objective into several goals.

Use root-relative paths for contracts in the coordinating repository. Cross-repository references must identify both the canonical repository and its root-relative contract path unambiguously without embedding one user-specific absolute workspace root.

The persistent objective should name the goal file, treat that file and its paired specification as the completion contract, require the full applicable verification, and require `Terminal Completion Audit`. Keep detailed context in project files instead of expanding the objective.

## Terminal Completion Audit

After implementation appears complete, audit the whole current task scope again from scratch against the complete goal file, paired specification, and every referenced stable source contract. The audit MUST inspect the current repository and external state owned by the task and MUST NOT be limited to the implementation plan, changed files, completed checklist entries, passing verification, or findings from an earlier audit.

If the audit finds any unfinished, contradictory, missing, stale, or unverified requirement, keep the goal active, fix every finding, rerun every verification affected by those fixes, and start another complete audit from scratch. Repeat this audit/fix cycle until one new full audit performed after the last fix finds no unfinished requirement.

Goal completion is allowed only after that zero-finding full audit and after all required verification remains successful. A fixed iteration limit, a weaker partial recheck, or completion because the remaining work is expensive is forbidden. A genuine external blocker follows the harness goal-blocking contract instead of being reported as completion.

This terminal cycle uses the current task contracts and current system state directly. It MUST NOT require a separately generated completion ledger, evidence document, completion report, or other persistent proof artifact.

## Lifecycle

1. Inspect persistent goal state before modifying an existing pair or creating a replacement for the same objective.
2. Create or update the approved specification after the design decisions and owner changes it depends on are approved.
3. Apply `Semantic Review` before creating or updating the paired goal.
4. Show both files and their stable source contracts before asking separately whether to activate the goal.
5. Keep the pair while the task is active, blocked, or explicitly paused.
6. Before presumed completion, move every durable resulting rule into its stable owner and confirm that deleting the pair loses no current contract.
7. Run `Terminal Completion Audit` to its zero-finding fixed point.
8. Delete both files after the task is completed or explicitly abandoned.

Workspace audit must report a stale pair whose task is known to be completed or abandoned. It must not delete an active, blocked, paused, or unclassified pair automatically.

## Semantic Review

Before creating the goal, reread all changed and directly affected documents as one contract set. This pre-activation review does not replace `Terminal Completion Audit` after implementation. Confirm that:

- each requirement has one owner;
- references identify exact source documents or sections without duplicating their content;
- public interfaces and ownership boundaries are explicit;
- state transitions, failure behavior, and recovery are complete when applicable;
- verification design satisfies `Verification Design` for every changed observable behavior;
- no open decision, contradiction, unnecessary wrapper, duplicated carrier, or transition-only target remains;
- the paired task artifacts contain no durable rule that belongs in `AGENTS.md`, `DESIGN.md`, `design/**`, `docs/**`, code, or a public interface.
