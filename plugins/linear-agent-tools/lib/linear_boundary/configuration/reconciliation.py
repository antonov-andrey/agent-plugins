"""Linear workflow configuration reconciliation against the canonical catalog."""

from __future__ import annotations

from collections.abc import Iterable

from linear_boundary.configuration.catalog import (
    ISSUE_STATUS_DESIRED,
    LABEL_DESIRED,
    PROJECT_STATUS_DESIRED,
)
from linear_boundary.configuration.model import (
    ConfigurationConflict,
    ConfigurationPlan,
    LinearLabel,
    StatusDefinition,
    WorkflowConfigurationSnapshot,
)


def _label_reconcile(
    current_label_iterable: Iterable[LinearLabel],
    desired_label_iterable: Iterable[LinearLabel],
    label_create_list: list[LinearLabel],
    conflict_list: list[ConfigurationConflict],
) -> None:
    """Append missing labels and exact conflicts to one plan under construction.

    Args:
        current_label_iterable: Fully paginated current labels.
        desired_label_iterable: Canonical provider-owned label catalog.
        label_create_list: Plan output receiving missing labels.
        conflict_list: Plan output receiving label conflicts.
    """

    current_label_list_by_casefold_name_map: dict[str, list[LinearLabel]] = {}
    for label in current_label_iterable:
        current_label_list_by_casefold_name_map.setdefault(label.name.casefold(), []).append(label)
    for desired_label in desired_label_iterable:
        matching_label_list = current_label_list_by_casefold_name_map.get(desired_label.name.casefold(), [])
        if not matching_label_list:
            label_create_list.append(desired_label)
        elif len(matching_label_list) > 1:
            conflict_list.append(ConfigurationConflict("label", desired_label.name, "ambiguous duplicate name"))
        elif matching_label_list[0].name != desired_label.name:
            conflict_list.append(ConfigurationConflict("label", desired_label.name, "same name uses different casing"))
        elif (
            matching_label_list[0].description != desired_label.description
            or matching_label_list[0].color.lower() != desired_label.color.lower()
        ):
            conflict_list.append(
                ConfigurationConflict(
                    "label",
                    desired_label.name,
                    "existing label is not the exact provider definition",
                )
            )


def _status_reconcile(
    current_status_iterable: Iterable[StatusDefinition],
    desired_status_iterable: Iterable[StatusDefinition],
    kind: str,
    status_create_list: list[StatusDefinition],
    conflict_list: list[ConfigurationConflict],
) -> None:
    """Append missing statuses and exact conflicts to one plan under construction.

    Args:
        current_status_iterable: Fully paginated current status family.
        desired_status_iterable: Canonical provider-owned status catalog.
        kind: Diagnostic status family.
        status_create_list: Plan output receiving missing statuses.
        conflict_list: Plan output receiving status conflicts.
    """

    current_status_list_by_casefold_name_map: dict[str, list[StatusDefinition]] = {}
    for status in current_status_iterable:
        current_status_list_by_casefold_name_map.setdefault(status.name.casefold(), []).append(status)
    for desired_status in desired_status_iterable:
        matching_status_list = current_status_list_by_casefold_name_map.get(desired_status.name.casefold(), [])
        if not matching_status_list:
            status_create_list.append(desired_status)
        elif len(matching_status_list) > 1:
            conflict_list.append(ConfigurationConflict(kind, desired_status.name, "ambiguous duplicate name"))
        elif matching_status_list[0].name != desired_status.name:
            conflict_list.append(ConfigurationConflict(kind, desired_status.name, "same name uses different casing"))
        elif matching_status_list[0].category != desired_status.category:
            conflict_list.append(
                ConfigurationConflict(
                    kind,
                    desired_status.name,
                    f"category is {matching_status_list[0].category}, expected {desired_status.category}",
                )
            )


class WorkflowConfigurationReconciler:
    """Build exact mutation plans from complete Linear configuration snapshots."""

    def __init__(self) -> None:
        """Own detached working copies of the immutable canonical catalog."""

        self._issue_status_desired_list = list(ISSUE_STATUS_DESIRED)
        self._label_desired_list = list(LABEL_DESIRED)
        self._project_status_desired_list = list(PROJECT_STATUS_DESIRED)

    def plan_get(self, snapshot: WorkflowConfigurationSnapshot) -> ConfigurationPlan:
        """Compare one complete snapshot with the canonical provider contract.

        Args:
            snapshot: Fully paginated current global configuration.

        Returns:
            Exact missing definitions and conflicts.
        """

        snapshot.destination.mutation_authority_require()
        conflict_list: list[ConfigurationConflict] = []
        issue_status_create_list: list[StatusDefinition] = []
        label_create_list: list[LinearLabel] = []
        project_status_create_list: list[StatusDefinition] = []
        _status_reconcile(
            snapshot.issue_status_list,
            self._issue_status_desired_list,
            "issue-status",
            issue_status_create_list,
            conflict_list,
        )
        _status_reconcile(
            snapshot.project_status_list,
            self._project_status_desired_list,
            "project-status",
            project_status_create_list,
            conflict_list,
        )
        _label_reconcile(
            snapshot.label_list,
            self._label_desired_list,
            label_create_list,
            conflict_list,
        )
        return ConfigurationPlan(
            destination=snapshot.destination,
            issue_status_create_list=issue_status_create_list,
            project_status_create_list=project_status_create_list,
            label_create_list=label_create_list,
            conflict_list=conflict_list,
        )
