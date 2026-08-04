"""Idempotent activation-barrier reconciliation for active-Project deltas."""

from __future__ import annotations

from task_graph.delta import TaskGraphDelta
from task_graph.model import TaskBlockerEdge, TaskGraphError, TaskRole
from task_graph.publication import (
    DeltaPublicationView,
    IssuePublication,
    delta_publication_view_build,
    project_description_build,
)
from task_graph.reconciliation import (
    PublicationAction,
    PublicationPhase,
    ReconciliationPlan,
    RemoteIssue,
    RemoteProject,
)
from task_graph.transaction_document import accepted_resource_key_set_get
from task_graph.topology import exist_ordered_role_path, exist_path

_ROLE_VALUE_SET = frozenset(item.value for item in TaskRole)
_KNOWN_ISSUE_STATUS_SET = frozenset(
    {
        "Backlog",
        "Todo",
        "In Progress",
        "Human Review",
        "Rework",
        "Merging",
        "Done",
        "Canceled",
    }
)


def delta_reconciliation_plan_build(delta: TaskGraphDelta, remote: RemoteProject) -> ReconciliationPlan:
    """Return only the next safe mutation phase for one approved graph delta.

    Args:
        delta: Exact user-approved additive graph delta.
        remote: Fully paginated current active Project snapshot.

    Returns:
        Next phase and host-executed mutations.
    """

    view = delta_publication_view_build(delta)
    _project_identity_require(delta, remote)
    accepted_resource_key_set = accepted_resource_key_set_get(
        remote.document_list,
        excluded_title=view.import_document_title,
    )
    new_resource_key_set = {resource.key for node in delta.node_list for resource in node.resource_list}
    conflicting_resource_key_set = accepted_resource_key_set & new_resource_key_set
    if conflicting_resource_key_set:
        raise TaskGraphError(
            "Graph delta repeats accepted resource keys: " + ", ".join(sorted(conflicting_resource_key_set))
        )
    remote_issue_by_node_key_map = _remote_issue_map_get(remote.issue_list)
    missing_existing_key_set = set(delta.existing_node_key_list) - set(remote_issue_by_node_key_map)
    if missing_existing_key_set:
        raise TaskGraphError(f"Graph delta references absent existing nodes: {sorted(missing_existing_key_set)}")
    _delta_relation_scope_require(delta, remote_issue_by_node_key_map)
    matching_document_list = [item for item in remote.document_list if item.title == view.import_document_title]
    if len(matching_document_list) > 1:
        raise TaskGraphError("Active Project contains duplicate receipts for the exact approved delta")
    if not matching_document_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_DOCUMENT,
            [
                PublicationAction(
                    kind="delta-document-create",
                    stable_key=view.delta_fingerprint,
                    payload={
                        "content": view.import_document_content,
                        "project_id": remote.id,
                        "title": view.import_document_title,
                    },
                ),
            ],
            activation_ready=False,
        )
    if matching_document_list[0].content != view.import_document_content:
        raise TaskGraphError("Active Project delta receipt differs from its exact approved content")

    desired_issue_by_node_key_map = {item.node_key: item for item in view.issue_list}
    issue_action_list: list[PublicationAction] = []
    for node_key, desired in desired_issue_by_node_key_map.items():
        current = remote_issue_by_node_key_map.get(node_key)
        if current is None:
            issue_action_list.append(_issue_create_action(desired, project_id=remote.id))
        else:
            _delta_issue_owned_fields_require(desired, current)
    if issue_action_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_ISSUES,
            issue_action_list,
            activation_ready=False,
        )

    _topology_require(delta, remote_issue_by_node_key_map)
    relation_action_list: list[PublicationAction] = []
    for edge in view.blocker_edge_list:
        blocked = remote_issue_by_node_key_map[edge.blocked_node_key]
        if edge.blocker_node_key not in blocked.blocker_key_list:
            new_key_set = {item.node_key for item in delta.node_list}
            active_new_endpoint_list = [
                key
                for key in [edge.blocker_node_key, edge.blocked_node_key]
                if key in new_key_set and remote_issue_by_node_key_map[key].status_name != "Backlog"
            ]
            if active_new_endpoint_list:
                raise TaskGraphError(
                    "Delta relation is absent after a new node left Backlog: "
                    + ", ".join(sorted(active_new_endpoint_list))
                )
            relation_action_list.append(
                PublicationAction(
                    kind="blocker-create",
                    stable_key=f"{edge.blocker_node_key}->{edge.blocked_node_key}",
                    payload={
                        "blocked_issue_id": blocked.id,
                        "blocker_issue_id": remote_issue_by_node_key_map[edge.blocker_node_key].id,
                    },
                )
            )
    if relation_action_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_RELATIONS,
            relation_action_list,
            activation_ready=False,
        )

    reverification_action_list: list[PublicationAction] = []
    reverification_key_set = set(delta.reverification_node_key_list)
    new_node_key_set = {item.node_key for item in delta.node_list}
    existing_node_key_set = set(delta.existing_node_key_list)
    existing_target_key_set = {
        edge.blocked_node_key
        for edge in delta.blocker_edge_list
        if edge.blocker_node_key in new_node_key_set and edge.blocked_node_key in existing_node_key_set
    }
    for node_key in sorted(existing_target_key_set):
        current = remote_issue_by_node_key_map[node_key]
        role = _issue_role_get(current)
        if role not in {TaskRole.REVIEW, TaskRole.ACCEPTANCE}:
            raise TaskGraphError(
                f"Delta may add a blocker to existing node {node_key} only when it is review or acceptance"
            )
        if current.status_name == "Todo":
            continue
        if current.status_name == "In Progress" and node_key in reverification_key_set:
            reverification_action_list.append(
                PublicationAction(
                    kind="issue-status-update",
                    stable_key=node_key,
                    payload={"issue_id": current.id, "status_name": "Todo"},
                )
            )
            continue
        raise TaskGraphError(
            f"Delta target {node_key} must already be Todo or explicitly return from In Progress to Todo"
        )
    if reverification_key_set - existing_target_key_set:
        raise TaskGraphError("Graph delta reverification node has no new incoming blocker")
    if reverification_action_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_REVERIFICATION,
            reverification_action_list,
            activation_ready=False,
        )

    node_by_key_map = {item.node_key: item for item in delta.node_list}
    metadata_action_list: list[PublicationAction] = []
    for node_key, desired in desired_issue_by_node_key_map.items():
        current = remote_issue_by_node_key_map[node_key]
        node = node_by_key_map[node_key]
        required_label_set = {node.role.value}
        if node.can_agent_execute():
            required_label_set.add("agent:codex")
        current_role_set = set(current.label_name_list) & _ROLE_VALUE_SET
        if current_role_set - {node.role.value}:
            raise TaskGraphError(f"Delta issue {node_key} has a conflicting task role label")
        if node.role is TaskRole.HUMAN and "agent:codex" in current.label_name_list:
            raise TaskGraphError(f"Human delta issue {node_key} has the forbidden dispatch label")
        unexpected_label_set = set(current.label_name_list) - required_label_set
        if unexpected_label_set:
            raise TaskGraphError(
                f"Delta issue {node_key} contains labels outside its approved activation metadata: "
                f"{sorted(unexpected_label_set)}"
            )
        missing_label_list = sorted(required_label_set - set(current.label_name_list))
        if (
            missing_label_list
            or current.assignee_id != desired.assignee_id
            or current.delegate_id != desired.delegate_id
        ):
            if current.status_name != "Backlog":
                raise TaskGraphError(f"Activated delta issue {node_key} has incomplete activation metadata")
            metadata_action_list.append(
                PublicationAction(
                    kind="issue-activation-metadata-update",
                    stable_key=node_key,
                    payload={
                        "assignee_id": desired.assignee_id,
                        "delegate_id": desired.delegate_id,
                        "issue_id": current.id,
                        "label_add_list": missing_label_list,
                    },
                )
            )
    if metadata_action_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_METADATA,
            metadata_action_list,
            activation_ready=False,
        )

    activation_action_list: list[PublicationAction] = []
    for node_key in sorted(desired_issue_by_node_key_map):
        current = remote_issue_by_node_key_map[node_key]
        if current.status_name == "Backlog":
            activation_action_list.append(
                PublicationAction(
                    kind="issue-status-update",
                    stable_key=node_key,
                    payload={"issue_id": current.id, "status_name": "Todo"},
                )
            )
        elif current.status_name not in _KNOWN_ISSUE_STATUS_SET - {"Backlog"}:
            raise TaskGraphError(f"Delta issue {node_key} has an unsupported lifecycle status")
    if activation_action_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_ACTIVATION,
            activation_action_list,
            activation_ready=True,
        )
    return ReconciliationPlan(PublicationPhase.COMPLETE, [], activation_ready=True)


def _delta_relation_scope_require(
    delta: TaskGraphDelta,
    remote_issue_by_node_key_map: dict[str, RemoteIssue],
) -> None:
    """Reject every current relation involving a new node that the delta did not approve."""

    approved_edge_set = set(delta.blocker_edge_list)
    new_key_set = {item.node_key for item in delta.node_list}
    for node_key in new_key_set:
        current = remote_issue_by_node_key_map.get(node_key)
        if current is not None:
            for blocker_key in current.blocker_key_list:
                if TaskBlockerEdge(blocker_node_key=blocker_key, blocked_node_key=node_key) not in approved_edge_set:
                    raise TaskGraphError(
                        f"Delta node {node_key} has an unapproved current blocker relation from {blocker_key}"
                    )
    for blocked in remote_issue_by_node_key_map.values():
        for blocker_key in set(blocked.blocker_key_list) & new_key_set:
            if (
                TaskBlockerEdge(blocker_node_key=blocker_key, blocked_node_key=blocked.node_key)
                not in approved_edge_set
            ):
                raise TaskGraphError(
                    f"Delta node {blocker_key} has an unapproved current blocked relation to {blocked.node_key}"
                )


def _project_identity_require(delta: TaskGraphDelta, remote: RemoteProject) -> None:
    """Require one exact active destination and immutable source identity.

    Args:
        delta: Approved delta.
        remote: Current Project snapshot.
    """

    if remote.id != delta.project_id or remote.team_id != delta.team_id or remote.project_key != delta.project_key:
        raise TaskGraphError("Graph delta destination differs from its approved Project identity")
    if remote.description != project_description_build(project_key=delta.project_key, source=delta.source):
        raise TaskGraphError("Graph delta destination lost its immutable provider description")
    if remote.status_name != "In Progress":
        raise TaskGraphError("Graph deltas apply only to an active In Progress Project")
    title = f"Linear task graph import {delta.source.fingerprint()}"
    matching_document_list = [item for item in remote.document_list if item.title == title]
    if len(matching_document_list) != 1:
        raise TaskGraphError("Graph delta destination has no unique immutable import receipt")
    content = matching_document_list[0].content
    required_fragment_list = [
        "# Linear Agent Tools Import Plan\n",
        f"- Project key: `{delta.project_key}`",
        f"- Source fingerprint: `{delta.source.fingerprint()}`",
        f"- Source: {delta.source.canonical_url}",
        f"- Revision: `{delta.source.revision}`",
    ]
    if not content.startswith(required_fragment_list[0]) or any(
        fragment not in content for fragment in required_fragment_list[1:]
    ):
        raise TaskGraphError("Graph delta destination import receipt lost its immutable source identity")


def _remote_issue_map_get(
    issue_list: list[RemoteIssue],
) -> dict[str, RemoteIssue]:
    """Return one unique complete issue mapping.

    Args:
        issue_list: Fully paginated Project issue snapshot.

    Returns:
        Mapping by provider node key.
    """

    issue_by_node_key_map: dict[str, RemoteIssue] = {}
    for issue in issue_list:
        if not issue.node_key or issue.node_key in issue_by_node_key_map:
            raise TaskGraphError("Active Project contains an absent or duplicate provider node key")
        issue_by_node_key_map[issue.node_key] = issue
    return issue_by_node_key_map


def _issue_create_action(issue: IssuePublication, *, project_id: str) -> PublicationAction:
    """Create one inactive Backlog issue without the dispatch label.

    Args:
        issue: Rendered delta issue.
        project_id: Exact active Project ID.

    Returns:
        Host mutation.
    """

    return PublicationAction(
        kind="issue-create",
        stable_key=issue.node_key,
        payload={
            "assignee_id": "",
            "delegate_id": "",
            "description": issue.description,
            "label_name_list": [],
            "node_key": issue.node_key,
            "project_id": project_id,
            "status_name": "Backlog",
            "title": issue.title,
        },
    )


def _delta_issue_owned_fields_require(desired: IssuePublication, current: RemoteIssue) -> None:
    """Reject a delta stable-key collision or unsafe partial activation.

    Args:
        desired: Exact approved issue.
        current: Existing issue with the same node key.
    """

    if current.title != desired.title or current.description != desired.description:
        raise TaskGraphError(f"Delta issue {desired.node_key} conflicts with its approved stable key")
    if current.status_name not in _KNOWN_ISSUE_STATUS_SET:
        raise TaskGraphError(f"Delta issue {desired.node_key} has an unsupported lifecycle status")


def _topology_require(delta: TaskGraphDelta, remote_issue_by_node_key_map: dict[str, RemoteIssue]) -> None:
    """Prove that additive edges retain review, acceptance and final cleanup gates.

    Args:
        delta: Approved delta.
        remote_issue_by_node_key_map: Complete current Project issues.
    """

    new_node_by_key_map = {item.node_key: item for item in delta.node_list}
    relevant_key_set = set(delta.existing_node_key_list) | set(new_node_by_key_map)
    role_by_node_key_map: dict[str, TaskRole] = {}
    for key in delta.existing_node_key_list:
        role_by_node_key_map[key] = _issue_role_get(remote_issue_by_node_key_map[key])
    role_by_node_key_map.update({key: node.role for key, node in new_node_by_key_map.items()})
    cleanup_key_list = [key for key, role in role_by_node_key_map.items() if role is TaskRole.CLEANUP]
    if len(cleanup_key_list) != 1:
        raise TaskGraphError("Graph delta must reference the one existing final cleanup node")

    downstream_node_key_set_by_blocker_key_map = {key: set() for key in relevant_key_set}
    for blocked_key in delta.existing_node_key_list:
        for blocker_key in remote_issue_by_node_key_map[blocked_key].blocker_key_list:
            if blocker_key in relevant_key_set:
                downstream_node_key_set_by_blocker_key_map[blocker_key].add(blocked_key)
    for edge in delta.blocker_edge_list:
        downstream_node_key_set_by_blocker_key_map[edge.blocker_node_key].add(edge.blocked_node_key)
    _acyclic_require(downstream_node_key_set_by_blocker_key_map)

    for node in delta.node_list:
        expected_role_list = {
            TaskRole.IMPLEMENTATION: [
                TaskRole.REVIEW,
                TaskRole.ACCEPTANCE,
                TaskRole.CLEANUP,
            ],
            TaskRole.REVIEW: [TaskRole.ACCEPTANCE, TaskRole.CLEANUP],
            TaskRole.ACCEPTANCE: [TaskRole.CLEANUP],
            TaskRole.HUMAN: [TaskRole.ACCEPTANCE, TaskRole.CLEANUP],
        }[node.role]
        if not exist_ordered_role_path(
            node.node_key,
            expected_role_list,
            downstream_node_key_set_by_blocker_key_map=downstream_node_key_set_by_blocker_key_map,
            role_by_node_key_map=role_by_node_key_map,
        ):
            expected = " -> ".join(item.value for item in expected_role_list)
            raise TaskGraphError(f"Delta node {node.node_key} does not retain downstream path {expected}")
        blocker_role_set = {
            role_by_node_key_map[blocker_key]
            for blocker_key in node.blocker_key_list
            if blocker_key in role_by_node_key_map
        }
        if node.role is TaskRole.REVIEW and TaskRole.IMPLEMENTATION not in blocker_role_set:
            raise TaskGraphError(f"Delta review {node.node_key} must be blocked by implementation work")
        if node.role is TaskRole.ACCEPTANCE and TaskRole.REVIEW not in blocker_role_set:
            raise TaskGraphError(f"Delta acceptance {node.node_key} must be blocked by review work")
        for resource in node.resource_list:
            if any(
                not exist_path(
                    node.node_key,
                    consumer_key,
                    downstream_node_key_set_by_blocker_key_map=downstream_node_key_set_by_blocker_key_map,
                )
                for consumer_key in resource.consumer_node_key_list
            ):
                raise TaskGraphError(f"Delta resource {resource.key} consumer must be downstream from its owning task")


def _issue_role_get(issue: RemoteIssue) -> TaskRole:
    """Return the exact single task role from one existing issue.

    Args:
        issue: Existing Project issue.

    Returns:
        Typed task role.
    """

    role_value_set = set(issue.label_name_list) & _ROLE_VALUE_SET
    if len(role_value_set) != 1:
        raise TaskGraphError(f"Existing delta node {issue.node_key} must have exactly one task role label")
    return TaskRole(next(iter(role_value_set)))


def _acyclic_require(downstream_node_key_set_by_blocker_key_map: dict[str, set[str]]) -> None:
    """Reject cycles in the exact relevant active-Project slice.

    Args:
        downstream_node_key_set_by_blocker_key_map: Directed blocker-to-blocked adjacency.
    """

    visiting_node_key_set: set[str] = set()
    visited_node_key_set: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting_node_key_set:
            raise TaskGraphError(f"Graph delta creates a blocker cycle at {key}")
        if key in visited_node_key_set:
            return
        visiting_node_key_set.add(key)
        for downstream_node_key in downstream_node_key_set_by_blocker_key_map[key]:
            visit(downstream_node_key)
        visiting_node_key_set.remove(key)
        visited_node_key_set.add(key)

    for node_key in sorted(downstream_node_key_set_by_blocker_key_map):
        visit(node_key)
