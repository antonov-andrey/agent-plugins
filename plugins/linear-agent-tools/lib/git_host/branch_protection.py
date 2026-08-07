"""Typed exact-base GitHub branch-protection and effective-ruleset boundary."""

from __future__ import annotations

from collections.abc import Sequence
import re
import subprocess
from urllib.parse import quote

from json_contract import JsonContractError, json_load_strict

from git_host.command import CommandRunner, command_run
from git_host.model import BranchProtectionSnapshot, GitHubContractError, RepositoryIdentity, branch_name_require

_HTTP_STATUS_PATTERN = re.compile(r"HTTP/\S+ (?P<status>[1-5][0-9]{2})(?: .*)?")


class GitHubBranchProtectionBoundary:
    """Read and minimally configure protection for one exact repository base."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        """Initialize one authenticated direct-command dependency.

        Args:
            runner: Optional deterministic command runner.
        """

        self._runner = runner or command_run

    def inspect(
        self,
        *,
        repository: RepositoryIdentity,
        base_branch: str,
    ) -> BranchProtectionSnapshot:
        """Read classic protection and every effective active branch rule.

        Args:
            repository: Exact GitHub repository.
            base_branch: Exact protected base branch.

        Returns:
            Protection bound to the authenticated executing identity.
        """

        if not isinstance(repository, RepositoryIdentity):
            raise GitHubContractError("Branch-protection repository identity is unsupported")
        branch_name_require(base_branch, label="protected base")
        execution_login = self._execution_login_get()
        execution_permission = self._execution_permission_get(
            repository=repository,
            execution_login=execution_login,
        )
        classic_payload = self._classic_protection_get(repository=repository, base_branch=base_branch)
        effective_rule_list = self._effective_rule_list_get(repository=repository, base_branch=base_branch)

        protection_source_set: set[str] = set()
        required_check_name_set: set[str] = set()
        strict_required_status_checks = False
        non_fast_forward_protected = False
        deletion_protected = False
        execution_bypass = False

        if classic_payload is not None:
            protection_source_set.add("classic")
            enforce_admins = _enabled_field_get(classic_payload, "enforce_admins")
            allow_force_pushes = _enabled_field_get(classic_payload, "allow_force_pushes")
            allow_deletions = _enabled_field_get(classic_payload, "allow_deletions")
            classic_check_name_set, classic_strict = _classic_required_checks_get(classic_payload)
            required_check_name_set.update(classic_check_name_set)
            strict_required_status_checks = strict_required_status_checks or classic_strict
            non_fast_forward_protected = not allow_force_pushes
            deletion_protected = not allow_deletions
            execution_bypass = execution_permission == "admin" and not enforce_admins

        rule_by_ruleset_id_map: dict[int, list[dict[str, object]]] = {}
        for rule in effective_rule_list:
            ruleset_id = rule["ruleset_id"]
            if isinstance(ruleset_id, bool) or not isinstance(ruleset_id, int) or ruleset_id < 1:
                raise GitHubContractError("Effective branch rule has another ruleset identity")
            rule_by_ruleset_id_map.setdefault(ruleset_id, []).append(rule)
        for ruleset_id, rule_list in sorted(rule_by_ruleset_id_map.items()):
            ruleset_payload = self._ruleset_get(repository=repository, ruleset_id=ruleset_id)
            _ruleset_identity_require(ruleset_payload, ruleset_id=ruleset_id, effective_rule_list=rule_list)
            protection_source_set.add(f"ruleset:{ruleset_id}")
            if _ruleset_bypass_present(ruleset_payload):
                # The effective-rule endpoint does not expose enough membership and
                # custom-role detail to prove a bypass actor is foreign. Rejecting
                # every applicable bypass is the closed executing-identity result.
                execution_bypass = True
            for rule in rule_list:
                rule_type = rule["type"]
                if rule_type == "non_fast_forward":
                    non_fast_forward_protected = True
                elif rule_type == "deletion":
                    deletion_protected = True
                elif rule_type == "required_status_checks":
                    check_name_set, strict = _ruleset_required_checks_get(rule)
                    required_check_name_set.update(check_name_set)
                    strict_required_status_checks = strict_required_status_checks or strict
                elif rule_type == "merge_queue":
                    raise GitHubContractError(
                        "Deferred GitHub merge-queue protection is incompatible with exact-base merge"
                    )

        return BranchProtectionSnapshot(
            repository=repository,
            base_branch=base_branch,
            execution_login=execution_login,
            execution_permission=execution_permission,
            protection_source_list=sorted(protection_source_set),
            ruleset_id_list=sorted(rule_by_ruleset_id_map),
            required_check_name_list=sorted(required_check_name_set),
            strict_required_status_checks=strict_required_status_checks,
            non_fast_forward_protected=non_fast_forward_protected,
            deletion_protected=deletion_protected,
            execution_bypass=execution_bypass,
        )

    def configure_for_protected_ref_cas(
        self,
        *,
        repository: RepositoryIdentity,
        base_branch: str,
    ) -> BranchProtectionSnapshot:
        """Create minimal classic protection only when protection is absent.

        Existing unsafe protection is a conflict and is never weakened or
        overwritten by this exact configuration path.

        Args:
            repository: Exact GitHub repository.
            base_branch: Exact protected base branch.

        Returns:
            Fresh effective protection readback.
        """

        before = self.inspect(repository=repository, base_branch=base_branch)
        if before.protection_source_list:
            before.merge_mechanism_require("merge")
            return before
        encoded_branch = quote(base_branch, safe="")
        completed_process = self._runner(
            [
                "gh",
                "api",
                "--method",
                "PUT",
                f"repos/{repository.value}/branches/{encoded_branch}/protection",
                "-F",
                "required_status_checks=null",
                "-F",
                "enforce_admins=true",
                "-F",
                "required_pull_request_reviews=null",
                "-F",
                "restrictions=null",
                "-F",
                "required_linear_history=false",
                "-F",
                "allow_force_pushes=false",
                "-F",
                "allow_deletions=false",
                "-F",
                "block_creations=false",
                "-F",
                "required_conversation_resolution=false",
                "-F",
                "lock_branch=false",
                "-F",
                "allow_fork_syncing=false",
            ]
        )
        _completed_json_require(completed_process, label="GitHub branch-protection configuration")
        after = self.inspect(repository=repository, base_branch=base_branch)
        after.merge_mechanism_require("merge")
        return after

    def _execution_login_get(self) -> str:
        """Read the exact authenticated GitHub login."""

        payload = self._json_get(("api", "user"), label="GitHub executing identity")
        if not isinstance(payload, dict) or not isinstance(payload.get("login"), str) or not payload["login"]:
            raise GitHubContractError("GitHub executing identity has another shape")
        if any(character in payload["login"] for character in ("\x00", "\n", "\r", "/")):
            raise GitHubContractError("GitHub executing identity has another shape")
        return payload["login"]

    def _execution_permission_get(
        self,
        *,
        repository: RepositoryIdentity,
        execution_login: str,
    ) -> str:
        """Read exact repository permission for the executing login."""

        payload = self._json_get(
            ("api", f"repos/{repository.value}/collaborators/{quote(execution_login, safe='')}/permission"),
            label="GitHub executing repository permission",
        )
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("permission"), str)
            or not isinstance(payload.get("user"), dict)
            or not isinstance(payload["user"].get("login"), str)
            or payload["user"]["login"].casefold() != execution_login.casefold()
        ):
            raise GitHubContractError("GitHub executing repository permission has another shape")
        return payload["permission"]

    def _classic_protection_get(
        self,
        *,
        repository: RepositoryIdentity,
        base_branch: str,
    ) -> dict[str, object] | None:
        """Read classic branch protection, accepting only one typed 404 as absence."""

        encoded_branch = quote(base_branch, safe="")
        completed_process = self._runner(
            [
                "gh",
                "api",
                "--include",
                f"repos/{repository.value}/branches/{encoded_branch}/protection",
            ]
        )
        status, payload = _included_json_get(completed_process)
        if status == 404:
            if completed_process.returncode != 1 or not isinstance(payload, dict) or payload.get("status") != "404":
                raise GitHubContractError("GitHub branch-protection absence response has another shape")
            return None
        if status != 200 or completed_process.returncode != 0 or not isinstance(payload, dict):
            raise GitHubContractError("Unable to read classic GitHub branch protection")
        return payload

    def _effective_rule_list_get(
        self,
        *,
        repository: RepositoryIdentity,
        base_branch: str,
    ) -> list[dict[str, object]]:
        """Fully paginate active rules that apply to the exact branch."""

        encoded_branch = quote(base_branch, safe="")
        payload = self._json_get(
            (
                "api",
                "--method",
                "GET",
                "--paginate",
                "--slurp",
                f"repos/{repository.value}/rules/branches/{encoded_branch}",
                "-f",
                "per_page=100",
            ),
            label="GitHub effective branch rules",
        )
        if not isinstance(payload, list) or any(not isinstance(page, list) for page in payload):
            raise GitHubContractError("GitHub effective branch-rule response has another shape")
        rule_list: list[dict[str, object]] = []
        for page in payload:
            for item in page:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("type"), str)
                    or not item["type"]
                    or not isinstance(item.get("ruleset_source_type"), str)
                    or not isinstance(item.get("ruleset_source"), str)
                    or not item["ruleset_source"]
                    or "ruleset_id" not in item
                ):
                    raise GitHubContractError("GitHub effective branch-rule response has another shape")
                rule_list.append(item)
        return rule_list

    def _ruleset_get(self, *, repository: RepositoryIdentity, ruleset_id: int) -> dict[str, object]:
        """Read one complete applicable ruleset including its bypass actors."""

        payload = self._json_get(
            ("api", f"repos/{repository.value}/rulesets/{ruleset_id}?includes_parents=true"),
            label="GitHub branch ruleset",
        )
        if not isinstance(payload, dict):
            raise GitHubContractError("GitHub branch ruleset has another shape")
        return payload

    def _json_get(self, argument_list: Sequence[str], *, label: str) -> object:
        """Run one successful gh read and require nonempty strict JSON."""

        completed_process = self._runner(["gh", *argument_list])
        return _completed_json_require(completed_process, label=label)


def _completed_json_require(completed_process: subprocess.CompletedProcess[str], *, label: str) -> object:
    """Require successful nonempty strict JSON provider output."""

    if completed_process.returncode != 0 or not completed_process.stdout:
        raise GitHubContractError(f"{label} failed")
    try:
        return json_load_strict(completed_process.stdout)
    except JsonContractError as error:
        raise GitHubContractError(f"{label} response is malformed") from error


def _included_json_get(completed_process: subprocess.CompletedProcess[str]) -> tuple[int, object]:
    """Parse an include-header gh response without treating generic exit one as absence."""

    normalized = completed_process.stdout.replace("\r\n", "\n")
    header, separator, body = normalized.partition("\n\n")
    first_line = header.split("\n", 1)[0]
    match = _HTTP_STATUS_PATTERN.fullmatch(first_line)
    if not separator or match is None or not body:
        raise GitHubContractError("GitHub branch-protection response is malformed")
    try:
        payload = json_load_strict(body)
    except JsonContractError as error:
        raise GitHubContractError("GitHub branch-protection response is malformed") from error
    return int(match.group("status")), payload


def _enabled_field_get(payload: dict[str, object], field_name: str) -> bool:
    """Read one classic protection enabled object."""

    field = payload.get(field_name)
    if not isinstance(field, dict) or not isinstance(field.get("enabled"), bool):
        raise GitHubContractError(f"Classic branch-protection {field_name} has another shape")
    return field["enabled"]


def _classic_required_checks_get(payload: dict[str, object]) -> tuple[set[str], bool]:
    """Read exact classic required-check definitions and strict policy."""

    value = payload.get("required_status_checks")
    if value is None:
        return set(), False
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("strict"), bool)
        or not isinstance(value.get("contexts"), list)
        or not isinstance(value.get("checks"), list)
    ):
        raise GitHubContractError("Classic required-status-check protection has another shape")
    context_name_set: set[str] = set()
    for context in value["contexts"]:
        _unique_check_name_add(context_name_set, context)
    check_name_set: set[str] = set()
    for check in value["checks"]:
        if not isinstance(check, dict) or set(check) < {"context", "app_id"}:
            raise GitHubContractError("Classic required-status-check definition has another shape")
        app_id = check["app_id"]
        if app_id is not None and (isinstance(app_id, bool) or not isinstance(app_id, int) or app_id < 1):
            raise GitHubContractError("Classic required-status-check app identity has another shape")
        _unique_check_name_add(check_name_set, check["context"])
    if context_name_set and check_name_set and context_name_set != check_name_set:
        raise GitHubContractError("Classic required-status-check definitions disagree")
    name_set = check_name_set or context_name_set
    return name_set, value["strict"] and bool(name_set)


def _ruleset_identity_require(
    payload: dict[str, object],
    *,
    ruleset_id: int,
    effective_rule_list: list[dict[str, object]],
) -> None:
    """Bind one full active ruleset to the effective-rule source identity."""

    first_rule = effective_rule_list[0]
    if (
        payload.get("id") != ruleset_id
        or payload.get("target") != "branch"
        or payload.get("enforcement") != "active"
        or payload.get("source_type") != first_rule["ruleset_source_type"]
        or payload.get("source") != first_rule["ruleset_source"]
        or not isinstance(payload.get("rules"), list)
        or "bypass_actors" not in payload
    ):
        raise GitHubContractError("GitHub effective ruleset identity has another shape")
    if any(
        rule.get("ruleset_source_type") != first_rule["ruleset_source_type"]
        or rule.get("ruleset_source") != first_rule["ruleset_source"]
        for rule in effective_rule_list
    ):
        raise GitHubContractError("GitHub effective rules disagree on ruleset source")
    full_rule_type_list: list[str] = []
    for rule in payload["rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("type"), str) or not rule["type"]:
            raise GitHubContractError("GitHub full ruleset rule has another shape")
        full_rule_type_list.append(rule["type"])
    if any(rule["type"] not in full_rule_type_list for rule in effective_rule_list):
        raise GitHubContractError("GitHub effective rules differ from their full ruleset")


def _ruleset_bypass_present(payload: dict[str, object]) -> bool:
    """Validate every bypass actor and report whether any bypass exists."""

    actor_list = payload["bypass_actors"]
    if not isinstance(actor_list, list):
        raise GitHubContractError("GitHub ruleset bypass actors have another shape")
    for actor in actor_list:
        if (
            not isinstance(actor, dict)
            or set(actor) < {"actor_id", "actor_type", "bypass_mode"}
            or actor["actor_type"]
            not in {"Integration", "OrganizationAdmin", "RepositoryRole", "Team", "DeployKey", "User"}
            or actor["bypass_mode"] not in {"always", "pull_request", "exempt"}
            or (
                actor["actor_id"] is not None
                and (isinstance(actor["actor_id"], bool) or not isinstance(actor["actor_id"], int))
            )
        ):
            raise GitHubContractError("GitHub ruleset bypass actor has another shape")
    return bool(actor_list)


def _ruleset_required_checks_get(rule: dict[str, object]) -> tuple[set[str], bool]:
    """Read exact required-check definitions from one effective ruleset rule."""

    parameters = rule.get("parameters")
    if (
        not isinstance(parameters, dict)
        or not isinstance(parameters.get("strict_required_status_checks_policy"), bool)
        or not isinstance(parameters.get("required_status_checks"), list)
    ):
        raise GitHubContractError("Ruleset required-status-check rule has another shape")
    name_set: set[str] = set()
    for check in parameters["required_status_checks"]:
        if not isinstance(check, dict) or set(check) < {"context", "integration_id"}:
            raise GitHubContractError("Ruleset required-status-check definition has another shape")
        integration_id = check["integration_id"]
        if integration_id is not None and (
            isinstance(integration_id, bool) or not isinstance(integration_id, int) or integration_id < 1
        ):
            raise GitHubContractError("Ruleset required-status-check integration has another shape")
        _unique_check_name_add(name_set, check["context"])
    return name_set, parameters["strict_required_status_checks_policy"] and bool(name_set)


def _unique_check_name_add(name_set: set[str], value: object) -> None:
    """Validate and add one nonrepeated required-check context name."""

    if not isinstance(value, str) or not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise GitHubContractError("Required-status-check context has another shape")
    if value in name_set:
        raise GitHubContractError("Required-status-check definitions repeat one context")
    name_set.add(value)
