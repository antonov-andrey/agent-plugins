"""Crash-recoverable cross-repository task-workspace sequencing."""

from __future__ import annotations

from pathlib import Path

from task_workspace.bootstrap import BootstrapPlan
from task_workspace.lock import IssueWorkspaceLock
from task_workspace.model import (
    RepositoryRequest,
    RepositoryWorkspaceState,
    TaskWorkspaceError,
    WorkspaceConfig,
    WorkspaceRequest,
)
from task_workspace.planning import TaskWorkspaceStatePlanner
from task_workspace.repository import WorkspaceRepository
from task_workspace.submodule import WorkspaceSubmoduleReader


class TaskWorkspaceTransaction:
    """Prepare or validate all worktrees owned by one exact Linear issue."""

    def __init__(self, config: WorkspaceConfig) -> None:
        """Bind one explicit local workspace root.

        Args:
            config: Validated workspace configuration.
        """

        self._config = config

    def prepare(self, request: WorkspaceRequest) -> list[RepositoryWorkspaceState]:
        """Create or recover every exact issue-owned branch and worktree.

        Args:
            request: Complete participating repository request.

        Returns:
            Current repository baseline states in request order.
        """

        with IssueWorkspaceLock(self._config, request.issue_identifier):
            return [self._repository_prepare(request, repository) for repository in request.repository_list]

    def validate(self, request: WorkspaceRequest) -> list[RepositoryWorkspaceState]:
        """Prove existing workspace ownership without creating or repairing state.

        Args:
            request: Complete participating repository request.

        Returns:
            Validated repository baseline states in request order.
        """

        with IssueWorkspaceLock(self._config, request.issue_identifier):
            state_list: list[RepositoryWorkspaceState] = []
            for repository_request in request.repository_list:
                repository = WorkspaceRepository.from_config(self._config, repository_request)
                repository.task_root_get(request.issue_identifier)
                state = repository.state_read(request.issue_identifier)
                if state is None:
                    raise TaskWorkspaceError("Issue workspace has no private ownership state")
                repository.state_identity_require(request.issue_identifier, state)
                task_root = repository.task_worktree_require(request.issue_identifier, state)
                WorkspaceSubmoduleReader(task_root).read()
                task_head = repository.commit_get(f"refs/heads/linear/{request.issue_identifier.lower()}")
                self._bootstrap_plan_get(repository, task_head).ready_require(
                    main_root=repository.main_root,
                    task_root=task_root,
                )
                state_list.append(state)
            return state_list

    @staticmethod
    def _bootstrap_manifest_bytes_get(
        repository: WorkspaceRepository,
        commit: str,
    ) -> bytes:
        """Read the exact manifest bytes from one selected task commit."""

        manifest_bytes = repository.tracked_file_bytes_get(commit, "worktree-bootstrap.yaml")
        if manifest_bytes is None:
            raise TaskWorkspaceError("Selected task commit omits required worktree-bootstrap.yaml")
        return manifest_bytes

    def _bootstrap_plan_get(
        self,
        repository: WorkspaceRepository,
        commit: str,
    ) -> BootstrapPlan:
        """Read the sole current-schema plan from one selected task commit.

        Args:
            repository: Bound canonical checkout.
            commit: New-attempt baseline or exact retained task head.

        Returns:
            Validated current manifest plan. Unsupported historical manifests fail.
        """

        manifest_bytes = self._bootstrap_manifest_bytes_get(repository, commit)
        return BootstrapPlan.from_manifest(manifest_bytes)

    def _repository_prepare(
        self,
        request: WorkspaceRequest,
        repository_request: RepositoryRequest,
    ) -> RepositoryWorkspaceState:
        """Prepare or recover one repository slice.

        Args:
            request: Owning issue request.
            repository_request: One repository target.

        Returns:
            Current repository baseline state.
        """

        repository = WorkspaceRepository.from_config(self._config, repository_request)
        repository.task_root_get(request.issue_identifier)
        repository.state_temporary_recover(request.issue_identifier)
        state = repository.state_read(request.issue_identifier)
        if state is None:
            state = TaskWorkspaceStatePlanner(repository, request).plan()
            self._bootstrap_plan_get(repository, state.baseline_commit)
            repository.state_write(request.issue_identifier, state)
        else:
            repository.state_identity_require(request.issue_identifier, state)
        task_root = repository.task_worktree_create_or_accept(request.issue_identifier, state)
        bootstrap_commit = repository.commit_get(f"refs/heads/linear/{request.issue_identifier.lower()}")
        bootstrap_plan = self._bootstrap_plan_get(repository, bootstrap_commit)
        WorkspaceSubmoduleReader(task_root).prepare()
        temporary_root = repository.bootstrap_temporary_root_get(request.issue_identifier, create=True)
        if temporary_root is None:
            raise TaskWorkspaceError("Workspace bootstrap temporary root was not created")
        try:
            bootstrap_plan.materialize(
                main_root=repository.main_root,
                task_root=task_root,
                temporary_root=temporary_root,
            )
        finally:
            repository.bootstrap_temporary_root_cleanup(request.issue_identifier)
        if repository.commit_get(f"refs/heads/linear/{request.issue_identifier.lower()}") != bootstrap_commit:
            raise TaskWorkspaceError("Task branch changed during bootstrap materialization")
        return state
