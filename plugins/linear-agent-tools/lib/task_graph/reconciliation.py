"""Activation-barrier reconciliation for one Linear Project graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from collections.abc import Iterable

from task_graph.model import TaskGraph, TaskGraphError, TaskRole
from task_graph.publication import (
    GraphPublicationView,
    IssuePublication,
)

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_NODE_KEY_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PROJECT_KEY_PATTERN = re.compile(
    r"linear-agent-tools:v1:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{64}"
)
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


def _uuid_require(value: object, *, label: str) -> None:
    """Require one canonical lowercase UUID identity."""

    if not isinstance(value, str) or _UUID_PATTERN.fullmatch(value) is None:
        raise TaskGraphError(f"{label} must be one lowercase UUID")


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

    @classmethod
    def from_payload(cls, payload: object) -> "RemoteIssue":
        """Parse one strict remote issue snapshot.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed remote issue.
        """

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
        document_id_list = [item.id for item in self.document_list]
        if len(document_id_list) != len(set(document_id_list)):
            raise TaskGraphError("Remote Project repeats one document identity")
        if not isinstance(self.issue_list, list) or any(
            not isinstance(item, RemoteIssue) for item in self.issue_list
        ):
            raise TaskGraphError("Remote Project issue list has another shape")
        issue_id_list = [item.id for item in self.issue_list]
        if len(issue_id_list) != len(set(issue_id_list)):
            raise TaskGraphError("Remote Project repeats one issue identity")
        object.__setattr__(self, "document_list", list(self.document_list))
        object.__setattr__(self, "issue_list", list(self.issue_list))

    @classmethod
    def from_payload(cls, payload: object) -> "RemoteProject":
        """Parse one strict remote Project snapshot.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed remote Project.
        """

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


@dataclass(frozen=True, slots=True)
class PublicationAction:
    """Describe one host-executed idempotent Linear mutation."""

    kind: str
    stable_key: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    """Contain only the next safe phase of one graph import."""

    phase: PublicationPhase
    action_list: list[PublicationAction]
    activation_ready: bool

    def __post_init__(self) -> None:
        """Detach the action sequence from caller mutation."""

        object.__setattr__(self, "action_list", list(self.action_list))

    def payload(self) -> dict[str, object]:
        """Return one JSON-ready plan.

        Returns:
            The plan payload.
        """

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


def reconciliation_plan_build(
    graph: TaskGraph, remote: RemoteProject | None
) -> ReconciliationPlan:
    """Return the next safe activation-barrier phase after exact read-back.

    Args:
        graph: Approved desired graph.
        remote: Fully paginated current Project snapshot, or absence.

    Returns:
        Next phase and exact idempotent host actions.
    """

    view = GraphPublicationView.from_graph(graph)
    if remote is None:
        return ReconciliationPlan(
            phase=PublicationPhase.PROJECT,
            action_list=[
                PublicationAction(
                    kind="project-create",
                    stable_key=view.project_key,
                    payload={
                        "name": view.project_name,
                        "description": view.project_description,
                        "project_key": view.project_key,
                        "status_name": "Planned",
                        "team_id": graph.team_id,
                    },
                ),
            ],
            activation_ready=False,
        )
    _project_identity_require(view, remote)
    if remote.status_name in {"In Progress", "Completed", "Canceled"}:
        _import_document_exact_require(view, remote)
        return ReconciliationPlan(
            PublicationPhase.COMPLETE,
            [],
            activation_ready=remote.status_name == "In Progress",
        )
    if remote.status_name != "Planned":
        raise TaskGraphError(
            "Graph import Project must remain Planned until its activation transition"
        )
    matching_document_list = [
        item
        for item in remote.document_list
        if item.title == view.import_document_title
    ]
    if len(matching_document_list) > 1:
        raise TaskGraphError(
            "Linear Project contains duplicate import documents for the exact source"
        )
    if not matching_document_list:
        return ReconciliationPlan(
            PublicationPhase.DOCUMENT,
            [
                PublicationAction(
                    kind="import-document-create",
                    stable_key=view.project_key,
                    payload={
                        "content": view.import_document_content,
                        "project_id": remote.id,
                        "title": view.import_document_title,
                    },
                ),
            ],
            activation_ready=False,
        )
    current_document = matching_document_list[0]
    if current_document.content != view.import_document_content:
        provider_marker = "# Linear Agent Tools Import Plan\n"
        project_key_marker = f"- Project key: `{view.project_key}`"
        if (
            not current_document.content.startswith(provider_marker)
            or project_key_marker not in current_document.content
        ):
            raise TaskGraphError(
                "Linear Project import-document title collides with a foreign document"
            )
        return ReconciliationPlan(
            PublicationPhase.DOCUMENT,
            [
                PublicationAction(
                    kind="import-document-update",
                    stable_key=view.project_key,
                    payload={
                        "content": view.import_document_content,
                        "document_id": current_document.id,
                        "project_id": remote.id,
                        "title": view.import_document_title,
                    },
                ),
            ],
            activation_ready=False,
        )
    desired_issue_by_node_key_map = {item.node_key: item for item in view.issue_list}
    remote_issue_by_node_key_map = _remote_issue_map_get(remote.issue_list)
    unknown_key_set = set(remote_issue_by_node_key_map) - set(
        desired_issue_by_node_key_map
    )
    if unknown_key_set:
        raise TaskGraphError(
            f"Planned Project contains unknown issue keys: {sorted(unknown_key_set)}"
        )
    issue_action_list: list[PublicationAction] = []
    for node_key, desired in desired_issue_by_node_key_map.items():
        current = remote_issue_by_node_key_map.get(node_key)
        if current is None:
            issue_action_list.append(
                _issue_create_action(desired, project_id=remote.id)
            )
        else:
            _staged_issue_owned_fields_require(desired, current)
    if issue_action_list:
        return ReconciliationPlan(
            PublicationPhase.ISSUES, issue_action_list, activation_ready=False
        )
    relation_action_list: list[PublicationAction] = []
    for desired in view.issue_list:
        current = remote_issue_by_node_key_map[desired.node_key]
        desired_blocker_set = {
            edge.blocker_node_key
            for edge in view.blocker_edge_list
            if edge.blocked_node_key == desired.node_key
        }
        unknown_blocker_set = set(current.blocker_key_list) - desired_blocker_set
        if unknown_blocker_set:
            raise TaskGraphError(
                f"Issue {desired.node_key} contains unknown blocker relations: {sorted(unknown_blocker_set)}"
            )
        for blocker_key in sorted(desired_blocker_set - set(current.blocker_key_list)):
            if current.status_name != "Backlog":
                raise TaskGraphError(
                    f"Activated issue {desired.node_key} is missing an approved blocker relation"
                )
            relation_action_list.append(
                PublicationAction(
                    kind="blocker-create",
                    stable_key=f"{blocker_key}->{desired.node_key}",
                    payload={
                        "blocked_issue_id": current.id,
                        "blocker_issue_id": remote_issue_by_node_key_map[
                            blocker_key
                        ].id,
                    },
                )
            )
    if relation_action_list:
        return ReconciliationPlan(
            PublicationPhase.RELATIONS,
            relation_action_list,
            activation_ready=False,
        )
    metadata_action_list: list[PublicationAction] = []
    node_by_key_map = {item.node_key: item for item in graph.node_list}
    for desired in view.issue_list:
        current = remote_issue_by_node_key_map[desired.node_key]
        required_label_set = {node_by_key_map[desired.node_key].role}
        if node_by_key_map[desired.node_key].role is not TaskRole.HUMAN:
            required_label_set.add("agent:codex")
        current_role_label_set = set(current.label_name_list) & {
            "task:implementation",
            "task:review",
            "task:acceptance",
            "task:cleanup",
            "task:human",
        }
        if current_role_label_set - {node_by_key_map[desired.node_key].role}:
            raise TaskGraphError(
                f"Issue {desired.node_key} has a conflicting task role label"
            )
        if (
            node_by_key_map[desired.node_key].role is TaskRole.HUMAN
            and "agent:codex" in current.label_name_list
        ):
            raise TaskGraphError(
                f"Human issue {desired.node_key} has the forbidden dispatch label"
            )
        unexpected_label_set = set(current.label_name_list) - required_label_set
        if unexpected_label_set:
            raise TaskGraphError(
                f"Issue {desired.node_key} contains labels outside its approved activation metadata: "
                f"{sorted(unexpected_label_set)}"
            )
        missing_label_list = sorted(required_label_set - set(current.label_name_list))
        if (
            missing_label_list
            or current.assignee_id != desired.assignee_id
            or current.delegate_id != desired.delegate_id
        ):
            if current.status_name != "Backlog":
                raise TaskGraphError(
                    f"Activated issue {desired.node_key} has incomplete activation metadata"
                )
            metadata_action_list.append(
                PublicationAction(
                    kind="issue-activation-metadata-update",
                    stable_key=desired.node_key,
                    payload={
                        "assignee_id": desired.assignee_id,
                        "delegate_id": desired.delegate_id,
                        "issue_id": current.id,
                        "label_add_list": missing_label_list,
                    },
                )
            )
    if metadata_action_list:
        return ReconciliationPlan(
            PublicationPhase.NODE_METADATA,
            metadata_action_list,
            activation_ready=False,
        )
    activation_action_list: list[PublicationAction] = []
    for desired in view.issue_list:
        current = remote_issue_by_node_key_map[desired.node_key]
        if current.status_name == "Backlog":
            activation_action_list.append(
                PublicationAction(
                    kind="issue-status-update",
                    stable_key=desired.node_key,
                    payload={"issue_id": current.id, "status_name": "Todo"},
                )
            )
        elif current.status_name != "Todo":
            raise TaskGraphError(
                f"Staged issue {desired.node_key} has an invalid activation status"
            )
    if activation_action_list:
        return ReconciliationPlan(
            PublicationPhase.NODE_ACTIVATION,
            activation_action_list,
            activation_ready=False,
        )
    return ReconciliationPlan(
        PublicationPhase.PROJECT_ACTIVATION,
        [
            PublicationAction(
                kind="project-status-update",
                stable_key=view.project_key,
                payload={"project_id": remote.id, "status_name": "In Progress"},
            ),
        ],
        activation_ready=True,
    )


def activation_readback_require(
    graph: TaskGraph, remote: RemoteProject
) -> ReconciliationPlan:
    """Prove the exact complete graph immediately after its one-way activation handoff.

    This strict comparison is intentionally separate from ordinary reconciliation: after
    successful handoff, issue lifecycle fields belong to Linear and may legitimately move.
    """

    view = GraphPublicationView.from_graph(graph)
    _project_identity_require(view, remote)
    if remote.name != view.project_name or remote.status_name != "In Progress":
        raise TaskGraphError(
            "Activated Linear Project differs from the exact handoff state"
        )
    _import_document_exact_require(view, remote)
    desired_issue_by_node_key_map = {item.node_key: item for item in view.issue_list}
    remote_issue_by_node_key_map = _remote_issue_map_get(remote.issue_list)
    if set(remote_issue_by_node_key_map) != set(desired_issue_by_node_key_map):
        raise TaskGraphError(
            "Activated Linear Project issue set differs from the exact handoff graph"
        )
    blocker_key_set_by_node_key_map = {
        node_key: {
            edge.blocker_node_key
            for edge in view.blocker_edge_list
            if edge.blocked_node_key == node_key
        }
        for node_key in desired_issue_by_node_key_map
    }
    node_by_key_map = {item.node_key: item for item in graph.node_list}
    for node_key, desired in desired_issue_by_node_key_map.items():
        current = remote_issue_by_node_key_map[node_key]
        required_label_set = {node_by_key_map[node_key].role}
        if node_by_key_map[node_key].role is not TaskRole.HUMAN:
            required_label_set.add("agent:codex")
        if (
            current.title != desired.title
            or current.description != desired.description
            or current.status_name != "Todo"
            or set(current.label_name_list) != required_label_set
            or current.assignee_id != desired.assignee_id
            or current.delegate_id != desired.delegate_id
            or set(current.blocker_key_list)
            != blocker_key_set_by_node_key_map[node_key]
        ):
            raise TaskGraphError(
                f"Activated Linear issue {node_key} differs from the exact handoff graph"
            )
    return ReconciliationPlan(PublicationPhase.COMPLETE, [], activation_ready=True)


def cancellation_plan_build(
    graph: TaskGraph,
    remote: RemoteProject,
    *,
    human_decision: bool,
) -> ReconciliationPlan:
    """Stop dispatch and cancel every unfinished issue in one exact Project.

    The Project transition is deliberately first.  It makes every issue
    non-dispatchable before the non-atomic issue-status mutations begin.
    """

    if not isinstance(human_decision, bool) or not human_decision:
        raise TaskGraphError("Project cancellation requires an explicit human decision")
    view = GraphPublicationView.from_graph(graph)
    _project_identity_require(view, remote)
    if remote.status_name == "Completed":
        raise TaskGraphError(
            "A completed Linear Project cannot be canceled or reopened"
        )
    if remote.status_name in {"Planned", "In Progress"}:
        return ReconciliationPlan(
            PublicationPhase.PROJECT_CANCELLATION,
            [
                PublicationAction(
                    kind="project-status-update",
                    stable_key=view.project_key,
                    payload={"project_id": remote.id, "status_name": "Canceled"},
                ),
            ],
            activation_ready=False,
        )
    if remote.status_name != "Canceled":
        raise TaskGraphError(
            "Linear Project cancellation encountered an unsupported status"
        )
    action_list = [
        PublicationAction(
            kind="issue-status-update",
            stable_key=f"{issue.node_key}:{issue.id}",
            payload={"issue_id": issue.id, "status_name": "Canceled"},
        )
        for issue in sorted(remote.issue_list, key=lambda item: item.id)
        if issue.status_name not in {"Done", "Canceled"}
    ]
    if action_list:
        return ReconciliationPlan(
            PublicationPhase.NODE_CANCELLATION, action_list, activation_ready=False
        )
    return ReconciliationPlan(PublicationPhase.COMPLETE, [], activation_ready=False)


def _project_identity_require(
    view: GraphPublicationView, remote: RemoteProject
) -> None:
    """Require exact Project identity before any import repair.

    Args:
        view: Desired publication view.
        remote: Current Project snapshot.
    """

    if (
        remote.team_id != view.team_id
        or remote.project_key != view.project_key
        or remote.description != view.project_description
    ):
        raise TaskGraphError(
            "Linear Project key conflicts with the requested source identity"
        )
    if remote.status_name == "Planned" and remote.name != view.project_name:
        raise TaskGraphError("Linear Project name conflicts with the approved graph")


def _import_document_exact_require(
    view: GraphPublicationView, remote: RemoteProject
) -> None:
    """Require exactly one immutable provider import document for this graph."""

    matching_document_list = [
        item
        for item in remote.document_list
        if item.title == view.import_document_title
    ]
    if (
        len(matching_document_list) != 1
        or matching_document_list[0].content != view.import_document_content
    ):
        raise TaskGraphError(
            "Activated Linear Project import receipt differs from its immutable source"
        )


def _remote_issue_map_get(issue_list: Iterable[RemoteIssue]) -> dict[str, RemoteIssue]:
    """Return one unique node-key mapping from a complete remote issue list.

    Args:
        issue_list: Fully paginated Project issues.

    Returns:
        Unique issue mapping.
    """

    issue_by_node_key_map: dict[str, RemoteIssue] = {}
    for issue in issue_list:
        if not issue.node_key or issue.node_key in issue_by_node_key_map:
            raise TaskGraphError(
                "Linear Project contains an absent or duplicate provider node key"
            )
        issue_by_node_key_map[issue.node_key] = issue
    return issue_by_node_key_map


def _issue_create_action(
    issue: IssuePublication, *, project_id: str
) -> PublicationAction:
    """Return one Backlog issue-create action without dispatch metadata.

    Args:
        issue: Desired issue content.
        project_id: Exact owning Project ID.

    Returns:
        The create action.
    """

    return PublicationAction(
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


def _staged_issue_owned_fields_require(
    desired: IssuePublication, current: RemoteIssue
) -> None:
    """Reject a stable key collision with different provider-owned content.

    Args:
        desired: Desired issue content.
        current: Current external issue.
    """

    if current.title != desired.title or current.description != desired.description:
        raise TaskGraphError(
            f"Issue {desired.node_key} conflicts with its stable source key"
        )
    if current.status_name not in {"Backlog", "Todo"}:
        raise TaskGraphError(
            f"Staged issue {desired.node_key} has an invalid pre-activation status"
        )
