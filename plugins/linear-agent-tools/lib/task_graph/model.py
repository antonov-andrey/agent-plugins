"""Closed source-independent task-graph domain models."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from urllib.parse import unquote, urlsplit

from git_origin.identity import GitOriginError, origin_identity_get
from task_graph.topology import cycle_node_key_get, exist_ordered_role_path, exist_path

_KEY_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
_PROJECT_GOALS_PREFIX_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(?:-[a-z0-9]+)*")


class TaskGraphError(RuntimeError):
    """Report one malformed or conflicting task graph."""


class TaskRole(StrEnum):
    """Transparent role labels for Linear issues."""

    IMPLEMENTATION = "task:implementation"
    REVIEW = "task:review"
    ACCEPTANCE = "task:acceptance"
    CLEANUP = "task:cleanup"
    HUMAN = "task:human"


class DeliveryKind(StrEnum):
    """Observable delivery kinds owned by graph issues."""

    CODE = "code"
    EVIDENCE = "evidence"
    CLEANUP = "cleanup"
    HUMAN = "human"


class ResourceLifetime(StrEnum):
    """Exact lifetime of one task-owned resource."""

    ATTEMPT = "attempt"
    ISSUE = "issue"
    PROJECT = "project"


class VerificationKind(StrEnum):
    """Stable levels used to build dependency-aware verification plans."""

    TARGETED = "targeted"
    FULL = "full"
    LIVE = "live"
    SEMANTIC = "semantic"


@dataclass(frozen=True, slots=True)
class TaskBlockerEdge:
    """Identify one directed blocker relation without positional tuple semantics."""

    blocker_node_key: str
    blocked_node_key: str

    def __post_init__(self) -> None:
        """Validate both endpoint identities and reject a self-edge."""

        if (
            not isinstance(self.blocker_node_key, str)
            or not isinstance(self.blocked_node_key, str)
            or _KEY_PATTERN.fullmatch(self.blocker_node_key) is None
            or _KEY_PATTERN.fullmatch(self.blocked_node_key) is None
            or self.blocker_node_key == self.blocked_node_key
        ):
            raise TaskGraphError("Task blocker edge must join two distinct lowercase semantic slugs")

    def payload(self) -> dict[str, str]:
        """Return one canonical JSON-ready edge object."""

        return {
            "blocked_node_key": self.blocked_node_key,
            "blocker_node_key": self.blocker_node_key,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "TaskBlockerEdge":
        """Parse one strict named blocker-edge object."""

        if not isinstance(payload, dict) or set(payload) != {
            "blocker_node_key",
            "blocked_node_key",
        }:
            raise TaskGraphError("Task blocker edge has another shape")
        return cls(
            blocker_node_key=payload["blocker_node_key"],
            blocked_node_key=payload["blocked_node_key"],
        )


_DELIVERY_KIND_SET_BY_ROLE_MAP = {
    TaskRole.IMPLEMENTATION: frozenset({DeliveryKind.CODE, DeliveryKind.EVIDENCE}),
    TaskRole.REVIEW: frozenset({DeliveryKind.EVIDENCE}),
    TaskRole.ACCEPTANCE: frozenset({DeliveryKind.EVIDENCE}),
    TaskRole.CLEANUP: frozenset({DeliveryKind.CLEANUP}),
    TaskRole.HUMAN: frozenset({DeliveryKind.HUMAN}),
}


def _text_validate(value: object, *, label: str, multiline: bool = False) -> str:
    """Return one required text value.

    Args:
        value: Candidate value.
        label: Diagnostic owner label.
        multiline: Whether line breaks are allowed.

    Returns:
        The validated text.
    """

    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise TaskGraphError(f"{label} must be non-empty text")
    if not multiline and any(character in value for character in ("\n", "\r")):
        raise TaskGraphError(f"{label} must be single-line text")
    return value


def _origin_identity_validate(value: object, *, label: str) -> str:
    """Return one safe normalized repository identity without leaking its input."""

    origin_url = _text_validate(value, label=label)
    try:
        return origin_identity_get(origin_url)
    except GitOriginError as error:
        raise TaskGraphError(f"{label} is unsafe or unsupported") from error


def _text_list_parse(value: object, *, label: str, empty_allowed: bool = False) -> list[str]:
    """Return one duplicate-free ordered text list.

    Args:
        value: Candidate list.
        label: Diagnostic owner label.
        empty_allowed: Whether an empty collection is valid.

    Returns:
        Validated values.
    """

    if not isinstance(value, list) or (not value and not empty_allowed):
        raise TaskGraphError(f"{label} must be a {'possibly empty ' if empty_allowed else 'non-empty '}list")
    result_list = [_text_validate(item, label=label) for item in value]
    if len(result_list) != len(set(result_list)):
        raise TaskGraphError(f"{label} must not contain duplicates")
    return result_list


def _text_list_validate(
    value: object,
    *,
    label: str,
    empty_allowed: bool = False,
) -> None:
    """Validate one already-typed text list."""

    if not isinstance(value, list) or (not value and not empty_allowed):
        raise TaskGraphError(f"{label} must be a {'possibly empty ' if empty_allowed else 'non-empty '}list")
    for item in value:
        _text_validate(item, label=label)
    if len(value) != len(set(value)):
        raise TaskGraphError(f"{label} must not contain duplicates")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Bind one immutable source snapshot independent of its producer."""

    kind: str
    canonical_url: str
    revision: str
    outcome: str
    content: str

    def __post_init__(self) -> None:
        """Validate the complete source identity and content."""

        _text_validate(self.kind, label="Source kind")
        _text_validate(self.canonical_url, label="Source canonical URL")
        _text_validate(self.revision, label="Source revision")
        _text_validate(self.outcome, label="Source outcome")
        _text_validate(self.content, label="Source content", multiline=True)
        if self.kind == "project-goals":
            if _COMMIT_PATTERN.fullmatch(self.revision) is None:
                raise TaskGraphError("A project-goals source revision must be one full Git commit")
            _project_goals_url_require(self.canonical_url, revision=self.revision)

    def fingerprint(self) -> str:
        """Return SHA-256 of canonical source identity and complete content.

        Returns:
            Lowercase source fingerprint.
        """

        return canonical_sha256(
            {
                "canonical_url": self.canonical_url,
                "content": self.content,
                "kind": self.kind,
                "outcome": self.outcome,
                "revision": self.revision,
            }
        )

    @classmethod
    def from_payload(cls, payload: object) -> "SourceIdentity":
        """Parse one strict source payload.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed source identity.
        """

        expected = {"kind", "canonical_url", "revision", "outcome", "content"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise TaskGraphError("Source payload has another shape")
        return cls(**payload)


def _project_goals_url_require(canonical_url: str, *, revision: str) -> None:
    """Require one exact commit-pinned project-goals task-directory URL.

    Args:
        canonical_url: Candidate immutable source-directory URL.
        revision: Exact full source commit.
    """

    parsed = urlsplit(canonical_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or parsed.query or parsed.fragment:
        raise TaskGraphError("A project-goals canonical URL must be one clean HTTPS GitHub URL")
    path_part_list = [unquote(item) for item in parsed.path.split("/") if item]
    if (
        len(path_part_list) != 5
        or path_part_list[1] != "project-goals"
        or path_part_list[2] != "tree"
        or path_part_list[3] != revision
        or _PROJECT_GOALS_PREFIX_PATTERN.fullmatch(path_part_list[4]) is None
        or any(
            item in {".", ".."} or not item or "/" in item or "\\" in item or "\x00" in item
            for item in path_part_list[4:]
        )
    ):
        raise TaskGraphError("A project-goals canonical URL must identify its exact commit-pinned task directory")


@dataclass(frozen=True, slots=True)
class RepositoryTarget:
    """Describe one canonical repository and intended base branch."""

    origin_url: str
    base_branch: str
    merge_method: str

    def __post_init__(self) -> None:
        """Validate one repository target."""

        _origin_identity_validate(self.origin_url, label="Repository origin URL")
        _text_validate(self.base_branch, label="Repository base branch")
        if (
            self.base_branch.startswith("-")
            or ".." in self.base_branch
            or any(character.isspace() for character in self.base_branch)
        ):
            raise TaskGraphError("Repository base branch is unsafe")
        if self.merge_method not in {"merge", "squash", "rebase"}:
            raise TaskGraphError("Repository merge method is unsupported")

    @classmethod
    def from_payload(cls, payload: object) -> "RepositoryTarget":
        """Parse one strict repository target.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed repository target.
        """

        if not isinstance(payload, dict) or set(payload) != {
            "origin_url",
            "base_branch",
            "merge_method",
        }:
            raise TaskGraphError("Repository target has another shape")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class VerificationStep:
    """Describe one project-local direct-argv verification requirement."""

    key: str
    kind: VerificationKind
    repository_url: str
    working_directory: str
    command_argument_list: list[str]
    dependency_path_list: list[str]
    environment_identity_required: bool

    def __post_init__(self) -> None:
        """Validate one verification requirement."""

        if _KEY_PATTERN.fullmatch(self.key) is None:
            raise TaskGraphError("Verification key must be a lowercase semantic slug")
        if not isinstance(self.kind, VerificationKind):
            raise TaskGraphError("Verification kind is unsupported")
        if self.repository_url:
            _text_validate(self.repository_url, label="Verification repository URL")
        _text_validate(self.working_directory, label="Verification working directory")
        if not isinstance(self.command_argument_list, list) or not self.command_argument_list:
            raise TaskGraphError("Verification command_argument_list must be a non-empty list")
        for argument in self.command_argument_list:
            _text_validate(argument, label="Verification direct argument")
        _text_list_validate(
            self.dependency_path_list,
            label="Verification dependency paths",
            empty_allowed=True,
        )
        if not isinstance(self.environment_identity_required, bool):
            raise TaskGraphError("environment_identity_required must be boolean")
        object.__setattr__(self, "command_argument_list", list(self.command_argument_list))
        object.__setattr__(self, "dependency_path_list", list(self.dependency_path_list))

    @classmethod
    def from_payload(cls, payload: object) -> "VerificationStep":
        """Parse one strict verification requirement.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed verification step.
        """

        expected = {
            "key",
            "kind",
            "repository_url",
            "working_directory",
            "command_argument_list",
            "dependency_path_list",
            "environment_identity_required",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise TaskGraphError("Verification step has another shape")
        return cls(
            key=payload["key"],
            kind=VerificationKind(payload["kind"]),
            repository_url=payload["repository_url"],
            working_directory=payload["working_directory"],
            command_argument_list=_text_list_parse(payload["command_argument_list"], label="Verification argv"),
            dependency_path_list=_text_list_parse(
                payload["dependency_path_list"],
                label="Verification dependency paths",
                empty_allowed=True,
            ),
            environment_identity_required=payload["environment_identity_required"],
        )


@dataclass(frozen=True, slots=True)
class ResourceDeclaration:
    """Bind one exact task-owned resource and its cleanup operation."""

    key: str
    lifetime: ResourceLifetime
    owner_identity: str
    repository_url: str
    cleanup_argument_list: list[str]
    consumer_node_key_list: list[str]

    def __post_init__(self) -> None:
        """Validate one exact resource declaration."""

        if _KEY_PATTERN.fullmatch(self.key) is None:
            raise TaskGraphError("Resource key must be a lowercase semantic slug")
        if not isinstance(self.lifetime, ResourceLifetime):
            raise TaskGraphError("Resource lifetime is unsupported")
        _text_validate(self.owner_identity, label="Resource owner identity")
        _text_validate(self.repository_url, label="Resource repository URL")
        if not isinstance(self.cleanup_argument_list, list) or not self.cleanup_argument_list:
            raise TaskGraphError("Resource cleanup must use one non-empty direct-argv list")
        for argument in self.cleanup_argument_list:
            _text_validate(argument, label="Resource cleanup direct argument")
        _text_list_validate(
            self.consumer_node_key_list,
            label="Resource consumer node keys",
            empty_allowed=True,
        )
        if any(_KEY_PATTERN.fullmatch(item) is None for item in self.consumer_node_key_list):
            raise TaskGraphError("Resource consumer node keys must be lowercase semantic slugs")
        if self.lifetime is not ResourceLifetime.ISSUE and self.consumer_node_key_list:
            raise TaskGraphError("Only an issue-lifetime resource may declare downstream consumers")
        object.__setattr__(self, "cleanup_argument_list", list(self.cleanup_argument_list))
        object.__setattr__(self, "consumer_node_key_list", list(self.consumer_node_key_list))

    def fingerprint(self) -> str:
        """Return the exact durable cleanup-declaration identity.

        Returns:
            Lowercase SHA-256 over every behavior-owning field.
        """

        return canonical_sha256(
            {
                "cleanup_argument_list": list(self.cleanup_argument_list),
                "consumer_node_key_list": list(self.consumer_node_key_list),
                "key": self.key,
                "lifetime": self.lifetime,
                "owner_identity": self.owner_identity,
                "repository_url": self.repository_url,
            }
        )

    @classmethod
    def from_payload(cls, payload: object) -> "ResourceDeclaration":
        """Parse one strict resource declaration.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed resource declaration.
        """

        expected = {
            "key",
            "lifetime",
            "owner_identity",
            "repository_url",
            "cleanup_argument_list",
            "consumer_node_key_list",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise TaskGraphError("Resource declaration has another shape")
        return cls(
            key=payload["key"],
            lifetime=ResourceLifetime(payload["lifetime"]),
            owner_identity=payload["owner_identity"],
            repository_url=payload["repository_url"],
            cleanup_argument_list=_text_list_parse(payload["cleanup_argument_list"], label="Resource cleanup argv"),
            consumer_node_key_list=_text_list_parse(
                payload["consumer_node_key_list"],
                label="Resource consumer node keys",
                empty_allowed=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class TaskNode:
    """Own one bounded executable or human Linear issue contract."""

    node_key: str
    title: str
    outcome: str
    scope_list: list[str]
    non_goal_list: list[str]
    role: TaskRole
    delivery_kind: DeliveryKind
    assignee_id: str
    delegate_id: str
    repository_list: list[RepositoryTarget]
    partial_merge_recovery: str
    required_contract_list: list[str]
    required_skill_list: list[str]
    blocker_key_list: list[str]
    resource_list: list[ResourceDeclaration]
    verification_list: list[VerificationStep]
    human_decision_boundary: str
    source_section_list: list[str]

    def __post_init__(self) -> None:
        """Validate one complete issue contract."""

        if _KEY_PATTERN.fullmatch(self.node_key) is None:
            raise TaskGraphError("Node key must be a lowercase semantic slug")
        _text_validate(self.title, label="Task title")
        _text_validate(self.outcome, label="Task outcome", multiline=True)
        _text_list_validate(self.scope_list, label="Task scope")
        _text_list_validate(self.non_goal_list, label="Task non-goals", empty_allowed=True)
        if not isinstance(self.role, TaskRole) or self.delivery_kind not in _DELIVERY_KIND_SET_BY_ROLE_MAP[self.role]:
            raise TaskGraphError("Task role and delivery kind are incompatible")
        assignment_id_list = [value for value in (self.assignee_id, self.delegate_id) if value]
        if len(assignment_id_list) != 1 or _UUID_PATTERN.fullmatch(assignment_id_list[0]) is None:
            raise TaskGraphError("Task must have exactly one lowercase Linear assignee or delegate UUID")
        if not isinstance(self.repository_list, list) or any(
            not isinstance(item, RepositoryTarget) for item in self.repository_list
        ):
            raise TaskGraphError("Task repository list must contain only repository targets")
        if self.delivery_kind is DeliveryKind.CODE and not self.repository_list:
            raise TaskGraphError("Code delivery requires at least one canonical repository")
        repository_identity_list = [
            _origin_identity_validate(item.origin_url, label="Repository origin URL") for item in self.repository_list
        ]
        if len(repository_identity_list) != len(set(repository_identity_list)):
            raise TaskGraphError("Task repeats one repository target")
        if not isinstance(self.resource_list, list) or any(
            not isinstance(item, ResourceDeclaration) for item in self.resource_list
        ):
            raise TaskGraphError("Task resource list must contain only resource declarations")
        if not isinstance(self.verification_list, list) or any(
            not isinstance(item, VerificationStep) for item in self.verification_list
        ):
            raise TaskGraphError("Task verification list must contain only verification steps")
        repository_identity_set = set(repository_identity_list)
        if any(
            item.repository_url
            and _origin_identity_validate(item.repository_url, label="Verification repository URL")
            not in repository_identity_set
            for item in self.verification_list
        ):
            raise TaskGraphError("Verification repository must be an explicit task repository target")
        if any(
            _origin_identity_validate(item.repository_url, label="Resource repository URL")
            not in repository_identity_set
            for item in self.resource_list
        ):
            raise TaskGraphError("Resource repository must be an explicit task repository target")
        if self.delivery_kind is DeliveryKind.CODE and len(self.repository_list) > 1:
            _text_validate(
                self.partial_merge_recovery,
                label="Cross-repository partial merge recovery",
                multiline=True,
            )
        elif self.partial_merge_recovery != "":
            raise TaskGraphError("Partial merge recovery is valid only for cross-repository code delivery")
        _text_list_validate(self.required_contract_list, label="Required contracts")
        _text_list_validate(self.required_skill_list, label="Required skills", empty_allowed=True)
        _text_list_validate(self.blocker_key_list, label="Task blockers", empty_allowed=True)
        if not self.verification_list:
            raise TaskGraphError("Task must define observable verification")
        if len(self.blocker_key_list) != len(set(self.blocker_key_list)) or self.node_key in self.blocker_key_list:
            raise TaskGraphError("Task blockers must be unique and must not contain the task itself")
        resource_key_list = [item.key for item in self.resource_list]
        if len(resource_key_list) != len(set(resource_key_list)):
            raise TaskGraphError("Task repeats one resource key")
        verification_key_list = [item.key for item in self.verification_list]
        if len(verification_key_list) != len(set(verification_key_list)):
            raise TaskGraphError("Task repeats one verification key")
        if self.human_decision_boundary:
            _text_validate(
                self.human_decision_boundary,
                label="Human decision boundary",
                multiline=True,
            )
            if self.role not in {TaskRole.ACCEPTANCE, TaskRole.HUMAN}:
                raise TaskGraphError("Human decision boundary is valid only for acceptance and human tasks")
        elif self.role in {TaskRole.ACCEPTANCE, TaskRole.HUMAN}:
            raise TaskGraphError("Acceptance and human tasks require the final human decision boundary")
        _text_list_validate(self.source_section_list, label="Source sections")
        object.__setattr__(self, "scope_list", list(self.scope_list))
        object.__setattr__(self, "non_goal_list", list(self.non_goal_list))
        object.__setattr__(self, "repository_list", list(self.repository_list))
        object.__setattr__(self, "required_contract_list", list(self.required_contract_list))
        object.__setattr__(self, "required_skill_list", list(self.required_skill_list))
        object.__setattr__(self, "blocker_key_list", list(self.blocker_key_list))
        object.__setattr__(self, "resource_list", list(self.resource_list))
        object.__setattr__(self, "verification_list", list(self.verification_list))
        object.__setattr__(self, "source_section_list", list(self.source_section_list))

    def can_agent_execute(self) -> bool:
        """Return whether the task receives the dispatch label.

        Returns:
            Whether a Codex agent may execute the task.
        """

        return self.role is not TaskRole.HUMAN

    def payload(self) -> dict[str, object]:
        """Return one canonical normalized task node object.

        Returns:
            JSON-ready node object.
        """

        return {
            "can_agent_execute": self.can_agent_execute(),
            "assignee_id": self.assignee_id,
            "delegate_id": self.delegate_id,
            "blocker_key_list": list(self.blocker_key_list),
            "delivery_kind": self.delivery_kind,
            "human_decision_boundary": self.human_decision_boundary,
            "node_key": self.node_key,
            "non_goal_list": list(self.non_goal_list),
            "outcome": self.outcome,
            "repository_list": [
                {
                    "base_branch": item.base_branch,
                    "merge_method": item.merge_method,
                    "origin_url": item.origin_url,
                }
                for item in self.repository_list
            ],
            "partial_merge_recovery": self.partial_merge_recovery,
            "required_contract_list": list(self.required_contract_list),
            "required_skill_list": list(self.required_skill_list),
            "resource_list": [
                {
                    "cleanup_argument_list": list(item.cleanup_argument_list),
                    "consumer_node_key_list": list(item.consumer_node_key_list),
                    "key": item.key,
                    "lifetime": item.lifetime,
                    "owner_identity": item.owner_identity,
                    "repository_url": item.repository_url,
                }
                for item in self.resource_list
            ],
            "role": self.role,
            "scope_list": list(self.scope_list),
            "source_section_list": list(self.source_section_list),
            "title": self.title,
            "verification_list": [
                {
                    "command_argument_list": list(item.command_argument_list),
                    "dependency_path_list": list(item.dependency_path_list),
                    "environment_identity_required": item.environment_identity_required,
                    "key": item.key,
                    "kind": item.kind,
                    "repository_url": item.repository_url,
                    "working_directory": item.working_directory,
                }
                for item in self.verification_list
            ],
        }

    @classmethod
    def from_payload(cls, payload: object) -> "TaskNode":
        """Parse one strict task node payload.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed task node.
        """

        expected = {
            "node_key",
            "title",
            "outcome",
            "scope_list",
            "non_goal_list",
            "role",
            "delivery_kind",
            "assignee_id",
            "delegate_id",
            "repository_list",
            "partial_merge_recovery",
            "required_contract_list",
            "required_skill_list",
            "blocker_key_list",
            "resource_list",
            "verification_list",
            "human_decision_boundary",
            "source_section_list",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise TaskGraphError("Task node has another shape")
        for name in ("repository_list", "resource_list", "verification_list"):
            if not isinstance(payload[name], list):
                raise TaskGraphError(f"Task field {name} must be a list")
        return cls(
            node_key=payload["node_key"],
            title=payload["title"],
            outcome=payload["outcome"],
            scope_list=_text_list_parse(payload["scope_list"], label="Task scope"),
            non_goal_list=_text_list_parse(payload["non_goal_list"], label="Task non-goals", empty_allowed=True),
            role=TaskRole(payload["role"]),
            delivery_kind=DeliveryKind(payload["delivery_kind"]),
            assignee_id=payload["assignee_id"],
            delegate_id=payload["delegate_id"],
            repository_list=[RepositoryTarget.from_payload(item) for item in payload["repository_list"]],
            partial_merge_recovery=payload["partial_merge_recovery"],
            required_contract_list=_text_list_parse(payload["required_contract_list"], label="Required contracts"),
            required_skill_list=_text_list_parse(
                payload["required_skill_list"],
                label="Required skills",
                empty_allowed=True,
            ),
            blocker_key_list=_text_list_parse(payload["blocker_key_list"], label="Task blockers", empty_allowed=True),
            resource_list=[ResourceDeclaration.from_payload(item) for item in payload["resource_list"]],
            verification_list=[VerificationStep.from_payload(item) for item in payload["verification_list"]],
            human_decision_boundary=payload["human_decision_boundary"],
            source_section_list=_text_list_parse(payload["source_section_list"], label="Source sections"),
        )


@dataclass(frozen=True, slots=True)
class TaskGraph:
    """Own one Linear Project and its canonical issue dependency graph."""

    team_id: str
    project_name: str
    source: SourceIdentity
    node_list: list[TaskNode]

    def __post_init__(self) -> None:
        """Validate graph identity, topology and mandatory terminal roles."""

        if not isinstance(self.team_id, str) or _UUID_PATTERN.fullmatch(self.team_id) is None:
            raise TaskGraphError("Graph team_id must be one lowercase Linear UUID")
        _text_validate(self.project_name, label="Linear Project name")
        if not isinstance(self.source, SourceIdentity):
            raise TaskGraphError("Task graph source has another shape")
        if not isinstance(self.node_list, list) or any(not isinstance(item, TaskNode) for item in self.node_list):
            raise TaskGraphError("Task graph node list must contain only task nodes")
        if not self.node_list:
            raise TaskGraphError("Task graph must contain at least one node")
        node_by_key_map = {node.node_key: node for node in self.node_list}
        if len(node_by_key_map) != len(self.node_list):
            raise TaskGraphError("Task graph repeats one node_key")
        for node in self.node_list:
            unknown_blocker_key_set = set(node.blocker_key_list) - set(node_by_key_map)
            if unknown_blocker_key_set:
                raise TaskGraphError(f"Task {node.node_key} has unknown blockers: {sorted(unknown_blocker_key_set)}")
            for resource in node.resource_list:
                unknown_consumer_set = set(resource.consumer_node_key_list) - set(node_by_key_map)
                if unknown_consumer_set:
                    raise TaskGraphError(
                        f"Resource {resource.key} has unknown consumers: {sorted(unknown_consumer_set)}"
                    )
                if node.node_key in resource.consumer_node_key_list:
                    raise TaskGraphError(f"Resource {resource.key} repeats its implicit owner as a consumer")
        blocker_node_key_set_by_node_key_map = {
            node_key: set(node.blocker_key_list) for node_key, node in node_by_key_map.items()
        }
        cycle_node_key = cycle_node_key_get(blocker_node_key_set_by_node_key_map)
        if cycle_node_key:
            raise TaskGraphError(f"Task graph contains a blocker cycle at {cycle_node_key}")
        review_list = [node for node in self.node_list if node.role is TaskRole.REVIEW]
        acceptance_list = [node for node in self.node_list if node.role is TaskRole.ACCEPTANCE]
        cleanup_list = [node for node in self.node_list if node.role is TaskRole.CLEANUP]
        if not review_list or not acceptance_list or len(cleanup_list) != 1:
            raise TaskGraphError("Graph requires review, acceptance and exactly one cleanup task")
        implementation_key_set = {node.node_key for node in self.node_list if node.role is TaskRole.IMPLEMENTATION}
        if not implementation_key_set:
            raise TaskGraphError("Graph requires at least one implementation or evidence-probe task")
        resource_key_list = [resource.key for node in self.node_list for resource in node.resource_list]
        if len(resource_key_list) != len(set(resource_key_list)):
            raise TaskGraphError("Task graph repeats one resource key across issue owners")
        for review in review_list:
            if not set(review.blocker_key_list) & implementation_key_set:
                raise TaskGraphError("Each review task must be blocked by implementation work")
        review_key_set = {node.node_key for node in review_list}
        for acceptance in acceptance_list:
            if not set(acceptance.blocker_key_list) & review_key_set:
                raise TaskGraphError("Each acceptance task must be blocked by a review task")
        acceptance_key_set = {node.node_key for node in acceptance_list}
        if not acceptance_key_set <= set(cleanup_list[0].blocker_key_list):
            raise TaskGraphError("Final cleanup must be blocked by every acceptance task")
        object.__setattr__(self, "node_list", list(self.node_list))
        downstream_node_key_set_by_blocker_key_map = {node_key: set() for node_key in node_by_key_map}
        for node in self.node_list:
            for blocker_key in node.blocker_key_list:
                downstream_node_key_set_by_blocker_key_map[blocker_key].add(node.node_key)
        role_by_node_key_map = {node_key: node.role for node_key, node in node_by_key_map.items()}
        for node in self.node_list:
            expected_role_list = {
                TaskRole.IMPLEMENTATION: [
                    TaskRole.REVIEW,
                    TaskRole.ACCEPTANCE,
                    TaskRole.CLEANUP,
                ],
                TaskRole.REVIEW: [TaskRole.ACCEPTANCE, TaskRole.CLEANUP],
                TaskRole.ACCEPTANCE: [TaskRole.CLEANUP],
                TaskRole.HUMAN: [TaskRole.ACCEPTANCE, TaskRole.CLEANUP],
                TaskRole.CLEANUP: [],
            }[node.role]
            if expected_role_list and not exist_ordered_role_path(
                node.node_key,
                expected_role_list,
                downstream_node_key_set_by_blocker_key_map=downstream_node_key_set_by_blocker_key_map,
                role_by_node_key_map=role_by_node_key_map,
            ):
                expected_path = " -> ".join(item.value for item in expected_role_list)
                raise TaskGraphError(f"Task {node.node_key} does not retain downstream path {expected_path}")
            for resource in node.resource_list:
                if any(
                    not exist_path(
                        node.node_key,
                        consumer_key,
                        downstream_node_key_set_by_blocker_key_map=downstream_node_key_set_by_blocker_key_map,
                    )
                    for consumer_key in resource.consumer_node_key_list
                ):
                    raise TaskGraphError(f"Resource {resource.key} consumer must be downstream from its owning task")

    def source_fingerprint(self) -> str:
        """Return the exact immutable source fingerprint.

        Returns:
            Lowercase SHA-256 identity.
        """

        return self.source.fingerprint()

    def graph_fingerprint(self) -> str:
        """Return the exact normalized graph fingerprint.

        Returns:
            Lowercase SHA-256 identity.
        """

        return canonical_sha256(self.normalized_payload())

    def project_key(self) -> str:
        """Return the provider-owned stable Project identity.

        Returns:
            Stable source-derived key.
        """

        return f"linear-agent-tools:v1:{self.team_id}:{self.source_fingerprint()}"

    def normalized_payload(self) -> dict[str, object]:
        """Return the canonical graph content used by import reconciliation.

        Returns:
            JSON-ready graph object.
        """

        return {
            "schema_version": 1,
            "project_name": self.project_name,
            "source": {
                "canonical_url": self.source.canonical_url,
                "fingerprint": self.source_fingerprint(),
                "kind": self.source.kind,
                "outcome": self.source.outcome,
                "revision": self.source.revision,
            },
            "team_id": self.team_id,
            "node_list": [node.payload() for node in sorted(self.node_list, key=lambda item: item.node_key)],
        }

    @classmethod
    def from_payload(cls, payload: object) -> "TaskGraph":
        """Parse one strict complete task graph payload.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed graph.
        """

        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "team_id",
            "project_name",
            "source",
            "node_list",
        }:
            raise TaskGraphError("Task graph payload has another shape")
        if payload["schema_version"] != 1 or not isinstance(payload["node_list"], list):
            raise TaskGraphError("Task graph schema version or node list is unsupported")
        try:
            return cls(
                team_id=payload["team_id"],
                project_name=payload["project_name"],
                source=SourceIdentity.from_payload(payload["source"]),
                node_list=[TaskNode.from_payload(item) for item in payload["node_list"]],
            )
        except (TypeError, ValueError) as error:
            raise TaskGraphError("Task graph contains an unsupported enum or field value") from error


def canonical_sha256(payload: object) -> str:
    """Return SHA-256 of one canonical JSON value.

    Args:
        payload: JSON-ready value.

    Returns:
        Lowercase SHA-256 identity.
    """

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
