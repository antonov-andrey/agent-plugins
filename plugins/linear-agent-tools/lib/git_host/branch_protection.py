"""Typed exact-base GitHub branch-protection and effective-ruleset boundary."""

from __future__ import annotations

from collections.abc import Sequence
import json
import re
import subprocess
from urllib.parse import quote

from json_contract import JsonContractError, json_load_strict

from git_host.authentication import GitHubAuthenticationBoundary
from git_host.command import CommandRunner, command_closed_run, command_run
from git_host.model import BranchProtectionSnapshot, GitHubContractError, RepositoryIdentity, branch_name_require

_HTTP_STATUS_PATTERN = re.compile(r"HTTP/\S+ (?P<status>[1-5][0-9]{2})(?: .*)?")
_CLASSIC_PROTECTION_FIELD_SET = {
    "allow_deletions",
    "allow_force_pushes",
    "allow_fork_syncing",
    "block_creations",
    "enforce_admins",
    "lock_branch",
    "required_conversation_resolution",
    "required_linear_history",
    "required_pull_request_reviews",
    "required_signatures",
    "required_status_checks",
    "restrictions",
    "url",
}
_CLASSIC_REQUIRED_FIELD_SET = _CLASSIC_PROTECTION_FIELD_SET - {
    "required_pull_request_reviews",
    "required_status_checks",
    "restrictions",
}
_CLASSIC_ENABLED_FIELD_ALLOWED_KEY_SET_BY_NAME = {
    "allow_deletions": {"enabled"},
    "allow_force_pushes": {"enabled"},
    "allow_fork_syncing": {"enabled"},
    "block_creations": {"enabled"},
    "enforce_admins": {"enabled", "url"},
    "lock_branch": {"enabled"},
    "required_conversation_resolution": {"enabled"},
    "required_linear_history": {"enabled"},
    "required_signatures": {"enabled", "url"},
}
_CLASSIC_REVIEW_FIELD_SET = {
    "bypass_pull_request_allowances",
    "dismiss_stale_reviews",
    "dismissal_restrictions",
    "require_code_owner_reviews",
    "require_last_push_approval",
    "required_approving_review_count",
    "url",
}
_CLASSIC_RESTRICTION_FIELD_SET = {
    "apps",
    "apps_url",
    "teams",
    "teams_url",
    "url",
    "users",
    "users_url",
}
_CLASSIC_STATUS_CHECK_FIELD_SET = {"checks", "contexts", "contexts_url", "strict", "url"}
_KNOWN_RULESET_RULE_TYPE_SET = {
    "branch_name_pattern",
    "code_scanning",
    "commit_author_email_pattern",
    "commit_message_pattern",
    "committer_email_pattern",
    "copilot_code_review",
    "creation",
    "deletion",
    "file_extension_restriction",
    "file_path_restriction",
    "license_compliance_scanning",
    "max_file_path_length",
    "max_file_size",
    "merge_queue",
    "non_fast_forward",
    "pull_request",
    "required_deployments",
    "required_linear_history",
    "required_signatures",
    "required_status_checks",
    "tag_name_pattern",
    "update",
    "workflows",
}


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
        authentication = GitHubAuthenticationBoundary(self._runner)
        principal = authentication.principal_get()
        execution_permission = authentication.repository_permission_get(
            repository=repository,
            principal=principal,
        )
        classic_payload = self._classic_protection_get(repository=repository, base_branch=base_branch)
        effective_rule_list = self._effective_rule_list_get(repository=repository, base_branch=base_branch)

        protection_source_set: set[str] = set()
        required_check_name_set: set[str] = set()
        strict_required_status_checks = False
        non_fast_forward_protected = False
        deletion_protected = False
        execution_bypass = False
        admin_enforcement_enabled = False
        required_pull_request_gate_enabled = False
        required_linear_history_enabled = False
        required_signatures_enabled = False
        required_conversation_resolution_enabled = False
        branch_lock_enabled = False
        push_restrictions_enabled = False
        force_push_allowed = False
        deletion_allowed = False
        creation_blocked = False
        fork_sync_allowed = False
        ruleset_rule_type_set: set[str] = set()

        if classic_payload is not None:
            _classic_shape_require(
                classic_payload,
                repository=repository,
                base_branch=base_branch,
            )
            protection_source_set.add("classic")
            enforce_admins = _enabled_field_get(classic_payload, "enforce_admins")
            allow_force_pushes = _enabled_field_get(classic_payload, "allow_force_pushes")
            allow_deletions = _enabled_field_get(classic_payload, "allow_deletions")
            admin_enforcement_enabled = enforce_admins
            required_pull_request_gate_enabled = _classic_review_gate_enabled(classic_payload)
            required_linear_history_enabled = _enabled_field_get(classic_payload, "required_linear_history")
            required_signatures_enabled = _enabled_field_get(classic_payload, "required_signatures")
            required_conversation_resolution_enabled = _enabled_field_get(
                classic_payload,
                "required_conversation_resolution",
            )
            branch_lock_enabled = _enabled_field_get(classic_payload, "lock_branch")
            push_restrictions_enabled = _classic_restrictions_enabled(classic_payload)
            force_push_allowed = allow_force_pushes
            deletion_allowed = allow_deletions
            creation_blocked = _enabled_field_get(classic_payload, "block_creations")
            fork_sync_allowed = _enabled_field_get(classic_payload, "allow_fork_syncing")
            classic_check_name_set, classic_strict = _classic_required_checks_get(classic_payload)
            required_check_name_set.update(classic_check_name_set)
            strict_required_status_checks = strict_required_status_checks or classic_strict
            non_fast_forward_protected = not allow_force_pushes
            deletion_protected = not allow_deletions
            # ``enforce_admins`` is the only classic-protection proof that the
            # authenticated account cannot exercise an administrator or custom
            # repository-role bypass. Permission text alone cannot prove absence.
            execution_bypass = not enforce_admins

        rule_by_ruleset_id_map: dict[int, list[dict[str, object]]] = {}
        for rule in effective_rule_list:
            ruleset_id = rule["ruleset_id"]
            if isinstance(ruleset_id, bool) or not isinstance(ruleset_id, int) or ruleset_id < 1:
                raise GitHubContractError("Effective branch rule has another ruleset identity")
            rule_by_ruleset_id_map.setdefault(ruleset_id, []).append(rule)
        for ruleset_id, rule_list in sorted(rule_by_ruleset_id_map.items()):
            ruleset_payload = self._ruleset_get(repository=repository, ruleset_id=ruleset_id)
            full_rule_type_set = _ruleset_identity_require(
                ruleset_payload,
                ruleset_id=ruleset_id,
                effective_rule_list=rule_list,
            )
            ruleset_rule_type_set.update(full_rule_type_set)
            protection_source_set.add(f"ruleset:{ruleset_id}")
            if _ruleset_bypass_present(ruleset_payload):
                # The effective-rule endpoint does not expose enough membership and
                # custom-role detail to prove a bypass actor is foreign. Rejecting
                # every applicable bypass is the closed executing-identity result.
                execution_bypass = True
            for rule in rule_list:
                rule_type = rule["type"]
                if rule_type not in _KNOWN_RULESET_RULE_TYPE_SET:
                    raise GitHubContractError("GitHub effective ruleset contains an unknown rule type")
                if rule_type == "non_fast_forward":
                    _parameterless_compatible_rule_require(rule)
                    non_fast_forward_protected = True
                elif rule_type == "deletion":
                    _parameterless_compatible_rule_require(rule)
                    deletion_protected = True
                elif rule_type == "required_status_checks":
                    check_name_set, strict = _ruleset_required_checks_get(rule)
                    required_check_name_set.update(check_name_set)
                    strict_required_status_checks = strict_required_status_checks or strict

        return BranchProtectionSnapshot(
            repository=repository,
            base_branch=base_branch,
            execution_login=principal.login,
            execution_user_id=principal.user_id,
            execution_node_id=principal.node_id,
            execution_permission=execution_permission,
            protection_source_list=sorted(protection_source_set),
            ruleset_id_list=sorted(rule_by_ruleset_id_map),
            required_check_name_list=sorted(required_check_name_set),
            strict_required_status_checks=strict_required_status_checks,
            non_fast_forward_protected=non_fast_forward_protected,
            deletion_protected=deletion_protected,
            execution_bypass=execution_bypass,
            admin_enforcement_enabled=admin_enforcement_enabled,
            required_pull_request_gate_enabled=required_pull_request_gate_enabled,
            required_linear_history_enabled=required_linear_history_enabled,
            required_signatures_enabled=required_signatures_enabled,
            required_conversation_resolution_enabled=required_conversation_resolution_enabled,
            branch_lock_enabled=branch_lock_enabled,
            push_restrictions_enabled=push_restrictions_enabled,
            force_push_allowed=force_push_allowed,
            deletion_allowed=deletion_allowed,
            creation_blocked=creation_blocked,
            fork_sync_allowed=fork_sync_allowed,
            ruleset_rule_type_list=sorted(ruleset_rule_type_set),
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
        completed_process = command_closed_run(
            self._runner,
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
            ],
        )
        _completed_json_require(completed_process, label="GitHub branch-protection configuration")
        after = self.inspect(repository=repository, base_branch=base_branch)
        after.merge_mechanism_require("merge")
        return after

    def _classic_protection_get(
        self,
        *,
        repository: RepositoryIdentity,
        base_branch: str,
    ) -> dict[str, object] | None:
        """Read classic branch protection, accepting only one typed 404 as absence."""

        encoded_branch = quote(base_branch, safe="")
        completed_process = command_closed_run(
            self._runner,
            [
                "gh",
                "api",
                "--include",
                f"repos/{repository.value}/branches/{encoded_branch}/protection",
            ],
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

        completed_process = command_closed_run(self._runner, ["gh", *argument_list])
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


def _classic_shape_require(
    payload: dict[str, object],
    *,
    repository: RepositoryIdentity,
    base_branch: str,
) -> None:
    """Require the complete known classic protection surface."""

    expected_url = f"https://api.github.com/repos/{repository.value}/branches/{quote(base_branch, safe='')}/protection"
    if (
        not _CLASSIC_REQUIRED_FIELD_SET <= set(payload) <= _CLASSIC_PROTECTION_FIELD_SET
        or payload["url"] != expected_url
    ):
        raise GitHubContractError("Classic branch-protection response has unknown or missing fields")


def _enabled_field_get(payload: dict[str, object], field_name: str) -> bool:
    """Read one classic protection enabled object."""

    field = payload.get(field_name)
    allowed_key_set = _CLASSIC_ENABLED_FIELD_ALLOWED_KEY_SET_BY_NAME.get(field_name)
    if (
        allowed_key_set is None
        or not isinstance(field, dict)
        or not {"enabled"} <= set(field) <= allowed_key_set
        or not isinstance(field.get("enabled"), bool)
        or ("url" in field and (not isinstance(field["url"], str) or not field["url"]))
    ):
        raise GitHubContractError(f"Classic branch-protection {field_name} has another shape")
    return field["enabled"]


def _classic_review_gate_enabled(payload: dict[str, object]) -> bool:
    """Validate and report the complete classic pull-request gate family."""

    field = payload.get("required_pull_request_reviews")
    if field is None:
        return False
    if (
        not isinstance(field, dict)
        or not {"url"} <= set(field) <= _CLASSIC_REVIEW_FIELD_SET
        or not isinstance(field["url"], str)
        or not field["url"]
    ):
        raise GitHubContractError("Classic branch-protection required_pull_request_reviews has another shape")
    for name in ("dismiss_stale_reviews", "require_code_owner_reviews", "require_last_push_approval"):
        if name in field and not isinstance(field[name], bool):
            raise GitHubContractError("Classic branch-protection required_pull_request_reviews has another shape")
    count = field.get("required_approving_review_count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        raise GitHubContractError("Classic branch-protection required_pull_request_reviews has another shape")
    for name in ("dismissal_restrictions", "bypass_pull_request_allowances"):
        if name in field:
            _classic_actor_collection_require(field[name], label=name)
    return True


def _classic_restrictions_enabled(payload: dict[str, object]) -> bool:
    """Validate and report the complete classic push-restriction family."""

    field = payload.get("restrictions")
    if field is None:
        return False
    if not isinstance(field, dict) or set(field) != _CLASSIC_RESTRICTION_FIELD_SET:
        raise GitHubContractError("Classic branch-protection restrictions has another shape")
    for name in ("url", "users_url", "teams_url", "apps_url"):
        if not isinstance(field[name], str) or not field[name]:
            raise GitHubContractError("Classic branch-protection restrictions has another shape")
    for name in ("users", "teams", "apps"):
        if not isinstance(field[name], list) or any(not isinstance(item, dict) for item in field[name]):
            raise GitHubContractError("Classic branch-protection restrictions has another shape")
    return True


def _classic_actor_collection_require(value: object, *, label: str) -> None:
    """Validate one nested classic review bypass or dismissal collection."""

    if not isinstance(value, dict):
        raise GitHubContractError(f"Classic branch-protection {label} has another shape")
    if label == "dismissal_restrictions":
        required_key_set = {"url", "users_url", "teams_url", "users", "teams"}
        allowed_key_set = required_key_set | {"apps"}
        url_name_list = ("url", "users_url", "teams_url")
    else:
        required_key_set = {"users", "teams"}
        allowed_key_set = required_key_set | {"apps"}
        url_name_list = ()
    if not required_key_set <= set(value) <= allowed_key_set:
        raise GitHubContractError(f"Classic branch-protection {label} has another shape")
    for name in url_name_list:
        if not isinstance(value[name], str) or not value[name]:
            raise GitHubContractError(f"Classic branch-protection {label} has another shape")
    for name in ("users", "teams", "apps"):
        if name in value and (
            not isinstance(value[name], list) or any(not isinstance(item, dict) for item in value[name])
        ):
            raise GitHubContractError(f"Classic branch-protection {label} has another shape")


def _classic_required_checks_get(payload: dict[str, object]) -> tuple[set[str], bool]:
    """Read exact classic required-check definitions and strict policy."""

    value = payload.get("required_status_checks")
    if value is None:
        return set(), False
    if (
        not isinstance(value, dict)
        or set(value) != _CLASSIC_STATUS_CHECK_FIELD_SET
        or not isinstance(value.get("strict"), bool)
        or not isinstance(value.get("contexts"), list)
        or not isinstance(value.get("checks"), list)
        or not isinstance(value.get("url"), str)
        or not value["url"]
        or not isinstance(value.get("contexts_url"), str)
        or not value["contexts_url"]
    ):
        raise GitHubContractError("Classic required-status-check protection has another shape")
    context_name_set: set[str] = set()
    for context in value["contexts"]:
        _unique_check_name_add(context_name_set, context)
    check_name_set: set[str] = set()
    for check in value["checks"]:
        if not isinstance(check, dict) or set(check) != {"context", "app_id"}:
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
) -> set[str]:
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
    full_rule_definition_list: list[str] = []
    for rule in payload["rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("type"), str) or not rule["type"]:
            raise GitHubContractError("GitHub full ruleset rule has another shape")
        if rule["type"] not in _KNOWN_RULESET_RULE_TYPE_SET:
            raise GitHubContractError("GitHub full ruleset contains an unknown rule type")
        full_rule_type_list.append(rule["type"])
        full_rule_definition_list.append(_rule_definition_json(rule))
    if len(full_rule_type_list) != len(set(full_rule_type_list)):
        raise GitHubContractError("GitHub full ruleset repeats one rule type")
    effective_rule_definition_list = [_effective_rule_definition_json(rule) for rule in effective_rule_list]
    if sorted(full_rule_definition_list) != sorted(effective_rule_definition_list):
        raise GitHubContractError("GitHub effective rules differ from their full ruleset")
    return set(full_rule_type_list)


def _rule_definition_json(rule: dict[str, object]) -> str:
    """Return one deterministic full ruleset rule definition."""

    return json.dumps(rule, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _effective_rule_definition_json(rule: dict[str, object]) -> str:
    """Strip only effective-source metadata from one exact rule definition."""

    return _rule_definition_json(_effective_rule_definition_get(rule))


def _effective_rule_definition_get(rule: dict[str, object]) -> dict[str, object]:
    """Return one rule without only the known effective-source metadata."""

    return {
        name: value
        for name, value in rule.items()
        if name not in {"ruleset_id", "ruleset_source", "ruleset_source_type"}
    }


def _parameterless_compatible_rule_require(rule: dict[str, object]) -> None:
    """Require an exact parameterless shape for an allowed ref-safety rule."""

    if set(_effective_rule_definition_get(rule)) != {"type"}:
        raise GitHubContractError("Compatible GitHub ruleset rule has another shape")


def _ruleset_bypass_present(payload: dict[str, object]) -> bool:
    """Validate every bypass actor and report whether any bypass exists."""

    actor_list = payload["bypass_actors"]
    if not isinstance(actor_list, list):
        raise GitHubContractError("GitHub ruleset bypass actors have another shape")
    for actor in actor_list:
        if (
            not isinstance(actor, dict)
            or set(actor) != {"actor_id", "actor_type", "bypass_mode"}
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

    definition = _effective_rule_definition_get(rule)
    parameters = definition.get("parameters")
    if (
        set(definition) != {"type", "parameters"}
        or not isinstance(parameters, dict)
        or not {"required_status_checks", "strict_required_status_checks_policy"}
        <= set(parameters)
        <= {"do_not_enforce_on_create", "required_status_checks", "strict_required_status_checks_policy"}
        or not isinstance(parameters.get("strict_required_status_checks_policy"), bool)
        or not isinstance(parameters.get("required_status_checks"), list)
        or ("do_not_enforce_on_create" in parameters and not isinstance(parameters["do_not_enforce_on_create"], bool))
    ):
        raise GitHubContractError("Ruleset required-status-check rule has another shape")
    name_set: set[str] = set()
    for check in parameters["required_status_checks"]:
        if not isinstance(check, dict) or set(check) != {"context", "integration_id"}:
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
