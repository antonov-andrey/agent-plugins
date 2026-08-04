"""Closed Linear task status-transition policy."""

from __future__ import annotations

from linear_boundary.contract import LinearContractError
from linear_boundary.status import IssueStatusName, ProjectStatusName
from linear_boundary.task.model import TransitionProof, task_delivery_validate


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
    task_delivery_validate(role_label=role_label, delivery_kind=delivery_kind)
    if not isinstance(proof, TransitionProof) or not isinstance(dispatchable, bool):
        raise LinearContractError("Workflow transition proof or dispatch decision has another shape")
    if target is IssueStatusName.CANCELED and current not in {IssueStatusName.DONE, IssueStatusName.CANCELED}:
        if project_status is ProjectStatusName.COMPLETED:
            raise LinearContractError("A completed Project cannot cancel an unfinished task")
        if not proof.human_decision:
            raise LinearContractError("Canceling a non-terminal task requires an explicit human decision")
        return
    if current is IssueStatusName.BACKLOG and target is IssueStatusName.TODO:
        if project_status not in {ProjectStatusName.PLANNED, ProjectStatusName.IN_PROGRESS}:
            raise LinearContractError("Task activation requires a planned or active Project")
        if not proof.task_definition_ready:
            raise LinearContractError("Todo activation requires a complete task definition")
        return
    if project_status is not ProjectStatusName.IN_PROGRESS:
        raise LinearContractError("A non-cancellation task transition requires an active Project")
    if current in {IssueStatusName.TODO, IssueStatusName.REWORK} and target is IssueStatusName.IN_PROGRESS:
        if not dispatchable or not proof.fresh_thread:
            raise LinearContractError("Starting an attempt requires dispatchability and a fresh thread")
        if current is IssueStatusName.REWORK and delivery_kind == "code" and not proof.workspace_preserved:
            raise LinearContractError("Rework must adopt the existing candidate workspace")
        return
    if current is IssueStatusName.IN_PROGRESS and target is IssueStatusName.HUMAN_REVIEW:
        common_ready = all(
            (
                proof.result_ready,
                proof.verification_ready,
                proof.evidence_ready,
                proof.candidate_fingerprint_ready,
            )
        )
        if delivery_kind == "code":
            common_ready = common_ready and proof.publication_ready and proof.required_ci_ready
        elif delivery_kind != "evidence":
            raise LinearContractError("Only code or evidence delivery may enter Human Review")
        if not common_ready:
            raise LinearContractError("Human Review requires every delivery-applicable result and candidate proof")
        return
    if current is IssueStatusName.IN_PROGRESS and target is IssueStatusName.TODO:
        if role_label not in {"task:review", "task:acceptance"} or not proof.remediation_blocker_ready:
            raise LinearContractError("Only review or acceptance may return to Todo with a remediation blocker")
        return
    if current is IssueStatusName.HUMAN_REVIEW and target is IssueStatusName.REWORK:
        if not proof.human_decision and not proof.review_finding_ready:
            raise LinearContractError("Rework requires an explicit human decision or review finding")
        return
    if current is IssueStatusName.HUMAN_REVIEW and target is IssueStatusName.MERGING:
        if delivery_kind != "code" or not proof.human_decision or not proof.candidate_unchanged:
            raise LinearContractError("Merging requires human approval of the unchanged code candidate")
        return
    if current is IssueStatusName.HUMAN_REVIEW and target is IssueStatusName.DONE:
        if delivery_kind == "code" or not proof.human_decision or not proof.candidate_unchanged:
            raise LinearContractError("Non-code completion requires human approval of the unchanged evidence candidate")
        return
    if current is IssueStatusName.MERGING and target is IssueStatusName.DONE:
        if delivery_kind != "code" or role_label != "task:implementation":
            raise LinearContractError("Only a code implementation task may complete through Merging")
        if not proof.candidate_unchanged or not proof.merge_complete:
            raise LinearContractError("Merge completion must match the exact approved candidate")
        return
    if current is IssueStatusName.MERGING and target is IssueStatusName.REWORK:
        if delivery_kind != "code" or role_label != "task:implementation" or not proof.candidate_mutated:
            raise LinearContractError("A merging code task returns to Rework only after proven candidate mutation")
        return
    if current is IssueStatusName.IN_PROGRESS and target is IssueStatusName.DONE:
        if role_label != "task:cleanup" or not proof.cleanup_complete:
            raise LinearContractError("Only a successfully reconciled cleanup task may complete directly")
        return
    if current is IssueStatusName.TODO and target is IssueStatusName.DONE:
        if role_label != "task:human" or not proof.human_decision or not proof.evidence_ready:
            raise LinearContractError("Human task completion requires explicit human evidence")
        return
    raise LinearContractError(f"Workflow transition {current} -> {target} is forbidden")
