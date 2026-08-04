"""Recursive read-only submodule preparation and exact snapshotting."""

from __future__ import annotations

from pathlib import Path

from task_workspace.model import TaskWorkspaceError
from task_workspace.repository import GitCommand, re_full_commit


def recursive_submodule_prepare(repository_root: Path) -> tuple[tuple[str, str], ...]:
    """Initialize exact recursive gitlinks in a newly owned task worktree.

    Args:
        repository_root: Exact newly created task-worktree root.

    Returns:
        Sorted recursive path/commit snapshot.
    """

    if not _direct_gitlink_by_path_get(repository_root):
        return ()
    GitCommand.run(repository_root, ("submodule", "sync", "--recursive"))
    GitCommand.run(
        repository_root,
        (
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--checkout",
        ),
    )
    return recursive_submodule_snapshot_get(repository_root)


def recursive_submodule_snapshot_get(
    repository_root: Path,
) -> tuple[tuple[str, str], ...]:
    """Return and validate every recursive submodule checkout at its index gitlink.

    Args:
        repository_root: Exact repository root to inspect without repair.

    Returns:
        Sorted repository-relative path/commit pairs.
    """

    result: list[tuple[str, str]] = []

    def visit(root: Path, parent_path: str) -> None:
        for relative_path, expected_commit in _direct_gitlink_by_path_get(root).items():
            full_path = f"{parent_path}/{relative_path}" if parent_path else relative_path
            child = root / relative_path
            if child.is_symlink() or not child.is_dir():
                raise TaskWorkspaceError(f"Recursive submodule is not initialized: {full_path}")
            top_level = GitCommand.text(child, ("rev-parse", "--show-toplevel"), check=False)
            if not top_level:
                raise TaskWorkspaceError(f"Recursive submodule is not a Git checkout: {full_path}")
            try:
                resolved_top_level = Path(top_level).resolve(strict=True)
                resolved_child = child.resolve(strict=True)
            except OSError as error:
                raise TaskWorkspaceError(f"Recursive submodule path is unavailable: {full_path}") from error
            if resolved_top_level != resolved_child:
                raise TaskWorkspaceError(f"Recursive submodule checkout has another root: {full_path}")
            current_commit = GitCommand.text(child, ("rev-parse", "--verify", "HEAD^{commit}"))
            if current_commit != expected_commit:
                raise TaskWorkspaceError(f"Recursive submodule differs from its exact index gitlink: {full_path}")
            if GitCommand.run(child, ("symbolic-ref", "--quiet", "HEAD"), check=False).returncode == 0:
                raise TaskWorkspaceError(f"Recursive submodule must remain detached at its gitlink: {full_path}")
            if GitCommand.run(
                child,
                ("status", "--porcelain=v1", "-z", "--ignore-submodules=none"),
            ).stdout:
                raise TaskWorkspaceError(f"Recursive submodule contains uncommitted state: {full_path}")
            result.append((full_path, current_commit))
            visit(child, full_path)

    visit(repository_root, "")
    return tuple(sorted(result))


def _direct_gitlink_by_path_get(repository_root: Path) -> dict[str, str]:
    """Read exact stage-zero direct gitlinks from one repository index."""

    result: dict[str, str] = {}
    payload = GitCommand.run(repository_root, ("ls-files", "--stage", "-z")).stdout
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
            or not re_full_commit(commit)
        ):
            raise TaskWorkspaceError("Submodule gitlink has an unsafe path or commit identity")
        if relative_path in result:
            raise TaskWorkspaceError("Git index repeats one submodule path")
        result[relative_path] = commit
    return result
