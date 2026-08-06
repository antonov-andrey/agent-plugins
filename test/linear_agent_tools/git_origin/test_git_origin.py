"""Behavior tests for canonical credential-free Git origins."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools" / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_origin.identity import GitOriginError, origin_identity_get


def test_origin_identity_preserves_security_relevant_url_components() -> None:
    """Ports and SSH users cannot collapse distinct repository origins."""

    assert origin_identity_get("git@github.com:owner/example.git") == "ssh://git@github.com/owner/example"
    assert origin_identity_get("ssh://git@github.com/owner/example.git") == "ssh://git@github.com/owner/example"
    assert origin_identity_get("ssh://git@github.com:2222/owner/example.git") == (
        "ssh://git@github.com:2222/owner/example"
    )
    assert origin_identity_get("ssh://github.com/owner/example.git") == "ssh://github.com/owner/example"
    assert origin_identity_get("ssh://deploy@github.com/owner/example.git") == "ssh://deploy@github.com/owner/example"


@pytest.mark.parametrize(
    "value",
    [
        "https://token@github.com/owner/example.git",
        "https://token:secret@github.com/owner/example.git",
        "https://github.com/owner/example.git?ref=main",
        "https://github.com/owner/example.git#main",
    ],
)
def test_origin_identity_rejects_credentials_and_suffixes_without_echo(value: str) -> None:
    """Unsafe Git origins fail without copying their secret-bearing value into diagnostics."""

    with pytest.raises(GitOriginError) as error:
        origin_identity_get(value)

    assert "token" not in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "git@github.com/owner:example.git",
        "git@github.com:owner/../example.git",
        "ssh://git@github.com/owner/./example.git",
        "https://github.com/owner/%2e%2e/example.git",
    ],
)
def test_origin_identity_rejects_malformed_authorities_and_dot_segments(value: str) -> None:
    """Malformed SCP authorities and path aliases cannot acquire a repository identity."""

    with pytest.raises(GitOriginError, match="Repository origin URL"):
        origin_identity_get(value)
