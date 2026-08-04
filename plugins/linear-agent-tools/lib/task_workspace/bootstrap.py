"""Strict YAML bootstrap manifest and owned resource materialization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil

from task_workspace.model import BootstrapResourceState, TaskWorkspaceError

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
    required: bool
    source_identity: str
    skipped: bool

    def planned_state(self) -> BootstrapResourceState:
        """Return the durable pre-mutation ownership record.

        Returns:
            Planned resource state.
        """

        return BootstrapResourceState(
            relative_path=self.relative_path,
            kind=self.kind,
            source_identity=self.source_identity,
            phase="planned",
            skipped=self.skipped,
        )


@dataclass(frozen=True, slots=True)
class BootstrapPlan:
    """Contain the exact validated bootstrap manifest effect."""

    manifest_sha256: str
    resource_list: list[BootstrapResource]
    cleanup_argument_list: list[str]

    def __post_init__(self) -> None:
        """Detach validated plan collections from parser-local mutation."""

        object.__setattr__(self, "resource_list", list(self.resource_list))
        object.__setattr__(self, "cleanup_argument_list", list(self.cleanup_argument_list))


def manifest_parse(payload_bytes: bytes, *, main_root: Path) -> BootstrapPlan:
    """Parse and bind one exact committed YAML bootstrap manifest.

    Args:
        payload_bytes: Exact ordinary blob bytes from the task baseline commit.
        main_root: Exact canonical repository checkout.

    Returns:
        Validated materialization plan.
    """

    payload = _yaml_document_load(payload_bytes)
    if not isinstance(payload, dict) or set(payload) not in (
        {"schema_version", "resource"},
        {"schema_version", "resource", "cleanup"},
    ):
        raise TaskWorkspaceError("worktree-bootstrap.yaml has another top-level shape")
    if payload["schema_version"] != 2:
        raise TaskWorkspaceError("worktree-bootstrap.yaml schema_version must be 2")
    resource = payload["resource"]
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
                    source_identity = f"absent:{normalized}"
                    skipped = True
                else:
                    source_identity = _source_identity_get(source, kind=kind)
                    skipped = False
                resource_list.append(
                    BootstrapResource(
                        relative_path=normalized,
                        kind=kind,
                        required=required,
                        source_identity=source_identity,
                        skipped=skipped,
                    )
                )
    path_list = [item.relative_path for item in resource_list]
    if len(path_list) != len(set(path_list)):
        raise TaskWorkspaceError("Bootstrap manifest repeats one resource path")
    cleanup_argument_list: list[str] = []
    if "cleanup" in payload:
        cleanup = payload["cleanup"]
        if not isinstance(cleanup, dict) or set(cleanup) != {"command_argument_list"}:
            raise TaskWorkspaceError("Bootstrap cleanup mapping has another shape")
        arguments = cleanup["command_argument_list"]
        if (
            not isinstance(arguments, list)
            or not arguments
            or any(not isinstance(item, str) or not item or "\x00" in item for item in arguments)
        ):
            raise TaskWorkspaceError("Bootstrap cleanup command must use direct non-empty argv")
        cleanup_argument_list = list(arguments)
    return BootstrapPlan(
        manifest_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        resource_list=sorted(resource_list, key=lambda item: item.relative_path),
        cleanup_argument_list=cleanup_argument_list,
    )


def resource_materialize(resource: BootstrapResource, *, main_root: Path, task_root: Path) -> None:
    """Materialize or reconcile one state-predeclared bootstrap resource.

    Args:
        resource: Exact planned resource.
        main_root: Canonical source checkout.
        task_root: Exact issue-owned worktree.
    """

    if resource.skipped:
        return
    source = main_root / resource.relative_path
    if _source_identity_get(source, kind=resource.kind) != resource.source_identity:
        raise TaskWorkspaceError(f"Bootstrap source changed after ownership was recorded: {resource.relative_path}")
    destination_parent = _destination_parent_require(
        task_root,
        relative_path=resource.relative_path,
        create=True,
    )
    destination = destination_parent / PurePosixPath(resource.relative_path).name
    if destination.exists() or destination.is_symlink():
        if _destination_matches(destination, resource):
            return
        raise TaskWorkspaceError(f"Owned bootstrap destination conflicts with its plan: {resource.relative_path}")
    temporary = destination.parent / f".{destination.name}.linear-agent-{secrets.token_hex(8)}"
    try:
        if resource.kind == "link":
            temporary.symlink_to(source.resolve(strict=True))
        elif source.is_dir():
            shutil.copytree(source, temporary, symlinks=False)
            _tree_sync(temporary)
        else:
            shutil.copy2(source, temporary, follow_symlinks=False)
            _file_sync(temporary)
        os.replace(temporary, destination)
        _directory_sync(destination.parent)
    except BaseException:
        if temporary.is_symlink() or temporary.is_file():
            temporary.unlink(missing_ok=True)
        elif temporary.is_dir():
            shutil.rmtree(temporary)
        raise
    if not _destination_matches(destination, resource):
        raise TaskWorkspaceError(f"Bootstrap resource read-back failed: {resource.relative_path}")


def resource_ready_require(resource: BootstrapResource, *, task_root: Path) -> None:
    """Require one ready state-owned destination without repairing it.

    Args:
        resource: Exact recorded resource contract.
        task_root: Exact issue-owned worktree.
    """

    if resource.skipped:
        return
    destination_parent = _destination_parent_require(
        task_root,
        relative_path=resource.relative_path,
        create=False,
    )
    destination = destination_parent / PurePosixPath(resource.relative_path).name
    if not (destination.exists() or destination.is_symlink()) or not _destination_matches(destination, resource):
        raise TaskWorkspaceError(f"Bootstrap resource is absent or changed: {resource.relative_path}")


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


def _source_identity_get(path: Path, *, kind: str) -> str:
    """Return the exact source identity required for reconciliation.

    Args:
        path: Source path.
        kind: Copy or link.

    Returns:
        Stable identity text.
    """

    if kind == "link":
        return f"link:{path.resolve(strict=True)}"
    return f"copy:{_path_fingerprint(path)}"


def _destination_matches(path: Path, resource: BootstrapResource) -> bool:
    """Return whether one pre-owned destination matches its planned identity.

    Args:
        path: Destination path.
        resource: Exact planned resource.

    Returns:
        Whether read-back matches.
    """

    if resource.kind == "link":
        return path.is_symlink() and f"link:{path.resolve(strict=True)}" == resource.source_identity
    return not path.is_symlink() and f"copy:{_path_fingerprint(path)}" == resource.source_identity


def _path_fingerprint(path: Path) -> str:
    """Return a canonical content and mode fingerprint for one copy tree.

    Args:
        path: File or directory root.

    Returns:
        Lowercase SHA-256 identity.
    """

    digest = hashlib.sha256()
    if path.is_symlink():
        raise TaskWorkspaceError(f"Copy bootstrap source may not be a symlink: {path}")
    entry_list = [path] if path.is_file() else [path, *sorted(path.rglob("*"))]
    for entry in entry_list:
        relative = "." if entry == path else entry.relative_to(path).as_posix()
        if entry.is_symlink():
            raise TaskWorkspaceError(f"Copy bootstrap tree may not contain symlinks: {entry}")
        kind = "d" if entry.is_dir() else "f" if entry.is_file() else ""
        if not kind:
            raise TaskWorkspaceError(f"Copy bootstrap tree contains unsupported entry: {entry}")
        digest.update(kind.encode())
        digest.update(relative.encode("utf-8"))
        digest.update((entry.stat().st_mode & 0o777).to_bytes(2, "big"))
        if kind == "f":
            with entry.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
    return digest.hexdigest()


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
