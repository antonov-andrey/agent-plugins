"""Private provider-state retirement after completed goal deletion."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import directory_sync
from goal_lifecycle.task.model import TaskState, repository_boundary_list_get


class GoalDeletionPrivateStateRetirer:
    """Remove cleanup bindings, task replicas, authoritative state, and journal."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        self._coordination = coordination
        self._git = git

    def retire(self, state: TaskState, *, journal: dict[str, object], journal_path: Path) -> None:
        """Retire every private artifact idempotently after public deletion completed."""

        task_directory_set: set[Path] = set()
        for repository in repository_boundary_list_get(state):
            common_directory = self._git.common_directory_get(Path(repository.main_root))
            task_directory = common_directory / "agent-workflows" / "task" / state.common_prefix
            task_directory_set.add(task_directory)
            for path in (
                common_directory / "agent-workflows" / "cleanup-binding" / f"{state.common_prefix}.json",
                common_directory / "agent-workflows" / "external-cleanup" / f"{state.common_prefix}.json",
                task_directory / "state.json",
            ):
                self._file_unlink(path)
        for item in journal["submodule_list"]:
            for common_directory_text in (
                item["main_common_directory"],
                item["task_common_directory"],
            ):
                common_directory = Path(common_directory_text)
                task_directory = common_directory / "agent-workflows" / "task" / state.common_prefix
                task_directory_set.add(task_directory)
                for path in (
                    common_directory / "agent-workflows" / "cleanup-binding" / f"{state.common_prefix}.json",
                    common_directory / "agent-workflows" / "external-cleanup" / f"{state.common_prefix}.json",
                    task_directory / "state.json",
                ):
                    self._file_unlink(path)
        coordination_task_directory = self._coordination.state_path_get(state.common_prefix).parent
        task_directory_set.add(coordination_task_directory)
        self._file_unlink(self._coordination.state_path_get(state.common_prefix))
        self._file_unlink(coordination_task_directory / "replica-index.json")
        self._file_unlink(journal_path)
        for task_directory in sorted(task_directory_set, key=str):
            try:
                task_directory.rmdir()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise GoalLifecycleError(
                    f"Goal private task directory contains an unknown artifact: {task_directory}"
                ) from error
            directory_sync(task_directory.parent)

    @staticmethod
    def _file_unlink(path: Path) -> None:
        """Idempotently remove one exact private file and persist its parent entry."""

        try:
            path.unlink()
        except FileNotFoundError:
            return
        directory_sync(path.parent)
