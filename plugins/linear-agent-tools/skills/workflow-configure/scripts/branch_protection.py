#!/usr/bin/env python3
"""Plan or apply exact minimal GitHub protection for atomic reviewed merges."""

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

from git_host.branch_protection import GitHubBranchProtectionBoundary
from git_host.model import BranchProtectionSnapshot, GitHubContractError, RepositoryIdentity
from json_contract import JsonContractError, json_load_strict


def _parser_get() -> argparse.ArgumentParser:
    """Build the exact branch-protection transaction parser."""

    parser = argparse.ArgumentParser(
        description="Plan or apply exact GitHub base protection for atomic reviewed merge transactions."
    )
    subparser_by_name = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "apply"):
        operation = subparser_by_name.add_parser(command)
        operation.add_argument("--repository", required=True)
        operation.add_argument("--base-branch", required=True)
        operation.add_argument("--merge-method", required=True, choices=("merge", "squash", "rebase"))
        if command == "apply":
            operation.add_argument("--approved-plan-input", required=True, type=Path)
    return parser


def _snapshot_payload(snapshot: BranchProtectionSnapshot) -> dict[str, object]:
    """Return one JSON-owned exact protection snapshot."""

    payload = asdict(snapshot)
    payload["repository"] = snapshot.repository.value
    return payload


def _plan_payload(snapshot: BranchProtectionSnapshot, *, merge_method: str) -> dict[str, object]:
    """Return the only allowed absent-or-ready configuration action."""

    if snapshot.protection_source_list:
        snapshot.merge_mechanism_require(merge_method)
        action = "none"
    else:
        if merge_method != "merge":
            raise GitHubContractError("Squash or rebase requires pre-existing strict required-check protection")
        action = "create-minimal-classic-protection"
    return {
        "schema_version": 1,
        "repository": snapshot.repository.value,
        "base_branch": snapshot.base_branch,
        "merge_method": merge_method,
        "action": action,
        "protection_before": _snapshot_payload(snapshot),
    }


def _approved_plan_load(path: Path) -> dict[str, object]:
    """Load one exact ordinary plan file from the current transaction."""

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise GitHubContractError("Approved GitHub protection plan must be one ordinary file")
    try:
        payload = json_load_strict(path.read_bytes())
    except (OSError, JsonContractError) as error:
        raise GitHubContractError("Approved GitHub protection plan is malformed") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "repository",
        "base_branch",
        "merge_method",
        "action",
        "protection_before",
    }:
        raise GitHubContractError("Approved GitHub protection plan has another shape")
    if payload["schema_version"] != 1 or payload["action"] not in {
        "none",
        "create-minimal-classic-protection",
    }:
        raise GitHubContractError("Approved GitHub protection plan has another shape")
    return payload


def main(argv: list[str] | None = None) -> int:
    """Run one exact read-only plan or approved configuration transaction."""

    args = _parser_get().parse_args(argv)
    boundary = GitHubBranchProtectionBoundary()
    try:
        repository = RepositoryIdentity(args.repository)
        before = boundary.inspect(repository=repository, base_branch=args.base_branch)
        plan = _plan_payload(before, merge_method=args.merge_method)
        if args.command == "plan":
            print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        approved_plan = _approved_plan_load(args.approved_plan_input)
        if (
            approved_plan["repository"] != repository.value
            or approved_plan["base_branch"] != args.base_branch
            or approved_plan["merge_method"] != args.merge_method
            or approved_plan != plan
        ):
            raise GitHubContractError("Current GitHub protection differs from the approved plan")
        changed = approved_plan["action"] == "create-minimal-classic-protection"
        if changed:
            after = boundary.configure_for_protected_ref_cas(
                repository=repository,
                base_branch=args.base_branch,
            )
        else:
            after = before
        after.merge_mechanism_require(args.merge_method)
    except GitHubContractError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema_version": 1,
                "status": "configured",
                "changed": changed,
                "protection_after": _snapshot_payload(after),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
