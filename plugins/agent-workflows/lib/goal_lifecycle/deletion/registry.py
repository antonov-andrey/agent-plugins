"""Remote-main registry access for deletion without canonical-worktree gates."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Iterator

from goal_lifecycle.checkpoint.model import CheckpointDocument
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.yaml_document import yaml_document_bytes_get, yaml_document_load


class GoalDeletionRegistry:
    """Read and mutate one retained goal record from clean temporary main."""

    def __init__(self, coordination: CoordinationRepository, *, git: Git) -> None:
        """Initialize the deletion registry boundary.

        Args:
            coordination: Canonical coordination checkout and private-state owner.
            git: Git command boundary.
        """

        self._coordination = coordination
        self._git = git

    def document_get(self, common_prefix: str) -> CheckpointDocument:
        """Read the current remote-main checkpoint document.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            Current checkpoint document.
        """

        with self._temporary_coordination_get() as coordination:
            return self._document_get(coordination, common_prefix=common_prefix)

    def deleted_mark(self, common_prefix: str) -> None:
        """Idempotently mark the retained remote-main record deleted.

        Args:
            common_prefix: Exact task common prefix.
        """

        for _ in range(4):
            try:
                with self._temporary_coordination_get() as coordination:
                    document = self._document_get(
                        coordination,
                        common_prefix=common_prefix,
                    )
                    if document.task_resource_state == "deleted":
                        self._canonical_fast_forward_if_safe()
                        return
                    updated = CheckpointDocument(
                        accepted_checkpoint_id=document.accepted_checkpoint_id,
                        checkpoint_list=document.checkpoint_list,
                        task_resource_state="deleted",
                    )
                    coordination.publish(
                        common_prefix=common_prefix,
                        message=f"Mark {common_prefix} task resources deleted",
                        relative_payload_by_path_map={
                            f"{common_prefix}/checkpoint.yaml": yaml_document_bytes_get(updated.payload_get())
                        },
                    )
                self._canonical_fast_forward_if_safe()
                return
            except GoalLifecycleError as error:
                if "Concurrent project-goals update overlaps this exact path set" not in str(error):
                    raise
        raise GoalLifecycleError("Goal registry deletion marker exceeded bounded concurrent-update retries")

    @staticmethod
    def _document_get(
        coordination: CoordinationRepository,
        *,
        common_prefix: str,
    ) -> CheckpointDocument:
        """Read one exact complete goal registry entry.

        Args:
            coordination: Clean temporary coordination checkout.
            common_prefix: Exact task common prefix.

        Returns:
            Current checkpoint document.
        """

        coordination.task_directory_shape_require(common_prefix, complete=True)
        checkpoint_path = coordination.task_directory_get(common_prefix) / "checkpoint.yaml"
        return CheckpointDocument.from_payload(yaml_document_load(checkpoint_path))

    @contextmanager
    def _temporary_coordination_get(self) -> Iterator[CoordinationRepository]:
        """Yield one isolated clean checkout of current project-goals main.

        Yields:
            Temporary coordination repository.
        """

        origin_url = self._git.origin_url_get(self._coordination.root)
        with tempfile.TemporaryDirectory(prefix="goal-delete-registry-") as directory:
            root = Path(directory) / "project-goals"
            self._git.run(
                self._coordination.root,
                [
                    "clone",
                    "--branch",
                    "main",
                    "--no-tags",
                    "--single-branch",
                    origin_url,
                    str(root),
                ],
            )
            for name in ("user.email", "user.name"):
                value = self._git.text(
                    self._coordination.root,
                    ["config", "--get", name],
                    check=False,
                )
                if value:
                    self._git.run(root, ["config", name, value])
            yield CoordinationRepository(root, git=self._git)

    def _canonical_fast_forward_if_safe(self) -> None:
        """Best-effort synchronize canonical main without touching dirty state."""

        try:
            self._git.clean_require(self._coordination.root)
            if self._git.branch_get(self._coordination.root) != "main":
                return
            self._git.fetch(self._coordination.root)
            local = self._git.commit_get(self._coordination.root)
            remote = self._git.commit_get(
                self._coordination.root,
                "refs/remotes/origin/main",
            )
            if local == remote:
                return
            self._git.ancestor_require(
                self._coordination.root,
                local,
                remote,
                label="project-goals best-effort synchronization",
            )
            self._git.run(
                self._coordination.root,
                ["merge", "--ff-only", remote],
            )
        except GoalLifecycleError:
            return
