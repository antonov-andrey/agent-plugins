# Goal Brainstorm Worktree Contract

## Table Of Contents

- [Ownership And Boundary](#ownership-and-boundary)
- [Task Identity](#task-identity)
- [Coordination Main Transaction](#coordination-main-transaction)
- [Repository Set](#repository-set)
- [Bootstrap Manifest](#bootstrap-manifest)
- [Resource Classes](#resource-classes)
- [External Cleanup Declaration](#external-cleanup-declaration)
- [Preparation Lifecycle](#preparation-lifecycle)
- [Submodules](#submodules)
- [Private State](#private-state)
- [Isolation Validation](#isolation-validation)
- [Diagnosis And Repair](#diagnosis-and-repair)
- [Library And Script Interface](#library-and-script-interface)
- [Publication Handoff](#publication-handoff)
- [Verification Contract](#verification-contract)

## Ownership And Boundary

This reference is the canonical reusable owner of task identity, central coordination binding, implementation-worktree preparation, bootstrap resources, submodule handling, isolation validation, deterministic repair, pre-activation revision, sealing, and activation for `agent-workflows:goal-brainstorm`.

`project-goals/DESIGN.md` owns tracked task-directory, checkpoint, merge, and deletion semantics. `agent-workflows:goal-checkpoint`, `agent-workflows:goal-merge`, and `agent-workflows:goal-delete` own those operations. `goal-brainstorm` must not expose a cleanup, checkpoint, or merge command and must not infer authorization for them.

Applicable project `AGENTS.md` files own concrete project paths. Each participating implementation repository binds `.worktree/` and root `worktree-bootstrap.yaml` to this contract without copying reusable semantics. The `project-goals` repository uses only canonical `main` and has neither path. The current workflow creates no project-local `.spec/` or task-artifact link and ignores inert pre-cutover files instead of treating them as identity or deleting them.

The workflow may commit and push only its exact approved coordination path set directly to `project-goals/main`. A task-artifact operation owns only its exact task directory; an approved `project-goals` stable-owner operation owns only its explicitly declared stable paths. The workflow never commits, pushes, merges, or deletes implementation-project source.

## Task Identity

One common prefix identifies the complete task:

```text
project-goals/<common-prefix>/spec.md
project-goals/<common-prefix>/goal.md
project-goals/<common-prefix>/checkpoint.yaml
<project>/.worktree/<common-prefix>/
refs/heads/<common-prefix>
```

The prefix is derived from the task directory basename and used unchanged for every participating implementation branch and linked-worktree basename. It must be one canonical filesystem basename, pass `git check-ref-format --branch`, and contain no path separator.

The workflow verifies the canonical `project-goals` repository identity, clean local `main`, exact `origin/main`, exact task directory, registered implementation worktrees, target paths, and private state before adoption. Existing state is reusable only for the same inactive task with matching identity. A colliding implementation path, branch, worktree registration, or task directory belonging to another task is a blocker and is never renamed or overwritten automatically.

Before creating a branch or worktree, the provider writes durable pending ownership below Git administration state. Resumption accepts only the exact recorded baseline, branch, registered path, Git common directory, index state, and provider-owned outputs. Partial checkouts, substituted directories, redirected `.git` pointers, staged drift, or unknown objects are not adoption evidence.

## Coordination Main Transaction

Every `project-goals` mutation is one short provider-owned transaction. Before reading mutable coordination state, the command obtains one non-blocking workspace-global write lock in the canonical repository Git common directory. It verifies the expected origin identity, checked-out `main`, an empty index and working tree, and exact local equality with fetched `origin/main`.

The transaction records durable intent, applies only the exact closed coordination path set owned by the command, rejects every unrelated index or working-tree change, creates one ordinary commit whose parent is the verified remote tip, and pushes it to `origin/main` without force. It releases the lock only after local `main`, `origin/main`, the index, and the working tree are synchronized and clean.

If the remote advances first through disjoint coordination paths, the command fetches the new tip, reapplies the unchanged exact delta, and retries. A concurrent change that overlaps the owned path set is a semantic conflict. Crash recovery resumes or rolls back only the recorded transaction phase and never discards unknown local or remote state.

## Repository Set

The workflow receives the complete affected top-level repository set from approved contracts and current task scope. It must not scan a workspace and infer participants from proximity, names, submodules, or dirty state.

The `project-goals` repository is the coordination carrier, never receives a task branch or linked worktree, and is never a checkpoint entry. Stable changes to that repository use the same direct-main coordination transaction and may not create a self-referential checkpoint entry.

For each top-level participant, discover recursive submodules before creating worktrees and classify each boundary as:

- read-only at the recorded gitlink; or
- explicitly task-owned with its own branch, worktree identity, and verification.

Undeclared dirty submodule state blocks preparation until its ownership is understood. `prepare` may extend the top-level or task-owned-submodule set only in `repository_prepared`. `contracts_authored` and `goal_ready` freeze the candidate set for review but do not mean approval. Before activation, `revise` returns the same task to `repository_prepared`, after which expansion is allowed. Revision never silently removes an existing participant or possible task work.

## Bootstrap Manifest

Every participating implementation top-level repository and task-owned submodule boundary has one tracked root `worktree-bootstrap.yaml`:

```yaml
schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
```

An owner with task-scoped external resources may add:

```yaml
cleanup:
  command_argument_list:
    - python
    - development_environment_manage.py
    - destroy
    - --git-worktree
    - "{common_prefix}"
```

The schema is closed and follows the shared machine-readable format contract: UTF-8, one YAML 1.2 document, `.yaml`, exact scalar and collection types, and rejection of duplicate keys, custom tags, anchors, aliases, merge keys, and unknown fields. No TOML or `.yml` compatibility reader remains in the target implementation.

Each resource path is one normalized root-relative POSIX path with no empty, absolute, `.`, `..`, NUL, backslash, or escape component. Duplicates, ancestor/descendant overlaps, cross-class overlaps, submodule crossings, special files, hardlinks, and reserved paths are rejected. Reserved paths include `.git`, `.worktree`, the manifest itself, and their descendants.

The provider never infers a resource class from a name, project, or content. A missing manifest is repaired in the task worktree before implementation by creating the empty current-version manifest. It does not trigger workspace-wide migration.

## Resource Classes

- `copy_required_path_list`: source must exist; copy the exact file, directory, or internal symbolic-link graph into the task worktree. The copy is independent and preserves type, mode, bytes, empty directories, and link targets without hardlinks.
- `copy_optional_path_list`: copy with the same semantics when present; exact source absence is a recorded skip.
- `link_required_path_list`: source must exist; create one relative symbolic link to the main-worktree object.
- `link_optional_path_list`: link when present; exact source absence is a recorded skip.

Required absence blocks preparation. Links may target only inside the declared source object and may not traverse or resolve outside repository ownership. A committed destination must match declared semantics; no tracked destination is replaced automatically. Copy-source drift remains observable through full fingerprints, including ignored files.

## External Cleanup Declaration

The optional `cleanup` mapping declares one project-owned idempotent external-resource hook for later use by `agent-workflows:goal-delete`. `goal-brainstorm` validates and seals the declaration but never executes it.

`command_argument_list` is a non-empty direct argv list. Shell evaluation, word splitting, environment expansion, operators, substring interpolation, and unknown placeholders are forbidden. The only replacement is an argument exactly equal to `{common_prefix}`.

Sealing binds the normalized declaration and manifest fingerprint into private state. A task-scoped resource creator must verify the same binding before its first mutation. A self-hosting task that introduces this current schema and its first consumer may bind the exact declaration specified by the sealed task contract once, before any external mutation, through a crash-safe content-free receipt. A later declaration change or missing receipt fails closed.

Detailed request/result schema, external absence proof, journal, execution order, and deletion authorization belong to `agent-workflows:goal-delete`; they are not duplicated here.

## Preparation Lifecycle

The lifecycle is monotonic except for explicit inactive revision:

1. `discovery`: inspect repositories, task state, stable owners, code, and verification without writes.
2. `design_approved`: create or update the task directory through one serialized direct-main transaction and bind the published `project-goals/main` commit.
3. `repository_identified`: validate every participant and record exact baselines before creating implementation worktrees.
4. `worktree_created`: create each branch and linked worktree from its recorded committed baseline.
5. `repository_prepared`: initialize recursive submodules, validate or create current bootstrap manifests, author minimum durable `.worktree/` ignore behavior, materialize declared resources, and validate every boundary.
6. `contracts_authored`: validate the complete set after approved implementation-task-root changes and any approved direct-main `project-goals` owner change, then explicitly record that authoring is finished.
7. `goal_ready`: complete semantic review, create `goal.md` and initial `checkpoint.yaml`, publish any exact final task-directory delta through the direct-main transaction, validate the exact candidate, and seal its exact commit and hashes. This state is not approval.
8. `active`: after the harness successfully creates the persistent goal, record activation and freeze task identity, participants, `spec.md`, and `goal.md`.

Before `active`, `revise` may return `contracts_authored` or `goal_ready` to `repository_prepared`. From `goal_ready`, it first proves the sealed coordination commit and files unchanged. It retains prior candidate identity as provenance, preserves all implementation worktrees and changes, and allows task-contract edits and participant expansion. The complete `contracts-authored` → semantic review → `seal` cycle then repeats. `revise` is idempotent in `repository_prepared` and forbidden in `active`.

Temporary provider-owned local excludes are limited to exact `.worktree/` or declared bootstrap destinations needed during first adoption. They are recorded and removed only after a durable merged ignore rule supersedes them.

## Submodules

Preparation runs recursive synchronization and initialization from each task worktree and validates every nested boundary. A task-owned submodule uses a physical independent checkout, never a symlink to main, and receives its own private-state replica and manifest processing.

Read-only submodules remain at exact recorded gitlinks. Task-owned submodules may advance only to descendants of their recorded baselines, and their parent gitlinks remain explicit task changes. Scheduling and mutation never cross from a parent owner into delegated submodule internals.

Every Git query disables repository-level submodule-ignore suppression and uses literal path handling. Before repairing a clean checkout, validation checks ignored untracked collisions against the target tree. Dirty or unavailable state that could contain user work is preserved and reported rather than reset.

## Private State

Store harness-independent workflow state only below per-worktree Git administration paths resolved by Git. Replicate complete state across participating Git common directories so any recorded task root can recover the task without workspace scanning. Do not store task-file content in private state.

State binds at least:

- schema version and common prefix;
- canonical `project-goals` repository, task directory, exact local and remote `main` commit, sealed commit, and task-file fingerprints;
- each main root, task root, branch, baseline commit, Git administration identity, index, and dirty-state fingerprint;
- task-owned submodules and delegated boundaries;
- manifest fingerprints, resource states, skipped optional resources, and cleanup-declaration binding;
- accepted caller attestations, main-leak provenance, provider-owned excludes, and pending durable transactions.

Writes use atomic replacement, file fsync, and parent-directory fsync. Pending worktree, resource, repair, coordination-publication, private-write, and migration operations expose durable intent before mutation. Staging identities are unpredictable and owned only by matching recorded intent; deterministic filenames alone never prove ownership. Fingerprints include object type, mode, structure, raw link target, and length-delimited bytes, preserving non-UTF-8 names without lossy conversion.

No obsolete state or preimage is retired until every current replica records the successor. Recovery completes or rolls back only an exact known phase and reports each repair once. Unknown or contradictory state fails closed.

## Isolation Validation

Before and after tracked implementation authoring, implementation, verification, or implementation-repository Git phases, resolve the current Git top level and compare it with the recorded task root. Coordination authoring runs only inside `Coordination Main Transaction` against the canonical `project-goals` root. Commands use explicit working directories.

Complete validation proves:

- common prefix, central task path, clean synchronized coordination `main`, bound commit, and current lifecycle;
- every implementation branch, worktree registration, canonical task root, baseline relation, Git common directory, `.git` pointer, and HEAD;
- no task-owned tracked change leaked into main and every recorded unrelated main change remains preserved;
- sealed `spec.md` and `goal.md` hashes after sealing and while active;
- recursive submodule state, physical ownership, exact read-only gitlinks, and baseline-descendant task-owned changes;
- current YAML manifest schema/hash, resource type and fingerprint, link/copy behavior, ignore behavior, and cleanup binding;
- consistency of every private-state replica with observable Git and filesystem state.

Main may receive independent user work. A new main commit or dirty path is recorded as unrelated only when provenance is clear and it neither overlaps task diffs nor prepared resource boundaries. Overlap includes equality, ancestor, and descendant paths. Matching bytes do not prove provenance. Ambiguous overlap is preserved and requires explicit user classification.

Starting in the wrong directory is repairable when one recorded task identity is unambiguous: reroute to the exact task root and report it.

## Diagnosis And Repair

A failed validation first identifies the changed object, owner, recorded and current state, likely producer, and smallest safe correction. Deterministic repairs include:

- rerouting from a wrong root;
- repairing a linked-worktree `.git` pointer or registration only from complete exact ownership proof;
- restoring a provider-created exclude or interrupted private-state write;
- reconstructing missing inactive private state from one complete agreeing replica;
- synchronizing and initializing missing recursive submodules;
- restoring a clean read-only submodule to its recorded gitlink after collision checks;
- completing or rolling back a known pending resource or coordination transaction;
- creating a missing initial current-version manifest;
- recording provably independent non-overlapping main work;
- restoring caller-known leaked paths from exact recorded preimages;
- recording explicitly accepted independent committed overlap for one exact owner, commit, and complete path set.

Never infer agent provenance from byte equality, reset or overwrite possible user work, cross a delegated submodule boundary, or broaden one attestation to later drift. After every repair set, rerun complete validation until it passes or one real ambiguity, unavailable dependency, external-state requirement, or unauthorized destructive/publication action remains.

## Library And Script Interface

Reusable lifecycle code is organized as cohesive packages by responsibility: coordination publication, task/repository model, Git access, bootstrap manifest, private state and transactions, worktree/submodule preparation, validation/repair, and workflow sequencing. A facade owns only dependency wiring and phase order. No single module or class may accumulate these independent owners.

The thin skill-local executable owns argument parsing, output, and exit status only. Its public operations are:

```text
worktree.py prepare --goals-repository <project-goals-main-root> --common-prefix <prefix> [--specification-input <ordinary-file>] [--repository <main-root>]... [--participating-submodule <main-root> <recursive-root-relative-path>]...
worktree.py revise --goals-repository <project-goals-main-root> --common-prefix <prefix>
worktree.py contracts-authored --goals-repository <project-goals-main-root> --common-prefix <prefix> [--goals-owner-input <root-relative-path> <ordinary-file>]...
worktree.py recover-main-leak --goals-repository <project-goals-main-root> --common-prefix <prefix> --main-repository <owner-root> --path <owner-relative-path> [--path <owner-relative-path>]...
worktree.py accept-main-commit-drift --goals-repository <project-goals-main-root> --common-prefix <prefix> --main-repository <owner-root> --commit <full-current-commit> --path <owner-relative-overlap-path> [--path <owner-relative-overlap-path>]...
worktree.py seal --goals-repository <project-goals-main-root> --common-prefix <prefix> [--goal-input <ordinary-file>]
worktree.py activate --goals-repository <project-goals-main-root> --common-prefix <prefix>
worktree.py validate --goals-repository <project-goals-main-root> --common-prefix <prefix> --required-state <state>
```

`prepare` reads an approved specification from `--specification-input` when the central specification is new or changed, publishes those exact bytes directly to `project-goals/main`, then creates or resumes the complete implementation set. `contracts-authored` may receive the complete approved `project-goals` stable-owner delta as repeated `--goals-owner-input` pairs, publishes only those exact normalized paths, validates the complete contract set, and records no semantic approval. `seal` reads a new or changed goal from `--goal-input`, creates the closed initial checkpoint when absent, validates, commits, and compare-and-swap publishes only the exact final coordination task-directory delta before binding its commit and hashes. Each input is an ordinary single-link UTF-8 file outside every participating repository tree; the provider reads it without modifying or retaining it. Omit an input only when the corresponding published file already exists and is unchanged. `activate` runs only after persistent-goal creation. `revise` is the only path from a sealed inactive candidate back to editing. None of these operations checkpoints, merges, or deletes implementation state.

Successful commands emit one closed machine-readable JSON object with common prefix, lifecycle, coordination repository/path/commit, exact task roots, task-owned submodule roots, performed repairs, and skipped optional resources. Diagnostics use standard error. Paths are explicit and canonicalized; implementation contains no workspace scan, project allowlist, cloud profile, or application resource name.

## Publication Handoff

Activation may proceed only after `seal`, known persistent-goal availability, and fresh sealed validation. The persistent objective names `project-goals/<common-prefix>/goal.md` and binds work to the returned task roots.

Later implementation-source commits and pushes belong to `agent-workflows:git-commit`. A user-approved closing snapshot belongs to `goal-checkpoint`; one-checkpoint merge and primary acceptance belong to `goal-merge`; resource/worktree/ref/task-directory deletion belongs to `goal-delete`. None is implied by sealing, activation, completion, or abandonment.

## Verification Contract

Executable tests use temporary real Git repositories and cover:

- arbitrary valid prefixes, central task-directory identity, absence of a `project-goals` task branch/worktree/bootstrap manifest, and every implementation path/ref collision;
- clean canonical-main preconditions, workspace-global write serialization, compare-and-swap candidate publication, unrelated concurrent coordination updates, same-task conflict rejection, clean synchronized return, and interruption recovery before/after commit and push;
- exact approved stable-owner path publication, path normalization and overlap rejection without broad coordination-tree staging;
- complete participant discovery input, extension only after revision, and no workspace inference;
- required/optional copy and link resources, independence, exact mode/bytes/links, absent optional paths, special files, hardlinks, escapes, overlaps, and submodule boundaries;
- strict current YAML schema, `.yaml` naming, duplicate/tag/anchor/alias/merge/unknown-key rejection, exact types, and absence of TOML/`.yml` compatibility;
- cleanup-declaration validation and seal-time binding without execution;
- recursive read-only and task-owned submodules, nested ownership, exact gitlinks, dirty/unavailable handling, and ignored-file collision safety;
- main/user-state preservation, overlap classification, caller-provenance leak recovery, exact committed-overlap attestation, and later-drift rejection;
- wrong-root rerouting and deterministic worktree, state, manifest, resource, and submodule repair;
- crash recovery for pending worktree, resource, coordination publication, private write, fingerprint migration, and repair transactions;
- sealed immutability, ordinary inactive `revise`, preserved task content, participant expansion, repeated authoring/review/seal, and active-state rejection;
- explicit `contracts_authored`, rejection of direct seal from `repository_prepared` or `goal_ready`, explicit activation, idempotence, and fresh full validation after every repair;
- absence of checkpoint, merge, cleanup, Product publication, project-specific cloud logic, or broad filesystem discovery in `goal-brainstorm`.

Tests assert observable Git, filesystem, closed output, and exit behavior. They do not assert private call order or substitute mocked Git interactions for required repository behavior. Stable prose is verified semantically, not through string-presence tests.
