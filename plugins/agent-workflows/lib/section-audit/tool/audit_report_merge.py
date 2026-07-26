#!/usr/bin/env python3
"""Merge validated section audit results into one deterministic report body."""

from __future__ import annotations

import argparse
from pathlib import Path

from lib.audit_contract import (
    ROOT,
    report_path_get,
    section_result_findings_get,
    section_result_requirement_line_list_get,
    section_result_section_get,
)


def _args_parse() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed command-line namespace.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-name", required=True)
    parser.add_argument("--mechanical-evidence", action="append", dest="mechanical_evidence_list", required=True)
    parser.add_argument("--mechanical-status", choices=("clean", "error", "finding"), required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scope-entry", action="append", dest="scope_entry_list", required=True)
    parser.add_argument("--scope-mode", choices=("default-changed", "explicit"), required=True)
    parser.add_argument("section_result_path_list", nargs="+", type=Path)
    return parser.parse_args()


def main() -> int:
    """Merge section results in caller-supplied canonical order.

    Returns:
        Zero after writing the merged report.
    """

    args = _args_parse()
    try:
        output_path = report_path_get(args.output, audit_name=args.audit_name)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    result_path_list = [path if path.is_absolute() else ROOT / path for path in args.section_result_path_list]
    title = " ".join(word.capitalize() for word in args.audit_name.split("-"))
    line_list = [
        f"# {title} Report",
        "",
        "## Scope",
        f"- Scope mode: {args.scope_mode}",
        *[f"- Scope entry: {entry}" for entry in args.scope_entry_list],
        "",
        "## Mechanical Verification",
        f"- Status: {args.mechanical_status.upper()}",
        *[f"- Evidence: {evidence}" for evidence in args.mechanical_evidence_list],
        "",
        "## Section Results",
    ]
    have_finding = False
    for result_path in result_path_list:
        finding_line_list = section_result_findings_get(result_path)
        have_finding = have_finding or finding_line_list != ["- None"]
        requirement_line_list = [
            f"##{line}" if line.startswith("### ") else line
            for line in section_result_requirement_line_list_get(result_path)
        ]
        line_list.extend(
            [
                "",
                f"### {section_result_section_get(result_path)}",
                "#### Requirement Results",
                *requirement_line_list,
                "#### Findings",
                *finding_line_list,
            ]
        )
    if args.mechanical_status == "error":
        verdict = "ERROR"
    elif args.mechanical_status == "finding" or have_finding:
        verdict = "FINDINGS"
    else:
        verdict = "CLEAN"
    line_list.extend(["", "## Verdict", f"- Status: {verdict}", ""])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(line_list), encoding="utf-8")
    print(output_path.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
