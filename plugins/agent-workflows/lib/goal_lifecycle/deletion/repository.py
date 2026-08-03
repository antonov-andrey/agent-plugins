"""Idempotent task-worktree and branch cleanup for explicit goal deletion."""

from __future__ import annotations

from pathlib import Path
import shutil

from goal_lifecycle.bootstrap_exception import CoordinationBootstrapException
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_bytes_write, atomic_json_write
from goal_lifecycle.task.model import TaskState


class GoalTaskRepositoryRetirer:
    """Delete task-owned Git resources while treating prior absence as success."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        """Initialize the repository cleanup dependencies.

        Args:
            coordination: Coordination.
            git: Git command boundary.
        """

        self._coordination = coordination
        self._git = git

    def local_refs_retire(
        self,
        *,
        bootstrap_exception: CoordinationBootstrapException | None,
        journal: dict[str, object],
        journal_path: Path,
        state: TaskState,
    ) -> None:
        """Delete every local task ref that still exists.

        Args:
            bootstrap_exception: Bootstrap marker.
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
        if bootstrap_exception is not None:
            self._local_ref_retire(
                command_root=self._coordination.root,
                common_directory=self._git.common_directory_get(self._coordination.root),
                common_prefix=bootstrap_exception.common_prefix,
            )

    def remote_refs_retire(
        self,
        *,
        bootstrap_exception: CoordinationBootstrapException | None,
        journal: dict[str, object],
        journal_path: Path,
        state: TaskState,
    ) -> None:
        """Delete every remote task ref that still exists.

        Args:
            bootstrap_exception: Bootstrap marker.
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
        if bootstrap_exception is not None:
            self._remote_ref_retire(
                command_root=self._coordination.root,
                common_prefix=bootstrap_exception.common_prefix,
                origin_url=self._git.origin_url_get(self._coordination.root),
            )

    def worktree_exclude_retire(self, common_directory: Path) -> None:
        """Remove one exact provider-owned worktree exclude line.

        Args:
            common_directory: Git common directory.
        """

        exclude_path = common_directory / "info" / "exclude"
        if not exclude_path.is_file():
            return
        line_list = exclude_path.read_text(encoding="utf-8").splitlines()
        if "/.worktree/" not in line_list:
            return
        remaining_line_list = [line for line in line_list if line != "/.worktree/"]
        payload = (("\n".join(remaining_line_list) + "\n") if remaining_line_list else "").encode()
        atomic_bytes_write(exclude_path, payload, mode=0o644)

    def worktrees_retire(
        self,
        *,
        bootstrap_exception: CoordinationBootstrapException | None,
        journal: dict[str, object],
    ) -> None:
        """Delete every recorded task worktree or exact remaining task path.

        Args:
            bootstrap_exception: Bootstrap marker.
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
        if bootstrap_exception is not None:
            self._worktree_retire(
                command_root=self._coordination.root,
                task_root=self._coordination.root / ".worktree" / bootstrap_exception.common_prefix,
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
