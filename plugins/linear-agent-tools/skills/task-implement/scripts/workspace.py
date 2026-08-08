#!/usr/bin/env python3
"""Prepare or validate local Git worktrees owned by one Linear issue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from json_contract import JsonContractError, json_load_strict
from task_workspace.model import (
    RepositoryRequest,
    TaskWorkspaceError,
    WorkspaceConfig,
    WorkspaceRequest,
)
from task_workspace.repository import WorkspaceRepository
from task_workspace.submodule import WorkspaceSubmoduleReader
from task_workspace.transaction import TaskWorkspaceTransaction


def _args_parse(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the closed workspace command arguments.

    Returns:
        Parsed arguments.
    """

    parser = argparse.ArgumentParser(
        description="Prepare or validate deterministic Linear issue branches and worktrees."
    )
    parser.add_argument("command", choices=("prepare", "validate"))
    parser.add_argument("--issue-identifier", required=True)
    parser.add_argument("--repositories-input", required=True, type=Path)
    return parser.parse_args(argv)


def _request_get(issue_identifier: str, path: Path) -> WorkspaceRequest:
    """Load one strict repository request list.

    Args:
        issue_identifier: Exact Linear issue identifier.
        path: JSON request file.

    Returns:
        Typed workspace request.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise TaskWorkspaceError("Repository request must be one ordinary file")
    try:
        payload = json_load_strict(path.read_bytes())
    except (OSError, JsonContractError) as error:
        raise TaskWorkspaceError("Repository request is malformed") from error
    if not isinstance(payload, list):
        raise TaskWorkspaceError("Repository request root must be a list")
    return WorkspaceRequest(
        issue_identifier=issue_identifier,
        repository_list=[RepositoryRequest.from_payload(item) for item in payload],
    )


def main(argv: list[str] | None = None) -> int:
    """Run one issue-workspace transaction.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for contract rejection.
    """

    args = _args_parse(argv)
    try:
        request = _request_get(args.issue_identifier, args.repositories_input)
        config = WorkspaceConfig.from_environment()
        transaction = TaskWorkspaceTransaction(config)
        state_list = transaction.prepare(request) if args.command == "prepare" else transaction.validate(request)
    except TaskWorkspaceError as error:
        print(str(error), file=sys.stderr)
        return 2
    repository_result_list: list[dict[str, object]] = []
    for index, state in enumerate(state_list):
        repository = WorkspaceRepository.from_config(config, request.repository_list[index])
        task_root = repository.main_root / ".worktree" / request.basename
        repository_result_list.append(
            {
                "baseline_commit": state.baseline_commit,
                "origin_identity": repository.origin_identity,
                "recursive_submodule_commit_by_path_map": {
                    submodule.relative_path: submodule.commit
                    for submodule in WorkspaceSubmoduleReader(task_root).read()
                },
                "task_root": str(task_root),
            }
        )
    print(
        json.dumps(
            {
                "schema_version": 1,
                "branch_name": request.branch_name,
                "repository_list": repository_result_list,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
