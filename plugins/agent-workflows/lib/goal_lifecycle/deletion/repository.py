"""Implementation task-worktree and branch retirement for goal deletion."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.bootstrap_exception import CoordinationBootstrapException
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_bytes_write, atomic_json_write
from goal_lifecycle.task.model import TaskState


class GoalTaskRepositoryRetirer:
    """Remove exact task worktrees, refs, and temporary provider excludes."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        """Initialize the goal task repository retirer dependencies.

        Args:
            coordination: Coordination.
            git: Git command boundary.
        """

        self._coordination = coordination
        self._git = git

    def worktrees_retire(
        self,
        *,
        bootstrap_exception: CoordinationBootstrapException | None,
        journal: dict[str, object],
        state: TaskState,
    ) -> None:
        """Reprove every recorded ref, then remove exact clean task worktrees.

        Args:
            bootstrap_exception: Bootstrap exception.
            journal: Journal.
            state: Exact runtime state.
        """

        expected_by_main_root_map = _expected_commit_by_main_root_map_get(journal)
        self._task_owned_submodule_worktrees_require(journal=journal, state=state)
        for repository in state.repository_list:
            main_root = Path(repository.main_root)
            task_root = Path(repository.task_root)
            expected_commit = expected_by_main_root_map[str(main_root.resolve(strict=True))]
            main_commit = self._git.synchronized_main_require(main_root)
            self._task_ref_exact_require(
                common_prefix=state.common_prefix,
                expected_commit=expected_commit,
                main_root=main_root,
            )
            self._git.ancestor_require(
                main_root,
                expected_commit,
                main_commit,
                label=f"{main_root.name} merged task ancestry",
            )
            if task_root.exists():
                self._git.clean_require(task_root)
                if self._git.branch_get(task_root) != state.common_prefix:
                    raise GoalLifecycleError(f"Task worktree branch changed before deletion: {task_root}")
                if self._git.commit_get(task_root) != expected_commit:
                    raise GoalLifecycleError(f"Task worktree commit changed before deletion: {task_root}")
                # Git requires --force to remove a worktree that contains an
                # initialized submodule even when every repository boundary is
                # clean.  The deletion preflight and the checks immediately
                # above prove the exact clean, pushed, merged identity first;
                # one --force therefore handles only Git's submodule guard and
                # does not weaken the lifecycle safety contract.
                self._git.run(main_root, ["worktree", "remove", "--force", str(task_root)])
        if bootstrap_exception is not None:
            main_commit = self._git.synchronized_main_require(self._coordination.root)
            self._task_ref_exact_require(
                common_prefix=bootstrap_exception.branch_name,
                expected_commit=bootstrap_exception.coordination_bootstrap_commit,
                main_root=self._coordination.root,
            )
            self._git.ancestor_require(
                self._coordination.root,
                bootstrap_exception.coordination_bootstrap_commit,
                main_commit,
                label="coordination bootstrap merged ancestry",
            )
            task_root = Path(bootstrap_exception.task_root)
            if task_root.exists():
                self._git.clean_require(task_root)
                if self._git.branch_get(task_root) != bootstrap_exception.branch_name:
                    raise GoalLifecycleError("Coordination bootstrap worktree branch changed before deletion")
                if self._git.commit_get(task_root) != bootstrap_exception.coordination_bootstrap_commit:
                    raise GoalLifecycleError("Coordination bootstrap worktree commit changed before deletion")
                self._git.run(self._coordination.root, ["worktree", "remove", str(task_root)])

    def _task_ref_exact_require(
        self,
        *,
        common_prefix: str,
        expected_commit: str,
        main_root: Path,
    ) -> None:
        """Require both local and fetched remote task refs at one recorded commit.

        Args:
            common_prefix: Exact task common prefix.
            expected_commit: Expected commit.
            main_root: Main root.
        """

        for ref, label in (
            (f"refs/heads/{common_prefix}", "Local"),
            (f"refs/remotes/origin/{common_prefix}", "Remote"),
        ):
            if self._git.run(main_root, ["show-ref", "--verify", ref], check=False).returncode != 0:
                raise GoalLifecycleError(f"{label} task ref disappeared before deletion: {main_root}")
            if self._git.commit_get(main_root, ref) != expected_commit:
                raise GoalLifecycleError(f"{label} task ref changed before deletion: {main_root}")

    def remote_refs_retire(
        self,
        *,
        bootstrap_exception: CoordinationBootstrapException | None,
        journal: dict[str, object],
        journal_path: Path,
        state: TaskState,
    ) -> None:
        """Remove every exact remote task ref with durable per-repository progress.

        Args:
            bootstrap_exception: Bootstrap exception.
            journal: Journal.
            journal_path: Exact filesystem path for journal.
            state: Exact runtime state.
        """

        owner_list = _ref_owner_list_get(journal, state=state)
        start_index = int(journal["repository_index"])
        for index, owner in enumerate(owner_list[start_index:], start=start_index):
            main_root = Path(owner["main_root"])
            self._git.fetch(main_root)
            remote_ref = f"refs/remotes/origin/{state.common_prefix}"
            exists = self._git.run(main_root, ["show-ref", "--verify", remote_ref], check=False).returncode == 0
            if exists:
                if self._git.commit_get(main_root, remote_ref) != owner["git_commit_final"]:
                    raise GoalLifecycleError(f"Remote task ref changed after deletion authorization: {main_root}")
                self._git.run(
                    main_root,
                    [
                        "push",
                        (f"--force-with-lease=refs/heads/{state.common_prefix}:" f"{owner['git_commit_final']}"),
                        "origin",
                        f":refs/heads/{state.common_prefix}",
                    ],
                )
            journal["repository_index"] = index + 1
            atomic_json_write(journal_path, journal)
        if bootstrap_exception is not None:
            remote_ref = f"refs/remotes/origin/{bootstrap_exception.branch_name}"
            exists = (
                self._git.run(
                    self._coordination.root,
                    ["show-ref", "--verify", remote_ref],
                    check=False,
                ).returncode
                == 0
            )
            if exists:
                if (
                    self._git.commit_get(self._coordination.root, remote_ref)
                    != bootstrap_exception.coordination_bootstrap_commit
                ):
                    raise GoalLifecycleError(
                        "Coordination bootstrap remote branch changed after deletion authorization"
                    )
                self._git.run(
                    self._coordination.root,
                    [
                        "push",
                        (
                            f"--force-with-lease=refs/heads/{bootstrap_exception.branch_name}:"
                            f"{bootstrap_exception.coordination_bootstrap_commit}"
                        ),
                        "origin",
                        f":refs/heads/{bootstrap_exception.branch_name}",
                    ],
                )

    def local_refs_retire(
        self,
        *,
        bootstrap_exception: CoordinationBootstrapException | None,
        journal: dict[str, object],
        journal_path: Path,
        state: TaskState,
    ) -> None:
        """Remove every exact local task ref with durable per-repository progress.

        Args:
            bootstrap_exception: Bootstrap exception.
            journal: Journal.
            journal_path: Exact filesystem path for journal.
            state: Exact runtime state.
        """

        owner_list = _ref_owner_list_get(journal, state=state)
        start_index = int(journal["repository_index"])
        for index, owner in enumerate(owner_list[start_index:], start=start_index):
            main_root = Path(owner["main_root"])
            local_ref = f"refs/heads/{state.common_prefix}"
            if self._git.run(main_root, ["show-ref", "--verify", local_ref], check=False).returncode == 0:
                if self._git.commit_get(main_root, local_ref) != owner["git_commit_final"]:
                    raise GoalLifecycleError(f"Local task ref changed after deletion authorization: {main_root}")
                self._git.run(main_root, ["worktree", "prune"])
                if owner["owner_kind"] == "submodule":
                    self._git.ancestor_require(
                        main_root,
                        owner["git_commit_final"],
                        self._git.commit_get(main_root, "refs/remotes/origin/main"),
                        label=f"{main_root.name} merged task-owned submodule ancestry",
                    )
                    self._git.run(
                        main_root,
                        ["update-ref", "-d", local_ref, owner["git_commit_final"]],
                    )
                else:
                    self._git.run(
                        main_root,
                        ["update-ref", "-d", local_ref, owner["git_commit_final"]],
                    )
            journal["repository_index"] = index + 1
            atomic_json_write(journal_path, journal)
        if bootstrap_exception is not None:
            local_ref = f"refs/heads/{bootstrap_exception.branch_name}"
            if (
                self._git.run(
                    self._coordination.root,
                    ["show-ref", "--verify", local_ref],
                    check=False,
                ).returncode
                == 0
            ):
                if (
                    self._git.commit_get(self._coordination.root, local_ref)
                    != bootstrap_exception.coordination_bootstrap_commit
                ):
                    raise GoalLifecycleError("Coordination bootstrap local branch changed after deletion authorization")
                self._git.run(
                    self._coordination.root,
                    [
                        "update-ref",
                        "-d",
                        local_ref,
                        bootstrap_exception.coordination_bootstrap_commit,
                    ],
                )

    def provider_excludes_retire(self, state: TaskState) -> None:
        """Remove each implementation repository's temporary worktree exclude.

        Args:
            state: Exact runtime state.
        """

        for repository in state.repository_list:
            main_root = Path(repository.main_root)
            gitignore_path = main_root / ".gitignore"
            if gitignore_path.is_file() and "/.worktree/" in gitignore_path.read_text(encoding="utf-8").splitlines():
                self.worktree_exclude_retire(main_root)

    def _task_owned_submodule_worktrees_require(self, *, journal: dict[str, object], state: TaskState) -> None:
        """Reprove every nested worktree and task ref before its parent tree is removed.

        Args:
            journal: Journal.
            state: Exact runtime state.
        """

        for item in journal["submodule_list"]:
            main_root = Path(item["main_root"])
            task_root = Path(item["task_root"])
            if self._git.origin_url_get(main_root) != item["origin_url"]:
                raise GoalLifecycleError(f"Task-owned submodule origin changed before deletion: {main_root}")
            self._git.fetch(main_root)
            remote_ref = f"refs/remotes/origin/{state.common_prefix}"
            if self._git.run(main_root, ["show-ref", "--verify", remote_ref], check=False).returncode != 0 or (
                self._git.commit_get(main_root, remote_ref) != item["git_commit_final"]
            ):
                raise GoalLifecycleError(f"Task-owned submodule remote task ref changed before deletion: {main_root}")
            self._git.ancestor_require(
                main_root,
                item["git_commit_final"],
                self._git.commit_get(main_root, "refs/remotes/origin/main"),
                label=f"{item['path']} merged task-owned submodule ancestry",
            )
            if task_root.exists():
                self._git.clean_require(task_root)
                if self._git.branch_get(task_root) != state.common_prefix:
                    raise GoalLifecycleError(f"Task-owned submodule branch changed before deletion: {task_root}")
                if self._git.commit_get(task_root) != item["git_commit_final"]:
                    raise GoalLifecycleError(f"Task-owned submodule commit changed before deletion: {task_root}")

    def worktree_exclude_retire(self, main_root: Path) -> None:
        """Remove one exact provider-owned common-directory exclude line.

        Args:
            main_root: Main root.
        """

        exclude_path = self._git.common_directory_get(main_root) / "info" / "exclude"
        if not exclude_path.is_file():
            return
        line_list = exclude_path.read_text(encoding="utf-8").splitlines()
        if "/.worktree/" not in line_list:
            return
        remaining_line_list = [line for line in line_list if line != "/.worktree/"]
        payload = (("\n".join(remaining_line_list) + "\n") if remaining_line_list else "").encode()
        atomic_bytes_write(exclude_path, payload, mode=0o644)


def _expected_commit_by_main_root_map_get(journal: dict[str, object]) -> dict[str, str]:
    """Return the prevalidated exact commit map retained in one deletion journal.

    Args:
        journal: Journal.

    Returns:
        The prevalidated exact commit map retained in one deletion journal.
    """

    return {str(item["main_root"]): str(item["git_commit_final"]) for item in journal["project_list"]}


def _ref_owner_list_get(journal: dict[str, object], *, state: TaskState) -> list[dict[str, str]]:
    """Return top-level and task-owned ref owners in one deterministic journal order.

    Args:
        journal: Journal.
        state: Exact runtime state.

    Returns:
        Top-level and task-owned ref owners in deterministic journal order.
    """

    result = [
        {
            "git_commit_final": str(item["git_commit_final"]),
            "main_root": str(item["main_root"]),
            "owner_kind": "top-level",
        }
        for item in journal["project_list"]
    ]
    result.extend(
        {
            "git_commit_final": str(item["git_commit_final"]),
            "main_root": str(item["main_root"]),
            "owner_kind": "submodule",
        }
        for item in journal["submodule_list"]
    )
    expected_count = len(state.repository_list) + sum(
        len(item.task_owned_submodule_list) for item in state.repository_list
    )
    if len(result) != expected_count:
        raise GoalLifecycleError("Goal deletion ref-owner snapshot is incomplete")
    return result
