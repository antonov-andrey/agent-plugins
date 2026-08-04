"""Closed Linear task status-transition policy."""

from __future__ import annotations

from dataclasses import dataclass

from linear_boundary.contract import LinearContractError
from linear_boundary.status import IssueStatusName, ProjectStatusName
from linear_boundary.task.model import TransitionProof, task_delivery_validate


@dataclass(frozen=True, slots=True)
class TaskTransition:
    """Bind one requested status transition to its complete semantic proof."""

    current: IssueStatusName
    target: IssueStatusName
    project_status: ProjectStatusName
    role_label: str
    delivery_kind: str
    proof: TransitionProof
    dispatchable: bool

    def require(self) -> None:
        """Require this exact workflow transition and its semantic evidence."""

        if (
            not isinstance(self.current, IssueStatusName)
            or not isinstance(self.target, IssueStatusName)
            or not isinstance(self.project_status, ProjectStatusName)
        ):
            raise LinearContractError("Workflow transition statuses are unsupported")
        task_delivery_validate(role_label=self.role_label, delivery_kind=self.delivery_kind)
        if not isinstance(self.proof, TransitionProof) or not isinstance(self.dispatchable, bool):
            raise LinearContractError("Workflow transition proof or dispatch decision has another shape")
        if self.target is IssueStatusName.CANCELED and self.current not in {
            IssueStatusName.DONE,
            IssueStatusName.CANCELED,
        }:
            if self.project_status is ProjectStatusName.COMPLETED:
                raise LinearContractError("A completed Project cannot cancel an unfinished task")
            if not self.proof.human_decision:
                raise LinearContractError("Canceling a non-terminal task requires an explicit human decision")
            return
        if self.current is IssueStatusName.BACKLOG and self.target is IssueStatusName.TODO:
            if self.project_status not in {
                ProjectStatusName.PLANNED,
                ProjectStatusName.IN_PROGRESS,
            }:
                raise LinearContractError("Task activation requires a planned or active Project")
            if not self.proof.task_definition_ready:
                raise LinearContractError("Todo activation requires a complete task definition")
            return
        if self.project_status is not ProjectStatusName.IN_PROGRESS:
            raise LinearContractError("A non-cancellation task transition requires an active Project")
        if (
            self.current in {IssueStatusName.TODO, IssueStatusName.REWORK}
            and self.target is IssueStatusName.IN_PROGRESS
        ):
            if not self.dispatchable or not self.proof.fresh_thread:
                raise LinearContractError("Starting an attempt requires dispatchability and a fresh thread")
            if (
                self.current is IssueStatusName.REWORK
                and self.delivery_kind == "code"
                and not self.proof.workspace_preserved
            ):
                raise LinearContractError("Rework must adopt the existing candidate workspace")
            return
        if self.current is IssueStatusName.IN_PROGRESS and self.target is IssueStatusName.HUMAN_REVIEW:
            common_ready = all(
                (
                    self.proof.result_ready,
                    self.proof.verification_ready,
                    self.proof.evidence_ready,
                    self.proof.candidate_fingerprint_ready,
                )
            )
            if self.delivery_kind == "code":
                common_ready = common_ready and self.proof.publication_ready and self.proof.required_ci_ready
            elif self.delivery_kind != "evidence":
                raise LinearContractError("Only code or evidence delivery may enter Human Review")
            if not common_ready:
                raise LinearContractError("Human Review requires every delivery-applicable result and candidate proof")
            return
        if self.current is IssueStatusName.IN_PROGRESS and self.target is IssueStatusName.TODO:
            if self.role_label not in {"task:review", "task:acceptance"} or not self.proof.remediation_blocker_ready:
                raise LinearContractError("Only review or acceptance may return to Todo with a remediation blocker")
            return
        if self.current is IssueStatusName.HUMAN_REVIEW and self.target is IssueStatusName.REWORK:
            if not self.proof.human_decision and not self.proof.review_finding_ready:
                raise LinearContractError("Rework requires an explicit human decision or review finding")
            return
        if self.current is IssueStatusName.HUMAN_REVIEW and self.target is IssueStatusName.MERGING:
            if self.delivery_kind != "code" or not self.proof.human_decision or not self.proof.candidate_unchanged:
                raise LinearContractError("Merging requires human approval of the unchanged code candidate")
            return
        if self.current is IssueStatusName.HUMAN_REVIEW and self.target is IssueStatusName.DONE:
            if self.delivery_kind == "code" or not self.proof.human_decision or not self.proof.candidate_unchanged:
                raise LinearContractError(
                    "Non-code completion requires human approval of the unchanged evidence candidate"
                )
            return
        if self.current is IssueStatusName.MERGING and self.target is IssueStatusName.DONE:
            if self.delivery_kind != "code" or self.role_label != "task:implementation":
                raise LinearContractError("Only a code implementation task may complete through Merging")
            if not self.proof.candidate_unchanged or not self.proof.merge_complete:
                raise LinearContractError("Merge completion must match the exact approved candidate")
            return
        if self.current is IssueStatusName.MERGING and self.target is IssueStatusName.REWORK:
            if (
                self.delivery_kind != "code"
                or self.role_label != "task:implementation"
                or not self.proof.candidate_mutated
            ):
                raise LinearContractError("A merging code task returns to Rework only after proven candidate mutation")
            return
        if self.current is IssueStatusName.IN_PROGRESS and self.target is IssueStatusName.DONE:
            if self.role_label != "task:cleanup" or not self.proof.cleanup_complete:
                raise LinearContractError("Only a successfully reconciled cleanup task may complete directly")
            return
        if self.current is IssueStatusName.TODO and self.target is IssueStatusName.DONE:
            if self.role_label != "task:human" or not self.proof.human_decision or not self.proof.evidence_ready:
                raise LinearContractError("Human task completion requires explicit human evidence")
            return
        raise LinearContractError(f"Workflow transition {self.current} -> {self.target} is forbidden")
