"""Checked Git boundary for lifecycle repositories and refs."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Sequence

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.model import commit_validate


class Git:
    """Run Git with repository-redirection variables removed."""

    @staticmethod
    def _environment_get(*, extra: dict[str, str] | None = None) -> dict[str, str]:
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

    def run(
        self,
        repository: Path,
        argument_list: Sequence[str],
        *,
        check: bool = True,
        input_bytes: bytes | None = None,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            ["git", "-C", str(repository), *argument_list],
            capture_output=True,
            check=False,
            env=self._environment_get(extra=extra_environment),
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
        return self.run(repository, argument_list, check=check).stdout.decode("utf-8", errors="strict").strip()

    def text_with_environment(
        self,
        repository: Path,
        argument_list: Sequence[str],
        *,
        extra_environment: dict[str, str],
    ) -> str:
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
        try:
            return Path(self.text(repository, ["rev-parse", "--show-toplevel"])).resolve(strict=True)
        except (OSError, UnicodeDecodeError) as error:
            raise GoalLifecycleError(f"Repository root is unavailable: {repository}") from error

    def common_directory_get(self, repository: Path) -> Path:
        value = Path(self.text(repository, ["rev-parse", "--path-format=absolute", "--git-common-dir"]))
        return value.resolve(strict=True)

    def commit_get(self, repository: Path, ref: str = "HEAD") -> str:
        return commit_validate(self.text(repository, ["rev-parse", "--verify", f"{ref}^{{commit}}"]), label=ref)

    def branch_get(self, repository: Path) -> str:
        branch = self.text(repository, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        if not branch:
            raise GoalLifecycleError(f"Detached HEAD is forbidden: {repository}")
        return branch

    def clean_require(self, repository: Path) -> None:
        if self.run(repository, ["status", "--porcelain=v1", "-z"]).stdout:
            raise GoalLifecycleError(f"Repository must be clean before lifecycle mutation: {repository}")

    def fetch(self, repository: Path) -> None:
        self.run(repository, ["fetch", "--prune", "origin"])

    def ancestor_require(self, repository: Path, ancestor: str, descendant: str, *, label: str) -> None:
        result = self.run(repository, ["merge-base", "--is-ancestor", ancestor, descendant], check=False)
        if result.returncode != 0:
            raise GoalLifecycleError(f"{label}: {ancestor} is not an ancestor of {descendant}")

    def origin_url_get(self, repository: Path) -> str:
        url = self.text(repository, ["remote", "get-url", "origin"])
        if not url:
            raise GoalLifecycleError(f"Repository has no origin URL: {repository}")
        return url
