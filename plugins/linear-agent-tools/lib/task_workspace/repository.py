"""Canonical checkout discovery and safe Git worktree operations."""

from __future__ import annotations

from collections.abc import Sequence
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
from urllib.parse import urlsplit

from git_origin.identity import GitOriginError, origin_identity_get
from json_contract import JsonContractError, json_load_strict
from task_workspace.model import (
    RepositoryRequest,
    RepositoryWorkspaceState,
    TaskWorkspaceError,
    WorkspaceConfig,
    issue_identifier_validate,
)

_GIT_CONFIG_ARGUMENT_LIST = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.askPass=",
    "-c",
    "core.gitProxy=",
    "-c",
    "core.useReplaceRefs=false",
    "-c",
    "credential.helper=",
    "-c",
    "credential.interactive=never",
    "-c",
    "http.extraHeader=",
    "-c",
    "http.proxy=",
    "-c",
    "https.proxy=",
    "-c",
    "http.followRedirects=false",
    "-c",
    "http.sslVerify=true",
    "-c",
    "protocol.allow=never",
    "-c",
    "commit.gpgSign=false",
    "-c",
    "push.gpgSign=false",
)
_STANDARD_EXECUTABLE_PATH = "/usr/bin:/bin"


def git_command_run(
    repository: Path,
    argument_list: Sequence[str],
    *,
    check: bool = True,
    input_bytes: bytes | None = None,
    transport_url_list: Sequence[str] = (),
) -> subprocess.CompletedProcess[bytes]:
    """Run one Git read or mutation through the same closed authority boundary.

    Args:
        repository: Exact repository working directory.
        argument_list: Fixed Git subcommand and arguments.
        check: Whether a nonzero result raises a task-workspace error.
        input_bytes: Optional bytes supplied to Git stdin.
        transport_url_list: Exact network or file destinations required by this command.

    Returns:
        Completed Git process result.
    """

    if any(
        argument == "-c" or argument == "--config-env" or argument.startswith("--config-env=")
        for argument in argument_list
    ):
        raise TaskWorkspaceError("Git invocation config belongs to the shared command boundary")
    protocol_set: set[str] = set()
    credential_config_argument_list: list[str] = []
    credential_key_set: set[str] = set()
    for transport_url in transport_url_list:
        protocol = _git_transport_protocol_get(transport_url)
        protocol_set.add(protocol)
        if protocol != "https":
            continue
        try:
            identity = origin_identity_get(transport_url)
        except GitOriginError:
            continue
        if not identity.startswith("github.com/"):
            continue
        repository_identity = identity.removeprefix("github.com/")
        credential_key = f"credential.https://github.com/{repository_identity}.git.helper"
        if credential_key in credential_key_set:
            continue
        credential_key_set.add(credential_key)
        credential_config_argument_list.extend(
            [
                "-c",
                f"{credential_key}=",
                "-c",
                f"{credential_key}=!/usr/bin/gh auth git-credential",
                "-c",
                "credential.useHttpPath=true",
            ]
        )
    if "ssh" in protocol_set:
        credential_config_argument_list.extend(
            [
                "-c",
                "core.sshCommand=/usr/bin/ssh -F /dev/null -oBatchMode=yes -oClearAllForwardings=yes",
            ]
        )
    environment_by_name_map = _git_environment_get(protocol_set)
    _git_repository_substitution_validate(repository)
    _git_repository_config_validate(repository, environment_by_name_map)
    command_prefix = [
        "/usr/bin/git",
        "-C",
        str(repository),
        *_GIT_CONFIG_ARGUMENT_LIST,
        *credential_config_argument_list,
        *(argument for protocol in sorted(protocol_set) for argument in ("-c", f"protocol.{protocol}.allow=always")),
    ]
    completed_process = subprocess.run(
        [*command_prefix, *argument_list],
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
    """Return strict UTF-8 output from one closed Git command.

    Args:
        repository: Exact repository working directory.
        argument_list: Fixed Git subcommand and arguments.
        check: Whether a nonzero result raises a task-workspace error.

    Returns:
        Strict decoded Git stdout without surrounding whitespace.
    """

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
        temporary = path.parent / ".workspace.json.tmp"
        self.state_temporary_recover(issue_identifier)
        encoded = (
            json.dumps(
                state.payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            _directory_sync(path.parent)
        except BaseException:
            _private_temporary_path_remove(temporary)
            raise

    def state_temporary_recover(self, issue_identifier: str) -> None:
        """Remove only the deterministic owned state-write temporary file."""

        parent = self._state_parent_get(issue_identifier, create=False)
        if parent is None:
            return
        _private_temporary_path_remove(parent / ".workspace.json.tmp")

    def bootstrap_temporary_root_get(self, issue_identifier: str, *, create: bool) -> Path | None:
        """Return the issue-private deterministic bootstrap staging root."""

        parent = self._state_parent_get(issue_identifier, create=create)
        if parent is None:
            return None
        root = parent / "bootstrap"
        try:
            metadata = root.stat(follow_symlinks=False)
        except FileNotFoundError:
            if not create:
                return None
            root.mkdir(mode=0o700)
            _directory_sync(parent)
            metadata = root.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise TaskWorkspaceError("Bootstrap temporary root must be one private user-owned physical directory")
        return root

    def bootstrap_temporary_root_cleanup(self, issue_identifier: str) -> None:
        """Remove the owned staging root only when no staged or foreign entry remains."""

        root = self.bootstrap_temporary_root_get(issue_identifier, create=False)
        if root is None:
            return
        try:
            root.rmdir()
        except OSError:
            return
        _directory_sync(root.parent)

    def state_delete(self, issue_identifier: str) -> None:
        """Delete exact private state idempotently.

        Args:
            issue_identifier: Exact Linear issue identifier.
        """

        parent = self._state_parent_get(issue_identifier, create=False)
        if parent is None:
            return
        self.state_temporary_recover(issue_identifier)
        self.bootstrap_temporary_root_cleanup(issue_identifier)
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

        fetch_url, _push_url = self._remote_transport_url_pair_get()
        self._remote_fetch_exact(fetch_url)

    def _remote_transport_url_pair_get(self) -> tuple[str, str]:
        """Resolve one exact effective fetch and push destination for origin."""

        url_list_by_kind: list[list[str]] = []
        for argument_list in (
            ("remote", "get-url", "--all", "origin"),
            ("remote", "get-url", "--push", "--all", "origin"),
        ):
            try:
                output = git_command_run(
                    self.main_root,
                    argument_list,
                ).stdout.decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise TaskWorkspaceError("Git origin transport destination is not valid UTF-8") from error
            value_list = output.splitlines()
            if len(value_list) != 1 or not value_list[0]:
                raise TaskWorkspaceError("Git origin requires one exact effective fetch and push destination")
            url_list_by_kind.append(value_list)
        fetch_url = url_list_by_kind[0][0]
        push_url = url_list_by_kind[1][0]
        if (
            _workspace_origin_identity_get(fetch_url) != self.origin_identity
            or _workspace_origin_identity_get(push_url) != self.origin_identity
        ):
            raise TaskWorkspaceError("Git origin fetch and push destinations differ from the repository owner")
        return fetch_url, push_url

    def _remote_fetch_exact(self, fetch_url: str) -> None:
        """Refresh origin branch refs from one already validated explicit destination."""

        git_command_run(
            self.main_root,
            (
                "fetch",
                "--no-tags",
                "--prune",
                fetch_url,
                "+refs/heads/*:refs/remotes/origin/*",
            ),
            transport_url_list=(fetch_url,),
        )

    def _remote_branch_head_get(self, push_url: str, branch_name: str) -> str:
        """Read one branch head directly from the validated effective push target."""

        ref = f"refs/heads/{branch_name}"
        output = git_command_run(
            self.main_root,
            ("ls-remote", "--refs", push_url, ref),
            transport_url_list=(push_url,),
        ).stdout
        record_list = [record for record in output.splitlines() if record]
        if not record_list:
            return ""
        if len(record_list) != 1 or b"\t" not in record_list[0]:
            raise TaskWorkspaceError("Remote task branch readback is ambiguous")
        commit_bytes, ref_bytes = record_list[0].split(b"\t", 1)
        try:
            commit = commit_bytes.decode("ascii")
            actual_ref = ref_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise TaskWorkspaceError("Remote task branch readback is malformed") from error
        if actual_ref != ref or not match_full_commit(commit):
            raise TaskWorkspaceError("Remote task branch readback is malformed")
        return commit

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

        git_command_run(self.main_root, ("check-ref-format", "--branch", branch_name))
        fetch_url, push_url = self._remote_transport_url_pair_get()
        self._remote_fetch_exact(fetch_url)
        current_target_commit = self._remote_branch_head_get(push_url, branch_name)
        if not current_target_commit:
            if self.exist_remote_branch(branch_name):
                raise TaskWorkspaceError("Remote task branch absence differs between fetch and push destinations")
            return
        if (
            not expected_commit
            or self.commit_get(f"refs/remotes/origin/{branch_name}") != expected_commit
            or current_target_commit != expected_commit
        ):
            raise TaskWorkspaceError("Remote task branch differs from its durable cleanup snapshot")
        if self._remote_transport_url_pair_get() != (fetch_url, push_url):
            raise TaskWorkspaceError("Git origin transport destination changed before branch deletion")
        current_target_commit = self._remote_branch_head_get(push_url, branch_name)
        if current_target_commit != expected_commit:
            raise TaskWorkspaceError("Remote task branch changed before exact deletion")
        completed_process = git_command_run(
            self.main_root,
            (
                "push",
                "--no-verify",
                f"--force-with-lease=refs/heads/{branch_name}:{expected_commit}",
                push_url,
                f":refs/heads/{branch_name}",
            ),
            check=False,
            transport_url_list=(push_url,),
        )
        if completed_process.returncode != 0:
            raise TaskWorkspaceError("Remote task branch changed during exact deletion")
        if self._remote_branch_head_get(push_url, branch_name):
            raise TaskWorkspaceError("Remote task branch remained at the validated push destination")
        self._remote_fetch_exact(fetch_url)
        if self.exist_remote_branch(branch_name):
            raise TaskWorkspaceError("Remote task branch remained after exact deletion")

    def task_worktree_create_or_accept(
        self,
        issue_identifier: str,
        state: RepositoryWorkspaceState,
    ) -> Path:
        """Create or prove the exact issue-owned branch and worktree.

        Args:
            issue_identifier: Exact Linear issue identifier.
            state: Durable first-attempt baseline.
        """

        issue_identifier_validate(issue_identifier)
        self.task_container_require(create=True)
        task_root = self.task_root_get(issue_identifier)
        branch_name = f"linear/{issue_identifier.lower()}"
        if task_root.exists():
            return self.task_worktree_require(issue_identifier, state)
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
                git_command_run(
                    self.main_root,
                    ("branch", branch_name, state.baseline_commit),
                )
        task_root.parent.mkdir(parents=True, exist_ok=True)
        git_command_run(
            self.main_root,
            ("worktree", "add", str(task_root), branch_name),
        )
        return self.task_worktree_require(issue_identifier, state)

    def task_worktree_require(self, issue_identifier: str, state: RepositoryWorkspaceState) -> Path:
        """Require exact registration, branch, origin and ancestry for an issue task root.

        Args:
            issue_identifier: Exact Linear issue identifier.
            state: Durable first-attempt baseline.
        """

        issue_identifier_validate(issue_identifier)
        task_root = self.task_root_get(issue_identifier)
        branch_name = f"linear/{issue_identifier.lower()}"
        if not task_root.exists():
            raise TaskWorkspaceError("Task worktree path is absent or unavailable")
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
        return task_root

    def task_worktree_branch_get(self, issue_identifier: str) -> str | None:
        """Return the exact registered branch for one issue task path when present.

        Args:
            issue_identifier: Exact Linear issue identifier.

        Returns:
            Registered local branch name, or absence.
        """

        return self._branch_name_by_worktree_path_map_get().get(self.task_root_get(issue_identifier))

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
        task_root = self.task_root_get(issue_identifier)
        if (
            task_root.exists()
            or self.task_worktree_branch_get(issue_identifier) is not None
            or self.exist_local_branch(branch_name)
            or self.exist_remote_branch(branch_name)
        ):
            raise TaskWorkspaceError("Task resources exist without private ownership proof")

    def task_root_get(self, issue_identifier: str) -> Path:
        """Return the exact issue path after rejecting filesystem aliases.

        Args:
            issue_identifier: Exact Linear issue identifier.

        Returns:
            The absent or physical canonical task-worktree path.
        """

        issue_identifier = issue_identifier_validate(issue_identifier)
        self.task_container_require(create=False)
        task_root = self.main_root / ".worktree" / issue_identifier.lower()
        try:
            metadata = task_root.stat(follow_symlinks=False)
        except FileNotFoundError:
            return task_root
        except OSError as error:
            raise TaskWorkspaceError("Task worktree path is unavailable") from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise TaskWorkspaceError("Task worktree path must be one physical canonical directory")
        try:
            resolved = task_root.resolve(strict=True)
        except OSError as error:
            raise TaskWorkspaceError("Task worktree path is unavailable") from error
        if resolved != task_root:
            raise TaskWorkspaceError("Task worktree path must be one physical canonical directory")
        return task_root

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
                current_path = Path(raw.removeprefix(b"worktree ").decode("utf-8"))
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


def _git_environment_get(protocol_set: set[str]) -> dict[str, str]:
    """Build one complete minimal standard-user environment for every Git command.

    Args:
        protocol_set: Exact transport protocols enabled for this invocation.

    Returns:
        Closed process environment with only standard-user transport authority.
    """

    try:
        account = pwd.getpwuid(os.getuid())
    except KeyError as error:
        raise TaskWorkspaceError("Git invocation requires one operating-system user") from error
    environment_by_name_map = {
        "GCM_INTERACTIVE": "never",
        "GH_PROMPT_DISABLED": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": account.pw_dir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": account.pw_name,
        "PATH": _STANDARD_EXECUTABLE_PATH,
        "SSH_ASKPASS_REQUIRE": "never",
        "USER": account.pw_name,
    }
    if "ssh" not in protocol_set:
        return environment_by_name_map
    socket_text = os.environ.get("SSH_AUTH_SOCK", "")
    if socket_text:
        socket_path = Path(socket_text)
        try:
            metadata = socket_path.stat(follow_symlinks=False)
        except OSError as error:
            raise TaskWorkspaceError("Git invocation SSH agent socket is unavailable") from error
        if (
            not socket_path.is_absolute()
            or str(socket_path) != socket_text
            or not stat.S_ISSOCK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise TaskWorkspaceError("Git invocation SSH agent socket is not one exact user-owned socket")
        environment_by_name_map["SSH_AUTH_SOCK"] = socket_text
    return environment_by_name_map


def _git_repository_substitution_validate(repository: Path) -> None:
    """Reject repository-local object substitution before Git resolves authority.

    Args:
        repository: Exact repository working directory.
    """

    marker = repository / ".git"
    try:
        marker_metadata = marker.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise TaskWorkspaceError("Git repository metadata is unavailable") from error
    if stat.S_ISDIR(marker_metadata.st_mode) and not marker.is_symlink():
        git_directory = marker
    elif stat.S_ISREG(marker_metadata.st_mode) and not marker.is_symlink() and marker_metadata.st_nlink == 1:
        try:
            marker_text = marker.read_text(encoding="utf-8")
        except OSError as error:
            raise TaskWorkspaceError("Git worktree metadata is unavailable") from error
        if not marker_text.startswith("gitdir: ") or "\n" in marker_text.rstrip("\n"):
            raise TaskWorkspaceError("Git worktree metadata is malformed")
        git_directory = Path(marker_text.removeprefix("gitdir: ").strip())
        if not git_directory.is_absolute():
            git_directory = repository / git_directory
    else:
        raise TaskWorkspaceError("Git repository metadata must be one physical directory or ordinary file")
    try:
        git_directory = git_directory.resolve(strict=True)
    except OSError as error:
        raise TaskWorkspaceError("Git repository metadata is unavailable") from error
    if not git_directory.is_dir():
        raise TaskWorkspaceError("Git repository metadata is not one directory")
    common_directory = git_directory
    common_path = git_directory / "commondir"
    if common_path.exists():
        try:
            common_text = common_path.read_text(encoding="utf-8")
        except OSError as error:
            raise TaskWorkspaceError("Git common-directory metadata is unavailable") from error
        if not common_text.strip() or "\n" in common_text.rstrip("\n"):
            raise TaskWorkspaceError("Git common-directory metadata is malformed")
        common_directory = Path(common_text.strip())
        if not common_directory.is_absolute():
            common_directory = git_directory / common_directory
        try:
            common_directory = common_directory.resolve(strict=True)
        except OSError as error:
            raise TaskWorkspaceError("Git common directory is unavailable") from error
    for metadata_directory in {git_directory, common_directory}:
        for relative_path in (
            Path("info/grafts"),
            Path("objects/info/alternates"),
            Path("objects/info/http-alternates"),
        ):
            if os.path.lexists(metadata_directory / relative_path):
                raise TaskWorkspaceError("Git repository contains ambient object substitution state")


def _git_repository_config_validate(repository: Path, environment_by_name_map: dict[str, str]) -> None:
    """Reject repository config that can redirect transport or execute checkout filters.

    Args:
        repository: Exact repository working directory.
        environment_by_name_map: Closed standard-user Git environment.
    """

    if not os.path.lexists(repository / ".git"):
        return
    completed_process = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "config",
            "--includes",
            "--null",
            "--name-only",
            "--get-regexp",
            ".",
        ],
        capture_output=True,
        check=False,
        env=environment_by_name_map,
        input=None,
    )
    if completed_process.returncode not in {0, 1}:
        raise TaskWorkspaceError("Git repository configuration is unavailable")
    try:
        config_name_list = [item.decode("utf-8").lower() for item in completed_process.stdout.split(b"\0") if item]
    except UnicodeDecodeError as error:
        raise TaskWorkspaceError("Git repository configuration is malformed") from error
    if any(
        name.startswith(("credential.", "filter.", "http.", "url."))
        or (
            name.startswith("remote.")
            and name.rsplit(".", 1)[-1] in {"proxy", "proxyauthmethod", "receivepack", "uploadpack", "vcs"}
        )
        for name in config_name_list
    ):
        raise TaskWorkspaceError("Git repository contains ambient transport or filter configuration")


def _git_transport_protocol_get(transport_url: str) -> str:
    """Return the one supported protocol required by an explicit Git destination.

    Args:
        transport_url: Exact destination supplied to the Git command.

    Returns:
        One Git protocol name enabled for the invocation.
    """

    if (
        not isinstance(transport_url, str)
        or not transport_url
        or any(character in transport_url for character in "\x00\r\n")
    ):
        raise TaskWorkspaceError("Git transport destination is malformed")
    if "://" not in transport_url and re.fullmatch(r"(?:[^/@:]+@)?[^/:]+:.+", transport_url):
        return "ssh"
    scheme = urlsplit(transport_url).scheme.lower()
    if scheme in {"file", "git", "http", "https", "ssh"}:
        return scheme
    if scheme:
        raise TaskWorkspaceError("Git transport protocol is unsupported")
    return "file"


def _private_temporary_path_remove(path: Path) -> None:
    """Remove one exact owned private temporary file without following aliases."""

    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise TaskWorkspaceError("Workspace private temporary state is unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise TaskWorkspaceError("Workspace private temporary state is not one owned private ordinary file")
    try:
        path.unlink()
        _directory_sync(path.parent)
    except OSError as error:
        raise TaskWorkspaceError("Workspace private temporary state could not be removed") from error


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
