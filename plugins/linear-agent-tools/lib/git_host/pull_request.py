"""Exact GitHub pull-request operations through authenticated gh CLI."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
import subprocess

from json_contract import JsonContractError, json_load_strict

from git_host.atomic_merge import GitHubAtomicMergeBoundary
from git_host.authentication import GitHubPrincipal
from git_host.branch_protection import GitHubBranchProtectionBoundary
from git_host.command import CommandRunner, command_closed_run, command_run
from git_host.model import (
    BranchProtectionSnapshot,
    GitHubContractError,
    PullRequestMergeInspection,
    PullRequestSnapshot,
    RepositoryIdentity,
    RequiredCheck,
    branch_name_require,
    issue_identifier_in_title_require,
)


class GitHubPullRequestBoundary:
    """Expose domain PR operations instead of generic GitHub commands."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        branch_protection: GitHubBranchProtectionBoundary | None = None,
    ) -> None:
        """Initialize one authenticated gh command dependency.

        Args:
            runner: Optional deterministic command runner.
            branch_protection: Optional typed protection dependency.
        """

        self._runner = runner or command_run
        self._branch_protection = branch_protection or GitHubBranchProtectionBoundary(self._runner)

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
        branch_name_require(base_branch, label="pull-request base")
        branch_name_require(head_branch, label="pull-request head")
        if head_branch != expected_head:
            raise GitHubContractError("Pull request head branch omits the exact Linear issue identity")
        if body_file.is_symlink() or not body_file.is_file() or body_file.stat().st_nlink != 1:
            raise GitHubContractError("Pull request body must be one ordinary file")
        existing_number_list = self.matching_number_list(
            repository=repository,
            base_branch=base_branch,
            head_branch=head_branch,
        )
        active_snapshot_list: list[PullRequestSnapshot] = []
        for existing_number in existing_number_list:
            snapshot = self.inspect(repository=repository, number=existing_number)
            snapshot.integration_identity_require(issue_identifier)
            snapshot.target_require(base_branch=base_branch, head_branch=head_branch)
            if snapshot.state != "CLOSED":
                active_snapshot_list.append(snapshot)
        if len(active_snapshot_list) > 1:
            raise GitHubContractError("More than one active pull request matches the exact task branch and base")
        if active_snapshot_list:
            snapshot = active_snapshot_list[0]
            if snapshot.state != "OPEN":
                raise GitHubContractError("Existing exact task pull request cannot be adopted in its current state")
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
            raise GitHubContractError("Pull-request lookup repository identity is unsupported")
        branch_name_require(base_branch, label="pull-request base")
        branch_name_require(head_branch, label="pull-request head")
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
            payload = json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise GitHubContractError("GitHub pull-request lookup response is malformed") from error
        if not isinstance(payload, list) or any(not isinstance(page, list) for page in payload):
            raise GitHubContractError("GitHub pull-request lookup response has another shape")
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
                    raise GitHubContractError("GitHub pull-request lookup response has another shape")
                number_list.append(item["number"])
        if len(number_list) != len(set(number_list)):
            raise GitHubContractError("GitHub pull-request lookup repeated one pull request")
        return sorted(number_list)

    def inspect(self, *, repository: RepositoryIdentity, number: int) -> PullRequestSnapshot:
        """Read one exact PR identity without claiming unread merge gates.

        Args:
            repository: Exact GitHub repository.
            number: Pull request number.

        Returns:
            Fully typed identity snapshot with required checks marked unread.
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
                (
                    "number,url,title,state,isDraft,autoMergeRequest,baseRefName,baseRefOid,headRefName,headRefOid,"
                    "headRepository,headRepositoryOwner,isCrossRepository,mergeStateStatus,mergedAt,mergeCommit,"
                    "mergedBy"
                ),
            )
        )
        try:
            payload = json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise GitHubContractError("GitHub PR response is malformed") from error
        merged_principal = None
        if isinstance(payload, dict) and payload.get("state") == "MERGED":
            merged_principal = self._merged_principal_get(repository=repository, number=number)
        snapshot = PullRequestSnapshot.from_gh_payload(
            repository,
            payload,
            required_check_list=[],
            merged_by_user_id=merged_principal.user_id if merged_principal is not None else 0,
        )
        if merged_principal is not None and (
            snapshot.merged_by_login != merged_principal.login or snapshot.merged_by_node_id != merged_principal.node_id
        ):
            raise GitHubContractError("GitHub merged provider identities disagree")
        expected_url = f"https://github.com/{repository.value}/pull/{number}"
        if snapshot.number != number or snapshot.url.rstrip("/") != expected_url:
            raise GitHubContractError("GitHub pull-request response differs from the exact requested identity")
        return snapshot

    def reviewed_inspect(
        self,
        *,
        repository: RepositoryIdentity,
        number: int,
        issue_identifier: str,
        base_branch: str,
        head_branch: str,
        reviewed_base_commit: str,
        reviewed_head_commit: str,
        merge_method: str,
        repository_path: Path | None = None,
    ) -> PullRequestMergeInspection:
        """Read exact reviewed PR, effective protection and required check results.

        Args:
            repository: Exact GitHub repository.
            number: Pull request number.
            issue_identifier: Exact Linear issue identifier.
            base_branch: Declared destination branch.
            head_branch: Deterministic task branch.
            reviewed_base_commit: Exact independently reviewed base commit.
            reviewed_head_commit: Exact independently reviewed head.
            merge_method: Declared repository merge strategy.
            repository_path: Exact worktree required to prove a terminal merge result.

        Returns:
            Terminal PR alone, or open PR plus executing-identity-bound protection.
        """

        snapshot = self.inspect(repository=repository, number=number)
        snapshot.target_require(base_branch=base_branch, head_branch=head_branch)
        snapshot.task_branch_identity_require(issue_identifier)
        if snapshot.state == "MERGED":
            if merge_method != "merge":
                raise GitHubContractError(
                    "Merged squash and rebase results are unsupported without exact immutable strategy proof"
                )
            if repository_path is None:
                raise GitHubContractError("Merged-result inspection requires the exact repository worktree path")
            GitHubAtomicMergeBoundary(self._runner).merged_result_require(
                repository=repository,
                repository_path=repository_path,
                snapshot=snapshot,
                reviewed_base_commit=reviewed_base_commit,
                reviewed_head_commit=reviewed_head_commit,
            )
            return PullRequestMergeInspection(pull_request=snapshot, branch_protection=None)
        if snapshot.state == "CLOSED":
            raise GitHubContractError("Closed unmerged pull request is never successful merge evidence")
        snapshot.integration_identity_require(issue_identifier)
        snapshot.reviewed_open_identity_require(
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
        )
        protection = self._branch_protection.inspect(repository=repository, base_branch=base_branch)
        protection.merge_mechanism_require(merge_method)
        required_check_list = self._required_check_list_get(
            repository=repository,
            number=number,
            required_check_name_list=protection.required_check_name_list,
        )
        protection.required_check_results_require(required_check_list)
        snapshot = replace(
            snapshot,
            required_check_list=required_check_list,
            required_checks_verified=True,
        )
        snapshot.merge_preconditions_require(
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
        )
        return PullRequestMergeInspection(pull_request=snapshot, branch_protection=protection)

    def merge(
        self,
        *,
        repository: RepositoryIdentity,
        number: int,
        issue_identifier: str,
        base_branch: str,
        head_branch: str,
        reviewed_base_commit: str,
        reviewed_head_commit: str,
        merge_method: str,
        repository_path: Path | None = None,
    ) -> PullRequestSnapshot:
        """Merge only one exact independently reviewed PR base and head.

        Args:
            repository: Exact GitHub repository.
            number: Pull request number.
            issue_identifier: Exact Linear issue identifier.
            base_branch: Declared destination branch.
            head_branch: Deterministic task branch.
            reviewed_base_commit: Exact independently reviewed base commit.
            reviewed_head_commit: Exact independently reviewed head.
            merge_method: Declared repository-supported merge, squash or rebase method.
            repository_path: Exact task worktree, required for reviewed-base CAS merges.

        Returns:
            Merged PR snapshot.
        """

        if merge_method != "merge":
            raise GitHubContractError(
                "Squash and rebase mutation are unsupported without exact immutable strategy proof"
            )
        inspection = self.reviewed_inspect(
            repository=repository,
            number=number,
            issue_identifier=issue_identifier,
            base_branch=base_branch,
            head_branch=head_branch,
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
            merge_method=merge_method,
            repository_path=repository_path,
        )
        before = inspection.pull_request
        if before.state == "MERGED":
            return before
        protection = inspection.branch_protection
        if protection is None:
            raise GitHubContractError("Open pull-request merge omitted applicable branch protection")
        if repository_path is None:
            raise GitHubContractError("Atomic merge requires the exact repository worktree path")
        expected_merge_commit = GitHubAtomicMergeBoundary(self._runner).merge(
            repository=repository,
            repository_path=repository_path,
            snapshot=before,
            execution_login=protection.execution_login,
            execution_user_id=protection.execution_user_id,
            execution_node_id=protection.execution_node_id,
            merge_method=merge_method,
        )
        after = self.inspect(repository=repository, number=number)
        after.target_require(base_branch=base_branch, head_branch=head_branch)
        after.task_branch_identity_require(issue_identifier)
        if expected_merge_commit and after.state != "MERGED":
            raise GitHubContractError(
                "Reviewed base CAS completed but GitHub merge readback is not terminal; retry exact recovery"
            )
        after.merged_metadata_require(
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
        )
        after.merged_by_require(
            login=protection.execution_login,
            user_id=protection.execution_user_id,
            node_id=protection.execution_node_id,
        )
        if after.merge_commit != expected_merge_commit:
            raise GitHubContractError("GitHub merged result differs from the reviewed base CAS transaction")
        GitHubAtomicMergeBoundary(self._runner).merged_result_require(
            repository=repository,
            repository_path=repository_path,
            snapshot=after,
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
        )
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
            raise GitHubContractError("Canceled-task pull request did not reach a terminal state")
        return snapshot

    def _merged_principal_get(
        self,
        *,
        repository: RepositoryIdentity,
        number: int,
    ) -> GitHubPrincipal:
        """Read the terminal REST principal including its numeric database ID."""

        completed_process = self._checked(
            (
                "api",
                "--hostname",
                "github.com",
                f"repos/{repository.value}/pulls/{number}",
                "--jq",
                "{login:.merged_by.login,user_id:.merged_by.id,node_id:.merged_by.node_id}",
            )
        )
        try:
            payload = json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise GitHubContractError("GitHub merged principal response is malformed") from error
        if not isinstance(payload, dict) or set(payload) != {"login", "user_id", "node_id"}:
            raise GitHubContractError("GitHub merged principal response has another shape")
        try:
            return GitHubPrincipal(
                login=payload["login"],
                user_id=payload["user_id"],
                node_id=payload["node_id"],
            )
        except (KeyError, TypeError) as error:
            raise GitHubContractError("GitHub merged principal response has another shape") from error

    def _required_check_list_get(
        self,
        *,
        repository: RepositoryIdentity,
        number: int,
        required_check_name_list: list[str],
    ) -> list[RequiredCheck]:
        """Read branch-protection-required check results only.

        Args:
            repository: Exact GitHub repository.
            number: Pull request number.
            required_check_name_list: Exact definitions from effective protection.

        Returns:
            Required check results.
        """

        if not required_check_name_list:
            completed_process = command_closed_run(
                self._runner,
                [
                    "gh",
                    "pr",
                    "view",
                    str(number),
                    "--repo",
                    repository.value,
                    "--json",
                    "statusCheckRollup",
                ],
            )
            if completed_process.returncode != 0 or not completed_process.stdout:
                raise GitHubContractError("Unable to read empty required GitHub check set")
            try:
                payload = json_load_strict(completed_process.stdout)
            except JsonContractError as error:
                raise GitHubContractError("GitHub status-check rollup response is malformed") from error
            if (
                not isinstance(payload, dict)
                or set(payload) != {"statusCheckRollup"}
                or not isinstance(payload["statusCheckRollup"], list)
                or any(not isinstance(item, dict) for item in payload["statusCheckRollup"])
            ):
                raise GitHubContractError("GitHub status-check rollup response has another shape")
            return []
        completed_process = command_closed_run(
            self._runner,
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
            ],
        )
        if completed_process.returncode not in {0, 8} or not completed_process.stdout:
            raise GitHubContractError("Unable to read required GitHub checks")
        try:
            payload = json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise GitHubContractError("GitHub required-check response is malformed") from error
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise GitHubContractError("GitHub required-check response has another shape")
        required_check_list: list[RequiredCheck] = []
        for item in payload:
            if set(item) != {"name", "bucket", "link"}:
                raise GitHubContractError("GitHub required-check item has another shape")
            required_check_list.append(RequiredCheck(name=item["name"], bucket=item["bucket"], link=item["link"] or ""))
        return sorted(required_check_list, key=lambda item: item.name)

    def _checked(self, argument_list: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Run one checked gh domain command without exposing raw provider output.

        Args:
            argument_list: Arguments after executable name.

        Returns:
            Completed command.
        """

        completed_process = command_closed_run(self._runner, ["gh", *argument_list])
        if completed_process.returncode != 0:
            raise GitHubContractError("Authenticated GitHub pull-request operation failed")
        return completed_process


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
