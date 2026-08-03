"""Publish one complete immutable cross-repository closing-commit snapshot."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from goal_lifecycle.checkpoint.model import (
    Checkpoint,
    CheckpointDocument,
    ProjectSnapshot,
)
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.identity import common_prefix_validate
from goal_lifecycle.task.repository import TaskRepositoryManager
from goal_lifecycle.task.repair import TaskRepairReport
from goal_lifecycle.task.state import TaskStateStore
from goal_lifecycle.task.validation import TaskLifecycleValidator
from goal_lifecycle.task.model import RepositoryState, TaskState
from goal_lifecycle.task.gitlink import task_owned_submodule_target_list_get
from goal_lifecycle.yaml_document import yaml_document_bytes_get, yaml_document_load


class GoalCheckpointPublisher:
    """Validate pushed task refs and append one full checkpoint atomically."""

    def __init__(self, goals_repository: Path, *, git: Git | None = None) -> None:
        """Initialize the goal checkpoint publisher dependencies.

        Args:
            goals_repository: Goals repository.
            git: Git command boundary.
        """

        self._git = git or Git()
        self._coordination = CoordinationRepository(goals_repository, git=self._git)
        repair_report = TaskRepairReport()
        self._repository_manager = TaskRepositoryManager(git=self._git, repair_report=repair_report)
        self._state_store = TaskStateStore(self._coordination, git=self._git, repair_report=repair_report)
        self._validator = TaskLifecycleValidator(
            self._coordination,
            git=self._git,
            repository_manager=self._repository_manager,
            state_store=self._state_store,
        )

    def publish(
        self,
        *,
        common_prefix: str,
        project_root_list: Sequence[Path],
    ) -> tuple[str, str]:
        """Append one complete sorted snapshot of clean fully pushed participants.

        Args:
            common_prefix: Exact task common prefix.
            project_root_list: Ordered project root values.

        Returns:
            Values in deterministic immutable order.
        """

        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            self._coordination.task_directory_shape_require(common_prefix, complete=True)
            checkpoint_path = self._coordination.task_directory_get(common_prefix) / "checkpoint.yaml"
            document = CheckpointDocument.from_payload(yaml_document_load(checkpoint_path))
            previous_by_path_map = (
                {item.project_path: item.git_commit_final for item in document.checkpoint_list[-1].project_list}
                if document.checkpoint_list
                else {}
            )
            state = self._state_store.get(common_prefix)
            self._validator.validate(
                state,
                required_state="active",
                main_integrity_required=False,
            )
            if hashlib.sha256(self._coordination.file_bytes_get(common_prefix, "spec.md")).hexdigest() != (
                state.sealed_spec_sha256
            ) or hashlib.sha256(self._coordination.file_bytes_get(common_prefix, "goal.md")).hexdigest() != (
                state.sealed_goal_sha256
            ):
                raise GoalLifecycleError("Checkpoint publication found changed sealed task artifacts")
            repository_by_task_root_map = {
                str(Path(item.task_root).resolve(strict=True)): item for item in state.repository_list
            }
            supplied_task_root_list = [self._git.root_get(item) for item in project_root_list]
            if len(supplied_task_root_list) != len(set(supplied_task_root_list)):
                raise GoalLifecycleError("Checkpoint repeats a task root")
            if {str(item) for item in supplied_task_root_list} != set(repository_by_task_root_map):
                raise GoalLifecycleError("Checkpoint roots must equal the complete sealed participant set")
            workspace_root = self._coordination.root.parent.resolve(strict=True)
            snapshot_list: list[ProjectSnapshot] = []
            for task_root in supplied_task_root_list:
                repository_state = repository_by_task_root_map[str(task_root)]
                main_root = Path(repository_state.main_root).resolve(strict=True)
                if main_root == self._coordination.root:
                    raise GoalLifecycleError("project-goals is excluded from checkpoint project_list")
                try:
                    project_path = main_root.relative_to(workspace_root).as_posix()
                except ValueError as error:
                    raise GoalLifecycleError(
                        f"Checkpoint repository is outside the canonical workspace: {main_root}"
                    ) from error
                if len(Path(project_path).parts) != 1:
                    raise GoalLifecycleError(f"Checkpoint repository must be a direct workspace child: {main_root}")
                if self._git.origin_url_get(task_root) != repository_state.origin_url:
                    raise GoalLifecycleError(f"Checkpoint repository origin changed: {task_root}")
                self._git.clean_require(task_root)
                if self._git.branch_get(task_root) != common_prefix:
                    raise GoalLifecycleError(f"Checkpoint repository is not on exact task branch: {task_root}")
                self._git.fetch(task_root)
                commit = self._git.commit_get(task_root)
                remote_task_ref = f"refs/remotes/origin/{common_prefix}"
                if self._git.commit_get(task_root, remote_task_ref) != commit:
                    raise GoalLifecycleError(f"Task branch is not fully pushed at exact HEAD: {task_root}")
                origin_main = self._git.commit_get(task_root, "refs/remotes/origin/main")
                self._git.ancestor_require(
                    task_root,
                    origin_main,
                    commit,
                    label=f"{project_path} task ancestry",
                )
                previous = previous_by_path_map.get(project_path)
                if document.checkpoint_list and previous is None:
                    raise GoalLifecycleError("Checkpoint participant set cannot change after first publication")
                if previous is not None:
                    self._git.ancestor_require(
                        task_root,
                        previous,
                        commit,
                        label=f"{project_path} checkpoint ancestry",
                    )
                self._task_owned_submodule_snapshot_require(
                    repository_state,
                    common_prefix=common_prefix,
                    top_level_commit=commit,
                    previous_top_level_commit=previous,
                )
                snapshot_list.append(ProjectSnapshot(project_path=project_path, git_commit_final=commit))
            snapshot_list.sort(key=lambda item: item.project_path)
            if previous_by_path_map and set(previous_by_path_map) != {item.project_path for item in snapshot_list}:
                raise GoalLifecycleError("Every checkpoint must contain the unchanged complete participant set")
            checkpoint_id = f"checkpoint-{len(document.checkpoint_list) + 1:04d}"
            updated = CheckpointDocument(
                accepted_checkpoint_id=document.accepted_checkpoint_id,
                checkpoint_list=(
                    *document.checkpoint_list,
                    Checkpoint(checkpoint_id=checkpoint_id, project_list=tuple(snapshot_list)),
                ),
            )
            relative_path = f"{common_prefix}/checkpoint.yaml"
            commit = self._coordination.publish(
                common_prefix=common_prefix,
                message=f"Publish {common_prefix} {checkpoint_id}",
                relative_payload_by_path_map={relative_path: yaml_document_bytes_get(updated.payload_get())},
                task_lock_already_held=True,
            )
            return checkpoint_id, commit

    def _task_owned_submodule_snapshot_require(
        self,
        repository_state: RepositoryState,
        *,
        common_prefix: str,
        top_level_commit: str,
        previous_top_level_commit: str | None,
    ) -> None:
        """Require every selected parent gitlink to equal one clean fully pushed submodule branch.

        Args:
            repository_state: Repository state.
            common_prefix: Exact task common prefix.
            top_level_commit: Top level commit.
            previous_top_level_commit: Previous top level commit.
        """

        previous_by_path_map = (
            {
                item.path: item.git_commit_final
                for item in task_owned_submodule_target_list_get(
                    repository_state,
                    top_level_commit=previous_top_level_commit,
                    git=self._git,
                )
            }
            if previous_top_level_commit is not None
            else {}
        )
        for target in task_owned_submodule_target_list_get(
            repository_state,
            top_level_commit=top_level_commit,
            git=self._git,
        ):
            boundary = target.state.repository
            task_root = Path(boundary.task_root).resolve(strict=True)
            if self._git.origin_url_get(task_root) != boundary.origin_url:
                raise GoalLifecycleError(f"Checkpoint task-owned submodule origin changed: {task_root}")
            self._git.clean_require(task_root)
            if self._git.branch_get(task_root) != common_prefix:
                raise GoalLifecycleError(f"Checkpoint task-owned submodule has another branch: {task_root}")
            self._git.fetch(task_root)
            commit = self._git.commit_get(task_root)
            if commit != target.git_commit_final:
                raise GoalLifecycleError(f"Parent gitlink differs from task-owned submodule HEAD: {task_root}")
            if self._git.commit_get(task_root, f"refs/remotes/origin/{common_prefix}") != commit:
                raise GoalLifecycleError(f"Task-owned submodule branch is not fully pushed: {task_root}")
            self._git.ancestor_require(
                task_root,
                boundary.baseline_commit,
                commit,
                label=f"{target.path} task-owned submodule baseline",
            )
            previous = previous_by_path_map.get(target.path)
            if previous is not None:
                self._git.ancestor_require(
                    task_root,
                    previous,
                    commit,
                    label=f"{target.path} task-owned submodule checkpoint ancestry",
                )
