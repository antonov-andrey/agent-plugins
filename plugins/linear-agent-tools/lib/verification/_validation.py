"""Private validation primitives shared by verification evidence models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urlsplit, urlunsplit

from url_identity.host import UrlHostError, canonical_host_get

COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ISSUE_IDENTIFIER_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*")
INSTANT_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?Z")
_EVIDENCE_URL_PATH_PATTERN = re.compile(r"/(?:[A-Za-z0-9._~!$&'()*+,;=:@/-]|%[0-9A-F]{2})*")
_URI_PERCENT_ENCODING_PATTERN = re.compile(r"%([0-9A-F]{2})")
_URI_UNRESERVED_CHARACTER_SET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


class EvidenceContractError(RuntimeError):
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
        raise EvidenceContractError(
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
        raise EvidenceContractError(f"{label} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise EvidenceContractError(f"{label} must be normalized to UTC")
    return value


def instant_parse(value: object, *, label: str) -> datetime:
    """Parse one strict RFC 3339 UTC instant.

    Args:
        value: Candidate JSON value.
        label: Diagnostic owner label.

    Returns:
        Parsed UTC datetime.
    """

    if not isinstance(value, str) or INSTANT_PATTERN.fullmatch(value) is None:
        raise EvidenceContractError(f"{label} must be RFC 3339 UTC text")
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00").astimezone(timezone.utc)
    except ValueError as error:
        raise EvidenceContractError(f"{label} is malformed") from error


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
        raise EvidenceContractError(f"{label} must be a {qualifier} mapping")
    text_by_text_map: dict[str, str] = {}
    for key, mapped_value in value.items():
        parsed_key = single_line_validate(key, label=f"{label} key")
        text_by_text_map[parsed_key] = single_line_validate(mapped_value, label=f"{label} value")
    return dict(sorted(text_by_text_map.items()))


def evidence_url_validate(value: object) -> str:
    """Return one durable canonical HTTPS evidence URL.

    Args:
        value: Candidate provider artifact URL.

    Returns:
        Validated evidence URL.
    """

    evidence_url = single_line_validate(value, label="Verification evidence URL")
    try:
        parsed = urlsplit(evidence_url)
    except ValueError as error:
        raise EvidenceContractError("Verification evidence URL must be one canonical HTTPS provider URL") from error
    try:
        hostname = canonical_host_get(parsed.hostname, ipv6_allowed=False) if parsed.hostname is not None else None
    except UrlHostError as error:
        raise EvidenceContractError("Verification evidence URL must be one canonical HTTPS provider URL") from error
    canonical_url = "" if hostname is None else urlunsplit(("https", hostname, parsed.path, "", ""))
    if (
        not evidence_url.isascii()
        or parsed.scheme != "https"
        or hostname is None
        or parsed.netloc != hostname
        or not parsed.path
        or _EVIDENCE_URL_PATH_PATTERN.fullmatch(parsed.path) is None
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or parsed.query
        or parsed.fragment
        or evidence_url != canonical_url
        or any(
            chr(int(match.group(1), 16)) in _URI_UNRESERVED_CHARACTER_SET
            for match in _URI_PERCENT_ENCODING_PATTERN.finditer(parsed.path)
        )
    ):
        raise EvidenceContractError("Verification evidence URL must be one canonical HTTPS provider URL")
    return evidence_url
