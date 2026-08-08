"""Closed GitHub Git transport destinations and relative submodule resolution."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import urlsplit

from git_origin.identity import GitOriginError, origin_identity_get

_GITHUB_IDENTITY_PREFIX = "github.com/"
_RELATIVE_PATH_PART_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_SCP_GITHUB_PATTERN = re.compile(r"git@github\.com:(?P<path>[^:]+)")


class GitTransportError(RuntimeError):
    """Report one destination outside the closed Git transport set."""


@dataclass(frozen=True, slots=True)
class GitTransportDestination:
    """One canonical GitHub transport destination approved for Git execution."""

    identity: str
    protocol: str
    style: str
    url: str


def git_transport_destination_get(value: str) -> GitTransportDestination:
    """Parse one absolute GitHub HTTPS, SSH URL, or SSH SCP destination.

    Args:
        value: Candidate Git transport destination.

    Returns:
        Canonical destination and the sole protocol it requires.
    """

    _transport_text_validate(value)
    if "::" in value:
        raise GitTransportError("Git transport helper syntax is unsupported")
    try:
        identity = origin_identity_get(value)
    except GitOriginError as error:
        raise GitTransportError(str(error)) from error
    if not identity.startswith(_GITHUB_IDENTITY_PREFIX):
        raise GitTransportError("Git transport destination must be one exact GitHub repository")
    repository_path = identity.removeprefix(_GITHUB_IDENTITY_PREFIX)

    scp_match = _SCP_GITHUB_PATTERN.fullmatch(value)
    if scp_match is not None:
        return GitTransportDestination(
            identity=identity,
            protocol="ssh",
            style="scp",
            url=f"git@github.com:{repository_path}.git",
        )

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise GitTransportError("Git transport destination has an invalid authority") from error
    if parsed.scheme.lower() == "https":
        if parsed.username is not None or parsed.password is not None or port is not None:
            raise GitTransportError("Git HTTPS transport cannot contain credentials or a port")
        return GitTransportDestination(
            identity=identity,
            protocol="https",
            style="https",
            url=f"https://github.com/{repository_path}.git",
        )
    if parsed.scheme.lower() == "ssh":
        if parsed.username != "git" or parsed.password is not None or port is not None:
            raise GitTransportError("Git SSH transport requires the standard GitHub authority")
        return GitTransportDestination(
            identity=identity,
            protocol="ssh",
            style="ssh",
            url=f"ssh://git@github.com/{repository_path}.git",
        )
    raise GitTransportError("Git transport protocol is unsupported")


def git_relative_transport_destination_get(
    parent: GitTransportDestination,
    value: str,
) -> GitTransportDestination:
    """Resolve one supported relative submodule URL against its validated parent.

    Args:
        parent: Already validated exact parent repository transport.
        value: Absolute or dot-relative submodule URL.

    Returns:
        Canonical absolute GitHub destination.
    """

    _transport_text_validate(value)
    if not value.startswith(("./", "../")):
        return git_transport_destination_get(value)
    if "::" in value or "\\" in value or "?" in value or "#" in value or ":" in value:
        raise GitTransportError("Relative Git submodule destination is unsupported")
    relative_part_list = value.split("/")
    if any(not part for part in relative_part_list):
        raise GitTransportError("Relative Git submodule destination is malformed")

    repository_part_list = parent.identity.removeprefix(_GITHUB_IDENTITY_PREFIX).split("/")
    for part in relative_part_list:
        if part == ".":
            continue
        if part == "..":
            if not repository_part_list:
                raise GitTransportError("Relative Git submodule destination escapes its GitHub authority")
            repository_part_list.pop()
            continue
        if _RELATIVE_PATH_PART_PATTERN.fullmatch(part) is None or part in {".", ".."}:
            raise GitTransportError("Relative Git submodule destination contains an unsafe path")
        repository_part_list.append(part)
    if len(repository_part_list) != 2:
        raise GitTransportError("Relative Git submodule destination is not one GitHub repository")
    repository_part_list[-1] = repository_part_list[-1].removesuffix(".git")
    repository_path = "/".join(repository_part_list)
    try:
        identity = origin_identity_get(f"github.com/{repository_path}")
    except GitOriginError as error:
        raise GitTransportError(str(error)) from error
    if parent.style == "https":
        url = f"https://github.com/{repository_path}.git"
    elif parent.style == "ssh":
        url = f"ssh://git@github.com/{repository_path}.git"
    else:
        url = f"git@github.com:{repository_path}.git"
    return GitTransportDestination(identity=identity, protocol=parent.protocol, style=parent.style, url=url)


def _transport_text_validate(value: object) -> None:
    """Reject empty, whitespace-bearing, and control-bearing transport text."""

    if (
        not isinstance(value, str)
        or not value
        or any(character.isspace() or unicodedata.category(character).startswith("C") for character in value)
    ):
        raise GitTransportError("Git transport destination is malformed")
