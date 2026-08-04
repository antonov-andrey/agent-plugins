"""Project-owned direct-argv cleanup execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import subprocess

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
        placeholder_by_name_map: Mapping[str, str],
    ) -> None:
        """Run one project-owned idempotent cleanup command exactly once.

        Args:
            resource: Exact declared resource.
            working_directory: Validated owning repository task root.
            placeholder_by_name_map: Closed safe placeholder values.
        """

        argument_list = [
            _argument_expand(argument, placeholder_by_name_map=placeholder_by_name_map)
            for argument in resource.cleanup_argument_list
        ]
        try:
            completed_process = self._runner(
                argument_list,
                cwd=working_directory,
                check=False,
                capture_output=True,
            )
        except OSError as error:
            raise TaskCleanupError(
                f"Project-owned cleanup could not start for exact resource {resource.key}"
            ) from error
        if completed_process.returncode != 0:
            raise TaskCleanupError(
                f"Project-owned cleanup failed for exact resource {resource.key}"
            )


def cleanup_binding_run(
    argument_list: Sequence[str],
    *,
    working_directory: Path,
    placeholder_by_name_map: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    """Run one repository bootstrap cleanup binding when declared.

    Args:
        argument_list: Direct project-owned argv.
        working_directory: Exact task root.
        placeholder_by_name_map: Closed placeholder mapping.
        runner: Injectable direct process runner.
    """

    if not argument_list:
        return
    expanded_argument_list = [
        _argument_expand(item, placeholder_by_name_map=placeholder_by_name_map)
        for item in argument_list
    ]
    try:
        completed_process = runner(
            expanded_argument_list,
            cwd=working_directory,
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise TaskCleanupError(
            "Project-local workspace cleanup binding could not start"
        ) from error
    if completed_process.returncode != 0:
        raise TaskCleanupError("Project-local workspace cleanup binding failed")


def _argument_expand(value: str, *, placeholder_by_name_map: Mapping[str, str]) -> str:
    """Expand only the closed literal placeholder set inside one argv item.

    Args:
        value: Raw direct argument.
        placeholder_by_name_map: Closed placeholder mapping.

    Returns:
        Expanded argument.
    """

    expanded_argument = value
    for name, replacement in placeholder_by_name_map.items():
        expanded_argument = expanded_argument.replace("{" + name + "}", replacement)
    if (
        "{" in expanded_argument
        or "}" in expanded_argument
        or "\x00" in expanded_argument
    ):
        raise TaskCleanupError(
            "Cleanup argv contains an unknown or malformed placeholder"
        )
    return expanded_argument
