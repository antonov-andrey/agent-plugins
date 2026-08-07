"""Shared direct-argv command boundary for GitHub and Git operations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import subprocess

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def command_run(argument_list: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one direct command with captured text output.

    Args:
        argument_list: Complete direct argument vector.

    Returns:
        Completed command without raising on provider rejection.
    """

    return subprocess.run(argument_list, check=False, capture_output=True, text=True)
