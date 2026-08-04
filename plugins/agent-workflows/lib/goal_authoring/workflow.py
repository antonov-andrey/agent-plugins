"""Thin workflow over source validation and direct-main publication."""

from __future__ import annotations

from pathlib import Path

from goal_authoring.model import GoalAuthoringError, GoalSource, GoalSourceSnapshot
from goal_authoring.repository import ProjectGoalsRepository
from goal_authoring.transaction import GoalSourceTransaction


class GoalAuthoringWorkflow:
    """Sequence one freely revisable source pair without execution lifecycle."""

    def __init__(self, goals_repository: Path) -> None:
        """Bind one exact canonical project-goals checkout.

        Args:
            goals_repository: Exact project-goals root.
        """

        self._repository = ProjectGoalsRepository(goals_repository)
        self._transaction = GoalSourceTransaction(self._repository)

    def write(self, *, common_prefix: str, goal_input: Path, specification_input: Path) -> GoalSourceSnapshot:
        """Publish one initial or revised complete source pair.

        Args:
            common_prefix: Exact source directory identity.
            goal_input: Complete goal Markdown input.
            specification_input: Complete specification Markdown input.

        Returns:
            The exact published source snapshot.
        """

        source = GoalSource(
            common_prefix=common_prefix,
            goal_markdown=_regular_file_read(goal_input, label="Goal input"),
            specification_markdown=_regular_file_read(specification_input, label="Specification input"),
        )
        commit = self._transaction.publish(source)
        return self._snapshot(source, commit=commit)

    def validate(self, *, common_prefix: str) -> GoalSourceSnapshot:
        """Validate and describe one exact published source pair.

        Args:
            common_prefix: Exact source directory identity.

        Returns:
            The current exact source snapshot.
        """

        commit = self._repository.synchronize_require()
        source = GoalSource(
            common_prefix=common_prefix,
            goal_markdown=self._repository.source_bytes(common_prefix, "goal.md"),
            specification_markdown=self._repository.source_bytes(common_prefix, "spec.md"),
        )
        return self._snapshot(source, commit=commit)

    def _snapshot(self, source: GoalSource, *, commit: str) -> GoalSourceSnapshot:
        """Build one immutable source result.

        Args:
            source: Exact source payload.
            commit: Exact containing commit.

        Returns:
            The source snapshot.
        """

        return GoalSourceSnapshot(
            common_prefix=source.common_prefix,
            commit=commit,
            fingerprint=source.fingerprint(),
            goal_path=f"{source.common_prefix}/goal.md",
            specification_path=f"{source.common_prefix}/spec.md",
            origin_url=self._repository.git.origin_url(),
        )


def _regular_file_read(path: Path, *, label: str) -> bytes:
    """Read one exact ordinary input file.

    Args:
        path: Candidate input path.
        label: Diagnostic owner label.

    Returns:
        The file bytes.
    """

    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise GoalAuthoringError(f"{label} must be one ordinary file: {path}")
        return path.read_bytes()
    except OSError as error:
        raise GoalAuthoringError(f"{label} is unavailable: {path}") from error
