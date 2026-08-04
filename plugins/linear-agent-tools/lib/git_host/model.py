"""Closed GitHub repository and pull-request models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
_ISSUE_IDENTIFIER_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*")


class GitHubContractError(RuntimeError):
    """Report one unsafe or conflicting GitHub operation."""


def issue_identifier_in_title_require(issue_identifier: str, title: str) -> None:
    """Require one exact Linear identifier token in a single-line PR title.

    Args:
        issue_identifier: Canonical Linear issue identifier.
        title: Candidate pull-request title.
    """

    if _ISSUE_IDENTIFIER_PATTERN.fullmatch(issue_identifier) is None:
        raise GitHubContractError("Linear issue identifier has another shape")
    if (
        not isinstance(title, str)
        or not title
        or any(character in title for character in ("\x00", "\n", "\r"))
    ):
        raise GitHubContractError(
            "Pull request title must be non-empty single-line text"
        )
    token_pattern = rf"(?<![A-Z0-9-]){re.escape(issue_identifier)}(?![A-Z0-9-])"
    if re.search(token_pattern, title) is None:
        raise GitHubContractError(
            "Pull request title omits the exact Linear issue token"
        )


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    """Bind one exact GitHub owner/repository identity."""

    value: str

    def __post_init__(self) -> None:
        """Validate one canonical GitHub repository identity."""

        if _REPOSITORY_PATTERN.fullmatch(self.value) is None:
            raise GitHubContractError(
                "GitHub repository must use exact owner/name form"
            )


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    """Describe one branch-protection-required GitHub check."""

    name: str
    bucket: str
    link: str

    def __post_init__(self) -> None:
        """Validate provider check output without accepting hidden multiline data."""

        for label, value, empty_allowed in (
            ("name", self.name, False),
            ("bucket", self.bucket, False),
            ("link", self.link, True),
        ):
            if (
                not isinstance(value, str)
                or (not value and not empty_allowed)
                or any(character in value for character in ("\x00", "\n", "\r"))
            ):
                raise GitHubContractError(f"Required check {label} has another shape")

    def is_passed(self) -> bool:
        """Return whether GitHub classified the required check as passing.

        Returns:
            Whether the check passed.
        """

        return self.bucket in {"pass", "skipping"}


@dataclass(frozen=True, slots=True)
class PullRequestSnapshot:
    """Contain all state used to bind one human approval and merge."""

    repository: RepositoryIdentity
    number: int
    url: str
    title: str
    state: str
    draft: bool
    base_branch: str
    head_branch: str
    head_commit: str
    merge_state: str
    review_decision: str
    merged_at: datetime | None
    merge_commit: str
    required_check_list: list[RequiredCheck]

    def __post_init__(self) -> None:
        """Validate one provider snapshot."""

        if not isinstance(self.repository, RepositoryIdentity):
            raise GitHubContractError("Pull request repository identity is unsupported")
        if (
            isinstance(self.number, bool)
            or not isinstance(self.number, int)
            or self.number < 1
        ):
            raise GitHubContractError("Pull request number must be positive")
        for label, value in (
            ("URL", self.url),
            ("title", self.title),
            ("state", self.state),
            ("base branch", self.base_branch),
            ("head branch", self.head_branch),
            ("merge state", self.merge_state),
            ("review decision", self.review_decision),
        ):
            if (
                not isinstance(value, str)
                or not value
                or any(character in value for character in ("\x00", "\n", "\r"))
            ):
                raise GitHubContractError(
                    f"Pull request {label} must be non-empty single-line text"
                )
        if _COMMIT_PATTERN.fullmatch(self.head_commit) is None:
            raise GitHubContractError(
                "Pull request head must be one full lowercase commit"
            )
        if self.merge_commit and _COMMIT_PATTERN.fullmatch(self.merge_commit) is None:
            raise GitHubContractError(
                "Pull request merge commit must be empty or one full lowercase commit"
            )
        if not isinstance(self.draft, bool):
            raise GitHubContractError("Pull request draft flag must be boolean")
        if not isinstance(self.required_check_list, list) or any(
            not isinstance(item, RequiredCheck) for item in self.required_check_list
        ):
            raise GitHubContractError("Pull request required checks have another shape")
        check_name_list = [item.name for item in self.required_check_list]
        if len(check_name_list) != len(set(check_name_list)):
            raise GitHubContractError("Pull request repeats one required check name")
        if self.merged_at is not None:
            if self.merged_at.tzinfo is None or self.merged_at.utcoffset() is None:
                raise GitHubContractError(
                    "Pull request merged_at must be timezone-aware"
                )
            if self.merged_at.utcoffset() != timezone.utc.utcoffset(self.merged_at):
                raise GitHubContractError(
                    "Pull request merged_at must be normalized to UTC"
                )
        object.__setattr__(self, "required_check_list", list(self.required_check_list))

    @classmethod
    def from_gh_payload(
        cls,
        repository: RepositoryIdentity,
        payload: object,
        *,
        required_check_list: list[RequiredCheck],
    ) -> "PullRequestSnapshot":
        """Parse one exact ``gh`` pull-request payload.

        Args:
            repository: Exact repository identity.
            payload: Candidate decoded response.
            required_check_list: Separately read required checks.

        Returns:
            Typed pull-request snapshot.
        """

        expected = {
            "number",
            "url",
            "title",
            "state",
            "isDraft",
            "baseRefName",
            "headRefName",
            "headRefOid",
            "mergeStateStatus",
            "reviewDecision",
            "mergedAt",
            "mergeCommit",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise GitHubContractError("GitHub PR response has another shape")
        merged_at = payload["mergedAt"]
        if merged_at is not None:
            if not isinstance(merged_at, str):
                raise GitHubContractError("GitHub mergedAt has another shape")
            try:
                merged_instant = datetime.fromisoformat(
                    merged_at.replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except ValueError as error:
                raise GitHubContractError("GitHub mergedAt is malformed") from error
        else:
            merged_instant = None
        merge_commit = payload["mergeCommit"]
        if merge_commit is None:
            merge_commit_value = ""
        elif (
            isinstance(merge_commit, dict)
            and set(merge_commit) >= {"oid"}
            and isinstance(merge_commit["oid"], str)
        ):
            merge_commit_value = merge_commit["oid"]
        else:
            raise GitHubContractError("GitHub mergeCommit has another shape")
        return cls(
            repository=repository,
            number=payload["number"],
            url=payload["url"],
            title=payload["title"],
            state=payload["state"],
            draft=payload["isDraft"],
            base_branch=payload["baseRefName"],
            head_branch=payload["headRefName"],
            head_commit=payload["headRefOid"],
            merge_state=payload["mergeStateStatus"],
            review_decision=payload["reviewDecision"] or "REVIEW_REQUIRED",
            merged_at=merged_instant,
            merge_commit=merge_commit_value,
            required_check_list=required_check_list,
        )

    def integration_identity_require(self, issue_identifier: str) -> None:
        """Require the issue identity that lets GitHub integration create a Linear link.

        Args:
            issue_identifier: Exact Linear issue identifier.
        """

        issue_identifier_in_title_require(issue_identifier, self.title)
        expected_branch = f"linear/{issue_identifier.lower()}"
        if self.head_branch != expected_branch:
            raise GitHubContractError(
                "Pull request lacks the exact Linear issue title and branch identity"
            )

    def target_require(self, *, base_branch: str, head_branch: str) -> None:
        """Require the PR to target the exact approved Git refs.

        Args:
            base_branch: Approved destination branch.
            head_branch: Approved task branch.
        """

        if self.base_branch != base_branch or self.head_branch != head_branch:
            raise GitHubContractError(
                "Pull request base or head differs from the approved repository target"
            )

    def merge_preconditions_require(self, *, approved_head_commit: str) -> None:
        """Require the exact approved candidate and all provider merge gates.

        Args:
            approved_head_commit: Human-approved PR head commit.
        """

        if _COMMIT_PATTERN.fullmatch(approved_head_commit) is None:
            raise GitHubContractError("Approved head must be one full lowercase commit")
        if self.head_commit != approved_head_commit:
            raise GitHubContractError("Pull request head changed after human approval")
        if self.state != "OPEN" or self.draft:
            raise GitHubContractError("Pull request must be open and ready for review")
        if self.merge_state not in {"CLEAN", "HAS_HOOKS"}:
            raise GitHubContractError(
                f"Pull request is not mergeable: {self.merge_state}"
            )
        if self.review_decision == "CHANGES_REQUESTED":
            raise GitHubContractError("Pull request has requested changes")
        failed_check_name_list = [
            item.name for item in self.required_check_list if not item.is_passed()
        ]
        if failed_check_name_list:
            raise GitHubContractError(
                f"Required GitHub checks are not passing: {sorted(failed_check_name_list)}"
            )

    def merged_result_require(self, *, approved_head_commit: str) -> None:
        """Require one exact already-merged human-approved candidate.

        This is the crash-recovery read path after the provider may have accepted
        the merge while the local process had not yet persisted its read-back.

        Args:
            approved_head_commit: Exact head covered by human approval.
        """

        if _COMMIT_PATTERN.fullmatch(approved_head_commit) is None:
            raise GitHubContractError("Approved head must be one full lowercase commit")
        if self.head_commit != approved_head_commit:
            raise GitHubContractError("Pull request head changed after human approval")
        if self.state != "MERGED" or self.merged_at is None or not self.merge_commit:
            raise GitHubContractError(
                "Pull request does not expose one complete merged result"
            )
        failed_check_name_list = [
            item.name for item in self.required_check_list if not item.is_passed()
        ]
        if failed_check_name_list:
            raise GitHubContractError(
                f"Required GitHub checks are not passing: {sorted(failed_check_name_list)}"
            )
