"""Idempotent cleanup sequencing from current provider state."""

from __future__ import annotations

from dataclasses import dataclass, field

from git_host.model import PullRequestSnapshot, RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary
from task_cleanup.model import CleanupRequest, PullRequestTarget, TaskCleanupError
from task_cleanup.workspace import TaskWorkspaceRetirement
from task_workspace.lock import IssueWorkspaceLock
from task_workspace.model import RepositoryWorkspaceState, TaskWorkspaceError, WorkspaceConfig
from task_workspace.repository import WorkspaceRepository


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Summarize exact cleanup reconciliation without duplicating provider state."""

    closed_pull_request_count: int
    removed_worktree_count: int
    removed_local_branch_count: int
    removed_remote_branch_count: int

    def payload(self) -> dict[str, int]:
        """Return one JSON-ready result."""

        return {
            "schema_version": 1,
            "closed_pull_request_count": self.closed_pull_request_count,
            "removed_local_branch_count": self.removed_local_branch_count,
            "removed_remote_branch_count": self.removed_remote_branch_count,
            "removed_worktree_count": self.removed_worktree_count,
        }


@dataclass(slots=True)
class CleanupState:
    """Carry run-local counters and current repository reads through cleanup."""

    request: CleanupRequest
    repository_by_origin_identity_map: dict[str, WorkspaceRepository] = field(default_factory=dict)
    workspace_state_by_origin_identity_map: dict[str, RepositoryWorkspaceState | None] = field(default_factory=dict)
    closed_pull_request_count: int = 0
    removed_worktree_count: int = 0
    removed_local_branch_count: int = 0
    removed_remote_branch_count: int = 0

    def pull_request_target_get(self, github_repository: RepositoryIdentity) -> PullRequestTarget:
        """Return the exact approved target for one participating GitHub repository."""

        matching_repository_list = [
            repository
            for repository in self.repository_by_origin_identity_map.values()
            if RepositoryIdentity.from_origin_identity(repository.origin_identity) == github_repository
        ]
        if len(matching_repository_list) != 1:
            raise TaskCleanupError("Linked pull request has no unique participating repository target")
        return PullRequestTarget(
            base_branch=matching_repository_list[0].request.base_branch,
            head_branch=f"linear/{self.request.issue_identifier.lower()}",
        )

    def task_absence_require(self, repository: WorkspaceRepository, issue_identifier: str) -> None:
        """Translate repository-level absence proof into the cleanup domain."""

        if not any(item is repository for item in self.repository_by_origin_identity_map.values()):
            raise TaskCleanupError("Task absence proof names a non-participating repository")
        try:
            repository.task_absence_require(issue_identifier)
        except TaskWorkspaceError as error:
            raise TaskCleanupError("Task resources exist without private ownership proof") from error


class TaskCleanupReconciler:
    """Sequence exact PR and workspace cleanup from live provider identities."""

    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        github: GitHubPullRequestBoundary,
    ) -> None:
        """Initialize explicit external boundaries."""

        self._config = config
        self._github = github

    def cleanup(self, request: CleanupRequest) -> CleanupResult:
        """Reconcile every exact requested cleanup target idempotently."""

        state = CleanupState(request=request)
        with IssueWorkspaceLock(self._config, request.issue_identifier):
            self._repository_state_load(state)
            self._terminal_contract_require(state)
            self._pull_request_reconcile(state)
            if request.authority.scope != "attempt":
                self._workspace_reconcile(state)
                self._project_absence_require(state)
        return CleanupResult(
            closed_pull_request_count=state.closed_pull_request_count,
            removed_worktree_count=state.removed_worktree_count,
            removed_local_branch_count=state.removed_local_branch_count,
            removed_remote_branch_count=state.removed_remote_branch_count,
        )

    def _project_absence_require(self, state: CleanupState) -> None:
        """Prove every Project issue workspace absent at the final cleanup gate."""

        if state.request.authority.scope != "project-final":
            return
        for repository in state.repository_by_origin_identity_map.values():
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
            before.target_require(base_branch=target.base_branch, head_branch=target.head_branch)
            before.task_branch_identity_require(state.request.issue_identifier)
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

        repository_list = [
            WorkspaceRepository.from_config(self._config, item) for item in state.request.repository_list
        ]
        state.repository_by_origin_identity_map = {
            repository.origin_identity: repository for repository in repository_list
        }
        for repository in state.repository_by_origin_identity_map.values():
            repository.fetch()
        state.workspace_state_by_origin_identity_map = {
            origin_identity: repository.state_read(state.request.issue_identifier)
            for origin_identity, repository in state.repository_by_origin_identity_map.items()
        }
        for origin_identity, workspace_state in state.workspace_state_by_origin_identity_map.items():
            repository = state.repository_by_origin_identity_map[origin_identity]
            if workspace_state is None:
                state.task_absence_require(repository, state.request.issue_identifier)
            else:
                repository.state_identity_require(state.request.issue_identifier, workspace_state)

    def _terminal_contract_require(self, state: CleanupState) -> None:
        """Validate current PR, worktree and branch identities before destructive cleanup."""

        if state.request.authority.scope == "attempt":
            return
        self._pull_request_contract_require(state)
        for origin_identity, workspace_state in state.workspace_state_by_origin_identity_map.items():
            if workspace_state is None:
                continue
            repository = state.repository_by_origin_identity_map[origin_identity]
            self._workspace_retirement_get(state, repository, workspace_state).removal_require()

    def _workspace_reconcile(self, run_state: CleanupState) -> None:
        """Retire every terminal task repository through its live-state owner."""

        for origin_identity, repository in run_state.repository_by_origin_identity_map.items():
            state = run_state.workspace_state_by_origin_identity_map[origin_identity]
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
            github=self._github,
            repository=repository,
            request=state.request,
            state=workspace_state,
        )

    def _pull_request_contract_require(self, state: CleanupState) -> None:
        """Require the complete exact PR set for participating GitHub repositories."""

        repository_by_github_identity_map = {
            identity: repository
            for repository in state.repository_by_origin_identity_map.values()
            if (identity := RepositoryIdentity.from_origin_identity(repository.origin_identity)) is not None
        }
        reference_by_repository_identity_map = {item.repository: item for item in state.request.pull_request_list}
        for reference in state.request.pull_request_list:
            if reference.repository not in repository_by_github_identity_map:
                raise TaskCleanupError("Linked pull request is outside the participating GitHub repository set")
        canceled = (
            state.request.authority.issue_status == "Canceled" or state.request.authority.project_status == "Canceled"
        )
        for identity, repository in repository_by_github_identity_map.items():
            matching_number_list = self._github.matching_number_list(
                repository=identity,
                base_branch=repository.request.base_branch,
                head_branch=f"linear/{state.request.issue_identifier.lower()}",
            )
            active_snapshot_list: list[PullRequestSnapshot] = []
            for number in matching_number_list:
                snapshot = self._github.inspect(repository=identity, number=number)
                snapshot.target_require(
                    base_branch=repository.request.base_branch,
                    head_branch=f"linear/{state.request.issue_identifier.lower()}",
                )
                snapshot.task_branch_identity_require(state.request.issue_identifier)
                if snapshot.state != "CLOSED":
                    active_snapshot_list.append(snapshot)
            if len(active_snapshot_list) > 1:
                raise TaskCleanupError("More than one active pull request exists for the exact task branch and base")
            if active_snapshot_list:
                active_snapshot = active_snapshot_list[0]
                if active_snapshot.state == "OPEN":
                    active_snapshot.integration_identity_require(state.request.issue_identifier)
                expected_number_list = [active_snapshot.number]
            elif matching_number_list and canceled:
                expected_number_list = [max(matching_number_list)]
            elif matching_number_list:
                raise TaskCleanupError("Closed unmerged pull request is never successful merge evidence")
            else:
                expected_number_list = []
            provided = reference_by_repository_identity_map.get(identity)
            provided_number_list = [] if provided is None else [provided.number]
            if expected_number_list != provided_number_list:
                raise TaskCleanupError("Cleanup request omits or substitutes the exact task pull request")
