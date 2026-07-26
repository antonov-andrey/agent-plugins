#!/usr/bin/env python3
"""Validate one provider-owned section audit result."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from lib.audit_contract import section_result_error_list_get


def _args_parse() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed command-line namespace.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-name", required=True)
    parser.add_argument("--expected-requirement", action="append", dest="requirement_list", required=True)
    parser.add_argument("--expected-scope", required=True)
    parser.add_argument("--expected-section", required=True)
    parser.add_argument("result_path", type=Path)
    return parser.parse_args()


def main() -> int:
    """Validate the requested section result.

    Returns:
        Zero for a valid result, otherwise one.
    """

    args = _args_parse()
    error_list = section_result_error_list_get(
        args.result_path,
        audit_name=args.audit_name,
        expected_requirement_list=args.requirement_list,
        expected_scope=args.expected_scope,
        expected_section=args.expected_section,
    )
    for error in error_list:
        print(f"ERROR: {error}", file=sys.stderr)
    return int(bool(error_list))


if __name__ == "__main__":
    raise SystemExit(main())
