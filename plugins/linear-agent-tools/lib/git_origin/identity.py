"""Canonical credential-free Git origin identities."""

from __future__ import annotations

from pathlib import Path
import re
from string import ascii_letters, digits
from urllib.parse import unquote_to_bytes, urlsplit

from url_identity.host import UrlHostError, canonical_host_get

_SCP_ORIGIN_PATTERN = re.compile(
    r"(?P<username>[A-Za-z0-9._-]+)@" r"(?P<host>(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[0-9A-Fa-f:.]+\])):" r"(?P<path>[^?#]+)"
)
_SSH_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
_HEX_DIGIT_SET = frozenset("0123456789abcdefABCDEF")
_URI_UNRESERVED_CHARACTER_SET = frozenset(ascii_letters + digits + "-._~")


class GitOriginError(RuntimeError):
    """Report one unsafe or unsupported Git origin."""


def _uri_unreserved_decode(value: str) -> str:
    """Decode only URI escapes that cannot change path structure or URL parsing."""

    character_list: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character != "%":
            character_list.append(character)
            index += 1
            continue
        if index + 2 >= len(value) or any(item not in _HEX_DIGIT_SET for item in value[index + 1 : index + 3]):
            raise GitOriginError("Repository origin URL contains a malformed escape")
        decoded = chr(int(value[index + 1 : index + 3], 16))
        if decoded not in _URI_UNRESERVED_CHARACTER_SET:
            raise GitOriginError("Repository origin URL encodes a reserved path character")
        character_list.append(decoded)
        index += 3
    return "".join(character_list)


def _network_path_normalize(value: str) -> str:
    """Return one unambiguous repository path without its transport suffix."""

    raw_part_list = value.strip("/").split("/")
    normalized_part_list: list[str] = []
    for raw_part in raw_part_list:
        part = _uri_unreserved_decode(raw_part)
        if (
            not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or any(character.isspace() or ord(character) < 32 for character in part)
        ):
            raise GitOriginError("Repository origin URL contains an unsafe path")
        normalized_part_list.append(part)
    if normalized_part_list[-1].endswith(".git.git"):
        raise GitOriginError("Repository origin URL contains an ambiguous transport suffix")
    if normalized_part_list[-1].endswith(".git"):
        normalized_part_list[-1] = normalized_part_list[-1].removesuffix(".git")
    if not normalized_part_list[-1]:
        raise GitOriginError("Repository origin URL has no repository name")
    return "/".join(normalized_part_list)


def _network_authority_render(host: str, port: int | None) -> str:
    """Render a parsed host and optional port as a reparsable URL authority."""

    rendered_host = f"[{host}]" if ":" in host else host
    return rendered_host if port is None else f"{rendered_host}:{port}"


def _network_host_normalize(value: str) -> str:
    """Return one unambiguous canonical network host."""

    host = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    try:
        return canonical_host_get(host, ipv6_allowed=True)
    except UrlHostError as error:
        raise GitOriginError("Repository origin URL contains an invalid host") from error


def _file_url_identity_get(path_text: str) -> str:
    """Return one location-independent canonical identity for an absolute file URL path."""

    try:
        decoded_path_text = unquote_to_bytes(path_text).decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitOriginError("Repository file URL path is not valid UTF-8") from error
    path = Path(decoded_path_text)
    if (
        not decoded_path_text
        or not path.is_absolute()
        or decoded_path_text.startswith("//")
        or str(path) != decoded_path_text
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise GitOriginError("Repository file URL requires one canonical absolute path")
    return path.as_uri()


def legacy_v1_origin_identity_get(value: str) -> str | None:
    """Return the identity produced by the previous workspace owner.

    This function exists only for a narrow private-state migration. It derives
    the old identity from the exact current configured remote and never accepts
    a state-provided value as input.

    Args:
        value: Exact current configured Git remote.

    Returns:
        Previous identity when that owner accepted the remote, or absence.
    """

    if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
        return None
    if value.startswith("git@") and ":" in value:
        authority, path = value.split(":", 1)
        host = authority.removeprefix("git@").lower()
        normalized_path = path.removesuffix(".git").strip("/")
        if not host or not normalized_path or any(character in path for character in ("?", "#")):
            return None
        return f"ssh://{host}/{normalized_path}"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.hostname:
        if parsed.query or parsed.fragment or parsed.password is not None:
            return None
        if parsed.scheme in {"http", "https", "git"} and parsed.username is not None:
            return None
        normalized_path = parsed.path.removesuffix(".git").strip("/")
        if not normalized_path:
            return None
        host = parsed.hostname.lower()
        authority = host if port is None else f"{host}:{port}"
        if parsed.scheme == "ssh" and parsed.username not in {None, "git"}:
            authority = f"{parsed.username}@{authority}"
        return f"{parsed.scheme.lower()}://{authority}/{normalized_path}"
    if parsed.scheme == "file":
        if (
            parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or parsed.netloc not in {"", "localhost"}
        ):
            return None
        return f"file://{Path(parsed.path).resolve(strict=False)}"
    path = Path(value)
    if path.is_absolute():
        return f"file://{path.resolve(strict=False)}"
    return None


def origin_identity_get(value: str) -> str:
    """Normalize one credential-free Git origin for equality.

    Args:
        value: Configured or requested origin URL.

    Returns:
        Canonical comparison identity.
    """

    if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise GitOriginError("Repository origin URL is malformed")
    scp_match = _SCP_ORIGIN_PATTERN.fullmatch(value)
    if scp_match is not None:
        username = scp_match.group("username")
        host = _network_host_normalize(scp_match.group("host"))
        normalized_path = _network_path_normalize(scp_match.group("path"))
        return f"ssh://{username}@{_network_authority_render(host, None)}/{normalized_path}"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise GitOriginError("Repository origin URL contains an invalid authority") from error
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.hostname:
        if parsed.query or parsed.fragment or parsed.password is not None:
            raise GitOriginError("Repository origin URL contains unsupported credentials or suffixes")
        if parsed.scheme in {"http", "https", "git"} and parsed.username is not None:
            raise GitOriginError("Repository origin URL contains unsupported credentials")
        normalized_path = _network_path_normalize(parsed.path)
        host = _network_host_normalize(parsed.hostname)
        authority = _network_authority_render(host, port)
        if parsed.scheme == "ssh" and parsed.username is not None:
            if _SSH_USERNAME_PATTERN.fullmatch(parsed.username) is None:
                raise GitOriginError("Repository origin URL contains an invalid SSH user")
            authority = f"{parsed.username}@{authority}"
        return f"{parsed.scheme.lower()}://{authority}/{normalized_path}"
    if parsed.scheme == "file":
        if (
            parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or parsed.netloc not in {"", "localhost"}
        ):
            raise GitOriginError("Repository file URL contains unsupported authority or suffixes")
        return _file_url_identity_get(parsed.path)
    path = Path(value)
    if path.is_absolute():
        if value.startswith("//") or str(path) != value or any(part in {".", ".."} for part in path.parts):
            raise GitOriginError("Repository path must use one canonical absolute form")
        return path.as_uri()
    raise GitOriginError("Repository origin URL uses an unsupported or relative form")
