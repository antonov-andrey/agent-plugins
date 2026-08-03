"""Durable workspace-wide ownership across merge and primary acceptance."""

from __future__ import annotations

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.io import atomic_json_write, directory_sync, json_object_load


class WorkspaceMergeOwner:
    """Bind one goal and checkpoint across separate merge and acceptance calls."""

    def __init__(self, coordination: CoordinationRepository) -> None:
        self._coordination = coordination

    def acquire(self, *, common_prefix: str, checkpoint_id: str) -> None:
        """Create or validate the exact exclusive durable owner marker."""

        owner_path = self._coordination.merge_owner_path_get()
        expected = {
            "schema_version": 1,
            "common_prefix": common_prefix,
            "checkpoint_id": checkpoint_id,
        }
        if not owner_path.exists():
            atomic_json_write(owner_path, expected)
            return
        owner = json_object_load(owner_path, label="workspace merge owner")
        if owner == expected:
            return
        raise GoalLifecycleError("Another goal owns the exclusive workspace merge lifecycle")

    def advance(
        self,
        *,
        common_prefix: str,
        previous_checkpoint_id: str,
        checkpoint_id: str,
    ) -> None:
        """Idempotently move the exact same goal owner to one fix-forward checkpoint."""

        owner_path = self._coordination.merge_owner_path_get()
        previous = {
            "schema_version": 1,
            "common_prefix": common_prefix,
            "checkpoint_id": previous_checkpoint_id,
        }
        updated = {
            "schema_version": 1,
            "common_prefix": common_prefix,
            "checkpoint_id": checkpoint_id,
        }
        owner = json_object_load(owner_path, label="workspace merge owner")
        if owner == updated:
            return
        if owner != previous:
            raise GoalLifecycleError("Workspace merge owner cannot advance from another checkpoint")
        atomic_json_write(
            owner_path,
            updated,
        )

    def release(self, *, common_prefix: str, checkpoint_id: str) -> None:
        """Remove the exact owner only after accepted-pointer publication."""

        owner_path = self._coordination.merge_owner_path_get()
        if not owner_path.exists():
            return
        self.acquire(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
        owner_path.unlink()
        directory_sync(owner_path.parent)
