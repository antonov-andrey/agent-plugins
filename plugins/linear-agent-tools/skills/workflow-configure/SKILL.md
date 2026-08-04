---
name: workflow-configure
description: Configure or verify the exact Linear team issue workflow, workspace Project statuses, task-role labels, agent:codex label, authentication boundary, and GitHub integration before any task graph is published.
---

# Configure Linear Workflow

Prepare one exact Linear destination. This skill changes global team/workspace configuration only after preview and explicit approval; it never creates a task Project or mutates Product repositories.

Read `../../references/manual-workflow.md` and `../../lib/task_graph/issue-contract.md` completely.

## Procedure

1. Resolve the explicit user-level Linear MCP profile. Authenticate interactively when needed; do not add project-local MCP configuration or export its managed token.
2. Read the authenticated viewer, workspace and exact team by immutable IDs. Require active membership, `admin=true`, `guest=false`, and the exact user-approved destination. Names and email are display evidence, never substitutes for IDs.
3. Fully paginate current issue statuses, workspace Project statuses and labels. Check GitHub integration by proving that the intended repositories can create official issue-identifier branch/PR links; a manually pasted URL is not proof.
4. Reconcile exact desired configuration from the shared model. Accept existing standard statuses only when their fixed category matches. Treat duplicate names, wrong categories and foreign same-name labels as conflicts; never rename, delete or overwrite them.
5. Save the complete freshly MCP-read labels to a transient input and run `scripts/configure.py plan`. On the first read-only plan, `--workspace-id` may be omitted; the authenticated GraphQL read discovers it while still requiring the exact viewer and team IDs. Show the fingerprinted exact destination together with the global delta: missing issue statuses, Project statuses and labels plus any conflict. Obtain explicit approval for that destination-bound set and retain the exact plan output only for this transaction.
6. Before any global mutation, run `scripts/configure.py apply` with the exact destination IDs from the approved plan, the fresh unchanged label snapshot and `--approved-plan-input` pointing to the exact displayed plan. The script proves both destination identity and the complete remaining delta against that plan, reads a separate admin-capable credential with no echo, creates only the proven missing statuses and performs its own status read-back. It intentionally leaves the still-approved label gap for the official MCP owner. Never pass the credential in argv or environment.
7. Use official MCP operations for the approved missing labels and every operation it supports, then fully reread all configuration through the owner transport, prove an empty reconciliation plan, and recheck GitHub linking. If any step is missing, stop graph publication with one exact bounded human action.

Every preview or handoff response MUST distinguish the fully paginated team issue statuses, workspace Project statuses and labels. State explicitly that the no-echo in-memory GraphQL transaction creates only still-missing approved status definitions before any other global mutation, and that supported label creation belongs to official Linear MCP operations performed afterward. Final success requires an exact destination read-back with no remaining delta.

Also state explicitly that `workflow-configure` never creates or mutates a task Project or issue graph. It only configures the destination and proves GitHub integration; graph publication belongs to `task-graph-create`.

The script's required `--labels-input` and apply-only `--approved-plan-input` are transient complete JSON artifacts produced during the current configuration transaction. They are not credentials or durable configuration files. Delete them after the final read-back.
