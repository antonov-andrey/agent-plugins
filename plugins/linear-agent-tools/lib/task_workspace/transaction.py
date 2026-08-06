"""Crash-recoverable cross-repository task-workspace sequencing."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from task_workspace.bootstrap import BootstrapResource
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
            Current bootstrap-ready states in request order.
        """

        with IssueWorkspaceLock(self._config, request.issue_identifier):
            state_list = [self._repository_prepare(request, repository) for repository in request.repository_list]
            return state_list

    def validate(self, request: WorkspaceRequest) -> list[RepositoryWorkspaceState]:
        """Prove existing workspace ownership without creating or repairing state.

        Args:
            request: Complete participating repository request.

        Returns:
            Validated states in request order.
        """

        with IssueWorkspaceLock(self._config, request.issue_identifier):
            state_list: list[RepositoryWorkspaceState] = []
            for repository_request in request.repository_list:
                repository = WorkspaceRepository.from_config(self._config, repository_request)
                state = repository.state_read(request.issue_identifier)
                if state is None:
                    raise TaskWorkspaceError("Issue workspace has no private ownership state")
                state = repository.state_current_view_require(request.issue_identifier, state)
                if state.phase != "bootstrap-ready" or any(item.phase != "ready" for item in state.resource_list):
                    raise TaskWorkspaceError("Issue workspace transaction is incomplete")
                repository.task_worktree_require(state)
                WorkspaceSubmoduleReader(Path(state.task_root)).read()
                for resource_state in state.resource_list:
                    BootstrapResource.from_state(resource_state).ready_require(task_root=Path(state.task_root))
                state_list.append(state)
            return state_list

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
            Bootstrap-ready private state.
        """

        repository = WorkspaceRepository.from_config(self._config, repository_request)
        state = repository.state_read(request.issue_identifier)
        if state is None:
            state = TaskWorkspaceStatePlanner(repository, request).plan()
            repository.state_write(state)
        else:
            state = repository.state_migrate_and_require(request.issue_identifier, state)
        repository.task_worktree_create_or_accept(state)
        if state.phase == "planned":
            WorkspaceSubmoduleReader(Path(state.task_root)).prepare()
            state = replace(state, phase="worktree-ready")
            repository.state_write(state)
        else:
            WorkspaceSubmoduleReader(Path(state.task_root)).read()
        resource_by_relative_path_map = {
            item.relative_path: item
            for item in [BootstrapResource.from_state(resource_state) for resource_state in state.resource_list]
        }
        for index, resource_state in enumerate(state.resource_list):
            resource = resource_by_relative_path_map[resource_state.relative_path]
            if resource_state.phase == "ready":
                resource.ready_require(task_root=Path(state.task_root))
                continue
            resource.materialize(
                main_root=Path(state.main_root),
                task_root=Path(state.task_root),
            )
            ready_state = replace(resource_state, phase="ready")
            state = replace(
                state,
                resource_list=[
                    ready_state if item_index == index else item for item_index, item in enumerate(state.resource_list)
                ],
            )
            repository.state_write(state)
        if state.phase != "bootstrap-ready":
            state = replace(state, phase="bootstrap-ready")
            repository.state_write(state)
        return state
