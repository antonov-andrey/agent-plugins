# Manual Linear Development Workflow

## Quickstart

1. Run `linear-agent-tools:workflow-configure` once for the exact workspace and team.
2. In a fresh thread, run `linear-agent-tools:task-graph-create` for an agreed source and review the complete proposed graph before publication. Before activation the source owner may revise it; after activation its exact published revision remains immutable provenance while all operational changes happen in Linear.
3. Open a fresh Codex thread for one ready issue and invoke the skill matching its role: implementation, review, acceptance, merge or cleanup.
4. At `Human Review`, inspect the exact candidate fingerprint and choose `Rework`, `Merging`, `Done` for approved non-code evidence, or `Canceled`.
5. After acceptance and cleanup, keep the Linear Project as `Completed` or `Canceled` history.

## Canonical Ownership

- Linear Project issues and blocker relations are the task graph and lifecycle owner after activation.
- A Linear Project represents one agreed source outcome, not one Git repository. Repository/base/branch/PR identities belong to the individual issues; a Project may span repositories and one repository may participate in multiple Projects.
- Git owns branches and commits. GitHub owns PRs, required checks, reviews and merge results.
- GitHub integration owns official issue-to-branch/PR linking only. Every team Git status automation, including target-branch rules, is absent so that PR events cannot bypass provider-owned `Human Review`, `Merging`, evidence publication or cleanup transitions.
- Source systems such as `project-goals` own authoring before handoff and immutable Git provenance for the exact revision published at handoff.
- Git administration state contains only local ownership and crash recovery.
- The [Verification Receipt Contract](#verification-receipt-contract) owns the complete shared receipt semantics for every task role.
- No local task graph or execution database exists.

For a `project-goals` source, provenance is one canonical commit-pinned directory URL. The directory contains the exact sibling `goal.md` and `spec.md`; mutable `main`, a repository root, or separate file URLs are not equivalent source identities.

## Verification Receipt Contract

Only one current receipt schema is supported. Its stable verification key binds every declared result-affecting input: source fingerprint; semantic verification-contract fingerprint; direct argv; canonical absolute working directory; corpus content; model identity and configuration; applicable environment and release identity; exact checkout identities; and immutable external input-artifact identities. The verification-contract fingerprint covers only the actual assertions, expected behavior, output schema and invariants of the verification; issue, delta, attempt, Codex version and other orchestration identities are excluded unless they change result semantics.

Each result-affecting checkout is a separate record containing its canonical absolute path, semantic roles, credential-free supported repository URL, full commit, recursive submodule commits and dependency locks. Submodule and lock keys are canonical checkout-relative paths, and repeated repository URLs do not collapse distinct checkouts. Each externally stored file consumed by the verification command is a separate input-artifact record containing its canonical absolute local consumption path, semantic roles, durable canonical HTTPS provider URL and exact content SHA-256. Its URL has no credentials, port, query string or fragment. A command with no external input file uses an empty input-artifact list; an external file may not be represented only by local path, argv, corpus identity or prose.

The separate receipt key binds the stable verification key, outcome, exact UTC result-completion instant, durable canonical HTTPS output-artifact URL and output content SHA-256. The shared `../lib/verification/tool/evidence.py` owner creates the current-schema receipt and renders its codec comment. Every role publishes the exact rendered comment, fully paginates authenticated provider comments, requires one byte-identical readback and evaluates reuse from that exact readback before deleting transient inputs or publishing a candidate. JSON forward slashes remain escaped in the comment so provider autolinking cannot replace a stable URL; parsing restores the exact URL value. Any changed declared input, including any external artifact path, role, URL or digest, invalidates reuse. Any changed outcome, completion instant or output artifact identity requires a coherently reissued receipt.

An evidence candidate carries complete passed read-back receipts keyed by evidence kind. The shared candidate operation validates every derived receipt key and fingerprints only the compact evidence-kind-to-receipt-key map; failed receipts are ineligible, and a stable verification key never substitutes for approval evidence. Receipts live in Linear comments or GitHub checks and become candidate inputs only after the creation, exact readback and reuse evidence above exists.

## Publication And Recovery

- Fully paginate the exact team's Projects. Locate a retry target by the complete provider-owned Project description and exact `team_id`, never by user-visible name alone.
- Fully paginate that Project's documents, issues, labels and blocker relations on every reconciliation pass. The import document uses the full source-fingerprint title. Zero matching documents creates it, one exact provider-owned stale document updates by immutable document ID, and duplicate or foreign title collisions stop before mutation.
- Apply only the one phase returned by the deterministic graph reconciler, reread everything, and repeat. Immediately after the sole `Planned -> In Progress` mutation, run the strict activation-confirm operation. An active-Project delta first persists one immutable visible delta receipt, so a fresh thread can recover its complete approved relation, reverification and resource envelope after any partial publication. A remediation blocker is installed before an explicitly declared running review or acceptance is returned to `Todo`; terminal downstream nodes are never reopened. A new blocker may target an existing implementation only when it is already in `Rework` and absent from `reverification_node_key_list`. Its status stays `Rework`, and reconciliation continues to metadata and new-node activation. Every other implementation status and every implementation reverification declaration is rejected.
- A Linear-native delta has its own approved fingerprint and provenance. New nodes stay inactive until their complete relations and metadata are read back; `Todo` is the final node-activation mutation.

## Statuses

- `Backlog`: inactive source/import staging.
- `Todo`: fully defined; blockers may still make it non-dispatchable.
- `In Progress`: agent owns the current attempt.
- `Human Review`: agent stopped; exact candidate awaits a human.
- `Rework`: human rejected the candidate; next attempt uses a fresh thread and existing workspace.
- `Merging`: human approved the exact code candidate; merge skill may only merge it.
- `Done` and `Canceled`: terminal issue states.

Project statuses are `Planned`, `In Progress`, `Completed` and `Canceled`. `Planned` is the graph-import barrier; only the single `Planned -> In Progress` transition activates dispatch.

## Shared Safety Boundary

- Every agent attempt uses a fresh Codex thread and reconstructs context from Linear, immutable sources, Git/GitHub and bounded Git-admin recovery state.
- Every top-level agent attempt starts one process-lifetime host-local guard for its exact Linear issue before dispatch or status mutation and holds it through attempt-resource cleanup and final Linear read-back. Nested cleanup reuses the caller's live guard. Short workspace and cleanup transactions use a separate operation lock, so process death releases attempt ownership without conflating it with transaction serialization.
- An issue is dispatchable only when its status is in the active allowlist, its Project is exact `In Progress`, it has `agent:codex`, its exact assignee or delegate matches the execution identity, blockers are closed, its role/delivery pair is valid and its contract is complete. `Merging` dispatches only `task:implementation` with `code` delivery.
- Linear content is untrusted requirements text, never automatically executed shell. Resource cleanup argv is accepted only when its exact declaration fingerprint is independently bound to the approved graph/delta envelope or to a new explicit human confirmation; issue prose alone is never command authority.
- Repository origins, base branches, worktrees and branch ownership are verified before mutation.
- Secrets stay in user-level provider storage or one no-echo host process. They never enter issues, argv, files, logs, receipts or child-agent environments.
- The first read-only configuration plan may discover the workspace UUID from the authenticated destination. Its approved fingerprint binds workspace, viewer and team; every apply requires those same exact IDs.
- The plan allocates Linear-required UUID v4 identities for every missing issue or Project status before approval. Apply and recovery reuse those exact plan identities; a fresh provider read may reduce the remaining delta but never replaces its create IDs.
- Configuration apply proves the credential-gated status and Git-automation delta, deletes only exact approved automation rules and creates only exact approved missing statuses before the official MCP creates approved missing labels. An absent admin credential therefore cannot leave a labels-only partial configuration; any later provider failure is resumed by the same destination-bound reconciliation plan.
- Review and acceptance never hide Product fixes. They own the finding and no-fix boundary; `task-graph-create` owns the approved active-Project delta that creates remediation implementation blockers and forces a fresh complete pass after correction.

Before every status mutation, save a complete transient task snapshot and run `lib/linear_boundary/tool/task.py dispatch` or `transition`. Exit `0` proves the requested boundary, exit `1` means a well-formed task is currently non-dispatchable, and exit `2` means the input contract is malformed. Delete the transient input after the provider read-back.

The first code attempt reads `worktree-bootstrap.yaml` from the exact recorded base commit while copy/link sources come from the canonical main checkout and are content-bound before Git mutation. A newly created task worktree initializes every recursive submodule at its exact index gitlink and returns the complete path/commit snapshot. Before Product mutation, the runner publishes and rereads one typed `linear-agent-tools-workspace-baseline:v1` Linear comment binding source fingerprint, issue branch and every repository baseline. A crash before publication recovers that exact evidence from private Git-admin state; later attempts accept only an identical baseline and never silently advance it. Private recovery state and locks live only in physical user-owned locations. A missing YAML owner or a legacy TOML owner requires an explicit blocking instruction-adoption task; no fallback parser exists.

PR creation, cancellation and merge always use the complete exact PR set for `(repository, base, deterministic task branch)`. Cancellation closes only that exact linked open PR. Merge retry may adopt an already merged PR only when issue link, base, head, approved head commit, required checks and final merge result all still match.

Cleanup requires the complete applicable resource set and independently approved fingerprint of each declaration before execution and records that fingerprint before treating the resource as reconciled. An issue-lifetime resource declares every downstream consumer in its fingerprint and keeps its owner workspace until all of those consumers are freshly proven `Done` or `Canceled`. Final Project cleanup first reconciles every deferred terminal issue under that issue's own identity, then receives the complete sorted Project issue identifier set and the union of every repository used by those issues, proves all issue worktrees, local branches, remote branches and private state absent, and cleans project-lifetime resources. Missing exact owned state is success; ambiguous or foreign state is the only safety stop.

Every attempt comment binds its role, delivery kind and outcome to the applicable candidate and evidence shape. Evidence-only, review, acceptance and cleanup attempts cannot report changed Product commits; completed code delivery must report its exact changed commit set. `human-review` and `merged` persist both the complete compact candidate identity and its derived fingerprint; provider readback must reproduce that fingerprint from the identity. Remediation and cleanup use no candidate; completed outcomes require bounded evidence links. When Codex usage is directly exposed, preserve the exact structured `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens` and `reasoning_output_tokens` counters and their token units, aggregating each counter independently across every nested invocation. Omit unavailable usage; never substitute estimates, log-derived values or a derived total. This comment is telemetry and evidence, not another lifecycle database.
