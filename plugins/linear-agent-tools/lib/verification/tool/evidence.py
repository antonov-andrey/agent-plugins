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

from verification import (
    AttemptSummary,
    CandidateInput,
    LocalPhaseBaseline,
    TaskWorkspaceBaseline,
    VerificationReceiptError,
    attempt_comment_render,
    baseline_comment_render,
    workspace_baseline_comment_render,
)


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed evidence command parser.

    Returns:
        The argument parser.
    """

    parser = argparse.ArgumentParser(description="Render exact Linear workflow evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("candidate", "attempt", "baseline", "workspace-baseline"):
        operation = subparsers.add_parser(command)
        operation.add_argument("--input", required=True, type=Path)
    return parser


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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationReceiptError("Evidence input is malformed") from error


def main(argv: list[str] | None = None) -> int:
    """Run one deterministic evidence operation.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two on malformed input.
    """

    args = _parser_get().parse_args(argv)
    try:
        payload = _json_load(args.input)
        if args.command == "candidate":
            candidate = CandidateInput.from_payload(payload)
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "candidate_fingerprint": candidate.fingerprint(),
                        "input": candidate.payload(),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif args.command == "attempt":
            print(attempt_comment_render(AttemptSummary.from_payload(payload)))
        elif args.command == "baseline":
            print(baseline_comment_render(LocalPhaseBaseline.from_payload(payload)))
        else:
            print(workspace_baseline_comment_render(TaskWorkspaceBaseline.from_payload(payload)))
        return 0
    except VerificationReceiptError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
