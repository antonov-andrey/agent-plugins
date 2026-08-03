"""Private provider-state retirement after completed goal deletion."""

from __future__ import annotations

from pathlib import Path
import shutil

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import directory_sync, json_object_load
from goal_lifecycle.task.model import TaskState


class GoalDeletionPrivateStateRetirer:
    """Remove every private artifact inside the exact task namespace."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        """Initialize the goal deletion private-state dependencies.

        Args:
            coordination: Coordination.
            git: Git command boundary.
        """

        self._coordination = coordination
        self._git = git

    def retire(self, state: TaskState, *, journal: dict[str, object], journal_path: Path) -> None:
        """Idempotently remove all private state for the exact deleted task.

        Args:
            state: Exact runtime state.
            journal: Journal.
            journal_path: Exact filesystem path for journal.
        """

        del journal_path
        self.merge_owner_retire(state.common_prefix)
        common_directory_set = {
            Path(value)
            for item in [*journal["project_list"], *journal["submodule_list"]]
            for value in (
                item.get("main_common_directory", ""),
                item.get("task_common_directory", ""),
            )
            if value
        }
        coordination_common_directory = self._git.common_directory_get(self._coordination.root)
        common_directory_set.add(coordination_common_directory)
        for common_directory in sorted(common_directory_set, key=str):
            if not common_directory.exists():
                continue
            for path in (
                common_directory / "agent-workflows" / "cleanup-binding" / f"{state.common_prefix}.json",
                common_directory / "agent-workflows" / "external-cleanup" / f"{state.common_prefix}.json",
            ):
                self._file_unlink(path)
        for common_directory in sorted(common_directory_set - {coordination_common_directory}, key=str):
            if not common_directory.exists():
                continue
            self._task_directory_retire(common_directory / "agent-workflows" / "task" / state.common_prefix)
        self.coordination_task_state_retire(state.common_prefix)

    def coordination_task_state_retire(self, common_prefix: str) -> None:
        """Remove an exact residual coordination task namespace.

        Args:
            common_prefix: Exact task common prefix.
        """

        common_directory = self._git.common_directory_get(self._coordination.root)
        self._task_directory_retire(common_directory / "agent-workflows" / "task" / common_prefix)

    def merge_owner_retire(self, common_prefix: str) -> None:
        """Remove the exact task's optional workspace merge owner.

        Args:
            common_prefix: Exact task common prefix.
        """

        merge_owner_path = self._coordination.merge_owner_path_get()
        if merge_owner_path.is_file():
            try:
                owner = json_object_load(merge_owner_path, label="workspace merge owner")
            except GoalLifecycleError:
                owner = {}
            if owner.get("common_prefix") == common_prefix:
                self._file_unlink(merge_owner_path)

    @staticmethod
    def _file_unlink(path: Path) -> None:
        """Idempotently remove one exact private file.

        Args:
            path: Exact filesystem path.
        """

        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            directory_sync(path.parent)

    @staticmethod
    def _task_directory_retire(path: Path) -> None:
        """Idempotently remove one exact private task namespace.

        Args:
            path: Exact private task directory.
        """

        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)
        else:
            return
        directory_sync(path.parent)
