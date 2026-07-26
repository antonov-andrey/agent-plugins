#!/usr/bin/env python3
"""Deterministically merge validated `code-antipattern-audit` source reports."""

from __future__ import annotations

import argparse

from lib.report_contract import (
    ROOT,
    confirmed_case_count,
    merged_report_relpath_build,
    parsed_report_build,
    report_contract_error_list_collect,
    source_report_heading_level_demote,
)


def args_parse() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed arguments.
    """

    parser = argparse.ArgumentParser(description="Merge validated code-antipattern-audit source reports.")
    parser.add_argument(
        "mechanical_report",
        help="Repository-relative validated mechanical report path under tmp/.",
    )
    parser.add_argument(
        "semantic_report",
        help="Repository-relative validated semantic report path under tmp/.",
    )
    return parser.parse_args()


def _merged_overall_verdict_get(mechanical_verdict: str, semantic_verdict: str) -> str:
    """Resolve the deterministic merged overall verdict.

    Args:
        mechanical_verdict: Mechanical source verdict.
        semantic_verdict: Semantic source verdict.

    Returns:
        Merged overall verdict.
    """

    if "ERROR" in {mechanical_verdict, semantic_verdict}:
        return "ERROR"
    if "FINDINGS" in {mechanical_verdict, semantic_verdict}:
        return "FINDINGS"
    if semantic_verdict == "NO_AUDITABLE_SCOPE":
        return "NO_AUDITABLE_SCOPE"
    return "CLEAN"


def main() -> int:
    """Run the deterministic report merger.

    Returns:
        Process exit code.
    """

    args = args_parse()
    mechanical_path = ROOT / args.mechanical_report
    semantic_path = ROOT / args.semantic_report
    try:
        mechanical_report = parsed_report_build(mechanical_path)
        semantic_report = parsed_report_build(semantic_path)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    if mechanical_report["report_kind"] != "mechanical":
        raise SystemExit(f"ERROR: expected mechanical report path, got {mechanical_report['report_relative_path']}")
    if semantic_report["report_kind"] != "semantic":
        raise SystemExit(f"ERROR: expected semantic report path, got {semantic_report['report_relative_path']}")

    mechanical_error_list = report_contract_error_list_collect(
        mechanical_path,
        expected_scope=mechanical_report["audit_scope"],
    )
    if mechanical_error_list:
        raise SystemExit("ERROR: invalid mechanical source report:\n- " + "\n- ".join(mechanical_error_list))
    semantic_error_list = report_contract_error_list_collect(
        semantic_path,
        expected_scope=semantic_report["audit_scope"],
    )
    if semantic_error_list:
        raise SystemExit("ERROR: invalid semantic source report:\n- " + "\n- ".join(semantic_error_list))
    if mechanical_report["audit_scope"] != semantic_report["audit_scope"]:
        raise SystemExit(
            f"ERROR: source report scope mismatch: "
            f"{mechanical_report['report_relative_path']} -> {mechanical_report['audit_scope']!r}, "
            f"{semantic_report['report_relative_path']} -> {semantic_report['audit_scope']!r}"
        )

    merged_relpath = merged_report_relpath_build(
        mechanical_report["report_relative_path"],
        semantic_report["report_relative_path"],
    )
    merged_path = ROOT / merged_relpath
    merged_path.parent.mkdir(parents=True, exist_ok=True)

    merged_text = (
        "# `code-antipattern-audit` Report\n\n"
        "## Scope\n"
        f"- `scope`: `{mechanical_report['audit_scope']}`\n\n"
        "## Report metadata\n"
        f"- `report_path`: `{merged_relpath}`\n\n"
        "## Source reports\n"
        f"- `mechanical_report_path`: `{mechanical_report['report_relative_path']}`\n"
        f"- `semantic_report_path`: `{semantic_report['report_relative_path']}`\n\n"
        "## Source verdicts\n"
        f"- `mechanical_overall_verdict`: `{mechanical_report['overall_verdict']}`\n"
        f"- `semantic_overall_verdict`: `{semantic_report['overall_verdict']}`\n"
        f"- `semantic_confirmed_case_count`: `{confirmed_case_count(semantic_report)}`\n\n"
        "## Mechanical source report\n"
        f"{source_report_heading_level_demote(mechanical_report['report_text'])}\n"
        "## Semantic source report\n"
        f"{source_report_heading_level_demote(semantic_report['report_text'])}\n"
        "## Verdict\n"
        f"- `overall_verdict`: "
        f"`{_merged_overall_verdict_get(mechanical_report['overall_verdict'], semantic_report['overall_verdict'])}`\n"
    )
    merged_path.write_text(merged_text, encoding="utf-8")
    print(merged_relpath)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
