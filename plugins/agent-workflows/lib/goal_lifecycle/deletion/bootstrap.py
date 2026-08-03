"""One-time coordination bootstrap-carrier retirement during goal deletion."""

from __future__ import annotations

import hashlib
from pathlib import Path

from goal_lifecycle.bootstrap_exception import (
    CoordinationBootstrapException,
    coordination_bootstrap_exception_path_get,
)
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import directory_sync, json_object_load
from goal_lifecycle.deletion.repository import GoalTaskRepositoryRetirer


class CoordinationBootstrapRetirer:
    """Retire the exact self-hosting exception and its physical carriers."""

    def __init__(
        self,
        coordination: CoordinationRepository,
        *,
        git: Git,
        repository_retirer: GoalTaskRepositoryRetirer,
    ) -> None:
        self._coordination = coordination
        self._git = git
        self._repository_retirer = repository_retirer

    def carriers_retire(self, exception: CoordinationBootstrapException | None) -> None:
        """Delete only exact content-bound legacy carrier files."""

        if exception is None:
            return
        for path, expected_sha256 in (
            (
                Path(exception.specification_carrier_path),
                exception.sealed_specification_sha256,
            ),
            (Path(exception.goal_carrier_path), exception.sealed_goal_sha256),
        ):
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise GoalLifecycleError(f"Bootstrap carrier identity changed: {path}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
                raise GoalLifecycleError(f"Bootstrap carrier content changed: {path}")
            path.unlink()
            directory_sync(path.parent)

    def exception_retire(self, exception: CoordinationBootstrapException | None) -> None:
        """Remove the exact exception marker, empty container, and temporary exclude."""

        if exception is None:
            return
        marker_path = coordination_bootstrap_exception_path_get(self._coordination.root, git=self._git)
        if marker_path.exists():
            current = CoordinationBootstrapException.from_payload(
                json_object_load(marker_path, label="coordination bootstrap exception")
            )
            if current != exception:
                raise GoalLifecycleError("Coordination bootstrap exception changed during deletion")
            marker_path.unlink()
            directory_sync(marker_path.parent)
        worktree_container = self._coordination.root / ".worktree"
        if worktree_container.exists():
            try:
                worktree_container.rmdir()
            except OSError as error:
                raise GoalLifecycleError("Coordination worktree container is not empty") from error
            directory_sync(worktree_container.parent)
        self._repository_retirer.worktree_exclude_retire(self._coordination.root)
