#!/usr/bin/env python3
"""Inspect or merge one exact human-approved GitHub pull request."""

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

from git_host import GitHubContractError, GitHubPullRequestBoundary, RepositoryIdentity


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed PR merge parser.

    Returns:
        The argument parser.
    """

    parser = argparse.ArgumentParser(description="Inspect or merge one exact GitHub pull request candidate.")
    parser.add_argument("command", choices=("inspect", "merge"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--issue-identifier", required=True)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--head-branch", required=True)
    parser.add_argument("--approved-head-commit", required=True)
    parser.add_argument("--merge-method", choices=("merge", "squash", "rebase"))
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
        if args.command == "merge":
            if not args.merge_method:
                raise GitHubContractError("Merge requires --merge-method")
            snapshot = boundary.merge(
                repository=repository,
                number=args.number,
                issue_identifier=args.issue_identifier,
                base_branch=args.base_branch,
                head_branch=args.head_branch,
                approved_head_commit=args.approved_head_commit,
                merge_method=args.merge_method,
            )
        else:
            snapshot = boundary.inspect(repository=repository, number=args.number)
        snapshot.integration_identity_require(args.issue_identifier)
        snapshot.target_require(base_branch=args.base_branch, head_branch=args.head_branch)
        if args.command == "inspect":
            if snapshot.state == "MERGED":
                snapshot.merged_result_require(approved_head_commit=args.approved_head_commit)
            else:
                snapshot.merge_preconditions_require(approved_head_commit=args.approved_head_commit)
    except GitHubContractError as error:
        print(str(error), file=sys.stderr)
        return 2
    payload = asdict(snapshot)
    payload["repository"] = snapshot.repository.value
    payload["merged_at"] = snapshot.merged_at.isoformat().replace("+00:00", "Z") if snapshot.merged_at else ""
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
