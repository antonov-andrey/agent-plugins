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
                for resource in self._bootstrap_plan_get(repository, state).resource_list:
                    resource.ready_require(main_root=repository.main_root, task_root=task_root)
                state_list.append(state)
            return state_list

    @staticmethod
    def _bootstrap_manifest_bytes_get(
        repository: WorkspaceRepository,
        state: RepositoryWorkspaceState,
    ) -> bytes:
        """Read the exact manifest bytes from one immutable task baseline."""

        manifest_bytes = repository.tracked_file_bytes_get(state.baseline_commit, "worktree-bootstrap.yaml")
        if manifest_bytes is None:
            raise TaskWorkspaceError("Repository baseline omits required worktree-bootstrap.yaml")
        return manifest_bytes

    def _bootstrap_plan_get(
        self,
        repository: WorkspaceRepository,
        state: RepositoryWorkspaceState,
    ) -> BootstrapPlan:
        """Read the sole current-schema plan from the immutable attempt baseline.

        Args:
            repository: Bound canonical checkout.
            state: Candidate or retained first-attempt baseline.

        Returns:
            Validated current manifest plan. Unsupported historical manifests fail.
        """

        manifest_bytes = self._bootstrap_manifest_bytes_get(repository, state)
        return BootstrapPlan.from_manifest(manifest_bytes, main_root=repository.main_root)

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
            bootstrap_plan = self._bootstrap_plan_get(repository, state)
            repository.state_write(request.issue_identifier, state)
        else:
            repository.state_identity_require(request.issue_identifier, state)
            bootstrap_plan = self._bootstrap_plan_get(repository, state)
        task_root = repository.task_worktree_create_or_accept(request.issue_identifier, state)
        WorkspaceSubmoduleReader(task_root).prepare()
        temporary_root = repository.bootstrap_temporary_root_get(request.issue_identifier, create=True)
        if temporary_root is None:
            raise TaskWorkspaceError("Workspace bootstrap temporary root was not created")
        try:
            for resource in bootstrap_plan.resource_list:
                resource.materialize(
                    main_root=repository.main_root,
                    task_root=task_root,
                    temporary_root=temporary_root,
                )
        finally:
            repository.bootstrap_temporary_root_cleanup(request.issue_identifier)
        return state
