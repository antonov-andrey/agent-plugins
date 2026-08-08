"""Behavior tests for semantic handoff evidence and exact GitHub review binding."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from urllib.parse import urlsplit

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools"
LIBRARY_ROOT = PLUGIN_ROOT / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

import git_host.command as git_host_command
from git_host.atomic_merge import GitHubAtomicMergeBoundary
from git_host.authentication import GitHubPrincipal, git_credential_config_argument_list_get
from git_host.branch_protection import GitHubBranchProtectionBoundary
from git_host.command import command_closed_run, command_run
from git_host.model import BranchProtectionSnapshot, GitHubContractError, RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary
from git_host.repository_policy import GitHubRepositoryMergePolicy, GitHubRepositoryMergePolicyBoundary
from git_host.transport_runtime import GitTransportRuntime
from verification._validation import EvidenceContractError, evidence_url_validate, instant_parse
from verification.baseline import LocalPhaseBaseline, TaskWorkspaceBaseline
from verification.comment import (
    HANDOFF_COMMENT_CODEC,
    LOCAL_PHASE_BASELINE_COMMENT_CODEC,
    TASK_WORKSPACE_BASELINE_COMMENT_CODEC,
)
from verification.handoff import (
    CodexUsage,
    TaskHandoff,
    TaskHandoffCheckResult,
    TaskHandoffPullRequestCandidate,
)

COMMIT_ONE = "a" * 40
COMMIT_TWO = "b" * 40
COMMIT_BASE = "c" * 40
ISSUE_EVIDENCE_URL = "https://linear.app/acme/issue/AND-17/direct-evidence"
BASELINE_EVIDENCE_URL = "https://linear.app/acme/issue/AND-17/local-phase-baseline"
PULL_REQUEST_URL = "https://github.com/antonov-andrey/example/pull/17"


@pytest.fixture(autouse=True)
def _ordinary_task_repository_create(tmp_path: Path) -> None:
    """Give every repository-bound test one ordinary local audit source."""

    git_dir = tmp_path / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    (git_dir / "objects" / "info").mkdir(parents=True)
    (git_dir / "info").mkdir(parents=True)
    (git_dir / "refs").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/linear/and-17\n", encoding="utf-8")
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n"
        '[remote "origin"]\n\turl = git@github.com:antonov-andrey/example.git\n'
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
        encoding="utf-8",
    )


def _check_result(**replacement_by_name: object) -> TaskHandoffCheckResult:
    """Return one direct deterministic check result."""

    field_by_name: dict[str, object] = {
        "name": "python -m pytest test/linear_agent_tools -q",
        "result": "436 passed",
        "evidence_url": ISSUE_EVIDENCE_URL,
    }
    field_by_name.update(replacement_by_name)
    return TaskHandoffCheckResult(**field_by_name)  # type: ignore[arg-type]


def _handoff(**replacement_by_name: object) -> TaskHandoff:
    """Return one final minimal implementation handoff."""

    field_by_name: dict[str, object] = {
        "summary": "Implemented the bounded provider owner and stopped at independent Review.",
        "pull_request_candidate_list": [_pull_request_candidate()],
        "check_result_list": [_check_result()],
        "codex_usage": CodexUsage(input_tokens=5, reasoning_output_tokens=11),
    }
    field_by_name.update(replacement_by_name)
    return TaskHandoff(**field_by_name)  # type: ignore[arg-type]


def _pull_request_candidate(**replacement_by_name: object) -> TaskHandoffPullRequestCandidate:
    """Return one exact deterministic PR candidate."""

    field_by_name: dict[str, object] = {
        "url": PULL_REQUEST_URL,
        "base_branch": "main",
        "base_commit": COMMIT_BASE,
        "head_commit": COMMIT_ONE,
        "merged_commit": None,
    }
    field_by_name.update(replacement_by_name)
    return TaskHandoffPullRequestCandidate(**field_by_name)  # type: ignore[arg-type]


def test_semantic_handoff_round_trips_direct_state_and_exact_usage() -> None:
    """The provider comment starts with summary and carries only consumed state."""

    handoff = _handoff()
    rendered = HANDOFF_COMMENT_CODEC.render(handoff.payload())
    parsed_payload = HANDOFF_COMMENT_CODEC.payload_parse(rendered)

    assert TaskHandoff.from_payload(parsed_payload) == handoff
    assert rendered.startswith('<!-- linear-agent-tools-handoff -->\n```json\n{\n  "summary":')
    assert parsed_payload["pull_request_candidate_list"] == [
        {
            "url": PULL_REQUEST_URL,
            "base_branch": "main",
            "base_commit": COMMIT_BASE,
            "head_commit": COMMIT_ONE,
        }
    ]
    assert parsed_payload["check_result_list"] == [
        {
            "name": "python -m pytest test/linear_agent_tools -q",
            "result": "436 passed",
            "evidence_url": ISSUE_EVIDENCE_URL,
        }
    ]
    assert parsed_payload["codex_usage"] == {"input_tokens": 5, "reasoning_output_tokens": 11}
    assert set(parsed_payload) == {
        "summary",
        "pull_request_candidate_list",
        "check_result_list",
        "codex_usage",
    }
    for removed_name in (
        "handoff_id",
        "issue_identifier",
        "operation",
        "role_label",
        "delivery_kind",
        "started_at",
        "completed_at",
        "outcome",
        "attempt_cleanup_complete",
        "commit_by_repository_map",
        "pull_request_base_branch_by_url_map",
        "pull_request_base_commit_by_url_map",
        "pull_request_head_by_url_map",
        "verification_summary_list",
        "evidence_url_list",
        "local_phase_baseline_evidence_url",
        "schema_version",
        "fingerprint",
        "receipt",
    ):
        assert removed_name not in rendered


@pytest.mark.parametrize(
    "field_name",
    (
        "cached_input_tokens",
        "cache_write_input_tokens",
        "input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    ),
)
def test_handoff_usage_validates_every_present_known_counter(field_name: str) -> None:
    """Every supported counter is validated even when it is the only exposed value."""

    with pytest.raises(EvidenceContractError, match=field_name):
        CodexUsage(**{field_name: -1})  # type: ignore[arg-type]


@pytest.mark.parametrize("value", (True, 1.5, -1))
def test_handoff_usage_rejects_nonexact_values(value: object) -> None:
    """Usage cannot be boolean, fractional or negative."""

    with pytest.raises(EvidenceContractError, match="input_tokens"):
        CodexUsage(input_tokens=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "usage_payload",
    (
        {},
        {"total_tokens": 1},
        {"estimated_input_tokens": 1},
        {"input_tokens": 1, "total_tokens": 1},
    ),
)
def test_handoff_usage_rejects_empty_unknown_and_estimated_shapes(usage_payload: dict[str, object]) -> None:
    """The telemetry object is one nonempty closed subset of known exact counters."""

    payload = _handoff().payload()
    payload["codex_usage"] = usage_payload

    with pytest.raises(EvidenceContractError, match="another shape"):
        TaskHandoff.from_payload(payload)


def test_handoff_usage_rejects_estimated_value_for_known_counter() -> None:
    """An estimate cannot masquerade under one otherwise known counter name."""

    payload = _handoff().payload()
    payload["codex_usage"] = {"input_tokens": "estimated: 100"}

    with pytest.raises(EvidenceContractError, match="input_tokens"):
        TaskHandoff.from_payload(payload)


def test_handoff_omits_usage_only_when_no_counter_is_exposed() -> None:
    """No telemetry object is emitted when Codex exposes no exact counter."""

    payload = _handoff(codex_usage=None).payload()

    assert "codex_usage" not in payload
    with pytest.raises(EvidenceContractError, match="at least one exposed counter"):
        CodexUsage()


def test_review_handoff_binds_current_composite_pr_candidate() -> None:
    """An independent reviewer compares one composite candidate with fresh state."""

    review = _handoff(
        summary="Independent full-scope review found zero findings.",
    )

    assert TaskHandoff.from_payload(review.payload()) == review
    review.current_pull_request_identity_require(
        current_pull_request_candidate_list=[_pull_request_candidate()],
    )
    with pytest.raises(EvidenceContractError, match="identity changed"):
        review.current_pull_request_identity_require(
            current_pull_request_candidate_list=[_pull_request_candidate(base_commit=COMMIT_TWO)],
        )


def test_noncode_handoff_omits_inapplicable_pr_state_and_links_direct_result() -> None:
    """Acceptance carries its baseline link without fake code-delivery state."""

    acceptance = _handoff(
        summary="Whole deployed outcome passed and awaits the final human decision.",
        pull_request_candidate_list=None,
        check_result_list=[
            _check_result(
                name="Local phase baseline",
                result="Published and read back",
                evidence_url=BASELINE_EVIDENCE_URL,
            )
        ],
        codex_usage=None,
    )

    payload = acceptance.payload()
    assert TaskHandoff.from_payload(payload) == acceptance
    assert "pull_request_candidate_list" not in payload
    assert payload["check_result_list"] == [
        {
            "name": "Local phase baseline",
            "result": "Published and read back",
            "evidence_url": BASELINE_EVIDENCE_URL,
        }
    ]


@pytest.mark.parametrize(
    "field_name",
    (
        "handoff_id",
        "issue_identifier",
        "operation",
        "role_label",
        "delivery_kind",
        "started_at",
        "completed_at",
        "outcome",
        "attempt_cleanup_complete",
        "commit_by_repository_map",
        "pull_request_base_branch_by_url_map",
        "pull_request_base_commit_by_url_map",
        "pull_request_head_by_url_map",
        "verification_summary_list",
        "evidence_url_list",
        "local_phase_baseline_evidence_url",
        "schema_version",
    ),
)
def test_handoff_rejects_every_removed_broad_field(field_name: str) -> None:
    """No legacy metadata or compatibility field crosses the final boundary."""

    payload = _handoff().payload()
    payload[field_name] = True

    with pytest.raises(EvidenceContractError, match="another shape"):
        TaskHandoff.from_payload(payload)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("summary", [], "summary"),
        ("pull_request_candidate_list", {}, "candidates must be a list"),
        ("pull_request_candidate_list", None, "candidates must be a list"),
        ("pull_request_candidate_list", [{}], "candidate has another shape"),
        ("check_result_list", {}, "check results must be a list"),
        ("check_result_list", None, "check results must be a list"),
        ("check_result_list", [{}], "check result has another shape"),
    ),
)
def test_handoff_rejects_malformed_external_field_types(
    field_name: str,
    value: object,
    message: str,
) -> None:
    """Untrusted handoff shapes fail as typed contract errors, never raw exceptions."""

    payload = _handoff().payload()
    payload[field_name] = value

    with pytest.raises(EvidenceContractError, match=message):
        TaskHandoff.from_payload(payload)


def test_handoff_rejects_empty_or_repeated_outcome_collections() -> None:
    """Outcome collections are omitted when absent and nonempty when present."""

    with pytest.raises(EvidenceContractError, match="nonempty typed list"):
        _handoff(pull_request_candidate_list=[])
    with pytest.raises(EvidenceContractError, match="nonempty duplicate-free typed list"):
        _handoff(check_result_list=[])
    with pytest.raises(EvidenceContractError, match="duplicate-free typed list"):
        _handoff(check_result_list=[_check_result(), _check_result()])
    with pytest.raises(EvidenceContractError, match="duplicate-free typed list"):
        _handoff(check_result_list=[_check_result(), _check_result(result="195 passed")])
    with pytest.raises(EvidenceContractError, match="repeats one pull-request candidate repository"):
        _handoff(
            pull_request_candidate_list=[
                _pull_request_candidate(),
                _pull_request_candidate(url="https://github.com/antonov-andrey/example/pull/18"),
            ]
        )


def test_handoff_omits_every_unavailable_optional_value() -> None:
    """A failed or interrupted attempt can report only its concise summary."""

    handoff = TaskHandoff(summary="The provider operation failed before direct evidence became available.")

    assert handoff.payload() == {"summary": "The provider operation failed before direct evidence became available."}
    assert TaskHandoff.from_payload(handoff.payload()) == handoff


def test_handoff_rejects_null_placeholders_for_nested_optional_values() -> None:
    """Present candidate and result fields carry values instead of null placeholders."""

    payload = _handoff().payload()
    payload["pull_request_candidate_list"][0]["merged_commit"] = None  # type: ignore[index]
    with pytest.raises(EvidenceContractError, match="omit an unavailable merged commit"):
        TaskHandoff.from_payload(payload)

    payload = _handoff().payload()
    payload["check_result_list"][0]["evidence_url"] = None  # type: ignore[index]
    with pytest.raises(EvidenceContractError, match="omit an unavailable evidence URL"):
        TaskHandoff.from_payload(payload)


def test_handoff_keeps_merged_commit_on_its_exact_pr_candidate() -> None:
    """A merge result extends its composite candidate without another commit map."""

    merged = _handoff(
        summary="Merged the independently reviewed candidate.",
        pull_request_candidate_list=[_pull_request_candidate(merged_commit=COMMIT_TWO)],
    )

    assert merged.payload()["pull_request_candidate_list"] == [
        {
            "url": PULL_REQUEST_URL,
            "base_branch": "main",
            "base_commit": COMMIT_BASE,
            "head_commit": COMMIT_ONE,
            "merged_commit": COMMIT_TWO,
        }
    ]
    with pytest.raises(EvidenceContractError, match="merged commit"):
        _pull_request_candidate(merged_commit="main")
    with pytest.raises(EvidenceContractError, match="merged commit"):
        _pull_request_candidate(merged_commit=True)


def test_handoff_requires_one_complete_composite_pr_review_identity() -> None:
    """Base branch, exact base commit and head stay in one candidate object."""

    with pytest.raises(EvidenceContractError, match="base is not a full lowercase commit"):
        _pull_request_candidate(base_commit="main")
    with pytest.raises(EvidenceContractError, match="head is not a full lowercase commit"):
        _pull_request_candidate(head_commit="main")


@pytest.mark.parametrize(
    "url",
    (
        "https://github.com/antonov-andrey/example/pull/17?token=secret",
        "https://user:secret@github.com/antonov-andrey/example/pull/17",
        "https://github.com/antonov-andrey/example/issues/17",
    ),
)
def test_handoff_rejects_noncanonical_pull_request_url_without_secret_echo(url: str) -> None:
    """Unsafe PR identity is rejected without reflecting credential-bearing input."""

    with pytest.raises(EvidenceContractError, match="canonical GitHub PR") as captured:
        _pull_request_candidate(url=url)

    assert "secret" not in str(captured.value)


@pytest.mark.parametrize(
    "url",
    (
        "http://linear.app/acme/evidence",
        "https://linear.app:443/acme/evidence",
        "https://linear.app/acme/../evidence",
        "https://linear.app/acme/evidence?token=secret",
    ),
)
def test_direct_evidence_url_is_canonical_and_secret_free(url: str) -> None:
    """Direct evidence links use stable provider URLs without URL credentials or state."""

    with pytest.raises(EvidenceContractError, match="canonical HTTPS provider URL") as captured:
        evidence_url_validate(url)

    assert "secret" not in str(captured.value)


@pytest.mark.parametrize(
    "value",
    (
        "2026-08-04T12:30:00+00:00",
        "2026-08-04T12:30:00.1Z",
        "2026-08-04T12:30:00.0000000Z",
    ),
)
def test_instant_parser_rejects_lossy_or_noncanonical_utc(value: str) -> None:
    """Evidence instants have one stable seconds-or-microseconds representation."""

    with pytest.raises(EvidenceContractError, match="RFC 3339 UTC"):
        instant_parse(value, label="Evidence instant")


def test_evidence_cli_exposes_only_handoff_and_direct_baselines(tmp_path: Path) -> None:
    """The owner CLI has no candidate, reusable-receipt or invalidation operation."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"
    help_result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "handoff" in help_result.stdout
    assert "workspace-baseline" in help_result.stdout
    assert "candidate" not in help_result.stdout
    assert "receipt" not in help_result.stdout

    input_path = tmp_path / "handoff.json"
    input_path.write_text(json.dumps(_handoff().payload()), encoding="utf-8")
    rendered = subprocess.run(
        [sys.executable, str(script), "handoff", "--input", str(input_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert TaskHandoff.from_payload(HANDOFF_COMMENT_CODEC.payload_parse(rendered)) == _handoff()


def test_evidence_cli_rejects_prior_candidate_shape(tmp_path: Path) -> None:
    """A legacy approval payload cannot cross the semantic handoff boundary."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"
    input_path = tmp_path / "legacy.json"
    payload = _handoff().payload()
    payload["candidate_fingerprint"] = "f" * 64
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), "handoff", "--input", str(input_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "another shape" in result.stderr
    assert result.stdout == ""


def test_local_phase_baseline_uses_exact_provider_history_without_candidate_identity() -> None:
    """All five phases round-trip without a synthetic approval fingerprint."""

    baseline = LocalPhaseBaseline(
        project_id="22222222-2222-4222-8222-222222222222",
        measured_at=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
        duration_seconds_by_phase_map={
            "queue": 1.0,
            "startup": 2.0,
            "execution": 3.0,
            "review": 4.0,
            "merge": 5.0,
        },
        evidence_url=ISSUE_EVIDENCE_URL,
    )
    rendered = LOCAL_PHASE_BASELINE_COMMENT_CODEC.render(baseline.payload())

    assert LocalPhaseBaseline.from_payload(LOCAL_PHASE_BASELINE_COMMENT_CODEC.payload_parse(rendered)) == baseline
    assert "candidate" not in rendered
    with pytest.raises(EvidenceContractError, match="queue, startup, execution, review and merge"):
        replace(baseline, duration_seconds_by_phase_map={"queue": 1.0})


def test_task_workspace_baseline_uses_natural_issue_and_repository_identities() -> None:
    """First dispatch retains only its issue and canonical repository base commits."""

    baseline = TaskWorkspaceBaseline(
        issue_identifier="AND-17",
        baseline_commit_by_repository_identity_map={
            "github.com/antonov-andrey/example": COMMIT_ONE,
            "github.com/antonov-andrey/other": COMMIT_TWO,
        },
    )
    rendered = TASK_WORKSPACE_BASELINE_COMMENT_CODEC.render(baseline.payload())

    assert TaskWorkspaceBaseline.from_payload(TASK_WORKSPACE_BASELINE_COMMENT_CODEC.payload_parse(rendered)) == baseline
    with pytest.raises(EvidenceContractError, match="not canonical"):
        replace(
            baseline,
            baseline_commit_by_repository_identity_map={
                "git@github.com:antonov-andrey/example.git": COMMIT_ONE,
            },
        )


class _GhRunner:
    """Return deterministic GitHub/Git responses and model remote ref mutation."""

    def __init__(
        self,
        *,
        base_commit: str = COMMIT_BASE,
        head_commit: str = COMMIT_ONE,
        base_branch: str = "main",
        protection_kind: str = "classic",
        required_check_name_list: list[str] | None = None,
    ) -> None:
        self.base_commit = base_commit
        self.head_commit = head_commit
        self.base_branch = base_branch
        self.head_repository = "antonov-andrey/example"
        self.cross_repository = False
        self.auto_merge_request: dict[str, object] | None = None
        self.pr_title = "AND-17 Implement exact owner"
        self.protection_kind = protection_kind
        self.required_check_name_list = [] if required_check_name_list is None else required_check_name_list
        self.strict_required_status_checks = True
        self.execution_login = "octocat"
        self.execution_user_id = 7
        self.execution_node_id = "U_octocat"
        self.changed_execution_login: str | None = None
        self.changed_execution_user_id: int | None = None
        self.changed_execution_node_id: str | None = None
        self.changed_execution_permission: str | None = None
        self.execution_identity_change_after_read_count = 1
        self.execution_identity_read_count = 0
        self.execution_permission_change_after_read_count = 1
        self.execution_permission_read_count = 0
        self.enforce_admins = True
        self.allow_force_pushes = False
        self.allow_deletions = False
        self.required_pull_request_reviews: dict[str, object] | None = None
        self.required_linear_history = False
        self.required_signatures = False
        self.required_conversation_resolution = False
        self.branch_locked = False
        self.push_restrictions: dict[str, object] | None = None
        self.block_creations = False
        self.allow_fork_syncing = False
        self.classic_extra_field_by_name_map: dict[str, object] = {}
        self.execution_permission = "admin"
        self.ruleset_enforcement = "active"
        self.ruleset_merge_queue = False
        self.ruleset_additional_rule_type_list: list[str] = []
        self.ruleset_required_check_extra_parameter_by_name_map: dict[str, object] = {}
        self.ruleset_bypass_actor_list: list[dict[str, object]] = []
        self.check_bucket = "pass"
        self.check_returncode: int | None = None
        self.check_stdout: str | None = None
        self.status_rollup_returncode = 0
        self.status_rollup_stdout: str | None = None
        self.state = "OPEN"
        self.defer_merge_readback = False
        self.pr_exists = False
        self.historical_closed_number_list: list[int] = []
        self.pull_request_state_by_number_map: dict[int, str] = {}
        self.pull_request_title_by_number_map: dict[int, str] = {}
        self.advance_base_on_push = False
        self.advance_head_on_push = False
        self.operation_mutation_count = 0
        self.http_proactive_authentication_supported = True
        self.http_proactive_authentication_failure_probe_number: int | None = None
        self.http_proactive_authentication_probe_count = 0
        self.fetch_url_list = ["git@github.com:antonov-andrey/example.git"]
        self.push_url_list = ["git@github.com:antonov-andrey/example.git"]
        self.explicit_fetch_url_list = ["https://github.com/antonov-andrey/example.git"]
        self.explicit_push_url_list = ["https://github.com/antonov-andrey/example.git"]
        self.local_config_name_list = [
            "core.repositoryformatversion",
            "remote.origin.url",
            "remote.origin.fetch",
        ]
        self.pre_push_hook = False
        self.alternate_object_store = False
        self.replace_ref_list: list[str] = []
        self.shallow_repository = False
        self.reviewed_base_is_ancestor = True
        self.merge_base_commit = base_commit
        self.merge_head_commit = head_commit
        self.merge_commit = COMMIT_TWO
        self.constructed_merge_tree = "f" * 40
        self.merge_commit_tree = "f" * 40
        self.merged_by_login = "octocat"
        self.merged_by_user_id = 7
        self.merged_by_node_id = "U_octocat"
        self.credential_login = "octocat"
        self.credential_user_id = 7
        self.credential_node_id = "U_octocat"
        self.repository_policy_read_count = 0
        self.repository_policy_read_returncode = 0
        self.repository_policy_read_stdout_override: str | None = None
        self.repository_policy_payload_override: object | None = None
        self.repository_policy_payload_by_read_count_map: dict[int, object] = {}
        self.repository_policy_field_by_name_map: dict[str, object] = {}
        self.repository_policy_drift_field_by_name_map: dict[str, object] = {}
        self.repository_policy_mutation_count = 0
        self.repository_policy_mutation_returncode = 0
        self.repository_policy_mutation_stdout_override: str | None = None
        self.repository_policy_mutation_payload_override: object | None = None
        self.remote_commit_by_ref_map = {
            f"refs/heads/{base_branch}": base_commit,
            "refs/heads/linear/and-17": head_commit,
        }
        self.command_list: list[list[str]] = []
        self.environment_list: list[dict[str, str]] = []

    def __call__(
        self,
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Return the provider response for one expected gh command."""

        argument_list = list(argument_list)
        self.command_list.append(argument_list)
        self.environment_list.append(dict(environment_by_name_map))
        if argument_list[0] == "/bin/sh":
            assert input_text == "protocol=https\nhost=github.com\npath=antonov-andrey/example.git\n\n"
            expected_identity = (self.execution_login, self.execution_user_id, self.execution_node_id)
            credential_identity = (self.credential_login, self.credential_user_id, self.credential_node_id)
            return subprocess.CompletedProcess(
                argument_list,
                0 if credential_identity == expected_identity else 1,
                "",
                "",
            )
        if argument_list[0] == "git":
            return self._git_call(
                argument_list,
                environment_by_name_map=environment_by_name_map,
                input_text=input_text,
            )
        assert input_text is None
        if argument_list[1:3] == ["pr", "checks"]:
            payload = [
                {"name": name, "bucket": self.check_bucket, "link": "https://example.test/check"}
                for name in self.required_check_name_list
            ]
            return subprocess.CompletedProcess(
                argument_list,
                (
                    self.check_returncode
                    if self.check_returncode is not None
                    else (8 if self.check_bucket == "pending" else 0)
                ),
                self.check_stdout if self.check_stdout is not None else json.dumps(payload),
                "",
            )
        if argument_list[1:5] == ["api", "--hostname", "github.com", "user"]:
            self.execution_identity_read_count += 1
            changed = (
                self.execution_identity_read_count > self.execution_identity_change_after_read_count
                and self.changed_execution_login is not None
            )
            payload = {
                "login": self.changed_execution_login if changed else self.execution_login,
                "id": self.changed_execution_user_id if changed else self.execution_user_id,
                "node_id": self.changed_execution_node_id if changed else self.execution_node_id,
            }
            return subprocess.CompletedProcess(argument_list, 0, json.dumps(payload), "")
        if argument_list[1:] == [
            "api",
            "--hostname",
            "github.com",
            "--method",
            "PATCH",
            "repos/antonov-andrey/example",
            "-F",
            "delete_branch_on_merge=false",
        ]:
            self.repository_policy_mutation_count += 1
            if self.repository_policy_mutation_returncode == 0:
                self.repository_policy_field_by_name_map["delete_branch_on_merge"] = False
            payload = (
                self.repository_policy_mutation_payload_override
                if self.repository_policy_mutation_payload_override is not None
                else self._repository_policy_payload_get()
            )
            return subprocess.CompletedProcess(
                argument_list,
                self.repository_policy_mutation_returncode,
                (
                    self.repository_policy_mutation_stdout_override
                    if self.repository_policy_mutation_stdout_override is not None
                    else json.dumps(payload)
                ),
                "",
            )
        if argument_list[1:5] == ["api", "--hostname", "github.com", "repos/antonov-andrey/example"]:
            self.repository_policy_read_count += 1
            if self.repository_policy_read_count in self.repository_policy_payload_by_read_count_map:
                payload = self.repository_policy_payload_by_read_count_map[self.repository_policy_read_count]
            elif self.repository_policy_payload_override is not None:
                payload = self.repository_policy_payload_override
            else:
                payload = self._repository_policy_payload_get()
                if self.repository_policy_read_count > 1:
                    payload.update(self.repository_policy_drift_field_by_name_map)
            return subprocess.CompletedProcess(
                argument_list,
                self.repository_policy_read_returncode,
                (
                    self.repository_policy_read_stdout_override
                    if self.repository_policy_read_stdout_override is not None
                    else json.dumps(payload)
                ),
                "",
            )
        if (
            argument_list[1:4] == ["api", "--hostname", "github.com"]
            and argument_list[4] == "repos/antonov-andrey/example/pulls/17"
        ):
            return subprocess.CompletedProcess(
                argument_list,
                0,
                json.dumps(
                    {
                        "login": self.merged_by_login,
                        "user_id": self.merged_by_user_id,
                        "node_id": self.merged_by_node_id,
                    }
                ),
                "",
            )
        if argument_list[1:3] == ["api", "--include"]:
            if self.protection_kind == "classic":
                payload = {
                    "url": (
                        "https://api.github.com/repos/antonov-andrey/example/branches/" f"{self.base_branch}/protection"
                    ),
                    "enforce_admins": {
                        "enabled": self.enforce_admins,
                        "url": "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/enforce_admins",
                    },
                    "allow_force_pushes": {"enabled": self.allow_force_pushes},
                    "allow_deletions": {"enabled": self.allow_deletions},
                    "required_linear_history": {"enabled": self.required_linear_history},
                    "required_signatures": {
                        "enabled": self.required_signatures,
                        "url": "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/required_signatures",
                    },
                    "required_conversation_resolution": {"enabled": self.required_conversation_resolution},
                    "lock_branch": {"enabled": self.branch_locked},
                    "block_creations": {"enabled": self.block_creations},
                    "allow_fork_syncing": {"enabled": self.allow_fork_syncing},
                    **self.classic_extra_field_by_name_map,
                }
                if self.required_pull_request_reviews is not None:
                    payload["required_pull_request_reviews"] = self.required_pull_request_reviews
                if self.push_restrictions is not None:
                    payload["restrictions"] = self.push_restrictions
                if self.required_check_name_list:
                    payload["required_status_checks"] = {
                        "url": "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/required_status_checks",
                        "contexts_url": (
                            "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/"
                            "required_status_checks/contexts"
                        ),
                        "strict": self.strict_required_status_checks,
                        "contexts": list(self.required_check_name_list),
                        "checks": [],
                    }
                return _included_response(argument_list, status=200, payload=payload)
            return _included_response(
                argument_list,
                status=404,
                payload={"message": "Branch not protected", "status": "404"},
            )
        if argument_list[1] == "api" and "/collaborators/" in argument_list[-1]:
            self.execution_permission_read_count += 1
            identity_changed = (
                self.execution_identity_read_count > self.execution_identity_change_after_read_count
                and self.changed_execution_login is not None
            )
            permission_changed = (
                self.execution_permission_read_count > self.execution_permission_change_after_read_count
                and self.changed_execution_permission is not None
            )
            return subprocess.CompletedProcess(
                argument_list,
                0,
                json.dumps(
                    {
                        "permission": (
                            self.changed_execution_permission if permission_changed else self.execution_permission
                        ),
                        "user": {
                            "login": self.changed_execution_login if identity_changed else self.execution_login,
                            "id": self.changed_execution_user_id if identity_changed else self.execution_user_id,
                            "node_id": self.changed_execution_node_id if identity_changed else self.execution_node_id,
                        },
                    }
                ),
                "",
            )
        if argument_list[1:4] == ["api", "--method", "GET"] and "/rules/branches/" in argument_list[-3]:
            if self.protection_kind != "ruleset":
                return subprocess.CompletedProcess(argument_list, 0, "[[]]", "")
            rule_list = [
                {
                    **definition,
                    "ruleset_source_type": "Repository",
                    "ruleset_source": "antonov-andrey/example",
                    "ruleset_id": 42,
                }
                for definition in self._ruleset_definition_list()
            ]
            if self.ruleset_merge_queue:
                rule_list.append(
                    {
                        "type": "merge_queue",
                        "ruleset_source_type": "Repository",
                        "ruleset_source": "antonov-andrey/example",
                        "ruleset_id": 42,
                        "parameters": {"merge_method": "SQUASH"},
                    }
                )
            return subprocess.CompletedProcess(argument_list, 0, json.dumps([rule_list]), "")
        if argument_list[1] == "api" and "/rulesets/42?" in argument_list[-1]:
            rule_list = self._ruleset_definition_list()
            if self.ruleset_merge_queue:
                rule_list.append({"type": "merge_queue", "parameters": {"merge_method": "SQUASH"}})
            return subprocess.CompletedProcess(
                argument_list,
                0,
                json.dumps(
                    {
                        "id": 42,
                        "target": "branch",
                        "source_type": "Repository",
                        "source": "antonov-andrey/example",
                        "enforcement": self.ruleset_enforcement,
                        "bypass_actors": self.ruleset_bypass_actor_list,
                        "rules": rule_list,
                    }
                ),
                "",
            )
        if argument_list[1:4] == ["api", "--method", "PUT"] and argument_list[4].endswith("/protection"):
            self.protection_kind = "classic"
            self.required_check_name_list = []
            self.enforce_admins = True
            self.allow_force_pushes = False
            self.allow_deletions = False
            return subprocess.CompletedProcess(
                argument_list,
                0,
                json.dumps(
                    {
                        "enforce_admins": {"enabled": True},
                        "allow_force_pushes": {"enabled": False},
                        "allow_deletions": {"enabled": False},
                        "required_status_checks": None,
                    }
                ),
                "",
            )
        if argument_list[1:3] == ["api", "--method"] and any("/pulls" in item for item in argument_list):
            number_list = [*self.historical_closed_number_list, *self.pull_request_state_by_number_map]
            if self.pr_exists:
                number_list.append(17)
            payload = [
                [
                    {
                        "number": number,
                        "base": {"ref": self.base_branch},
                        "head": {"ref": "linear/and-17"},
                    }
                    for number in sorted(set(number_list))
                ]
            ]
            return subprocess.CompletedProcess(argument_list, 0, json.dumps(payload), "")
        if argument_list[1:3] == ["pr", "create"]:
            self.pr_exists = True
            return subprocess.CompletedProcess(
                argument_list,
                0,
                PULL_REQUEST_URL + "\n",
                "",
            )
        if argument_list[1:3] == ["pr", "merge"]:
            self.state = "MERGED"
            return subprocess.CompletedProcess(argument_list, 0, "", "")
        if argument_list[1:3] == ["pr", "close"]:
            self.state = "CLOSED"
            return subprocess.CompletedProcess(argument_list, 0, "", "")
        if argument_list[1:3] == ["pr", "view"]:
            if argument_list[-1] == "statusCheckRollup":
                return subprocess.CompletedProcess(
                    argument_list,
                    self.status_rollup_returncode,
                    (
                        self.status_rollup_stdout
                        if self.status_rollup_stdout is not None
                        else json.dumps({"statusCheckRollup": []})
                    ),
                    "",
                )
            number = int(argument_list[3])
            state = self.pull_request_state_by_number_map.get(
                number,
                "CLOSED" if number in self.historical_closed_number_list else self.state,
            )
            payload = {
                "number": number,
                "url": f"https://github.com/antonov-andrey/example/pull/{number}",
                "title": self.pull_request_title_by_number_map.get(number, self.pr_title),
                "state": state,
                "isDraft": False,
                "autoMergeRequest": self.auto_merge_request,
                "baseRefName": self.base_branch,
                "baseRefOid": self.base_commit,
                "headRefName": "linear/and-17",
                "headRefOid": self.head_commit,
                "headRepository": {
                    "id": "R_example",
                    "name": "example",
                    "nameWithOwner": self.head_repository,
                },
                "headRepositoryOwner": {"id": "U_octocat", "name": "Octocat", "login": "octocat"},
                "isCrossRepository": self.cross_repository,
                "mergeStateStatus": "CLEAN",
                "mergedAt": "2026-08-04T12:30:00Z" if state == "MERGED" else None,
                "mergeCommit": {"oid": self.merge_commit} if state == "MERGED" else None,
                "mergedBy": (
                    {
                        "id": self.merged_by_node_id,
                        "login": self.merged_by_login,
                        "name": "Octocat",
                        "is_bot": False,
                    }
                    if state == "MERGED"
                    else None
                ),
            }
            return subprocess.CompletedProcess(argument_list, 0, json.dumps(payload), "")
        raise AssertionError(f"Unexpected gh command: {argument_list}")

    def _ruleset_definition_list(self) -> list[dict[str, object]]:
        """Return the exact full ruleset definitions used by both provider reads."""

        rule_list: list[dict[str, object]] = [{"type": "non_fast_forward"}, {"type": "deletion"}]
        if self.required_check_name_list:
            rule_list.append(
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": self.strict_required_status_checks,
                        **self.ruleset_required_check_extra_parameter_by_name_map,
                        "required_status_checks": [
                            {"context": name, "integration_id": None} for name in self.required_check_name_list
                        ],
                    },
                }
            )
        rule_list.extend({"type": rule_type} for rule_type in self.ruleset_additional_rule_type_list)
        return rule_list

    def _repository_policy_payload_get(self) -> dict[str, object]:
        """Return one complete strict repository-policy response."""

        payload: dict[str, object] = {
            "id": 123,
            "node_id": "R_example",
            "name": "example",
            "full_name": "antonov-andrey/example",
            "owner": {
                "login": "antonov-andrey",
                "id": 13316422,
                "node_id": "U_owner",
                "type": "User",
                "site_admin": False,
            },
            "private": False,
            "fork": False,
            "archived": False,
            "disabled": False,
            "visibility": "public",
            "default_branch": "main",
            "mirror_url": None,
            "allow_forking": True,
            "is_template": False,
            "web_commit_signoff_required": False,
            "has_discussions": False,
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": True,
            "allow_auto_merge": False,
            "delete_branch_on_merge": False,
            "use_squash_pr_title_as_default": False,
            "squash_merge_commit_title": "COMMIT_OR_PR_TITLE",
            "squash_merge_commit_message": "COMMIT_MESSAGES",
            "merge_commit_title": "MERGE_MESSAGE",
            "merge_commit_message": "PR_TITLE",
            "allow_update_branch": False,
        }
        payload.update(self.repository_policy_field_by_name_map)
        return payload

    def _git_call(
        self,
        argument_list: list[str],
        *,
        environment_by_name_map: Mapping[str, str],
        input_text: str | None,
    ) -> subprocess.CompletedProcess[str]:
        """Model local object creation and one atomic remote ref transaction."""

        assert environment_by_name_map["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert environment_by_name_map["GIT_CONFIG_SYSTEM"] == "/dev/null"
        assert "GIT_CONFIG" not in environment_by_name_map
        probe_url = next(
            (
                argument
                for argument in argument_list
                if argument.startswith("http://127.0.0.1:")
                and argument.endswith("/linear-agent-proactive-authentication-probe.git")
            ),
            None,
        )
        if probe_url is not None:
            self.http_proactive_authentication_probe_count += 1
            parsed = urlsplit(probe_url)
            connection = HTTPConnection(parsed.hostname, parsed.port)
            header_by_name_map = {}
            if self.http_proactive_authentication_supported and (
                self.http_proactive_authentication_failure_probe_number
                != self.http_proactive_authentication_probe_count
            ):
                helper_argument = next(
                    argument for argument in argument_list if argument.startswith("credential.helper=!f()")
                )
                probe_password = helper_argument.split("'password=", 1)[1].split("'", 1)[0]
                credential = b64encode(
                    f"linear-agent-proactive-authentication-probe:{probe_password}".encode("utf-8")
                ).decode("ascii")
                header_by_name_map["Authorization"] = f"Basic {credential}"
            connection.request(
                "GET",
                f"{parsed.path}/info/refs?service=git-upload-pack",
                headers=header_by_name_map,
            )
            response = connection.getresponse()
            response.read()
            connection.close()
            return subprocess.CompletedProcess(argument_list, 128, "", "probe rejected")
        if "init" in argument_list:
            assert input_text is None
            private_git_dir = Path(argument_list[-1])
            (private_git_dir / "hooks").mkdir(parents=True)
            (private_git_dir / "objects" / "info").mkdir(parents=True)
            (private_git_dir / "info").mkdir(parents=True)
            (private_git_dir / "refs").mkdir(parents=True)
            (private_git_dir / "config").write_text("[core]\n\tbare = true\n", encoding="utf-8")
            return subprocess.CompletedProcess(argument_list, 0, "", "")
        if argument_list[:2] == ["git", "config"]:
            assert input_text is not None
            assert argument_list[2:4] == ["--file", "-"]
            if "--name-only" in argument_list:
                return subprocess.CompletedProcess(
                    argument_list,
                    0,
                    "".join(f"{name}\x00" for name in self.local_config_name_list),
                    "",
                )
            if argument_list[-1] == "remote.origin.url":
                value_list = self.fetch_url_list
            elif argument_list[-1] == "remote.origin.pushurl":
                value_list = self.push_url_list if self.push_url_list != self.fetch_url_list else []
            else:
                raise AssertionError(f"Unexpected config value read: {argument_list}")
            return subprocess.CompletedProcess(
                argument_list,
                0 if value_list else 1,
                "".join(f"{value}\x00" for value in value_list),
                "",
            )
        assert input_text is None
        if "fetch" in argument_list:
            return subprocess.CompletedProcess(argument_list, 0, "", "")
        if "merge-base" in argument_list:
            return subprocess.CompletedProcess(argument_list, 0 if self.reviewed_base_is_ancestor else 1, "", "")
        if "rev-parse" in argument_list:
            revision = argument_list[-1]
            if revision.endswith("^{tree}"):
                return subprocess.CompletedProcess(argument_list, 0, self.constructed_merge_tree + "\n", "")
            if revision == "refs/provider/reviewed-base^{commit}":
                commit = self.base_commit
            elif revision == "refs/provider/reviewed-head^{commit}":
                commit = self.head_commit
            else:
                commit = revision.removesuffix("^{commit}")
            return subprocess.CompletedProcess(argument_list, 0, commit + "\n", "")
        if "cat-file" in argument_list and "-p" in argument_list:
            payload = (
                f"tree {self.merge_commit_tree}\n"
                f"parent {self.merge_base_commit}\n"
                f"parent {self.merge_head_commit}\n"
                "author Octocat <octocat@users.noreply.github.com> 0 +0000\n"
                "committer Octocat <octocat@users.noreply.github.com> 0 +0000\n\n"
                "Merge reviewed pull request\n"
            )
            return subprocess.CompletedProcess(argument_list, 0, payload, "")
        if "commit-tree" in argument_list:
            return subprocess.CompletedProcess(argument_list, 0, self.merge_commit + "\n", "")
        if "push" in argument_list:
            if self.advance_base_on_push:
                self.remote_commit_by_ref_map[f"refs/heads/{self.base_branch}"] = "d" * 40
                self.advance_base_on_push = False
            if self.advance_head_on_push:
                self.remote_commit_by_ref_map["refs/heads/linear/and-17"] = "e" * 40
                self.advance_head_on_push = False
            expected_by_ref_map: dict[str, str] = {}
            for argument in argument_list:
                if argument.startswith("--force-with-lease="):
                    ref_name, expected = argument.removeprefix("--force-with-lease=").split(":", 1)
                    expected_by_ref_map[ref_name] = expected
            if any(
                self.remote_commit_by_ref_map.get(ref_name) != expected
                for ref_name, expected in expected_by_ref_map.items()
            ):
                return subprocess.CompletedProcess(argument_list, 1, "", "stale info")
            assert "--atomic" in argument_list
            base_ref = f"refs/heads/{self.base_branch}"
            self.remote_commit_by_ref_map[base_ref] = self.merge_commit
            self.operation_mutation_count += 1
            if not self.defer_merge_readback:
                self.state = "MERGED"
            return subprocess.CompletedProcess(argument_list, 0, "", "")
        if "ls-remote" in argument_list:
            ls_remote_index = argument_list.index("ls-remote")
            remote_index = (
                ls_remote_index + 2 if argument_list[ls_remote_index + 1] == "--refs" else ls_remote_index + 1
            )
            requested_ref_set = set(argument_list[remote_index + 1 :])
            output = "".join(
                f"{commit}\t{ref_name}\n"
                for ref_name, commit in sorted(self.remote_commit_by_ref_map.items())
                if ref_name in requested_ref_set
            )
            return subprocess.CompletedProcess(argument_list, 0, output, "")
        raise AssertionError(f"Unexpected git command: {argument_list}")


def _included_response(
    argument_list: list[str],
    *,
    status: int,
    payload: object,
) -> subprocess.CompletedProcess[str]:
    """Return one deterministic ``gh api --include`` response."""

    reason = "OK" if status == 200 else "Not Found"
    stdout = f"HTTP/2.0 {status} {reason}\r\nContent-Type: application/json\r\n\r\n{json.dumps(payload)}"
    return subprocess.CompletedProcess(argument_list, 0 if status == 200 else 1, stdout, "")


def _workflow_configuration_module_get() -> ModuleType:
    """Load the workflow-configure GitHub transaction script."""

    script = PLUGIN_ROOT / "skills" / "workflow-configure" / "scripts" / "branch_protection.py"
    spec = importlib.util.spec_from_file_location("linear_workflow_github_configuration", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configuration_snapshot_get(
    runner: _GhRunner,
) -> tuple[BranchProtectionSnapshot, GitHubRepositoryMergePolicy]:
    """Read one matching protection and correctable repository-policy pair."""

    protection = GitHubBranchProtectionBoundary(runner).inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        base_branch="main",
    )
    principal = GitHubPrincipal(
        login=protection.execution_login,
        user_id=protection.execution_user_id,
        node_id=protection.execution_node_id,
    )
    repository_policy = GitHubRepositoryMergePolicyBoundary(runner).configuration_inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        principal=principal,
        merge_method="merge",
    )
    return protection, repository_policy


def _workflow_configuration_module_bind(
    runner: _GhRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    """Load workflow-configure with both GitHub boundaries on one provider."""

    module = _workflow_configuration_module_get()
    monkeypatch.setattr(module, "GitHubBranchProtectionBoundary", lambda: GitHubBranchProtectionBoundary(runner))
    monkeypatch.setattr(
        module,
        "GitHubRepositoryMergePolicyBoundary",
        lambda: GitHubRepositoryMergePolicyBoundary(runner),
    )
    return module


def _workflow_configuration_plan_write(
    *,
    module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> tuple[dict[str, object], Path]:
    """Run and persist the exact displayed plan for the apply transaction."""

    assert (
        module.main(
            [
                "plan",
                "--repository",
                "antonov-andrey/example",
                "--base-branch",
                "main",
                "--merge-method",
                "merge",
            ]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    plan_path = tmp_path / "github-configuration-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan, plan_path


def _workflow_configuration_apply_argument_list(plan_path: Path) -> list[str]:
    """Return exact workflow-configure apply arguments for one approved plan."""

    return [
        "apply",
        "--repository",
        "antonov-andrey/example",
        "--base-branch",
        "main",
        "--merge-method",
        "merge",
        "--approved-plan-input",
        str(plan_path),
    ]


@dataclass(frozen=True, slots=True)
class _RealGitHistory:
    """Carry one local remote and exact commits for real-subprocess tests."""

    remote_path: Path
    task_path: Path
    base_commit: str
    head_commit: str
    head_tree: str
    merge_commit: str


class _RealGitBoundaryRunner:
    """Run Git as a real subprocess while keeping GitHub reads deterministic."""

    def __init__(
        self,
        *,
        provider: _GhRunner,
        remote_path: Path,
        isolated_home: Path,
        mutate_task_config_path: Path | None = None,
        inject_private_attributes: bool = False,
        adopt_successful_push: bool = False,
    ) -> None:
        self.provider = provider
        self.remote_path = remote_path
        self.isolated_home = isolated_home
        self.mutate_task_config_path = mutate_task_config_path
        self.inject_private_attributes = inject_private_attributes
        self.adopt_successful_push = adopt_successful_push
        self.git_command_list: list[list[str]] = []
        self.git_push_count = 0
        self.task_config_mutated = False

    def __call__(
        self,
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Delegate Git to the executable and GitHub/token reads to the fake provider."""

        argument_list = list(argument_list)
        if argument_list[0] != "git":
            return self.provider(
                argument_list,
                environment_by_name_map=environment_by_name_map,
                input_text=input_text,
            )
        if any("/linear-agent-proactive-authentication-probe.git" in argument for argument in argument_list):
            return self.provider(
                argument_list,
                environment_by_name_map=environment_by_name_map,
                input_text=input_text,
            )
        assert environment_by_name_map["HOME"] == "/home/andrey"
        assert environment_by_name_map["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert environment_by_name_map["GIT_CONFIG_SYSTEM"] == "/dev/null"
        assert environment_by_name_map["GIT_ATTR_NOSYSTEM"] == "1"
        assert "GIT_CONFIG" not in environment_by_name_map
        assert "CODEX_HOME" not in environment_by_name_map
        transformed_argument_list = [
            (
                str(self.remote_path)
                if argument == "https://github.com/antonov-andrey/example.git"
                else "protocol.file.allow=always" if argument == "protocol.https.allow=always" else argument
            )
            for argument in argument_list
        ]
        self.git_command_list.append(list(transformed_argument_list))
        if "push" in transformed_argument_list:
            self.git_push_count += 1
        child_environment = dict(environment_by_name_map)
        child_environment["HOME"] = str(self.isolated_home)
        completed_process = command_run(
            transformed_argument_list,
            environment_by_name_map=child_environment,
            input_text=input_text,
        )
        if "push" in transformed_argument_list and completed_process.returncode == 0 and self.adopt_successful_push:
            base_refspec = next(
                argument
                for argument in transformed_argument_list
                if argument.endswith(":refs/heads/main") and not argument.startswith("--force-with-lease=")
            )
            self.provider.merge_commit = base_refspec.split(":", 1)[0]
            self.provider.base_commit = self.provider.merge_commit
            self.provider.state = "MERGED"
        if "init" in transformed_argument_list and completed_process.returncode == 0:
            private_git_dir = Path(transformed_argument_list[-1])
            if self.mutate_task_config_path is not None:
                self.mutate_task_config_path.write_text(
                    "[include]\n\tpath = /tmp/attacker-git-config\n"
                    '[url "https://attacker.invalid/"]\n\tinsteadOf = https://github.com/\n'
                    '[http "https://github.com/"]\n\textraHeader = Authorization: bearer hidden\n'
                    '[merge "attacker"]\n\tdriver = /bin/false\n',
                    encoding="utf-8",
                )
                self.task_config_mutated = True
            if self.inject_private_attributes:
                (private_git_dir / "info").mkdir(exist_ok=True)
                (private_git_dir / "info" / "attributes").write_text(
                    "*.txt merge=attacker\n",
                    encoding="utf-8",
                )
        return completed_process


def _real_git_environment(home: Path) -> dict[str, str]:
    """Return a deterministic isolated environment for test repository construction."""

    return {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }


def test_command_run_resolves_semantic_git_to_the_owned_absolute_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The closed path cannot select an older host Git for a semantic Git command."""

    runtime = GitTransportRuntime(
        root=tmp_path / "runtime",
        executable=tmp_path / "runtime" / "usr" / "bin" / "git",
        exec_path=tmp_path / "runtime" / "usr" / "lib" / "git-core",
    )
    captured_argument_list: list[str] = []

    def fake_run(
        argument_list: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        env: Mapping[str, str],
        input: str | None,
    ) -> subprocess.CompletedProcess[str]:
        """Capture the final direct argument vector without executing it."""

        assert check is False
        assert capture_output is True
        assert text is True
        assert env["PATH"] == "/usr/bin:/bin"
        assert input is None
        captured_argument_list.extend(argument_list)
        return subprocess.CompletedProcess(argument_list, 0, stdout="", stderr="")

    monkeypatch.setattr(git_host_command, "git_transport_runtime_get", lambda: runtime)
    monkeypatch.setattr(git_host_command.subprocess, "run", fake_run)

    completed_process = git_host_command.command_run(
        ["git", "ls-remote", "https://github.com/antonov-andrey/example.git"],
        environment_by_name_map={"PATH": "/usr/bin:/bin"},
    )

    assert completed_process.returncode == 0
    assert captured_argument_list == [
        str(runtime.executable),
        f"--exec-path={runtime.exec_path}",
        "ls-remote",
        "https://github.com/antonov-andrey/example.git",
    ]


def _real_git_checked(argument_list: list[str], *, home: Path, input_text: str | None = None) -> str:
    """Run one successful real Git subprocess for a test fixture."""

    completed_process = command_run(
        argument_list,
        environment_by_name_map=_real_git_environment(home),
        input_text=input_text,
    )
    assert completed_process.returncode == 0, completed_process.stderr
    return completed_process.stdout.strip()


def _real_git_history_create(
    root: Path,
    *,
    divergent: bool,
    altered_merge: bool,
) -> _RealGitHistory:
    """Create exact local refs and objects used through the production Git boundary."""

    root.mkdir()
    home = root / "fixture-home"
    home.mkdir()
    source_path = root / "source"
    task_path = root / "task"
    remote_path = root / "remote.git"
    source_path.mkdir()
    task_path.mkdir()
    _real_git_checked(["git", "init", "--initial-branch=main", str(source_path)], home=home)
    (source_path / "shared.txt").write_text("root\n", encoding="utf-8")
    _real_git_checked(["git", "-C", str(source_path), "add", "shared.txt"], home=home)
    _real_git_checked(
        [
            "git",
            "-C",
            str(source_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-m",
            "root",
        ],
        home=home,
    )
    root_commit = _real_git_checked(["git", "-C", str(source_path), "rev-parse", "HEAD"], home=home)
    if divergent:
        (source_path / "base.txt").write_text("base\n", encoding="utf-8")
        _real_git_checked(["git", "-C", str(source_path), "add", "base.txt"], home=home)
        _real_git_checked(
            [
                "git",
                "-C",
                str(source_path),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-m",
                "base",
            ],
            home=home,
        )
        base_commit = _real_git_checked(["git", "-C", str(source_path), "rev-parse", "HEAD"], home=home)
        _real_git_checked(
            ["git", "-C", str(source_path), "checkout", "-b", "linear/and-17", root_commit],
            home=home,
        )
    else:
        base_commit = root_commit
        _real_git_checked(["git", "-C", str(source_path), "checkout", "-b", "linear/and-17"], home=home)
    (source_path / "head.txt").write_text("head\n", encoding="utf-8")
    _real_git_checked(["git", "-C", str(source_path), "add", "head.txt"], home=home)
    _real_git_checked(
        [
            "git",
            "-C",
            str(source_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-m",
            "head",
        ],
        home=home,
    )
    head_commit = _real_git_checked(["git", "-C", str(source_path), "rev-parse", "HEAD"], home=home)
    head_tree = _real_git_checked(["git", "-C", str(source_path), "rev-parse", f"{head_commit}^{{tree}}"], home=home)
    merge_commit = ""
    if altered_merge:
        base_tree = _real_git_checked(
            ["git", "-C", str(source_path), "rev-parse", f"{base_commit}^{{tree}}"],
            home=home,
        )
        merge_commit = _real_git_checked(
            [
                "git",
                "-C",
                str(source_path),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit-tree",
                base_tree,
                "-p",
                base_commit,
                "-p",
                head_commit,
                "-m",
                "malicious altered merge",
            ],
            home=home,
        )
    _real_git_checked(
        ["git", "-c", "protocol.file.allow=always", "clone", "--bare", str(source_path), str(remote_path)],
        home=home,
    )
    remote_base_commit = merge_commit or base_commit
    _real_git_checked(
        ["git", f"--git-dir={remote_path}", "symbolic-ref", "HEAD", "refs/heads/main"],
        home=home,
    )
    _real_git_checked(
        ["git", f"--git-dir={remote_path}", "update-ref", "refs/heads/main", remote_base_commit],
        home=home,
    )
    if altered_merge:
        _real_git_checked(
            ["git", f"--git-dir={remote_path}", "update-ref", "-d", "refs/heads/linear/and-17"],
            home=home,
        )
    else:
        _real_git_checked(
            ["git", f"--git-dir={remote_path}", "update-ref", "refs/heads/linear/and-17", head_commit],
            home=home,
        )
    git_dir = task_path / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    (git_dir / "objects" / "info").mkdir(parents=True)
    (git_dir / "info").mkdir(parents=True)
    (git_dir / "refs").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/linear/and-17\n", encoding="utf-8")
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n"
        '[remote "origin"]\n\turl = git@github.com:antonov-andrey/example.git\n'
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
        encoding="utf-8",
    )
    return _RealGitHistory(
        remote_path=remote_path,
        task_path=task_path,
        base_commit=base_commit,
        head_commit=head_commit,
        head_tree=head_tree,
        merge_commit=merge_commit,
    )


def test_real_git_global_include_url_header_merge_driver_and_attributes_are_closed(tmp_path: Path) -> None:
    """Real Git cannot load any malicious global config or attribute source."""

    home = tmp_path / "hostile-home"
    repository_path = tmp_path / "repository"
    home.mkdir()
    include_path = home / "included.gitconfig"
    marker_path = tmp_path / "merge-driver-invoked"
    include_path.write_text(
        '[url "https://attacker.invalid/"]\n\tinsteadOf = https://github.com/\n'
        '[http "https://github.com/"]\n\textraHeader = Authorization: bearer hidden\n'
        f'[merge "attacker"]\n\tdriver = /usr/bin/touch {marker_path}\n',
        encoding="utf-8",
    )
    (home / ".gitconfig").write_text(f"[include]\n\tpath = {include_path}\n", encoding="utf-8")
    global_attributes_path = home / ".config" / "git" / "attributes"
    global_attributes_path.parent.mkdir(parents=True)
    global_attributes_path.write_text("*.txt merge=attacker\n", encoding="utf-8")
    _real_git_checked(["git", "init", str(repository_path)], home=home)
    _real_git_checked(
        [
            "git",
            "-C",
            str(repository_path),
            "remote",
            "add",
            "origin",
            "https://github.com/antonov-andrey/example.git",
        ],
        home=home,
    )
    closed_environment = _real_git_environment(home)
    ineffective_git_config_environment = dict(closed_environment)
    del ineffective_git_config_environment["GIT_CONFIG_GLOBAL"]
    del ineffective_git_config_environment["GIT_CONFIG_SYSTEM"]
    ineffective_git_config_environment["GIT_CONFIG"] = "/dev/null"
    ineffective_url_process = command_run(
        ["git", "-C", str(repository_path), "remote", "get-url", "origin"],
        environment_by_name_map=ineffective_git_config_environment,
    )
    closed_url_process = command_run(
        ["git", "-C", str(repository_path), "remote", "get-url", "origin"],
        environment_by_name_map=closed_environment,
    )
    config_process = command_run(
        [
            "git",
            "-C",
            str(repository_path),
            "-c",
            "core.attributesFile=/dev/null",
            "config",
            "--includes",
            "--get-regexp",
            "^(include|url|http|merge)\\.",
        ],
        environment_by_name_map=closed_environment,
    )
    attribute_process = command_run(
        [
            "git",
            "-C",
            str(repository_path),
            "-c",
            "core.attributesFile=/dev/null",
            "check-attr",
            "merge",
            "--",
            "conflict.txt",
        ],
        environment_by_name_map=closed_environment,
    )

    assert ineffective_url_process.returncode == 0
    assert ineffective_url_process.stdout == "https://attacker.invalid/antonov-andrey/example.git\n"
    assert closed_url_process.returncode == 0
    assert closed_url_process.stdout == "https://github.com/antonov-andrey/example.git\n"
    assert config_process.returncode == 1
    assert config_process.stdout == ""
    assert attribute_process.returncode == 0
    assert attribute_process.stdout == "conflict.txt: merge: unspecified\n"
    assert not marker_path.exists()


@pytest.mark.parametrize(
    "credential_request",
    (
        "protocol=https\nhost=attacker.invalid\npath=antonov-andrey/example.git\n\n",
        "protocol=https\nhost=github.com\npath=attacker/example.git\n\n",
    ),
)
def test_real_git_destination_mismatch_never_receives_invocation_credential(
    credential_request: str,
) -> None:
    """The real credential protocol fails before emitting a token to another destination."""

    captured_environment: dict[str, str] = {}

    def recording_runner(
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured_environment.update(environment_by_name_map)
        return command_run(
            argument_list,
            environment_by_name_map=environment_by_name_map,
            input_text=input_text,
        )

    principal = GitHubPrincipal(login="octocat", user_id=7, node_id="U_octocat")
    repository = RepositoryIdentity("antonov-andrey/example")
    completed_process = command_closed_run(
        recording_runner,
        [
            "git",
            *git_credential_config_argument_list_get(principal, repository),
            "credential",
            "fill",
        ],
        input_text=credential_request,
    )

    assert completed_process.returncode != 0
    assert "password=" not in completed_process.stdout
    assert "x-access-token" not in completed_process.stdout
    assert captured_environment["HOME"] == "/home/andrey"
    assert captured_environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert captured_environment["GIT_CONFIG_SYSTEM"] == "/dev/null"
    assert "CODEX_HOME" not in captured_environment


def test_real_git_divergent_merge_ignores_global_and_task_attributes_without_push(tmp_path: Path) -> None:
    """Divergent reviewed commits cannot invoke a merge driver or produce an altered tree."""

    history = _real_git_history_create(tmp_path / "history", divergent=True, altered_merge=False)
    hostile_home = tmp_path / "boundary-home"
    hostile_home.mkdir()
    marker_path = tmp_path / "merge-driver-invoked"
    included_config_path = hostile_home / "included.gitconfig"
    included_config_path.write_text(
        '[url "https://attacker.invalid/"]\n\tinsteadOf = https://github.com/\n'
        '[http "https://github.com/"]\n\textraHeader = Authorization: bearer hidden\n'
        f'[merge "attacker"]\n\tdriver = /usr/bin/touch {marker_path}\n',
        encoding="utf-8",
    )
    (hostile_home / ".gitconfig").write_text(
        f"[include]\n\tpath = {included_config_path}\n[core]\n\tattributesFile = {hostile_home / 'attributes'}\n",
        encoding="utf-8",
    )
    (hostile_home / "attributes").write_text("*.txt merge=attacker\n", encoding="utf-8")
    (history.task_path / ".git" / "info" / "attributes").write_text(
        "*.txt merge=attacker\n",
        encoding="utf-8",
    )
    provider = _GhRunner(base_commit=history.base_commit, head_commit=history.head_commit)
    runner = _RealGitBoundaryRunner(
        provider=provider,
        remote_path=history.remote_path,
        isolated_home=hostile_home,
    )

    with pytest.raises(GitHubContractError, match="base is not an ancestor"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=history.base_commit,
            reviewed_head_commit=history.head_commit,
            merge_method="merge",
            repository_path=history.task_path,
        )

    assert runner.git_push_count == 0
    assert provider.operation_mutation_count == 0
    assert not marker_path.exists()
    assert not any("merge-tree" in command for command in runner.git_command_list)


def test_real_git_task_local_config_change_after_audit_cannot_redirect_private_operations(tmp_path: Path) -> None:
    """A post-audit task config replacement is never used for objects, network or recovery."""

    history = _real_git_history_create(tmp_path / "history", divergent=True, altered_merge=False)
    hostile_home = tmp_path / "boundary-home"
    hostile_home.mkdir()
    task_config_path = history.task_path / ".git" / "config"
    provider = _GhRunner(base_commit=history.base_commit, head_commit=history.head_commit)
    runner = _RealGitBoundaryRunner(
        provider=provider,
        remote_path=history.remote_path,
        isolated_home=hostile_home,
        mutate_task_config_path=task_config_path,
    )

    with pytest.raises(GitHubContractError, match="base is not an ancestor"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=history.base_commit,
            reviewed_head_commit=history.head_commit,
            merge_method="merge",
            repository_path=history.task_path,
        )

    assert runner.task_config_mutated is True
    assert "attacker.invalid" in task_config_path.read_text(encoding="utf-8")
    assert runner.git_push_count == 0
    assert provider.operation_mutation_count == 0
    assert all(str(history.task_path) not in argument for command in runner.git_command_list for argument in command)


def test_real_git_private_info_attributes_injection_fails_before_fetch_or_push(tmp_path: Path) -> None:
    """The resolved provider-owned info/attributes path must remain absent."""

    history = _real_git_history_create(tmp_path / "history", divergent=False, altered_merge=False)
    hostile_home = tmp_path / "boundary-home"
    hostile_home.mkdir()
    provider = _GhRunner(base_commit=history.base_commit, head_commit=history.head_commit)
    runner = _RealGitBoundaryRunner(
        provider=provider,
        remote_path=history.remote_path,
        isolated_home=hostile_home,
        inject_private_attributes=True,
    )

    with pytest.raises(GitHubContractError, match="Private Git repository contains unsafe"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=history.base_commit,
            reviewed_head_commit=history.head_commit,
            merge_method="merge",
            repository_path=history.task_path,
        )

    assert runner.git_push_count == 0
    assert provider.operation_mutation_count == 0
    assert not any("fetch" in command for command in runner.git_command_list)


def test_real_git_recovery_rejects_altered_tree_despite_global_and_task_attributes(tmp_path: Path) -> None:
    """Recovery accepts only the exact reviewed head tree and ordered parents."""

    history = _real_git_history_create(tmp_path / "history", divergent=False, altered_merge=True)
    hostile_home = tmp_path / "boundary-home"
    hostile_home.mkdir()
    (hostile_home / ".gitconfig").write_text(
        f"[core]\n\tattributesFile = {hostile_home / 'attributes'}\n" '[merge "attacker"]\n\tdriver = /bin/true\n',
        encoding="utf-8",
    )
    (hostile_home / "attributes").write_text("*.txt merge=attacker\n", encoding="utf-8")
    (history.task_path / ".git" / "info" / "attributes").write_text(
        "*.txt merge=attacker\n",
        encoding="utf-8",
    )
    provider = _GhRunner(base_commit=history.merge_commit, head_commit=history.head_commit)
    provider.state = "MERGED"
    provider.merge_commit = history.merge_commit
    runner = _RealGitBoundaryRunner(
        provider=provider,
        remote_path=history.remote_path,
        isolated_home=hostile_home,
    )

    with pytest.raises(GitHubContractError, match="exact reviewed merge identity"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=history.base_commit,
            reviewed_head_commit=history.head_commit,
            merge_method="merge",
            repository_path=history.task_path,
        )

    assert history.merge_commit
    assert runner.git_push_count == 0
    assert provider.operation_mutation_count == 0
    assert not any("merge-tree" in command for command in runner.git_command_list)


def test_real_git_private_repository_constructs_and_recovers_exact_head_tree(tmp_path: Path) -> None:
    """The complete real Git path pushes and re-proves one exact two-parent result."""

    history = _real_git_history_create(tmp_path / "history", divergent=False, altered_merge=False)
    isolated_home = tmp_path / "boundary-home"
    isolated_home.mkdir()
    provider = _GhRunner(base_commit=history.base_commit, head_commit=history.head_commit)
    runner = _RealGitBoundaryRunner(
        provider=provider,
        remote_path=history.remote_path,
        isolated_home=isolated_home,
        adopt_successful_push=True,
    )

    merged = GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=history.base_commit,
        reviewed_head_commit=history.head_commit,
        merge_method="merge",
        repository_path=history.task_path,
    )

    isolated_git_home = tmp_path / "inspection-home"
    isolated_git_home.mkdir()
    commit_payload = _real_git_checked(
        ["git", f"--git-dir={history.remote_path}", "cat-file", "-p", merged.merge_commit],
        home=isolated_git_home,
    )
    ref_output = _real_git_checked(
        ["git", f"--git-dir={history.remote_path}", "for-each-ref", "--format=%(refname)", "refs/heads/"],
        home=isolated_git_home,
    )

    assert merged.state == "MERGED"
    assert f"tree {history.head_tree}\n" in commit_payload
    assert f"parent {history.base_commit}\nparent {history.head_commit}\n" in commit_payload
    assert ref_output.splitlines() == ["refs/heads/linear/and-17", "refs/heads/main"]
    assert runner.git_push_count == 1
    assert runner.provider.repository_policy_read_count == 2
    assert not any("merge-tree" in command for command in runner.git_command_list)


def test_github_inspect_reads_current_base_oid_for_review_identity() -> None:
    """The provider inspector returns the exact base commit needed by review."""

    runner = _GhRunner()
    snapshot = GitHubPullRequestBoundary(runner).inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
    )

    assert snapshot.base_branch == "main"
    assert snapshot.base_commit == COMMIT_BASE
    assert snapshot.head_commit == COMMIT_ONE
    assert snapshot.required_checks_verified is False
    view_command = next(item for item in runner.command_list if item[1:3] == ["pr", "view"])
    assert "baseRefOid" in view_command[-1]
    assert "headRepository" in view_command[-1]


def test_github_inspect_rejects_cross_repository_head_before_any_merge_gate() -> None:
    """Atomic ref mutation cannot target a fork or a foreign same-name head."""

    runner = _GhRunner()
    runner.head_repository = "attacker/example"
    runner.cross_repository = True
    with pytest.raises(GitHubContractError, match="head repository"):
        GitHubPullRequestBoundary(runner).inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
        )


def test_github_inspect_rejects_existing_deferred_auto_merge_request() -> None:
    """A later-base auto-merge cannot race the exact reviewed mutation owner."""

    runner = _GhRunner()
    runner.auto_merge_request = {"enabledAt": "2026-08-07T07:00:00Z"}
    with pytest.raises(GitHubContractError, match="deferred auto-merge"):
        GitHubPullRequestBoundary(runner).inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
        )


def test_merge_cli_requires_reviewed_base_and_head_identity() -> None:
    """The inspect/merge CLI cannot omit either independently reviewed commit."""

    script = PLUGIN_ROOT / "skills" / "task-merge" / "scripts" / "pull_request.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--reviewed-base-commit" in result.stdout
    assert "--reviewed-head-commit" in result.stdout
    assert "--merge-method" in result.stdout
    assert "--repository-path" in result.stdout

    terminal_inspection_without_worktree = subprocess.run(
        [
            sys.executable,
            str(script),
            "inspect",
            "--repository",
            "antonov-andrey/example",
            "--number",
            "17",
            "--issue-identifier",
            "AND-17",
            "--base-branch",
            "main",
            "--head-branch",
            "linear/and-17",
            "--reviewed-base-commit",
            COMMIT_BASE,
            "--reviewed-head-commit",
            COMMIT_ONE,
            "--merge-method",
            "merge",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert terminal_inspection_without_worktree.returncode == 2
    assert "require --repository-path" in terminal_inspection_without_worktree.stderr

    protection_script = PLUGIN_ROOT / "skills" / "workflow-configure" / "scripts" / "branch_protection.py"
    protection_help = subprocess.run(
        [sys.executable, str(protection_script), "plan", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--merge-method" in protection_help.stdout


def test_github_merge_binds_exact_reviewed_base_head_and_typed_zero_required_checks(tmp_path: Path) -> None:
    """The protected transaction CASes only base and retains the exact open head."""

    runner = _GhRunner()
    merged = GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
        repository_path=tmp_path,
    )

    assert merged.state == "MERGED"
    assert merged.merge_commit == COMMIT_TWO
    push_command = next(item for item in runner.command_list if "push" in item)
    assert "--atomic" in push_command
    assert f"--force-with-lease=refs/heads/main:{COMMIT_BASE}" in push_command
    assert not any(argument.startswith("--force-with-lease=refs/heads/linear/and-17:") for argument in push_command)
    assert f"{COMMIT_TWO}:refs/heads/main" in push_command
    assert ":refs/heads/linear/and-17" not in push_command
    assert runner.remote_commit_by_ref_map["refs/heads/linear/and-17"] == COMMIT_ONE
    assert "https://github.com/antonov-andrey/example.git" in push_command
    assert "origin" not in push_command
    assert "credential.helper=" in push_command
    helper_argument = next(item for item in push_command if item.startswith("credential.helper=!"))
    assert "/usr/bin/gh auth token --hostname github.com --user octocat" in helper_argument
    assert (
        "/usr/bin/curl -q --fail --silent --show-error --proto '=https' --netrc-file /dev/null --config -"
        in helper_argument
    )
    assert "/usr/bin/jq -er" in helper_argument
    assert "Authorization: Bearer %s" in helper_argument
    assert "GH_TOKEN" not in helper_argument
    assert "7" in helper_argument
    assert "U_octocat" in helper_argument
    assert helper_argument.index("response=") < helper_argument.index("/usr/bin/curl")
    assert helper_argument.index("/usr/bin/curl") < helper_argument.index("actual=")
    assert helper_argument.index("actual=") < helper_argument.index("/usr/bin/jq")
    assert helper_argument.index("/user") < helper_argument.index("username=x-access-token")
    assert "credential.useHttpPath=true" in push_command
    assert "http.proactiveAuth=basic" in push_command
    github_network_command_list = [
        command for command in runner.command_list if "https://github.com/antonov-andrey/example.git" in command
    ]
    assert {
        verb for command in github_network_command_list for verb in ("fetch", "push", "ls-remote") if verb in command
    } == {
        "fetch",
        "push",
        "ls-remote",
    }
    assert all("http.proactiveAuth=basic" in command for command in github_network_command_list)
    assert "core.hooksPath=/dev/null" in push_command
    assert "http.extraHeader=" in push_command
    assert "http.followRedirects=false" in push_command
    assert "--no-verify" in push_command
    assert "--no-signed" in push_command
    assert not any(item[1:3] == ["config", "--global"] for item in runner.command_list)
    assert not any("ghp_" in argument for item in runner.command_list for argument in item)
    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)
    assert runner.operation_mutation_count == 1
    assert runner.http_proactive_authentication_probe_count == 5
    probe_index_set = {
        index
        for index, command in enumerate(runner.command_list)
        if any("/linear-agent-proactive-authentication-probe.git" in argument for argument in command)
    }
    github_network_index_list = [
        index
        for index, command in enumerate(runner.command_list)
        if "https://github.com/antonov-andrey/example.git" in command
        and any(verb in command for verb in ("fetch", "push", "ls-remote"))
    ]
    github_network_verb_list = [
        next(verb for verb in ("fetch", "push", "ls-remote") if verb in runner.command_list[index])
        for index in github_network_index_list
    ]
    assert github_network_verb_list == ["fetch", "push", "ls-remote", "fetch", "ls-remote"]
    assert all(index > 0 and index - 1 in probe_index_set for index in github_network_index_list)
    push_environment = runner.environment_list[runner.command_list.index(push_command)]
    assert push_environment["HOME"] == "/home/andrey"
    assert push_environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert push_environment["GIT_CONFIG_SYSTEM"] == "/dev/null"
    assert "GIT_CONFIG" not in push_environment
    assert push_environment["GIT_TERMINAL_PROMPT"] == "0"
    assert "CODEX_HOME" not in push_environment
    assert "GIT_ASKPASS" not in push_environment
    assert "GH_TOKEN" not in push_environment
    view_command = next(item for item in runner.command_list if item[1:3] == ["pr", "view"])
    assert "baseRefOid" in view_command[-1]
    assert "reviewDecision" not in view_command[-1]


def test_atomic_merge_fails_before_remote_ref_access_when_git_cannot_prove_proactive_authentication(
    tmp_path: Path,
) -> None:
    """An ignored proactive-auth key cannot fall through to fetch or mutation."""

    runner = _GhRunner()
    runner.http_proactive_authentication_supported = False
    original_ref_map = dict(runner.remote_commit_by_ref_map)

    with pytest.raises(GitHubContractError, match="cannot prove proactive invocation-helper authentication"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.http_proactive_authentication_probe_count == 1
    assert not any("fetch" in command or "push" in command for command in runner.command_list)
    assert not any("https://github.com/antonov-andrey/example.git" in command for command in runner.command_list)
    assert runner.remote_commit_by_ref_map == original_ref_map
    assert runner.operation_mutation_count == 0


@pytest.mark.parametrize("failure_probe_number", (1, 2, 3))
def test_atomic_merge_probes_each_authenticated_git_command_and_stops_at_exact_failed_boundary(
    tmp_path: Path,
    failure_probe_number: int,
) -> None:
    """Fetch, push and ref readback each require their own immediately preceding probe."""

    runner = _GhRunner()
    snapshot = GitHubPullRequestBoundary(runner).inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
    )
    runner.command_list.clear()
    runner.environment_list.clear()
    runner.http_proactive_authentication_failure_probe_number = failure_probe_number
    runner.http_proactive_authentication_probe_count = 0

    with pytest.raises(GitHubContractError, match="cannot prove proactive invocation-helper authentication"):
        GitHubAtomicMergeBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            repository_path=tmp_path,
            snapshot=snapshot,
            execution_login="octocat",
            execution_user_id=7,
            execution_node_id="U_octocat",
            merge_method="merge",
        )

    github_network_verb_list = [
        next(verb for verb in ("fetch", "push", "ls-remote") if verb in command)
        for command in runner.command_list
        if "https://github.com/antonov-andrey/example.git" in command
        and any(verb in command for verb in ("fetch", "push", "ls-remote"))
    ]
    assert (
        github_network_verb_list
        == {
            1: [],
            2: ["fetch"],
            3: ["fetch", "push"],
        }[failure_probe_number]
    )
    assert runner.http_proactive_authentication_probe_count == failure_probe_number
    assert runner.operation_mutation_count == (1 if failure_probe_number == 3 else 0)


@pytest.mark.parametrize("failure_probe_number", (1, 2))
def test_atomic_merge_recovery_probes_each_authenticated_git_command_and_stops_at_failed_boundary(
    tmp_path: Path,
    failure_probe_number: int,
) -> None:
    """Recovery fetch and retained-ref readback each require a fresh adjacent probe."""

    runner = _GhRunner()
    runner.state = "MERGED"
    runner.remote_commit_by_ref_map = {
        "refs/heads/main": COMMIT_TWO,
        "refs/heads/linear/and-17": COMMIT_ONE,
    }
    snapshot = GitHubPullRequestBoundary(runner).inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
    )
    runner.command_list.clear()
    runner.environment_list.clear()
    runner.http_proactive_authentication_failure_probe_number = failure_probe_number
    runner.http_proactive_authentication_probe_count = 0

    with pytest.raises(GitHubContractError, match="cannot prove proactive invocation-helper authentication"):
        GitHubAtomicMergeBoundary(runner).merged_result_require(
            repository=RepositoryIdentity("antonov-andrey/example"),
            repository_path=tmp_path,
            snapshot=snapshot,
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
        )

    github_network_verb_list = [
        next(verb for verb in ("fetch", "ls-remote") if verb in command)
        for command in runner.command_list
        if "https://github.com/antonov-andrey/example.git" in command
        and any(verb in command for verb in ("fetch", "ls-remote"))
    ]
    assert github_network_verb_list == {1: [], 2: ["fetch"]}[failure_probe_number]
    assert runner.http_proactive_authentication_probe_count == failure_probe_number
    assert runner.operation_mutation_count == 0


def test_atomic_merge_reads_exact_principal_bound_repository_policy_before_construction_and_push(
    tmp_path: Path,
) -> None:
    """Both complete policy reads surround construction and bind the same principal."""

    runner = _GhRunner()
    GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
        repository_path=tmp_path,
    )

    policy_index_list = [
        index
        for index, command in enumerate(runner.command_list)
        if command[1:5] == ["api", "--hostname", "github.com", "repos/antonov-andrey/example"]
    ]
    construction_index = next(index for index, command in enumerate(runner.command_list) if "commit-tree" in command)
    push_index = next(index for index, command in enumerate(runner.command_list) if "push" in command)
    assert len(policy_index_list) == 2
    assert policy_index_list[0] < construction_index < policy_index_list[1] < push_index
    assert runner.execution_identity_read_count >= 6


def test_atomic_merge_rejects_identical_reviewed_base_and_head_before_git_mutation(tmp_path: Path) -> None:
    """Commit construction cannot collapse duplicate ordered parents after review."""

    runner = _GhRunner(base_commit=COMMIT_BASE, head_commit=COMMIT_BASE)

    with pytest.raises(GitHubContractError, match="distinct commits"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_BASE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any(command[0] == "git" for command in runner.command_list)


@pytest.mark.parametrize(
    ("field_by_name_map", "message"),
    (
        ({"allow_merge_commit": False}, "merge method is not enabled"),
        ({"disabled": True}, "policy is inactive"),
        ({"allow_merge_commit": "true"}, "boolean field"),
        ({"merge_commit_title": "UNKNOWN"}, "merge-policy option"),
        ({"full_name": "attacker/example"}, "identity differs"),
    ),
)
def test_atomic_merge_rejects_disabled_malformed_or_identity_conflicting_repository_policy_without_ref_mutation(
    tmp_path: Path,
    field_by_name_map: dict[str, object],
    message: str,
) -> None:
    """A strict fresh repository response cannot be partial, disabled or foreign."""

    runner = _GhRunner()
    runner.repository_policy_field_by_name_map.update(field_by_name_map)

    with pytest.raises(GitHubContractError, match=message):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any("commit-tree" in command or "push" in command for command in runner.command_list)


def test_atomic_merge_rejects_missing_repository_policy_field_without_ref_mutation(tmp_path: Path) -> None:
    """Every relevant provider field is mandatory rather than defaulted."""

    runner = _GhRunner()
    payload = runner._repository_policy_payload_get()
    del payload["allow_merge_commit"]
    runner.repository_policy_payload_override = payload

    with pytest.raises(GitHubContractError, match="response has another shape"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any("commit-tree" in command or "push" in command for command in runner.command_list)


def test_atomic_merge_rejects_repository_policy_drift_after_construction_without_ref_mutation(tmp_path: Path) -> None:
    """Any relevant metadata change between the two reads stops before push."""

    runner = _GhRunner()
    runner.repository_policy_drift_field_by_name_map["has_discussions"] = True

    with pytest.raises(GitHubContractError, match="policy changed during merge construction"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert any("commit-tree" in command for command in runner.command_list)
    assert runner.operation_mutation_count == 0
    assert not any("push" in command for command in runner.command_list)


def test_atomic_merge_rejects_automatic_head_deletion_before_construction(tmp_path: Path) -> None:
    """GitHub cannot own head deletion before terminal merged readback and cleanup."""

    runner = _GhRunner()
    runner.repository_policy_field_by_name_map["delete_branch_on_merge"] = True

    with pytest.raises(GitHubContractError, match="automatic head-branch deletion.*disabled"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any("commit-tree" in command or "push" in command for command in runner.command_list)


def test_atomic_merge_rejects_principal_change_around_repository_policy_without_ref_mutation(tmp_path: Path) -> None:
    """The repository policy is accepted only between matching fresh identity reads."""

    runner = _GhRunner()
    runner.changed_execution_login = "mallory"
    runner.changed_execution_user_id = 8
    runner.changed_execution_node_id = "U_mallory"
    runner.execution_identity_change_after_read_count = 3

    with pytest.raises(GitHubContractError, match="identity changed before Git mutation"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.repository_policy_read_count == 1
    assert runner.operation_mutation_count == 0
    assert not any("commit-tree" in command or "push" in command for command in runner.command_list)


@pytest.mark.parametrize(
    ("attribute_name", "value", "message"),
    (
        (
            "fetch_url_list",
            [
                "git@github.com:antonov-andrey/example.git",
                "https://github.com/antonov-andrey/example.git",
            ],
            "configured fetch URL set.*exactly one",
        ),
        (
            "push_url_list",
            [
                "git@github.com:antonov-andrey/example.git",
                "https://github.com/antonov-andrey/example.git",
            ],
            "configured push URL set.*exactly one",
        ),
        (
            "push_url_list",
            ["git@github.com:attacker/example.git"],
            "configured fetch and push URLs diverge",
        ),
        (
            "fetch_url_list",
            ["ssh://git@github.com/antonov-andrey/example.git"],
            "canonical GitHub URL",
        ),
        (
            "fetch_url_list",
            ["git@github.com:attacker/example.git"],
            "configured fetch and push URLs diverge",
        ),
        (
            "local_config_name_list",
            ["remote.origin.url", "url.https://attacker.example/.insteadof"],
            "merge-unsafe keys",
        ),
        (
            "local_config_name_list",
            ["remote.origin.url", "url.https://attacker.example/.pushinsteadof"],
            "merge-unsafe keys",
        ),
        (
            "local_config_name_list",
            ["remote.origin.url", "http.https://github.com/.extraheader"],
            "merge-unsafe keys",
        ),
    ),
)
def test_atomic_merge_rejects_ambiguous_or_noncanonical_git_destination_without_mutation(
    tmp_path: Path,
    attribute_name: str,
    value: object,
    message: str,
) -> None:
    """Every effective URL and the explicit destination are closed before push."""

    runner = _GhRunner()
    setattr(runner, attribute_name, value)

    with pytest.raises(GitHubContractError, match=message):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any("push" in item for item in runner.command_list)


@pytest.mark.parametrize(
    "config_name",
    (
        "http.https://github.com/.extraheader",
        "url.https://attacker.example/.insteadof",
    ),
)
def test_atomic_merge_rejects_local_authorization_or_url_redirection_without_mutation(
    tmp_path: Path,
    config_name: str,
) -> None:
    """Local headers and URL rewrites cannot enter the explicit GitHub transaction."""

    runner = _GhRunner()
    runner.local_config_name_list.append(config_name)

    with pytest.raises(GitHubContractError, match="merge-unsafe keys"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any("commit-tree" in item or "push" in item for item in runner.command_list)


@pytest.mark.parametrize(
    "environment_by_name_map",
    (
        {"GIT_ASKPASS": "/tmp/task-askpass"},
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": "Authorization: bearer hidden",
        },
        {"HTTPS_PROXY": "http://task-proxy.invalid"},
        {"CODEX_HOME": "/tmp/task-codex-home"},
        {"HOME": "/tmp/task-home"},
    ),
)
def test_merge_rejects_ambient_process_controls_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment_by_name_map: dict[str, str],
) -> None:
    """Ambient task process controls are rejected by name before any mutation."""

    for name, value in environment_by_name_map.items():
        monkeypatch.setenv(name, value)
    runner = _GhRunner()

    with pytest.raises(GitHubContractError, match="unsafe inputs"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert runner.command_list == []


def test_atomic_merge_rejects_mutating_pre_push_hook_even_with_no_verify(tmp_path: Path) -> None:
    """Repository hooks are rejected and also disabled on the atomic push."""

    runner = _GhRunner()
    hook_path = tmp_path / ".git" / "hooks" / "pre-push"
    hook_path.write_text(f"#!/bin/sh\nprintf attacked > {tmp_path / 'hook-mutated'}\n", encoding="utf-8")
    hook_path.chmod(0o755)

    with pytest.raises(GitHubContractError, match="hook or object substitution"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not (tmp_path / "hook-mutated").exists()
    assert not any("push" in item for item in runner.command_list)


def test_atomic_merge_rejects_replace_refs_without_mutation(tmp_path: Path) -> None:
    """A local replacement object cannot alter reviewed commit or tree semantics."""

    runner = _GhRunner()
    replace_ref_path = tmp_path / ".git" / "refs" / "replace" / COMMIT_BASE
    replace_ref_path.parent.mkdir(parents=True)
    replace_ref_path.write_text(COMMIT_ONE + "\n", encoding="utf-8")

    with pytest.raises(GitHubContractError, match="hook or object substitution"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any("commit-tree" in item or "push" in item for item in runner.command_list)


def test_atomic_merge_rejects_alternate_object_store_without_mutation(tmp_path: Path) -> None:
    """Repository-local alternate object storage cannot supply reviewed objects."""

    runner = _GhRunner()
    (tmp_path / ".git" / "objects" / "info" / "alternates").write_text("/tmp/foreign\n", encoding="utf-8")

    with pytest.raises(GitHubContractError, match="hook or object substitution"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any("commit-tree" in item or "push" in item for item in runner.command_list)


@pytest.mark.parametrize(
    ("attribute_name", "value"),
    (
        ("credential_login", "mallory"),
        ("credential_user_id", 8),
        ("credential_node_id", "U_mallory"),
    ),
)
def test_atomic_merge_rejects_actual_credential_token_principal_mismatch_without_mutation(
    tmp_path: Path,
    attribute_name: str,
    value: object,
) -> None:
    """The helper validates its token's login, numeric ID and node ID before object creation."""

    runner = _GhRunner()
    setattr(runner, attribute_name, value)

    with pytest.raises(GitHubContractError, match="credential token differs"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert any(item[0] == "/bin/sh" for item in runner.command_list)
    assert runner.operation_mutation_count == 0
    assert not any("commit-tree" in item or "push" in item for item in runner.command_list)


def test_atomic_merge_rejects_changed_gh_principal_before_git_mutation(tmp_path: Path) -> None:
    """The credential principal cannot differ from the inspected authority principal."""

    runner = _GhRunner()
    runner.changed_execution_login = "mallory"
    runner.changed_execution_user_id = 8
    runner.changed_execution_node_id = "U_mallory"

    with pytest.raises(GitHubContractError, match="identity changed before Git mutation"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert not any("push" in item for item in runner.command_list)


def test_atomic_merge_rejects_matching_foreign_fetch_and_push_destination_without_mutation(tmp_path: Path) -> None:
    """Two internally consistent URLs still must identify the reviewed repository."""

    runner = _GhRunner()
    runner.fetch_url_list = ["git@github.com:attacker/example.git"]
    runner.push_url_list = ["git@github.com:attacker/example.git"]

    with pytest.raises(GitHubContractError, match="destination differs"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0


def test_github_base_only_cas_rejects_base_advance_without_merge_mutation(tmp_path: Path) -> None:
    """The sole reviewed-base lease rejects a concurrent base advance."""

    runner = _GhRunner()
    runner.advance_base_on_push = True
    with pytest.raises(GitHubContractError, match="Reviewed base Git CAS transaction failed"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    # The injected external base advance is observable, but the merge boundary
    # changed neither ref and retained the reviewed head.
    assert runner.operation_mutation_count == 0
    assert runner.remote_commit_by_ref_map == {
        "refs/heads/main": "d" * 40,
        "refs/heads/linear/and-17": COMMIT_ONE,
    }
    push_command = next(item for item in runner.command_list if "push" in item)
    assert "--atomic" in push_command
    assert f"--force-with-lease=refs/heads/main:{COMMIT_BASE}" in push_command
    assert not any(argument.startswith("--force-with-lease=refs/heads/linear/and-17:") for argument in push_command)


def test_github_base_only_cas_rejects_changed_head_at_exact_post_push_readback(tmp_path: Path) -> None:
    """A concurrent head change cannot pass retained-head terminal proof."""

    runner = _GhRunner()
    runner.advance_head_on_push = True

    with pytest.raises(GitHubContractError, match="did not retain the exact reviewed head ref"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 1
    assert runner.remote_commit_by_ref_map == {
        "refs/heads/main": COMMIT_TWO,
        "refs/heads/linear/and-17": "e" * 40,
    }


def test_atomic_merge_terminal_read_lag_routes_to_exact_recovery_not_another_mutation(tmp_path: Path) -> None:
    """An accepted ref transaction can be recovered after delayed PR state visibility."""

    runner = _GhRunner()
    runner.defer_merge_readback = True
    with pytest.raises(GitHubContractError, match="base CAS completed.*retry exact recovery"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 1
    runner.state = "MERGED"
    recovered = GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
        repository_path=tmp_path,
    )

    assert recovered.state == "MERGED"
    assert runner.operation_mutation_count == 1
    assert runner.remote_commit_by_ref_map["refs/heads/linear/and-17"] == COMMIT_ONE


def test_classic_protection_accepts_typed_legitimate_zero_required_checks() -> None:
    """Classic protected-ref CAS can distinguish a typed empty set from failure."""

    runner = _GhRunner(required_check_name_list=[])
    inspection = GitHubPullRequestBoundary(runner).reviewed_inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
    )

    snapshot = inspection.pull_request
    protection = inspection.branch_protection
    assert protection is not None
    assert snapshot.required_check_list == []
    assert snapshot.required_checks_verified is True
    assert protection.protection_source_list == ["classic"]
    assert protection.required_check_name_list == []
    assert any(item[1:3] == ["pr", "view"] and item[-1] == "statusCheckRollup" for item in runner.command_list)
    assert not any(item[1:3] == ["pr", "checks"] for item in runner.command_list)


def test_classic_protection_snapshot_captures_nonblocking_creation_and_fork_sync_conditions() -> None:
    """The closed classic snapshot retains every known boolean even when compatible."""

    runner = _GhRunner()
    runner.block_creations = True
    runner.allow_fork_syncing = True
    inspection = GitHubPullRequestBoundary(runner).reviewed_inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
    )

    protection = inspection.branch_protection
    assert protection is not None
    assert protection.creation_blocked is True
    assert protection.fork_sync_allowed is True
    assert protection.admin_enforcement_enabled is True
    assert protection.force_push_allowed is False
    assert protection.deletion_allowed is False


@pytest.mark.parametrize(
    ("attribute_name", "value", "message"),
    (
        (
            "required_pull_request_reviews",
            {
                "url": (
                    "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/"
                    "required_pull_request_reviews"
                ),
                "dismiss_stale_reviews": False,
                "require_code_owner_reviews": False,
                "required_approving_review_count": 1,
                "require_last_push_approval": False,
            },
            "pull-request reviews",
        ),
        ("required_linear_history", True, "linear history"),
        ("required_signatures", True, "signatures"),
        ("required_conversation_resolution", True, "conversation resolution"),
        ("branch_locked", True, "branch lock"),
        (
            "push_restrictions",
            {
                "url": "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/restrictions",
                "users_url": (
                    "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/restrictions/users"
                ),
                "teams_url": (
                    "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/restrictions/teams"
                ),
                "apps_url": (
                    "https://api.github.com/repos/antonov-andrey/example/branches/main/protection/restrictions/apps"
                ),
                "users": [],
                "teams": [],
                "apps": [],
            },
            "push restrictions",
        ),
    ),
)
def test_classic_protection_rejects_each_incompatible_merge_family(
    attribute_name: str,
    value: object,
    message: str,
) -> None:
    """Each classic gate that can reject the local merge is an explicit conflict."""

    runner = _GhRunner()
    setattr(runner, attribute_name, value)

    with pytest.raises(GitHubContractError, match=f"incompatible.*{message}"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    assert runner.operation_mutation_count == 0


def test_classic_protection_rejects_unknown_top_level_condition() -> None:
    """A newly introduced classic protection condition fails closed."""

    runner = _GhRunner()
    runner.classic_extra_field_by_name_map = {"future_merge_gate": {"enabled": True}}

    with pytest.raises(GitHubContractError, match="unknown or missing fields"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )


def test_classic_protection_rejects_foreign_response_identity() -> None:
    """A typed classic response still must belong to the exact repository/base."""

    runner = _GhRunner()
    runner.classic_extra_field_by_name_map = {
        "url": "https://api.github.com/repos/attacker/example/branches/main/protection"
    }

    with pytest.raises(GitHubContractError, match="unknown or missing fields"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )


def test_classic_protection_rejects_unknown_nested_condition() -> None:
    """A new nested classic condition cannot be silently discarded."""

    runner = _GhRunner()
    runner.classic_extra_field_by_name_map = {
        "allow_force_pushes": {"enabled": False, "future_bypass_mode": "selected_users"}
    }

    with pytest.raises(GitHubContractError, match="allow_force_pushes has another shape"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )


@pytest.mark.parametrize(
    ("attribute_name", "value", "permission", "message"),
    (
        ("enforce_admins", False, "write", "bypass"),
        ("allow_force_pushes", True, "write", "CAS safety"),
        ("allow_deletions", True, "write", "CAS safety"),
    ),
)
def test_classic_protection_rejects_each_ref_or_identity_bypass_family(
    attribute_name: str,
    value: bool,
    permission: str,
    message: str,
) -> None:
    """Admin enforcement, force updates and deletion are direct closed gates."""

    runner = _GhRunner()
    setattr(runner, attribute_name, value)
    runner.execution_permission = permission

    with pytest.raises(GitHubContractError, match=message):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )


@pytest.mark.parametrize("permission", ("read", "triage"))
def test_protection_rejects_execution_identity_without_write_authority(permission: str) -> None:
    """Repository visibility or triage is never mistaken for mutation authority."""

    runner = _GhRunner()
    runner.execution_permission = permission
    with pytest.raises(GitHubContractError, match="write authority"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )


def test_exact_configuration_path_creates_only_absent_minimal_cas_protection() -> None:
    """Approved configuration can close an absence without adding a human gate."""

    runner = _GhRunner(protection_kind="none", required_check_name_list=[])
    boundary = GitHubBranchProtectionBoundary(runner)
    approved_snapshot = boundary.inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        base_branch="main",
    )
    protection = boundary.configure_for_protected_ref_cas(
        repository=RepositoryIdentity("antonov-andrey/example"),
        base_branch="main",
        approved_snapshot=approved_snapshot,
    )

    protection.merge_mechanism_require("merge")
    assert protection.protection_source_list == ["classic"]
    assert protection.required_check_name_list == []
    configure_command = next(item for item in runner.command_list if item[1:4] == ["api", "--method", "PUT"])
    assert "required_pull_request_reviews=null" in configure_command
    assert "enforce_admins=true" in configure_command
    assert "allow_force_pushes=false" in configure_command
    assert "allow_deletions=false" in configure_command


def test_exact_configuration_rejects_compatible_second_snapshot_drift_before_mutation() -> None:
    """The second pre-mutation snapshot must equal the approved snapshot."""

    runner = _GhRunner(protection_kind="none", required_check_name_list=[])
    boundary = GitHubBranchProtectionBoundary(runner)
    approved_snapshot = boundary.inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        base_branch="main",
    )
    runner.changed_execution_permission = "maintain"

    with pytest.raises(GitHubContractError, match="differs from the approved snapshot"):
        boundary.configure_for_protected_ref_cas(
            repository=RepositoryIdentity("antonov-andrey/example"),
            base_branch="main",
            approved_snapshot=approved_snapshot,
        )

    assert not any(item[1:4] == ["api", "--method", "PUT"] for item in runner.command_list)


def test_exact_configuration_rejects_changed_second_snapshot_principal_before_mutation() -> None:
    """A compatible replacement principal cannot execute the approved transaction."""

    runner = _GhRunner(protection_kind="none", required_check_name_list=[])
    boundary = GitHubBranchProtectionBoundary(runner)
    approved_snapshot = boundary.inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        base_branch="main",
    )
    runner.changed_execution_login = "mallory"
    runner.changed_execution_user_id = 8
    runner.changed_execution_node_id = "U_mallory"

    with pytest.raises(GitHubContractError, match="differs from the approved snapshot"):
        boundary.configure_for_protected_ref_cas(
            repository=RepositoryIdentity("antonov-andrey/example"),
            base_branch="main",
            approved_snapshot=approved_snapshot,
        )

    assert not any(item[1:4] == ["api", "--method", "PUT"] for item in runner.command_list)


def test_exact_configuration_final_readback_requires_the_approved_principal() -> None:
    """Final configured state cannot be certified through another compatible principal."""

    runner = _GhRunner(protection_kind="none", required_check_name_list=[])
    boundary = GitHubBranchProtectionBoundary(runner)
    approved_snapshot = boundary.inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        base_branch="main",
    )
    runner.changed_execution_login = "mallory"
    runner.changed_execution_user_id = 8
    runner.changed_execution_node_id = "U_mallory"
    runner.execution_identity_change_after_read_count = 2

    with pytest.raises(GitHubContractError, match="Final.*approved GitHub identity"):
        boundary.configure_for_protected_ref_cas(
            repository=RepositoryIdentity("antonov-andrey/example"),
            base_branch="main",
            approved_snapshot=approved_snapshot,
        )

    configure_command_list = [item for item in runner.command_list if item[1:4] == ["api", "--method", "PUT"]]
    assert len(configure_command_list) == 1


def test_workflow_configuration_already_correct_policy_plans_none_and_rereads_final_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ready policy remains mutation-free but still receives a fresh final readback."""

    runner = _GhRunner()
    module = _workflow_configuration_module_bind(runner, monkeypatch)
    plan, plan_path = _workflow_configuration_plan_write(module=module, tmp_path=tmp_path, capsys=capsys)

    assert plan["repository_policy_action"] == "none"
    assert plan["repository_policy_before"]["delete_branch_on_merge"] is False
    assert plan["repository_policy_after"] == plan["repository_policy_before"]
    assert module.main(_workflow_configuration_apply_argument_list(plan_path)) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "configured"
    assert result["changed"] is False
    assert result["repository_policy_changed"] is False
    assert result["repository_policy_after"]["delete_branch_on_merge"] is False
    assert runner.repository_policy_mutation_count == 0
    assert runner.repository_policy_read_count == 5


def test_workflow_configuration_plans_exact_automatic_branch_deletion_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The approved plan exposes exact current and desired repository settings."""

    runner = _GhRunner()
    runner.repository_policy_field_by_name_map["delete_branch_on_merge"] = True
    module = _workflow_configuration_module_bind(runner, monkeypatch)
    plan, _ = _workflow_configuration_plan_write(module=module, tmp_path=tmp_path, capsys=capsys)

    assert plan["schema_version"] == 2
    assert plan["repository"] == "antonov-andrey/example"
    assert plan["repository_policy_action"] == "disable-automatic-branch-deletion"
    assert plan["repository_policy_before"]["delete_branch_on_merge"] is True
    assert plan["repository_policy_after"]["delete_branch_on_merge"] is False
    assert plan["repository_policy_before"]["principal"] == {
        "login": "octocat",
        "node_id": "U_octocat",
        "user_id": 7,
    }
    assert runner.repository_policy_mutation_count == 0


def test_workflow_configuration_apply_mutates_only_automatic_branch_deletion_and_reads_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Approved correction sends one narrow PATCH and certifies exact final state."""

    runner = _GhRunner()
    runner.repository_policy_field_by_name_map["delete_branch_on_merge"] = True
    module = _workflow_configuration_module_bind(runner, monkeypatch)
    _, plan_path = _workflow_configuration_plan_write(module=module, tmp_path=tmp_path, capsys=capsys)

    assert module.main(_workflow_configuration_apply_argument_list(plan_path)) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["changed"] is True
    assert result["protection_changed"] is False
    assert result["repository_policy_changed"] is True
    assert result["repository_policy_after"]["delete_branch_on_merge"] is False
    assert runner.repository_policy_mutation_count == 1
    assert runner.repository_policy_read_count == 5
    assert [
        command
        for command in runner.command_list
        if command[1:6] == ["api", "--hostname", "github.com", "--method", "PATCH"]
    ] == [
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            "PATCH",
            "repos/antonov-andrey/example",
            "-F",
            "delete_branch_on_merge=false",
        ]
    ]


def test_workflow_configuration_rejects_stale_second_policy_snapshot_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A post-approval compatible setting change stops the compare-and-set write."""

    runner = _GhRunner()
    runner.repository_policy_field_by_name_map["delete_branch_on_merge"] = True
    module = _workflow_configuration_module_bind(runner, monkeypatch)
    _, plan_path = _workflow_configuration_plan_write(module=module, tmp_path=tmp_path, capsys=capsys)
    stale_payload = runner._repository_policy_payload_get()
    stale_payload["delete_branch_on_merge"] = False
    runner.repository_policy_payload_by_read_count_map[3] = stale_payload

    assert module.main(_workflow_configuration_apply_argument_list(plan_path)) == 2
    assert "differs from the approved policy" in capsys.readouterr().err
    assert runner.repository_policy_mutation_count == 0


@pytest.mark.parametrize("tampered_identity", ("repository", "principal"))
def test_workflow_configuration_rejects_wrong_approved_repository_or_principal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tampered_identity: str,
) -> None:
    """Apply is bound to the exact approved repository and executing principal."""

    runner = _GhRunner()
    module = _workflow_configuration_module_bind(runner, monkeypatch)
    plan, plan_path = _workflow_configuration_plan_write(module=module, tmp_path=tmp_path, capsys=capsys)
    if tampered_identity == "repository":
        plan["repository_policy_before"]["repository"] = "attacker/example"
    else:
        plan["repository_policy_before"]["principal"]["login"] = "mallory"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert module.main(_workflow_configuration_apply_argument_list(plan_path)) == 2
    assert "differs from the approved plan" in capsys.readouterr().err
    assert runner.repository_policy_mutation_count == 0


@pytest.mark.parametrize("provider_result", ("failure", "malformed"))
def test_workflow_configuration_rejects_repository_policy_mutation_provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider_result: str,
) -> None:
    """Failed or malformed repository PATCH output can never certify readiness."""

    runner = _GhRunner()
    runner.repository_policy_field_by_name_map["delete_branch_on_merge"] = True
    module = _workflow_configuration_module_bind(runner, monkeypatch)
    _, plan_path = _workflow_configuration_plan_write(module=module, tmp_path=tmp_path, capsys=capsys)
    if provider_result == "failure":
        runner.repository_policy_mutation_returncode = 1
    else:
        runner.repository_policy_mutation_stdout_override = "{"

    assert module.main(_workflow_configuration_apply_argument_list(plan_path)) == 2
    error = capsys.readouterr().err
    expected_message = "configuration failed" if provider_result == "failure" else "response is malformed"
    assert expected_message in error
    assert runner.repository_policy_mutation_count == 1


@pytest.mark.parametrize("final_result", ("automatic-deletion-enabled", "unrelated-drift", "incomplete"))
def test_workflow_configuration_rejects_inexact_final_repository_policy_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    final_result: str,
) -> None:
    """Final provider readback must be complete and equal the approved desired policy."""

    runner = _GhRunner()
    runner.repository_policy_field_by_name_map["delete_branch_on_merge"] = True
    module = _workflow_configuration_module_bind(runner, monkeypatch)
    _, plan_path = _workflow_configuration_plan_write(module=module, tmp_path=tmp_path, capsys=capsys)
    final_payload = runner._repository_policy_payload_get()
    final_payload["delete_branch_on_merge"] = False
    if final_result == "automatic-deletion-enabled":
        final_payload["delete_branch_on_merge"] = True
    elif final_result == "unrelated-drift":
        final_payload["has_discussions"] = True
    else:
        del final_payload["delete_branch_on_merge"]
    runner.repository_policy_payload_by_read_count_map[4] = final_payload

    assert module.main(_workflow_configuration_apply_argument_list(plan_path)) == 2
    error = capsys.readouterr().err
    expected_message_by_result = {
        "automatic-deletion-enabled": "must be disabled",
        "unrelated-drift": "differs from the approved result",
        "incomplete": "response has another shape",
    }
    assert expected_message_by_result[final_result] in error
    assert runner.repository_policy_mutation_count == 1


def test_exact_configuration_plan_rejects_absent_protection_without_write_authority() -> None:
    """An absent branch rule cannot hide an executing identity that cannot create it."""

    module = _workflow_configuration_module_get()
    runner = _GhRunner(protection_kind="none")
    runner.execution_permission = "read"
    snapshot, repository_policy = _configuration_snapshot_get(runner)

    with pytest.raises(GitHubContractError, match="write authority"):
        module._plan_payload(snapshot, repository_policy, merge_method="merge")


def test_effective_ruleset_protection_enforces_strict_merge_and_has_typed_sources() -> None:
    """Active no-bypass rulesets can prove both protected CAS and strict API merge."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    inspection = GitHubPullRequestBoundary(runner).reviewed_inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="squash",
    )

    protection = inspection.branch_protection
    assert protection is not None
    assert protection.protection_source_list == ["ruleset:42"]
    assert protection.ruleset_id_list == [42]
    assert protection.strict_required_status_checks is True
    assert protection.non_fast_forward_protected is True
    assert protection.deletion_protected is True


def test_effective_merge_queue_rule_is_rejected_as_deferred_later_base_mutation() -> None:
    """Exact reviewed-base merge never enables auto-merge or joins a queue."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    runner.ruleset_merge_queue = True
    with pytest.raises(GitHubContractError, match="incompatible.*merge_queue"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="squash",
        )


@pytest.mark.parametrize(
    "rule_type",
    (
        "branch_name_pattern",
        "code_scanning",
        "commit_author_email_pattern",
        "commit_message_pattern",
        "committer_email_pattern",
        "copilot_code_review",
        "creation",
        "file_extension_restriction",
        "file_path_restriction",
        "license_compliance_scanning",
        "max_file_path_length",
        "max_file_size",
        "pull_request",
        "required_deployments",
        "required_linear_history",
        "required_signatures",
        "tag_name_pattern",
        "update",
        "workflows",
    ),
)
def test_effective_ruleset_rejects_each_known_incompatible_rule_family(rule_type: str) -> None:
    """Every known rule that can alter or reject the constructed merge is explicit."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    runner.ruleset_additional_rule_type_list = [rule_type]

    with pytest.raises(GitHubContractError, match=f"incompatible.*{rule_type}"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="squash",
        )


def test_effective_ruleset_rejects_unknown_rule_type_fail_closed() -> None:
    """An unimplemented provider rule cannot become an implicit allow."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    runner.ruleset_additional_rule_type_list = ["future_repository_rule"]

    with pytest.raises(GitHubContractError, match="unknown rule type"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="squash",
        )


def test_effective_ruleset_rejects_unknown_compatible_rule_parameter_fail_closed() -> None:
    """A future status-check parameter cannot silently change an allowed rule."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    runner.ruleset_required_check_extra_parameter_by_name_map = {"future_merge_gate": True}

    with pytest.raises(GitHubContractError, match="required-status-check rule has another shape"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="squash",
        )


def test_workflow_configuration_cannot_plan_none_for_incompatible_protection() -> None:
    """The canonical configuration owner reports conflict instead of action none."""

    module = _workflow_configuration_module_get()
    runner = _GhRunner()
    runner.required_signatures = True
    snapshot, repository_policy = _configuration_snapshot_get(runner)

    with pytest.raises(GitHubContractError, match="incompatible.*required signatures"):
        module._plan_payload(snapshot, repository_policy, merge_method="merge")


@pytest.mark.parametrize("merge_method", ("squash", "rebase"))
def test_unprovable_strategy_fails_closed_before_provider_mutation(merge_method: str) -> None:
    """No unsupported strategy mutates a PR before exact immutable proof exists."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    with pytest.raises(GitHubContractError, match="unsupported without exact immutable strategy proof"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method=merge_method,
        )

    assert runner.operation_mutation_count == 0
    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


@pytest.mark.parametrize("protection_kind", ("classic", "ruleset"))
def test_api_merge_rejects_each_nonstrict_required_check_family(protection_kind: str) -> None:
    """Classic and ruleset checks must both enforce the reviewed base."""

    runner = _GhRunner(protection_kind=protection_kind, required_check_name_list=["test"])
    runner.strict_required_status_checks = False

    with pytest.raises(GitHubContractError, match="strict up-to-date"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="squash",
        )


def test_protected_ref_cas_rejects_nonzero_required_check_definitions() -> None:
    """A newly constructed CAS merge commit cannot pre-satisfy provider checks."""

    runner = _GhRunner(required_check_name_list=["test"])
    with pytest.raises(GitHubContractError, match="zero required-check"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )


@pytest.mark.parametrize(
    ("returncode", "stdout", "message"),
    (
        (1, '{"statusCheckRollup":[]}', "Unable to read empty"),
        (0, "", "Unable to read empty"),
        (0, "not-json", "malformed"),
    ),
)
def test_zero_required_check_read_rejects_provider_failure_empty_or_malformed_output(
    returncode: int,
    stdout: str,
    message: str,
) -> None:
    """A legitimate zero set still requires a typed successful provider read."""

    runner = _GhRunner()
    runner.status_rollup_returncode = returncode
    runner.status_rollup_stdout = stdout
    with pytest.raises(GitHubContractError, match=message):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )


@pytest.mark.parametrize(
    ("protection_kind", "mutate", "message"),
    (
        ("none", lambda runner: None, "unprotected"),
        ("classic", lambda runner: setattr(runner, "enforce_admins", False), "bypass"),
        ("classic", lambda runner: setattr(runner, "allow_force_pushes", True), "CAS safety"),
        ("ruleset", lambda runner: setattr(runner, "ruleset_enforcement", "disabled"), "ruleset identity"),
        (
            "ruleset",
            lambda runner: runner.ruleset_bypass_actor_list.append(
                {"actor_id": 7, "actor_type": "User", "bypass_mode": "always"}
            ),
            "bypass",
        ),
    ),
)
def test_merge_rejects_absent_disabled_bypassed_or_ineffective_protection(
    protection_kind: str,
    mutate: Callable[[_GhRunner], None],
    message: str,
) -> None:
    """No provider protection gap can silently authorize a merge mutation."""

    runner = _GhRunner(protection_kind=protection_kind)
    mutate(runner)
    with pytest.raises(GitHubContractError, match=message):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )


@pytest.mark.parametrize(
    ("returncode", "stdout", "message"),
    (
        (1, "[]", "Unable to read required"),
        (0, "", "Unable to read required"),
        (0, "not-json", "malformed"),
    ),
)
def test_required_check_provider_failure_or_malformed_output_never_becomes_success(
    returncode: int,
    stdout: str,
    message: str,
) -> None:
    """Only typed provider success or pending status can carry required results."""

    runner = _GhRunner(required_check_name_list=["test"])
    runner.check_returncode = returncode
    runner.check_stdout = stdout
    with pytest.raises(GitHubContractError, match=message):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="squash",
        )


def test_branch_protection_provider_failure_never_becomes_absence() -> None:
    """A generic failed or empty protection read is not an unprotected 404."""

    delegate = _GhRunner()

    def runner(
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Replace only the classic-protection response with a generic failure."""

        argument_list = list(argument_list)
        if argument_list[1:3] == ["api", "--include"]:
            return subprocess.CompletedProcess(argument_list, 1, "", "provider failed")
        return delegate(
            argument_list,
            environment_by_name_map=environment_by_name_map,
            input_text=input_text,
        )

    with pytest.raises(GitHubContractError, match="response is malformed"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="squash",
        )


def test_github_merge_retry_adopts_exact_already_merged_reviewed_identity(tmp_path: Path) -> None:
    """A crash after provider merge recovers from exact reviewed base/head readback."""

    runner = _GhRunner()
    runner.state = "MERGED"
    runner.remote_commit_by_ref_map = {
        "refs/heads/main": COMMIT_TWO,
        "refs/heads/linear/and-17": COMMIT_ONE,
    }
    merged = GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
        repository_path=tmp_path,
    )

    assert merged.state == "MERGED"
    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)
    assert any("cat-file" in item and "-p" in item for item in runner.command_list)


def test_public_reviewed_inspection_proves_terminal_merge_before_reporting_success(tmp_path: Path) -> None:
    """Public terminal inspection proves REST identity, tree, parents and retained head."""

    runner = _GhRunner()
    runner.state = "MERGED"
    runner.remote_commit_by_ref_map = {
        "refs/heads/main": COMMIT_TWO,
        "refs/heads/linear/and-17": COMMIT_ONE,
    }

    inspection = GitHubPullRequestBoundary(runner).reviewed_inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
        repository_path=tmp_path,
    )

    assert inspection.pull_request.state == "MERGED"
    assert inspection.pull_request.merged_by_user_id == 7
    assert inspection.branch_protection is None
    assert any(
        item[1:4] == ["api", "--hostname", "github.com"] and "/pulls/17" in item[4] for item in runner.command_list
    )
    assert any("merge-base" in item for item in runner.command_list)
    assert any("rev-parse" in item and item[-1].endswith("^{tree}") for item in runner.command_list)
    assert any("cat-file" in item and "-p" in item for item in runner.command_list)
    assert any("ls-remote" in item for item in runner.command_list)
    assert not any(item[1:3] == ["api", "--include"] for item in runner.command_list)


def test_public_terminal_inspection_requires_repository_for_immutable_proof() -> None:
    """A terminal PR cannot fall back to generic metadata when Git proof is unavailable."""

    runner = _GhRunner()
    runner.state = "MERGED"

    with pytest.raises(GitHubContractError, match="requires the exact repository worktree path"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    assert not any("fetch" in item or "commit-tree" in item for item in runner.command_list)


@pytest.mark.parametrize(
    ("reviewed_base_commit", "reviewed_head_commit", "message"),
    (
        (COMMIT_ONE, COMMIT_ONE, "distinct commits"),
        (COMMIT_BASE, COMMIT_TWO, "head changed after independent review"),
    ),
)
def test_public_terminal_inspection_rejects_changed_reviewed_base_or_head(
    tmp_path: Path,
    reviewed_base_commit: str,
    reviewed_head_commit: str,
    message: str,
) -> None:
    """Neither reviewed commit can drift behind generic merged metadata."""

    runner = _GhRunner()
    runner.state = "MERGED"
    runner.remote_commit_by_ref_map = {"refs/heads/main": COMMIT_TWO}

    with pytest.raises(GitHubContractError, match=message):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=reviewed_base_commit,
            reviewed_head_commit=reviewed_head_commit,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0


@pytest.mark.parametrize("merge_method", ("squash", "rebase"))
def test_public_terminal_inspection_rejects_unprovable_strategy(
    tmp_path: Path,
    merge_method: str,
) -> None:
    """Generic provider metadata never certifies an unsupported terminal strategy."""

    runner = _GhRunner()
    runner.state = "MERGED"
    runner.remote_commit_by_ref_map = {"refs/heads/main": COMMIT_TWO}

    with pytest.raises(GitHubContractError, match="unsupported without exact immutable strategy proof"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method=merge_method,
            repository_path=tmp_path,
        )

    assert not any("fetch" in item or "commit-tree" in item for item in runner.command_list)


@pytest.mark.parametrize(
    ("protection_kind", "required_check_name_list"),
    (("none", []), ("classic", ["replacement-check"])),
)
def test_atomic_merge_recovery_ignores_current_protection_and_check_definition_drift(
    tmp_path: Path,
    protection_kind: str,
    required_check_name_list: list[str],
) -> None:
    """Post-mutation recovery uses immutable result identity, not later gate state."""

    runner = _GhRunner(protection_kind=protection_kind, required_check_name_list=required_check_name_list)
    runner.state = "MERGED"
    runner.base_commit = "d" * 40
    runner.remote_commit_by_ref_map = {
        "refs/heads/main": "d" * 40,
        "refs/heads/linear/and-17": COMMIT_ONE,
    }
    runner.status_rollup_returncode = 1
    runner.check_returncode = 1
    runner.pr_title = "Mutable title edited after merge"
    runner.auto_merge_request = {"enabledAt": "2026-08-07T07:00:00Z"}

    merged = GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
        repository_path=tmp_path,
    )

    assert merged.state == "MERGED"
    assert not any(item[1:3] == ["api", "--include"] for item in runner.command_list)
    assert not any("/rules/branches/" in part for item in runner.command_list for part in item)
    assert not any(item[1:3] == ["pr", "checks"] for item in runner.command_list)
    assert not any(item[1:3] == ["pr", "view"] and item[-1] == "statusCheckRollup" for item in runner.command_list)


@pytest.mark.parametrize(
    ("attribute_name", "value", "message"),
    (
        ("merge_commit_tree", "a" * 40, "exact reviewed merge identity"),
        ("merge_base_commit", COMMIT_ONE, "exact reviewed merge identity"),
        ("merge_head_commit", COMMIT_BASE, "exact reviewed merge identity"),
        ("merged_by_login", "mallory", "merged provider identity"),
        ("merged_by_user_id", 8, "merged provider identity"),
        ("merged_by_node_id", "U_mallory", "merged provider identity"),
    ),
)
def test_atomic_merge_recovery_rejects_inexact_immutable_terminal_identity(
    tmp_path: Path,
    attribute_name: str,
    value: object,
    message: str,
) -> None:
    """Tree, ordered parents and terminal provider principal are all exact."""

    runner = _GhRunner()
    runner.state = "MERGED"
    runner.remote_commit_by_ref_map = {"refs/heads/main": "d" * 40}
    setattr(runner, attribute_name, value)

    with pytest.raises(GitHubContractError, match=message):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0


@pytest.mark.parametrize("retained_head_commit", (None, COMMIT_TWO))
def test_atomic_merge_recovery_rejects_missing_or_changed_reviewed_head(
    tmp_path: Path,
    retained_head_commit: str | None,
) -> None:
    """Terminal recovery requires the reviewed head until issue cleanup owns deletion."""

    runner = _GhRunner()
    runner.state = "MERGED"
    runner.remote_commit_by_ref_map = {"refs/heads/main": COMMIT_TWO}
    if retained_head_commit is not None:
        runner.remote_commit_by_ref_map["refs/heads/linear/and-17"] = retained_head_commit

    with pytest.raises(GitHubContractError, match="did not retain the exact reviewed head ref"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )


def test_github_merge_rejects_closed_unmerged_as_success_evidence(tmp_path: Path) -> None:
    """CLOSED without GitHub merged state never enters mutation or recovery."""

    runner = _GhRunner()
    runner.state = "CLOSED"

    with pytest.raises(GitHubContractError, match="Closed unmerged.*never successful merge evidence"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
            repository_path=tmp_path,
        )

    assert runner.operation_mutation_count == 0
    assert runner.remote_commit_by_ref_map["refs/heads/linear/and-17"] == COMMIT_ONE


def test_github_pr_create_is_idempotent_for_exact_issue_branch(tmp_path: Path) -> None:
    """A retry adopts the one exact open PR instead of creating a duplicate."""

    runner = _GhRunner()
    boundary = GitHubPullRequestBoundary(runner)
    body = tmp_path / "body.md"
    body.write_text("# Change\n", encoding="utf-8")
    arguments = {
        "repository": RepositoryIdentity("antonov-andrey/example"),
        "issue_identifier": "AND-17",
        "base_branch": "main",
        "head_branch": "linear/and-17",
        "title": "AND-17 Implement exact owner",
        "body_file": body,
    }

    first = boundary.create(**arguments)
    second = boundary.create(**arguments)

    assert first.number == second.number == 17
    assert sum(item[1:3] == ["pr", "create"] for item in runner.command_list) == 1
    lookup_command = next(item for item in runner.command_list if item[1:3] == ["api", "--method"])
    assert "--paginate" in lookup_command
    assert "--slurp" in lookup_command


def test_github_pr_create_ignores_closed_unmerged_history_for_replacement(tmp_path: Path) -> None:
    """A prior closed-unmerged PR does not block one new exact open candidate."""

    runner = _GhRunner()
    runner.historical_closed_number_list = [8]
    runner.pull_request_title_by_number_map[8] = "Historical candidate title was edited"
    boundary = GitHubPullRequestBoundary(runner)
    body = tmp_path / "body.md"
    body.write_text("# Replacement\n", encoding="utf-8")

    created = boundary.create(
        repository=RepositoryIdentity("antonov-andrey/example"),
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        title="AND-17 Replace closed-unmerged candidate",
        body_file=body,
    )

    assert created.number == 17
    assert created.state == "OPEN"
    assert sum(item[1:3] == ["pr", "create"] for item in runner.command_list) == 1


def test_github_pr_create_rejects_current_open_candidate_without_issue_title(tmp_path: Path) -> None:
    """The one current open candidate remains bound to its issue title token."""

    runner = _GhRunner()
    runner.pr_exists = True
    runner.pr_title = "Current candidate title was edited"
    body = tmp_path / "body.md"
    body.write_text("# Candidate\n", encoding="utf-8")

    with pytest.raises(GitHubContractError, match="title omits the exact Linear issue token"):
        GitHubPullRequestBoundary(runner).create(
            repository=RepositoryIdentity("antonov-andrey/example"),
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            title="AND-17 Keep exact current candidate",
            body_file=body,
        )

    assert not any(item[1:3] == ["pr", "create"] for item in runner.command_list)


def test_github_pr_create_rejects_duplicate_open_candidates(tmp_path: Path) -> None:
    """Two exact open candidates remain an active-identity conflict."""

    runner = _GhRunner()
    runner.pr_exists = True
    runner.pull_request_state_by_number_map[16] = "OPEN"
    body = tmp_path / "body.md"
    body.write_text("# Candidate\n", encoding="utf-8")

    with pytest.raises(GitHubContractError, match="More than one active pull request"):
        GitHubPullRequestBoundary(runner).create(
            repository=RepositoryIdentity("antonov-andrey/example"),
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            title="AND-17 Keep one exact candidate",
            body_file=body,
        )

    assert not any(item[1:3] == ["pr", "create"] for item in runner.command_list)


def test_github_pr_create_rejects_foreign_base_from_matching_lookup(tmp_path: Path) -> None:
    """A provider response cannot include one PR from another base."""

    runner = _GhRunner(base_branch="release")
    runner.historical_closed_number_list = [8]
    body = tmp_path / "body.md"
    body.write_text("# Candidate\n", encoding="utf-8")

    with pytest.raises(GitHubContractError, match="lookup response has another shape"):
        GitHubPullRequestBoundary(runner).create(
            repository=RepositoryIdentity("antonov-andrey/example"),
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            title="AND-17 Keep exact target",
            body_file=body,
        )

    assert not any(item[1:3] == ["pr", "create"] for item in runner.command_list)


@pytest.mark.parametrize(
    "payload",
    (
        [{"number": 17, "base": {"ref": "main"}, "head": {"ref": "linear/and-17"}}],
        [[{"number": 17, "base": {"ref": "release"}, "head": {"ref": "linear/and-17"}}]],
        [
            [{"number": 17, "base": {"ref": "main"}, "head": {"ref": "linear/and-17"}}],
            [{"number": 17, "base": {"ref": "main"}, "head": {"ref": "linear/and-17"}}],
        ],
    ),
)
def test_github_pr_lookup_rejects_malformed_or_conflicting_pages(payload: object) -> None:
    """Native paginated output cannot weaken exact PR identity or uniqueness."""

    def runner(
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Return one malformed or conflicting lookup payload."""

        del environment_by_name_map, input_text
        argument_list = list(argument_list)
        return subprocess.CompletedProcess(argument_list, 0, json.dumps(payload), "")

    with pytest.raises(GitHubContractError, match="lookup"):
        GitHubPullRequestBoundary(runner).matching_number_list(
            repository=RepositoryIdentity("antonov-andrey/example"),
            base_branch="main",
            head_branch="linear/and-17",
        )


def test_github_merge_rejects_head_change_after_independent_review() -> None:
    """A changed PR head forces Rework and no merge call occurs."""

    runner = _GhRunner(head_commit=COMMIT_TWO)
    with pytest.raises(GitHubContractError, match="changed after independent review"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_github_merge_rejects_open_base_change_after_independent_review() -> None:
    """A changed base forces Rework while the pull request remains open."""

    runner = _GhRunner(base_commit=COMMIT_TWO)
    with pytest.raises(GitHubContractError, match="base changed after independent review"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_github_merge_rejects_pending_required_check() -> None:
    """A pending branch-protection check is never an allowed merge."""

    runner = _GhRunner(required_check_name_list=["test"])
    runner.check_bucket = "pending"
    with pytest.raises(GitHubContractError, match="not passing"):
        GitHubPullRequestBoundary(runner).reviewed_inspect(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="squash",
        )

    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_github_merge_rejects_wrong_base_before_mutation() -> None:
    """A PR aimed at another base cannot enter the provider merge."""

    runner = _GhRunner(base_branch="release")
    with pytest.raises(GitHubContractError, match="declared repository target"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_base_commit=COMMIT_BASE,
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_canceled_pull_request_close_is_idempotent_and_exact() -> None:
    """An open linked PR closes once while terminal readback is accepted."""

    runner = _GhRunner()
    boundary = GitHubPullRequestBoundary(runner)
    arguments = {
        "repository": RepositoryIdentity("antonov-andrey/example"),
        "number": 17,
        "issue_identifier": "AND-17",
        "base_branch": "main",
        "head_branch": "linear/and-17",
    }

    assert boundary.close_if_open(**arguments).state == "CLOSED"
    assert boundary.close_if_open(**arguments).state == "CLOSED"
    assert sum(item[1:3] == ["pr", "close"] for item in runner.command_list) == 1


def test_canceled_pull_request_close_accepts_closed_history_without_issue_title() -> None:
    """A terminal historical title does not block idempotent cancellation cleanup."""

    runner = _GhRunner()
    runner.state = "CLOSED"
    runner.pr_title = "Historical candidate title was edited"

    snapshot = GitHubPullRequestBoundary(runner).close_if_open(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
    )

    assert snapshot.state == "CLOSED"
    assert not any(item[1:3] == ["pr", "close"] for item in runner.command_list)


def test_canceled_pull_request_close_rejects_foreign_target() -> None:
    """Cancellation cannot close another PR from the same repository."""

    runner = _GhRunner(base_branch="release")
    with pytest.raises(GitHubContractError, match="declared repository target"):
        GitHubPullRequestBoundary(runner).close_if_open(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
        )

    assert not any(item[1:3] == ["pr", "close"] for item in runner.command_list)
