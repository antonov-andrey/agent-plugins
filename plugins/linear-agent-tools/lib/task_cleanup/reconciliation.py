"""Idempotent cleanup sequencing for exact task-owned state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from git_host.model import RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary
from task_cleanup.model import (
    CleanupRequest,
    PullRequestReference,
    PullRequestTarget,
    TaskCleanupError,
)
from task_cleanup.resource import ResourceCleaner, cleanup_binding_run
from task_workspace.lock import IssueWorkspaceLock
from task_workspace.model import (
    RepositoryWorkspaceState,
    TaskWorkspaceError,
    WorkspaceConfig,
)
from task_workspace.repository import GitCommand, WorkspaceRepository


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Summarize exact cleanup reconciliation without duplicating Linear history."""

    closed_pull_request_count: int
    cleaned_resource_count: int
    removed_worktree_count: int
    removed_local_branch_count: int
    removed_remote_branch_count: int

    def payload(self) -> dict[str, int]:
        """Return one JSON-ready result.

        Returns:
            Cleanup counters.
        """

        return {
            "schema_version": 1,
            "cleaned_resource_count": self.cleaned_resource_count,
            "closed_pull_request_count": self.closed_pull_request_count,
            "removed_local_branch_count": self.removed_local_branch_count,
            "removed_remote_branch_count": self.removed_remote_branch_count,
            "removed_worktree_count": self.removed_worktree_count,
        }


class TaskCleanupReconciler:
    """Delete only state whose exact Linear and Git ownership is proven."""

    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        github: GitHubPullRequestBoundary | None = None,
        resources: ResourceCleaner | None = None,
    ) -> None:
        """Initialize explicit external boundaries.

        Args:
            config: Exact configured workspace root.
            github: Typed GitHub PR boundary.
            resources: Direct-argv resource cleaner.
        """

        self._config = config
        self._github = github or GitHubPullRequestBoundary()
        self._resources = resources or ResourceCleaner()

    def cleanup(self, request: CleanupRequest) -> CleanupResult:
        """Reconcile every exact requested cleanup target idempotently.

        Args:
            request: Complete cleanup authority and target set.

        Returns:
            Exact cleanup counters.
        """

        with IssueWorkspaceLock(self._config, request.issue_identifier):
            repository_by_origin_url_map = {
                item.origin_url: WorkspaceRepository.from_config(self._config, item) for item in request.repository_list
            }
            for repository in repository_by_origin_url_map.values():
                repository.fetch()
            workspace_state_by_origin_url_map = {
                origin_url: repository.state_read(request.issue_identifier)
                for origin_url, repository in repository_by_origin_url_map.items()
            }
            for origin_url, state in workspace_state_by_origin_url_map.items():
                if state is not None:
                    repository_by_origin_url_map[origin_url].state_identity_require(request.issue_identifier, state)
                else:
                    self._absence_require(
                        repository_by_origin_url_map[origin_url],
                        request.issue_identifier,
                    )
            if request.authority.scope != "attempt":
                self._pull_request_contract_require(
                    request,
                    list(repository_by_origin_url_map.values()),
                )
                for origin_url, state in workspace_state_by_origin_url_map.items():
                    if state is None:
                        continue
                    state = self._cleanup_branch_snapshot_prepare(
                        repository_by_origin_url_map[origin_url],
                        state,
                        successful=request.authority.issue_status == "Done",
                        pull_request_list=request.pull_request_list,
                    )
                    repository_by_origin_url_map[origin_url].state_write(state)
                    workspace_state_by_origin_url_map[origin_url] = state
            for resource in request.resource_list:
                if resource.repository_url not in repository_by_origin_url_map:
                    raise TaskCleanupError(f"Resource {resource.key} has no exact participating repository")
            closed_pull_request_count = 0
            canceled = request.authority.issue_status == "Canceled" or request.authority.project_status == "Canceled"
            for reference in request.pull_request_list:
                before = self._github.inspect(repository=reference.repository, number=reference.number)
                target = self._pull_request_target_get(
                    reference.repository.value,
                    request=request,
                    repository_list=list(repository_by_origin_url_map.values()),
                )
                before.integration_identity_require(request.issue_identifier)
                before.target_require(base_branch=target.base_branch, head_branch=target.head_branch)
                if canceled:
                    self._github.close_if_open(
                        repository=reference.repository,
                        number=reference.number,
                        issue_identifier=request.issue_identifier,
                        base_branch=target.base_branch,
                        head_branch=target.head_branch,
                    )
                    if before.state == "OPEN":
                        closed_pull_request_count += 1
                elif before.state != "MERGED":
                    raise TaskCleanupError("Successful task cleanup requires every linked pull request to be merged")
            cleaned_resource_count = 0
            for resource in request.resource_list:
                repository = repository_by_origin_url_map[resource.repository_url]
                state = workspace_state_by_origin_url_map[resource.repository_url]
                if request.authority.scope != "attempt" and state is not None:
                    cleaned_resource_fingerprint_by_resource_key_map = dict(
                        state.cleaned_resource_fingerprint_by_resource_key_map
                    )
                    if resource.key in cleaned_resource_fingerprint_by_resource_key_map:
                        if cleaned_resource_fingerprint_by_resource_key_map[resource.key] != resource.fingerprint():
                            raise TaskCleanupError(
                                f"Cleanup declaration changed after exact resource {resource.key} was reconciled"
                            )
                        continue
                task_root = Path(state.task_root) if state is not None else None
                if state is not None:
                    self._task_root_require(repository, state)
                working_directory = task_root if task_root is not None else repository.main_root
                placeholder_by_name_map = (
                    state.cleanup_placeholder_map(request.issue_identifier)
                    if state is not None
                    else {
                        "linear_issue_identifier": request.issue_identifier,
                        "main_root": str(repository.main_root),
                        "task_branch": f"linear/{request.issue_identifier.lower()}",
                        "task_root": str(repository.main_root / ".worktree" / request.issue_identifier.lower()),
                    }
                )
                self._resources.cleanup(
                    resource,
                    working_directory=working_directory,
                    placeholder_by_name_map=placeholder_by_name_map,
                )
                if state is not None and request.authority.scope != "attempt":
                    cleaned_resource_fingerprint_by_resource_key_map[resource.key] = resource.fingerprint()
                    state = replace(
                        state,
                        cleaned_resource_fingerprint_by_resource_key_map=dict(
                            sorted(cleaned_resource_fingerprint_by_resource_key_map.items())
                        ),
                    )
                    repository.state_write(state)
                    workspace_state_by_origin_url_map[resource.repository_url] = state
                cleaned_resource_count += 1
            if request.authority.scope == "attempt":
                return CleanupResult(
                    closed_pull_request_count=0,
                    cleaned_resource_count=cleaned_resource_count,
                    removed_worktree_count=0,
                    removed_local_branch_count=0,
                    removed_remote_branch_count=0,
                )
            removed_worktree_count = 0
            removed_local_branch_count = 0
            removed_remote_branch_count = 0
            for origin_url, repository in repository_by_origin_url_map.items():
                state = workspace_state_by_origin_url_map[origin_url]
                if state is None:
                    self._absence_require(repository, request.issue_identifier)
                    continue
                task_root = Path(state.task_root)
                if not state.cleanup_binding_completed:
                    self._task_root_require(repository, state)
                    cleanup_binding_run(
                        state.cleanup_argument_list,
                        working_directory=task_root,
                        placeholder_by_name_map=state.cleanup_placeholder_map(request.issue_identifier),
                    )
                    state = replace(state, cleanup_binding_completed=True)
                    repository.state_write(state)
                registered_branch = repository.worktree_branch_get(task_root)
                if state.worktree_removed and (task_root.exists() or registered_branch is not None):
                    raise TaskCleanupError("Removed task worktree reappeared after durable cleanup")
                if registered_branch is not None and registered_branch != state.branch_name:
                    raise TaskCleanupError("Task worktree registration changed after durable cleanup snapshot")
                state = self._cleanup_branch_snapshot_prepare(
                    repository,
                    state,
                    successful=request.authority.issue_status == "Done",
                    pull_request_list=request.pull_request_list,
                )
                if not state.cleanup_worktree_removal_ready:
                    if request.authority.issue_status != "Canceled":
                        if not task_root.is_dir():
                            raise TaskCleanupError("Successful task worktree disappeared before dirty-state proof")
                        dirty = GitCommand.run(
                            task_root,
                            (
                                "status",
                                "--porcelain=v1",
                                "-z",
                                "--ignore-submodules=none",
                            ),
                        ).stdout
                        if dirty:
                            raise TaskCleanupError("Successful task worktree contains uncommitted user work")
                    state = replace(state, cleanup_worktree_removal_ready=True)
                    repository.state_write(state)
                if not state.worktree_removed and (task_root.exists() or registered_branch is not None):
                    GitCommand.run(
                        repository.main_root,
                        ("worktree", "remove", "--force", str(task_root)),
                    )
                    removed_worktree_count += 1
                if not state.worktree_removed:
                    state = replace(state, worktree_removed=True)
                    repository.state_write(state)
                repository.fetch()
                remote_exists = repository.exist_remote_branch(state.branch_name)
                if state.remote_branch_removed and remote_exists:
                    raise TaskCleanupError("Removed remote task branch reappeared after durable cleanup")
                if not state.remote_branch_removed and remote_exists:
                    if not state.cleanup_remote_branch_commit:
                        raise TaskCleanupError("Remote task branch appeared after the durable cleanup snapshot")
                    repository.remote_branch_delete_exact(
                        state.branch_name,
                        expected_commit=state.cleanup_remote_branch_commit,
                    )
                    removed_remote_branch_count += 1
                if not state.remote_branch_removed:
                    state = replace(state, remote_branch_removed=True)
                    repository.state_write(state)
                local_exists = repository.exist_local_branch(state.branch_name)
                if state.local_branch_removed and local_exists:
                    raise TaskCleanupError("Removed local task branch reappeared after durable cleanup")
                if not state.local_branch_removed and local_exists:
                    if repository.commit_get(state.branch_name) != state.cleanup_local_branch_commit:
                        raise TaskCleanupError("Local task branch changed after the durable cleanup snapshot")
                    GitCommand.run(repository.main_root, ("branch", "-D", state.branch_name))
                    removed_local_branch_count += 1
                if not state.local_branch_removed:
                    state = replace(state, local_branch_removed=True)
                    repository.state_write(state)
                repository.fetch()
                if (
                    task_root.exists()
                    or repository.worktree_branch_get(task_root) is not None
                    or repository.exist_local_branch(state.branch_name)
                    or repository.exist_remote_branch(state.branch_name)
                ):
                    raise TaskCleanupError("Task workspace or branch remained after cleanup")
                repository.state_delete(request.issue_identifier)
            if request.authority.scope == "project-final":
                for repository in repository_by_origin_url_map.values():
                    for issue_identifier in request.project_issue_identifier_list:
                        if repository.state_read(issue_identifier) is not None:
                            raise TaskCleanupError(
                                f"Project issue {issue_identifier} retains private task-workspace state"
                            )
                        self._absence_require(repository, issue_identifier)
            return CleanupResult(
                closed_pull_request_count=closed_pull_request_count,
                cleaned_resource_count=cleaned_resource_count,
                removed_worktree_count=removed_worktree_count,
                removed_local_branch_count=removed_local_branch_count,
                removed_remote_branch_count=removed_remote_branch_count,
            )

    def _absence_require(self, repository: WorkspaceRepository, issue_identifier: str) -> None:
        """Treat complete absence as success but reject orphaned foreign-looking state.

        Args:
            repository: Exact participating repository.
            issue_identifier: Exact Linear issue identifier.
        """

        branch_name = f"linear/{issue_identifier.lower()}"
        repository.task_container_require(create=False)
        task_root = repository.main_root / ".worktree" / issue_identifier.lower()
        if (
            task_root.exists()
            or repository.exist_local_branch(branch_name)
            or repository.exist_remote_branch(branch_name)
        ):
            raise TaskCleanupError("Task resources exist without private ownership proof")

    def _cleanup_branch_snapshot_prepare(
        self,
        repository: WorkspaceRepository,
        state: RepositoryWorkspaceState,
        *,
        successful: bool,
        pull_request_list: list[PullRequestReference],
    ) -> RepositoryWorkspaceState:
        """Durably bind exact branch heads before destructive cleanup."""

        repository.fetch()
        if state.cleanup_branch_snapshot_ready:
            if repository.exist_local_branch(state.branch_name):
                if state.local_branch_removed:
                    raise TaskCleanupError("Removed local task branch reappeared after durable cleanup")
                if repository.commit_get(state.branch_name) != state.cleanup_local_branch_commit:
                    raise TaskCleanupError("Local task branch changed after the durable cleanup snapshot")
            if repository.exist_remote_branch(state.branch_name):
                if state.remote_branch_removed:
                    raise TaskCleanupError("Removed remote task branch reappeared after durable cleanup")
                if (
                    not state.cleanup_remote_branch_commit
                    or repository.commit_get(f"refs/remotes/origin/{state.branch_name}")
                    != state.cleanup_remote_branch_commit
                ):
                    raise TaskCleanupError("Remote task branch changed after the durable cleanup snapshot")
            return state
        if not repository.exist_local_branch(state.branch_name):
            raise TaskCleanupError("Local task branch disappeared before its durable cleanup snapshot")
        local_commit = repository.commit_get(state.branch_name)
        remote_commit = (
            repository.commit_get(f"refs/remotes/origin/{state.branch_name}")
            if repository.exist_remote_branch(state.branch_name)
            else ""
        )
        if successful:
            base_commit = repository.commit_get(f"refs/remotes/origin/{state.base_branch}")
            for branch_commit in {local_commit, remote_commit} - {""}:
                self._one_branch_commit_integration_require(
                    repository,
                    state,
                    branch_commit=branch_commit,
                    base_commit=base_commit,
                    pull_request_list=pull_request_list,
                )
        return replace(
            state,
            cleanup_branch_snapshot_ready=True,
            cleanup_local_branch_commit=local_commit,
            cleanup_remote_branch_commit=remote_commit,
        )

    def _one_branch_commit_integration_require(
        self,
        repository: WorkspaceRepository,
        state: RepositoryWorkspaceState,
        *,
        branch_commit: str,
        base_commit: str,
        pull_request_list: list[PullRequestReference],
    ) -> None:
        """Require one local or remote task head to be integrated into its base."""

        if (
            GitCommand.run(
                repository.main_root,
                ("merge-base", "--is-ancestor", branch_commit, base_commit),
                check=False,
            ).returncode
            == 0
        ):
            return
        github_repository = _github_repository_identity_get(repository.origin_identity)
        if github_repository is None:
            raise TaskCleanupError("Successful task branch contains commits absent from its remote base")
        matching_snapshot_list = []
        for reference in pull_request_list:
            if reference.repository.value != github_repository:
                continue
            snapshot = self._github.inspect(repository=reference.repository, number=reference.number)
            if (
                snapshot.state == "MERGED"
                and snapshot.base_branch == state.base_branch
                and snapshot.head_branch == state.branch_name
                and snapshot.head_commit == branch_commit
                and snapshot.merge_commit
            ):
                merge_commit = repository.commit_get(snapshot.merge_commit)
                if (
                    GitCommand.run(
                        repository.main_root,
                        ("merge-base", "--is-ancestor", merge_commit, base_commit),
                        check=False,
                    ).returncode
                    == 0
                ):
                    matching_snapshot_list.append(snapshot)
        if len(matching_snapshot_list) != 1:
            raise TaskCleanupError(
                "Successful task branch is absent from its remote base and lacks one exact integrated pull request"
            )

    def _pull_request_contract_require(
        self,
        request: CleanupRequest,
        repository_list: list[WorkspaceRepository],
    ) -> None:
        """Require the complete exact PR set for every participating GitHub repository."""

        repository_by_github_identity_map = {
            identity: repository
            for repository in repository_list
            if (identity := _github_repository_identity_get(repository.origin_identity)) is not None
        }
        reference_by_repository_identity_map = {item.repository.value: item for item in request.pull_request_list}
        for reference in request.pull_request_list:
            if reference.repository.value not in repository_by_github_identity_map:
                raise TaskCleanupError("Linked pull request is outside the participating GitHub repository set")
        for identity, repository in repository_by_github_identity_map.items():
            expected_number_list = self._github.matching_number_list(
                repository=RepositoryIdentity(identity),
                base_branch=repository.request.base_branch,
                head_branch=f"linear/{request.issue_identifier.lower()}",
            )
            if len(expected_number_list) > 1:
                raise TaskCleanupError("More than one pull request exists for the exact task branch and base")
            provided = reference_by_repository_identity_map.get(identity)
            provided_number_list = [] if provided is None else [provided.number]
            if expected_number_list != provided_number_list:
                raise TaskCleanupError("Cleanup request omits or substitutes the exact task pull request")
            if provided is not None:
                snapshot = self._github.inspect(repository=provided.repository, number=provided.number)
                snapshot.integration_identity_require(request.issue_identifier)
                snapshot.target_require(
                    base_branch=repository.request.base_branch,
                    head_branch=f"linear/{request.issue_identifier.lower()}",
                )

    def _pull_request_target_get(
        self,
        github_repository: str,
        *,
        request: CleanupRequest,
        repository_list: list[WorkspaceRepository],
    ) -> PullRequestTarget:
        """Return the exact approved target for one participating GitHub repository."""

        matching_repository_list = [
            repository
            for repository in repository_list
            if _github_repository_identity_get(repository.origin_identity) == github_repository
        ]
        if len(matching_repository_list) != 1:
            raise TaskCleanupError("Linked pull request has no unique participating repository target")
        return PullRequestTarget(
            base_branch=matching_repository_list[0].request.base_branch,
            head_branch=f"linear/{request.issue_identifier.lower()}",
        )

    def _task_root_require(
        self,
        repository: WorkspaceRepository,
        state: RepositoryWorkspaceState,
    ) -> None:
        """Require one configured physical task root before owned code execution."""

        if repository.main_root != self._config.root and repository.main_root.parent != self._config.root:
            raise TaskCleanupError("Owned task worktree is outside the configured workspace")
        task_root = Path(state.task_root)
        if task_root.is_symlink() or not task_root.is_dir():
            raise TaskCleanupError("Owned task worktree is unavailable before project-owned cleanup execution")
        try:
            repository.task_worktree_require(state)
        except TaskWorkspaceError as error:
            raise TaskCleanupError(
                "Owned task worktree identity changed before project-owned cleanup execution"
            ) from error


def _github_repository_identity_get(origin_identity: str) -> str | None:
    """Return owner/name for one canonical GitHub origin, or absence for another host."""

    parsed = urlsplit(origin_identity)
    if parsed.hostname != "github.com":
        return None
    path_part_list = [item for item in parsed.path.split("/") if item]
    if len(path_part_list) != 2:
        raise TaskCleanupError("Participating GitHub origin does not identify exact owner/repository")
    return "/".join(path_part_list)
