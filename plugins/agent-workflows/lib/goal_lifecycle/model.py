"""Closed task, repository, checkpoint, and journal models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from pathlib import Path, PurePosixPath
from typing import Any

from goal_lifecycle.error import GoalLifecycleError

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_COMMON_PREFIX_PATTERN = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}-[a-z0-9][a-z0-9-]*")
_CHECKPOINT_ID_PATTERN = re.compile(r"checkpoint-[0-9]{4}")


def common_prefix_validate(value: str) -> str:
    """Validate the one filesystem and branch-safe task identity."""

    if _COMMON_PREFIX_PATTERN.fullmatch(value) is None or len(value) > 120:
        raise GoalLifecycleError("Task common prefix is not a canonical dated semantic basename")
    return value


def commit_validate(value: object, *, label: str) -> str:
    """Return one exact lowercase full Git commit identity."""

    if not isinstance(value, str) or _COMMIT_PATTERN.fullmatch(value) is None:
        raise GoalLifecycleError(f"{label} must be one full lowercase Git commit")
    return value


def relative_project_path_validate(value: object) -> str:
    """Validate one workspace-relative repository path without escape syntax."""

    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise GoalLifecycleError("Checkpoint project_path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GoalLifecycleError("Checkpoint project_path must be normalized and workspace-relative")
    if value == "project-goals" or path.name == "project-goals":
        raise GoalLifecycleError("project-goals cannot be a self-referential checkpoint participant")
    return value


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """Bind one implementation repository to its exact closing commit."""

    project_path: str
    git_commit_final: str

    @classmethod
    def from_payload(cls, payload: object) -> "ProjectSnapshot":
        if not isinstance(payload, dict) or set(payload) != {"project_path", "git_commit_final"}:
            raise GoalLifecycleError("Checkpoint project entry has another shape")
        return cls(
            project_path=relative_project_path_validate(payload["project_path"]),
            git_commit_final=commit_validate(payload["git_commit_final"], label="git_commit_final"),
        )


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One immutable full cross-repository commit snapshot."""

    checkpoint_id: str
    project_list: tuple[ProjectSnapshot, ...]

    @classmethod
    def from_payload(cls, payload: object) -> "Checkpoint":
        if not isinstance(payload, dict) or set(payload) != {"checkpoint_id", "project_list"}:
            raise GoalLifecycleError("Checkpoint entry has another shape")
        checkpoint_id = payload["checkpoint_id"]
        if not isinstance(checkpoint_id, str) or _CHECKPOINT_ID_PATTERN.fullmatch(checkpoint_id) is None:
            raise GoalLifecycleError("checkpoint_id must use checkpoint-NNNN")
        project_payload_list = payload["project_list"]
        if not isinstance(project_payload_list, list) or not project_payload_list:
            raise GoalLifecycleError("Checkpoint must contain a non-empty full project_list")
        project_list = tuple(ProjectSnapshot.from_payload(item) for item in project_payload_list)
        project_path_list = [item.project_path for item in project_list]
        if project_path_list != sorted(project_path_list) or len(project_path_list) != len(set(project_path_list)):
            raise GoalLifecycleError("Checkpoint project_list must be unique and sorted by project_path")
        return cls(checkpoint_id=checkpoint_id, project_list=project_list)


@dataclass(frozen=True, slots=True)
class CheckpointDocument:
    """Canonical append-only checkpoint document."""

    accepted_checkpoint_id: str
    checkpoint_list: tuple[Checkpoint, ...]
    schema_version: int = 1

    @classmethod
    def empty(cls) -> "CheckpointDocument":
        return cls(accepted_checkpoint_id="", checkpoint_list=())

    @classmethod
    def from_payload(cls, payload: object) -> "CheckpointDocument":
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "accepted_checkpoint_id",
            "checkpoint_list",
        }:
            raise GoalLifecycleError("checkpoint.yaml has another shape")
        if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
            raise GoalLifecycleError("checkpoint.yaml schema_version must equal integer 1")
        accepted = payload["accepted_checkpoint_id"]
        if not isinstance(accepted, str) or (accepted and _CHECKPOINT_ID_PATTERN.fullmatch(accepted) is None):
            raise GoalLifecycleError("accepted_checkpoint_id must be empty or one checkpoint id")
        raw_checkpoint_list = payload["checkpoint_list"]
        if not isinstance(raw_checkpoint_list, list):
            raise GoalLifecycleError("checkpoint_list must be a list")
        checkpoint_list = tuple(Checkpoint.from_payload(item) for item in raw_checkpoint_list)
        expected_id_list = [f"checkpoint-{index:04d}" for index in range(1, len(checkpoint_list) + 1)]
        if [item.checkpoint_id for item in checkpoint_list] != expected_id_list:
            raise GoalLifecycleError("checkpoint identifiers must be contiguous and monotonic")
        if accepted and accepted not in expected_id_list:
            raise GoalLifecycleError("accepted checkpoint is absent from checkpoint_list")
        return cls(accepted_checkpoint_id=accepted, checkpoint_list=checkpoint_list)

    def payload_get(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "accepted_checkpoint_id": self.accepted_checkpoint_id,
            "checkpoint_list": [
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "project_list": [asdict(project) for project in checkpoint.project_list],
                }
                for checkpoint in self.checkpoint_list
            ],
        }


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


def repository_relative_path_validate(value: object, *, label: str) -> str:
    """Validate one literal normalized path owned by a repository."""

    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise GoalLifecycleError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GoalLifecycleError(f"{label} must be normalized and repository-relative")
    if path.parts[0] in {".git", ".worktree"}:
        raise GoalLifecycleError(f"{label} is reserved")
    return value


@dataclass(frozen=True, slots=True)
class RepositoryState:
    """Private identity of one participating implementation worktree."""

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

    @classmethod
    def from_payload(cls, payload: object) -> "RepositoryState":
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
        return cls(
            accepted_main_commit_drift_list=tuple(
                MainCommitDriftAttestation.from_payload(item)
                for item in _list_require(
                    payload["accepted_main_commit_drift_list"],
                    label="accepted_main_commit_drift_list",
                )
            ),
            baseline_commit=commit_validate(payload["baseline_commit"], label="baseline_commit"),
            branch_name=str(payload["branch_name"]),
            cleanup_declaration_sha256=str(payload["cleanup_declaration_sha256"]),
            main_commit=commit_validate(payload["main_commit"], label="main_commit"),
            main_root=str(payload["main_root"]),
            manifest_sha256=str(payload["manifest_sha256"]),
            origin_url=str(payload["origin_url"]),
            resource_state_list=tuple(
                BootstrapResourceState.from_payload(item)
                for item in _list_require(payload["resource_state_list"], label="resource_state_list")
            ),
            task_root=str(payload["task_root"]),
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
    schema_version: int = 2

    def payload_get(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "common_prefix": self.common_prefix,
            "cleanup_binding_generation": self.cleanup_binding_generation,
            "coordination_commit": self.coordination_commit,
            "goals_repository": self.goals_repository,
            "lifecycle_state": self.lifecycle_state,
            "provider_state_generation": self.provider_state_generation,
            "repository_list": [asdict(item) for item in self.repository_list],
            "sealed_goal_sha256": self.sealed_goal_sha256,
            "sealed_spec_sha256": self.sealed_spec_sha256,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "TaskState":
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
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 2:
            raise GoalLifecycleError("Private task state has another shape")
        repository_payload_list = payload["repository_list"]
        if not isinstance(repository_payload_list, list):
            raise GoalLifecycleError("Private task repository_list is malformed")
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
        if lifecycle_state not in {"repository_prepared", "contracts_authored", "goal_ready", "active"}:
            raise GoalLifecycleError("Private task lifecycle state is unsupported")
        if (lifecycle_state == "active") != (cleanup_binding_generation > 0):
            raise GoalLifecycleError("Private task lifecycle and cleanup-binding generation disagree")
        state = cls(
            common_prefix=common_prefix_validate(str(payload["common_prefix"])),
            cleanup_binding_generation=cleanup_binding_generation,
            coordination_commit=commit_validate(payload["coordination_commit"], label="coordination_commit"),
            goals_repository=str(payload["goals_repository"]),
            lifecycle_state=lifecycle_state,
            provider_state_generation=provider_state_generation,
            repository_list=tuple(RepositoryState.from_payload(item) for item in repository_payload_list),
            sealed_goal_sha256=str(payload["sealed_goal_sha256"]),
            sealed_spec_sha256=str(payload["sealed_spec_sha256"]),
        )
        if len({item.main_root for item in state.repository_list}) != len(state.repository_list):
            raise GoalLifecycleError("Private task state repeats a repository")
        return state


def _list_require(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise GoalLifecycleError(f"{label} must be a list")
    return value


def workspace_repository_resolve(workspace_root: Path, project_path: str) -> Path:
    """Resolve one checkpoint project without symlink or parent escape."""

    relative_project_path_validate(project_path)
    workspace = workspace_root.resolve(strict=True)
    candidate = workspace / project_path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise GoalLifecycleError(f"Checkpoint project does not exist: {project_path}") from error
    if candidate.is_symlink() or resolved.parent != workspace:
        raise GoalLifecycleError(f"Checkpoint project escapes the workspace: {project_path}")
    return resolved
