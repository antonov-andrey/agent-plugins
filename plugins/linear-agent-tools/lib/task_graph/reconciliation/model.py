"""Typed remote snapshots and host actions for graph reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from task_graph.model import TaskGraphError, TaskRole
from task_graph.publication import GraphPublicationView, IssuePublication

_KNOWN_ISSUE_STATUS_SET = frozenset(
    {
        "Backlog",
        "Todo",
        "In Progress",
        "Human Review",
        "Rework",
        "Merging",
        "Done",
        "Canceled",
    }
)
_KNOWN_PROJECT_STATUS_SET = frozenset(
    {"Planned", "In Progress", "Completed", "Canceled"}
)
_NODE_KEY_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PROJECT_KEY_PATTERN = re.compile(
    r"linear-agent-tools:v1:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{64}"
)
_ROLE_VALUE_SET = frozenset(item.value for item in TaskRole)
_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _node_key_require(value: object, *, label: str) -> None:
    """Require one canonical provider node key."""

    if not isinstance(value, str) or _NODE_KEY_PATTERN.fullmatch(value) is None:
        raise TaskGraphError(f"{label} must be one lowercase semantic slug")


def _single_line_require(value: object, *, label: str) -> None:
    """Require one non-empty single-line string."""

    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise TaskGraphError(f"{label} must be non-empty single-line text")


def _uuid_require(value: object, *, label: str) -> None:
    """Require one canonical lowercase UUID identity."""

    if not isinstance(value, str) or _UUID_PATTERN.fullmatch(value) is None:
        raise TaskGraphError(f"{label} must be one lowercase UUID")


class PublicationPhase(StrEnum):
    """Ordered phases of a non-atomic Linear graph import."""

    PROJECT = "project"
    DOCUMENT = "document"
    ISSUES = "issues"
    RELATIONS = "relations"
    NODE_METADATA = "node-metadata"
    NODE_ACTIVATION = "node-activation"
    PROJECT_ACTIVATION = "project-activation"
    DELTA_DOCUMENT = "delta-document"
    DELTA_ISSUES = "delta-issues"
    DELTA_RELATIONS = "delta-relations"
    DELTA_REVERIFICATION = "delta-reverification"
    DELTA_METADATA = "delta-metadata"
    DELTA_ACTIVATION = "delta-activation"
    PROJECT_CANCELLATION = "project-cancellation"
    NODE_CANCELLATION = "node-cancellation"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class RemoteIssue:
    """Contain provider-relevant current state of one Project issue."""

    id: str
    node_key: str
    title: str
    description: str
    status_name: str
    label_name_list: list[str]
    assignee_id: str
    delegate_id: str
    blocker_key_list: list[str]

    def __post_init__(self) -> None:
        """Validate one fully read external issue snapshot."""

        _uuid_require(self.id, label="Remote issue ID")
        _node_key_require(self.node_key, label="Remote issue node key")
        _single_line_require(self.title, label="Remote issue title")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or "\x00" in self.description
        ):
            raise TaskGraphError("Remote issue description must be non-empty text")
        if self.status_name not in _KNOWN_ISSUE_STATUS_SET:
            raise TaskGraphError("Remote issue status is unsupported")
        assignment_id_list = [
            value for value in (self.assignee_id, self.delegate_id) if value
        ]
        if len(assignment_id_list) > 1 or (
            not assignment_id_list and self.status_name != "Backlog"
        ):
            raise TaskGraphError(
                "Only a staged Backlog issue may omit its exact assignee or delegate"
            )
        if assignment_id_list:
            _uuid_require(
                assignment_id_list[0], label="Remote issue execution identity"
            )
        for label, value_list in (
            ("label names", self.label_name_list),
            ("blocker keys", self.blocker_key_list),
        ):
            if not isinstance(value_list, list) or len(value_list) != len(
                set(value_list)
            ):
                raise TaskGraphError(
                    f"Remote issue {label} must be a duplicate-free list"
                )
            for value in value_list:
                _single_line_require(value, label=f"Remote issue {label}")
        for blocker_key in self.blocker_key_list:
            _node_key_require(blocker_key, label="Remote issue blocker key")
        object.__setattr__(self, "label_name_list", list(self.label_name_list))
        object.__setattr__(self, "blocker_key_list", list(self.blocker_key_list))

    def delta_owned_fields_require(self, desired: IssuePublication) -> None:
        """Reject a delta stable-key collision or unsupported lifecycle."""

        if self.title != desired.title or self.description != desired.description:
            raise TaskGraphError(
                f"Delta issue {desired.node_key} conflicts with its approved stable key"
            )
        if self.status_name not in _KNOWN_ISSUE_STATUS_SET:
            raise TaskGraphError(
                f"Delta issue {desired.node_key} has an unsupported lifecycle status"
            )

    @classmethod
    def from_payload(cls, payload: object) -> "RemoteIssue":
        """Parse one strict remote issue snapshot."""

        expected = {
            "id",
            "node_key",
            "title",
            "description",
            "status_name",
            "label_name_list",
            "assignee_id",
            "delegate_id",
            "blocker_key_list",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise TaskGraphError("Remote issue snapshot has another shape")
        for name in ("label_name_list", "blocker_key_list"):
            if not isinstance(payload[name], list) or any(
                not isinstance(item, str) for item in payload[name]
            ):
                raise TaskGraphError(f"Remote issue {name} must be a text list")
        return cls(
            id=payload["id"],
            node_key=payload["node_key"],
            title=payload["title"],
            description=payload["description"],
            status_name=payload["status_name"],
            label_name_list=list(payload["label_name_list"]),
            assignee_id=payload["assignee_id"],
            delegate_id=payload["delegate_id"],
            blocker_key_list=list(payload["blocker_key_list"]),
        )

    def role_get(self) -> TaskRole:
        """Return the exact single task role from this existing issue."""

        role_value_set = set(self.label_name_list) & _ROLE_VALUE_SET
        if len(role_value_set) != 1:
            raise TaskGraphError(
                f"Existing delta node {self.node_key} must have exactly one task role label"
            )
        return TaskRole(next(iter(role_value_set)))

    def staged_owned_fields_require(self, desired: IssuePublication) -> None:
        """Reject an initial stable-key collision with other owned content."""

        if self.title != desired.title or self.description != desired.description:
            raise TaskGraphError(
                f"Issue {desired.node_key} conflicts with its stable source key"
            )
        if self.status_name not in {"Backlog", "Todo"}:
            raise TaskGraphError(
                f"Staged issue {desired.node_key} has an invalid pre-activation status"
            )


@dataclass(frozen=True, slots=True)
class RemoteDocument:
    """Contain one fully read Linear Project document."""

    id: str
    title: str
    content: str

    def __post_init__(self) -> None:
        """Validate exact provider document fields."""

        _uuid_require(self.id, label="Remote document ID")
        _single_line_require(self.title, label="Remote document title")
        if not isinstance(self.content, str) or "\x00" in self.content:
            raise TaskGraphError("Remote document content must be text")

    @classmethod
    def from_payload(cls, payload: object) -> "RemoteDocument":
        """Parse one strict remote document snapshot."""

        if not isinstance(payload, dict) or set(payload) != {"id", "title", "content"}:
            raise TaskGraphError("Remote document snapshot has another shape")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class RemoteProject:
    """Contain one fully paginated current Project import snapshot."""

    id: str
    team_id: str
    project_key: str
    name: str
    description: str
    status_name: str
    document_list: list[RemoteDocument]
    issue_list: list[RemoteIssue]

    def __post_init__(self) -> None:
        """Validate one fully paginated external Project snapshot."""

        _uuid_require(self.id, label="Remote Project ID")
        _uuid_require(self.team_id, label="Remote Project team ID")
        if (
            not isinstance(self.project_key, str)
            or _PROJECT_KEY_PATTERN.fullmatch(self.project_key) is None
        ):
            raise TaskGraphError("Remote Project key has another shape")
        _single_line_require(self.name, label="Remote Project name")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or "\x00" in self.description
        ):
            raise TaskGraphError("Remote Project description must be non-empty text")
        if self.status_name not in _KNOWN_PROJECT_STATUS_SET:
            raise TaskGraphError("Remote Project status is unsupported")
        if not isinstance(self.document_list, list) or any(
            not isinstance(item, RemoteDocument) for item in self.document_list
        ):
            raise TaskGraphError("Remote Project document list has another shape")
        if len({item.id for item in self.document_list}) != len(self.document_list):
            raise TaskGraphError("Remote Project repeats one document identity")
        if not isinstance(self.issue_list, list) or any(
            not isinstance(item, RemoteIssue) for item in self.issue_list
        ):
            raise TaskGraphError("Remote Project issue list has another shape")
        if len({item.id for item in self.issue_list}) != len(self.issue_list):
            raise TaskGraphError("Remote Project repeats one issue identity")
        object.__setattr__(self, "document_list", list(self.document_list))
        object.__setattr__(self, "issue_list", list(self.issue_list))

    @classmethod
    def from_payload(cls, payload: object) -> "RemoteProject":
        """Parse one strict remote Project snapshot."""

        expected = {
            "id",
            "team_id",
            "project_key",
            "name",
            "description",
            "status_name",
            "document_list",
            "issue_list",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or not isinstance(payload["document_list"], list)
            or not isinstance(payload["issue_list"], list)
        ):
            raise TaskGraphError("Remote Project snapshot has another shape")
        return cls(
            id=payload["id"],
            team_id=payload["team_id"],
            project_key=payload["project_key"],
            name=payload["name"],
            description=payload["description"],
            status_name=payload["status_name"],
            document_list=[
                RemoteDocument.from_payload(item) for item in payload["document_list"]
            ],
            issue_list=[
                RemoteIssue.from_payload(item) for item in payload["issue_list"]
            ],
        )

    def import_document_exact_require(self, view: GraphPublicationView) -> None:
        """Require exactly one immutable provider import document for a graph."""

        matching_document_list = [
            item
            for item in self.document_list
            if item.title == view.import_document_title
        ]
        if (
            len(matching_document_list) != 1
            or matching_document_list[0].content != view.import_document_content
        ):
            raise TaskGraphError(
                "Activated Linear Project import receipt differs from its immutable source"
            )

    def initial_identity_require(self, view: GraphPublicationView) -> None:
        """Require exact Project identity before an initial-import repair."""

        if (
            self.team_id != view.team_id
            or self.project_key != view.project_key
            or self.description != view.project_description
        ):
            raise TaskGraphError(
                "Linear Project key conflicts with the requested source identity"
            )
        if self.status_name == "Planned" and self.name != view.project_name:
            raise TaskGraphError(
                "Linear Project name conflicts with the approved graph"
            )

    def issue_by_node_key_map(self) -> dict[str, RemoteIssue]:
        """Return one unique node-key mapping from the complete issue list."""

        issue_by_node_key_map: dict[str, RemoteIssue] = {}
        for issue in self.issue_list:
            if not issue.node_key or issue.node_key in issue_by_node_key_map:
                raise TaskGraphError(
                    "Linear Project contains an absent or duplicate provider node key"
                )
            issue_by_node_key_map[issue.node_key] = issue
        return issue_by_node_key_map


@dataclass(frozen=True, slots=True)
class PublicationAction:
    """Describe one host-executed idempotent Linear mutation."""

    kind: str
    stable_key: str
    payload: dict[str, object]

    @classmethod
    def from_issue_create(
        cls, issue: IssuePublication, *, project_id: str
    ) -> "PublicationAction":
        """Create one inactive Backlog issue without dispatch metadata."""

        return cls(
            kind="issue-create",
            stable_key=issue.node_key,
            payload={
                "assignee_id": "",
                "delegate_id": "",
                "description": issue.description,
                "label_name_list": [],
                "node_key": issue.node_key,
                "project_id": project_id,
                "status_name": "Backlog",
                "title": issue.title,
            },
        )


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    """Contain only the next safe phase of one graph reconciliation."""

    phase: PublicationPhase
    action_list: list[PublicationAction]
    activation_ready: bool

    def __post_init__(self) -> None:
        """Detach the action sequence from caller mutation."""

        object.__setattr__(self, "action_list", list(self.action_list))

    def payload(self) -> dict[str, object]:
        """Return one JSON-ready plan."""

        return {
            "schema_version": 1,
            "activation_ready": self.activation_ready,
            "action_list": [
                {
                    "kind": item.kind,
                    "payload": item.payload,
                    "stable_key": item.stable_key,
                }
                for item in self.action_list
            ],
            "phase": self.phase,
        }
