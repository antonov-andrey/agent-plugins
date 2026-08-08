"""Linear workflow configuration reconciliation against the canonical catalog."""

from __future__ import annotations

from dataclasses import replace

from linear_boundary.configuration.catalog import (
    ISSUE_STATUS_DESIRED,
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
        issue_status_archive_list: list[StatusDefinition] = []
        label_create_list: list[LinearLabel] = []
        project_status_create_list: list[StatusDefinition] = []
        self._status_reconcile(
            snapshot.issue_status_list,
            "issue-status",
            issue_status_create_list,
            issue_status_update_list,
            issue_status_archive_list,
            set(snapshot.active_issue_status_id_list),
            conflict_list,
        )
        self._status_reconcile(
            snapshot.project_status_list,
            "project-status",
            project_status_create_list,
            [],
            [],
            set(),
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
            issue_status_archive_list=issue_status_archive_list,
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
        status_archive_list: list[StatusDefinition],
        active_issue_status_id_set: set[str],
        conflict_list: list[ConfigurationConflict],
    ) -> None:
        """Append missing statuses and exact conflicts to one plan under construction."""

        desired_status_list = (
            self._issue_status_desired_list if kind == "issue-status" else self._project_status_desired_list
        )
        current_status_list_by_casefold_name_map: dict[str, list[StatusDefinition]] = {}
        for status in current_status_list:
            current_status_list_by_casefold_name_map.setdefault(status.name.casefold(), []).append(status)
        for desired_status in desired_status_list:
            if kind == "issue-status" and desired_status.name == "Review":
                if self._review_status_reconcile(
                    desired_status=desired_status,
                    current_status_list_by_casefold_name_map=current_status_list_by_casefold_name_map,
                    active_issue_status_id_set=active_issue_status_id_set,
                    status_update_list=status_update_list,
                    status_archive_list=status_archive_list,
                    conflict_list=conflict_list,
                ):
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

    def _review_status_reconcile(
        self,
        *,
        desired_status: StatusDefinition,
        current_status_list_by_casefold_name_map: dict[str, list[StatusDefinition]],
        active_issue_status_id_set: set[str],
        status_update_list: list[StatusDefinition],
        status_archive_list: list[StatusDefinition],
        conflict_list: list[ConfigurationConflict],
    ) -> bool:
        """Reconcile the one bounded historical Review cutover without a third status."""

        review_list = current_status_list_by_casefold_name_map.get("review", [])
        provider_legacy_list = current_status_list_by_casefold_name_map.get("human review", [])
        inactive_alias_list = current_status_list_by_casefold_name_map.get("in review", [])
        alias_list_by_name_map = {
            "Review": review_list,
            "Human Review": provider_legacy_list,
            "In Review": inactive_alias_list,
        }
        duplicate_name_list = [name for name, status_list in alias_list_by_name_map.items() if len(status_list) > 1]
        if duplicate_name_list:
            conflict_list.append(
                ConfigurationConflict(
                    "issue-status",
                    "Review",
                    f"ambiguous duplicate {duplicate_name_list[0]} status",
                )
            )
            return True
        for expected_name, status_list in alias_list_by_name_map.items():
            if status_list and status_list[0].name != expected_name:
                conflict_list.append(
                    ConfigurationConflict("issue-status", "Review", f"{expected_name} alias uses different casing")
                )
                return True

        current_review = review_list[0] if review_list else None
        provider_legacy = provider_legacy_list[0] if provider_legacy_list else None
        inactive_alias = inactive_alias_list[0] if inactive_alias_list else None
        if current_review is not None and provider_legacy is not None:
            conflict_list.append(
                ConfigurationConflict(
                    "issue-status",
                    "Review",
                    "canonical and provider-history Review identities coexist",
                )
            )
            return True
        if current_review is not None:
            if current_review.category != desired_status.category:
                conflict_list.append(
                    ConfigurationConflict(
                        "issue-status",
                        "Review",
                        f"category is {current_review.category}, expected {desired_status.category}",
                    )
                )
                return True
            if inactive_alias is not None:
                self._inactive_review_alias_archive(
                    inactive_alias,
                    expected_category=desired_status.category,
                    active_issue_status_id_set=active_issue_status_id_set,
                    status_archive_list=status_archive_list,
                    conflict_list=conflict_list,
                )
            return True
        if provider_legacy is not None:
            if replace(provider_legacy, id="") != ISSUE_STATUS_LEGACY_REVIEW:
                conflict_list.append(
                    ConfigurationConflict(
                        "issue-status",
                        "Review",
                        "provider-history status is not the exact recognized definition",
                    )
                )
                return True
            alias_safe = True
            if inactive_alias is not None:
                alias_safe = self._inactive_review_alias_archive(
                    inactive_alias,
                    expected_category=desired_status.category,
                    active_issue_status_id_set=active_issue_status_id_set,
                    status_archive_list=status_archive_list,
                    conflict_list=conflict_list,
                )
            if alias_safe:
                status_update_list.append(replace(desired_status, id=provider_legacy.id))
            return True
        if inactive_alias is not None:
            if inactive_alias.category != desired_status.category:
                conflict_list.append(
                    ConfigurationConflict(
                        "issue-status",
                        "Review",
                        f"In Review category is {inactive_alias.category}, expected {desired_status.category}",
                    )
                )
            else:
                status_update_list.append(replace(desired_status, id=inactive_alias.id))
            return True
        return False

    @staticmethod
    def _inactive_review_alias_archive(
        status: StatusDefinition,
        *,
        expected_category: str,
        active_issue_status_id_set: set[str],
        status_archive_list: list[StatusDefinition],
        conflict_list: list[ConfigurationConflict],
    ) -> bool:
        """Archive only the exact inactive started-category In Review alias."""

        if status.category != expected_category:
            conflict_list.append(
                ConfigurationConflict(
                    "issue-status",
                    "Review",
                    f"In Review category is {status.category}, expected {expected_category}",
                )
            )
            return False
        elif status.id in active_issue_status_id_set:
            conflict_list.append(
                ConfigurationConflict("issue-status", "Review", "In Review alias still owns an active issue")
            )
            return False
        else:
            status_archive_list.append(status)
            return True
