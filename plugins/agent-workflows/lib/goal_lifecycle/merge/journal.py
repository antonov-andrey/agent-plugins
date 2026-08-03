"""Closed durable journal semantics for one goal merge."""

from __future__ import annotations

from dataclasses import asdict

from goal_lifecycle.checkpoint.model import Checkpoint
from goal_lifecycle.error import GoalLifecycleError


def merge_journal_validate(
    journal: dict[str, object],
    *,
    common_prefix: str,
    checkpoint: Checkpoint,
    submodule_snapshot_list: list[dict[str, object]] | None,
) -> None:
    """Require one durable journal to match the exact selected checkpoint.

    Args:
        journal: Journal.
        common_prefix: Exact task common prefix.
        checkpoint: Checkpoint.
        submodule_snapshot_list: Ordered submodule snapshot values.
    """

    if (
        set(journal)
        != {
            "schema_version",
            "common_prefix",
            "checkpoint_id",
            "phase",
            "project_list",
            "submodule_list",
        }
        or journal.get("schema_version") != 2
        or journal.get("common_prefix") != common_prefix
    ):
        raise GoalLifecycleError("Goal merge journal has another identity or shape")
    if journal.get("checkpoint_id") != checkpoint.checkpoint_id:
        raise GoalLifecycleError("Another checkpoint already owns the resumable merge journal")
    if journal.get("phase") not in {"merging", "awaiting-acceptance", "accepted"}:
        raise GoalLifecycleError("Goal merge journal phase is unsupported")
    expected = [
        {**asdict(project), "merged": item.get("merged")}
        for project, item in zip(checkpoint.project_list, journal.get("project_list", []), strict=False)
        if isinstance(item, dict)
    ]
    if len(expected) != len(checkpoint.project_list) or any(item["merged"] not in {True, False} for item in expected):
        raise GoalLifecycleError("Goal merge journal project snapshot is malformed")
    for expected_item, actual_item in zip(expected, journal["project_list"], strict=True):
        if expected_item != actual_item:
            raise GoalLifecycleError("Goal merge journal differs from the selected checkpoint")
    submodule_list = journal.get("submodule_list")
    if not isinstance(submodule_list, list) or any(
        not isinstance(item, dict)
        or set(item)
        != {
            "git_commit_final",
            "main_root",
            "merged",
            "origin_url",
            "parent_project_path",
            "path",
            "task_root",
        }
        or item["merged"] not in {True, False}
        for item in submodule_list
    ):
        raise GoalLifecycleError("Goal merge journal task-owned submodule snapshot is malformed")
    if submodule_snapshot_list is not None:
        expected_submodule_list = [
            {**item, "merged": actual["merged"]}
            for item, actual in zip(submodule_snapshot_list, submodule_list, strict=False)
        ]
        if len(expected_submodule_list) != len(submodule_snapshot_list) or expected_submodule_list != submodule_list:
            raise GoalLifecycleError("Goal merge journal differs from selected task-owned submodule targets")


def merge_journal_supersede_get(
    journal: dict[str, object],
    *,
    common_prefix: str,
    checkpoint: Checkpoint,
    previous_checkpoint: Checkpoint,
    submodule_snapshot_list: list[dict[str, object]],
) -> tuple[dict[str, object], dict[str, str], list[dict[str, object]]]:
    """Return one fix-forward journal and the exact prior commit map it supersedes.

    Args:
        journal: Journal.
        common_prefix: Exact task common prefix.
        checkpoint: Checkpoint.
        previous_checkpoint: Previous checkpoint.
        submodule_snapshot_list: Ordered submodule snapshot values.

    Returns:
        One fix-forward journal and the exact prior commit map it supersedes.
    """

    merge_journal_validate(
        journal,
        common_prefix=common_prefix,
        checkpoint=previous_checkpoint,
        submodule_snapshot_list=None,
    )
    if journal["phase"] not in {"merging", "awaiting-acceptance"}:
        raise GoalLifecycleError("Existing merge journal cannot be superseded after acceptance")
    if previous_checkpoint.checkpoint_id == checkpoint.checkpoint_id:
        raise GoalLifecycleError("Malformed merge journal cannot supersede itself")
    previous_by_path_map = {
        project.project_path: project.git_commit_final for project in previous_checkpoint.project_list
    }
    if set(previous_by_path_map) != {item.project_path for item in checkpoint.project_list}:
        raise GoalLifecycleError("Fix-forward checkpoint changes the merge participant set")
    previous_submodule_list = [dict(item) for item in journal["submodule_list"]]
    return (
        {
            "schema_version": 2,
            "common_prefix": common_prefix,
            "checkpoint_id": checkpoint.checkpoint_id,
            "phase": "merging",
            "project_list": [{**asdict(project), "merged": False} for project in checkpoint.project_list],
            "submodule_list": [{**item, "merged": False} for item in submodule_snapshot_list],
        },
        previous_by_path_map,
        previous_submodule_list,
    )
