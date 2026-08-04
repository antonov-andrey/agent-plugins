"""Idempotent task-worktree and branch cleanup for explicit goal deletion."""

from __future__ import annotations

from pathlib import Path
import shutil

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write
from goal_lifecycle.task.model import TaskState


class GoalTaskRepositoryRetirer:
    """Delete task-owned Git resources while treating prior absence as success."""

    def __init__(self, *, git: Git) -> None:
        """Initialize the repository cleanup dependencies.

        Args:
            git: Git command boundary.
        """

        self._git = git

    def local_refs_retire(
        self,
        *,
        journal: dict[str, object],
        journal_path: Path,
        state: TaskState,
    ) -> None:
        """Delete every local task ref that still exists.

        Args:
            journal: Journal.
            journal_path: Exact filesystem path for journal.
            state: Exact runtime state.
        """

        owner_list = _ref_owner_list_get(journal)
        start_index = int(journal["repository_index"])
        for index, owner in enumerate(owner_list[start_index:], start=start_index):
            if owner["main_common_directory"]:
                self._local_ref_retire(
                    command_root=Path(owner["command_root"]),
                    common_directory=Path(owner["main_common_directory"]),
                    common_prefix=state.common_prefix,
                )
            journal["repository_index"] = index + 1
            atomic_json_write(journal_path, journal)

    def remote_refs_retire(
        self,
        *,
        journal: dict[str, object],
        journal_path: Path,
        state: TaskState,
    ) -> None:
        """Delete every remote task ref that still exists.

        Args:
            journal: Journal.
            journal_path: Exact filesystem path for journal.
            state: Exact runtime state.
        """

        owner_list = _ref_owner_list_get(journal)
        start_index = int(journal["repository_index"])
        for index, owner in enumerate(owner_list[start_index:], start=start_index):
            self._remote_ref_retire(
                command_root=Path(owner["command_root"]),
                common_prefix=state.common_prefix,
                origin_url=owner["origin_url"],
            )
            journal["repository_index"] = index + 1
            atomic_json_write(journal_path, journal)

    def worktrees_retire(
        self,
        *,
        journal: dict[str, object],
    ) -> None:
        """Delete every recorded task worktree or exact remaining task path.

        Args:
            journal: Journal.
        """

        submodule_list = sorted(
            journal["submodule_list"],
            key=lambda item: (len(Path(item["task_root"]).parts), item["task_root"]),
            reverse=True,
        )
        for item in submodule_list:
            self._worktree_retire(
                command_root=Path(item["main_root"]),
                task_root=Path(item["task_root"]),
            )
        for item in journal["project_list"]:
            self._worktree_retire(
                command_root=Path(item["main_root"]),
                task_root=Path(item["task_root"]),
            )

    def _local_ref_retire(self, *, command_root: Path, common_directory: Path, common_prefix: str) -> None:
        """Delete one local ref through its exact Git common directory.

        Args:
            command_root: Existing Git command root.
            common_directory: Git common directory.
            common_prefix: Task branch name.
        """

        if not common_directory.exists():
            return
        ref = f"refs/heads/{common_prefix}"
        self._git.run(command_root, [f"--git-dir={common_directory}", "update-ref", "-d", ref])
        if (
            self._git.run(
                command_root,
                [f"--git-dir={common_directory}", "show-ref", "--verify", ref],
                check=False,
            ).returncode
            == 0
        ):
            raise GoalLifecycleError(f"Local task ref remains after deletion: {common_directory}")

    def _remote_ref_retire(self, *, command_root: Path, common_prefix: str, origin_url: str) -> None:
        """Delete one remote ref and accept an already absent ref.

        Args:
            command_root: Existing Git command root.
            common_prefix: Task branch name.
            origin_url: Exact remote URL.
        """

        ref = f"refs/heads/{common_prefix}"
        self._git.run(command_root, ["push", origin_url, f":{ref}"], check=False)
        result = self._git.run(
            command_root,
            ["ls-remote", "--exit-code", "--heads", origin_url, ref],
            check=False,
        )
        if result.returncode == 2 and not result.stdout:
            return
        if result.returncode == 0:
            raise GoalLifecycleError(f"Remote task ref remains after deletion: {origin_url}")
        diagnostic = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise GoalLifecycleError(f"Remote task ref absence cannot be observed: {origin_url}: {diagnostic}")

    def _worktree_retire(self, *, command_root: Path, task_root: Path) -> None:
        """Delete one exact task path and prune any stale Git registration.

        Args:
            command_root: Existing Git command root.
            task_root: Exact task path.
        """

        if task_root.is_symlink() or task_root.is_file():
            task_root.unlink(missing_ok=True)
        elif task_root.exists():
            shutil.rmtree(task_root)
        if command_root.exists():
            self._git.run(command_root, ["worktree", "prune"])


def _ref_owner_list_get(journal: dict[str, object]) -> list[dict[str, str]]:
    """Return every remote and local ref owner in deterministic order.

    Args:
        journal: Journal.

    Returns:
        Every task ref owner.
    """

    owner_list = [
        {
            "command_root": item["main_root"],
            "main_common_directory": item["main_common_directory"],
            "origin_url": item["origin_url"],
        }
        for item in journal["project_list"]
    ]
    owner_list.extend(
        {
            "command_root": item["parent_main_root"],
            "main_common_directory": item["main_common_directory"],
            "origin_url": item["origin_url"],
        }
        for item in journal["submodule_list"]
    )
    return owner_list
