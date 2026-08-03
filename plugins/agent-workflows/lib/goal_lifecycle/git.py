"""Checked Git boundary for lifecycle repositories and refs."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Sequence

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.identity import commit_validate


class Git:
    """Run Git with repository-redirection variables removed."""

    def run(
        self,
        repository: Path,
        argument_list: Sequence[str],
        *,
        check: bool = True,
        input_bytes: bytes | None = None,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run Git with caller repository redirection removed from the environment.

        Args:
            repository: Exact Git repository root.
            argument_list: Exact command arguments.
            check: Whether a nonzero command exit raises an error.
            input_bytes: Input bytes.
            extra_environment: Extra environment.

        Returns:
            Completed binary-mode subprocess result.
        """

        result = subprocess.run(
            ["git", "-C", str(repository), *argument_list],
            capture_output=True,
            check=False,
            env=_git_environment_get(extra=extra_environment),
            input=input_bytes,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise GoalLifecycleError(
                f"Git command failed in {repository}: git {' '.join(argument_list)}: "
                f"{detail or f'exit status {result.returncode}'}"
            )
        return result

    def text(self, repository: Path, argument_list: Sequence[str], *, check: bool = True) -> str:
        """Run Git and decode its standard output as UTF-8 text.

        Args:
            repository: Exact Git repository root.
            argument_list: Exact command arguments.
            check: Whether a nonzero command exit raises an error.

        Returns:
            Resulting text value.
        """

        return self.run(repository, argument_list, check=check).stdout.decode("utf-8", errors="strict").strip()

    def text_with_environment(
        self,
        repository: Path,
        argument_list: Sequence[str],
        *,
        extra_environment: dict[str, str],
    ) -> str:
        """Run Git with explicit environment additions and decode UTF-8 stdout.

        Args:
            repository: Exact Git repository root.
            argument_list: Exact command arguments.
            extra_environment: Extra environment.

        Returns:
            Resulting text value.
        """

        return (
            self.run(
                repository,
                argument_list,
                extra_environment=extra_environment,
            )
            .stdout.decode("utf-8", errors="strict")
            .strip()
        )

    def root_get(self, repository: Path) -> Path:
        """Resolve the canonical top-level worktree root for one Git path.

        Args:
            repository: Exact Git repository root.

        Returns:
            The root.
        """

        try:
            return Path(self.text(repository, ["rev-parse", "--show-toplevel"])).resolve(strict=True)
        except (OSError, UnicodeDecodeError) as error:
            raise GoalLifecycleError(f"Repository root is unavailable: {repository}") from error

    def common_directory_get(self, repository: Path) -> Path:
        """Resolve the shared Git administration directory for one worktree.

        Args:
            repository: Exact Git repository root.

        Returns:
            The common directory.
        """

        value = Path(self.text(repository, ["rev-parse", "--path-format=absolute", "--git-common-dir"]))
        return value.resolve(strict=True)

    def commit_get(self, repository: Path, ref: str = "HEAD") -> str:
        """Resolve one ref to its exact lowercase full commit identity.

        Args:
            repository: Exact Git repository root.
            ref: Ref.

        Returns:
            The commit.
        """

        return commit_validate(
            self.text(repository, ["rev-parse", "--verify", f"{ref}^{{commit}}"]),
            label=ref,
        )

    def branch_get(self, repository: Path) -> str:
        """Return the exact symbolic branch checked out by one worktree.

        Args:
            repository: Exact Git repository root.

        Returns:
            The branch.
        """

        branch = self.text(repository, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        if not branch:
            raise GoalLifecycleError(f"Detached HEAD is forbidden: {repository}")
        return branch

    def clean_require(self, repository: Path) -> None:
        """Reject a repository with any tracked or untracked worktree change.

        Args:
            repository: Exact Git repository root.
        """

        if self.run(repository, ["status", "--porcelain=v1", "-z", "--ignore-submodules=none"]).stdout:
            raise GoalLifecycleError(f"Repository must be clean before lifecycle mutation: {repository}")

    def fetch(self, repository: Path) -> None:
        """Fetch current origin refs without modifying a checked-out branch.

        Args:
            repository: Exact Git repository root.
        """

        self.run(repository, ["fetch", "--prune", "origin"])

    def synchronized_main_require(self, repository: Path) -> str:
        """Require one clean canonical main checkout equal to fetched origin/main.

        Args:
            repository: Exact Git repository root.

        Returns:
            Resulting text value.
        """

        self.clean_require(repository)
        if self.branch_get(repository) != "main":
            raise GoalLifecycleError(f"Canonical main checkout is required: {repository}")
        self.fetch(repository)
        local_commit = self.commit_get(repository)
        if local_commit != self.commit_get(repository, "refs/remotes/origin/main"):
            raise GoalLifecycleError(f"Local and remote main differ: {repository}")
        return local_commit

    def ancestor_require(self, repository: Path, ancestor: str, descendant: str, *, label: str) -> None:
        """Require one commit to be reachable from another in the selected repository.

        Args:
            repository: Exact Git repository root.
            ancestor: Ancestor.
            descendant: Descendant.
            label: Diagnostic owner label.
        """

        result = self.run(
            repository,
            ["merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
        )
        if result.returncode != 0:
            raise GoalLifecycleError(f"{label}: {ancestor} is not an ancestor of {descendant}")

    def origin_url_get(self, repository: Path) -> str:
        """Return the normalized exact origin identity for one repository.

        Args:
            repository: Exact Git repository root.

        Returns:
            The origin URL.
        """

        url = self.text(repository, ["remote", "get-url", "origin"])
        if not url:
            raise GoalLifecycleError(f"Repository has no origin URL: {repository}")
        return url


def _git_environment_get(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return one Git environment without inherited repository redirection.

    Args:
        extra: Extra.

    Returns:
        One Git environment without inherited repository redirection.
    """

    environment = os.environ.copy()
    for name in tuple(environment):
        if name in {
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
        } or name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            environment.pop(name, None)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        environment.update(extra)
    return environment
