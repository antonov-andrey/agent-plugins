"""Closed Linear identity and workflow-configuration models."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import re
import uuid

from linear_boundary.contract import (
    LinearContractError,
    single_line_text_validate,
    uuid_validate,
)

_HEX_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")


@dataclass(frozen=True, slots=True)
class DestinationIdentity:
    """Bind one exact authenticated workspace, viewer and team."""

    workspace_id: str
    viewer_id: str
    team_id: str
    viewer_is_admin: bool
    viewer_is_guest: bool
    viewer_is_active: bool

    def __post_init__(self) -> None:
        """Validate all exact destination identities."""

        uuid_validate(self.workspace_id, label="Workspace ID")
        uuid_validate(self.viewer_id, label="Viewer ID")
        uuid_validate(self.team_id, label="Team ID")
        boolean_by_field_name_map = {
            "viewer_is_admin": self.viewer_is_admin,
            "viewer_is_guest": self.viewer_is_guest,
            "viewer_is_active": self.viewer_is_active,
        }
        for field_name, value in boolean_by_field_name_map.items():
            if not isinstance(value, bool):
                raise LinearContractError(f"{field_name} must be boolean")

    def mutation_authority_require(self) -> None:
        """Require the authenticated viewer to be an active non-guest admin."""

        if not self.viewer_is_admin or self.viewer_is_guest or not self.viewer_is_active:
            raise LinearContractError("Authenticated Linear viewer is not an active non-guest administrator")


def _uuid_v4_validate(value: str, *, label: str) -> str:
    """Return one canonical UUID v4 accepted by Linear create inputs.

    Args:
        value: Candidate identifier.
        label: Diagnostic object name.

    Returns:
        Validated lowercase identifier.
    """

    uuid_validate(value, label=label)
    try:
        identifier = uuid.UUID(value)
    except ValueError as error:
        raise LinearContractError(f"{label} must be one UUID v4") from error
    if identifier.version != 4 or str(identifier) != value:
        raise LinearContractError(f"{label} must be one UUID v4")
    return value


@dataclass(frozen=True, slots=True)
class StatusDefinition:
    """Describe one existing or desired Linear status."""

    id: str
    name: str
    category: str
    color: str
    description: str
    position: float

    def __post_init__(self) -> None:
        """Validate one status definition."""

        if self.id:
            uuid_validate(self.id, label="Status ID")
        single_line_text_validate(self.name, label="Status name")
        single_line_text_validate(self.category, label="Status category")
        if _HEX_COLOR_PATTERN.fullmatch(self.color) is None:
            raise LinearContractError("Status color must use #RRGGBB")
        if not isinstance(self.description, str) or "\x00" in self.description:
            raise LinearContractError("Status description must be text")
        if (
            isinstance(self.position, bool)
            or not isinstance(self.position, (int, float))
            or not math.isfinite(self.position)
        ):
            raise LinearContractError("Status position must be one finite number")

    def create_identifier_allocate(self) -> "StatusDefinition":
        """Return this status with one stable UUID v4 create identity.

        Returns:
            This status or a copy with a new create identity.
        """

        if self.id:
            self.create_identifier_require()
            return self
        return replace(self, id=str(uuid.uuid4()))

    def create_identifier_require(self) -> None:
        """Require a stable UUID v4 before a non-repeatable create mutation."""

        _uuid_v4_validate(self.id, label=f"Status {self.name} create ID")

    def payload(self) -> dict[str, object]:
        """Return one canonical status payload.

        Returns:
            JSON-ready status object.
        """

        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "color": self.color,
            "description": self.description,
            "position": self.position,
        }

    @classmethod
    def from_graphql_node(cls, value: object) -> "StatusDefinition":
        """Parse one complete status selected by the owned GraphQL documents.

        Args:
            value: Candidate status node.

        Returns:
            Exact typed status definition.
        """

        expected = {"id", "name", "type", "color", "description", "position"}
        if not isinstance(value, dict) or set(value) != expected:
            raise LinearContractError("Linear status node has another shape")
        for name in ("id", "name", "type", "color"):
            if not isinstance(value[name], str) or not value[name]:
                raise LinearContractError(f"Linear field {name} must be non-empty text")
        description = value["description"]
        if description is not None and not isinstance(description, str):
            raise LinearContractError("Linear status description must be text or null")
        return cls(
            id=value["id"],
            name=value["name"],
            category=value["type"],
            color=value["color"],
            description="" if description is None else description,
            position=value["position"],
        )

    @classmethod
    def list_from_payload(cls, value: object) -> list["StatusDefinition"]:
        """Parse one strict status list.

        Args:
            value: Candidate JSON value.

        Returns:
            Typed status definitions.
        """

        expected = {"id", "name", "category", "color", "description", "position"}
        status_list: list[StatusDefinition] = []
        for item in _object_list_parse(value, label="status list"):
            if set(item) != expected:
                raise LinearContractError("Configuration plan status has another shape")
            status_list.append(cls(**item))
        return status_list

    @classmethod
    def list_from_graphql_connection(cls, connection: dict[str, object]) -> list["StatusDefinition"]:
        """Parse one strict Linear status connection page.

        Args:
            connection: GraphQL connection object.

        Returns:
            Typed status definitions.
        """

        if set(connection) != {"nodes", "pageInfo"}:
            raise LinearContractError("Linear status connection has another shape")
        node_list = connection["nodes"]
        if not isinstance(node_list, list) or any(not isinstance(item, dict) for item in node_list):
            raise LinearContractError("Linear status connection nodes have another shape")
        return [cls.from_graphql_node(item) for item in node_list]


@dataclass(frozen=True, slots=True)
class LinearLabel:
    """Describe one existing or desired issue label."""

    id: str
    name: str
    color: str
    description: str

    def __post_init__(self) -> None:
        """Validate one label definition."""

        if self.id:
            uuid_validate(self.id, label="Label ID")
        single_line_text_validate(self.name, label="Label name")
        if _HEX_COLOR_PATTERN.fullmatch(self.color) is None:
            raise LinearContractError("Label color must use #RRGGBB")
        if not isinstance(self.description, str) or any(
            character in self.description for character in ("\x00", "\n", "\r")
        ):
            raise LinearContractError("Label description must be single-line text")

    def payload(self) -> dict[str, str]:
        """Return one canonical label payload.

        Returns:
            JSON-ready label object.
        """

        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "description": self.description,
        }

    @classmethod
    def list_from_payload(cls, value: object) -> list["LinearLabel"]:
        """Parse one strict label list.

        Args:
            value: Candidate JSON value.

        Returns:
            Typed labels.
        """

        expected = {"id", "name", "color", "description"}
        label_list: list[LinearLabel] = []
        for item in _object_list_parse(value, label="label list"):
            if set(item) != expected:
                raise LinearContractError("Configuration plan label has another shape")
            label_list.append(cls(**item))
        return label_list


@dataclass(frozen=True, slots=True)
class GitStatusAutomation:
    """Identify one team-level Git status automation rule."""

    id: str

    def __post_init__(self) -> None:
        """Validate the natural Linear identity used for exact deletion."""

        uuid_validate(self.id, label="Git status automation ID")

    def payload(self) -> dict[str, str]:
        """Return one canonical deletion identity payload."""

        return {"id": self.id}

    @classmethod
    def list_from_payload(cls, value: object) -> list["GitStatusAutomation"]:
        """Parse one strict canonical automation list."""

        expected = {"id"}
        automation_list: list[GitStatusAutomation] = []
        for item in _object_list_parse(value, label="Git status automation list"):
            if set(item) != expected:
                raise LinearContractError("Configuration plan Git status automation has another shape")
            automation_list.append(cls(**item))
        return automation_list

    @classmethod
    def list_from_graphql_connection(cls, connection: dict[str, object]) -> list["GitStatusAutomation"]:
        """Parse one strict page of Linear Git automation rules."""

        node_list = connection.get("nodes")
        if not isinstance(node_list, list) or any(not isinstance(item, dict) for item in node_list):
            raise LinearContractError("Linear Git status automation connection nodes have another shape")
        automation_list: list[GitStatusAutomation] = []
        for item in node_list:
            identifier = item.get("id")
            if not isinstance(identifier, str):
                raise LinearContractError("Linear Git status automation identity has another shape")
            automation_list.append(cls(id=identifier))
        return automation_list


def _snapshot_definition_list_validate(value: object, *, expected_type: type[object], label: str) -> None:
    """Validate one complete external definition list.

    Args:
        value: Candidate definition collection.
        expected_type: Exact member type.
        label: Diagnostic collection name.
    """

    if not isinstance(value, list) or any(not isinstance(item, expected_type) for item in value):
        raise LinearContractError(f"Linear snapshot {label} list has another shape")
    identifier_list = [item.id for item in value]
    if any(not identifier for identifier in identifier_list):
        raise LinearContractError(f"Linear snapshot {label} identity is absent")
    if len(identifier_list) != len(set(identifier_list)):
        raise LinearContractError(f"Linear snapshot repeats one {label} identity")


def _plan_definition_list_validate(value: object, *, expected_type: type[object], label: str) -> None:
    """Validate one unambiguous planned definition list.

    Args:
        value: Candidate planned collection.
        expected_type: Exact member type.
        label: Diagnostic collection name.
    """

    if not isinstance(value, list) or any(not isinstance(item, expected_type) for item in value):
        raise LinearContractError(f"Configuration plan {label} list has another shape")
    identity_list = [
        (f"{item.kind}\x00{item.name}" if isinstance(item, ConfigurationConflict) else item.name) for item in value
    ]
    if len(identity_list) != len(set(identity_list)):
        raise LinearContractError(f"Configuration plan repeats one {label}")


@dataclass(frozen=True, slots=True)
class WorkflowConfigurationSnapshot:
    """Contain one fully paginated read of relevant global configuration."""

    destination: DestinationIdentity
    issue_status_list: list[StatusDefinition]
    project_status_list: list[StatusDefinition]
    label_list: list[LinearLabel]
    git_status_automation_list: list[GitStatusAutomation]

    def __post_init__(self) -> None:
        """Reject duplicate external identities in one snapshot."""

        if not isinstance(self.destination, DestinationIdentity):
            raise LinearContractError("Linear snapshot destination has another shape")
        _snapshot_definition_list_validate(self.issue_status_list, expected_type=StatusDefinition, label="issue status")
        _snapshot_definition_list_validate(
            self.project_status_list,
            expected_type=StatusDefinition,
            label="Project status",
        )
        _snapshot_definition_list_validate(self.label_list, expected_type=LinearLabel, label="label")
        _snapshot_definition_list_validate(
            self.git_status_automation_list,
            expected_type=GitStatusAutomation,
            label="Git status automation",
        )
        object.__setattr__(self, "issue_status_list", list(self.issue_status_list))
        object.__setattr__(self, "project_status_list", list(self.project_status_list))
        object.__setattr__(self, "label_list", list(self.label_list))
        object.__setattr__(
            self,
            "git_status_automation_list",
            sorted(self.git_status_automation_list, key=lambda item: item.id),
        )


@dataclass(frozen=True, slots=True)
class ConfigurationConflict:
    """Describe one exact global configuration conflict."""

    kind: str
    name: str
    reason: str

    def __post_init__(self) -> None:
        """Validate one concise conflict without external payload leakage."""

        single_line_text_validate(self.kind, label="Conflict kind")
        single_line_text_validate(self.name, label="Conflict name")
        single_line_text_validate(self.reason, label="Conflict reason")

    @classmethod
    def list_from_payload(cls, value: object) -> list["ConfigurationConflict"]:
        """Parse one strict conflict list.

        Args:
            value: Candidate JSON value.

        Returns:
            Typed conflicts.
        """

        expected = {"kind", "name", "reason"}
        conflict_list: list[ConfigurationConflict] = []
        for item in _object_list_parse(value, label="conflict list"):
            if set(item) != expected:
                raise LinearContractError("Configuration plan conflict has another shape")
            conflict_list.append(cls(**item))
        return conflict_list


@dataclass(frozen=True, slots=True)
class ConfigurationPlan:
    """Describe the exact missing global configuration and conflicts."""

    destination: DestinationIdentity
    issue_status_create_list: list[StatusDefinition]
    project_status_create_list: list[StatusDefinition]
    label_create_list: list[LinearLabel]
    git_status_automation_delete_list: list[GitStatusAutomation]
    conflict_list: list[ConfigurationConflict]

    def __post_init__(self) -> None:
        """Reject malformed or ambiguous definition collections."""

        if not isinstance(self.destination, DestinationIdentity):
            raise LinearContractError("Configuration plan destination has another shape")
        self.destination.mutation_authority_require()
        _plan_definition_list_validate(
            self.issue_status_create_list,
            expected_type=StatusDefinition,
            label="issue status",
        )
        _plan_definition_list_validate(
            self.project_status_create_list,
            expected_type=StatusDefinition,
            label="Project status",
        )
        _plan_definition_list_validate(self.label_create_list, expected_type=LinearLabel, label="label")
        _snapshot_definition_list_validate(
            self.git_status_automation_delete_list,
            expected_type=GitStatusAutomation,
            label="Git status automation deletion",
        )
        _plan_definition_list_validate(
            self.conflict_list,
            expected_type=ConfigurationConflict,
            label="conflict",
        )
        object.__setattr__(self, "issue_status_create_list", list(self.issue_status_create_list))
        object.__setattr__(self, "project_status_create_list", list(self.project_status_create_list))
        object.__setattr__(self, "label_create_list", list(self.label_create_list))
        object.__setattr__(
            self,
            "git_status_automation_delete_list",
            sorted(self.git_status_automation_delete_list, key=lambda item: item.id),
        )
        object.__setattr__(self, "conflict_list", list(self.conflict_list))

    def can_mutate(self) -> bool:
        """Return whether the plan has no ambiguity or conflict.

        Returns:
            Whether applying only missing definitions is safe.
        """

        return not self.conflict_list

    def is_current(self) -> bool:
        """Return whether no mutation is required.

        Returns:
            Whether configuration already matches.
        """

        return self.can_mutate() and not (
            self.issue_status_create_list
            or self.project_status_create_list
            or self.label_create_list
            or self.git_status_automation_delete_list
        )

    def status_identifier_allocate(self) -> "ConfigurationPlan":
        """Return a plan with UUID v4 identities allocated for status creates.

        Returns:
            Retry-stable mutation plan.
        """

        return replace(
            self,
            issue_status_create_list=[status.create_identifier_allocate() for status in self.issue_status_create_list],
            project_status_create_list=[
                status.create_identifier_allocate() for status in self.project_status_create_list
            ],
        )

    def status_identifier_require(self) -> None:
        """Require retry-stable UUID v4 identities for all status creates."""

        for status in [
            *self.issue_status_create_list,
            *self.project_status_create_list,
        ]:
            status.create_identifier_require()

    def subset_require(self, approved: "ConfigurationPlan") -> None:
        """Require this fresh plan to remain an exact subset of an approved delta.

        Args:
            approved: Exact previously approved mutation plan.
        """

        if not approved.can_mutate():
            raise LinearContractError("Conflicting workflow configuration was not approvable")
        if self.destination != approved.destination:
            raise LinearContractError("Linear workflow destination changed after approval")
        if self.conflict_list:
            raise LinearContractError("Linear workflow configuration changed to a conflicting state before apply")
        approved_issue_status_by_name_map = {item.name: item for item in approved.issue_status_create_list}
        if any(
            approved_issue_status_by_name_map.get(item.name) is None
            or replace(approved_issue_status_by_name_map[item.name], id="") != replace(item, id="")
            for item in self.issue_status_create_list
        ):
            raise LinearContractError("Linear issue status plan changed after approval")
        approved_project_status_by_name_map = {item.name: item for item in approved.project_status_create_list}
        if any(
            approved_project_status_by_name_map.get(item.name) is None
            or replace(approved_project_status_by_name_map[item.name], id="") != replace(item, id="")
            for item in self.project_status_create_list
        ):
            raise LinearContractError("Linear Project status plan changed after approval")
        approved_label_by_name_map = {item.name: item for item in approved.label_create_list}
        if any(approved_label_by_name_map.get(item.name) != item for item in self.label_create_list):
            raise LinearContractError("Linear label plan changed after approval")
        approved_automation_by_id_map = {item.id: item for item in approved.git_status_automation_delete_list}
        if any(approved_automation_by_id_map.get(item.id) != item for item in self.git_status_automation_delete_list):
            raise LinearContractError("Linear Git status automation plan changed after approval")

    def payload(self) -> dict[str, object]:
        """Return the canonical approved-delta payload.

        Returns:
            JSON-ready plan object.
        """

        return {
            "schema_version": 1,
            "destination": {
                "team_id": self.destination.team_id,
                "viewer_id": self.destination.viewer_id,
                "viewer_is_active": self.destination.viewer_is_active,
                "viewer_is_admin": self.destination.viewer_is_admin,
                "viewer_is_guest": self.destination.viewer_is_guest,
                "workspace_id": self.destination.workspace_id,
            },
            "conflict_list": [
                {"kind": item.kind, "name": item.name, "reason": item.reason} for item in self.conflict_list
            ],
            "issue_status_create_list": [item.payload() for item in self.issue_status_create_list],
            "git_status_automation_delete_list": [item.payload() for item in self.git_status_automation_delete_list],
            "label_create_list": [item.payload() for item in self.label_create_list],
            "project_status_create_list": [item.payload() for item in self.project_status_create_list],
        }

    @classmethod
    def from_payload(cls, payload: object) -> "ConfigurationPlan":
        """Parse one strict approved global delta.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed configuration plan.
        """

        expected = {
            "schema_version",
            "destination",
            "conflict_list",
            "issue_status_create_list",
            "git_status_automation_delete_list",
            "label_create_list",
            "project_status_create_list",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise LinearContractError("Configuration plan has another shape")
        destination_payload = payload["destination"]
        destination_expected = {
            "team_id",
            "viewer_id",
            "viewer_is_active",
            "viewer_is_admin",
            "viewer_is_guest",
            "workspace_id",
        }
        if not isinstance(destination_payload, dict) or set(destination_payload) != destination_expected:
            raise LinearContractError("Configuration plan destination has another shape")
        return cls(
            destination=DestinationIdentity(**destination_payload),
            issue_status_create_list=StatusDefinition.list_from_payload(payload["issue_status_create_list"]),
            project_status_create_list=StatusDefinition.list_from_payload(payload["project_status_create_list"]),
            label_create_list=LinearLabel.list_from_payload(payload["label_create_list"]),
            git_status_automation_delete_list=GitStatusAutomation.list_from_payload(
                payload["git_status_automation_delete_list"]
            ),
            conflict_list=ConfigurationConflict.list_from_payload(payload["conflict_list"]),
        )


def _object_list_parse(value: object, *, label: str) -> list[dict[str, object]]:
    """Return one strict list of JSON objects.

    Args:
        value: Candidate JSON value.
        label: Diagnostic owner label.

    Returns:
        Typed object list.
    """

    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise LinearContractError(f"Configuration plan {label} must be a list of objects")
    return value
