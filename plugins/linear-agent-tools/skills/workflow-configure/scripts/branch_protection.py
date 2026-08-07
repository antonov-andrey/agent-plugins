#!/usr/bin/env python3
"""Plan or apply exact GitHub protection and repository merge policy."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_host.authentication import GitHubPrincipal
from git_host.branch_protection import GitHubBranchProtectionBoundary
from git_host.model import BranchProtectionSnapshot, GitHubContractError, RepositoryIdentity
from git_host.repository_policy import GitHubRepositoryMergePolicy, GitHubRepositoryMergePolicyBoundary
from json_contract import JsonContractError, json_load_strict


def _parser_get() -> argparse.ArgumentParser:
    """Build the exact branch-protection transaction parser."""

    parser = argparse.ArgumentParser(
        description="Plan or apply exact GitHub repository merge policy and base protection."
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


def _repository_policy_payload(policy: GitHubRepositoryMergePolicy) -> dict[str, object]:
    """Return one JSON-owned exact principal-bound repository-policy snapshot."""

    payload = asdict(policy)
    payload["repository"] = policy.repository.value
    return payload


def _snapshot_principal_get(snapshot: BranchProtectionSnapshot) -> GitHubPrincipal:
    """Return the exact principal embedded in one protection snapshot."""

    return GitHubPrincipal(
        login=snapshot.execution_login,
        user_id=snapshot.execution_user_id,
        node_id=snapshot.execution_node_id,
    )


def _plan_payload(
    snapshot: BranchProtectionSnapshot,
    repository_policy: GitHubRepositoryMergePolicy,
    *,
    merge_method: str,
) -> dict[str, object]:
    """Return the exact current and desired GitHub configuration transaction."""

    snapshot.mutation_authority_require()
    if repository_policy.repository != snapshot.repository or repository_policy.principal != _snapshot_principal_get(
        snapshot
    ):
        raise GitHubContractError("GitHub protection and repository policy name another destination or principal")
    repository_policy.selected_method_enabled_require(merge_method)
    if snapshot.protection_source_list:
        snapshot.merge_mechanism_require(merge_method)
        protection_action = "none"
    else:
        if merge_method != "merge":
            raise GitHubContractError("Squash or rebase requires pre-existing strict required-check protection")
        protection_action = "create-minimal-classic-protection"
    repository_policy_action = (
        "disable-automatic-branch-deletion" if repository_policy.delete_branch_on_merge else "none"
    )
    desired_repository_policy = replace(repository_policy, delete_branch_on_merge=False)
    return {
        "schema_version": 2,
        "repository": snapshot.repository.value,
        "base_branch": snapshot.base_branch,
        "merge_method": merge_method,
        "protection_action": protection_action,
        "repository_policy_action": repository_policy_action,
        "protection_before": _snapshot_payload(snapshot),
        "repository_policy_before": _repository_policy_payload(repository_policy),
        "repository_policy_after": _repository_policy_payload(desired_repository_policy),
    }


def _approved_plan_load(path: Path) -> dict[str, object]:
    """Load one exact ordinary plan file from the current transaction."""

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise GitHubContractError("Approved GitHub configuration plan must be one ordinary file")
    try:
        payload = json_load_strict(path.read_bytes())
    except (OSError, JsonContractError) as error:
        raise GitHubContractError("Approved GitHub configuration plan is malformed") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "repository",
        "base_branch",
        "merge_method",
        "protection_action",
        "repository_policy_action",
        "protection_before",
        "repository_policy_before",
        "repository_policy_after",
    }:
        raise GitHubContractError("Approved GitHub configuration plan has another shape")
    if (
        payload["schema_version"] != 2
        or payload["protection_action"] not in {"none", "create-minimal-classic-protection"}
        or payload["repository_policy_action"] not in {"none", "disable-automatic-branch-deletion"}
    ):
        raise GitHubContractError("Approved GitHub configuration plan has another shape")
    return payload


def main(argv: list[str] | None = None) -> int:
    """Run one exact read-only plan or approved configuration transaction."""

    args = _parser_get().parse_args(argv)
    protection_boundary = GitHubBranchProtectionBoundary()
    repository_policy_boundary = GitHubRepositoryMergePolicyBoundary()
    try:
        repository = RepositoryIdentity(args.repository)
        protection_before = protection_boundary.inspect(repository=repository, base_branch=args.base_branch)
        principal = _snapshot_principal_get(protection_before)
        repository_policy_before = repository_policy_boundary.configuration_inspect(
            repository=repository,
            principal=principal,
            merge_method=args.merge_method,
        )
        plan = _plan_payload(
            protection_before,
            repository_policy_before,
            merge_method=args.merge_method,
        )
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
            raise GitHubContractError("Current GitHub configuration differs from the approved plan")
        protection_changed = approved_plan["protection_action"] == "create-minimal-classic-protection"
        repository_policy_changed = approved_plan["repository_policy_action"] == "disable-automatic-branch-deletion"
        if protection_changed:
            configured_protection = protection_boundary.configure_for_protected_ref_cas(
                repository=repository,
                base_branch=args.base_branch,
                approved_snapshot=protection_before,
            )
        else:
            configured_protection = protection_before
        configured_repository_policy = repository_policy_boundary.automatic_branch_deletion_disable(
            repository=repository,
            principal=principal,
            merge_method=args.merge_method,
            approved_policy=repository_policy_before,
        )
        protection_after = protection_boundary.inspect(repository=repository, base_branch=args.base_branch)
        if protection_after != configured_protection:
            raise GitHubContractError("Final GitHub protection readback differs from the configured result")
        protection_after.merge_mechanism_require(args.merge_method)
        repository_policy_after = repository_policy_boundary.inspect(
            repository=repository,
            principal=principal,
            merge_method=args.merge_method,
        )
        if repository_policy_after != configured_repository_policy:
            raise GitHubContractError("Final GitHub repository-policy readback differs from the configured result")
    except GitHubContractError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema_version": 2,
                "status": "configured",
                "changed": protection_changed or repository_policy_changed,
                "protection_changed": protection_changed,
                "repository_policy_changed": repository_policy_changed,
                "protection_after": _snapshot_payload(protection_after),
                "repository_policy_after": _repository_policy_payload(repository_policy_after),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
