#!/usr/bin/env python3
"""Hold one host-local Linear issue attempt lock for this process lifetime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[2]
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from task_workspace import IssueAttemptLock, TaskWorkspaceError, WorkspaceConfig


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed attempt-guard parser."""

    parser = argparse.ArgumentParser(
        description="Hold the process-lifetime host-local lock for one Linear issue attempt."
    )
    parser.add_argument("hold", choices=("hold",))
    parser.add_argument("--issue-identifier", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Acquire, announce and hold the lock until this process is terminated."""

    args = _parser_get().parse_args(argv)
    stopped = threading.Event()

    def stop(_signal_number: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        with IssueAttemptLock(WorkspaceConfig.from_environment(), args.issue_identifier):
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "issue_identifier": args.issue_identifier,
                        "status": "held",
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                flush=True,
            )
            stopped.wait()
    except TaskWorkspaceError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
