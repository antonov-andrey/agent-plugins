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

        processed_owner_path_set: set[tuple[Path, str]] = set()
        while missing_update_list := _missing_gitlink_update_list_get(self._repository_root):
            for owner_root, missing_path_list in missing_update_list:
                owner_path_set = {(owner_root, relative_path) for relative_path in missing_path_list}
                if processed_owner_path_set.intersection(owner_path_set):
                    raise TaskWorkspaceError("Submodule initialization did not create one declared checkout")
                processed_owner_path_set.update(owner_path_set)
                git_command_run(owner_root, ("submodule", "init", "--", *missing_path_list))
                git_command_run(owner_root, ("submodule", "sync", "--", *missing_path_list))
                git_command_run(
                    owner_root,
                    (
                        "submodule",
                        "update",
                        "--checkout",
                        "--",
                        *missing_path_list,
                    ),
                    transport_url_list=_submodule_transport_url_list_get(owner_root, missing_path_list),
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


def _submodule_transport_url_list_get(repository_root: Path, relative_path_list: list[str]) -> list[str]:
    """Return exact declared submodule destinations for one update invocation.

    Args:
        repository_root: Exact repository that owns the current gitlinks.
        relative_path_list: Direct initialized gitlink paths updated by the invocation.

    Returns:
        Duplicate-free sorted transport destinations from its tracked declaration.
    """

    if (
        not relative_path_list
        or relative_path_list != sorted(relative_path_list)
        or len(relative_path_list) != len(set(relative_path_list))
    ):
        raise TaskWorkspaceError("Submodule update path list is malformed")
    completed_process = git_command_run(
        repository_root,
        (
            "config",
            "--file",
            ".gitmodules",
            "--no-includes",
            "--null",
            "--get-regexp",
            r"^submodule\..*\.path$",
        ),
        check=False,
    )
    if completed_process.returncode not in {0, 1}:
        raise TaskWorkspaceError("Submodule transport declaration is malformed")
    if completed_process.returncode == 1:
        raise TaskWorkspaceError("Submodule transport declaration is missing")
    try:
        record_list = [record for record in completed_process.stdout.split(b"\0") if record]
        key_value_list = [record.split(b"\n", 1) for record in record_list]
        path_by_name_map = {
            key.removeprefix(b"submodule.").removesuffix(b".path").decode("utf-8"): value.decode("utf-8")
            for key, value in key_value_list
        }
    except (UnicodeDecodeError, ValueError) as error:
        raise TaskWorkspaceError("Submodule transport declaration is malformed") from error
    if (
        not path_by_name_map
        or len(path_by_name_map) != len(record_list)
        or len(path_by_name_map.values()) != len(set(path_by_name_map.values()))
        or any(not name or any(ord(character) < 32 for character in name) for name in path_by_name_map)
    ):
        raise TaskWorkspaceError("Submodule transport declaration is ambiguous")
    name_by_path_map = {path: name for name, path in path_by_name_map.items()}
    if any(relative_path not in name_by_path_map for relative_path in relative_path_list):
        raise TaskWorkspaceError("Submodule transport declaration is missing one initialized path")
    url_list: list[str] = []
    for relative_path in relative_path_list:
        name = name_by_path_map[relative_path]
        url_process = git_command_run(
            repository_root,
            ("config", "--local", "--null", "--get-all", f"submodule.{name}.url"),
            check=False,
        )
        value_list = [value for value in url_process.stdout.split(b"\0") if value]
        if url_process.returncode != 0 or len(value_list) != 1:
            raise TaskWorkspaceError("Submodule transport configuration is missing or ambiguous")
        try:
            url = value_list[0].decode("utf-8")
        except UnicodeDecodeError as error:
            raise TaskWorkspaceError("Submodule transport configuration is malformed") from error
        if not url or any(character in url for character in "\x00\r\n"):
            raise TaskWorkspaceError("Submodule transport configuration is malformed")
        url_list.append(url)
    return sorted(set(url_list))


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
