# Goal Brainstorm Worktree Contract

## Table Of Contents

- [Ownership And Boundary](#ownership-and-boundary)
- [Task Identity](#task-identity)
- [Repository Set](#repository-set)
- [Bootstrap Manifest](#bootstrap-manifest)
- [Resource Classes](#resource-classes)
- [Specification Link](#specification-link)
- [Preparation Lifecycle](#preparation-lifecycle)
- [Submodules](#submodules)
- [Private State](#private-state)
- [Isolation Validation](#isolation-validation)
- [Diagnosis And Repair](#diagnosis-and-repair)
- [Library And Script Interface](#library-and-script-interface)
- [Publication Handoff](#publication-handoff)
- [Verification Contract](#verification-contract)

## Ownership And Boundary

This reference is the canonical reusable owner of task-worktree identity, preparation, bootstrap resources, submodule handling, isolation validation, and deterministic repair for `agent-workflows:goal-brainstorm`.

The applicable project `AGENTS.md` remains the owner of concrete project paths. Its `Key Directory Map` must bind `.spec/`, `.worktree/`, and root `worktree-bootstrap.toml` to `agent-workflows:goal-brainstorm` without copying this contract. The root `worktree-bootstrap.toml` owns only that repository boundary's concrete bootstrap resource paths.

The workflow prepares repository state. It does not authorize commit, push, merge, worktree removal, destructive recovery, or publication.

## Task Identity

One common prefix identifies the complete task:

```text
.spec/<common-prefix>-spec.md
.spec/<common-prefix>-goal.md
.worktree/<common-prefix>/
refs/heads/<common-prefix>
```

The workflow derives the prefix from the specification filename and uses it unchanged for the paired goal filename, branch name, and linked-worktree basename. It must be one filesystem basename, pass `git check-ref-format --branch` unchanged, and contain no path separator.

The project-local worktree container is singular `.worktree/`. Git's internal `.git/worktrees/` administration directory is not a project-local alternative.

Before preparation, inspect the specification pair, local branch refs, registered worktrees, target filesystem paths, and private workflow state. Reuse is allowed only for the same inactive task with matching recorded identity.

When private state is absent after an interrupted or tool-less bootstrap, the workflow may adopt an existing worktree only when its common prefix, branch, canonical path, baseline relation, specification link, participating repository set, and observable main state match one unambiguous inactive task. It reconstructs private state before tracked work continues. Any unrelated or underdetermined collision must not be overwritten.

Worktree creation records a durable pending marker below the repository's Git administration path before it creates the task branch or filesystem checkout. Resumption accepts only the marker's exact baseline, exact registered branch and path, an unchanged baseline index, and working-tree differences attributable solely to already proven provider bootstrap outputs or clean repairable submodule drift. A partial checkout, staged change, redirected `.git` pointer, or ordinary directory substituted for a registered worktree is never an adoption candidate.

## Repository Set

One coordinating repository owns the physical specification pair. The workflow determines that repository and every other affected top-level repository from approved project contracts and the current task scope. It must not scan a broader workspace and infer participating projects from proximity, names, or dirty state.

Each participating top-level repository starts from the selected committed `main` baseline and receives the same task branch name and worktree basename. Pre-existing main-worktree index, tracked modifications, untracked paths, and submodule state are user state and must be recorded before preparation.

Recursive submodules are repository boundaries discovered from recorded gitlinks, not additional top-level worktrees. Each initialized submodule applies its own instructions. A submodule becomes a participating manifest boundary only when the task changes its repository content or uses its local bootstrap resources.

`prepare` may extend the top-level repository set or task-owned submodule set only while lifecycle state is `repository_prepared`. The complete set is immutable after `contracts_authored` is recorded. A later discovery that genuinely changes either set requires returning to design and preparing a new task identity; it must not bypass stable-owner review by extending the recorded task in place.

## Bootstrap Manifest

Every participating top-level repository and every task-owned submodule boundary must have one tracked root `worktree-bootstrap.toml`:

```toml
schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = []
link_optional_path_list = []
link_required_path_list = []
```

The schema is closed. Unknown keys, unknown tables, and unsupported versions are invalid.

Every list entry must be an exact POSIX path relative to the owning repository root. Reject:

- empty paths, absolute paths, globs, and platform-specific separators;
- `.` or `..` path segments;
- `.git`, `.spec`, `.worktree`, the manifest itself, and descendants of those paths;
- duplicates across or within lists;
- parent-child overlaps across or within lists;
- a path that crosses into a submodule boundary.

The provider must not infer a resource class from a filename, directory name, project name, or file content. A missing manifest is an adoption state that the workflow repairs before implementation by creating the empty versioned manifest in that task checkout. If implementation needs an unclassified local resource, its owner must classify it before that dependency is used.

After the first merged adoption, a missing manifest is a project-contract violation rather than an optional configuration.

## Resource Classes

A copy resource is an isolated snapshot from the recorded main worktree into the task checkout. Task writes may change, rename, or delete only the copy; after its initial materialization is durably recorded, an absent destination is task state and is not recreated by validation. Copy preserves ordinary files, directories, executable bits, and symbolic links whose resolved targets stay inside the copied source tree. It rejects sockets, devices, FIFOs, external symbolic links, hardlinked regular files, and any source that changes during materialization.

A link resource is an explicitly shared, read-only dependency. The task checkout receives a relative symbolic link to the main-worktree source. The workflow records the source identity and content fingerprint and treats any task-time source change as isolation drift. Any resource that implementation may mutate must be classified as copy, never link.

A required resource must exist and satisfy its class contract. An absent optional resource is reported in preparation output and skipped without creating a destination.

Before materialization, the destination must be absent or be a previously recorded provider-created object with no independent changes. Before sealing, tracked project ignore rules must ignore the destination as its actual object type. During first adoption, an exact recorded local exclude may cover a provider-created destination only until the task worktree authors and verifies the durable tracked rule. The workflow must test the real file, directory, or symbolic link; checking only a hypothetical child path is insufficient. After materialization, both copy and link source fingerprints remain protected task inputs. Main drift at, above, or below a materialized resource boundary is not independent user work; skipped absent resources are not materialized boundaries and retain their separate absent-state check.

The source and destination are canonicalized against their owning roots before filesystem mutation. Resource preparation must not overwrite tracked files, cross repository boundaries, or follow a destination link during replacement. Every protected regular file, including a resource source, transaction object, private preimage, manifest, specification, or goal, must have exactly one filesystem link; a hardlink makes mutation provenance and alias isolation ambiguous and is rejected without changing either name.

## Specification Link

`Artifact Directory` in `plugins/agent-workflows/skills/goal-brainstorm/references/specification-contract.md` owns physical task-artifact placement. Each participating top-level task worktree exposes that coordinating directory as a relative `.spec` symbolic link. The link target is computed from canonical roots; no workspace path is embedded in provider code or project artifacts.

Tracked ignore behavior must ignore both the physical directory and the symbolic link before sealing. During first adoption, an exact recorded local exclude may protect the symbolic link until the task worktree authors and verifies the durable tracked rule. Equivalent ignore rules are allowed, but validation must exercise both real object forms. Preparation validates or creates the initial manifest, derives required ignore paths, authors and verifies task-root tracked ignore behavior, and only then exposes the `.spec` link or materializes a resource. A later preparation failure must therefore never leave an unignored link or provider-created resource.

The `.spec` link is reserved workflow behavior and must not appear in `worktree-bootstrap.toml`. Creating and updating the approved specification and goal through this link is the only task-artifact write intentionally directed to the coordinating main worktree. After sealing, both artifacts are immutable inputs until the inactive goal is explicitly revised and sealed again through the goal lifecycle.

## Preparation Lifecycle

The workflow uses these states:

1. `designing`: inspect owners, goal state, participating repositories, and current repository state without tracked task writes.
2. `design_approved`: write the approved specification to the coordinating main worktree's physical `.spec/`.
3. `worktree_created`: create each task branch and linked worktree from its recorded committed baseline.
4. `repository_prepared`: initialize recursive submodules, validate or create participating manifests, author minimum durable ignore rules, install any exact temporary local excludes needed for first adoption, materialize resources, and create specification links.
5. `contracts_authored`: write approved tracked owner changes only inside prepared task roots, pass full worktree validation, and explicitly record the transition before semantic review.
6. `goal_ready`: complete semantic review, create the paired goal through the specification link, pass full validation, seal the task artifacts, and show the resulting contract set.
7. `active`: create the persistent goal and bind all implementation work to the recorded task roots.

The workflow may add exact provider-owned patterns to a participating repository's local Git exclude when committed ignore rules do not yet cover the `.worktree/` container, the `.spec` link, or a declared bootstrap destination. It must add only the minimum root-relative patterns required for real objects and record every entry it added. During preparation, each task branch adds and verifies minimum durable tracked ignore rules for those same objects. Each temporary local entry is removed only after the corresponding durable rule is present in merged `main`.

Creating a missing initial manifest and authoring its minimum durable ignore rules are the only tracked preparation writes allowed before all required repository boundaries reach `repository_prepared`. No other tracked contract authoring or implementation may begin before that state.

## Submodules

For every recursive submodule, run the semantic equivalent of these commands against the exact task root:

```text
git submodule sync --recursive
git submodule update --init --recursive --checkout
```

This synchronizes configured URLs and initializes each checkout at the exact gitlink recorded by its parent task worktree. Verify recursive status and every effective commit after Git reports success. Each recursive root must also be the exact physical, non-symbolic filesystem path recorded below its parent task root; resolving both an expected path and a redirected path to the same external checkout is not an identity check and must fail before any recursive Git command crosses that boundary.

Each initialized submodule is its own instruction boundary. A parent manifest must not classify any path inside it.

A submodule that stays read-only at its recorded gitlink does not receive a manifest change only because initialization occurred. When the task changes submodule content or needs local bootstrap resources inside it, that submodule becomes participating. The workflow creates or validates its root manifest before the first such use.

Every participating submodule is declared to `prepare` as its top-level participating main root plus its exact recursive root-relative path. If a nested submodule participates, every submodule ancestor participates too, because the ancestor owns the dirty descendant gitlink. The workflow records a baseline commit, manifest fingerprint, resource state, nested main commit, nested main status fingerprints, recoverable preimages, and leak provenance for each declared boundary. Subsequent preparation supplies the complete same declaration set. Top-level Git status collapses descendant edits to a gitlink path, so isolation and `recover-main-leak` attribute a path below a participating boundary to that submodule's own main/task roots instead of treating the gitlink as the file owner.

Read-only submodules retain exact recorded index gitlinks and effective commits. A participating submodule may advance its effective commit and parent index gitlink only to descendants of its recorded baseline; its dirty implementation state is preserved and validated rather than reset. An uninitialized submodule, stale URL configuration, or clean read-only checkout at the wrong commit has a deterministic repair: synchronize recursively and update to the recorded gitlink. An undeclared dirty checkout must first be inspected for its branch, tracked diff, untracked paths, nested submodule state, and relation to recorded task work.

Every Git comparison and status query explicitly disables repository-level submodule ignore suppression. Submodule paths are transported as literal paths, including names that resemble pathspec magic. Before a clean checkout repair, validation compares ignored untracked objects with the target gitlink tree and blocks rather than overwriting any collision.

Never reset a dirty submodule automatically. Never move a linked worktree that contains initialized submodules.

## Private State

Store workflow state below the per-worktree Git administration path resolved through `git rev-parse --git-path`. Do not store harness state, locks, caches, or recovery snapshots in `.spec/` or another tracked project path.

Schema-v2 private state records:

- the common prefix, current lifecycle state, and coordinating repository;
- canonical main and task roots for each top-level repository;
- each Git common directory, baseline commit, branch, and worktree registration;
- main index and working-state fingerprints plus sufficient private preimages to preserve pre-existing dirty paths at top-level and participating-submodule boundaries;
- recursive gitlinks plus each explicitly task-owned submodule baseline, manifest, resources, physical roots, nested main state, and intended descendant state;
- exact caller attestations that accept one full current main commit and its complete reported overlapping path set without changing either checkout;
- caller-recorded provenance for an exact agent-created main leak while recovery is in progress;
- manifest hashes, classified resources, materialization results, and source fingerprints;
- specification and goal hashes after sealing;
- exact temporary local excludes added by the workflow.

Private state must not be printed with file content or secrets. Filesystem path text is transported with the platform filesystem encoding and surrogate escape so a non-UTF-8 name or link target is preserved rather than rejected or rewritten. Tree fingerprints are type-tagged and length-delimited, include modes, file bytes, directory structure, and raw link targets, and cannot collide merely by redistributing bytes between adjacent fields. An interrupted write uses atomic replacement so the last complete state remains recoverable.

Filesystem mutations use durable provider transactions. Resource transactions preserve the previous destination and stage the exact copy, link, or removal before exposure. Main-leak transactions stage the complete target object and record both working-object and index preimages before touching main. Ordinary project text writes record the previous and target fingerprints before atomic replacement. Private file writes and clone-based main/resource preimage or copy-fingerprint-migration operations create an unpredictable staging identity only after atomically exposing a matching intent marker; process death can therefore discard partial provider-owned stages without treating a predictable filename as provenance. Resource source preimages carry closed path/fingerprint metadata, their owner directory is close-scanned, and an exact obsolete snapshot is retired only after every state replica durably drops that resource. A later command completes or rolls back only when the observed object matches one recorded phase; it reports that repair once. Proven metadata-less pre-marker staging is removed and reported, while unknown files, changed fingerprints, symlinked parents, escaped ownership boundaries, and metadata-less obsolete legacy snapshots remain blockers.

Pending worktree ownership, completed mutation transactions, and obsolete preimages are retired only after the complete schema-v2 state has been written to every participating replica. A crash at any earlier point therefore resumes from durable ownership rather than inferring provenance from current bytes.

A valid schema-v1 replica is migrated deterministically to schema v2 on first access. Before fallback migration, the workflow searches every repository discoverable from that legacy state for a valid schema-v2 secondary replica and treats one agreeing v2 set as authoritative. Migration adds empty task-owned-submodule and main-leak-provenance state, writes complete schema-v2 replicas atomically, and leaves task content unchanged. After all v2 replicas are written, every obsolete v1 replica is atomically retired so loss of one v2 replica cannot reactivate stale lifecycle, repository, submodule, or recovery state.

## Isolation Validation

Before and after every tracked authoring, implementation, verification, or Git phase, resolve `git rev-parse --show-toplevel` and compare it with the recorded task root. Run repository commands with an explicit recorded working directory.

Complete validation confirms:

- the common prefix, branch ref, worktree registration, canonical task root, and baseline relation;
- the physical linked-worktree identity, including its exact Git common directory, registered administration record, branch, HEAD, and ordinary `.git` pointer;
- the absence of task-owned tracked changes in every main worktree;
- preservation of every recorded unrelated main-worktree change;
- the physical coordinating `.spec/` directory and every relative task-worktree link;
- sealed specification and goal hashes after sealing and while active;
- recursive submodule initialization, physical non-symbolic roots, strict read-only gitlinks, and baseline-descendant task-owned submodule state with independent nested-main isolation;
- manifest schema, manifest hash, resource object type, link target, copy independence, ignore behavior, and source fingerprint;
- consistency between private state and observable Git and filesystem state.

Main may receive independent user work while the task is active. A new main commit or dirty path that is provably outside task provenance and does not overlap the task diff or a materialized prepared resource is preserved, classified as unrelated, and added to the recorded fingerprints without stopping the task. Overlap means equality, ancestor, or descendant, not only one identical Git path. Copy-source fingerprint drift catches ignored filesystem changes that Git status cannot expose. An overlapping change or unclear provenance remains ambiguous and must not be reverted.

Starting from the wrong directory is not itself a blocker. When task identity is unambiguous, rerun the operation against the recorded task root and report the reroute.

## Diagnosis And Repair

A validation failure starts diagnosis. Identify the changed object, its repository owner, recorded state, current state, likely producing operation, and smallest correction that preserves user work.

Apply deterministic repairs without asking the user, including:

- rerouting an operation from the wrong root;
- restoring a missing or redirected linked-worktree `.git` pointer and running `git worktree repair` only when registered administration state, complete private ownership, exact manifest, specification link, branch, HEAD, and common Git directory prove one identity;
- recreating a missing or incorrect `.spec` link when its path contains no independent content;
- restoring a provider-created local exclude or incomplete private-state write;
- reconstructing absent private state from one fully matching, unambiguous inactive task worktree;
- synchronizing and initializing missing recursive submodules;
- returning a clean drifted submodule to its recorded gitlink;
- completing or rolling back a pending initial copy transaction before its resource state is durable; a later task deletion or rename of a committed copy is preserved;
- creating a missing initial manifest before implementation;
- recording a provably independent, non-overlapping main commit or dirty-state change as unrelated user work;
- after one explicit user decision, recording the exact current commit and complete overlap path set as accepted independent committed main drift without changing main, the task branch, or task content;
- recording caller-known agent provenance for exact leaked paths, then restoring their exact recorded main preimages only while current main and task fingerprints still match that record.

Matching main and task bytes alone never prove that the main change belongs to the agent. Ordinary validation must preserve overlapping committed or dirty main state and report ambiguity. The agent calls `recover-main-leak` without asking the user only for dirty paths it knows it wrote during the current task; the command verifies task ownership and byte identity, records provenance in private state, restores the exact preimage, and repeats complete validation. It rejects inferred, stale, differing, or unrecorded paths.

When the user explicitly confirms that overlapping committed main history is independent work to preserve, the agent calls `accept-main-commit-drift` with the exact full current commit and every exact overlap path reported for that repository owner. The owner root is either one participating top-level main worktree or one explicitly task-owned submodule main root; each path is relative to that exact owner, and a parent owner cannot attest across a delegated submodule boundary. The command rejects a changed commit, a missing or additional path, a nonparticipating owner, nonlinear history, or a path not present in that owner's committed history. If an accepted commit made a previously recorded dirty main object clean, validation reconciles that transition only when the committed object exactly matches the captured working preimage; a changed object or a new dirty layer remains ambiguous. The durable attestation applies only to the named net path at that accepted commit; another path and a later committed change to the same path remain unaccepted. The command never resets, rebases, checks out, stages, commits, or changes either working tree. Ordinary `validate` never infers or creates this attestation.

After a repair set, rerun complete validation from current state. Continue the diagnose, repair, and validate cycle until it passes or one genuine ambiguity or external blocker remains.

Ask the user only when a correction would overwrite or reinterpret possible user work, select among multiple valid owner contracts, classify a resource whose write semantics are unknown, require unavailable external state, or perform an unauthorized destructive or publication action.

## Library And Script Interface

The reusable implementation module is `plugins/agent-workflows/lib/goal-brainstorm/worktree.py`. It is a library module and must not parse process arguments, print command output, or expose a direct-execution guard.

The thin skill-local executable is `plugins/agent-workflows/skills/goal-brainstorm/scripts/worktree.py`. It owns only CLI parsing, process output, and exit status. It delegates repository behavior to the library module.

Owner-local library and script behavior tests live under `plugins/agent-workflows/lib/goal-brainstorm/test/`. The plugin exposes no `plugins/agent-workflows/lib/goal-brainstorm/tool/` path.

The script exposes:

```text
plugins/agent-workflows/skills/goal-brainstorm/scripts/worktree.py prepare --coordinating-repository <main-root> --specification <root-relative-spec-path> [--repository <main-root>]... [--participating-submodule <main-root> <recursive-root-relative-path>]...
plugins/agent-workflows/skills/goal-brainstorm/scripts/worktree.py contracts-authored --coordinating-repository <main-root> --specification <root-relative-spec-path>
plugins/agent-workflows/skills/goal-brainstorm/scripts/worktree.py recover-main-leak --coordinating-repository <main-root> --specification <root-relative-spec-path> --main-repository <main-root> --path <root-relative-path> [--path <root-relative-path>]...
plugins/agent-workflows/skills/goal-brainstorm/scripts/worktree.py accept-main-commit-drift --coordinating-repository <main-root> --specification <root-relative-spec-path> --main-repository <participating-main-owner-root> --commit <full-current-commit> --path <owner-relative-overlap-path> [--path <owner-relative-overlap-path>]...
plugins/agent-workflows/skills/goal-brainstorm/scripts/worktree.py activate --coordinating-repository <main-root> --specification <root-relative-spec-path>
plugins/agent-workflows/skills/goal-brainstorm/scripts/worktree.py validate --coordinating-repository <main-root> --specification <root-relative-spec-path> --required-state <state>
plugins/agent-workflows/skills/goal-brainstorm/scripts/worktree.py seal --coordinating-repository <main-root> --specification <root-relative-spec-path> --goal <root-relative-goal-path>
```

`prepare` includes the coordinating repository automatically; repeated `--repository` values add affected top-level repositories. Repeated `--participating-submodule` pairs declare the complete task-owned recursive-submodule set by participating main root. It derives task identity, creates or resumes matching worktrees, prepares every repository boundary, and reaches `repository_prepared`.

`contracts-authored` is called after approved stable-owner changes are complete and before semantic review begins. It validates the complete repository set, explicitly advances `repository_prepared` to `contracts_authored`, and is idempotent in `contracts_authored`. It does not perform or infer semantic approval, create the paired goal, seal artifacts, or accept a later lifecycle state.

`validate` loads recorded identity, diagnoses drift, performs every safe deterministic repair, repeats complete validation, and succeeds only at or beyond the requested lifecycle state.

`recover-main-leak` is an explicit caller-provenance operation, not a general validation heuristic. The calling agent lists only exact participating-main paths it knows it wrote. The command verifies that each path is one current task path with identical current content before it records recovery ownership and invokes complete validation.

`accept-main-commit-drift` is an explicit caller-attestation operation, not a general validation heuristic. It records only an exact full current commit and the complete exact committed overlap set confirmed by the user at one participating top-level or task-owned-submodule owner boundary, persists that decision before complete validation, and is idempotent for the same commit and path set. It preserves both main and task content and does not authorize later commit drift.

`seal` requires recorded `contracts_authored` and a semantically approved goal-ready pair, validates the complete repository set, records task-artifact hashes, leaves the workflow in sealed `goal_ready`, and returns the exact task roots that the persistent objective must bind. It rejects a direct transition from `repository_prepared`. Calling `seal` again from inactive `goal_ready` is the explicit reseal operation for an intentionally revised pair after the workflow has inspected persistent goal state; ordinary `prepare` and `validate` continue to reject sealed artifact drift.

`activate` is called only after the harness has successfully created the persistent goal. It revalidates sealed state, records `active` in every private-state replica, is idempotent for the same active task, and never creates a harness-specific goal itself. Active task artifacts remain sealed, and resealing is rejected until the persistent goal is no longer active.

Successful commands emit one machine-readable JSON object to standard output containing the task prefix, lifecycle state, exact top-level task roots, task-owned submodule roots, performed repairs, and skipped optional resources. Human diagnostics go to standard error. A nonzero result identifies one remaining ambiguity, invalid contract, unavailable dependency, or external blocker and does not claim preparation success.

The library and script accept explicit paths only and canonicalize them before use. They contain no project-name, workspace-root, profile, environment, or concrete resource-path allowlist. They never commit, push, merge, or remove worktrees.

## Publication Handoff

Goal activation may proceed only after `seal` succeeds, persistent goal state is known to allow creation, and a fresh validation confirms the sealed hashes and repository state. The persistent objective names the paired goal and binds all implementation and verification to the returned exact task roots.

A later explicit publication request uses `agent-workflows:git-commit`. Before merge, full validation must pass and main tracked state must still match its recorded baseline plus unrelated user changes. After the task branch is merged into `main`, remove only provider-added temporary local excludes whose durable replacement is now effective. Worktree removal remains a separately authorized cleanup action.

The physical task pair remains retained in the coordinating main worktree for every goal state, including after merge, completion, abandonment, or worktree cleanup.

## Verification Contract

Executable behavior tests use temporary real Git repositories and cover:

- arbitrary valid task prefixes and every identity collision;
- physical specification ownership, relative links, and ignore behavior for directory and link objects;
- required and optional copy and link resources with arbitrary names;
- copy independence, committed-copy deletion and rename, read-only link drift, safe internal links, hardlink rejection, and rejected special or escaping objects;
- ignored and committed main drift at, above, or below materialized copy and link resource boundaries;
- closed manifest parsing, path canonicalization, duplicates, overlaps, reserved paths, and submodule boundaries;
- recursive and nested submodules at exact gitlinks and exact physical non-symbolic roots;
- missing, clean-drifted, dirty, unavailable, read-only, explicitly task-owned, and nested task-owned submodule states with independent nested-main isolation and leak recovery;
- pre-existing and later independent main-worktree preservation, overlapping drift classification, and absence of task changes in main;
- exact caller-attested committed-overlap acceptance at top-level and task-owned-submodule owner boundaries, exact recorded dirty-to-committed reconciliation, mismatched-preimage rejection, idempotence, complete path-set binding, delegated-boundary isolation, later-drift rejection, and preservation of both checkouts;
- explicit agent-provenance recovery of exact main leaks and rejection of byte-equality inference;
- wrong-root rerouting and deterministic worktree, link, state, manifest, copy, and submodule repairs;
- ambiguous user changes that must not be overwritten;
- interruption and partial-bootstrap recovery;
- durable pending-worktree, resource, main-leak, and ordinary-text transaction resumption plus random private-write, preimage, and copy-fingerprint-migration recovery with one-time repair reporting;
- rejected incomplete pending checkouts, substituted registered paths, redirected Git pointers, and staged baseline drift before bootstrap writes;
- literal pathspec-like submodule names, repository-level `ignore = all`, and ignored-untracked checkout collisions;
- sealed task-artifact immutability under ordinary preparation and validation, plus explicit inactive resealing;
- explicit and idempotent `contracts_authored` recording after stable-owner authoring and before semantic review, rejection of direct sealing from `repository_prepared`, and rejection of repository or task-owned-submodule set expansion after that transition;
- explicit activation only from sealed state, idempotent active recording, and active task-artifact immutability;
- deterministic schema-v1 to schema-v2 private-state and collision-safe fingerprint migration, v2-secondary precedence, and retirement of every legacy replica;
- complete revalidation after every repair.

Tests assert observable Git, filesystem, JSON output, and exit behavior. They do not assert private call order or substitute mocked Git interactions for required repository behavior.
