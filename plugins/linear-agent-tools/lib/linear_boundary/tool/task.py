#!/usr/bin/env python3
"""Validate one exact Linear task dispatch or status transition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[2]
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from json_contract import JsonContractError, json_load_strict
from linear_boundary.contract import LinearContractError
from linear_boundary.status import IssueStatusName, ProjectStatusName
from linear_boundary.task.model import TaskExecutionSnapshot, TransitionProof
from linear_boundary.task.workflow import TaskTransition


def _parser_get() -> argparse.ArgumentParser:
    """Build the one-input task-state parser."""

    parser = argparse.ArgumentParser(
        description="Validate one Linear task dispatch or transition boundary."
    )
    parser.add_argument("operation", choices=("dispatch", "transition"))
    parser.add_argument("--input", required=True, type=Path)
    return parser


def _json_load(path: Path) -> object:
    """Read one strict ordinary transient JSON file."""

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise LinearContractError("Task-state input must be one ordinary file")
    try:
        return json_load_strict(path.read_bytes())
    except (OSError, JsonContractError) as error:
        raise LinearContractError("Task-state input is malformed") from error


def _transition_validate(payload: object) -> None:
    """Parse and validate one requested workflow transition."""

    expected = {
        "schema_version",
        "current_status",
        "target_status",
        "project_status",
        "role_label",
        "delivery_kind",
        "dispatchable",
        "proof",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or payload["schema_version"] != 1
    ):
        raise LinearContractError("Task transition input has another shape")
    proof_payload = payload["proof"]
    proof_field_set = set(TransitionProof.__dataclass_fields__)
    if not isinstance(proof_payload, dict) or set(proof_payload) != proof_field_set:
        raise LinearContractError("Task transition proof has another shape")
    try:
        TaskTransition(
            current=IssueStatusName(payload["current_status"]),
            target=IssueStatusName(payload["target_status"]),
            project_status=ProjectStatusName(payload["project_status"]),
            role_label=payload["role_label"],
            delivery_kind=payload["delivery_kind"],
            proof=TransitionProof(**proof_payload),
            dispatchable=payload["dispatchable"],
        ).require()
    except (TypeError, ValueError) as error:
        raise LinearContractError(
            "Task transition input contains an unsupported value"
        ) from error


def main(argv: list[str] | None = None) -> int:
    """Run one deterministic task-state decision."""

    args = _parser_get().parse_args(argv)
    try:
        payload = _json_load(args.input)
        if args.operation == "dispatch":
            snapshot = TaskExecutionSnapshot.from_dispatch_payload(payload)
            blocker_list = snapshot.dispatch_blocker_list()
            result = {
                "schema_version": 1,
                "dispatchable": not blocker_list,
                "blocker_list": list(blocker_list),
            }
            print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            return 0 if not blocker_list else 1
        _transition_validate(payload)
        print('{"schema_version":1,"transition_allowed":true}')
        return 0
    except LinearContractError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
