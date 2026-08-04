#!/usr/bin/env python3
"""Publish or validate one complete goal/spec source pair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from goal_authoring import GoalAuthoringError, GoalAuthoringWorkflow


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed goal-source command parser.

    Returns:
        The argument parser.
    """

    parser = argparse.ArgumentParser(
        description="Atomically publish or validate one freely revisable project-goals source pair."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("write", "validate"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--goals-repository", required=True, type=Path)
        command_parser.add_argument("--common-prefix", required=True)
        if command == "write":
            command_parser.add_argument("--goal-input", required=True, type=Path)
            command_parser.add_argument("--specification-input", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one authoring command.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for a rejected contract.
    """

    args = _parser_get().parse_args(argv)
    try:
        workflow = GoalAuthoringWorkflow(args.goals_repository)
        if args.command == "write":
            result = workflow.write(
                common_prefix=args.common_prefix,
                goal_input=args.goal_input,
                specification_input=args.specification_input,
            )
        else:
            result = workflow.validate(common_prefix=args.common_prefix)
    except GoalAuthoringError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result.payload(), ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
