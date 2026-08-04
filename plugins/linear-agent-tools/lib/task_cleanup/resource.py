"""Project-owned direct-argv cleanup execution."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable, Mapping, Sequence

from task_cleanup.model import TaskCleanupError
from task_graph.model import ResourceDeclaration


class ResourceCleaner:
    """Execute exact resource cleanup without shell evaluation or hidden retry."""

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        """Initialize one direct process runner.

        Args:
            runner: Injectable direct subprocess boundary.
        """

        self._runner = runner

    def cleanup(
        self,
        resource: ResourceDeclaration,
        *,
        working_directory: Path,
        placeholder_by_name: Mapping[str, str],
    ) -> None:
        """Run one project-owned idempotent cleanup command exactly once.

        Args:
            resource: Exact declared resource.
            working_directory: Validated owning repository task root.
            placeholder_by_name: Closed safe placeholder values.
        """

        argument_list = tuple(
            _argument_expand(argument, placeholder_by_name=placeholder_by_name)
            for argument in resource.cleanup_argument_list
        )
        try:
            result = self._runner(
                argument_list,
                cwd=working_directory,
                check=False,
                capture_output=True,
            )
        except OSError as error:
            raise TaskCleanupError(
                f"Project-owned cleanup could not start for exact resource {resource.key}"
            ) from error
        if result.returncode != 0:
            raise TaskCleanupError(f"Project-owned cleanup failed for exact resource {resource.key}")


def cleanup_binding_run(
    argument_list: Sequence[str],
    *,
    working_directory: Path,
    placeholder_by_name: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    """Run one repository bootstrap cleanup binding when declared.

    Args:
        argument_list: Direct project-owned argv.
        working_directory: Exact task root.
        placeholder_by_name: Closed placeholder mapping.
        runner: Injectable direct process runner.
    """

    if not argument_list:
        return
    expanded = tuple(_argument_expand(item, placeholder_by_name=placeholder_by_name) for item in argument_list)
    try:
        result = runner(expanded, cwd=working_directory, check=False, capture_output=True)
    except OSError as error:
        raise TaskCleanupError("Project-local workspace cleanup binding could not start") from error
    if result.returncode != 0:
        raise TaskCleanupError("Project-local workspace cleanup binding failed")


def _argument_expand(value: str, *, placeholder_by_name: Mapping[str, str]) -> str:
    """Expand only the closed literal placeholder set inside one argv item.

    Args:
        value: Raw direct argument.
        placeholder_by_name: Closed placeholder mapping.

    Returns:
        Expanded argument.
    """

    result = value
    for name, replacement in placeholder_by_name.items():
        result = result.replace("{" + name + "}", replacement)
    if "{" in result or "}" in result or "\x00" in result:
        raise TaskCleanupError("Cleanup argv contains an unknown or malformed placeholder")
    return result
