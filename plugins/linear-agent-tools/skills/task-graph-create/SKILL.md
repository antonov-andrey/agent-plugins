---
name: task-graph-create
description: Decompose any agreed source revision into one bounded Linear Project issue DAG, preview it, publish it idempotently through the Planned activation barrier, or apply an approved Linear-native delta to an active Project.
---

# Create Linear Task Graph

Convert one explicit source into one Linear Project. Issues and blocker relations are the canonical graph; do not write `task-graph.yaml`, a project-goals task graph or a private execution journal.

Every graph preview and handoff MUST repeat that ownership boundary explicitly: the Linear issues and blocker relations are canonical, and no `task-graph.yaml`, per-task goal artifact or parallel operational graph is created.

Read `../../references/manual-workflow.md` and `../../lib/task_graph/issue-contract.md` completely.

## Preflight

1. Open a fresh thread. Resolve the exact source kind, published revision, canonical URL/paths, outcome and complete content. For `project-goals`, use one commit-pinned canonical URL for the exact source directory; its only source artifacts are the `goal.md` and `spec.md` children at that same commit. The source remains revisable before successful handoff; the selected Git revision becomes immutable provenance only after activation.
2. Resolve the exact Linear workspace/team and prove `workflow-configure` is current. Verify GitHub integration before adding any code-mutating node.
3. Inspect every intended repository's current instructions, canonical origin and `worktree-bootstrap.yaml`. A missing current owner, stale goal-brainstorm worktree rule or legacy TOML manifest becomes a blocking instruction-adoption implementation issue; do not add a compatibility parser or dispatch dependent Product work first.
4. Separate unknown external properties into evidence-only `task:implementation` probes that block bulk implementation.

## Decomposition

- Prefer one independently verifiable owner slice and one repository/PR per implementation issue. Use a cross-repository issue only for a truly indivisible outcome and include ordered merge and partial-recovery contracts.
- Use only allowed role/delivery pairs. Create explicit implementation/evidence, post-merge review, acceptance, human decision when needed, and exactly one final cleanup node.
- Review depends on implementation. Acceptance depends on review. Final cleanup depends on every acceptance task. All resource identities, lifetimes and direct-argv cleanup bindings are explicit.
- Every node contains the complete shared issue template with compact relevant source sections, not the full source repeated in every issue.

Create a transient JSON graph input, run `scripts/graph.py validate` and `render`, then show the complete Project, nodes, roles, repositories, blockers, resource lifetimes and verification plan to the user. Obtain approval before any Linear mutation.

## Activation-Barrier Publication

Use official Linear MCP host tools for external operations. After every mutation phase, fully paginate and write a transient strict Project snapshot, then run `scripts/graph.py reconcile` to obtain only the next safe phase:

Action payloads represent the unselected `assignee_id` or `delegate_id` as `""`. Omit that empty field when calling the official MCP operation; never translate it into persisted `null` or clear an unrelated field outside the exact staged issue.

1. create or accept the exact Project in `Planned`;
2. create/update its visible import-plan document;
3. create exact issues in `Backlog` without `agent:codex`;
4. add exact blocker relations;
5. add role/dispatch labels, set exactly one assignee or delegate, and move complete nodes to `Todo` while Project remains `Planned`;
6. reread and compare the complete graph;
7. perform the single Project `Planned -> In Progress` activation mutation;
8. immediately fully reread the Project and run `scripts/graph.py activation-confirm`; do not report handoff until this exact post-transition proof succeeds.

Retry reconciles exact source keys and never overwrites unknown foreign objects. A partial import remains non-dispatchable. Terminal Projects are never reopened. After successful activation, source changes belong to Linear; delete transient graph/snapshot inputs.

Project lookup is exact: fully paginate the selected team's Projects and match the complete provider-owned description containing the exact team/source key. Never adopt by display name. Fully paginate Project documents and represent each with exact ID, title and content in the strict snapshot. Create a missing import document; update one stale provider-owned document through its exact ID; stop on duplicate or foreign same-title documents. Every snapshot includes the Project's actual `team_id`.

An approved delta to an active Project is Linear-native. Capture its exact Linear decision, finding, or manual provenance in a transient delta input. Run `scripts/graph.py delta-validate` and `delta-render`, show the complete delta, and obtain approval. Then repeatedly fully reread the Project and run `delta-reconcile` in this exact barrier order: validate every existing target role, status and reverification declaration without returning any mutation action; create or accept the immutable visible delta transaction document; create new nodes in `Backlog` without dispatch label; establish relations and downstream review/acceptance/cleanup blockers; return each explicitly declared currently-running review or acceptance node to blocked `Todo`; only then add metadata/labels while new nodes remain inactive; reread; and finally use the `Todo` transition as each new node's activation mutation. `reverification_node_key_list` may name only an existing review or acceptance node that receives a new blocker; it accepts an already-`Todo` retry, permits only the proved `In Progress -> Todo` transition, and never reopens a terminal node. A new blocker may target an existing implementation only when that implementation is already in `Rework` and absent from `reverification_node_key_list`. Keep the implementation in `Rework` and continue reconciliation without a status mutation. Reject every other implementation status and every implementation reverification declaration before relation planning. The document preserves the complete approved recovery envelope; issues and relations remain the only operational DAG. Never reimport later source-main content, overwrite unrelated nodes, weaken mandatory downstream gates, reopen terminal nodes, or mutate a completed/canceled Project.

When an active-Project delta originates from a semantic-review finding, use `linear-agent-tools:task-review` for the independent finding and no-fix boundary, and this skill for the approved graph mutation. Neither owner substitutes for the other.

Do not generalize reverification to other running review or acceptance issues: return only the exact nodes named in the approved `reverification_node_key_list`. In a review-finding workflow, that means the explicitly selected currently-running review, not an unrelated acceptance issue. Every delta preview or handoff response MUST identify that exact selected currently-running review or acceptance issue, state that no other review or acceptance issue changes state, and state that terminal nodes are never reopened. It MUST name the `Backlog -> relations/gates -> selected reverification node back to blocked Todo -> metadata/label -> final Todo` activation order and state that the original source is not reimported.

Explicit cancellation uses `scripts/graph.py cancel-plan --human-decision`. Apply only its next returned phase and reread. It first moves an exact `Planned` or `In Progress` Project to `Canceled`, stopping all dispatch, and only then moves every unfinished Project issue to `Canceled`. A partial draft needs no import document to be cancelable. Completed Projects never reopen; a fully canceled retry is a no-op and preserves Linear history.
