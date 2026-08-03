"""One-time coordination bootstrap-carrier retirement during goal deletion."""

from __future__ import annotations

from pathlib import Path
import shutil

from goal_lifecycle.bootstrap_exception import (
    CoordinationBootstrapException,
    coordination_bootstrap_exception_path_get,
)
from goal_lifecycle.coordination import CoordinationRepository
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
        """Initialize the coordination bootstrap retirer dependencies.

        Args:
            coordination: Coordination.
            git: Git command boundary.
            repository_retirer: Repository retirer.
        """

        self._coordination = coordination
        self._git = git
        self._repository_retirer = repository_retirer

    def carriers_retire(self, exception: CoordinationBootstrapException | None) -> None:
        """Delete exact legacy carrier paths and accept prior absence.

        Args:
            exception: Exception.
        """

        if exception is None:
            return
        for path, expected_name in (
            (
                Path(exception.specification_carrier_path),
                f"{exception.common_prefix}-spec.md",
            ),
            (Path(exception.goal_carrier_path), f"{exception.common_prefix}-goal.md"),
        ):
            if path.name != expected_name:
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            directory_sync(path.parent)

    def exception_retire(self, exception: CoordinationBootstrapException | None) -> None:
        """Remove the exact exception marker, empty container, and temporary exclude.

        Args:
            exception: Exception.
        """

        if exception is None:
            return
        marker_path = coordination_bootstrap_exception_path_get(self._coordination.root, git=self._git)
        if marker_path.exists():
            current = CoordinationBootstrapException.from_payload(
                json_object_load(marker_path, label="coordination bootstrap exception")
            )
            if current.common_prefix == exception.common_prefix:
                marker_path.unlink()
                directory_sync(marker_path.parent)
        worktree_container = self._coordination.root / ".worktree"
        if worktree_container.exists():
            try:
                worktree_container.rmdir()
            except OSError:
                pass
            else:
                directory_sync(worktree_container.parent)
        self._repository_retirer.worktree_exclude_retire(self._git.common_directory_get(self._coordination.root))
