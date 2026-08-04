"""Dependency-aware verification receipt contracts."""

from verification.invalidation import ReceiptDecision, receipt_reuse_decide
from verification._validation import VerificationReceiptError
from verification.attempt import AttemptSummary
from verification.baseline import LocalPhaseBaseline, TaskWorkspaceBaseline
from verification.candidate import CandidateInput
from verification.model import VerificationInput, VerificationReceipt
from verification.receipt import (
    attempt_comment_parse,
    attempt_comment_render,
    baseline_comment_parse,
    baseline_comment_render,
    receipt_comment_parse,
    receipt_comment_render,
    receipt_create,
    workspace_baseline_comment_parse,
    workspace_baseline_comment_render,
)

__all__ = [
    "AttemptSummary",
    "CandidateInput",
    "LocalPhaseBaseline",
    "TaskWorkspaceBaseline",
    "ReceiptDecision",
    "VerificationInput",
    "VerificationReceipt",
    "VerificationReceiptError",
    "attempt_comment_parse",
    "attempt_comment_render",
    "baseline_comment_parse",
    "baseline_comment_render",
    "receipt_comment_parse",
    "receipt_comment_render",
    "receipt_create",
    "receipt_reuse_decide",
    "workspace_baseline_comment_parse",
    "workspace_baseline_comment_render",
]
