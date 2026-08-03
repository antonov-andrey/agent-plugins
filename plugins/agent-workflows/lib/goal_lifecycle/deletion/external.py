"""Bound external-resource cleanup hook execution for goal deletion."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from goal_lifecycle.cleanup_manifest import (
    BOOTSTRAP_MANIFEST_NAME,
    bootstrap_manifest_load,
)
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write
from goal_lifecycle.task.model import TaskState, repository_boundary_list_get


class GoalExternalResourceCleanup:
    """Run each sealed repository cleanup hook until it proves exact absence."""

    def __init__(self, *, git: Git) -> None:
        """Initialize the goal external resource cleanup dependencies.

        Args:
            git: Git command boundary.
        """

        self._git = git

    def resume(self, *, state: TaskState, journal: dict[str, object], journal_path: Path) -> None:
        """Resume external cleanup at the durable repository index.

        Args:
            state: Exact runtime state.
            journal: Journal.
            journal_path: Exact filesystem path for journal.
        """

        start_index = int(journal["repository_index"])
        boundary_list = repository_boundary_list_get(state)
        top_level_main_root_set = {item.main_root for item in state.repository_list}
        for index, repository in enumerate(boundary_list[start_index:], start=start_index):
            main_root = Path(repository.main_root)
            if self._git.origin_url_get(main_root) != repository.origin_url:
                owner = "Task repository" if repository.main_root in top_level_main_root_set else "Task-owned submodule"
                raise GoalLifecycleError(f"{owner} origin differs before cleanup: {main_root}")
            if repository.cleanup_declaration_sha256:
                self._cleanup_hook_run_from_remote_main(
                    common_prefix=state.common_prefix,
                    main_root=main_root,
                    operation_identity=str(journal["operation_identity"]),
                )
            journal["repository_index"] = index + 1
            atomic_json_write(journal_path, journal)

    def _cleanup_hook_run_from_remote_main(
        self,
        *,
        common_prefix: str,
        main_root: Path,
        operation_identity: str,
    ) -> None:
        """Run the current cleanup owner from one temporary clean remote-main worktree.

        Args:
            common_prefix: Exact task common prefix.
            main_root: Main repository root.
            operation_identity: Exact operation identity.
        """

        self._git.fetch(main_root)
        with tempfile.TemporaryDirectory(prefix="goal-delete-") as temporary_directory:
            cleanup_root = Path(temporary_directory) / "checkout"
            self._git.run(
                main_root,
                [
                    "worktree",
                    "add",
                    "--detach",
                    str(cleanup_root),
                    "refs/remotes/origin/main",
                ],
            )
            try:
                manifest = bootstrap_manifest_load(cleanup_root / BOOTSTRAP_MANIFEST_NAME)
                if manifest.cleanup is None:
                    raise GoalLifecycleError(f"Current cleanup owner has no deletion hook: {main_root}")
                _cleanup_hook_run(
                    command=manifest.cleanup.command_get(common_prefix=common_prefix),
                    common_prefix=common_prefix,
                    repository_root=cleanup_root,
                    operation_identity=operation_identity,
                )
            finally:
                self._git.run(
                    main_root,
                    ["worktree", "remove", "--force", "--force", str(cleanup_root)],
                    check=False,
                )
                self._git.run(main_root, ["worktree", "prune"], check=False)


def _cleanup_hook_run(
    *,
    command: list[str],
    common_prefix: str,
    repository_root: Path,
    operation_identity: str,
) -> None:
    """Run one sealed cleanup command from clean synchronized main and require exact absence.

    Args:
        command: Command.
        common_prefix: Exact task common prefix.
        repository_root: Repository root.
        operation_identity: Exact operation identity.
    """

    request = {
        "schema_version": 1,
        "common_prefix": common_prefix,
        "operation_identity": operation_identity,
    }
    environment = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        command,
        cwd=repository_root,
        env=environment,
        input=(json.dumps(request, separators=(",", ":"), sort_keys=True) + "\n").encode(),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise GoalLifecycleError(f"External cleanup hook failed for {repository_root}: {diagnostic}")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GoalLifecycleError(f"External cleanup hook returned invalid JSON: {repository_root}") from error
    if response != {**request, "external_resources_absent": True}:
        raise GoalLifecycleError(f"External cleanup hook did not prove exact absence: {repository_root}")
