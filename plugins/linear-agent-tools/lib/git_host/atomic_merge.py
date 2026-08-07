"""Server-atomic exact-base-and-head Git merge through protected refs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from git_host.authentication import GitHubAuthenticationBoundary, GitHubPrincipal
from git_host.command import CommandRunner
from git_host.model import GitHubContractError, PullRequestSnapshot, RepositoryIdentity

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")


@dataclass(frozen=True, slots=True)
class GitHubRemoteDestination:
    """Bind one validated worktree to its sole explicit GitHub destination."""

    root: Path
    configured_url: str
    explicit_url: str


@dataclass(frozen=True, slots=True)
class GitUrlRewrite:
    """Contain one effective Git URL prefix rewrite from current configuration."""

    replacement: str
    match_prefix: str
    push_only: bool


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
        network_config_argument_list = _network_config_argument_list(
            authentication=authentication,
            principal=principal,
            explicit_url=destination.explicit_url,
        )
        for commit in (snapshot.base_commit, snapshot.head_commit):
            self._checked(
                ("git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"),
                label="Reviewed Git commit lookup",
            )
        merge_tree = self._checked(
            (
                "git",
                "-C",
                str(root),
                "merge-tree",
                "--write-tree",
                snapshot.base_commit,
                snapshot.head_commit,
            ),
            label="Reviewed Git merge-tree construction",
        ).stdout.strip()
        if _COMMIT_PATTERN.fullmatch(merge_tree) is None:
            raise GitHubContractError("Git merge-tree construction returned another identity")
        merge_commit = self._checked(
            (
                "git",
                "-C",
                str(root),
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
        self._checked(
            (
                *_git_prefix(root, network_config_argument_list),
                "push",
                "--porcelain",
                "--atomic",
                f"--force-with-lease={base_ref}:{snapshot.base_commit}",
                f"--force-with-lease={head_ref}:{snapshot.head_commit}",
                destination.explicit_url,
                f"{merge_commit}:{base_ref}",
                f":{head_ref}",
            ),
            label="Atomic reviewed Git ref transaction",
        )
        remote_ref_process = self._checked(
            (
                *_git_prefix(root, network_config_argument_list),
                "ls-remote",
                "--refs",
                destination.explicit_url,
                base_ref,
                head_ref,
            ),
            label="Atomic reviewed Git ref readback",
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

        snapshot.merged_result_require(
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
            node_id=snapshot.merged_by_node_id,
        )
        network_config_argument_list = _network_config_argument_list(
            authentication=authentication,
            principal=principal,
            explicit_url=destination.explicit_url,
        )
        self._checked(
            (
                *_git_prefix(root, network_config_argument_list),
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                destination.explicit_url,
                snapshot.merge_commit,
            ),
            label="Merged Git commit fetch",
        )
        merge_tree = self._checked(
            (
                "git",
                "-C",
                str(root),
                "merge-tree",
                "--write-tree",
                reviewed_base_commit,
                reviewed_head_commit,
            ),
            label="Recovered Git merge-tree construction",
        ).stdout.strip()
        if _COMMIT_PATTERN.fullmatch(merge_tree) is None:
            raise GitHubContractError("Recovered Git merge-tree returned another identity")
        commit_payload = self._checked(
            ("git", "-C", str(root), "cat-file", "-p", snapshot.merge_commit),
            label="Merged Git commit inspection",
        ).stdout
        _merge_commit_identity_require(
            commit_payload,
            expected_tree=merge_tree,
            expected_parent_list=[reviewed_base_commit, reviewed_head_commit],
        )
        head_ref = f"refs/heads/{snapshot.head_branch}"
        remote_ref_process = self._checked(
            (
                *_git_prefix(root, network_config_argument_list),
                "ls-remote",
                "--refs",
                destination.explicit_url,
                head_ref,
            ),
            label="Merged Git ref readback",
        )
        _deleted_ref_result_require(remote_ref_process.stdout, deleted_ref=head_ref)

    def _repository_root_require(self, repository_path: Path) -> Path:
        """Require one exact ordinary local Git worktree root."""

        if not isinstance(repository_path, Path) or repository_path.is_symlink() or not repository_path.is_dir():
            raise GitHubContractError("Merge repository path must be one ordinary directory")
        root = repository_path.resolve()
        reported = self._checked(
            ("git", "-C", str(root), "rev-parse", "--show-toplevel"),
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
        """Require one canonical matching effective fetch/push destination."""

        root = self._repository_root_require(repository_path)
        fetch_url = _single_effective_url_get(
            self._checked(
                ("git", "-C", str(root), "remote", "get-url", "--all", "origin"),
                label="Git effective fetch URL read",
            ).stdout,
            label="fetch",
        )
        push_url = _single_effective_url_get(
            self._checked(
                ("git", "-C", str(root), "remote", "get-url", "--push", "--all", "origin"),
                label="Git effective push URL read",
            ).stdout,
            label="push",
        )
        fetch_repository = _github_remote_repository_get(fetch_url)
        push_repository = _github_remote_repository_get(push_url)
        if fetch_url != push_url:
            raise GitHubContractError("Git effective fetch and push URLs diverge")
        if fetch_repository != repository or push_repository != repository:
            raise GitHubContractError("Local Git destination differs from the exact pull-request repository")
        explicit_url = repository.canonical_https_url
        rewrite_list = self._url_rewrite_list_get(root)
        resolved_explicit_fetch_url = _rewritten_url_get(explicit_url, rewrite_list=rewrite_list, push=False)
        resolved_explicit_push_url = _rewritten_url_get(explicit_url, rewrite_list=rewrite_list, push=True)
        if resolved_explicit_fetch_url != explicit_url or resolved_explicit_push_url != explicit_url:
            raise GitHubContractError("Explicit GitHub destination is rewritten by Git configuration")
        return GitHubRemoteDestination(root=root, configured_url=fetch_url, explicit_url=explicit_url)

    def _url_rewrite_list_get(self, root: Path) -> list[GitUrlRewrite]:
        """Read every effective URL rewrite without contacting an unresolved host."""

        completed_process = self._runner(
            [
                "git",
                "-C",
                str(root),
                "config",
                "--null",
                "--get-regexp",
                r"^url\..*\.(insteadof|pushinsteadof)$",
            ]
        )
        if completed_process.returncode == 1 and not completed_process.stdout:
            return []
        if completed_process.returncode != 0 or not completed_process.stdout:
            raise GitHubContractError("Git URL rewrite configuration read failed")
        rewrite_list: list[GitUrlRewrite] = []
        record_list = completed_process.stdout.split("\x00")
        if record_list[-1] != "":
            raise GitHubContractError("Git URL rewrite configuration has another shape")
        for record in record_list[:-1]:
            key, separator, match_prefix = record.partition("\n")
            if (
                not separator
                or "\n" in match_prefix
                or not match_prefix
                or any(character in record for character in ("\r",))
            ):
                raise GitHubContractError("Git URL rewrite configuration has another shape")
            if key.startswith("url.") and key.endswith(".pushinsteadof"):
                replacement = key[len("url.") : -len(".pushinsteadof")]
                push_only = True
            elif key.startswith("url.") and key.endswith(".insteadof"):
                replacement = key[len("url.") : -len(".insteadof")]
                push_only = False
            else:
                raise GitHubContractError("Git URL rewrite configuration has another shape")
            if not replacement or any(character in replacement for character in ("\x00", "\n", "\r")):
                raise GitHubContractError("Git URL rewrite configuration has another shape")
            rewrite_list.append(GitUrlRewrite(replacement=replacement, match_prefix=match_prefix, push_only=push_only))
        return rewrite_list

    def _checked(
        self,
        argument_list: tuple[str, ...],
        *,
        label: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run one direct Git command and hide untrusted provider diagnostics."""

        completed_process = self._runner(argument_list)
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


def _single_effective_url_get(value: str, *, label: str) -> str:
    """Require one and only one effective Git remote URL."""

    url_list = value.splitlines()
    if len(url_list) != 1 or not url_list[0]:
        raise GitHubContractError(f"Git effective {label} URL set must contain exactly one destination")
    _github_remote_repository_get(url_list[0])
    return url_list[0]


def _rewritten_url_get(value: str, *, rewrite_list: list[GitUrlRewrite], push: bool) -> str:
    """Resolve Git's longest matching fetch or push URL rewrite fail-closed."""

    instead_of_match_list = [
        item for item in rewrite_list if not item.push_only and value.startswith(item.match_prefix)
    ]
    push_instead_of_match_list = [
        item for item in rewrite_list if item.push_only and value.startswith(item.match_prefix)
    ]
    candidate_list = push_instead_of_match_list if push and push_instead_of_match_list else instead_of_match_list
    if not candidate_list:
        return value
    longest_length = max(len(item.match_prefix) for item in candidate_list)
    selected_list = [item for item in candidate_list if len(item.match_prefix) == longest_length]
    resolved_url_set = {item.replacement + value[len(item.match_prefix) :] for item in selected_list}
    if len(resolved_url_set) != 1:
        raise GitHubContractError("Git URL rewrite configuration has multiple effective destinations")
    resolved_url = resolved_url_set.pop()
    _github_remote_repository_get(resolved_url)
    return resolved_url


def _network_config_argument_list(
    *,
    authentication: GitHubAuthenticationBoundary,
    principal: GitHubPrincipal,
    explicit_url: str,
) -> tuple[str, ...]:
    """Return a credential- and destination-bound invocation-local Git config."""

    return (
        *authentication.git_credential_config_argument_list(principal),
        "-c",
        f"url.{explicit_url}.insteadOf={explicit_url}",
        "-c",
        f"url.{explicit_url}.pushInsteadOf={explicit_url}",
    )


def _git_prefix(root: Path, config_argument_list: tuple[str, ...]) -> tuple[str, ...]:
    """Return the direct Git prefix shared by authenticated network operations."""

    return ("git", "-C", str(root), *config_argument_list)


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
