"""Server-atomic exact-base-and-head Git merge through protected refs."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from git_host.command import CommandRunner
from git_host.model import GitHubContractError, PullRequestSnapshot, RepositoryIdentity

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")


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

        Returns:
            Exact locally constructed merge commit pushed to the base.
        """

        root = self._repository_and_origin_require(repository=repository, repository_path=repository_path)
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
                "git",
                "-C",
                str(root),
                "push",
                "--porcelain",
                "--atomic",
                f"--force-with-lease={base_ref}:{snapshot.base_commit}",
                f"--force-with-lease={head_ref}:{snapshot.head_commit}",
                "origin",
                f"{merge_commit}:{base_ref}",
                f":{head_ref}",
            ),
            label="Atomic reviewed Git ref transaction",
        )
        remote_ref_process = self._checked(
            ("git", "-C", str(root), "ls-remote", "--refs", "origin", base_ref, head_ref),
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
    ) -> None:
        """Prove a crash-recovered result is the declared exact merge transaction.

        Args:
            repository: Exact GitHub repository.
            repository_path: Exact local task worktree.
            snapshot: Provider-confirmed merged PR snapshot.
        """

        root = self._repository_and_origin_require(repository=repository, repository_path=repository_path)
        self._checked(
            (
                "git",
                "-C",
                str(root),
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "origin",
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
                snapshot.base_commit,
                snapshot.head_commit,
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
            expected_parent_list=[snapshot.base_commit, snapshot.head_commit],
        )
        head_ref = f"refs/heads/{snapshot.head_branch}"
        head_readback = self._checked(
            ("git", "-C", str(root), "ls-remote", "--refs", "origin", head_ref),
            label="Merged Git head-ref readback",
        )
        if head_readback.stdout:
            raise GitHubContractError("Crash-recovered atomic merge did not delete the reviewed head ref")

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

    def _repository_and_origin_require(
        self,
        *,
        repository: RepositoryIdentity,
        repository_path: Path,
    ) -> Path:
        """Require the exact worktree and matching GitHub origin."""

        root = self._repository_root_require(repository_path)
        origin = self._checked(("git", "-C", str(root), "remote", "get-url", "origin"), label="Git origin read")
        if _github_remote_repository_get(origin.stdout.strip()) != repository:
            raise GitHubContractError("Local Git origin differs from the exact pull-request repository")
        return root

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
    """Parse one exact GitHub HTTPS, SSH URL or canonical scp-like remote."""

    if not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise GitHubContractError("Git origin has another shape")
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlsplit(value)
        if parsed.hostname != "github.com" or parsed.query or parsed.fragment:
            raise GitHubContractError("Git origin is not one exact GitHub repository")
        path = parsed.path.removeprefix("/")
    if path.endswith(".git"):
        path = path[:-4]
    return RepositoryIdentity(path)


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
    if commit_by_ref_map != {expected_base_ref: expected_base_commit} or deleted_head_ref in commit_by_ref_map:
        raise GitHubContractError("Atomic Git ref readback differs from the complete transaction")


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
