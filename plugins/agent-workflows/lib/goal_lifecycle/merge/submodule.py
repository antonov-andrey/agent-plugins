"""Checkpoint-derived task-owned submodule main publication."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from goal_lifecycle.checkpoint.model import Checkpoint
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write
from goal_lifecycle.task.gitlink import task_owned_submodule_target_list_get
from goal_lifecycle.task.model import TaskState


class TaskOwnedSubmoduleMainPublisher:
    """Resolve, preflight, publish, and prove submodule mains before parent mains."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        self._coordination = coordination
        self._git = git

    def snapshot_get(self, state: TaskState, *, checkpoint: Checkpoint) -> list[dict[str, object]]:
        """Return the exact submodule targets selected transitively by one checkpoint."""

        workspace_root = self._coordination.root.parent.resolve(strict=True)
        checkpoint_by_path_map = {item.project_path: item.git_commit_final for item in checkpoint.project_list}
        result: list[dict[str, object]] = []
        for repository in state.repository_list:
            project_path = Path(repository.main_root).resolve(strict=True).relative_to(workspace_root).as_posix()
            top_commit = checkpoint_by_path_map.get(project_path)
            if top_commit is None:
                raise GoalLifecycleError(f"Checkpoint omits task-owned submodule parent: {project_path}")
            for target in task_owned_submodule_target_list_get(
                repository,
                top_level_commit=top_commit,
                git=self._git,
            ):
                result.append(
                    {
                        "git_commit_final": target.git_commit_final,
                        "main_root": target.state.repository.main_root,
                        "merged": False,
                        "origin_url": target.state.repository.origin_url,
                        "parent_project_path": project_path,
                        "path": target.path,
                        "task_root": target.state.repository.task_root,
                    }
                )
        return sorted(
            result,
            key=lambda item: (
                -len(PurePosixPath(str(item["path"])).parts),
                str(item["parent_project_path"]),
                str(item["path"]),
            ),
        )

    def preflight(self, snapshot_list: list[dict[str, object]], *, common_prefix: str) -> None:
        """Prove every remote fast-forward and exact task branch before any main mutation."""

        for item in snapshot_list:
            root = Path(str(item["main_root"])).resolve(strict=True)
            task_root = Path(str(item["task_root"])).resolve(strict=True)
            if (
                self._git.origin_url_get(root) != item["origin_url"]
                or self._git.origin_url_get(task_root) != item["origin_url"]
            ):
                raise GoalLifecycleError(f"Task-owned submodule origin changed before merge: {root}")
            self._git.clean_require(root)
            self._git.clean_require(task_root)
            if self._git.branch_get(root) != "main":
                raise GoalLifecycleError(f"Task-owned submodule requires canonical main checkout: {root}")
            if self._git.branch_get(task_root) != common_prefix:
                raise GoalLifecycleError(f"Task-owned submodule has another task branch: {task_root}")
            self._git.fetch(root)
            target = str(item["git_commit_final"])
            if (
                self._git.commit_get(task_root) != target
                or self._git.commit_get(task_root, f"refs/remotes/origin/{common_prefix}") != target
            ):
                raise GoalLifecycleError(f"Task-owned submodule target is not fully pushed: {task_root}")
            remote_main = self._git.commit_get(root, "refs/remotes/origin/main")
            if (
                self._git.commit_get(root) != remote_main
                or self._git.commit_get(root, "refs/heads/main") != remote_main
            ):
                raise GoalLifecycleError(f"Task-owned submodule local and remote main differ before merge: {root}")
            self._git.ancestor_require(
                root,
                remote_main,
                target,
                label=f"{item['path']} submodule fast-forward",
            )

    def resume(
        self,
        *,
        common_prefix: str,
        journal: dict[str, object],
        journal_path: Path,
    ) -> None:
        """Compare-and-swap each task-owned submodule main, deepest first."""

        for item in journal["submodule_list"]:
            if item["merged"] is True:
                self._remote_item_require(item)
                continue
            root = Path(str(item["main_root"])).resolve(strict=True)
            if self._git.origin_url_get(root) != item["origin_url"]:
                raise GoalLifecycleError(f"Task-owned submodule origin changed during merge: {root}")
            self._git.clean_require(root)
            self._git.fetch(root)
            target = str(item["git_commit_final"])
            remote_main = self._git.commit_get(root, "refs/remotes/origin/main")
            if remote_main != target:
                self._git.ancestor_require(
                    root,
                    remote_main,
                    target,
                    label=f"{item['path']} submodule merge resume",
                )
                result = self._git.run(root, ["push", "origin", f"{target}:refs/heads/main"], check=False)
                if result.returncode != 0:
                    raise GoalLifecycleError(f"Concurrent submodule main update interrupted merge: {root}")
                self._git.fetch(root)
            if self._git.commit_get(root, "refs/remotes/origin/main") != target:
                raise GoalLifecycleError(f"Task-owned submodule remote main did not reach target: {root}")
            item["merged"] = True
            atomic_json_write(journal_path, journal)

    def local_checkouts_sync(self, snapshot_list: list[dict[str, object]]) -> None:
        """Attach canonical submodule checkouts only after parent gitlinks reached target."""

        for item in snapshot_list:
            self._remote_item_require(item)
            root = Path(str(item["main_root"])).resolve(strict=True)
            target = str(item["git_commit_final"])
            self._git.clean_require(root)
            if self._git.commit_get(root) != target:
                raise GoalLifecycleError(f"Parent gitlink did not expose task-owned submodule target: {root}")
            local_main_ref = "refs/heads/main"
            current_local_main = self._git.commit_get(root, local_main_ref)
            if current_local_main != target:
                self._git.ancestor_require(
                    root,
                    current_local_main,
                    target,
                    label=f"{item['path']} local submodule main publication",
                )
                self._git.run(root, ["update-ref", local_main_ref, target, current_local_main])
            self._git.run(root, ["switch", "main"])
            self._merged_item_require(item)

    def merged_exact_require(self, snapshot_list: list[dict[str, object]]) -> None:
        """Require every selected submodule target on local and remote main."""

        for item in snapshot_list:
            self._merged_item_require(item)

    def fix_forward_ancestry_require(
        self,
        snapshot_list: list[dict[str, object]],
        *,
        previous_snapshot_list: list[dict[str, object]],
    ) -> None:
        """Require each replacement submodule target to include interrupted task work."""

        previous_by_identity_map = {
            (str(item["parent_project_path"]), str(item["path"])): str(item["git_commit_final"])
            for item in previous_snapshot_list
        }
        current_by_identity_map = {
            (str(item["parent_project_path"]), str(item["path"])): item for item in snapshot_list
        }
        if set(previous_by_identity_map) != set(current_by_identity_map):
            raise GoalLifecycleError("Fix-forward checkpoint changes the task-owned submodule set")
        for identity, item in current_by_identity_map.items():
            root = Path(str(item["main_root"])).resolve(strict=True)
            self._git.ancestor_require(
                root,
                previous_by_identity_map[identity],
                str(item["git_commit_final"]),
                label=f"{item['path']} task-owned submodule fix-forward ancestry",
            )

    def _merged_item_require(self, item: dict[str, object]) -> None:
        self._remote_item_require(item)
        root = Path(str(item["main_root"])).resolve(strict=True)
        self._git.clean_require(root)
        target = str(item["git_commit_final"])
        if (
            self._git.branch_get(root) != "main"
            or self._git.commit_get(root) != target
            or self._git.commit_get(root, "refs/heads/main") != target
        ):
            raise GoalLifecycleError(f"Task-owned submodule main is not the selected target: {root}")

    def _remote_item_require(self, item: dict[str, object]) -> None:
        """Require only the publication that may precede its parent gitlink."""

        root = Path(str(item["main_root"])).resolve(strict=True)
        if self._git.origin_url_get(root) != item["origin_url"]:
            raise GoalLifecycleError(f"Task-owned submodule origin changed after merge: {root}")
        self._git.fetch(root)
        if self._git.commit_get(root, "refs/remotes/origin/main") != str(item["git_commit_final"]):
            raise GoalLifecycleError(f"Task-owned submodule remote main is not the selected target: {root}")
