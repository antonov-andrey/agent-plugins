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

    def _attempt_cleanup_require(self) -> None:
        """Require nested attempt-resource cleanup before an agent transition."""

        if self.role_label != "task:human" and not self.proof.attempt_cleanup_complete:
            raise LinearContractError("Agent transition requires completed nested attempt-resource cleanup")

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
            self._attempt_cleanup_require()
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
                raise LinearContractError("Rework must adopt the existing task workspace")
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.IN_PROGRESS and self.target is IssueStatusName.REVIEW:
            common_ready = all(
                (
                    self.proof.result_ready,
                    self.proof.verification_ready,
                    self.proof.evidence_ready,
                    self.proof.handoff_ready,
                )
            )
            if self.role_label == "task:implementation" and self.delivery_kind == "code":
                common_ready = common_ready and self.proof.publication_ready and self.proof.required_ci_ready
            elif not (
                self.delivery_kind == "evidence" and self.role_label in {"task:implementation", "task:acceptance"}
            ):
                raise LinearContractError("Only implementation or final acceptance may enter Review")
            if self.role_label == "task:acceptance" and not self.proof.local_phase_baseline_readback_ready:
                raise LinearContractError(
                    "Final acceptance requires published and semantically provider-read local phase baseline evidence"
                )
            if not common_ready:
                raise LinearContractError("Review requires every delivery-applicable result and semantic handoff")
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.IN_PROGRESS and self.target is IssueStatusName.TODO:
            if (
                self.role_label not in {"task:review", "task:acceptance"}
                or not self.proof.remediation_blocker_ready
                or not self.proof.evidence_ready
                or not self.proof.handoff_ready
            ):
                raise LinearContractError(
                    "Only review or acceptance may return to Todo with a remediation blocker and semantic handoff"
                )
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.REVIEW and self.target is IssueStatusName.REWORK:
            implementation_finding = self.role_label == "task:implementation" and (
                self.proof.review_finding_ready or self.proof.reviewed_state_changed
            )
            acceptance_rejection = self.role_label == "task:acceptance" and self.proof.human_decision
            if (
                (not implementation_finding and not acceptance_rejection)
                or not self.proof.evidence_ready
                or not self.proof.handoff_ready
            ):
                raise LinearContractError(
                    "Rework requires an independent review finding or final human rejection with semantic handoff"
                )
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.REVIEW and self.target is IssueStatusName.MERGING:
            if (
                self.role_label != "task:implementation"
                or self.delivery_kind != "code"
                or not self.proof.review_complete
                or not self.proof.reviewed_state_current
                or not self.proof.evidence_ready
                or not self.proof.handoff_ready
            ):
                raise LinearContractError(
                    "Merging requires a zero-finding independent review handoff for the current PR identities"
                )
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.REVIEW and self.target is IssueStatusName.DONE:
            implementation_review = (
                self.role_label == "task:implementation"
                and self.delivery_kind == "evidence"
                and self.proof.review_complete
                and self.proof.reviewed_state_current
            )
            final_human_boundary = (
                self.role_label == "task:acceptance"
                and self.delivery_kind == "evidence"
                and self.proof.human_decision
                and self.proof.reviewed_state_current
            )
            if (
                (not implementation_review and not final_human_boundary)
                or not self.proof.evidence_ready
                or not self.proof.handoff_ready
            ):
                raise LinearContractError(
                    "Review completion requires independent evidence review or final human acceptance handoff"
                )
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.MERGING and self.target is IssueStatusName.DONE:
            if self.delivery_kind != "code" or self.role_label != "task:implementation":
                raise LinearContractError("Only a code implementation task may complete through Merging")
            if (
                not self.proof.reviewed_state_current
                or not self.proof.merge_complete
                or not self.proof.evidence_ready
                or not self.proof.handoff_ready
            ):
                raise LinearContractError(
                    "Merge completion requires a semantic handoff for the exact independently reviewed PR identities"
                )
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.MERGING and self.target is IssueStatusName.REWORK:
            if (
                self.delivery_kind != "code"
                or self.role_label != "task:implementation"
                or not self.proof.reviewed_state_changed
                or not self.proof.evidence_ready
                or not self.proof.handoff_ready
            ):
                raise LinearContractError(
                    "A merging code task returns to Rework only after a reviewed PR identity changed with semantic handoff"
                )
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.IN_PROGRESS and self.target is IssueStatusName.DONE:
            cleanup_complete = (
                self.role_label == "task:cleanup"
                and self.proof.cleanup_complete
                and self.proof.evidence_ready
                and self.proof.handoff_ready
            )
            semantic_review_complete = (
                self.role_label == "task:review"
                and self.delivery_kind == "evidence"
                and self.proof.review_complete
                and self.proof.evidence_ready
                and self.proof.handoff_ready
            )
            if not cleanup_complete and not semantic_review_complete:
                raise LinearContractError("Direct completion requires cleanup or a zero-finding semantic review")
            self._attempt_cleanup_require()
            return
        if self.current is IssueStatusName.TODO and self.target is IssueStatusName.DONE:
            if self.role_label != "task:human" or not self.proof.human_decision or not self.proof.evidence_ready:
                raise LinearContractError("Human task completion requires explicit human evidence")
            return
        raise LinearContractError(f"Workflow transition {self.current} -> {self.target} is forbidden")
