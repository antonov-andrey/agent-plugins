"""Server-atomic exact-base-and-head Git merge through a private repository."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from git_host.authentication import (
    GitHubAuthenticationBoundary,
    GitHubPrincipal,
    git_credential_config_argument_list_get,
)
from git_host.command import CommandRunner, command_closed_run
from git_host.model import GitHubContractError, PullRequestSnapshot, RepositoryIdentity
from git_host.repository_policy import GitHubRepositoryMergePolicyBoundary

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
_MAXIMUM_METADATA_FILE_BYTE_COUNT = 1024 * 1024
_CLOSED_GIT_CONFIG_ARGUMENT_LIST = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "core.askPass=",
    "-c",
    "core.gitProxy=",
    "-c",
    "core.sshCommand=",
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
    "protocol.https.allow=always",
    "-c",
    "commit.gpgSign=false",
    "-c",
    "push.gpgSign=false",
)
_DANGEROUS_CONFIG_PREFIX_LIST = (
    "credential.",
    "diff.",
    "difftool.",
    "filter.",
    "gpg.",
    "http.",
    "https.",
    "include.",
    "includeif.",
    "merge.",
    "mergetool.",
    "protocol.",
    "push.",
    "transport.",
    "url.",
)
_DANGEROUS_CONFIG_NAME_SET = {
    "commit.gpgsign",
    "core.alternaterefscommand",
    "core.alternaterefsprefixes",
    "core.askpass",
    "core.attributesfile",
    "core.fsmonitor",
    "core.gitproxy",
    "core.hookspath",
    "core.sshcommand",
    "core.usereplacerefs",
    "extensions.partialclone",
    "ssh.variant",
    "tag.gpgsign",
    "user.signingkey",
}


@dataclass(frozen=True, slots=True)
class GitHubRemoteDestination:
    """Carry the sole canonical destination after task metadata is discarded."""

    explicit_url: str


class GitHubAtomicMergeBoundary:
    """Construct and prove merges only inside a provider-owned private repository."""

    def __init__(self, runner: CommandRunner) -> None:
        """Initialize one direct-command dependency.

        Args:
            runner: Shared deterministic or subprocess command runner.
        """

        self._runner = runner

    def merge(
        self,
        *,
        repository: RepositoryIdentity,
        repository_path: Path,
        snapshot: PullRequestSnapshot,
        execution_login: str,
        execution_user_id: int,
        execution_node_id: str,
        merge_method: str,
    ) -> str:
        """Create one ancestry-closed merge commit and atomically update both refs."""

        if merge_method != "merge":
            raise GitHubContractError("Only exact merge-commit construction is supported")
        if snapshot.base_commit == snapshot.head_commit:
            raise GitHubContractError("Reviewed base and head must identify distinct commits")
        destination = self._repository_destination_require(
            repository=repository,
            repository_path=repository_path,
        )
        authentication = GitHubAuthenticationBoundary(self._runner)
        principal = authentication.principal_require(
            GitHubPrincipal(
                login=execution_login,
                user_id=execution_user_id,
                node_id=execution_node_id,
            )
        )
        authentication.credential_validate(principal, repository)
        authentication.git_http_proactive_authentication_require()
        network_config_argument_list = _network_config_argument_list(
            principal=principal,
            repository=repository,
        )
        base_ref = f"refs/heads/{snapshot.base_branch}"
        head_ref = f"refs/heads/{snapshot.head_branch}"
        private_base_ref = "refs/provider/reviewed-base"
        private_head_ref = "refs/provider/reviewed-head"
        policy_boundary = GitHubRepositoryMergePolicyBoundary(self._runner)
        with self._private_repository() as private_git_dir:
            self._private_git_checked(
                private_git_dir,
                (
                    "fetch",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "--no-write-fetch-head",
                    destination.explicit_url,
                    f"+{base_ref}:{private_base_ref}",
                    f"+{head_ref}:{private_head_ref}",
                ),
                label="Reviewed Git ref fetch",
                config_argument_list=network_config_argument_list,
            )
            self._private_ref_require(private_git_dir, private_base_ref, snapshot.base_commit)
            self._private_ref_require(private_git_dir, private_head_ref, snapshot.head_commit)
            ancestry_process = self._private_git_run(
                private_git_dir,
                ("merge-base", "--is-ancestor", snapshot.base_commit, snapshot.head_commit),
            )
            if ancestry_process.returncode != 0:
                raise GitHubContractError("Reviewed base is not an ancestor of the exact reviewed head")
            merge_tree = self._private_git_checked(
                private_git_dir,
                ("rev-parse", "--verify", f"{snapshot.head_commit}^{{tree}}"),
                label="Reviewed head-tree inspection",
            ).stdout.strip()
            if _COMMIT_PATTERN.fullmatch(merge_tree) is None:
                raise GitHubContractError("Reviewed head tree returned another identity")
            policy_before_construction = policy_boundary.inspect(
                repository=repository,
                principal=principal,
                merge_method=merge_method,
            )
            merge_commit = self._private_git_checked(
                private_git_dir,
                (
                    "-c",
                    f"user.name={execution_login}",
                    "-c",
                    f"user.email={execution_login}@users.noreply.github.com",
                    "-c",
                    "commit.gpgsign=false",
                    "commit-tree",
                    merge_tree,
                    "-p",
                    snapshot.base_commit,
                    "-p",
                    snapshot.head_commit,
                    "-m",
                    f"Merge pull request #{snapshot.number}: {snapshot.title}",
                ),
                label="Reviewed Git merge-commit construction",
            ).stdout.strip()
            if _COMMIT_PATTERN.fullmatch(merge_commit) is None:
                raise GitHubContractError("Git merge-commit construction returned another identity")
            policy_before_push = policy_boundary.inspect(
                repository=repository,
                principal=principal,
                merge_method=merge_method,
            )
            if policy_before_push != policy_before_construction:
                raise GitHubContractError("GitHub repository merge policy changed during merge construction")
            self._private_git_checked(
                private_git_dir,
                (
                    "push",
                    "--porcelain",
                    "--atomic",
                    "--no-signed",
                    "--no-verify",
                    f"--force-with-lease={base_ref}:{snapshot.base_commit}",
                    f"--force-with-lease={head_ref}:{snapshot.head_commit}",
                    destination.explicit_url,
                    f"{merge_commit}:{base_ref}",
                    f":{head_ref}",
                ),
                label="Atomic reviewed Git ref transaction",
                config_argument_list=network_config_argument_list,
            )
            remote_ref_process = self._private_git_checked(
                private_git_dir,
                ("ls-remote", "--refs", destination.explicit_url, base_ref, head_ref),
                label="Atomic reviewed Git ref readback",
                config_argument_list=network_config_argument_list,
            )
        _remote_ref_result_require(
            remote_ref_process.stdout,
            expected_base_ref=base_ref,
            expected_base_commit=merge_commit,
            deleted_head_ref=head_ref,
        )
        return merge_commit

    def merged_result_require(
        self,
        *,
        repository: RepositoryIdentity,
        repository_path: Path,
        snapshot: PullRequestSnapshot,
        reviewed_base_commit: str,
        reviewed_head_commit: str,
    ) -> None:
        """Prove terminal tree, parents and deleted head in the same closed boundary."""

        snapshot.merged_metadata_require(
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
        )
        if reviewed_base_commit == reviewed_head_commit:
            raise GitHubContractError("Reviewed base and head must identify distinct commits")
        destination = self._repository_destination_require(
            repository=repository,
            repository_path=repository_path,
        )
        authentication = GitHubAuthenticationBoundary(self._runner)
        principal = authentication.principal_identity_require(
            login=snapshot.merged_by_login,
            user_id=snapshot.merged_by_user_id,
            node_id=snapshot.merged_by_node_id,
        )
        authentication.credential_validate(principal, repository)
        authentication.git_http_proactive_authentication_require()
        network_config_argument_list = _network_config_argument_list(
            principal=principal,
            repository=repository,
        )
        base_ref = f"refs/heads/{snapshot.base_branch}"
        head_ref = f"refs/heads/{snapshot.head_branch}"
        private_merge_ref = "refs/provider/merged-base"
        with self._private_repository() as private_git_dir:
            self._private_git_checked(
                private_git_dir,
                (
                    "fetch",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "--no-write-fetch-head",
                    destination.explicit_url,
                    f"+{base_ref}:{private_merge_ref}",
                ),
                label="Merged Git commit fetch",
                config_argument_list=network_config_argument_list,
            )
            self._private_commit_require(private_git_dir, snapshot.merge_commit)
            ancestry_process = self._private_git_run(
                private_git_dir,
                ("merge-base", "--is-ancestor", reviewed_base_commit, reviewed_head_commit),
            )
            if ancestry_process.returncode != 0:
                raise GitHubContractError("Recovered reviewed base is not an ancestor of the reviewed head")
            merge_tree = self._private_git_checked(
                private_git_dir,
                ("rev-parse", "--verify", f"{reviewed_head_commit}^{{tree}}"),
                label="Recovered reviewed head-tree inspection",
            ).stdout.strip()
            if _COMMIT_PATTERN.fullmatch(merge_tree) is None:
                raise GitHubContractError("Recovered reviewed head tree returned another identity")
            commit_payload = self._private_git_checked(
                private_git_dir,
                ("cat-file", "-p", snapshot.merge_commit),
                label="Merged Git commit inspection",
            ).stdout
            _merge_commit_identity_require(
                commit_payload,
                expected_tree=merge_tree,
                expected_parent_list=[reviewed_base_commit, reviewed_head_commit],
            )
            remote_ref_process = self._private_git_checked(
                private_git_dir,
                ("ls-remote", "--refs", destination.explicit_url, head_ref),
                label="Merged Git ref readback",
                config_argument_list=network_config_argument_list,
            )
        _deleted_ref_result_require(remote_ref_process.stdout, deleted_ref=head_ref)

    def _repository_destination_require(
        self,
        *,
        repository: RepositoryIdentity,
        repository_path: Path,
    ) -> GitHubRemoteDestination:
        """Snapshot-audit task metadata without using its Git object boundary."""

        root, git_dir, common_git_dir = _task_repository_metadata_get(repository_path)
        config_path_list = [common_git_dir / "config"]
        worktree_config_path = git_dir / "config.worktree"
        if worktree_config_path.exists() or worktree_config_path.is_symlink():
            config_path_list.append(worktree_config_path)
        fetch_url_list: list[str] = []
        push_url_list: list[str] = []
        for config_path in config_path_list:
            config_text = _ordinary_file_text_get(config_path, label="Git repository config")
            self._config_safety_require(config_text)
            fetch_url_list.extend(self._config_value_list_get(config_text, "remote.origin.url"))
            push_url_list.extend(self._config_value_list_get(config_text, "remote.origin.pushurl"))
        fetch_url = _single_configured_url_get(fetch_url_list, label="fetch", empty_allowed=False)
        push_url = _single_configured_url_get(push_url_list, label="push", empty_allowed=True) or fetch_url
        if fetch_url != push_url:
            raise GitHubContractError("Git configured fetch and push URLs diverge")
        if (
            _github_remote_repository_get(fetch_url) != repository
            or _github_remote_repository_get(push_url) != repository
        ):
            raise GitHubContractError("Local Git destination differs from the exact pull-request repository")
        _task_repository_substitution_state_require(git_dir=git_dir, common_git_dir=common_git_dir)
        return GitHubRemoteDestination(explicit_url=repository.canonical_https_url)

    def _config_safety_require(self, config_text: str) -> None:
        """Reject snapshot keys that could have redirected the audited origin."""

        completed_process = command_closed_run(
            self._runner,
            ["git", "config", "--file", "-", "--no-includes", "--null", "--name-only", "--list"],
            input_text=config_text,
        )
        if completed_process.returncode != 0:
            raise GitHubContractError("Git repository config-name audit failed")
        name_list = _null_record_list_get(completed_process.stdout, label="Git repository config-name audit")
        if any(_config_name_is_dangerous(name) for name in name_list):
            raise GitHubContractError("Git repository config contains merge-unsafe keys")

    def _config_value_list_get(self, config_text: str, name: str) -> list[str]:
        """Read one local value from the already captured config bytes."""

        completed_process = command_closed_run(
            self._runner,
            ["git", "config", "--file", "-", "--no-includes", "--null", "--get-all", name],
            input_text=config_text,
        )
        if completed_process.returncode == 1 and not completed_process.stdout:
            return []
        if completed_process.returncode != 0 or not completed_process.stdout:
            raise GitHubContractError(f"Git repository {name} read failed")
        return _null_record_list_get(completed_process.stdout, label=f"Git repository {name}")

    @contextmanager
    def _private_repository(self) -> Iterator[Path]:
        """Create one mode-0700 temporary directory for an empty bare repository."""

        with TemporaryDirectory(prefix="linear-agent-private-git-", dir="/tmp") as temporary_directory_name:
            root = Path(temporary_directory_name)
            root.chmod(0o700)
            root_stat = root.stat(follow_symlinks=False)
            if (
                root.is_symlink()
                or not stat.S_ISDIR(root_stat.st_mode)
                or root_stat.st_uid != os.getuid()
                or stat.S_IMODE(root_stat.st_mode) != 0o700
            ):
                raise GitHubContractError("Private Git parent directory has another shape")
            private_git_dir = root / "repository.git"
            completed_process = command_closed_run(
                self._runner,
                ["git", *_CLOSED_GIT_CONFIG_ARGUMENT_LIST, "init", "--bare", "--template=", str(private_git_dir)],
            )
            if completed_process.returncode != 0:
                raise GitHubContractError("Private Git repository initialization failed")
            generated_config = private_git_dir / "config"
            if generated_config.is_symlink() or (generated_config.exists() and not generated_config.is_file()):
                raise GitHubContractError("Private Git repository config has another shape")
            if generated_config.exists():
                generated_config.unlink()
            _private_repository_state_require(private_git_dir)
            yield private_git_dir

    def _private_ref_require(self, private_git_dir: Path, ref_name: str, expected_commit: str) -> None:
        """Require one fetched ref to resolve to the exact reviewed commit."""

        actual_commit = self._private_git_checked(
            private_git_dir,
            ("rev-parse", "--verify", f"{ref_name}^{{commit}}"),
            label="Fetched Git ref inspection",
        ).stdout.strip()
        if actual_commit != expected_commit:
            raise GitHubContractError("Fetched Git ref differs from the exact reviewed identity")

    def _private_commit_require(self, private_git_dir: Path, expected_commit: str) -> None:
        """Require one immutable commit to be reachable from the canonical fetch."""

        actual_commit = self._private_git_checked(
            private_git_dir,
            ("rev-parse", "--verify", f"{expected_commit}^{{commit}}"),
            label="Fetched Git commit inspection",
        ).stdout.strip()
        if actual_commit != expected_commit:
            raise GitHubContractError("Fetched Git commit differs from the immutable merge identity")

    def _private_git_run(
        self,
        private_git_dir: Path,
        argument_list: tuple[str, ...],
        *,
        config_argument_list: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Run Git only after and before proving the private repository stays closed."""

        _private_repository_state_require(private_git_dir)
        completed_process = command_closed_run(
            self._runner,
            [
                "git",
                f"--git-dir={private_git_dir}",
                "--bare",
                *_CLOSED_GIT_CONFIG_ARGUMENT_LIST,
                *config_argument_list,
                *argument_list,
            ],
        )
        _private_repository_state_require(private_git_dir)
        return completed_process

    def _private_git_checked(
        self,
        private_git_dir: Path,
        argument_list: tuple[str, ...],
        *,
        label: str,
        config_argument_list: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Run one private-repository Git command and hide untrusted diagnostics."""

        completed_process = self._private_git_run(
            private_git_dir,
            argument_list,
            config_argument_list=config_argument_list,
        )
        if completed_process.returncode != 0:
            raise GitHubContractError(f"{label} failed")
        return completed_process


def _task_repository_metadata_get(repository_path: Path) -> tuple[Path, Path, Path]:
    """Resolve an ordinary root, linked-worktree Git dir and common Git dir."""

    if not isinstance(repository_path, Path) or repository_path.is_symlink() or not repository_path.is_dir():
        raise GitHubContractError("Merge repository path must be one ordinary directory")
    root = repository_path.resolve()
    if Path(os.path.abspath(repository_path)) != root:
        raise GitHubContractError("Merge repository path contains a symbolic directory component")
    git_marker = root / ".git"
    if git_marker.is_symlink():
        raise GitHubContractError("Git worktree metadata marker has another shape")
    if git_marker.is_dir():
        git_dir = git_marker.resolve()
    elif git_marker.is_file():
        marker_text = _ordinary_file_text_get(git_marker, label="Git worktree metadata marker").strip()
        if not marker_text.startswith("gitdir: ") or "\n" in marker_text or "\r" in marker_text:
            raise GitHubContractError("Git worktree metadata marker has another shape")
        git_dir_candidate = Path(marker_text.removeprefix("gitdir: "))
        if not git_dir_candidate.is_absolute():
            raise GitHubContractError("Git worktree metadata directory is not absolute")
        if git_dir_candidate.is_symlink() or not git_dir_candidate.is_dir():
            raise GitHubContractError("Git worktree metadata directory has another shape")
        git_dir = git_dir_candidate.resolve(strict=True)
        if Path(os.path.abspath(git_dir_candidate)) != git_dir:
            raise GitHubContractError("Git worktree metadata directory contains a symbolic component")
        backlink_path = git_dir / "gitdir"
        backlink = _ordinary_file_text_get(backlink_path, label="Git worktree metadata backlink").strip()
        backlink_candidate = Path(backlink)
        if not backlink or not backlink_candidate.is_absolute() or backlink_candidate.resolve() != git_marker.resolve():
            raise GitHubContractError("Git worktree metadata backlink differs from the task worktree")
    else:
        raise GitHubContractError("Merge repository path is not an exact Git worktree root")
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise GitHubContractError("Git worktree metadata directory has another shape")
    common_marker = git_dir / "commondir"
    if common_marker.exists() or common_marker.is_symlink():
        common_value = _ordinary_file_text_get(common_marker, label="Git common metadata marker").strip()
        if not common_value or "\n" in common_value or "\r" in common_value:
            raise GitHubContractError("Git common metadata marker has another shape")
        common_git_dir_candidate = git_dir / common_value
        if common_git_dir_candidate.is_symlink() or not common_git_dir_candidate.is_dir():
            raise GitHubContractError("Git common metadata directory has another shape")
        common_git_dir = common_git_dir_candidate.resolve(strict=True)
        if Path(os.path.abspath(common_git_dir_candidate)) != common_git_dir:
            raise GitHubContractError("Git common metadata directory contains a symbolic component")
    else:
        common_git_dir = git_dir
    if common_git_dir.is_symlink() or not common_git_dir.is_dir():
        raise GitHubContractError("Git common metadata directory has another shape")
    if git_dir != common_git_dir and git_dir.parent != common_git_dir / "worktrees":
        raise GitHubContractError("Git worktree metadata directory is outside the exact common repository")
    for required_directory in (common_git_dir / "objects", common_git_dir / "refs"):
        if required_directory.is_symlink() or not required_directory.is_dir():
            raise GitHubContractError("Git common repository directory has another shape")
    head_value = _ordinary_file_text_get(git_dir / "HEAD", label="Git worktree HEAD").strip()
    if not head_value or any(character in head_value for character in ("\x00", "\n", "\r")):
        raise GitHubContractError("Git worktree HEAD has another shape")
    return root, git_dir, common_git_dir


def _ordinary_file_text_get(path: Path, *, label: str) -> str:
    """Read one stable, singly linked, non-symlink metadata snapshot."""

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise GitHubContractError(f"{label} must be one ordinary file") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > _MAXIMUM_METADATA_FILE_BYTE_COUNT
        ):
            raise GitHubContractError(f"{label} must be one ordinary file")
        payload = os.read(descriptor, _MAXIMUM_METADATA_FILE_BYTE_COUNT + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(payload) > _MAXIMUM_METADATA_FILE_BYTE_COUNT
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(payload) != before.st_size
    ):
        raise GitHubContractError(f"{label} changed while it was captured")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitHubContractError(f"{label} has another encoding") from error


def _task_repository_substitution_state_require(*, git_dir: Path, common_git_dir: Path) -> None:
    """Reject task-local hook, alternate, graft, replace and shallow state."""

    forbidden_path_list = [
        common_git_dir / "hooks" / "pre-push",
        git_dir / "hooks" / "pre-push",
        common_git_dir / "objects" / "info" / "alternates",
        common_git_dir / "objects" / "info" / "http-alternates",
        common_git_dir / "info" / "grafts",
        common_git_dir / "shallow",
        git_dir / "shallow",
        common_git_dir / "refs" / "replace",
    ]
    if any(path.exists() or path.is_symlink() for path in forbidden_path_list):
        raise GitHubContractError("Git repository contains merge-unsafe hook or object substitution state")
    packed_refs_path = common_git_dir / "packed-refs"
    if packed_refs_path.exists() or packed_refs_path.is_symlink():
        packed_refs = _ordinary_file_text_get(packed_refs_path, label="Git packed refs")
        if any(line.split(" ", 1)[-1].startswith("refs/replace/") for line in packed_refs.splitlines()):
            raise GitHubContractError("Git repository contains replace refs")


def _private_repository_state_require(private_git_dir: Path) -> None:
    """Require the provider repository to contain no mutable execution sources."""

    if private_git_dir.is_symlink() or not private_git_dir.is_dir():
        raise GitHubContractError("Private Git repository has another shape")
    private_git_dir_stat = private_git_dir.stat(follow_symlinks=False)
    if private_git_dir_stat.st_uid != os.getuid():
        raise GitHubContractError("Private Git repository has another owner")
    for directory_path in (
        private_git_dir / "objects",
        private_git_dir / "objects" / "info",
        private_git_dir / "objects" / "pack",
        private_git_dir / "refs",
        private_git_dir / "info",
    ):
        if directory_path.is_symlink() or (directory_path.exists() and not directory_path.is_dir()):
            raise GitHubContractError("Private Git repository contains a redirected metadata directory")
    forbidden_path_list = [
        private_git_dir / "config",
        private_git_dir / "config.worktree",
        private_git_dir / "info" / "attributes",
        private_git_dir / "objects" / "info" / "alternates",
        private_git_dir / "objects" / "info" / "http-alternates",
        private_git_dir / "info" / "grafts",
        private_git_dir / "shallow",
        private_git_dir / "refs" / "replace",
    ]
    if any(path.exists() or path.is_symlink() for path in forbidden_path_list):
        raise GitHubContractError("Private Git repository contains unsafe configuration or object state")
    hooks_path = private_git_dir / "hooks"
    if hooks_path.is_symlink() or (hooks_path.exists() and (not hooks_path.is_dir() or any(hooks_path.iterdir()))):
        raise GitHubContractError("Private Git repository contains hooks")
    packed_refs_path = private_git_dir / "packed-refs"
    if packed_refs_path.exists() or packed_refs_path.is_symlink():
        packed_refs = _ordinary_file_text_get(packed_refs_path, label="Private Git packed refs")
        if any(line.split(" ", 1)[-1].startswith("refs/replace/") for line in packed_refs.splitlines()):
            raise GitHubContractError("Private Git repository contains replace refs")


def _github_remote_repository_get(value: str) -> RepositoryIdentity:
    """Parse one canonical GitHub HTTPS or scp-like remote URL."""

    if not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise GitHubContractError("Git origin has another shape")
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
        if not path.endswith(".git"):
            raise GitHubContractError("Git remote is not one canonical GitHub URL")
    else:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as error:
            raise GitHubContractError("Git remote is not one canonical GitHub URL") from error
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith(".git")
        ):
            raise GitHubContractError("Git remote is not one canonical GitHub URL")
        path = parsed.path.removeprefix("/")
    return RepositoryIdentity(path[:-4])


def _single_configured_url_get(url_list: list[str], *, label: str, empty_allowed: bool) -> str:
    """Require one audited local URL, or an allowed absent push override."""

    if not url_list and empty_allowed:
        return ""
    if len(url_list) != 1 or not url_list[0]:
        raise GitHubContractError(f"Git configured {label} URL set must contain exactly one destination")
    _github_remote_repository_get(url_list[0])
    return url_list[0]


def _null_record_list_get(value: str, *, label: str) -> list[str]:
    """Parse one exact NUL-terminated Git config record sequence."""

    if not value:
        return []
    record_list = value.split("\x00")
    if record_list[-1] != "" or any(
        not record or any(character in record for character in ("\n", "\r")) for record in record_list[:-1]
    ):
        raise GitHubContractError(f"{label} has another shape")
    return record_list[:-1]


def _config_name_is_dangerous(name: str) -> bool:
    """Return whether one local key could redirect the task-origin audit."""

    lowered = name.casefold()
    if lowered in _DANGEROUS_CONFIG_NAME_SET or any(
        lowered.startswith(prefix) for prefix in _DANGEROUS_CONFIG_PREFIX_LIST
    ):
        return True
    if lowered.startswith("objects."):
        return True
    if lowered.startswith("remote."):
        return lowered not in {
            "remote.origin.fetch",
            "remote.origin.pushurl",
            "remote.origin.tagopt",
            "remote.origin.url",
        }
    return False


def _network_config_argument_list(
    *,
    principal: GitHubPrincipal,
    repository: RepositoryIdentity,
) -> tuple[str, ...]:
    """Return proactive authentication and the principal-bound helper."""

    return git_credential_config_argument_list_get(principal, repository)


def _remote_ref_result_require(
    value: str,
    *,
    expected_base_ref: str,
    expected_base_commit: str,
    deleted_head_ref: str,
) -> None:
    """Require exact post-transaction base and absent head refs."""

    commit_by_ref_map: dict[str, str] = {}
    for line in value.splitlines():
        field_list = line.split("\t")
        if len(field_list) != 2 or _COMMIT_PATTERN.fullmatch(field_list[0]) is None:
            raise GitHubContractError("Atomic Git ref readback has another shape")
        if field_list[1] in commit_by_ref_map:
            raise GitHubContractError("Atomic Git ref readback repeats one ref")
        commit_by_ref_map[field_list[1]] = field_list[0]
    if deleted_head_ref in commit_by_ref_map:
        raise GitHubContractError("Crash-recovered atomic merge did not delete the reviewed head ref")
    if commit_by_ref_map != {expected_base_ref: expected_base_commit}:
        raise GitHubContractError("Atomic Git ref readback differs from the complete transaction")


def _deleted_ref_result_require(value: str, *, deleted_ref: str) -> None:
    """Require an exact empty read for one reviewed head ref after recovery."""

    if value:
        for line in value.splitlines():
            field_list = line.split("\t")
            if len(field_list) != 2 or _COMMIT_PATTERN.fullmatch(field_list[0]) is None:
                raise GitHubContractError("Recovered deleted Git ref readback has another shape")
            if field_list[1] == deleted_ref:
                raise GitHubContractError("Crash-recovered atomic merge did not delete the reviewed head ref")
        raise GitHubContractError("Recovered deleted Git ref readback returned an unexpected ref")


def _merge_commit_identity_require(
    value: str,
    *,
    expected_tree: str,
    expected_parent_list: list[str],
) -> None:
    """Require exact tree and ordered parents for one recovered merge commit."""

    header, separator, _message = value.partition("\n\n")
    if not separator:
        raise GitHubContractError("Merged Git commit payload has another shape")
    tree_list = [line.removeprefix("tree ") for line in header.splitlines() if line.startswith("tree ")]
    parent_list = [line.removeprefix("parent ") for line in header.splitlines() if line.startswith("parent ")]
    if tree_list != [expected_tree] or parent_list != expected_parent_list:
        raise GitHubContractError("Merged Git commit differs from the exact reviewed merge identity")
