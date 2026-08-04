"""Private validation primitives shared by verification evidence models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ISSUE_IDENTIFIER_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*")


class VerificationReceiptError(RuntimeError):
    """Report one malformed verification evidence object."""


def single_line_validate(value: object, *, label: str, empty_allowed: bool = False) -> str:
    """Return one bounded single-line string.

    Args:
        value: Candidate text.
        label: Diagnostic owner label.
        empty_allowed: Whether an empty string is valid.

    Returns:
        Validated text.
    """

    if (
        not isinstance(value, str)
        or (not value and not empty_allowed)
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise VerificationReceiptError(
            f"{label} must be {'possibly empty ' if empty_allowed else 'non-empty '}single-line text"
        )
    return value


def utc_validate(value: object, *, label: str) -> datetime:
    """Return one timezone-aware UTC instant.

    Args:
        value: Candidate datetime.
        label: Diagnostic owner label.

    Returns:
        Validated UTC datetime.
    """

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise VerificationReceiptError(f"{label} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise VerificationReceiptError(f"{label} must be normalized to UTC")
    return value


def instant_parse(value: object, *, label: str) -> datetime:
    """Parse one strict RFC 3339 UTC instant.

    Args:
        value: Candidate JSON value.
        label: Diagnostic owner label.

    Returns:
        Parsed UTC datetime.
    """

    if not isinstance(value, str) or not value.endswith("Z"):
        raise VerificationReceiptError(f"{label} must be RFC 3339 UTC text")
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00").astimezone(timezone.utc)
    except ValueError as error:
        raise VerificationReceiptError(f"{label} is malformed") from error


def instant_render(value: datetime) -> str:
    """Render one validated UTC datetime.

    Args:
        value: UTC datetime.

    Returns:
        RFC 3339 text.
    """

    return value.isoformat().replace("+00:00", "Z")


def text_by_text_map_parse(value: object, *, label: str, empty_allowed: bool = True) -> dict[str, str]:
    """Parse one closed text-to-text mapping.

    Args:
        value: Candidate JSON value.
        label: Diagnostic owner label.
        empty_allowed: Whether an empty mapping is valid.

    Returns:
        Canonically ordered mapping.
    """

    if not isinstance(value, dict) or (not value and not empty_allowed):
        qualifier = "possibly empty" if empty_allowed else "non-empty"
        raise VerificationReceiptError(f"{label} must be a {qualifier} mapping")
    text_by_text_map: dict[str, str] = {}
    for key, mapped_value in value.items():
        parsed_key = single_line_validate(key, label=f"{label} key")
        text_by_text_map[parsed_key] = single_line_validate(mapped_value, label=f"{label} value")
    return dict(sorted(text_by_text_map.items()))
