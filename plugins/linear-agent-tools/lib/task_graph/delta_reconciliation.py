"""Idempotent activation-barrier reconciliation for active-Project deltas."""

from __future__ import annotations

from collections import deque

from task_graph.delta import TaskGraphDelta
from task_graph.model import TaskGraphError, TaskRole
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
    remote_by_key = _remote_issue_map_get(remote.issue_list)
    missing_existing_key_set = set(delta.existing_node_key_list) - set(remote_by_key)
    if missing_existing_key_set:
        raise TaskGraphError(f"Graph delta references absent existing nodes: {sorted(missing_existing_key_set)}")
    _delta_relation_scope_require(delta, remote_by_key)
    matching_document_list = tuple(item for item in remote.document_list if item.title == view.import_document_title)
    if len(matching_document_list) > 1:
        raise TaskGraphError("Active Project contains duplicate receipts for the exact approved delta")
    if not matching_document_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_DOCUMENT,
            (
                PublicationAction(
                    kind="delta-document-create",
                    stable_key=view.delta_fingerprint,
                    payload={
                        "content": view.import_document_content,
                        "project_id": remote.id,
                        "title": view.import_document_title,
                    },
                ),
            ),
            activation_ready=False,
        )
    if matching_document_list[0].content != view.import_document_content:
        raise TaskGraphError("Active Project delta receipt differs from its exact approved content")

    desired_by_key = {item.node_key: item for item in view.issue_list}
    issue_action_list: list[PublicationAction] = []
    for node_key, desired in desired_by_key.items():
        current = remote_by_key.get(node_key)
        if current is None:
            issue_action_list.append(_issue_create_action(desired, project_id=remote.id))
        else:
            _delta_issue_owned_fields_require(desired, current)
    if issue_action_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_ISSUES,
            tuple(issue_action_list),
            activation_ready=False,
        )

    _topology_require(delta, remote_by_key)
    relation_action_list: list[PublicationAction] = []
    for blocker_key, blocked_key in view.blocker_edge_list:
        blocked = remote_by_key[blocked_key]
        if blocker_key not in blocked.blocker_key_list:
            new_key_set = {item.node_key for item in delta.node_list}
            active_new_endpoint_list = [
                key
                for key in (blocker_key, blocked_key)
                if key in new_key_set and remote_by_key[key].status_name != "Backlog"
            ]
            if active_new_endpoint_list:
                raise TaskGraphError(
                    "Delta relation is absent after a new node left Backlog: "
                    + ", ".join(sorted(active_new_endpoint_list))
                )
            relation_action_list.append(
                PublicationAction(
                    kind="blocker-create",
                    stable_key=f"{blocker_key}->{blocked_key}",
                    payload={
                        "blocked_issue_id": blocked.id,
                        "blocker_issue_id": remote_by_key[blocker_key].id,
                    },
                )
            )
    if relation_action_list:
        return ReconciliationPlan(
            PublicationPhase.DELTA_RELATIONS,
            tuple(relation_action_list),
            activation_ready=False,
        )

    reverification_action_list: list[PublicationAction] = []
    reverification_key_set = set(delta.reverification_node_key_list)
    new_node_key_set = {item.node_key for item in delta.node_list}
    existing_node_key_set = set(delta.existing_node_key_list)
    existing_target_key_set = {
        blocked_key
        for blocker_key, blocked_key in delta.blocker_edge_list
        if blocker_key in new_node_key_set and blocked_key in existing_node_key_set
    }
    for node_key in sorted(existing_target_key_set):
        current = remote_by_key[node_key]
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
            tuple(reverification_action_list),
            activation_ready=False,
        )

    node_by_key = {item.node_key: item for item in delta.node_list}
    metadata_action_list: list[PublicationAction] = []
    for node_key, desired in desired_by_key.items():
        current = remote_by_key[node_key]
        node = node_by_key[node_key]
        required_label_set = {node.role.value}
        if node.agent_executable():
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
            tuple(metadata_action_list),
            activation_ready=False,
        )

    activation_action_list: list[PublicationAction] = []
    for node_key in sorted(desired_by_key):
        current = remote_by_key[node_key]
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
            tuple(activation_action_list),
            activation_ready=True,
        )
    return ReconciliationPlan(PublicationPhase.COMPLETE, (), activation_ready=True)


def _delta_relation_scope_require(delta: TaskGraphDelta, remote_by_key: dict[str, RemoteIssue]) -> None:
    """Reject every current relation involving a new node that the delta did not approve."""

    approved_edge_set = set(delta.blocker_edge_list)
    new_key_set = {item.node_key for item in delta.node_list}
    for node_key in new_key_set:
        current = remote_by_key.get(node_key)
        if current is not None:
            for blocker_key in current.blocker_key_list:
                if (blocker_key, node_key) not in approved_edge_set:
                    raise TaskGraphError(
                        f"Delta node {node_key} has an unapproved current blocker relation from {blocker_key}"
                    )
    for blocked in remote_by_key.values():
        for blocker_key in set(blocked.blocker_key_list) & new_key_set:
            if (blocker_key, blocked.node_key) not in approved_edge_set:
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
    matching_document_list = tuple(item for item in remote.document_list if item.title == title)
    if len(matching_document_list) != 1:
        raise TaskGraphError("Graph delta destination has no unique immutable import receipt")
    content = matching_document_list[0].content
    required_fragment_list = (
        "# Linear Agent Tools Import Plan\n",
        f"- Project key: `{delta.project_key}`",
        f"- Source fingerprint: `{delta.source.fingerprint()}`",
        f"- Source: {delta.source.canonical_url}",
        f"- Revision: `{delta.source.revision}`",
    )
    if not content.startswith(required_fragment_list[0]) or any(
        fragment not in content for fragment in required_fragment_list[1:]
    ):
        raise TaskGraphError("Graph delta destination import receipt lost its immutable source identity")


def _remote_issue_map_get(
    issue_list: tuple[RemoteIssue, ...],
) -> dict[str, RemoteIssue]:
    """Return one unique complete issue mapping.

    Args:
        issue_list: Fully paginated Project issue snapshot.

    Returns:
        Mapping by provider node key.
    """

    result: dict[str, RemoteIssue] = {}
    for issue in issue_list:
        if not issue.node_key or issue.node_key in result:
            raise TaskGraphError("Active Project contains an absent or duplicate provider node key")
        result[issue.node_key] = issue
    return result


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


def _topology_require(delta: TaskGraphDelta, remote_by_key: dict[str, RemoteIssue]) -> None:
    """Prove that additive edges retain review, acceptance and final cleanup gates.

    Args:
        delta: Approved delta.
        remote_by_key: Complete current Project issues.
    """

    new_node_by_key = {item.node_key: item for item in delta.node_list}
    relevant_key_set = set(delta.existing_node_key_list) | set(new_node_by_key)
    role_by_key: dict[str, TaskRole] = {}
    for key in delta.existing_node_key_list:
        role_by_key[key] = _issue_role_get(remote_by_key[key])
    role_by_key.update({key: node.role for key, node in new_node_by_key.items()})
    cleanup_key_list = [key for key, role in role_by_key.items() if role is TaskRole.CLEANUP]
    if len(cleanup_key_list) != 1:
        raise TaskGraphError("Graph delta must reference the one existing final cleanup node")

    downstream_by_key = {key: set() for key in relevant_key_set}
    for blocked_key in delta.existing_node_key_list:
        for blocker_key in remote_by_key[blocked_key].blocker_key_list:
            if blocker_key in relevant_key_set:
                downstream_by_key[blocker_key].add(blocked_key)
    for blocker_key, blocked_key in delta.blocker_edge_list:
        downstream_by_key[blocker_key].add(blocked_key)
    _acyclic_require(downstream_by_key)

    for node in delta.node_list:
        expected_role_sequence = {
            TaskRole.IMPLEMENTATION: (
                TaskRole.REVIEW,
                TaskRole.ACCEPTANCE,
                TaskRole.CLEANUP,
            ),
            TaskRole.REVIEW: (TaskRole.ACCEPTANCE, TaskRole.CLEANUP),
            TaskRole.ACCEPTANCE: (TaskRole.CLEANUP,),
            TaskRole.HUMAN: (TaskRole.ACCEPTANCE, TaskRole.CLEANUP),
        }[node.role]
        if not _ordered_role_path_exists(
            node.node_key,
            expected_role_sequence,
            downstream_by_key=downstream_by_key,
            role_by_key=role_by_key,
        ):
            expected = " -> ".join(item.value for item in expected_role_sequence)
            raise TaskGraphError(f"Delta node {node.node_key} does not retain downstream path {expected}")
        blocker_role_set = {
            role_by_key[blocker_key] for blocker_key in node.blocker_key_list if blocker_key in role_by_key
        }
        if node.role is TaskRole.REVIEW and TaskRole.IMPLEMENTATION not in blocker_role_set:
            raise TaskGraphError(f"Delta review {node.node_key} must be blocked by implementation work")
        if node.role is TaskRole.ACCEPTANCE and TaskRole.REVIEW not in blocker_role_set:
            raise TaskGraphError(f"Delta acceptance {node.node_key} must be blocked by review work")
        for resource in node.resource_list:
            if any(
                not _path_exists(
                    node.node_key,
                    consumer_key,
                    downstream_by_key=downstream_by_key,
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


def _acyclic_require(downstream_by_key: dict[str, set[str]]) -> None:
    """Reject cycles in the exact relevant active-Project slice.

    Args:
        downstream_by_key: Directed blocker-to-blocked adjacency.
    """

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise TaskGraphError(f"Graph delta creates a blocker cycle at {key}")
        if key in visited:
            return
        visiting.add(key)
        for downstream in downstream_by_key[key]:
            visit(downstream)
        visiting.remove(key)
        visited.add(key)

    for node_key in sorted(downstream_by_key):
        visit(node_key)


def _ordered_role_path_exists(
    start_key: str,
    expected_role_sequence: tuple[TaskRole, ...],
    *,
    downstream_by_key: dict[str, set[str]],
    role_by_key: dict[str, TaskRole],
) -> bool:
    """Return whether one downstream path encounters required roles in order.

    Args:
        start_key: New delta node key.
        expected_role_sequence: Required downstream gates.
        downstream_by_key: Directed blocker-to-blocked adjacency.
        role_by_key: Exact role mapping.

    Returns:
        Whether an ordered path exists.
    """

    queue = deque([(start_key, 0)])
    visited: set[tuple[str, int]] = set()
    while queue:
        key, index = queue.popleft()
        state = (key, index)
        if state in visited:
            continue
        visited.add(state)
        for downstream in downstream_by_key[key]:
            next_index = index
            if role_by_key[downstream] is expected_role_sequence[index]:
                next_index += 1
                if next_index == len(expected_role_sequence):
                    return True
            queue.append((downstream, next_index))
    return False


def _path_exists(start_key: str, target_key: str, *, downstream_by_key: dict[str, set[str]]) -> bool:
    """Return whether one blocker path reaches an exact downstream task."""

    queue = deque([start_key])
    visited: set[str] = set()
    while queue:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        for downstream in downstream_by_key[key]:
            if downstream == target_key:
                return True
            queue.append(downstream)
    return False
