"""Closed Linear identity and workflow-configuration models."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
import uuid

from linear_boundary.contract import LinearContractError, single_line_text_validate, uuid_validate

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
        f"{item.kind}\x00{item.name}" if isinstance(item, ConfigurationConflict) else item.name for item in value
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
        object.__setattr__(self, "issue_status_list", list(self.issue_status_list))
        object.__setattr__(self, "project_status_list", list(self.project_status_list))
        object.__setattr__(self, "label_list", list(self.label_list))


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


def _label_plan_subset_require(
    current_label_list: list[LinearLabel],
    approved_label_list: list[LinearLabel],
) -> None:
    """Require one fresh label delta to remain inside its approved delta.

    Args:
        current_label_list: Missing labels from the latest read.
        approved_label_list: Exact previously approved missing labels.
    """

    approved_label_by_name_map = {item.name: item for item in approved_label_list}
    if any(approved_label_by_name_map.get(item.name) != item for item in current_label_list):
        raise LinearContractError("Linear label plan changed after approval")


def _status_plan_subset_require(
    current_status_list: list[StatusDefinition],
    approved_status_list: list[StatusDefinition],
    *,
    label: str,
) -> None:
    """Require one fresh status delta to remain inside its approved delta.

    Args:
        current_status_list: Missing statuses from the latest read.
        approved_status_list: Exact previously approved missing statuses.
        label: Diagnostic status family.
    """

    approved_status_by_name_map = {item.name: item for item in approved_status_list}
    if any(
        approved_status_by_name_map.get(item.name) is None
        or replace(approved_status_by_name_map[item.name], id="") != replace(item, id="")
        for item in current_status_list
    ):
        raise LinearContractError(f"Linear {label} plan changed after approval")


@dataclass(frozen=True, slots=True)
class ConfigurationPlan:
    """Describe the exact missing global configuration and conflicts."""

    destination: DestinationIdentity
    issue_status_create_list: list[StatusDefinition]
    project_status_create_list: list[StatusDefinition]
    label_create_list: list[LinearLabel]
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
        _plan_definition_list_validate(
            self.conflict_list,
            expected_type=ConfigurationConflict,
            label="conflict",
        )
        object.__setattr__(self, "issue_status_create_list", list(self.issue_status_create_list))
        object.__setattr__(self, "project_status_create_list", list(self.project_status_create_list))
        object.__setattr__(self, "label_create_list", list(self.label_create_list))
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
            self.issue_status_create_list or self.project_status_create_list or self.label_create_list
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

        for status in [*self.issue_status_create_list, *self.project_status_create_list]:
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
        _status_plan_subset_require(
            self.issue_status_create_list,
            approved.issue_status_create_list,
            label="issue status",
        )
        _status_plan_subset_require(
            self.project_status_create_list,
            approved.project_status_create_list,
            label="Project status",
        )
        _label_plan_subset_require(self.label_create_list, approved.label_create_list)

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
            "issue_status_create_list": [_status_payload(item) for item in self.issue_status_create_list],
            "label_create_list": [_label_payload(item) for item in self.label_create_list],
            "project_status_create_list": [_status_payload(item) for item in self.project_status_create_list],
        }

    def fingerprint(self) -> str:
        """Return SHA-256 of the exact previewed global delta.

        Returns:
            Lowercase plan fingerprint.
        """

        return hashlib.sha256(
            json.dumps(
                self.payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

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
            issue_status_create_list=_status_payload_list_parse(payload["issue_status_create_list"]),
            project_status_create_list=_status_payload_list_parse(payload["project_status_create_list"]),
            label_create_list=_label_payload_list_parse(payload["label_create_list"]),
            conflict_list=_conflict_payload_list_parse(payload["conflict_list"]),
        )


def _status_payload(status: StatusDefinition) -> dict[str, object]:
    """Return one canonical status payload.

    Args:
        status: Typed status definition.

    Returns:
        JSON-ready status object.
    """

    return {
        "id": status.id,
        "name": status.name,
        "category": status.category,
        "color": status.color,
        "description": status.description,
        "position": status.position,
    }


def _label_payload(label: LinearLabel) -> dict[str, str]:
    """Return one canonical label payload.

    Args:
        label: Typed label definition.

    Returns:
        JSON-ready label object.
    """

    return {
        "id": label.id,
        "name": label.name,
        "color": label.color,
        "description": label.description,
    }


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


def _status_payload_list_parse(value: object) -> list[StatusDefinition]:
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
        status_list.append(StatusDefinition(**item))
    return status_list


def _label_payload_list_parse(value: object) -> list[LinearLabel]:
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
        label_list.append(LinearLabel(**item))
    return label_list


def _conflict_payload_list_parse(value: object) -> list[ConfigurationConflict]:
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
        conflict_list.append(ConfigurationConflict(**item))
    return conflict_list
