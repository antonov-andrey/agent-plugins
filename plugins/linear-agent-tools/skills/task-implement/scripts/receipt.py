#!/usr/bin/env python3
"""Create or evaluate one dependency-aware verification receipt."""

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
from verification._validation import VerificationReceiptError
from verification.invalidation import ReceiptReuseEvaluator
from verification.model import VerificationInput, VerificationReceipt
from verification.receipt import VERIFICATION_RECEIPT_COMMENT_CODEC


def _args_parse(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the closed receipt command arguments.

    Args:
        argv: Optional direct argument list.

    Returns:
        Parsed arguments.
    """

    parser = argparse.ArgumentParser(description="Create or evaluate one exact verification receipt.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--input", required=True, type=Path)
    create.add_argument("--outcome", choices=("passed", "failed"), required=True)
    create.add_argument("--evidence-url", required=True)
    create.add_argument("--evidence-content-sha256", required=True)
    reuse = subparsers.add_parser("reuse")
    reuse.add_argument(
        "--receipt-comment",
        required=True,
        type=Path,
        help="Exact provider-owned Linear comment body saved as one UTF-8 file.",
    )
    reuse.add_argument("--input", required=True, type=Path)
    return parser.parse_args(argv)


def _json_load(path: Path, *, label: str) -> object:
    """Load one ordinary JSON input.

    Args:
        path: Exact file path.
        label: Diagnostic owner label.

    Returns:
        Decoded JSON value.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise VerificationReceiptError(f"{label} must be one ordinary file")
    try:
        return json_load_strict(path.read_bytes())
    except (OSError, JsonContractError) as error:
        raise VerificationReceiptError(f"{label} is malformed") from error


def _text_load(path: Path, *, label: str) -> str:
    """Load one ordinary UTF-8 text input.

    Args:
        path: Exact file path.
        label: Diagnostic owner label.

    Returns:
        Exact text.
    """

    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise VerificationReceiptError(f"{label} must be one ordinary file")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationReceiptError(f"{label} is malformed") from error


def main(argv: list[str] | None = None) -> int:
    """Run one receipt operation.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success, one for a cache miss, or two for malformed input.
    """

    args = _args_parse(argv)
    try:
        current = VerificationInput.from_payload(_json_load(args.input, label="Verification input"))
        if args.command == "create":
            receipt = VerificationReceipt.from_input(
                current,
                outcome=args.outcome,
                evidence_url=args.evidence_url,
                evidence_content_sha256=args.evidence_content_sha256,
            )
            print(VERIFICATION_RECEIPT_COMMENT_CODEC.render(receipt.payload()))
            return 0
        receipt = VerificationReceipt.from_payload(
            VERIFICATION_RECEIPT_COMMENT_CODEC.payload_parse(
                _text_load(args.receipt_comment, label="Verification receipt comment")
            )
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
    except VerificationReceiptError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
