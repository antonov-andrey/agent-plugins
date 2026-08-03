"""Crash-safe ownership and recovery for one top-level implementation worktree."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write, json_object_load
from goal_lifecycle.task.model import RepositoryState
from goal_lifecycle.task.repair import TaskRepairReport


class TaskWorktreeManager:
    """Create or resume one exact linked worktree from durable pending ownership."""

    def __init__(self, *, git: Git, repair_report: TaskRepairReport | None = None) -> None:
        self._git = git
        self._repair_report = repair_report or TaskRepairReport()

    def prepare(
        self,
        main_root: Path,
        *,
        baseline_commit: str,
        common_prefix: str,
        previous_state: RepositoryState | None,
    ) -> Path:
        """Create, adopt, or repair only the provider-owned exact task worktree."""

        task_root = main_root / ".worktree" / common_prefix
        marker_path = self._marker_path_get(main_root, common_prefix=common_prefix)
        expected_marker = {
            "schema_version": 1,
            "baseline_commit": baseline_commit,
            "branch_name": common_prefix,
            "common_directory": str(self._git.common_directory_get(main_root)),
            "main_root": str(main_root),
            "origin_url": self._git.origin_url_get(main_root),
            "task_root": str(task_root),
        }
        marker_preexisting = marker_path.exists()
        if marker_preexisting:
            if json_object_load(marker_path, label="pending task-worktree ownership") != expected_marker:
                raise GoalLifecycleError(f"Pending task-worktree ownership differs: {marker_path}")
        elif previous_state is None:
            self._unowned_collision_reject(main_root, task_root=task_root, branch_name=common_prefix)
            atomic_json_write(marker_path, expected_marker)

        worktree_by_path_map = self._worktree_by_path_map_get(main_root)
        registered = worktree_by_path_map.get(str(task_root.resolve())) if task_root.exists() else None
        if registered is None and task_root.exists():
            if previous_state is None:
                raise GoalLifecycleError(f"Unregistered task path has no durable ownership: {task_root}")
            self._git.run(main_root, ["worktree", "repair", str(task_root)])
            self._repair_report.record(f"task-worktree-registration-repaired:{task_root}")
            worktree_by_path_map = self._worktree_by_path_map_get(main_root)
            registered = worktree_by_path_map.get(str(task_root.resolve()))
            if registered is None:
                raise GoalLifecycleError(f"Recorded task worktree could not be repaired: {task_root}")
        if registered is None:
            branch_ref = f"refs/heads/{common_prefix}"
            branch_exists = self._git.run(main_root, ["show-ref", "--verify", branch_ref], check=False).returncode == 0
            if branch_exists:
                branch_commit = self._git.commit_get(main_root, branch_ref)
                if previous_state is None and branch_commit != baseline_commit:
                    raise GoalLifecycleError(f"Pending task branch differs from its exact baseline: {main_root}")
                self._git.ancestor_require(
                    main_root,
                    baseline_commit,
                    branch_commit,
                    label=f"{main_root.name} task branch baseline",
                )
                self._git.run(main_root, ["worktree", "add", str(task_root), common_prefix])
            else:
                if previous_state is not None:
                    raise GoalLifecycleError(f"Recorded task branch disappeared: {main_root}")
                self._git.run(
                    main_root,
                    [
                        "worktree",
                        "add",
                        "-b",
                        common_prefix,
                        str(task_root),
                        baseline_commit,
                    ],
                )
        self._identity_require(
            main_root,
            task_root=task_root,
            baseline_commit=baseline_commit,
            common_prefix=common_prefix,
            exact_baseline=previous_state is None,
        )
        if marker_preexisting:
            self._repair_report.record(f"task-worktree-transaction-recovered:{task_root}")
        return task_root

    def pending_retire(self, main_root: Path, *, common_prefix: str) -> None:
        """Retire one pending marker only after complete replicated task state exists."""

        try:
            self._marker_path_get(main_root, common_prefix=common_prefix).unlink()
        except FileNotFoundError:
            pass

    def validate(self, state: RepositoryState, *, common_prefix: str) -> Path:
        """Require one recorded worktree registration and branch identity."""

        main_root = Path(state.main_root).resolve(strict=True)
        task_root = Path(state.task_root).resolve(strict=True)
        self._identity_require(
            main_root,
            task_root=task_root,
            baseline_commit=state.baseline_commit,
            common_prefix=common_prefix,
            exact_baseline=False,
        )
        return task_root

    def _identity_require(
        self,
        main_root: Path,
        *,
        task_root: Path,
        baseline_commit: str,
        common_prefix: str,
        exact_baseline: bool,
    ) -> None:
        worktree = self._worktree_by_path_map_get(main_root).get(str(task_root.resolve(strict=True)))
        if worktree is None or worktree["branch"] != common_prefix:
            raise GoalLifecycleError(f"Task path is not the exact registered branch worktree: {task_root}")
        if self._git.root_get(task_root) != task_root.resolve(strict=True):
            raise GoalLifecycleError(f"Task path is not its exact Git root: {task_root}")
        if self._git.common_directory_get(task_root) != self._git.common_directory_get(main_root):
            raise GoalLifecycleError(f"Task worktree uses another Git common directory: {task_root}")
        if self._git.branch_get(task_root) != common_prefix:
            raise GoalLifecycleError(f"Task worktree has another branch: {task_root}")
        task_commit = self._git.commit_get(task_root)
        if exact_baseline and task_commit != baseline_commit:
            raise GoalLifecycleError(f"New task worktree is not at its exact recorded baseline: {task_root}")
        self._git.ancestor_require(
            task_root,
            baseline_commit,
            task_commit,
            label=f"{task_root.name} baseline relation",
        )

    def _unowned_collision_reject(self, main_root: Path, *, task_root: Path, branch_name: str) -> None:
        if task_root.exists() or task_root.is_symlink():
            raise GoalLifecycleError(f"Task path exists without durable provider ownership: {task_root}")
        if str(task_root.resolve()) in self._worktree_by_path_map_get(main_root):
            raise GoalLifecycleError(
                f"Task worktree registration exists without durable provider ownership: {task_root}"
            )
        if (
            self._git.run(
                main_root,
                ["show-ref", "--verify", f"refs/heads/{branch_name}"],
                check=False,
            ).returncode
            == 0
        ):
            raise GoalLifecycleError(f"Task branch exists without durable provider ownership: {main_root}")

    def _marker_path_get(self, main_root: Path, *, common_prefix: str) -> Path:
        return (
            self._git.common_directory_get(main_root)
            / "agent-workflows"
            / "task"
            / common_prefix
            / "pending-worktree.json"
        )

    def _worktree_by_path_map_get(self, main_root: Path) -> dict[str, dict[str, str]]:
        """Return complete registered worktree identity keyed by canonical path."""

        payload = self._git.run(main_root, ["worktree", "list", "--porcelain", "-z"]).stdout
        record: dict[str, str] = {}
        result: dict[str, dict[str, str]] = {}
        for raw_item in payload.split(b"\0"):
            if not raw_item:
                if record:
                    path_text = record.get("worktree")
                    if path_text is None:
                        raise GoalLifecycleError(f"Cannot parse Git worktree inventory: {main_root}")
                    result[str(Path(path_text).resolve())] = {
                        "branch": record.get("branch", "").removeprefix("refs/heads/"),
                        "head": record.get("HEAD", ""),
                    }
                    record = {}
                continue
            try:
                item = raw_item.decode("utf-8")
            except UnicodeDecodeError as error:
                raise GoalLifecycleError("Goal lifecycle requires UTF-8 Git worktree paths") from error
            key, separator, value = item.partition(" ")
            record[key] = value if separator else ""
        if record:
            path_text = record.get("worktree")
            if path_text is None:
                raise GoalLifecycleError(f"Cannot parse Git worktree inventory: {main_root}")
            result[str(Path(path_text).resolve())] = {
                "branch": record.get("branch", "").removeprefix("refs/heads/"),
                "head": record.get("HEAD", ""),
            }
        return result
