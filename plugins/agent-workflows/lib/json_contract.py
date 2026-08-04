"""Strict dependency-free JSON decoding for agent-workflows boundaries."""

from __future__ import annotations

import json


class JsonContractError(ValueError):
    """Report malformed or ambiguous JSON without including its payload."""


def _constant_reject(value: str) -> object:
    """Reject non-standard numeric constants accepted by Python's decoder."""

    raise JsonContractError(f"JSON numeric constant is unsupported: {value}")


def _object_from_pair_list(pair_list: list[tuple[str, object]]) -> dict[str, object]:
    """Build one object while rejecting duplicate member names."""

    value_by_name_map: dict[str, object] = {}
    for name, value in pair_list:
        if name in value_by_name_map:
            raise JsonContractError("JSON object repeats one member name")
        value_by_name_map[name] = value
    return value_by_name_map


def json_load_strict(payload: str | bytes) -> object:
    """Decode one standards-compliant JSON value without ambiguous object keys."""

    if not isinstance(payload, (str, bytes)):
        raise JsonContractError("JSON payload must be text or bytes")
    try:
        return json.loads(
            payload,
            object_pairs_hook=_object_from_pair_list,
            parse_constant=_constant_reject,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, JsonContractError) as error:
        raise JsonContractError("JSON payload is malformed or ambiguous") from error
