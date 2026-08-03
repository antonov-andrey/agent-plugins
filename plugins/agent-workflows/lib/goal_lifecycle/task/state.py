"""Crash-safe replicated private state for one tracked goal task."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write, directory_sync, json_object_load
from goal_lifecycle.task.model import TaskState, repository_boundary_list_get
from goal_lifecycle.task.repair import TaskRepairReport


class TaskStateStore:
    """Own one journaled authoritative state and its exact repository replicas."""

    def __init__(
        self,
        coordination: CoordinationRepository,
        *,
        git: Git,
        repair_report: TaskRepairReport | None = None,
    ) -> None:
        """Initialize the task state store dependencies.

        Args:
            coordination: Coordination.
            git: Git command boundary.
            repair_report: Repair report.
        """

        self._coordination = coordination
        self._git = git
        self._repair_report = repair_report or TaskRepairReport()

    def optional_get(self, common_prefix: str) -> TaskState | None:
        """Recover one interrupted write and return the authoritative task state.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            Resulting task state.
        """

        self._pending_recover(common_prefix)
        state = self._authoritative_optional_get(common_prefix)
        if state is None:
            state = self._authoritative_restore_optional(common_prefix)
            if state is None:
                return None
        self._replica_set_ensure(state)
        return state

    def get(self, common_prefix: str) -> TaskState:
        """Return the required recovered authoritative task state.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            The required recovered authoritative task state.
        """

        state = self.optional_get(common_prefix)
        if state is None:
            raise GoalLifecycleError(f"Task private state does not exist: {common_prefix}")
        return state

    def write(self, state: TaskState) -> None:
        """Commit one generation through durable intent before any replica mutation.

        Args:
            state: Exact runtime state.
        """

        self._pending_recover(state.common_prefix)
        current = self._authoritative_optional_get(state.common_prefix)
        if current is None and self._index_path_get(state.common_prefix).exists():
            current = self._authoritative_restore_optional(state.common_prefix)
        if current is None:
            if state.provider_state_generation != 1:
                raise GoalLifecycleError("Initial private task state must use generation 1")
        else:
            if state.common_prefix != current.common_prefix or state.goals_repository != current.goals_repository:
                raise GoalLifecycleError("Private task identity cannot change during a state write")
            generation_delta = state.provider_state_generation - current.provider_state_generation
            if generation_delta not in {0, 1}:
                raise GoalLifecycleError("Private task state generation must be idempotent or advance by one")
            if generation_delta == 0 and state != current:
                raise GoalLifecycleError("Equal private task state generation contains another payload")
        self._transaction_commit(previous=current, successor=state)

    def replica_require(self, state: TaskState, *, task_root: Path) -> None:
        """Require one repository replica to equal the authoritative canonical state.

        Args:
            state: Exact runtime state.
            task_root: Task root.
        """

        path = self._replica_path_get(task_root, common_prefix=state.common_prefix)
        payload = json_object_load(path, label="replicated task private state")
        replica = TaskState.from_payload(payload)
        if replica != state or payload != state.payload_get():
            raise GoalLifecycleError(f"Private task-state replicas differ: {task_root}")

    def _pending_recover(self, common_prefix: str) -> None:
        """Recover an interrupted replicated-state publication from its durable journal.

        Args:
            common_prefix: Exact task common prefix.
        """

        journal_path = self._journal_path_get(common_prefix)
        if not journal_path.exists():
            return
        journal = json_object_load(journal_path, label="private task-state write journal")
        previous, successor, replica_path_list = self._journal_validate(journal, common_prefix=common_prefix)
        self._transaction_resume(
            journal_path=journal_path,
            previous=previous,
            successor=successor,
            replica_path_list=replica_path_list,
        )
        self._repair_report.record(f"private-state-write-recovered:{common_prefix}")

    def _transaction_commit(self, *, previous: TaskState | None, successor: TaskState) -> None:
        """Commit one prepared task-state transaction to every durable replica.

        Args:
            previous: Previous.
            successor: Successor.
        """

        journal_path = self._journal_path_get(successor.common_prefix)
        if journal_path.exists():
            raise GoalLifecycleError("Pending private task-state write was not recovered")
        replica_path_list = self._replica_path_list_get(successor)
        previous_replica_path_list = self._replica_path_list_get(previous) if previous is not None else []
        complete_replica_path_list = sorted(set(previous_replica_path_list) | set(replica_path_list))
        journal = {
            "schema_version": 1,
            "common_prefix": successor.common_prefix,
            "previous_payload": (previous.payload_get() if previous is not None else None),
            "successor_payload": successor.payload_get(),
            "replica_path_list": complete_replica_path_list,
        }
        atomic_json_write(journal_path, journal)
        self._transaction_resume(
            journal_path=journal_path,
            previous=previous,
            successor=successor,
            replica_path_list=complete_replica_path_list,
        )

    def _transaction_resume(
        self,
        *,
        journal_path: Path,
        previous: TaskState | None,
        successor: TaskState,
        replica_path_list: list[str],
    ) -> None:
        """Resume one interrupted task-state transaction from its durable journal.

        Args:
            journal_path: Exact filesystem path for journal.
            previous: Previous.
            successor: Successor.
            replica_path_list: Ordered replica path values.
        """

        successor_payload = successor.payload_get()
        current_path_list = [
            self._coordination.state_path_get(successor.common_prefix),
            *(Path(value) for value in replica_path_list),
        ]
        for path in current_path_list:
            if not path.exists():
                continue
            candidate = TaskState.from_payload(json_object_load(path, label="private task-state transaction target"))
            if candidate != successor and (previous is None or candidate != previous):
                raise GoalLifecycleError(f"Private task-state transaction found contradictory state: {path}")
        index_path = self._index_path_get(successor.common_prefix)
        if index_path.exists():
            current_index = self._index_payload_load(index_path, common_prefix=successor.common_prefix)
            allowed_index_list = [self._index_payload_get(successor)]
            if previous is not None:
                allowed_index_list.append(self._index_payload_get(previous))
            if current_index not in allowed_index_list:
                raise GoalLifecycleError("Private task-state replica index is contradictory")

        atomic_json_write(index_path, self._index_payload_get(successor))
        successor_replica_path_set = set(self._replica_path_list_get(successor))
        if successor_replica_path_set != set(replica_path_list):
            obsolete_path_set = set(replica_path_list) - successor_replica_path_set
            if obsolete_path_set:
                raise GoalLifecycleError("Private task-state transaction cannot retire replica owners")
        for path_text in sorted(successor_replica_path_set):
            atomic_json_write(Path(path_text), successor_payload)
        authoritative_path = self._coordination.state_path_get(successor.common_prefix)
        atomic_json_write(authoritative_path, successor_payload)

        if self._index_payload_load(index_path, common_prefix=successor.common_prefix) != self._index_payload_get(
            successor
        ):
            raise GoalLifecycleError("Private task-state replica index did not commit")
        for path in [
            authoritative_path,
            *(Path(value) for value in sorted(successor_replica_path_set)),
        ]:
            if json_object_load(path, label="committed private task state") != successor_payload:
                raise GoalLifecycleError(f"Private task-state target did not commit: {path}")
        journal_path.unlink()
        directory_sync(journal_path.parent)

    def _authoritative_optional_get(self, common_prefix: str) -> TaskState | None:
        """Return the optional authoritative task state after replica agreement proof.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            The optional authoritative.
        """

        path = self._coordination.state_path_get(common_prefix)
        if not path.exists():
            return None
        return TaskState.from_payload(json_object_load(path, label="task private state"))

    def _authoritative_restore_optional(self, common_prefix: str) -> TaskState | None:
        """Restore missing replicas from one uniquely authoritative task state when possible.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            Resulting task state.
        """

        index_path = self._index_path_get(common_prefix)
        if not index_path.exists():
            return None
        index = self._index_payload_load(index_path, common_prefix=common_prefix)
        state: TaskState | None = None
        for path_text in index["replica_path_list"]:
            path = Path(path_text)
            if not path.exists():
                continue
            candidate = TaskState.from_payload(json_object_load(path, label="task private-state recovery replica"))
            if state is None:
                state = candidate
            elif candidate != state:
                raise GoalLifecycleError("Private task-state recovery replicas are contradictory")
        if state is None:
            raise GoalLifecycleError("Private task state is missing and no indexed replica survives")
        if self._index_payload_get(state) != index:
            raise GoalLifecycleError("Private task-state replica index differs from surviving state")
        self._transaction_commit(previous=state, successor=state)
        self._repair_report.record(f"private-state-authoritative-restored:{common_prefix}")
        return state

    def _replica_set_ensure(self, state: TaskState) -> None:
        """Repair missing private-state replicas from one authoritative state.

        Args:
            state: Exact runtime state.
        """

        expected_index = self._index_payload_get(state)
        index_path = self._index_path_get(state.common_prefix)
        repair_required = not index_path.exists()
        if (
            index_path.exists()
            and self._index_payload_load(index_path, common_prefix=state.common_prefix) != expected_index
        ):
            raise GoalLifecycleError("Private task-state replica index differs from authoritative state")
        for path_text in expected_index["replica_path_list"]:
            path = Path(path_text)
            if not path.exists():
                repair_required = True
                continue
            payload = json_object_load(path, label="indexed task private-state replica")
            replica = TaskState.from_payload(payload)
            if replica != state:
                raise GoalLifecycleError(f"Private task-state replica is contradictory: {path}")
            if payload != state.payload_get():
                repair_required = True
        if repair_required:
            self._transaction_commit(previous=state, successor=state)
            self._repair_report.record(f"private-state-replicas-repaired:{state.common_prefix}")

    def _journal_validate(
        self,
        payload: dict[str, Any],
        *,
        common_prefix: str,
    ) -> tuple[TaskState | None, TaskState, list[str]]:
        """Require one replicated-state journal to match its exact pending transaction.

        Args:
            payload: Structured operation payload.
            common_prefix: Exact task common prefix.

        Returns:
            Values in deterministic immutable order.
        """

        expected_key_set = {
            "schema_version",
            "common_prefix",
            "previous_payload",
            "successor_payload",
            "replica_path_list",
        }
        if (
            set(payload) != expected_key_set
            or payload.get("schema_version") != 1
            or payload.get("common_prefix") != common_prefix
        ):
            raise GoalLifecycleError("Private task-state write journal has another identity or shape")
        previous_payload = payload["previous_payload"]
        if previous_payload is not None and not isinstance(previous_payload, dict):
            raise GoalLifecycleError("Private task-state write journal previous payload is malformed")
        successor_payload = payload["successor_payload"]
        if not isinstance(successor_payload, dict):
            raise GoalLifecycleError("Private task-state write journal successor payload is malformed")
        previous = TaskState.from_payload(previous_payload) if previous_payload is not None else None
        successor = TaskState.from_payload(successor_payload)
        if successor.common_prefix != common_prefix or (
            previous is not None and previous.common_prefix != common_prefix
        ):
            raise GoalLifecycleError("Private task-state write journal task identity differs")
        raw_path_list = payload["replica_path_list"]
        if (
            not isinstance(raw_path_list, list)
            or any(not isinstance(item, str) or not Path(item).is_absolute() for item in raw_path_list)
            or raw_path_list != sorted(set(raw_path_list))
        ):
            raise GoalLifecycleError("Private task-state write journal replica inventory is malformed")
        expected_path_list = sorted(
            set(self._replica_path_list_get(successor))
            | (set(self._replica_path_list_get(previous)) if previous is not None else set())
        )
        if raw_path_list != expected_path_list:
            raise GoalLifecycleError("Private task-state write journal replica inventory differs")
        return previous, successor, raw_path_list

    def _index_payload_get(self, state: TaskState) -> dict[str, Any]:
        """Return the canonical private replica-index payload for one task state.

        Args:
            state: Exact runtime state.

        Returns:
            The index payload.
        """

        return {
            "schema_version": 1,
            "common_prefix": state.common_prefix,
            "replica_path_list": self._replica_path_list_get(state),
        }

    def _index_payload_load(self, path: Path, *, common_prefix: str) -> dict[str, Any]:
        """Load and validate one private replica index from its ordinary JSON file.

        Args:
            path: Exact filesystem path.
            common_prefix: Exact task common prefix.

        Returns:
            The index payload.
        """

        payload = json_object_load(path, label="private task-state replica index")
        if (
            set(payload) != {"schema_version", "common_prefix", "replica_path_list"}
            or payload.get("schema_version") != 1
            or payload.get("common_prefix") != common_prefix
            or not isinstance(payload.get("replica_path_list"), list)
            or any(not isinstance(item, str) or not Path(item).is_absolute() for item in payload["replica_path_list"])
            or payload["replica_path_list"] != sorted(set(payload["replica_path_list"]))
        ):
            raise GoalLifecycleError("Private task-state replica index is malformed")
        return payload

    def _replica_path_list_get(self, state: TaskState | None) -> list[str]:
        """Return every durable task-state replica path in deterministic order.

        Args:
            state: Exact runtime state.

        Returns:
            The replica path list.
        """

        if state is None:
            return []
        return sorted(
            {
                str(self._replica_path_get(root, common_prefix=state.common_prefix))
                for repository in repository_boundary_list_get(state)
                for root in {Path(repository.main_root), Path(repository.task_root)}
            }
        )

    def _index_path_get(self, common_prefix: str) -> Path:
        """Return the repository-common path of one task replica index.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            The index path.
        """

        return self._coordination.state_path_get(common_prefix).parent / "replica-index.json"

    def _journal_path_get(self, common_prefix: str) -> Path:
        """Return the repository-common path of one pending state journal.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            The journal path.
        """

        return self._coordination.journal_path_get(common_prefix, "private-write")

    def _replica_path_get(self, task_root: Path, *, common_prefix: str) -> Path:
        """Return the private replica path owned by one Git common directory.

        Args:
            task_root: Task root.
            common_prefix: Exact task common prefix.

        Returns:
            The private replica path owned by one Git common directory.
        """

        return self._git.common_directory_get(task_root) / "agent-workflows" / "task" / common_prefix / "state.json"
