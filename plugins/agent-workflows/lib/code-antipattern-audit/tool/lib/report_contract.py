"""Shared report-contract helpers for `code-antipattern-audit` tools."""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
import re
from typing import TypedDict

ALLOWED_OVERALL_VERDICT_SET = {"CLEAN", "ERROR", "FINDINGS", "NO_AUDITABLE_SCOPE"}
FILENAME_RE_BY_KIND_MAP = {
    "mechanical": re.compile(
        r"^tmp/code-antipattern-audit-mechanical-(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.md$"
    ),
    "semantic": re.compile(
        r"^tmp/code-antipattern-audit-semantic-(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.md$"
    ),
}
KEYED_BULLET_RE = re.compile(r"^- `(?P<key>[^`]+)`: (?P<value>.+)$")
PLUGIN_ROOT = Path(__file__).resolve().parents[4]
MECHANICAL_TEMPLATE_PATH = PLUGIN_ROOT / "lib" / "code-antipattern-audit" / "template" / "mechanical.md"
REPORT_ROOT_OVERRIDE_ENV_NAME = "CODE_ANTIPATTERN_AUDIT_REPORT_ROOT"
ROOT = Path(os.environ.get(REPORT_ROOT_OVERRIDE_ENV_NAME, str(Path.cwd()))).resolve()
SEMANTIC_TEMPLATE_PATH = PLUGIN_ROOT / "lib" / "code-antipattern-audit" / "template" / "semantic.md"
TEMPLATE_PATH_BY_KIND_MAP = {
    "mechanical": MECHANICAL_TEMPLATE_PATH,
    "semantic": SEMANTIC_TEMPLATE_PATH,
}


def _nonempty_bullet_list_collect(section_line_sequence: Sequence[str]) -> list[str]:
    """Return non-empty bullet lines from one section.

    Args:
        section_line_sequence: Section lines.

    Returns:
        Non-empty bullet lines.
    """

    return [line.strip() for line in section_line_sequence if line.strip()]


def _non_none_bullet_count(section_line_sequence: Sequence[str]) -> int:
    """Count bullet entries other than the canonical empty marker.

    Args:
        section_line_sequence: Section lines.

    Returns:
        Count of concrete bullet entries.
    """

    return sum(line != "- None" for line in _nonempty_bullet_list_collect(section_line_sequence))


def confirmed_case_count(parsed_report: ParsedReport) -> int:
    """Count non-empty confirmed-case bullets in one parsed report.

    Args:
        parsed_report: Parsed report.

    Returns:
        Count of non-`None` confirmed-case bullets.
    """

    return _non_none_bullet_count(
        parsed_report["section_line_list_by_name_map"].get("Confirmed anti-pattern cases", ())
    )


def _report_kind_get(relpath: str) -> str | None:
    """Infer report kind from repository-relative path.

    Args:
        relpath: Repository-relative report path.

    Returns:
        Report kind or `None`.
    """

    for kind, pattern in FILENAME_RE_BY_KIND_MAP.items():
        if pattern.fullmatch(relpath):
            return kind
    return None


def _report_uuid_get(relpath: str) -> str | None:
    """Extract report UUID from repository-relative path.

    Args:
        relpath: Repository-relative report path.

    Returns:
        UUID string or `None`.
    """

    kind = _report_kind_get(relpath)
    if kind is None:
        return None
    match = FILENAME_RE_BY_KIND_MAP[kind].fullmatch(relpath)
    if match is None:
        return None
    return match.group("uuid")


def merged_report_relpath_build(mechanical_relpath: str, semantic_relpath: str) -> str:
    """Build deterministic merged-report path from two validated source reports.

    Args:
        mechanical_relpath: Repository-relative mechanical report path.
        semantic_relpath: Repository-relative semantic report path.

    Returns:
        Deterministic merged-report path.
    """

    mechanical_uuid = _report_uuid_get(mechanical_relpath)
    semantic_uuid = _report_uuid_get(semantic_relpath)
    if mechanical_uuid is None or semantic_uuid is None:
        raise ValueError("source report paths do not match canonical anti-pattern report families")
    return f"tmp/code-antipattern-audit-merged-{mechanical_uuid}-{semantic_uuid}.md"


def _backtick_wrapped_text_strip(value: str) -> str:
    """Strip one pair of wrapping backticks when present.

    Args:
        value: Raw keyed-bullet value text.

    Returns:
        Unwrapped value.
    """

    normalized = value.strip()
    if normalized.startswith("`") and normalized.endswith("`") and len(normalized) >= 2:
        return normalized[1:-1]
    return normalized


def _keyed_section_value_map_build(section_line_sequence: Sequence[str]) -> dict[str, str]:
    """Parse keyed bullet values from one section.

    Args:
        section_line_sequence: Lines inside one markdown section.

    Returns:
        Mapping from key name to unwrapped value.
    """

    value_by_key_map: dict[str, str] = {}
    for line in section_line_sequence:
        match = KEYED_BULLET_RE.match(line.strip())
        if match is None:
            continue
        value_by_key_map[match.group("key")] = _backtick_wrapped_text_strip(match.group("value"))
    return value_by_key_map


def _root_relative_get(path: Path) -> str:
    """Return repository-relative POSIX path.

    Args:
        path: Path inside repository root.

    Returns:
        Repository-relative POSIX path.
    """

    return path.resolve().relative_to(ROOT).as_posix()


def _section_parse_result_build(text: str) -> SectionParseResult:
    """Parse level-2 markdown section_map from one report or template.

    Args:
        text: Markdown text.

    Returns:
        Parsed section result.
    """

    section_line_list_by_name_map: dict[str, list[str]] = {}
    section_name_list: list[str] = []
    current_section_name: str | None = None
    for raw_line in text.splitlines():
        if raw_line.startswith("## "):
            current_section_name = raw_line[3:].strip()
            section_name_list.append(current_section_name)
            section_line_list_by_name_map[current_section_name] = []
            continue
        if current_section_name is not None:
            section_line_list_by_name_map[current_section_name].append(raw_line)
    return SectionParseResult(
        section_line_list_by_name_map=section_line_list_by_name_map,
        section_name_list=section_name_list,
    )


def parsed_report_build(path: Path) -> ParsedReport:
    """Parse one anti-pattern report without applying the full validator.

    Args:
        path: Absolute report path.

    Returns:
        Parsed report payload.
    """

    relpath = _root_relative_get(path)
    kind = _report_kind_get(relpath)
    if kind is None:
        raise ValueError(f"path does not match a canonical anti-pattern report family: {relpath}")
    text = path.read_text(encoding="utf-8")
    section_parse_result = _section_parse_result_build(text)
    scope_value_by_key_map = _keyed_section_value_map_build(
        section_parse_result["section_line_list_by_name_map"].get("Scope", ())
    )
    verdict_value_by_key_map = _keyed_section_value_map_build(
        section_parse_result["section_line_list_by_name_map"].get("Verdict", ())
    )
    return ParsedReport(
        audit_scope=scope_value_by_key_map.get("scope", ""),
        overall_verdict=verdict_value_by_key_map.get("overall_verdict", ""),
        report_kind=kind,
        report_relative_path=relpath,
        report_text=text,
        section_line_list_by_name_map=section_parse_result["section_line_list_by_name_map"],
        section_name_list=section_parse_result["section_name_list"],
    )


def _required_section_order_get(kind: str) -> list[str]:
    """Load canonical section order from the selected template.

    Args:
        kind: Report kind.

    Returns:
        Ordered required section names.
    """

    template_path = TEMPLATE_PATH_BY_KIND_MAP[kind]
    section_parse_result = _section_parse_result_build(template_path.read_text(encoding="utf-8"))
    return list(section_parse_result["section_name_list"])


def report_contract_error_list_collect(path: Path, *, expected_scope: str) -> list[str]:
    """Validate one anti-pattern source-report contract.

    Args:
        path: Absolute report path.
        expected_scope: Required normalized declared scope.

    Returns:
        Collected validation errors.
    """

    error_list: list[str] = []
    if not path.is_file():
        return [f"report path does not exist: {path.as_posix()}"]

    try:
        relpath = _root_relative_get(path)
    except ValueError:
        return [f"report path is outside repository root: {path.as_posix()}"]

    kind = _report_kind_get(relpath)
    if kind is None:
        return [f"report path does not match a canonical anti-pattern report family: {relpath}"]

    parsed_report = parsed_report_build(path)
    if "<fill " in parsed_report["report_text"]:
        error_list.append("report still contains unfilled template placeholders")

    expected_section_name_list = _required_section_order_get(kind)
    if parsed_report["section_name_list"] != expected_section_name_list:
        error_list.append(
            f"section order mismatch: expected {list(expected_section_name_list)}, "
            f"got {list(parsed_report['section_name_list'])}"
        )

    scope_value_by_key_map = _keyed_section_value_map_build(
        parsed_report["section_line_list_by_name_map"].get("Scope", ())
    )
    metadata_value_by_key_map = _keyed_section_value_map_build(
        parsed_report["section_line_list_by_name_map"].get("Report metadata", ())
    )
    verdict_value_by_key_map = _keyed_section_value_map_build(
        parsed_report["section_line_list_by_name_map"].get("Verdict", ())
    )

    scope = scope_value_by_key_map.get("scope")
    if not scope:
        error_list.append("missing `scope` in `## Scope`")
    elif scope != expected_scope:
        error_list.append(f"`scope` mismatch: expected {expected_scope!r}, got {scope!r}")

    metadata_report_uuid = metadata_value_by_key_map.get("report_uuid")
    if not metadata_report_uuid:
        error_list.append("missing `report_uuid` in `## Report metadata`")
    else:
        path_uuid = _report_uuid_get(relpath)
        if path_uuid is not None and metadata_report_uuid != path_uuid:
            error_list.append(f"`report_uuid` mismatch: expected path UUID {path_uuid!r}, got {metadata_report_uuid!r}")

    report_path = metadata_value_by_key_map.get("report_path")
    if not report_path:
        error_list.append("missing `report_path` in `## Report metadata`")
    elif report_path != relpath:
        error_list.append(f"`report_path` mismatch: expected {relpath!r}, got {report_path!r}")

    overall_verdict = verdict_value_by_key_map.get("overall_verdict")
    if overall_verdict not in ALLOWED_OVERALL_VERDICT_SET:
        error_list.append(
            f"`overall_verdict` must be one of {sorted(ALLOWED_OVERALL_VERDICT_SET)}, got {overall_verdict!r}"
        )

    for section_name, section_line_list in parsed_report["section_line_list_by_name_map"].items():
        if section_name in {"Scope", "Report metadata", "Verdict"}:
            continue
        if not _nonempty_bullet_list_collect(section_line_list):
            error_list.append(f"`## {section_name}` is empty")

    if kind == "mechanical":
        mechanical_scope_value_by_key_map = _keyed_section_value_map_build(
            parsed_report["section_line_list_by_name_map"].get("Mechanical scope", ())
        )
        mechanical_result_value_by_key_map = _keyed_section_value_map_build(
            parsed_report["section_line_list_by_name_map"].get("Mechanical result", ())
        )
        if mechanical_scope_value_by_key_map.get("project_standard_check_scope") != "all":
            error_list.append("`project_standard_check_scope` must be `all`")
        if mechanical_result_value_by_key_map.get("semantic_audit_required") != "true":
            error_list.append("`semantic_audit_required` must be `true`")
        mechanical_status = mechanical_result_value_by_key_map.get("mechanical_status")
        expected_verdict_by_status_map = {
            "clean": "CLEAN",
            "error": "ERROR",
            "finding": "FINDINGS",
        }
        expected_exit_status_by_status_map = {
            "clean": "0",
            "error": "2",
            "finding": "1",
        }
        if mechanical_status not in expected_verdict_by_status_map:
            error_list.append("`mechanical_status` must be one of `clean`, `finding`, or `error`")
        else:
            if overall_verdict != expected_verdict_by_status_map[mechanical_status]:
                error_list.append(
                    f"`overall_verdict` {overall_verdict!r} is inconsistent with mechanical status "
                    f"{mechanical_status!r}"
                )
            if (
                mechanical_result_value_by_key_map.get("command_exit_status")
                != expected_exit_status_by_status_map[mechanical_status]
            ):
                error_list.append(f"`command_exit_status` is inconsistent with mechanical status {mechanical_status!r}")
        for numeric_key in ("command_exit_status", "mechanical_checker_count"):
            value = mechanical_result_value_by_key_map.get(numeric_key, "")
            if not value.isdigit():
                error_list.append(f"`{numeric_key}` must be one non-negative integer")
        finding_count = _non_none_bullet_count(
            parsed_report["section_line_list_by_name_map"].get("Mechanical findings", ())
        )
        error_count = _non_none_bullet_count(
            parsed_report["section_line_list_by_name_map"].get("Mechanical errors", ())
        )
        if mechanical_status == "clean" and (finding_count or error_count):
            error_list.append("mechanical status `clean` requires empty findings and errors")
        if mechanical_status == "finding" and (finding_count == 0 or error_count):
            error_list.append("mechanical status `finding` requires findings and no errors")
        if mechanical_status == "error" and error_count == 0:
            error_list.append("mechanical status `error` requires at least one error")
    else:
        case_count = confirmed_case_count(parsed_report)
        if overall_verdict == "CLEAN" and case_count > 0:
            error_list.append("`overall_verdict` CLEAN is inconsistent with non-empty `Confirmed anti-pattern cases`")
        if overall_verdict == "FINDINGS" and case_count == 0:
            error_list.append("`overall_verdict` FINDINGS is inconsistent with empty `Confirmed anti-pattern cases`")
        if overall_verdict == "NO_AUDITABLE_SCOPE" and case_count > 0:
            error_list.append(
                "`overall_verdict` NO_AUDITABLE_SCOPE is inconsistent with non-empty `Confirmed anti-pattern cases`"
            )
        if overall_verdict == "ERROR":
            error_list.append("semantic `overall_verdict` must not be `ERROR`")

    return error_list


def source_report_heading_level_demote(text: str) -> str:
    """Embed one source report under a merged report by demoting section headings.

    Args:
        text: Full source report text.

    Returns:
        Embedded markdown without the top-level report heading.
    """

    body_line_list: list[str] = []
    line_list = text.splitlines()
    skipping_lead = True
    for line in line_list:
        if skipping_lead and line.startswith("# "):
            continue
        if skipping_lead and line == "":
            continue
        skipping_lead = False
        if line.startswith("## "):
            body_line_list.append(f"### {line[3:]}")
            continue
        body_line_list.append(line)
    return "\n".join(body_line_list).rstrip() + "\n"


class ParsedReport(TypedDict):
    """Define one parsed anti-pattern audit report mapping."""

    audit_scope: str
    overall_verdict: str
    report_kind: str
    report_relative_path: str
    report_text: str
    section_line_list_by_name_map: dict[str, list[str]]
    section_name_list: list[str]


class SectionParseResult(TypedDict):
    """Define one parsed level-2 Markdown section mapping."""

    section_line_list_by_name_map: dict[str, list[str]]
    section_name_list: list[str]
