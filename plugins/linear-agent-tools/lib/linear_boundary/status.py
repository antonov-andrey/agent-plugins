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
    """Exact issue workflow names used by manual and future Symphony runners."""

    BACKLOG = "Backlog"
    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    HUMAN_REVIEW = "Human Review"
    REWORK = "Rework"
    MERGING = "Merging"
    DONE = "Done"
    CANCELED = "Canceled"


class ProjectStatusName(StrEnum):
    """Exact Project workflow names used by graph activation and completion."""

    PLANNED = "Planned"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    CANCELED = "Canceled"
