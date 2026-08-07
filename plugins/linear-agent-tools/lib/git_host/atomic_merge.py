"""Server-atomic exact-base-and-head Git merge through protected refs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from git_host.authentication import (
    GitHubAuthenticationBoundary,
    GitHubPrincipal,
    git_credential_config_argument_list_get,
)
from git_host.command import CommandRunner, command_closed_run
from git_host.model import GitHubContractError, PullRequestSnapshot, RepositoryIdentity

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
_CLOSED_GIT_CONFIG_ARGUMENT_LIST = (
    "-c",
    "core.hooksPath=/dev/null",
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
    """Bind one validated worktree to its sole explicit GitHub destination."""

    root: Path
    explicit_url: str


class GitHubAtomicMergeBoundary:
    """Create a no-fast-forward merge and update both reviewed refs atomically."""

    def __init__(self, runner: CommandRunner) -> None:
        """Initialize one direct-command dependency.

        Args:
            runner: Shared deterministic or process command runner.
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
    ) -> str:
        """Atomically fast-forward base and delete head against both reviewed OIDs.

        Git's receive-pack transaction validates both explicit old-object leases
        while holding ref locks. ``--atomic`` therefore rejects the complete
        transaction when either base or head moved after provider preflight.

        Args:
            repository: Exact GitHub repository.
            repository_path: Exact local task worktree.
            snapshot: Exact open reviewed PR snapshot.
            execution_login: Authenticated identity used for merge authorship.
            execution_user_id: Numeric identity used for protection authority.
            execution_node_id: GraphQL identity used for terminal readback.

        Returns:
            Exact locally constructed merge commit pushed to the base.
        """

        destination = self._repository_destination_require(
            repository=repository,
            repository_path=repository_path,
        )
        root = destination.root
        authentication = GitHubAuthenticationBoundary(self._runner)
        principal = authentication.principal_require(
            GitHubPrincipal(
                login=execution_login,
                user_id=execution_user_id,
                node_id=execution_node_id,
            )
        )
        authentication.credential_validate(principal)
        network_config_argument_list = _network_config_argument_list(
            principal=principal,
        )
        for commit in (snapshot.base_commit, snapshot.head_commit):
            self._git_checked(
                root,
                ("cat-file", "-e", f"{commit}^{{commit}}"),
                label="Reviewed Git commit lookup",
            )
        merge_tree = self._git_checked(
            root,
            (
                "merge-tree",
                "--write-tree",
                snapshot.base_commit,
                snapshot.head_commit,
            ),
            label="Reviewed Git merge-tree construction",
        ).stdout.strip()
        if _COMMIT_PATTERN.fullmatch(merge_tree) is None:
            raise GitHubContractError("Git merge-tree construction returned another identity")
        merge_commit = self._git_checked(
            root,
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
        base_ref = f"refs/heads/{snapshot.base_branch}"
        head_ref = f"refs/heads/{snapshot.head_branch}"
        self._git_checked(
            root,
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
        remote_ref_process = self._git_checked(
            root,
            (
                "ls-remote",
                "--refs",
                destination.explicit_url,
                base_ref,
                head_ref,
            ),
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
        """Prove a crash-recovered result is the declared exact merge transaction.

        Args:
            repository: Exact GitHub repository.
            repository_path: Exact local task worktree.
            snapshot: Provider-confirmed merged PR snapshot.
            reviewed_base_commit: Immutable base covered by independent review.
            reviewed_head_commit: Immutable head covered by independent review.
        """

        snapshot.merged_metadata_require(
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
        )
        destination = self._repository_destination_require(
            repository=repository,
            repository_path=repository_path,
        )
        root = destination.root
        authentication = GitHubAuthenticationBoundary(self._runner)
        principal = authentication.principal_identity_require(
            login=snapshot.merged_by_login,
            user_id=snapshot.merged_by_user_id,
            node_id=snapshot.merged_by_node_id,
        )
        authentication.credential_validate(principal)
        network_config_argument_list = _network_config_argument_list(
            principal=principal,
        )
        self._git_checked(
            root,
            (
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "--no-write-fetch-head",
                destination.explicit_url,
                snapshot.merge_commit,
            ),
            label="Merged Git commit fetch",
            config_argument_list=network_config_argument_list,
        )
        merge_tree = self._git_checked(
            root,
            (
                "merge-tree",
                "--write-tree",
                reviewed_base_commit,
                reviewed_head_commit,
            ),
            label="Recovered Git merge-tree construction",
        ).stdout.strip()
        if _COMMIT_PATTERN.fullmatch(merge_tree) is None:
            raise GitHubContractError("Recovered Git merge-tree returned another identity")
        commit_payload = self._git_checked(
            root,
            ("cat-file", "-p", snapshot.merge_commit),
            label="Merged Git commit inspection",
        ).stdout
        _merge_commit_identity_require(
            commit_payload,
            expected_tree=merge_tree,
            expected_parent_list=[reviewed_base_commit, reviewed_head_commit],
        )
        head_ref = f"refs/heads/{snapshot.head_branch}"
        remote_ref_process = self._git_checked(
            root,
            (
                "ls-remote",
                "--refs",
                destination.explicit_url,
                head_ref,
            ),
            label="Merged Git ref readback",
            config_argument_list=network_config_argument_list,
        )
        _deleted_ref_result_require(remote_ref_process.stdout, deleted_ref=head_ref)

    def _repository_root_require(self, repository_path: Path) -> Path:
        """Require one exact ordinary local Git worktree root."""

        if not isinstance(repository_path, Path) or repository_path.is_symlink() or not repository_path.is_dir():
            raise GitHubContractError("Merge repository path must be one ordinary directory")
        root = repository_path.resolve()
        reported = self._git_checked(
            root,
            ("rev-parse", "--show-toplevel"),
            label="Git worktree-root read",
        ).stdout.strip()
        if not reported or Path(reported).resolve() != root:
            raise GitHubContractError("Merge repository path differs from its exact Git worktree root")
        return root

    def _repository_destination_require(
        self,
        *,
        repository: RepositoryIdentity,
        repository_path: Path,
    ) -> GitHubRemoteDestination:
        """Require one audited local repository and canonical destination."""

        root = self._repository_root_require(repository_path)
        git_dir = self._git_path_get(root, "--absolute-git-dir", label="Git worktree metadata directory")
        common_git_dir = self._git_path_get(
            root,
            "--path-format=absolute",
            "--git-common-dir",
            label="Git common metadata directory",
        )
        if git_dir.is_symlink() or not git_dir.is_dir() or common_git_dir.is_symlink() or not common_git_dir.is_dir():
            raise GitHubContractError("Git metadata directory has another shape")
        config_path_list = [common_git_dir / "config"]
        worktree_config_path = git_dir / "config.worktree"
        if worktree_config_path.exists() or worktree_config_path.is_symlink():
            config_path_list.append(worktree_config_path)
        fetch_url_list: list[str] = []
        push_url_list: list[str] = []
        for config_path in config_path_list:
            self._config_file_require(config_path)
            self._config_safety_require(config_path)
            fetch_url_list.extend(self._config_value_list_get(config_path, "remote.origin.url"))
            push_url_list.extend(self._config_value_list_get(config_path, "remote.origin.pushurl"))
        fetch_url = _single_configured_url_get(fetch_url_list, label="fetch", empty_allowed=False)
        push_url = _single_configured_url_get(push_url_list, label="push", empty_allowed=True) or fetch_url
        fetch_repository = _github_remote_repository_get(fetch_url)
        push_repository = _github_remote_repository_get(push_url)
        if fetch_url != push_url:
            raise GitHubContractError("Git configured fetch and push URLs diverge")
        if fetch_repository != repository or push_repository != repository:
            raise GitHubContractError("Local Git destination differs from the exact pull-request repository")
        self._repository_substitution_and_hook_state_require(root=root, common_git_dir=common_git_dir)
        return GitHubRemoteDestination(root=root, explicit_url=repository.canonical_https_url)

    def _git_path_get(self, root: Path, *argument_list: str, label: str) -> Path:
        """Read one absolute Git metadata path through the config-free boundary."""

        value = self._git_checked(root, ("rev-parse", *argument_list), label=label).stdout.strip()
        if not value or any(character in value for character in ("\x00", "\n", "\r")):
            raise GitHubContractError(f"{label} has another shape")
        path = Path(value)
        if not path.is_absolute():
            raise GitHubContractError(f"{label} is not absolute")
        return path

    def _config_file_require(self, config_path: Path) -> None:
        """Require one ordinary exact Git config file before auditing its names."""

        if config_path.is_symlink() or not config_path.is_file() or config_path.stat().st_nlink != 1:
            raise GitHubContractError("Git repository config must be one ordinary file")

    def _config_safety_require(self, config_path: Path) -> None:
        """Reject local keys that could redirect any merge-time Git operation."""

        completed_process = command_closed_run(
            self._runner,
            ["git", "config", "--no-includes", "--null", "--name-only", "--list"],
            git_config_path=config_path,
        )
        if completed_process.returncode != 0:
            raise GitHubContractError("Git repository config-name audit failed")
        name_list = _null_record_list_get(completed_process.stdout, label="Git repository config-name audit")
        dangerous_name_list = sorted(name for name in name_list if _config_name_is_dangerous(name))
        if dangerous_name_list:
            raise GitHubContractError("Git repository config contains merge-unsafe keys")

    def _config_value_list_get(self, config_path: Path, name: str) -> list[str]:
        """Read values for one audited local key without loading any other config."""

        completed_process = command_closed_run(
            self._runner,
            ["git", "config", "--no-includes", "--null", "--get-all", name],
            git_config_path=config_path,
        )
        if completed_process.returncode == 1 and not completed_process.stdout:
            return []
        if completed_process.returncode != 0 or not completed_process.stdout:
            raise GitHubContractError(f"Git repository {name} read failed")
        return _null_record_list_get(completed_process.stdout, label=f"Git repository {name}")

    def _repository_substitution_and_hook_state_require(self, *, root: Path, common_git_dir: Path) -> None:
        """Reject hooks, alternate object stores, grafts, replacements and shallow history."""

        forbidden_path_list = [
            common_git_dir / "hooks" / "pre-push",
            common_git_dir / "objects" / "info" / "alternates",
            common_git_dir / "objects" / "info" / "http-alternates",
            common_git_dir / "info" / "grafts",
        ]
        for path in forbidden_path_list:
            if path.exists() or path.is_symlink():
                raise GitHubContractError("Git repository contains merge-unsafe hook or object substitution state")
        replace_ref_output = self._git_checked(
            root,
            ("for-each-ref", "--format=%(refname)", "refs/replace/"),
            label="Git replace-ref audit",
        ).stdout
        if replace_ref_output:
            raise GitHubContractError("Git repository contains replace refs")
        shallow_value = self._git_checked(
            root,
            ("rev-parse", "--is-shallow-repository"),
            label="Git shallow-repository audit",
        ).stdout.strip()
        if shallow_value != "false":
            raise GitHubContractError("Git merge repository must contain complete non-shallow history")

    def _git_checked(
        self,
        root: Path,
        argument_list: tuple[str, ...],
        *,
        label: str,
        config_argument_list: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Run one config-free Git command and hide untrusted diagnostics."""

        completed_process = command_closed_run(
            self._runner,
            [*_git_prefix(root, config_argument_list), *argument_list],
        )
        if completed_process.returncode != 0:
            raise GitHubContractError(f"{label} failed")
        return completed_process


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
    path = path[:-4]
    return RepositoryIdentity(path)


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
    """Return whether one local key can redirect or extend merge-time Git."""

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
) -> tuple[str, ...]:
    """Return the one credential-bound invocation-local Git helper."""

    return git_credential_config_argument_list_get(principal)


def _git_prefix(root: Path, config_argument_list: tuple[str, ...]) -> tuple[str, ...]:
    """Return the config-free Git prefix shared by every merge operation."""

    return ("git", "-C", str(root), *_CLOSED_GIT_CONFIG_ARGUMENT_LIST, *config_argument_list)


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
