"""Behavior tests for canonical credential-free Git origins."""

from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools" / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_origin.identity import (
    GitOriginError,
    origin_identity_get,
)


def test_origin_identity_collapses_supported_github_transport_aliases() -> None:
    """GitHub SCP, SSH and HTTPS transports identify one owner/repository."""

    expected = "github.com/owner/example"
    assert origin_identity_get("git@github.com:Owner/Example.git") == expected
    assert origin_identity_get("ssh://git@github.com/OWNER/example.git") == expected
    assert origin_identity_get("https://github.com/owner/EXAMPLE.git") == expected
    assert origin_identity_get("github.com/Owner/Example") == expected
    assert origin_identity_get(expected) == expected


def test_origin_identity_preserves_non_github_transport_semantics() -> None:
    """Non-GitHub users, ports and SCP path modes remain distinct identities."""

    assert origin_identity_get("ssh://git@[2001:db8::1]:2222/owner/example.git") == (
        "ssh://git@[2001:db8::1]:2222/owner/example"
    )
    assert origin_identity_get("git@example.com:owner/example.git") == ("ssh+scp://git@example.com/owner/example")
    assert origin_identity_get("git@example.com:/owner/example.git") == ("ssh://git@example.com/owner/example")


@pytest.mark.parametrize(
    "value",
    [
        "ssh://git@github.com:2222/owner/example.git",
        "ssh://github.com/owner/example.git",
        "ssh://deploy@github.com/owner/example.git",
        "http://github.com/owner/example.git",
        "git://github.com/owner/example.git",
        "ssh+scp://git@github.com/owner/example.git",
        "git@github.com:/owner/example.git",
        "https://github.com/owner/example/extra.git",
        "https://github.com/owner/~example.git",
    ],
)
def test_origin_identity_rejects_unsupported_or_ambiguous_github_origins(value: str) -> None:
    """Only explicit standard GitHub transport aliases reach repository ownership."""

    with pytest.raises(GitOriginError, match="GitHub repository origin"):
        origin_identity_get(value)


@pytest.mark.parametrize(
    "value",
    [
        "git@github.com:owner/example.git",
        "git@example.com:owner/example.git",
        "ssh://git@[2001:db8::1]:2222/owner/example.git",
    ],
)
def test_network_origin_identity_is_idempotent(value: str) -> None:
    """Every accepted canonical identity can pass through the same owner again unchanged."""

    identity = origin_identity_get(value)

    assert origin_identity_get(identity) == identity


def test_git_argv_and_origin_identity_preserve_scp_relative_path_mode(
    tmp_path: Path,
) -> None:
    """Canonical identity follows the different paths Git sends to SSH."""

    capture_path = tmp_path / "ssh-argv.json"
    ssh_path = tmp_path / "ssh-capture.py"
    ssh_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['SSH_ARGV_CAPTURE'], 'w', encoding='utf-8') as handle:\n"
        "    json.dump(sys.argv[1:], handle)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    ssh_path.chmod(0o700)
    environment = {
        **os.environ,
        "GIT_SSH": str(ssh_path),
        "GIT_SSH_VARIANT": "ssh",
        "GIT_TERMINAL_PROMPT": "0",
        "SSH_ARGV_CAPTURE": str(capture_path),
    }

    remote_command_by_origin_map: dict[str, str] = {}
    for origin in (
        "git@example.com:owner/example.git",
        "ssh://git@example.com/owner/example.git",
    ):
        subprocess.run(
            ["git", "ls-remote", origin],
            check=False,
            capture_output=True,
            env=environment,
        )
        remote_command_by_origin_map[origin] = json.loads(capture_path.read_text(encoding="utf-8"))[-1]

    assert remote_command_by_origin_map["git@example.com:owner/example.git"] == ("git-upload-pack 'owner/example.git'")
    assert remote_command_by_origin_map["ssh://git@example.com/owner/example.git"] == (
        "git-upload-pack '/owner/example.git'"
    )
    assert origin_identity_get("git@example.com:owner/example.git") != origin_identity_get(
        "ssh://git@example.com/owner/example.git"
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://token@github.com/owner/example.git",
        "https://token:secret@github.com/owner/example.git",
        "https://github.com/owner/example.git?ref=main",
        "https://github.com/owner/example.git#main",
    ],
)
def test_origin_identity_rejects_credentials_and_suffixes_without_echo(
    value: str,
) -> None:
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
        "https://git hub.com/owner/example.git",
        "https://github%2ecom/owner/example.git",
        "https://github_com/owner/example.git",
        "https://-github.com/owner/example.git",
        "https://github..com/owner/example.git",
        "https://127.1/owner/example.git",
        "https://2130706433/owner/example.git",
        "git@github.com:owner/example.git.git",
        "https://github.com/owner/example.git.git",
        "ssh://git@example.com//owner/example.git",
        "git@example.com:owner/example.git/",
    ],
)
def test_origin_identity_rejects_malformed_authorities_and_dot_segments(
    value: str,
) -> None:
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
