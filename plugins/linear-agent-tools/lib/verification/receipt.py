"""Typed provider-owned comment envelopes for verification evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json

from json_contract import JsonContractError, json_load_strict
from verification._validation import VerificationReceiptError

_COMMENT_SUFFIX = "\n```"


@dataclass(frozen=True, slots=True)
class VerificationCommentCodec:
    """Render and parse one exact provider-owned Linear comment family."""

    prefix: str
    label: str

    def __post_init__(self) -> None:
        """Reject an ambiguous or unsafe comment marker."""

        if (
            not isinstance(self.prefix, str)
            or not self.prefix.startswith("<!-- linear-agent-tools-")
            or not self.prefix.endswith("-->\n```json\n")
            or not isinstance(self.label, str)
            or not self.label
            or any(character in self.label for character in "\x00\r\n")
        ):
            raise VerificationReceiptError("Verification comment codec has another shape")

    def payload_parse(self, value: str) -> object:
        """Decode one exact provider-owned comment payload.

        Args:
            value: Candidate Linear comment body.

        Returns:
            Decoded JSON payload.
        """

        if not isinstance(value, str) or not value.startswith(self.prefix) or not value.endswith(_COMMENT_SUFFIX):
            raise VerificationReceiptError(f"{self.label} comment has another shape")
        encoded = value[len(self.prefix) : -len(_COMMENT_SUFFIX)]
        try:
            return json_load_strict(encoded)
        except JsonContractError as error:
            raise VerificationReceiptError(f"{self.label} comment contains malformed JSON") from error

    def render(self, payload: dict[str, object]) -> str:
        """Render one canonical JSON payload as a marked Linear comment.

        Args:
            payload: Canonical JSON-ready object.

        Returns:
            Markdown comment body.
        """

        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).replace("/", r"\/")
        return self.prefix + encoded + _COMMENT_SUFFIX


ATTEMPT_COMMENT_CODEC = VerificationCommentCodec(
    prefix="<!-- linear-agent-tools-attempt:v2 -->\n```json\n",
    label="Attempt",
)
LOCAL_PHASE_BASELINE_COMMENT_CODEC = VerificationCommentCodec(
    prefix="<!-- linear-agent-tools-local-baseline:v1 -->\n```json\n",
    label="Local phase baseline",
)
TASK_WORKSPACE_BASELINE_COMMENT_CODEC = VerificationCommentCodec(
    prefix="<!-- linear-agent-tools-workspace-baseline:v1 -->\n```json\n",
    label="Workspace baseline",
)
VERIFICATION_RECEIPT_COMMENT_CODEC = VerificationCommentCodec(
    prefix="<!-- linear-agent-tools-verification:v4 -->\n```json\n",
    label="Verification receipt",
)
