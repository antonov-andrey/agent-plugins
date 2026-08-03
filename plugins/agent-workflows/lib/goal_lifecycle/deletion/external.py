"""Bound external-resource cleanup hook execution for goal deletion."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from goal_lifecycle.cleanup_manifest import (
    BOOTSTRAP_MANIFEST_NAME,
    bootstrap_manifest_load,
    cleanup_binding_receipt_validate,
)
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write
from goal_lifecycle.task.model import TaskState, repository_boundary_list_get


class GoalExternalResourceCleanup:
    """Run each sealed repository cleanup hook until it proves exact absence."""

    def __init__(self, *, git: Git) -> None:
        self._git = git

    def resume(self, *, state: TaskState, journal: dict[str, object], journal_path: Path) -> None:
        """Resume external cleanup at the durable repository index."""

        start_index = int(journal["repository_index"])
        boundary_list = repository_boundary_list_get(state)
        top_level_main_root_set = {item.main_root for item in state.repository_list}
        for index, repository in enumerate(boundary_list[start_index:], start=start_index):
            main_root = Path(repository.main_root)
            if repository.main_root in top_level_main_root_set:
                self._git.synchronized_main_require(main_root)
            else:
                self._git.clean_require(main_root)
                if self._git.origin_url_get(main_root) != repository.origin_url:
                    raise GoalLifecycleError(f"Task-owned submodule origin changed before cleanup: {main_root}")
            manifest = bootstrap_manifest_load(main_root / BOOTSTRAP_MANIFEST_NAME)
            if manifest.sha256 != repository.manifest_sha256:
                raise GoalLifecycleError(f"Main cleanup manifest differs from sealed binding: {main_root}")
            if manifest.cleanup is not None:
                cleanup_binding_receipt_validate(
                    main_root,
                    common_prefix=state.common_prefix,
                    provider_state_generation=state.cleanup_binding_generation,
                    sealed_specification_sha256=state.sealed_spec_sha256,
                    git=self._git,
                )
                _cleanup_hook_run(
                    command=manifest.cleanup.command_get(common_prefix=state.common_prefix),
                    common_prefix=state.common_prefix,
                    repository_root=main_root,
                    operation_identity=str(journal["operation_identity"]),
                )
            journal["repository_index"] = index + 1
            atomic_json_write(journal_path, journal)


def _cleanup_hook_run(
    *,
    command: list[str],
    common_prefix: str,
    repository_root: Path,
    operation_identity: str,
) -> None:
    """Run one sealed cleanup command from clean synchronized main and require exact absence."""

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
