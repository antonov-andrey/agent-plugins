"""Closed models for reusable verification receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json

from verification._validation import (
    COMMIT_PATTERN,
    SHA256_PATTERN,
    VerificationReceiptError,
    instant_parse,
    single_line_validate,
    text_by_text_map_parse,
    utc_validate,
)


@dataclass(frozen=True, slots=True)
class VerificationInput:
    """Own every input that can change one verification result."""

    command_argument_list: list[str]
    working_directory: str
    repository_url: str
    source_fingerprint: str
    repository_commit_by_url_map: dict[str, str]
    recursive_submodule_commit_by_path_map: dict[str, str]
    dependency_lock_sha256_by_path_map: dict[str, str]
    environment_identity: str
    release_identity: str

    def __post_init__(self) -> None:
        """Validate the complete receipt input boundary."""

        if (
            not isinstance(self.command_argument_list, list)
            or not self.command_argument_list
            or any(not isinstance(item, str) or not item or "\x00" in item for item in self.command_argument_list)
        ):
            raise VerificationReceiptError("Verification command must be direct non-empty argv")
        single_line_validate(self.working_directory, label="Verification working directory")
        single_line_validate(self.repository_url, label="Verification repository URL", empty_allowed=True)
        if not isinstance(self.source_fingerprint, str) or SHA256_PATTERN.fullmatch(self.source_fingerprint) is None:
            raise VerificationReceiptError("Verification source fingerprint must be SHA-256")
        for label, value in (
            ("repository commits", self.repository_commit_by_url_map),
            ("recursive submodule commits", self.recursive_submodule_commit_by_path_map),
            ("dependency lock fingerprints", self.dependency_lock_sha256_by_path_map),
        ):
            if not isinstance(value, dict):
                raise VerificationReceiptError(f"{label} must be a mapping")
            for path, identity in value.items():
                single_line_validate(path, label=f"Verification {label} path")
                single_line_validate(identity, label=f"Verification {label} identity")
        if any(COMMIT_PATTERN.fullmatch(commit) is None for commit in self.repository_commit_by_url_map.values()):
            raise VerificationReceiptError("Repository commit is not a full lowercase identity")
        repository_url_set = set(self.repository_commit_by_url_map)
        if self.repository_url and self.repository_url not in repository_url_set:
            raise VerificationReceiptError("Verification repository URL has no exact repository commit")
        if not self.repository_url and (
            self.recursive_submodule_commit_by_path_map or self.dependency_lock_sha256_by_path_map
        ):
            raise VerificationReceiptError(
                "Repository-scoped submodule or dependency-lock inputs require a verification repository URL"
            )
        if any(
            COMMIT_PATTERN.fullmatch(commit) is None for commit in self.recursive_submodule_commit_by_path_map.values()
        ):
            raise VerificationReceiptError("Submodule commit is not a full lowercase identity")
        if any(SHA256_PATTERN.fullmatch(digest) is None for digest in self.dependency_lock_sha256_by_path_map.values()):
            raise VerificationReceiptError("Dependency lock fingerprint is not SHA-256")
        for label, value in (
            ("environment identity", self.environment_identity),
            ("release identity", self.release_identity),
        ):
            single_line_validate(value, label=f"Verification {label}", empty_allowed=True)
        object.__setattr__(self, "command_argument_list", list(self.command_argument_list))
        object.__setattr__(
            self,
            "repository_commit_by_url_map",
            dict(sorted(self.repository_commit_by_url_map.items())),
        )
        object.__setattr__(
            self,
            "recursive_submodule_commit_by_path_map",
            dict(sorted(self.recursive_submodule_commit_by_path_map.items())),
        )
        object.__setattr__(
            self,
            "dependency_lock_sha256_by_path_map",
            dict(sorted(self.dependency_lock_sha256_by_path_map.items())),
        )

    def key(self) -> str:
        """Return SHA-256 of every declared verification input.

        Returns:
            Lowercase receipt key.
        """

        return hashlib.sha256(
            json.dumps(
                self.payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def payload(self) -> dict[str, object]:
        """Return the canonical JSON-ready input object.

        Returns:
            Input payload.
        """

        return {
            "command_argument_list": list(self.command_argument_list),
            "dependency_lock_sha256_by_path_map": dict(self.dependency_lock_sha256_by_path_map),
            "environment_identity": self.environment_identity,
            "repository_url": self.repository_url,
            "recursive_submodule_commit_by_path_map": dict(self.recursive_submodule_commit_by_path_map),
            "release_identity": self.release_identity,
            "repository_commit_by_url_map": dict(self.repository_commit_by_url_map),
            "source_fingerprint": self.source_fingerprint,
            "working_directory": self.working_directory,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "VerificationInput":
        """Parse one strict receipt input.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed verification input.
        """

        expected = {
            "command_argument_list",
            "dependency_lock_sha256_by_path_map",
            "environment_identity",
            "recursive_submodule_commit_by_path_map",
            "release_identity",
            "repository_url",
            "repository_commit_by_url_map",
            "source_fingerprint",
            "working_directory",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise VerificationReceiptError("Verification input has another shape")
        command_list = payload["command_argument_list"]
        if not isinstance(command_list, list) or any(not isinstance(item, str) for item in command_list):
            raise VerificationReceiptError("Verification command must be a string list")
        return cls(
            command_argument_list=list(command_list),
            working_directory=payload["working_directory"],
            repository_url=payload["repository_url"],
            repository_commit_by_url_map=text_by_text_map_parse(
                payload["repository_commit_by_url_map"],
                label="repository commits",
                empty_allowed=True,
            ),
            recursive_submodule_commit_by_path_map=text_by_text_map_parse(
                payload["recursive_submodule_commit_by_path_map"],
                label="recursive submodule commits",
                empty_allowed=True,
            ),
            dependency_lock_sha256_by_path_map=text_by_text_map_parse(
                payload["dependency_lock_sha256_by_path_map"],
                label="dependency lock fingerprints",
                empty_allowed=True,
            ),
            environment_identity=payload["environment_identity"],
            release_identity=payload["release_identity"],
            source_fingerprint=payload["source_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class VerificationReceipt:
    """Contain one concise immutable verification result and its exact inputs."""

    verification_key: str
    outcome: str
    completed_at: datetime
    evidence_url: str
    input: VerificationInput

    def __post_init__(self) -> None:
        """Validate one receipt result."""

        if not isinstance(self.input, VerificationInput) or (
            not isinstance(self.verification_key, str)
            or SHA256_PATTERN.fullmatch(self.verification_key) is None
            or self.verification_key != self.input.key()
        ):
            raise VerificationReceiptError("Verification receipt key differs from its inputs")
        if not isinstance(self.outcome, str) or self.outcome not in {
            "passed",
            "failed",
        }:
            raise VerificationReceiptError("Verification receipt outcome is unsupported")
        utc_validate(self.completed_at, label="Verification completion instant")
        if (
            not isinstance(self.evidence_url, str)
            or not self.evidence_url
            or any(character in self.evidence_url for character in ("\x00", "\n", "\r"))
        ):
            raise VerificationReceiptError("Verification evidence URL must be non-empty single-line text")

    def payload(self) -> dict[str, object]:
        """Return one canonical JSON-ready receipt object.

        Returns:
            Receipt payload.
        """

        return {
            "schema_version": 1,
            "completed_at": self.completed_at.isoformat().replace("+00:00", "Z"),
            "evidence_url": self.evidence_url,
            "input": self.input.payload(),
            "outcome": self.outcome,
            "verification_key": self.verification_key,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "VerificationReceipt":
        """Parse one strict verification receipt.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed verification receipt.
        """

        expected = {
            "schema_version",
            "completed_at",
            "evidence_url",
            "input",
            "outcome",
            "verification_key",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise VerificationReceiptError("Verification receipt has another shape")
        return cls(
            verification_key=payload["verification_key"],
            outcome=payload["outcome"],
            completed_at=instant_parse(payload["completed_at"], label="Verification completed_at"),
            evidence_url=payload["evidence_url"],
            input=VerificationInput.from_payload(payload["input"]),
        )
