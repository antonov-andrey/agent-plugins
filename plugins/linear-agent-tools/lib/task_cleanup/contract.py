"""Typed natural identities for provider-owned non-standard cleanup resources."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import ClassVar, TypeAlias
import uuid

from git_origin.identity import GitOriginError, origin_identity_get
from task_workspace.model import TaskWorkspaceError, issue_identifier_validate


class CleanupResourceContractError(RuntimeError):
    """Report one malformed or unsupported typed cleanup resource."""


_ACCEPTANCE_BRANCH_PATTERN = re.compile(r"acceptance/[a-z0-9][a-z0-9-]*-complete-base")
_COMMON_PREFIX_PATTERN = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}-[a-z0-9][a-z0-9-]*")


def _project_id_validate(value: object) -> str:
    """Return one canonical Project UUID."""

    if not isinstance(value, str):
        raise CleanupResourceContractError("Cleanup resource Project ID must be text")
    try:
        identifier = uuid.UUID(value)
    except ValueError as error:
        raise CleanupResourceContractError("Cleanup resource Project ID must be one UUID") from error
    if str(identifier) != value:
        raise CleanupResourceContractError("Cleanup resource Project ID must be canonical")
    return value


def _repository_identity_get(value: object) -> str:
    """Return one canonical repository origin identity."""

    if not isinstance(value, str):
        raise CleanupResourceContractError("Cleanup resource repository must be text")
    try:
        return origin_identity_get(value)
    except GitOriginError as error:
        raise CleanupResourceContractError("Cleanup resource repository is unsafe or unsupported") from error


def _owner_identity_validate(*, project_id: object, owner_issue_identifier: object) -> tuple[str, str]:
    """Validate the natural Linear owner pair shared by every resource."""

    project_id = _project_id_validate(project_id)
    if not isinstance(owner_issue_identifier, str):
        raise CleanupResourceContractError("Cleanup resource owner issue must be text")
    try:
        owner_issue_identifier = issue_identifier_validate(owner_issue_identifier)
    except TaskWorkspaceError as error:
        raise CleanupResourceContractError("Cleanup resource owner issue is malformed") from error
    return project_id, owner_issue_identifier


@dataclass(frozen=True, slots=True)
class AcceptanceBaseBranchCleanupResource:
    """Own one retained remote acceptance base branch until Project cleanup."""

    project_id: str
    owner_issue_identifier: str
    repository: str
    branch: str

    handler_key: ClassVar[str] = "development-infrastructure-acceptance-base-branch"
    lifetime: ClassVar[str] = "project"

    def __post_init__(self) -> None:
        """Normalize the exact owner repository and reject an unsafe branch identity."""

        project_id, owner_issue_identifier = _owner_identity_validate(
            project_id=self.project_id,
            owner_issue_identifier=self.owner_issue_identifier,
        )
        repository = _repository_identity_get(self.repository)
        if not isinstance(self.branch, str) or _ACCEPTANCE_BRANCH_PATTERN.fullmatch(self.branch) is None:
            raise CleanupResourceContractError("Acceptance base cleanup branch is outside its fixed namespace")
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "owner_issue_identifier", owner_issue_identifier)
        object.__setattr__(self, "repository", repository)

    @classmethod
    def from_payload(cls, payload: object) -> "AcceptanceBaseBranchCleanupResource":
        """Parse one strict acceptance-base resource identity."""

        identity = _resource_identity_payload_get(payload, handler_key=cls.handler_key, lifetime=cls.lifetime)
        if set(identity) != {"project_id", "owner_issue_identifier", "repository", "branch"}:
            raise CleanupResourceContractError("Acceptance base cleanup identity has another shape")
        return cls(**identity)

    def payload(self) -> dict[str, object]:
        """Return the closed external resource declaration."""

        return {
            "handler_key": self.handler_key,
            "lifetime": self.lifetime,
            "identity": {
                "branch": self.branch,
                "owner_issue_identifier": self.owner_issue_identifier,
                "project_id": self.project_id,
                "repository": self.repository,
            },
        }


@dataclass(frozen=True, slots=True)
class WorkflowInfrastructureDevelopmentEnvironmentCleanupResource:
    """Own one dated workflow-infrastructure development environment until Project cleanup."""

    project_id: str
    owner_issue_identifier: str
    repository: str
    common_prefix: str

    handler_key: ClassVar[str] = "workflow-infrastructure-development-environment"
    lifetime: ClassVar[str] = "project"

    def __post_init__(self) -> None:
        """Normalize the owner repository and require the exact dated environment identity."""

        project_id, owner_issue_identifier = _owner_identity_validate(
            project_id=self.project_id,
            owner_issue_identifier=self.owner_issue_identifier,
        )
        repository = _repository_identity_get(self.repository)
        if not isinstance(self.common_prefix, str) or _COMMON_PREFIX_PATTERN.fullmatch(self.common_prefix) is None:
            raise CleanupResourceContractError("Development environment common prefix is malformed")
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "owner_issue_identifier", owner_issue_identifier)
        object.__setattr__(self, "repository", repository)

    @classmethod
    def from_payload(cls, payload: object) -> "WorkflowInfrastructureDevelopmentEnvironmentCleanupResource":
        """Parse one strict development-environment resource identity."""

        identity = _resource_identity_payload_get(payload, handler_key=cls.handler_key, lifetime=cls.lifetime)
        if set(identity) != {"project_id", "owner_issue_identifier", "repository", "common_prefix"}:
            raise CleanupResourceContractError("Development environment cleanup identity has another shape")
        return cls(**identity)

    def payload(self) -> dict[str, object]:
        """Return the closed external resource declaration."""

        return {
            "handler_key": self.handler_key,
            "lifetime": self.lifetime,
            "identity": {
                "common_prefix": self.common_prefix,
                "owner_issue_identifier": self.owner_issue_identifier,
                "project_id": self.project_id,
                "repository": self.repository,
            },
        }


CleanupResource: TypeAlias = (
    AcceptanceBaseBranchCleanupResource | WorkflowInfrastructureDevelopmentEnvironmentCleanupResource
)

_RESOURCE_TYPE_BY_HANDLER_KEY_MAP = {
    AcceptanceBaseBranchCleanupResource.handler_key: AcceptanceBaseBranchCleanupResource,
    WorkflowInfrastructureDevelopmentEnvironmentCleanupResource.handler_key: (
        WorkflowInfrastructureDevelopmentEnvironmentCleanupResource
    ),
}


def cleanup_handler_key_validate(value: object) -> str:
    """Return one exact provider-registry handler key."""

    if not isinstance(value, str) or value not in _RESOURCE_TYPE_BY_HANDLER_KEY_MAP:
        raise CleanupResourceContractError("Cleanup handler key is absent from the provider registry")
    return value


def cleanup_resource_from_payload(payload: object) -> CleanupResource:
    """Resolve one strict external resource only through the provider registry."""

    if not isinstance(payload, dict):
        raise CleanupResourceContractError("Cleanup resource has another shape")
    handler_key = cleanup_handler_key_validate(payload.get("handler_key"))
    return _RESOURCE_TYPE_BY_HANDLER_KEY_MAP[handler_key].from_payload(payload)


def cleanup_resource_identity_key(resource: CleanupResource) -> tuple[str, ...]:
    """Return one collision-resistant natural identity tuple without hashing."""

    if isinstance(resource, AcceptanceBaseBranchCleanupResource):
        return (
            resource.handler_key,
            resource.project_id,
            resource.owner_issue_identifier,
            resource.repository,
            resource.branch,
        )
    return (
        resource.handler_key,
        resource.project_id,
        resource.owner_issue_identifier,
        resource.repository,
        resource.common_prefix,
    )


def _resource_identity_payload_get(payload: object, *, handler_key: str, lifetime: str) -> dict[str, object]:
    """Return one exact handler-bound identity mapping."""

    if (
        not isinstance(payload, dict)
        or set(payload) != {"handler_key", "lifetime", "identity"}
        or payload["handler_key"] != handler_key
        or payload["lifetime"] != lifetime
        or not isinstance(payload["identity"], dict)
    ):
        raise CleanupResourceContractError("Cleanup resource handler or lifetime has another shape")
    return payload["identity"]
