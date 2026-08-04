"""Approved Linear-native changes to one active Project graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from task_graph.model import (
    SourceIdentity,
    TaskBlockerEdge,
    TaskGraphError,
    TaskNode,
    TaskRole,
    canonical_sha256,
)

_UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_NODE_KEY_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class DeltaProvenanceKind(StrEnum):
    """Supported visible origins of an approved active-Project change."""

    LINEAR_DECISION = "linear-decision"
    FINDING = "finding"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class DeltaProvenance:
    """Identify the exact Linear decision, finding or manual source of a delta."""

    kind: DeltaProvenanceKind
    canonical_url: str
    revision: str
    decision: str

    def __post_init__(self) -> None:
        """Validate one visible immutable provenance record."""

        if not isinstance(self.kind, DeltaProvenanceKind):
            raise TaskGraphError("Delta provenance kind is unsupported")
        for label, value in (
            ("canonical_url", self.canonical_url),
            ("revision", self.revision),
            ("decision", self.decision),
        ):
            if not isinstance(value, str) or not value.strip() or any(character in value for character in "\x00\r\n"):
                raise TaskGraphError(f"Delta provenance {label} must be non-empty single-line text")

    @classmethod
    def from_payload(cls, payload: object) -> "DeltaProvenance":
        """Parse one strict provenance payload.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed provenance.
        """

        if not isinstance(payload, dict) or set(payload) != {
            "kind",
            "canonical_url",
            "revision",
            "decision",
        }:
            raise TaskGraphError("Delta provenance has another shape")
        try:
            return cls(
                kind=DeltaProvenanceKind(payload["kind"]),
                canonical_url=payload["canonical_url"],
                revision=payload["revision"],
                decision=payload["decision"],
            )
        except (TypeError, ValueError) as error:
            raise TaskGraphError("Delta provenance contains an unsupported field value") from error

    def payload(self) -> dict[str, str]:
        """Return one canonical JSON-ready record.

        Returns:
            Provenance object.
        """

        return {
            "canonical_url": self.canonical_url,
            "decision": self.decision,
            "kind": self.kind,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class TaskGraphDelta:
    """Own one approved additive graph delta for an active Linear Project."""

    team_id: str
    project_id: str
    project_key: str
    source: SourceIdentity
    provenance: DeltaProvenance
    existing_node_key_list: list[str]
    reverification_node_key_list: list[str]
    node_list: list[TaskNode]
    blocker_edge_list: list[TaskBlockerEdge]

    def __post_init__(self) -> None:
        """Validate identity, additive scope and complete relation declarations."""

        if (
            not isinstance(self.team_id, str)
            or not isinstance(self.project_id, str)
            or _UUID_PATTERN.fullmatch(self.team_id) is None
            or _UUID_PATTERN.fullmatch(self.project_id) is None
        ):
            raise TaskGraphError("Delta team_id and project_id must be lowercase Linear UUIDs")
        if not isinstance(self.source, SourceIdentity) or not isinstance(self.provenance, DeltaProvenance):
            raise TaskGraphError("Graph delta source or provenance has another shape")
        if not isinstance(self.project_key, str):
            raise TaskGraphError("Graph delta Project key must be text")
        expected_project_key = f"linear-agent-tools:v1:{self.team_id}:{self.source.fingerprint()}"
        if self.project_key != expected_project_key:
            raise TaskGraphError("Delta Project key differs from its immutable original source")
        if not isinstance(self.node_list, list) or any(not isinstance(item, TaskNode) for item in self.node_list):
            raise TaskGraphError("Graph delta node list must contain only task nodes")
        if not self.node_list:
            raise TaskGraphError("Graph delta must add at least one complete node")
        new_node_by_key_map = {item.node_key: item for item in self.node_list}
        if len(new_node_by_key_map) != len(self.node_list):
            raise TaskGraphError("Graph delta repeats one new node key")
        if any(item.role is TaskRole.CLEANUP for item in self.node_list):
            raise TaskGraphError("An active Project delta must reuse its one existing final cleanup node")
        resource_key_list = [resource.key for node in self.node_list for resource in node.resource_list]
        if len(resource_key_list) != len(set(resource_key_list)):
            raise TaskGraphError("Graph delta repeats one resource key across new issue owners")
        if (
            not isinstance(self.existing_node_key_list, list)
            or any(not isinstance(item, str) for item in self.existing_node_key_list)
            or not self.existing_node_key_list
            or len(self.existing_node_key_list) != len(set(self.existing_node_key_list))
        ):
            raise TaskGraphError("Graph delta must name a duplicate-free existing node set")
        if any(_NODE_KEY_PATTERN.fullmatch(item) is None for item in self.existing_node_key_list):
            raise TaskGraphError("Graph delta existing node keys must be lowercase semantic slugs")
        if set(new_node_by_key_map) & set(self.existing_node_key_list):
            raise TaskGraphError("Graph delta new and existing node identities overlap")
        if (
            not isinstance(self.reverification_node_key_list, list)
            or any(not isinstance(item, str) for item in self.reverification_node_key_list)
            or len(self.reverification_node_key_list) != len(set(self.reverification_node_key_list))
        ):
            raise TaskGraphError("Graph delta reverification node keys must be a duplicate-free list")
        if any(_NODE_KEY_PATTERN.fullmatch(item) is None for item in self.reverification_node_key_list):
            raise TaskGraphError("Graph delta reverification node keys must be lowercase semantic slugs")
        unknown_reverification_key_set = set(self.reverification_node_key_list) - set(self.existing_node_key_list)
        if unknown_reverification_key_set:
            raise TaskGraphError(
                "Graph delta reverification references absent existing nodes: "
                + ", ".join(sorted(unknown_reverification_key_set))
            )
        if not isinstance(self.blocker_edge_list, list) or any(
            not isinstance(item, TaskBlockerEdge) for item in self.blocker_edge_list
        ):
            raise TaskGraphError("Graph delta blocker edge list must contain only named blocker edges")
        edge_set = set(self.blocker_edge_list)
        if len(edge_set) != len(self.blocker_edge_list):
            raise TaskGraphError("Graph delta repeats one blocker edge")
        known_key_set = set(new_node_by_key_map) | set(self.existing_node_key_list)
        for edge in self.blocker_edge_list:
            edge_node_key_set = {edge.blocker_node_key, edge.blocked_node_key}
            if not edge_node_key_set <= known_key_set:
                raise TaskGraphError("Graph delta contains a malformed or unknown blocker edge")
            if not edge_node_key_set & set(new_node_by_key_map):
                raise TaskGraphError("Graph delta may not mutate relations solely between existing nodes")
        new_node_key_set = set(new_node_by_key_map)
        for node_key in self.reverification_node_key_list:
            if not any(
                edge.blocked_node_key == node_key and edge.blocker_node_key in new_node_key_set for edge in edge_set
            ):
                raise TaskGraphError(f"Graph delta reverification node {node_key} must receive one new blocker")
        for node in self.node_list:
            declared_incoming = {edge for edge in edge_set if edge.blocked_node_key == node.node_key}
            expected_incoming = {
                TaskBlockerEdge(blocker_node_key=blocker_key, blocked_node_key=node.node_key)
                for blocker_key in node.blocker_key_list
            }
            if declared_incoming != expected_incoming:
                raise TaskGraphError(f"Delta node {node.node_key} blocker list differs from relation additions")
            for resource in node.resource_list:
                unknown_consumer_set = set(resource.consumer_node_key_list) - known_key_set
                if unknown_consumer_set:
                    raise TaskGraphError(
                        f"Delta resource {resource.key} has unknown consumers: {sorted(unknown_consumer_set)}"
                    )
                if node.node_key in resource.consumer_node_key_list:
                    raise TaskGraphError(f"Delta resource {resource.key} repeats its implicit owner as a consumer")
        object.__setattr__(self, "existing_node_key_list", list(self.existing_node_key_list))
        object.__setattr__(
            self,
            "reverification_node_key_list",
            list(self.reverification_node_key_list),
        )
        object.__setattr__(self, "node_list", list(self.node_list))
        object.__setattr__(self, "blocker_edge_list", list(self.blocker_edge_list))

    def fingerprint(self) -> str:
        """Return the canonical approved-delta fingerprint.

        Returns:
            Lowercase SHA-256 identity.
        """

        return canonical_sha256(self.normalized_payload())

    def normalized_payload(self) -> dict[str, object]:
        """Return the exact content used by approval and reconciliation.

        Returns:
            JSON-ready normalized delta.
        """

        return {
            "schema_version": 1,
            "blocker_edge_list": [
                item.payload()
                for item in sorted(
                    self.blocker_edge_list,
                    key=lambda item: (item.blocker_node_key, item.blocked_node_key),
                )
            ],
            "existing_node_key_list": sorted(self.existing_node_key_list),
            "reverification_node_key_list": sorted(self.reverification_node_key_list),
            "node_list": [item.payload() for item in sorted(self.node_list, key=lambda item: item.node_key)],
            "project_id": self.project_id,
            "project_key": self.project_key,
            "provenance": self.provenance.payload(),
            "source": {
                "canonical_url": self.source.canonical_url,
                "fingerprint": self.source.fingerprint(),
                "kind": self.source.kind,
                "outcome": self.source.outcome,
                "revision": self.source.revision,
            },
            "team_id": self.team_id,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "TaskGraphDelta":
        """Parse one strict complete delta payload.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed approved delta.
        """

        expected = {
            "schema_version",
            "team_id",
            "project_id",
            "project_key",
            "source",
            "provenance",
            "existing_node_key_list",
            "reverification_node_key_list",
            "node_list",
            "blocker_edge_list",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise TaskGraphError("Task graph delta payload has another shape")
        for name in (
            "existing_node_key_list",
            "reverification_node_key_list",
            "node_list",
            "blocker_edge_list",
        ):
            if not isinstance(payload[name], list):
                raise TaskGraphError(f"Task graph delta {name} must be a list")
        for name in ("existing_node_key_list", "reverification_node_key_list"):
            if any(not isinstance(item, str) for item in payload[name]):
                raise TaskGraphError(f"Task graph delta {name} must contain text")
        try:
            return cls(
                team_id=payload["team_id"],
                project_id=payload["project_id"],
                project_key=payload["project_key"],
                source=SourceIdentity.from_payload(payload["source"]),
                provenance=DeltaProvenance.from_payload(payload["provenance"]),
                existing_node_key_list=list(payload["existing_node_key_list"]),
                reverification_node_key_list=list(payload["reverification_node_key_list"]),
                node_list=[TaskNode.from_payload(item) for item in payload["node_list"]],
                blocker_edge_list=[TaskBlockerEdge.from_payload(item) for item in payload["blocker_edge_list"]],
            )
        except (TypeError, ValueError) as error:
            raise TaskGraphError("Task graph delta contains an unsupported enum or field value") from error
