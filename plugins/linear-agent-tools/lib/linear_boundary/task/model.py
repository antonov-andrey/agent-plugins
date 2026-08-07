"""Linear task dispatch snapshots and transition proof models."""

from __future__ import annotations

from dataclasses import dataclass

from linear_boundary.contract import LinearContractError, uuid_validate
from linear_boundary.status import IssueStatusName, ProjectStatusName, issue_status_name_parse

_ROLE_LABEL_SET = frozenset(
    {
        "task:implementation",
        "task:review",
        "task:acceptance",
        "task:cleanup",
        "task:human",
    }
)
_DELIVERY_KIND_SET_BY_ROLE_MAP = {
    "task:implementation": frozenset({"code", "evidence"}),
    "task:review": frozenset({"evidence"}),
    "task:acceptance": frozenset({"evidence"}),
    "task:cleanup": frozenset({"cleanup"}),
    "task:human": frozenset({"human"}),
}


def task_delivery_validate(*, role_label: str, delivery_kind: str) -> None:
    """Require one supported Linear task role and delivery pair.

    Args:
        role_label: Exact task role label.
        delivery_kind: Code, evidence, cleanup or human.
    """

    if role_label not in _ROLE_LABEL_SET or delivery_kind not in _DELIVERY_KIND_SET_BY_ROLE_MAP[role_label]:
        raise LinearContractError("Task role and delivery kind are incompatible")


@dataclass(frozen=True, slots=True)
class TaskExecutionSnapshot:
    """Contain all current facts needed to decide agent dispatchability."""

    issue_status: IssueStatusName
    project_status: ProjectStatusName
    role_label: str
    delivery_kind: str
    label_name_list: list[str]
    assignee_id: str
    delegate_id: str
    execution_identity_id: str
    unresolved_blocker_count: int
    issue_contract_complete: bool

    def __post_init__(self) -> None:
        """Validate one complete status decision input."""

        if not isinstance(self.issue_status, IssueStatusName) or not isinstance(self.project_status, ProjectStatusName):
            raise LinearContractError("Task execution statuses are unsupported")
        task_delivery_validate(role_label=self.role_label, delivery_kind=self.delivery_kind)
        if (
            not isinstance(self.label_name_list, list)
            or len(self.label_name_list) != len(set(self.label_name_list))
            or any(
                not isinstance(item, str) or not item or any(character in item for character in "\x00\r\n")
                for item in self.label_name_list
            )
        ):
            raise LinearContractError("Task execution labels must be a duplicate-free single-line list")
        actual_role_set = set(self.label_name_list) & _ROLE_LABEL_SET
        if actual_role_set != {self.role_label}:
            raise LinearContractError("Task execution role must match the exact single Linear role label")
        if (
            isinstance(self.unresolved_blocker_count, bool)
            or not isinstance(self.unresolved_blocker_count, int)
            or self.unresolved_blocker_count < 0
        ):
            raise LinearContractError("Task unresolved blocker count must be non-negative")
        if not isinstance(self.issue_contract_complete, bool):
            raise LinearContractError("Task contract completeness must be boolean")
        assignment_id_list = [value for value in (self.assignee_id, self.delegate_id) if value]
        if len(assignment_id_list) != 1:
            raise LinearContractError("Task must have exactly one assignee or delegate")
        uuid_validate(assignment_id_list[0], label="Task execution assignment ID")
        uuid_validate(self.execution_identity_id, label="Execution identity ID")
        object.__setattr__(self, "label_name_list", list(self.label_name_list))

    def can_dispatch(self) -> bool:
        """Return whether a runner may own the next attempt.

        Returns:
            Dispatchability under the exact shared allowlist.
        """

        return not self.dispatch_blocker_list()

    @classmethod
    def from_dispatch_payload(cls, payload: object) -> "TaskExecutionSnapshot":
        """Parse one complete external task dispatch snapshot.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed dispatch snapshot.
        """

        expected = {
            "schema_version",
            "issue_status",
            "project_status",
            "role_label",
            "delivery_kind",
            "label_name_list",
            "assignee_id",
            "delegate_id",
            "execution_identity_id",
            "unresolved_blocker_count",
            "issue_contract_complete",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise LinearContractError("Task dispatch snapshot has another shape")
        if not isinstance(payload["label_name_list"], list):
            raise LinearContractError("Task dispatch labels must be a list")
        try:
            return cls(
                issue_status=issue_status_name_parse(payload["issue_status"]),
                project_status=ProjectStatusName(payload["project_status"]),
                role_label=payload["role_label"],
                delivery_kind=payload["delivery_kind"],
                label_name_list=list(payload["label_name_list"]),
                assignee_id=payload["assignee_id"],
                delegate_id=payload["delegate_id"],
                execution_identity_id=payload["execution_identity_id"],
                unresolved_blocker_count=payload["unresolved_blocker_count"],
                issue_contract_complete=payload["issue_contract_complete"],
            )
        except (TypeError, ValueError) as error:
            raise LinearContractError("Task dispatch snapshot contains an unsupported value") from error

    def dispatch_blocker_list(self) -> list[str]:
        """Return concise deterministic reasons why a new attempt cannot run."""

        blocker_list: list[str] = []
        if self.issue_status not in {
            IssueStatusName.TODO,
            IssueStatusName.IN_PROGRESS,
            IssueStatusName.REVIEW,
            IssueStatusName.REWORK,
            IssueStatusName.MERGING,
        }:
            blocker_list.append("issue-status-inactive")
        if self.project_status is not ProjectStatusName.IN_PROGRESS:
            blocker_list.append("project-not-active")
        if self.role_label == "task:human":
            blocker_list.append("human-task")
        if self.issue_status is IssueStatusName.REVIEW and self.role_label != "task:implementation":
            blocker_list.append("review-role-incompatible")
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
        return blocker_list


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
    handoff_ready: bool = False
    review_complete: bool = False
    reviewed_state_current: bool = False
    reviewed_state_changed: bool = False
    remediation_blocker_ready: bool = False
    review_finding_ready: bool = False
    merge_complete: bool = False
    cleanup_complete: bool = False

    def __post_init__(self) -> None:
        """Require every proof flag to be a real boolean."""

        boolean_by_field_name_map = {
            "human_decision": self.human_decision,
            "task_definition_ready": self.task_definition_ready,
            "fresh_thread": self.fresh_thread,
            "workspace_preserved": self.workspace_preserved,
            "result_ready": self.result_ready,
            "verification_ready": self.verification_ready,
            "publication_ready": self.publication_ready,
            "required_ci_ready": self.required_ci_ready,
            "evidence_ready": self.evidence_ready,
            "handoff_ready": self.handoff_ready,
            "review_complete": self.review_complete,
            "reviewed_state_current": self.reviewed_state_current,
            "reviewed_state_changed": self.reviewed_state_changed,
            "remediation_blocker_ready": self.remediation_blocker_ready,
            "review_finding_ready": self.review_finding_ready,
            "merge_complete": self.merge_complete,
            "cleanup_complete": self.cleanup_complete,
        }
        for field_name, value in boolean_by_field_name_map.items():
            if not isinstance(value, bool):
                raise LinearContractError(f"Transition proof {field_name} must be boolean")
        if self.reviewed_state_current and self.reviewed_state_changed:
            raise LinearContractError("Reviewed state cannot be both current and changed")
        if self.review_complete and self.review_finding_ready:
            raise LinearContractError("Review cannot be both complete and finding-bearing")
