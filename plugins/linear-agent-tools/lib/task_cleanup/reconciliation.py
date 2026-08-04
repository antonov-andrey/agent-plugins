"""Idempotent cleanup sequencing for exact task-owned state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from git_host.model import RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary
from task_cleanup.model import CleanupRequest, PullRequestTarget, TaskCleanupError
from task_cleanup.resource import ResourceCleaner
from task_cleanup.workspace import TaskWorkspaceRetirement
from task_workspace.lock import IssueWorkspaceLock
from task_workspace.model import RepositoryWorkspaceState, TaskWorkspaceError, WorkspaceConfig
from task_workspace.repository import WorkspaceRepository


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Summarize exact cleanup reconciliation without duplicating Linear history."""

    closed_pull_request_count: int
    cleaned_resource_count: int
    removed_worktree_count: int
    removed_local_branch_count: int
    removed_remote_branch_count: int

    def payload(self) -> dict[str, int]:
        """Return one JSON-ready result."""

        return {
            "schema_version": 1,
            "cleaned_resource_count": self.cleaned_resource_count,
            "closed_pull_request_count": self.closed_pull_request_count,
            "removed_local_branch_count": self.removed_local_branch_count,
            "removed_remote_branch_count": self.removed_remote_branch_count,
            "removed_worktree_count": self.removed_worktree_count,
        }


@dataclass(slots=True)
class CleanupState:
    """Carry mutable counters and exact repository snapshots through one cleanup run."""

    request: CleanupRequest
    repository_by_origin_url_map: dict[str, WorkspaceRepository] = field(default_factory=dict)
    workspace_state_by_origin_url_map: dict[str, RepositoryWorkspaceState | None] = field(default_factory=dict)
    closed_pull_request_count: int = 0
    cleaned_resource_count: int = 0
    removed_worktree_count: int = 0
    removed_local_branch_count: int = 0
    removed_remote_branch_count: int = 0

    def pull_request_target_get(self, github_repository: RepositoryIdentity) -> PullRequestTarget:
        """Return the exact approved target for one participating GitHub repository."""

        matching_repository_list = [
            repository
            for repository in self.repository_by_origin_url_map.values()
            if RepositoryIdentity.from_origin_identity(repository.origin_identity) == github_repository
        ]
        if len(matching_repository_list) != 1:
            raise TaskCleanupError("Linked pull request has no unique participating repository target")
        return PullRequestTarget(
            base_branch=matching_repository_list[0].request.base_branch,
            head_branch=f"linear/{self.request.issue_identifier.lower()}",
        )

    def resource_scope_require(self) -> None:
        """Require every cleanup resource to name one participating repository."""

        for resource in self.request.resource_list:
            if resource.repository_url not in self.repository_by_origin_url_map:
                raise TaskCleanupError(f"Resource {resource.key} has no exact participating repository")

    def task_absence_require(self, repository: WorkspaceRepository, issue_identifier: str) -> None:
        """Translate repository-level absence proof into the cleanup domain."""

        if not any(item is repository for item in self.repository_by_origin_url_map.values()):
            raise TaskCleanupError("Task absence proof names a non-participating repository")
        try:
            repository.task_absence_require(issue_identifier)
        except TaskWorkspaceError as error:
            raise TaskCleanupError("Task resources exist without private ownership proof") from error


class TaskCleanupReconciler:
    """Sequence exact PR, resource and workspace cleanup owners."""

    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        github: GitHubPullRequestBoundary,
        resources: ResourceCleaner,
    ) -> None:
        """Initialize explicit external boundaries."""

        self._config = config
        self._github = github
        self._resources = resources

    def cleanup(self, request: CleanupRequest) -> CleanupResult:
        """Reconcile every exact requested cleanup target idempotently."""

        state = CleanupState(request=request)
        with IssueWorkspaceLock(self._config, request.issue_identifier):
            self._repository_state_load(state)
            state.resource_scope_require()
            self._terminal_contract_prepare(state)
            self._pull_request_reconcile(state)
            self._resource_reconcile(state)
            if request.authority.scope != "attempt":
                self._workspace_reconcile(state)
                self._project_absence_require(state)
        return CleanupResult(
            closed_pull_request_count=state.closed_pull_request_count,
            cleaned_resource_count=state.cleaned_resource_count,
            removed_worktree_count=state.removed_worktree_count,
            removed_local_branch_count=state.removed_local_branch_count,
            removed_remote_branch_count=state.removed_remote_branch_count,
        )

    def _project_absence_require(self, state: CleanupState) -> None:
        """Prove every Project issue workspace absent at the final cleanup gate."""

        if state.request.authority.scope != "project-final":
            return
        for repository in state.repository_by_origin_url_map.values():
            for issue_identifier in state.request.project_issue_identifier_list:
                if repository.state_read(issue_identifier) is not None:
                    raise TaskCleanupError(f"Project issue {issue_identifier} retains private task-workspace state")
                state.task_absence_require(repository, issue_identifier)

    def _pull_request_reconcile(self, state: CleanupState) -> None:
        """Close canceled PRs or prove every successful task PR merged."""

        canceled = (
            state.request.authority.issue_status == "Canceled" or state.request.authority.project_status == "Canceled"
        )
        for reference in state.request.pull_request_list:
            before = self._github.inspect(repository=reference.repository, number=reference.number)
            target = state.pull_request_target_get(reference.repository)
            before.integration_identity_require(state.request.issue_identifier)
            before.target_require(base_branch=target.base_branch, head_branch=target.head_branch)
            if canceled:
                self._github.close_if_open(
                    repository=reference.repository,
                    number=reference.number,
                    issue_identifier=state.request.issue_identifier,
                    base_branch=target.base_branch,
                    head_branch=target.head_branch,
                )
                if before.state == "OPEN":
                    state.closed_pull_request_count += 1
            elif before.state != "MERGED":
                raise TaskCleanupError("Successful task cleanup requires every linked pull request to be merged")

    def _repository_state_load(self, state: CleanupState) -> None:
        """Load and validate exact participating repositories under the issue lock."""

        state.repository_by_origin_url_map = {
            item.origin_url: WorkspaceRepository.from_config(self._config, item)
            for item in state.request.repository_list
        }
        for repository in state.repository_by_origin_url_map.values():
            repository.fetch()
        state.workspace_state_by_origin_url_map = {
            origin_url: repository.state_read(state.request.issue_identifier)
            for origin_url, repository in state.repository_by_origin_url_map.items()
        }
        for origin_url, workspace_state in state.workspace_state_by_origin_url_map.items():
            repository = state.repository_by_origin_url_map[origin_url]
            if workspace_state is not None:
                repository.state_identity_require(state.request.issue_identifier, workspace_state)
            else:
                state.task_absence_require(repository, state.request.issue_identifier)

    def _resource_reconcile(self, run_state: CleanupState) -> None:
        """Execute each declared resource cleanup and durably record completion."""

        for resource in run_state.request.resource_list:
            repository = run_state.repository_by_origin_url_map[resource.repository_url]
            state = run_state.workspace_state_by_origin_url_map[resource.repository_url]
            cleaned_resource_fingerprint_by_resource_key_map: dict[str, str] = {}
            if run_state.request.authority.scope != "attempt" and state is not None:
                cleaned_resource_fingerprint_by_resource_key_map = dict(
                    state.cleaned_resource_fingerprint_by_resource_key_map
                )
                if resource.key in cleaned_resource_fingerprint_by_resource_key_map:
                    if cleaned_resource_fingerprint_by_resource_key_map[resource.key] != resource.fingerprint():
                        raise TaskCleanupError(
                            f"Cleanup declaration changed after exact resource {resource.key} was reconciled"
                        )
                    continue
            if state is None:
                working_directory = repository.main_root
                placeholder_by_name_map = {
                    "linear_issue_identifier": run_state.request.issue_identifier,
                    "main_root": str(repository.main_root),
                    "task_branch": f"linear/{run_state.request.issue_identifier.lower()}",
                    "task_root": str(repository.main_root / ".worktree" / run_state.request.issue_identifier.lower()),
                }
            else:
                retirement = self._workspace_retirement_get(run_state, repository, state)
                working_directory = retirement.task_root_require()
                placeholder_by_name_map = state.cleanup_placeholder_map(run_state.request.issue_identifier)
            self._resources.cleanup(
                resource,
                working_directory=working_directory,
                placeholder_by_name_map=placeholder_by_name_map,
            )
            if state is not None and run_state.request.authority.scope != "attempt":
                cleaned_resource_fingerprint_by_resource_key_map[resource.key] = resource.fingerprint()
                state = replace(
                    state,
                    cleaned_resource_fingerprint_by_resource_key_map=dict(
                        sorted(cleaned_resource_fingerprint_by_resource_key_map.items())
                    ),
                )
                repository.state_write(state)
                run_state.workspace_state_by_origin_url_map[resource.repository_url] = state
            run_state.cleaned_resource_count += 1

    def _terminal_contract_prepare(self, state: CleanupState) -> None:
        """Bind PR and branch identities before terminal destructive cleanup."""

        if state.request.authority.scope == "attempt":
            return
        self._pull_request_contract_require(state)
        for origin_url, workspace_state in state.workspace_state_by_origin_url_map.items():
            if workspace_state is None:
                continue
            repository = state.repository_by_origin_url_map[origin_url]
            retirement = self._workspace_retirement_get(state, repository, workspace_state)
            state.workspace_state_by_origin_url_map[origin_url] = retirement.branch_snapshot_prepare()

    def _workspace_reconcile(self, run_state: CleanupState) -> None:
        """Retire every terminal task repository through its durable owner."""

        for origin_url, repository in run_state.repository_by_origin_url_map.items():
            state = run_state.workspace_state_by_origin_url_map[origin_url]
            if state is None:
                run_state.task_absence_require(repository, run_state.request.issue_identifier)
                continue
            result = self._workspace_retirement_get(run_state, repository, state).reconcile()
            run_state.removed_worktree_count += result.removed_worktree_count
            run_state.removed_local_branch_count += result.removed_local_branch_count
            run_state.removed_remote_branch_count += result.removed_remote_branch_count

    def _workspace_retirement_get(
        self,
        state: CleanupState,
        repository: WorkspaceRepository,
        workspace_state: RepositoryWorkspaceState,
    ) -> TaskWorkspaceRetirement:
        """Wire one repository retirement owner to the current cleanup run."""

        return TaskWorkspaceRetirement(
            self._config,
            github=self._github,
            repository=repository,
            request=state.request,
            state=workspace_state,
        )

    def _pull_request_contract_require(self, state: CleanupState) -> None:
        """Require the complete exact PR set for participating GitHub repositories."""

        repository_by_github_identity_map = {
            identity: repository
            for repository in state.repository_by_origin_url_map.values()
            if (identity := RepositoryIdentity.from_origin_identity(repository.origin_identity)) is not None
        }
        reference_by_repository_identity_map = {item.repository: item for item in state.request.pull_request_list}
        for reference in state.request.pull_request_list:
            if reference.repository not in repository_by_github_identity_map:
                raise TaskCleanupError("Linked pull request is outside the participating GitHub repository set")
        for identity, repository in repository_by_github_identity_map.items():
            expected_number_list = self._github.matching_number_list(
                repository=identity,
                base_branch=repository.request.base_branch,
                head_branch=f"linear/{state.request.issue_identifier.lower()}",
            )
            if len(expected_number_list) > 1:
                raise TaskCleanupError("More than one pull request exists for the exact task branch and base")
            provided = reference_by_repository_identity_map.get(identity)
            provided_number_list = [] if provided is None else [provided.number]
            if expected_number_list != provided_number_list:
                raise TaskCleanupError("Cleanup request omits or substitutes the exact task pull request")
            if provided is not None:
                snapshot = self._github.inspect(repository=provided.repository, number=provided.number)
                snapshot.integration_identity_require(state.request.issue_identifier)
                snapshot.target_require(
                    base_branch=repository.request.base_branch,
                    head_branch=f"linear/{state.request.issue_identifier.lower()}",
                )
