"""Closed direct-argv process boundary for GitHub and Git operations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
import pwd
import subprocess
from typing import Protocol

from git_host.model import GitHubContractError

_STANDARD_EXECUTABLE_PATH = "/usr/bin:/bin"
_IGNORED_ENVIRONMENT_NAME_SET = {"GH_PAGER", "GIT_PAGER"}
_UNSAFE_EXACT_ENVIRONMENT_NAME_SET = {
    "CODEX_HOME",
    "CURL_CA_BUNDLE",
    "GCM_CREDENTIAL_STORE",
    "GH_CONFIG_DIR",
    "GH_ENTERPRISE_TOKEN",
    "GH_HOST",
    "GH_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_TOKEN",
    "REQUESTS_CA_BUNDLE",
    "SSH_ASKPASS",
    "SSH_AUTH_SOCK",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "XDG_CONFIG_HOME",
}
_UNSAFE_CASEFOLD_ENVIRONMENT_NAME_SET = {
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


class CommandRunner(Protocol):
    """Execute one command using only the supplied closed environment."""

    def __call__(
        self,
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Return a completed process without raising for its exit status."""


def command_run(
    argument_list: Sequence[str],
    *,
    environment_by_name_map: Mapping[str, str],
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one direct command with captured text output and an exact environment.

    Args:
        argument_list: Complete direct argument vector.
        environment_by_name_map: Complete environment rather than an overlay.
        input_text: Optional standard-input text.

    Returns:
        Completed command without raising on provider rejection.
    """

    return subprocess.run(
        argument_list,
        check=False,
        capture_output=True,
        text=True,
        env=dict(environment_by_name_map),
        input=input_text,
    )


def command_closed_run(
    runner: CommandRunner,
    argument_list: Sequence[str],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one command after rejecting ambient process-control inputs.

    Args:
        runner: Deterministic or subprocess-backed command runner.
        argument_list: Complete direct argument vector.
        input_text: Optional standard-input text.

    Returns:
        Completed command without raising for its exit status.
    """

    return runner(
        argument_list,
        environment_by_name_map=_closed_environment_get(),
        input_text=input_text,
    )


def _closed_environment_get() -> dict[str, str]:
    """Build the complete standard-user environment for one provider command."""

    account = pwd.getpwuid(os.getuid())
    unsafe_name_list = _unsafe_environment_name_list_get(
        source=os.environ,
        standard_home=account.pw_dir,
    )
    if unsafe_name_list:
        raise GitHubContractError("Closed GitHub command environment contains unsafe inputs")
    return {
        "GCM_INTERACTIVE": "never",
        "GH_PROMPT_DISABLED": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": account.pw_dir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": account.pw_name,
        "PATH": _STANDARD_EXECUTABLE_PATH,
        "SSH_ASKPASS_REQUIRE": "never",
        "USER": account.pw_name,
    }


def _unsafe_environment_name_list_get(*, source: Mapping[str, str], standard_home: str) -> list[str]:
    """Return ambient names that could redirect authentication or Git execution."""

    unsafe_name_list: list[str] = []
    for name, value in source.items():
        upper_name = name.upper()
        if upper_name in _IGNORED_ENVIRONMENT_NAME_SET:
            continue
        if (
            (upper_name == "HOME" and value != standard_home)
            or upper_name in _UNSAFE_EXACT_ENVIRONMENT_NAME_SET
            or name.casefold() in _UNSAFE_CASEFOLD_ENVIRONMENT_NAME_SET
            or upper_name.startswith("GIT_")
            or upper_name.startswith("LD_")
            or upper_name.startswith("DYLD_")
        ):
            unsafe_name_list.append(name)
    return sorted(unsafe_name_list)
