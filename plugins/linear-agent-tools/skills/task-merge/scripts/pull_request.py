#!/usr/bin/env python3
"""Inspect or merge one exact independently reviewed GitHub pull request."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_host.model import GitHubContractError, RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed PR merge parser.

    Returns:
        The argument parser.
    """

    parser = argparse.ArgumentParser(
        description="Inspect or atomically merge one exact reviewed GitHub pull request base and head."
    )
    parser.add_argument("command", choices=("inspect", "merge"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--issue-identifier", required=True)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--head-branch", required=True)
    parser.add_argument("--reviewed-base-commit", required=True)
    parser.add_argument("--reviewed-head-commit", required=True)
    parser.add_argument("--merge-method", required=True, choices=("merge", "squash", "rebase"))
    parser.add_argument("--repository-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one exact pull-request operation.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for rejected preconditions.
    """

    args = _parser_get().parse_args(argv)
    boundary = GitHubPullRequestBoundary()
    try:
        repository = RepositoryIdentity(args.repository)
        protection = None
        if args.command == "merge":
            if args.merge_method == "merge" and args.repository_path is None:
                raise GitHubContractError("Atomic merge requires --repository-path")
            snapshot = boundary.merge(
                repository=repository,
                number=args.number,
                issue_identifier=args.issue_identifier,
                base_branch=args.base_branch,
                head_branch=args.head_branch,
                reviewed_base_commit=args.reviewed_base_commit,
                reviewed_head_commit=args.reviewed_head_commit,
                merge_method=args.merge_method,
                repository_path=args.repository_path,
            )
        else:
            inspection = boundary.reviewed_inspect(
                repository=repository,
                number=args.number,
                issue_identifier=args.issue_identifier,
                base_branch=args.base_branch,
                head_branch=args.head_branch,
                reviewed_base_commit=args.reviewed_base_commit,
                reviewed_head_commit=args.reviewed_head_commit,
                merge_method=args.merge_method,
            )
            snapshot = inspection.pull_request
            protection = inspection.branch_protection
    except GitHubContractError as error:
        print(str(error), file=sys.stderr)
        return 2
    payload = asdict(snapshot)
    payload["repository"] = snapshot.repository.value
    payload["merged_at"] = snapshot.merged_at.isoformat().replace("+00:00", "Z") if snapshot.merged_at else ""
    if protection is None:
        payload["branch_protection"] = None
    else:
        payload["branch_protection"] = asdict(protection)
        payload["branch_protection"]["repository"] = protection.repository.value
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
