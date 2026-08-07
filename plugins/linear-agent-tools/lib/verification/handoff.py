"""Minimal human-first task handoff and exact Codex usage telemetry."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from verification._validation import (
    COMMIT_PATTERN,
    EvidenceContractError,
    evidence_url_validate,
    single_line_validate,
)

_CODEX_USAGE_FIELD_SET = frozenset(
    {
        "cached_input_tokens",
        "cache_write_input_tokens",
        "input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    }
)
_GITHUB_PULL_REQUEST_PATH_PATTERN = re.compile(
    r"/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repository>[A-Za-z0-9_.-]+)/pull/[1-9][0-9]*"
)


def _github_pull_request_repository_get(value: object) -> str:
    """Return the exact repository identity from one canonical GitHub PR URL.

    Args:
        value: Candidate external URL.

    Returns:
        Canonical owner and repository identity.
    """

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

    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None

    def __post_init__(self) -> None:
        """Require every exposed counter to be an exact non-negative integer."""

        exposed_counter_count = 0
        for field_name in sorted(_CODEX_USAGE_FIELD_SET):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EvidenceContractError(f"Handoff Codex usage {field_name} must be a non-negative integer")
            exposed_counter_count += 1
        if not exposed_counter_count:
            raise EvidenceContractError("Handoff Codex usage must contain at least one exposed counter")

    def payload(self) -> dict[str, int]:
        """Return the exact surface counter names in token units.

        Returns:
            Every directly exposed counter.
        """

        return {
            field_name: value
            for field_name in sorted(_CODEX_USAGE_FIELD_SET)
            if (value := getattr(self, field_name)) is not None
        }

    @classmethod
    def from_payload(cls, payload: object) -> "CodexUsage":
        """Parse one closed structured Codex usage object.

        Args:
            payload: Candidate external value.

        Returns:
            Validated exact Codex counters.
        """

        if not isinstance(payload, dict) or not payload or not set(payload).issubset(_CODEX_USAGE_FIELD_SET):
            raise EvidenceContractError("Handoff Codex usage has another shape")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class TaskHandoff:
    """Record only human summary and values consumed by the next transition."""

    summary: str
    pull_request_candidate_list: list[TaskHandoffPullRequestCandidate] | None = None
    check_result_list: list[TaskHandoffCheckResult] | None = None
    codex_usage: CodexUsage | None = None

    def __post_init__(self) -> None:
        """Reject empty, repeated, or unrelated handoff state."""

        single_line_validate(self.summary, label="Handoff summary")
        if self.pull_request_candidate_list is not None:
            if (
                not isinstance(self.pull_request_candidate_list, list)
                or not self.pull_request_candidate_list
                or any(
                    not isinstance(item, TaskHandoffPullRequestCandidate) for item in self.pull_request_candidate_list
                )
            ):
                raise EvidenceContractError("Handoff pull-request candidates must be a nonempty typed list")
            url_list = [item.url for item in self.pull_request_candidate_list]
            repository_list = [
                _github_pull_request_repository_get(item.url) for item in self.pull_request_candidate_list
            ]
            if len(url_list) != len(set(url_list)) or len(repository_list) != len(set(repository_list)):
                raise EvidenceContractError("Handoff repeats one pull-request candidate repository")
            object.__setattr__(self, "pull_request_candidate_list", list(self.pull_request_candidate_list))
        if self.check_result_list is not None:
            if (
                not isinstance(self.check_result_list, list)
                or not self.check_result_list
                or any(not isinstance(item, TaskHandoffCheckResult) for item in self.check_result_list)
            ):
                raise EvidenceContractError("Handoff check results must be a nonempty duplicate-free typed list")
            check_name_list = [item.name for item in self.check_result_list]
            if len(self.check_result_list) != len(set(self.check_result_list)) or len(check_name_list) != len(
                set(check_name_list)
            ):
                raise EvidenceContractError("Handoff check results must be a nonempty duplicate-free typed list")
            object.__setattr__(self, "check_result_list", list(self.check_result_list))
        if self.codex_usage is not None and not isinstance(self.codex_usage, CodexUsage):
            raise EvidenceContractError("Handoff Codex usage must be absent or exact structured counters")

    def payload(self) -> dict[str, object]:
        """Return the final minimal human-first handoff.

        Returns:
            JSON-ready handoff with every absent outcome value omitted.
        """

        payload: dict[str, object] = {"summary": self.summary}
        if self.pull_request_candidate_list is not None:
            payload["pull_request_candidate_list"] = [item.payload() for item in self.pull_request_candidate_list]
        if self.check_result_list is not None:
            payload["check_result_list"] = [item.payload() for item in self.check_result_list]
        if self.codex_usage is not None:
            payload["codex_usage"] = self.codex_usage.payload()
        return payload

    def current_pull_request_identity_require(
        self,
        *,
        current_pull_request_candidate_list: list[TaskHandoffPullRequestCandidate],
    ) -> None:
        """Require fresh GitHub state to match each declared PR candidate.

        Args:
            current_pull_request_candidate_list: Fresh typed GitHub candidate state.
        """

        if (
            not isinstance(current_pull_request_candidate_list, list)
            or self.pull_request_candidate_list is None
            or current_pull_request_candidate_list != self.pull_request_candidate_list
        ):
            raise EvidenceContractError("Current pull-request identity changed from the semantic handoff")

    @classmethod
    def from_payload(cls, payload: object) -> "TaskHandoff":
        """Parse the final minimal handoff without legacy compatibility.

        Args:
            payload: Candidate external value.

        Returns:
            Validated minimal handoff.
        """

        required = {"summary"}
        allowed = required | {"pull_request_candidate_list", "check_result_list", "codex_usage"}
        if not isinstance(payload, dict) or not required.issubset(payload) or not set(payload).issubset(allowed):
            raise EvidenceContractError("Task handoff has another shape")
        pull_request_candidate_payload_list = payload.get("pull_request_candidate_list")
        if "pull_request_candidate_list" in payload and not isinstance(pull_request_candidate_payload_list, list):
            raise EvidenceContractError("Handoff pull-request candidates must be a list")
        check_result_payload_list = payload.get("check_result_list")
        if "check_result_list" in payload and not isinstance(check_result_payload_list, list):
            raise EvidenceContractError("Handoff check results must be a list")
        return cls(
            summary=payload["summary"],
            pull_request_candidate_list=(
                None
                if pull_request_candidate_payload_list is None
                else [
                    TaskHandoffPullRequestCandidate.from_payload(item) for item in pull_request_candidate_payload_list
                ]
            ),
            check_result_list=(
                None
                if check_result_payload_list is None
                else [TaskHandoffCheckResult.from_payload(item) for item in check_result_payload_list]
            ),
            codex_usage=(None if "codex_usage" not in payload else CodexUsage.from_payload(payload["codex_usage"])),
        )


@dataclass(frozen=True, slots=True)
class TaskHandoffCheckResult:
    """Carry one direct check result and its optional provider link."""

    name: str
    result: str
    evidence_url: str | None = None

    def __post_init__(self) -> None:
        """Require concise direct result text and a canonical optional link."""

        single_line_validate(self.name, label="Handoff check name")
        single_line_validate(self.result, label="Handoff check result")
        if self.evidence_url is not None:
            evidence_url_validate(self.evidence_url)

    def payload(self) -> dict[str, str]:
        """Return one compact direct check result.

        Returns:
            JSON-ready check result.
        """

        payload = {"name": self.name, "result": self.result}
        if self.evidence_url is not None:
            payload["evidence_url"] = self.evidence_url
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "TaskHandoffCheckResult":
        """Parse one strict direct check result.

        Args:
            payload: Candidate external value.

        Returns:
            Validated direct check result.
        """

        required = {"name", "result"}
        if not isinstance(payload, dict) or set(payload) not in (
            required,
            required | {"evidence_url"},
        ):
            raise EvidenceContractError("Handoff check result has another shape")
        if "evidence_url" in payload and payload["evidence_url"] is None:
            raise EvidenceContractError("Handoff check result must omit an unavailable evidence URL")
        return cls(
            name=payload["name"],
            result=payload["result"],
            evidence_url=payload.get("evidence_url"),
        )


@dataclass(frozen=True, slots=True)
class TaskHandoffPullRequestCandidate:
    """Carry one exact PR candidate and its optional terminal merge commit."""

    url: str
    base_branch: str
    base_commit: str
    head_commit: str
    merged_commit: str | None = None

    def __post_init__(self) -> None:
        """Require one canonical URL and complete immutable Git identities."""

        _github_pull_request_repository_get(self.url)
        single_line_validate(self.base_branch, label="Handoff pull-request base branch")
        for label, commit in (
            ("base", self.base_commit),
            ("head", self.head_commit),
        ):
            if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
                raise EvidenceContractError(f"Handoff pull-request {label} is not a full lowercase commit")
        if self.merged_commit is not None and (
            not isinstance(self.merged_commit, str) or COMMIT_PATTERN.fullmatch(self.merged_commit) is None
        ):
            raise EvidenceContractError("Handoff merged commit is not one full lowercase identity")

    def payload(self) -> dict[str, str]:
        """Return one composite PR candidate.

        Returns:
            JSON-ready candidate and optional merge result.
        """

        payload = {
            "url": self.url,
            "base_branch": self.base_branch,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
        }
        if self.merged_commit is not None:
            payload["merged_commit"] = self.merged_commit
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "TaskHandoffPullRequestCandidate":
        """Parse one strict composite PR candidate.

        Args:
            payload: Candidate external value.

        Returns:
            Validated PR candidate.
        """

        required = {"url", "base_branch", "base_commit", "head_commit"}
        if not isinstance(payload, dict) or set(payload) not in (
            required,
            required | {"merged_commit"},
        ):
            raise EvidenceContractError("Handoff pull-request candidate has another shape")
        if "merged_commit" in payload and payload["merged_commit"] is None:
            raise EvidenceContractError("Handoff pull-request candidate must omit an unavailable merged commit")
        return cls(
            url=payload["url"],
            base_branch=payload["base_branch"],
            base_commit=payload["base_commit"],
            head_commit=payload["head_commit"],
            merged_commit=payload.get("merged_commit"),
        )
