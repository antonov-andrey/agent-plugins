"""Durable retirement lifecycle for one issue-owned repository workspace."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from git_host.model import RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary
from task_cleanup.model import CleanupRequest, TaskCleanupError
from task_cleanup.resource import cleanup_binding_run
from task_workspace.model import RepositoryWorkspaceState, TaskWorkspaceError, WorkspaceConfig
from task_workspace.repository import WorkspaceRepository, git_command_run


@dataclass(frozen=True, slots=True)
class WorkspaceRetirementResult:
    """Report physical resources removed for one repository workspace."""

    removed_worktree_count: int
    removed_local_branch_count: int
    removed_remote_branch_count: int


class TaskWorkspaceRetirement:
    """Own one crash-recoverable repository-workspace retirement transaction."""

    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        github: GitHubPullRequestBoundary,
        repository: WorkspaceRepository,
        request: CleanupRequest,
        state: RepositoryWorkspaceState,
    ) -> None:
        """Bind exact cleanup authority, repository and durable state."""

        self._config = config
        self._github = github
        self._repository = repository
        self._request = request
        self._state = state

    def branch_snapshot_prepare(self) -> RepositoryWorkspaceState:
        """Durably bind current local and remote branch heads before deletion."""

        prepared_state = self._branch_snapshot_get()
        if prepared_state != self._state:
            self._repository.state_write(prepared_state)
            self._state = prepared_state
        return self._state

    def reconcile(self) -> WorkspaceRetirementResult:
        """Retire the bound worktree, branches and private state idempotently."""

        self._cleanup_binding_reconcile()
        self.branch_snapshot_prepare()
        removed_worktree_count = self._worktree_removal_reconcile()
        removed_remote_branch_count = self._remote_branch_removal_reconcile()
        removed_local_branch_count = self._local_branch_removal_reconcile()
        self._repository.fetch()
        try:
            self._repository.task_absence_require(self._request.issue_identifier)
        except TaskWorkspaceError as error:
            raise TaskCleanupError("Task workspace or branch remained after cleanup") from error
        self._repository.state_delete(self._request.issue_identifier)
        return WorkspaceRetirementResult(
            removed_worktree_count=removed_worktree_count,
            removed_local_branch_count=removed_local_branch_count,
            removed_remote_branch_count=removed_remote_branch_count,
        )

    def task_root_require(self) -> Path:
        """Return the exact physical task root after proving its ownership."""

        if self._repository.main_root != self._config.root and self._repository.main_root.parent != self._config.root:
            raise TaskCleanupError("Owned task worktree is outside the configured workspace")
        task_root = Path(self._state.task_root)
        if task_root.is_symlink() or not task_root.is_dir():
            raise TaskCleanupError("Owned task worktree is unavailable before project-owned cleanup execution")
        try:
            self._repository.task_worktree_require(self._state)
        except TaskWorkspaceError as error:
            raise TaskCleanupError(
                "Owned task worktree identity changed before project-owned cleanup execution"
            ) from error
        return task_root

    def _cleanup_binding_reconcile(self) -> None:
        """Execute and durably record one project-local cleanup binding."""

        if self._state.cleanup_binding_completed:
            return
        task_root = self.task_root_require()
        cleanup_binding_run(
            self._state.cleanup_argument_list,
            working_directory=task_root,
            placeholder_by_name_map=self._state.cleanup_placeholder_map(self._request.issue_identifier),
        )
        self._state = replace(self._state, cleanup_binding_completed=True)
        self._repository.state_write(self._state)

    def _branch_snapshot_get(self) -> RepositoryWorkspaceState:
        """Return a validated or newly bound durable branch snapshot."""

        self._repository.fetch()
        if self._state.cleanup_branch_snapshot_ready:
            if self._repository.exist_local_branch(self._state.branch_name):
                if self._state.local_branch_removed:
                    raise TaskCleanupError("Removed local task branch reappeared after durable cleanup")
                if self._repository.commit_get(self._state.branch_name) != self._state.cleanup_local_branch_commit:
                    raise TaskCleanupError("Local task branch changed after the durable cleanup snapshot")
            if self._repository.exist_remote_branch(self._state.branch_name):
                if self._state.remote_branch_removed:
                    raise TaskCleanupError("Removed remote task branch reappeared after durable cleanup")
                if (
                    not self._state.cleanup_remote_branch_commit
                    or self._repository.commit_get(f"refs/remotes/origin/{self._state.branch_name}")
                    != self._state.cleanup_remote_branch_commit
                ):
                    raise TaskCleanupError("Remote task branch changed after the durable cleanup snapshot")
            return self._state
        if not self._repository.exist_local_branch(self._state.branch_name):
            raise TaskCleanupError("Local task branch disappeared before its durable cleanup snapshot")
        local_commit = self._repository.commit_get(self._state.branch_name)
        remote_commit = (
            self._repository.commit_get(f"refs/remotes/origin/{self._state.branch_name}")
            if self._repository.exist_remote_branch(self._state.branch_name)
            else ""
        )
        if self._request.authority.issue_status == "Done":
            base_commit = self._repository.commit_get(f"refs/remotes/origin/{self._state.base_branch}")
            for branch_commit in {local_commit, remote_commit} - {""}:
                self._branch_commit_integration_require(branch_commit=branch_commit, base_commit=base_commit)
        return replace(
            self._state,
            cleanup_branch_snapshot_ready=True,
            cleanup_local_branch_commit=local_commit,
            cleanup_remote_branch_commit=remote_commit,
        )

    def _branch_commit_integration_require(self, *, branch_commit: str, base_commit: str) -> None:
        """Require one local or remote task head to be integrated into its base."""

        if (
            git_command_run(
                self._repository.main_root,
                ("merge-base", "--is-ancestor", branch_commit, base_commit),
                check=False,
            ).returncode
            == 0
        ):
            return
        github_repository = RepositoryIdentity.from_origin_identity(self._repository.origin_identity)
        if github_repository is None:
            raise TaskCleanupError("Successful task branch contains commits absent from its remote base")
        matching_snapshot_list = []
        for reference in self._request.pull_request_list:
            if reference.repository != github_repository:
                continue
            snapshot = self._github.inspect(repository=reference.repository, number=reference.number)
            if (
                snapshot.state == "MERGED"
                and snapshot.base_branch == self._state.base_branch
                and snapshot.head_branch == self._state.branch_name
                and snapshot.head_commit == branch_commit
                and snapshot.merge_commit
            ):
                merge_commit = self._repository.commit_get(snapshot.merge_commit)
                if (
                    git_command_run(
                        self._repository.main_root,
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

    def _worktree_removal_reconcile(self) -> int:
        """Prove clean state and remove one exact registered task worktree."""

        task_root = Path(self._state.task_root)
        registered_branch = self._repository.worktree_branch_get(task_root)
        if self._state.worktree_removed and (task_root.exists() or registered_branch is not None):
            raise TaskCleanupError("Removed task worktree reappeared after durable cleanup")
        if registered_branch is not None and registered_branch != self._state.branch_name:
            raise TaskCleanupError("Task worktree registration changed after durable cleanup snapshot")
        if not self._state.cleanup_worktree_removal_ready:
            if self._request.authority.issue_status != "Canceled":
                if not task_root.is_dir():
                    raise TaskCleanupError("Successful task worktree disappeared before dirty-state proof")
                dirty = git_command_run(
                    task_root,
                    ("status", "--porcelain=v1", "-z", "--ignore-submodules=none"),
                ).stdout
                if dirty:
                    raise TaskCleanupError("Successful task worktree contains uncommitted user work")
            self._state = replace(self._state, cleanup_worktree_removal_ready=True)
            self._repository.state_write(self._state)
        removed_count = 0
        if not self._state.worktree_removed and (task_root.exists() or registered_branch is not None):
            git_command_run(self._repository.main_root, ("worktree", "remove", "--force", str(task_root)))
            removed_count = 1
        if not self._state.worktree_removed:
            self._state = replace(self._state, worktree_removed=True)
            self._repository.state_write(self._state)
        return removed_count

    def _remote_branch_removal_reconcile(self) -> int:
        """Remove one exact remote task branch after its durable snapshot."""

        self._repository.fetch()
        remote_exists = self._repository.exist_remote_branch(self._state.branch_name)
        if self._state.remote_branch_removed and remote_exists:
            raise TaskCleanupError("Removed remote task branch reappeared after durable cleanup")
        removed_count = 0
        if not self._state.remote_branch_removed and remote_exists:
            if not self._state.cleanup_remote_branch_commit:
                raise TaskCleanupError("Remote task branch appeared after the durable cleanup snapshot")
            self._repository.remote_branch_delete_exact(
                self._state.branch_name,
                expected_commit=self._state.cleanup_remote_branch_commit,
            )
            removed_count = 1
        if not self._state.remote_branch_removed:
            self._state = replace(self._state, remote_branch_removed=True)
            self._repository.state_write(self._state)
        return removed_count

    def _local_branch_removal_reconcile(self) -> int:
        """Remove one exact local task branch after its durable snapshot."""

        local_exists = self._repository.exist_local_branch(self._state.branch_name)
        if self._state.local_branch_removed and local_exists:
            raise TaskCleanupError("Removed local task branch reappeared after durable cleanup")
        removed_count = 0
        if not self._state.local_branch_removed and local_exists:
            if self._repository.commit_get(self._state.branch_name) != self._state.cleanup_local_branch_commit:
                raise TaskCleanupError("Local task branch changed after the durable cleanup snapshot")
            git_command_run(self._repository.main_root, ("branch", "-D", self._state.branch_name))
            removed_count = 1
        if not self._state.local_branch_removed:
            self._state = replace(self._state, local_branch_removed=True)
            self._repository.state_write(self._state)
        return removed_count
