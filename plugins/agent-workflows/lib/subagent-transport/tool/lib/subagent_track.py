"""Helpers for tracking recent Codex subagent activity."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import UUID

from json_contract import JsonContractError, json_load_strict

LOG_THREAD_ID_PATTERN = re.compile(r"\bthread_id=([^\s}]+)")
LOG_TURN_THREAD_ID_PATTERN = re.compile(r"\bthread\.id=([^\s}]+)")
STATUS_ERROR_AGENT_ID_NOT_FOUND = "ERROR_AGENT_ID_NOT_FOUND"
STATUS_OK = "OK"
STATUS_TIMEOUT = "TIMEOUT"
SUBAGENT_STATUS_TIMEOUT_MS = 600_000


def codex_root_path_get(codex_root: Path | None = None) -> Path:
    """Return the effective Codex home root.

    Args:
        codex_root: Optional explicit Codex root override.

    Returns:
        Resolved Codex home root path.
    """

    return (codex_root or Path("~/.codex")).expanduser().resolve(strict=False)


def _agent_activity_status_get(
    agent_id: str,
    *,
    codex_root: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Return the direct activity status for one agent.

    Args:
        agent_id: Full subagent UUID.
        codex_root: Optional explicit Codex root override.
        now: Optional deterministic current UTC time.

    Returns:
        One of `OK`, `TIMEOUT`, or `ERROR_AGENT_ID_NOT_FOUND`.
    """

    normalized_agent_id = _uuid_normalized_get(agent_id)
    if normalized_agent_id is None:
        return STATUS_ERROR_AGENT_ID_NOT_FOUND
    try:
        idle_ms = _subagent_idle_ms_get(
            normalized_agent_id, codex_root=codex_root, now=now
        )
    except SubagentTrackError:
        return STATUS_ERROR_AGENT_ID_NOT_FOUND
    if idle_ms > SUBAGENT_STATUS_TIMEOUT_MS:
        return STATUS_TIMEOUT
    return STATUS_OK


def _jsonl_last_timestamp_get(path: Path) -> datetime | None:
    """Return the newest valid timestamp found in one JSONL file.

    Args:
        path: JSONL session file path.

    Returns:
        Latest parsed timestamp when at least one valid record exists, otherwise `None`.
    """

    latest: datetime | None = None
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            try:
                record = json_load_strict(raw_line)
            except JsonContractError:
                continue
            if not isinstance(record, dict):
                continue
            raw_timestamp = record.get("timestamp")
            if not isinstance(raw_timestamp, str):
                continue
            parsed = _timestamp_parse(raw_timestamp)
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
    return latest


def _log_last_timestamp_get(log_path: Path, agent_id: str) -> datetime | None:
    """Return the newest valid timestamp from log lines matching one subagent.

    Args:
        log_path: Codex TUI log file path.
        agent_id: Full normalized subagent UUID.

    Returns:
        Latest parsed log timestamp when matching lines exist, otherwise `None`.
    """

    if not log_path.is_file():
        return None

    latest: datetime | None = None
    with log_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            if agent_id not in raw_line:
                continue
            observed_agent_id_list = _log_observed_agent_id_list_collect(raw_line)
            if not observed_agent_id_list:
                continue
            if not any(
                _uuid_normalized_get(observed_agent_id) == agent_id
                for observed_agent_id in observed_agent_id_list
            ):
                continue
            raw_timestamp = raw_line.split(maxsplit=1)[0]
            parsed = _timestamp_parse(raw_timestamp)
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
    return latest


def _log_observed_agent_id_list_collect(raw_line: str) -> list[str]:
    """Collect all agent/thread identifiers observable in one log line.

    Args:
        raw_line: Raw TUI log line.

    Returns:
        Stable left-to-right unique identifier list.
    """

    ordered_id_list: list[str] = []
    seen_id_set: set[str] = set()
    for observed_agent_id in LOG_THREAD_ID_PATTERN.findall(
        raw_line
    ) + LOG_TURN_THREAD_ID_PATTERN.findall(raw_line):
        if observed_agent_id in seen_id_set:
            continue
        seen_id_set.add(observed_agent_id)
        ordered_id_list.append(observed_agent_id)
    return ordered_id_list


def _session_last_timestamp_get(sessions_root: Path, agent_id: str) -> datetime | None:
    """Return the newest valid timestamp across matching session files.

    Args:
        sessions_root: Codex sessions root.
        agent_id: Full normalized subagent UUID.

    Returns:
        Latest parsed session timestamp when matching data exists, otherwise `None`.
    """

    latest: datetime | None = None
    for path in _session_rollout_path_list_collect(sessions_root, agent_id):
        parsed = _jsonl_last_timestamp_get(path)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _session_rollout_path_list_collect(
    sessions_root: Path, agent_id: str
) -> list[Path]:
    """Collect matching session rollout files for one subagent.

    Args:
        sessions_root: Codex sessions root.
        agent_id: Full normalized subagent UUID.

    Returns:
        Matching rollout file paths in stable sorted order.
    """

    if not sessions_root.is_dir():
        return []

    return sorted(
        path for path in sessions_root.rglob(f"*{agent_id}.jsonl") if path.is_file()
    )


def _subagent_idle_ms_get(
    agent_id: str,
    *,
    codex_root: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Return milliseconds since the last observed subagent activity.

    Args:
        agent_id: Full subagent UUID.
        codex_root: Optional explicit Codex root override.
        now: Optional current UTC time override for deterministic tests.

    Returns:
        Non-negative milliseconds since the last observed activity.
    """

    current = _utc_now_get(now)
    last_activity = _subagent_last_activity_at_get(agent_id, codex_root=codex_root)
    idle_ms = int((current - last_activity).total_seconds() * 1000)
    return max(0, idle_ms)


def _subagent_last_activity_at_get(
    agent_id: str, *, codex_root: Path | None = None
) -> datetime:
    """Return the latest observed activity timestamp for one subagent.

    Args:
        agent_id: Full subagent UUID.
        codex_root: Optional explicit Codex root override.

    Returns:
        Latest observed UTC timestamp for the subagent.

    Raises:
        SubagentTrackError: No matching activity can be resolved.
    """

    normalized_agent_id = _uuid_normalized_get(agent_id)
    if normalized_agent_id is None:
        raise SubagentTrackError("agent_id must be a valid UUID")

    root = codex_root_path_get(codex_root)
    sessions_root = root / "sessions"
    log_path = root / "log" / "codex-tui.log"
    session_timestamp = _session_last_timestamp_get(sessions_root, normalized_agent_id)
    log_timestamp = _log_last_timestamp_get(log_path, normalized_agent_id)
    timestamp_list = [
        timestamp
        for timestamp in [session_timestamp, log_timestamp]
        if timestamp is not None
    ]
    if timestamp_list:
        return max(timestamp_list)
    raise SubagentTrackError(f"no activity found for subagent '{normalized_agent_id}'")


def _timestamp_parse(raw_timestamp: str) -> datetime | None:
    """Parse one Codex timestamp into UTC.

    Args:
        raw_timestamp: Raw timestamp token from session or log data.

    Returns:
        Parsed UTC datetime when the token is valid, otherwise `None`.
    """

    value = raw_timestamp.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_get(now: datetime | None = None) -> datetime:
    """Return the current UTC datetime.

    Args:
        now: Optional deterministic override.

    Returns:
        UTC-aware datetime.
    """

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _uuid_normalized_get(raw_value: str) -> str | None:
    """Return one canonical UUID string when the raw value is valid.

    Args:
        raw_value: Raw UUID candidate.

    Returns:
        Canonical lowercase UUID string when valid, otherwise `None`.
    """

    try:
        return str(UUID(raw_value.strip()))
    except AttributeError, ValueError:
        return None


def subagent_status_get(
    agent_id: str,
    *,
    codex_root: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Return the tracked subagent status for one agent.

    Args:
        agent_id: Agent UUID.
        codex_root: Optional explicit Codex root override.
        now: Optional deterministic current UTC time.

    Returns:
        One of `OK`, `TIMEOUT`, or `ERROR_AGENT_ID_NOT_FOUND`.
    """

    normalized_agent_id = _uuid_normalized_get(agent_id)
    if normalized_agent_id is None:
        return STATUS_ERROR_AGENT_ID_NOT_FOUND
    return _agent_activity_status_get(
        normalized_agent_id, codex_root=codex_root, now=now
    )


class SubagentTrackError(RuntimeError):
    """Raised when the tracker request itself is invalid."""
