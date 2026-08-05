"""Canonical credential-free Git origin identities."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit


class GitOriginError(RuntimeError):
    """Report one unsafe or unsupported Git origin."""


def origin_identity_get(value: str) -> str:
    """Normalize one credential-free Git origin for equality.

    Args:
        value: Configured or requested origin URL.

    Returns:
        Canonical comparison identity.
    """

    if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise GitOriginError("Repository origin URL is malformed")
    if value.startswith("git@") and ":" in value:
        authority, path = value.split(":", 1)
        host = authority.removeprefix("git@").lower()
        normalized_path = path.removesuffix(".git").strip("/")
        if not host or not normalized_path or any(character in path for character in ("?", "#")):
            raise GitOriginError("Repository origin URL has no path")
        return f"ssh://{host}/{normalized_path}"
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
        normalized_path = parsed.path.removesuffix(".git").strip("/")
        if not normalized_path:
            raise GitOriginError("Repository origin URL has no path")
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
            raise GitOriginError("Repository file URL contains unsupported authority or suffixes")
        return f"file://{Path(parsed.path).resolve(strict=False)}"
    path = Path(value)
    if path.is_absolute():
        return f"file://{path.resolve(strict=False)}"
    raise GitOriginError("Repository origin URL uses an unsupported or relative form")
