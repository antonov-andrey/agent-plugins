"""Fail-closed recovery for provider-omitted recursive submodule inventory."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.task.boundary import RepositoryBoundaryManager
from goal_lifecycle.task.model import (
    RepositoryState,
    SubmoduleGitlinkState,
    TaskOwnedSubmoduleState,
)
from goal_lifecycle.task.repair import TaskRepairReport
from goal_lifecycle.task.submodule_branch import TaskSubmoduleBranchManager
from goal_lifecycle.task.submodule_graph import TaskSubmoduleGraph


class TaskSubmoduleInventoryRecoverer:
    """Reconstruct only an entirely omitted inventory from committed Git evidence."""

    def __init__(
        self,
        *,
        git: Git,
        boundary_manager: RepositoryBoundaryManager,
        branch_manager: TaskSubmoduleBranchManager,
        graph: TaskSubmoduleGraph,
        repair_report: TaskRepairReport | None = None,
    ) -> None:
        """Initialize recovery dependencies.

        Args:
            git: Git command boundary.
            boundary_manager: Repository boundary manager.
            branch_manager: Task-owned branch manager.
            graph: Recursive submodule graph owner.
            repair_report: Repair report.
        """

        self._git = git
        self._boundary_manager = boundary_manager
        self._branch_manager = branch_manager
        self._graph = graph
        self._repair_report = repair_report or TaskRepairReport()

    def recover(self, repository: RepositoryState, *, common_prefix: str) -> RepositoryState:
        """Return an exact successor when a legacy provider omitted the complete inventory.

        Args:
            repository: Recorded top-level repository state.
            common_prefix: Exact task common prefix.

        Returns:
            Original or recovered repository state.
        """

        if repository.submodule_gitlink_list or repository.task_owned_submodule_list:
            return repository
        main_root = Path(repository.main_root).resolve(strict=True)
        task_root = Path(repository.task_root).resolve(strict=True)
        current_task_commit = self._git.commit_get(task_root)
        if not self._graph.commit_contains_gitlink(
            task_root,
            commit=repository.baseline_commit,
        ) and not self._graph.commit_contains_gitlink(task_root, commit=current_task_commit):
            return repository
        self._git.clean_require(task_root)
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
            task_owned_path_set=set(),
            detach_read_only=True,
            repair_read_only=False,
            interrupted_state_exists=True,
        )
        baseline_by_path_map = self._graph.recursive_at_commit_get(
            task_root,
            commit=repository.baseline_commit,
        )
        current_by_path_map = self._graph.recursive_at_commit_get(
            task_root,
            commit=current_task_commit,
        )
        if not baseline_by_path_map and not current_by_path_map:
            return repository
        if set(baseline_by_path_map) != set(current_by_path_map):
            raise GoalLifecycleError(
                f"Provider-omitted recursive submodule path set cannot be recovered safely: {task_root}"
            )
        task_owned_path_set = {
            path
            for path, baseline_commit in baseline_by_path_map.items()
            if current_by_path_map[path] != baseline_commit
        }
        self._graph.owned_path_set_validate(
            task_owned_path_set,
            complete_path_set=set(current_by_path_map),
        )
        self._graph.initialize(
            task_root,
            common_prefix=common_prefix,
            task_owned_path_set=task_owned_path_set,
            detach_read_only=True,
            repair_read_only=False,
            interrupted_state_exists=True,
        )

        owned_state_list: list[TaskOwnedSubmoduleState] = []
        for path_text in sorted(task_owned_path_set, key=lambda item: (len(PurePosixPath(item).parts), item)):
            main_submodule_root = main_root / path_text
            task_submodule_root = task_root / path_text
            baseline_commit = baseline_by_path_map[path_text]
            current_commit = current_by_path_map[path_text]
            self._branch_manager.existing_pushed_adopt(
                main_root=main_submodule_root,
                task_root=task_submodule_root,
                path_text=path_text,
                common_prefix=common_prefix,
                baseline_commit=baseline_commit,
                current_commit=current_commit,
            )
            boundary = self._boundary_manager.existing_state_capture(
                main_root=main_submodule_root,
                task_root=task_submodule_root,
                baseline_commit=baseline_commit,
                common_prefix=common_prefix,
            )
            owned_state_list.append(TaskOwnedSubmoduleState(path=path_text, repository=boundary))

        recovered = replace(
            repository,
            submodule_gitlink_list=tuple(
                SubmoduleGitlinkState(path=path, baseline_commit=baseline_by_path_map[path])
                for path in sorted(baseline_by_path_map)
            ),
            task_owned_submodule_list=tuple(owned_state_list),
        )
        self._repair_report.record(f"provider-omitted-submodule-inventory-recovered:{task_root}")
        return recovered
