"""Concise provider-owned agent-attempt telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from git_host.model import GitHubContractError, RepositoryIdentity
from verification._validation import (
    COMMIT_PATTERN,
    ISSUE_IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    UUID_PATTERN,
    VerificationReceiptError,
    instant_parse,
    instant_render,
    single_line_validate,
    text_by_text_map_parse,
    utc_validate,
)
from verification.candidate import CandidateIdentity
from verification.model import evidence_url_validate

_TASK_ROLE_SET = {
    "task:implementation",
    "task:review",
    "task:acceptance",
    "task:cleanup",
}
_DELIVERY_BY_ROLE = {
    "task:implementation": {"code", "evidence"},
    "task:review": {"evidence"},
    "task:acceptance": {"evidence"},
    "task:cleanup": {"cleanup"},
}
_ATTEMPT_OUTCOME_SET = {
    "human-review",
    "remediation-required",
    "merged",
    "cleanup-complete",
    "canceled",
    "failed",
    "interrupted",
}
_OUTCOME_SET_BY_ROLE = {
    "task:implementation": {
        "human-review",
        "merged",
        "canceled",
        "failed",
        "interrupted",
    },
    "task:review": {
        "human-review",
        "remediation-required",
        "canceled",
        "failed",
        "interrupted",
    },
    "task:acceptance": {
        "human-review",
        "remediation-required",
        "canceled",
        "failed",
        "interrupted",
    },
    "task:cleanup": {"cleanup-complete", "canceled", "failed", "interrupted"},
}
_CANDIDATE_OUTCOME_SET = {"human-review", "merged"}
_EVIDENCE_OUTCOME_SET = {
    "human-review",
    "remediation-required",
    "merged",
    "cleanup-complete",
}


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    """Contain one concise Linear-native agent-attempt summary."""

    attempt_id: str
    issue_identifier: str
    role_label: str
    delivery_kind: str
    started_at: datetime
    completed_at: datetime
    outcome: str
    changed_commit_by_repository_map: dict[str, str]
    receipt_hit_count: int
    receipt_miss_count: int
    external_wait_seconds: float
    token_count: int | None
    candidate_identity: CandidateIdentity | None
    candidate_fingerprint: str
    evidence_url_list: list[str]

    def __post_init__(self) -> None:
        """Validate bounded telemetry without accepting raw logs or prompts."""

        if not isinstance(self.attempt_id, str) or UUID_PATTERN.fullmatch(self.attempt_id) is None:
            raise VerificationReceiptError("Attempt identity must be one lowercase UUID")
        if (
            not isinstance(self.issue_identifier, str)
            or ISSUE_IDENTIFIER_PATTERN.fullmatch(self.issue_identifier) is None
        ):
            raise VerificationReceiptError("Attempt issue identifier has another shape")
        if not isinstance(self.role_label, str) or self.role_label not in _TASK_ROLE_SET:
            raise VerificationReceiptError("Attempt role is unsupported")
        if not isinstance(self.delivery_kind, str) or self.delivery_kind not in _DELIVERY_BY_ROLE[self.role_label]:
            raise VerificationReceiptError("Attempt role and delivery kind are incompatible")
        utc_validate(self.started_at, label="Attempt start")
        utc_validate(self.completed_at, label="Attempt completion")
        if self.completed_at < self.started_at:
            raise VerificationReceiptError("Attempt completion precedes its start")
        if not isinstance(self.outcome, str) or self.outcome not in _ATTEMPT_OUTCOME_SET:
            raise VerificationReceiptError("Attempt outcome is unsupported")
        if self.outcome not in _OUTCOME_SET_BY_ROLE[self.role_label]:
            raise VerificationReceiptError("Attempt role and outcome are incompatible")
        if not isinstance(self.changed_commit_by_repository_map, dict):
            raise VerificationReceiptError("Attempt commit set must be a mapping")
        for repository, commit in self.changed_commit_by_repository_map.items():
            try:
                RepositoryIdentity(repository)
            except GitHubContractError as error:
                raise VerificationReceiptError("Attempt repository must use exact owner/name form") from error
            if COMMIT_PATTERN.fullmatch(commit) is None:
                raise VerificationReceiptError("Attempt commit is not a full lowercase identity")
        if self.delivery_kind != "code" and self.changed_commit_by_repository_map:
            raise VerificationReceiptError("Non-code attempt cannot report changed Product commits")
        if (
            self.delivery_kind == "code"
            and self.outcome in _CANDIDATE_OUTCOME_SET
            and not self.changed_commit_by_repository_map
        ):
            raise VerificationReceiptError("Completed code delivery requires one or more changed Product commits")
        if self.outcome == "merged" and self.delivery_kind != "code":
            raise VerificationReceiptError("Only code delivery may report a merged outcome")
        for label, value in (
            ("receipt hit count", self.receipt_hit_count),
            ("receipt miss count", self.receipt_miss_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise VerificationReceiptError(f"Attempt {label} must be a non-negative integer")
        if (
            isinstance(self.external_wait_seconds, bool)
            or not isinstance(self.external_wait_seconds, (int, float))
            or not math.isfinite(self.external_wait_seconds)
            or self.external_wait_seconds < 0
        ):
            raise VerificationReceiptError("Attempt external wait must be non-negative seconds")
        if self.token_count is not None and (
            isinstance(self.token_count, bool) or not isinstance(self.token_count, int) or self.token_count < 0
        ):
            raise VerificationReceiptError("Attempt token count must be absent or a non-negative integer")
        if not isinstance(self.candidate_fingerprint, str) or (
            self.candidate_fingerprint and SHA256_PATTERN.fullmatch(self.candidate_fingerprint) is None
        ):
            raise VerificationReceiptError("Attempt candidate fingerprint must be empty or SHA-256")
        have_candidate = self.outcome in _CANDIDATE_OUTCOME_SET
        if self.candidate_identity is not None and not isinstance(self.candidate_identity, CandidateIdentity):
            raise VerificationReceiptError("Attempt candidate identity has another shape")
        if have_candidate != (self.candidate_identity is not None):
            raise VerificationReceiptError("Attempt candidate identity is incompatible with its outcome")
        if have_candidate and self.candidate_identity.delivery_kind != self.delivery_kind:
            raise VerificationReceiptError("Attempt delivery kind differs from its candidate identity")
        if have_candidate and self.candidate_identity.fingerprint() != self.candidate_fingerprint:
            raise VerificationReceiptError("Attempt candidate fingerprint differs from its persisted identity")
        if not have_candidate and self.candidate_fingerprint:
            raise VerificationReceiptError("Attempt candidate fingerprint is incompatible with its outcome")
        if (
            not isinstance(self.evidence_url_list, list)
            or self.evidence_url_list != sorted(self.evidence_url_list)
            or len(self.evidence_url_list) != len(set(self.evidence_url_list))
        ):
            raise VerificationReceiptError("Attempt evidence links must be unique and sorted")
        for value in self.evidence_url_list:
            evidence_url_validate(value)
        if self.outcome in _EVIDENCE_OUTCOME_SET and not self.evidence_url_list:
            raise VerificationReceiptError("Completed attempt outcome requires bounded evidence links")
        object.__setattr__(
            self,
            "changed_commit_by_repository_map",
            dict(sorted(self.changed_commit_by_repository_map.items())),
        )
        object.__setattr__(self, "evidence_url_list", list(self.evidence_url_list))

    def payload(self) -> dict[str, object]:
        """Return canonical concise attempt telemetry.

        Returns:
            JSON-ready attempt summary.
        """

        payload: dict[str, object] = {
            "schema_version": 2,
            "attempt_id": self.attempt_id,
            "candidate_identity": (None if self.candidate_identity is None else self.candidate_identity.payload()),
            "candidate_fingerprint": self.candidate_fingerprint,
            "changed_commit_by_repository_map": dict(self.changed_commit_by_repository_map),
            "completed_at": instant_render(self.completed_at),
            "delivery_kind": self.delivery_kind,
            "evidence_url_list": list(self.evidence_url_list),
            "external_wait_seconds": self.external_wait_seconds,
            "issue_identifier": self.issue_identifier,
            "outcome": self.outcome,
            "receipt_hit_count": self.receipt_hit_count,
            "receipt_miss_count": self.receipt_miss_count,
            "role_label": self.role_label,
            "started_at": instant_render(self.started_at),
        }
        if self.token_count is not None:
            payload["token_count"] = self.token_count
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "AttemptSummary":
        """Parse one strict attempt summary.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed attempt summary.
        """

        required = {
            "schema_version",
            "attempt_id",
            "candidate_identity",
            "candidate_fingerprint",
            "changed_commit_by_repository_map",
            "completed_at",
            "delivery_kind",
            "evidence_url_list",
            "external_wait_seconds",
            "issue_identifier",
            "outcome",
            "receipt_hit_count",
            "receipt_miss_count",
            "role_label",
            "started_at",
        }
        allowed = required | {"token_count"}
        if (
            not isinstance(payload, dict)
            or (set(payload) != required and set(payload) != allowed)
            or payload["schema_version"] != 2
        ):
            raise VerificationReceiptError("Attempt summary has another shape")
        evidence_url_list = payload["evidence_url_list"]
        if not isinstance(evidence_url_list, list) or any(not isinstance(item, str) for item in evidence_url_list):
            raise VerificationReceiptError("Attempt evidence URLs must be a string list")
        return cls(
            attempt_id=payload["attempt_id"],
            issue_identifier=payload["issue_identifier"],
            role_label=payload["role_label"],
            delivery_kind=payload["delivery_kind"],
            started_at=instant_parse(payload["started_at"], label="Attempt start"),
            completed_at=instant_parse(payload["completed_at"], label="Attempt completion"),
            outcome=payload["outcome"],
            changed_commit_by_repository_map=text_by_text_map_parse(
                payload["changed_commit_by_repository_map"], label="attempt commits"
            ),
            receipt_hit_count=payload["receipt_hit_count"],
            receipt_miss_count=payload["receipt_miss_count"],
            external_wait_seconds=payload["external_wait_seconds"],
            token_count=payload.get("token_count"),
            candidate_identity=(
                None
                if payload["candidate_identity"] is None
                else CandidateIdentity.from_payload(payload["candidate_identity"])
            ),
            candidate_fingerprint=payload["candidate_fingerprint"],
            evidence_url_list=list(evidence_url_list),
        )
