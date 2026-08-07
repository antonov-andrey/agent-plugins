"""Linear workflow configuration reconciliation against the canonical catalog."""

from __future__ import annotations

from dataclasses import replace

from linear_boundary.configuration.catalog import (
    ISSUE_STATUS_DESIRED,
    ISSUE_STATUS_LEGACY_MERGING,
    ISSUE_STATUS_LEGACY_REVIEW,
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
        issue_status_update_list: list[StatusDefinition] = []
        label_create_list: list[LinearLabel] = []
        project_status_create_list: list[StatusDefinition] = []
        self._status_reconcile(
            snapshot.issue_status_list,
            "issue-status",
            issue_status_create_list,
            issue_status_update_list,
            conflict_list,
        )
        self._status_reconcile(
            snapshot.project_status_list,
            "project-status",
            project_status_create_list,
            [],
            conflict_list,
        )
        self._label_reconcile(
            snapshot.label_list,
            label_create_list,
            conflict_list,
        )
        return ConfigurationPlan(
            destination=snapshot.destination,
            issue_status_create_list=issue_status_create_list,
            issue_status_update_list=issue_status_update_list,
            project_status_create_list=project_status_create_list,
            label_create_list=label_create_list,
            git_status_automation_delete_list=list(snapshot.git_status_automation_list),
            conflict_list=conflict_list,
        )

    def _label_reconcile(
        self,
        current_label_list: list[LinearLabel],
        label_create_list: list[LinearLabel],
        conflict_list: list[ConfigurationConflict],
    ) -> None:
        """Append missing labels and exact conflicts to one plan under construction."""

        current_label_list_by_casefold_name_map: dict[str, list[LinearLabel]] = {}
        for label in current_label_list:
            current_label_list_by_casefold_name_map.setdefault(label.name.casefold(), []).append(label)
        for desired_label in self._label_desired_list:
            matching_label_list = current_label_list_by_casefold_name_map.get(desired_label.name.casefold(), [])
            if not matching_label_list:
                label_create_list.append(desired_label)
            elif len(matching_label_list) > 1:
                conflict_list.append(ConfigurationConflict("label", desired_label.name, "ambiguous duplicate name"))
            elif matching_label_list[0].name != desired_label.name:
                conflict_list.append(
                    ConfigurationConflict("label", desired_label.name, "same name uses different casing")
                )
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
        self,
        current_status_list: list[StatusDefinition],
        kind: str,
        status_create_list: list[StatusDefinition],
        status_update_list: list[StatusDefinition],
        conflict_list: list[ConfigurationConflict],
    ) -> None:
        """Append missing statuses and exact conflicts to one plan under construction."""

        desired_status_list = (
            self._issue_status_desired_list if kind == "issue-status" else self._project_status_desired_list
        )
        current_status_list_by_casefold_name_map: dict[str, list[StatusDefinition]] = {}
        for status in current_status_list:
            current_status_list_by_casefold_name_map.setdefault(status.name.casefold(), []).append(status)
        migrated_desired_name_set: set[str] = set()
        if kind == "issue-status":
            desired_review = next(item for item in desired_status_list if item.name == "Review")
            current_review_list = current_status_list_by_casefold_name_map.get("review", [])
            legacy_review_list = current_status_list_by_casefold_name_map.get("human review", [])
            if current_review_list and legacy_review_list:
                conflict_list.append(
                    ConfigurationConflict(
                        kind,
                        desired_review.name,
                        "current and legacy review statuses coexist",
                    )
                )
            elif not current_review_list and legacy_review_list:
                migrated_desired_name_set.add(desired_review.name)
                if len(legacy_review_list) > 1:
                    conflict_list.append(
                        ConfigurationConflict(kind, desired_review.name, "ambiguous duplicate legacy status")
                    )
                elif replace(legacy_review_list[0], id="") != ISSUE_STATUS_LEGACY_REVIEW:
                    conflict_list.append(
                        ConfigurationConflict(
                            kind,
                            desired_review.name,
                            "legacy status is not the exact provider definition",
                        )
                    )
                else:
                    status_update_list.append(replace(desired_review, id=legacy_review_list[0].id))
            desired_merging = next(item for item in desired_status_list if item.name == "Merging")
            current_merging_list = current_status_list_by_casefold_name_map.get("merging", [])
            if (
                len(current_merging_list) == 1
                and replace(current_merging_list[0], id="") == ISSUE_STATUS_LEGACY_MERGING
            ):
                migrated_desired_name_set.add(desired_merging.name)
                status_update_list.append(replace(desired_merging, id=current_merging_list[0].id))
        for desired_status in desired_status_list:
            if desired_status.name in migrated_desired_name_set:
                continue
            matching_status_list = current_status_list_by_casefold_name_map.get(desired_status.name.casefold(), [])
            if not matching_status_list:
                status_create_list.append(desired_status)
            elif len(matching_status_list) > 1:
                conflict_list.append(ConfigurationConflict(kind, desired_status.name, "ambiguous duplicate name"))
            elif matching_status_list[0].name != desired_status.name:
                conflict_list.append(
                    ConfigurationConflict(kind, desired_status.name, "same name uses different casing")
                )
            elif matching_status_list[0].category != desired_status.category:
                conflict_list.append(
                    ConfigurationConflict(
                        kind,
                        desired_status.name,
                        f"category is {matching_status_list[0].category}, expected {desired_status.category}",
                    )
                )
