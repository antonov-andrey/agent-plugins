"""Resolve task-owned recursive submodule commits from one top-level commit tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.task.model import RepositoryState, TaskOwnedSubmoduleState


@dataclass(frozen=True, slots=True)
class TaskOwnedSubmoduleTarget:
    """One task-owned submodule and its commit selected by a parent checkpoint."""

    path: str
    state: TaskOwnedSubmoduleState
    git_commit_final: str


def task_owned_submodule_target_list_get(
    repository: RepositoryState,
    *,
    top_level_commit: str,
    git: Git,
) -> tuple[TaskOwnedSubmoduleTarget, ...]:
    """Resolve every owned descendant through exact committed parent gitlinks.

    Args:
        repository: Exact Git repository root.
        top_level_commit: Top level commit.
        git: Git command boundary.

    Returns:
        Every owned descendant resolved through exact committed parent gitlinks.
    """

    owned_by_path_map = {item.path: item for item in repository.task_owned_submodule_list}
    resolved_commit_by_path_map: dict[str, str] = {}
    result: list[TaskOwnedSubmoduleTarget] = []
    top_main_root = Path(repository.main_root)
    for path_text, item in sorted(
        owned_by_path_map.items(),
        key=lambda pair: (len(PurePosixPath(pair[0]).parts), pair[0]),
    ):
        parent_path = max(
            (
                candidate
                for candidate in owned_by_path_map
                if candidate != path_text and PurePosixPath(candidate) in PurePosixPath(path_text).parents
            ),
            key=lambda candidate: len(PurePosixPath(candidate).parts),
            default="",
        )
        parent_repository_root = top_main_root / parent_path if parent_path else top_main_root
        parent_commit = resolved_commit_by_path_map[parent_path] if parent_path else top_level_commit
        relative_path = (
            PurePosixPath(path_text).relative_to(PurePosixPath(parent_path)).as_posix() if parent_path else path_text
        )
        gitlink_commit = gitlink_commit_get(
            git,
            parent_repository_root,
            commit=parent_commit,
            path=relative_path,
        )
        resolved_commit_by_path_map[path_text] = gitlink_commit
        result.append(TaskOwnedSubmoduleTarget(path=path_text, state=item, git_commit_final=gitlink_commit))
    return tuple(result)


def gitlink_commit_get(git: Git, repository_root: Path, *, commit: str, path: str) -> str:
    """Return one exact stage-zero gitlink from a committed tree.

    Args:
        git: Git command boundary.
        repository_root: Repository root.
        commit: Commit.
        path: Exact filesystem path.

    Returns:
        One exact stage-zero gitlink from a committed tree.
    """

    payload = git.run(repository_root, ["ls-tree", "-z", commit, "--", path]).stdout
    entry_list = [item for item in payload.split(b"\0") if item]
    if len(entry_list) != 1:
        raise GoalLifecycleError(f"Committed tree has no single submodule gitlink: {repository_root}:{path}")
    metadata, raw_path = entry_list[0].split(b"\t", maxsplit=1)
    mode, object_type, object_id = metadata.split(b" ")
    try:
        observed_path = raw_path.decode("utf-8")
        object_id_text = object_id.decode("ascii")
    except UnicodeDecodeError as error:
        raise GoalLifecycleError("Goal lifecycle requires UTF-8 submodule paths") from error
    if mode != b"160000" or object_type != b"commit" or observed_path != path:
        raise GoalLifecycleError(f"Committed path is not a submodule gitlink: {repository_root}:{path}")
    return object_id_text
