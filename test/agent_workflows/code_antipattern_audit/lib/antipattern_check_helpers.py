"""Shared helpers for anti-pattern audit checker tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = ROOT / "plugins" / "agent-workflows"
MECHANICAL_TEMPLATE_PATH = PLUGIN_ROOT / "lib" / "code-antipattern-audit" / "template" / "mechanical.md"
SEMANTIC_TEMPLATE_PATH = PLUGIN_ROOT / "lib" / "code-antipattern-audit" / "template" / "semantic.md"
VALID_REPORT_SCOPE = "script/demo"
MECHANICAL_REPORT_UUID = "00000000-0000-0000-0000-000000000001"
SEMANTIC_REPORT_UUID = "00000000-0000-0000-0000-000000000002"
MECHANICAL_REPORT_RELPATH = f"tmp/code-antipattern-audit-mechanical-{MECHANICAL_REPORT_UUID}.md"
SEMANTIC_REPORT_RELPATH = f"tmp/code-antipattern-audit-semantic-{SEMANTIC_REPORT_UUID}.md"
REPORT_ROOT_OVERRIDE_ENV_NAME = "CODE_ANTIPATTERN_AUDIT_REPORT_ROOT"


def repo_tool_run(tool_relpath: str, *args: str, report_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run one project Python tool script.

    Args:
        tool_relpath: Repository-relative script path.
        args: Additional CLI arguments.
        report_root: Optional runtime root override used by report tools.

    Returns:
        Completed process result.
    """

    env = os.environ.copy()
    if report_root is not None:
        env[REPORT_ROOT_OVERRIDE_ENV_NAME] = str(report_root.resolve())
    return subprocess.run(
        [sys.executable, tool_relpath, *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=env,
    )


@contextmanager
def temporary_repo_file_create(*, repo_root: Path, relpath: str, content: str) -> Iterator[Path]:
    """Create one temporary repository-root-relative file and remove it after use.

    Args:
        repo_root: Temporary runtime root that should own the created file.
        relpath: Repository-relative file path.
        content: File content to write.

    Returns:
        Iterator that yields the absolute created file path.
    """

    path = repo_root / relpath
    if path.exists():
        raise AssertionError(f"temporary_repo_file_create requires a missing path, got existing {relpath}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _keyed_bullet_value_replace(report: str, key: str, replacement_value: str) -> str:
    """Replace one keyed bullet value inside a template-derived report.

    Args:
        report: Full template report text.
        key: Literal keyed-bullet key.
        replacement_value: Replacement value text after the colon.

    Returns:
        Updated report text.
    """

    pattern = re.compile(
        rf"^(?P<prefix>- `{re.escape(key)}`: ).+$",
        flags=re.MULTILINE,
    )
    replacement = rf"\g<prefix>{replacement_value}"
    updated, count = pattern.subn(replacement, report, count=1)
    if count != 1:
        raise AssertionError(f"expected exactly one keyed bullet for {key!r} in template")
    return updated


def _section_bullet_replace(
    report: str,
    section_name: str,
    bullet_index: int,
    replacement_line_list: list[str],
) -> str:
    """Replace one bullet slot inside a named template section by bullet index.

    Args:
        report: Full template report text.
        section_name: Target section heading without leading hashes.
        bullet_index: Zero-based bullet index inside the section body.
        replacement_line_list: Final bullet lines that replace the targeted slot.

    Returns:
        Updated report text.
    """

    pattern = re.compile(
        rf"(^## {re.escape(section_name)}\n)(?P<body>.*?)(?=^## |\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(report)
    if match is None:
        raise AssertionError(f"expected exactly one section named {section_name!r} in template")
    body_lines = match.group("body").splitlines()
    bullet_positions = [index for index, line in enumerate(body_lines) if line.startswith("- ")]
    if bullet_index >= len(bullet_positions):
        raise AssertionError(f"expected bullet index {bullet_index} in section {section_name!r}")
    start = bullet_positions[bullet_index]
    end = bullet_positions[bullet_index + 1] if bullet_index + 1 < len(bullet_positions) else len(body_lines)
    new_body_lines = body_lines[:start] + replacement_line_list + body_lines[end:]
    replacement = match.group(1) + "\n".join(new_body_lines).rstrip() + "\n\n"
    return report[: match.start()] + replacement + report[match.end() :]


def valid_mechanical_report() -> str:
    """Build one valid mechanical source report.

    Returns:
        Canonical valid mechanical source-report text.
    """

    report = MECHANICAL_TEMPLATE_PATH.read_text(encoding="utf-8")
    report = _keyed_bullet_value_replace(report, "scope", f"`{VALID_REPORT_SCOPE}`")
    report = _keyed_bullet_value_replace(report, "report_uuid", f"`{MECHANICAL_REPORT_UUID}`")
    report = _keyed_bullet_value_replace(report, "report_path", f"`{MECHANICAL_REPORT_RELPATH}`")
    report = _keyed_bullet_value_replace(report, "project_root", "`/tmp/repository`")
    report = _keyed_bullet_value_replace(report, "command_exit_status", "`0`")
    report = _keyed_bullet_value_replace(report, "mechanical_status", "`clean`")
    report = _keyed_bullet_value_replace(report, "mechanical_checker_count", "`3`")
    report = _keyed_bullet_value_replace(report, "overall_verdict", "`CLEAN`")
    report = _section_bullet_replace(
        report,
        "Executed commands",
        0,
        ["- `project-standard-check --project-root /tmp/repository --scope all`"],
    )
    report = _section_bullet_replace(report, "Mechanical findings", 0, ["- None"])
    report = _section_bullet_replace(report, "Mechanical errors", 0, ["- None"])
    return report


def valid_semantic_report() -> str:
    """Build one valid semantic source report.

    Returns:
        Canonical valid semantic source-report text.
    """

    report = SEMANTIC_TEMPLATE_PATH.read_text(encoding="utf-8")
    report = _keyed_bullet_value_replace(report, "scope", f"`{VALID_REPORT_SCOPE}`")
    report = _keyed_bullet_value_replace(report, "report_uuid", f"`{SEMANTIC_REPORT_UUID}`")
    report = _keyed_bullet_value_replace(report, "report_path", f"`{SEMANTIC_REPORT_RELPATH}`")
    report = _keyed_bullet_value_replace(report, "overall_verdict", "`FINDINGS`")
    report = _section_bullet_replace(report, "Reviewed anti-pattern cards", 0, ["- `PRJ-10`"])
    report = _section_bullet_replace(
        report,
        "Collected signals",
        0,
        [
            "- anti-pattern id: `PRJ-10`; file path: `script/demo.py`; line: `10`; observed signal: `pass-through method`; scope expansion used: `None`"
        ],
    )
    report = _section_bullet_replace(report, "Rejected signals", 0, ["- None"])
    report = _section_bullet_replace(
        report,
        "Confirmed anti-pattern cases",
        0,
        [
            "- anti-pattern ids: `PRJ-10`; violated owner rule: `Main project code Rules`; file path: `script/demo.py`; line: `10`; observed evidence: `direct forwarding`; competing cards rejected: `BOOK-06`; exception status: `rejected`; scope expansion used: `None`; remediation direction: `delete the proxy`"
        ],
    )
    report = _section_bullet_replace(
        report,
        "Clean cards checked",
        0,
        ["- `PRJ-11`: inspected `script/demo.py` and found no confirmed case in the declared scope"],
    )
    return report
