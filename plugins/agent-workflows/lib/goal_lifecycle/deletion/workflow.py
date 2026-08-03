"""Explicit resumable resources-to-artifacts goal deletion workflow."""

from __future__ import annotations

from pathlib import Path
import secrets

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.deletion.bootstrap import CoordinationBootstrapRetirer
from goal_lifecycle.deletion.external import GoalExternalResourceCleanup
from goal_lifecycle.deletion.journal import (
    deletion_bootstrap_exception_get,
    deletion_journal_validate,
)
from goal_lifecycle.deletion.preflight import GoalDeletionPreflight
from goal_lifecycle.deletion.repository import GoalTaskRepositoryRetirer
from goal_lifecycle.deletion.state import GoalDeletionPrivateStateRetirer
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.identity import common_prefix_validate
from goal_lifecycle.io import atomic_json_write, json_object_load
from goal_lifecycle.task.model import TaskState


class GoalDeletionWorkflow:
    """Sequence one exact accepted task through its durable deletion phases."""

    def __init__(self, goals_repository: Path, *, git: Git | None = None) -> None:
        self._git = git or Git()
        self._coordination = CoordinationRepository(goals_repository, git=self._git)
        self._preflight = GoalDeletionPreflight(self._coordination, git=self._git)
        self._external_cleanup = GoalExternalResourceCleanup(git=self._git)
        self._repository_retirer = GoalTaskRepositoryRetirer(self._coordination, git=self._git)
        self._bootstrap_retirer = CoordinationBootstrapRetirer(
            self._coordination,
            git=self._git,
            repository_retirer=self._repository_retirer,
        )
        self._private_state_retirer = GoalDeletionPrivateStateRetirer(self._coordination, git=self._git)

    def delete(self, *, common_prefix: str, unfinished_goal_absent: bool) -> dict[str, object]:
        """Delete one accepted task only after explicit current-harness completion proof."""

        common_prefix_validate(common_prefix)
        if not unfinished_goal_absent:
            raise GoalLifecycleError("Explicit current-harness proof of no unfinished bound goal is required")
        with self._coordination.task_lock(common_prefix):
            state_path = self._coordination.state_path_get(common_prefix)
            journal_path = self._coordination.journal_path_get(common_prefix, "delete")
            if journal_path.exists():
                journal = json_object_load(journal_path, label="goal deletion journal")
                state = TaskState.from_payload(journal.get("task_state"))
                if (
                    state_path.exists()
                    and TaskState.from_payload(json_object_load(state_path, label="task private state")) != state
                ):
                    raise GoalLifecycleError("Goal deletion journal and private task state differ")
                deletion_journal_validate(journal, state=state)
            else:
                state = TaskState.from_payload(json_object_load(state_path, label="task private state"))
                journal = {
                    "schema_version": 2,
                    "common_prefix": common_prefix,
                    "operation_identity": secrets.token_hex(16),
                    "phase": "external-resources",
                    "coordination_bootstrap_exception": self._preflight.bootstrap_exception_payload_get(state),
                    "project_list": self._preflight.project_list_get(state),
                    "submodule_list": self._preflight.submodule_list_get(state),
                    "repository_index": 0,
                    "task_state": state.payload_get(),
                }
                atomic_json_write(journal_path, journal)
            self._resume(state=state, journal=journal, journal_path=journal_path)
            return journal

    def _resume(self, *, state: TaskState, journal: dict[str, object], journal_path: Path) -> None:
        """Resume every ordered phase from the exact durable journal marker."""

        phase = journal["phase"]
        bootstrap_exception = deletion_bootstrap_exception_get(journal)
        if phase == "external-resources":
            self._external_cleanup.resume(state=state, journal=journal, journal_path=journal_path)
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="worktrees")
        if phase == "worktrees":
            self._repository_retirer.worktrees_retire(
                bootstrap_exception=bootstrap_exception,
                journal=journal,
                state=state,
            )
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="remote-refs")
        if phase == "remote-refs":
            self._repository_retirer.remote_refs_retire(
                bootstrap_exception=bootstrap_exception,
                journal=journal,
                journal_path=journal_path,
                state=state,
            )
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="local-refs")
        if phase == "local-refs":
            self._repository_retirer.local_refs_retire(
                bootstrap_exception=bootstrap_exception,
                journal=journal,
                journal_path=journal_path,
                state=state,
            )
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="provider-excludes")
        if phase == "provider-excludes":
            self._repository_retirer.provider_excludes_retire(state)
            phase = _journal_phase_update(
                journal,
                journal_path=journal_path,
                phase="bootstrap-carriers",
                repository_index=len(state.repository_list),
            )
        if phase == "bootstrap-carriers":
            self._bootstrap_retirer.carriers_retire(bootstrap_exception)
            phase = _journal_phase_update(
                journal,
                journal_path=journal_path,
                phase="coordination-bootstrap-retire",
            )
        if phase == "coordination-bootstrap-retire":
            self._bootstrap_retirer.exception_retire(bootstrap_exception)
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="coordination-delete")
        if phase == "coordination-delete":
            self._coordination.publish(
                common_prefix=state.common_prefix,
                message=f"Delete completed task {state.common_prefix}",
                relative_payload_by_path_map={
                    f"{state.common_prefix}/checkpoint.yaml": None,
                    f"{state.common_prefix}/goal.md": None,
                    f"{state.common_prefix}/spec.md": None,
                },
                task_lock_already_held=True,
            )
            phase = _journal_phase_update(journal, journal_path=journal_path, phase="complete")
        if phase == "complete":
            self._private_state_retirer.retire(state, journal=journal, journal_path=journal_path)


def _journal_phase_update(
    journal: dict[str, object],
    *,
    journal_path: Path,
    phase: str,
    repository_index: int = 0,
) -> str:
    """Persist one next deletion phase and reset its repository cursor."""

    journal.update({"phase": phase, "repository_index": repository_index})
    atomic_json_write(journal_path, journal)
    return phase
