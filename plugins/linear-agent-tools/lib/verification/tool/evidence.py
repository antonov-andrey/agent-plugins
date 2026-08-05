#!/usr/bin/env python3
"""Create, evaluate, and render shared Linear workflow evidence."""

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
from verification._validation import VerificationReceiptError, instant_parse
from verification.attempt import AttemptSummary
from verification.baseline import LocalPhaseBaseline, TaskWorkspaceBaseline
from verification.candidate import CandidateInput
from verification.invalidation import ReceiptReuseEvaluator
from verification.model import VerificationInput, VerificationReceipt
from verification.receipt import (
    ATTEMPT_COMMENT_CODEC,
    LOCAL_PHASE_BASELINE_COMMENT_CODEC,
    TASK_WORKSPACE_BASELINE_COMMENT_CODEC,
    VERIFICATION_RECEIPT_COMMENT_CODEC,
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
    receipt_create = subparsers.add_parser("receipt-create")
    receipt_create.add_argument("--input", required=True, type=Path)
    receipt_create.add_argument("--outcome", choices=("passed", "failed"), required=True)
    receipt_create.add_argument("--completed-at", required=True)
    receipt_create.add_argument(
        "--evidence-url",
        required=True,
        help="Durable canonical HTTPS provider URL without credentials, port, query, or fragment.",
    )
    receipt_create.add_argument(
        "--evidence-content-sha256",
        required=True,
        help="Lowercase SHA-256 of the exact independently readable evidence bytes.",
    )
    receipt_reuse = subparsers.add_parser("receipt-reuse")
    receipt_reuse.add_argument("--input", required=True, type=Path)
    receipt_reuse.add_argument(
        "--receipt-comment",
        required=True,
        type=Path,
        help="Exact provider-owned Linear comment body saved as one UTF-8 file.",
    )
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


def _text_load(path: Path) -> str:
    """Load one ordinary exact UTF-8 provider comment.

    Args:
        path: Exact file path.

    Returns:
        Exact text.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise VerificationReceiptError("Verification receipt comment must be one ordinary file")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationReceiptError("Verification receipt comment is malformed") from error


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
        if args.command == "receipt-create":
            current = VerificationInput.from_payload(payload)
            receipt = VerificationReceipt.from_input(
                current,
                outcome=args.outcome,
                completed_at=instant_parse(args.completed_at, label="Verification completed_at"),
                evidence_url=args.evidence_url,
                evidence_content_sha256=args.evidence_content_sha256,
            )
            print(VERIFICATION_RECEIPT_COMMENT_CODEC.render(receipt.payload()))
        elif args.command == "receipt-reuse":
            current = VerificationInput.from_payload(payload)
            receipt = VerificationReceipt.from_payload(
                VERIFICATION_RECEIPT_COMMENT_CODEC.payload_parse(_text_load(args.receipt_comment))
            )
            decision = ReceiptReuseEvaluator(current).decision_get(receipt)
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "reason_list": list(decision.reason_list),
                        "reusable": decision.reusable,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0 if decision.reusable else 1
        elif args.command == "candidate":
            candidate = CandidateInput.from_payload(payload)
            print(
                json.dumps(
                    {
                        "schema_version": 2,
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
