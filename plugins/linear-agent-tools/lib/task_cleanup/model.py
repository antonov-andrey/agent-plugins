"""Closed cleanup authority and natural provider identity models."""

from __future__ import annotations

from dataclasses import dataclass, field

from git_host.model import RepositoryIdentity
from linear_boundary.contract import LinearContractError, uuid_validate
from task_cleanup.contract import (
    AcceptanceBaseBranchCleanupResource,
    CleanupResource,
    CleanupResourceContractError,
    WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
    cleanup_resource_from_payload,
    cleanup_resource_identity_key,
)
from task_workspace.model import RepositoryRequest, TaskWorkspaceError, issue_identifier_validate


class TaskCleanupError(RuntimeError):
    """Report one unsafe, foreign or failed cleanup operation."""


def _single_line(value: object, *, label: str) -> str:
    """Return one non-empty single-line cleanup identity.

    Args:
        value: Candidate external text.
        label: Diagnostic field name.

    Returns:
        The validated text.
    """

    if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise TaskCleanupError(f"{label} must be non-empty single-line text")
    return value


@dataclass(frozen=True, slots=True)
class CleanupAuthority:
    """Bind explicit Linear state that authorizes one cleanup scope."""

    scope: str
    issue_status: str
    project_status: str
    final_acceptance_done: bool
    all_other_project_nodes_terminal: bool
    unresolved_remediation_blocker_count: int

    def __post_init__(self) -> None:
        """Validate exact cleanup authority without inventing hidden states."""

        if not isinstance(self.scope, str) or self.scope not in {
            "attempt",
            "terminal-issue",
            "project-final",
        }:
            raise TaskCleanupError("Cleanup scope is unsupported")
        known_issue_status_set = {
            "Backlog",
            "Todo",
            "In Progress",
            "Review",
            "Rework",
            "Merging",
            "Done",
            "Canceled",
        }
        if not isinstance(self.issue_status, str) or self.issue_status not in known_issue_status_set:
            raise TaskCleanupError("Cleanup issue status is unsupported")
        if not isinstance(self.project_status, str) or self.project_status not in {
            "Planned",
            "In Progress",
            "Completed",
            "Canceled",
        }:
            raise TaskCleanupError("Cleanup Project status is unsupported")
        if not isinstance(self.final_acceptance_done, bool) or not isinstance(
            self.all_other_project_nodes_terminal,
            bool,
        ):
            raise TaskCleanupError("Cleanup proof flags must be boolean")
        if (
            isinstance(self.unresolved_remediation_blocker_count, bool)
            or not isinstance(self.unresolved_remediation_blocker_count, int)
            or self.unresolved_remediation_blocker_count < 0
        ):
            raise TaskCleanupError("Remediation blocker count must be non-negative")
        if self.scope == "attempt":
            if self.issue_status not in {
                "Todo",
                "In Progress",
                "Review",
                "Rework",
                "Merging",
            }:
                raise TaskCleanupError("Attempt cleanup requires one exact non-terminal attempt state")
            if self.final_acceptance_done or self.all_other_project_nodes_terminal:
                raise TaskCleanupError("Attempt cleanup cannot claim Project-final proof")
            return
        if self.scope == "terminal-issue" and self.issue_status not in {"Done", "Canceled"}:
            raise TaskCleanupError("Terminal issue cleanup requires explicit terminal issue status")
        if self.scope == "project-final":
            if self.project_status == "Canceled":
                if self.issue_status != "Canceled":
                    raise TaskCleanupError("Canceled Project cleanup requires its terminal Canceled cleanup issue")
                if not self.all_other_project_nodes_terminal or self.unresolved_remediation_blocker_count:
                    raise TaskCleanupError(
                        "Canceled Project cleanup requires every other node terminal and no remediation blocker"
                    )
                return
            if self.issue_status != "In Progress":
                raise TaskCleanupError("Active Project cleanup node must own one In Progress attempt")
            if self.project_status != "In Progress":
                raise TaskCleanupError("Final cleanup requires an active or canceled Project")
            if (
                not self.final_acceptance_done
                or not self.all_other_project_nodes_terminal
                or self.unresolved_remediation_blocker_count
            ):
                raise TaskCleanupError("Project completion prerequisites are not satisfied")


@dataclass(frozen=True, slots=True)
class PullRequestReference:
    """Identify one exact linked pull request owned by the task."""

    repository: RepositoryIdentity
    number: int

    def __post_init__(self) -> None:
        """Require one real positive pull-request number."""

        if not isinstance(self.repository, RepositoryIdentity):
            raise TaskCleanupError("Pull request repository identity is unsupported")
        if isinstance(self.number, bool) or not isinstance(self.number, int) or self.number < 1:
            raise TaskCleanupError("Pull request number must be positive")

    @classmethod
    def from_payload(cls, payload: object) -> "PullRequestReference":
        """Parse one strict pull-request reference.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed PR reference.
        """

        if not isinstance(payload, dict) or set(payload) != {"repository", "number"}:
            raise TaskCleanupError("Pull request reference has another shape")
        return cls(repository=RepositoryIdentity(payload["repository"]), number=payload["number"])


@dataclass(frozen=True, slots=True)
class PullRequestTarget:
    """Bind one pull request to its exact approved base and task branches."""

    base_branch: str
    head_branch: str

    def __post_init__(self) -> None:
        """Require two non-empty single-line branch names."""

        _single_line(self.base_branch, label="Pull request base branch")
        _single_line(self.head_branch, label="Pull request head branch")


@dataclass(frozen=True, slots=True)
class CleanupRequest:
    """Own every exact local and GitHub cleanup target."""

    issue_identifier: str
    project_id: str
    authority: CleanupAuthority
    repository_list: list[RepositoryRequest]
    pull_request_list: list[PullRequestReference]
    resource_list: list[CleanupResource]
    project_issue_identifier_list: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate one complete cleanup request."""

        try:
            issue_identifier_validate(self.issue_identifier)
            uuid_validate(self.project_id, label="Cleanup Project ID")
        except (LinearContractError, TaskWorkspaceError) as error:
            raise TaskCleanupError("Cleanup owner identity is malformed") from error
        if not isinstance(self.authority, CleanupAuthority):
            raise TaskCleanupError("Cleanup authority has another shape")
        for label, value, expected_type in (
            ("repository", self.repository_list, RepositoryRequest),
            ("pull request", self.pull_request_list, PullRequestReference),
        ):
            if not isinstance(value, list) or any(not isinstance(item, expected_type) for item in value):
                raise TaskCleanupError(f"Cleanup {label} list has another shape")
        resource_type_tuple = (
            AcceptanceBaseBranchCleanupResource,
            WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
        )
        if not isinstance(self.resource_list, list) or any(
            not isinstance(item, resource_type_tuple) for item in self.resource_list
        ):
            raise TaskCleanupError("Cleanup resource list has another shape")
        if not isinstance(self.project_issue_identifier_list, list):
            raise TaskCleanupError("Project issue identifier list must be a list")
        repository_identity_list = [item.origin_identity for item in self.repository_list]
        if len(set(repository_identity_list)) != len(self.repository_list):
            raise TaskCleanupError("Cleanup request repeats one repository")
        repository_identity_set = set(repository_identity_list)
        if any(resource.repository not in repository_identity_set for resource in self.resource_list):
            raise TaskCleanupError("Cleanup resource repository must match one exact participating repository")
        if len(self.pull_request_list) != len(set(self.pull_request_list)):
            raise TaskCleanupError("Cleanup request repeats one pull request")
        pull_request_repository_list = [item.repository.value for item in self.pull_request_list]
        if len(pull_request_repository_list) != len(set(pull_request_repository_list)):
            raise TaskCleanupError("One deterministic task branch may own at most one pull request per repository")
        if self.authority.scope == "attempt" and self.pull_request_list:
            raise TaskCleanupError("Attempt cleanup cannot mutate pull requests")
        resource_identity_key_list = [cleanup_resource_identity_key(item) for item in self.resource_list]
        if resource_identity_key_list != sorted(resource_identity_key_list) or len(resource_identity_key_list) != len(
            set(resource_identity_key_list)
        ):
            raise TaskCleanupError("Cleanup typed resource identities must be unique and sorted")
        for resource in self.resource_list:
            if resource.project_id != self.project_id:
                raise TaskCleanupError("Cleanup resource belongs to another Project")
            if self.authority.scope != "project-final" and resource.owner_issue_identifier != self.issue_identifier:
                raise TaskCleanupError("Non-final cleanup resource belongs to another issue")
        if self.project_issue_identifier_list != sorted(self.project_issue_identifier_list) or len(
            self.project_issue_identifier_list
        ) != len(set(self.project_issue_identifier_list)):
            raise TaskCleanupError("Project issue identifiers must be unique and sorted")
        for identifier in self.project_issue_identifier_list:
            try:
                issue_identifier_validate(identifier)
            except TaskWorkspaceError as error:
                raise TaskCleanupError("Project issue identifier is malformed") from error
        if self.authority.scope == "project-final":
            if (
                not self.project_issue_identifier_list
                or self.issue_identifier not in self.project_issue_identifier_list
            ):
                raise TaskCleanupError("Final Project cleanup requires its complete issue identifier set")
            if any(
                resource.owner_issue_identifier not in self.project_issue_identifier_list
                for resource in self.resource_list
            ):
                raise TaskCleanupError("Final Project cleanup resource owner is absent from the complete issue set")
        elif self.project_issue_identifier_list:
            raise TaskCleanupError("Only final Project cleanup may carry Project issue identifiers")
        object.__setattr__(self, "repository_list", list(self.repository_list))
        object.__setattr__(self, "pull_request_list", list(self.pull_request_list))
        object.__setattr__(self, "resource_list", list(self.resource_list))
        object.__setattr__(self, "project_issue_identifier_list", list(self.project_issue_identifier_list))

    @classmethod
    def from_payload(cls, payload: object) -> "CleanupRequest":
        """Parse one strict cleanup request payload.

        Args:
            payload: Candidate JSON value.

        Returns:
            Typed cleanup request.
        """

        expected = {
            "schema_version",
            "issue_identifier",
            "project_id",
            "authority",
            "repository_list",
            "pull_request_list",
            "resource_list",
            "project_issue_identifier_list",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise TaskCleanupError("Cleanup request has another shape")
        for name in ("repository_list", "pull_request_list", "resource_list", "project_issue_identifier_list"):
            if not isinstance(payload[name], list):
                raise TaskCleanupError(f"Cleanup field {name} must be a list")
        if any(not isinstance(item, str) for item in payload["project_issue_identifier_list"]):
            raise TaskCleanupError("Project issue identifiers must be text")
        authority_payload = payload["authority"]
        authority_fields = {
            "scope",
            "issue_status",
            "project_status",
            "final_acceptance_done",
            "all_other_project_nodes_terminal",
            "unresolved_remediation_blocker_count",
        }
        if not isinstance(authority_payload, dict) or set(authority_payload) != authority_fields:
            raise TaskCleanupError("Cleanup authority has another shape")
        try:
            return cls(
                issue_identifier=payload["issue_identifier"],
                project_id=payload["project_id"],
                authority=CleanupAuthority(**authority_payload),
                repository_list=[RepositoryRequest.from_payload(item) for item in payload["repository_list"]],
                pull_request_list=[PullRequestReference.from_payload(item) for item in payload["pull_request_list"]],
                resource_list=[cleanup_resource_from_payload(item) for item in payload["resource_list"]],
                project_issue_identifier_list=list(payload["project_issue_identifier_list"]),
            )
        except (CleanupResourceContractError, ValueError, TypeError) as error:
            raise TaskCleanupError("Cleanup request contains an unsupported enum or field value") from error
