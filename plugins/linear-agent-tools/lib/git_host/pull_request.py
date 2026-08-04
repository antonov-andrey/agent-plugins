"""Exact GitHub pull-request operations through authenticated gh CLI."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
from pathlib import Path
import subprocess

from git_host.model import (
    GitHubContractError,
    PullRequestSnapshot,
    RepositoryIdentity,
    RequiredCheck,
    issue_identifier_in_title_require,
)


class GitHubPullRequestBoundary:
    """Expose domain PR operations instead of generic GitHub commands."""

    def __init__(
        self,
        runner: (
            Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None
        ) = None,
    ) -> None:
        """Initialize one authenticated gh command dependency.

        Args:
            runner: Optional deterministic command runner.
        """

        self._runner = runner or _gh_run

    def create(
        self,
        *,
        repository: RepositoryIdentity,
        issue_identifier: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body_file: Path,
    ) -> PullRequestSnapshot:
        """Create one issue-linked pull request and read it back.

        Args:
            repository: Exact GitHub repository.
            issue_identifier: Exact Linear issue identifier.
            base_branch: Approved base branch.
            head_branch: Deterministic task branch.
            title: Human-readable title including issue identifier.
            body_file: Ordinary Markdown body file.

        Returns:
            Created exact pull request snapshot.
        """

        expected_head = f"linear/{issue_identifier.lower()}"
        issue_identifier_in_title_require(issue_identifier, title)
        _branch_require(base_branch, label="base")
        _branch_require(head_branch, label="head")
        if head_branch != expected_head:
            raise GitHubContractError(
                "Pull request head branch omits the exact Linear issue identity"
            )
        if (
            body_file.is_symlink()
            or not body_file.is_file()
            or body_file.stat().st_nlink != 1
        ):
            raise GitHubContractError("Pull request body must be one ordinary file")
        existing_number_list = self.matching_number_list(
            repository=repository,
            base_branch=base_branch,
            head_branch=head_branch,
        )
        if len(existing_number_list) > 1:
            raise GitHubContractError(
                "More than one pull request matches the exact task branch and base"
            )
        if existing_number_list:
            snapshot = self.inspect(
                repository=repository, number=existing_number_list[0]
            )
            snapshot.integration_identity_require(issue_identifier)
            snapshot.target_require(base_branch=base_branch, head_branch=head_branch)
            if snapshot.state != "OPEN":
                raise GitHubContractError(
                    "Existing exact task pull request cannot be adopted in its current state"
                )
            return snapshot
        output = self._checked(
            (
                "pr",
                "create",
                "--repo",
                repository.value,
                "--base",
                base_branch,
                "--head",
                head_branch,
                "--title",
                title,
                "--body-file",
                str(body_file),
            )
        ).stdout.strip()
        number = _pr_number_from_url(output)
        snapshot = self.inspect(repository=repository, number=number)
        snapshot.integration_identity_require(issue_identifier)
        snapshot.target_require(base_branch=base_branch, head_branch=head_branch)
        return snapshot

    def matching_number_list(
        self,
        *,
        repository: RepositoryIdentity,
        base_branch: str,
        head_branch: str,
    ) -> list[int]:
        """Return exact existing pull requests for one base/head pair.

        Args:
            repository: Exact GitHub repository.
            base_branch: Approved base branch.
            head_branch: Deterministic task branch.

        Returns:
            Sorted matching pull-request numbers.
        """

        if not isinstance(repository, RepositoryIdentity):
            raise GitHubContractError(
                "Pull-request lookup repository identity is unsupported"
            )
        _branch_require(base_branch, label="base")
        _branch_require(head_branch, label="head")
        owner, _name = repository.value.split("/", 1)
        completed_process = self._checked(
            (
                "api",
                "--method",
                "GET",
                "--paginate",
                "--slurp",
                f"repos/{repository.value}/pulls",
                "-f",
                "state=all",
                "-f",
                f"head={owner}:{head_branch}",
                "-f",
                f"base={base_branch}",
                "-f",
                "per_page=100",
            )
        )
        try:
            payload = json.loads(completed_process.stdout)
        except json.JSONDecodeError as error:
            raise GitHubContractError(
                "GitHub pull-request lookup response is malformed"
            ) from error
        if not isinstance(payload, list) or any(
            not isinstance(page, list) for page in payload
        ):
            raise GitHubContractError(
                "GitHub pull-request lookup response has another shape"
            )
        number_list: list[int] = []
        for page in payload:
            for item in page:
                if (
                    not isinstance(item, dict)
                    or isinstance(item.get("number"), bool)
                    or not isinstance(item.get("number"), int)
                    or item["number"] < 1
                    or not isinstance(item.get("base"), dict)
                    or not isinstance(item.get("head"), dict)
                    or item["base"].get("ref") != base_branch
                    or item["head"].get("ref") != head_branch
                ):
                    raise GitHubContractError(
                        "GitHub pull-request lookup response has another shape"
                    )
                number_list.append(item["number"])
        if len(number_list) != len(set(number_list)):
            raise GitHubContractError(
                "GitHub pull-request lookup repeated one pull request"
            )
        return sorted(number_list)

    def inspect(
        self, *, repository: RepositoryIdentity, number: int
    ) -> PullRequestSnapshot:
        """Read one exact PR and its required checks.

        Args:
            repository: Exact GitHub repository.
            number: Pull request number.

        Returns:
            Fully typed snapshot.
        """

        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise GitHubContractError("Pull request number must be positive")
        completed_process = self._checked(
            (
                "pr",
                "view",
                str(number),
                "--repo",
                repository.value,
                "--json",
                "number,url,title,state,isDraft,baseRefName,headRefName,headRefOid,mergeStateStatus,reviewDecision,mergedAt,mergeCommit",
            )
        )
        try:
            payload = json.loads(completed_process.stdout)
        except json.JSONDecodeError as error:
            raise GitHubContractError("GitHub PR response is malformed") from error
        required_check_list = self._required_check_list_get(
            repository=repository, number=number
        )
        snapshot = PullRequestSnapshot.from_gh_payload(
            repository,
            payload,
            required_check_list=required_check_list,
        )
        expected_url = f"https://github.com/{repository.value}/pull/{number}"
        if snapshot.number != number or snapshot.url.rstrip("/") != expected_url:
            raise GitHubContractError(
                "GitHub pull-request response differs from the exact requested identity"
            )
        return snapshot

    def merge(
        self,
        *,
        repository: RepositoryIdentity,
        number: int,
        issue_identifier: str,
        base_branch: str,
        head_branch: str,
        approved_head_commit: str,
        merge_method: str,
    ) -> PullRequestSnapshot:
        """Merge only one exact human-approved PR head and verify final state.

        Args:
            repository: Exact GitHub repository.
            number: Pull request number.
            issue_identifier: Exact Linear issue identifier.
            base_branch: Approved destination branch.
            head_branch: Approved task branch.
            approved_head_commit: Exact human-approved head.
            merge_method: Approved repository-supported merge, squash or rebase method.

        Returns:
            Merged PR snapshot.
        """

        if merge_method not in {"merge", "squash", "rebase"}:
            raise GitHubContractError("Pull request merge method is unsupported")
        before = self.inspect(repository=repository, number=number)
        before.integration_identity_require(issue_identifier)
        before.target_require(base_branch=base_branch, head_branch=head_branch)
        if before.state == "MERGED":
            before.merged_result_require(approved_head_commit=approved_head_commit)
            return before
        before.merge_preconditions_require(approved_head_commit=approved_head_commit)
        self._checked(
            (
                "pr",
                "merge",
                str(number),
                "--repo",
                repository.value,
                f"--{merge_method}",
                "--match-head-commit",
                approved_head_commit,
            )
        )
        after = self.inspect(repository=repository, number=number)
        after.integration_identity_require(issue_identifier)
        after.target_require(base_branch=base_branch, head_branch=head_branch)
        after.merged_result_require(approved_head_commit=approved_head_commit)
        return after

    def close_if_open(
        self,
        *,
        repository: RepositoryIdentity,
        number: int,
        issue_identifier: str,
        base_branch: str,
        head_branch: str,
    ) -> PullRequestSnapshot:
        """Close one exact canceled-task pull request idempotently.

        Args:
            repository: Exact GitHub repository.
            number: Pull request number.
            issue_identifier: Exact Linear issue identifier.
            base_branch: Approved destination branch.
            head_branch: Deterministic task branch.

        Returns:
            Current closed, merged or already closed snapshot.
        """

        snapshot = self.inspect(repository=repository, number=number)
        snapshot.integration_identity_require(issue_identifier)
        snapshot.target_require(base_branch=base_branch, head_branch=head_branch)
        if snapshot.state == "OPEN":
            self._checked(("pr", "close", str(number), "--repo", repository.value))
            snapshot = self.inspect(repository=repository, number=number)
            snapshot.integration_identity_require(issue_identifier)
            snapshot.target_require(base_branch=base_branch, head_branch=head_branch)
        if snapshot.state not in {"CLOSED", "MERGED"}:
            raise GitHubContractError(
                "Canceled-task pull request did not reach a terminal state"
            )
        return snapshot

    def _required_check_list_get(
        self, *, repository: RepositoryIdentity, number: int
    ) -> list[RequiredCheck]:
        """Read branch-protection-required check results only.

        Args:
            repository: Exact GitHub repository.
            number: Pull request number.

        Returns:
            Required check results.
        """

        completed_process = self._runner(
            [
                "gh",
                "pr",
                "checks",
                str(number),
                "--repo",
                repository.value,
                "--required",
                "--json",
                "name,bucket,link",
            ]
        )
        if completed_process.returncode not in {0, 1, 8}:
            raise GitHubContractError("Unable to read required GitHub checks")
        try:
            payload = json.loads(completed_process.stdout or "[]")
        except json.JSONDecodeError as error:
            raise GitHubContractError(
                "GitHub required-check response is malformed"
            ) from error
        if not isinstance(payload, list) or any(
            not isinstance(item, dict) for item in payload
        ):
            raise GitHubContractError(
                "GitHub required-check response has another shape"
            )
        required_check_list: list[RequiredCheck] = []
        for item in payload:
            if set(item) != {"name", "bucket", "link"}:
                raise GitHubContractError(
                    "GitHub required-check item has another shape"
                )
            required_check_list.append(
                RequiredCheck(
                    name=item["name"], bucket=item["bucket"], link=item["link"] or ""
                )
            )
        return sorted(required_check_list, key=lambda item: item.name)

    def _checked(
        self, argument_list: Sequence[str]
    ) -> subprocess.CompletedProcess[str]:
        """Run one checked gh domain command without exposing raw provider output.

        Args:
            argument_list: Arguments after executable name.

        Returns:
            Completed command.
        """

        completed_process = self._runner(["gh", *argument_list])
        if completed_process.returncode != 0:
            raise GitHubContractError(
                "Authenticated GitHub pull-request operation failed"
            )
        return completed_process


def _gh_run(argument_list: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one gh command with captured text output.

    Args:
        argument_list: Complete direct argv.

    Returns:
        Completed command.
    """

    return subprocess.run(argument_list, check=False, capture_output=True, text=True)


def _pr_number_from_url(value: str) -> int:
    """Extract one PR number from gh's canonical creation result.

    Args:
        value: Candidate PR URL.

    Returns:
        Positive PR number.
    """

    tail = value.rstrip("/").rsplit("/", 1)[-1]
    if not tail.isdigit() or int(tail) < 1:
        raise GitHubContractError("GitHub did not return a canonical pull-request URL")
    return int(tail)


def _branch_require(value: str, *, label: str) -> None:
    """Reject branch text that cannot be one safe GitHub ref name.

    Args:
        value: Candidate branch name.
        label: Diagnostic branch role.
    """

    forbidden_character_set = set(" ~^:?*[\\")
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("-", "."))
        or value.endswith((".", "/", ".lock"))
        or ".." in value
        or "//" in value
        or "@{" in value
        or any(
            character in forbidden_character_set or ord(character) < 32
            for character in value
        )
    ):
        raise GitHubContractError(
            f"Pull request {label} branch has an unsafe ref shape"
        )
