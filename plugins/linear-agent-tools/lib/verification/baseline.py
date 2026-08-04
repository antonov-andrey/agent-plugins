"""Complete local workflow phase baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from verification._validation import (
    COMMIT_PATTERN,
    ISSUE_IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    UUID_PATTERN,
    VerificationReceiptError,
    instant_parse,
    instant_render,
    single_line_validate,
    utc_validate,
)

_BASELINE_PHASE_SET = {"queue", "startup", "execution", "review", "merge"}


@dataclass(frozen=True, slots=True)
class TaskWorkspaceBaseline:
    """Bind the immutable first-dispatch Git baseline recorded in Linear."""

    issue_identifier: str
    source_fingerprint: str
    branch_name: str
    baseline_commit_by_repository_url: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """Require one complete deterministic repository baseline map."""

        if (
            not isinstance(self.issue_identifier, str)
            or ISSUE_IDENTIFIER_PATTERN.fullmatch(self.issue_identifier) is None
        ):
            raise VerificationReceiptError("Workspace baseline issue identifier has another shape")
        if not isinstance(self.source_fingerprint, str) or SHA256_PATTERN.fullmatch(self.source_fingerprint) is None:
            raise VerificationReceiptError("Workspace baseline source fingerprint must be SHA-256")
        if self.branch_name != f"linear/{self.issue_identifier.lower()}":
            raise VerificationReceiptError("Workspace baseline branch differs from its Linear issue")
        value = self.baseline_commit_by_repository_url
        if (
            not isinstance(value, tuple)
            or not value
            or value != tuple(sorted(value))
            or len(value) != len(set(value))
            or len({repository for repository, _commit in value}) != len(value)
        ):
            raise VerificationReceiptError("Workspace baseline repository commits must be non-empty, unique and sorted")
        for repository_url, commit in value:
            single_line_validate(repository_url, label="Workspace baseline repository URL")
            if COMMIT_PATTERN.fullmatch(commit) is None:
                raise VerificationReceiptError("Workspace baseline commit must be one full lowercase identity")

    def payload(self) -> dict[str, object]:
        """Return canonical first-dispatch baseline evidence."""

        return {
            "schema_version": 1,
            "baseline_commit_by_repository_url": [list(item) for item in self.baseline_commit_by_repository_url],
            "branch_name": self.branch_name,
            "issue_identifier": self.issue_identifier,
            "source_fingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "TaskWorkspaceBaseline":
        """Parse one strict first-dispatch baseline evidence object."""

        expected = {
            "schema_version",
            "baseline_commit_by_repository_url",
            "branch_name",
            "issue_identifier",
            "source_fingerprint",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise VerificationReceiptError("Workspace baseline has another shape")
        pair_list = payload["baseline_commit_by_repository_url"]
        if not isinstance(pair_list, list):
            raise VerificationReceiptError("Workspace baseline repository commits must be a list")
        parsed: list[tuple[str, str]] = []
        for item in pair_list:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], str)
            ):
                raise VerificationReceiptError("Workspace baseline contains a malformed repository commit")
            parsed.append((item[0], item[1]))
        return cls(
            issue_identifier=payload["issue_identifier"],
            source_fingerprint=payload["source_fingerprint"],
            branch_name=payload["branch_name"],
            baseline_commit_by_repository_url=tuple(parsed),
        )


@dataclass(frozen=True, slots=True)
class LocalPhaseBaseline:
    """Record the complete local workflow phase baseline for one accepted flow."""

    project_id: str
    source_fingerprint: str
    candidate_fingerprint: str
    measured_at: datetime
    duration_seconds_by_phase: tuple[tuple[str, float], ...]
    evidence_url: str

    def __post_init__(self) -> None:
        """Require every agreed local phase exactly once."""

        if not isinstance(self.project_id, str) or UUID_PATTERN.fullmatch(self.project_id) is None:
            raise VerificationReceiptError("Baseline Project identity must be one lowercase UUID")
        for label, value in (
            ("source fingerprint", self.source_fingerprint),
            ("candidate fingerprint", self.candidate_fingerprint),
        ):
            if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
                raise VerificationReceiptError(f"Baseline {label} must be SHA-256")
        utc_validate(self.measured_at, label="Baseline measurement")
        if any(not isinstance(item, tuple) or len(item) != 2 for item in self.duration_seconds_by_phase):
            raise VerificationReceiptError("Baseline contains a malformed phase")
        if self.duration_seconds_by_phase != tuple(sorted(self.duration_seconds_by_phase)):
            raise VerificationReceiptError("Baseline phases must be sorted")
        if {name for name, _duration in self.duration_seconds_by_phase} != _BASELINE_PHASE_SET or len(
            self.duration_seconds_by_phase
        ) != len(_BASELINE_PHASE_SET):
            raise VerificationReceiptError(
                "Baseline must contain queue, startup, execution, review and merge exactly once"
            )
        if any(
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
            for _name, duration in self.duration_seconds_by_phase
        ):
            raise VerificationReceiptError("Baseline phase durations must be non-negative seconds")
        single_line_validate(self.evidence_url, label="Baseline evidence URL")

    def payload(self) -> dict[str, object]:
        """Return canonical local phase telemetry.

        Returns:
            JSON-ready baseline.
        """

        return {
            "schema_version": 1,
            "candidate_fingerprint": self.candidate_fingerprint,
            "duration_seconds_by_phase": [list(item) for item in self.duration_seconds_by_phase],
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
            "candidate_fingerprint",
            "duration_seconds_by_phase",
            "evidence_url",
            "measured_at",
            "project_id",
            "source_fingerprint",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise VerificationReceiptError("Local phase baseline has another shape")
        phase_list = payload["duration_seconds_by_phase"]
        if not isinstance(phase_list, list):
            raise VerificationReceiptError("Baseline phases must be a list")
        duration_list: list[tuple[str, float]] = []
        for item in phase_list:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or isinstance(item[1], bool)
                or not isinstance(item[1], (int, float))
            ):
                raise VerificationReceiptError("Baseline contains a malformed phase")
            duration_list.append((item[0], item[1]))
        return cls(
            project_id=payload["project_id"],
            source_fingerprint=payload["source_fingerprint"],
            candidate_fingerprint=payload["candidate_fingerprint"],
            measured_at=instant_parse(payload["measured_at"], label="Baseline measurement"),
            duration_seconds_by_phase=tuple(duration_list),
            evidence_url=payload["evidence_url"],
        )
