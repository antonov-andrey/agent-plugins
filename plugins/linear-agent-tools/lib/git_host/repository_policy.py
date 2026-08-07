"""Strict principal-bound GitHub repository merge-policy inspection."""

from __future__ import annotations

from dataclasses import dataclass

from json_contract import JsonContractError, json_load_strict

from git_host.authentication import GitHubAuthenticationBoundary, GitHubPrincipal
from git_host.command import CommandRunner, command_closed_run
from git_host.model import GitHubContractError, RepositoryIdentity, branch_name_require

_VISIBILITY_SET = {"public", "private", "internal"}
_OWNER_TYPE_SET = {"User", "Organization"}
_SQUASH_TITLE_SET = {"PR_TITLE", "COMMIT_OR_PR_TITLE"}
_SQUASH_MESSAGE_SET = {"PR_BODY", "COMMIT_MESSAGES", "BLANK"}
_MERGE_TITLE_SET = {"PR_TITLE", "MERGE_MESSAGE"}
_MERGE_MESSAGE_SET = {"PR_TITLE", "PR_BODY", "BLANK"}


@dataclass(frozen=True, slots=True)
class GitHubRepositoryMergePolicy:
    """Bind every relevant repository merge setting to one fresh principal."""

    repository: RepositoryIdentity
    principal: GitHubPrincipal
    repository_id: int
    repository_node_id: str
    owner_login: str
    owner_id: int
    owner_node_id: str
    owner_type: str
    owner_site_admin: bool
    private: bool
    fork: bool
    archived: bool
    disabled: bool
    visibility: str
    default_branch: str
    mirror_url: str | None
    allow_forking: bool
    is_template: bool
    web_commit_signoff_required: bool
    has_discussions: bool
    allow_squash_merge: bool
    allow_merge_commit: bool
    allow_rebase_merge: bool
    allow_auto_merge: bool
    delete_branch_on_merge: bool
    use_squash_pr_title_as_default: bool
    squash_merge_commit_title: str
    squash_merge_commit_message: str
    merge_commit_title: str
    merge_commit_message: str
    allow_update_branch: bool

    def __post_init__(self) -> None:
        """Reject missing, malformed, inactive or identity-conflicting policy."""

        if not isinstance(self.repository, RepositoryIdentity):
            raise GitHubContractError("GitHub repository policy repository has another shape")
        owner_name, repository_name = self.repository.value.split("/", 1)
        if not isinstance(self.principal, GitHubPrincipal):
            raise GitHubContractError("GitHub repository policy principal has another shape")
        for label, value in (("repository", self.repository_id), ("owner", self.owner_id)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise GitHubContractError(f"GitHub repository policy {label} ID has another shape")
        for label, value in (
            ("repository node", self.repository_node_id),
            ("owner node", self.owner_node_id),
            ("owner login", self.owner_login),
            ("default branch", self.default_branch),
        ):
            if not isinstance(value, str) or not value or any(character in value for character in "\x00\n\r"):
                raise GitHubContractError(f"GitHub repository policy {label} has another shape")
        if self.owner_login != owner_name or repository_name == "":
            raise GitHubContractError("GitHub repository policy identity differs from the declared repository")
        branch_name_require(self.default_branch, label="repository default")
        if (
            not isinstance(self.owner_type, str)
            or not isinstance(self.visibility, str)
            or self.owner_type not in _OWNER_TYPE_SET
            or self.visibility not in _VISIBILITY_SET
        ):
            raise GitHubContractError("GitHub repository policy classification has another shape")
        bool_value_list = [
            self.owner_site_admin,
            self.private,
            self.fork,
            self.archived,
            self.disabled,
            self.allow_forking,
            self.is_template,
            self.web_commit_signoff_required,
            self.has_discussions,
            self.allow_squash_merge,
            self.allow_merge_commit,
            self.allow_rebase_merge,
            self.allow_auto_merge,
            self.delete_branch_on_merge,
            self.use_squash_pr_title_as_default,
            self.allow_update_branch,
        ]
        if any(not isinstance(value, bool) for value in bool_value_list):
            raise GitHubContractError("GitHub repository policy boolean field has another shape")
        if self.mirror_url is not None and (
            not isinstance(self.mirror_url, str)
            or not self.mirror_url
            or any(character in self.mirror_url for character in "\x00\n\r")
        ):
            raise GitHubContractError("GitHub repository policy mirror URL has another shape")
        merge_option_list = [
            self.squash_merge_commit_title,
            self.squash_merge_commit_message,
            self.merge_commit_title,
            self.merge_commit_message,
        ]
        if any(not isinstance(value, str) for value in merge_option_list) or (
            self.squash_merge_commit_title not in _SQUASH_TITLE_SET
            or self.squash_merge_commit_message not in _SQUASH_MESSAGE_SET
            or self.merge_commit_title not in _MERGE_TITLE_SET
            or self.merge_commit_message not in _MERGE_MESSAGE_SET
        ):
            raise GitHubContractError("GitHub repository merge-policy option has another shape")
        if self.archived or self.disabled:
            raise GitHubContractError("GitHub repository policy is inactive")

    def selected_method_require(self, merge_method: str) -> None:
        """Require the declared merge method to be enabled by this exact read."""

        if not isinstance(merge_method, str):
            raise GitHubContractError("Declared repository merge method has another shape")
        enabled_by_method = {
            "merge": self.allow_merge_commit,
            "squash": self.allow_squash_merge,
            "rebase": self.allow_rebase_merge,
        }
        if merge_method not in enabled_by_method or not enabled_by_method[merge_method]:
            raise GitHubContractError("Declared repository merge method is not enabled")
        if merge_method == "merge" and self.delete_branch_on_merge:
            raise GitHubContractError("Exact merge requires GitHub automatic head-branch deletion to be disabled")


class GitHubRepositoryMergePolicyBoundary:
    """Read complete relevant repository policy around fresh principal checks."""

    def __init__(self, runner: CommandRunner) -> None:
        """Initialize one strict direct-command repository-policy reader.

        Args:
            runner: Shared deterministic or subprocess command runner.
        """

        self._runner = runner

    def inspect(
        self,
        *,
        repository: RepositoryIdentity,
        principal: GitHubPrincipal,
        merge_method: str,
    ) -> GitHubRepositoryMergePolicy:
        """Return one strict repository policy bound to an unchanged principal.

        Args:
            repository: Exact GitHub repository identity.
            principal: Exact authenticated mutation principal.
            merge_method: Declared method that must be enabled.

        Returns:
            Complete relevant repository policy snapshot.
        """

        if not isinstance(repository, RepositoryIdentity) or not isinstance(principal, GitHubPrincipal):
            raise GitHubContractError("GitHub repository policy identity has another shape")
        authentication = GitHubAuthenticationBoundary(self._runner)
        authentication.principal_require(principal)
        completed_process = command_closed_run(
            self._runner,
            ["gh", "api", "--hostname", "github.com", f"repos/{repository.value}"],
        )
        if completed_process.returncode != 0 or not completed_process.stdout:
            raise GitHubContractError("GitHub repository merge-policy read failed")
        try:
            payload = json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise GitHubContractError("GitHub repository merge-policy response is malformed") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("owner"), dict):
            raise GitHubContractError("GitHub repository merge-policy response has another shape")
        owner = payload["owner"]
        try:
            if payload["name"] != repository.value.split("/", 1)[1] or payload["full_name"] != repository.value:
                raise GitHubContractError("GitHub repository policy identity differs from the declared repository")
            policy = GitHubRepositoryMergePolicy(
                repository=repository,
                principal=principal,
                repository_id=payload["id"],
                repository_node_id=payload["node_id"],
                owner_login=owner["login"],
                owner_id=owner["id"],
                owner_node_id=owner["node_id"],
                owner_type=owner["type"],
                owner_site_admin=owner["site_admin"],
                private=payload["private"],
                fork=payload["fork"],
                archived=payload["archived"],
                disabled=payload["disabled"],
                visibility=payload["visibility"],
                default_branch=payload["default_branch"],
                mirror_url=payload["mirror_url"],
                allow_forking=payload["allow_forking"],
                is_template=payload["is_template"],
                web_commit_signoff_required=payload["web_commit_signoff_required"],
                has_discussions=payload["has_discussions"],
                allow_squash_merge=payload["allow_squash_merge"],
                allow_merge_commit=payload["allow_merge_commit"],
                allow_rebase_merge=payload["allow_rebase_merge"],
                allow_auto_merge=payload["allow_auto_merge"],
                delete_branch_on_merge=payload["delete_branch_on_merge"],
                use_squash_pr_title_as_default=payload["use_squash_pr_title_as_default"],
                squash_merge_commit_title=payload["squash_merge_commit_title"],
                squash_merge_commit_message=payload["squash_merge_commit_message"],
                merge_commit_title=payload["merge_commit_title"],
                merge_commit_message=payload["merge_commit_message"],
                allow_update_branch=payload["allow_update_branch"],
            )
        except KeyError as error:
            raise GitHubContractError("GitHub repository merge-policy response has another shape") from error
        policy.selected_method_require(merge_method)
        authentication.principal_require(principal)
        return policy
