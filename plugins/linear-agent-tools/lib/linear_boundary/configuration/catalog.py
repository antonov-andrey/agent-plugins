"""Canonical Linear workflow status and label catalog."""

from __future__ import annotations

from linear_boundary.configuration.model import LinearLabel, StatusDefinition
from linear_boundary.status import IssueStatusCategory, ProjectStatusCategory

ISSUE_STATUS_LEGACY_REVIEW = StatusDefinition(
    "",
    "Human Review",
    IssueStatusCategory.STARTED,
    "#7C3AED",
    "Candidate awaits a human decision",
    400.0,
)
ISSUE_STATUS_LEGACY_MERGING = StatusDefinition(
    "",
    "Merging",
    IssueStatusCategory.STARTED,
    "#0F766E",
    "Approved exact candidate is being merged",
    600.0,
)

ISSUE_STATUS_DESIRED = (
    StatusDefinition("", "Backlog", IssueStatusCategory.BACKLOG, "#6B7280", "Idea or inactive import staging", 100.0),
    StatusDefinition(
        "",
        "Todo",
        IssueStatusCategory.UNSTARTED,
        "#4F46E5",
        "Defined task ready when blockers close",
        200.0,
    ),
    StatusDefinition(
        "",
        "In Progress",
        IssueStatusCategory.STARTED,
        "#2563EB",
        "Agent owns the current attempt",
        300.0,
    ),
    StatusDefinition(
        "",
        "Review",
        IssueStatusCategory.STARTED,
        "#7C3AED",
        "Independent Codex review or final deployed-result human boundary",
        400.0,
    ),
    StatusDefinition(
        "",
        "Rework",
        IssueStatusCategory.STARTED,
        "#DC2626",
        "A fresh attempt must revise the candidate",
        500.0,
    ),
    StatusDefinition(
        "",
        "Merging",
        IssueStatusCategory.STARTED,
        "#0F766E",
        "Independently reviewed pull request heads are being merged",
        600.0,
    ),
    StatusDefinition("", "Done", IssueStatusCategory.COMPLETED, "#16A34A", "Task completed", 700.0),
    StatusDefinition("", "Canceled", IssueStatusCategory.CANCELED, "#9CA3AF", "Task canceled by a human", 800.0),
)

PROJECT_STATUS_DESIRED = (
    StatusDefinition(
        "",
        "Planned",
        ProjectStatusCategory.PLANNED,
        "#6B7280",
        "Graph is non-dispatchable staging",
        100.0,
    ),
    StatusDefinition(
        "",
        "In Progress",
        ProjectStatusCategory.STARTED,
        "#2563EB",
        "Graph passed the activation barrier",
        200.0,
    ),
    StatusDefinition(
        "",
        "Completed",
        ProjectStatusCategory.COMPLETED,
        "#16A34A",
        "Accepted graph and cleanup completed",
        300.0,
    ),
    StatusDefinition(
        "",
        "Canceled",
        ProjectStatusCategory.CANCELED,
        "#9CA3AF",
        "Graph canceled and reconciled",
        400.0,
    ),
)

LABEL_DESIRED = (
    LinearLabel(
        "",
        "task:implementation",
        "#4F46E5",
        "[linear-agent-tools:v1] Code or evidence implementation task",
    ),
    LinearLabel("", "task:review", "#7C3AED", "[linear-agent-tools:v1] Independent semantic review task"),
    LinearLabel("", "task:acceptance", "#0F766E", "[linear-agent-tools:v1] Whole-outcome acceptance task"),
    LinearLabel("", "task:cleanup", "#B45309", "[linear-agent-tools:v1] Exact owned-resource cleanup task"),
    LinearLabel("", "task:human", "#64748B", "[linear-agent-tools:v1] Human-only decision or action"),
    LinearLabel("", "agent:codex", "#2563EB", "[linear-agent-tools:v1] Dispatch may use a Codex agent"),
)
