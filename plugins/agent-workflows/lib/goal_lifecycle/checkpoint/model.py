"""Closed append-only cross-repository checkpoint document model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.identity import checkpoint_project_path_validate, commit_validate

_CHECKPOINT_ID_PATTERN = re.compile(r"checkpoint-[0-9]{4}")


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """Bind one implementation repository to its exact closing commit."""

    project_path: str
    git_commit_final: str

    @classmethod
    def from_payload(cls, payload: object) -> "ProjectSnapshot":
        """Build one snapshot from its exact closed payload."""

        if not isinstance(payload, dict) or set(payload) != {
            "project_path",
            "git_commit_final",
        }:
            raise GoalLifecycleError("Checkpoint project entry has another shape")
        return cls(
            project_path=checkpoint_project_path_validate(payload["project_path"]),
            git_commit_final=commit_validate(payload["git_commit_final"], label="git_commit_final"),
        )


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One immutable full cross-repository commit snapshot."""

    checkpoint_id: str
    project_list: tuple[ProjectSnapshot, ...]

    @classmethod
    def from_payload(cls, payload: object) -> "Checkpoint":
        """Build one checkpoint from its exact closed payload."""

        if not isinstance(payload, dict) or set(payload) != {
            "checkpoint_id",
            "project_list",
        }:
            raise GoalLifecycleError("Checkpoint entry has another shape")
        checkpoint_id = payload["checkpoint_id"]
        if not isinstance(checkpoint_id, str) or _CHECKPOINT_ID_PATTERN.fullmatch(checkpoint_id) is None:
            raise GoalLifecycleError("checkpoint_id must use checkpoint-NNNN")
        project_payload_list = payload["project_list"]
        if not isinstance(project_payload_list, list) or not project_payload_list:
            raise GoalLifecycleError("Checkpoint must contain a non-empty full project_list")
        project_list = tuple(ProjectSnapshot.from_payload(item) for item in project_payload_list)
        project_path_list = [item.project_path for item in project_list]
        if project_path_list != sorted(project_path_list) or len(project_path_list) != len(set(project_path_list)):
            raise GoalLifecycleError("Checkpoint project_list must be unique and sorted by project_path")
        return cls(checkpoint_id=checkpoint_id, project_list=project_list)


@dataclass(frozen=True, slots=True)
class CheckpointDocument:
    """Canonical append-only checkpoint document."""

    accepted_checkpoint_id: str
    checkpoint_list: tuple[Checkpoint, ...]
    schema_version: int = 1

    @classmethod
    def empty(cls) -> "CheckpointDocument":
        """Build the only valid initial checkpoint document."""

        return cls(accepted_checkpoint_id="", checkpoint_list=())

    @classmethod
    def from_payload(cls, payload: object) -> "CheckpointDocument":
        """Build one document from its exact closed payload."""

        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "accepted_checkpoint_id",
            "checkpoint_list",
        }:
            raise GoalLifecycleError("checkpoint.yaml has another shape")
        if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
            raise GoalLifecycleError("checkpoint.yaml schema_version must equal integer 1")
        accepted = payload["accepted_checkpoint_id"]
        if not isinstance(accepted, str) or (accepted and _CHECKPOINT_ID_PATTERN.fullmatch(accepted) is None):
            raise GoalLifecycleError("accepted_checkpoint_id must be empty or one checkpoint id")
        raw_checkpoint_list = payload["checkpoint_list"]
        if not isinstance(raw_checkpoint_list, list):
            raise GoalLifecycleError("checkpoint_list must be a list")
        checkpoint_list = tuple(Checkpoint.from_payload(item) for item in raw_checkpoint_list)
        expected_id_list = [f"checkpoint-{index:04d}" for index in range(1, len(checkpoint_list) + 1)]
        if [item.checkpoint_id for item in checkpoint_list] != expected_id_list:
            raise GoalLifecycleError("checkpoint identifiers must be contiguous and monotonic")
        if checkpoint_list:
            first_project_path_tuple = tuple(item.project_path for item in checkpoint_list[0].project_list)
            if any(
                tuple(item.project_path for item in checkpoint.project_list) != first_project_path_tuple
                for checkpoint in checkpoint_list[1:]
            ):
                raise GoalLifecycleError("Every checkpoint must contain the same complete participant set")
        if accepted and accepted not in expected_id_list:
            raise GoalLifecycleError("accepted checkpoint is absent from checkpoint_list")
        return cls(accepted_checkpoint_id=accepted, checkpoint_list=checkpoint_list)

    def payload_get(self) -> dict[str, Any]:
        """Return the canonical YAML-ready document payload."""

        return {
            "schema_version": 1,
            "accepted_checkpoint_id": self.accepted_checkpoint_id,
            "checkpoint_list": [
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "project_list": [asdict(project) for project in checkpoint.project_list],
                }
                for checkpoint in self.checkpoint_list
            ],
        }

    def selection_validate(self, checkpoint: Checkpoint) -> None:
        """Require one selected checkpoint to follow the accepted pointer."""

        if self.accepted_checkpoint_id == checkpoint.checkpoint_id:
            raise GoalLifecycleError("Checkpoint is already accepted")
        accepted_index = (
            next(
                index
                for index, item in enumerate(self.checkpoint_list)
                if item.checkpoint_id == self.accepted_checkpoint_id
            )
            if self.accepted_checkpoint_id
            else -1
        )
        if self.checkpoint_list.index(checkpoint) <= accepted_index:
            raise GoalLifecycleError("Selected checkpoint precedes the accepted pointer")
