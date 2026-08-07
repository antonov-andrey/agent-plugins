"""Closed GitHub repository and pull-request models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
_ISSUE_IDENTIFIER_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*")
_BRANCH_FORBIDDEN_CHARACTER_SET = set(" ~^:?*[\\")


class GitHubContractError(RuntimeError):
    """Report one unsafe or conflicting GitHub operation."""


def branch_name_require(value: str, *, label: str) -> None:
    """Reject text that cannot be one safe GitHub branch ref.

    Args:
        value: Candidate branch name.
        label: Diagnostic branch role.
    """

    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("-", "."))
        or value.endswith((".", "/", ".lock"))
        or ".." in value
        or "//" in value
        or "@{" in value
        or any(character in _BRANCH_FORBIDDEN_CHARACTER_SET or ord(character) < 32 for character in value)
    ):
        raise GitHubContractError(f"GitHub {label} branch has an unsafe ref shape")


def issue_identifier_in_title_require(issue_identifier: str, title: str) -> None:
    """Require one exact Linear identifier token in a single-line PR title.

    Args:
        issue_identifier: Canonical Linear issue identifier.
        title: Candidate pull-request title.
    """

    if _ISSUE_IDENTIFIER_PATTERN.fullmatch(issue_identifier) is None:
        raise GitHubContractError("Linear issue identifier has another shape")
    if not isinstance(title, str) or not title or any(character in title for character in ("\x00", "\n", "\r")):
        raise GitHubContractError("Pull request title must be non-empty single-line text")
    token_pattern = rf"(?<![A-Z0-9-]){re.escape(issue_identifier)}(?![A-Z0-9-])"
    if re.search(token_pattern, title) is None:
        raise GitHubContractError("Pull request title omits the exact Linear issue token")


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    """Bind one exact GitHub owner/repository identity."""

    value: str

    def __post_init__(self) -> None:
        """Validate one canonical GitHub repository identity."""

        if _REPOSITORY_PATTERN.fullmatch(self.value) is None or any(
            part in {".", ".."} for part in self.value.split("/")
        ):
            raise GitHubContractError("GitHub repository must use exact owner/name form")

    @property
    def canonical_https_url(self) -> str:
        """Return the one explicit HTTPS mutation destination for this repository."""

        return f"https://github.com/{self.value}.git"

    @classmethod
    def from_origin_identity(cls, origin_identity: str) -> "RepositoryIdentity | None":
        """Parse one canonical Git origin when it belongs to GitHub.

        Args:
            origin_identity: Canonical comparison identity of a Git remote.

        Returns:
            Exact GitHub repository identity, or absence for another host.
        """

        parsed = urlsplit(origin_identity)
        if parsed.hostname != "github.com":
            return None
        path_part_list = [item for item in parsed.path.split("/") if item]
        if len(path_part_list) != 2:
            raise GitHubContractError("GitHub origin does not identify exact owner/repository")
        return cls("/".join(path_part_list))


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    """Describe one branch-protection-required GitHub check."""

    name: str
    bucket: str
    link: str

    def __post_init__(self) -> None:
        """Validate provider check output without accepting hidden multiline data."""

        for label, value, empty_allowed in (
            ("name", self.name, False),
            ("bucket", self.bucket, False),
            ("link", self.link, True),
        ):
            if (
                not isinstance(value, str)
                or (not value and not empty_allowed)
                or any(character in value for character in ("\x00", "\n", "\r"))
            ):
                raise GitHubContractError(f"Required check {label} has another shape")

    def is_passed(self) -> bool:
        """Return whether GitHub classified the required check as passing.

        Returns:
            Whether the check passed.
        """

        return self.bucket in {"pass", "skipping"}


@dataclass(frozen=True, slots=True)
class BranchProtectionSnapshot:
    """Bind effective base protection to one repository and executing identity."""

    repository: RepositoryIdentity
    base_branch: str
    execution_login: str
    execution_user_id: int
    execution_node_id: str
    execution_permission: str
    protection_source_list: list[str]
    ruleset_id_list: list[int]
    required_check_name_list: list[str]
    strict_required_status_checks: bool
    non_fast_forward_protected: bool
    deletion_protected: bool
    execution_bypass: bool
    admin_enforcement_enabled: bool
    required_pull_request_gate_enabled: bool
    required_linear_history_enabled: bool
    required_signatures_enabled: bool
    required_conversation_resolution_enabled: bool
    branch_lock_enabled: bool
    push_restrictions_enabled: bool
    force_push_allowed: bool
    deletion_allowed: bool
    creation_blocked: bool
    fork_sync_allowed: bool
    ruleset_rule_type_list: list[str]

    def __post_init__(self) -> None:
        """Validate a complete typed protection readback."""

        if not isinstance(self.repository, RepositoryIdentity):
            raise GitHubContractError("Branch-protection repository identity is unsupported")
        branch_name_require(self.base_branch, label="protected base")
        for label, value in (
            ("execution login", self.execution_login),
            ("execution node ID", self.execution_node_id),
            ("execution permission", self.execution_permission),
        ):
            if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
                raise GitHubContractError(f"Branch-protection {label} has another shape")
        if self.execution_permission not in {"admin", "maintain", "write", "triage", "read"}:
            raise GitHubContractError("Branch-protection execution permission is unsupported")
        if (
            isinstance(self.execution_user_id, bool)
            or not isinstance(self.execution_user_id, int)
            or self.execution_user_id < 1
        ):
            raise GitHubContractError("Branch-protection execution user ID has another shape")
        if not isinstance(self.protection_source_list, list) or any(
            not isinstance(item, str) or not item for item in self.protection_source_list
        ):
            raise GitHubContractError("Branch-protection sources have another shape")
        if not isinstance(self.ruleset_id_list, list) or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in self.ruleset_id_list
        ):
            raise GitHubContractError("Branch-protection ruleset identities have another shape")
        if not isinstance(self.required_check_name_list, list) or any(
            not isinstance(item, str) or not item or any(character in item for character in ("\x00", "\n", "\r"))
            for item in self.required_check_name_list
        ):
            raise GitHubContractError("Branch-protection required checks have another shape")
        for label, value in (
            ("strict required-check flag", self.strict_required_status_checks),
            ("non-fast-forward flag", self.non_fast_forward_protected),
            ("deletion flag", self.deletion_protected),
            ("execution bypass flag", self.execution_bypass),
            ("admin enforcement flag", self.admin_enforcement_enabled),
            ("required pull-request gate flag", self.required_pull_request_gate_enabled),
            ("required linear-history flag", self.required_linear_history_enabled),
            ("required signatures flag", self.required_signatures_enabled),
            ("required conversation-resolution flag", self.required_conversation_resolution_enabled),
            ("branch lock flag", self.branch_lock_enabled),
            ("push restrictions flag", self.push_restrictions_enabled),
            ("force-push allowance flag", self.force_push_allowed),
            ("deletion allowance flag", self.deletion_allowed),
            ("creation block flag", self.creation_blocked),
            ("fork-sync allowance flag", self.fork_sync_allowed),
        ):
            if not isinstance(value, bool):
                raise GitHubContractError(f"Branch-protection {label} must be boolean")
        for label, value_list in (
            ("sources", self.protection_source_list),
            ("ruleset identities", self.ruleset_id_list),
            ("required checks", self.required_check_name_list),
            ("ruleset rule types", self.ruleset_rule_type_list),
        ):
            if value_list != sorted(set(value_list)):
                raise GitHubContractError(f"Branch-protection {label} must be sorted and unique")
        object.__setattr__(self, "protection_source_list", list(self.protection_source_list))
        object.__setattr__(self, "ruleset_id_list", list(self.ruleset_id_list))
        object.__setattr__(self, "required_check_name_list", list(self.required_check_name_list))
        object.__setattr__(self, "ruleset_rule_type_list", list(self.ruleset_rule_type_list))

    def required_check_results_require(self, required_check_list: list[RequiredCheck]) -> None:
        """Require typed check results for exactly the protected check definitions.

        A legitimate empty protected set is accepted only when the provider returned
        a typed empty result list; the transport parser owns that distinction.

        Args:
            required_check_list: Typed results from ``gh pr checks --required``.
        """

        if not isinstance(required_check_list, list) or any(
            not isinstance(item, RequiredCheck) for item in required_check_list
        ):
            raise GitHubContractError("Required GitHub check results have another shape")
        result_name_list = sorted(item.name for item in required_check_list)
        if result_name_list != self.required_check_name_list:
            raise GitHubContractError("Required GitHub check results differ from branch protection")

    def mutation_authority_require(self) -> None:
        """Require exact repository write authority for the executing principal."""

        if self.execution_permission not in {"admin", "maintain", "write"}:
            raise GitHubContractError("Executing GitHub identity lacks exact repository write authority")

    def merge_mechanism_require(self, merge_method: str) -> None:
        """Require protection that enforces the selected atomic merge mechanism.

        Args:
            merge_method: Declared merge strategy.
        """

        if merge_method not in {"merge", "squash", "rebase"}:
            raise GitHubContractError("Pull request merge method is unsupported")
        if not self.protection_source_list:
            raise GitHubContractError("Pull request base branch is unprotected")
        self.mutation_authority_require()
        if self.execution_bypass:
            raise GitHubContractError("Executing GitHub identity can bypass base branch protection")
        incompatible_classic_condition_list = [
            label
            for label, enabled in (
                ("required pull-request reviews", self.required_pull_request_gate_enabled),
                ("required linear history", self.required_linear_history_enabled),
                ("required signatures", self.required_signatures_enabled),
                ("required conversation resolution", self.required_conversation_resolution_enabled),
                ("branch lock", self.branch_lock_enabled),
                ("push restrictions", self.push_restrictions_enabled),
            )
            if enabled
        ]
        if incompatible_classic_condition_list:
            raise GitHubContractError(
                "Classic branch protection is incompatible with the selected merge mechanism: "
                + ", ".join(incompatible_classic_condition_list)
            )
        compatible_ruleset_rule_type_set = {"non_fast_forward", "deletion", "required_status_checks"}
        incompatible_ruleset_rule_type_list = sorted(
            set(self.ruleset_rule_type_list) - compatible_ruleset_rule_type_set
        )
        if incompatible_ruleset_rule_type_list:
            raise GitHubContractError(
                "GitHub ruleset is incompatible with the selected merge mechanism: "
                + ", ".join(incompatible_ruleset_rule_type_list)
            )
        if merge_method == "merge":
            if not self.non_fast_forward_protected or not self.deletion_protected:
                raise GitHubContractError("Base protection does not enforce protected-ref CAS safety")
            if self.required_check_name_list:
                raise GitHubContractError("Protected-ref CAS requires an exact zero required-check definition set")
            return
        if not self.strict_required_status_checks or not self.required_check_name_list:
            raise GitHubContractError("Base protection does not enforce strict up-to-date required checks")


@dataclass(frozen=True, slots=True)
class PullRequestSnapshot:
    """Contain all state used to bind one independent review and merge."""

    repository: RepositoryIdentity
    number: int
    url: str
    title: str
    state: str
    draft: bool
    base_branch: str
    base_commit: str
    head_branch: str
    head_commit: str
    merge_state: str
    merged_at: datetime | None
    merge_commit: str
    merged_by_login: str
    merged_by_node_id: str
    required_check_list: list[RequiredCheck]
    required_checks_verified: bool = False

    def __post_init__(self) -> None:
        """Validate one provider snapshot."""

        if not isinstance(self.repository, RepositoryIdentity):
            raise GitHubContractError("Pull request repository identity is unsupported")
        if isinstance(self.number, bool) or not isinstance(self.number, int) or self.number < 1:
            raise GitHubContractError("Pull request number must be positive")
        for label, value in (
            ("URL", self.url),
            ("title", self.title),
            ("state", self.state),
            ("base branch", self.base_branch),
            ("head branch", self.head_branch),
            ("merge state", self.merge_state),
        ):
            if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
                raise GitHubContractError(f"Pull request {label} must be non-empty single-line text")
        if _COMMIT_PATTERN.fullmatch(self.base_commit) is None:
            raise GitHubContractError("Pull request base must be one full lowercase commit")
        if _COMMIT_PATTERN.fullmatch(self.head_commit) is None:
            raise GitHubContractError("Pull request head must be one full lowercase commit")
        if self.merge_commit and _COMMIT_PATTERN.fullmatch(self.merge_commit) is None:
            raise GitHubContractError("Pull request merge commit must be empty or one full lowercase commit")
        if not isinstance(self.draft, bool):
            raise GitHubContractError("Pull request draft flag must be boolean")
        if not isinstance(self.required_check_list, list) or any(
            not isinstance(item, RequiredCheck) for item in self.required_check_list
        ):
            raise GitHubContractError("Pull request required checks have another shape")
        if not isinstance(self.required_checks_verified, bool):
            raise GitHubContractError("Pull request required-check verification flag must be boolean")
        check_name_list = [item.name for item in self.required_check_list]
        if len(check_name_list) != len(set(check_name_list)):
            raise GitHubContractError("Pull request repeats one required check name")
        if self.merged_at is not None:
            if self.merged_at.tzinfo is None or self.merged_at.utcoffset() is None:
                raise GitHubContractError("Pull request merged_at must be timezone-aware")
            if self.merged_at.utcoffset() != timezone.utc.utcoffset(self.merged_at):
                raise GitHubContractError("Pull request merged_at must be normalized to UTC")
        for label, value in (
            ("merged-by login", self.merged_by_login),
            ("merged-by node ID", self.merged_by_node_id),
        ):
            if not isinstance(value, str) or any(character in value for character in ("\x00", "\n", "\r")):
                raise GitHubContractError(f"Pull request {label} has another shape")
        if bool(self.merged_by_login) != bool(self.merged_by_node_id):
            raise GitHubContractError("Pull request merged-by identity is incomplete")
        object.__setattr__(self, "required_check_list", list(self.required_check_list))

    @classmethod
    def from_gh_payload(
        cls,
        repository: RepositoryIdentity,
        payload: object,
        *,
        required_check_list: list[RequiredCheck],
    ) -> "PullRequestSnapshot":
        """Parse one exact ``gh`` pull-request payload.

        Args:
            repository: Exact repository identity.
            payload: Candidate decoded response.
            required_check_list: Separately read required checks.

        Returns:
            Typed pull-request snapshot.
        """

        expected = {
            "number",
            "url",
            "title",
            "state",
            "isDraft",
            "autoMergeRequest",
            "baseRefName",
            "baseRefOid",
            "headRefName",
            "headRefOid",
            "headRepository",
            "headRepositoryOwner",
            "isCrossRepository",
            "mergeStateStatus",
            "mergedAt",
            "mergeCommit",
            "mergedBy",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise GitHubContractError("GitHub PR response has another shape")
        if payload.get("state") != "MERGED" and payload["autoMergeRequest"] is not None:
            raise GitHubContractError("Pull request has a deferred auto-merge request")
        head_repository = payload["headRepository"]
        head_repository_owner = payload["headRepositoryOwner"]
        if (
            not isinstance(head_repository, dict)
            or not isinstance(head_repository.get("nameWithOwner"), str)
            or RepositoryIdentity(head_repository["nameWithOwner"]) != repository
            or not isinstance(head_repository_owner, dict)
            or not isinstance(head_repository_owner.get("login"), str)
            or payload["isCrossRepository"] is not False
        ):
            raise GitHubContractError("Pull request head repository differs from the exact base repository")
        merged_at = payload["mergedAt"]
        if merged_at is not None:
            if not isinstance(merged_at, str):
                raise GitHubContractError("GitHub mergedAt has another shape")
            try:
                merged_instant = datetime.fromisoformat(merged_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError as error:
                raise GitHubContractError("GitHub mergedAt is malformed") from error
        else:
            merged_instant = None
        merge_commit = payload["mergeCommit"]
        if merge_commit is None:
            merge_commit_value = ""
        elif isinstance(merge_commit, dict) and set(merge_commit) >= {"oid"} and isinstance(merge_commit["oid"], str):
            merge_commit_value = merge_commit["oid"]
        else:
            raise GitHubContractError("GitHub mergeCommit has another shape")
        merged_by = payload["mergedBy"]
        if merged_by is None:
            merged_by_login = ""
            merged_by_node_id = ""
        elif (
            isinstance(merged_by, dict)
            and isinstance(merged_by.get("login"), str)
            and isinstance(merged_by.get("id"), str)
        ):
            merged_by_login = merged_by["login"]
            merged_by_node_id = merged_by["id"]
        else:
            raise GitHubContractError("GitHub mergedBy has another shape")
        return cls(
            repository=repository,
            number=payload["number"],
            url=payload["url"],
            title=payload["title"],
            state=payload["state"],
            draft=payload["isDraft"],
            base_branch=payload["baseRefName"],
            base_commit=payload["baseRefOid"],
            head_branch=payload["headRefName"],
            head_commit=payload["headRefOid"],
            merge_state=payload["mergeStateStatus"],
            merged_at=merged_instant,
            merge_commit=merge_commit_value,
            merged_by_login=merged_by_login,
            merged_by_node_id=merged_by_node_id,
            required_check_list=required_check_list,
        )

    def integration_identity_require(self, issue_identifier: str) -> None:
        """Require the issue identity that lets GitHub integration create a Linear link.

        Args:
            issue_identifier: Exact Linear issue identifier.
        """

        self.task_branch_identity_require(issue_identifier)
        issue_identifier_in_title_require(issue_identifier, self.title)

    def task_branch_identity_require(self, issue_identifier: str) -> None:
        """Require the immutable deterministic issue branch without mutable title text.

        Args:
            issue_identifier: Exact Linear issue identifier.
        """

        if _ISSUE_IDENTIFIER_PATTERN.fullmatch(issue_identifier) is None:
            raise GitHubContractError("Linear issue identifier has another shape")
        expected_branch = f"linear/{issue_identifier.lower()}"
        if self.head_branch != expected_branch:
            raise GitHubContractError("Pull request lacks the exact Linear issue branch identity")

    def target_require(self, *, base_branch: str, head_branch: str) -> None:
        """Require the PR to target the exact task Git refs.

        Args:
            base_branch: Declared destination branch.
            head_branch: Deterministic task branch.
        """

        if self.base_branch != base_branch or self.head_branch != head_branch:
            raise GitHubContractError("Pull request base or head differs from the declared repository target")

    def reviewed_open_identity_require(
        self,
        *,
        reviewed_base_commit: str,
        reviewed_head_commit: str,
    ) -> None:
        """Require immutable reviewed identities before reading mutable merge gates.

        Args:
            reviewed_base_commit: Exact base commit covered by independent review.
            reviewed_head_commit: Exact PR head covered by independent review.
        """

        if _COMMIT_PATTERN.fullmatch(reviewed_base_commit) is None:
            raise GitHubContractError("Reviewed base must be one full lowercase commit")
        if _COMMIT_PATTERN.fullmatch(reviewed_head_commit) is None:
            raise GitHubContractError("Reviewed head must be one full lowercase commit")
        if self.base_commit != reviewed_base_commit:
            raise GitHubContractError("Pull request base changed after independent review")
        if self.head_commit != reviewed_head_commit:
            raise GitHubContractError("Pull request head changed after independent review")
        if self.state != "OPEN" or self.draft:
            raise GitHubContractError("Pull request must be open and ready for review")
        if self.merge_state not in {"CLEAN", "HAS_HOOKS"}:
            raise GitHubContractError(f"Pull request is not mergeable: {self.merge_state}")

    def merge_preconditions_require(
        self,
        *,
        reviewed_base_commit: str,
        reviewed_head_commit: str,
    ) -> None:
        """Require the exact open identity and all freshly read provider gates.

        Args:
            reviewed_base_commit: Exact base commit covered by independent review.
            reviewed_head_commit: Exact PR head covered by independent review.
        """

        self.reviewed_open_identity_require(
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
        )
        if not self.required_checks_verified:
            raise GitHubContractError("Pull request required checks were not read through the typed merge boundary")
        failed_check_name_list = [item.name for item in self.required_check_list if not item.is_passed()]
        if failed_check_name_list:
            raise GitHubContractError(f"Required GitHub checks are not passing: {sorted(failed_check_name_list)}")

    def merged_result_require(
        self,
        *,
        reviewed_base_commit: str,
        reviewed_head_commit: str,
    ) -> None:
        """Require one exact already-merged independently reviewed base and head.

        This is the crash-recovery read path after the provider may have accepted
        the merge while the local process had not yet persisted its read-back.

        Args:
            reviewed_base_commit: Exact base commit covered by independent review.
            reviewed_head_commit: Exact head covered by independent review.
        """

        if _COMMIT_PATTERN.fullmatch(reviewed_base_commit) is None:
            raise GitHubContractError("Reviewed base must be one full lowercase commit")
        if _COMMIT_PATTERN.fullmatch(reviewed_head_commit) is None:
            raise GitHubContractError("Reviewed head must be one full lowercase commit")
        if self.state != "MERGED" or self.merged_at is None or not self.merge_commit:
            raise GitHubContractError("Pull request does not expose one complete merged result")
        if not self.merged_by_login or not self.merged_by_node_id:
            raise GitHubContractError("Pull request does not expose exact merged provider identity")

    def merged_by_require(self, *, login: str, node_id: str) -> None:
        """Require terminal provider identity to match the pre-mutation principal."""

        if self.merged_by_login.casefold() != login.casefold() or self.merged_by_node_id != node_id:
            raise GitHubContractError("GitHub merged provider identity differs from the executing principal")


@dataclass(frozen=True, slots=True)
class PullRequestMergeInspection:
    """Carry terminal-or-open PR state and only the applicable protection snapshot."""

    pull_request: PullRequestSnapshot
    branch_protection: BranchProtectionSnapshot | None

    def __post_init__(self) -> None:
        """Require protection only for an open pre-mutation pull request."""

        if not isinstance(self.pull_request, PullRequestSnapshot):
            raise GitHubContractError("Pull-request merge inspection has another shape")
        if self.pull_request.state == "MERGED":
            if self.branch_protection is not None:
                raise GitHubContractError("Merged pull-request recovery cannot depend on current protection")
        elif self.pull_request.state == "OPEN":
            if not isinstance(self.branch_protection, BranchProtectionSnapshot):
                raise GitHubContractError("Open pull-request inspection requires branch protection")
        else:
            raise GitHubContractError("Pull-request merge inspection requires an open or merged candidate")
