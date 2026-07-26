#!/usr/bin/env python3
"""Validate one merged provider-owned audit report."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from lib.audit_contract import report_error_list_get


def _args_parse() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed command-line namespace.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-name", required=True)
    parser.add_argument("--expected-scope-entry", action="append", dest="scope_entry_list", required=True)
    parser.add_argument("--expected-scope-mode", choices=("default-changed", "explicit"), required=True)
    parser.add_argument("report_path", type=Path)
    return parser.parse_args()


def main() -> int:
    """Validate the requested merged audit report.

    Returns:
        Zero for a valid report, otherwise one.
    """

    args = _args_parse()
    error_list = report_error_list_get(
        args.report_path,
        audit_name=args.audit_name,
        expected_scope_entry_list=args.scope_entry_list,
        expected_scope_mode=args.expected_scope_mode,
    )
    for error in error_list:
        print(f"ERROR: {error}", file=sys.stderr)
    return int(bool(error_list))


if __name__ == "__main__":
    raise SystemExit(main())
