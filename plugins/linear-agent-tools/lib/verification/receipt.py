"""Create and transport concise receipts through Linear comments."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from verification._validation import VerificationReceiptError
from verification.attempt import AttemptSummary
from verification.baseline import LocalPhaseBaseline, TaskWorkspaceBaseline
from verification.model import VerificationInput, VerificationReceipt

_COMMENT_PREFIX = "<!-- linear-agent-tools-verification:v1 -->\n```json\n"
_COMMENT_SUFFIX = "\n```"
_ATTEMPT_COMMENT_PREFIX = "<!-- linear-agent-tools-attempt:v1 -->\n```json\n"
_BASELINE_COMMENT_PREFIX = "<!-- linear-agent-tools-local-baseline:v1 -->\n```json\n"
_WORKSPACE_BASELINE_COMMENT_PREFIX = "<!-- linear-agent-tools-workspace-baseline:v1 -->\n```json\n"


def _comment_render(payload: dict[str, object], *, prefix: str) -> str:
    """Render one provider-owned structured Linear comment.

    Args:
        payload: Canonical JSON-ready payload.
        prefix: Exact provider marker.

    Returns:
        Markdown comment body.
    """

    return prefix + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + _COMMENT_SUFFIX


def _comment_payload_parse(value: str, *, prefix: str, label: str) -> object:
    """Decode one exact provider-owned Linear comment.

    Args:
        value: Candidate comment body.
        prefix: Exact provider marker.
        label: Diagnostic owner label.

    Returns:
        Decoded JSON payload.
    """

    if not isinstance(value, str) or not value.startswith(prefix) or not value.endswith(_COMMENT_SUFFIX):
        raise VerificationReceiptError(f"{label} comment has another shape")
    encoded = value[len(prefix) : -len(_COMMENT_SUFFIX)]
    try:
        return json.loads(encoded)
    except json.JSONDecodeError as error:
        raise VerificationReceiptError(f"{label} comment contains malformed JSON") from error


def receipt_create(
    verification_input: VerificationInput,
    *,
    outcome: str,
    evidence_url: str,
    completed_at: datetime | None = None,
) -> VerificationReceipt:
    """Create one immutable receipt at an exact UTC instant.

    Args:
        verification_input: Complete declared inputs.
        outcome: Passed or failed.
        evidence_url: Link to the owning log or CI result.
        completed_at: Optional deterministic UTC instant.

    Returns:
        The typed receipt.
    """

    instant = completed_at or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise VerificationReceiptError("Receipt creation instant must be timezone-aware")
    return VerificationReceipt(
        verification_key=verification_input.key(),
        outcome=outcome,
        completed_at=instant.astimezone(timezone.utc),
        evidence_url=evidence_url,
        input=verification_input,
    )


def receipt_comment_render(receipt: VerificationReceipt) -> str:
    """Render one concise machine-readable Linear comment block.

    Args:
        receipt: Exact verification receipt.

    Returns:
        Markdown comment content.
    """

    return _comment_render(receipt.payload(), prefix=_COMMENT_PREFIX)


def receipt_comment_parse(value: str) -> VerificationReceipt:
    """Parse one exact provider-owned Linear receipt comment.

    Args:
        value: Candidate comment body.

    Returns:
        Typed verification receipt.
    """

    return VerificationReceipt.from_payload(
        _comment_payload_parse(value, prefix=_COMMENT_PREFIX, label="Verification receipt")
    )


def attempt_comment_render(summary: AttemptSummary) -> str:
    """Render one concise attempt summary for a Linear comment.

    Args:
        summary: Validated attempt summary.

    Returns:
        Markdown comment body.
    """

    return _comment_render(summary.payload(), prefix=_ATTEMPT_COMMENT_PREFIX)


def attempt_comment_parse(value: str) -> AttemptSummary:
    """Parse one exact provider-owned attempt comment.

    Args:
        value: Candidate comment body.

    Returns:
        Typed attempt summary.
    """

    return AttemptSummary.from_payload(_comment_payload_parse(value, prefix=_ATTEMPT_COMMENT_PREFIX, label="Attempt"))


def baseline_comment_render(baseline: LocalPhaseBaseline) -> str:
    """Render one complete local workflow baseline for a Linear comment.

    Args:
        baseline: Validated phase baseline.

    Returns:
        Markdown comment body.
    """

    return _comment_render(baseline.payload(), prefix=_BASELINE_COMMENT_PREFIX)


def baseline_comment_parse(value: str) -> LocalPhaseBaseline:
    """Parse one exact provider-owned local workflow baseline.

    Args:
        value: Candidate comment body.

    Returns:
        Typed phase baseline.
    """

    return LocalPhaseBaseline.from_payload(
        _comment_payload_parse(value, prefix=_BASELINE_COMMENT_PREFIX, label="Local phase baseline")
    )


def workspace_baseline_comment_render(baseline: TaskWorkspaceBaseline) -> str:
    """Render the immutable first-dispatch workspace baseline for Linear."""

    return _comment_render(baseline.payload(), prefix=_WORKSPACE_BASELINE_COMMENT_PREFIX)


def workspace_baseline_comment_parse(value: str) -> TaskWorkspaceBaseline:
    """Parse one exact first-dispatch workspace-baseline comment."""

    return TaskWorkspaceBaseline.from_payload(
        _comment_payload_parse(
            value,
            prefix=_WORKSPACE_BASELINE_COMMENT_PREFIX,
            label="Workspace baseline",
        )
    )
