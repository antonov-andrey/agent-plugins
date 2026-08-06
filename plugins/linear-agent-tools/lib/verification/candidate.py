"""Exact Human Review candidate identity."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import urlsplit

from verification._validation import (
    COMMIT_PATTERN,
    SHA256_PATTERN,
    VerificationReceiptError,
    single_line_validate,
    text_by_text_map_parse,
)
from verification.model import VerificationReceipt

_GITHUB_PULL_REQUEST_PATH_PATTERN = re.compile(
    r"/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repository>[A-Za-z0-9_.-]+)/pull/[1-9][0-9]*"
)


def _github_pull_request_url_validate(value: str) -> None:
    """Require one exact provider-form GitHub pull-request URL."""

    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise VerificationReceiptError("Code candidate pull-request URL is not one canonical GitHub PR") from error
    path_match = _GITHUB_PULL_REQUEST_PATH_PATTERN.fullmatch(parsed.path)
    if (
        not value.isascii()
        or parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or value != f"https://github.com{parsed.path}"
        or path_match is None
        or path_match.group("owner") in {".", ".."}
        or path_match.group("repository") in {".", ".."}
    ):
        raise VerificationReceiptError("Code candidate pull-request URL is not one canonical GitHub PR")


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    """Persist the compact exact state approved at Human Review."""

    delivery_kind: str
    evidence_receipt_key_by_kind_map: dict[str, str]
    pull_request_head_by_url_map: dict[str, str]

    def __post_init__(self) -> None:
        """Require one code or evidence identity with no mixed surface."""

        if not isinstance(self.delivery_kind, str) or self.delivery_kind not in {"code", "evidence"}:
            raise VerificationReceiptError("Candidate identity delivery kind must be code or evidence")
        for label, value_by_name_map in (
            ("evidence receipt keys", self.evidence_receipt_key_by_kind_map),
            ("pull-request heads", self.pull_request_head_by_url_map),
        ):
            if not isinstance(value_by_name_map, dict):
                raise VerificationReceiptError(f"Candidate identity {label} must be a mapping")
            for name, identity in value_by_name_map.items():
                single_line_validate(name, label=f"Candidate identity {label} name")
                single_line_validate(identity, label=f"Candidate identity {label} value")
        if self.delivery_kind == "code":
            if not self.pull_request_head_by_url_map or self.evidence_receipt_key_by_kind_map:
                raise VerificationReceiptError("Code candidate identity requires only exact pull-request heads")
            if any(COMMIT_PATTERN.fullmatch(commit) is None for commit in self.pull_request_head_by_url_map.values()):
                raise VerificationReceiptError("Code candidate pull-request head is not a full lowercase commit")
            for url in self.pull_request_head_by_url_map:
                _github_pull_request_url_validate(url)
        elif self.pull_request_head_by_url_map or not self.evidence_receipt_key_by_kind_map:
            raise VerificationReceiptError("Evidence candidate identity requires only exact receipt keys")
        elif any(SHA256_PATTERN.fullmatch(key) is None for key in self.evidence_receipt_key_by_kind_map.values()):
            raise VerificationReceiptError("Evidence candidate receipt key is not SHA-256")
        object.__setattr__(
            self,
            "evidence_receipt_key_by_kind_map",
            dict(sorted(self.evidence_receipt_key_by_kind_map.items())),
        )
        object.__setattr__(
            self,
            "pull_request_head_by_url_map",
            dict(sorted(self.pull_request_head_by_url_map.items())),
        )

    def fingerprint(self) -> str:
        """Return the immutable candidate SHA-256.

        Returns:
            Lowercase candidate fingerprint.
        """

        return hashlib.sha256(
            json.dumps(
                self.payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_payload(cls, payload: object) -> "CandidateIdentity":
        """Parse one strict compact candidate identity.

        Args:
            payload: Candidate JSON value.

        Returns:
            Validated compact identity.
        """

        expected = {
            "delivery_kind",
            "evidence_receipt_key_by_kind_map",
            "pull_request_head_by_url_map",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise VerificationReceiptError("Candidate identity has another shape")
        return cls(
            delivery_kind=payload["delivery_kind"],
            evidence_receipt_key_by_kind_map=text_by_text_map_parse(
                payload["evidence_receipt_key_by_kind_map"], label="evidence receipt keys"
            ),
            pull_request_head_by_url_map=text_by_text_map_parse(
                payload["pull_request_head_by_url_map"], label="pull-request heads"
            ),
        )

    def payload(self) -> dict[str, object]:
        """Return the persisted compact identity.

        Returns:
            Canonical PR heads or receipt keys by evidence kind.
        """

        return {
            "delivery_kind": self.delivery_kind,
            "evidence_receipt_key_by_kind_map": dict(self.evidence_receipt_key_by_kind_map),
            "pull_request_head_by_url_map": dict(self.pull_request_head_by_url_map),
        }


@dataclass(frozen=True, slots=True)
class CandidateInput:
    """Own the exact external identities approved at Human Review."""

    delivery_kind: str
    pull_request_head_by_url_map: dict[str, str]
    evidence_receipt_by_kind_map: dict[str, VerificationReceipt]

    def __post_init__(self) -> None:
        """Require one code or evidence candidate with no mixed identity surface."""

        if not isinstance(self.delivery_kind, str) or self.delivery_kind not in {
            "code",
            "evidence",
        }:
            raise VerificationReceiptError("Candidate delivery kind must be code or evidence")
        if not isinstance(self.pull_request_head_by_url_map, dict):
            raise VerificationReceiptError("Candidate pull-request heads must be a mapping")
        for url, commit in self.pull_request_head_by_url_map.items():
            single_line_validate(url, label="Candidate pull-request heads key")
            single_line_validate(commit, label="Candidate pull-request heads identity")
        if not isinstance(self.evidence_receipt_by_kind_map, dict):
            raise VerificationReceiptError("Candidate evidence receipts must be a mapping")
        for evidence_kind, receipt in self.evidence_receipt_by_kind_map.items():
            single_line_validate(evidence_kind, label="Candidate evidence receipt kind")
            if not isinstance(receipt, VerificationReceipt):
                raise VerificationReceiptError("Candidate evidence receipt must use the current receipt schema")
            if receipt.outcome != "passed":
                raise VerificationReceiptError("Candidate evidence receipt must record a passed outcome")
        object.__setattr__(
            self,
            "pull_request_head_by_url_map",
            dict(sorted(self.pull_request_head_by_url_map.items())),
        )
        object.__setattr__(
            self,
            "evidence_receipt_by_kind_map",
            dict(sorted(self.evidence_receipt_by_kind_map.items())),
        )
        self.identity_get()

    def payload(self) -> dict[str, object]:
        """Return the canonical candidate input.

        Returns:
            JSON-ready candidate input.
        """

        return {
            "delivery_kind": self.delivery_kind,
            "evidence_receipt_by_kind_map": {
                evidence_kind: receipt.payload() for evidence_kind, receipt in self.evidence_receipt_by_kind_map.items()
            },
            "pull_request_head_by_url_map": dict(self.pull_request_head_by_url_map),
        }

    def identity_get(self) -> CandidateIdentity:
        """Return the exact compact identity approved at Human Review.

        Returns:
            Validated compact candidate identity.
        """

        return CandidateIdentity(
            delivery_kind=self.delivery_kind,
            evidence_receipt_key_by_kind_map={
                evidence_kind: receipt.receipt_key
                for evidence_kind, receipt in self.evidence_receipt_by_kind_map.items()
            },
            pull_request_head_by_url_map=dict(self.pull_request_head_by_url_map),
        )

    def fingerprint(self) -> str:
        """Return the immutable candidate SHA-256.

        Returns:
            Lowercase candidate fingerprint.
        """

        return self.identity_get().fingerprint()

    @classmethod
    def from_payload(cls, payload: object) -> "CandidateInput":
        """Parse one strict candidate input.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed candidate input.
        """

        expected = {
            "delivery_kind",
            "evidence_receipt_by_kind_map",
            "pull_request_head_by_url_map",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise VerificationReceiptError("Candidate input has another shape")
        return cls(
            delivery_kind=payload["delivery_kind"],
            pull_request_head_by_url_map=text_by_text_map_parse(
                payload["pull_request_head_by_url_map"], label="pull-request heads"
            ),
            evidence_receipt_by_kind_map=_evidence_receipt_by_kind_map_parse(payload["evidence_receipt_by_kind_map"]),
        )


def _evidence_receipt_by_kind_map_parse(value: object) -> dict[str, VerificationReceipt]:
    """Parse one closed mapping of evidence kinds to current-schema receipts.

    Args:
        value: Candidate JSON value.

    Returns:
        Canonically ordered typed receipt mapping.
    """

    if not isinstance(value, dict):
        raise VerificationReceiptError("Candidate evidence receipts must be a mapping")
    receipt_by_kind_map: dict[str, VerificationReceipt] = {}
    for evidence_kind, receipt_payload in value.items():
        parsed_kind = single_line_validate(evidence_kind, label="Candidate evidence receipt kind")
        receipt_by_kind_map[parsed_kind] = VerificationReceipt.from_payload(receipt_payload)
    return dict(sorted(receipt_by_kind_map.items()))
