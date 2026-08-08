"""Live-state retirement lifecycle for one issue-owned repository workspace."""

from __future__ import annotations

from dataclasses import dataclass

from git_host.model import RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary
from task_cleanup.model import CleanupRequest, TaskCleanupError
from task_workspace.model import RepositoryWorkspaceState, TaskWorkspaceError
from task_workspace.repository import WorkspaceRepository, git_command_run


@dataclass(frozen=True, slots=True)
class WorkspaceRetirementResult:
    """Report physical resources removed for one repository workspace."""

    removed_worktree_count: int
    removed_local_branch_count: int
    removed_remote_branch_count: int


class TaskWorkspaceRetirement:
    """Retire one repository workspace from current guarded Git and GitHub state."""

    def __init__(
        self,
        *,
        github: GitHubPullRequestBoundary,
        repository: WorkspaceRepository,
        request: CleanupRequest,
        state: RepositoryWorkspaceState,
    ) -> None:
        """Bind exact cleanup authority, repository and first-attempt baseline."""

        self._branch_name = f"linear/{request.issue_identifier.lower()}"
        self._github = github
        self._repository = repository
        self._request = request
        self._state = state
        self._task_root = repository.main_root / ".worktree" / request.issue_identifier.lower()

    def reconcile(self) -> WorkspaceRetirementResult:
        """Retire current worktree and branches, then remove private ownership state."""

        self.removal_require()
        registered_branch = self._repository.worktree_branch_get(self._task_root)
        removed_worktree_count = 0
        if self._task_root.exists() or registered_branch is not None:
            git_command_run(self._repository.main_root, ("worktree", "remove", "--force", str(self._task_root)))
            removed_worktree_count = 1

        self._repository.fetch()
        removed_remote_branch_count = 0
        if self._repository.exist_remote_branch(self._branch_name):
            remote_commit = self._repository.commit_get(f"refs/remotes/origin/{self._branch_name}")
            self._branch_removal_require(remote_commit)
            self._repository.remote_branch_delete_exact(self._branch_name, expected_commit=remote_commit)
            removed_remote_branch_count = 1

        removed_local_branch_count = 0
        if self._repository.exist_local_branch(self._branch_name):
            local_commit = self._repository.commit_get(self._branch_name)
            self._branch_removal_require(local_commit)
            git_command_run(self._repository.main_root, ("branch", "-D", self._branch_name))
            removed_local_branch_count = 1

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

    def removal_require(self) -> None:
        """Require every currently present task resource to remain exact and removable."""

        self._repository.fetch()
        registered_branch = self._repository.worktree_branch_get(self._task_root)
        if self._task_root.exists() or registered_branch is not None:
            if registered_branch != self._branch_name:
                raise TaskCleanupError("Task worktree registration differs from its issue branch")
            try:
                self._repository.task_worktree_require(self._request.issue_identifier, self._state)
            except TaskWorkspaceError as error:
                raise TaskCleanupError("Owned task worktree identity changed before cleanup") from error
            if self._request.authority.issue_status != "Canceled":
                dirty = git_command_run(
                    self._task_root,
                    ("status", "--porcelain=v1", "-z", "--ignore-submodules=none"),
                ).stdout
                if dirty:
                    raise TaskCleanupError("Successful task worktree contains uncommitted user work")
        if self._repository.exist_local_branch(self._branch_name):
            self._branch_removal_require(self._repository.commit_get(self._branch_name))
        if self._repository.exist_remote_branch(self._branch_name):
            self._branch_removal_require(self._repository.commit_get(f"refs/remotes/origin/{self._branch_name}"))

    def _branch_commit_integration_require(self, *, branch_commit: str, base_commit: str) -> None:
        """Require one successful task head to be integrated into its base."""

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
                and snapshot.base_branch == self._repository.request.base_branch
                and snapshot.head_branch == self._branch_name
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

    def _branch_removal_require(self, branch_commit: str) -> None:
        """Require one current branch commit to remain owned and safe to delete."""

        if (
            git_command_run(
                self._repository.main_root,
                ("merge-base", "--is-ancestor", self._state.baseline_commit, branch_commit),
                check=False,
            ).returncode
            != 0
        ):
            raise TaskCleanupError("Task branch is not descended from its first-attempt baseline")
        if self._request.authority.issue_status == "Done":
            base_commit = self._repository.commit_get(f"refs/remotes/origin/{self._repository.request.base_branch}")
            self._branch_commit_integration_require(branch_commit=branch_commit, base_commit=base_commit)
