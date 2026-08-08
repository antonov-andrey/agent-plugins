"""Pre-mutation planning for one issue-owned repository workspace."""

from __future__ import annotations

from task_workspace.model import RepositoryWorkspaceState, TaskWorkspaceError, WorkspaceRequest
from task_workspace.repository import WorkspaceRepository, git_command_run


class TaskWorkspaceStatePlanner:
    """Derive one durable initial workspace state from an exact Git baseline."""

    def __init__(self, repository: WorkspaceRepository, request: WorkspaceRequest) -> None:
        """Bind the exact repository and issue request before planning."""

        self._repository = repository
        self._request = request

    def plan(self) -> RepositoryWorkspaceState:
        """Validate the baseline and return pre-mutation durable state."""

        self._repository.fetch()
        self._repository.task_container_require(create=False)
        repository_request = self._repository.request
        base_commit = self._repository.commit_get(f"refs/remotes/origin/{repository_request.base_branch}")
        baseline = repository_request.expected_baseline_commit or base_commit
        if repository_request.expected_baseline_commit:
            result = git_command_run(
                self._repository.main_root,
                ("merge-base", "--is-ancestor", baseline, base_commit),
                check=False,
            )
            if result.returncode != 0:
                raise TaskWorkspaceError("Expected task baseline is not reachable from the current remote base")
        branch_name = self._request.branch_name
        if self._repository.exist_remote_branch(branch_name) and not repository_request.expected_baseline_commit:
            raise TaskWorkspaceError("Adopting an existing remote task branch requires its recorded Linear baseline")
        if self._repository.exist_local_branch(branch_name):
            raise TaskWorkspaceError("Local task branch exists without private ownership state")
        if self._repository.tracked_file_bytes_get(baseline, "worktree-bootstrap.toml") is not None:
            raise TaskWorkspaceError("Repository baseline uses legacy worktree-bootstrap.toml and requires adoption")
        manifest_bytes = self._repository.tracked_file_bytes_get(baseline, "worktree-bootstrap.yaml")
        if manifest_bytes is None:
            raise TaskWorkspaceError("Repository baseline omits required worktree-bootstrap.yaml")
        task_root = self._repository.main_root / ".worktree" / self._request.basename
        if task_root.exists() or task_root.is_symlink():
            raise TaskWorkspaceError("Task worktree path exists without private ownership state")
        return RepositoryWorkspaceState(baseline_commit=baseline)
