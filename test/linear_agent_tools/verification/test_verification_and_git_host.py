"""Behavior tests for semantic handoff evidence and exact GitHub review binding."""

from __future__ import annotations

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
ISSUE_EVIDENCE_URL = "https://linear.app/acme/issue/AND-17/direct-evidence"
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
        "commit_by_repository_map": {"antonov-andrey/example": COMMIT_ONE},
        "pull_request_head_by_url_map": {PULL_REQUEST_URL: COMMIT_ONE},
        "verification_summary_list": [
            "pytest -q passed for unchanged source, command, environment, and semantic owner contract"
        ],
        "evidence_url_list": sorted([ISSUE_EVIDENCE_URL, PULL_REQUEST_URL]),
        "codex_usage": CodexUsage(
            cached_input_tokens=2,
            cache_write_input_tokens=3,
            input_tokens=5,
            output_tokens=7,
            reasoning_output_tokens=11,
        ),
    }
    field_by_name.update(replacement_by_name)
    return TaskHandoff(**field_by_name)  # type: ignore[arg-type]


def test_semantic_handoff_round_trips_direct_state_and_exact_usage() -> None:
    """The provider comment carries current state without a derived approval identity."""

    handoff = _handoff()
    rendered = HANDOFF_COMMENT_CODEC.render(handoff.payload())
    parsed_payload = HANDOFF_COMMENT_CODEC.payload_parse(rendered)

    assert TaskHandoff.from_payload(parsed_payload) == handoff
    assert parsed_payload["pull_request_head_by_url_map"] == {PULL_REQUEST_URL: COMMIT_ONE}
    assert parsed_payload["codex_usage"] == {
        "cached_input_tokens": 2,
        "cache_write_input_tokens": 3,
        "input_tokens": 5,
        "output_tokens": 7,
        "reasoning_output_tokens": 11,
    }
    assert "fingerprint" not in rendered
    assert "receipt" not in rendered


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("input_tokens", -1),
        ("output_tokens", 1.5),
        ("reasoning_output_tokens", True),
    ),
)
def test_handoff_usage_accepts_only_exact_surface_counters(field_name: str, value: object) -> None:
    """Usage cannot be estimated, boolean, fractional or negative."""

    argument_by_name = {
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    argument_by_name[field_name] = value

    with pytest.raises(EvidenceContractError, match=field_name):
        CodexUsage(**argument_by_name)  # type: ignore[arg-type]


def test_review_handoff_binds_current_pr_heads_without_claiming_product_changes() -> None:
    """An independent reviewer records direct head state but no changed commits."""

    review = _handoff(
        operation="review",
        outcome="review-passed",
        summary="Independent full-scope review found zero findings.",
        commit_by_repository_map={},
    )

    assert TaskHandoff.from_payload(review.payload()) == review
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
        pull_request_head_by_url_map={},
        evidence_url_list=[ISSUE_EVIDENCE_URL],
        codex_usage=None,
    )

    assert TaskHandoff.from_payload(acceptance.payload()) == acceptance
    with pytest.raises(EvidenceContractError, match="Non-code handoff"):
        replace(acceptance, commit_by_repository_map={"antonov-andrey/example": COMMIT_ONE})
    with pytest.raises(EvidenceContractError, match="semantic verification and direct evidence"):
        replace(acceptance, verification_summary_list=[])


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
        _handoff(
            pull_request_head_by_url_map={
                PULL_REQUEST_URL: COMMIT_ONE,
                "https://github.com/antonov-andrey/example/pull/18": COMMIT_ONE,
            }
        )


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
        _handoff(pull_request_head_by_url_map={url: COMMIT_ONE})

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
    """Return deterministic gh responses and record exact command argv."""

    def __init__(self, *, head_commit: str = COMMIT_ONE, base_branch: str = "main") -> None:
        self.head_commit = head_commit
        self.base_branch = base_branch
        self.check_bucket = "pass"
        self.state = "OPEN"
        self.pr_exists = False
        self.command_list: list[list[str]] = []

    def __call__(self, argument_list: list[str]) -> subprocess.CompletedProcess[str]:
        """Return the provider response for one expected gh command."""

        self.command_list.append(list(argument_list))
        if argument_list[1:3] == ["pr", "checks"]:
            return subprocess.CompletedProcess(
                argument_list,
                8 if self.check_bucket == "pending" else 0,
                json.dumps([{"name": "test", "bucket": self.check_bucket, "link": "https://example.test/check"}]),
                "",
            )
        if argument_list[1:3] == ["api", "--method"]:
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
            payload = {
                "number": 17,
                "url": PULL_REQUEST_URL,
                "title": "AND-17 Implement exact owner",
                "state": self.state,
                "isDraft": False,
                "baseRefName": self.base_branch,
                "headRefName": "linear/and-17",
                "headRefOid": self.head_commit,
                "mergeStateStatus": "CLEAN",
                "mergedAt": "2026-08-04T12:30:00Z" if self.state == "MERGED" else None,
                "mergeCommit": {"oid": COMMIT_TWO} if self.state == "MERGED" else None,
            }
            return subprocess.CompletedProcess(argument_list, 0, json.dumps(payload), "")
        raise AssertionError(f"Unexpected gh command: {argument_list}")


def test_github_merge_binds_exact_independently_reviewed_head_and_required_checks() -> None:
    """The merge boundary uses the reviewed head and verifies final state."""

    runner = _GhRunner()
    merged = GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
    )

    assert merged.state == "MERGED"
    assert merged.merge_commit == COMMIT_TWO
    merge_command = next(item for item in runner.command_list if item[1:3] == ["pr", "merge"])
    assert merge_command[-2:] == ["--match-head-commit", COMMIT_ONE]
    view_command = next(item for item in runner.command_list if item[1:3] == ["pr", "view"])
    assert "reviewDecision" not in view_command[-1]


def test_github_merge_retry_adopts_exact_already_merged_reviewed_head() -> None:
    """A crash after provider merge recovers from exact merged-result readback."""

    runner = _GhRunner()
    runner.state = "MERGED"
    merged = GitHubPullRequestBoundary(runner).merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        reviewed_head_commit=COMMIT_ONE,
        merge_method="merge",
    )

    assert merged.state == "MERGED"
    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


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
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_github_merge_rejects_pending_required_check() -> None:
    """A pending branch-protection check is never an allowed merge."""

    runner = _GhRunner()
    runner.check_bucket = "pending"
    with pytest.raises(GitHubContractError, match="not passing"):
        GitHubPullRequestBoundary(runner).merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            reviewed_head_commit=COMMIT_ONE,
            merge_method="merge",
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
