#!/usr/bin/env python3
"""Validate `code-antipattern-audit` source-report contracts."""

from __future__ import annotations

import argparse
import sys

from lib.report_contract import ROOT, report_contract_error_list_collect


def args_parse() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed arguments.
    """

    parser = argparse.ArgumentParser(description="Validate code-antipattern-audit source-report contracts.")
    parser.add_argument(
        "--expected-scope",
        required=True,
        help="Normalized declared repository-relative Python scope expected in every validated report.",
    )
    parser.add_argument(
        "report_path_list",
        nargs="+",
        help="Repository-relative anti-pattern source-report paths under tmp/.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the report-contract validator.

    Returns:
        Process exit code.
    """

    args = args_parse()
    have_error = False
    for report_relpath in args.report_path_list:
        report_path = ROOT / report_relpath
        error_list = report_contract_error_list_collect(report_path, expected_scope=args.expected_scope)
        if error_list:
            have_error = True
            print(f"FAIL: {report_relpath}", file=sys.stderr)
            for error in error_list:
                print(f"  - {error}", file=sys.stderr)
            continue
        print(f"PASS: {report_relpath}")
    return int(have_error)


if __name__ == "__main__":
    raise SystemExit(main())
