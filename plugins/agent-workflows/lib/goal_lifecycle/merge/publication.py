"""Exact cross-repository fast-forward publication for one goal checkpoint."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.checkpoint.model import Checkpoint
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.identity import workspace_repository_resolve
from goal_lifecycle.io import atomic_json_write


class CheckpointMainPublisher:
    """Preflight, publish, resume, and prove exact checkpoint commits on main."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        self._coordination = coordination
        self._git = git

    def preflight(
        self,
        checkpoint: Checkpoint,
        *,
        common_prefix: str,
        expected_origin_by_project_path_map: dict[str, str],
    ) -> None:
        """Prove every main and exact task ref before the first mutation."""

        workspace_root = self._coordination.root.parent
        for project in checkpoint.project_list:
            root = workspace_repository_resolve(workspace_root, project.project_path)
            self._origin_require(
                root,
                project_path=project.project_path,
                expected_origin_by_project_path_map=expected_origin_by_project_path_map,
            )
            self._git.clean_require(root)
            if self._git.branch_get(root) != "main":
                raise GoalLifecycleError(f"Merge requires canonical main checkout: {project.project_path}")
            self._git.fetch(root)
            local = self._git.commit_get(root)
            remote = self._git.commit_get(root, "refs/remotes/origin/main")
            if local != remote:
                raise GoalLifecycleError(f"Local and origin main differ before merge: {project.project_path}")
            target = self._git.commit_get(root, project.git_commit_final)
            if target != project.git_commit_final:
                raise GoalLifecycleError(f"Checkpoint commit is unavailable: {project.project_path}")
            self._git.ancestor_require(
                root,
                remote,
                target,
                label=f"{project.project_path} fast-forward preflight",
            )
            remote_task = self._git.commit_get(root, f"refs/remotes/origin/{common_prefix}")
            self._git.ancestor_require(
                root,
                target,
                remote_task,
                label=f"{project.project_path} selected checkpoint preservation",
            )

    def resume(
        self,
        *,
        expected_origin_by_project_path_map: dict[str, str],
        journal: dict[str, object],
        journal_path: Path,
    ) -> None:
        """Resume compare-and-swap main publication from one durable journal."""

        workspace_root = self._coordination.root.parent
        project_payload_list = journal["project_list"]
        if not isinstance(project_payload_list, list):
            raise GoalLifecycleError("Goal merge journal project_list is malformed")
        for project in project_payload_list:
            if not isinstance(project, dict):
                raise GoalLifecycleError("Goal merge journal project entry is malformed")
            root = workspace_repository_resolve(workspace_root, str(project["project_path"]))
            self._origin_require(
                root,
                project_path=str(project["project_path"]),
                expected_origin_by_project_path_map=expected_origin_by_project_path_map,
            )
            target = str(project["git_commit_final"])
            if self._git.branch_get(root) != "main":
                raise GoalLifecycleError(f"Merge resume requires canonical main checkout: {root}")
            self._git.fetch(root)
            local = self._git.commit_get(root)
            remote = self._git.commit_get(root, "refs/remotes/origin/main")
            if local == target:
                self._submodule_checkout_sync(root)
            self._git.clean_require(root)
            if remote == target:
                if local != target:
                    self._git.ancestor_require(root, local, target, label=f"{root.name} local merge recovery")
                    self._git.run(root, ["merge", "--ff-only", target])
            elif local == remote:
                self._git.ancestor_require(root, remote, target, label=f"{root.name} merge resume")
                result = self._git.run(root, ["push", "origin", f"{target}:refs/heads/main"], check=False)
                if result.returncode != 0:
                    raise GoalLifecycleError(f"Concurrent remote main update interrupted merge: {root}")
                self._git.run(root, ["merge", "--ff-only", target])
            else:
                raise GoalLifecycleError(f"Local and origin main diverged during merge: {root}")
            if self._git.commit_get(root) != target:
                raise GoalLifecycleError(f"Local main did not reach checkpoint target during merge: {root}")
            self._submodule_checkout_sync(root)
            self._git.clean_require(root)
            project["merged"] = True
            atomic_json_write(journal_path, journal)

    def merged_exact_require(
        self,
        checkpoint: Checkpoint,
        *,
        expected_origin_by_project_path_map: dict[str, str],
    ) -> None:
        """Require local and remote main to equal every exact selected commit."""

        workspace_root = self._coordination.root.parent
        for project in checkpoint.project_list:
            root = workspace_repository_resolve(workspace_root, project.project_path)
            self._origin_require(
                root,
                project_path=project.project_path,
                expected_origin_by_project_path_map=expected_origin_by_project_path_map,
            )
            self._git.fetch(root)
            self._submodule_checkout_sync(root)
            self._git.clean_require(root)
            if (
                self._git.branch_get(root) != "main"
                or self._git.commit_get(root) != project.git_commit_final
                or self._git.commit_get(root, "refs/remotes/origin/main") != project.git_commit_final
            ):
                raise GoalLifecycleError(f"Merged main is not the exact selected checkpoint: {project.project_path}")

    def fix_forward_ancestry_require(
        self,
        checkpoint: Checkpoint,
        *,
        previous_by_path_map: dict[str, str],
    ) -> None:
        """Require every replacement commit to descend from the partial merge snapshot."""

        workspace_root = self._coordination.root.parent
        for project in checkpoint.project_list:
            root = workspace_repository_resolve(workspace_root, project.project_path)
            self._git.ancestor_require(
                root,
                previous_by_path_map[project.project_path],
                project.git_commit_final,
                label=f"{project.project_path} fix-forward ancestry",
            )

    def _origin_require(
        self,
        root: Path,
        *,
        project_path: str,
        expected_origin_by_project_path_map: dict[str, str],
    ) -> None:
        expected_origin = expected_origin_by_project_path_map.get(project_path)
        if expected_origin is None or self._git.origin_url_get(root) != expected_origin:
            raise GoalLifecycleError(f"Merge repository origin changed: {project_path}")

    def _submodule_checkout_sync(self, root: Path) -> None:
        """Move clean main-checkout submodules to exact merged gitlinks."""

        if not (root / ".gitmodules").is_file():
            return
        self._git.run(root, ["submodule", "sync", "--recursive"])
        self._git.run(
            root,
            [
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "update",
                "--init",
                "--recursive",
                "--checkout",
            ],
        )
