"""Recursive read-only submodule preparation and exact snapshotting."""

from __future__ import annotations

from pathlib import Path

from task_workspace.model import TaskWorkspaceError, WorkspaceSubmoduleState
from task_workspace.repository import (
    git_command_run,
    git_command_text_get,
    match_full_commit,
)


class WorkspaceSubmoduleReader:
    """Prepare and read recursive submodules for one exact repository root."""

    def __init__(self, repository_root: Path) -> None:
        """Bind the repository inspected by all operations.

        Args:
            repository_root: Exact newly created task-worktree root.
        """

        self._repository_root = repository_root

    def prepare(self) -> list[WorkspaceSubmoduleState]:
        """Initialize exact recursive gitlinks in the bound task worktree.

        Returns:
            Sorted recursive path/commit snapshot.
        """

        missing_update_list = _missing_gitlink_update_list_get(self._repository_root)
        if not missing_update_list:
            return self.read()
        for owner_root, missing_path_list in missing_update_list:
            git_command_run(owner_root, ("submodule", "sync", "--recursive"))
            git_command_run(
                owner_root,
                (
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "update",
                    "--init",
                    "--recursive",
                    "--checkout",
                    "--",
                    *missing_path_list,
                ),
            )
        return self.read()

    def read(self) -> list[WorkspaceSubmoduleState]:
        """Validate every recursive checkout at its exact index gitlink.

        Returns:
            Sorted repository-relative path/commit pairs.
        """

        submodule_state_list: list[WorkspaceSubmoduleState] = []

        def visit(root: Path, parent_path: str) -> None:
            for relative_path, expected_commit in _direct_gitlink_by_path_get(root).items():
                full_path = f"{parent_path}/{relative_path}" if parent_path else relative_path
                child = root / relative_path
                if child.is_symlink() or not child.is_dir():
                    raise TaskWorkspaceError(f"Recursive submodule is not initialized: {full_path}")
                top_level = git_command_text_get(child, ("rev-parse", "--show-toplevel"), check=False)
                if not top_level:
                    raise TaskWorkspaceError(f"Recursive submodule is not a Git checkout: {full_path}")
                try:
                    resolved_top_level = Path(top_level).resolve(strict=True)
                    resolved_child = child.resolve(strict=True)
                except OSError as error:
                    raise TaskWorkspaceError(f"Recursive submodule path is unavailable: {full_path}") from error
                if resolved_top_level != resolved_child:
                    raise TaskWorkspaceError(f"Recursive submodule checkout has another root: {full_path}")
                current_commit = git_command_text_get(child, ("rev-parse", "--verify", "HEAD^{commit}"))
                if current_commit != expected_commit:
                    raise TaskWorkspaceError(f"Recursive submodule differs from its exact index gitlink: {full_path}")
                if git_command_run(child, ("symbolic-ref", "--quiet", "HEAD"), check=False).returncode == 0:
                    raise TaskWorkspaceError(f"Recursive submodule must remain detached at its gitlink: {full_path}")
                if git_command_run(
                    child,
                    ("status", "--porcelain=v1", "-z", "--ignore-submodules=none"),
                ).stdout:
                    raise TaskWorkspaceError(f"Recursive submodule contains uncommitted state: {full_path}")
                submodule_state_list.append(WorkspaceSubmoduleState(relative_path=full_path, commit=current_commit))
                visit(child, full_path)

        visit(self._repository_root, "")
        return sorted(submodule_state_list, key=lambda item: item.relative_path)


def _missing_gitlink_update_list_get(repository_root: Path) -> list[tuple[Path, list[str]]]:
    """Return owner-local updates for uninitialized recursive gitlinks.

    Args:
        repository_root: Exact task-worktree root.

    Returns:
        Traversal-ordered repository roots and their direct paths to initialize.
    """

    missing_path_list_by_owner_root_map: dict[Path, list[str]] = {}

    def missing_append(owner_root: Path, relative_path: str) -> None:
        """Append one direct gitlink to its current owning repository."""

        missing_path_list_by_owner_root_map.setdefault(owner_root, []).append(relative_path)

    def visit(root: Path) -> None:
        for relative_path in _direct_gitlink_by_path_get(root):
            child = root / relative_path
            if child.is_symlink() or not child.is_dir():
                if not child.exists() and not child.is_symlink():
                    missing_append(root, relative_path)
                continue
            top_level = git_command_text_get(child, ("rev-parse", "--show-toplevel"), check=False)
            if not top_level:
                missing_append(root, relative_path)
                continue
            try:
                resolved_top_level = Path(top_level).resolve(strict=True)
                resolved_child = child.resolve(strict=True)
            except OSError:
                missing_append(root, relative_path)
                continue
            if resolved_top_level != resolved_child:
                missing_append(root, relative_path)
                continue
            visit(child)

    visit(repository_root)
    return [
        (owner_root, sorted(relative_path_list))
        for owner_root, relative_path_list in missing_path_list_by_owner_root_map.items()
    ]


def _direct_gitlink_by_path_get(repository_root: Path) -> dict[str, str]:
    """Read exact stage-zero direct gitlinks from one repository index."""

    commit_by_relative_path_map: dict[str, str] = {}
    payload = git_command_run(repository_root, ("ls-files", "--stage", "-z")).stdout
    for record in payload.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, encoded_commit, encoded_stage = metadata.split(b" ")
        except ValueError as error:
            raise TaskWorkspaceError("Git index returned malformed stage metadata") from error
        if mode != b"160000":
            continue
        if encoded_stage != b"0":
            raise TaskWorkspaceError("Submodule has no single stage-zero gitlink")
        try:
            relative_path = encoded_path.decode("utf-8")
            commit = encoded_commit.decode("ascii")
        except UnicodeDecodeError as error:
            raise TaskWorkspaceError("Submodule path or commit identity is malformed") from error
        if (
            not relative_path
            or relative_path.startswith("/")
            or "\\" in relative_path
            or any(ord(character) < 32 for character in relative_path)
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
            or not match_full_commit(commit)
        ):
            raise TaskWorkspaceError("Submodule gitlink has an unsafe path or commit identity")
        if relative_path in commit_by_relative_path_map:
            raise TaskWorkspaceError("Git index repeats one submodule path")
        commit_by_relative_path_map[relative_path] = commit
    return commit_by_relative_path_map
