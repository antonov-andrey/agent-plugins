"""Crash-recoverable cross-repository task-workspace sequencing."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from task_workspace.bootstrap import (
    BootstrapResource,
    manifest_parse,
    resource_materialize,
    resource_ready_require,
)
from task_workspace.lock import IssueWorkspaceLock
from task_workspace.model import (
    BootstrapResourceState,
    RepositoryRequest,
    RepositoryWorkspaceState,
    TaskWorkspaceError,
    WorkspaceConfig,
    WorkspaceRequest,
)
from task_workspace.repository import GitCommand, WorkspaceRepository
from task_workspace.submodule import (
    recursive_submodule_prepare,
    recursive_submodule_snapshot_get,
)


class TaskWorkspaceTransaction:
    """Prepare or validate all worktrees owned by one exact Linear issue."""

    def __init__(self, config: WorkspaceConfig) -> None:
        """Bind one explicit local workspace root.

        Args:
            config: Validated workspace configuration.
        """

        self._config = config

    def prepare(self, request: WorkspaceRequest) -> tuple[RepositoryWorkspaceState, ...]:
        """Create or recover every exact issue-owned branch and worktree.

        Args:
            request: Complete participating repository request.

        Returns:
            Current bootstrap-ready states in request order.
        """

        with IssueWorkspaceLock(self._config, request.issue_identifier):
            result = tuple(self._repository_prepare(request, repository) for repository in request.repository_list)
            return result

    def validate(self, request: WorkspaceRequest) -> tuple[RepositoryWorkspaceState, ...]:
        """Prove existing workspace ownership without creating or repairing state.

        Args:
            request: Complete participating repository request.

        Returns:
            Validated states in request order.
        """

        with IssueWorkspaceLock(self._config, request.issue_identifier):
            result: list[RepositoryWorkspaceState] = []
            for repository_request in request.repository_list:
                repository = WorkspaceRepository.discover(self._config, repository_request)
                state = repository.state_read(request.issue_identifier)
                if state is None:
                    raise TaskWorkspaceError("Issue workspace has no private ownership state")
                repository.state_identity_require(request.issue_identifier, state)
                if state.phase != "bootstrap-ready" or any(item.phase != "ready" for item in state.resource_list):
                    raise TaskWorkspaceError("Issue workspace transaction is incomplete")
                repository.task_worktree_require(state)
                recursive_submodule_snapshot_get(Path(state.task_root))
                for resource in _resource_list_from_state(state.resource_list):
                    resource_ready_require(resource, task_root=Path(state.task_root))
                result.append(state)
            return tuple(result)

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

        repository = WorkspaceRepository.discover(self._config, repository_request)
        state = repository.state_read(request.issue_identifier)
        if state is None:
            state = self._state_initial_create(request, repository, repository_request)
            repository.state_write(state)
        else:
            repository.state_identity_require(request.issue_identifier, state)
        repository.task_worktree_create_or_accept(state)
        if state.phase == "planned":
            recursive_submodule_prepare(Path(state.task_root))
            state = replace(state, phase="worktree-ready")
            repository.state_write(state)
        else:
            recursive_submodule_snapshot_get(Path(state.task_root))
        resource_by_path = {item.relative_path: item for item in _resource_list_from_state(state.resource_list)}
        for index, resource_state in enumerate(state.resource_list):
            resource = resource_by_path[resource_state.relative_path]
            if resource_state.phase == "ready":
                resource_ready_require(resource, task_root=Path(state.task_root))
                continue
            resource_materialize(
                resource,
                main_root=Path(state.main_root),
                task_root=Path(state.task_root),
            )
            ready_state = replace(resource_state, phase="ready")
            state = replace(
                state,
                resource_list=tuple(
                    ready_state if item_index == index else item for item_index, item in enumerate(state.resource_list)
                ),
            )
            repository.state_write(state)
        if state.phase != "bootstrap-ready":
            state = replace(state, phase="bootstrap-ready")
            repository.state_write(state)
        return state

    def _state_initial_create(
        self,
        request: WorkspaceRequest,
        repository: WorkspaceRepository,
        repository_request: RepositoryRequest,
    ) -> RepositoryWorkspaceState:
        """Build and validate durable ownership state before Git mutation.

        Args:
            request: Owning issue request.
            repository: Exact discovered repository.
            repository_request: Approved repository target.

        Returns:
            Planned private state.
        """

        repository.fetch()
        repository.task_container_require(create=False)
        base_commit = repository.commit_get(f"refs/remotes/origin/{repository_request.base_branch}")
        baseline = repository_request.expected_baseline_commit or base_commit
        if repository_request.expected_baseline_commit:
            result = GitCommand.run(
                repository.main_root,
                ("merge-base", "--is-ancestor", baseline, base_commit),
                check=False,
            )
            if result.returncode != 0:
                raise TaskWorkspaceError("Expected task baseline is not reachable from the current remote base")
        branch_name = request.branch_name
        remote_branch_exists = repository.remote_branch_exists(branch_name)
        if remote_branch_exists and not repository_request.expected_baseline_commit:
            raise TaskWorkspaceError("Adopting an existing remote task branch requires its recorded Linear baseline")
        if repository.local_branch_exists(branch_name):
            raise TaskWorkspaceError("Local task branch exists without private ownership state")
        if repository.tracked_file_bytes_get(baseline, "worktree-bootstrap.toml") is not None:
            raise TaskWorkspaceError("Repository baseline uses legacy worktree-bootstrap.toml and requires adoption")
        manifest_bytes = repository.tracked_file_bytes_get(baseline, "worktree-bootstrap.yaml")
        if manifest_bytes is None:
            raise TaskWorkspaceError("Repository baseline omits required worktree-bootstrap.yaml")
        plan = manifest_parse(manifest_bytes, main_root=repository.main_root)
        task_root = repository.main_root / ".worktree" / request.basename
        if task_root.exists() or task_root.is_symlink():
            raise TaskWorkspaceError("Task worktree path exists without private ownership state")
        return RepositoryWorkspaceState(
            issue_identifier=request.issue_identifier,
            origin_identity=repository.origin_identity,
            base_branch=repository_request.base_branch,
            baseline_commit=baseline,
            branch_name=branch_name,
            main_root=str(repository.main_root),
            task_root=str(task_root),
            manifest_sha256=plan.manifest_sha256,
            phase="planned",
            resource_list=tuple(item.planned_state() for item in plan.resource_list),
            cleanup_argument_list=plan.cleanup_argument_list,
            cleaned_resource_fingerprint_by_key=(),
            cleanup_binding_completed=not plan.cleanup_argument_list,
            cleanup_branch_snapshot_ready=False,
            cleanup_local_branch_commit="",
            cleanup_remote_branch_commit="",
            cleanup_worktree_removal_ready=False,
            worktree_removed=False,
            remote_branch_removed=False,
            local_branch_removed=False,
        )


def _resource_list_from_state(
    state_list: tuple[BootstrapResourceState, ...],
) -> tuple[BootstrapResource, ...]:
    """Reconstruct exact materialization contracts from durable state.

    Args:
        state_list: Persisted resource state.

    Returns:
        Exact resource contracts.
    """

    return tuple(
        BootstrapResource(
            relative_path=item.relative_path,
            kind=item.kind,
            required=not item.skipped,
            source_identity=item.source_identity,
            skipped=item.skipped,
        )
        for item in state_list
    )
