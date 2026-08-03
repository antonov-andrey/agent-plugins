"""Complete lifecycle-state validation for one tracked goal task."""

from __future__ import annotations

import hashlib
from pathlib import Path

from goal_lifecycle.checkpoint.model import CheckpointDocument
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.task.model import TaskState, repository_boundary_list_get
from goal_lifecycle.task.repository import TaskRepositoryManager
from goal_lifecycle.task.state import TaskStateStore
from goal_lifecycle.yaml_document import yaml_document_load

_LIFECYCLE_INDEX_BY_NAME_MAP = {
    "repository_prepared": 0,
    "contracts_authored": 1,
    "goal_ready": 2,
    "active": 3,
}


class TaskLifecycleValidator:
    """Prove coordination, artifacts, participants, resources, and state replicas together."""

    def __init__(
        self,
        coordination: CoordinationRepository,
        *,
        git: Git,
        repository_manager: TaskRepositoryManager,
        state_store: TaskStateStore,
    ) -> None:
        """Initialize the task lifecycle validator dependencies.

        Args:
            coordination: Coordination.
            git: Git command boundary.
            repository_manager: Repository manager.
            state_store: State store.
        """

        self._coordination = coordination
        self._git = git
        self._repository_manager = repository_manager
        self._state_store = state_store

    def validate(
        self,
        state: TaskState,
        *,
        required_state: str,
        main_integrity_required: bool = True,
    ) -> None:
        """Require the complete current task to satisfy one lifecycle floor.

        Args:
            state: Exact runtime state.
            required_state: Required state.
            main_integrity_required: Main integrity required.
        """

        if required_state not in _LIFECYCLE_INDEX_BY_NAME_MAP:
            raise GoalLifecycleError("Unknown required lifecycle state")
        if _LIFECYCLE_INDEX_BY_NAME_MAP[state.lifecycle_state] < _LIFECYCLE_INDEX_BY_NAME_MAP[required_state]:
            raise GoalLifecycleError(f"Task lifecycle is {state.lifecycle_state}, below {required_state}")
        if Path(state.goals_repository).resolve(strict=True) != self._coordination.root:
            raise GoalLifecycleError("Private task state belongs to another project-goals repository")
        coordination_commit = self._coordination.synchronize_require()
        self._git.ancestor_require(
            self._coordination.root,
            state.coordination_commit,
            coordination_commit,
            label="Recorded coordination publication",
        )
        task_artifact_name_set = self._coordination.task_directory_shape_require(
            state.common_prefix,
            complete=state.is_sealed(),
        )
        if "checkpoint.yaml" in task_artifact_name_set:
            checkpoint_document = CheckpointDocument.from_payload(
                yaml_document_load(self._coordination.task_directory_get(state.common_prefix) / "checkpoint.yaml")
            )
            if state.lifecycle_state != "active" and checkpoint_document != CheckpointDocument.empty():
                raise GoalLifecycleError("An inactive goal candidate must have an empty checkpoint document")
        if state.is_sealed():
            if hashlib.sha256(self._coordination.file_bytes_get(state.common_prefix, "spec.md")).hexdigest() != (
                state.sealed_spec_sha256
            ) or hashlib.sha256(self._coordination.file_bytes_get(state.common_prefix, "goal.md")).hexdigest() != (
                state.sealed_goal_sha256
            ):
                raise GoalLifecycleError("Sealed task artifacts changed")
        for repository in state.repository_list:
            task_root = self._repository_manager.validate(
                repository,
                task_state=state,
                main_integrity_required=main_integrity_required,
            )
            self._state_store.replica_require(state, task_root=task_root)
        for boundary in repository_boundary_list_get(state):
            self._state_store.replica_require(state, task_root=Path(boundary.task_root))
            self._state_store.replica_require(state, task_root=Path(boundary.main_root))
        self._repository_manager.pending_retire(state)
