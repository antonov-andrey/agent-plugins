"""Complete local workflow phase baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from git_origin.identity import GitOriginError, origin_identity_get
from verification._validation import (
    COMMIT_PATTERN,
    EvidenceContractError,
    ISSUE_IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    UUID_PATTERN,
    evidence_url_validate,
    instant_parse,
    instant_render,
    utc_validate,
)

_BASELINE_PHASE_SET = {"queue", "startup", "execution", "review", "merge"}


@dataclass(frozen=True, slots=True)
class TaskWorkspaceBaseline:
    """Bind the immutable first-dispatch Git baseline recorded in Linear."""

    issue_identifier: str
    source_fingerprint: str
    branch_name: str
    baseline_commit_by_repository_url_map: dict[str, str]

    def __post_init__(self) -> None:
        """Require one complete deterministic repository baseline map."""

        if (
            not isinstance(self.issue_identifier, str)
            or ISSUE_IDENTIFIER_PATTERN.fullmatch(self.issue_identifier) is None
        ):
            raise EvidenceContractError("Workspace baseline issue identifier has another shape")
        if not isinstance(self.source_fingerprint, str) or SHA256_PATTERN.fullmatch(self.source_fingerprint) is None:
            raise EvidenceContractError("Workspace baseline source fingerprint must be SHA-256")
        if self.branch_name != f"linear/{self.issue_identifier.lower()}":
            raise EvidenceContractError("Workspace baseline branch differs from its Linear issue")
        if not isinstance(self.baseline_commit_by_repository_url_map, dict) or not (
            self.baseline_commit_by_repository_url_map
        ):
            raise EvidenceContractError("Workspace baseline repository commits must be a non-empty mapping")
        repository_identity_set: set[str] = set()
        for repository_url, commit in self.baseline_commit_by_repository_url_map.items():
            try:
                repository_identity = origin_identity_get(repository_url)
            except GitOriginError as error:
                raise EvidenceContractError("Workspace baseline repository URL is unsafe or unsupported") from error
            if repository_identity in repository_identity_set:
                raise EvidenceContractError("Workspace baseline repeats one repository identity")
            repository_identity_set.add(repository_identity)
            if COMMIT_PATTERN.fullmatch(commit) is None:
                raise EvidenceContractError("Workspace baseline commit must be one full lowercase identity")
        object.__setattr__(
            self,
            "baseline_commit_by_repository_url_map",
            dict(sorted(self.baseline_commit_by_repository_url_map.items())),
        )

    def payload(self) -> dict[str, object]:
        """Return canonical first-dispatch baseline evidence."""

        return {
            "schema_version": 1,
            "baseline_commit_by_repository_url_map": dict(self.baseline_commit_by_repository_url_map),
            "branch_name": self.branch_name,
            "issue_identifier": self.issue_identifier,
            "source_fingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "TaskWorkspaceBaseline":
        """Parse one strict first-dispatch baseline evidence object."""

        expected = {
            "schema_version",
            "baseline_commit_by_repository_url_map",
            "branch_name",
            "issue_identifier",
            "source_fingerprint",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise EvidenceContractError("Workspace baseline has another shape")
        baseline_commit_by_repository_url_map = payload["baseline_commit_by_repository_url_map"]
        if not isinstance(baseline_commit_by_repository_url_map, dict):
            raise EvidenceContractError("Workspace baseline repository commits must be a mapping")
        return cls(
            issue_identifier=payload["issue_identifier"],
            source_fingerprint=payload["source_fingerprint"],
            branch_name=payload["branch_name"],
            baseline_commit_by_repository_url_map=dict(baseline_commit_by_repository_url_map),
        )


@dataclass(frozen=True, slots=True)
class LocalPhaseBaseline:
    """Record the complete local workflow phase baseline for one accepted flow."""

    project_id: str
    source_fingerprint: str
    measured_at: datetime
    duration_seconds_by_phase_map: dict[str, float]
    evidence_url: str

    def __post_init__(self) -> None:
        """Require every agreed local phase exactly once."""

        if not isinstance(self.project_id, str) or UUID_PATTERN.fullmatch(self.project_id) is None:
            raise EvidenceContractError("Baseline Project identity must be one lowercase UUID")
        if not isinstance(self.source_fingerprint, str) or SHA256_PATTERN.fullmatch(self.source_fingerprint) is None:
            raise EvidenceContractError("Baseline source fingerprint must be SHA-256")
        utc_validate(self.measured_at, label="Baseline measurement")
        if not isinstance(self.duration_seconds_by_phase_map, dict):
            raise EvidenceContractError("Baseline phases must be a mapping")
        if set(self.duration_seconds_by_phase_map) != _BASELINE_PHASE_SET:
            raise EvidenceContractError(
                "Baseline must contain queue, startup, execution, review and merge exactly once"
            )
        if any(
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
            for duration in self.duration_seconds_by_phase_map.values()
        ):
            raise EvidenceContractError("Baseline phase durations must be non-negative seconds")
        evidence_url_validate(self.evidence_url)
        object.__setattr__(
            self,
            "duration_seconds_by_phase_map",
            dict(sorted(self.duration_seconds_by_phase_map.items())),
        )

    def payload(self) -> dict[str, object]:
        """Return canonical local phase telemetry.

        Returns:
            JSON-ready baseline.
        """

        return {
            "schema_version": 1,
            "duration_seconds_by_phase_map": dict(self.duration_seconds_by_phase_map),
            "evidence_url": self.evidence_url,
            "measured_at": instant_render(self.measured_at),
            "project_id": self.project_id,
            "source_fingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "LocalPhaseBaseline":
        """Parse one strict local phase baseline.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed baseline.
        """

        expected = {
            "schema_version",
            "duration_seconds_by_phase_map",
            "evidence_url",
            "measured_at",
            "project_id",
            "source_fingerprint",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise EvidenceContractError("Local phase baseline has another shape")
        duration_seconds_by_phase_map = payload["duration_seconds_by_phase_map"]
        if not isinstance(duration_seconds_by_phase_map, dict):
            raise EvidenceContractError("Baseline phases must be a mapping")
        return cls(
            project_id=payload["project_id"],
            source_fingerprint=payload["source_fingerprint"],
            measured_at=instant_parse(payload["measured_at"], label="Baseline measurement"),
            duration_seconds_by_phase_map=dict(duration_seconds_by_phase_map),
            evidence_url=payload["evidence_url"],
        )
