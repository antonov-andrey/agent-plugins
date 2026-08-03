"""Central task-artifact and isolated implementation-worktree lifecycle sequencing."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from goal_lifecycle.checkpoint.model import CheckpointDocument
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.identity import (
    common_prefix_validate,
    repository_relative_path_validate,
)
from goal_lifecycle.task.artifact import (
    ordinary_task_artifact_input_get,
    ordinary_task_artifact_input_require,
)
from goal_lifecycle.task.repository import TaskRepositoryManager
from goal_lifecycle.task.model import TaskState
from goal_lifecycle.task.repair import TaskRepairReport
from goal_lifecycle.task.state import TaskStateStore
from goal_lifecycle.task.validation import TaskLifecycleValidator
from goal_lifecycle.yaml_document import yaml_document_bytes_get, yaml_document_load


class GoalWorktreeWorkflow:
    """Sequence approved task contracts, participant preparation, sealing, and activation."""

    def __init__(self, goals_repository: Path, *, git: Git | None = None) -> None:
        """Initialize the goal worktree workflow dependencies.

        Args:
            goals_repository: Goals repository.
            git: Git command boundary.
        """

        self._git = git or Git()
        self._coordination = CoordinationRepository(goals_repository, git=self._git)
        self._repair_report = TaskRepairReport()
        self._repository_manager = TaskRepositoryManager(git=self._git, repair_report=self._repair_report)
        self._state_store = TaskStateStore(
            self._coordination,
            git=self._git,
            repair_report=self._repair_report,
        )
        self._validator = TaskLifecycleValidator(
            self._coordination,
            git=self._git,
            repository_manager=self._repository_manager,
            state_store=self._state_store,
        )

    def prepare(
        self,
        *,
        common_prefix: str,
        repository_root_list: Sequence[Path],
        participating_submodule_list: Sequence[tuple[Path, Path]] = (),
        specification_input: Path | None = None,
    ) -> dict[str, object]:
        """Publish an approved specification and prepare the complete participant set.

        Args:
            common_prefix: Exact task common prefix.
            repository_root_list: Ordered repository root values.
            participating_submodule_list: Ordered participating submodule values.
            specification_input: Specification input.

        Returns:
            Canonical preparation result payload.
        """

        self._repair_report.reset()
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_store.optional_get(common_prefix)
            if state is not None and state.lifecycle_state != "repository_prepared":
                raise GoalLifecycleError("Participant set and specification can change only in repository_prepared")
            supplied_main_root_list = [self._git.root_get(item) for item in repository_root_list]
            if len(supplied_main_root_list) != len(set(supplied_main_root_list)):
                raise GoalLifecycleError("Task preparation repeats an implementation repository")
            requested_submodule_path_set_by_main_root_map = {str(item): set() for item in supplied_main_root_list}
            for raw_main_root, raw_path in participating_submodule_list:
                main_root = self._git.root_get(raw_main_root)
                if str(main_root) not in requested_submodule_path_set_by_main_root_map:
                    raise GoalLifecycleError("Task-owned submodule references a nonparticipating top-level repository")
                path_text = repository_relative_path_validate(
                    raw_path.as_posix(),
                    label="participating submodule path",
                )
                path_set = requested_submodule_path_set_by_main_root_map[str(main_root)]
                if path_text in path_set:
                    raise GoalLifecycleError(
                        f"Task-owned submodule is declared more than once: {main_root}:{path_text}"
                    )
                path_set.add(path_text)
            input_forbidden_root_list = [
                self._coordination.root,
                *supplied_main_root_list,
                *([Path(item.main_root) for item in state.repository_list] if state else []),
            ]
            specification_bytes = ordinary_task_artifact_input_get(
                specification_input,
                "specification",
                forbidden_root_list=input_forbidden_root_list,
            )
            task_directory = self._coordination.task_directory_get(common_prefix)
            if task_directory.exists():
                self._coordination.task_directory_shape_require(common_prefix, complete=False)
            if specification_bytes is not None:
                coordination_commit = self._coordination.publish(
                    common_prefix=common_prefix,
                    message=f"Prepare {common_prefix} specification",
                    relative_payload_by_path_map={f"{common_prefix}/spec.md": specification_bytes},
                    task_lock_already_held=True,
                )
            elif not (task_directory / "spec.md").is_file():
                raise GoalLifecycleError("New task preparation requires --specification-input")
            else:
                coordination_commit = self._coordination.synchronize_require()
            self._coordination.task_directory_shape_require(common_prefix, complete=False)
            existing_by_main_map = {item.main_root: item for item in state.repository_list} if state else {}
            missing_existing_root_set = set(existing_by_main_map) - {str(item) for item in supplied_main_root_list}
            if missing_existing_root_set:
                raise GoalLifecycleError(
                    "Prepare cannot remove or omit an existing implementation repository: "
                    + ", ".join(sorted(missing_existing_root_set))
                )
            repository_state_list = []
            for main_root in supplied_main_root_list:
                if main_root == self._coordination.root:
                    raise GoalLifecycleError("project-goals never receives a task branch or worktree")
                previous_repository = existing_by_main_map.get(str(main_root))
                previous_submodule_path_set = (
                    {item.path for item in previous_repository.task_owned_submodule_list}
                    if previous_repository is not None
                    else set()
                )
                requested_submodule_path_set = requested_submodule_path_set_by_main_root_map[str(main_root)]
                removed_submodule_path_set = previous_submodule_path_set - requested_submodule_path_set
                if removed_submodule_path_set:
                    raise GoalLifecycleError(
                        "Prepare cannot remove or omit an existing task-owned submodule: "
                        + ", ".join(sorted(removed_submodule_path_set))
                    )
                repository_state_list.append(
                    self._repository_manager.prepare(
                        main_root,
                        common_prefix=common_prefix,
                        requested_submodule_path_set=requested_submodule_path_set,
                        previous_state=previous_repository,
                    )
                )
            if not repository_state_list:
                raise GoalLifecycleError("Task must contain at least one implementation repository")
            repository_state_list.sort(key=lambda item: item.main_root)
            next_state = TaskState(
                common_prefix=common_prefix,
                cleanup_binding_generation=0,
                coordination_commit=coordination_commit,
                goals_repository=str(self._coordination.root),
                lifecycle_state="repository_prepared",
                provider_state_generation=(state.provider_state_generation + 1 if state else 1),
                repository_list=tuple(repository_state_list),
                sealed_goal_sha256="",
                sealed_spec_sha256=hashlib.sha256((task_directory / "spec.md").read_bytes()).hexdigest(),
            )
            self._state_store.write(next_state)
            self._repository_manager.pending_retire(next_state)
            next_state = self._validator.validate(next_state, required_state="repository_prepared")
            return self._result_payload_get(next_state)

    def revise(self, *, common_prefix: str) -> dict[str, object]:
        """Return one sealed inactive candidate to its existing preparation identity.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            One sealed inactive candidate to its existing preparation identity.
        """

        self._repair_report.reset()
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_store.get(common_prefix)
            if state.lifecycle_state == "active":
                raise GoalLifecycleError("Active goal artifacts and participant identity are immutable")
            if state.lifecycle_state == "repository_prepared":
                self._state_store.write(state)
                return self._result_payload_get(state)
            next_state = TaskState(
                common_prefix=state.common_prefix,
                cleanup_binding_generation=0,
                coordination_commit=self._coordination.synchronize_require(),
                goals_repository=state.goals_repository,
                lifecycle_state="repository_prepared",
                provider_state_generation=state.provider_state_generation + 1,
                repository_list=state.repository_list,
                sealed_goal_sha256="",
                sealed_spec_sha256=state.sealed_spec_sha256,
            )
            self._state_store.write(next_state)
            self._repository_manager.cleanup_binding_receipt_retire(next_state)
            return self._result_payload_get(next_state)

    def contracts_authored(
        self,
        *,
        common_prefix: str,
        goals_owner_input_by_path_map: Mapping[str, Path] | None = None,
    ) -> dict[str, object]:
        """Bind completed approved stable-owner authoring before semantic review.

        Args:
            common_prefix: Exact task common prefix.
            goals_owner_input_by_path_map: Goals owner input by path mapping.

        Returns:
            Updated manifest payload after stable-owner authoring.
        """

        self._repair_report.reset()
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_store.get(common_prefix)
            if state.lifecycle_state == "contracts_authored":
                self._state_store.write(state)
                state = self._validator.validate(state, required_state="contracts_authored")
                return self._result_payload_get(state)
            if state.lifecycle_state != "repository_prepared":
                raise GoalLifecycleError("contracts-authored requires repository_prepared")
            if goals_owner_input_by_path_map:
                self._coordination.publish(
                    common_prefix=common_prefix,
                    message=f"Author project-goals contracts for {common_prefix}",
                    relative_payload_by_path_map={
                        path: ordinary_task_artifact_input_require(
                            input_path,
                            "goals owner input",
                            forbidden_root_list=[
                                self._coordination.root,
                                *(Path(item.main_root) for item in state.repository_list),
                            ],
                        )
                        for path, input_path in goals_owner_input_by_path_map.items()
                    },
                    task_lock_already_held=True,
                )
            refreshed_repository_list = tuple(self._repository_manager.refresh(item) for item in state.repository_list)
            next_state = TaskState(
                common_prefix=state.common_prefix,
                cleanup_binding_generation=0,
                coordination_commit=self._coordination.synchronize_require(),
                goals_repository=state.goals_repository,
                lifecycle_state="contracts_authored",
                provider_state_generation=state.provider_state_generation + 1,
                repository_list=refreshed_repository_list,
                sealed_goal_sha256="",
                sealed_spec_sha256=hashlib.sha256(
                    self._coordination.file_bytes_get(common_prefix, "spec.md")
                ).hexdigest(),
            )
            self._state_store.write(next_state)
            next_state = self._validator.validate(next_state, required_state="contracts_authored")
            return self._result_payload_get(next_state)

    def seal(self, *, common_prefix: str, goal_input: Path | None = None) -> dict[str, object]:
        """Publish and bind one semantically reviewed inactive goal candidate.

        Args:
            common_prefix: Exact task common prefix.
            goal_input: Goal input.

        Returns:
            Updated manifest payload for the sealed goal candidate.
        """

        self._repair_report.reset()
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_store.get(common_prefix)
            if state.lifecycle_state == "goal_ready":
                if goal_input is not None:
                    raise GoalLifecycleError("Changing a sealed goal candidate requires revise")
                self._state_store.write(state)
                state = self._validator.validate(state, required_state="goal_ready")
                return self._result_payload_get(state)
            if state.lifecycle_state != "contracts_authored":
                raise GoalLifecycleError("seal requires contracts_authored")
            task_directory = self._coordination.task_directory_get(common_prefix)
            payload_by_path_map: dict[str, bytes | None] = {}
            goal_bytes = ordinary_task_artifact_input_get(
                goal_input,
                "goal",
                forbidden_root_list=[
                    self._coordination.root,
                    *(Path(item.main_root) for item in state.repository_list),
                ],
            )
            if goal_bytes is not None:
                payload_by_path_map[f"{common_prefix}/goal.md"] = goal_bytes
            elif not (task_directory / "goal.md").is_file():
                raise GoalLifecycleError("First seal requires --goal-input")
            checkpoint_path = task_directory / "checkpoint.yaml"
            if checkpoint_path.exists():
                if CheckpointDocument.from_payload(yaml_document_load(checkpoint_path)) != CheckpointDocument.empty():
                    raise GoalLifecycleError("An inactive goal candidate must have an empty checkpoint document")
            else:
                payload_by_path_map[f"{common_prefix}/checkpoint.yaml"] = yaml_document_bytes_get(
                    CheckpointDocument.empty().payload_get()
                )
            if payload_by_path_map:
                self._coordination.publish(
                    common_prefix=common_prefix,
                    message=f"Seal {common_prefix} goal candidate",
                    relative_payload_by_path_map=payload_by_path_map,
                    task_lock_already_held=True,
                )
            next_state = TaskState(
                common_prefix=state.common_prefix,
                cleanup_binding_generation=0,
                coordination_commit=self._coordination.synchronize_require(),
                goals_repository=state.goals_repository,
                lifecycle_state="goal_ready",
                provider_state_generation=state.provider_state_generation + 1,
                repository_list=state.repository_list,
                sealed_goal_sha256=hashlib.sha256(
                    self._coordination.file_bytes_get(common_prefix, "goal.md")
                ).hexdigest(),
                sealed_spec_sha256=hashlib.sha256(
                    self._coordination.file_bytes_get(common_prefix, "spec.md")
                ).hexdigest(),
            )
            self._state_store.write(next_state)
            next_state = self._validator.validate(next_state, required_state="goal_ready")
            return self._result_payload_get(next_state)

    def activate(self, *, common_prefix: str) -> dict[str, object]:
        """Freeze one successfully created persistent goal and bind cleanup receipts.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            Activated goal and cleanup-binding payload.
        """

        self._repair_report.reset()
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_store.get(common_prefix)
            if state.lifecycle_state == "active":
                self._state_store.write(state)
                self._repository_manager.cleanup_binding_receipt_ensure(state)
                state = self._validator.validate(state, required_state="active")
                return self._result_payload_get(state)
            if state.lifecycle_state != "goal_ready":
                raise GoalLifecycleError("activate requires goal_ready")
            state = self._validator.validate(state, required_state="goal_ready")
            next_state = replace(
                state,
                cleanup_binding_generation=state.provider_state_generation + 1,
                lifecycle_state="active",
                provider_state_generation=state.provider_state_generation + 1,
            )
            self._state_store.write(next_state)
            self._repository_manager.cleanup_binding_receipt_ensure(next_state)
            next_state = self._validator.validate(next_state, required_state="active")
            return self._result_payload_get(next_state)

    def recover_main_leak(
        self,
        *,
        common_prefix: str,
        main_repository: Path,
        path_list: Sequence[str],
    ) -> dict[str, object]:
        """Recover the complete caller-attested uncommitted task leak in one main owner.

        Args:
            common_prefix: Exact task common prefix.
            main_repository: Main repository.
            path_list: Ordered path values.

        Returns:
            Recovery result payload for the selected main owner.
        """

        self._repair_report.reset()
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_store.get(common_prefix)
            self._repository_manager.main_leak_recover(
                state,
                main_repository=main_repository,
                path_list=list(path_list),
            )
            state = self._validator.validate(state, required_state=state.lifecycle_state)
            self._repair_report.record(f"main-worktree-leak-recovered:{Path(main_repository)}")
            return self._result_payload_get(state)

    def accept_main_commit_drift(
        self,
        *,
        common_prefix: str,
        main_repository: Path,
        commit: str,
        path_list: Sequence[str],
    ) -> dict[str, object]:
        """Persist one exact explicit attestation for committed overlapping main work.

        Args:
            common_prefix: Exact task common prefix.
            main_repository: Main repository.
            commit: Commit.
            path_list: Ordered path values.

        Returns:
            Persisted main-drift attestation payload.
        """

        self._repair_report.reset()
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_store.get(common_prefix)
            repository_list = self._repository_manager.main_commit_drift_accept(
                state,
                main_repository=main_repository,
                commit=commit,
                path_list=list(path_list),
            )
            next_state = replace(
                state,
                provider_state_generation=state.provider_state_generation + 1,
                repository_list=repository_list,
            )
            self._state_store.write(next_state)
            next_state = self._validator.validate(next_state, required_state=next_state.lifecycle_state)
            return self._result_payload_get(next_state)

    def validate(self, *, common_prefix: str, required_state: str) -> dict[str, object]:
        """Run complete validation against one required lifecycle floor.

        Args:
            common_prefix: Exact task common prefix.
            required_state: Required state.

        Returns:
            Validated task-state payload at the requested lifecycle floor.
        """

        self._repair_report.reset()
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_store.get(common_prefix)
            state = self._validator.validate(state, required_state=required_state)
            return self._result_payload_get(state)

    def _result_payload_get(self, state: TaskState) -> dict[str, object]:
        """Return one command result with every operation-local repair exactly once.

        Args:
            state: Exact runtime state.

        Returns:
            One command result with every operation-local repair exactly once.
        """

        return state.result_payload_get(performed_repair_list=self._repair_report.payload_get())
