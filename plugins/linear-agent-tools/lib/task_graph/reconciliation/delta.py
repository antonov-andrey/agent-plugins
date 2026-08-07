"""Idempotent activation-barrier reconciliation for active-Project deltas."""

from __future__ import annotations

from task_graph.delta import TaskGraphDelta
from task_graph.model import TaskBlockerEdge, TaskGraphError, TaskRole
from task_graph.publication import (
    DeltaPublicationView,
    IssuePublication,
    linear_markdown_link,
    project_description_build,
)
from task_graph.reconciliation.model import (
    PublicationAction,
    PublicationPhase,
    ReconciliationPlan,
    RemoteIssue,
    RemoteProject,
)
from task_graph.transaction_document import TransactionDocumentReader
from task_graph.topology import cycle_node_key_get, exist_ordered_role_path, exist_path

_KNOWN_ISSUE_STATUS_SET = frozenset(
    {
        "Backlog",
        "Todo",
        "In Progress",
        "Review",
        # Migration compatibility for active Projects configured before AND-35.
        "Human Review",
        "Rework",
        "Merging",
        "Done",
        "Canceled",
    }
)
_ROLE_VALUE_SET = frozenset(item.value for item in TaskRole)


class TaskGraphDeltaReconciler:
    """Sequence one approved additive delta against an exact active Project."""

    def __init__(self, delta: TaskGraphDelta) -> None:
        """Bind the immutable approved delta for every reconciliation read."""

        if not isinstance(delta, TaskGraphDelta):
            raise TaskGraphError("Delta reconciler requires one typed task graph delta")
        self._delta = delta
        self._view = DeltaPublicationView.from_delta(delta)

    def plan(self, remote: RemoteProject) -> ReconciliationPlan:
        """Return only the next safe mutation phase for the approved delta."""

        self._project_identity_require(remote)
        self._accepted_resource_keys_require(remote)
        remote_issue_by_node_key_map = remote.issue_by_node_key_map()
        self._referenced_nodes_require(remote_issue_by_node_key_map)
        self._relation_scope_require(remote_issue_by_node_key_map)
        self._existing_target_validate(remote_issue_by_node_key_map)

        document_plan = self._transaction_document_plan(remote)
        if document_plan is not None:
            return document_plan

        desired_issue_by_node_key_map = {item.node_key: item for item in self._view.issue_list}
        issue_plan = self._issue_reconciliation_plan(
            remote,
            remote_issue_by_node_key_map=remote_issue_by_node_key_map,
        )
        if issue_plan is not None:
            return issue_plan

        self._topology_require(remote_issue_by_node_key_map)
        relation_plan = self._relation_reconciliation_plan(remote_issue_by_node_key_map)
        if relation_plan is not None:
            return relation_plan

        reverification_plan = self._reverification_plan(remote_issue_by_node_key_map)
        if reverification_plan is not None:
            return reverification_plan

        metadata_plan = self._metadata_reconciliation_plan(
            desired_issue_by_node_key_map=desired_issue_by_node_key_map,
            remote_issue_by_node_key_map=remote_issue_by_node_key_map,
        )
        if metadata_plan is not None:
            return metadata_plan

        activation_plan = self._activation_plan(
            remote_issue_by_node_key_map=remote_issue_by_node_key_map,
        )
        if activation_plan is not None:
            return activation_plan
        return ReconciliationPlan(PublicationPhase.COMPLETE, [], activation_ready=True)

    def _accepted_resource_keys_require(self, remote: RemoteProject) -> None:
        """Reject resources already accepted by an earlier graph transaction."""

        accepted_resource_key_set = TransactionDocumentReader(remote.document_list).accepted_resource_key_set_get(
            excluded_title=self._view.import_document_title
        )
        new_resource_key_set = {resource.key for node in self._delta.node_list for resource in node.resource_list}
        conflicting_resource_key_set = accepted_resource_key_set & new_resource_key_set
        if conflicting_resource_key_set:
            raise TaskGraphError(
                "Graph delta repeats accepted resource keys: " + ", ".join(sorted(conflicting_resource_key_set))
            )

    def _referenced_nodes_require(self, remote_issue_by_node_key_map: dict[str, RemoteIssue]) -> None:
        """Require every existing node named by the approved delta."""

        missing_existing_key_set = set(self._delta.existing_node_key_list) - set(remote_issue_by_node_key_map)
        if missing_existing_key_set:
            raise TaskGraphError(f"Graph delta references absent existing nodes: {sorted(missing_existing_key_set)}")

    def _transaction_document_plan(self, remote: RemoteProject) -> ReconciliationPlan | None:
        """Create or validate the immutable receipt for this exact delta."""

        matching_document_list = [
            item for item in remote.document_list if item.title == self._view.import_document_title
        ]
        if len(matching_document_list) > 1:
            raise TaskGraphError("Active Project contains duplicate receipts for the exact approved delta")
        if not matching_document_list:
            return ReconciliationPlan(
                PublicationPhase.DELTA_DOCUMENT,
                [
                    PublicationAction(
                        kind="delta-document-create",
                        stable_key=self._view.delta_fingerprint,
                        payload={
                            "content": self._view.import_document_content,
                            "project_id": remote.id,
                            "title": self._view.import_document_title,
                        },
                    )
                ],
                activation_ready=False,
            )
        if matching_document_list[0].content != self._view.import_document_content:
            raise TaskGraphError("Active Project delta receipt differs from its exact approved content")
        return None

    def _issue_reconciliation_plan(
        self,
        remote: RemoteProject,
        *,
        remote_issue_by_node_key_map: dict[str, RemoteIssue],
    ) -> ReconciliationPlan | None:
        """Create missing Backlog issues and reject stable-key collisions."""

        desired_issue_by_node_key_map = {item.node_key: item for item in self._view.issue_list}
        issue_action_list: list[PublicationAction] = []
        for node_key, desired in desired_issue_by_node_key_map.items():
            current = remote_issue_by_node_key_map.get(node_key)
            if current is None:
                issue_action_list.append(PublicationAction.from_issue_create(desired, project_id=remote.id))
            else:
                current.delta_owned_fields_require(desired)
        if issue_action_list:
            return ReconciliationPlan(
                PublicationPhase.DELTA_ISSUES,
                issue_action_list,
                activation_ready=False,
            )
        return None

    def _existing_target_validate(self, remote_issue_by_node_key_map: dict[str, RemoteIssue]) -> None:
        """Reject unsafe existing targets before returning any mutation action.

        Args:
            remote_issue_by_node_key_map: Fully read Project issues by stable node key.
        """

        reverification_key_set = set(self._delta.reverification_node_key_list)
        new_node_key_set = {item.node_key for item in self._delta.node_list}
        existing_node_key_set = set(self._delta.existing_node_key_list)
        existing_target_key_set = {
            edge.blocked_node_key
            for edge in self._delta.blocker_edge_list
            if edge.blocker_node_key in new_node_key_set and edge.blocked_node_key in existing_node_key_set
        }
        for node_key in sorted(existing_target_key_set):
            current = remote_issue_by_node_key_map[node_key]
            role = current.role_get()
            if node_key in reverification_key_set and role not in {TaskRole.REVIEW, TaskRole.ACCEPTANCE}:
                raise TaskGraphError(
                    f"Delta may add a blocker to existing node {node_key} only when it is review or acceptance"
                )
            if role is TaskRole.IMPLEMENTATION:
                if current.status_name == "Rework":
                    continue
                raise TaskGraphError(
                    f"Delta implementation target {node_key} must already be Rework and absent from reverification"
                )
            if role not in {TaskRole.REVIEW, TaskRole.ACCEPTANCE}:
                raise TaskGraphError(
                    f"Delta target {node_key} must be review, acceptance, or implementation already in Rework"
                )
            if current.status_name == "Todo":
                continue
            if current.status_name == "In Progress" and node_key in reverification_key_set:
                continue
            raise TaskGraphError(
                f"Delta target {node_key} must already be Todo or explicitly return from In Progress to Todo"
            )
        if reverification_key_set - existing_target_key_set:
            raise TaskGraphError("Graph delta reverification node has no new incoming blocker")

    def _relation_reconciliation_plan(
        self,
        remote_issue_by_node_key_map: dict[str, RemoteIssue],
    ) -> ReconciliationPlan | None:
        """Create approved blocker relations while every new endpoint is inert."""

        relation_action_list: list[PublicationAction] = []
        for edge in self._view.blocker_edge_list:
            blocked = remote_issue_by_node_key_map[edge.blocked_node_key]
            if edge.blocker_node_key not in blocked.blocker_key_list:
                new_key_set = {item.node_key for item in self._delta.node_list}
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
        return None

    def _reverification_plan(
        self,
        remote_issue_by_node_key_map: dict[str, RemoteIssue],
    ) -> ReconciliationPlan | None:
        """Return affected existing review gates to Todo before new work runs."""

        reverification_action_list: list[PublicationAction] = []
        for node_key in sorted(self._delta.reverification_node_key_list):
            current = remote_issue_by_node_key_map[node_key]
            if current.status_name == "In Progress":
                reverification_action_list.append(
                    PublicationAction(
                        kind="issue-status-update",
                        stable_key=node_key,
                        payload={"issue_id": current.id, "status_name": "Todo"},
                    )
                )
        if reverification_action_list:
            return ReconciliationPlan(
                PublicationPhase.DELTA_REVERIFICATION,
                reverification_action_list,
                activation_ready=False,
            )
        return None

    def _metadata_reconciliation_plan(
        self,
        *,
        desired_issue_by_node_key_map: dict[str, IssuePublication],
        remote_issue_by_node_key_map: dict[str, RemoteIssue],
    ) -> ReconciliationPlan | None:
        """Attach exact labels and execution identity before delta dispatch."""

        node_by_key_map = {item.node_key: item for item in self._delta.node_list}
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
        return None

    def _activation_plan(
        self,
        *,
        remote_issue_by_node_key_map: dict[str, RemoteIssue],
    ) -> ReconciliationPlan | None:
        """Move fully wired new issues to Todo without altering progressed nodes."""

        desired_issue_by_node_key_map = {item.node_key: item for item in self._view.issue_list}
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
        return None

    def _project_identity_require(self, remote: RemoteProject) -> None:
        """Require one exact active destination and immutable source identity."""

        if (
            remote.id != self._delta.project_id
            or remote.team_id != self._delta.team_id
            or remote.project_key != self._delta.project_key
        ):
            raise TaskGraphError("Graph delta destination differs from its approved Project identity")
        if remote.description != project_description_build(
            project_key=self._delta.project_key,
            source=self._delta.source,
        ):
            raise TaskGraphError("Graph delta destination lost its immutable provider description")
        if remote.status_name != "In Progress":
            raise TaskGraphError("Graph deltas apply only to an active In Progress Project")
        title = f"Linear task graph import {self._delta.source.fingerprint()}"
        matching_document_list = [item for item in remote.document_list if item.title == title]
        if len(matching_document_list) != 1:
            raise TaskGraphError("Graph delta destination has no unique immutable import receipt")
        content = matching_document_list[0].content
        required_fragment_list = [
            "# Linear Agent Tools Import Plan\n",
            f"* Project key: `{self._delta.project_key}`",
            f"* Source fingerprint: `{self._delta.source.fingerprint()}`",
            f"* Source: {linear_markdown_link(self._delta.source.canonical_url)}",
            f"* Revision: `{self._delta.source.revision}`",
        ]
        if not content.startswith(required_fragment_list[0]) or any(
            fragment not in content for fragment in required_fragment_list[1:]
        ):
            raise TaskGraphError("Graph delta destination import receipt lost its immutable source identity")

    def _relation_scope_require(self, remote_issue_by_node_key_map: dict[str, RemoteIssue]) -> None:
        """Reject current relations involving a new node that the delta did not approve."""

        approved_edge_set = set(self._delta.blocker_edge_list)
        new_key_set = {item.node_key for item in self._delta.node_list}
        for node_key in new_key_set:
            current = remote_issue_by_node_key_map.get(node_key)
            if current is not None:
                for blocker_key in current.blocker_key_list:
                    if (
                        TaskBlockerEdge(blocker_node_key=blocker_key, blocked_node_key=node_key)
                        not in approved_edge_set
                    ):
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

    def _topology_require(self, remote_issue_by_node_key_map: dict[str, RemoteIssue]) -> None:
        """Prove additive edges retain review, acceptance and final cleanup gates."""

        new_node_by_key_map = {item.node_key: item for item in self._delta.node_list}
        relevant_key_set = set(self._delta.existing_node_key_list) | set(new_node_by_key_map)
        role_by_node_key_map = {
            key: remote_issue_by_node_key_map[key].role_get() for key in self._delta.existing_node_key_list
        }
        role_by_node_key_map.update({key: node.role for key, node in new_node_by_key_map.items()})
        cleanup_key_list = [key for key, role in role_by_node_key_map.items() if role is TaskRole.CLEANUP]
        if len(cleanup_key_list) != 1:
            raise TaskGraphError("Graph delta must reference the one existing final cleanup node")
        downstream_node_key_set_by_blocker_key_map = {key: set() for key in relevant_key_set}
        for blocked_key in self._delta.existing_node_key_list:
            for blocker_key in remote_issue_by_node_key_map[blocked_key].blocker_key_list:
                if blocker_key in relevant_key_set:
                    downstream_node_key_set_by_blocker_key_map[blocker_key].add(blocked_key)
        for edge in self._delta.blocker_edge_list:
            downstream_node_key_set_by_blocker_key_map[edge.blocker_node_key].add(edge.blocked_node_key)
        cycle_node_key = cycle_node_key_get(downstream_node_key_set_by_blocker_key_map)
        if cycle_node_key:
            raise TaskGraphError(f"Graph delta creates a blocker cycle at {cycle_node_key}")
        for node in self._delta.node_list:
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
                    raise TaskGraphError(
                        f"Delta resource {resource.key} consumer must be downstream from its owning task"
                    )
