"""Strict YAML bootstrap manifest and owned resource materialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat

from task_cleanup.contract import CleanupResourceContractError, cleanup_handler_key_validate
from task_workspace.model import TaskWorkspaceError

_MAPPING_LINE_PATTERN = re.compile(r"(?P<indent> *)(?P<key>[a-z][a-z0-9_]*)\s*:(?P<value>.*)")
_SEQUENCE_LINE_PATTERN = re.compile(r" {4}- (?P<value>.+)")
_INTEGER_PATTERN = re.compile(r"[-+]?(?:0|[1-9][0-9]*)")
_FLOAT_PATTERN = re.compile(r"[-+]?(?:(?:0|[1-9][0-9]*)\.[0-9]+|(?:0|[1-9][0-9]*)[eE][-+]?[0-9]+)")
_SINGLE_QUOTED_PATTERN = re.compile(r"'(?P<value>(?:[^']|'')*)'")


@dataclass(frozen=True, slots=True)
class BootstrapResource:
    """Describe one manifest-owned copy or link materialization."""

    relative_path: str
    kind: str
    skipped: bool

    def materialize(self, *, main_root: Path, task_root: Path, temporary_root: Path) -> None:
        """Materialize or reconcile this manifest-declared resource.

        Args:
            main_root: Canonical source checkout.
            task_root: Exact issue-owned worktree.
            temporary_root: Issue-private deterministic staging root.
        """

        if self.skipped:
            return
        temporary_parent = _temporary_parent_require(
            temporary_root,
            relative_path=self.relative_path,
            create=True,
        )
        if temporary_parent is None:
            raise TaskWorkspaceError("Bootstrap temporary parent was not created")
        temporary = temporary_parent / PurePosixPath(self.relative_path).name
        _temporary_path_remove(temporary)
        try:
            source = main_root / self.relative_path
            if self.kind == "copy":
                _copy_source_validate(source)
            destination_parent = _destination_parent_require(
                task_root,
                relative_path=self.relative_path,
                create=True,
            )
            destination = destination_parent / PurePosixPath(self.relative_path).name
            if destination.exists() or destination.is_symlink():
                if _match_destination(destination, source=source, kind=self.kind):
                    return
                raise TaskWorkspaceError(f"Owned bootstrap destination conflicts with its plan: {self.relative_path}")
            if self.kind == "link":
                temporary.symlink_to(source.resolve(strict=True))
            elif source.is_dir():
                shutil.copytree(source, temporary, symlinks=True)
                _copy_source_validate(temporary)
                _tree_sync(temporary)
            else:
                shutil.copy2(source, temporary, follow_symlinks=False)
                _copy_source_validate(temporary)
                _file_sync(temporary)
            if self.kind == "copy":
                _copy_source_validate(source)
                if not _path_match(source, temporary):
                    raise TaskWorkspaceError(f"Copy bootstrap source changed during materialization: {source}")
            os.replace(temporary, destination)
            _directory_sync(destination.parent)
            if not _match_destination(destination, source=source, kind=self.kind):
                raise TaskWorkspaceError(f"Bootstrap resource read-back failed: {self.relative_path}")
        finally:
            _temporary_path_remove(temporary)
            _temporary_parent_prune(temporary_root, temporary_parent)

    def transient_cleanup(self, *, temporary_root: Path) -> None:
        """Remove only this resource's deterministic owned crash residue."""

        if self.skipped:
            return
        temporary_parent = _temporary_parent_require(
            temporary_root,
            relative_path=self.relative_path,
            create=False,
        )
        if temporary_parent is None:
            return
        _temporary_path_remove(temporary_parent / PurePosixPath(self.relative_path).name)
        _temporary_parent_prune(temporary_root, temporary_parent)

    def ready_require(self, *, main_root: Path, task_root: Path) -> None:
        """Require this manifest-owned destination without repairing it.

        Args:
            main_root: Canonical source checkout.
            task_root: Exact issue-owned worktree.
        """

        if self.skipped:
            return
        destination_parent = _destination_parent_require(
            task_root,
            relative_path=self.relative_path,
            create=False,
        )
        destination = destination_parent / PurePosixPath(self.relative_path).name
        source = main_root / self.relative_path
        if not (destination.exists() or destination.is_symlink()) or not _match_destination(
            destination,
            source=source,
            kind=self.kind,
        ):
            raise TaskWorkspaceError(f"Bootstrap resource is absent or changed: {self.relative_path}")


@dataclass(frozen=True, slots=True)
class BootstrapPlan:
    """Contain the exact validated bootstrap manifest effect."""

    resource_list: list[BootstrapResource]
    cleanup_handler_key_list: list[str]

    def __post_init__(self) -> None:
        """Detach validated plan collections from parser-local mutation."""

        object.__setattr__(self, "resource_list", list(self.resource_list))
        object.__setattr__(self, "cleanup_handler_key_list", list(self.cleanup_handler_key_list))

    @classmethod
    def from_manifest(cls, payload_bytes: bytes, *, main_root: Path) -> "BootstrapPlan":
        """Parse and bind one exact committed YAML bootstrap manifest.

        Args:
            payload_bytes: Exact ordinary blob bytes from the task baseline commit.
            main_root: Exact canonical repository checkout.

        Returns:
            Validated materialization plan.
        """

        payload = _yaml_document_load(payload_bytes)
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "resource", "cleanup"}:
            raise TaskWorkspaceError("worktree-bootstrap.yaml has another top-level shape")
        if payload["schema_version"] != 3:
            raise TaskWorkspaceError("worktree-bootstrap.yaml schema_version must be 3")
        resource_list = _bootstrap_resource_list_get(payload["resource"], main_root=main_root)
        cleanup = payload["cleanup"]
        if not isinstance(cleanup, dict) or set(cleanup) != {"handler_key_list"}:
            raise TaskWorkspaceError("Bootstrap cleanup mapping has another shape")
        handler_key_list = cleanup["handler_key_list"]
        if (
            not isinstance(handler_key_list, list)
            or any(not isinstance(item, str) for item in handler_key_list)
            or handler_key_list != sorted(handler_key_list)
            or len(handler_key_list) != len(set(handler_key_list))
        ):
            raise TaskWorkspaceError("Bootstrap cleanup handler keys must be unique and sorted")
        try:
            validated_handler_key_list = [cleanup_handler_key_validate(item) for item in handler_key_list]
        except CleanupResourceContractError as error:
            raise TaskWorkspaceError("Bootstrap cleanup handler is absent from the provider registry") from error
        return cls(
            resource_list=sorted(resource_list, key=lambda item: item.relative_path),
            cleanup_handler_key_list=validated_handler_key_list,
        )


def _bootstrap_resource_list_get(resource: object, *, main_root: Path) -> list[BootstrapResource]:
    """Return the exact materialization resources from the current schema.

    Args:
        resource: Parsed resource mapping.
        main_root: Exact canonical repository checkout.

    Returns:
        Sorted validated bootstrap resources.
    """

    expected_resource_fields = {
        "copy_optional_path_list",
        "copy_required_path_list",
        "link_optional_path_list",
        "link_required_path_list",
    }
    if not isinstance(resource, dict) or set(resource) != expected_resource_fields:
        raise TaskWorkspaceError("Bootstrap resource mapping has another shape")
    resource_list: list[BootstrapResource] = []
    for kind in ("copy", "link"):
        for required in (False, True):
            field = f"{kind}_{'required' if required else 'optional'}_path_list"
            path_list = resource[field]
            if not isinstance(path_list, list) or any(not isinstance(item, str) for item in path_list):
                raise TaskWorkspaceError(f"Bootstrap field {field} must be a text list")
            for relative_path in path_list:
                normalized = _relative_path_validate(relative_path)
                source = main_root / normalized
                if not source.exists() and not source.is_symlink():
                    if required:
                        raise TaskWorkspaceError(f"Required bootstrap source is absent: {normalized}")
                    skipped = True
                else:
                    skipped = False
                resource_list.append(
                    BootstrapResource(
                        relative_path=normalized,
                        kind=kind,
                        skipped=skipped,
                    )
                )
    path_list = [item.relative_path for item in resource_list]
    if len(path_list) != len(set(path_list)):
        raise TaskWorkspaceError("Bootstrap manifest repeats one resource path")
    return sorted(resource_list, key=lambda item: item.relative_path)


def _yaml_document_load(payload_bytes: bytes) -> object:
    """Read the exact strict YAML subset owned by the bootstrap manifest.

    Args:
        payload_bytes: Exact committed ordinary-file bytes.

    Returns:
        Decoded object.
    """

    if not isinstance(payload_bytes, bytes):
        raise TaskWorkspaceError("Bootstrap manifest payload must be bytes")
    try:
        payload_text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TaskWorkspaceError("Bootstrap manifest is not UTF-8") from error
    if "\x00" in payload_text:
        raise TaskWorkspaceError("Bootstrap manifest contains NUL")
    if "\t" in payload_text:
        raise TaskWorkspaceError("Bootstrap manifest may not contain tabs")
    line_list = payload_text.splitlines()
    if not line_list or any(line.strip() in {"---", "..."} for line in line_list):
        raise TaskWorkspaceError("Bootstrap manifest must contain exactly one implicit document")

    payload: dict[str, object] = {}
    section_by_name_map: dict[str, dict[str, object]] = {}
    current_section_name = ""
    current_list_name = ""
    for line_number, line in enumerate(line_list, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        sequence_match = _SEQUENCE_LINE_PATTERN.fullmatch(line)
        if sequence_match is not None:
            if not current_section_name or not current_list_name:
                raise TaskWorkspaceError(f"Bootstrap manifest has an unexpected list item at line {line_number}")
            item_list = section_by_name_map[current_section_name][current_list_name]
            if not isinstance(item_list, list):
                raise TaskWorkspaceError(f"Bootstrap manifest list ownership is malformed at line {line_number}")
            item_list.append(_yaml_scalar_parse(sequence_match.group("value"), line_number=line_number))
            continue

        mapping_match = _MAPPING_LINE_PATTERN.fullmatch(line)
        if mapping_match is None:
            raise TaskWorkspaceError(f"Bootstrap manifest has unsupported YAML at line {line_number}")
        indent = len(mapping_match.group("indent"))
        key = mapping_match.group("key")
        raw_value = mapping_match.group("value").strip()
        if indent == 0:
            current_list_name = ""
            if key in payload:
                raise TaskWorkspaceError(f"Bootstrap manifest repeats key {key}")
            if raw_value:
                payload[key] = _yaml_scalar_parse(raw_value, line_number=line_number)
                current_section_name = ""
            else:
                section: dict[str, object] = {}
                payload[key] = section
                section_by_name_map[key] = section
                current_section_name = key
            continue
        if indent != 2 or not current_section_name:
            raise TaskWorkspaceError(f"Bootstrap manifest has invalid indentation at line {line_number}")
        section = section_by_name_map[current_section_name]
        if key in section:
            raise TaskWorkspaceError(f"Bootstrap manifest repeats key {current_section_name}.{key}")
        if raw_value == "[]":
            section[key] = []
            current_list_name = ""
        elif not raw_value:
            section[key] = []
            current_list_name = key
        else:
            section[key] = _yaml_scalar_parse(raw_value, line_number=line_number)
            current_list_name = ""
    if not payload:
        raise TaskWorkspaceError("Bootstrap manifest must contain exactly one non-empty document")
    return payload


def _yaml_scalar_parse(value: str, *, line_number: int) -> object:
    """Parse one scalar from the manifest-owned YAML subset.

    Args:
        value: Exact scalar text without surrounding indentation.
        line_number: One-based source line for bounded diagnostics.

    Returns:
        Parsed scalar value.
    """

    if not value or any(character in value for character in "\x00\r\n"):
        raise TaskWorkspaceError(f"Bootstrap manifest scalar is malformed at line {line_number}")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise TaskWorkspaceError(f"Bootstrap manifest quoted scalar is malformed at line {line_number}") from error
        if not isinstance(parsed, str):
            raise TaskWorkspaceError(f"Bootstrap manifest quoted scalar must be text at line {line_number}")
        return parsed
    if value.startswith("'"):
        single_quoted_match = _SINGLE_QUOTED_PATTERN.fullmatch(value)
        if single_quoted_match is None:
            raise TaskWorkspaceError(f"Bootstrap manifest quoted scalar is malformed at line {line_number}")
        return single_quoted_match.group("value").replace("''", "'")
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if _INTEGER_PATTERN.fullmatch(value) is not None:
        return int(value, 10)
    if _FLOAT_PATTERN.fullmatch(value) is not None:
        return float(value)
    if value.startswith(("&", "*", "!", "%", "[", "{", "|", ">", "@", "`", "#")) or ": " in value or " #" in value:
        raise TaskWorkspaceError(f"Bootstrap manifest plain scalar is unsupported at line {line_number}")
    return value


def _destination_parent_require(
    task_root: Path,
    *,
    relative_path: str,
    create: bool,
) -> Path:
    """Return a physical destination parent without following repository symlinks.

    Args:
        task_root: Exact registered task-worktree root.
        relative_path: Already validated repository-relative resource path.
        create: Whether missing physical parent directories may be created.

    Returns:
        Exact physical destination parent.
    """

    if task_root.is_symlink() or not task_root.is_dir() or task_root.resolve(strict=True) != task_root:
        raise TaskWorkspaceError("Task root must be one physical canonical directory")
    current = task_root
    for part in PurePosixPath(relative_path).parts[:-1]:
        child = current / part
        if child.is_symlink() or (child.exists() and not child.is_dir()):
            raise TaskWorkspaceError(f"Bootstrap destination parent is not a physical directory: {relative_path}")
        if not child.exists():
            if not create:
                raise TaskWorkspaceError(f"Bootstrap destination parent is absent: {relative_path}")
            child.mkdir(mode=0o755)
            _directory_sync(current)
        if child.resolve(strict=True) != child:
            raise TaskWorkspaceError(f"Bootstrap destination parent escapes the task root: {relative_path}")
        current = child
    return current


def _temporary_parent_require(
    temporary_root: Path,
    *,
    relative_path: str,
    create: bool,
) -> Path | None:
    """Return a private physical staging parent for one deterministic resource."""

    try:
        root_metadata = temporary_root.stat(follow_symlinks=False)
    except OSError as error:
        raise TaskWorkspaceError("Bootstrap temporary root is unavailable") from error
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != os.getuid()
        or stat.S_IMODE(root_metadata.st_mode) & 0o077
        or temporary_root.resolve(strict=True) != temporary_root
    ):
        raise TaskWorkspaceError("Bootstrap temporary root must be one private user-owned physical directory")
    current = temporary_root
    for part in PurePosixPath(relative_path).parts[:-1]:
        child = current / part
        try:
            metadata = child.stat(follow_symlinks=False)
        except FileNotFoundError:
            if not create:
                return None
            child.mkdir(mode=0o700)
            _directory_sync(current)
            metadata = child.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise TaskWorkspaceError("Bootstrap temporary parent is not one private user-owned physical directory")
        current = child
    return current


def _temporary_path_remove(path: Path) -> None:
    """Remove one exact owned staging path without touching any sibling."""

    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise TaskWorkspaceError("Bootstrap temporary path is unavailable") from error
    if metadata.st_uid != os.getuid():
        raise TaskWorkspaceError("Bootstrap temporary path belongs to another user")
    try:
        if stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
            path.unlink()
        elif stat.S_ISDIR(metadata.st_mode):
            shutil.rmtree(path)
        else:
            raise TaskWorkspaceError("Bootstrap temporary path has a foreign file type")
        _directory_sync(path.parent)
    except TaskWorkspaceError:
        raise
    except OSError as error:
        raise TaskWorkspaceError("Bootstrap temporary path could not be removed") from error


def _temporary_parent_prune(temporary_root: Path, parent: Path) -> None:
    """Prune only empty resource-created staging parents below the owner root."""

    current = parent
    while current != temporary_root:
        try:
            current.rmdir()
        except OSError:
            return
        parent_directory = current.parent
        _directory_sync(parent_directory)
        current = parent_directory


def _relative_path_validate(value: str) -> str:
    """Return one safe normalized repository-relative path.

    Args:
        value: Candidate path.

    Returns:
        Normalized POSIX path text.
    """

    if not value or "\x00" in value or "\\" in value:
        raise TaskWorkspaceError("Bootstrap resource path is malformed")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", "..", ".git", ".worktree"} for part in path.parts)
    ):
        raise TaskWorkspaceError(f"Bootstrap resource path is unsafe: {value}")
    return value


def _copy_source_validate(source: Path) -> None:
    """Reject every non-physical copy source before destination mutation.

    Args:
        source: Copy root or descendant to inspect with lstat semantics.
    """

    try:
        source_stat = source.lstat()
    except OSError as error:
        raise TaskWorkspaceError(f"Copy bootstrap source cannot be inspected: {source}") from error
    if stat.S_ISLNK(source_stat.st_mode):
        raise TaskWorkspaceError(f"Copy bootstrap source may not be a symlink: {source}")
    if stat.S_ISREG(source_stat.st_mode):
        return
    if not stat.S_ISDIR(source_stat.st_mode):
        raise TaskWorkspaceError(f"Copy bootstrap source has an unsupported type: {source}")
    try:
        with os.scandir(source) as child_iterator:
            child_name_list = sorted(child.name for child in child_iterator)
    except OSError as error:
        raise TaskWorkspaceError(f"Copy bootstrap source cannot be inspected: {source}") from error
    for child_name in child_name_list:
        _copy_source_validate(source / child_name)


def _match_destination(path: Path, *, source: Path, kind: str) -> bool:
    """Return whether one destination has the manifest-declared source semantics.

    Args:
        path: Destination path.
        source: Current canonical source path.
        kind: Copy or link materialization.

    Returns:
        Whether the destination matches the source.
    """

    if kind == "link":
        return path.is_symlink() and path.resolve(strict=True) == source.resolve(strict=True)
    return _path_match(source, path)


def _path_match(source: Path, destination: Path) -> bool:
    """Compare one copy source and destination directly without a stored digest.

    Args:
        source: Canonical copy source.
        destination: Materialized task-worktree path.

    Returns:
        Whether type, mode, names and file content match recursively.
    """

    if source.is_symlink():
        raise TaskWorkspaceError(f"Copy bootstrap source may not be a symlink: {source}")
    if destination.is_symlink() or (source.stat().st_mode & 0o777) != (destination.stat().st_mode & 0o777):
        return False
    if source.is_file():
        if not destination.is_file() or source.stat().st_size != destination.stat().st_size:
            return False
        with source.open("rb") as source_handle, destination.open("rb") as destination_handle:
            while source_chunk := source_handle.read(1024 * 1024):
                if destination_handle.read(1024 * 1024) != source_chunk:
                    return False
            return destination_handle.read(1) == b""
    if not source.is_dir():
        raise TaskWorkspaceError(f"Copy bootstrap source has an unsupported type: {source}")
    if not destination.is_dir():
        return False
    source_child_by_name_map = {child.name: child for child in source.iterdir()}
    destination_child_by_name_map = {child.name: child for child in destination.iterdir()}
    if set(source_child_by_name_map) != set(destination_child_by_name_map):
        return False
    return all(
        _path_match(source_child, destination_child_by_name_map[name])
        for name, source_child in source_child_by_name_map.items()
    )


def _file_sync(path: Path) -> None:
    """Fsync one regular file.

    Args:
        path: Exact file path.
    """

    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory_sync(path: Path) -> None:
    """Fsync one directory.

    Args:
        path: Exact directory path.
    """

    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _tree_sync(root: Path) -> None:
    """Fsync every copied file and directory bottom-up.

    Args:
        root: Copied tree root.
    """

    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            _file_sync(path)
        elif path.is_dir():
            _directory_sync(path)
    _directory_sync(root)
