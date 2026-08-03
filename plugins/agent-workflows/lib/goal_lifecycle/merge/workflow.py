"""Exclusive resumable one-checkpoint fast-forward merge workflow."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.identity import common_prefix_validate
from goal_lifecycle.io import atomic_json_write, directory_sync, json_object_load
from goal_lifecycle.checkpoint.model import Checkpoint
from goal_lifecycle.merge.acceptance import AcceptedCheckpointPublisher
from goal_lifecycle.merge.checkpoint import GoalMergeCheckpointReader
from goal_lifecycle.merge.journal import (
    merge_journal_supersede_get,
    merge_journal_validate,
)
from goal_lifecycle.merge.owner import WorkspaceMergeOwner
from goal_lifecycle.merge.publication import CheckpointMainPublisher
from goal_lifecycle.merge.submodule import TaskOwnedSubmoduleMainPublisher
from goal_lifecycle.task.model import TaskState


class GoalMergeWorkflow:
    """Sequence one exact checkpoint merge and separately publish primary acceptance."""

    def __init__(self, goals_repository: Path, *, git: Git | None = None) -> None:
        """Initialize the goal merge workflow dependencies.

        Args:
            goals_repository: Goals repository.
            git: Git command boundary.
        """

        self._git = git or Git()
        self._coordination = CoordinationRepository(goals_repository, git=self._git)
        self._checkpoint_reader = GoalMergeCheckpointReader(self._coordination)
        self._publisher = CheckpointMainPublisher(self._coordination, git=self._git)
        self._submodule_publisher = TaskOwnedSubmoduleMainPublisher(self._coordination, git=self._git)
        self._acceptance = AcceptedCheckpointPublisher(self._coordination)
        self._owner = WorkspaceMergeOwner(self._coordination)

    def merge(self, *, common_prefix: str, checkpoint_id: str) -> dict[str, object]:
        """Fast-forward every main to one exact checkpoint under the exclusive lock.

        Args:
            common_prefix: Exact task common prefix.
            checkpoint_id: Exact checkpoint identity.

        Returns:
            Final cross-repository merge result payload.
        """

        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix), self._coordination.merge_lock():
            self._coordination.synchronize_require()
            document, checkpoint = self._checkpoint_reader.get(
                common_prefix=common_prefix,
                checkpoint_id=checkpoint_id,
            )
            document.selection_validate(checkpoint)
            task_state = self._task_state_get(common_prefix)
            submodule_snapshot_list = self._submodule_publisher.snapshot_get(task_state, checkpoint=checkpoint)
            expected_origin_by_project_path_map = self._expected_origin_by_project_path_map_get(
                state=task_state,
                checkpoint=checkpoint,
            )
            journal_path = self._coordination.journal_path_get(common_prefix, "merge")
            if journal_path.exists():
                journal = json_object_load(journal_path, label="goal merge journal")
                try:
                    merge_journal_validate(
                        journal,
                        common_prefix=common_prefix,
                        checkpoint=checkpoint,
                        submodule_snapshot_list=submodule_snapshot_list,
                    )
                except GoalLifecycleError as selected_checkpoint_error:
                    previous_checkpoint_id = str(journal.get("checkpoint_id"))
                    previous_checkpoint = next(
                        (item for item in document.checkpoint_list if item.checkpoint_id == previous_checkpoint_id),
                        None,
                    )
                    if previous_checkpoint is None:
                        raise GoalLifecycleError(
                            "Existing merge journal does not identify a tracked checkpoint"
                        ) from selected_checkpoint_error
                    if document.checkpoint_list.index(checkpoint) <= document.checkpoint_list.index(
                        previous_checkpoint
                    ):
                        raise GoalLifecycleError(
                            "Fix-forward checkpoint must follow the interrupted checkpoint"
                        ) from selected_checkpoint_error
                    journal, previous_by_path_map, previous_submodule_snapshot_list = merge_journal_supersede_get(
                        journal,
                        common_prefix=common_prefix,
                        checkpoint=checkpoint,
                        previous_checkpoint=previous_checkpoint,
                        submodule_snapshot_list=submodule_snapshot_list,
                    )
                    self._publisher.fix_forward_ancestry_require(
                        checkpoint,
                        previous_by_path_map=previous_by_path_map,
                    )
                    self._submodule_publisher.fix_forward_ancestry_require(
                        submodule_snapshot_list,
                        previous_snapshot_list=previous_submodule_snapshot_list,
                    )
                    self._submodule_publisher.preflight(
                        submodule_snapshot_list,
                        common_prefix=common_prefix,
                    )
                    self._publisher.preflight(
                        checkpoint,
                        common_prefix=common_prefix,
                        expected_origin_by_project_path_map=expected_origin_by_project_path_map,
                    )
                    self._owner.advance(
                        common_prefix=common_prefix,
                        previous_checkpoint_id=previous_checkpoint_id,
                        checkpoint_id=checkpoint_id,
                    )
                    atomic_json_write(journal_path, journal)
            else:
                journal = _merge_journal_new(
                    common_prefix=common_prefix,
                    checkpoint=checkpoint,
                    submodule_snapshot_list=submodule_snapshot_list,
                )
                self._submodule_publisher.preflight(submodule_snapshot_list, common_prefix=common_prefix)
                self._publisher.preflight(
                    checkpoint,
                    common_prefix=common_prefix,
                    expected_origin_by_project_path_map=expected_origin_by_project_path_map,
                )
                self._owner.acquire(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
                atomic_json_write(journal_path, journal)
            self._owner.acquire(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
            self._submodule_publisher.resume(
                common_prefix=common_prefix,
                journal=journal,
                journal_path=journal_path,
            )
            self._publisher.resume(
                expected_origin_by_project_path_map=expected_origin_by_project_path_map,
                journal=journal,
                journal_path=journal_path,
            )
            self._submodule_publisher.local_checkouts_sync(submodule_snapshot_list)
            journal["phase"] = "awaiting-acceptance"
            atomic_json_write(journal_path, journal)
            return journal

    def accept(self, *, common_prefix: str, checkpoint_id: str) -> str:
        """Publish accepted_checkpoint_id after exact primary-environment acceptance.

        Args:
            common_prefix: Exact task common prefix.
            checkpoint_id: Exact checkpoint identity.

        Returns:
            Resulting text value.
        """

        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix), self._coordination.merge_lock():
            self._coordination.synchronize_require()
            document, checkpoint = self._checkpoint_reader.get(
                common_prefix=common_prefix,
                checkpoint_id=checkpoint_id,
            )
            task_state = self._task_state_get(common_prefix)
            submodule_snapshot_list = self._submodule_publisher.snapshot_get(task_state, checkpoint=checkpoint)
            expected_origin_by_project_path_map = self._expected_origin_by_project_path_map_get(
                state=task_state,
                checkpoint=checkpoint,
            )
            journal_path = self._coordination.journal_path_get(common_prefix, "merge")
            journal = json_object_load(journal_path, label="goal merge journal")
            merge_journal_validate(
                journal,
                common_prefix=common_prefix,
                checkpoint=checkpoint,
                submodule_snapshot_list=submodule_snapshot_list,
            )
            if journal.get("phase") not in {"awaiting-acceptance", "accepted"}:
                raise GoalLifecycleError("Checkpoint cannot be accepted before every merge completes")
            self._publisher.merged_exact_require(
                checkpoint,
                expected_origin_by_project_path_map=expected_origin_by_project_path_map,
            )
            self._submodule_publisher.merged_exact_require(submodule_snapshot_list)
            commit = self._acceptance.publish(
                common_prefix=common_prefix,
                checkpoint=checkpoint,
                document=document,
            )
            journal["phase"] = "accepted"
            atomic_json_write(journal_path, journal)
            self._owner.release(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
            try:
                journal_path.unlink()
            except FileNotFoundError:
                pass
            directory_sync(journal_path.parent)
            return commit

    def _expected_origin_by_project_path_map_get(
        self,
        *,
        state: TaskState,
        checkpoint: Checkpoint,
    ) -> dict[str, str]:
        """Bind merge publication to the exact sealed repository origins.

        Args:
            state: Exact runtime state.
            checkpoint: Checkpoint.

        Returns:
            Expected repository origin URL keyed by project path.
        """

        workspace_root = self._coordination.root.parent.resolve(strict=True)
        result: dict[str, str] = {}
        for repository in state.repository_list:
            try:
                project_path = Path(repository.main_root).resolve(strict=True).relative_to(workspace_root).as_posix()
            except ValueError as error:
                raise GoalLifecycleError("Merge participant is outside the canonical workspace") from error
            result[project_path] = repository.origin_url
        if set(result) != {project.project_path for project in checkpoint.project_list}:
            raise GoalLifecycleError("Merge checkpoint differs from the sealed participant set")
        return result

    def _task_state_get(self, common_prefix: str) -> TaskState:
        """Load the exact task state bound to the selected checkpoint snapshot.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            The task state.
        """

        return TaskState.from_payload(
            json_object_load(
                self._coordination.state_path_get(common_prefix),
                label="task private state",
            )
        )


def _merge_journal_new(
    *,
    common_prefix: str,
    checkpoint: Checkpoint,
    submodule_snapshot_list: list[dict[str, object]],
) -> dict[str, object]:
    """Return one initial durable journal for the exact selected checkpoint.

    Args:
        common_prefix: Exact task common prefix.
        checkpoint: Checkpoint.
        submodule_snapshot_list: Ordered submodule snapshot values.

    Returns:
        One initial durable journal for the exact selected checkpoint.
    """

    return {
        "schema_version": 2,
        "common_prefix": common_prefix,
        "checkpoint_id": checkpoint.checkpoint_id,
        "phase": "merging",
        "project_list": [{**asdict(project), "merged": False} for project in checkpoint.project_list],
        "submodule_list": [{**item, "merged": False} for item in submodule_snapshot_list],
    }
