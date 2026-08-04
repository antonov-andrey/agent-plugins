#!/usr/bin/env python3
"""Create one exact Linear-linked GitHub pull request."""

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
    """Build the closed PR creation parser.

    Returns:
        The argument parser.
    """

    parser = argparse.ArgumentParser(description="Create one GitHub pull request linked to a Linear issue.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue-identifier", required=True)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--head-branch", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Create and read back one exact task PR.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for rejected preconditions.
    """

    args = _parser_get().parse_args(argv)
    try:
        snapshot = GitHubPullRequestBoundary().create(
            repository=RepositoryIdentity(args.repository),
            issue_identifier=args.issue_identifier,
            base_branch=args.base_branch,
            head_branch=args.head_branch,
            title=args.title,
            body_file=args.body_file,
        )
    except GitHubContractError as error:
        print(str(error), file=sys.stderr)
        return 2
    payload = asdict(snapshot)
    payload["repository"] = snapshot.repository.value
    payload["merged_at"] = ""
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
