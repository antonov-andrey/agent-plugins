"""Central-task preparation and isolated implementation-worktree sequencing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

from goal_lifecycle.cleanup_manifest import (
    BOOTSTRAP_MANIFEST_NAME,
    BootstrapManifest,
    bootstrap_manifest_load,
    cleanup_binding_receipt_path_get,
    cleanup_binding_receipt_validate,
    cleanup_binding_receipt_write,
)
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_bytes_write, atomic_json_write, json_object_load
from goal_lifecycle.main_integrity import MainWorktreeIntegrity
from goal_lifecycle.model import BootstrapResourceState, RepositoryState, TaskState, common_prefix_validate
from goal_lifecycle.resource import BootstrapResourceManager
from goal_lifecycle.yaml_document import yaml_document_bytes_get

_EMPTY_MANIFEST_PAYLOAD = {
    "schema_version": 2,
    "resource": {
        "copy_optional_path_list": [],
        "copy_required_path_list": [],
        "link_optional_path_list": [],
        "link_required_path_list": [],
    },
}
_LIFECYCLE_INDEX_BY_NAME_MAP = {
    "repository_prepared": 0,
    "contracts_authored": 1,
    "goal_ready": 2,
    "active": 3,
}


class GoalWorktreeWorkflow:
    """Own central task artifacts and implementation worktree preparation."""

    def __init__(self, goals_repository: Path, *, git: Git | None = None) -> None:
        self._git = git or Git()
        self._coordination = CoordinationRepository(goals_repository, git=self._git)
        self._main_integrity = MainWorktreeIntegrity(git=self._git)
        self._resource_manager = BootstrapResourceManager(git=self._git)

    def prepare(
        self,
        *,
        common_prefix: str,
        repository_root_list: Sequence[Path],
        specification_input: Path | None = None,
    ) -> dict[str, object]:
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_optional_get(common_prefix)
            if state is not None and state.lifecycle_state != "repository_prepared":
                raise GoalLifecycleError("Participant set and specification can change only in repository_prepared")
            supplied_main_root_list = [self._git.root_get(item) for item in repository_root_list]
            input_forbidden_root_list = [
                self._coordination.root,
                *supplied_main_root_list,
                *([Path(item.main_root) for item in state.repository_list] if state else []),
            ]
            specification_bytes = self._ordinary_input_get(
                specification_input,
                "specification",
                forbidden_root_list=input_forbidden_root_list,
            )
            task_directory = self._coordination.task_directory_get(common_prefix)
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
            existing_by_main_map = {item.main_root: item for item in state.repository_list} if state else {}
            repository_state_list = list(state.repository_list) if state else []
            for main_root in supplied_main_root_list:
                if main_root == self._coordination.root:
                    raise GoalLifecycleError("project-goals never receives a task branch or worktree")
                if str(main_root) in existing_by_main_map:
                    continue
                repository_state_list.append(self._repository_prepare(main_root, common_prefix=common_prefix))
            if not repository_state_list:
                raise GoalLifecycleError("Task must contain at least one implementation repository")
            repository_state_list.sort(key=lambda item: item.main_root)
            specification_sha256 = hashlib.sha256((task_directory / "spec.md").read_bytes()).hexdigest()
            next_state = TaskState(
                common_prefix=common_prefix,
                cleanup_binding_generation=0,
                coordination_commit=coordination_commit,
                goals_repository=str(self._coordination.root),
                lifecycle_state="repository_prepared",
                provider_state_generation=(state.provider_state_generation + 1 if state else 1),
                repository_list=tuple(repository_state_list),
                sealed_goal_sha256="",
                sealed_spec_sha256=specification_sha256,
            )
            self._state_write(next_state)
            self._validate(next_state, required_state="repository_prepared", sealed_files=False)
            return self._result_get(next_state)

    def revise(self, *, common_prefix: str) -> dict[str, object]:
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_get(common_prefix)
            if state.lifecycle_state == "active":
                raise GoalLifecycleError("Active goal artifacts and participant identity are immutable")
            if state.lifecycle_state == "repository_prepared":
                self._state_write(state)
                return self._result_get(state)
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
            self._state_write(next_state)
            for repository in next_state.repository_list:
                receipt_path = cleanup_binding_receipt_path_get(
                    Path(repository.task_root),
                    common_prefix=common_prefix,
                    git=self._git,
                )
                try:
                    receipt_path.unlink()
                except FileNotFoundError:
                    pass
            return self._result_get(next_state)

    def contracts_authored(
        self,
        *,
        common_prefix: str,
        goals_owner_input_by_path_map: Mapping[str, Path] | None = None,
    ) -> dict[str, object]:
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_get(common_prefix)
            if state.lifecycle_state == "contracts_authored":
                self._state_write(state)
                self._validate(state, required_state="contracts_authored", sealed_files=False)
                return self._result_get(state)
            if state.lifecycle_state != "repository_prepared":
                raise GoalLifecycleError("contracts-authored requires repository_prepared")
            if goals_owner_input_by_path_map:
                self._coordination.publish(
                    common_prefix=common_prefix,
                    message=f"Author project-goals contracts for {common_prefix}",
                    relative_payload_by_path_map={
                        path: self._ordinary_input_require(
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
            refreshed_repository_list = tuple(self._repository_state_refresh(item) for item in state.repository_list)
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
            self._state_write(next_state)
            self._validate(next_state, required_state="contracts_authored", sealed_files=False)
            return self._result_get(next_state)

    def seal(self, *, common_prefix: str, goal_input: Path | None = None) -> dict[str, object]:
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_get(common_prefix)
            if state.lifecycle_state == "goal_ready":
                if goal_input is not None:
                    raise GoalLifecycleError("Changing a sealed goal candidate requires revise")
                self._state_write(state)
                self._validate(state, required_state="goal_ready", sealed_files=True)
                return self._result_get(state)
            if state.lifecycle_state != "contracts_authored":
                raise GoalLifecycleError("seal requires contracts_authored")
            task_directory = self._coordination.task_directory_get(common_prefix)
            payload_by_path_map: dict[str, bytes | None] = {}
            goal_bytes = self._ordinary_input_get(
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
            if not checkpoint_path.exists():
                payload_by_path_map[f"{common_prefix}/checkpoint.yaml"] = yaml_document_bytes_get(
                    {
                        "schema_version": 1,
                        "accepted_checkpoint_id": "",
                        "checkpoint_list": [],
                    }
                )
            if payload_by_path_map:
                self._coordination.publish(
                    common_prefix=common_prefix,
                    message=f"Seal {common_prefix} goal candidate",
                    relative_payload_by_path_map=payload_by_path_map,
                    task_lock_already_held=True,
                )
            goal_sha256 = hashlib.sha256(self._coordination.file_bytes_get(common_prefix, "goal.md")).hexdigest()
            spec_sha256 = hashlib.sha256(self._coordination.file_bytes_get(common_prefix, "spec.md")).hexdigest()
            next_state = TaskState(
                common_prefix=state.common_prefix,
                cleanup_binding_generation=0,
                coordination_commit=self._coordination.synchronize_require(),
                goals_repository=state.goals_repository,
                lifecycle_state="goal_ready",
                provider_state_generation=state.provider_state_generation + 1,
                repository_list=state.repository_list,
                sealed_goal_sha256=goal_sha256,
                sealed_spec_sha256=spec_sha256,
            )
            self._state_write(next_state)
            self._validate(next_state, required_state="goal_ready", sealed_files=True)
            return self._result_get(next_state)

    def activate(self, *, common_prefix: str) -> dict[str, object]:
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_get(common_prefix)
            if state.lifecycle_state == "active":
                self._state_write(state)
                self._cleanup_binding_receipt_ensure(state)
                self._validate(state, required_state="active", sealed_files=True)
                return self._result_get(state)
            if state.lifecycle_state != "goal_ready":
                raise GoalLifecycleError("activate requires goal_ready")
            self._validate(state, required_state="goal_ready", sealed_files=True)
            next_state = TaskState(
                common_prefix=state.common_prefix,
                cleanup_binding_generation=state.provider_state_generation + 1,
                coordination_commit=state.coordination_commit,
                goals_repository=state.goals_repository,
                lifecycle_state="active",
                provider_state_generation=state.provider_state_generation + 1,
                repository_list=state.repository_list,
                sealed_goal_sha256=state.sealed_goal_sha256,
                sealed_spec_sha256=state.sealed_spec_sha256,
            )
            self._state_write(next_state)
            self._cleanup_binding_receipt_ensure(next_state)
            self._validate(next_state, required_state="active", sealed_files=True)
            return self._result_get(next_state)

    def recover_main_leak(
        self,
        *,
        common_prefix: str,
        main_repository: Path,
        path_list: Sequence[str],
    ) -> dict[str, object]:
        """Recover the complete caller-attested uncommitted task leak in one main owner."""

        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_get(common_prefix)
            repository = self._repository_by_main_get(state, main_repository)
            self._main_integrity.leak_recover(repository, path_list=path_list)
            self._validate(state, required_state=state.lifecycle_state, sealed_files=self._is_sealed(state))
            return self._result_get(state)

    def accept_main_commit_drift(
        self,
        *,
        common_prefix: str,
        main_repository: Path,
        commit: str,
        path_list: Sequence[str],
    ) -> dict[str, object]:
        """Persist one exact explicit attestation for committed overlapping main work."""

        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_get(common_prefix)
            repository = self._repository_by_main_get(state, main_repository)
            accepted = self._main_integrity.commit_drift_accept(
                repository,
                commit=commit,
                path_list=path_list,
            )
            repository_list = tuple(
                accepted if item.main_root == repository.main_root else item for item in state.repository_list
            )
            next_state = replace(
                state,
                provider_state_generation=state.provider_state_generation + 1,
                repository_list=repository_list,
            )
            self._state_write(next_state)
            self._validate(
                next_state,
                required_state=next_state.lifecycle_state,
                sealed_files=self._is_sealed(next_state),
            )
            return self._result_get(next_state)

    def validate(self, *, common_prefix: str, required_state: str) -> dict[str, object]:
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix):
            state = self._state_get(common_prefix)
            self._validate(
                state,
                required_state=required_state,
                sealed_files=_LIFECYCLE_INDEX_BY_NAME_MAP[state.lifecycle_state]
                >= _LIFECYCLE_INDEX_BY_NAME_MAP["goal_ready"],
            )
            return self._result_get(state)

    def _repository_prepare(self, main_root: Path, *, common_prefix: str) -> RepositoryState:
        self._git.clean_require(main_root)
        if self._git.branch_get(main_root) != "main":
            raise GoalLifecycleError(f"Implementation preparation requires canonical main checkout: {main_root}")
        self._git.fetch(main_root)
        baseline = self._git.commit_get(main_root)
        if baseline != self._git.commit_get(main_root, "refs/remotes/origin/main"):
            raise GoalLifecycleError(f"Implementation main must equal origin/main: {main_root}")
        self._main_worktree_exclude_ensure(main_root)
        task_root = main_root / ".worktree" / common_prefix
        branch_ref_result = self._git.run(
            main_root, ["show-ref", "--verify", f"refs/heads/{common_prefix}"], check=False
        )
        if task_root.exists():
            if self._git.root_get(task_root) != task_root.resolve(strict=True):
                raise GoalLifecycleError(f"Task path is not a registered worktree: {task_root}")
        elif branch_ref_result.returncode == 0:
            self._git.run(main_root, ["worktree", "add", str(task_root), common_prefix])
        else:
            self._git.run(main_root, ["worktree", "add", "-b", common_prefix, str(task_root), baseline])
        if self._git.branch_get(task_root) != common_prefix:
            raise GoalLifecycleError(f"Task worktree has another branch: {task_root}")
        manifest_path = task_root / BOOTSTRAP_MANIFEST_NAME
        if not manifest_path.exists():
            atomic_bytes_write(manifest_path, yaml_document_bytes_get(_EMPTY_MANIFEST_PAYLOAD), mode=0o644)
        manifest = bootstrap_manifest_load(manifest_path)
        resource_state_list = self._resource_manager.materialize(
            main_root=main_root,
            task_root=task_root,
            manifest=manifest,
        )
        self._worktree_ignore_ensure(task_root)
        return self._repository_state_get(
            main_root=main_root,
            task_root=task_root,
            manifest=manifest,
            resource_state_list=resource_state_list,
        )

    def _repository_state_refresh(self, state: RepositoryState) -> RepositoryState:
        state = self._main_integrity.refresh_if_independent(state)
        main_root = Path(state.main_root)
        task_root = Path(state.task_root)
        manifest = bootstrap_manifest_load(task_root / BOOTSTRAP_MANIFEST_NAME)
        resource_state_list = self._resource_manager.materialize(
            main_root=main_root,
            task_root=task_root,
            manifest=manifest,
        )
        return self._repository_state_get(
            main_root=main_root,
            task_root=task_root,
            manifest=manifest,
            baseline_commit=state.baseline_commit,
            previous_state=state,
            resource_state_list=resource_state_list,
        )

    def _repository_state_get(
        self,
        *,
        main_root: Path,
        task_root: Path,
        manifest: BootstrapManifest,
        baseline_commit: str | None = None,
        previous_state: RepositoryState | None = None,
        resource_state_list: tuple[BootstrapResourceState, ...] = (),
    ) -> RepositoryState:
        return RepositoryState(
            accepted_main_commit_drift_list=(previous_state.accepted_main_commit_drift_list if previous_state else ()),
            baseline_commit=baseline_commit or self._git.commit_get(main_root),
            branch_name=self._git.branch_get(task_root),
            cleanup_declaration_sha256=(manifest.cleanup.normalized_sha256_get() if manifest.cleanup else ""),
            main_root=str(main_root),
            main_commit=(previous_state.main_commit if previous_state else self._git.commit_get(main_root)),
            manifest_sha256=manifest.sha256,
            origin_url=self._git.origin_url_get(main_root),
            resource_state_list=resource_state_list,
            task_root=str(task_root),
        )

    def _worktree_ignore_ensure(self, task_root: Path) -> None:
        gitignore_path = task_root / ".gitignore"
        text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.is_file() else ""
        line_list = text.splitlines()
        if "/.worktree/" not in line_list:
            line_list.append("/.worktree/")
            atomic_bytes_write(gitignore_path, ("\n".join(line_list).strip() + "\n").encode(), mode=0o644)

    def _main_worktree_exclude_ensure(self, main_root: Path) -> None:
        """Hide only the provider-owned worktree container until tracked ignore merges."""

        exclude_path = self._git.common_directory_get(main_root) / "info" / "exclude"
        text = exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
        line_list = text.splitlines()
        if "/.worktree/" not in line_list:
            line_list.append("/.worktree/")
            atomic_bytes_write(exclude_path, ("\n".join(line_list).strip() + "\n").encode(), mode=0o644)

    def _validate(self, state: TaskState, *, required_state: str, sealed_files: bool) -> None:
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
        if sealed_files:
            if hashlib.sha256(self._coordination.file_bytes_get(state.common_prefix, "spec.md")).hexdigest() != (
                state.sealed_spec_sha256
            ) or hashlib.sha256(self._coordination.file_bytes_get(state.common_prefix, "goal.md")).hexdigest() != (
                state.sealed_goal_sha256
            ):
                raise GoalLifecycleError("Sealed task artifacts changed")
        for repository in state.repository_list:
            main_root = Path(repository.main_root).resolve(strict=True)
            task_root = Path(repository.task_root).resolve(strict=True)
            if self._git.root_get(task_root) != task_root or self._git.branch_get(task_root) != state.common_prefix:
                raise GoalLifecycleError(f"Recorded task worktree identity differs: {task_root}")
            if self._git.origin_url_get(main_root) != repository.origin_url:
                raise GoalLifecycleError(f"Repository origin changed: {main_root}")
            self._main_integrity.validate(repository)
            self._git.ancestor_require(
                task_root,
                repository.baseline_commit,
                self._git.commit_get(task_root),
                label=f"{task_root.name} baseline relation",
            )
            manifest = bootstrap_manifest_load(task_root / BOOTSTRAP_MANIFEST_NAME)
            if manifest.sha256 != repository.manifest_sha256:
                raise GoalLifecycleError(f"Bootstrap manifest changed after lifecycle binding: {task_root}")
            self._resource_manager.validate(
                main_root=main_root,
                task_root=task_root,
                state_list=repository.resource_state_list,
            )
            if state.lifecycle_state == "active":
                cleanup_binding_receipt_validate(
                    task_root,
                    common_prefix=state.common_prefix,
                    provider_state_generation=state.cleanup_binding_generation,
                    sealed_specification_sha256=state.sealed_spec_sha256,
                    git=self._git,
                )
            replica = TaskState.from_payload(
                json_object_load(
                    self._git.common_directory_get(task_root)
                    / "agent-workflows"
                    / "task"
                    / state.common_prefix
                    / "state.json",
                    label="replicated task private state",
                )
            )
            if replica != state:
                raise GoalLifecycleError(f"Private task-state replicas differ: {task_root}")

    def _repository_by_main_get(self, state: TaskState, main_repository: Path) -> RepositoryState:
        main_root = self._git.root_get(main_repository)
        for repository in state.repository_list:
            if Path(repository.main_root).resolve(strict=True) == main_root:
                return repository
        raise GoalLifecycleError(f"Repository is not a participant in this task: {main_root}")

    @staticmethod
    def _is_sealed(state: TaskState) -> bool:
        return _LIFECYCLE_INDEX_BY_NAME_MAP[state.lifecycle_state] >= _LIFECYCLE_INDEX_BY_NAME_MAP["goal_ready"]

    def _state_optional_get(self, common_prefix: str) -> TaskState | None:
        path = self._coordination.state_path_get(common_prefix)
        if not path.exists():
            return None
        return TaskState.from_payload(json_object_load(path, label="task private state"))

    def _state_get(self, common_prefix: str) -> TaskState:
        state = self._state_optional_get(common_prefix)
        if state is None:
            raise GoalLifecycleError(f"Task private state does not exist: {common_prefix}")
        return state

    def _state_write(self, state: TaskState) -> None:
        payload = state.payload_get()
        coordination_path = self._coordination.state_path_get(state.common_prefix)
        path_set: set[Path] = set()
        for repository in state.repository_list:
            common_directory = self._git.common_directory_get(Path(repository.task_root))
            path_set.add(common_directory / "agent-workflows" / "task" / state.common_prefix / "state.json")
        # Replicas are prepared first and the coordination copy is the commit marker.
        # A crash can therefore leave replicas one generation ahead, but the next
        # idempotent lifecycle invocation always resumes from the authoritative
        # coordination generation and converges every replica before returning.
        for path in sorted(path_set):
            atomic_json_write(path, payload)
        atomic_json_write(coordination_path, payload)

    def _cleanup_binding_receipt_ensure(self, state: TaskState) -> None:
        """Create every active-state receipt idempotently after durable activation."""

        if state.lifecycle_state != "active" or state.cleanup_binding_generation < 1:
            raise GoalLifecycleError("Cleanup binding receipts require durable active state")
        for repository in state.repository_list:
            cleanup_binding_receipt_write(
                Path(repository.task_root),
                common_prefix=state.common_prefix,
                provider_state_generation=state.cleanup_binding_generation,
                sealed_specification_sha256=state.sealed_spec_sha256,
                git=self._git,
            )

    @staticmethod
    def _ordinary_input_get(
        path: Path | None,
        label: str,
        *,
        forbidden_root_list: Sequence[Path],
    ) -> bytes | None:
        return (
            None
            if path is None
            else GoalWorktreeWorkflow._ordinary_input_require(
                path,
                label,
                forbidden_root_list=forbidden_root_list,
            )
        )

    @staticmethod
    def _ordinary_input_require(
        path: Path,
        label: str,
        *,
        forbidden_root_list: Sequence[Path],
    ) -> bytes:
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise GoalLifecycleError(f"{label} must be one ordinary single-link file outside repository trees")
        resolved = path.resolve(strict=True)
        for root in forbidden_root_list:
            resolved_root = root.resolve(strict=True)
            if resolved == resolved_root or resolved_root in resolved.parents:
                raise GoalLifecycleError(f"{label} must be outside every participating repository tree")
        try:
            payload = path.read_bytes()
            payload.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise GoalLifecycleError(f"{label} must contain UTF-8 bytes") from error
        return payload

    @staticmethod
    def _result_get(state: TaskState) -> dict[str, object]:
        return {
            "schema_version": 1,
            "common_prefix": state.common_prefix,
            "coordination_repository": state.goals_repository,
            "lifecycle_state": state.lifecycle_state,
            "task_root_list": [item.task_root for item in state.repository_list],
        }
