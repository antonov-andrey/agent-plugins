"""Canonical credential-free Git origin identities."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

_SCP_ORIGIN_PATTERN = re.compile(
    r"(?P<username>[A-Za-z0-9._-]+)@" r"(?P<host>(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[0-9A-Fa-f:.]+\])):" r"(?P<path>[^?#]+)"
)
_SSH_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


class GitOriginError(RuntimeError):
    """Report one unsafe or unsupported Git origin."""


def _network_path_normalize(value: str) -> str:
    """Return one unambiguous repository path without its transport suffix."""

    raw_part_list = value.strip("/").split("/")
    normalized_part_list: list[str] = []
    for raw_part in raw_part_list:
        part = unquote(raw_part)
        if (
            not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or any(character.isspace() or ord(character) < 32 for character in part)
        ):
            raise GitOriginError("Repository origin URL contains an unsafe path")
        normalized_part_list.append(part)
    if normalized_part_list[-1].endswith(".git"):
        normalized_part_list[-1] = normalized_part_list[-1].removesuffix(".git")
    if not normalized_part_list[-1]:
        raise GitOriginError("Repository origin URL has no repository name")
    return "/".join(normalized_part_list)


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
        host = scp_match.group("host").lower()
        normalized_path = _network_path_normalize(scp_match.group("path"))
        return f"ssh://{username}@{host}/{normalized_path}"
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
        host = parsed.hostname.lower()
        authority = host if port is None else f"{host}:{port}"
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
        path = Path(parsed.path)
        if any(part in {".", ".."} for part in path.parts):
            raise GitOriginError("Repository file URL contains an unsafe path")
        return f"file://{path.resolve(strict=False)}"
    path = Path(value)
    if path.is_absolute():
        if any(part in {".", ".."} for part in path.parts):
            raise GitOriginError("Repository path contains an unsafe segment")
        return f"file://{path.resolve(strict=False)}"
    raise GitOriginError("Repository origin URL uses an unsupported or relative form")
