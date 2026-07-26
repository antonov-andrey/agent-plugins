"""Shared report contracts for provider-owned section audit workflows."""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path, PurePosixPath
import re
from uuid import UUID

AUDIT_ROOT_ENV_NAME = "AGENT_WORKFLOWS_AUDIT_ROOT"
ROOT = Path(os.environ.get(AUDIT_ROOT_ENV_NAME, str(Path.cwd()))).resolve()
SEVERITY_RE = re.compile(r"^- (High|Medium|Low): .+$")


def _audit_title_get(audit_name: str) -> str:
    """Return one report title from a canonical audit name.

    Args:
        audit_name: Lowercase hyphenated audit identifier.

    Returns:
        Human-readable report title.
    """

    return " ".join(word.capitalize() for word in audit_name.split("-"))


def _metadata_value_get(line_list: Sequence[str], key: str) -> str | None:
    """Return one exact metadata bullet value.

    Args:
        line_list: Candidate report lines.
        key: Metadata label.

    Returns:
        Stripped value when present.
    """

    prefix = f"- {key}: "
    for line in line_list:
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def report_error_list_get(
    report_path: Path,
    *,
    audit_name: str,
    expected_scope_entry_list: Sequence[str],
    expected_scope_mode: str,
) -> list[str]:
    """Validate one final merged audit report.

    Args:
        report_path: Absolute or target-repository-relative report path.
        audit_name: Canonical audit identifier.
        expected_scope_entry_list: Ordered declared scope entries.
        expected_scope_mode: Declared scope mode.

    Returns:
        Collected contract errors.
    """

    try:
        path = report_path_get(report_path, audit_name=audit_name)
    except ValueError as exc:
        return [str(exc)]
    error_list: list[str] = []
    if not path.is_file():
        error_list.append("report file does not exist")
        return error_list
    line_list = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not line_list or line_list[0] != f"# {_audit_title_get(audit_name)} Report":
        return [*error_list, "report heading mismatch"]
    if _metadata_value_get(line_list, "Scope mode") != expected_scope_mode:
        error_list.append("scope mode mismatch")
    scope_entry_list = [
        line[len("- Scope entry: ") :].strip() for line in line_list if line.startswith("- Scope entry: ")
    ]
    if scope_entry_list != list(expected_scope_entry_list):
        error_list.append("scope entries mismatch")
    try:
        mechanical_index = line_list.index("## Mechanical Verification")
        section_result_index = line_list.index("## Section Results")
    except ValueError:
        error_list.append("mechanical verification or section results heading is missing")
        return error_list
    if mechanical_index >= section_result_index:
        error_list.append("mechanical verification must precede section results")
        return error_list
    mechanical_line_list = line_list[mechanical_index + 1 : section_result_index]
    mechanical_status = _metadata_value_get(mechanical_line_list, "Status")
    if mechanical_status not in {"CLEAN", "ERROR", "FINDING"}:
        error_list.append("mechanical status must be CLEAN, FINDING, or ERROR")
    if not any(line.startswith("- Evidence: ") and line != "- Evidence: " for line in mechanical_line_list):
        error_list.append("mechanical verification requires concrete evidence")
    try:
        verdict_index = line_list.index("## Verdict")
    except ValueError:
        error_list.append("verdict heading is missing")
        return error_list
    if section_result_index >= verdict_index:
        error_list.append("section results must precede the verdict")
        return error_list
    status = _metadata_value_get(line_list[verdict_index + 1 :], "Status")
    if status not in {"CLEAN", "ERROR", "FINDINGS"}:
        error_list.append("verdict status must be CLEAN, FINDINGS, or ERROR")
    have_finding = any(SEVERITY_RE.fullmatch(line) for line in line_list[section_result_index + 1 : verdict_index])
    if mechanical_status == "ERROR":
        expected_status = "ERROR"
    elif mechanical_status == "FINDING" or have_finding:
        expected_status = "FINDINGS"
    else:
        expected_status = "CLEAN"
    if status != expected_status:
        error_list.append(f"verdict status {status!r} is inconsistent with mechanical status and semantic findings")
    return error_list


def report_path_get(report_path: Path, *, audit_name: str) -> Path:
    """Return one validated canonical final-report path.

    Args:
        report_path: Absolute or target-repository-relative report path.
        audit_name: Canonical audit identifier.

    Returns:
        Validated absolute report path.

    Raises:
        ValueError: The path is outside the canonical report family.
    """

    path = report_path if report_path.is_absolute() else ROOT / report_path
    expected_name_re = re.compile(
        rf"^{re.escape(audit_name)}-[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-"
        rf"[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}\.md$"
    )
    if path.parent != ROOT / "tmp" or expected_name_re.fullmatch(path.name) is None:
        raise ValueError(f"report path must match tmp/{audit_name}-<uuid>.md")
    return path


def _finding_error_list_get(line_list: Sequence[str]) -> list[str]:
    """Validate canonical finding entries.

    Args:
        line_list: Nonblank lines below one Findings heading.

    Returns:
        Collected contract errors.
    """

    if not line_list:
        return ["findings section is empty"]
    if list(line_list) == ["- None"]:
        return []
    error_list: list[str] = []
    index = 0
    while index < len(line_list):
        if SEVERITY_RE.fullmatch(line_list[index]) is None:
            error_list.append(f"invalid problem line: {line_list[index]!r}")
            break
        if index + 1 >= len(line_list) or not line_list[index + 1].startswith("  Fix: "):
            error_list.append("every problem must include one indented Fix line")
            break
        if index + 2 >= len(line_list) or not line_list[index + 2].startswith("  Path: "):
            error_list.append("every problem must include one indented Path line")
            break
        path_text = line_list[index + 2][8:].strip()
        path = PurePosixPath(path_text)
        if not path_text or path.is_absolute() or ".." in path.parts:
            error_list.append(f"problem Path must be repository-relative: {path_text!r}")
            break
        index += 3
    return error_list


def _requirement_result_error_list_get(
    line_list: Sequence[str],
    expected_requirement_list: Sequence[str],
) -> list[str]:
    """Validate exhaustive requirement-result blocks.

    Args:
        line_list: Nonblank lines between requirement and findings headings.
        expected_requirement_list: Complete ordered assigned requirement inventory.

    Returns:
        Collected requirement-result contract errors.
    """

    error_list: list[str] = []
    block_line_list: list[list[str]] = []
    current_block_line_list: list[str] = []
    for line in line_list:
        if line.startswith("### "):
            if current_block_line_list:
                block_line_list.append(current_block_line_list)
            current_block_line_list = [line]
        elif current_block_line_list:
            current_block_line_list.append(line)
        else:
            error_list.append(f"requirement result content precedes its heading: {line!r}")
    if current_block_line_list:
        block_line_list.append(current_block_line_list)
    actual_requirement_list = [
        requirement_block_line_list[0][4:].strip() for requirement_block_line_list in block_line_list
    ]
    if actual_requirement_list != list(expected_requirement_list):
        error_list.append(
            f"requirement coverage mismatch: expected {list(expected_requirement_list)!r}, "
            f"got {actual_requirement_list!r}"
        )
    for requirement_block_line_list in block_line_list:
        requirement = requirement_block_line_list[0][4:].strip()
        if len(requirement_block_line_list) != 4:
            error_list.append(f"{requirement!r}: requirement result must contain exactly three metadata bullets")
            continue
        status = _metadata_value_get(requirement_block_line_list[1:], "Status")
        evidence = _metadata_value_get(requirement_block_line_list[1:], "Evidence")
        not_applicable_reason = _metadata_value_get(requirement_block_line_list[1:], "Not applicable reason")
        if status not in {"Not applicable", "Problems", "Satisfied"}:
            error_list.append(f"{requirement!r}: invalid requirement status {status!r}")
            continue
        if not evidence or evidence == "None":
            error_list.append(f"{requirement!r}: current evidence is required")
        if status == "Not applicable":
            if not not_applicable_reason or not_applicable_reason == "None":
                error_list.append(f"{requirement!r}: not-applicable status requires one concrete reason")
        elif not_applicable_reason != "None":
            error_list.append(f"{requirement!r}: applicable status requires `Not applicable reason: None`")
    return error_list


def section_result_error_list_get(
    result_path: Path,
    *,
    audit_name: str,
    expected_requirement_list: Sequence[str],
    expected_scope: str,
    expected_section: str,
) -> list[str]:
    """Validate one section-agent result artifact.

    Args:
        result_path: Absolute or target-repository-relative result path.
        audit_name: Canonical audit identifier.
        expected_requirement_list: Complete ordered assigned requirement inventory.
        expected_scope: Exact assigned scope.
        expected_section: Exact assigned checklist section.

    Returns:
        Collected contract errors.
    """

    path = result_path if result_path.is_absolute() else ROOT / result_path
    error_list: list[str] = []
    try:
        relative_path = path.relative_to(ROOT)
    except ValueError:
        return ["section result path is outside target repository"]
    if len(relative_path.parts) != 4 or relative_path.parts[:2] != ("tmp", audit_name):
        error_list.append(f"section result path must stay under tmp/{audit_name}/<run_uuid>/")
    elif not relative_path.name.endswith(".result.md"):
        error_list.append("section result filename must end with .result.md")
    else:
        try:
            UUID(relative_path.parts[2])
        except ValueError:
            error_list.append("section result run directory must be one UUID")
    if not path.is_file():
        error_list.append("section result file does not exist")
        return error_list
    line_list = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not line_list or line_list[0] != "# Audit Section Result":
        return [*error_list, "section result heading mismatch"]
    if _metadata_value_get(line_list, "Audit") != audit_name:
        error_list.append("audit name mismatch")
    if _metadata_value_get(line_list, "Section") != expected_section:
        error_list.append("section name mismatch")
    if _metadata_value_get(line_list, "Scope") != expected_scope:
        error_list.append("scope mismatch")
    try:
        requirement_index = line_list.index("## Requirement Results")
        findings_index = line_list.index("## Findings")
    except ValueError:
        error_list.append("requirement results or findings heading is missing")
        return error_list
    if requirement_index >= findings_index:
        error_list.append("requirement results must precede findings")
        return error_list
    requirement_line_list = line_list[requirement_index + 1 : findings_index]
    requirement_error_list = _requirement_result_error_list_get(
        requirement_line_list,
        expected_requirement_list,
    )
    error_list.extend(requirement_error_list)
    finding_line_list = line_list[findings_index + 1 :]
    have_problem = "- Status: Problems" in requirement_line_list
    have_finding = finding_line_list != ["- None"]
    if have_problem != have_finding:
        error_list.append("requirement problem statuses and findings presence disagree")
    error_list.extend(_finding_error_list_get(finding_line_list))
    return error_list


def section_result_findings_get(result_path: Path) -> list[str]:
    """Return canonical finding lines from one validated section result.

    Args:
        result_path: Absolute section result path.

    Returns:
        Finding lines below the Findings heading.
    """

    line_list = [line for line in result_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return line_list[line_list.index("## Findings") + 1 :]


def section_result_requirement_line_list_get(result_path: Path) -> list[str]:
    """Return canonical requirement-result lines from one validated section result.

    Args:
        result_path: Absolute section result path.

    Returns:
        Requirement-result lines below the owning heading.
    """

    line_list = [line for line in result_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    start = line_list.index("## Requirement Results") + 1
    return line_list[start : line_list.index("## Findings")]


def section_result_section_get(result_path: Path) -> str:
    """Return the declared section from one validated section result.

    Args:
        result_path: Absolute section result path.

    Returns:
        Declared section name.
    """

    line_list = [line for line in result_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    section_name = _metadata_value_get(line_list, "Section")
    if section_name is None:
        raise ValueError("section result has no Section metadata")
    return section_name
