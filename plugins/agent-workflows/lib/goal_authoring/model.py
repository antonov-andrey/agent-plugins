"""Closed models for one authoring source in project-goals."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re

_COMMON_PREFIX_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(?:-[a-z0-9]+)*")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")


class GoalAuthoringError(RuntimeError):
    """Report one rejected goal-source authoring operation."""


def common_prefix_validate(value: str) -> str:
    """Return one canonical task source directory basename.

    Args:
        value: Candidate common prefix.

    Returns:
        The validated common prefix.
    """

    if not isinstance(value, str) or _COMMON_PREFIX_PATTERN.fullmatch(value) is None:
        raise GoalAuthoringError(
            "Common prefix must use YYYY-MM-DD followed by lowercase semantic words separated by hyphens"
        )
    if PurePosixPath(value).name != value:
        raise GoalAuthoringError("Common prefix must be one filesystem basename")
    return value


def commit_validate(value: str, *, label: str) -> str:
    """Return one full lowercase Git object identity.

    Args:
        value: Candidate Git commit.
        label: Diagnostic owner label.

    Returns:
        The validated commit.
    """

    if not isinstance(value, str) or _COMMIT_PATTERN.fullmatch(value) is None:
        raise GoalAuthoringError(f"{label} must be one full lowercase Git commit")
    return value


def _markdown_validate(payload: bytes, *, label: str) -> bytes:
    """Return one non-empty UTF-8 Markdown payload.

    Args:
        payload: Candidate bytes.
        label: Diagnostic owner label.

    Returns:
        The validated bytes.
    """

    if not isinstance(payload, bytes) or not payload:
        raise GoalAuthoringError(f"{label} must be non-empty Markdown")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GoalAuthoringError(f"{label} must be UTF-8 Markdown") from error
    if not text.strip() or "\x00" in text:
        raise GoalAuthoringError(f"{label} must be non-empty UTF-8 Markdown")
    return payload


@dataclass(frozen=True, slots=True)
class GoalSource:
    """Own the complete revisable source pair for one coherent outcome."""

    common_prefix: str
    goal_markdown: bytes
    specification_markdown: bytes

    def __post_init__(self) -> None:
        """Validate the complete closed source pair."""

        common_prefix_validate(self.common_prefix)
        _markdown_validate(self.goal_markdown, label="goal.md")
        _markdown_validate(self.specification_markdown, label="spec.md")

    def relative_payload_by_path(self) -> dict[str, bytes]:
        """Return the exact pair of tracked paths and payloads.

        Returns:
            The complete source payload map.
        """

        return {
            f"{self.common_prefix}/goal.md": self.goal_markdown,
            f"{self.common_prefix}/spec.md": self.specification_markdown,
        }

    def fingerprint(self) -> str:
        """Return a canonical content fingerprint for the complete pair.

        Returns:
            A lowercase SHA-256 identity.
        """

        digest = hashlib.sha256()
        for relative_path, payload in sorted(self.relative_payload_by_path().items()):
            encoded_path = relative_path.encode("utf-8")
            digest.update(len(encoded_path).to_bytes(8, "big"))
            digest.update(encoded_path)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class GoalSourceSnapshot:
    """Describe one exact published project-goals source revision."""

    common_prefix: str
    commit: str
    fingerprint: str
    goal_path: str
    specification_path: str
    origin_url: str

    def __post_init__(self) -> None:
        """Validate identities exposed by the authoring boundary."""

        common_prefix_validate(self.common_prefix)
        commit_validate(self.commit, label="Source commit")
        if not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise GoalAuthoringError("Source fingerprint must be one lowercase SHA-256 identity")
        if self.goal_path != f"{self.common_prefix}/goal.md":
            raise GoalAuthoringError("Goal path differs from the canonical source path")
        if self.specification_path != f"{self.common_prefix}/spec.md":
            raise GoalAuthoringError("Specification path differs from the canonical source path")
        if not self.origin_url or any(character in self.origin_url for character in ("\x00", "\n", "\r")):
            raise GoalAuthoringError("Source origin URL must be non-empty single-line text")

    def payload(self) -> dict[str, str | int]:
        """Return the canonical JSON-ready result.

        Returns:
            The result object.
        """

        return {
            "schema_version": 1,
            "common_prefix": self.common_prefix,
            "commit": self.commit,
            "fingerprint": self.fingerprint,
            "goal_path": self.goal_path,
            "origin_url": self.origin_url,
            "specification_path": self.specification_path,
        }
