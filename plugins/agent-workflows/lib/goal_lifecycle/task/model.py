"""Closed private state model for goal task repositories and bootstrap resources."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.identity import (
    commit_validate,
    common_prefix_validate,
    repository_relative_path_validate,
)

_SEALED_TASK_LIFECYCLE_STATE_SET = frozenset({"goal_ready", "active"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class BootstrapResourceState:
    """Sealed materialization state of one declared bootstrap resource."""

    path: str
    resource_class: str
    skipped: bool
    source_fingerprint: str
    task_fingerprint: str

    @classmethod
    def from_payload(cls, payload: object) -> "BootstrapResourceState":
        """Build one bootstrap-resource state from its exact payload.

        Args:
            payload: Structured operation payload.

        Returns:
            One bootstrap-resource state from its exact payload.
        """

        expected = {
            "path",
            "resource_class",
            "skipped",
            "source_fingerprint",
            "task_fingerprint",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise GoalLifecycleError("Bootstrap resource state has another shape")
        resource_class = payload["resource_class"]
        if resource_class not in {
            "copy_optional_path_list",
            "copy_required_path_list",
            "link_optional_path_list",
            "link_required_path_list",
        }:
            raise GoalLifecycleError("Bootstrap resource state has an unknown class")
        skipped = payload["skipped"]
        if not isinstance(skipped, bool):
            raise GoalLifecycleError("Bootstrap resource skipped marker must be boolean")
        source_fingerprint = payload["source_fingerprint"]
        task_fingerprint = payload["task_fingerprint"]
        if any(
            not isinstance(value, str) or (value and len(value) != 64)
            for value in (source_fingerprint, task_fingerprint)
        ):
            raise GoalLifecycleError("Bootstrap resource fingerprints are malformed")
        if skipped != (source_fingerprint == task_fingerprint == ""):
            raise GoalLifecycleError("Bootstrap resource skipped state and fingerprints disagree")
        return cls(
            path=repository_relative_path_validate(payload["path"], label="bootstrap resource state path"),
            resource_class=resource_class,
            skipped=skipped,
            source_fingerprint=source_fingerprint,
            task_fingerprint=task_fingerprint,
        )


@dataclass(frozen=True, slots=True)
class MainCommitDriftAttestation:
    """One user-approved overlapping main commit and its complete path set."""

    commit: str
    path_list: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: object) -> "MainCommitDriftAttestation":
        """Build one main-drift attestation from its exact payload.

        Args:
            payload: Structured operation payload.

        Returns:
            One main-drift attestation from its exact payload.
        """

        if not isinstance(payload, dict) or set(payload) != {"commit", "path_list"}:
            raise GoalLifecycleError("Main-commit drift attestation has another shape")
        raw_path_list = payload["path_list"]
        if not isinstance(raw_path_list, list) or not raw_path_list:
            raise GoalLifecycleError("Main-commit drift attestation requires a non-empty path_list")
        path_list = tuple(
            repository_relative_path_validate(item, label="attested overlap path") for item in raw_path_list
        )
        if list(path_list) != sorted(path_list) or len(path_list) != len(set(path_list)):
            raise GoalLifecycleError("Attested overlap paths must be unique and sorted")
        return cls(
            commit=commit_validate(payload["commit"], label="attested main commit"),
            path_list=path_list,
        )


@dataclass(frozen=True, slots=True)
class RepositoryBoundaryState:
    """Private identity shared by a top-level or task-owned repository boundary."""

    baseline_commit: str
    branch_name: str
    main_commit: str
    origin_url: str
    main_root: str
    manifest_sha256: str
    task_root: str
    accepted_main_commit_drift_list: tuple[MainCommitDriftAttestation, ...] = ()
    resource_state_list: tuple[BootstrapResourceState, ...] = ()
    cleanup_declaration_sha256: str = ""

    def main_commit_drift_attestation_get(self, commit: str) -> MainCommitDriftAttestation | None:
        """Return the newest exact attestation for one main commit.

        Args:
            commit: Commit.

        Returns:
            The newest exact attestation for one main commit.
        """

        for item in reversed(self.accepted_main_commit_drift_list):
            if item.commit == commit:
                return item
        return None

    @classmethod
    def from_payload(cls, payload: object) -> "RepositoryBoundaryState":
        """Build one repository-boundary state from its exact payload.

        Args:
            payload: Structured operation payload.

        Returns:
            One repository-boundary state from its exact payload.
        """

        expected = {
            "accepted_main_commit_drift_list",
            "baseline_commit",
            "branch_name",
            "cleanup_declaration_sha256",
            "main_commit",
            "main_root",
            "manifest_sha256",
            "origin_url",
            "resource_state_list",
            "task_root",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise GoalLifecycleError("Private repository state has another shape")
        accepted_main_commit_drift_list = tuple(
            MainCommitDriftAttestation.from_payload(item)
            for item in _list_require(
                payload["accepted_main_commit_drift_list"],
                label="accepted_main_commit_drift_list",
            )
        )
        if len({item.commit for item in accepted_main_commit_drift_list}) != len(accepted_main_commit_drift_list):
            raise GoalLifecycleError("Private repository state repeats a main-drift attestation")
        resource_state_list = tuple(
            BootstrapResourceState.from_payload(item)
            for item in _list_require(payload["resource_state_list"], label="resource_state_list")
        )
        if list(resource_state_list) != sorted(
            resource_state_list,
            key=lambda item: (item.path, item.resource_class),
        ) or len({(item.path, item.resource_class) for item in resource_state_list}) != len(resource_state_list):
            raise GoalLifecycleError("Bootstrap resource states must be unique and sorted")
        branch_name = _nonempty_text_validate(payload["branch_name"], label="branch_name")
        cleanup_declaration_sha256 = _sha256_validate(
            payload["cleanup_declaration_sha256"],
            label="cleanup_declaration_sha256",
            empty_allowed=True,
        )
        main_root = _absolute_path_validate(payload["main_root"], label="main_root")
        task_root = _absolute_path_validate(payload["task_root"], label="task_root")
        if main_root == task_root:
            raise GoalLifecycleError("Private repository main_root and task_root must differ")
        return cls(
            accepted_main_commit_drift_list=accepted_main_commit_drift_list,
            baseline_commit=commit_validate(payload["baseline_commit"], label="baseline_commit"),
            branch_name=branch_name,
            cleanup_declaration_sha256=cleanup_declaration_sha256,
            main_commit=commit_validate(payload["main_commit"], label="main_commit"),
            main_root=main_root,
            manifest_sha256=_sha256_validate(payload["manifest_sha256"], label="manifest_sha256"),
            origin_url=_nonempty_text_validate(payload["origin_url"], label="origin_url"),
            resource_state_list=resource_state_list,
            task_root=task_root,
        )


@dataclass(frozen=True, slots=True)
class TaskOwnedSubmoduleState:
    """One explicitly task-owned recursive submodule and its repository boundary."""

    path: str
    repository: RepositoryBoundaryState

    @classmethod
    def from_payload(cls, payload: object) -> "TaskOwnedSubmoduleState":
        """Build one task-owned submodule state from its exact payload.

        Args:
            payload: Structured operation payload.

        Returns:
            One task-owned submodule state from its exact payload.
        """

        if not isinstance(payload, dict) or set(payload) != {"path", "repository"}:
            raise GoalLifecycleError("Task-owned submodule state has another shape")
        return cls(
            path=repository_relative_path_validate(payload["path"], label="task-owned submodule path"),
            repository=RepositoryBoundaryState.from_payload(payload["repository"]),
        )


@dataclass(frozen=True, slots=True)
class SubmoduleGitlinkState:
    """One recursive submodule path and its preparation-time gitlink."""

    path: str
    baseline_commit: str

    @classmethod
    def from_payload(cls, payload: object) -> "SubmoduleGitlinkState":
        """Build one recursive gitlink state from its exact payload.

        Args:
            payload: Structured operation payload.

        Returns:
            One recursive gitlink state from its exact payload.
        """

        if not isinstance(payload, dict) or set(payload) != {"baseline_commit", "path"}:
            raise GoalLifecycleError("Recursive submodule gitlink state has another shape")
        return cls(
            baseline_commit=commit_validate(payload["baseline_commit"], label="submodule baseline commit"),
            path=repository_relative_path_validate(payload["path"], label="recursive submodule path"),
        )


@dataclass(frozen=True, slots=True)
class RepositoryState(RepositoryBoundaryState):
    """Private identity of one top-level implementation worktree and delegated submodules."""

    submodule_gitlink_list: tuple[SubmoduleGitlinkState, ...] = ()
    task_owned_submodule_list: tuple[TaskOwnedSubmoduleState, ...] = ()

    @classmethod
    def from_payload(cls, payload: object) -> "RepositoryState":
        """Build one top-level repository state from its exact current shape.

        Args:
            payload: Structured operation payload.

        Returns:
            One top-level repository state from its exact current shape.
        """

        if not isinstance(payload, dict):
            raise GoalLifecycleError("Private repository state has another shape")
        expected = {
            "accepted_main_commit_drift_list",
            "baseline_commit",
            "branch_name",
            "cleanup_declaration_sha256",
            "main_commit",
            "main_root",
            "manifest_sha256",
            "origin_url",
            "resource_state_list",
            "submodule_gitlink_list",
            "task_owned_submodule_list",
            "task_root",
        }
        if set(payload) != expected:
            raise GoalLifecycleError("Private repository state has another shape")
        boundary = RepositoryBoundaryState.from_payload(
            {
                key: value
                for key, value in payload.items()
                if key not in {"submodule_gitlink_list", "task_owned_submodule_list"}
            }
        )
        task_owned_submodule_list = tuple(
            TaskOwnedSubmoduleState.from_payload(item)
            for item in _list_require(
                payload["task_owned_submodule_list"],
                label="task_owned_submodule_list",
            )
        )
        submodule_gitlink_list = tuple(
            SubmoduleGitlinkState.from_payload(item)
            for item in _list_require(payload["submodule_gitlink_list"], label="submodule_gitlink_list")
        )
        gitlink_path_list = [item.path for item in submodule_gitlink_list]
        if gitlink_path_list != sorted(gitlink_path_list) or len(gitlink_path_list) != len(set(gitlink_path_list)):
            raise GoalLifecycleError("Recursive submodule gitlinks must be unique and sorted by path")
        path_list = [item.path for item in task_owned_submodule_list]
        if path_list != sorted(path_list, key=lambda item: (len(Path(item).parts), item)) or len(path_list) != len(
            set(path_list)
        ):
            raise GoalLifecycleError("Task-owned submodules must be unique and sorted by depth and path")
        main_root = Path(boundary.main_root)
        task_root = Path(boundary.task_root)
        for item in task_owned_submodule_list:
            if item.path not in set(gitlink_path_list):
                raise GoalLifecycleError("Task-owned submodule is absent from the recursive gitlink set")
            if (
                Path(item.repository.main_root) != main_root / item.path
                or Path(item.repository.task_root) != task_root / item.path
            ):
                raise GoalLifecycleError("Task-owned submodule roots differ from their recorded recursive path")
            if item.repository.branch_name != boundary.branch_name:
                raise GoalLifecycleError("Task-owned submodule branch differs from the top-level task branch")
        return cls(
            accepted_main_commit_drift_list=boundary.accepted_main_commit_drift_list,
            baseline_commit=boundary.baseline_commit,
            branch_name=boundary.branch_name,
            cleanup_declaration_sha256=boundary.cleanup_declaration_sha256,
            main_commit=boundary.main_commit,
            main_root=boundary.main_root,
            manifest_sha256=boundary.manifest_sha256,
            origin_url=boundary.origin_url,
            resource_state_list=boundary.resource_state_list,
            submodule_gitlink_list=submodule_gitlink_list,
            task_owned_submodule_list=task_owned_submodule_list,
            task_root=boundary.task_root,
        )


@dataclass(frozen=True, slots=True)
class TaskState:
    """Replicated private state required by checkpoint, merge, and deletion."""

    common_prefix: str
    cleanup_binding_generation: int
    coordination_commit: str
    goals_repository: str
    lifecycle_state: str
    provider_state_generation: int
    repository_list: tuple[RepositoryState, ...]
    sealed_goal_sha256: str = ""
    sealed_spec_sha256: str = ""
    schema_version: int = 3

    def is_sealed(self) -> bool:
        """Return whether specification and goal bytes are immutable.

        Returns:
            Whether specification and goal bytes are immutable.
        """

        return self.lifecycle_state in _SEALED_TASK_LIFECYCLE_STATE_SET

    def result_payload_get(self, *, performed_repair_list: list[str] | None = None) -> dict[str, object]:
        """Return the stable user-facing task lifecycle result.

        Args:
            performed_repair_list: Ordered performed repair values.

        Returns:
            The stable user-facing task lifecycle result.
        """

        return {
            "schema_version": 1,
            "common_prefix": self.common_prefix,
            "coordination_commit": self.coordination_commit,
            "coordination_path": str(Path(self.goals_repository) / self.common_prefix),
            "coordination_repository": self.goals_repository,
            "lifecycle_state": self.lifecycle_state,
            "performed_repair_list": list(performed_repair_list or []),
            "skipped_optional_resource_list": sorted(
                f"{boundary.task_root}:{resource.path}"
                for boundary in repository_boundary_list_get(self)
                for resource in boundary.resource_state_list
                if resource.skipped
            ),
            "task_root_list": [item.task_root for item in self.repository_list],
            "participating_submodule_root_list": [
                submodule.repository.task_root
                for repository in self.repository_list
                for submodule in repository.task_owned_submodule_list
            ],
        }

    def payload_get(self) -> dict[str, Any]:
        """Return the canonical JSON-ready replicated-state payload.

        Returns:
            The canonical JSON-ready replicated-state payload.
        """

        return {
            "schema_version": 3,
            "common_prefix": self.common_prefix,
            "cleanup_binding_generation": self.cleanup_binding_generation,
            "coordination_commit": self.coordination_commit,
            "goals_repository": self.goals_repository,
            "lifecycle_state": self.lifecycle_state,
            "provider_state_generation": self.provider_state_generation,
            "repository_list": [_json_value_get(asdict(item)) for item in self.repository_list],
            "sealed_goal_sha256": self.sealed_goal_sha256,
            "sealed_spec_sha256": self.sealed_spec_sha256,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "TaskState":
        """Build one task state from its exact payload.

        Args:
            payload: Structured operation payload.

        Returns:
            One task state from its exact payload.
        """

        expected = {
            "schema_version",
            "common_prefix",
            "cleanup_binding_generation",
            "coordination_commit",
            "goals_repository",
            "lifecycle_state",
            "provider_state_generation",
            "repository_list",
            "sealed_goal_sha256",
            "sealed_spec_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 3:
            raise GoalLifecycleError("Private task state has another shape")
        repository_payload_list = payload["repository_list"]
        if not isinstance(repository_payload_list, list) or not repository_payload_list:
            raise GoalLifecycleError("Private task repository_list must be non-empty")
        provider_state_generation = payload["provider_state_generation"]
        cleanup_binding_generation = payload["cleanup_binding_generation"]
        if (
            not isinstance(provider_state_generation, int)
            or isinstance(provider_state_generation, bool)
            or provider_state_generation < 1
        ):
            raise GoalLifecycleError("Private task state generation must be positive")
        if (
            not isinstance(cleanup_binding_generation, int)
            or isinstance(cleanup_binding_generation, bool)
            or cleanup_binding_generation < 0
            or cleanup_binding_generation > provider_state_generation
        ):
            raise GoalLifecycleError("Private task cleanup-binding generation is malformed")
        lifecycle_state = str(payload["lifecycle_state"])
        if lifecycle_state not in {
            "repository_prepared",
            "contracts_authored",
            "goal_ready",
            "active",
        }:
            raise GoalLifecycleError("Private task lifecycle state is unsupported")
        if (lifecycle_state == "active") != (cleanup_binding_generation > 0):
            raise GoalLifecycleError("Private task lifecycle and cleanup-binding generation disagree")
        common_prefix = common_prefix_validate(_nonempty_text_validate(payload["common_prefix"], label="common_prefix"))
        repository_list = tuple(RepositoryState.from_payload(item) for item in repository_payload_list)
        main_root_list = [item.main_root for item in repository_list]
        if main_root_list != sorted(main_root_list) or len(main_root_list) != len(set(main_root_list)):
            raise GoalLifecycleError("Private task repositories must be unique and sorted by main_root")
        if len({item.task_root for item in repository_list}) != len(repository_list):
            raise GoalLifecycleError("Private task state repeats a task worktree")
        if any(item.branch_name != common_prefix for item in repository_list):
            raise GoalLifecycleError("Private task repository branch differs from common_prefix")
        submodule_root_list = [
            submodule.repository.main_root
            for repository in repository_list
            for submodule in repository.task_owned_submodule_list
        ]
        if len(submodule_root_list) != len(set(submodule_root_list)):
            raise GoalLifecycleError("Private task state repeats a task-owned submodule root")
        sealed_spec_sha256 = _sha256_validate(payload["sealed_spec_sha256"], label="sealed_spec_sha256")
        sealed_goal_sha256 = _sha256_validate(
            payload["sealed_goal_sha256"],
            label="sealed_goal_sha256",
            empty_allowed=lifecycle_state not in _SEALED_TASK_LIFECYCLE_STATE_SET,
        )
        if lifecycle_state in _SEALED_TASK_LIFECYCLE_STATE_SET and not sealed_goal_sha256:
            raise GoalLifecycleError("Sealed task lifecycle requires sealed_goal_sha256")
        state = cls(
            common_prefix=common_prefix,
            cleanup_binding_generation=cleanup_binding_generation,
            coordination_commit=commit_validate(payload["coordination_commit"], label="coordination_commit"),
            goals_repository=_absolute_path_validate(payload["goals_repository"], label="goals_repository"),
            lifecycle_state=lifecycle_state,
            provider_state_generation=provider_state_generation,
            repository_list=repository_list,
            sealed_goal_sha256=sealed_goal_sha256,
            sealed_spec_sha256=sealed_spec_sha256,
            schema_version=3,
        )
        return state


def repository_boundary_list_get(
    state: TaskState,
) -> tuple[RepositoryBoundaryState, ...]:
    """Return every top-level and task-owned repository boundary in deterministic order.

    Args:
        state: Exact runtime state.

    Returns:
        Every top-level and task-owned repository boundary in deterministic order.
    """

    result: list[RepositoryBoundaryState] = []
    for repository in state.repository_list:
        result.append(repository)
        result.extend(item.repository for item in repository.task_owned_submodule_list)
    return tuple(result)


def _list_require(value: object, *, label: str) -> list[object]:
    """Return one required list payload.

    Args:
        value: Candidate value.
        label: Diagnostic owner label.

    Returns:
        One required list payload.
    """

    if not isinstance(value, list):
        raise GoalLifecycleError(f"{label} must be a list")
    return value


def _nonempty_text_validate(value: object, *, label: str) -> str:
    """Return one non-empty single-line state identity.

    Args:
        value: Candidate value.
        label: Diagnostic owner label.

    Returns:
        One non-empty single-line state identity.
    """

    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
        raise GoalLifecycleError(f"{label} must be non-empty single-line text")
    return value


def _sha256_validate(value: object, *, label: str, empty_allowed: bool = False) -> str:
    """Return one lowercase SHA-256 identity or one explicitly allowed empty value.

    Args:
        value: Candidate value.
        label: Diagnostic owner label.
        empty_allowed: Empty allowed.

    Returns:
        One lowercase SHA-256 identity or one explicitly allowed empty value.
    """

    if empty_allowed and value == "":
        return ""
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise GoalLifecycleError(f"{label} must be one lowercase SHA-256 identity")
    return value


def _absolute_path_validate(value: object, *, label: str) -> str:
    """Return one normalized absolute filesystem identity without resolving it.

    Args:
        value: Candidate value.
        label: Diagnostic owner label.

    Returns:
        One normalized absolute filesystem identity without resolving it.
    """

    text = _nonempty_text_validate(value, label=label)
    path = Path(text)
    if not path.is_absolute() or str(path) != text or any(part in {".", ".."} for part in path.parts):
        raise GoalLifecycleError(f"{label} must be one normalized absolute path")
    return text


def _json_value_get(value: Any) -> Any:
    """Convert dataclass tuple trees into their canonical JSON collection types.

    Args:
        value: Candidate value.

    Returns:
        Resulting any.
    """

    if isinstance(value, dict):
        return {key: _json_value_get(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value_get(item) for item in value]
    return value
