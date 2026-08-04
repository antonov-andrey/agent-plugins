"""Closed Linear identity and workflow-configuration models."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Iterable
import uuid

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_HEX_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")
_ROLE_LABEL_SET = frozenset(
    {
        "task:implementation",
        "task:review",
        "task:acceptance",
        "task:cleanup",
        "task:human",
    }
)
_DELIVERY_BY_ROLE = {
    "task:implementation": frozenset({"code", "evidence"}),
    "task:review": frozenset({"evidence"}),
    "task:acceptance": frozenset({"evidence"}),
    "task:cleanup": frozenset({"cleanup"}),
    "task:human": frozenset({"human"}),
}


class LinearContractError(RuntimeError):
    """Report one malformed or conflicting Linear contract."""


class IssueStatusCategory(StrEnum):
    """Linear fixed issue workflow categories."""

    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"


class ProjectStatusCategory(StrEnum):
    """Linear fixed Project workflow categories used by this provider."""

    PLANNED = "planned"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"


class IssueStatusName(StrEnum):
    """Exact issue workflow names used by manual and future Symphony runners."""

    BACKLOG = "Backlog"
    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    HUMAN_REVIEW = "Human Review"
    REWORK = "Rework"
    MERGING = "Merging"
    DONE = "Done"
    CANCELED = "Canceled"


class ProjectStatusName(StrEnum):
    """Exact Project workflow names used by graph activation and completion."""

    PLANNED = "Planned"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    CANCELED = "Canceled"


def _uuid_validate(value: str, *, label: str) -> str:
    """Return one canonical lowercase UUID identity.

    Args:
        value: Candidate identity.
        label: Diagnostic owner label.

    Returns:
        The validated identity.
    """

    if not isinstance(value, str) or _UUID_PATTERN.fullmatch(value) is None:
        raise LinearContractError(f"{label} must be one lowercase UUID")
    return value


def _single_line_validate(value: str, *, label: str) -> str:
    """Return one non-empty single-line string.

    Args:
        value: Candidate text.
        label: Diagnostic owner label.

    Returns:
        The validated text.
    """

    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise LinearContractError(f"{label} must be non-empty single-line text")
    return value


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

        _uuid_validate(self.workspace_id, label="Workspace ID")
        _uuid_validate(self.viewer_id, label="Viewer ID")
        _uuid_validate(self.team_id, label="Team ID")
        for name in ("viewer_is_admin", "viewer_is_guest", "viewer_is_active"):
            if not isinstance(getattr(self, name), bool):
                raise LinearContractError(f"{name} must be boolean")

    def mutation_authority_require(self) -> None:
        """Require the authenticated viewer to be an active non-guest admin."""

        if (
            not self.viewer_is_admin
            or self.viewer_is_guest
            or not self.viewer_is_active
        ):
            raise LinearContractError(
                "Authenticated Linear viewer is not an active non-guest administrator"
            )


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
            _uuid_validate(self.id, label="Status ID")
        _single_line_validate(self.name, label="Status name")
        _single_line_validate(self.category, label="Status category")
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
            _uuid_validate(self.id, label="Label ID")
        _single_line_validate(self.name, label="Label name")
        if _HEX_COLOR_PATTERN.fullmatch(self.color) is None:
            raise LinearContractError("Label color must use #RRGGBB")
        if not isinstance(self.description, str) or any(
            character in self.description for character in ("\x00", "\n", "\r")
        ):
            raise LinearContractError("Label description must be single-line text")


@dataclass(frozen=True, slots=True)
class WorkflowConfigurationSnapshot:
    """Contain one fully paginated read of relevant global configuration."""

    destination: DestinationIdentity
    issue_status_list: tuple[StatusDefinition, ...]
    project_status_list: tuple[StatusDefinition, ...]
    label_list: tuple[LinearLabel, ...]

    def __post_init__(self) -> None:
        """Reject duplicate external identities in one snapshot."""

        if not isinstance(self.destination, DestinationIdentity):
            raise LinearContractError("Linear snapshot destination has another shape")
        for label, item_list, expected_type in (
            ("issue status", self.issue_status_list, StatusDefinition),
            ("Project status", self.project_status_list, StatusDefinition),
            ("label", self.label_list, LinearLabel),
        ):
            if not isinstance(item_list, tuple) or any(
                not isinstance(item, expected_type) for item in item_list
            ):
                raise LinearContractError(
                    f"Linear snapshot {label} list has another shape"
                )
            identifier_list = [item.id for item in item_list]
            if any(not identifier for identifier in identifier_list):
                raise LinearContractError(f"Linear snapshot {label} identity is absent")
            if len(identifier_list) != len(set(identifier_list)):
                raise LinearContractError(
                    f"Linear snapshot repeats one {label} identity"
                )


@dataclass(frozen=True, slots=True)
class ConfigurationConflict:
    """Describe one exact global configuration conflict."""

    kind: str
    name: str
    reason: str

    def __post_init__(self) -> None:
        """Validate one concise conflict without external payload leakage."""

        _single_line_validate(self.kind, label="Conflict kind")
        _single_line_validate(self.name, label="Conflict name")
        _single_line_validate(self.reason, label="Conflict reason")


@dataclass(frozen=True, slots=True)
class ConfigurationPlan:
    """Describe the exact missing global configuration and conflicts."""

    destination: DestinationIdentity
    issue_status_create_list: tuple[StatusDefinition, ...]
    project_status_create_list: tuple[StatusDefinition, ...]
    label_create_list: tuple[LinearLabel, ...]
    conflict_list: tuple[ConfigurationConflict, ...]

    def __post_init__(self) -> None:
        """Reject malformed or ambiguous definition collections."""

        if not isinstance(self.destination, DestinationIdentity):
            raise LinearContractError(
                "Configuration plan destination has another shape"
            )
        self.destination.mutation_authority_require()
        for label, value, expected_type in (
            ("issue status", self.issue_status_create_list, StatusDefinition),
            ("Project status", self.project_status_create_list, StatusDefinition),
            ("label", self.label_create_list, LinearLabel),
            ("conflict", self.conflict_list, ConfigurationConflict),
        ):
            if not isinstance(value, tuple) or any(
                not isinstance(item, expected_type) for item in value
            ):
                raise LinearContractError(
                    f"Configuration plan {label} list has another shape"
                )
            identity_list = [
                (
                    (item.kind, item.name)
                    if isinstance(item, ConfigurationConflict)
                    else item.name
                )
                for item in value
            ]
            if len(identity_list) != len(set(identity_list)):
                raise LinearContractError(f"Configuration plan repeats one {label}")

    def mutation_allowed(self) -> bool:
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

        return self.mutation_allowed() and not (
            self.issue_status_create_list
            or self.project_status_create_list
            or self.label_create_list
        )

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
                {"kind": item.kind, "name": item.name, "reason": item.reason}
                for item in self.conflict_list
            ],
            "issue_status_create_list": [
                _status_payload(item) for item in self.issue_status_create_list
            ],
            "label_create_list": [
                _label_payload(item) for item in self.label_create_list
            ],
            "project_status_create_list": [
                _status_payload(item) for item in self.project_status_create_list
            ],
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
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or payload["schema_version"] != 1
        ):
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
        if (
            not isinstance(destination_payload, dict)
            or set(destination_payload) != destination_expected
        ):
            raise LinearContractError(
                "Configuration plan destination has another shape"
            )
        return cls(
            destination=DestinationIdentity(**destination_payload),
            issue_status_create_list=_status_payload_list_parse(
                payload["issue_status_create_list"]
            ),
            project_status_create_list=_status_payload_list_parse(
                payload["project_status_create_list"]
            ),
            label_create_list=_label_payload_list_parse(payload["label_create_list"]),
            conflict_list=_conflict_payload_list_parse(payload["conflict_list"]),
        )


@dataclass(frozen=True, slots=True)
class TaskExecutionSnapshot:
    """Contain all current facts needed to decide agent dispatchability."""

    issue_status: IssueStatusName
    project_status: ProjectStatusName
    role_label: str
    delivery_kind: str
    label_name_list: tuple[str, ...]
    assignee_id: str
    delegate_id: str
    execution_identity_id: str
    unresolved_blocker_count: int
    issue_contract_complete: bool

    def __post_init__(self) -> None:
        """Validate one complete status decision input."""

        if not isinstance(self.issue_status, IssueStatusName) or not isinstance(
            self.project_status,
            ProjectStatusName,
        ):
            raise LinearContractError("Task execution statuses are unsupported")
        if self.role_label not in _ROLE_LABEL_SET:
            raise LinearContractError("Task execution role is unsupported")
        if self.delivery_kind not in _DELIVERY_BY_ROLE[self.role_label]:
            raise LinearContractError(
                "Task execution role and delivery kind are incompatible"
            )
        if (
            not isinstance(self.label_name_list, tuple)
            or len(self.label_name_list) != len(set(self.label_name_list))
            or any(
                not isinstance(item, str)
                or not item
                or any(char in item for char in "\x00\r\n")
                for item in self.label_name_list
            )
        ):
            raise LinearContractError(
                "Task execution labels must be a duplicate-free single-line tuple"
            )
        actual_role_set = set(self.label_name_list) & _ROLE_LABEL_SET
        if actual_role_set != {self.role_label}:
            raise LinearContractError(
                "Task execution role must match the exact single Linear role label"
            )
        if (
            isinstance(self.unresolved_blocker_count, bool)
            or not isinstance(self.unresolved_blocker_count, int)
            or self.unresolved_blocker_count < 0
        ):
            raise LinearContractError(
                "Task unresolved blocker count must be non-negative"
            )
        if not isinstance(self.issue_contract_complete, bool):
            raise LinearContractError("Task contract completeness must be boolean")
        assignment_id_list = [
            value for value in (self.assignee_id, self.delegate_id) if value
        ]
        if len(assignment_id_list) != 1:
            raise LinearContractError("Task must have exactly one assignee or delegate")
        _uuid_validate(assignment_id_list[0], label="Task execution assignment ID")
        _uuid_validate(self.execution_identity_id, label="Execution identity ID")

    def dispatchable(self) -> bool:
        """Return whether a runner may own the next attempt.

        Returns:
            Dispatchability under the exact shared allowlist.
        """

        return not self.dispatch_blocker_list()

    def dispatch_blocker_list(self) -> tuple[str, ...]:
        """Return concise deterministic reasons why a new attempt cannot run."""

        blocker_list: list[str] = []
        if self.issue_status not in {
            IssueStatusName.TODO,
            IssueStatusName.IN_PROGRESS,
            IssueStatusName.REWORK,
            IssueStatusName.MERGING,
        }:
            blocker_list.append("issue-status-inactive")
        if self.project_status is not ProjectStatusName.IN_PROGRESS:
            blocker_list.append("project-not-active")
        if self.role_label == "task:human":
            blocker_list.append("human-task")
        if self.issue_status is IssueStatusName.MERGING and (
            self.role_label != "task:implementation" or self.delivery_kind != "code"
        ):
            blocker_list.append("merging-role-incompatible")
        if "agent:codex" not in self.label_name_list:
            blocker_list.append("dispatch-label-absent")
        if self.execution_identity_id not in {self.assignee_id, self.delegate_id}:
            blocker_list.append("execution-identity-mismatch")
        if self.unresolved_blocker_count:
            blocker_list.append("unresolved-blockers")
        if not self.issue_contract_complete:
            blocker_list.append("issue-contract-incomplete")
        return tuple(blocker_list)


@dataclass(frozen=True, slots=True)
class TransitionProof:
    """Carry only facts required for one requested status transition."""

    human_decision: bool = False
    task_definition_ready: bool = False
    fresh_thread: bool = False
    workspace_preserved: bool = False
    result_ready: bool = False
    verification_ready: bool = False
    publication_ready: bool = False
    required_ci_ready: bool = False
    evidence_ready: bool = False
    candidate_fingerprint_ready: bool = False
    candidate_unchanged: bool = False
    candidate_mutated: bool = False
    remediation_blocker_ready: bool = False
    review_finding_ready: bool = False
    merge_complete: bool = False
    cleanup_complete: bool = False

    def __post_init__(self) -> None:
        """Require every proof flag to be a real boolean."""

        for name in self.__dataclass_fields__:
            if not isinstance(getattr(self, name), bool):
                raise LinearContractError(f"Transition proof {name} must be boolean")
        if self.candidate_unchanged and self.candidate_mutated:
            raise LinearContractError("Candidate cannot be both unchanged and mutated")


def transition_require(
    *,
    current: IssueStatusName,
    target: IssueStatusName,
    project_status: ProjectStatusName,
    role_label: str,
    delivery_kind: str,
    proof: TransitionProof,
    dispatchable: bool,
) -> None:
    """Require one exact allowed workflow transition and its semantic evidence.

    Args:
        current: Current exact status.
        target: Requested exact status.
        project_status: Current exact owning Project status.
        role_label: Exact task role label.
        delivery_kind: Code, evidence, cleanup or human.
        proof: Current transition proof.
        dispatchable: Current dispatch decision when starting an attempt.
    """

    if (
        not isinstance(current, IssueStatusName)
        or not isinstance(target, IssueStatusName)
        or not isinstance(project_status, ProjectStatusName)
    ):
        raise LinearContractError("Workflow transition statuses are unsupported")
    if (
        role_label not in _DELIVERY_BY_ROLE
        or delivery_kind not in _DELIVERY_BY_ROLE[role_label]
    ):
        raise LinearContractError(
            "Workflow transition role and delivery kind are incompatible"
        )
    if not isinstance(proof, TransitionProof) or not isinstance(dispatchable, bool):
        raise LinearContractError(
            "Workflow transition proof or dispatch decision has another shape"
        )
    if target is IssueStatusName.CANCELED and current not in {
        IssueStatusName.DONE,
        IssueStatusName.CANCELED,
    }:
        if project_status is ProjectStatusName.COMPLETED:
            raise LinearContractError(
                "A completed Project cannot cancel an unfinished task"
            )
        if not proof.human_decision:
            raise LinearContractError(
                "Canceling a non-terminal task requires an explicit human decision"
            )
        return
    if current is IssueStatusName.BACKLOG and target is IssueStatusName.TODO:
        if project_status not in {
            ProjectStatusName.PLANNED,
            ProjectStatusName.IN_PROGRESS,
        }:
            raise LinearContractError(
                "Task activation requires a planned or active Project"
            )
        if not proof.task_definition_ready:
            raise LinearContractError(
                "Todo activation requires a complete task definition"
            )
        return
    if project_status is not ProjectStatusName.IN_PROGRESS:
        raise LinearContractError(
            "A non-cancellation task transition requires an active Project"
        )
    if (
        current in {IssueStatusName.TODO, IssueStatusName.REWORK}
        and target is IssueStatusName.IN_PROGRESS
    ):
        if not dispatchable or not proof.fresh_thread:
            raise LinearContractError(
                "Starting an attempt requires dispatchability and a fresh thread"
            )
        if (
            current is IssueStatusName.REWORK
            and delivery_kind == "code"
            and not proof.workspace_preserved
        ):
            raise LinearContractError(
                "Rework must adopt the existing candidate workspace"
            )
        return
    if (
        current is IssueStatusName.IN_PROGRESS
        and target is IssueStatusName.HUMAN_REVIEW
    ):
        common_ready = all(
            (
                proof.result_ready,
                proof.verification_ready,
                proof.evidence_ready,
                proof.candidate_fingerprint_ready,
            )
        )
        if delivery_kind == "code":
            common_ready = (
                common_ready and proof.publication_ready and proof.required_ci_ready
            )
        elif delivery_kind != "evidence":
            raise LinearContractError(
                "Only code or evidence delivery may enter Human Review"
            )
        if not common_ready:
            raise LinearContractError(
                "Human Review requires every delivery-applicable result and candidate proof"
            )
        return
    if current is IssueStatusName.IN_PROGRESS and target is IssueStatusName.TODO:
        if (
            role_label not in {"task:review", "task:acceptance"}
            or not proof.remediation_blocker_ready
        ):
            raise LinearContractError(
                "Only review or acceptance may return to Todo with a remediation blocker"
            )
        return
    if current is IssueStatusName.HUMAN_REVIEW and target is IssueStatusName.REWORK:
        if not proof.human_decision and not proof.review_finding_ready:
            raise LinearContractError(
                "Rework requires an explicit human decision or review finding"
            )
        return
    if current is IssueStatusName.HUMAN_REVIEW and target is IssueStatusName.MERGING:
        if (
            delivery_kind != "code"
            or not proof.human_decision
            or not proof.candidate_unchanged
        ):
            raise LinearContractError(
                "Merging requires human approval of the unchanged code candidate"
            )
        return
    if current is IssueStatusName.HUMAN_REVIEW and target is IssueStatusName.DONE:
        if (
            delivery_kind == "code"
            or not proof.human_decision
            or not proof.candidate_unchanged
        ):
            raise LinearContractError(
                "Non-code completion requires human approval of the unchanged evidence candidate"
            )
        return
    if current is IssueStatusName.MERGING and target is IssueStatusName.DONE:
        if delivery_kind != "code" or role_label != "task:implementation":
            raise LinearContractError(
                "Only a code implementation task may complete through Merging"
            )
        if not proof.candidate_unchanged or not proof.merge_complete:
            raise LinearContractError(
                "Merge completion must match the exact approved candidate"
            )
        return
    if current is IssueStatusName.MERGING and target is IssueStatusName.REWORK:
        if (
            delivery_kind != "code"
            or role_label != "task:implementation"
            or not proof.candidate_mutated
        ):
            raise LinearContractError(
                "A merging code task returns to Rework only after proven candidate mutation"
            )
        return
    if current is IssueStatusName.IN_PROGRESS and target is IssueStatusName.DONE:
        if role_label != "task:cleanup" or not proof.cleanup_complete:
            raise LinearContractError(
                "Only a successfully reconciled cleanup task may complete directly"
            )
        return
    if current is IssueStatusName.TODO and target is IssueStatusName.DONE:
        if (
            role_label != "task:human"
            or not proof.human_decision
            or not proof.evidence_ready
        ):
            raise LinearContractError(
                "Human task completion requires explicit human evidence"
            )
        return
    raise LinearContractError(f"Workflow transition {current} -> {target} is forbidden")


ISSUE_STATUS_DESIRED = (
    StatusDefinition(
        "",
        "Backlog",
        IssueStatusCategory.BACKLOG,
        "#6B7280",
        "Idea or inactive import staging",
        100.0,
    ),
    StatusDefinition(
        "",
        "Todo",
        IssueStatusCategory.UNSTARTED,
        "#4F46E5",
        "Defined task ready when blockers close",
        200.0,
    ),
    StatusDefinition(
        "",
        "In Progress",
        IssueStatusCategory.STARTED,
        "#2563EB",
        "Agent owns the current attempt",
        300.0,
    ),
    StatusDefinition(
        "",
        "Human Review",
        IssueStatusCategory.STARTED,
        "#7C3AED",
        "Candidate awaits a human decision",
        400.0,
    ),
    StatusDefinition(
        "",
        "Rework",
        IssueStatusCategory.STARTED,
        "#DC2626",
        "A fresh attempt must revise the candidate",
        500.0,
    ),
    StatusDefinition(
        "",
        "Merging",
        IssueStatusCategory.STARTED,
        "#0F766E",
        "Approved exact candidate is being merged",
        600.0,
    ),
    StatusDefinition(
        "", "Done", IssueStatusCategory.COMPLETED, "#16A34A", "Task completed", 700.0
    ),
    StatusDefinition(
        "",
        "Canceled",
        IssueStatusCategory.CANCELED,
        "#9CA3AF",
        "Task canceled by a human",
        800.0,
    ),
)

PROJECT_STATUS_DESIRED = (
    StatusDefinition(
        "",
        "Planned",
        ProjectStatusCategory.PLANNED,
        "#6B7280",
        "Graph is non-dispatchable staging",
        100.0,
    ),
    StatusDefinition(
        "",
        "In Progress",
        ProjectStatusCategory.STARTED,
        "#2563EB",
        "Graph passed the activation barrier",
        200.0,
    ),
    StatusDefinition(
        "",
        "Completed",
        ProjectStatusCategory.COMPLETED,
        "#16A34A",
        "Accepted graph and cleanup completed",
        300.0,
    ),
    StatusDefinition(
        "",
        "Canceled",
        ProjectStatusCategory.CANCELED,
        "#9CA3AF",
        "Graph canceled and reconciled",
        400.0,
    ),
)

LABEL_DESIRED = (
    LinearLabel(
        "",
        "task:implementation",
        "#4F46E5",
        "[linear-agent-tools:v1] Code or evidence implementation task",
    ),
    LinearLabel(
        "",
        "task:review",
        "#7C3AED",
        "[linear-agent-tools:v1] Independent semantic review task",
    ),
    LinearLabel(
        "",
        "task:acceptance",
        "#0F766E",
        "[linear-agent-tools:v1] Whole-outcome acceptance task",
    ),
    LinearLabel(
        "",
        "task:cleanup",
        "#B45309",
        "[linear-agent-tools:v1] Exact owned-resource cleanup task",
    ),
    LinearLabel(
        "",
        "task:human",
        "#64748B",
        "[linear-agent-tools:v1] Human-only decision or action",
    ),
    LinearLabel(
        "",
        "agent:codex",
        "#2563EB",
        "[linear-agent-tools:v1] Dispatch may use a Codex agent",
    ),
)


def configuration_plan_subset_require(
    current: ConfigurationPlan, approved: ConfigurationPlan
) -> None:
    """Require a fresh plan to be an exact subset of one approved delta.

    Args:
        current: Fresh destination-bound plan.
        approved: Previously displayed and approved plan.
    """

    if not approved.mutation_allowed():
        raise LinearContractError(
            "Conflicting workflow configuration was not approvable"
        )
    if current.destination != approved.destination:
        raise LinearContractError("Linear workflow destination changed after approval")
    if current.conflict_list:
        raise LinearContractError(
            "Linear workflow configuration changed to a conflicting state before apply"
        )
    for label, current_list, approved_list in (
        (
            "issue status",
            current.issue_status_create_list,
            approved.issue_status_create_list,
        ),
        (
            "Project status",
            current.project_status_create_list,
            approved.project_status_create_list,
        ),
        ("label", current.label_create_list, approved.label_create_list),
    ):
        approved_by_name = {item.name: item for item in approved_list}
        if label in {"issue status", "Project status"}:
            changed = any(
                approved_by_name.get(item.name) is None
                or replace(approved_by_name[item.name], id="") != replace(item, id="")
                for item in current_list
            )
        else:
            changed = any(
                approved_by_name.get(item.name) != item for item in current_list
            )
        if changed:
            raise LinearContractError(f"Linear {label} plan changed after approval")


def configuration_plan_status_identifiers_allocate(
    plan: ConfigurationPlan,
) -> ConfigurationPlan:
    """Allocate UUID v4 identities once for every planned status mutation.

    The returned plan is the durable retry identity. Re-reading current Linear
    state must not allocate replacement identities during apply or recovery.

    Args:
        plan: Fresh destination-bound configuration plan.

    Returns:
        The same plan semantics with exact create identities assigned.
    """

    def identifier_allocate(status: StatusDefinition) -> StatusDefinition:
        if status.id:
            _uuid_v4_validate(status.id, label=f"Status {status.name} create ID")
            return status
        return replace(status, id=str(uuid.uuid4()))

    return replace(
        plan,
        issue_status_create_list=tuple(
            identifier_allocate(item) for item in plan.issue_status_create_list
        ),
        project_status_create_list=tuple(
            identifier_allocate(item) for item in plan.project_status_create_list
        ),
    )


def configuration_plan_status_identifiers_require(plan: ConfigurationPlan) -> None:
    """Require exact UUID v4 create identities in an approved mutation plan.

    Args:
        plan: Approved plan used by apply or recovery.
    """

    for status in (*plan.issue_status_create_list, *plan.project_status_create_list):
        _uuid_v4_validate(status.id, label=f"Status {status.name} create ID")


def _uuid_v4_validate(value: str, *, label: str) -> str:
    """Return one canonical UUID v4 accepted by Linear create inputs.

    Args:
        value: Candidate identifier.
        label: Diagnostic owner label.

    Returns:
        The validated identifier.
    """

    _uuid_validate(value, label=label)
    try:
        identifier = uuid.UUID(value)
    except ValueError as error:
        raise LinearContractError(f"{label} must be one UUID v4") from error
    if identifier.version != 4 or str(identifier) != value:
        raise LinearContractError(f"{label} must be one UUID v4")
    return value


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
        raise LinearContractError(
            f"Configuration plan {label} must be a list of objects"
        )
    return value


def _status_payload_list_parse(value: object) -> tuple[StatusDefinition, ...]:
    """Parse one strict status list.

    Args:
        value: Candidate JSON value.

    Returns:
        Typed status definitions.
    """

    expected = {"id", "name", "category", "color", "description", "position"}
    result: list[StatusDefinition] = []
    for item in _object_list_parse(value, label="status list"):
        if set(item) != expected:
            raise LinearContractError("Configuration plan status has another shape")
        result.append(StatusDefinition(**item))
    return tuple(result)


def _label_payload_list_parse(value: object) -> tuple[LinearLabel, ...]:
    """Parse one strict label list.

    Args:
        value: Candidate JSON value.

    Returns:
        Typed labels.
    """

    expected = {"id", "name", "color", "description"}
    result: list[LinearLabel] = []
    for item in _object_list_parse(value, label="label list"):
        if set(item) != expected:
            raise LinearContractError("Configuration plan label has another shape")
        result.append(LinearLabel(**item))
    return tuple(result)


def _conflict_payload_list_parse(value: object) -> tuple[ConfigurationConflict, ...]:
    """Parse one strict conflict list.

    Args:
        value: Candidate JSON value.

    Returns:
        Typed conflicts.
    """

    expected = {"kind", "name", "reason"}
    result: list[ConfigurationConflict] = []
    for item in _object_list_parse(value, label="conflict list"):
        if set(item) != expected:
            raise LinearContractError("Configuration plan conflict has another shape")
        result.append(ConfigurationConflict(**item))
    return tuple(result)


def configuration_plan_build(
    snapshot: WorkflowConfigurationSnapshot,
) -> ConfigurationPlan:
    """Reconcile one complete snapshot against the fixed provider contract.

    Args:
        snapshot: Fully paginated current configuration.

    Returns:
        Exact missing definitions and conflicts.
    """

    snapshot.destination.mutation_authority_require()
    issue_create, issue_conflicts = _status_reconcile(
        snapshot.issue_status_list, ISSUE_STATUS_DESIRED, "issue-status"
    )
    project_create, project_conflicts = _status_reconcile(
        snapshot.project_status_list,
        PROJECT_STATUS_DESIRED,
        "project-status",
    )
    label_create, label_conflicts = _label_reconcile(snapshot.label_list, LABEL_DESIRED)
    return ConfigurationPlan(
        destination=snapshot.destination,
        issue_status_create_list=issue_create,
        project_status_create_list=project_create,
        label_create_list=label_create,
        conflict_list=tuple((*issue_conflicts, *project_conflicts, *label_conflicts)),
    )


def _status_reconcile(
    current: Iterable[StatusDefinition],
    desired: tuple[StatusDefinition, ...],
    kind: str,
) -> tuple[tuple[StatusDefinition, ...], tuple[ConfigurationConflict, ...]]:
    """Compare one status family by exact name and fixed category.

    Args:
        current: Current external definitions.
        desired: Desired definitions.
        kind: Diagnostic object kind.

    Returns:
        Missing definitions and conflicts.
    """

    current_by_name: dict[str, list[StatusDefinition]] = {}
    for item in current:
        current_by_name.setdefault(item.name.casefold(), []).append(item)
    missing: list[StatusDefinition] = []
    conflict_list: list[ConfigurationConflict] = []
    for expected in desired:
        matching = current_by_name.get(expected.name.casefold(), [])
        if not matching:
            missing.append(expected)
        elif len(matching) > 1:
            conflict_list.append(
                ConfigurationConflict(kind, expected.name, "ambiguous duplicate name")
            )
        elif matching[0].name != expected.name:
            conflict_list.append(
                ConfigurationConflict(
                    kind, expected.name, "same name uses different casing"
                )
            )
        elif matching[0].category != expected.category:
            conflict_list.append(
                ConfigurationConflict(
                    kind,
                    expected.name,
                    f"category is {matching[0].category}, expected {expected.category}",
                )
            )
    return tuple(missing), tuple(conflict_list)


def _label_reconcile(
    current: Iterable[LinearLabel],
    desired: tuple[LinearLabel, ...],
) -> tuple[tuple[LinearLabel, ...], tuple[ConfigurationConflict, ...]]:
    """Compare provider-owned labels without overwriting foreign definitions.

    Args:
        current: Current external labels.
        desired: Desired provider labels.

    Returns:
        Missing labels and conflicts.
    """

    current_by_name: dict[str, list[LinearLabel]] = {}
    for item in current:
        current_by_name.setdefault(item.name.casefold(), []).append(item)
    missing: list[LinearLabel] = []
    conflict_list: list[ConfigurationConflict] = []
    for expected in desired:
        matching = current_by_name.get(expected.name.casefold(), [])
        if not matching:
            missing.append(expected)
        elif len(matching) > 1:
            conflict_list.append(
                ConfigurationConflict(
                    "label", expected.name, "ambiguous duplicate name"
                )
            )
        elif matching[0].name != expected.name:
            conflict_list.append(
                ConfigurationConflict(
                    "label", expected.name, "same name uses different casing"
                )
            )
        elif (
            matching[0].description != expected.description
            or matching[0].color.lower() != expected.color.lower()
        ):
            conflict_list.append(
                ConfigurationConflict(
                    "label",
                    expected.name,
                    "existing label is not the exact provider definition",
                )
            )
    return tuple(missing), tuple(conflict_list)
