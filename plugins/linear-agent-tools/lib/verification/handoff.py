"""Compact semantic handoff and exact Codex usage telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlsplit

from git_host.model import GitHubContractError, RepositoryIdentity
from verification._validation import (
    COMMIT_PATTERN,
    EvidenceContractError,
    ISSUE_IDENTIFIER_PATTERN,
    UUID_PATTERN,
    evidence_url_validate,
    instant_parse,
    instant_render,
    single_line_validate,
    text_by_text_map_parse,
    utc_validate,
)

_GITHUB_PULL_REQUEST_PATH_PATTERN = re.compile(
    r"/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repository>[A-Za-z0-9_.-]+)/pull/[1-9][0-9]*"
)
_DELIVERY_BY_ROLE = {
    "task:implementation": {"code", "evidence"},
    "task:review": {"evidence"},
    "task:acceptance": {"evidence"},
    "task:cleanup": {"cleanup"},
}
_ROLE_BY_OPERATION = {
    "implementation": {"task:implementation"},
    "review": {"task:implementation", "task:review"},
    "acceptance": {"task:acceptance"},
    "merge": {"task:implementation"},
    "cleanup": {"task:cleanup"},
}
_OUTCOME_BY_OPERATION = {
    "implementation": {"review-ready", "canceled", "failed", "interrupted"},
    "review": {"review-passed", "review-findings", "canceled", "failed", "interrupted"},
    "acceptance": {"final-boundary", "remediation-required", "canceled", "failed", "interrupted"},
    "merge": {"merged", "rework-required", "canceled", "failed", "interrupted"},
    "cleanup": {"cleanup-complete", "canceled", "failed", "interrupted"},
}
_DIRECT_EVIDENCE_OUTCOME_SET = {
    "review-ready",
    "review-passed",
    "review-findings",
    "final-boundary",
    "remediation-required",
    "merged",
    "rework-required",
    "cleanup-complete",
}


def _github_pull_request_url_validate(value: object) -> str:
    """Return the exact repository identity from one canonical GitHub PR URL."""

    if not isinstance(value, str):
        raise EvidenceContractError("Handoff pull-request URL is not one canonical GitHub PR")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise EvidenceContractError("Handoff pull-request URL is not one canonical GitHub PR") from error
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
        raise EvidenceContractError("Handoff pull-request URL is not one canonical GitHub PR")
    return f"{path_match.group('owner')}/{path_match.group('repository')}"


@dataclass(frozen=True, slots=True)
class CodexUsage:
    """Preserve exact structured counters exposed by completed Codex turns."""

    cached_input_tokens: int
    cache_write_input_tokens: int
    input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int

    def __post_init__(self) -> None:
        """Require every exposed counter to be an exact non-negative integer."""

        for field_name in (
            "cached_input_tokens",
            "cache_write_input_tokens",
            "input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EvidenceContractError(f"Handoff Codex usage {field_name} must be a non-negative integer")

    def payload(self) -> dict[str, int]:
        """Return the exact surface counter names in token units."""

        return {
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "CodexUsage":
        """Parse one closed structured Codex usage object."""

        expected = {
            "cached_input_tokens",
            "cache_write_input_tokens",
            "input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise EvidenceContractError("Handoff Codex usage has another shape")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class TaskHandoff:
    """Record semantic current state without becoming a reusable receipt."""

    handoff_id: str
    issue_identifier: str
    operation: str
    role_label: str
    delivery_kind: str
    started_at: datetime
    completed_at: datetime
    outcome: str
    summary: str
    commit_by_repository_map: dict[str, str]
    pull_request_head_by_url_map: dict[str, str]
    verification_summary_list: list[str]
    evidence_url_list: list[str]
    codex_usage: CodexUsage | None

    def __post_init__(self) -> None:
        """Validate compact state, direct evidence, and exact usage telemetry."""

        if not isinstance(self.handoff_id, str) or UUID_PATTERN.fullmatch(self.handoff_id) is None:
            raise EvidenceContractError("Handoff identity must be one lowercase UUID")
        if (
            not isinstance(self.issue_identifier, str)
            or ISSUE_IDENTIFIER_PATTERN.fullmatch(self.issue_identifier) is None
        ):
            raise EvidenceContractError("Handoff issue identifier has another shape")
        if (
            not isinstance(self.operation, str)
            or not isinstance(self.role_label, str)
            or self.operation not in _ROLE_BY_OPERATION
            or self.role_label not in _ROLE_BY_OPERATION[self.operation]
        ):
            raise EvidenceContractError("Handoff operation and task role are incompatible")
        if (
            not isinstance(self.delivery_kind, str)
            or self.role_label not in _DELIVERY_BY_ROLE
            or self.delivery_kind not in _DELIVERY_BY_ROLE[self.role_label]
        ):
            raise EvidenceContractError("Handoff role and delivery kind are incompatible")
        if self.operation == "merge" and self.delivery_kind != "code":
            raise EvidenceContractError("Merge handoff requires code delivery")
        utc_validate(self.started_at, label="Handoff start")
        utc_validate(self.completed_at, label="Handoff completion")
        if self.completed_at < self.started_at:
            raise EvidenceContractError("Handoff completion precedes its start")
        if not isinstance(self.outcome, str) or self.outcome not in _OUTCOME_BY_OPERATION[self.operation]:
            raise EvidenceContractError("Handoff operation and outcome are incompatible")
        single_line_validate(self.summary, label="Handoff semantic summary")
        if not isinstance(self.commit_by_repository_map, dict):
            raise EvidenceContractError("Handoff commit set must be a mapping")
        for repository, commit in self.commit_by_repository_map.items():
            if not isinstance(repository, str) or not isinstance(commit, str):
                raise EvidenceContractError("Handoff repository commits must be text identities")
            try:
                RepositoryIdentity(repository)
            except GitHubContractError as error:
                raise EvidenceContractError("Handoff repository must use exact owner/name form") from error
            if COMMIT_PATTERN.fullmatch(commit) is None:
                raise EvidenceContractError("Handoff commit is not a full lowercase identity")
        if self.delivery_kind != "code" and self.commit_by_repository_map:
            raise EvidenceContractError("Non-code handoff cannot report Product commits")
        if self.operation == "review" and self.commit_by_repository_map:
            raise EvidenceContractError("Review handoff cannot report changed Product commits")
        if not isinstance(self.pull_request_head_by_url_map, dict):
            raise EvidenceContractError("Handoff pull-request heads must be a mapping")
        pull_request_repository_list: list[str] = []
        for url, commit in self.pull_request_head_by_url_map.items():
            if not isinstance(commit, str):
                raise EvidenceContractError("Handoff pull-request head must be a text identity")
            pull_request_repository_list.append(_github_pull_request_url_validate(url))
            if COMMIT_PATTERN.fullmatch(commit) is None:
                raise EvidenceContractError("Handoff pull-request head is not a full lowercase commit")
        if len(pull_request_repository_list) != len(set(pull_request_repository_list)):
            raise EvidenceContractError("Handoff repeats one pull-request repository")
        if self.delivery_kind == "code":
            if self.outcome in _DIRECT_EVIDENCE_OUTCOME_SET and not self.pull_request_head_by_url_map:
                raise EvidenceContractError("Code handoff requires exact pull-request heads")
        elif self.pull_request_head_by_url_map:
            raise EvidenceContractError("Only code handoff may report pull-request heads")
        if (
            self.operation in {"implementation", "merge"}
            and self.delivery_kind == "code"
            and self.outcome in _DIRECT_EVIDENCE_OUTCOME_SET
            and not self.commit_by_repository_map
        ):
            raise EvidenceContractError("Completed code handoff requires repository commits")
        if self.operation in {"implementation", "merge"} and self.outcome in _DIRECT_EVIDENCE_OUTCOME_SET:
            for url, pull_request_head in self.pull_request_head_by_url_map.items():
                repository = _github_pull_request_url_validate(url)
                if repository not in self.commit_by_repository_map:
                    raise EvidenceContractError("Handoff pull-request repository is absent from current commits")
                if (
                    self.operation == "implementation"
                    and self.commit_by_repository_map[repository] != pull_request_head
                ):
                    raise EvidenceContractError("Implementation handoff pull-request head differs from current commit")
        if (
            not isinstance(self.verification_summary_list, list)
            or any(not isinstance(item, str) for item in self.verification_summary_list)
            or len(self.verification_summary_list) != len(set(self.verification_summary_list))
        ):
            raise EvidenceContractError("Handoff verification summaries must be a duplicate-free list")
        for summary in self.verification_summary_list:
            single_line_validate(summary, label="Handoff verification summary")
        if (
            not isinstance(self.evidence_url_list, list)
            or any(not isinstance(item, str) for item in self.evidence_url_list)
            or self.evidence_url_list != sorted(self.evidence_url_list)
            or len(self.evidence_url_list) != len(set(self.evidence_url_list))
        ):
            raise EvidenceContractError("Handoff evidence links must be unique and sorted")
        for value in self.evidence_url_list:
            evidence_url_validate(value)
        if self.outcome in _DIRECT_EVIDENCE_OUTCOME_SET and (
            not self.verification_summary_list or not self.evidence_url_list
        ):
            raise EvidenceContractError("Completed handoff requires semantic verification and direct evidence")
        if self.codex_usage is not None and not isinstance(self.codex_usage, CodexUsage):
            raise EvidenceContractError("Handoff Codex usage must be absent or exact structured counters")
        object.__setattr__(self, "commit_by_repository_map", dict(sorted(self.commit_by_repository_map.items())))
        object.__setattr__(
            self,
            "pull_request_head_by_url_map",
            dict(sorted(self.pull_request_head_by_url_map.items())),
        )
        object.__setattr__(self, "verification_summary_list", list(self.verification_summary_list))
        object.__setattr__(self, "evidence_url_list", list(self.evidence_url_list))

    def payload(self) -> dict[str, object]:
        """Return one canonical compact semantic handoff."""

        payload: dict[str, object] = {
            "schema_version": 1,
            "commit_by_repository_map": dict(self.commit_by_repository_map),
            "completed_at": instant_render(self.completed_at),
            "delivery_kind": self.delivery_kind,
            "evidence_url_list": list(self.evidence_url_list),
            "handoff_id": self.handoff_id,
            "issue_identifier": self.issue_identifier,
            "operation": self.operation,
            "outcome": self.outcome,
            "pull_request_head_by_url_map": dict(self.pull_request_head_by_url_map),
            "role_label": self.role_label,
            "started_at": instant_render(self.started_at),
            "summary": self.summary,
            "verification_summary_list": list(self.verification_summary_list),
        }
        if self.codex_usage is not None:
            payload["codex_usage"] = self.codex_usage.payload()
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "TaskHandoff":
        """Parse one strict semantic handoff payload."""

        required = {
            "schema_version",
            "commit_by_repository_map",
            "completed_at",
            "delivery_kind",
            "evidence_url_list",
            "handoff_id",
            "issue_identifier",
            "operation",
            "outcome",
            "pull_request_head_by_url_map",
            "role_label",
            "started_at",
            "summary",
            "verification_summary_list",
        }
        allowed = required | {"codex_usage"}
        if (
            not isinstance(payload, dict)
            or (set(payload) != required and set(payload) != allowed)
            or payload["schema_version"] != 1
        ):
            raise EvidenceContractError("Task handoff has another shape")
        evidence_url_list = payload["evidence_url_list"]
        verification_summary_list = payload["verification_summary_list"]
        if not isinstance(evidence_url_list, list) or any(not isinstance(item, str) for item in evidence_url_list):
            raise EvidenceContractError("Handoff evidence URLs must be a string list")
        if not isinstance(verification_summary_list, list) or any(
            not isinstance(item, str) for item in verification_summary_list
        ):
            raise EvidenceContractError("Handoff verification summaries must be a string list")
        return cls(
            handoff_id=payload["handoff_id"],
            issue_identifier=payload["issue_identifier"],
            operation=payload["operation"],
            role_label=payload["role_label"],
            delivery_kind=payload["delivery_kind"],
            started_at=instant_parse(payload["started_at"], label="Handoff start"),
            completed_at=instant_parse(payload["completed_at"], label="Handoff completion"),
            outcome=payload["outcome"],
            summary=payload["summary"],
            commit_by_repository_map=text_by_text_map_parse(
                payload["commit_by_repository_map"], label="handoff commits"
            ),
            pull_request_head_by_url_map=text_by_text_map_parse(
                payload["pull_request_head_by_url_map"], label="handoff pull-request heads"
            ),
            verification_summary_list=list(verification_summary_list),
            evidence_url_list=list(evidence_url_list),
            codex_usage=(None if "codex_usage" not in payload else CodexUsage.from_payload(payload["codex_usage"])),
        )
