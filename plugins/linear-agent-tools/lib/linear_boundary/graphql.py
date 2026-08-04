"""Minimal typed GraphQL owner for Linear workflow-configuration gaps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from linear_boundary.model import (
    ConfigurationPlan,
    DestinationIdentity,
    LinearContractError,
    LinearLabel,
    StatusDefinition,
    WorkflowConfigurationSnapshot,
    configuration_plan_build,
    configuration_plan_status_identifiers_require,
    configuration_plan_subset_require,
)
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
class GraphQLConfigurationRead:
    """Contain the GraphQL-owned configuration and guarded destination."""

    destination: DestinationIdentity
    issue_status_list: tuple[StatusDefinition, ...]
    project_status_list: tuple[StatusDefinition, ...]


class LinearWorkflowConfigurationGraphQL:
    """Read and create only missing issue and Project workflow statuses."""

    def __init__(self, transport: LinearGraphQLTransport) -> None:
        """Initialize the exact transport dependency.

        Args:
            transport: One-process secret-bearing GraphQL transport.
        """

        self._transport = transport

    def read(
        self,
        *,
        expected_workspace_id: str | None,
        expected_viewer_id: str,
        expected_team_id: str,
    ) -> GraphQLConfigurationRead:
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
            current_destination = DestinationIdentity(
                workspace_id=_text_get(organization, "id"),
                viewer_id=_text_get(viewer, "id"),
                team_id=_text_get(team, "id"),
                viewer_is_admin=_bool_get(viewer, "admin"),
                viewer_is_guest=_bool_get(viewer, "guest"),
                viewer_is_active=_bool_get(viewer, "active")
                and membership.get("archivedAt") is None,
            )
            if (
                (
                    expected_workspace_id is not None
                    and current_destination.workspace_id != expected_workspace_id
                )
                or current_destination.viewer_id != expected_viewer_id
                or current_destination.team_id != expected_team_id
            ):
                raise LinearContractError(
                    "Authenticated Linear destination differs from the exact approved IDs"
                )
            current_destination.mutation_authority_require()
            if destination is not None and destination != current_destination:
                raise LinearContractError(
                    "Linear destination changed while workflow configuration was read"
                )
            destination = current_destination
            connection = _object_get(team, "states")
            workflow_status_list.extend(_status_list_get(connection))
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
                raise LinearContractError(
                    "Linear workspace changed while Project statuses were read"
                )
            connection = _object_get(data, "projectStatuses")
            project_status_list.extend(_status_list_get(connection))
            after = _next_cursor_get(connection)
            if after is None:
                break
        if destination is None:
            raise LinearContractError("Linear destination read produced no page")
        return GraphQLConfigurationRead(
            destination=destination,
            issue_status_list=tuple(workflow_status_list),
            project_status_list=tuple(project_status_list),
        )

    def plan(
        self,
        *,
        expected_workspace_id: str | None,
        expected_viewer_id: str,
        expected_team_id: str,
        label_list: Iterable[LinearLabel] = (),
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
        return configuration_plan_build(
            WorkflowConfigurationSnapshot(
                destination=current.destination,
                issue_status_list=current.issue_status_list,
                project_status_list=current.project_status_list,
                label_list=tuple(label_list),
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

        if not approved_plan.mutation_allowed():
            raise LinearContractError(
                "Conflicting workflow configuration cannot be applied"
            )
        configuration_plan_status_identifiers_require(approved_plan)
        approved_destination = approved_plan.destination
        if (
            approved_destination.workspace_id != expected_workspace_id
            or approved_destination.viewer_id != expected_viewer_id
            or approved_destination.team_id != expected_team_id
        ):
            raise LinearContractError(
                "Approved plan destination differs from apply destination"
            )
        current = self.read(
            expected_workspace_id=expected_workspace_id,
            expected_viewer_id=expected_viewer_id,
            expected_team_id=expected_team_id,
        )
        current_plan = configuration_plan_build(
            WorkflowConfigurationSnapshot(
                destination=current.destination,
                issue_status_list=current.issue_status_list,
                project_status_list=current.project_status_list,
                label_list=(),
            )
        )
        configuration_plan_subset_require(
            ConfigurationPlan(
                destination=current_plan.destination,
                issue_status_create_list=current_plan.issue_status_create_list,
                project_status_create_list=current_plan.project_status_create_list,
                label_create_list=(),
                conflict_list=current_plan.conflict_list,
            ),
            ConfigurationPlan(
                destination=approved_plan.destination,
                issue_status_create_list=approved_plan.issue_status_create_list,
                project_status_create_list=approved_plan.project_status_create_list,
                label_create_list=(),
                conflict_list=approved_plan.conflict_list,
            ),
        )
        approved_issue_status_by_name = {
            item.name: item for item in approved_plan.issue_status_create_list
        }
        for status in current_plan.issue_status_create_list:
            approved_status = approved_issue_status_by_name[status.name]
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
        approved_project_status_by_name = {
            item.name: item for item in approved_plan.project_status_create_list
        }
        for status in current_plan.project_status_create_list:
            approved_status = approved_project_status_by_name[status.name]
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
        readback = configuration_plan_build(
            WorkflowConfigurationSnapshot(
                destination=current.destination,
                issue_status_list=current.issue_status_list,
                project_status_list=current.project_status_list,
                label_list=(),
            )
        )
        if (
            readback.conflict_list
            or readback.issue_status_create_list
            or readback.project_status_create_list
        ):
            raise LinearContractError(
                "Linear status read-back differs from the approved configuration plan"
            )

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


def _bool_get(payload: dict[str, object], name: str) -> bool:
    """Return one required boolean field.

    Args:
        payload: Parent payload.
        name: Exact field name.

    Returns:
        The boolean value.
    """

    value = payload.get(name)
    if not isinstance(value, bool):
        raise LinearContractError(f"Linear field {name} must be boolean")
    return value


def _status_list_get(connection: dict[str, object]) -> list[StatusDefinition]:
    """Parse one status connection page.

    Args:
        connection: GraphQL connection object.

    Returns:
        Parsed status definitions.
    """

    node_list = connection.get("nodes")
    if not isinstance(node_list, list) or any(
        not isinstance(item, dict) for item in node_list
    ):
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
                    position
                    if isinstance(position, (int, float))
                    and not isinstance(position, bool)
                    else 0.0
                ),
            )
        )
    return result


def _next_cursor_get(connection: dict[str, object]) -> str | None:
    """Return the next cursor while rejecting incomplete pagination metadata.

    Args:
        connection: GraphQL connection object.

    Returns:
        Next cursor, or ``None`` at completion.
    """

    page_info = _object_get(connection, "pageInfo")
    has_next = _bool_get(page_info, "hasNextPage")
    cursor = page_info.get("endCursor")
    if has_next:
        if not isinstance(cursor, str) or not cursor:
            raise LinearContractError(
                "Linear paginated connection omitted its next cursor"
            )
        return cursor
    if cursor is not None and not isinstance(cursor, str):
        raise LinearContractError("Linear pagination end cursor has another shape")
    return None
