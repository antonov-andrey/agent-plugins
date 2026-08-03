"""Composition owner for task worktree, repository boundary, and submodule preparation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_bytes_write
from goal_lifecycle.task.boundary import RepositoryBoundaryManager
from goal_lifecycle.task.model import (
    RepositoryBoundaryState,
    RepositoryState,
    TaskOwnedSubmoduleState,
    TaskState,
    repository_boundary_list_get,
)
from goal_lifecycle.task.repair import TaskRepairReport
from goal_lifecycle.task.submodule import TaskSubmoduleManager
from goal_lifecycle.task.worktree import TaskWorktreeManager


class TaskRepositoryManager:
    """Wire cohesive owners for each top-level participant and delegated submodule."""

    def __init__(self, *, git: Git, repair_report: TaskRepairReport | None = None) -> None:
        self._git = git
        self._repair_report = repair_report or TaskRepairReport()
        self._boundary_manager = RepositoryBoundaryManager(git=git, repair_report=self._repair_report)
        self._submodule_manager = TaskSubmoduleManager(
            git=git,
            boundary_manager=self._boundary_manager,
            repair_report=self._repair_report,
        )
        self._worktree_manager = TaskWorktreeManager(git=git, repair_report=self._repair_report)

    def prepare(
        self,
        main_root: Path,
        *,
        common_prefix: str,
        requested_submodule_path_set: set[str],
        previous_state: RepositoryState | None = None,
    ) -> RepositoryState:
        """Create or resume one exact top-level worktree and all recursive boundaries."""

        main_root = self._git.root_get(main_root)
        self._git.clean_require(main_root)
        if self._git.branch_get(main_root) != "main":
            raise GoalLifecycleError(f"Implementation preparation requires canonical main checkout: {main_root}")
        self._git.fetch(main_root)
        current_main_commit = self._git.commit_get(main_root)
        if current_main_commit != self._git.commit_get(main_root, "refs/remotes/origin/main"):
            raise GoalLifecycleError(f"Implementation main must equal origin/main: {main_root}")
        baseline_commit = previous_state.baseline_commit if previous_state else current_main_commit
        self._git.ancestor_require(
            main_root,
            baseline_commit,
            current_main_commit,
            label=f"{main_root.name} current main ancestry",
        )
        self._submodule_manager.preflight(
            main_root,
            common_prefix=common_prefix,
            requested_path_set=requested_submodule_path_set,
        )
        self._main_worktree_exclude_ensure(main_root)
        task_root = self._worktree_manager.prepare(
            main_root,
            baseline_commit=baseline_commit,
            common_prefix=common_prefix,
            previous_state=previous_state,
        )
        top_boundary = self._boundary_manager.prepare(
            main_root=main_root,
            task_root=task_root,
            baseline_commit=baseline_commit,
            common_prefix=common_prefix,
            previous_state=previous_state,
        )
        submodule_gitlink_list, task_owned_submodule_list = self._submodule_manager.prepare(
            main_root=main_root,
            task_root=task_root,
            common_prefix=common_prefix,
            requested_path_set=requested_submodule_path_set,
            previous_state=previous_state,
        )
        return RepositoryState(
            accepted_main_commit_drift_list=top_boundary.accepted_main_commit_drift_list,
            baseline_commit=top_boundary.baseline_commit,
            branch_name=top_boundary.branch_name,
            cleanup_declaration_sha256=top_boundary.cleanup_declaration_sha256,
            main_commit=top_boundary.main_commit,
            main_root=top_boundary.main_root,
            manifest_sha256=top_boundary.manifest_sha256,
            origin_url=top_boundary.origin_url,
            resource_state_list=top_boundary.resource_state_list,
            submodule_gitlink_list=submodule_gitlink_list,
            task_owned_submodule_list=task_owned_submodule_list,
            task_root=top_boundary.task_root,
        )

    def refresh(self, state: RepositoryState) -> RepositoryState:
        """Refresh one participant after approved owner authoring without changing ownership."""

        return self.prepare(
            Path(state.main_root),
            common_prefix=state.branch_name,
            requested_submodule_path_set={item.path for item in state.task_owned_submodule_list},
            previous_state=state,
        )

    def validate(
        self,
        repository: RepositoryState,
        *,
        task_state: TaskState,
        main_integrity_required: bool = True,
    ) -> Path:
        """Require the top-level worktree and every nested boundary to remain exact."""

        task_root = self._worktree_manager.validate(repository, common_prefix=task_state.common_prefix)
        self._boundary_manager.validate(
            repository,
            task_state=task_state,
            main_integrity_required=main_integrity_required,
        )
        self._submodule_manager.validate(
            repository,
            task_state=task_state,
            main_integrity_required=main_integrity_required,
        )
        return task_root

    def pending_retire(self, state: TaskState) -> None:
        """Retire creation markers only after the complete state reached every replica."""

        for repository in state.repository_list:
            self._submodule_manager.pending_retire(repository, common_prefix=state.common_prefix)
            self._worktree_manager.pending_retire(Path(repository.main_root), common_prefix=state.common_prefix)

    def main_leak_recover(
        self,
        state: TaskState,
        *,
        main_repository: Path,
        path_list: Sequence[str],
    ) -> None:
        """Recover one exact top-level or task-owned boundary after explicit attestation."""

        _, boundary = self._boundary_locate(state, main_repository=main_repository)
        self._boundary_manager.main_integrity.leak_recover(boundary, path_list=path_list)

    def main_commit_drift_accept(
        self,
        state: TaskState,
        *,
        main_repository: Path,
        commit: str,
        path_list: Sequence[str],
    ) -> tuple[RepositoryState, ...]:
        """Return the participant list with one exact boundary attestation replaced."""

        top_index, boundary = self._boundary_locate(state, main_repository=main_repository)
        accepted = self._boundary_manager.main_integrity.commit_drift_accept(
            boundary,
            commit=commit,
            path_list=path_list,
        )
        top = state.repository_list[top_index]
        if top.main_root == accepted.main_root:
            replacement = replace(
                top,
                accepted_main_commit_drift_list=accepted.accepted_main_commit_drift_list,
                main_commit=accepted.main_commit,
            )
        else:
            replacement = replace(
                top,
                task_owned_submodule_list=tuple(
                    (replace(item, repository=accepted) if item.repository.main_root == accepted.main_root else item)
                    for item in top.task_owned_submodule_list
                ),
            )
        return tuple(replacement if index == top_index else item for index, item in enumerate(state.repository_list))

    def cleanup_binding_receipt_ensure(self, state: TaskState) -> None:
        """Create every active-state cleanup receipt idempotently."""

        if state.lifecycle_state != "active" or state.cleanup_binding_generation < 1:
            raise GoalLifecycleError("Cleanup binding receipts require durable active state")
        for boundary in repository_boundary_list_get(state):
            self._boundary_manager.cleanup_binding_receipt_ensure(boundary, task_state=state)

    def cleanup_binding_receipt_retire(self, state: TaskState) -> None:
        """Remove every inactive-candidate cleanup receipt idempotently."""

        for boundary in repository_boundary_list_get(state):
            self._boundary_manager.cleanup_binding_receipt_retire(boundary, common_prefix=state.common_prefix)

    def _boundary_locate(self, state: TaskState, *, main_repository: Path) -> tuple[int, RepositoryBoundaryState]:
        main_root = self._git.root_get(main_repository)
        for index, repository in enumerate(state.repository_list):
            if Path(repository.main_root).resolve(strict=True) == main_root:
                return index, repository
            for submodule in repository.task_owned_submodule_list:
                if Path(submodule.repository.main_root).resolve(strict=True) == main_root:
                    return index, submodule.repository
        raise GoalLifecycleError(f"Repository boundary is not a participant in this task: {main_root}")

    def _main_worktree_exclude_ensure(self, main_root: Path) -> None:
        exclude_path = self._git.common_directory_get(main_root) / "info" / "exclude"
        text = exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
        line_list = text.splitlines()
        if "/.worktree/" not in line_list:
            line_list.append("/.worktree/")
            atomic_bytes_write(exclude_path, ("\n".join(line_list).strip() + "\n").encode(), mode=0o644)
