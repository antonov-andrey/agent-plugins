"""Exact private ownership of the one pre-cutover carrier pair."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import json_object_load
from goal_lifecycle.identity import common_prefix_validate


@dataclass(frozen=True, slots=True)
class CoordinationBootstrapException:
    """Bind only the physical carriers left by the pre-cutover bootstrap."""

    common_prefix: str
    goal_carrier_path: str
    specification_carrier_path: str
    schema_version: int = 2

    @classmethod
    def from_payload(cls, payload: object) -> "CoordinationBootstrapException":
        """Build one coordination bootstrap exception from an untrusted payload.

        Args:
            payload: Structured operation payload.

        Returns:
            One coordination bootstrap exception from an untrusted payload.
        """

        expected = {
            "schema_version",
            "common_prefix",
            "goal_carrier_path",
            "specification_carrier_path",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload.get("schema_version") != 2:
            raise GoalLifecycleError("Coordination bootstrap exception has another shape")
        common_prefix = common_prefix_validate(str(payload["common_prefix"]))
        for field_name in (
            "goal_carrier_path",
            "specification_carrier_path",
        ):
            value = payload[field_name]
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise GoalLifecycleError(f"Coordination bootstrap {field_name} must be absolute")
        return cls(
            common_prefix=common_prefix,
            goal_carrier_path=str(payload["goal_carrier_path"]),
            specification_carrier_path=str(payload["specification_carrier_path"]),
        )

    def payload_get(self) -> dict[str, object]:
        """Return the canonical serialized payload.

        Returns:
            The canonical serialized payload.
        """

        return asdict(self)


def coordination_bootstrap_exception_path_get(goals_root: Path, *, git: Git | None = None) -> Path:
    """Locate the shared bootstrap-exception record in the goals Git directory.

    Args:
        goals_root: Goals root.
        git: Git command boundary.

    Returns:
        Absolute path of the shared exception record.
    """

    command = git or Git()
    return command.common_directory_get(goals_root) / "agent-workflows" / "coordination-bootstrap-exception.json"


def coordination_bootstrap_exception_optional_get(
    goals_root: Path,
    *,
    git: Git | None = None,
) -> CoordinationBootstrapException | None:
    """Return the optional coordination bootstrap exception.

    Args:
        goals_root: Goals root.
        git: Git command boundary.

    Returns:
        The optional coordination bootstrap exception.
    """

    command = git or Git()
    path = coordination_bootstrap_exception_path_get(goals_root, git=command)
    if not path.exists():
        return None
    return CoordinationBootstrapException.from_payload(json_object_load(path, label="coordination bootstrap exception"))


def coordination_bootstrap_exception_validate(
    goals_root: Path,
    exception: CoordinationBootstrapException,
    *,
    git: Git | None = None,
) -> None:
    """Validate marker ownership without requiring retained cleanup resources.

    Args:
        goals_root: Goals root.
        exception: Exception.
        git: Git command boundary.
    """

    command = git or Git()
    workspace_root = command.root_get(goals_root).parent
    carrier_path_list = [
        Path(exception.specification_carrier_path),
        Path(exception.goal_carrier_path),
    ]
    expected_name_list = [
        f"{exception.common_prefix}-spec.md",
        f"{exception.common_prefix}-goal.md",
    ]
    if any(
        path.name != expected_name
        or ".." in path.parts
        or path.parent.name != ".spec"
        or path.parent.parent.parent != workspace_root
        for path, expected_name in zip(carrier_path_list, expected_name_list, strict=True)
    ):
        raise GoalLifecycleError("Coordination bootstrap carrier is outside its exact legacy task scope")
    if carrier_path_list[0].parent != carrier_path_list[1].parent:
        raise GoalLifecycleError("Coordination bootstrap carriers have different legacy owners")
