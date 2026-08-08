"""Closed local workspace identity and recovery-state models."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from git_origin.identity import GitOriginError, origin_identity_get

_ISSUE_IDENTIFIER_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")


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


def _absolute_path_text_validate(value: str, *, label: str) -> str:
    """Return one canonical absolute host path identity.

    Args:
        value: Candidate path text.
        label: Diagnostic owner label.

    Returns:
        Validated path text.
    """

    path_text = _single_line(value, label=label)
    path = Path(path_text)
    if (
        not path.is_absolute()
        or path_text.startswith("//")
        or str(path) != path_text
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise TaskWorkspaceError(f"{label} must be one canonical absolute path")
    return path_text


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
        """Validate one checkout-container root without searching ancestors."""

        _absolute_path_text_validate(str(self.root), label="LINEAR_AGENT_WORKSPACE_ROOT")
        try:
            resolved = self.root.resolve(strict=True)
        except OSError as error:
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT is unavailable") from error
        if not resolved.is_dir() or resolved != self.root:
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT must be a normalized existing directory")
        if os.path.lexists(self.root / ".git"):
            raise TaskWorkspaceError(
                "LINEAR_AGENT_WORKSPACE_ROOT must contain canonical checkouts, not be a Git repository or worktree"
            )

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> "WorkspaceConfig":
        """Read the one intentional external workspace-root configuration key.

        Args:
            environment: Optional deterministic environment mapping.

        Returns:
            Validated workspace configuration.
        """

        environment_by_name_map = os.environ if environment is None else environment
        if "LINEAR_AGENT_WORKSPACE_ROOT" not in environment_by_name_map:
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT is required")
        value = environment_by_name_map["LINEAR_AGENT_WORKSPACE_ROOT"]
        if value == "":
            raise TaskWorkspaceError("LINEAR_AGENT_WORKSPACE_ROOT is present but empty")
        _absolute_path_text_validate(value, label="LINEAR_AGENT_WORKSPACE_ROOT")
        return cls(Path(value))


@dataclass(frozen=True, slots=True)
class RepositoryRequest:
    """Request one exact canonical repository worktree at one base branch."""

    origin_identity: str
    base_branch: str
    expected_baseline_commit: str

    def __post_init__(self) -> None:
        """Validate canonical repository identity and Git ref shape."""

        _single_line(self.origin_identity, label="Repository origin identity")
        try:
            normalized_identity = origin_identity_get(self.origin_identity)
        except GitOriginError as error:
            raise TaskWorkspaceError("Repository origin identity is unsafe or unsupported") from error
        object.__setattr__(self, "origin_identity", normalized_identity)
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
        return cls(
            origin_identity=payload["origin_url"],
            base_branch=payload["base_branch"],
            expected_baseline_commit=payload["expected_baseline_commit"],
        )


@dataclass(frozen=True, slots=True)
class WorkspaceSubmoduleState:
    """Bind one recursive repository-relative submodule path to its exact commit."""

    relative_path: str
    commit: str

    def __post_init__(self) -> None:
        """Validate one exact recursive submodule snapshot entry."""

        _single_line(self.relative_path, label="Submodule relative path")
        if (
            self.relative_path.startswith("/")
            or "\\" in self.relative_path
            or any(part in {"", ".", ".."} for part in self.relative_path.split("/"))
        ):
            raise TaskWorkspaceError("Submodule relative path is unsafe")
        if _COMMIT_PATTERN.fullmatch(self.commit) is None:
            raise TaskWorkspaceError("Submodule commit must be one full Git commit")

    def payload(self) -> dict[str, str]:
        """Return one JSON-ready submodule state."""

        return {"commit": self.commit, "relative_path": self.relative_path}


@dataclass(frozen=True, slots=True)
class WorkspaceRequest:
    """Own one issue identity and all participating repository worktrees."""

    issue_identifier: str
    repository_list: list[RepositoryRequest]

    def __post_init__(self) -> None:
        """Validate the complete cross-repository request."""

        issue_identifier_validate(self.issue_identifier)
        if not isinstance(self.repository_list, list) or any(
            not isinstance(item, RepositoryRequest) for item in self.repository_list
        ):
            raise TaskWorkspaceError("Workspace repository list must contain only repository requests")
        if not self.repository_list:
            raise TaskWorkspaceError("Code-mutating task requires at least one repository")
        origin_identity_list = [item.origin_identity for item in self.repository_list]
        if len(origin_identity_list) != len(set(origin_identity_list)):
            raise TaskWorkspaceError("Workspace request repeats one repository origin")
        object.__setattr__(self, "repository_list", list(self.repository_list))

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
class RepositoryWorkspaceState:
    """Retain only the first-attempt Git baseline that recovery cannot derive."""

    baseline_commit: str

    def __post_init__(self) -> None:
        """Validate one immutable repository baseline."""

        if _COMMIT_PATTERN.fullmatch(self.baseline_commit) is None:
            raise TaskWorkspaceError("Workspace baseline must be one full Git commit")

    def payload(self) -> dict[str, object]:
        """Return the minimal private state payload.

        Returns:
            State payload.
        """

        return {"schema_version": 1, "baseline_commit": self.baseline_commit}

    @classmethod
    def from_payload(cls, payload: object) -> "RepositoryWorkspaceState":
        """Parse one strict minimal private state payload.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed repository state.
        """

        if not isinstance(payload, dict) or set(payload) != {"schema_version", "baseline_commit"}:
            raise TaskWorkspaceError("Workspace state has another shape")
        if payload["schema_version"] != 1:
            raise TaskWorkspaceError("Workspace state schema version is unsupported")
        return cls(baseline_commit=payload["baseline_commit"])
