#!/usr/bin/env python3
"""Validate, render or reconcile one source-independent Linear task graph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from json_contract import JsonContractError, json_load_strict
from task_graph.delta import TaskGraphDelta
from task_graph.model import TaskGraph, TaskGraphError
from task_graph.publication import DeltaPublicationView, GraphPublicationView
from task_graph.reconciliation.delta import TaskGraphDeltaReconciler
from task_graph.reconciliation.initial import TaskGraphReconciler
from task_graph.reconciliation.model import RemoteProject


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed graph utility parser.

    Returns:
        The argument parser.
    """

    parser = argparse.ArgumentParser(description="Validate and plan one Linear Project task graph import.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "validate",
        "render",
        "reconcile",
        "activation-confirm",
        "cancel-plan",
    ):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--graph-input", required=True, type=Path)
        if command in {"reconcile", "activation-confirm", "cancel-plan"}:
            command_parser.add_argument(
                "--snapshot-input",
                required=command in {"activation-confirm", "cancel-plan"},
                type=Path,
                help="Fully paginated current Project snapshot; omit only for reconcile when the Project is absent.",
            )
        if command == "cancel-plan":
            command_parser.add_argument(
                "--human-decision",
                action="store_true",
                help="Confirm the explicit human cancellation decision for this exact Project.",
            )
    for command in ("delta-validate", "delta-render", "delta-reconcile"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--delta-input", required=True, type=Path)
        if command == "delta-reconcile":
            command_parser.add_argument(
                "--snapshot-input",
                required=True,
                type=Path,
                help="Fully paginated current active Project snapshot.",
            )
    return parser


def _json_load(path: Path, *, label: str) -> object:
    """Load one ordinary UTF-8 JSON input file.

    Args:
        path: Exact input path.
        label: Diagnostic owner label.

    Returns:
        Decoded JSON value.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise TaskGraphError(f"{label} must be one ordinary file")
    try:
        return json_load_strict(path.read_bytes())
    except (OSError, JsonContractError) as error:
        raise TaskGraphError(f"{label} is malformed") from error


def main(argv: list[str] | None = None) -> int:
    """Run one deterministic graph operation.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for contract rejection.
    """

    args = _parser_get().parse_args(argv)
    try:
        if args.command.startswith("delta-"):
            delta = TaskGraphDelta.from_payload(_json_load(args.delta_input, label="Delta input"))
            if args.command == "delta-validate":
                payload: dict[str, object] = {
                    "schema_version": 1,
                    "delta_fingerprint": delta.fingerprint(),
                    "new_node_count": len(delta.node_list),
                    "project_id": delta.project_id,
                    "project_key": delta.project_key,
                    "source_fingerprint": delta.source.fingerprint(),
                }
            elif args.command == "delta-render":
                payload = DeltaPublicationView.from_delta(delta).payload()
            else:
                remote = RemoteProject.from_payload(_json_load(args.snapshot_input, label="Project snapshot"))
                payload = TaskGraphDeltaReconciler(delta).plan(remote).payload()
        else:
            graph = TaskGraph.from_payload(_json_load(args.graph_input, label="Graph input"))
            if args.command == "validate":
                payload = {
                    "schema_version": 1,
                    "graph_fingerprint": graph.graph_fingerprint(),
                    "node_count": len(graph.node_list),
                    "project_key": graph.project_key(),
                    "source_fingerprint": graph.source_fingerprint(),
                }
            elif args.command == "render":
                payload = GraphPublicationView.from_graph(graph).payload()
            elif args.command == "reconcile":
                remote = (
                    RemoteProject.from_payload(_json_load(args.snapshot_input, label="Project snapshot"))
                    if args.snapshot_input is not None
                    else None
                )
                payload = TaskGraphReconciler(graph).plan(remote).payload()
            elif args.command == "activation-confirm":
                remote = RemoteProject.from_payload(_json_load(args.snapshot_input, label="Project snapshot"))
                payload = TaskGraphReconciler(graph).activation_readback_require(remote).payload()
            else:
                remote = RemoteProject.from_payload(_json_load(args.snapshot_input, label="Project snapshot"))
                payload = (
                    TaskGraphReconciler(graph)
                    .cancellation_plan(
                        remote,
                        human_decision=args.human_decision,
                    )
                    .payload()
                )
    except (TaskGraphError, ValueError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
