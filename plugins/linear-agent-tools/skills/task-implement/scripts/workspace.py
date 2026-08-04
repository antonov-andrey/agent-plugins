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

from task_workspace import (
    RepositoryRequest,
    TaskWorkspaceError,
    TaskWorkspaceTransaction,
    WorkspaceConfig,
    WorkspaceRequest,
    recursive_submodule_snapshot_get,
)


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed workspace command parser.

    Returns:
        The argument parser.
    """

    parser = argparse.ArgumentParser(
        description="Prepare or validate deterministic Linear issue branches and worktrees."
    )
    parser.add_argument("command", choices=("prepare", "validate"))
    parser.add_argument("--issue-identifier", required=True)
    parser.add_argument("--repositories-input", required=True, type=Path)
    return parser


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
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TaskWorkspaceError("Repository request is malformed") from error
    if not isinstance(payload, list):
        raise TaskWorkspaceError("Repository request root must be a list")
    return WorkspaceRequest(
        issue_identifier=issue_identifier,
        repository_list=tuple(RepositoryRequest.from_payload(item) for item in payload),
    )


def main(argv: list[str] | None = None) -> int:
    """Run one issue-workspace transaction.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for contract rejection.
    """

    args = _parser_get().parse_args(argv)
    try:
        request = _request_get(args.issue_identifier, args.repositories_input)
        transaction = TaskWorkspaceTransaction(WorkspaceConfig.from_environment())
        state_list = transaction.prepare(request) if args.command == "prepare" else transaction.validate(request)
    except TaskWorkspaceError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema_version": 1,
                "branch_name": request.branch_name,
                "repository_list": [
                    {
                        "baseline_commit": item.baseline_commit,
                        "origin_identity": item.origin_identity,
                        "recursive_submodule_commit_by_path": [
                            list(pair) for pair in recursive_submodule_snapshot_get(Path(item.task_root))
                        ],
                        "task_root": item.task_root,
                    }
                    for item in state_list
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
