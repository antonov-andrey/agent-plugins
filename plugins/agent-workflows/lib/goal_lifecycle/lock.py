"""Non-blocking local lifecycle serialization."""

from __future__ import annotations

from contextlib import AbstractContextManager
import fcntl
import os
from pathlib import Path

from goal_lifecycle.error import GoalLifecycleError


class ExclusiveFileLock(AbstractContextManager["ExclusiveFileLock"]):
    """Hold one kernel-released non-blocking exclusive lock."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._descriptor: int | None = None

    def __enter__(self) -> "ExclusiveFileLock":
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise GoalLifecycleError(f"Another lifecycle operation owns lock {self._path.name}") from error
        self._descriptor = descriptor
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None
