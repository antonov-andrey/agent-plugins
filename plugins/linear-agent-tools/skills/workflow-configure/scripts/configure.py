#!/usr/bin/env python3
"""Plan or apply GraphQL-owned statuses and Git status automation removal."""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from json_contract import JsonContractError, json_load_strict
from linear_boundary.configuration.graphql import LinearWorkflowConfigurationGraphQL
from linear_boundary.configuration.model import (
    ConfigurationPlan,
    LinearLabel,
)
from linear_boundary.configuration.reconciliation import WorkflowConfigurationReconciler
from linear_boundary.contract import LinearContractError
from linear_boundary.transport import (
    LinearAuthenticationError,
    LinearGraphQLTransport,
    LinearTransportError,
)


def _args_parse(argv: list[str] | None) -> argparse.Namespace:
    """Parse the closed one-shot configuration arguments.

    Args:
        argv: Optional direct argument list.

    Returns:
        Parsed arguments.
    """

    parser = argparse.ArgumentParser(
        description="Plan or apply GraphQL-owned Linear workflow statuses and Git status automation removal."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "apply"):
        operation = subparsers.add_parser(command)
        operation.add_argument(
            "--workspace-id",
            required=command == "apply",
            help="Exact workspace UUID; omit only for the read-only initial plan that discovers it.",
        )
        operation.add_argument("--viewer-id", required=True)
        operation.add_argument("--team-id", required=True)
        operation.add_argument(
            "--labels-input",
            required=True,
            type=Path,
            help="Complete freshly MCP-read label snapshot JSON.",
        )
        if command == "apply":
            operation.add_argument(
                "--approved-plan-input",
                required=True,
                type=Path,
                help="Exact plan output previously approved by the user.",
            )
    return parser.parse_args(argv)


def _credential_get() -> str:
    """Read one admin-capable credential without echo or persistence.

    Returns:
        The in-memory credential.
    """

    value = getpass.getpass("Linear admin-capable API credential: ")
    if not value:
        raise LinearAuthenticationError("Linear credential was not provided")
    return value


def _json_load(path: Path, *, label: str) -> object:
    """Load one ordinary transient JSON input.

    Args:
        path: Exact JSON path.
        label: Diagnostic owner label.

    Returns:
        Decoded JSON value.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise LinearContractError(f"{label} must be one ordinary file")
    try:
        return json_load_strict(path.read_bytes())
    except (OSError, JsonContractError) as error:
        raise LinearContractError(f"{label} is malformed") from error


def _labels_load(path: Path) -> list[LinearLabel]:
    """Load one complete MCP-read label snapshot.

    Args:
        path: Exact JSON input path.

    Returns:
        Typed labels.
    """

    payload = _json_load(path, label="Label snapshot")
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise LinearContractError("Label snapshot root must be a list of objects")
    expected = {"id", "name", "color", "description"}
    if any(set(item) != expected for item in payload):
        raise LinearContractError("Label snapshot object has another shape")
    normalized: list[LinearLabel] = []
    for item in payload:
        description = item["description"]
        if description is None:
            description = ""
        normalized.append(
            LinearLabel(
                id=item["id"],
                name=item["name"],
                color=item["color"],
                description=description,
            )
        )
    return normalized


def _plan_envelope(plan: ConfigurationPlan) -> dict[str, object]:
    """Return one fingerprinted plan envelope.

    Args:
        plan: Typed configuration plan.

    Returns:
        The result payload.
    """

    return {**plan.payload(), "plan_sha256": plan.fingerprint()}


def _approved_plan_load(path: Path) -> ConfigurationPlan:
    """Load and verify one exact previously displayed plan.

    Args:
        path: Exact plan-output path.

    Returns:
        Typed approved plan.
    """

    payload = _json_load(path, label="Approved plan")
    if not isinstance(payload, dict) or "plan_sha256" not in payload:
        raise LinearContractError("Approved plan envelope has another shape")
    plan_payload = {name: value for name, value in payload.items() if name != "plan_sha256"}
    plan = ConfigurationPlan.from_payload(plan_payload)
    if payload["plan_sha256"] != plan.fingerprint():
        raise LinearContractError("Approved plan fingerprint differs from its content")
    if not plan.can_mutate():
        raise LinearContractError("Conflicting workflow configuration cannot be approved")
    plan.status_identifier_require()
    return plan


def main(argv: list[str] | None = None) -> int:
    """Run one secret-bounded configuration transaction.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for contract rejection.
    """

    args = _args_parse(argv)
    try:
        label_list = _labels_load(args.labels_input)
        service = LinearWorkflowConfigurationGraphQL(
            LinearGraphQLTransport(_credential_get()),
            WorkflowConfigurationReconciler(),
        )
        plan = service.plan(
            expected_workspace_id=args.workspace_id,
            expected_viewer_id=args.viewer_id,
            expected_team_id=args.team_id,
            label_list=label_list,
        )
        if args.command == "plan":
            plan = plan.status_identifier_allocate()
            print(json.dumps(_plan_envelope(plan), ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if plan.can_mutate() else 2
        approved_plan = _approved_plan_load(args.approved_plan_input)
        if (
            approved_plan.destination.workspace_id != args.workspace_id
            or approved_plan.destination.viewer_id != args.viewer_id
            or approved_plan.destination.team_id != args.team_id
        ):
            raise LinearContractError("Approved plan destination differs from apply arguments")
        plan.subset_require(approved_plan)
        service.approved_configuration_apply(
            expected_workspace_id=args.workspace_id,
            expected_viewer_id=args.viewer_id,
            expected_team_id=args.team_id,
            approved_plan=approved_plan,
        )
    except (LinearContractError, LinearTransportError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema_version": 1,
                "approved_plan_sha256": approved_plan.fingerprint(),
                "status": "configured",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
