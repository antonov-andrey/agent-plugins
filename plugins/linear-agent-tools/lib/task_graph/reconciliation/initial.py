"""Activation-barrier reconciliation for one initial Linear Project graph."""

from __future__ import annotations

from task_graph.model import TaskGraph, TaskGraphError, TaskRole
from task_graph.publication import GraphPublicationView
from task_graph.reconciliation.model import (
    PublicationAction,
    PublicationPhase,
    ReconciliationPlan,
    RemoteProject,
)


class TaskGraphReconciler:
    """Sequence one approved graph through its non-atomic activation barrier."""

    def __init__(self, graph: TaskGraph) -> None:
        """Bind the immutable desired graph for every reconciliation read."""

        if not isinstance(graph, TaskGraph):
            raise TaskGraphError("Graph reconciler requires one typed task graph")
        self._graph = graph
        self._view = GraphPublicationView.from_graph(graph)

    def activation_readback_require(self, remote: RemoteProject) -> ReconciliationPlan:
        """Prove the exact complete graph after its one-way activation handoff."""

        remote.initial_identity_require(self._view)
        if remote.name != self._view.project_name or remote.status_name != "In Progress":
            raise TaskGraphError("Activated Linear Project differs from the exact handoff state")
        remote.import_document_exact_require(self._view)
        desired_issue_by_node_key_map = {item.node_key: item for item in self._view.issue_list}
        remote_issue_by_node_key_map = remote.issue_by_node_key_map()
        if set(remote_issue_by_node_key_map) != set(desired_issue_by_node_key_map):
            raise TaskGraphError("Activated Linear Project issue set differs from the exact handoff graph")
        blocker_key_set_by_node_key_map = {
            node_key: {
                edge.blocker_node_key for edge in self._view.blocker_edge_list if edge.blocked_node_key == node_key
            }
            for node_key in desired_issue_by_node_key_map
        }
        node_by_key_map = {item.node_key: item for item in self._graph.node_list}
        for node_key, desired in desired_issue_by_node_key_map.items():
            current = remote_issue_by_node_key_map[node_key]
            required_label_set = {node_by_key_map[node_key].role}
            if node_by_key_map[node_key].role is not TaskRole.HUMAN:
                required_label_set.add("agent:codex")
            if (
                current.title != desired.title
                or current.description != desired.description
                or current.status_name != "Todo"
                or set(current.label_name_list) != required_label_set
                or current.assignee_id != desired.assignee_id
                or current.delegate_id != desired.delegate_id
                or set(current.blocker_key_list) != blocker_key_set_by_node_key_map[node_key]
            ):
                raise TaskGraphError(f"Activated Linear issue {node_key} differs from the exact handoff graph")
        return ReconciliationPlan(PublicationPhase.COMPLETE, [], activation_ready=True)

    def cancellation_plan(self, remote: RemoteProject, *, human_decision: bool) -> ReconciliationPlan:
        """Stop dispatch and cancel every unfinished issue in this exact Project."""

        if not isinstance(human_decision, bool) or not human_decision:
            raise TaskGraphError("Project cancellation requires an explicit human decision")
        remote.initial_identity_require(self._view)
        if remote.status_name == "Completed":
            raise TaskGraphError("A completed Linear Project cannot be canceled or reopened")
        if remote.status_name in {"Planned", "In Progress"}:
            return ReconciliationPlan(
                PublicationPhase.PROJECT_CANCELLATION,
                [
                    PublicationAction(
                        kind="project-status-update",
                        stable_key=self._view.project_key,
                        payload={"project_id": remote.id, "status_name": "Canceled"},
                    )
                ],
                activation_ready=False,
            )
        if remote.status_name != "Canceled":
            raise TaskGraphError("Linear Project cancellation encountered an unsupported status")
        action_list = [
            PublicationAction(
                kind="issue-status-update",
                stable_key=f"{issue.node_key}:{issue.id}",
                payload={"issue_id": issue.id, "status_name": "Canceled"},
            )
            for issue in sorted(remote.issue_list, key=lambda item: item.id)
            if issue.status_name not in {"Done", "Canceled"}
        ]
        if action_list:
            return ReconciliationPlan(
                PublicationPhase.NODE_CANCELLATION,
                action_list,
                activation_ready=False,
            )
        return ReconciliationPlan(PublicationPhase.COMPLETE, [], activation_ready=False)

    def plan(self, remote: RemoteProject | None) -> ReconciliationPlan:
        """Return the next safe activation-barrier phase after exact read-back."""

        if remote is None:
            return ReconciliationPlan(
                phase=PublicationPhase.PROJECT,
                action_list=[
                    PublicationAction(
                        kind="project-create",
                        stable_key=self._view.project_key,
                        payload={
                            "name": self._view.project_name,
                            "description": self._view.project_description,
                            "project_key": self._view.project_key,
                            "status_name": "Planned",
                            "team_id": self._graph.team_id,
                        },
                    )
                ],
                activation_ready=False,
            )
        remote.initial_identity_require(self._view)
        if remote.status_name in {"In Progress", "Completed", "Canceled"}:
            remote.import_document_exact_require(self._view)
            return ReconciliationPlan(
                PublicationPhase.COMPLETE,
                [],
                activation_ready=remote.status_name == "In Progress",
            )
        if remote.status_name != "Planned":
            raise TaskGraphError("Graph import Project must remain Planned until its activation transition")
        matching_document_list = [
            item for item in remote.document_list if item.title == self._view.import_document_title
        ]
        if len(matching_document_list) > 1:
            raise TaskGraphError("Linear Project contains duplicate import documents for the exact source")
        if not matching_document_list:
            return ReconciliationPlan(
                PublicationPhase.DOCUMENT,
                [
                    PublicationAction(
                        kind="import-document-create",
                        stable_key=self._view.project_key,
                        payload={
                            "content": self._view.import_document_content,
                            "project_id": remote.id,
                            "title": self._view.import_document_title,
                        },
                    )
                ],
                activation_ready=False,
            )
        current_document = matching_document_list[0]
        if current_document.content != self._view.import_document_content:
            provider_marker = "# Linear Agent Tools Import Plan\n"
            project_key_marker = f"- Project key: `{self._view.project_key}`"
            if (
                not current_document.content.startswith(provider_marker)
                or project_key_marker not in current_document.content
            ):
                raise TaskGraphError("Linear Project import-document title collides with a foreign document")
            return ReconciliationPlan(
                PublicationPhase.DOCUMENT,
                [
                    PublicationAction(
                        kind="import-document-update",
                        stable_key=self._view.project_key,
                        payload={
                            "content": self._view.import_document_content,
                            "document_id": current_document.id,
                            "project_id": remote.id,
                            "title": self._view.import_document_title,
                        },
                    )
                ],
                activation_ready=False,
            )
        desired_issue_by_node_key_map = {item.node_key: item for item in self._view.issue_list}
        remote_issue_by_node_key_map = remote.issue_by_node_key_map()
        unknown_key_set = set(remote_issue_by_node_key_map) - set(desired_issue_by_node_key_map)
        if unknown_key_set:
            raise TaskGraphError(f"Planned Project contains unknown issue keys: {sorted(unknown_key_set)}")
        issue_action_list: list[PublicationAction] = []
        for node_key, desired in desired_issue_by_node_key_map.items():
            current = remote_issue_by_node_key_map.get(node_key)
            if current is None:
                issue_action_list.append(PublicationAction.from_issue_create(desired, project_id=remote.id))
            else:
                current.staged_owned_fields_require(desired)
        if issue_action_list:
            return ReconciliationPlan(PublicationPhase.ISSUES, issue_action_list, activation_ready=False)
        relation_action_list: list[PublicationAction] = []
        for desired in self._view.issue_list:
            current = remote_issue_by_node_key_map[desired.node_key]
            desired_blocker_set = {
                edge.blocker_node_key
                for edge in self._view.blocker_edge_list
                if edge.blocked_node_key == desired.node_key
            }
            unknown_blocker_set = set(current.blocker_key_list) - desired_blocker_set
            if unknown_blocker_set:
                raise TaskGraphError(
                    f"Issue {desired.node_key} contains unknown blocker relations: {sorted(unknown_blocker_set)}"
                )
            for blocker_key in sorted(desired_blocker_set - set(current.blocker_key_list)):
                if current.status_name != "Backlog":
                    raise TaskGraphError(f"Activated issue {desired.node_key} is missing an approved blocker relation")
                relation_action_list.append(
                    PublicationAction(
                        kind="blocker-create",
                        stable_key=f"{blocker_key}->{desired.node_key}",
                        payload={
                            "blocked_issue_id": current.id,
                            "blocker_issue_id": remote_issue_by_node_key_map[blocker_key].id,
                        },
                    )
                )
        if relation_action_list:
            return ReconciliationPlan(PublicationPhase.RELATIONS, relation_action_list, activation_ready=False)
        metadata_action_list: list[PublicationAction] = []
        node_by_key_map = {item.node_key: item for item in self._graph.node_list}
        for desired in self._view.issue_list:
            current = remote_issue_by_node_key_map[desired.node_key]
            required_label_set = {node_by_key_map[desired.node_key].role}
            if node_by_key_map[desired.node_key].role is not TaskRole.HUMAN:
                required_label_set.add("agent:codex")
            current_role_label_set = set(current.label_name_list) & {
                "task:implementation",
                "task:review",
                "task:acceptance",
                "task:cleanup",
                "task:human",
            }
            if current_role_label_set - {node_by_key_map[desired.node_key].role}:
                raise TaskGraphError(f"Issue {desired.node_key} has a conflicting task role label")
            if node_by_key_map[desired.node_key].role is TaskRole.HUMAN and "agent:codex" in current.label_name_list:
                raise TaskGraphError(f"Human issue {desired.node_key} has the forbidden dispatch label")
            unexpected_label_set = set(current.label_name_list) - required_label_set
            if unexpected_label_set:
                raise TaskGraphError(
                    f"Issue {desired.node_key} contains labels outside its approved activation metadata: "
                    f"{sorted(unexpected_label_set)}"
                )
            missing_label_list = sorted(required_label_set - set(current.label_name_list))
            if (
                missing_label_list
                or current.assignee_id != desired.assignee_id
                or current.delegate_id != desired.delegate_id
            ):
                if current.status_name != "Backlog":
                    raise TaskGraphError(f"Activated issue {desired.node_key} has incomplete activation metadata")
                metadata_action_list.append(
                    PublicationAction(
                        kind="issue-activation-metadata-update",
                        stable_key=desired.node_key,
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
                PublicationPhase.NODE_METADATA,
                metadata_action_list,
                activation_ready=False,
            )
        activation_action_list: list[PublicationAction] = []
        for desired in self._view.issue_list:
            current = remote_issue_by_node_key_map[desired.node_key]
            if current.status_name == "Backlog":
                activation_action_list.append(
                    PublicationAction(
                        kind="issue-status-update",
                        stable_key=desired.node_key,
                        payload={"issue_id": current.id, "status_name": "Todo"},
                    )
                )
            elif current.status_name != "Todo":
                raise TaskGraphError(f"Staged issue {desired.node_key} has an invalid activation status")
        if activation_action_list:
            return ReconciliationPlan(
                PublicationPhase.NODE_ACTIVATION,
                activation_action_list,
                activation_ready=False,
            )
        return ReconciliationPlan(
            PublicationPhase.PROJECT_ACTIVATION,
            [
                PublicationAction(
                    kind="project-status-update",
                    stable_key=self._view.project_key,
                    payload={"project_id": remote.id, "status_name": "In Progress"},
                )
            ],
            activation_ready=True,
        )
