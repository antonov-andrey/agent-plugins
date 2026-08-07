"""Closed Linear issue and Project status vocabulary."""

from enum import StrEnum


class IssueStatusCategory(StrEnum):
    """Linear fixed issue workflow categories."""

    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"


class ProjectStatusCategory(StrEnum):
    """Linear fixed Project workflow categories used by this provider."""

    PLANNED = "planned"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"


class IssueStatusName(StrEnum):
    """Exact issue workflow names used by the local task provider."""

    BACKLOG = "Backlog"
    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    REVIEW = "Review"
    REWORK = "Rework"
    MERGING = "Merging"
    DONE = "Done"
    CANCELED = "Canceled"


LEGACY_REVIEW_STATUS_NAME = "Human Review"


def issue_status_name_parse(value: object) -> IssueStatusName:
    """Parse current or migration-compatible provider status text.

    Args:
        value: Exact status name read from Linear.

    Returns:
        Current semantic issue status.
    """

    if value == LEGACY_REVIEW_STATUS_NAME:
        return IssueStatusName.REVIEW
    if not isinstance(value, str):
        raise ValueError("Issue status name must be text")
    return IssueStatusName(value)


class ProjectStatusName(StrEnum):
    """Exact Project workflow names used by graph activation and completion."""

    PLANNED = "Planned"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    CANCELED = "Canceled"
