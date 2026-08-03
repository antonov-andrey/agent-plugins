"""Closed durable journal contract for goal deletion."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.bootstrap_exception import CoordinationBootstrapException
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.task.model import TaskState

DELETION_PHASE_SET = frozenset(
    {
        "external-resources",
        "worktrees",
        "remote-refs",
        "local-refs",
        "bootstrap-carriers",
        "coordination-bootstrap-retire",
        "registry-update",
        "complete",
    }
)


def deletion_journal_validate(journal: dict[str, object], *, state: TaskState) -> None:
    """Require one deletion journal to match its exact durable task snapshot.

    Args:
        journal: Journal.
        state: Exact runtime state.
    """

    if (
        set(journal)
        != {
            "schema_version",
            "common_prefix",
            "operation_identity",
            "phase",
            "coordination_bootstrap_exception",
            "project_list",
            "repository_index",
            "submodule_list",
            "task_resource_state",
            "task_state",
        }
        or journal.get("schema_version") != 3
        or journal.get("common_prefix") != state.common_prefix
        or journal.get("task_resource_state") not in {"deleted", "retained"}
    ):
        raise GoalLifecycleError("Goal deletion journal has another shape or task identity")
    if journal.get("phase") not in DELETION_PHASE_SET:
        raise GoalLifecycleError("Goal deletion journal phase is unsupported")
    operation_identity = journal.get("operation_identity")
    repository_index = journal.get("repository_index")
    project_list = journal.get("project_list")
    submodule_list = journal.get("submodule_list")
    boundary_count = len(state.repository_list) + sum(
        len(item.task_owned_submodule_list) for item in state.repository_list
    )
    if (
        not isinstance(operation_identity, str)
        or len(operation_identity) != 32
        or not isinstance(repository_index, int)
        or isinstance(repository_index, bool)
        or not 0 <= repository_index <= boundary_count
    ):
        raise GoalLifecycleError("Goal deletion journal identity or position is malformed")
    expected_main_root_set = {str(Path(item.main_root).resolve(strict=True)) for item in state.repository_list}
    if (
        not isinstance(project_list, list)
        or len(project_list) != len(state.repository_list)
        or any(
            not isinstance(item, dict)
            or set(item)
            != {
                "main_common_directory",
                "main_root",
                "origin_url",
                "project_path",
                "task_root",
            }
            for item in project_list
        )
        or {str(item["main_root"]) for item in project_list} != expected_main_root_set
    ):
        raise GoalLifecycleError("Goal deletion journal project snapshot is malformed")
    expected_submodule_root_set = {
        item.repository.main_root
        for repository in state.repository_list
        for item in repository.task_owned_submodule_list
    }
    if (
        not isinstance(submodule_list, list)
        or len(submodule_list) != len(expected_submodule_root_set)
        or any(
            not isinstance(item, dict)
            or set(item)
            != {
                "main_common_directory",
                "main_root",
                "origin_url",
                "parent_main_root",
                "path",
                "task_common_directory",
                "task_root",
            }
            for item in submodule_list
        )
        or {str(item["main_root"]) for item in submodule_list} != expected_submodule_root_set
    ):
        raise GoalLifecycleError("Goal deletion journal task-owned submodule snapshot is malformed")
    if TaskState.from_payload(journal.get("task_state")) != state:
        raise GoalLifecycleError("Goal deletion journal task-state snapshot differs")
    bootstrap_exception = deletion_bootstrap_exception_get(journal)
    if bootstrap_exception is not None and bootstrap_exception.common_prefix != state.common_prefix:
        raise GoalLifecycleError("Goal deletion bootstrap exception belongs to another task")


def deletion_bootstrap_exception_get(
    journal: dict[str, object],
) -> CoordinationBootstrapException | None:
    """Return the optional bootstrap exception bound into one deletion journal.

    Args:
        journal: Journal.

    Returns:
        The optional bootstrap exception bound into one deletion journal.
    """

    payload = journal.get("coordination_bootstrap_exception")
    return None if payload is None else CoordinationBootstrapException.from_payload(payload)
