#!/usr/bin/env python3
"""Delete one exact goal through its resumable transaction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from goal_lifecycle import GoalDeletionWorkflow, GoalLifecycleError


def main(argv: list[str] | None = None) -> int:
    """Run the command-line entrypoint.

    Args:
        argv: Argv.

    Returns:
        Zero on success or 2 when the lifecycle contract rejects the request.
    """

    parser = argparse.ArgumentParser(description="Delete one exact accepted goal lifecycle.")
    parser.add_argument("--goals-repository", required=True, type=Path)
    parser.add_argument("--common-prefix", required=True)
    parser.add_argument("--unfinished-goal-absent", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = GoalDeletionWorkflow(args.goals_repository).delete(
            common_prefix=args.common_prefix,
            unfinished_goal_absent=args.unfinished_goal_absent,
        )
    except GoalLifecycleError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
