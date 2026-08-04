"""Exact Human Review candidate identity."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import urlsplit

from verification._validation import (
    COMMIT_PATTERN,
    VerificationReceiptError,
    single_line_validate,
    text_pair_tuple,
)

_GITHUB_PULL_REQUEST_PATH_PATTERN = re.compile(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*")


@dataclass(frozen=True, slots=True)
class CandidateInput:
    """Own the exact external identities approved at Human Review."""

    delivery_kind: str
    pull_request_head_by_url: tuple[tuple[str, str], ...]
    evidence_identity_by_kind: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """Require one code or evidence candidate with no mixed identity surface."""

        if not isinstance(self.delivery_kind, str) or self.delivery_kind not in {
            "code",
            "evidence",
        }:
            raise VerificationReceiptError("Candidate delivery kind must be code or evidence")
        for label, value in (
            ("pull-request heads", self.pull_request_head_by_url),
            ("evidence identities", self.evidence_identity_by_kind),
        ):
            if not isinstance(value, tuple) or any(not isinstance(item, tuple) or len(item) != 2 for item in value):
                raise VerificationReceiptError(f"Candidate {label} contains a malformed pair")
            if (
                value != tuple(sorted(value))
                or len(value) != len(set(value))
                or len({key for key, _identity in value}) != len(value)
            ):
                raise VerificationReceiptError(f"Candidate {label} must be unique and sorted")
            for key, identity in value:
                single_line_validate(key, label=f"Candidate {label} key")
                single_line_validate(identity, label=f"Candidate {label} identity")
        if self.delivery_kind == "code":
            if not self.pull_request_head_by_url or self.evidence_identity_by_kind:
                raise VerificationReceiptError("Code candidate requires only one or more exact pull-request heads")
            if any(COMMIT_PATTERN.fullmatch(commit) is None for _url, commit in self.pull_request_head_by_url):
                raise VerificationReceiptError("Code candidate pull-request head is not a full lowercase commit")
            for url, _commit in self.pull_request_head_by_url:
                parsed = urlsplit(url)
                if (
                    parsed.scheme != "https"
                    or parsed.netloc.lower() != "github.com"
                    or parsed.query
                    or parsed.fragment
                    or _GITHUB_PULL_REQUEST_PATH_PATTERN.fullmatch(parsed.path) is None
                ):
                    raise VerificationReceiptError("Code candidate pull-request URL is not one canonical GitHub PR")
        elif self.pull_request_head_by_url or not self.evidence_identity_by_kind:
            raise VerificationReceiptError("Evidence candidate requires only one or more exact evidence identities")

    def payload(self) -> dict[str, object]:
        """Return the canonical candidate input.

        Returns:
            JSON-ready candidate input.
        """

        return {
            "delivery_kind": self.delivery_kind,
            "evidence_identity_by_kind": [list(item) for item in self.evidence_identity_by_kind],
            "pull_request_head_by_url": [list(item) for item in self.pull_request_head_by_url],
        }

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
    def from_payload(cls, payload: object) -> "CandidateInput":
        """Parse one strict candidate input.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed candidate input.
        """

        expected = {
            "delivery_kind",
            "evidence_identity_by_kind",
            "pull_request_head_by_url",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise VerificationReceiptError("Candidate input has another shape")
        return cls(
            delivery_kind=payload["delivery_kind"],
            pull_request_head_by_url=text_pair_tuple(payload["pull_request_head_by_url"], label="pull-request heads"),
            evidence_identity_by_kind=text_pair_tuple(
                payload["evidence_identity_by_kind"], label="evidence identities"
            ),
        )
