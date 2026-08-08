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
_SCP_RELATIVE_SCHEME = "ssh+scp"


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

    if not value or value.startswith("//") or value.endswith("/"):
        raise GitOriginError("Repository origin URL contains an unsafe path")
    raw_part_list = value.removeprefix("/").split("/")
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
        path = scp_match.group("path")
        normalized_path = _network_path_normalize(path)
        scheme = "ssh" if path.startswith("/") or host == "github.com" else _SCP_RELATIVE_SCHEME
        return f"{scheme}://{username}@{_network_authority_render(host, None)}/{normalized_path}"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise GitOriginError("Repository origin URL contains an invalid authority") from error
    if parsed.scheme in {"http", "https", "ssh", "git", _SCP_RELATIVE_SCHEME} and parsed.hostname:
        if parsed.query or parsed.fragment or parsed.password is not None:
            raise GitOriginError("Repository origin URL contains unsupported credentials or suffixes")
        if parsed.scheme in {"http", "https", "git"} and parsed.username is not None:
            raise GitOriginError("Repository origin URL contains unsupported credentials")
        if parsed.scheme == _SCP_RELATIVE_SCHEME and (parsed.username is None or port is not None):
            raise GitOriginError("Repository SCP identity contains an invalid authority")
        normalized_path = _network_path_normalize(parsed.path)
        host = _network_host_normalize(parsed.hostname)
        authority = _network_authority_render(host, port)
        if parsed.scheme in {"ssh", _SCP_RELATIVE_SCHEME} and parsed.username is not None:
            if _SSH_USERNAME_PATTERN.fullmatch(parsed.username) is None:
                raise GitOriginError("Repository origin URL contains an invalid SSH user")
            authority = f"{parsed.username}@{authority}"
        scheme = "ssh" if parsed.scheme == _SCP_RELATIVE_SCHEME and host == "github.com" else parsed.scheme.lower()
        return f"{scheme}://{authority}/{normalized_path}"
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
