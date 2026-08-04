"""Explicit resumable cleanup that retains the tracked goal registry."""

from __future__ import annotations

from pathlib import Path
import secrets

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.deletion.external import GoalExternalResourceCleanup
from goal_lifecycle.deletion.journal import deletion_journal_validate
from goal_lifecycle.deletion.repository import GoalTaskRepositoryRetirer
from goal_lifecycle.deletion.registry import GoalDeletionRegistry
from goal_lifecycle.deletion.scope import GoalDeletionScopeResolver
from goal_lifecycle.deletion.state import GoalDeletionPrivateStateRetirer
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.identity import common_prefix_validate
from goal_lifecycle.io import atomic_json_write, json_object_load
from goal_lifecycle.task.model import TaskState


class GoalDeletionWorkflow:
    """Delete one task's resources and retain its tracked historical record."""

    def __init__(self, goals_repository: Path, *, git: Git | None = None) -> None:
        """Initialize the goal deletion workflow dependencies.

        Args:
            goals_repository: Goals repository.
            git: Git command boundary.
        """

        self._git = git or Git()
        self._coordination = CoordinationRepository(goals_repository, git=self._git)
        self._scope = GoalDeletionScopeResolver(self._coordination, git=self._git)
        self._external_cleanup = GoalExternalResourceCleanup(git=self._git)
        self._repository_retirer = GoalTaskRepositoryRetirer(git=self._git)
        self._registry = GoalDeletionRegistry(self._coordination, git=self._git)
        self._private_state_retirer = GoalDeletionPrivateStateRetirer(self._coordination, git=self._git)

    def delete(self, *, common_prefix: str) -> dict[str, object]:
        """Delete every remaining resource for one explicitly selected task.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            Final deletion result payload.
        """

        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state_path = self._coordination.state_path_get(common_prefix)
            journal_path = self._coordination.journal_path_get(common_prefix, "delete")
            if journal_path.exists():
                journal = json_object_load(journal_path, label="goal deletion journal")
                state = TaskState.from_payload(journal.get("task_state"))
                deletion_journal_validate(journal, state=state)
            else:
                if not state_path.exists():
                    document = self._registry.document_get(common_prefix)
                    if document.task_resource_state == "deleted":
                        self._private_state_retirer.merge_owner_retire(common_prefix)
                        self._private_state_retirer.coordination_task_state_retire(common_prefix)
                        return {
                            "schema_version": 4,
                            "common_prefix": common_prefix,
                            "phase": "complete",
                            "task_resource_state": "deleted",
                        }
                    raise GoalLifecycleError("Task cleanup scope is unavailable")
                state = TaskState.from_payload(json_object_load(state_path, label="task private state"))
                journal = {
                    "schema_version": 4,
                    "common_prefix": common_prefix,
                    "operation_identity": secrets.token_hex(16),
                    "phase": "external-resources",
                    "project_list": self._scope.project_list_get(state),
                    "submodule_list": self._scope.submodule_list_get(state),
                    "repository_index": 0,
                    "task_resource_state": "retained",
                    "task_state": state.payload_get(),
                }
                atomic_json_write(journal_path, journal)
            self._resume(state=state, journal=journal, journal_path=journal_path)
            return journal

    def _resume(self, *, state: TaskState, journal: dict[str, object], journal_path: Path) -> None:
        """Resume every ordered phase from the exact durable journal marker.

        Args:
            state: Exact runtime state.
            journal: Journal.
            journal_path: Exact filesystem path for journal.
        """

        phase = journal["phase"]
        if phase == "external-resources":
            self._external_cleanup.resume(state=state, journal=journal, journal_path=journal_path)
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="worktrees")
        if phase == "worktrees":
            self._repository_retirer.worktrees_retire(journal=journal)
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="remote-refs")
        if phase == "remote-refs":
            self._repository_retirer.remote_refs_retire(
                journal=journal,
                journal_path=journal_path,
                state=state,
            )
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="local-refs")
        if phase == "local-refs":
            self._repository_retirer.local_refs_retire(
                journal=journal,
                journal_path=journal_path,
                state=state,
            )
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="registry-update")
        if phase == "registry-update":
            self._registry.deleted_mark(state.common_prefix)
            journal["task_resource_state"] = "deleted"
            phase = _journal_phase_update(
                journal,
                journal_path=journal_path,
                phase="complete",
            )
        if phase == "complete":
            self._private_state_retirer.retire(state, journal=journal, journal_path=journal_path)


def _journal_phase_update(
    journal: dict[str, object],
    *,
    journal_path: Path,
    phase: str,
    repository_index: int = 0,
) -> str:
    """Persist one next deletion phase and reset its repository cursor.

    Args:
        journal: Journal.
        journal_path: Exact filesystem path for journal.
        phase: Phase.
        repository_index: Repository index.

    Returns:
        Resulting text value.
    """

    journal.update({"phase": phase, "repository_index": repository_index})
    atomic_json_write(journal_path, journal)
    return phase
