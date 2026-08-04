"""Test contracts for `plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_merge.py`."""

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


def test_report_merge_creates_deterministic_merged_report(tmp_path: Path) -> None:
    """Merger must create one deterministic merged report from the two validated source reports.

    Args:
        tmp_path: Per-test temporary report root.
    """

    expected_relpath = (
        "tmp/code-antipattern-audit-merged-00000000-0000-0000-0000-000000000001-"
        "00000000-0000-0000-0000-000000000002.md"
    )
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
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_merge.py",
            MECHANICAL_REPORT_RELPATH,
            SEMANTIC_REPORT_RELPATH,
            report_root=tmp_path,
        )
        merged_path = tmp_path / expected_relpath
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected_relpath
        assert merged_path.is_file()
        merged_text = merged_path.read_text(encoding="utf-8")

    assert f"- `scope`: `{VALID_REPORT_SCOPE}`" in merged_text
    assert f"- `mechanical_report_path`: `{MECHANICAL_REPORT_RELPATH}`" in merged_text
    assert f"- `semantic_report_path`: `{SEMANTIC_REPORT_RELPATH}`" in merged_text
    assert "- `overall_verdict`: `FINDINGS`" in merged_text
    assert "## Mechanical source report" in merged_text
    assert "## Semantic source report" in merged_text
    merged_path.unlink(missing_ok=True)


def test_report_merge_rejects_scope_mismatch(tmp_path: Path) -> None:
    """Merger must reject source reports with different declared scopes.

    Args:
        tmp_path: Per-test temporary report root.
    """

    mismatched_semantic = valid_semantic_report().replace(
        f"- `scope`: `{VALID_REPORT_SCOPE}`",
        "- `scope`: `script/other`",
    )
    with (
        temporary_repo_file_create(
            repo_root=tmp_path,
            relpath=MECHANICAL_REPORT_RELPATH,
            content=valid_mechanical_report(),
        ),
        temporary_repo_file_create(
            repo_root=tmp_path,
            relpath=SEMANTIC_REPORT_RELPATH,
            content=mismatched_semantic,
        ),
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_merge.py",
            MECHANICAL_REPORT_RELPATH,
            SEMANTIC_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 1
    assert "scope mismatch" in result.stderr


def test_report_merge_rejects_invalid_source_report(tmp_path: Path) -> None:
    """Merger must reject one malformed source report instead of merging it.

    Args:
        tmp_path: Per-test temporary report root.
    """

    malformed_semantic = valid_semantic_report().replace(
        "- `overall_verdict`: `FINDINGS`",
        "- `overall_verdict`: `CLEAN`",
    )
    with (
        temporary_repo_file_create(
            repo_root=tmp_path,
            relpath=MECHANICAL_REPORT_RELPATH,
            content=valid_mechanical_report(),
        ),
        temporary_repo_file_create(
            repo_root=tmp_path,
            relpath=SEMANTIC_REPORT_RELPATH,
            content=malformed_semantic,
        ),
    ):
        result = repo_tool_run(
            "plugins/agent-workflows/lib/code-antipattern-audit/tool/code_antipattern_audit_report_merge.py",
            MECHANICAL_REPORT_RELPATH,
            SEMANTIC_REPORT_RELPATH,
            report_root=tmp_path,
        )

    assert result.returncode == 1
    assert "invalid semantic source report" in result.stderr
    assert "`overall_verdict` CLEAN is inconsistent" in result.stderr
