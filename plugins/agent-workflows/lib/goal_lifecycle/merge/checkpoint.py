"""Exact tracked checkpoint selection for goal merge."""

from __future__ import annotations

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.checkpoint.model import Checkpoint, CheckpointDocument
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.yaml_document import yaml_document_load


class GoalMergeCheckpointReader:
    """Load one exact checkpoint from a closed central task directory."""

    def __init__(self, coordination: CoordinationRepository) -> None:
        """Initialize the goal merge checkpoint reader dependencies.

        Args:
            coordination: Coordination.
        """

        self._coordination = coordination

    def get(self, *, common_prefix: str, checkpoint_id: str) -> tuple[CheckpointDocument, Checkpoint]:
        """Return one exact checkpoint and its complete validated document.

        Args:
            common_prefix: Exact task common prefix.
            checkpoint_id: Exact checkpoint identity.

        Returns:
            One exact checkpoint and its complete validated document.
        """

        self._coordination.task_directory_shape_require(common_prefix, complete=True)
        document = CheckpointDocument.from_payload(
            yaml_document_load(self._coordination.task_directory_get(common_prefix) / "checkpoint.yaml")
        )
        for checkpoint in document.checkpoint_list:
            if checkpoint.checkpoint_id == checkpoint_id:
                return document, checkpoint
        raise GoalLifecycleError(f"Checkpoint does not exist: {checkpoint_id}")
