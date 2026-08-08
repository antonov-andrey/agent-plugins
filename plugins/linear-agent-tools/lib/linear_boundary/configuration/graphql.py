"""Minimal typed GraphQL owner for Linear workflow-configuration gaps."""

from __future__ import annotations

from dataclasses import dataclass

from linear_boundary.configuration.model import (
    ConfigurationPlan,
    DestinationIdentity,
    GitStatusAutomation,
    LinearLabel,
    StatusDefinition,
    WorkflowConfigurationSnapshot,
)
from linear_boundary.configuration.reconciliation import WorkflowConfigurationReconciler
from linear_boundary.contract import LinearContractError, uuid_validate
from linear_boundary.transport import LinearGraphQLTransport, LinearTransportError

_IDENTITY_AND_WORKFLOW_QUERY = """
query LinearAgentWorkflowConfiguration($teamId: String!, $viewerId: String!, $after: String) {
  viewer { id admin guest active }
  organization { id }
  team(id: $teamId) {
    id
    membership(userId: $viewerId) { id archivedAt }
    states(first: 100, after: $after, includeArchived: false) {
      nodes {
        id name type color description position
        issues(first: 1, includeArchived: false) { nodes { id } }
      }
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

_GIT_STATUS_AUTOMATION_QUERY = """
query LinearAgentGitStatusAutomations($teamId: String!, $after: String) {
  team(id: $teamId) {
    id
    gitAutomationStates(first: 100, after: $after, includeArchived: false) {
      nodes { id }
      pageInfo { hasNextPage endCursor }
    }
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

_WORKFLOW_STATE_UPDATE = """
mutation LinearAgentWorkflowStateUpdate($id: String!, $input: WorkflowStateUpdateInput!) {
  workflowStateUpdate(id: $id, input: $input) {
    success
    workflowState { id name type color description position }
  }
}
"""

_WORKFLOW_STATE_ARCHIVE = """
mutation LinearAgentWorkflowStateArchive($id: String!) {
  workflowStateArchive(id: $id) {
    success
    entity { id name archivedAt }
  }
}
"""

_WORKFLOW_STATE_ARCHIVE_READBACK = """
query LinearAgentWorkflowStateArchiveReadback($id: String!) {
  workflowState(id: $id) { id name archivedAt }
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

_GIT_STATUS_AUTOMATION_DELETE = """
mutation LinearAgentGitStatusAutomationDelete($id: String!) {
  gitAutomationStateDelete(id: $id) {
    entityId
    lastSyncId
    success
  }
}
"""


@dataclass(frozen=True, slots=True)
class WorkflowConfigurationGraphQLRead:
    """Contain the GraphQL-owned configuration and guarded destination."""

    destination: DestinationIdentity
    issue_status_list: list[StatusDefinition]
    active_issue_status_id_list: list[str]
    project_status_list: list[StatusDefinition]
    git_status_automation_list: list[GitStatusAutomation]

    def __post_init__(self) -> None:
        """Detach one trusted typed GraphQL read from pagination builders."""

        if not isinstance(self.destination, DestinationIdentity):
            raise LinearContractError("Linear GraphQL read destination has another shape")
        if not isinstance(self.issue_status_list, list) or any(
            not isinstance(item, StatusDefinition) for item in self.issue_status_list
        ):
            raise LinearContractError("Linear GraphQL issue status list has another shape")
        if (
            not isinstance(self.active_issue_status_id_list, list)
            or any(not isinstance(item, str) for item in self.active_issue_status_id_list)
            or self.active_issue_status_id_list != sorted(self.active_issue_status_id_list)
            or len(self.active_issue_status_id_list) != len(set(self.active_issue_status_id_list))
        ):
            raise LinearContractError("Linear GraphQL active issue status IDs have another shape")
        issue_status_id_set = {item.id for item in self.issue_status_list}
        if any(item not in issue_status_id_set for item in self.active_issue_status_id_list):
            raise LinearContractError("Linear GraphQL active issue status ID is absent from the status list")
        if not isinstance(self.project_status_list, list) or any(
            not isinstance(item, StatusDefinition) for item in self.project_status_list
        ):
            raise LinearContractError("Linear GraphQL Project status list has another shape")
        if not isinstance(self.git_status_automation_list, list) or any(
            not isinstance(item, GitStatusAutomation) for item in self.git_status_automation_list
        ):
            raise LinearContractError("Linear GraphQL Git status automation list has another shape")
        object.__setattr__(self, "issue_status_list", list(self.issue_status_list))
        object.__setattr__(self, "active_issue_status_id_list", list(self.active_issue_status_id_list))
        object.__setattr__(self, "project_status_list", list(self.project_status_list))
        object.__setattr__(
            self,
            "git_status_automation_list",
            sorted(self.git_status_automation_list, key=lambda item: item.id),
        )


class LinearWorkflowConfigurationGraphQL:
    """Read and reconcile GraphQL-owned statuses and Git automation rules."""

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
        active_issue_status_id_set: set[str] = set()
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
            page_status_list, page_active_issue_status_id_list = _issue_status_connection_parse(connection)
            workflow_status_list.extend(page_status_list)
            active_issue_status_id_set.update(page_active_issue_status_id_list)
            after = _next_cursor_get(connection)
            if after is None:
                break

        if destination is None:
            raise LinearContractError("Linear destination read produced no page")

        git_status_automation_list: list[GitStatusAutomation] = []
        after = None
        while True:
            data = self._transport.execute(
                operation_name="LinearAgentGitStatusAutomations",
                document=_GIT_STATUS_AUTOMATION_QUERY,
                variables={"teamId": expected_team_id, "after": after},
                repeat_safe=True,
            )
            team = _object_get(data, "team")
            if _text_get(team, "id") != destination.team_id:
                raise LinearContractError("Linear team changed while Git status automations were read")
            connection = _object_get(team, "gitAutomationStates")
            git_status_automation_list.extend(GitStatusAutomation.list_from_graphql_connection(connection))
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
            project_status_list.extend(StatusDefinition.list_from_graphql_connection(connection))
            after = _next_cursor_get(connection)
            if after is None:
                break
        return WorkflowConfigurationGraphQLRead(
            destination=destination,
            issue_status_list=workflow_status_list,
            active_issue_status_id_list=sorted(active_issue_status_id_set),
            project_status_list=project_status_list,
            git_status_automation_list=git_status_automation_list,
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
                active_issue_status_id_list=current.active_issue_status_id_list,
                project_status_list=current.project_status_list,
                label_list=list(label_list),
                git_status_automation_list=current.git_status_automation_list,
            )
        )

    def approved_configuration_apply(
        self,
        *,
        expected_workspace_id: str,
        expected_viewer_id: str,
        expected_team_id: str,
        approved_plan: ConfigurationPlan,
    ) -> None:
        """Apply the exact remaining GraphQL-owned part of one approved plan.

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
                active_issue_status_id_list=current.active_issue_status_id_list,
                project_status_list=current.project_status_list,
                label_list=[],
                git_status_automation_list=current.git_status_automation_list,
            )
        )
        current_graphql_plan = ConfigurationPlan(
            destination=current_plan.destination,
            issue_status_create_list=current_plan.issue_status_create_list,
            issue_status_update_list=current_plan.issue_status_update_list,
            issue_status_archive_list=current_plan.issue_status_archive_list,
            project_status_create_list=current_plan.project_status_create_list,
            label_create_list=[],
            git_status_automation_delete_list=current_plan.git_status_automation_delete_list,
            conflict_list=current_plan.conflict_list,
        )
        approved_graphql_plan = ConfigurationPlan(
            destination=approved_plan.destination,
            issue_status_create_list=approved_plan.issue_status_create_list,
            issue_status_update_list=approved_plan.issue_status_update_list,
            issue_status_archive_list=approved_plan.issue_status_archive_list,
            project_status_create_list=approved_plan.project_status_create_list,
            label_create_list=[],
            git_status_automation_delete_list=approved_plan.git_status_automation_delete_list,
            conflict_list=approved_plan.conflict_list,
        )
        current_graphql_plan.subset_require(approved_graphql_plan)
        for automation in current_plan.git_status_automation_delete_list:
            self._delete_once(automation.id)
        approved_issue_status_update_by_id_map = {item.id: item for item in approved_plan.issue_status_update_list}
        for status in current_plan.issue_status_update_list:
            approved_status = approved_issue_status_update_by_id_map.get(status.id)
            if approved_status != status:
                raise LinearContractError("Current status migration differs from the exact approved definition")
            self._workflow_state_update_once(approved_status)
        approved_issue_status_archive_by_id_map = {item.id: item for item in approved_plan.issue_status_archive_list}
        for status in current_plan.issue_status_archive_list:
            approved_status = approved_issue_status_archive_by_id_map.get(status.id)
            if approved_status != status:
                raise LinearContractError("Current status archive differs from the exact approved definition")
            self._workflow_state_archive_once(approved_status)
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
        _status_update_readback_require(
            approved_status_list=approved_plan.issue_status_update_list,
            current_status_list=current.issue_status_list,
        )
        for status in approved_plan.issue_status_archive_list:
            self._workflow_state_archive_readback_require(status)
        readback = self._reconciler.plan_get(
            WorkflowConfigurationSnapshot(
                destination=current.destination,
                issue_status_list=current.issue_status_list,
                active_issue_status_id_list=current.active_issue_status_id_list,
                project_status_list=current.project_status_list,
                label_list=[],
                git_status_automation_list=current.git_status_automation_list,
            )
        )
        if (
            readback.conflict_list
            or readback.issue_status_create_list
            or readback.issue_status_update_list
            or readback.issue_status_archive_list
            or readback.project_status_create_list
            or readback.git_status_automation_delete_list
        ):
            raise LinearContractError("Linear workflow read-back differs from the approved configuration plan")

    def _delete_once(self, identifier: str) -> None:
        """Delete one exact approved Git status automation rule."""

        uuid_validate(identifier, label="Git status automation deletion ID")
        data = self._transport.execute(
            operation_name="LinearAgentGitStatusAutomationDelete",
            document=_GIT_STATUS_AUTOMATION_DELETE,
            variables={"id": identifier},
            repeat_safe=False,
        )
        result = _object_get(data, "gitAutomationStateDelete")
        if result.get("success") is not True or result.get("entityId") != identifier:
            raise LinearTransportError("Linear Git status automation deletion did not confirm exact success")

    def _workflow_state_update_once(self, status: StatusDefinition) -> None:
        """Update one exact historical workflow state without replacing its identity."""

        uuid_validate(status.id, label="Workflow status update ID")
        data = self._transport.execute(
            operation_name="LinearAgentWorkflowStateUpdate",
            document=_WORKFLOW_STATE_UPDATE,
            variables={
                "id": status.id,
                "input": {
                    "name": status.name,
                    "color": status.color,
                    "description": status.description,
                    "position": status.position,
                },
            },
            repeat_safe=False,
        )
        result = _object_get(data, "workflowStateUpdate")
        if set(result) != {"success", "workflowState"}:
            raise LinearTransportError("Linear workflow status update response has another shape")
        try:
            returned_status = StatusDefinition.from_graphql_node(_object_get(result, "workflowState"))
        except LinearContractError as error:
            raise LinearTransportError("Linear workflow status update response has another shape") from error
        if result["success"] is not True or returned_status != status:
            raise LinearTransportError("Linear workflow status update differs from the full approved definition")

    def _workflow_state_archive_once(self, status: StatusDefinition) -> None:
        """Archive one exact inactive legacy alias while retaining its provider identity."""

        uuid_validate(status.id, label="Workflow status archive ID")
        data = self._transport.execute(
            operation_name="LinearAgentWorkflowStateArchive",
            document=_WORKFLOW_STATE_ARCHIVE,
            variables={"id": status.id},
            repeat_safe=False,
        )
        result = _object_get(data, "workflowStateArchive")
        if set(result) != {"success", "entity"}:
            raise LinearTransportError("Linear workflow status archive response has another shape")
        self._archived_status_identity_require(_object_get(result, "entity"), status=status)
        if result["success"] is not True:
            raise LinearTransportError("Linear workflow status archive did not confirm success")

    def _workflow_state_archive_readback_require(self, status: StatusDefinition) -> None:
        """Read back one archived legacy alias by its preserved natural identity."""

        data = self._transport.execute(
            operation_name="LinearAgentWorkflowStateArchiveReadback",
            document=_WORKFLOW_STATE_ARCHIVE_READBACK,
            variables={"id": status.id},
            repeat_safe=True,
        )
        self._archived_status_identity_require(_object_get(data, "workflowState"), status=status)

    @staticmethod
    def _archived_status_identity_require(payload: dict[str, object], *, status: StatusDefinition) -> None:
        """Require one archived status to retain its exact ID and historical name."""

        if set(payload) != {"id", "name", "archivedAt"}:
            raise LinearTransportError("Linear archived workflow status response has another shape")
        if payload["id"] != status.id or payload["name"] != status.name:
            raise LinearTransportError("Linear archived workflow status changed identity")
        archived_at = payload["archivedAt"]
        if not isinstance(archived_at, str) or not archived_at:
            raise LinearTransportError("Linear workflow status archive is absent on readback")

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


def _issue_status_connection_parse(
    connection: dict[str, object],
) -> tuple[list[StatusDefinition], list[str]]:
    """Parse one issue-status page together with current non-archived issue use."""

    if set(connection) != {"nodes", "pageInfo"}:
        raise LinearContractError("Linear issue status connection has another shape")
    node_list = connection["nodes"]
    if not isinstance(node_list, list) or any(not isinstance(item, dict) for item in node_list):
        raise LinearContractError("Linear issue status connection nodes have another shape")
    status_list: list[StatusDefinition] = []
    active_status_id_list: list[str] = []
    status_field_set = {"id", "name", "type", "color", "description", "position"}
    for node in node_list:
        if set(node) != {*status_field_set, "issues"}:
            raise LinearContractError("Linear issue status node has another shape")
        status_payload = {name: node[name] for name in status_field_set}
        status = StatusDefinition.from_graphql_node(status_payload)
        issues = _object_get(node, "issues")
        if set(issues) != {"nodes"}:
            raise LinearContractError("Linear workflow status issue connection has another shape")
        issue_list = issues["nodes"]
        if not isinstance(issue_list, list) or len(issue_list) > 1:
            raise LinearContractError("Linear workflow status issue probe has another shape")
        for issue in issue_list:
            if not isinstance(issue, dict) or set(issue) != {"id"}:
                raise LinearContractError("Linear workflow status issue identity has another shape")
            uuid_validate(issue["id"], label="Workflow status issue ID")
        if issue_list:
            active_status_id_list.append(status.id)
        status_list.append(status)
    return status_list, active_status_id_list


def _status_update_readback_require(
    *,
    approved_status_list: list[StatusDefinition],
    current_status_list: list[StatusDefinition],
) -> None:
    """Require every in-place migration at its preserved ID and exact definition."""

    current_status_list_by_id_map: dict[str, list[StatusDefinition]] = {}
    for status in current_status_list:
        current_status_list_by_id_map.setdefault(status.id, []).append(status)
    for approved_status in approved_status_list:
        if current_status_list_by_id_map.get(approved_status.id, []) != [approved_status]:
            raise LinearContractError(
                f"Linear workflow status {approved_status.name} read-back differs from its preserved approved identity"
            )


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
