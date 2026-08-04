"""Closed local workspace identity and recovery-state models."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

_ISSUE_IDENTIFIER_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class TaskWorkspaceError(RuntimeError):
    """Report one unsafe or conflicting task-workspace operation."""


def issue_identifier_validate(value: str) -> str:
    """Return one canonical uppercase Linear issue identifier.

    Args:
        value: Candidate issue identifier.

    Returns:
        The validated identifier.
    """

    if not isinstance(value, str) or _ISSUE_IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise TaskWorkspaceError("Linear issue identifier must use the canonical TEAM-123 form")
    return value


def _single_line(value: str, *, label: str) -> str:
    """Return one non-empty single-line string.

    Args:
        value: Candidate value.
        label: Diagnostic owner label.

    Returns:
        The validated value.
    """

    if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise TaskWorkspaceError(f"{label} must be non-empty single-line text")
    return value


@dataclass(frozen=True, slots=True)
class WorkspaceConfig:
    """Bind the explicit user-level root that contains canonical checkouts."""

    root: Path

    def __post_init__(self) -> None:
        """Validate one existing absolute directory without searching ancestors."""

        if not self.root.is_absolute():
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT must be an absolute path")
        try:
            resolved = self.root.resolve(strict=True)
        except OSError as error:
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT is unavailable") from error
        if not resolved.is_dir() or resolved != self.root:
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT must be a normalized existing directory")

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> "WorkspaceConfig":
        """Read the one intentional external workspace-root configuration key.

        Args:
            environment: Optional deterministic environment mapping.

        Returns:
            Validated workspace configuration.
        """

        values = os.environ if environment is None else environment
        if "LINEAR_AGENT_WORKSPACE_ROOT" not in values:
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT is required")
        value = values["LINEAR_AGENT_WORKSPACE_ROOT"]
        if value == "":
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT is present but empty")
        return cls(Path(value))


@dataclass(frozen=True, slots=True)
class RepositoryRequest:
    """Request one exact canonical repository worktree at one base branch."""

    origin_url: str
    base_branch: str
    expected_baseline_commit: str

    def __post_init__(self) -> None:
        """Validate external repository identity text and Git ref shape."""

        _single_line(self.origin_url, label="Repository origin URL")
        _single_line(self.base_branch, label="Repository base branch")
        if (
            self.base_branch.startswith("-")
            or ".." in self.base_branch
            or any(character.isspace() for character in self.base_branch)
        ):
            raise TaskWorkspaceError("Repository base branch is unsafe")
        if self.expected_baseline_commit and _COMMIT_PATTERN.fullmatch(self.expected_baseline_commit) is None:
            raise TaskWorkspaceError("Expected workspace baseline must be one full Git commit or empty")

    @classmethod
    def from_payload(cls, payload: object) -> "RepositoryRequest":
        """Parse one strict repository request.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed repository request.
        """

        if not isinstance(payload, dict) or set(payload) != {
            "origin_url",
            "base_branch",
            "expected_baseline_commit",
        }:
            raise TaskWorkspaceError("Repository request has another shape")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class WorkspaceRequest:
    """Own one issue identity and all participating repository worktrees."""

    issue_identifier: str
    repository_list: tuple[RepositoryRequest, ...]

    def __post_init__(self) -> None:
        """Validate the complete cross-repository request."""

        issue_identifier_validate(self.issue_identifier)
        if not isinstance(self.repository_list, tuple) or any(
            not isinstance(item, RepositoryRequest) for item in self.repository_list
        ):
            raise TaskWorkspaceError("Workspace repository list must contain only repository requests")
        if not self.repository_list:
            raise TaskWorkspaceError("Code-mutating task requires at least one repository")
        origin_list = [item.origin_url for item in self.repository_list]
        if len(origin_list) != len(set(origin_list)):
            raise TaskWorkspaceError("Workspace request repeats one repository origin")

    @property
    def basename(self) -> str:
        """Return the deterministic lowercase worktree basename.

        Returns:
            Lowercase issue identifier.
        """

        return self.issue_identifier.lower()

    @property
    def branch_name(self) -> str:
        """Return the deterministic task branch.

        Returns:
            Branch name shared by participating repositories.
        """

        return f"linear/{self.basename}"


@dataclass(frozen=True, slots=True)
class BootstrapResourceState:
    """Record proof and phase for one manifest-owned materialization."""

    relative_path: str
    kind: str
    source_identity: str
    phase: str
    skipped: bool

    def __post_init__(self) -> None:
        """Validate one bootstrap state record."""

        _single_line(self.relative_path, label="Bootstrap resource path")
        if self.kind not in {"copy", "link"} or self.phase not in {"planned", "ready"}:
            raise TaskWorkspaceError("Bootstrap resource kind or phase is unsupported")
        _single_line(self.source_identity, label="Bootstrap source identity")
        if not isinstance(self.skipped, bool):
            raise TaskWorkspaceError("Bootstrap skipped flag must be boolean")

    def payload(self) -> dict[str, object]:
        """Return the JSON-ready resource state.

        Returns:
            Resource state payload.
        """

        return {
            "kind": self.kind,
            "phase": self.phase,
            "relative_path": self.relative_path,
            "skipped": self.skipped,
            "source_identity": self.source_identity,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "BootstrapResourceState":
        """Parse one strict bootstrap resource state.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed resource state.
        """

        expected = {"kind", "phase", "relative_path", "skipped", "source_identity"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise TaskWorkspaceError("Bootstrap resource state has another shape")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class RepositoryWorkspaceState:
    """Contain minimal crash-recovery state for one issue-owned worktree."""

    issue_identifier: str
    origin_identity: str
    base_branch: str
    baseline_commit: str
    branch_name: str
    main_root: str
    task_root: str
    manifest_sha256: str
    phase: str
    resource_list: tuple[BootstrapResourceState, ...]
    cleanup_argument_list: tuple[str, ...]
    cleaned_resource_fingerprint_by_key: tuple[tuple[str, str], ...]
    cleanup_binding_completed: bool
    cleanup_branch_snapshot_ready: bool
    cleanup_local_branch_commit: str
    cleanup_remote_branch_commit: str
    cleanup_worktree_removal_ready: bool
    worktree_removed: bool
    remote_branch_removed: bool
    local_branch_removed: bool

    def __post_init__(self) -> None:
        """Validate one complete private state record."""

        issue_identifier_validate(self.issue_identifier)
        for label, value in (
            ("origin identity", self.origin_identity),
            ("base branch", self.base_branch),
            ("branch name", self.branch_name),
            ("main root", self.main_root),
            ("task root", self.task_root),
        ):
            _single_line(value, label=label)
        if _COMMIT_PATTERN.fullmatch(self.baseline_commit) is None:
            raise TaskWorkspaceError("Workspace baseline must be one full Git commit")
        if _SHA256_PATTERN.fullmatch(self.manifest_sha256) is None:
            raise TaskWorkspaceError("Workspace manifest fingerprint must be SHA-256")
        if self.phase not in {"planned", "worktree-ready", "bootstrap-ready"}:
            raise TaskWorkspaceError("Workspace transaction phase is unsupported")
        expected_branch = f"linear/{self.issue_identifier.lower()}"
        if self.branch_name != expected_branch:
            raise TaskWorkspaceError("Workspace branch differs from its issue identity")
        expected_task_root = Path(self.main_root) / ".worktree" / self.issue_identifier.lower()
        if Path(self.task_root) != expected_task_root:
            raise TaskWorkspaceError("Workspace path differs from its issue identity")
        if not isinstance(self.resource_list, tuple) or any(
            not isinstance(item, BootstrapResourceState) for item in self.resource_list
        ):
            raise TaskWorkspaceError("Workspace resource list must contain only bootstrap resource states")
        resource_path_list = [item.relative_path for item in self.resource_list]
        if len(resource_path_list) != len(set(resource_path_list)):
            raise TaskWorkspaceError("Workspace state repeats one bootstrap resource")
        if not isinstance(self.cleanup_argument_list, tuple) or any(
            not isinstance(item, str) or not item or any(character in item for character in ("\x00", "\n", "\r"))
            for item in self.cleanup_argument_list
        ):
            raise TaskWorkspaceError("Workspace cleanup binding must use direct non-empty argv")
        if not isinstance(self.cleaned_resource_fingerprint_by_key, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 for item in self.cleaned_resource_fingerprint_by_key
        ):
            raise TaskWorkspaceError("Cleaned resource declaration identities contain a malformed pair")
        if self.cleaned_resource_fingerprint_by_key != tuple(sorted(self.cleaned_resource_fingerprint_by_key)):
            raise TaskWorkspaceError("Cleaned resource declaration identities must be sorted")
        cleaned_key_list = [item[0] for item in self.cleaned_resource_fingerprint_by_key]
        if len(cleaned_key_list) != len(set(cleaned_key_list)):
            raise TaskWorkspaceError("Cleaned resource declaration identities repeat one key")
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(fingerprint, str)
            or _SHA256_PATTERN.fullmatch(fingerprint) is None
            for key, fingerprint in self.cleaned_resource_fingerprint_by_key
        ):
            raise TaskWorkspaceError("Cleaned resource declaration identities must bind text keys to SHA-256")
        for name in (
            "cleanup_binding_completed",
            "cleanup_branch_snapshot_ready",
            "cleanup_worktree_removal_ready",
            "worktree_removed",
            "remote_branch_removed",
            "local_branch_removed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TaskWorkspaceError(f"{name} must be boolean")
        for label, value in (
            ("cleanup local branch commit", self.cleanup_local_branch_commit),
            ("cleanup remote branch commit", self.cleanup_remote_branch_commit),
        ):
            if not isinstance(value, str) or (value and _COMMIT_PATTERN.fullmatch(value) is None):
                raise TaskWorkspaceError(f"Workspace {label} must be empty or one full Git commit")
        if self.cleanup_branch_snapshot_ready and not self.cleanup_local_branch_commit:
            raise TaskWorkspaceError("Cleanup branch snapshot must retain the exact local task head")
        if not self.cleanup_branch_snapshot_ready and (
            self.cleanup_local_branch_commit or self.cleanup_remote_branch_commit
        ):
            raise TaskWorkspaceError("Cleanup branch commits require a durable snapshot marker")
        if (self.worktree_removed or self.remote_branch_removed or self.local_branch_removed) and not (
            self.cleanup_branch_snapshot_ready
        ):
            raise TaskWorkspaceError("Destructive cleanup state requires a durable branch snapshot")
        if self.worktree_removed and not self.cleanup_worktree_removal_ready:
            raise TaskWorkspaceError("Removed worktree requires a durable removal-ready marker")
        if self.worktree_removed and not self.cleanup_binding_completed:
            raise TaskWorkspaceError("Worktree cannot be removed before its cleanup binding completes")

    def payload(self) -> dict[str, object]:
        """Return the canonical JSON-ready state object.

        Returns:
            State payload.
        """

        return {
            "schema_version": 1,
            "base_branch": self.base_branch,
            "baseline_commit": self.baseline_commit,
            "branch_name": self.branch_name,
            "cleanup_argument_list": list(self.cleanup_argument_list),
            "cleaned_resource_fingerprint_by_key": [list(item) for item in self.cleaned_resource_fingerprint_by_key],
            "cleanup_binding_completed": self.cleanup_binding_completed,
            "cleanup_branch_snapshot_ready": self.cleanup_branch_snapshot_ready,
            "cleanup_local_branch_commit": self.cleanup_local_branch_commit,
            "cleanup_remote_branch_commit": self.cleanup_remote_branch_commit,
            "cleanup_worktree_removal_ready": self.cleanup_worktree_removal_ready,
            "issue_identifier": self.issue_identifier,
            "main_root": self.main_root,
            "manifest_sha256": self.manifest_sha256,
            "origin_identity": self.origin_identity,
            "phase": self.phase,
            "local_branch_removed": self.local_branch_removed,
            "remote_branch_removed": self.remote_branch_removed,
            "resource_list": [item.payload() for item in self.resource_list],
            "task_root": self.task_root,
            "worktree_removed": self.worktree_removed,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "RepositoryWorkspaceState":
        """Parse one strict private workspace state.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed repository state.
        """

        expected = {
            "schema_version",
            "base_branch",
            "baseline_commit",
            "branch_name",
            "cleanup_argument_list",
            "cleaned_resource_fingerprint_by_key",
            "cleanup_binding_completed",
            "cleanup_branch_snapshot_ready",
            "cleanup_local_branch_commit",
            "cleanup_remote_branch_commit",
            "cleanup_worktree_removal_ready",
            "issue_identifier",
            "main_root",
            "manifest_sha256",
            "origin_identity",
            "phase",
            "local_branch_removed",
            "remote_branch_removed",
            "resource_list",
            "task_root",
            "worktree_removed",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise TaskWorkspaceError("Workspace state has another shape")
        if (
            not isinstance(payload["resource_list"], list)
            or not isinstance(payload["cleanup_argument_list"], list)
            or not isinstance(payload["cleaned_resource_fingerprint_by_key"], list)
        ):
            raise TaskWorkspaceError("Workspace state collections have another shape")
        return cls(
            issue_identifier=payload["issue_identifier"],
            origin_identity=payload["origin_identity"],
            base_branch=payload["base_branch"],
            baseline_commit=payload["baseline_commit"],
            branch_name=payload["branch_name"],
            main_root=payload["main_root"],
            task_root=payload["task_root"],
            manifest_sha256=payload["manifest_sha256"],
            phase=payload["phase"],
            resource_list=tuple(BootstrapResourceState.from_payload(item) for item in payload["resource_list"]),
            cleanup_argument_list=tuple(payload["cleanup_argument_list"]),
            cleaned_resource_fingerprint_by_key=tuple(
                tuple(item) if isinstance(item, list) else item
                for item in payload["cleaned_resource_fingerprint_by_key"]
            ),
            cleanup_binding_completed=payload["cleanup_binding_completed"],
            cleanup_branch_snapshot_ready=payload["cleanup_branch_snapshot_ready"],
            cleanup_local_branch_commit=payload["cleanup_local_branch_commit"],
            cleanup_remote_branch_commit=payload["cleanup_remote_branch_commit"],
            cleanup_worktree_removal_ready=payload["cleanup_worktree_removal_ready"],
            worktree_removed=payload["worktree_removed"],
            remote_branch_removed=payload["remote_branch_removed"],
            local_branch_removed=payload["local_branch_removed"],
        )
