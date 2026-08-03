#!/usr/bin/env python3
"""Publish one complete goal checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from goal_lifecycle import GoalCheckpointPublisher, GoalLifecycleError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish one full cross-repository checkpoint.")
    parser.add_argument("--goals-repository", required=True, type=Path)
    parser.add_argument("--common-prefix", required=True)
    parser.add_argument("--project-root", action="append", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        checkpoint_id, commit = GoalCheckpointPublisher(args.goals_repository).publish(
            common_prefix=args.common_prefix,
            project_root_list=args.project_root,
        )
    except GoalLifecycleError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"checkpoint_id": checkpoint_id, "coordination_commit": commit},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
