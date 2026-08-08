#!/usr/bin/env python3
"""Synchronize and install the exact merged linear-agent-tools provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from task_merge.provider_installation import (
    ProviderInstallationError,
    ProviderInstallationReconciler,
    ProviderInstallationRequest,
    standard_home_environment_get,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed lifecycle-provider installation parser."""

    parser = argparse.ArgumentParser(
        description="Synchronize the configured local marketplace source and install the merged lifecycle provider."
    )
    parser.add_argument("--issue-identifier", required=True)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--reviewed-base-commit", required=True)
    parser.add_argument("--reviewed-head-commit", required=True)
    parser.add_argument("--merged-base-commit", required=True)
    parser.add_argument("--expected-version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Recover or complete the merged lifecycle-provider installation."""

    args = _parser_get().parse_args(argv)
    try:
        standard_home, environment_by_name_map = standard_home_environment_get()
        result = ProviderInstallationReconciler(
            bootstrap_repository_root=REPOSITORY_ROOT,
            environment_by_name_map=environment_by_name_map,
            standard_home=standard_home,
        ).reconcile(
            ProviderInstallationRequest(
                issue_identifier=args.issue_identifier,
                base_branch=args.base_branch,
                reviewed_base_commit=args.reviewed_base_commit,
                reviewed_head_commit=args.reviewed_head_commit,
                merged_base_commit=args.merged_base_commit,
                expected_version=args.expected_version,
            )
        )
    except ProviderInstallationError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result.payload(), ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
