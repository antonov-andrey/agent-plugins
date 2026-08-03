"""Recursive Git submodule graph discovery and checkout validation."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.task.repair import TaskRepairReport


class TaskSubmoduleGraph:
    """Own recursive gitlink discovery, initialization, and path closure."""

    def __init__(self, *, git: Git, repair_report: TaskRepairReport | None = None) -> None:
        """Initialize the recursive graph dependencies.

        Args:
            git: Git command boundary.
            repair_report: Repair report.
        """

        self._git = git
        self._repair_report = repair_report or TaskRepairReport()

    def initialize(
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
        """Initialize the exact recursive graph in dependency order.

        Args:
            repository_root: Repository root.
            common_prefix: Exact task common prefix.
            task_owned_path_set: Unique task-owned path values.
            detach_read_only: Detach read-only checkouts.
            repair_read_only: Repair clean read-only checkouts.
            interrupted_state_exists: Interrupted state exists.
            parent_path: Exact filesystem path for parent.
        """

        if not (repository_root / ".gitmodules").is_file():
            return
        self._git.run(repository_root, ["submodule", "sync", "--recursive"])
        for direct_path, expected_commit in self.direct_current_get(repository_root).items():
            full_path = f"{parent_path}/{direct_path}" if parent_path else direct_path
            submodule_root = repository_root / direct_path
            initialized = self.repository_is_exact_root(submodule_root)
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
            if not self.repository_is_exact_root(submodule_root):
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
            self.initialize(
                submodule_root,
                common_prefix=common_prefix,
                task_owned_path_set=task_owned_path_set,
                detach_read_only=detach_read_only,
                repair_read_only=repair_read_only,
                interrupted_state_exists=interrupted_state_exists,
                parent_path=full_path,
            )

    def recursive_current_get(self, repository_root: Path) -> dict[str, str]:
        """Return the recursive index gitlink map.

        Args:
            repository_root: Repository root.

        Returns:
            Recursive gitlink commits by path.
        """

        result: dict[str, str] = {}

        def populate(root: Path, parent_path: str) -> None:
            """Populate one initialized recursive checkout.

            Args:
                root: Exact owner root path.
                parent_path: Exact filesystem path for parent.
            """

            for direct_path, commit in self.direct_current_get(root).items():
                full_path = f"{parent_path}/{direct_path}" if parent_path else direct_path
                submodule_root = root / direct_path
                if not self.repository_is_exact_root(submodule_root):
                    raise GoalLifecycleError(f"Recursive submodule is uninitialized: {submodule_root}")
                result[full_path] = commit
                populate(submodule_root, full_path)

        populate(repository_root, "")
        return result

    def recursive_at_commit_get(self, repository_root: Path, *, commit: str) -> dict[str, str]:
        """Return the recursive gitlink graph rooted at one committed tree.

        Args:
            repository_root: Repository root.
            commit: Exact root repository commit.

        Returns:
            Recursive gitlink commits by path.
        """

        result: dict[str, str] = {}

        def populate(root: Path, owner_commit: str, parent_path: str) -> None:
            """Read one committed owner tree and descend through physical submodule repositories.

            Args:
                root: Exact owner root path.
                owner_commit: Exact owner commit.
                parent_path: Exact filesystem path for parent.
            """

            for direct_path, child_commit in self._direct_at_commit_get(root, commit=owner_commit).items():
                full_path = f"{parent_path}/{direct_path}" if parent_path else direct_path
                submodule_root = root / direct_path
                if not self.repository_is_exact_root(submodule_root):
                    raise GoalLifecycleError(
                        f"Committed recursive submodule repository is unavailable: {submodule_root}"
                    )
                result[full_path] = child_commit
                populate(submodule_root, child_commit, full_path)

        populate(repository_root, commit, "")
        return result

    def commit_contains_gitlink(self, repository_root: Path, *, commit: str) -> bool:
        """Return whether one committed owner tree contains a direct submodule boundary.

        Args:
            repository_root: Repository root.
            commit: Exact owner commit.

        Returns:
            Whether the committed tree contains a gitlink.
        """

        return bool(self._direct_at_commit_get(repository_root, commit=commit))

    def direct_current_get(self, repository_root: Path) -> dict[str, str]:
        """Return direct stage-zero gitlinks from the current index.

        Args:
            repository_root: Repository root.

        Returns:
            Direct gitlink commits by path.
        """

        return self._gitlink_payload_parse(
            self._git.run(repository_root, ["ls-files", "--stage", "-z"]).stdout,
            repository_root=repository_root,
            metadata_kind="index",
        )

    def owned_path_set_validate(self, path_set: set[str], *, complete_path_set: set[str]) -> None:
        """Require task-owned paths to form one closed recursive ancestry graph.

        Args:
            path_set: Unique path values.
            complete_path_set: Unique complete path values.
        """

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

    def repository_is_exact_root(self, path: Path) -> bool:
        """Return whether one path is the canonical root of its own Git repository.

        Args:
            path: Exact filesystem path.

        Returns:
            Whether the path is an exact non-symlink repository root.
        """

        if path.is_symlink() or not path.is_dir():
            return False
        result = self._git.run(path, ["rev-parse", "--show-toplevel"], check=False)
        if result.returncode != 0:
            return False
        try:
            return Path(result.stdout.decode().strip()).resolve(strict=True) == path.resolve(strict=True)
        except OSError, UnicodeDecodeError:
            return False

    def _direct_at_commit_get(self, repository_root: Path, *, commit: str) -> dict[str, str]:
        """Return direct gitlinks contained in one committed owner tree.

        Args:
            repository_root: Repository root.
            commit: Exact owner commit.

        Returns:
            Direct gitlink commits by path.
        """

        payload = self._git.run(repository_root, ["ls-tree", "-r", "-z", commit]).stdout
        return self._gitlink_payload_parse(
            payload,
            repository_root=repository_root,
            metadata_kind="tree",
        )

    def _gitlink_payload_parse(
        self,
        payload: bytes,
        *,
        repository_root: Path,
        metadata_kind: str,
    ) -> dict[str, str]:
        """Parse stage or tree records and return only gitlinks.

        Args:
            payload: NUL-delimited Git records.
            repository_root: Repository root.
            metadata_kind: Metadata record kind.

        Returns:
            Direct gitlink commits by path.
        """

        result: dict[str, str] = {}
        for entry in payload.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", maxsplit=1)
            field_list = metadata.split(b" ")
            if metadata_kind == "index":
                mode, commit, stage = field_list
                if mode != b"160000":
                    continue
                if stage != b"0":
                    raise GoalLifecycleError(f"Submodule has no single stage-zero gitlink: {repository_root}")
            else:
                mode, object_type, commit = field_list
                if mode != b"160000":
                    continue
                if object_type != b"commit":
                    raise GoalLifecycleError(f"Committed gitlink has another object type: {repository_root}")
            try:
                path = raw_path.decode("utf-8")
                commit_text = commit.decode("ascii")
            except UnicodeDecodeError as error:
                raise GoalLifecycleError("Goal lifecycle requires UTF-8 submodule paths") from error
            result[path] = commit_text
        return result

    def _uninitialized_collision_reject(self, path: Path) -> None:
        """Reject an uninitialized submodule path occupied by unrelated state.

        Args:
            path: Exact filesystem path.
        """

        if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
            raise GoalLifecycleError(f"Uninitialized submodule path contains independent content: {path}")

    def _ignored_checkout_collision_reject(self, root: Path, *, expected_commit: str) -> None:
        """Reject ignored objects that a gitlink checkout would overwrite.

        Args:
            root: Exact owner root path.
            expected_commit: Expected commit.
        """

        ignored = {
            item.decode("utf-8")
            for item in self._git.run(
                root,
                ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
            ).stdout.split(b"\0")
            if item
        }
        target = {
            item.decode("utf-8")
            for item in self._git.run(
                root,
                ["ls-tree", "-r", "--name-only", "-z", expected_commit],
            ).stdout.split(b"\0")
            if item
        }
        collision = _path_overlap_set_get(ignored, target)
        if collision:
            raise GoalLifecycleError(
                f"Ignored submodule objects would be overwritten by gitlink checkout in {root}: "
                + ", ".join(sorted(collision))
            )


def _path_overlap_set_get(left: set[str], right: set[str]) -> set[str]:
    """Return path identities whose repository trees overlap in either direction.

    Args:
        left: Left.
        right: Right.

    Returns:
        The path overlap set.
    """

    result: set[str] = set()
    for left_text in left:
        left_path = PurePosixPath(left_text)
        for right_text in right:
            right_path = PurePosixPath(right_text)
            if left_path == right_path or left_path in right_path.parents or right_path in left_path.parents:
                result.add(left_text)
    return result
