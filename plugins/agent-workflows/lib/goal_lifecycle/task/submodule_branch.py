"""Durable task-owned submodule branch creation and exact adoption."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.identity import commit_validate
from goal_lifecycle.io import atomic_json_write, json_object_load
from goal_lifecycle.task.model import TaskOwnedSubmoduleState
from goal_lifecycle.task.repair import TaskRepairReport


class TaskSubmoduleBranchManager:
    """Own pending intent and branch transitions for delegated submodules."""

    def __init__(self, *, git: Git, repair_report: TaskRepairReport | None = None) -> None:
        """Initialize the branch manager dependencies.

        Args:
            git: Git command boundary.
            repair_report: Repair report.
        """

        self._git = git
        self._repair_report = repair_report or TaskRepairReport()

    def prepare(
        self,
        *,
        main_root: Path,
        task_root: Path,
        path_text: str,
        common_prefix: str,
        baseline_commit: str,
        previous: TaskOwnedSubmoduleState | None,
    ) -> None:
        """Create or resume the exact task branch during ordinary preparation.

        Args:
            main_root: Main root.
            task_root: Task root.
            path_text: Exact recursive path.
            common_prefix: Exact task common prefix.
            baseline_commit: Baseline commit.
            previous: Previous task-owned state.
        """

        marker_path, expected_marker = self._pending_marker_get(
            main_root=main_root,
            task_root=task_root,
            path_text=path_text,
            common_prefix=common_prefix,
            baseline_commit=baseline_commit,
        )
        marker_preexisting = marker_path.exists()
        if marker_preexisting:
            self._pending_marker_validate(marker_path, expected=expected_marker, task_root=task_root)
        elif previous is None:
            self._git.clean_require(task_root)
            if self._symbolic_branch_optional_get(task_root):
                raise GoalLifecycleError(f"Unrecorded task-owned submodule is unexpectedly on a branch: {task_root}")
            atomic_json_write(marker_path, expected_marker)

        current_branch = self._symbolic_branch_optional_get(task_root)
        if current_branch != common_prefix:
            if previous is not None:
                raise GoalLifecycleError(f"Recorded task-owned submodule branch changed: {task_root}")
            branch_ref = f"refs/heads/{common_prefix}"
            if self._ref_commit_optional_get(task_root, branch_ref) is not None:
                if self._git.commit_get(task_root, branch_ref) != self._git.commit_get(task_root):
                    raise GoalLifecycleError(f"Existing task-owned submodule branch has another commit: {task_root}")
                self._git.run(task_root, ["switch", common_prefix])
            else:
                self._git.run(task_root, ["switch", "-c", common_prefix, baseline_commit])
        self._git.ancestor_require(
            task_root,
            baseline_commit,
            self._git.commit_get(task_root),
            label=f"{path_text} task-owned submodule baseline",
        )
        if marker_preexisting:
            self._repair_report.record(f"task-owned-submodule-transaction-recovered:{task_root}")

    def existing_pushed_adopt(
        self,
        *,
        main_root: Path,
        task_root: Path,
        path_text: str,
        common_prefix: str,
        baseline_commit: str,
        current_commit: str,
    ) -> None:
        """Adopt only an exact clean local and remote task branch already at the parent gitlink.

        Args:
            main_root: Main root.
            task_root: Task root.
            path_text: Exact recursive path.
            common_prefix: Exact task common prefix.
            baseline_commit: Baseline commit.
            current_commit: Current parent gitlink commit.
        """

        self._git.clean_require(task_root)
        if self._git.commit_get(task_root) != current_commit:
            raise GoalLifecycleError(f"Recovered submodule checkout differs from its parent gitlink: {task_root}")
        if self._git.origin_url_get(main_root) != self._git.origin_url_get(task_root):
            raise GoalLifecycleError(f"Recovered task-owned submodule origins differ: {task_root}")
        self._git.ancestor_require(
            task_root,
            baseline_commit,
            current_commit,
            label=f"{path_text} recovered task-owned submodule baseline",
        )
        self._git.fetch(task_root)
        local_ref = f"refs/heads/{common_prefix}"
        remote_ref = f"refs/remotes/origin/{common_prefix}"
        if self._ref_commit_optional_get(task_root, local_ref) != current_commit:
            raise GoalLifecycleError(
                f"Recovered task-owned submodule local task branch is absent or stale: {task_root}"
            )
        if self._ref_commit_optional_get(task_root, remote_ref) != current_commit:
            raise GoalLifecycleError(f"Recovered task-owned submodule task branch is not fully pushed: {task_root}")
        current_branch = self._symbolic_branch_optional_get(task_root)
        if current_branch not in {"", common_prefix}:
            raise GoalLifecycleError(f"Recovered task-owned submodule is on another branch: {task_root}")

        marker_path, expected_marker = self._pending_marker_get(
            main_root=main_root,
            task_root=task_root,
            path_text=path_text,
            common_prefix=common_prefix,
            baseline_commit=baseline_commit,
        )
        marker_preexisting = marker_path.exists()
        if marker_preexisting:
            self._pending_marker_validate(marker_path, expected=expected_marker, task_root=task_root)
        else:
            atomic_json_write(marker_path, expected_marker)
        if current_branch != common_prefix:
            self._git.run(task_root, ["switch", common_prefix])
        if self._git.commit_get(task_root) != current_commit:
            raise GoalLifecycleError(f"Recovered task-owned submodule branch moved during adoption: {task_root}")
        if marker_preexisting:
            self._repair_report.record(f"task-owned-submodule-inventory-transaction-recovered:{task_root}")

    def pending_exists(self, task_root: Path, *, common_prefix: str) -> bool:
        """Return whether one exact submodule branch transaction remains pending.

        Args:
            task_root: Task root.
            common_prefix: Exact task common prefix.

        Returns:
            Whether the pending marker exists.
        """

        return self.pending_marker_path_get(task_root, common_prefix=common_prefix).exists()

    def pending_retire(self, task_root: Path, *, common_prefix: str) -> None:
        """Retire one exact pending marker after durable state and receipt validation.

        Args:
            task_root: Task root.
            common_prefix: Exact task common prefix.
        """

        marker = self.pending_marker_path_get(task_root, common_prefix=common_prefix)
        try:
            marker.unlink()
        except FileNotFoundError:
            pass

    def pending_marker_path_get(self, task_root: Path, *, common_prefix: str) -> Path:
        """Return the Git-common marker path for one branch transaction.

        Args:
            task_root: Task root.
            common_prefix: Exact task common prefix.

        Returns:
            Pending marker path.
        """

        return (
            self._git.common_directory_get(task_root)
            / "agent-workflows"
            / "task"
            / common_prefix
            / "pending-participating-submodule.json"
        )

    def _pending_marker_get(
        self,
        *,
        main_root: Path,
        task_root: Path,
        path_text: str,
        common_prefix: str,
        baseline_commit: str,
    ) -> tuple[Path, dict[str, object]]:
        """Return one marker path and its exact identity payload.

        Args:
            main_root: Main root.
            task_root: Task root.
            path_text: Exact recursive path.
            common_prefix: Exact task common prefix.
            baseline_commit: Baseline commit.

        Returns:
            Marker path and payload.
        """

        return self.pending_marker_path_get(task_root, common_prefix=common_prefix), {
            "schema_version": 1,
            "baseline_commit": baseline_commit,
            "common_prefix": common_prefix,
            "main_root": str(main_root),
            "origin_url": self._git.origin_url_get(main_root),
            "path": path_text,
            "task_root": str(task_root),
        }

    def _pending_marker_validate(self, path: Path, *, expected: dict[str, object], task_root: Path) -> None:
        """Require an existing branch marker to have the exact transaction identity.

        Args:
            path: Marker path.
            expected: Expected payload.
            task_root: Task root.
        """

        if json_object_load(path, label="pending task-owned submodule") != expected:
            raise GoalLifecycleError(f"Pending task-owned submodule identity differs: {task_root}")

    def _symbolic_branch_optional_get(self, task_root: Path) -> str:
        """Return the current symbolic branch or an empty string for detached HEAD.

        Args:
            task_root: Task root.

        Returns:
            Current branch or an empty string.
        """

        return (
            self._git.run(task_root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
            .stdout.decode()
            .strip()
        )

    def _ref_commit_optional_get(self, task_root: Path, ref: str) -> str | None:
        """Return one exact ref commit when it exists.

        Args:
            task_root: Task root.
            ref: Exact ref.

        Returns:
            Commit or ``None``.
        """

        result = self._git.run(task_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
        if result.returncode != 0:
            return None
        try:
            return commit_validate(result.stdout.decode("ascii").strip(), label=ref)
        except UnicodeDecodeError as error:
            raise GoalLifecycleError(f"Git ref has a non-ASCII commit identity: {ref}") from error
