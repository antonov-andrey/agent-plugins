"""Closed cleanup authority and input models."""

from __future__ import annotations

from dataclasses import dataclass
import re

from git_host.model import RepositoryIdentity
from task_graph.model import ResourceDeclaration, ResourceLifetime
from task_workspace.model import (
    RepositoryRequest,
    WorkspaceRequest,
    issue_identifier_validate,
)


class TaskCleanupError(RuntimeError):
    """Report one unsafe, foreign or failed cleanup operation."""


_NODE_KEY_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


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
            "Human Review",
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
            self.all_other_project_nodes_terminal, bool
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
                "Human Review",
                "Rework",
                "Merging",
            }:
                raise TaskCleanupError("Attempt cleanup requires one exact non-terminal attempt state")
            if self.final_acceptance_done or self.all_other_project_nodes_terminal:
                raise TaskCleanupError("Attempt cleanup cannot claim Project-final proof")
            return
        if self.scope == "terminal-issue" and self.issue_status not in {
            "Done",
            "Canceled",
        }:
            raise TaskCleanupError("Issue resource cleanup requires explicit terminal issue status")
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
        return cls(
            repository=RepositoryIdentity(payload["repository"]),
            number=payload["number"],
        )


@dataclass(frozen=True, slots=True)
class CleanupRequest:
    """Own every exact local, GitHub and declared resource cleanup target."""

    issue_identifier: str
    authority: CleanupAuthority
    repository_list: tuple[RepositoryRequest, ...]
    pull_request_list: tuple[PullRequestReference, ...]
    resource_list: tuple[ResourceDeclaration, ...]
    approved_resource_fingerprint_list: tuple[str, ...] = ()
    terminal_consumer_node_key_list: tuple[str, ...] = ()
    project_issue_identifier_list: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate one complete cleanup request."""

        issue_identifier_validate(self.issue_identifier)
        if not isinstance(self.authority, CleanupAuthority):
            raise TaskCleanupError("Cleanup authority has another shape")
        for label, value, expected_type in (
            ("repository", self.repository_list, RepositoryRequest),
            ("pull request", self.pull_request_list, PullRequestReference),
            ("resource", self.resource_list, ResourceDeclaration),
        ):
            if not isinstance(value, tuple) or any(not isinstance(item, expected_type) for item in value):
                raise TaskCleanupError(f"Cleanup {label} list has another shape")
        if not isinstance(self.project_issue_identifier_list, tuple):
            raise TaskCleanupError("Project issue identifier list must be a tuple")
        repository_url_set = {item.origin_url for item in self.repository_list}
        if len(repository_url_set) != len(self.repository_list):
            raise TaskCleanupError("Cleanup request repeats one repository")
        pr_key_list = [(item.repository.value, item.number) for item in self.pull_request_list]
        if len(pr_key_list) != len(set(pr_key_list)):
            raise TaskCleanupError("Cleanup request repeats one pull request")
        pr_repository_list = [item.repository.value for item in self.pull_request_list]
        if len(pr_repository_list) != len(set(pr_repository_list)):
            raise TaskCleanupError("One deterministic task branch may own at most one pull request per repository")
        resource_key_list = [item.key for item in self.resource_list]
        if len(resource_key_list) != len(set(resource_key_list)):
            raise TaskCleanupError("Cleanup request repeats one resource key")
        expected_resource_fingerprint_list = tuple(sorted(item.fingerprint() for item in self.resource_list))
        if (
            not isinstance(self.approved_resource_fingerprint_list, tuple)
            or self.approved_resource_fingerprint_list != expected_resource_fingerprint_list
            or len(self.approved_resource_fingerprint_list) != len(set(self.approved_resource_fingerprint_list))
        ):
            raise TaskCleanupError(
                "Cleanup request must bind every resource to its independently approved declaration fingerprint"
            )
        expected_terminal_consumer_node_key_list = tuple(
            sorted(
                {
                    consumer_key
                    for item in self.resource_list
                    if item.lifetime is ResourceLifetime.ISSUE
                    for consumer_key in item.consumer_node_key_list
                }
            )
        )
        if (
            not isinstance(self.terminal_consumer_node_key_list, tuple)
            or self.terminal_consumer_node_key_list != expected_terminal_consumer_node_key_list
            or any(_NODE_KEY_PATTERN.fullmatch(item) is None for item in self.terminal_consumer_node_key_list)
        ):
            raise TaskCleanupError("Cleanup request must prove every declared issue-resource consumer terminal")
        allowed_lifetime = {
            "attempt": {ResourceLifetime.ATTEMPT},
            "terminal-issue": {ResourceLifetime.ATTEMPT, ResourceLifetime.ISSUE},
            "project-final": {ResourceLifetime.PROJECT},
        }[self.authority.scope]
        if any(item.lifetime not in allowed_lifetime for item in self.resource_list):
            raise TaskCleanupError("Cleanup request includes a resource outside its authorized lifetime")
        if self.authority.scope == "attempt" and self.pull_request_list:
            raise TaskCleanupError("Attempt cleanup cannot mutate pull requests")
        if self.authority.scope != "terminal-issue" and self.terminal_consumer_node_key_list:
            raise TaskCleanupError("Only terminal issue cleanup may carry downstream consumer proof")
        if self.project_issue_identifier_list != tuple(sorted(self.project_issue_identifier_list)) or len(
            self.project_issue_identifier_list
        ) != len(set(self.project_issue_identifier_list)):
            raise TaskCleanupError("Project issue identifiers must be unique and sorted")
        for identifier in self.project_issue_identifier_list:
            issue_identifier_validate(identifier)
        if self.authority.scope == "project-final":
            if (
                not self.project_issue_identifier_list
                or self.issue_identifier not in self.project_issue_identifier_list
            ):
                raise TaskCleanupError("Final Project cleanup requires its complete issue identifier set")
        elif self.project_issue_identifier_list:
            raise TaskCleanupError("Only final Project cleanup may carry Project issue identifiers")

    def workspace_request(self) -> WorkspaceRequest:
        """Return the exact participating repository workspace identity.

        Returns:
            Typed workspace request.
        """

        return WorkspaceRequest(self.issue_identifier, self.repository_list)

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
            "authority",
            "repository_list",
            "pull_request_list",
            "resource_list",
            "approved_resource_fingerprint_list",
            "terminal_consumer_node_key_list",
            "project_issue_identifier_list",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] != 1:
            raise TaskCleanupError("Cleanup request has another shape")
        for name in (
            "repository_list",
            "pull_request_list",
            "resource_list",
            "approved_resource_fingerprint_list",
            "terminal_consumer_node_key_list",
            "project_issue_identifier_list",
        ):
            if not isinstance(payload[name], list):
                raise TaskCleanupError(f"Cleanup field {name} must be a list")
        if any(not isinstance(item, str) for item in payload["project_issue_identifier_list"]):
            raise TaskCleanupError("Project issue identifiers must be text")
        if any(not isinstance(item, str) for item in payload["approved_resource_fingerprint_list"]):
            raise TaskCleanupError("Approved resource fingerprints must be text")
        if any(not isinstance(item, str) for item in payload["terminal_consumer_node_key_list"]):
            raise TaskCleanupError("Terminal resource consumer keys must be text")
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
                authority=CleanupAuthority(**authority_payload),
                repository_list=tuple(RepositoryRequest.from_payload(item) for item in payload["repository_list"]),
                pull_request_list=tuple(
                    PullRequestReference.from_payload(item) for item in payload["pull_request_list"]
                ),
                resource_list=tuple(ResourceDeclaration.from_payload(item) for item in payload["resource_list"]),
                approved_resource_fingerprint_list=tuple(payload["approved_resource_fingerprint_list"]),
                terminal_consumer_node_key_list=tuple(payload["terminal_consumer_node_key_list"]),
                project_issue_identifier_list=tuple(payload["project_issue_identifier_list"]),
            )
        except (ValueError, TypeError) as error:
            raise TaskCleanupError("Cleanup request contains an unsupported enum or field value") from error
