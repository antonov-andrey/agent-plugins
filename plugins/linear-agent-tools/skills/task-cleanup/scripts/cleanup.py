#!/usr/bin/env python3
"""Reconcile exact resources owned by one Linear issue or Project cleanup task."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_host.model import GitHubContractError
from task_cleanup import CleanupRequest, TaskCleanupError, TaskCleanupReconciler
from task_workspace import TaskWorkspaceError, WorkspaceConfig


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed cleanup command parser.

    Returns:
        The argument parser.
    """

    parser = argparse.ArgumentParser(description="Idempotently clean exact state owned by one Linear task.")
    parser.add_argument("--request-input", required=True, type=Path)
    return parser


def _request_load(path: Path) -> CleanupRequest:
    """Load one strict ordinary cleanup request JSON file.

    Args:
        path: Exact request path.

    Returns:
        Typed cleanup request.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise TaskCleanupError("Cleanup request must be one ordinary file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TaskCleanupError("Cleanup request is malformed") from error
    return CleanupRequest.from_payload(payload)


def main(argv: list[str] | None = None) -> int:
    """Run one exact cleanup reconciliation.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for an unsafe or failed request.
    """

    args = _parser_get().parse_args(argv)
    try:
        result = TaskCleanupReconciler(WorkspaceConfig.from_environment()).cleanup(_request_load(args.request_input))
    except (GitHubContractError, TaskCleanupError, TaskWorkspaceError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result.payload(), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
