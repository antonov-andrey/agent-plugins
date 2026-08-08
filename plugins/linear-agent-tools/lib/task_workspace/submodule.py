"""Recursive read-only submodule preparation and exact snapshotting."""

from __future__ import annotations

from pathlib import Path
import re

from task_workspace.model import TaskWorkspaceError, WorkspaceSubmoduleState
from task_workspace.repository import (
    git_config_bytes_record_list_get,
    git_command_run,
    git_command_text_get,
    git_relative_transport_destination_parse,
    git_repository_origin_transport_pair_get,
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
                transport_by_name_map = _submodule_transport_by_name_map_get(owner_root, missing_path_list)
                git_command_run(
                    owner_root,
                    (
                        "submodule",
                        "update",
                        "--init",
                        "--checkout",
                        "--",
                        *missing_path_list,
                    ),
                    submodule_transport_by_name_map=transport_by_name_map,
                    transport_url_list=tuple(sorted(set(transport_by_name_map.values()))),
                )
        return self.read()

    def read(self) -> list[WorkspaceSubmoduleState]:
        """Validate every recursive checkout at its exact index gitlink.

        Returns:
            Sorted repository-relative path/commit pairs.
        """

        submodule_state_list: list[WorkspaceSubmoduleState] = []

        def visit(root: Path, parent_path: str) -> None:
            declaration_by_path_map = _submodule_declaration_by_path_get(root)
            gitlink_by_path_map = _direct_gitlink_by_path_get(root)
            if set(declaration_by_path_map) != set(gitlink_by_path_map):
                raise TaskWorkspaceError("Submodule declaration differs from the exact index gitlinks")
            for relative_path, expected_commit in gitlink_by_path_map.items():
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
        declaration_by_path_map = _submodule_declaration_by_path_get(root)
        gitlink_by_path_map = _direct_gitlink_by_path_get(root)
        if set(declaration_by_path_map) != set(gitlink_by_path_map):
            raise TaskWorkspaceError("Submodule declaration differs from the exact index gitlinks")
        for relative_path in gitlink_by_path_map:
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


def _submodule_transport_by_name_map_get(repository_root: Path, relative_path_list: list[str]) -> dict[str, str]:
    """Return exact resolved submodule destinations for one update invocation.

    Args:
        repository_root: Exact repository that owns the current gitlinks.
        relative_path_list: Direct initialized gitlink paths updated by the invocation.

    Returns:
        Canonical transport destinations keyed by exact submodule name.
    """

    if (
        not relative_path_list
        or relative_path_list != sorted(relative_path_list)
        or len(relative_path_list) != len(set(relative_path_list))
    ):
        raise TaskWorkspaceError("Submodule update path list is malformed")
    declaration_by_path_map = _submodule_declaration_by_path_get(repository_root)
    if any(relative_path not in declaration_by_path_map for relative_path in relative_path_list):
        raise TaskWorkspaceError("Submodule transport declaration is missing one initialized path")
    return {
        declaration_by_path_map[relative_path][0]: declaration_by_path_map[relative_path][1]
        for relative_path in relative_path_list
    }


def _submodule_declaration_by_path_get(repository_root: Path) -> dict[str, tuple[str, str]]:
    """Parse and resolve every direct submodule declaration before invoking Git."""

    record_list = _gitmodules_record_list_get(repository_root)
    if not record_list:
        return {}
    value_list_by_name_and_field_map: dict[tuple[str, str], list[str]] = {}
    allowed_field_set = {"branch", "fetchrecursesubmodules", "ignore", "path", "shallow", "update", "url"}
    for config_name, value in record_list:
        if not config_name.startswith("submodule.") or "." not in config_name.removeprefix("submodule."):
            raise TaskWorkspaceError("Submodule transport declaration is malformed")
        name_and_field = config_name.removeprefix("submodule.")
        name, field = name_and_field.rsplit(".", 1)
        if (
            not name
            or re.fullmatch(r"[A-Za-z0-9._/-]+", name) is None
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or field not in allowed_field_set
            or (field == "update" and value.startswith("!"))
        ):
            raise TaskWorkspaceError("Submodule transport declaration is unsafe")
        value_list_by_name_and_field_map.setdefault((name, field), []).append(value)

    name_set = {name for name, _field in value_list_by_name_and_field_map}
    origin_pair = git_repository_origin_transport_pair_get(repository_root)
    if origin_pair is None:
        raise TaskWorkspaceError("Submodule owner has no validated origin")
    declaration_by_path_map: dict[str, tuple[str, str]] = {}
    for name in name_set:
        path_value_list = value_list_by_name_and_field_map.get((name, "path"), [])
        url_value_list = value_list_by_name_and_field_map.get((name, "url"), [])
        if len(path_value_list) != 1 or len(url_value_list) != 1:
            raise TaskWorkspaceError("Submodule transport declaration is missing or ambiguous")
        relative_path = path_value_list[0]
        if (
            not relative_path
            or relative_path.startswith(("/", "-"))
            or "\\" in relative_path
            or any(ord(character) < 32 or ord(character) == 127 for character in relative_path)
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
            or relative_path in declaration_by_path_map
        ):
            raise TaskWorkspaceError("Submodule transport declaration has an unsafe or repeated path")
        destination = git_relative_transport_destination_parse(origin_pair[0], url_value_list[0])
        declaration_by_path_map[relative_path] = (name, destination.url)
    return declaration_by_path_map


def _gitmodules_record_list_get(repository_root: Path) -> list[tuple[str, str]]:
    """Read the clean stage-zero Gitmodules blob used by the current gitlinks.

    Args:
        repository_root: Exact repository that owns the current index.

    Returns:
        Committed configuration records from the clean stage-zero index entry.
    """

    for argument_list in (
        ("diff", "--quiet", "--", ".gitmodules"),
        ("diff", "--cached", "--quiet", "--", ".gitmodules"),
    ):
        completed_process = git_command_run(repository_root, argument_list, check=False)
        if completed_process.returncode == 1:
            raise TaskWorkspaceError("Submodule authority requires a clean committed .gitmodules")
        if completed_process.returncode != 0:
            raise TaskWorkspaceError("Submodule authority could not validate committed .gitmodules")

    index_payload = git_command_run(
        repository_root,
        ("ls-files", "--stage", "-z", "--", ".gitmodules"),
    ).stdout
    entry_list = [entry for entry in index_payload.split(b"\0") if entry]
    if not entry_list:
        return []
    if len(entry_list) != 1 or b"\t" not in entry_list[0]:
        raise TaskWorkspaceError("Submodule authority found ambiguous .gitmodules index state")
    try:
        metadata, encoded_path = entry_list[0].split(b"\t", 1)
        mode, encoded_blob, stage = metadata.split(b" ")
        path = encoded_path.decode("utf-8", errors="strict")
        blob = encoded_blob.decode("ascii", errors="strict")
    except (UnicodeDecodeError, ValueError) as error:
        raise TaskWorkspaceError("Submodule authority found malformed .gitmodules index state") from error
    if mode not in {b"100644", b"100755"} or stage != b"0" or path != ".gitmodules" or not match_full_commit(blob):
        raise TaskWorkspaceError("Submodule authority requires one ordinary stage-zero .gitmodules")
    payload_bytes = git_command_run(repository_root, ("cat-file", "blob", blob)).stdout
    return git_config_bytes_record_list_get(payload_bytes)


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
