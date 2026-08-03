"""Crash-safe content-free private state and ordinary file writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
from typing import Any

from goal_lifecycle.error import GoalLifecycleError


def directory_sync(path: Path) -> None:
    """Fsync one directory after an atomic namespace mutation.

    Args:
        path: Exact filesystem path.
    """

    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_bytes_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Atomically replace one file and fsync both bytes and parent.

    Args:
        path: Exact filesystem path.
        payload: Structured operation payload.
        mode: Mode.
    """

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = path.parent / f".{path.name}.{secrets.token_hex(12)}.tmp"
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
        directory_sync(path.parent)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace one canonical JSON file and fsync its parent.

    Args:
        path: Exact filesystem path.
        payload: Structured operation payload.
    """

    atomic_bytes_write(
        path,
        (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode(),
    )


def json_object_load(path: Path, *, label: str) -> dict[str, Any]:
    """Read one ordinary JSON file and require an object root.

    Args:
        path: Exact filesystem path.
        label: Diagnostic owner label.

    Returns:
        Decoded JSON object.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise GoalLifecycleError(f"{label} is unavailable: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GoalLifecycleError(f"{label} is malformed: {path}") from error
    if not isinstance(payload, dict):
        raise GoalLifecycleError(f"{label} must be a JSON object: {path}")
    return payload
