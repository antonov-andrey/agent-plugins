"""Non-blocking host-local serialization for one Linear issue."""

from __future__ import annotations

from contextlib import AbstractContextManager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Self

from task_workspace.model import (
    TaskWorkspaceError,
    WorkspaceConfig,
    issue_identifier_validate,
)


class IssueFileLock(AbstractContextManager["IssueFileLock"]):
    """Hold one kernel-released lock keyed by workspace root, issue and purpose."""

    def __init__(
        self,
        config: WorkspaceConfig,
        issue_identifier: str,
        *,
        purpose: str,
    ) -> None:
        """Initialize the lock path without persisting task contract data.

        Args:
            config: Exact workspace configuration.
            issue_identifier: Exact Linear issue identifier.
            purpose: Closed lock-purpose suffix.
        """

        if purpose not in {"attempt", "operation"}:
            raise TaskWorkspaceError("Issue lock purpose is unsupported")

        workspace_key = hashlib.sha256(str(config.root).encode("utf-8")).hexdigest()[:24]
        self._path = (
            Path(tempfile.gettempdir())
            / f"linear-agent-tools-{os.getuid()}"
            / workspace_key
            / f"{issue_identifier_validate(issue_identifier).lower()}.{purpose}.lock"
        )
        self._descriptor: int | None = None

    def __enter__(self) -> Self:
        """Acquire one non-blocking issue lock.

        Returns:
            This held lock.
        """

        private_root = self._path.parent.parent
        _private_directory_require(private_root)
        _private_directory_require(self._path.parent)
        descriptor = os.open(
            self._path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise TaskWorkspaceError("Issue lock must be one private user-owned ordinary file")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise TaskWorkspaceError("Another local session owns this Linear issue workspace") from error
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Release the host-local lock.

        Args:
            exc_type: Exception type.
            exc_value: Exception value.
            traceback: Exception traceback.
        """

        del exc_type, exc_value, traceback
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None


class IssueWorkspaceLock(IssueFileLock):
    """Serialize short Git workspace and cleanup transactions for one issue."""

    def __init__(self, config: WorkspaceConfig, issue_identifier: str) -> None:
        """Bind the operation lock for one exact issue."""

        super().__init__(config, issue_identifier, purpose="operation")


class IssueAttemptLock(IssueFileLock):
    """Prevent two local agent attempts from owning one issue concurrently."""

    def __init__(self, config: WorkspaceConfig, issue_identifier: str) -> None:
        """Bind the process-lifetime attempt lock for one exact issue."""

        super().__init__(config, issue_identifier, purpose="attempt")


def _private_directory_require(path: Path) -> None:
    """Create or validate one private physical lock directory."""

    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise TaskWorkspaceError("Issue lock directory is unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise TaskWorkspaceError("Issue lock directory must be one private user-owned physical directory")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        path.chmod(0o700)
        metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise TaskWorkspaceError("Issue lock directory permissions could not be made private")
