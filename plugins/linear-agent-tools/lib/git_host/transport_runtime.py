"""Owned reproducible Git transport runtime for authenticated merge operations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import pwd
import stat
import subprocess
from tempfile import TemporaryDirectory
from urllib.request import ProxyHandler, build_opener

from git_host.model import GitHubContractError

_GIT_UPSTREAM_VERSION = "2.54.0"
_GIT_PACKAGE_VERSION = "1:2.54.0-0ppa1~ubuntu24.04.1"
_GIT_PACKAGE_ARCHITECTURE = "amd64"
_GIT_PACKAGE_BYTE_COUNT = 7_331_490
_GIT_PACKAGE_SHA256 = "afd1453a4c84539812b8f27be1017eda858ba656778eb6aa1c30fd7b6a659141"
_GIT_PACKAGE_URL = (
    "https://ppa.launchpadcontent.net/git-core/ppa/ubuntu/pool/main/g/git/" "git_2.54.0-0ppa1~ubuntu24.04.1_amd64.deb"
)
_GIT_RUNTIME_DIRECTORY_NAME = "2.54.0-0ppa1-ubuntu24.04.1-amd64"
_DOWNLOAD_CHUNK_BYTE_COUNT = 1024 * 1024


@dataclass(frozen=True, slots=True)
class GitTransportRuntime:
    """Identify the exact main executable and its matching libexec directory."""

    root: Path
    executable: Path
    exec_path: Path

    def command_argument_list_get(self, argument_list: Sequence[str]) -> list[str]:
        """Bind Git arguments to this exact relocatable package installation.

        Args:
            argument_list: Arguments after the semantic Git executable.

        Returns:
            Direct argument vector with the absolute executable and libexec path.
        """

        return [str(self.executable), f"--exec-path={self.exec_path}", *argument_list]


def git_transport_runtime_get() -> GitTransportRuntime:
    """Return the exact installed transport runtime after ownership checks.

    Returns:
        Validated runtime rooted below the standard OS-user home.
    """

    _supported_host_require()
    account = pwd.getpwuid(os.getuid())
    _standard_process_context_require(account.pw_dir)
    root = Path(account.pw_dir) / ".local" / "lib" / "linear-agent-tools" / "git" / _GIT_RUNTIME_DIRECTORY_NAME
    _directory_chain_require(root, owner_uid=account.pw_uid, exact_root_mode=0o700)
    executable = root / "usr" / "bin" / "git"
    exec_path = root / "usr" / "lib" / "git-core"
    _ordinary_executable_require(executable, owner_uid=account.pw_uid)
    _directory_require(exec_path, owner_uid=account.pw_uid)
    _ordinary_executable_require(exec_path / "git", owner_uid=account.pw_uid)
    _ordinary_executable_require(exec_path / "git-remote-http", owner_uid=account.pw_uid)
    _relative_symlink_require(
        exec_path / "git-remote-https", expected_target="git-remote-http", owner_uid=account.pw_uid
    )
    return GitTransportRuntime(root=root, executable=executable, exec_path=exec_path)


def git_transport_runtime_provision() -> GitTransportRuntime:
    """Install the pinned transport package once outside any merge attempt.

    Returns:
        Atomically installed and build-validated runtime.
    """

    _supported_host_require()
    account = pwd.getpwuid(os.getuid())
    _standard_process_context_require(account.pw_dir)
    parent = Path(account.pw_dir) / ".local" / "lib" / "linear-agent-tools" / "git"
    _private_directory_chain_prepare(parent, owner_uid=account.pw_uid)
    directory_descriptor = os.open(parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        fcntl.flock(directory_descriptor, fcntl.LOCK_EX)
        target = parent / _GIT_RUNTIME_DIRECTORY_NAME
        if target.exists() or target.is_symlink():
            runtime = git_transport_runtime_get()
            _runtime_version_require(runtime, standard_home=account.pw_dir)
            return runtime
        with TemporaryDirectory(prefix=".provision-", dir=parent) as temporary_directory_name:
            temporary_directory = Path(temporary_directory_name)
            package_path = temporary_directory / "git.deb"
            _package_download(package_path)
            _package_metadata_require(package_path)
            extracted_root = temporary_directory / "runtime"
            _package_extract(package_path, extracted_root, standard_home=account.pw_dir)
            _tree_make_private(extracted_root)
            _runtime_at_root_require(extracted_root, owner_uid=account.pw_uid)
            staged_runtime = _runtime_from_root_get(extracted_root)
            _runtime_version_require(staged_runtime, standard_home=account.pw_dir)
            os.rename(extracted_root, target)
        runtime = git_transport_runtime_get()
        _runtime_version_require(runtime, standard_home=account.pw_dir)
        return runtime
    finally:
        os.close(directory_descriptor)


def git_transport_runtime_description_get(runtime: GitTransportRuntime) -> dict[str, object]:
    """Return safe inspect output for the installed pinned runtime.

    Args:
        runtime: Validated runtime to inspect.

    Returns:
        Non-secret package and executable identity fields.
    """

    if not isinstance(runtime, GitTransportRuntime):
        raise GitHubContractError("Git transport runtime has another shape")
    account = pwd.getpwuid(os.getuid())
    version_output = _runtime_version_require(runtime, standard_home=account.pw_dir)
    return {
        "architecture": _GIT_PACKAGE_ARCHITECTURE,
        "executable": str(runtime.executable),
        "exec_path": str(runtime.exec_path),
        "package_sha256": _GIT_PACKAGE_SHA256,
        "package_url": _GIT_PACKAGE_URL,
        "package_version": _GIT_PACKAGE_VERSION,
        "root": str(runtime.root),
        "status": "ready",
        "version": version_output.splitlines()[0],
    }


def _supported_host_require() -> None:
    """Require the exact distribution and architecture targeted by the package."""

    try:
        operating_system = platform.freedesktop_os_release()
    except OSError as error:
        raise GitHubContractError("Git transport host operating system cannot be identified") from error
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or operating_system.get("ID") != "ubuntu"
        or operating_system.get("VERSION_ID") != "24.04"
    ):
        raise GitHubContractError("Git transport runtime supports only Ubuntu 24.04 amd64")


def _standard_process_context_require(standard_home: str) -> None:
    """Require the caller's standard home without a Codex runtime override."""

    if os.environ.get("HOME") != standard_home or "CODEX_HOME" in os.environ:
        raise GitHubContractError("Git transport runtime requires standard HOME and unset CODEX_HOME")


def _private_directory_chain_prepare(path: Path, *, owner_uid: int) -> None:
    """Create the private provider path without accepting redirected components."""

    account_home = Path(pwd.getpwuid(owner_uid).pw_dir)
    try:
        relative_part_list = path.relative_to(account_home).parts
    except ValueError as error:
        raise GitHubContractError("Git transport installation is outside the standard home") from error
    current = account_home
    _directory_require(current, owner_uid=owner_uid)
    for part in relative_part_list:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _directory_require(current, owner_uid=owner_uid)
        if stat.S_IMODE(current.stat(follow_symlinks=False).st_mode) & 0o022:
            raise GitHubContractError("Git transport installation path is writable by another account")


def _directory_chain_require(path: Path, *, owner_uid: int, exact_root_mode: int) -> None:
    """Require every standard-home path component to be ordinary and non-writable by others."""

    account_home = Path(pwd.getpwuid(owner_uid).pw_dir)
    try:
        relative_part_list = path.relative_to(account_home).parts
    except ValueError as error:
        raise GitHubContractError("Git transport installation is outside the standard home") from error
    current = account_home
    for part in relative_part_list:
        _directory_require(current, owner_uid=owner_uid)
        if stat.S_IMODE(current.stat(follow_symlinks=False).st_mode) & 0o022:
            raise GitHubContractError("Git transport installation path is writable by another account")
        current = current / part
    _directory_require(current, owner_uid=owner_uid)
    root_mode = stat.S_IMODE(current.stat(follow_symlinks=False).st_mode)
    if root_mode != exact_root_mode:
        raise GitHubContractError("Git transport runtime root must have mode 0700")


def _directory_require(path: Path, *, owner_uid: int) -> None:
    """Require one ordinary directory owned by the standard account."""

    try:
        path_stat = path.stat(follow_symlinks=False)
    except OSError as error:
        raise GitHubContractError("Git transport runtime is not provisioned") from error
    if path.is_symlink() or not stat.S_ISDIR(path_stat.st_mode) or path_stat.st_uid != owner_uid:
        raise GitHubContractError("Git transport runtime directory has another shape")


def _ordinary_executable_require(path: Path, *, owner_uid: int) -> None:
    """Require one non-symlink executable that no other account can modify."""

    try:
        path_stat = path.stat(follow_symlinks=False)
    except OSError as error:
        raise GitHubContractError("Git transport runtime is incomplete") from error
    mode = stat.S_IMODE(path_stat.st_mode)
    if (
        path.is_symlink()
        or not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_uid != owner_uid
        or not mode & stat.S_IXUSR
        or mode & 0o022
    ):
        raise GitHubContractError("Git transport executable has another shape")


def _relative_symlink_require(path: Path, *, expected_target: str, owner_uid: int) -> None:
    """Require one exact package-internal executable alias."""

    try:
        path_stat = path.lstat()
        target = os.readlink(path)
    except OSError as error:
        raise GitHubContractError("Git transport runtime is incomplete") from error
    if not stat.S_ISLNK(path_stat.st_mode) or path_stat.st_uid != owner_uid or target != expected_target:
        raise GitHubContractError("Git transport executable alias has another shape")


def _package_download(path: Path) -> None:
    """Download exactly the pinned package without ambient proxy configuration."""

    digest = hashlib.sha256()
    byte_count = 0
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(_GIT_PACKAGE_URL) as response:
            if (
                response.status != 200
                or response.geturl() != _GIT_PACKAGE_URL
                or response.headers.get("Content-Length") != str(_GIT_PACKAGE_BYTE_COUNT)
            ):
                raise GitHubContractError("Pinned Git transport package response has another identity")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_BYTE_COUNT)
                    if not chunk:
                        break
                    byte_count += len(chunk)
                    if byte_count > _GIT_PACKAGE_BYTE_COUNT:
                        raise GitHubContractError("Pinned Git transport package is larger than declared")
                    digest.update(chunk)
                    output.write(chunk)
    except GitHubContractError:
        raise
    except OSError as error:
        raise GitHubContractError("Pinned Git transport package download failed") from error
    if byte_count != _GIT_PACKAGE_BYTE_COUNT or digest.hexdigest() != _GIT_PACKAGE_SHA256:
        raise GitHubContractError("Pinned Git transport package digest differs")


def _package_metadata_require(path: Path) -> None:
    """Require the downloaded Debian package's exact declared identity."""

    completed_process = subprocess.run(
        ["/usr/bin/dpkg-deb", "--field", str(path), "Package", "Version", "Architecture"],
        check=False,
        capture_output=True,
        text=True,
        env=_provision_environment_get(pwd.getpwuid(os.getuid()).pw_dir),
    )
    expected = "Package: git\n" f"Version: {_GIT_PACKAGE_VERSION}\n" f"Architecture: {_GIT_PACKAGE_ARCHITECTURE}\n"
    if completed_process.returncode != 0 or completed_process.stdout != expected:
        raise GitHubContractError("Pinned Git transport package metadata differs")


def _package_extract(path: Path, root: Path, *, standard_home: str) -> None:
    """Extract the already authenticated package into the private staging path."""

    completed_process = subprocess.run(
        ["/usr/bin/dpkg-deb", "--extract", str(path), str(root)],
        check=False,
        capture_output=True,
        text=True,
        env=_provision_environment_get(standard_home),
    )
    if completed_process.returncode != 0:
        raise GitHubContractError("Pinned Git transport package extraction failed")


def _tree_make_private(root: Path) -> None:
    """Make extracted content private while preserving executable bits."""

    for current_root, directory_name_list, file_name_list in os.walk(root, followlinks=False):
        current_path = Path(current_root)
        current_path.chmod(0o700)
        for name in directory_name_list:
            child = current_path / name
            if not child.is_symlink():
                child.chmod(0o700)
        for name in file_name_list:
            child = current_path / name
            child_stat = child.lstat()
            if stat.S_ISLNK(child_stat.st_mode):
                continue
            if not stat.S_ISREG(child_stat.st_mode):
                raise GitHubContractError("Pinned Git transport package contains another file type")
            child.chmod(0o500 if child_stat.st_mode & 0o111 else 0o400)


def _runtime_at_root_require(root: Path, *, owner_uid: int) -> None:
    """Validate the critical executable boundary before atomic installation."""

    if stat.S_IMODE(root.stat(follow_symlinks=False).st_mode) != 0o700:
        raise GitHubContractError("Git transport staging root has another mode")
    _ordinary_executable_require(root / "usr" / "bin" / "git", owner_uid=owner_uid)
    exec_path = root / "usr" / "lib" / "git-core"
    _directory_require(exec_path, owner_uid=owner_uid)
    _ordinary_executable_require(exec_path / "git", owner_uid=owner_uid)
    _ordinary_executable_require(exec_path / "git-remote-http", owner_uid=owner_uid)
    _relative_symlink_require(exec_path / "git-remote-https", expected_target="git-remote-http", owner_uid=owner_uid)


def _runtime_from_root_get(root: Path) -> GitTransportRuntime:
    """Build one already-validated runtime identity rooted at a staging path."""

    return GitTransportRuntime(
        root=root,
        executable=root / "usr" / "bin" / "git",
        exec_path=root / "usr" / "lib" / "git-core",
    )


def _runtime_version_require(runtime: GitTransportRuntime, *, standard_home: str) -> str:
    """Require the provisioned main binary to report the pinned build version."""

    completed_process = subprocess.run(
        runtime.command_argument_list_get(["version", "--build-options"]),
        check=False,
        capture_output=True,
        text=True,
        env=_provision_environment_get(standard_home),
    )
    if (
        completed_process.returncode != 0
        or not completed_process.stdout.startswith(f"git version {_GIT_UPSTREAM_VERSION}\n")
        or not any(line.startswith("libcurl: ") for line in completed_process.stdout.splitlines())
    ):
        raise GitHubContractError("Git transport runtime reports another build identity")
    return completed_process.stdout.rstrip("\n")


def _provision_environment_get(standard_home: str) -> dict[str, str]:
    """Return a minimal standard-home environment for provisioning commands."""

    account = pwd.getpwuid(os.getuid())
    if standard_home != account.pw_dir:
        raise GitHubContractError("Git transport provisioning requires the standard home")
    return {
        "HOME": standard_home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": account.pw_name,
        "PATH": "/usr/bin:/bin",
        "USER": account.pw_name,
    }
