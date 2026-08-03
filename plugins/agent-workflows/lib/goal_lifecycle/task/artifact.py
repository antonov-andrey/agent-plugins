"""Trusted ordinary-file input boundary for tracked task artifacts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from goal_lifecycle.error import GoalLifecycleError


def ordinary_task_artifact_input_get(
    path: Path | None,
    label: str,
    *,
    forbidden_root_list: Sequence[Path],
) -> bytes | None:
    """Return optional UTF-8 task-artifact bytes from outside repository trees.

    Args:
        path: Exact filesystem path.
        label: Diagnostic owner label.
        forbidden_root_list: Ordered forbidden root values.

    Returns:
        The optional UTF-8 task-artifact bytes from outside repository trees.
    """

    return (
        None
        if path is None
        else ordinary_task_artifact_input_require(
            path,
            label,
            forbidden_root_list=forbidden_root_list,
        )
    )


def ordinary_task_artifact_input_require(
    path: Path,
    label: str,
    *,
    forbidden_root_list: Sequence[Path],
) -> bytes:
    """Return one ordinary single-link UTF-8 input outside every forbidden root.

    Args:
        path: Exact filesystem path.
        label: Diagnostic owner label.
        forbidden_root_list: Ordered forbidden root values.

    Returns:
        One ordinary single-link UTF-8 input outside every forbidden root.
    """

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
