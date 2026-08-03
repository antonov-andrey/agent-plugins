"""Resolve the exact task-owned deletion scope without preservation gates."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.bootstrap_exception import (
    CoordinationBootstrapException,
    coordination_bootstrap_exception_optional_get,
    coordination_bootstrap_exception_validate,
)
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.task.model import TaskState


class GoalDeletionScopeResolver:
    """Resolve only identities required to delete task-owned resources."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        """Initialize the deletion-scope dependencies.

        Args:
            coordination: Coordination.
            git: Git command boundary.
        """

        self._coordination = coordination
        self._git = git

    def bootstrap_exception_payload_get(self, state: TaskState) -> dict[str, object] | None:
        """Return the optional task-owned self-hosting cleanup marker.

        Args:
            state: Exact runtime state.

        Returns:
            The optional marker payload.
        """

        exception = self.bootstrap_exception_get(state.common_prefix)
        return None if exception is None else exception.payload_get()

    def bootstrap_exception_get(
        self,
        common_prefix: str,
    ) -> CoordinationBootstrapException | None:
        """Return the exact task's optional self-hosting marker.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            Matching marker or none.
        """

        exception = coordination_bootstrap_exception_optional_get(self._coordination.root, git=self._git)
        if exception is None:
            return None
        if exception.common_prefix != common_prefix:
            return None
        coordination_bootstrap_exception_validate(self._coordination.root, exception, git=self._git)
        return exception

    def project_list_get(self, state: TaskState) -> list[dict[str, str]]:
        """Return every exact top-level task repository identity.

        Args:
            state: Exact runtime state.

        Returns:
            Every top-level task repository identity.
        """

        workspace_root = self._coordination.root.parent.resolve(strict=True)
        project_list: list[dict[str, str]] = []
        for repository in state.repository_list:
            main_root = Path(repository.main_root).resolve(strict=True)
            if (
                self._git.root_get(main_root) != main_root
                or self._git.origin_url_get(main_root) != repository.origin_url
            ):
                raise GoalLifecycleError(f"Goal deletion repository ownership differs: {main_root}")
            task_root = Path(repository.task_root)
            if task_root != main_root / ".worktree" / state.common_prefix:
                raise GoalLifecycleError(f"Goal deletion task path is outside its owner: {task_root}")
            try:
                project_path = main_root.relative_to(workspace_root).as_posix()
            except ValueError as error:
                raise GoalLifecycleError(
                    f"Goal deletion repository is outside the canonical workspace: {main_root}"
                ) from error
            project_list.append(
                {
                    "main_common_directory": str(self._git.common_directory_get(main_root)),
                    "main_root": str(main_root),
                    "origin_url": repository.origin_url,
                    "project_path": project_path,
                    "task_root": str(task_root),
                }
            )
        return sorted(project_list, key=lambda item: item["project_path"])

    def submodule_list_get(self, state: TaskState) -> list[dict[str, str]]:
        """Return every exact task-owned submodule identity.

        Args:
            state: Exact runtime state.

        Returns:
            Every task-owned submodule identity.
        """

        submodule_list: list[dict[str, str]] = []
        for repository in state.repository_list:
            for item in repository.task_owned_submodule_list:
                boundary = item.repository
                main_root = Path(boundary.main_root)
                if main_root.exists() and self._git.origin_url_get(main_root) != boundary.origin_url:
                    raise GoalLifecycleError(f"Task-owned submodule ownership differs: {main_root}")
                submodule_list.append(
                    {
                        "main_common_directory": (
                            str(self._git.common_directory_get(main_root)) if main_root.exists() else ""
                        ),
                        "main_root": boundary.main_root,
                        "origin_url": boundary.origin_url,
                        "parent_main_root": repository.main_root,
                        "path": item.path,
                        "task_root": boundary.task_root,
                        "task_common_directory": (
                            str(self._git.common_directory_get(Path(boundary.task_root)))
                            if Path(boundary.task_root).exists()
                            else ""
                        ),
                    }
                )
        return sorted(submodule_list, key=lambda item: (item["parent_main_root"], item["path"]))
