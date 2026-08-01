#!/usr/bin/env python3
"""Expose central goal preparation and implementation-worktree lifecycle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from goal_lifecycle import GoalLifecycleError, GoalWorktreeWorkflow


def _parser_get() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and validate one tracked cross-repository goal.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "prepare",
        "revise",
        "contracts-authored",
        "recover-main-leak",
        "accept-main-commit-drift",
        "seal",
        "activate",
        "validate",
    ):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--goals-repository", required=True, type=Path)
        command_parser.add_argument("--common-prefix", required=True)
        if command == "prepare":
            command_parser.add_argument("--specification-input", type=Path)
            command_parser.add_argument("--repository", action="append", default=[], type=Path)
        elif command == "contracts-authored":
            command_parser.add_argument(
                "--goals-owner-input",
                action="append",
                default=[],
                metavar=("ROOT_RELATIVE_PATH", "INPUT_FILE"),
                nargs=2,
            )
        elif command == "seal":
            command_parser.add_argument("--goal-input", type=Path)
        elif command in {"recover-main-leak", "accept-main-commit-drift"}:
            command_parser.add_argument("--main-repository", required=True, type=Path)
            command_parser.add_argument("--path", action="append", required=True)
            if command == "accept-main-commit-drift":
                command_parser.add_argument("--commit", required=True)
        elif command == "validate":
            command_parser.add_argument(
                "--required-state",
                choices=("repository_prepared", "contracts_authored", "goal_ready", "active"),
                required=True,
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser_get().parse_args(argv)
    workflow = GoalWorktreeWorkflow(args.goals_repository)
    try:
        if args.command == "prepare":
            result = workflow.prepare(
                common_prefix=args.common_prefix,
                repository_root_list=args.repository,
                specification_input=args.specification_input,
            )
        elif args.command == "revise":
            result = workflow.revise(common_prefix=args.common_prefix)
        elif args.command == "contracts-authored":
            owner_input_by_path_map = {path: Path(input_path) for path, input_path in args.goals_owner_input}
            result = workflow.contracts_authored(
                common_prefix=args.common_prefix,
                goals_owner_input_by_path_map=owner_input_by_path_map,
            )
        elif args.command == "seal":
            result = workflow.seal(common_prefix=args.common_prefix, goal_input=args.goal_input)
        elif args.command == "recover-main-leak":
            result = workflow.recover_main_leak(
                common_prefix=args.common_prefix,
                main_repository=args.main_repository,
                path_list=args.path,
            )
        elif args.command == "accept-main-commit-drift":
            result = workflow.accept_main_commit_drift(
                common_prefix=args.common_prefix,
                main_repository=args.main_repository,
                commit=args.commit,
                path_list=args.path,
            )
        elif args.command == "activate":
            result = workflow.activate(common_prefix=args.common_prefix)
        else:
            result = workflow.validate(common_prefix=args.common_prefix, required_state=args.required_state)
    except GoalLifecycleError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
