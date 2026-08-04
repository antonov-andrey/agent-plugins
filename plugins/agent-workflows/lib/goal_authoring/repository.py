"""Exact project-goals repository boundary for source authoring."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import stat
import subprocess

from goal_authoring.model import (
    GoalAuthoringError,
    commit_validate,
    common_prefix_validate,
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


def _git_command_run(
    repository: Path,
    argument_list: Sequence[str],
    *,
    check: bool = True,
    input_bytes: bytes | None = None,
    extra_environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run Git at one explicit path without inherited repository redirection."""

    environment = os.environ.copy()
    for name in list(environment):
        if name in _GIT_REDIRECTION_NAME_SET or name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            environment.pop(name, None)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if extra_environment:
        environment.update(extra_environment)
    result = subprocess.run(
        ["git", "-C", str(repository), *argument_list],
        capture_output=True,
        check=False,
        env=environment,
        input=input_bytes,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise GoalAuthoringError(
            f"Git command failed in {repository}: git {' '.join(argument_list)}: "
            f"{detail or f'exit status {result.returncode}'}"
        )
    return result


def _git_command_text_get(repository: Path, argument_list: Sequence[str], *, check: bool = True) -> str:
    """Run Git at one explicit path and return strict UTF-8 output."""

    return _git_command_run(repository, argument_list, check=check).stdout.decode("utf-8", errors="strict").strip()


class GitRepository:
    """Run checked Git operations without inherited repository redirection."""

    def __init__(self, root: Path) -> None:
        """Bind one exact canonical worktree root.

        Args:
            root: Candidate repository path.
        """

        discovered = _git_command_text_get(root, ("rev-parse", "--show-toplevel"))
        try:
            self.root = Path(discovered).resolve(strict=True)
        except OSError as error:
            raise GoalAuthoringError(f"Repository root is unavailable: {root}") from error

    def run(
        self,
        argument_list: Sequence[str],
        *,
        check: bool = True,
        input_bytes: bytes | None = None,
        extra_environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run Git inside the bound repository.

        Args:
            argument_list: Direct Git arguments.
            check: Whether to reject a nonzero result.
            input_bytes: Optional standard input bytes.
            extra_environment: Explicit environment additions.

        Returns:
            The completed command.
        """

        return _git_command_run(
            self.root,
            argument_list,
            check=check,
            input_bytes=input_bytes,
            extra_environment=extra_environment,
        )

    def output(self, argument_list: Sequence[str], *, check: bool = True) -> str:
        """Return decoded output from Git in the bound repository.

        Args:
            argument_list: Direct Git arguments.
            check: Whether to reject a nonzero result.

        Returns:
            The stripped command output.
        """

        return self.run(argument_list, check=check).stdout.decode("utf-8", errors="strict").strip()

    def commit(self, ref: str = "HEAD") -> str:
        """Resolve one ref to a full commit identity.

        Args:
            ref: Git ref.

        Returns:
            The commit identity.
        """

        return commit_validate(self.output(("rev-parse", "--verify", f"{ref}^{{commit}}")), label=ref)

    def common_directory(self) -> Path:
        """Return the shared Git administration directory.

        Returns:
            The resolved common directory.
        """

        candidate = Path(self.output(("rev-parse", "--path-format=absolute", "--git-common-dir")))
        if candidate.is_symlink() or not candidate.is_dir():
            raise GoalAuthoringError("project-goals Git common directory must be one physical directory")
        resolved = candidate.resolve(strict=True)
        if resolved != candidate:
            raise GoalAuthoringError("project-goals Git common directory must use one canonical physical path")
        return resolved

    def origin_url(self) -> str:
        """Return the exact configured origin URL.

        Returns:
            The origin URL.
        """

        value = self.output(("remote", "get-url", "origin"))
        if not value:
            raise GoalAuthoringError("project-goals has no origin URL")
        return value

    def clean_require(self) -> None:
        """Require an entirely clean worktree."""

        if self.run(("status", "--porcelain=v1", "-z", "--ignore-submodules=none")).stdout:
            raise GoalAuthoringError("project-goals must be clean before source publication")


class ProjectGoalsRepository:
    """Validate and expose the canonical project-goals main checkout."""

    def __init__(self, root: Path) -> None:
        """Bind one explicit project-goals repository.

        Args:
            root: Exact canonical checkout path.
        """

        self.git = GitRepository(root)
        self.root = self.git.root
        if self.root.name != "project-goals":
            raise GoalAuthoringError("Source repository must be the canonical project-goals checkout")
        self.common_directory = self.git.common_directory()
        metadata_directory = self.root / ".git"
        if (
            metadata_directory.is_symlink()
            or not metadata_directory.is_dir()
            or metadata_directory.resolve(strict=True) != self.common_directory
        ):
            raise GoalAuthoringError("project-goals canonical checkout must own one physical .git directory")
        self.private_root = self.common_directory / "agent-workflows" / "goal-authoring"

    def private_directory_require(self, *relative_part_list: str) -> Path:
        """Create or validate one private physical transaction directory."""

        current = self.common_directory
        for name in ("agent-workflows", "goal-authoring", *relative_part_list):
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                raise GoalAuthoringError("Private authoring directory name is unsafe")
            child = current / name
            try:
                child.mkdir(mode=0o700)
            except FileExistsError:
                pass
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as error:
                raise GoalAuthoringError("Private authoring directory is unavailable") from error
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise GoalAuthoringError("Private authoring directory must be user-owned and physical")
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                child.chmod(0o700)
                metadata = child.stat(follow_symlinks=False)
                if stat.S_IMODE(metadata.st_mode) != 0o700:
                    raise GoalAuthoringError("Private authoring directory permissions could not be made private")
            current = child
        return current

    def synchronize_require(self) -> str:
        """Require clean local main equal to freshly fetched origin/main.

        Returns:
            The current main commit.
        """

        self.checkout_shape_require()
        self.git.run(("fetch", "--prune", "origin"))
        local_commit = self.git.commit()
        remote_commit = self.git.commit("refs/remotes/origin/main")
        if local_commit != remote_commit:
            raise GoalAuthoringError("project-goals local main must equal origin/main before source publication")
        return local_commit

    def checkout_shape_require(self) -> None:
        """Require the clean single-main project-goals checkout shape.

        This shape check intentionally does not require local and remote commits to
        match, so an interrupted successful push can be recovered.
        """

        self.git.clean_require()
        branch = self.git.output(("symbolic-ref", "--quiet", "--short", "HEAD"))
        if branch != "main":
            raise GoalAuthoringError("project-goals canonical checkout must have main checked out")
        worktree_output = self.git.run(("worktree", "list", "--porcelain", "-z")).stdout
        root_set = {
            Path(item.removeprefix(b"worktree ").decode("utf-8")).resolve(strict=False)
            for item in worktree_output.split(b"\0")
            if item.startswith(b"worktree ")
        }
        if root_set != {self.root}:
            raise GoalAuthoringError("project-goals must have exactly one canonical main worktree")
        if (self.root / ".worktree").exists():
            raise GoalAuthoringError("project-goals may not contain a worktree container")
        if (self.root / "worktree-bootstrap.yaml").exists() or (self.root / "worktree-bootstrap.toml").exists():
            raise GoalAuthoringError("project-goals may not contain a worktree bootstrap manifest")

    def source_directory(self, common_prefix: str) -> Path:
        """Return one canonical source directory.

        Args:
            common_prefix: Exact source identity.

        Returns:
            The source directory.
        """

        return self.root / common_prefix_validate(common_prefix)

    def source_shape_require(self, common_prefix: str) -> None:
        """Require one published directory to contain only the complete pair.

        Args:
            common_prefix: Exact source identity.
        """

        directory = self.source_directory(common_prefix)
        if directory.is_symlink() or not directory.is_dir():
            raise GoalAuthoringError(f"Goal source directory is unavailable: {directory}")
        entry_by_name = {entry.name: entry for entry in directory.iterdir()}
        if set(entry_by_name) != {"goal.md", "spec.md"}:
            raise GoalAuthoringError("Goal source directory must contain exactly goal.md and spec.md")
        for name, path in entry_by_name.items():
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise GoalAuthoringError(f"Goal source artifact is unavailable: {name}")

    def source_bytes(self, common_prefix: str, name: str) -> bytes:
        """Read one exact source artifact after closed-shape validation.

        Args:
            common_prefix: Exact source identity.
            name: Canonical artifact name.

        Returns:
            The artifact bytes.
        """

        if name not in {"goal.md", "spec.md"}:
            raise GoalAuthoringError("Unknown goal source artifact")
        self.source_shape_require(common_prefix)
        return (self.source_directory(common_prefix) / name).read_bytes()
