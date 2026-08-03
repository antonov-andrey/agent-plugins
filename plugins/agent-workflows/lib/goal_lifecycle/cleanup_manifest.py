"""Closed YAML bootstrap resource and external-cleanup declaration owner."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write, json_object_load
from goal_lifecycle.identity import common_prefix_validate
from goal_lifecycle.yaml_document import yaml_document_load

BOOTSTRAP_MANIFEST_NAME = "worktree-bootstrap.yaml"
_RESOURCE_KEY_TUPLE = (
    "copy_optional_path_list",
    "copy_required_path_list",
    "link_optional_path_list",
    "link_required_path_list",
)
_RESERVED_FIRST_COMPONENT_SET = {".git", ".worktree", BOOTSTRAP_MANIFEST_NAME}


def _path_validate(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise GoalLifecycleError("Bootstrap resource path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GoalLifecycleError(f"Bootstrap resource path must be normalized and relative: {value}")
    if path.parts[0] in _RESERVED_FIRST_COMPONENT_SET:
        raise GoalLifecycleError(f"Bootstrap resource path is reserved: {value}")
    return value


@dataclass(frozen=True, slots=True)
class CleanupDeclaration:
    """One closed direct-argv project-owned cleanup hook."""

    command_argument_list: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: object) -> "CleanupDeclaration":
        if not isinstance(payload, dict) or set(payload) != {"command_argument_list"}:
            raise GoalLifecycleError("Bootstrap cleanup declaration has another shape")
        argument_list = payload["command_argument_list"]
        if (
            not isinstance(argument_list, list)
            or not argument_list
            or any(not isinstance(item, str) or not item or "\x00" in item for item in argument_list)
        ):
            raise GoalLifecycleError("Cleanup command_argument_list must be a non-empty string list")
        for argument in argument_list:
            if "{" in argument or "}" in argument:
                if argument != "{common_prefix}":
                    raise GoalLifecycleError("Cleanup declaration contains an unknown or embedded placeholder")
        return cls(command_argument_list=tuple(argument_list))

    def normalized_sha256_get(self) -> str:
        payload = b"\x00".join(item.encode("utf-8") for item in self.command_argument_list)
        return hashlib.sha256(payload).hexdigest()

    def command_get(self, *, common_prefix: str) -> list[str]:
        common_prefix_validate(common_prefix)
        return [common_prefix if item == "{common_prefix}" else item for item in self.command_argument_list]


@dataclass(frozen=True, slots=True)
class BootstrapManifest:
    """One current schema-v2 bootstrap manifest."""

    cleanup: CleanupDeclaration | None
    resource_by_key_map: dict[str, tuple[str, ...]]
    sha256: str


def bootstrap_manifest_load(path: Path) -> BootstrapManifest:
    payload = yaml_document_load(path)
    if not isinstance(payload, dict) or set(payload) not in (
        {"schema_version", "resource"},
        {"schema_version", "resource", "cleanup"},
    ):
        raise GoalLifecycleError("Bootstrap manifest has another top-level shape")
    if payload["schema_version"] != 2 or isinstance(payload["schema_version"], bool):
        raise GoalLifecycleError("Bootstrap manifest schema_version must equal integer 2")
    resource = payload["resource"]
    if not isinstance(resource, dict) or set(resource) != set(_RESOURCE_KEY_TUPLE):
        raise GoalLifecycleError("Bootstrap resource mapping has another shape")
    path_by_key_map: dict[str, tuple[str, ...]] = {}
    all_path_list: list[str] = []
    for key in _RESOURCE_KEY_TUPLE:
        value = resource[key]
        if not isinstance(value, list):
            raise GoalLifecycleError(f"Bootstrap resource {key} must be a list")
        path_list = tuple(_path_validate(item) for item in value)
        if len(path_list) != len(set(path_list)):
            raise GoalLifecycleError(f"Bootstrap resource {key} repeats a path")
        path_by_key_map[key] = path_list
        all_path_list.extend(path_list)
    if len(all_path_list) != len(set(all_path_list)):
        raise GoalLifecycleError("Bootstrap resource classes overlap")
    for index, left in enumerate(all_path_list):
        for right in all_path_list[index + 1 :]:
            if left.startswith(right + "/") or right.startswith(left + "/"):
                raise GoalLifecycleError("Bootstrap resource paths overlap by ancestry")
    cleanup = CleanupDeclaration.from_payload(payload["cleanup"]) if "cleanup" in payload else None
    return BootstrapManifest(
        cleanup=cleanup,
        resource_by_key_map=path_by_key_map,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def cleanup_binding_receipt_path_get(repository_root: Path, *, common_prefix: str, git: Git | None = None) -> Path:
    command = git or Git()
    common_directory = command.common_directory_get(repository_root)
    return common_directory / "agent-workflows" / "cleanup-binding" / f"{common_prefix}.json"


def cleanup_binding_receipt_write(
    repository_root: Path,
    *,
    common_prefix: str,
    provider_state_generation: int,
    sealed_specification_sha256: str,
    git: Git | None = None,
    storage_repository_root: Path | None = None,
) -> Path:
    """Bind one sealed manifest declaration before external task-resource mutation."""

    if provider_state_generation < 1:
        raise GoalLifecycleError("Cleanup binding provider-state generation must be positive")
    common_prefix_validate(common_prefix)
    if len(sealed_specification_sha256) != 64:
        raise GoalLifecycleError("Cleanup binding requires one SHA-256 specification identity")
    manifest_path = repository_root / BOOTSTRAP_MANIFEST_NAME
    manifest = bootstrap_manifest_load(manifest_path)
    cleanup_sha256 = manifest.cleanup.normalized_sha256_get() if manifest.cleanup else ""
    path = cleanup_binding_receipt_path_get(
        storage_repository_root or repository_root,
        common_prefix=common_prefix,
        git=git,
    )
    expected = {
        "schema_version": 1,
        "common_prefix": common_prefix,
        "cleanup_declaration_sha256": cleanup_sha256,
        "manifest_sha256": manifest.sha256,
        "provider_state_generation": provider_state_generation,
        "sealed_specification_sha256": sealed_specification_sha256,
    }
    if path.exists():
        if json_object_load(path, label="cleanup binding receipt") != expected:
            raise GoalLifecycleError("Existing cleanup binding receipt differs from the sealed task")
        return path
    atomic_json_write(path, expected)
    return path


def cleanup_binding_receipt_validate(
    repository_root: Path,
    *,
    common_prefix: str,
    provider_state_generation: int,
    sealed_specification_sha256: str,
    git: Git | None = None,
    storage_repository_root: Path | None = None,
) -> dict[str, Any]:
    """Require an exact current receipt before task-scoped external mutation."""

    manifest = bootstrap_manifest_load(repository_root / BOOTSTRAP_MANIFEST_NAME)
    path = cleanup_binding_receipt_path_get(
        storage_repository_root or repository_root,
        common_prefix=common_prefix,
        git=git,
    )
    payload = json_object_load(path, label="cleanup binding receipt")
    cleanup_sha256 = manifest.cleanup.normalized_sha256_get() if manifest.cleanup else ""
    recorded_provider_state_generation = payload.get("provider_state_generation")
    if set(payload) != {
        "schema_version",
        "common_prefix",
        "cleanup_declaration_sha256",
        "manifest_sha256",
        "provider_state_generation",
        "sealed_specification_sha256",
    } or (
        payload.get("schema_version") != 1
        or payload.get("common_prefix") != common_prefix
        or payload.get("cleanup_declaration_sha256") != cleanup_sha256
        or payload.get("manifest_sha256") != manifest.sha256
        or payload.get("sealed_specification_sha256") != sealed_specification_sha256
        or recorded_provider_state_generation != provider_state_generation
        or not isinstance(recorded_provider_state_generation, int)
        or isinstance(recorded_provider_state_generation, bool)
        or recorded_provider_state_generation < 1
    ):
        raise GoalLifecycleError("Cleanup binding receipt is absent, stale, or malformed")
    return payload
