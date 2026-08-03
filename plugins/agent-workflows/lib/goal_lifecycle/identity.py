"""Canonical task, commit, repository-path, and workspace identity rules."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from goal_lifecycle.error import GoalLifecycleError

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_COMMON_PREFIX_PATTERN = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}-[a-z0-9][a-z0-9-]*")


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


def checkpoint_project_path_validate(value: object) -> str:
    """Validate one workspace-relative checkpoint repository path."""

    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise GoalLifecycleError("Checkpoint project_path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GoalLifecycleError("Checkpoint project_path must be normalized and workspace-relative")
    if value == "project-goals" or path.name == "project-goals":
        raise GoalLifecycleError("project-goals cannot be a self-referential checkpoint participant")
    return value


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


def workspace_repository_resolve(workspace_root: Path, project_path: str) -> Path:
    """Resolve one checkpoint project without symlink or parent escape."""

    checkpoint_project_path_validate(project_path)
    workspace = workspace_root.resolve(strict=True)
    candidate = workspace / project_path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise GoalLifecycleError(f"Checkpoint project does not exist: {project_path}") from error
    if candidate.is_symlink() or resolved.parent != workspace:
        raise GoalLifecycleError(f"Checkpoint project escapes the workspace: {project_path}")
    return resolved
