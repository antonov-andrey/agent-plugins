"""Behavior tests for semantic handoff evidence and exact GitHub review binding."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools"
LIBRARY_ROOT = PLUGIN_ROOT / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_host.branch_protection import GitHubBranchProtectionBoundary
from git_host.model import GitHubContractError, RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary
from verification._validation import EvidenceContractError, evidence_url_validate, instant_parse
from verification.baseline import LocalPhaseBaseline, TaskWorkspaceBaseline
from verification.comment import (
    HANDOFF_COMMENT_CODEC,
    LOCAL_PHASE_BASELINE_COMMENT_CODEC,
    TASK_WORKSPACE_BASELINE_COMMENT_CODEC,
)
from verification.handoff import CodexUsage, TaskHandoff

COMMIT_ONE = "a" * 40
COMMIT_TWO = "b" * 40
COMMIT_BASE = "c" * 40
ISSUE_EVIDENCE_URL = "https://linear.app/acme/issue/AND-17/direct-evidence"
BASELINE_EVIDENCE_URL = "https://linear.app/acme/issue/AND-17/local-phase-baseline"
PULL_REQUEST_URL = "https://github.com/antonov-andrey/example/pull/17"


def _handoff(**replacement_by_name: object) -> TaskHandoff:
    """Return one complete deterministic implementation handoff."""

    field_by_name: dict[str, object] = {
        "handoff_id": "11111111-1111-4111-8111-111111111111",
        "issue_identifier": "AND-17",
        "operation": "implementation",
        "role_label": "task:implementation",
        "delivery_kind": "code",
        "started_at": datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
        "outcome": "review-ready",
        "summary": "Implemented the bounded provider owner and stopped at independent Review.",
        "attempt_cleanup_complete": True,
        "commit_by_repository_map": {"antonov-andrey/example": COMMIT_ONE},
        "local_phase_baseline_evidence_url": "",
        "pull_request_base_branch_by_url_map": {PULL_REQUEST_URL: "main"},
        "pull_request_base_commit_by_url_map": {PULL_REQUEST_URL: COMMIT_BASE},
        "pull_request_head_by_url_map": {PULL_REQUEST_URL: COMMIT_ONE},
        "verification_summary_list": [
            "pytest -q passed for unchanged source, command, environment, and semantic owner contract"
        ],
        "evidence_url_list": sorted([ISSUE_EVIDENCE_URL, PULL_REQUEST_URL]),
        "codex_usage": CodexUsage(input_tokens=5, reasoning_output_tokens=11),
    }
    field_by_name.update(replacement_by_name)
    return TaskHandoff(**field_by_name)  # type: ignore[arg-type]


def test_semantic_handoff_round_trips_direct_state_and_exact_usage() -> None:
    """The provider comment carries current state without a derived approval identity."""

    handoff = _handoff()
    rendered = HANDOFF_COMMENT_CODEC.render(handoff.payload())
    parsed_payload = HANDOFF_COMMENT_CODEC.payload_parse(rendered)

    assert TaskHandoff.from_payload(parsed_payload) == handoff
    assert parsed_payload["pull_request_base_branch_by_url_map"] == {PULL_REQUEST_URL: "main"}
    assert parsed_payload["pull_request_base_commit_by_url_map"] == {PULL_REQUEST_URL: COMMIT_BASE}
    assert parsed_payload["pull_request_head_by_url_map"] == {PULL_REQUEST_URL: COMMIT_ONE}
    assert parsed_payload["codex_usage"] == {"input_tokens": 5, "reasoning_output_tokens": 11}
    assert "fingerprint" not in rendered
    assert "receipt" not in rendered


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


def test_review_handoff_binds_current_pr_identity_without_claiming_product_changes() -> None:
    """An independent reviewer records direct base and head state but no changed commits."""

    review = _handoff(
        operation="review",
        outcome="review-passed",
        summary="Independent full-scope review found zero findings.",
        commit_by_repository_map={},
    )

    assert TaskHandoff.from_payload(review.payload()) == review
    review.current_pull_request_identity_require(
        base_branch_by_url_map={PULL_REQUEST_URL: "main"},
        base_commit_by_url_map={PULL_REQUEST_URL: COMMIT_BASE},
        head_commit_by_url_map={PULL_REQUEST_URL: COMMIT_ONE},
    )
    with pytest.raises(EvidenceContractError, match="identity changed"):
        review.current_pull_request_identity_require(
            base_branch_by_url_map={PULL_REQUEST_URL: "main"},
            base_commit_by_url_map={PULL_REQUEST_URL: COMMIT_TWO},
            head_commit_by_url_map={PULL_REQUEST_URL: COMMIT_ONE},
        )
    with pytest.raises(EvidenceContractError, match="Review handoff cannot report changed"):
        replace(review, commit_by_repository_map={"antonov-andrey/example": COMMIT_ONE})


def test_noncode_handoff_requires_direct_evidence_but_rejects_product_state() -> None:
    """Acceptance evidence has semantic proof without fake commits or pull requests."""

    acceptance = _handoff(
        operation="acceptance",
        role_label="task:acceptance",
        delivery_kind="evidence",
        outcome="final-boundary",
        summary="Whole deployed outcome passed and awaits the final human decision.",
        commit_by_repository_map={},
        local_phase_baseline_evidence_url=BASELINE_EVIDENCE_URL,
        pull_request_base_branch_by_url_map={},
        pull_request_base_commit_by_url_map={},
        pull_request_head_by_url_map={},
        evidence_url_list=sorted([BASELINE_EVIDENCE_URL, ISSUE_EVIDENCE_URL]),
        codex_usage=None,
    )

    assert TaskHandoff.from_payload(acceptance.payload()) == acceptance
    with pytest.raises(EvidenceContractError, match="Non-code handoff"):
        replace(acceptance, commit_by_repository_map={"antonov-andrey/example": COMMIT_ONE})
    with pytest.raises(EvidenceContractError, match="semantic verification and direct evidence"):
        replace(acceptance, verification_summary_list=[])
    with pytest.raises(EvidenceContractError, match="evidence URL"):
        replace(acceptance, local_phase_baseline_evidence_url="")
    with pytest.raises(EvidenceContractError, match="include its local phase baseline"):
        replace(acceptance, evidence_url_list=[ISSUE_EVIDENCE_URL])


@pytest.mark.parametrize(
    ("operation", "role_label", "delivery_kind", "outcome"),
    (
        ("review", "task:implementation", "code", "review-passed"),
        ("review", "task:implementation", "code", "review-findings"),
        ("review", "task:implementation", "code", "failed"),
        ("review", "task:implementation", "code", "canceled"),
        ("review", "task:implementation", "code", "interrupted"),
        ("acceptance", "task:acceptance", "evidence", "final-boundary"),
        ("acceptance", "task:acceptance", "evidence", "remediation-required"),
        ("acceptance", "task:acceptance", "evidence", "failed"),
        ("acceptance", "task:acceptance", "evidence", "canceled"),
        ("acceptance", "task:acceptance", "evidence", "interrupted"),
        ("merge", "task:implementation", "code", "merged"),
        ("merge", "task:implementation", "code", "rework-required"),
        ("merge", "task:implementation", "code", "failed"),
        ("merge", "task:implementation", "code", "canceled"),
        ("merge", "task:implementation", "code", "interrupted"),
    ),
)
def test_review_accept_merge_handoffs_require_cleanup_for_every_outcome(
    operation: str,
    role_label: str,
    delivery_kind: str,
    outcome: str,
) -> None:
    """No review, acceptance or merge handoff may be rendered before nested cleanup."""

    with pytest.raises(EvidenceContractError, match="nested attempt-resource cleanup"):
        _handoff(
            operation=operation,
            role_label=role_label,
            delivery_kind=delivery_kind,
            outcome=outcome,
            attempt_cleanup_complete=False,
        )


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("operation", [], "operation and task role"),
        ("role_label", {}, "operation and task role"),
        ("delivery_kind", [], "role and delivery kind"),
        ("outcome", {}, "operation and outcome"),
        ("verification_summary_list", [{}], "verification summaries"),
        ("evidence_url_list", [{}], "evidence URLs"),
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


def test_implementation_handoff_binds_each_pr_head_to_its_current_repository_commit() -> None:
    """Implementation cannot publish unrelated or stale repository and PR identities."""

    with pytest.raises(EvidenceContractError, match="repository is absent"):
        _handoff(commit_by_repository_map={"antonov-andrey/other": COMMIT_ONE})
    with pytest.raises(EvidenceContractError, match="differs from current commit"):
        _handoff(commit_by_repository_map={"antonov-andrey/example": COMMIT_TWO})
    with pytest.raises(EvidenceContractError, match="repeats one pull-request repository"):
        pull_request_url_list = [
            PULL_REQUEST_URL,
            "https://github.com/antonov-andrey/example/pull/18",
        ]
        _handoff(
            pull_request_base_branch_by_url_map={url: "main" for url in pull_request_url_list},
            pull_request_base_commit_by_url_map={url: COMMIT_BASE for url in pull_request_url_list},
            pull_request_head_by_url_map={url: COMMIT_ONE for url in pull_request_url_list},
        )


def test_handoff_requires_one_aligned_complete_pr_review_identity() -> None:
    """Base branch, exact base commit and head are one closed reviewed PR identity."""

    with pytest.raises(EvidenceContractError, match="must align"):
        _handoff(pull_request_base_commit_by_url_map={})
    with pytest.raises(EvidenceContractError, match="base is not a full lowercase commit"):
        _handoff(pull_request_base_commit_by_url_map={PULL_REQUEST_URL: "main"})


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
        _handoff(
            pull_request_base_branch_by_url_map={url: "main"},
            pull_request_base_commit_by_url_map={url: COMMIT_BASE},
            pull_request_head_by_url_map={url: COMMIT_ONE},
        )

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
        source_fingerprint="c" * 64,
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


def test_task_workspace_baseline_is_deterministic_migration_evidence() -> None:
    """First dispatch remains bound to source, branch and every remote-base commit."""

    baseline = TaskWorkspaceBaseline(
        issue_identifier="AND-17",
        source_fingerprint="d" * 64,
        branch_name="linear/and-17",
        baseline_commit_by_repository_url_map={
            "git@github.com:antonov-andrey/example.git": COMMIT_ONE,
            "ssh://git@github.com/antonov-andrey/other.git": COMMIT_TWO,
        },
    )
    rendered = TASK_WORKSPACE_BASELINE_COMMENT_CODEC.render(baseline.payload())

    assert TaskWorkspaceBaseline.from_payload(TASK_WORKSPACE_BASELINE_COMMENT_CODEC.payload_parse(rendered)) == baseline
    with pytest.raises(EvidenceContractError, match="branch differs"):
        replace(baseline, branch_name="linear/and-18")
    with pytest.raises(EvidenceContractError, match="repeats one repository identity"):
        replace(
            baseline,
            baseline_commit_by_repository_url_map={
                "git@github.com:antonov-andrey/example.git": COMMIT_ONE,
                "ssh://git@github.com/antonov-andrey/example.git": COMMIT_TWO,
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
        self.protection_kind = protection_kind
        self.required_check_name_list = [] if required_check_name_list is None else required_check_name_list
        self.enforce_admins = True
        self.allow_force_pushes = False
        self.allow_deletions = False
        self.execution_permission = "admin"
        self.ruleset_enforcement = "active"
        self.ruleset_merge_queue = False
        self.ruleset_bypass_actor_list: list[dict[str, object]] = []
        self.check_bucket = "pass"
        self.check_returncode: int | None = None
        self.check_stdout: str | None = None
        self.status_rollup_returncode = 0
        self.status_rollup_stdout: str | None = None
        self.state = "OPEN"
        self.defer_merge_readback = False
        self.pr_exists = False
        self.advance_base_on_push = False
        self.advance_head_on_push = False
        self.operation_mutation_count = 0
        self.remote_commit_by_ref_map = {
            f"refs/heads/{base_branch}": base_commit,
            "refs/heads/linear/and-17": head_commit,
        }
        self.command_list: list[list[str]] = []

    def __call__(self, argument_list: list[str]) -> subprocess.CompletedProcess[str]:
        """Return the provider response for one expected gh command."""

        argument_list = list(argument_list)
        self.command_list.append(argument_list)
        if argument_list[0] == "git":
            return self._git_call(argument_list)
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
        if argument_list[1:3] == ["api", "user"]:
            return subprocess.CompletedProcess(argument_list, 0, json.dumps({"login": "octocat", "id": 7}), "")
        if argument_list[1:3] == ["api", "--include"]:
            if self.protection_kind == "classic":
                payload = {
                    "enforce_admins": {"enabled": self.enforce_admins},
                    "allow_force_pushes": {"enabled": self.allow_force_pushes},
                    "allow_deletions": {"enabled": self.allow_deletions},
                    "required_status_checks": (
                        {
                            "strict": True,
                            "contexts": list(self.required_check_name_list),
                            "checks": [],
                        }
                        if self.required_check_name_list
                        else None
                    ),
                }
                return _included_response(argument_list, status=200, payload=payload)
            return _included_response(
                argument_list,
                status=404,
                payload={"message": "Branch not protected", "status": "404"},
            )
        if argument_list[1] == "api" and "/collaborators/" in argument_list[-1]:
            return subprocess.CompletedProcess(
                argument_list,
                0,
                json.dumps({"permission": self.execution_permission, "user": {"login": "octocat"}}),
                "",
            )
        if argument_list[1:4] == ["api", "--method", "GET"] and "/rules/branches/" in argument_list[-3]:
            if self.protection_kind != "ruleset":
                return subprocess.CompletedProcess(argument_list, 0, "[[]]", "")
            rule_list: list[dict[str, object]] = [
                {
                    "type": "non_fast_forward",
                    "ruleset_source_type": "Repository",
                    "ruleset_source": "antonov-andrey/example",
                    "ruleset_id": 42,
                },
                {
                    "type": "deletion",
                    "ruleset_source_type": "Repository",
                    "ruleset_source": "antonov-andrey/example",
                    "ruleset_id": 42,
                },
            ]
            if self.required_check_name_list:
                rule_list.append(
                    {
                        "type": "required_status_checks",
                        "ruleset_source_type": "Repository",
                        "ruleset_source": "antonov-andrey/example",
                        "ruleset_id": 42,
                        "parameters": {
                            "strict_required_status_checks_policy": True,
                            "required_status_checks": [
                                {"context": name, "integration_id": None} for name in self.required_check_name_list
                            ],
                        },
                    }
                )
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
            rule_list: list[dict[str, object]] = [{"type": "non_fast_forward"}, {"type": "deletion"}]
            if self.required_check_name_list:
                rule_list.append({"type": "required_status_checks"})
            if self.ruleset_merge_queue:
                rule_list.append({"type": "merge_queue"})
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
            payload = (
                [
                    [
                        {
                            "number": 17,
                            "base": {"ref": self.base_branch},
                            "head": {"ref": "linear/and-17"},
                        }
                    ]
                ]
                if self.pr_exists
                else [[]]
            )
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
            payload = {
                "number": 17,
                "url": PULL_REQUEST_URL,
                "title": "AND-17 Implement exact owner",
                "state": self.state,
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
                "mergedAt": "2026-08-04T12:30:00Z" if self.state == "MERGED" else None,
                "mergeCommit": {"oid": COMMIT_TWO} if self.state == "MERGED" else None,
            }
            return subprocess.CompletedProcess(argument_list, 0, json.dumps(payload), "")
        raise AssertionError(f"Unexpected gh command: {argument_list}")

    def _git_call(self, argument_list: list[str]) -> subprocess.CompletedProcess[str]:
        """Model local object creation and one atomic remote ref transaction."""

        if "rev-parse" in argument_list:
            return subprocess.CompletedProcess(argument_list, 0, argument_list[2] + "\n", "")
        if argument_list[-3:] == ["remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(argument_list, 0, "git@github.com:antonov-andrey/example.git\n", "")
        if "fetch" in argument_list:
            return subprocess.CompletedProcess(argument_list, 0, "", "")
        if "cat-file" in argument_list and "-p" in argument_list:
            payload = (
                f"tree {'f' * 40}\n"
                f"parent {self.base_commit}\n"
                f"parent {self.head_commit}\n"
                "author Octocat <octocat@users.noreply.github.com> 0 +0000\n"
                "committer Octocat <octocat@users.noreply.github.com> 0 +0000\n\n"
                "Merge reviewed pull request\n"
            )
            return subprocess.CompletedProcess(argument_list, 0, payload, "")
        if "cat-file" in argument_list:
            return subprocess.CompletedProcess(argument_list, 0, "", "")
        if "merge-tree" in argument_list:
            return subprocess.CompletedProcess(argument_list, 0, "f" * 40 + "\n", "")
        if "commit-tree" in argument_list:
            return subprocess.CompletedProcess(argument_list, 0, COMMIT_TWO + "\n", "")
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
            head_ref = "refs/heads/linear/and-17"
            self.remote_commit_by_ref_map[base_ref] = COMMIT_TWO
            del self.remote_commit_by_ref_map[head_ref]
            self.operation_mutation_count += 1
            if not self.defer_merge_readback:
                self.state = "MERGED"
            return subprocess.CompletedProcess(argument_list, 0, "", "")
        if "ls-remote" in argument_list:
            requested_ref_set = set(argument_list[argument_list.index("origin") + 1 :])
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

    protection_script = PLUGIN_ROOT / "skills" / "workflow-configure" / "scripts" / "branch_protection.py"
    protection_help = subprocess.run(
        [sys.executable, str(protection_script), "plan", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--merge-method" in protection_help.stdout


def test_github_merge_binds_exact_reviewed_base_head_and_typed_zero_required_checks(tmp_path: Path) -> None:
    """The protected transaction leases both refs after a typed zero-check read."""

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
    assert f"--force-with-lease=refs/heads/linear/and-17:{COMMIT_ONE}" in push_command
    assert f"{COMMIT_TWO}:refs/heads/main" in push_command
    assert ":refs/heads/linear/and-17" in push_command
    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)
    assert runner.operation_mutation_count == 1
    view_command = next(item for item in runner.command_list if item[1:3] == ["pr", "view"])
    assert "baseRefOid" in view_command[-1]
    assert "reviewDecision" not in view_command[-1]


@pytest.mark.parametrize("racing_ref", ("base", "head"))
def test_github_atomic_merge_rejects_ref_advance_in_preflight_mutation_window_without_ref_mutation(
    tmp_path: Path,
    racing_ref: str,
) -> None:
    """A racing base or head advance rejects both ref updates as one transaction."""

    runner = _GhRunner()
    if racing_ref == "base":
        runner.advance_base_on_push = True
    else:
        runner.advance_head_on_push = True
    with pytest.raises(GitHubContractError, match="Atomic reviewed Git ref transaction failed"):
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
    # changed neither ref and did not delete the reviewed head.
    assert runner.operation_mutation_count == 0
    assert runner.remote_commit_by_ref_map == (
        {
            "refs/heads/main": "d" * 40,
            "refs/heads/linear/and-17": COMMIT_ONE,
        }
        if racing_ref == "base"
        else {
            "refs/heads/main": COMMIT_BASE,
            "refs/heads/linear/and-17": "e" * 40,
        }
    )
    push_command = next(item for item in runner.command_list if "push" in item)
    assert "--atomic" in push_command
    assert f"--force-with-lease=refs/heads/main:{COMMIT_BASE}" in push_command
    assert f"--force-with-lease=refs/heads/linear/and-17:{COMMIT_ONE}" in push_command


def test_atomic_merge_terminal_read_lag_routes_to_exact_recovery_not_another_mutation(tmp_path: Path) -> None:
    """An accepted ref transaction can be recovered after delayed PR state visibility."""

    runner = _GhRunner()
    runner.defer_merge_readback = True
    with pytest.raises(GitHubContractError, match="transaction completed.*retry exact recovery"):
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


def test_classic_protection_accepts_typed_legitimate_zero_required_checks() -> None:
    """Classic protected-ref CAS can distinguish a typed empty set from failure."""

    runner = _GhRunner(required_check_name_list=[])
    snapshot, protection = GitHubPullRequestBoundary(runner).reviewed_inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
    )

    assert snapshot.required_check_list == []
    assert snapshot.required_checks_verified is True
    assert protection.protection_source_list == ["classic"]
    assert protection.required_check_name_list == []
    assert any(item[1:3] == ["pr", "view"] and item[-1] == "statusCheckRollup" for item in runner.command_list)
    assert not any(item[1:3] == ["pr", "checks"] for item in runner.command_list)


def test_exact_configuration_path_creates_only_absent_minimal_cas_protection() -> None:
    """Approved configuration can close an absence without adding a human gate."""

    runner = _GhRunner(protection_kind="none", required_check_name_list=[])
    protection = GitHubBranchProtectionBoundary(runner).configure_for_protected_ref_cas(
        repository=RepositoryIdentity("antonov-andrey/example"),
        base_branch="main",
    )

    protection.merge_mechanism_require("merge")
    assert protection.protection_source_list == ["classic"]
    assert protection.required_check_name_list == []
    configure_command = next(item for item in runner.command_list if item[1:4] == ["api", "--method", "PUT"])
    assert "required_pull_request_reviews=null" in configure_command
    assert "enforce_admins=true" in configure_command
    assert "allow_force_pushes=false" in configure_command
    assert "allow_deletions=false" in configure_command


def test_effective_ruleset_protection_enforces_strict_merge_and_has_typed_sources() -> None:
    """Active no-bypass rulesets can prove both protected CAS and strict API merge."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    _snapshot, protection = GitHubPullRequestBoundary(runner).reviewed_inspect(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="squash",
    )

    assert protection.protection_source_list == ["ruleset:42"]
    assert protection.ruleset_id_list == [42]
    assert protection.strict_required_status_checks is True
    assert protection.non_fast_forward_protected is True
    assert protection.deletion_protected is True


def test_effective_merge_queue_rule_is_rejected_as_deferred_later_base_mutation() -> None:
    """Exact reviewed-base merge never enables auto-merge or joins a queue."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    runner.ruleset_merge_queue = True
    with pytest.raises(GitHubContractError, match="merge-queue.*incompatible"):
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


def test_strict_ruleset_api_merge_keeps_exact_head_match() -> None:
    """Squash/rebase mutation retains exact head and server strict-base gates."""

    runner = _GhRunner(protection_kind="ruleset", required_check_name_list=["test"])
    merged = GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_base_commit=COMMIT_BASE,
        reviewed_head_commit=COMMIT_ONE,
        merge_method="squash",
    )

    assert merged.state == "MERGED"
    merge_command = next(item for item in runner.command_list if item[1:3] == ["pr", "merge"])
    assert "--squash" in merge_command
    assert merge_command[-2:] == ["--match-head-commit", COMMIT_ONE]


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

    def runner(argument_list: list[str]) -> subprocess.CompletedProcess[str]:
        """Replace only the classic-protection response with a generic failure."""

        if argument_list[1:3] == ["api", "--include"]:
            return subprocess.CompletedProcess(argument_list, 1, "", "provider failed")
        return delegate(argument_list)

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
    runner.remote_commit_by_ref_map = {"refs/heads/main": COMMIT_TWO}
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


def test_atomic_merge_recovery_rejects_merged_pr_without_atomic_head_deletion(tmp_path: Path) -> None:
    """A foreign provider merge cannot masquerade as a recovered two-ref transaction."""

    runner = _GhRunner()
    runner.state = "MERGED"
    with pytest.raises(GitHubContractError, match="did not delete"):
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

    def runner(argument_list: list[str]) -> subprocess.CompletedProcess[str]:
        """Return one malformed or conflicting lookup payload."""

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


@pytest.mark.parametrize("already_merged", (False, True))
def test_github_merge_rejects_base_change_after_independent_review(already_merged: bool) -> None:
    """A changed base forces Rework in both live merge and crash-recovery inspection."""

    runner = _GhRunner(base_commit=COMMIT_TWO)
    if already_merged:
        runner.state = "MERGED"
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
        GitHubPullRequestBoundary(runner).merge(
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
