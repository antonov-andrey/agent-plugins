#!/usr/bin/env python3
"""Merge or accept one exact goal checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from goal_lifecycle import GoalLifecycleError, GoalMergeWorkflow


def main(argv: list[str] | None = None) -> int:
    """Run the command-line entrypoint.

    Args:
        argv: Argv.

    Returns:
        Zero on success or 2 when the lifecycle contract rejects the request.
    """

    parser = argparse.ArgumentParser(description="Merge or accept one published checkpoint.")
    parser.add_argument("operation", choices=("merge", "accept"))
    parser.add_argument("--goals-repository", required=True, type=Path)
    parser.add_argument("--common-prefix", required=True)
    parser.add_argument("--checkpoint-id", required=True)
    args = parser.parse_args(argv)
    workflow = GoalMergeWorkflow(args.goals_repository)
    try:
        result: object = (
            workflow.merge(common_prefix=args.common_prefix, checkpoint_id=args.checkpoint_id)
            if args.operation == "merge"
            else {
                "coordination_commit": workflow.accept(
                    common_prefix=args.common_prefix,
                    checkpoint_id=args.checkpoint_id,
                )
            }
        )
    except GoalLifecycleError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
