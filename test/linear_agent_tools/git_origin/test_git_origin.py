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
    assert origin_identity_get("ssh://git@[2001:db8::1]:2222/owner/example.git") == (
        "ssh://git@[2001:db8::1]:2222/owner/example"
    )


@pytest.mark.parametrize(
    "value",
    [
        "git@github.com:owner/example.git",
        "ssh://git@[2001:db8::1]:2222/owner/example.git",
        "https://github.com/owner/%7Eexample.git",
    ],
)
def test_network_origin_identity_is_idempotent(value: str) -> None:
    """Every accepted canonical identity can pass through the same owner again unchanged."""

    identity = origin_identity_get(value)

    assert origin_identity_get(identity) == identity


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
        "https://github.com/owner/example%2Fshadow.git",
        "https://github.com/owner/example%3Fshadow.git",
        "https://github.com/owner/example%23shadow.git",
        "https://github.com/owner/example%5Cshadow.git",
        "https://github.com/owner/example%ZZshadow.git",
    ],
)
def test_origin_identity_rejects_malformed_authorities_and_dot_segments(value: str) -> None:
    """Malformed SCP authorities and path aliases cannot acquire a repository identity."""

    with pytest.raises(GitOriginError, match="Repository origin URL"):
        origin_identity_get(value)


def test_file_origin_identity_requires_absolute_location_independent_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relative file URLs never acquire a different identity from the caller's working directory."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for working_directory in (first, second):
        monkeypatch.chdir(working_directory)
        with pytest.raises(GitOriginError, match="canonical absolute path"):
            origin_identity_get("file:relative/repository.git")
        with pytest.raises(GitOriginError, match="canonical absolute path"):
            origin_identity_get("file:")


def test_file_origin_identity_is_encoded_and_idempotent(tmp_path: Path) -> None:
    """A filesystem remote with URL delimiters has one reparsable canonical identity."""

    repository_path = tmp_path / "repository #1.git"
    identity = origin_identity_get(str(repository_path))

    assert identity == repository_path.as_uri()
    assert origin_identity_get(identity) == identity
