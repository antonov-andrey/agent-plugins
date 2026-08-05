#!/usr/bin/env python3
"""Render exact candidate, attempt, and local-baseline evidence."""

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
from verification._validation import VerificationReceiptError
from verification.attempt import AttemptSummary
from verification.baseline import LocalPhaseBaseline, TaskWorkspaceBaseline
from verification.candidate import CandidateInput
from verification.receipt import (
    ATTEMPT_COMMENT_CODEC,
    LOCAL_PHASE_BASELINE_COMMENT_CODEC,
    TASK_WORKSPACE_BASELINE_COMMENT_CODEC,
)


def _args_parse(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the closed evidence command arguments.

    Args:
        argv: Optional direct argument list.

    Returns:
        Parsed arguments.
    """

    parser = argparse.ArgumentParser(description="Render exact Linear workflow evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("candidate", "attempt", "baseline", "workspace-baseline"):
        operation = subparsers.add_parser(command)
        operation.add_argument("--input", required=True, type=Path)
    return parser.parse_args(argv)


def _json_load(path: Path) -> object:
    """Load one ordinary transient JSON input.

    Args:
        path: Exact file path.

    Returns:
        Decoded JSON value.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise VerificationReceiptError("Evidence input must be one ordinary file")
    try:
        return json_load_strict(path.read_bytes())
    except (OSError, JsonContractError) as error:
        raise VerificationReceiptError("Evidence input is malformed") from error


def main(argv: list[str] | None = None) -> int:
    """Run one deterministic evidence operation.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two on malformed input.
    """

    args = _args_parse(argv)
    try:
        payload = _json_load(args.input)
        if args.command == "candidate":
            candidate = CandidateInput.from_payload(payload)
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "candidate_fingerprint": candidate.fingerprint(),
                        "candidate_identity": candidate.identity_payload(),
                        "input": candidate.payload(),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif args.command == "attempt":
            print(ATTEMPT_COMMENT_CODEC.render(AttemptSummary.from_payload(payload).payload()))
        elif args.command == "baseline":
            print(LOCAL_PHASE_BASELINE_COMMENT_CODEC.render(LocalPhaseBaseline.from_payload(payload).payload()))
        else:
            print(TASK_WORKSPACE_BASELINE_COMMENT_CODEC.render(TaskWorkspaceBaseline.from_payload(payload).payload()))
        return 0
    except VerificationReceiptError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
