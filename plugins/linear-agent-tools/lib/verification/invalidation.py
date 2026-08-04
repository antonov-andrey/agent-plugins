"""Exact receipt reuse and invalidation decisions."""

from __future__ import annotations

from dataclasses import dataclass

from verification._validation import VerificationReceiptError
from verification.model import VerificationInput, VerificationReceipt


@dataclass(frozen=True, slots=True)
class ReceiptDecision:
    """Describe whether one prior receipt is reusable and why."""

    reusable: bool
    reason_list: list[str]

    def __post_init__(self) -> None:
        """Detach the concise reason list from caller mutation."""

        if not isinstance(self.reusable, bool) or not isinstance(self.reason_list, list):
            raise VerificationReceiptError("Receipt decision has another shape")
        object.__setattr__(self, "reason_list", list(self.reason_list))


class ReceiptReuseEvaluator:
    """Compare prior receipts with one complete current verification input."""

    def __init__(self, current: VerificationInput) -> None:
        """Bind the exact current dependency state.

        Args:
            current: Complete current verification inputs.
        """

        if not isinstance(current, VerificationInput):
            raise VerificationReceiptError("Current verification input has another shape")
        self._current = current

    def decision_get(self, receipt: VerificationReceipt) -> ReceiptDecision:
        """Compare every declared dependency rather than command text alone.

        Args:
            receipt: Prior immutable receipt.

        Returns:
            Reuse decision with concise invalidation reasons.
        """

        reason_list: list[str] = []
        prior = receipt.input
        if receipt.outcome != "passed":
            reason_list.append("prior-outcome-not-passed")
        if prior.command_argument_list != self._current.command_argument_list:
            reason_list.append("command-changed")
        if prior.working_directory != self._current.working_directory:
            reason_list.append("working-directory-changed")
        if prior.repository_url != self._current.repository_url:
            reason_list.append("verification-repository-changed")
        if prior.source_fingerprint != self._current.source_fingerprint:
            reason_list.append("source-fingerprint-changed")
        if prior.repository_commit_by_url_map != self._current.repository_commit_by_url_map:
            reason_list.append("repository-commit-set-changed")
        if prior.recursive_submodule_commit_by_path_map != self._current.recursive_submodule_commit_by_path_map:
            reason_list.append("recursive-submodule-set-changed")
        if prior.dependency_lock_sha256_by_path_map != self._current.dependency_lock_sha256_by_path_map:
            reason_list.append("dependency-lock-set-changed")
        if prior.environment_identity != self._current.environment_identity:
            reason_list.append("environment-identity-changed")
        if prior.release_identity != self._current.release_identity:
            reason_list.append("release-identity-changed")
        if receipt.verification_key != self._current.key() and not reason_list:
            reason_list.append("canonical-key-changed")
        return ReceiptDecision(reusable=not reason_list, reason_list=reason_list)
