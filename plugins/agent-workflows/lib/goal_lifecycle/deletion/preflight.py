"""Accepted-checkpoint and repository preconditions for goal deletion."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.bootstrap_exception import (
    coordination_bootstrap_exception_optional_get,
    coordination_bootstrap_exception_validate,
)
from goal_lifecycle.checkpoint.model import CheckpointDocument
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.task.model import TaskState
from goal_lifecycle.task.gitlink import task_owned_submodule_target_list_get
from goal_lifecycle.yaml_document import yaml_document_load


class GoalDeletionPreflight:
    """Freeze the exact accepted participant and optional bootstrap-exception identity."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        """Initialize the goal deletion preflight dependencies.

        Args:
            coordination: Coordination.
            git: Git command boundary.
        """

        self._coordination = coordination
        self._git = git

    def project_list_get(self, state: TaskState) -> list[dict[str, str]]:
        """Return the complete deletion snapshot after proving every Git precondition.

        Args:
            state: Exact runtime state.

        Returns:
            The complete deletion snapshot after proving every Git precondition.
        """

        self._coordination.synchronize_require()
        checkpoint_path = self._coordination.task_directory_get(state.common_prefix) / "checkpoint.yaml"
        self._coordination.task_directory_shape_require(state.common_prefix, complete=True)
        document = CheckpointDocument.from_payload(yaml_document_load(checkpoint_path))
        if (
            not document.checkpoint_list
            or document.accepted_checkpoint_id != document.checkpoint_list[-1].checkpoint_id
        ):
            raise GoalLifecycleError("Goal deletion requires the latest full checkpoint to be accepted")
        accepted = document.checkpoint_list[-1]
        accepted_by_path_map = {item.project_path: item.git_commit_final for item in accepted.project_list}
        workspace_root = self._coordination.root.parent.resolve(strict=True)
        project_list: list[dict[str, str]] = []
        for repository in state.repository_list:
            main_root = Path(repository.main_root).resolve(strict=True)
            task_root = Path(repository.task_root)
            project_path = main_root.relative_to(workspace_root).as_posix()
            if accepted_by_path_map.get(project_path) is None:
                raise GoalLifecycleError(f"Accepted checkpoint omits task repository: {project_path}")
            expected_commit = accepted_by_path_map[project_path]
            if self._git.origin_url_get(main_root) != repository.origin_url:
                raise GoalLifecycleError(f"Goal deletion repository origin changed: {main_root}")
            main_commit = self._git.synchronized_main_require(main_root)
            self._git.ancestor_require(
                main_root,
                expected_commit,
                main_commit,
                label=f"{project_path} accepted ancestry",
            )
            self._task_worktree_require(
                common_prefix=state.common_prefix,
                expected_commit=expected_commit,
                main_commit=main_commit,
                project_path=project_path,
                task_root=task_root,
            )
            self._task_ref_require(
                common_prefix=state.common_prefix,
                expected_commit=expected_commit,
                main_root=main_root,
            )
            project_list.append(
                {
                    "git_commit_final": expected_commit,
                    "main_root": str(main_root),
                    "project_path": project_path,
                    "task_root": str(task_root),
                }
            )
        if set(accepted_by_path_map) != {item["project_path"] for item in project_list}:
            raise GoalLifecycleError("Accepted checkpoint and sealed participant set differ")
        return sorted(project_list, key=lambda item: item["project_path"])

    def bootstrap_exception_payload_get(self, state: TaskState) -> dict[str, object] | None:
        """Return the exact current self-hosting exception when this task owns it.

        Args:
            state: Exact runtime state.

        Returns:
            The exact current self-hosting exception when this task owns it.
        """

        exception = coordination_bootstrap_exception_optional_get(self._coordination.root, git=self._git)
        if exception is None:
            return None
        if exception.common_prefix != state.common_prefix:
            raise GoalLifecycleError("Another task owns the coordination bootstrap exception")
        coordination_bootstrap_exception_validate(self._coordination.root, exception, git=self._git)
        return exception.payload_get()

    def submodule_list_get(self, state: TaskState) -> list[dict[str, str]]:
        """Freeze every task-owned submodule ref and accepted parent-gitlink identity.

        Args:
            state: Exact runtime state.

        Returns:
            Requested values in deterministic order.
        """

        checkpoint_path = self._coordination.task_directory_get(state.common_prefix) / "checkpoint.yaml"
        document = CheckpointDocument.from_payload(yaml_document_load(checkpoint_path))
        if (
            not document.checkpoint_list
            or document.accepted_checkpoint_id != document.checkpoint_list[-1].checkpoint_id
        ):
            raise GoalLifecycleError("Goal deletion requires the latest full checkpoint to be accepted")
        accepted_by_path_map = {
            item.project_path: item.git_commit_final for item in document.checkpoint_list[-1].project_list
        }
        workspace_root = self._coordination.root.parent.resolve(strict=True)
        result: list[dict[str, str]] = []
        for repository in state.repository_list:
            top_main_root = Path(repository.main_root).resolve(strict=True)
            project_path = top_main_root.relative_to(workspace_root).as_posix()
            top_commit = accepted_by_path_map[project_path]
            for target in task_owned_submodule_target_list_get(
                repository,
                top_level_commit=top_commit,
                git=self._git,
            ):
                path_text = target.path
                item = target.state
                gitlink_commit = target.git_commit_final
                task_root = Path(item.repository.task_root)
                self._git.clean_require(task_root)
                if self._git.branch_get(task_root) != state.common_prefix:
                    raise GoalLifecycleError(f"Task-owned submodule has another branch before deletion: {task_root}")
                task_commit = self._git.commit_get(task_root)
                if task_commit != gitlink_commit:
                    raise GoalLifecycleError(f"Accepted parent gitlink differs from task-owned submodule: {task_root}")
                if self._git.origin_url_get(task_root) != item.repository.origin_url:
                    raise GoalLifecycleError(f"Task-owned submodule origin changed before deletion: {task_root}")
                self._git.fetch(task_root)
                if self._git.commit_get(task_root, f"refs/remotes/origin/{state.common_prefix}") != task_commit:
                    raise GoalLifecycleError(f"Task-owned submodule branch is not fully pushed: {task_root}")
                self._git.ancestor_require(
                    task_root,
                    task_commit,
                    self._git.commit_get(task_root, "refs/remotes/origin/main"),
                    label=f"{path_text} merged task-owned submodule ancestry",
                )
                result.append(
                    {
                        "git_commit_final": task_commit,
                        "main_common_directory": str(self._git.common_directory_get(Path(item.repository.main_root))),
                        "main_root": item.repository.main_root,
                        "origin_url": item.repository.origin_url,
                        "parent_main_root": repository.main_root,
                        "path": path_text,
                        "task_root": item.repository.task_root,
                        "task_common_directory": str(self._git.common_directory_get(task_root)),
                    }
                )
        return sorted(result, key=lambda item: (item["parent_main_root"], item["path"]))

    def _task_worktree_require(
        self,
        *,
        common_prefix: str,
        expected_commit: str,
        main_commit: str,
        project_path: str,
        task_root: Path,
    ) -> None:
        """Require one task worktree to remain clean at its checkpoint commit.

        Args:
            common_prefix: Exact task common prefix.
            expected_commit: Expected commit.
            main_commit: Main commit.
            project_path: Exact filesystem path for project.
            task_root: Task root.
        """

        if not task_root.exists():
            raise GoalLifecycleError(f"Task worktree is absent before deletion was journaled: {task_root}")
        self._git.clean_require(task_root)
        if self._git.branch_get(task_root) != common_prefix:
            raise GoalLifecycleError(f"Task worktree branch differs: {task_root}")
        task_commit = self._git.commit_get(task_root)
        if task_commit != expected_commit or task_commit != self._git.commit_get(
            task_root,
            f"refs/remotes/origin/{common_prefix}",
        ):
            raise GoalLifecycleError(f"Task branch is not fully pushed: {task_root}")
        self._git.ancestor_require(task_root, task_commit, main_commit, label=f"{project_path} merged ancestry")

    def _task_ref_require(self, *, common_prefix: str, expected_commit: str, main_root: Path) -> None:
        """Require one local and remote task ref to retain its authorized commit.

        Args:
            common_prefix: Exact task common prefix.
            expected_commit: Expected commit.
            main_root: Main root.
        """

        for ref, label in (
            (f"refs/heads/{common_prefix}", "local"),
            (f"refs/remotes/origin/{common_prefix}", "remote"),
        ):
            if self._git.run(main_root, ["show-ref", "--verify", ref], check=False).returncode != 0:
                raise GoalLifecycleError(f"{label.capitalize()} task ref is absent before deletion: {main_root}")
            if self._git.commit_get(main_root, ref) != expected_commit:
                raise GoalLifecycleError(f"{label.capitalize()} task ref changed before deletion: {main_root}")
