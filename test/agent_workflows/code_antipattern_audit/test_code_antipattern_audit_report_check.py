"""Test contracts for `plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py`."""

from __future__ import annotations

from pathlib import Path

from lib.antipattern_check_helpers import (
    MECHANICAL_REPORT_RELPATH,
    SEMANTIC_REPORT_RELPATH,
    VALID_REPORT_SCOPE,
    repo_tool_run,
    temporary_repo_file_create,
    valid_mechanical_report,
    valid_semantic_report,
)


def test_report_check_accepts_valid_source_reports(tmp_path: Path) -> None:
    """Validator must accept one valid mechanical report and one valid semantic report.

    Args:
        tmp_path: Per-test temporary report root.
    """

    with (
        temporary_repo_file_create(
            repo_root=tmp_path,
            relpath=MECHANICAL_REPORT_RELPATH,
            content=valid_mechanical_report(),
        ),
        temporary_repo_file_create(
            repo_root=tmp_path,
            relpath=SEMANTIC_REPORT_RELPATH,
            content=valid_semantic_report(),
        ),
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py",
            "--expected-scope",
            VALID_REPORT_SCOPE,
            MECHANICAL_REPORT_RELPATH,
            SEMANTIC_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 0, result.stderr
    assert f"PASS: {MECHANICAL_REPORT_RELPATH}" in result.stdout
    assert f"PASS: {SEMANTIC_REPORT_RELPATH}" in result.stdout


def test_report_check_accepts_mechanical_report_rendered_from_owner_template(tmp_path: Path) -> None:
    """Validator must accept a mechanical report rendered from the owner template scaffold.

    Args:
        tmp_path: Per-test temporary report root.
    """

    with temporary_repo_file_create(
        repo_root=tmp_path,
        relpath=MECHANICAL_REPORT_RELPATH,
        content=valid_mechanical_report(),
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py",
            "--expected-scope",
            VALID_REPORT_SCOPE,
            MECHANICAL_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 0, result.stderr
    assert f"PASS: {MECHANICAL_REPORT_RELPATH}" in result.stdout


def test_report_check_rejects_scope_mismatch(tmp_path: Path) -> None:
    """Validator must reject a source report whose scope differs from the declared scope.

    Args:
        tmp_path: Per-test temporary report root.
    """

    with temporary_repo_file_create(
        repo_root=tmp_path,
        relpath=MECHANICAL_REPORT_RELPATH,
        content=valid_mechanical_report(),
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py",
            "--expected-scope",
            "script/other",
            MECHANICAL_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 1
    assert f"FAIL: {MECHANICAL_REPORT_RELPATH}" in result.stderr
    assert "`scope` mismatch" in result.stderr


def test_report_check_rejects_missing_required_section(tmp_path: Path) -> None:
    """Validator must reject a malformed report that omits one required section.

    Args:
        tmp_path: Per-test temporary report root.
    """

    malformed = valid_semantic_report().replace("\n## Rejected signals\n- None\n", "\n")
    with temporary_repo_file_create(
        repo_root=tmp_path,
        relpath=SEMANTIC_REPORT_RELPATH,
        content=malformed,
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py",
            "--expected-scope",
            VALID_REPORT_SCOPE,
            SEMANTIC_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 1
    assert f"FAIL: {SEMANTIC_REPORT_RELPATH}" in result.stderr
    assert "section order mismatch" in result.stderr


def test_report_check_rejects_clean_verdict_with_confirmed_cases(tmp_path: Path) -> None:
    """Validator must reject one clean verdict that still reports confirmed cases.

    Args:
        tmp_path: Per-test temporary report root.
    """

    malformed = valid_semantic_report().replace(
        "- `overall_verdict`: `FINDINGS`",
        "- `overall_verdict`: `CLEAN`",
    )
    with temporary_repo_file_create(
        repo_root=tmp_path,
        relpath=SEMANTIC_REPORT_RELPATH,
        content=malformed,
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py",
            "--expected-scope",
            VALID_REPORT_SCOPE,
            SEMANTIC_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 1
    assert "`overall_verdict` CLEAN is inconsistent" in result.stderr


def test_report_check_rejects_inconsistent_mechanical_verdict(tmp_path: Path) -> None:
    """Validator must reject a verdict that contradicts the mechanical status.

    Args:
        tmp_path: Per-test temporary report root.
    """

    malformed = valid_mechanical_report().replace(
        "- `overall_verdict`: `CLEAN`",
        "- `overall_verdict`: `FINDINGS`",
    )
    with temporary_repo_file_create(
        repo_root=tmp_path,
        relpath=MECHANICAL_REPORT_RELPATH,
        content=malformed,
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py",
            "--expected-scope",
            VALID_REPORT_SCOPE,
            MECHANICAL_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 1
    assert "is inconsistent with mechanical status" in result.stderr


def test_report_check_rejects_non_numeric_mechanical_count(tmp_path: Path) -> None:
    """Validator must reject a non-numeric mechanical checker count.

    Args:
        tmp_path: Per-test temporary report root.
    """

    malformed = valid_mechanical_report().replace(
        "- `mechanical_checker_count`: `3`",
        "- `mechanical_checker_count`: `three`",
    )
    with temporary_repo_file_create(
        repo_root=tmp_path,
        relpath=MECHANICAL_REPORT_RELPATH,
        content=malformed,
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py",
            "--expected-scope",
            VALID_REPORT_SCOPE,
            MECHANICAL_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 1
    assert "`mechanical_checker_count` must be one non-negative integer" in result.stderr
