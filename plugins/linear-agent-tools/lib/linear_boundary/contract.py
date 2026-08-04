"""Shared validation primitives for the Linear provider boundary."""

from __future__ import annotations

import re

_UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class LinearContractError(RuntimeError):
    """Report one malformed or conflicting Linear contract."""


def uuid_validate(value: str, *, label: str) -> str:
    """Return one canonical lowercase UUID identity.

    Args:
        value: Candidate identity.
        label: Diagnostic owner label.

    Returns:
        The validated identity.
    """

    if not isinstance(value, str) or _UUID_PATTERN.fullmatch(value) is None:
        raise LinearContractError(f"{label} must be one lowercase UUID")
    return value


def single_line_text_validate(value: str, *, label: str) -> str:
    """Return one non-empty single-line string.

    Args:
        value: Candidate text.
        label: Diagnostic owner label.

    Returns:
        The validated text.
    """

    if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise LinearContractError(f"{label} must be non-empty single-line text")
    return value
