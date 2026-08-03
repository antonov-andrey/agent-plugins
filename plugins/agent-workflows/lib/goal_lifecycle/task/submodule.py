"""Recursive read-only and explicitly task-owned submodule preparation."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write, json_object_load
from goal_lifecycle.task.boundary import RepositoryBoundaryManager
from goal_lifecycle.task.model import (
    RepositoryState,
    SubmoduleGitlinkState,
    TaskOwnedSubmoduleState,
    TaskState,
)
from goal_lifecycle.task.repair import TaskRepairReport


class TaskSubmoduleManager:
    """Initialize every recursive boundary and delegate only explicitly owned paths."""

    def __init__(
        self,
        *,
        git: Git,
        boundary_manager: RepositoryBoundaryManager,
        repair_report: TaskRepairReport | None = None,
    ) -> None:
        self._git = git
        self._boundary_manager = boundary_manager
        self._repair_report = repair_report or TaskRepairReport()

    def prepare(
        self,
        *,
        main_root: Path,
        task_root: Path,
        common_prefix: str,
        requested_path_set: set[str],
        previous_state: RepositoryState | None,
    ) -> tuple[tuple[SubmoduleGitlinkState, ...], tuple[TaskOwnedSubmoduleState, ...]]:
        """Prepare recursive submodules and return the complete frozen graph and owned states."""

        previous_owned_by_path_map = (
            {item.path: item for item in previous_state.task_owned_submodule_list} if previous_state else {}
        )
        if set(previous_owned_by_path_map) - requested_path_set:
            raise GoalLifecycleError("Prepare cannot remove a task-owned submodule from an existing task")
        self._initialize_recursive(
            main_root,
            common_prefix=common_prefix,
            task_owned_path_set=set(),
            detach_read_only=False,
            repair_read_only=False,
        )
        self._initialize_recursive(
            task_root,
            common_prefix=common_prefix,
            task_owned_path_set=requested_path_set,
            detach_read_only=True,
            repair_read_only=True,
            interrupted_state_exists=previous_state is not None,
        )
        task_gitlink_by_path_map = self._recursive_gitlink_by_path_map_get(task_root)
        main_gitlink_by_path_map = self._recursive_gitlink_by_path_map_get(main_root)
        if set(task_gitlink_by_path_map) != set(main_gitlink_by_path_map):
            raise GoalLifecycleError(f"Main and task recursive submodule sets differ during preparation: {task_root}")
        self._owned_path_set_validate(requested_path_set, complete_path_set=set(task_gitlink_by_path_map))

        baseline_by_path_map = (
            {item.path: item.baseline_commit for item in previous_state.submodule_gitlink_list}
            if previous_state and previous_state.submodule_gitlink_list
            else dict(task_gitlink_by_path_map)
        )
        if set(baseline_by_path_map) != set(task_gitlink_by_path_map):
            raise GoalLifecycleError("Recursive submodule set changed after initial task preparation")
        owned_state_list: list[TaskOwnedSubmoduleState] = []
        for path_text in sorted(requested_path_set, key=lambda item: (len(PurePosixPath(item).parts), item)):
            main_submodule_root = main_root / path_text
            task_submodule_root = task_root / path_text
            previous = previous_owned_by_path_map.get(path_text)
            baseline_commit = baseline_by_path_map[path_text]
            self._owned_branch_prepare(
                main_root=main_submodule_root,
                task_root=task_submodule_root,
                path_text=path_text,
                common_prefix=common_prefix,
                baseline_commit=baseline_commit,
                previous=previous,
            )
            boundary = self._boundary_manager.prepare(
                main_root=main_submodule_root,
                task_root=task_submodule_root,
                baseline_commit=baseline_commit,
                common_prefix=common_prefix,
                previous_state=previous.repository if previous else None,
            )
            owned_state_list.append(TaskOwnedSubmoduleState(path=path_text, repository=boundary))
        return (
            tuple(
                SubmoduleGitlinkState(path=path, baseline_commit=baseline_by_path_map[path])
                for path in sorted(baseline_by_path_map)
            ),
            tuple(owned_state_list),
        )

    def preflight(self, main_root: Path, *, common_prefix: str, requested_path_set: set[str]) -> None:
        """Discover and classify the complete recursive main graph before worktree creation."""

        self._initialize_recursive(
            main_root,
            common_prefix=common_prefix,
            task_owned_path_set=set(),
            detach_read_only=False,
            repair_read_only=False,
        )
        complete_path_set = set(self._recursive_gitlink_by_path_map_get(main_root))
        self._owned_path_set_validate(requested_path_set, complete_path_set=complete_path_set)

    def validate(
        self,
        repository: RepositoryState,
        *,
        task_state: TaskState,
        main_integrity_required: bool = True,
    ) -> None:
        """Prove the complete recursive set, read-only gitlinks, and task-owned descendants."""

        task_root = Path(repository.task_root).resolve(strict=True)
        owned_by_path_map = {item.path: item for item in repository.task_owned_submodule_list}
        recorded_by_path_map = {item.path: item.baseline_commit for item in repository.submodule_gitlink_list}
        self._initialize_recursive(
            task_root,
            common_prefix=task_state.common_prefix,
            task_owned_path_set=set(owned_by_path_map),
            detach_read_only=True,
            repair_read_only=True,
            interrupted_state_exists=True,
        )
        current_by_path_map = self._recursive_gitlink_by_path_map_get(task_root)
        if current_by_path_map.keys() != recorded_by_path_map.keys():
            raise GoalLifecycleError(f"Recursive submodule set changed after preparation: {task_root}")
        self._owned_path_set_validate(set(owned_by_path_map), complete_path_set=set(current_by_path_map))
        for path_text, baseline_commit in recorded_by_path_map.items():
            submodule_root = task_root / path_text
            index_commit = current_by_path_map[path_text]
            effective_commit = self._git.commit_get(submodule_root)
            owned = owned_by_path_map.get(path_text)
            if owned is None:
                if index_commit != baseline_commit or effective_commit != baseline_commit:
                    raise GoalLifecycleError(f"Read-only submodule moved from its recorded gitlink: {submodule_root}")
                self._git.clean_require(submodule_root)
                continue
            if self._git.branch_get(submodule_root) != task_state.common_prefix:
                raise GoalLifecycleError(f"Task-owned submodule has another branch: {submodule_root}")
            for candidate, label in (
                (index_commit, "index gitlink"),
                (effective_commit, "effective commit"),
            ):
                self._git.ancestor_require(
                    submodule_root,
                    baseline_commit,
                    candidate,
                    label=f"{path_text} task-owned submodule {label}",
                )
            self._boundary_manager.validate(
                owned.repository,
                task_state=task_state,
                main_integrity_required=main_integrity_required,
            )

    def pending_retire(self, repository: RepositoryState, *, common_prefix: str) -> None:
        """Retire exact submodule pending markers after replicated state commits."""

        for item in repository.task_owned_submodule_list:
            marker = self._pending_marker_path_get(Path(item.repository.task_root), common_prefix=common_prefix)
            try:
                marker.unlink()
            except FileNotFoundError:
                pass

    def _owned_branch_prepare(
        self,
        *,
        main_root: Path,
        task_root: Path,
        path_text: str,
        common_prefix: str,
        baseline_commit: str,
        previous: TaskOwnedSubmoduleState | None,
    ) -> None:
        marker_path = self._pending_marker_path_get(task_root, common_prefix=common_prefix)
        expected_marker = {
            "schema_version": 1,
            "baseline_commit": baseline_commit,
            "common_prefix": common_prefix,
            "main_root": str(main_root),
            "origin_url": self._git.origin_url_get(main_root),
            "path": path_text,
            "task_root": str(task_root),
        }
        marker_preexisting = marker_path.exists()
        if marker_preexisting:
            if json_object_load(marker_path, label="pending task-owned submodule") != expected_marker:
                raise GoalLifecycleError(f"Pending task-owned submodule identity differs: {task_root}")
        elif previous is None:
            self._git.clean_require(task_root)
            current_branch = (
                self._git.run(
                    task_root,
                    ["symbolic-ref", "--quiet", "--short", "HEAD"],
                    check=False,
                )
                .stdout.decode()
                .strip()
            )
            if current_branch:
                raise GoalLifecycleError(f"Unrecorded task-owned submodule is unexpectedly on a branch: {task_root}")
            atomic_json_write(marker_path, expected_marker)

        current_branch = (
            self._git.run(task_root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
            .stdout.decode()
            .strip()
        )
        if current_branch != common_prefix:
            if previous is not None:
                raise GoalLifecycleError(f"Recorded task-owned submodule branch changed: {task_root}")
            branch_ref = f"refs/heads/{common_prefix}"
            branch_exists = self._git.run(task_root, ["show-ref", "--verify", branch_ref], check=False).returncode == 0
            if branch_exists:
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

    def _initialize_recursive(
        self,
        repository_root: Path,
        *,
        common_prefix: str,
        task_owned_path_set: set[str],
        detach_read_only: bool,
        repair_read_only: bool,
        interrupted_state_exists: bool = False,
        parent_path: str = "",
    ) -> None:
        if not (repository_root / ".gitmodules").is_file():
            return
        self._git.run(repository_root, ["submodule", "sync", "--recursive"])
        for direct_path, expected_commit in self._direct_gitlink_by_path_map_get(repository_root).items():
            full_path = f"{parent_path}/{direct_path}" if parent_path else direct_path
            submodule_root = repository_root / direct_path
            initialized = self._repository_is_exact_root(submodule_root)
            owned = full_path in task_owned_path_set
            if not initialized:
                self._uninitialized_collision_reject(submodule_root)
                self._git.run(
                    repository_root,
                    [
                        "-c",
                        "protocol.file.allow=always",
                        "submodule",
                        "update",
                        "--init",
                        "--checkout",
                        "--",
                        f"./{direct_path}",
                    ],
                )
                if interrupted_state_exists:
                    self._repair_report.record(f"submodule-checkout-restored:{submodule_root}")
            elif not owned:
                self._git.clean_require(submodule_root)
                if self._git.commit_get(submodule_root) != expected_commit:
                    if not repair_read_only:
                        raise GoalLifecycleError(
                            f"Canonical main submodule differs from its exact gitlink: {submodule_root}"
                        )
                    self._ignored_checkout_collision_reject(submodule_root, expected_commit=expected_commit)
                    self._git.run(
                        repository_root,
                        [
                            "-c",
                            "protocol.file.allow=always",
                            "submodule",
                            "update",
                            "--checkout",
                            "--",
                            f"./{direct_path}",
                        ],
                    )
                    self._repair_report.record(f"read-only-submodule-checkout-repaired:{submodule_root}")
            if not self._repository_is_exact_root(submodule_root):
                raise GoalLifecycleError(f"Submodule is not initialized at its exact root: {submodule_root}")
            current_commit = self._git.commit_get(submodule_root)
            if owned:
                self._git.ancestor_require(
                    submodule_root,
                    expected_commit,
                    current_commit,
                    label=f"{full_path} task-owned submodule checkout",
                )
            elif current_commit != expected_commit:
                raise GoalLifecycleError(f"Read-only submodule is not at its exact gitlink: {submodule_root}")
            elif detach_read_only and (
                self._git.run(
                    submodule_root,
                    ["symbolic-ref", "--quiet", "--short", "HEAD"],
                    check=False,
                ).returncode
                == 0
            ):
                self._git.run(submodule_root, ["switch", "--detach", expected_commit])
            self._initialize_recursive(
                submodule_root,
                common_prefix=common_prefix,
                task_owned_path_set=task_owned_path_set,
                detach_read_only=detach_read_only,
                repair_read_only=repair_read_only,
                interrupted_state_exists=interrupted_state_exists,
                parent_path=full_path,
            )

    def _recursive_gitlink_by_path_map_get(self, repository_root: Path) -> dict[str, str]:
        result: dict[str, str] = {}

        def populate(root: Path, parent_path: str) -> None:
            for direct_path, commit in self._direct_gitlink_by_path_map_get(root).items():
                full_path = f"{parent_path}/{direct_path}" if parent_path else direct_path
                submodule_root = root / direct_path
                if not self._repository_is_exact_root(submodule_root):
                    raise GoalLifecycleError(f"Recursive submodule is uninitialized: {submodule_root}")
                result[full_path] = commit
                populate(submodule_root, full_path)

        populate(repository_root, "")
        return result

    def _direct_gitlink_by_path_map_get(self, repository_root: Path) -> dict[str, str]:
        payload = self._git.run(repository_root, ["ls-files", "--stage", "-z"]).stdout
        result: dict[str, str] = {}
        for entry in payload.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", maxsplit=1)
            mode, commit, stage = metadata.split(b" ")
            if mode != b"160000":
                continue
            if stage != b"0":
                raise GoalLifecycleError(f"Submodule has no single stage-zero gitlink: {repository_root}")
            try:
                path = raw_path.decode("utf-8")
                commit_text = commit.decode("ascii")
            except UnicodeDecodeError as error:
                raise GoalLifecycleError("Goal lifecycle requires UTF-8 submodule paths") from error
            result[path] = commit_text
        return result

    def _owned_path_set_validate(self, path_set: set[str], *, complete_path_set: set[str]) -> None:
        unknown = path_set - complete_path_set
        if unknown:
            raise GoalLifecycleError("Task-owned submodule is not one recursive gitlink: " + ", ".join(sorted(unknown)))
        for path_text in sorted(path_set):
            path = PurePosixPath(path_text)
            missing = [
                candidate
                for candidate in complete_path_set
                if PurePosixPath(candidate) in path.parents and candidate not in path_set
            ]
            if missing:
                raise GoalLifecycleError(
                    f"Nested task-owned submodule requires every submodule ancestor: {path_text}; "
                    + ", ".join(sorted(missing))
                )

    def _repository_is_exact_root(self, path: Path) -> bool:
        if path.is_symlink() or not path.is_dir():
            return False
        result = self._git.run(path, ["rev-parse", "--show-toplevel"], check=False)
        if result.returncode != 0:
            return False
        try:
            return Path(result.stdout.decode().strip()).resolve(strict=True) == path.resolve(strict=True)
        except OSError, UnicodeDecodeError:
            return False

    def _uninitialized_collision_reject(self, path: Path) -> None:
        if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
            raise GoalLifecycleError(f"Uninitialized submodule path contains independent content: {path}")

    def _ignored_checkout_collision_reject(self, root: Path, *, expected_commit: str) -> None:
        ignored = {
            item.decode("utf-8")
            for item in self._git.run(
                root, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"]
            ).stdout.split(b"\0")
            if item
        }
        target = {
            item.decode("utf-8")
            for item in self._git.run(root, ["ls-tree", "-r", "--name-only", "-z", expected_commit]).stdout.split(b"\0")
            if item
        }
        collision = _path_overlap_set_get(ignored, target)
        if collision:
            raise GoalLifecycleError(
                f"Ignored submodule objects would be overwritten by gitlink checkout in {root}: "
                + ", ".join(sorted(collision))
            )

    def _pending_marker_path_get(self, task_root: Path, *, common_prefix: str) -> Path:
        return (
            self._git.common_directory_get(task_root)
            / "agent-workflows"
            / "task"
            / common_prefix
            / "pending-participating-submodule.json"
        )


def _path_overlap_set_get(left: set[str], right: set[str]) -> set[str]:
    result: set[str] = set()
    for left_text in left:
        left_path = PurePosixPath(left_text)
        for right_text in right:
            right_path = PurePosixPath(right_text)
            if left_path == right_path or left_path in right_path.parents or right_path in left_path.parents:
                result.add(left_text)
    return result
