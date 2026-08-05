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
        if prior.source_fingerprint != self._current.source_fingerprint:
            reason_list.append("source-fingerprint-changed")
        if prior.checkout_list != self._current.checkout_list:
            reason_list.append("checkout-set-changed")
        if prior.corpus_content_sha256 != self._current.corpus_content_sha256:
            reason_list.append("corpus-content-changed")
        if prior.model_identity != self._current.model_identity:
            reason_list.append("model-identity-changed")
        if prior.model_configuration_by_name_map != self._current.model_configuration_by_name_map:
            reason_list.append("model-configuration-changed")
        if prior.environment_identity != self._current.environment_identity:
            reason_list.append("environment-identity-changed")
        if prior.release_identity != self._current.release_identity:
            reason_list.append("release-identity-changed")
        if receipt.verification_key != self._current.key() and not reason_list:
            reason_list.append("canonical-key-changed")
        return ReceiptDecision(reusable=not reason_list, reason_list=reason_list)
