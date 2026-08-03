"""Recursive read-only and explicitly task-owned submodule preparation."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.task.boundary import RepositoryBoundaryManager
from goal_lifecycle.task.model import (
    RepositoryState,
    SubmoduleGitlinkState,
    TaskOwnedSubmoduleState,
    TaskState,
)
from goal_lifecycle.task.repair import TaskRepairReport
from goal_lifecycle.task.submodule_branch import TaskSubmoduleBranchManager
from goal_lifecycle.task.submodule_graph import TaskSubmoduleGraph
from goal_lifecycle.task.submodule_inventory import TaskSubmoduleInventoryRecoverer


class TaskSubmoduleManager:
    """Initialize every recursive boundary and delegate only explicitly owned paths."""

    def __init__(
        self,
        *,
        git: Git,
        boundary_manager: RepositoryBoundaryManager,
        repair_report: TaskRepairReport | None = None,
    ) -> None:
        """Initialize the task submodule manager dependencies.

        Args:
            git: Git command boundary.
            boundary_manager: Boundary manager.
            repair_report: Repair report.
        """

        self._git = git
        self._boundary_manager = boundary_manager
        self._repair_report = repair_report or TaskRepairReport()
        self._branch_manager = TaskSubmoduleBranchManager(git=git, repair_report=self._repair_report)
        self._graph = TaskSubmoduleGraph(git=git, repair_report=self._repair_report)
        self._inventory_recoverer = TaskSubmoduleInventoryRecoverer(
            git=git,
            boundary_manager=boundary_manager,
            branch_manager=self._branch_manager,
            graph=self._graph,
            repair_report=self._repair_report,
        )

    def prepare(
        self,
        *,
        main_root: Path,
        task_root: Path,
        common_prefix: str,
        requested_path_set: set[str],
        previous_state: RepositoryState | None,
    ) -> tuple[tuple[SubmoduleGitlinkState, ...], tuple[TaskOwnedSubmoduleState, ...]]:
        """Prepare recursive submodules and return the complete frozen graph and owned states.

        Args:
            main_root: Main root.
            task_root: Task root.
            common_prefix: Exact task common prefix.
            requested_path_set: Unique requested path values.
            previous_state: Previous state.

        Returns:
            Values in deterministic immutable order.
        """

        previous_owned_by_path_map = (
            {item.path: item for item in previous_state.task_owned_submodule_list} if previous_state else {}
        )
        if set(previous_owned_by_path_map) - requested_path_set:
            raise GoalLifecycleError("Prepare cannot remove a task-owned submodule from an existing task")
        self._graph.initialize(
            main_root,
            common_prefix=common_prefix,
            task_owned_path_set=set(),
            detach_read_only=False,
            repair_read_only=False,
        )
        self._graph.initialize(
            task_root,
            common_prefix=common_prefix,
            task_owned_path_set=requested_path_set,
            detach_read_only=True,
            repair_read_only=True,
            interrupted_state_exists=previous_state is not None,
        )
        task_gitlink_by_path_map = self._graph.recursive_current_get(task_root)
        main_gitlink_by_path_map = self._graph.recursive_current_get(main_root)
        if set(task_gitlink_by_path_map) != set(main_gitlink_by_path_map):
            raise GoalLifecycleError(f"Main and task recursive submodule sets differ during preparation: {task_root}")
        self._graph.owned_path_set_validate(requested_path_set, complete_path_set=set(task_gitlink_by_path_map))

        baseline_by_path_map = (
            {item.path: item.baseline_commit for item in previous_state.submodule_gitlink_list}
            if previous_state and previous_state.submodule_gitlink_list
            else dict(task_gitlink_by_path_map)
        )
        if set(baseline_by_path_map) != set(task_gitlink_by_path_map):
            raise GoalLifecycleError("Recursive submodule set changed after initial task preparation")
        owned_state_list: list[TaskOwnedSubmoduleState] = []
        for path_text in sorted(requested_path_set, key=lambda item: (len(PurePosixPath(item).parts), item)):
            main_submodule_root = main_root / path_text
            task_submodule_root = task_root / path_text
            previous = previous_owned_by_path_map.get(path_text)
            baseline_commit = baseline_by_path_map[path_text]
            self._branch_manager.prepare(
                main_root=main_submodule_root,
                task_root=task_submodule_root,
                path_text=path_text,
                common_prefix=common_prefix,
                baseline_commit=baseline_commit,
                previous=previous,
            )
            boundary = self._boundary_manager.prepare(
                main_root=main_submodule_root,
                task_root=task_submodule_root,
                baseline_commit=baseline_commit,
                common_prefix=common_prefix,
                previous_state=previous.repository if previous else None,
            )
            owned_state_list.append(TaskOwnedSubmoduleState(path=path_text, repository=boundary))
        return (
            tuple(
                SubmoduleGitlinkState(path=path, baseline_commit=baseline_by_path_map[path])
                for path in sorted(baseline_by_path_map)
            ),
            tuple(owned_state_list),
        )

    def preflight(self, main_root: Path, *, common_prefix: str, requested_path_set: set[str]) -> None:
        """Discover and classify the complete recursive main graph before worktree creation.

        Args:
            main_root: Main root.
            common_prefix: Exact task common prefix.
            requested_path_set: Unique requested path values.
        """

        self._graph.initialize(
            main_root,
            common_prefix=common_prefix,
            task_owned_path_set=set(),
            detach_read_only=False,
            repair_read_only=False,
        )
        complete_path_set = set(self._graph.recursive_current_get(main_root))
        self._graph.owned_path_set_validate(requested_path_set, complete_path_set=complete_path_set)

    def validate(
        self,
        repository: RepositoryState,
        *,
        task_state: TaskState,
        main_integrity_required: bool = True,
    ) -> None:
        """Prove the complete recursive set, read-only gitlinks, and task-owned descendants.

        Args:
            repository: Exact Git repository root.
            task_state: Task state.
            main_integrity_required: Main integrity required.
        """

        task_root = Path(repository.task_root).resolve(strict=True)
        owned_by_path_map = {item.path: item for item in repository.task_owned_submodule_list}
        recorded_by_path_map = {item.path: item.baseline_commit for item in repository.submodule_gitlink_list}
        self._graph.initialize(
            task_root,
            common_prefix=task_state.common_prefix,
            task_owned_path_set=set(owned_by_path_map),
            detach_read_only=True,
            repair_read_only=True,
            interrupted_state_exists=True,
        )
        current_by_path_map = self._graph.recursive_current_get(task_root)
        if current_by_path_map.keys() != recorded_by_path_map.keys():
            raise GoalLifecycleError(f"Recursive submodule set changed after preparation: {task_root}")
        self._graph.owned_path_set_validate(set(owned_by_path_map), complete_path_set=set(current_by_path_map))
        for path_text, baseline_commit in recorded_by_path_map.items():
            submodule_root = task_root / path_text
            index_commit = current_by_path_map[path_text]
            effective_commit = self._git.commit_get(submodule_root)
            owned = owned_by_path_map.get(path_text)
            if owned is None:
                if index_commit != baseline_commit or effective_commit != baseline_commit:
                    raise GoalLifecycleError(f"Read-only submodule moved from its recorded gitlink: {submodule_root}")
                self._git.clean_require(submodule_root)
                continue
            if self._git.branch_get(submodule_root) != task_state.common_prefix:
                raise GoalLifecycleError(f"Task-owned submodule has another branch: {submodule_root}")
            for candidate, label in (
                (index_commit, "index gitlink"),
                (effective_commit, "effective commit"),
            ):
                self._git.ancestor_require(
                    submodule_root,
                    baseline_commit,
                    candidate,
                    label=f"{path_text} task-owned submodule {label}",
                )
            self._boundary_manager.validate(
                owned.repository,
                task_state=task_state,
                main_integrity_required=main_integrity_required,
            )

    def pending_retire(self, repository: RepositoryState, *, common_prefix: str) -> None:
        """Retire exact submodule pending markers after replicated state commits.

        Args:
            repository: Exact Git repository root.
            common_prefix: Exact task common prefix.
        """

        for item in repository.task_owned_submodule_list:
            self._branch_manager.pending_retire(
                Path(item.repository.task_root),
                common_prefix=common_prefix,
            )

    def missing_inventory_recover(self, repository: RepositoryState, *, common_prefix: str) -> RepositoryState:
        """Recover only a completely provider-omitted recursive inventory.

        Args:
            repository: Recorded top-level repository state.
            common_prefix: Exact task common prefix.

        Returns:
            Original or recovered repository state.
        """

        return self._inventory_recoverer.recover(repository, common_prefix=common_prefix)

    def pending_cleanup_binding_receipt_ensure(self, repository: RepositoryState, *, task_state: TaskState) -> None:
        """Finish receipt publication for a recovered active delegated boundary.

        Args:
            repository: Recorded top-level repository state.
            task_state: Exact task state.
        """

        if task_state.lifecycle_state != "active":
            return
        for item in repository.task_owned_submodule_list:
            task_root = Path(item.repository.task_root)
            if not self._branch_manager.pending_exists(task_root, common_prefix=task_state.common_prefix):
                continue
            self._boundary_manager.cleanup_binding_receipt_ensure(item.repository, task_state=task_state)
            self._repair_report.record(f"recovered-submodule-cleanup-binding-ensured:{task_root}")
