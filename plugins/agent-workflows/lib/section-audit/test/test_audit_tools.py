"""Scenario tests for shared section-audit report tools."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

TOOL_ROOT = Path(__file__).resolve().parents[1] / "tool"


def _tool_run(
    script_name: str,
    argument_list: list[str],
    *,
    audit_root: Path,
) -> subprocess.CompletedProcess[str]:
    """Run one section-audit tool against an isolated target root.

    Args:
        script_name: Tool entrypoint filename.
        argument_list: Tool CLI arguments.
        audit_root: Target repository surrogate.

    Returns:
        Completed tool process.
    """

    env = os.environ.copy()
    env["AGENT_WORKFLOWS_AUDIT_ROOT"] = str(audit_root)
    return subprocess.run(
        [sys.executable, str(TOOL_ROOT / script_name), *argument_list],
        capture_output=True,
        check=False,
        cwd=audit_root,
        env=env,
        text=True,
    )


def test_section_results_merge_and_validate(tmp_path: Path) -> None:
    """Valid section results produce one valid deterministic report body.

    Args:
        tmp_path: Isolated target root.
    """

    run_uuid = "00000000-0000-0000-0000-000000000001"
    result_path = tmp_path / "tmp" / "code-audit" / run_uuid / "python.agent.result.md"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        "# Audit Section Result\n"
        "- Audit: code-audit\n"
        "- Section: Python\n"
        "- Scope: script/demo\n\n"
        "## Requirement Results\n"
        "### Python owner rule\n"
        "- Status: Problems\n"
        "- Evidence: script/demo.py: one current violation\n"
        "- Not applicable reason: None\n\n"
        "## Findings\n"
        "- High: One concrete problem.\n"
        "  Fix: Apply one concrete correction.\n"
        "  Path: script/demo.py\n",
        encoding="utf-8",
    )

    result_check = _tool_run(
        "audit_section_result_check.py",
        [
            "--audit-name",
            "code-audit",
            "--expected-requirement",
            "Python owner rule",
            "--expected-scope",
            "script/demo",
            "--expected-section",
            "Python",
            str(result_path),
        ],
        audit_root=tmp_path,
    )
    output_path = Path("tmp/code-audit-00000000-0000-0000-0000-000000000002.md")
    merge = _tool_run(
        "audit_report_merge.py",
        [
            "--audit-name",
            "code-audit",
            "--mechanical-status",
            "clean",
            "--mechanical-evidence",
            "project-standard-check --scope all exited 0 with mechanical_status=clean",
            "--output",
            str(output_path),
            "--scope-mode",
            "explicit",
            "--scope-entry",
            "script/demo",
            str(result_path),
        ],
        audit_root=tmp_path,
    )
    report_check = _tool_run(
        "audit_report_check.py",
        [
            "--audit-name",
            "code-audit",
            "--expected-scope-mode",
            "explicit",
            "--expected-scope-entry",
            "script/demo",
            str(output_path),
        ],
        audit_root=tmp_path,
    )

    assert result_check.returncode == 0, result_check.stderr
    assert merge.returncode == 0, merge.stderr
    assert UUID(run_uuid)
    assert merge.stdout.strip() == output_path.as_posix()
    assert report_check.returncode == 0, report_check.stderr
    report_text = (tmp_path / output_path).read_text(encoding="utf-8")
    assert "## Mechanical Verification" in report_text
    assert "##### Python owner rule" in report_text
    assert "- Status: Problems" in report_text


def test_section_result_rejects_problem_without_fix(tmp_path: Path) -> None:
    """Malformed problem entries fail before final report assembly.

    Args:
        tmp_path: Isolated target root.
    """

    result_path = (
        tmp_path / "tmp" / "instruction-audit" / "00000000-0000-0000-0000-000000000003" / "ownership.agent.result.md"
    )
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        "# Audit Section Result\n"
        "- Audit: instruction-audit\n"
        "- Section: Ownership\n"
        "- Scope: AGENTS.md\n\n"
        "## Requirement Results\n"
        "### Instruction ownership rule\n"
        "- Status: Problems\n"
        "- Evidence: AGENTS.md: one current violation\n"
        "- Not applicable reason: None\n\n"
        "## Findings\n"
        "- High: One problem without its required correction.\n",
        encoding="utf-8",
    )

    result = _tool_run(
        "audit_section_result_check.py",
        [
            "--audit-name",
            "instruction-audit",
            "--expected-requirement",
            "Instruction ownership rule",
            "--expected-scope",
            "AGENTS.md",
            "--expected-section",
            "Ownership",
            str(result_path),
        ],
        audit_root=tmp_path,
    )

    assert result.returncode == 1
    assert "Fix line" in result.stderr


def test_section_result_rejects_missing_requirement_coverage(tmp_path: Path) -> None:
    """A structurally valid subset cannot stand in for the assigned inventory.

    Args:
        tmp_path: Isolated target root.
    """

    result_path = (
        tmp_path / "tmp" / "instruction-audit" / "00000000-0000-0000-0000-000000000004" / "ownership.agent.result.md"
    )
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        "# Audit Section Result\n"
        "- Audit: instruction-audit\n"
        "- Section: Ownership\n"
        "- Scope: AGENTS.md\n\n"
        "## Requirement Results\n"
        "### First rule\n"
        "- Status: Satisfied\n"
        "- Evidence: AGENTS.md: inspected the current owner\n"
        "- Not applicable reason: None\n\n"
        "## Findings\n"
        "- None\n",
        encoding="utf-8",
    )

    result = _tool_run(
        "audit_section_result_check.py",
        [
            "--audit-name",
            "instruction-audit",
            "--expected-requirement",
            "First rule",
            "--expected-requirement",
            "Second rule",
            "--expected-scope",
            "AGENTS.md",
            "--expected-section",
            "Ownership",
            str(result_path),
        ],
        audit_root=tmp_path,
    )

    assert result.returncode == 1
    assert "requirement coverage mismatch" in result.stderr


def test_report_merge_rejects_noncanonical_output_path(tmp_path: Path) -> None:
    """The merger must not write outside its canonical final-report family.

    Args:
        tmp_path: Isolated target root.
    """

    result = _tool_run(
        "audit_report_merge.py",
        [
            "--audit-name",
            "code-audit",
            "--mechanical-status",
            "clean",
            "--mechanical-evidence",
            "project-standard-check --scope all exited 0 with mechanical_status=clean",
            "--output",
            "outside.md",
            "--scope-mode",
            "explicit",
            "--scope-entry",
            "script/demo",
            "missing.result.md",
        ],
        audit_root=tmp_path,
    )

    assert result.returncode == 1
    assert "report path must match tmp/code-audit-<uuid>.md" in result.stderr
    assert not (tmp_path / "outside.md").exists()
