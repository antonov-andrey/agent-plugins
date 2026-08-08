"""Canonical checkout discovery and safe Git worktree operations."""

from __future__ import annotations

from collections.abc import Sequence
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess

from git_origin.identity import GitOriginError, origin_identity_get
from json_contract import JsonContractError, json_load_strict
from task_workspace.model import (
    RepositoryRequest,
    RepositoryWorkspaceState,
    TaskWorkspaceError,
    WorkspaceConfig,
    issue_identifier_validate,
)

_GIT_REDIRECTION_NAME_SET = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_WORK_TREE",
    }
)


def git_command_run(
    repository: Path,
    argument_list: Sequence[str],
    *,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run Git without inherited repository-redirection variables."""

    environment_by_name_map = os.environ.copy()
    for name in list(environment_by_name_map):
        if name in _GIT_REDIRECTION_NAME_SET or name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            environment_by_name_map.pop(name, None)
    environment_by_name_map["GIT_TERMINAL_PROMPT"] = "0"
    completed_process = subprocess.run(
        ["git", "-C", str(repository), *argument_list],
        capture_output=True,
        check=False,
        env=environment_by_name_map,
        input=input_bytes,
    )
    if check and completed_process.returncode != 0:
        detail = (completed_process.stderr or completed_process.stdout).decode("utf-8", errors="replace").strip()
        raise TaskWorkspaceError(
            f"Git command failed in {repository}: git {' '.join(argument_list)}: "
            f"{detail or f'exit status {completed_process.returncode}'}"
        )
    return completed_process


def git_command_text_get(repository: Path, argument_list: Sequence[str], *, check: bool = True) -> str:
    """Return strict UTF-8 output from one Git command."""

    return git_command_run(repository, argument_list, check=check).stdout.decode("utf-8", errors="strict").strip()


def _workspace_origin_identity_get(value: str) -> str:
    """Translate shared Git-origin validation into the workspace boundary.

    Args:
        value: Configured or requested origin URL.

    Returns:
        Canonical comparison identity.
    """

    try:
        return origin_identity_get(value)
    except GitOriginError as error:
        raise TaskWorkspaceError(str(error)) from error


class WorkspaceRepository:
    """Own one discovered canonical checkout and its task worktree state."""

    def __init__(self, main_root: Path, request: RepositoryRequest) -> None:
        """Bind one exact canonical checkout.

        Args:
            main_root: Discovered main checkout root.
            request: User-approved repository request.
        """

        self.main_root = main_root.resolve(strict=True)
        self.request = request
        metadata_directory = self.main_root / ".git"
        if metadata_directory.is_symlink() or not metadata_directory.is_dir():
            raise TaskWorkspaceError(f"Canonical checkout must own one .git directory: {self.main_root}")
        discovered_root = Path(git_command_text_get(self.main_root, ("rev-parse", "--show-toplevel"))).resolve(
            strict=True
        )
        if discovered_root != self.main_root:
            raise TaskWorkspaceError("Canonical checkout discovery returned another root")
        if metadata_directory.resolve(strict=True) != self._common_directory_get():
            raise TaskWorkspaceError("Canonical checkout must own its physical Git common directory")
        configured_origin = git_command_text_get(self.main_root, ("remote", "get-url", "origin"))
        self.origin_identity = _workspace_origin_identity_get(configured_origin)
        if self.origin_identity != request.origin_identity:
            raise TaskWorkspaceError("Canonical checkout origin differs from the approved issue contract")
        git_command_run(self.main_root, ("check-ref-format", "--branch", request.base_branch))

    @classmethod
    def from_config(cls, config: WorkspaceConfig, request: RepositoryRequest) -> "WorkspaceRepository":
        """Find one unique direct-child main checkout by canonical origin.

        Args:
            config: Exact workspace root.
            request: Approved repository identity.

        Returns:
            Unique bound repository.
        """

        requested_identity = request.origin_identity
        candidate_list: list[Path] = []
        root_candidate_list = [
            config.root,
            *sorted(path for path in config.root.iterdir() if not path.is_symlink() and path.is_dir()),
        ]
        for candidate in root_candidate_list:
            metadata_directory = candidate / ".git"
            if metadata_directory.is_symlink() or not metadata_directory.is_dir():
                continue
            origin = git_command_text_get(candidate, ("remote", "get-url", "origin"), check=False)
            if not origin:
                continue
            try:
                candidate_identity = _workspace_origin_identity_get(origin)
            except TaskWorkspaceError:
                # An unrelated checkout with a non-canonical remote is outside this
                # request. It must not make discovery of the exact approved origin
                # depend on every sibling repository being well configured.
                continue
            if candidate_identity == requested_identity:
                candidate_list.append(candidate)
        if len(candidate_list) != 1:
            raise TaskWorkspaceError(
                f"Workspace must contain exactly one canonical checkout for {requested_identity}; found {len(candidate_list)}"
            )
        return cls(candidate_list[0], request)

    def state_path_get(self, issue_identifier: str) -> Path:
        """Return the private Git-admin state path for one issue.

        Args:
            issue_identifier: Exact Linear issue identifier.

        Returns:
            Private state path.
        """

        issue_identifier = issue_identifier_validate(issue_identifier)
        common_directory = self._common_directory_get()
        return common_directory / "linear-agent-tools" / "task" / issue_identifier.lower() / "workspace.json"

    def state_read(self, issue_identifier: str) -> RepositoryWorkspaceState | None:
        """Read one private state when present.

        Args:
            issue_identifier: Exact Linear issue identifier.

        Returns:
            Typed state or absence.
        """

        parent = self._state_parent_get(issue_identifier, create=False)
        if parent is None:
            return None
        path = parent / "workspace.json"
        if not path.exists():
            return None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                metadata = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) & 0o077
                ):
                    raise TaskWorkspaceError("Workspace private state is not one private user-owned ordinary file")
                payload = json_load_strict(handle.read())
        except TaskWorkspaceError:
            raise
        except (OSError, JsonContractError) as error:
            raise TaskWorkspaceError("Workspace private state is malformed") from error
        return RepositoryWorkspaceState.from_payload(payload)

    def state_write(self, issue_identifier: str, state: RepositoryWorkspaceState) -> None:
        """Atomically replace one private state and fsync its parent.

        Args:
            issue_identifier: Exact Linear issue identifier.
            state: Exact repository workspace state.
        """

        parent = self._state_parent_get(issue_identifier, create=True)
        if parent is None:
            raise TaskWorkspaceError("Workspace private state parent was not created")
        path = parent / "workspace.json"
        temporary = path.parent / f".{path.name}.{secrets.token_hex(12)}.tmp"
        encoded = (
            json.dumps(
                state.payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            _directory_sync(path.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def state_delete(self, issue_identifier: str) -> None:
        """Delete exact private state idempotently.

        Args:
            issue_identifier: Exact Linear issue identifier.
        """

        parent = self._state_parent_get(issue_identifier, create=False)
        if parent is None:
            return
        path = parent / "workspace.json"
        path.unlink(missing_ok=True)
        if path.parent.exists():
            _directory_sync(parent)
            try:
                parent.rmdir()
            except OSError:
                return
            _directory_sync(parent.parent)

    def _common_directory_get(self) -> Path:
        """Return one physical canonical Git common directory."""

        candidate = Path(
            git_command_text_get(
                self.main_root,
                ("rev-parse", "--path-format=absolute", "--git-common-dir"),
            )
        )
        if candidate.is_symlink() or not candidate.is_dir():
            raise TaskWorkspaceError("Git common directory must be one physical directory")
        resolved = candidate.resolve(strict=True)
        if resolved != candidate:
            raise TaskWorkspaceError("Git common directory must use one canonical physical path")
        return resolved

    def _state_parent_get(self, issue_identifier: str, *, create: bool) -> Path | None:
        """Return a physical private-state parent without following symlinks."""

        issue_identifier = issue_identifier_validate(issue_identifier)
        current = self._common_directory_get()
        for name in ("linear-agent-tools", "task", issue_identifier.lower()):
            child = current / name
            try:
                metadata = child.stat(follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    return None
                child.mkdir(mode=0o700)
                _directory_sync(current)
                metadata = child.stat(follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise TaskWorkspaceError("Workspace private state parent must be one user-owned physical directory")
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise TaskWorkspaceError("Workspace private state parent must not grant group or other access")
            current = child
        return current

    def state_identity_require(self, issue_identifier: str, state: RepositoryWorkspaceState) -> None:
        """Require private state to match this exact issue and repository contract.

        Args:
            issue_identifier: Canonical Linear issue identifier.
            state: Current private workspace state.
        """

        issue_identifier_validate(issue_identifier)
        if self.request.expected_baseline_commit and state.baseline_commit != self.request.expected_baseline_commit:
            raise TaskWorkspaceError("Private workspace baseline differs from Linear attempt evidence")

    def fetch(self) -> None:
        """Fetch current origin refs without changing a checked-out branch."""

        git_command_run(self.main_root, ("fetch", "--prune", "origin"))

    def commit_get(self, ref: str) -> str:
        """Resolve one ref to a full commit.

        Args:
            ref: Exact Git ref.

        Returns:
            Full lowercase commit.
        """

        value = git_command_text_get(self.main_root, ("rev-parse", "--verify", f"{ref}^{{commit}}"))
        if not match_full_commit(value):
            raise TaskWorkspaceError(f"Git ref did not resolve to a full commit: {ref}")
        return value

    def tracked_file_bytes_get(self, commit: str, relative_path: str) -> bytes | None:
        """Read one ordinary tracked file from an exact commit without a checkout.

        Args:
            commit: Exact full commit identity.
            relative_path: Exact repository-root file path.

        Returns:
            Blob bytes, or absence when the path is not tracked at that commit.
        """

        if not match_full_commit(commit):
            raise TaskWorkspaceError("Tracked-file source must be one full Git commit")
        if (
            not relative_path
            or relative_path.startswith(("-", "/"))
            or "\x00" in relative_path
            or "\\" in relative_path
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
        ):
            raise TaskWorkspaceError("Tracked-file path is unsafe")
        completed_process = git_command_run(
            self.main_root,
            ("ls-tree", "-z", commit, "--", relative_path),
        )
        record_list = [item for item in completed_process.stdout.split(b"\0") if item]
        if not record_list:
            return None
        if len(record_list) != 1 or b"\t" not in record_list[0]:
            raise TaskWorkspaceError("Tracked-file lookup returned an ambiguous tree entry")
        header, encoded_path = record_list[0].split(b"\t", 1)
        try:
            mode, object_kind, object_identity = header.decode("ascii").split(" ")
            decoded_path = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise TaskWorkspaceError("Tracked-file lookup returned malformed metadata") from error
        if decoded_path != relative_path or mode not in {"100644", "100755"} or object_kind != "blob":
            raise TaskWorkspaceError("Tracked bootstrap manifest must be one ordinary committed file")
        return git_command_run(
            self.main_root,
            ("cat-file", "blob", object_identity),
        ).stdout

    def exist_remote_branch(self, branch_name: str) -> bool:
        """Return whether origin has one exact branch.

        Args:
            branch_name: Exact task branch.

        Returns:
            Branch existence.
        """

        return (
            git_command_run(
                self.main_root,
                (
                    "show-ref",
                    "--verify",
                    "--quiet",
                    f"refs/remotes/origin/{branch_name}",
                ),
                check=False,
            ).returncode
            == 0
        )

    def exist_local_branch(self, branch_name: str) -> bool:
        """Return whether one local task branch exists.

        Args:
            branch_name: Exact task branch.

        Returns:
            Branch existence.
        """

        return (
            git_command_run(
                self.main_root,
                ("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"),
                check=False,
            ).returncode
            == 0
        )

    def remote_branch_delete_exact(self, branch_name: str, *, expected_commit: str) -> None:
        """Delete one current remote branch with an exact force-with-lease guard.

        Args:
            branch_name: Exact deterministic task branch.
            expected_commit: Durable pre-deletion remote branch head.

        Returns:
            Nothing. Absence is already reconciled.
        """

        self.fetch()
        if not self.exist_remote_branch(branch_name):
            return
        if not expected_commit or self.commit_get(f"refs/remotes/origin/{branch_name}") != expected_commit:
            raise TaskWorkspaceError("Remote task branch differs from its durable cleanup snapshot")
        completed_process = git_command_run(
            self.main_root,
            (
                "push",
                f"--force-with-lease=refs/heads/{branch_name}:{expected_commit}",
                "origin",
                f":refs/heads/{branch_name}",
            ),
            check=False,
        )
        if completed_process.returncode != 0:
            raise TaskWorkspaceError("Remote task branch changed during exact deletion")
        self.fetch()
        if self.exist_remote_branch(branch_name):
            raise TaskWorkspaceError("Remote task branch remained after exact deletion")

    def task_worktree_create_or_accept(
        self,
        issue_identifier: str,
        state: RepositoryWorkspaceState,
    ) -> None:
        """Create or prove the exact issue-owned branch and worktree.

        Args:
            issue_identifier: Exact Linear issue identifier.
            state: Durable first-attempt baseline.
        """

        issue_identifier_validate(issue_identifier)
        self.task_container_require(create=True)
        task_root = self.main_root / ".worktree" / issue_identifier.lower()
        branch_name = f"linear/{issue_identifier.lower()}"
        if task_root.exists():
            self.task_worktree_require(issue_identifier, state)
            return
        if self._exist_branch_checkout_elsewhere(branch_name):
            raise TaskWorkspaceError("Task branch is already checked out in another worktree")
        if not self.exist_local_branch(branch_name):
            if self.exist_remote_branch(branch_name):
                remote_commit = self.commit_get(f"refs/remotes/origin/{branch_name}")
                self._ancestor_require(state.baseline_commit, remote_commit, label="Remote task branch")
                git_command_run(
                    self.main_root,
                    (
                        "branch",
                        "--track",
                        branch_name,
                        f"refs/remotes/origin/{branch_name}",
                    ),
                )
            else:
                git_command_run(self.main_root, ("branch", branch_name, state.baseline_commit))
        task_root.parent.mkdir(parents=True, exist_ok=True)
        git_command_run(self.main_root, ("worktree", "add", str(task_root), branch_name))
        self.task_worktree_require(issue_identifier, state)

    def task_worktree_require(self, issue_identifier: str, state: RepositoryWorkspaceState) -> None:
        """Require exact registration, branch, origin and ancestry for an issue task root.

        Args:
            issue_identifier: Exact Linear issue identifier.
            state: Durable first-attempt baseline.
        """

        issue_identifier_validate(issue_identifier)
        self.task_container_require(create=False)
        task_root = self.main_root / ".worktree" / issue_identifier.lower()
        branch_name = f"linear/{issue_identifier.lower()}"
        try:
            task_root = task_root.resolve(strict=True)
        except OSError as error:
            raise TaskWorkspaceError("Task worktree path is absent or unavailable") from error
        registration = self._branch_name_by_worktree_path_map_get().get(task_root)
        if registration != branch_name:
            raise TaskWorkspaceError("Task path is absent from Git worktree registration or uses another branch")
        branch = git_command_text_get(task_root, ("symbolic-ref", "--quiet", "--short", "HEAD"))
        if branch != branch_name:
            raise TaskWorkspaceError("Task worktree checked out another branch")
        origin = _workspace_origin_identity_get(git_command_text_get(task_root, ("remote", "get-url", "origin")))
        if origin != self.origin_identity:
            raise TaskWorkspaceError("Task worktree origin differs from the participating repository identity")
        head = self.commit_get(branch_name)
        self._ancestor_require(state.baseline_commit, head, label="Task branch")

    def worktree_branch_get(self, task_root: Path) -> str | None:
        """Return the exact registered branch for one task path when present.

        Args:
            task_root: Exact task-worktree path.

        Returns:
            Registered local branch name, or absence.
        """

        return self._branch_name_by_worktree_path_map_get().get(task_root.resolve(strict=False))

    def task_container_require(self, *, create: bool) -> None:
        """Require the repository-local worktree container to be a physical directory.

        Args:
            create: Whether an absent container may be created.
        """

        container = self.main_root / ".worktree"
        if container.is_symlink() or (container.exists() and not container.is_dir()):
            raise TaskWorkspaceError("Task worktree container must be one physical repository-local directory")
        if not container.exists():
            if not create:
                return
            container.mkdir(mode=0o755)
            _directory_sync(self.main_root)
        if container.resolve(strict=True) != container:
            raise TaskWorkspaceError("Task worktree container resolves outside its canonical repository path")

    def task_absence_require(self, issue_identifier: str) -> None:
        """Treat complete task absence as success and reject orphaned state.

        Args:
            issue_identifier: Exact Linear issue identifier.
        """

        issue_identifier = issue_identifier_validate(issue_identifier)
        branch_name = f"linear/{issue_identifier.lower()}"
        self.task_container_require(create=False)
        task_root = self.main_root / ".worktree" / issue_identifier.lower()
        if (
            task_root.exists()
            or self.worktree_branch_get(task_root) is not None
            or self.exist_local_branch(branch_name)
            or self.exist_remote_branch(branch_name)
        ):
            raise TaskWorkspaceError("Task resources exist without private ownership proof")

    def _branch_name_by_worktree_path_map_get(self) -> dict[Path, str]:
        """Return registered branch by worktree path.

        Returns:
            Registration mapping.
        """

        item_list = git_command_run(self.main_root, ("worktree", "list", "--porcelain", "-z")).stdout.split(b"\0")
        branch_name_by_worktree_path_map: dict[Path, str] = {}
        current_path: Path | None = None
        for raw in item_list:
            if raw.startswith(b"worktree "):
                current_path = Path(raw.removeprefix(b"worktree ").decode("utf-8")).resolve(strict=False)
            elif raw.startswith(b"branch ") and current_path is not None:
                ref = raw.removeprefix(b"branch ").decode("utf-8")
                branch_name_by_worktree_path_map[current_path] = ref.removeprefix("refs/heads/")
        return branch_name_by_worktree_path_map

    def _exist_branch_checkout_elsewhere(self, branch_name: str) -> bool:
        """Return whether a local branch is already attached to any worktree.

        Args:
            branch_name: Exact branch name.

        Returns:
            Whether branch is attached.
        """

        return branch_name in set(self._branch_name_by_worktree_path_map_get().values())

    def _ancestor_require(self, ancestor: str, descendant: str, *, label: str) -> None:
        """Require commit ancestry without changing refs.

        Args:
            ancestor: Expected ancestor.
            descendant: Expected descendant.
            label: Diagnostic owner label.
        """

        if (
            git_command_run(
                self.main_root,
                ("merge-base", "--is-ancestor", ancestor, descendant),
                check=False,
            ).returncode
            != 0
        ):
            raise TaskWorkspaceError(f"{label} is not a descendant of its recorded baseline")


def match_full_commit(value: str) -> bool:
    """Return whether text is one supported full lowercase Git object identity.

    Args:
        value: Candidate commit text.

    Returns:
        Match result.
    """

    return len(value) in {40, 64} and all(character in "0123456789abcdef" for character in value)


def _directory_sync(path: Path) -> None:
    """Fsync one directory after private state mutation.

    Args:
        path: Exact directory path.
    """

    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
