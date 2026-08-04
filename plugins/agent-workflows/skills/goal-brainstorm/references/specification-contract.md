# Goal Source Contract

## Owners

- Applicable `AGENTS.md` files own durable project instructions.
- Root `DESIGN.md` and routed `design/**` documents own stable architecture and domain contracts.
- `docs/**` owns maintained operational and user documentation that is not stable design.
- `project-goals/<common-prefix>/spec.md` owns one coherent task-specific implementation and acceptance source.
- Its sibling `goal.md` owns the concise outcome and exact source-contract routing.
- After successful handoff, one Linear Project and its issue graph own operational task lifecycle. Git and GitHub own branches, commits, pull requests, checks and merges.

The goal source is not a parallel execution database. It contains no checkpoint, issue graph, attempt journal, worktree record, merge state or cleanup state.

## Document Selection

Use a direct owner update when the requested change belongs completely and naturally to one or two existing stable owner documents. The source pair states the required owner delta and verification without copying those owners.

Use a dedicated implementation specification when the outcome spans several owners, needs an integrating migration or rollout contract, or has shared acceptance criteria that do not belong in one stable owner. Stable owners may still change during implementation; the task specification owns only the temporary integrating requirements.

One source must describe one coherent final outcome and be fully decomposable into bounded Linear tasks. It does not need one implementation boundary or one repository. Separate unrelated outcomes instead of building one mega-source.

## Canonical Repository And Shape

The caller supplies the exact canonical `project-goals` root. The workflow binds its Git common directory and origin identity and never selects a nearby checkout by name scan.

New source shape is exactly:

```text
project-goals/
  YYYY-MM-DD-<semantic-name>/
    goal.md
    spec.md
```

The common prefix is a lowercase date-prefixed filesystem basename. It identifies the source directory only; it does not name implementation branches or worktrees.

`project-goals` has one clean synchronized `main` checkout, no linked worktrees, `.worktree`, bootstrap manifest or project-local `.spec` copies. Historical directories and historical `checkpoint.yaml` files remain Git history/data but are not current authoring input and are not rewritten.

## Atomic Direct-Main Authoring

Initial authoring and every pre-handoff revision publish the complete pair through one workspace-global non-blocking lock and compare-and-swap direct-main transaction. The transaction:

1. requires clean local `main` equal to freshly fetched `origin/main`;
2. stages only the exact pair in a detached index;
3. records a private content-digest journal before push;
4. pushes without force;
5. retries only a bounded disjoint concurrent main update;
6. rejects overlap with either source path;
7. recovers an interruption after commit construction or successful push without duplicating publication;
8. returns the canonical checkout to clean synchronized state before releasing its lock.

There is one `write` operation for both initial authoring and revision, plus read-only `validate`. There is no `seal`, `activate`, `revise`, checkpoint or persistent-goal state.

## Specification Content

Use a structure shaped by the outcome rather than a mandatory heading template. Include every applicable element:

- required outcome and problem;
- verified current state;
- scope and non-goals;
- approved decisions and rationale;
- ownership and public interfaces;
- data and state transitions;
- failure handling and recovery;
- migration and compatibility;
- intended decomposition constraints;
- verification obligations and observable acceptance criteria.

Describe the final steady state. Remove rejected alternatives, open questions, TODO markers, placeholders and transition-only compatibility that is not part of the target. Reference stable owners instead of copying their rules.

## Verification Design

For every changed observable behavior, identify:

- the observable contract;
- its verification owner and appropriate unit, integration, workflow, semantic or operational boundary;
- the success path, contract-defining failure and critical edge cases;
- required data, environment and external dependencies;
- an exact stable command when one exists;
- which verification tasks must block review and acceptance.

Automated tests verify behavior and public contracts rather than prose, file placement, private call order or mocked substitutes for a required live boundary. Semantic document review is direct and does not create a proof artifact merely to claim completion.

The source must require a fresh whole-outcome acceptance after the last fix. Review and acceptance are explicit downstream Linear tasks; they are not hidden inside one large implementation issue.

## Goal File

Keep `goal.md` concise and use this shape:

```markdown
# <Result name>

## Outcome

<Concrete final state.>

## Source Contracts

- `spec.md`: <document role>
- `<repository>/<stable-owner-path>`: <exact section or document role>

## Constraints

<Task-specific boundaries not already owned elsewhere.>

## Verification

<Observable completion definition or exact owner references.>
```

The goal does not copy its specification, predict a brittle implementation-file list or become a second architecture owner.

## Linear Handoff

`task-graph-create` receives exact canonical source identity, full Git commit, root-relative paths and complete content. That immutable commit is the source snapshot.

Before handoff, the pair remains freely revisable. Successful handoff is proven only after the Linear workflow fully publishes and rereads a Project graph and performs its single `Planned -> In Progress` activation transition. From then on, changes to that work belong to Linear issues, relations, comments and statuses. A later `project-goals/main` commit neither mutates nor reimports the active graph.

## Semantic Review

Before every publication, reread the complete pair and directly affected stable owners together. Confirm:

- one owner for every requirement;
- explicit ownership and public boundaries;
- complete state, failure and recovery semantics when applicable;
- verification coverage for every changed observable behavior;
- complete decomposability into bounded tasks;
- no unresolved decision, contradiction, duplicate carrier, unnecessary wrapper, secret or implementation chat history;
- no durable rule owned only by the temporary source when it belongs in project instructions or design.
