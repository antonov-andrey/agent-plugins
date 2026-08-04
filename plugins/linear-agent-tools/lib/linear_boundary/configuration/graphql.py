"""Minimal typed GraphQL owner for Linear workflow-configuration gaps."""

from __future__ import annotations

from dataclasses import dataclass

from linear_boundary.configuration.model import (
    ConfigurationPlan,
    DestinationIdentity,
    LinearLabel,
    StatusDefinition,
    WorkflowConfigurationSnapshot,
)
from linear_boundary.configuration.reconciliation import WorkflowConfigurationReconciler
from linear_boundary.contract import LinearContractError
from linear_boundary.transport import LinearGraphQLTransport, LinearTransportError

_IDENTITY_AND_WORKFLOW_QUERY = """
query LinearAgentWorkflowConfiguration($teamId: String!, $viewerId: String!, $after: String) {
  viewer { id admin guest active }
  organization { id }
  team(id: $teamId) {
    id
    membership(userId: $viewerId) { id archivedAt }
    states(first: 100, after: $after, includeArchived: false) {
      nodes { id name type color description position }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_PROJECT_STATUS_QUERY = """
query LinearAgentProjectStatuses($after: String) {
  organization { id }
  projectStatuses(first: 100, after: $after, includeArchived: false) {
    nodes { id name type color description position }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_WORKFLOW_STATE_CREATE = """
mutation LinearAgentWorkflowStateCreate($input: WorkflowStateCreateInput!) {
  workflowStateCreate(input: $input) {
    success
    workflowState { id name type color description position }
  }
}
"""

_PROJECT_STATUS_CREATE = """
mutation LinearAgentProjectStatusCreate($input: ProjectStatusCreateInput!) {
  projectStatusCreate(input: $input) {
    success
    status { id name type color description position }
  }
}
"""


@dataclass(frozen=True, slots=True)
class WorkflowConfigurationGraphQLRead:
    """Contain the GraphQL-owned configuration and guarded destination."""

    destination: DestinationIdentity
    issue_status_list: list[StatusDefinition]
    project_status_list: list[StatusDefinition]

    def __post_init__(self) -> None:
        """Detach one trusted typed GraphQL read from pagination builders."""

        if not isinstance(self.destination, DestinationIdentity):
            raise LinearContractError("Linear GraphQL read destination has another shape")
        if not isinstance(self.issue_status_list, list) or any(
            not isinstance(item, StatusDefinition) for item in self.issue_status_list
        ):
            raise LinearContractError("Linear GraphQL issue status list has another shape")
        if not isinstance(self.project_status_list, list) or any(
            not isinstance(item, StatusDefinition) for item in self.project_status_list
        ):
            raise LinearContractError("Linear GraphQL Project status list has another shape")
        object.__setattr__(self, "issue_status_list", list(self.issue_status_list))
        object.__setattr__(self, "project_status_list", list(self.project_status_list))


class LinearWorkflowConfigurationGraphQL:
    """Read and create only missing issue and Project workflow statuses."""

    def __init__(
        self,
        transport: LinearGraphQLTransport,
        reconciler: WorkflowConfigurationReconciler,
    ) -> None:
        """Initialize the exact transport dependency.

        Args:
            transport: One-process secret-bearing GraphQL transport.
            reconciler: Canonical workflow-configuration comparison owner.
        """

        self._reconciler = reconciler
        self._transport = transport

    def read(
        self,
        *,
        expected_workspace_id: str | None,
        expected_viewer_id: str,
        expected_team_id: str,
    ) -> WorkflowConfigurationGraphQLRead:
        """Fully paginate relevant configuration and enforce destination identity.

        Args:
            expected_workspace_id: User-approved exact workspace ID, or ``None``
                for the read-only initial discovery plan.
            expected_viewer_id: User-approved exact viewer ID.
            expected_team_id: User-approved exact team ID.

        Returns:
            Current GraphQL-owned configuration.
        """

        workflow_status_list: list[StatusDefinition] = []
        after: str | None = None
        destination: DestinationIdentity | None = None
        while True:
            data = self._transport.execute(
                operation_name="LinearAgentWorkflowConfiguration",
                document=_IDENTITY_AND_WORKFLOW_QUERY,
                variables={
                    "teamId": expected_team_id,
                    "viewerId": expected_viewer_id,
                    "after": after,
                },
                repeat_safe=True,
            )
            viewer = _object_get(data, "viewer")
            organization = _object_get(data, "organization")
            team = _object_get(data, "team")
            membership = _object_get(team, "membership")
            viewer_admin = viewer.get("admin")
            viewer_guest = viewer.get("guest")
            viewer_active = viewer.get("active")
            if (
                not isinstance(viewer_admin, bool)
                or not isinstance(viewer_guest, bool)
                or not isinstance(viewer_active, bool)
            ):
                raise LinearContractError("Linear viewer authority fields must be boolean")
            current_destination = DestinationIdentity(
                workspace_id=_text_get(organization, "id"),
                viewer_id=_text_get(viewer, "id"),
                team_id=_text_get(team, "id"),
                viewer_is_admin=viewer_admin,
                viewer_is_guest=viewer_guest,
                viewer_is_active=viewer_active and membership.get("archivedAt") is None,
            )
            if (
                (expected_workspace_id is not None and current_destination.workspace_id != expected_workspace_id)
                or current_destination.viewer_id != expected_viewer_id
                or current_destination.team_id != expected_team_id
            ):
                raise LinearContractError("Authenticated Linear destination differs from the exact approved IDs")
            current_destination.mutation_authority_require()
            if destination is not None and destination != current_destination:
                raise LinearContractError("Linear destination changed while workflow configuration was read")
            destination = current_destination
            connection = _object_get(team, "states")
            workflow_status_list.extend(self._status_list_get(connection))
            after = _next_cursor_get(connection)
            if after is None:
                break

        project_status_list: list[StatusDefinition] = []
        after = None
        while True:
            data = self._transport.execute(
                operation_name="LinearAgentProjectStatuses",
                document=_PROJECT_STATUS_QUERY,
                variables={"after": after},
                repeat_safe=True,
            )
            organization = _object_get(data, "organization")
            workspace_id = _text_get(organization, "id")
            if workspace_id != destination.workspace_id:
                raise LinearContractError("Linear workspace changed while Project statuses were read")
            connection = _object_get(data, "projectStatuses")
            project_status_list.extend(self._status_list_get(connection))
            after = _next_cursor_get(connection)
            if after is None:
                break
        if destination is None:
            raise LinearContractError("Linear destination read produced no page")
        return WorkflowConfigurationGraphQLRead(
            destination=destination,
            issue_status_list=workflow_status_list,
            project_status_list=project_status_list,
        )

    def plan(
        self,
        *,
        expected_workspace_id: str | None,
        expected_viewer_id: str,
        expected_team_id: str,
        label_list: list[LinearLabel],
    ) -> ConfigurationPlan:
        """Build an exact configuration plan including MCP-read labels.

        Args:
            expected_workspace_id: User-approved exact workspace ID, or ``None``
                for read-only initial discovery.
            expected_viewer_id: User-approved exact viewer ID.
            expected_team_id: User-approved exact team ID.
            label_list: Fully paginated labels read through official MCP.

        Returns:
            Exact missing configuration and conflicts.
        """

        current = self.read(
            expected_workspace_id=expected_workspace_id,
            expected_viewer_id=expected_viewer_id,
            expected_team_id=expected_team_id,
        )
        return self._reconciler.plan_get(
            WorkflowConfigurationSnapshot(
                destination=current.destination,
                issue_status_list=current.issue_status_list,
                project_status_list=current.project_status_list,
                label_list=list(label_list),
            )
        )

    def missing_statuses_create(
        self,
        *,
        expected_workspace_id: str,
        expected_viewer_id: str,
        expected_team_id: str,
        approved_plan: ConfigurationPlan,
    ) -> None:
        """Create only the exact missing GraphQL-owned statuses from one approved plan.

        Args:
            expected_workspace_id: Exact workspace ID.
            expected_viewer_id: Exact viewer ID.
            expected_team_id: Exact team ID.
            approved_plan: Previewed conflict-free plan.
        """

        if not approved_plan.can_mutate():
            raise LinearContractError("Conflicting workflow configuration cannot be applied")
        approved_plan.status_identifier_require()
        approved_destination = approved_plan.destination
        if (
            approved_destination.workspace_id != expected_workspace_id
            or approved_destination.viewer_id != expected_viewer_id
            or approved_destination.team_id != expected_team_id
        ):
            raise LinearContractError("Approved plan destination differs from apply destination")
        current = self.read(
            expected_workspace_id=expected_workspace_id,
            expected_viewer_id=expected_viewer_id,
            expected_team_id=expected_team_id,
        )
        current_plan = self._reconciler.plan_get(
            WorkflowConfigurationSnapshot(
                destination=current.destination,
                issue_status_list=current.issue_status_list,
                project_status_list=current.project_status_list,
                label_list=[],
            )
        )
        current_status_plan = ConfigurationPlan(
            destination=current_plan.destination,
            issue_status_create_list=current_plan.issue_status_create_list,
            project_status_create_list=current_plan.project_status_create_list,
            label_create_list=[],
            conflict_list=current_plan.conflict_list,
        )
        approved_status_plan = ConfigurationPlan(
            destination=approved_plan.destination,
            issue_status_create_list=approved_plan.issue_status_create_list,
            project_status_create_list=approved_plan.project_status_create_list,
            label_create_list=[],
            conflict_list=approved_plan.conflict_list,
        )
        current_status_plan.subset_require(approved_status_plan)
        approved_issue_status_by_name_map = {item.name: item for item in approved_plan.issue_status_create_list}
        for status in current_plan.issue_status_create_list:
            approved_status = approved_issue_status_by_name_map[status.name]
            self._create_once(
                operation_name="LinearAgentWorkflowStateCreate",
                document=_WORKFLOW_STATE_CREATE,
                variables={
                    "input": {
                        "id": approved_status.id,
                        "teamId": expected_team_id,
                        "name": status.name,
                        "type": status.category,
                        "color": status.color,
                        "description": status.description,
                        "position": status.position,
                    }
                },
                result_key="workflowStateCreate",
            )
        approved_project_status_by_name_map = {item.name: item for item in approved_plan.project_status_create_list}
        for status in current_plan.project_status_create_list:
            approved_status = approved_project_status_by_name_map[status.name]
            self._create_once(
                operation_name="LinearAgentProjectStatusCreate",
                document=_PROJECT_STATUS_CREATE,
                variables={
                    "input": {
                        "id": approved_status.id,
                        "name": status.name,
                        "type": status.category,
                        "color": status.color,
                        "description": status.description,
                        "position": status.position,
                    }
                },
                result_key="projectStatusCreate",
            )
        current = self.read(
            expected_workspace_id=expected_workspace_id,
            expected_viewer_id=expected_viewer_id,
            expected_team_id=expected_team_id,
        )
        readback = self._reconciler.plan_get(
            WorkflowConfigurationSnapshot(
                destination=current.destination,
                issue_status_list=current.issue_status_list,
                project_status_list=current.project_status_list,
                label_list=[],
            )
        )
        if readback.conflict_list or readback.issue_status_create_list or readback.project_status_create_list:
            raise LinearContractError("Linear status read-back differs from the approved configuration plan")

    def _create_once(
        self,
        *,
        operation_name: str,
        document: str,
        variables: dict[str, object],
        result_key: str,
    ) -> None:
        """Execute one non-repeatable create with an approved retry-stable identity.

        Args:
            operation_name: Exact GraphQL operation name.
            document: Static GraphQL mutation.
            variables: Validated mutation variables.
            result_key: Expected mutation result key.
        """

        data = self._transport.execute(
            operation_name=operation_name,
            document=document,
            variables=variables,
            repeat_safe=False,
        )
        result = _object_get(data, result_key)
        if result.get("success") is not True:
            raise LinearTransportError("Linear mutation did not confirm success")

    def _status_list_get(self, connection: dict[str, object]) -> list[StatusDefinition]:
        """Parse one status connection page.

        Args:
            connection: GraphQL connection object.

        Returns:
            Parsed status definitions.
        """

        node_list = connection.get("nodes")
        if not isinstance(node_list, list) or any(not isinstance(item, dict) for item in node_list):
            raise LinearContractError("Linear status connection nodes have another shape")
        result: list[StatusDefinition] = []
        for item in node_list:
            description = item.get("description")
            position = item.get("position")
            result.append(
                StatusDefinition(
                    id=_text_get(item, "id"),
                    name=_text_get(item, "name"),
                    category=_text_get(item, "type"),
                    color=_text_get(item, "color"),
                    description=description if isinstance(description, str) else "",
                    position=(
                        position if isinstance(position, (int, float)) and not isinstance(position, bool) else 0.0
                    ),
                )
            )
        return result


def _object_get(payload: dict[str, object], name: str) -> dict[str, object]:
    """Return one required object field.

    Args:
        payload: Parent payload.
        name: Exact field name.

    Returns:
        The object value.
    """

    value = payload.get(name)
    if not isinstance(value, dict):
        raise LinearContractError(f"Linear field {name} must be an object")
    return value


def _text_get(payload: dict[str, object], name: str) -> str:
    """Return one required non-empty text field.

    Args:
        payload: Parent payload.
        name: Exact field name.

    Returns:
        The text value.
    """

    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise LinearContractError(f"Linear field {name} must be non-empty text")
    return value


def _next_cursor_get(connection: dict[str, object]) -> str | None:
    """Return the next cursor while rejecting incomplete pagination metadata.

    Args:
        connection: GraphQL connection object.

    Returns:
        Next cursor, or ``None`` at completion.
    """

    page_info = _object_get(connection, "pageInfo")
    has_next = page_info.get("hasNextPage")
    if not isinstance(has_next, bool):
        raise LinearContractError("Linear field hasNextPage must be boolean")
    cursor = page_info.get("endCursor")
    if has_next:
        if not isinstance(cursor, str) or not cursor:
            raise LinearContractError("Linear paginated connection omitted its next cursor")
        return cursor
    if cursor is not None and not isinstance(cursor, str):
        raise LinearContractError("Linear pagination end cursor has another shape")
    return None
