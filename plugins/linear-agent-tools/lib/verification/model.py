"""Closed models for reusable verification receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath

from verification._validation import (
    COMMIT_PATTERN,
    SHA256_PATTERN,
    VerificationReceiptError,
    instant_parse,
    single_line_validate,
    text_by_text_map_parse,
    utc_validate,
)


def _absolute_path_validate(value: object, *, label: str) -> str:
    """Return one canonical absolute POSIX path.

    Args:
        value: Candidate path.
        label: Diagnostic owner label.

    Returns:
        Validated path.
    """

    path = PurePosixPath(single_line_validate(value, label=label))
    if not path.is_absolute() or str(path) != value or any(part in {".", ".."} for part in path.parts):
        raise VerificationReceiptError(f"{label} must be a canonical absolute POSIX path")
    return value


def _repository_path_validate(value: object, *, label: str) -> str:
    """Return one canonical repository-relative POSIX path.

    Args:
        value: Candidate path.
        label: Diagnostic owner label.

    Returns:
        Validated path.
    """

    path = PurePosixPath(single_line_validate(value, label=label))
    if (
        path.is_absolute()
        or str(path) in {"", "."}
        or str(path) != value
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise VerificationReceiptError(f"{label} must be a canonical repository-relative POSIX path")
    return value


@dataclass(frozen=True, slots=True)
class VerificationCheckout:
    """Bind one result-affecting checkout without collapsing equal repository URLs."""

    path: str
    role_list: list[str]
    repository_url: str
    commit: str
    recursive_submodule_commit_by_path_map: dict[str, str]
    dependency_lock_sha256_by_path_map: dict[str, str]

    def __post_init__(self) -> None:
        """Validate one exact checkout identity and its repository-local dependencies."""

        _absolute_path_validate(self.path, label="Verification checkout path")
        single_line_validate(self.repository_url, label="Verification checkout repository URL")
        if not isinstance(self.commit, str) or COMMIT_PATTERN.fullmatch(self.commit) is None:
            raise VerificationReceiptError("Verification checkout commit is not a full lowercase identity")
        if (
            not isinstance(self.role_list, list)
            or not self.role_list
            or len(self.role_list) != len(set(self.role_list))
        ):
            raise VerificationReceiptError("Verification checkout roles must be a non-empty duplicate-free list")
        for role in self.role_list:
            single_line_validate(role, label="Verification checkout role")
        for label, value_by_path_map in (
            ("recursive submodule commits", self.recursive_submodule_commit_by_path_map),
            ("dependency lock fingerprints", self.dependency_lock_sha256_by_path_map),
        ):
            if not isinstance(value_by_path_map, dict):
                raise VerificationReceiptError(f"Verification checkout {label} must be a mapping")
            for path, identity in value_by_path_map.items():
                _repository_path_validate(path, label=f"Verification checkout {label} path")
                single_line_validate(identity, label=f"Verification checkout {label} identity")
        if any(
            COMMIT_PATTERN.fullmatch(commit) is None for commit in self.recursive_submodule_commit_by_path_map.values()
        ):
            raise VerificationReceiptError("Verification checkout submodule commit is not a full lowercase identity")
        if any(SHA256_PATTERN.fullmatch(digest) is None for digest in self.dependency_lock_sha256_by_path_map.values()):
            raise VerificationReceiptError("Verification checkout dependency lock fingerprint is not SHA-256")
        object.__setattr__(self, "role_list", sorted(self.role_list))
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

    @classmethod
    def from_payload(cls, payload: object) -> "VerificationCheckout":
        """Parse one strict checkout identity.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed checkout identity.
        """

        expected = {
            "commit",
            "dependency_lock_sha256_by_path_map",
            "path",
            "recursive_submodule_commit_by_path_map",
            "repository_url",
            "role_list",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise VerificationReceiptError("Verification checkout has another shape")
        role_list = payload["role_list"]
        if not isinstance(role_list, list) or any(not isinstance(item, str) for item in role_list):
            raise VerificationReceiptError("Verification checkout roles must be a string list")
        return cls(
            path=payload["path"],
            role_list=list(role_list),
            repository_url=payload["repository_url"],
            commit=payload["commit"],
            recursive_submodule_commit_by_path_map=text_by_text_map_parse(
                payload["recursive_submodule_commit_by_path_map"],
                label="recursive submodule commits",
            ),
            dependency_lock_sha256_by_path_map=text_by_text_map_parse(
                payload["dependency_lock_sha256_by_path_map"],
                label="dependency lock fingerprints",
            ),
        )

    def payload(self) -> dict[str, object]:
        """Return the canonical JSON-ready checkout identity.

        Returns:
            Checkout payload.
        """

        return {
            "commit": self.commit,
            "dependency_lock_sha256_by_path_map": dict(self.dependency_lock_sha256_by_path_map),
            "path": self.path,
            "recursive_submodule_commit_by_path_map": dict(self.recursive_submodule_commit_by_path_map),
            "repository_url": self.repository_url,
            "role_list": list(self.role_list),
        }


@dataclass(frozen=True, slots=True)
class VerificationInput:
    """Own every input that can change one verification result."""

    command_argument_list: list[str]
    working_directory: str
    source_fingerprint: str
    checkout_list: list[VerificationCheckout]
    corpus_content_sha256: str
    model_identity: str
    model_configuration_by_name_map: dict[str, str]
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
        _absolute_path_validate(self.working_directory, label="Verification working directory")
        if not isinstance(self.source_fingerprint, str) or SHA256_PATTERN.fullmatch(self.source_fingerprint) is None:
            raise VerificationReceiptError("Verification source fingerprint must be SHA-256")
        if not isinstance(self.checkout_list, list) or any(
            not isinstance(item, VerificationCheckout) for item in self.checkout_list
        ):
            raise VerificationReceiptError("Verification checkouts must be a list of exact checkout identities")
        checkout_path_list = [item.path for item in self.checkout_list]
        if len(checkout_path_list) != len(set(checkout_path_list)):
            raise VerificationReceiptError("Verification checkout paths must be unique")
        if self.corpus_content_sha256 and SHA256_PATTERN.fullmatch(self.corpus_content_sha256) is None:
            raise VerificationReceiptError("Verification corpus content identity must be empty or SHA-256")
        single_line_validate(self.model_identity, label="Verification model identity", empty_allowed=True)
        if not isinstance(self.model_configuration_by_name_map, dict):
            raise VerificationReceiptError("Verification model configuration must be a mapping")
        for name, identity in self.model_configuration_by_name_map.items():
            single_line_validate(name, label="Verification model configuration name")
            single_line_validate(identity, label="Verification model configuration identity")
        if not self.model_identity and self.model_configuration_by_name_map:
            raise VerificationReceiptError("Verification model configuration requires a model identity")
        for label, value in (
            ("environment identity", self.environment_identity),
            ("release identity", self.release_identity),
        ):
            single_line_validate(value, label=f"Verification {label}", empty_allowed=True)
        object.__setattr__(self, "command_argument_list", list(self.command_argument_list))
        object.__setattr__(
            self,
            "checkout_list",
            sorted(self.checkout_list, key=lambda item: item.path),
        )
        object.__setattr__(
            self,
            "model_configuration_by_name_map",
            dict(sorted(self.model_configuration_by_name_map.items())),
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
            "checkout_list": [item.payload() for item in self.checkout_list],
            "command_argument_list": list(self.command_argument_list),
            "corpus_content_sha256": self.corpus_content_sha256,
            "environment_identity": self.environment_identity,
            "model_configuration_by_name_map": dict(self.model_configuration_by_name_map),
            "model_identity": self.model_identity,
            "release_identity": self.release_identity,
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
            "checkout_list",
            "command_argument_list",
            "corpus_content_sha256",
            "environment_identity",
            "model_configuration_by_name_map",
            "model_identity",
            "release_identity",
            "source_fingerprint",
            "working_directory",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise VerificationReceiptError("Verification input has another shape")
        command_list = payload["command_argument_list"]
        if not isinstance(command_list, list) or any(not isinstance(item, str) for item in command_list):
            raise VerificationReceiptError("Verification command must be a string list")
        checkout_list = payload["checkout_list"]
        if not isinstance(checkout_list, list):
            raise VerificationReceiptError("Verification checkouts must be a list")
        return cls(
            command_argument_list=list(command_list),
            working_directory=payload["working_directory"],
            checkout_list=[VerificationCheckout.from_payload(item) for item in checkout_list],
            corpus_content_sha256=payload["corpus_content_sha256"],
            model_identity=payload["model_identity"],
            model_configuration_by_name_map=text_by_text_map_parse(
                payload["model_configuration_by_name_map"], label="model configuration"
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
    evidence_content_sha256: str
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
        if (
            not isinstance(self.evidence_content_sha256, str)
            or SHA256_PATTERN.fullmatch(self.evidence_content_sha256) is None
        ):
            raise VerificationReceiptError("Verification evidence content identity must be SHA-256")

    @classmethod
    def from_input(
        cls,
        verification_input: VerificationInput,
        *,
        outcome: str,
        evidence_url: str,
        evidence_content_sha256: str,
        completed_at: datetime | None = None,
    ) -> "VerificationReceipt":
        """Create one immutable receipt at an exact UTC instant.

        Args:
            verification_input: Complete declared inputs.
            outcome: Passed or failed.
            evidence_url: Link to the owning log or CI result.
            evidence_content_sha256: Exact evidence artifact content identity.
            completed_at: Optional deterministic UTC instant.

        Returns:
            Typed verification receipt.
        """

        instant = completed_at or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise VerificationReceiptError("Receipt creation instant must be timezone-aware")
        return cls(
            verification_key=verification_input.key(),
            outcome=outcome,
            completed_at=instant.astimezone(timezone.utc),
            evidence_url=evidence_url,
            evidence_content_sha256=evidence_content_sha256,
            input=verification_input,
        )

    def payload(self) -> dict[str, object]:
        """Return one canonical JSON-ready receipt object.

        Returns:
            Receipt payload.
        """

        return {
            "schema_version": 2,
            "completed_at": self.completed_at.isoformat().replace("+00:00", "Z"),
            "evidence_content_sha256": self.evidence_content_sha256,
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
            "evidence_content_sha256",
            "evidence_url",
            "input",
            "outcome",
            "verification_key",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 2:
            raise VerificationReceiptError("Verification receipt has another shape")
        return cls(
            verification_key=payload["verification_key"],
            outcome=payload["outcome"],
            completed_at=instant_parse(payload["completed_at"], label="Verification completed_at"),
            evidence_url=payload["evidence_url"],
            evidence_content_sha256=payload["evidence_content_sha256"],
            input=VerificationInput.from_payload(payload["input"]),
        )
