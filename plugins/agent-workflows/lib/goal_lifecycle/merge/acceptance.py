"""Accepted-checkpoint publication after exact primary-environment acceptance."""

from __future__ import annotations

from goal_lifecycle.checkpoint.model import Checkpoint, CheckpointDocument
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.yaml_document import yaml_document_bytes_get


class AcceptedCheckpointPublisher:
    """Advance the tracked accepted pointer by one exact checkpoint."""

    def __init__(self, coordination: CoordinationRepository) -> None:
        """Initialize the accepted checkpoint publisher dependencies.

        Args:
            coordination: Coordination.
        """

        self._coordination = coordination

    def publish(
        self,
        *,
        common_prefix: str,
        checkpoint: Checkpoint,
        document: CheckpointDocument,
    ) -> str:
        """Idempotently publish one accepted checkpoint on coordination main.

        Args:
            common_prefix: Exact task common prefix.
            checkpoint: Checkpoint.
            document: Document.

        Returns:
            Resulting text value.
        """

        if document.accepted_checkpoint_id == checkpoint.checkpoint_id:
            return self._coordination.synchronize_require()
        document.selection_validate(checkpoint)
        updated = CheckpointDocument(
            accepted_checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_list=document.checkpoint_list,
        )
        return self._coordination.publish(
            common_prefix=common_prefix,
            message=f"Accept {common_prefix} {checkpoint.checkpoint_id}",
            relative_payload_by_path_map={
                f"{common_prefix}/checkpoint.yaml": yaml_document_bytes_get(updated.payload_get())
            },
            task_lock_already_held=True,
        )
