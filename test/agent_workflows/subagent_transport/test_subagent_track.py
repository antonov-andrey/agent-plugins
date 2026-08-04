"""Unit tests for the subagent activity tracker."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SUBAGENT_TRANSPORT_TOOL_ROOT = REPOSITORY_ROOT / "plugins" / "agent-workflows" / "lib" / "subagent-transport" / "tool"
if str(SUBAGENT_TRANSPORT_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(SUBAGENT_TRANSPORT_TOOL_ROOT))

import subagent_track
from lib.subagent_track import (
    _subagent_idle_ms_get,
    STATUS_ERROR_AGENT_ID_NOT_FOUND,
    STATUS_OK,
    STATUS_TIMEOUT,
    subagent_status_get,
)
import lib.subagent_track as subagent_track_lib

AGENT_ID = "019d29ad-926b-74c0-8220-57fc4883256e"
OTHER_AGENT_ID = "019d29b6-82e1-7f80-b55d-281d1ca0fa3f"


def _jsonl_append(path: Path, row_map: dict[str, object]) -> None:
    """Append one JSON row to a synthetic JSONL file.

    Args:
        path: Destination JSONL file path.
        row_map: Serializable JSON row payload.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row_map))
        handle.write("\n")


def test_subagent_idle_ms_get_prefers_matching_session_rollout(tmp_path: Path) -> None:
    """The helper must still use the newest timestamp across session and log sources.

    Args:
        tmp_path: Temporary filesystem root provided by pytest.
    """

    codex_root = tmp_path / ".codex"
    session_path = codex_root / "sessions" / "2026" / "03" / "26" / f"rollout-demo-{AGENT_ID}.jsonl"
    _jsonl_append(session_path, {"timestamp": "2026-03-26T10:00:00Z", "type": "session_meta"})
    _jsonl_append(session_path, {"timestamp": "2026-03-26T10:00:05Z", "type": "response_item"})
    (codex_root / "log").mkdir(parents=True, exist_ok=True)
    (codex_root / "log" / "codex-tui.log").write_text(
        "\n".join(
            [
                f"2026-03-26T10:00:09Z  INFO thread_id={AGENT_ID} newer matching line",
                f"2026-03-26T10:00:01Z  INFO thread_id={OTHER_AGENT_ID} unrelated line",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    idle_ms = _subagent_idle_ms_get(
        AGENT_ID,
        codex_root=codex_root,
        now=datetime(2026, 3, 26, 10, 0, 10, tzinfo=timezone.utc),
    )

    assert idle_ms == 1000


def test_session_rollout_path_list_collect_uses_filename_lookup(
    tmp_path: Path,
) -> None:
    """Matching rollout paths must be resolved only from the filename.

    Args:
        tmp_path: Temporary filesystem root provided by pytest.
    """

    sessions_root = tmp_path / ".codex" / "sessions"
    direct_path = sessions_root / "2026" / "03" / "26" / f"rollout-demo-{AGENT_ID}.jsonl"
    _jsonl_append(direct_path, {"timestamp": "2026-03-26T10:00:00Z", "type": "session_meta"})

    assert subagent_track_lib._session_rollout_path_list_collect(sessions_root, AGENT_ID) == [direct_path]


def test_subagent_idle_ms_get_matches_nested_child_agent_id_in_log(tmp_path: Path) -> None:
    """Nested log lines must count as child-agent activity.

    Args:
        tmp_path: Temporary filesystem root provided by pytest.
    """

    codex_root = tmp_path / ".codex"
    log_path = codex_root / "log" / "codex-tui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                f'2026-03-26T10:00:01Z  INFO session_loop{{thread_id={OTHER_AGENT_ID}}}:submission_dispatch{{submission.id="parent-turn"}}:turn{{otel.name="session_task.turn" thread.id={OTHER_AGENT_ID} turn.id=parent-turn model=gpt-5.5}}:session_loop{{thread_id={AGENT_ID}}}:submission_dispatch{{submission.id="child-turn"}}:turn{{otel.name="session_task.turn" thread.id={AGENT_ID} turn.id=child-turn model=gpt-5.5}}: codex_core::tasks: close',
                "2026-03-26T10:00:03Z  INFO thread_id=other-agent unrelated line",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    idle_ms = _subagent_idle_ms_get(
        AGENT_ID,
        codex_root=codex_root,
        now=datetime(2026, 3, 26, 10, 0, 10, tzinfo=timezone.utc),
    )

    assert idle_ms == 9000


def test_log_last_timestamp_get_skips_regex_parse_for_unrelated_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unrelated log lines must be filtered by raw agent id before regex parsing.

    Args:
        monkeypatch: Pytest monkeypatch helper.
        tmp_path: Temporary filesystem root provided by pytest.
    """

    codex_root = tmp_path / ".codex"
    log_path = codex_root / "log" / "codex-tui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                f"2026-03-26T10:00:01Z  INFO thread_id={OTHER_AGENT_ID} unrelated line",
                f"2026-03-26T10:00:09Z  INFO thread_id={AGENT_ID} matching line",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    observed_line_list: list[str] = []
    original_collect = subagent_track_lib._log_observed_agent_id_list_collect

    def _observed_agent_id_list_collect(raw_line: str) -> list[str]:
        """Record parsed lines while preserving the real collector behavior.

        Args:
            raw_line: Raw log line routed through the parser.

        Returns:
            Parsed identifier list from the original helper.
        """

        observed_line_list.append(raw_line)
        return original_collect(raw_line)

    monkeypatch.setattr(subagent_track_lib, "_log_observed_agent_id_list_collect", _observed_agent_id_list_collect)

    latest = subagent_track_lib._log_last_timestamp_get(log_path, AGENT_ID)

    assert latest == datetime(2026, 3, 26, 10, 0, 9, tzinfo=timezone.utc)
    assert observed_line_list == [f"2026-03-26T10:00:09Z  INFO thread_id={AGENT_ID} matching line\n"]


def test_subagent_status_get_returns_ok_for_active_agent(tmp_path: Path) -> None:
    """Agent-only tracker must return `OK` for one active agent.

    Args:
        tmp_path: Temporary filesystem root provided by pytest.
    """

    codex_root = tmp_path / ".codex"
    session_path = codex_root / "sessions" / "2026" / "03" / "26" / f"rollout-demo-{AGENT_ID}.jsonl"
    _jsonl_append(session_path, {"timestamp": "2026-03-26T10:00:05Z", "type": "response_item"})

    status = subagent_status_get(
        AGENT_ID,
        codex_root=codex_root,
        now=datetime(2026, 3, 26, 10, 0, 10, tzinfo=timezone.utc),
    )

    assert status == STATUS_OK


def test_subagent_status_get_returns_timeout_for_idle_agent(tmp_path: Path) -> None:
    """Agent-only tracker must return `TIMEOUT` when the agent is idle past the threshold.

    Args:
        tmp_path: Temporary filesystem root provided by pytest.
    """

    codex_root = tmp_path / ".codex"
    session_path = codex_root / "sessions" / "2026" / "03" / "26" / f"rollout-demo-{AGENT_ID}.jsonl"
    _jsonl_append(session_path, {"timestamp": "2026-03-26T10:00:05Z", "type": "response_item"})

    status = subagent_status_get(
        AGENT_ID,
        codex_root=codex_root,
        now=datetime(2026, 3, 26, 10, 10, 6, tzinfo=timezone.utc),
    )

    assert status == STATUS_TIMEOUT


def test_subagent_status_get_returns_error_agent_id_not_found_for_missing_agent(tmp_path: Path) -> None:
    """Missing agents must return `ERROR_AGENT_ID_NOT_FOUND`.

    Args:
        tmp_path: Temporary filesystem root provided by pytest.
    """

    status = subagent_status_get(
        AGENT_ID,
        codex_root=tmp_path / ".codex",
        now=datetime(2026, 3, 26, 10, 0, 10, tzinfo=timezone.utc),
    )

    assert status == STATUS_ERROR_AGENT_ID_NOT_FOUND


def test_subagent_status_get_returns_error_agent_id_not_found_for_invalid_uuid(tmp_path: Path) -> None:
    """Non-UUID agent ids must map to `ERROR_AGENT_ID_NOT_FOUND`.

    Args:
        tmp_path: Temporary filesystem root provided by pytest.
    """

    status = subagent_status_get(
        "not-a-uuid",
        codex_root=tmp_path / ".codex",
        now=datetime(2026, 3, 26, 10, 0, 10, tzinfo=timezone.utc),
    )

    assert status == STATUS_ERROR_AGENT_ID_NOT_FOUND


def test_main_prints_ok(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI wrapper must print `OK`.

    Args:
        monkeypatch: Pytest monkeypatch helper.
        capsys: Pytest output-capture helper.
    """

    monkeypatch.setattr(subagent_track, "subagent_status_get", lambda *args, **kwargs: STATUS_OK)

    exit_code = subagent_track.main(["--agent-id", AGENT_ID])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == "OK\n"
    assert captured.err == ""


def test_main_prints_timeout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI wrapper must print `TIMEOUT`.

    Args:
        monkeypatch: Pytest monkeypatch helper.
        capsys: Pytest output-capture helper.
    """

    monkeypatch.setattr(subagent_track, "subagent_status_get", lambda *args, **kwargs: STATUS_TIMEOUT)

    exit_code = subagent_track.main(["--agent-id", AGENT_ID])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == "TIMEOUT\n"
    assert captured.err == ""


def test_main_prints_error_agent_id_not_found(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI wrapper must print `ERROR_AGENT_ID_NOT_FOUND`.

    Args:
        monkeypatch: Pytest monkeypatch helper.
        capsys: Pytest output-capture helper.
    """

    monkeypatch.setattr(
        subagent_track,
        "subagent_status_get",
        lambda *args, **kwargs: STATUS_ERROR_AGENT_ID_NOT_FOUND,
    )

    exit_code = subagent_track.main(["--agent-id", AGENT_ID])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == "ERROR_AGENT_ID_NOT_FOUND\n"
    assert captured.err == ""


def test_main_rejects_removed_compatibility_argument(capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI must reject the removed compatibility argument.

    Args:
        capsys: Pytest output-capture helper.
    """

    removed_argument = "--" + "task" + "-id"
    with pytest.raises(SystemExit) as exc_info:
        subagent_track.main([removed_argument, "019d2be0-b4cd-7be3-bf62-38262211a669", "--agent-id", AGENT_ID])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert captured.out == ""
    assert f"unrecognized arguments: {removed_argument}" in captured.err
