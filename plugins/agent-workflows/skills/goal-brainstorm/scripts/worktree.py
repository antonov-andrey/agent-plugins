#!/usr/bin/env python3
"""Expose goal-brainstorm task-worktree lifecycle through one thin CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib" / "goal-brainstorm"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from worktree import (
    WorktreeError,
    worktree_activate,
    worktree_contracts_authored,
    worktree_main_commit_drift_accept,
    worktree_main_leak_recover,
    worktree_prepare,
    worktree_seal,
    worktree_validate,
)


def _args_parse(argv_list: list[str]) -> argparse.Namespace:
    """Parse goal-brainstorm worktree command arguments.

    Args:
        argv_list: Raw command arguments.

    Returns:
        Parsed command namespace.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Prepare, attest main drift, record authored contracts, seal, activate, or validate isolated "
            "goal-brainstorm task worktrees."
        ),
    )
    subparser_by_name = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparser_by_name.add_parser("prepare", help="Create or resume one task worktree set.")
    prepare_parser.add_argument(
        "--coordinating-repository",
        required=True,
        type=Path,
        help="Main-worktree root that owns the physical specification.",
    )
    prepare_parser.add_argument(
        "--specification",
        required=True,
        type=Path,
        help="Specification path relative to the coordinating repository.",
    )
    prepare_parser.add_argument(
        "--repository",
        action="append",
        default=[],
        type=Path,
        help="Additional affected main-worktree root. Repeat for each repository.",
    )
    prepare_parser.add_argument(
        "--participating-submodule",
        action="append",
        default=[],
        metavar=("MAIN_ROOT", "PATH"),
        nargs=2,
        type=Path,
        help=(
            "Explicit task-owned recursive submodule as a participating main root and its root-relative path. "
            "Repeat for each submodule."
        ),
    )

    contracts_authored_parser = subparser_by_name.add_parser(
        "contracts-authored",
        help="Record validated completion of stable-owner contract authoring.",
    )
    contracts_authored_parser.add_argument(
        "--coordinating-repository",
        required=True,
        type=Path,
        help="Main-worktree root that owns the physical specification.",
    )
    contracts_authored_parser.add_argument(
        "--specification",
        required=True,
        type=Path,
        help="Specification path relative to the coordinating repository.",
    )

    recover_main_leak_parser = subparser_by_name.add_parser(
        "recover-main-leak",
        help="Recover exact task patches the calling agent confirms it leaked into main.",
    )
    recover_main_leak_parser.add_argument(
        "--coordinating-repository",
        required=True,
        type=Path,
        help="Main-worktree root that owns the physical specification.",
    )
    recover_main_leak_parser.add_argument(
        "--specification",
        required=True,
        type=Path,
        help="Specification path relative to the coordinating repository.",
    )
    recover_main_leak_parser.add_argument(
        "--main-repository",
        required=True,
        type=Path,
        help="Participating main-worktree root that received the leaked task patch.",
    )
    recover_main_leak_parser.add_argument(
        "--path",
        action="append",
        required=True,
        type=Path,
        help="Root-relative path the caller confirms it leaked. Repeat for each path.",
    )

    accept_main_commit_drift_parser = subparser_by_name.add_parser(
        "accept-main-commit-drift",
        help="Accept exact overlapping committed main drift after caller attestation.",
    )
    accept_main_commit_drift_parser.add_argument(
        "--coordinating-repository",
        required=True,
        type=Path,
        help="Main-worktree root that owns the physical specification.",
    )
    accept_main_commit_drift_parser.add_argument(
        "--specification",
        required=True,
        type=Path,
        help="Specification path relative to the coordinating repository.",
    )
    accept_main_commit_drift_parser.add_argument(
        "--main-repository",
        required=True,
        type=Path,
        help="Participating top-level or task-owned-submodule main owner root.",
    )
    accept_main_commit_drift_parser.add_argument(
        "--commit",
        required=True,
        help="Exact full current main commit identity accepted by the caller.",
    )
    accept_main_commit_drift_parser.add_argument(
        "--path",
        action="append",
        required=True,
        type=Path,
        help="Exact owner-relative overlapping committed path accepted by the caller. Repeat for each path.",
    )

    activate_parser = subparser_by_name.add_parser(
        "activate",
        help="Record activation after the caller creates the persistent goal.",
    )
    activate_parser.add_argument(
        "--coordinating-repository",
        required=True,
        type=Path,
        help="Main-worktree root that owns the physical task pair.",
    )
    activate_parser.add_argument(
        "--specification",
        required=True,
        type=Path,
        help="Specification path relative to the coordinating repository.",
    )

    validate_parser = subparser_by_name.add_parser("validate", help="Validate and repair one recorded task set.")
    validate_parser.add_argument(
        "--coordinating-repository",
        required=True,
        type=Path,
        help="Main-worktree root that owns the physical specification.",
    )
    validate_parser.add_argument(
        "--specification",
        required=True,
        type=Path,
        help="Specification path relative to the coordinating repository.",
    )
    validate_parser.add_argument(
        "--required-state",
        choices=(
            "designing",
            "design_approved",
            "worktree_created",
            "repository_prepared",
            "contracts_authored",
            "goal_ready",
            "active",
        ),
        required=True,
        help="Minimum lifecycle state required for success.",
    )

    seal_parser = subparser_by_name.add_parser("seal", help="Seal a validated specification and goal pair.")
    seal_parser.add_argument(
        "--coordinating-repository",
        required=True,
        type=Path,
        help="Main-worktree root that owns the physical task pair.",
    )
    seal_parser.add_argument(
        "--specification",
        required=True,
        type=Path,
        help="Specification path relative to the coordinating repository.",
    )
    seal_parser.add_argument(
        "--goal",
        required=True,
        type=Path,
        help="Goal path relative to the coordinating repository.",
    )
    return parser.parse_args(argv_list)


def main(argv_list: list[str]) -> int:
    """Run the goal-brainstorm worktree CLI.

    Args:
        argv_list: Raw command arguments.

    Returns:
        Process exit code.
    """

    args = _args_parse(argv_list)
    try:
        if args.command == "activate":
            result_json = worktree_activate(
                args.coordinating_repository,
                args.specification,
            )
        elif args.command == "prepare":
            result_json = worktree_prepare(
                args.coordinating_repository,
                args.specification,
                args.repository,
                [(main_root, submodule_path) for main_root, submodule_path in args.participating_submodule],
            )
        elif args.command == "contracts-authored":
            result_json = worktree_contracts_authored(
                args.coordinating_repository,
                args.specification,
            )
        elif args.command == "accept-main-commit-drift":
            result_json = worktree_main_commit_drift_accept(
                args.coordinating_repository,
                args.specification,
                args.main_repository,
                args.commit,
                args.path,
            )
        elif args.command == "recover-main-leak":
            result_json = worktree_main_leak_recover(
                args.coordinating_repository,
                args.specification,
                args.main_repository,
                args.path,
            )
        elif args.command == "seal":
            result_json = worktree_seal(
                args.coordinating_repository,
                args.goal,
                args.specification,
            )
        else:
            result_json = worktree_validate(
                args.coordinating_repository,
                args.required_state,
                args.specification,
            )
    except (OSError, WorktreeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(result_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
