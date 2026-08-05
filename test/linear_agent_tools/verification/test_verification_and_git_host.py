"""Behavior tests for evidence reuse and exact GitHub candidate binding."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from verification._validation import VerificationReceiptError
from verification.attempt import AttemptSummary
from verification.baseline import LocalPhaseBaseline, TaskWorkspaceBaseline
from verification.candidate import CandidateInput
from verification.invalidation import ReceiptReuseEvaluator
from verification.model import VerificationCheckout, VerificationInput, VerificationReceipt
from verification.receipt import (
    ATTEMPT_COMMENT_CODEC,
    LOCAL_PHASE_BASELINE_COMMENT_CODEC,
    TASK_WORKSPACE_BASELINE_COMMENT_CODEC,
    VERIFICATION_RECEIPT_COMMENT_CODEC,
)

COMMIT_ONE = "a" * 40
COMMIT_TWO = "b" * 40
CORPUS_ONE = "1" * 64
CORPUS_TWO = "2" * 64
EVIDENCE_ONE = "3" * 64
LOCK_ONE = "c" * 64
LOCK_TWO = "d" * 64


def _verification_input(
    *,
    commit: str = COMMIT_ONE,
    lock: str = LOCK_ONE,
    environment: str = "development:release-one",
    corpus: str = CORPUS_ONE,
    model: str = "gpt-5.6-sol",
    reasoning_effort: str = "medium",
) -> VerificationInput:
    """Return one complete deterministic verification input.

    Args:
        commit: Repository commit.
        lock: Dependency lock fingerprint.
        environment: Exact external environment identity.
        corpus: Exact corpus content identity.
        model: Exact model identity.
        reasoning_effort: Exact model reasoning configuration.

    Returns:
        Typed input.
    """

    return VerificationInput(
        command_argument_list=["pytest", "-q"],
        working_directory="/workspace/example/.worktree/and-17",
        source_fingerprint="f" * 64,
        checkout_list=[
            VerificationCheckout(
                path="/workspace/example/.worktree/and-17",
                role_list=["verification", "corpus"],
                repository_url="git@github.com:antonov-andrey/example.git",
                commit=commit,
                recursive_submodule_commit_by_path_map={"module/provider": COMMIT_ONE},
                dependency_lock_sha256_by_path_map={"requirements-dev.txt": lock},
            )
        ],
        corpus_content_sha256=corpus,
        model_identity=model,
        model_configuration_by_name_map={"reasoning-effort": reasoning_effort},
        environment_identity=environment,
        release_identity="sha256:" + "e" * 64,
    )


def test_receipt_roundtrip_and_exact_reuse_key() -> None:
    """A passed receipt reuses only when every declared input is identical."""

    current = _verification_input()
    receipt = VerificationReceipt.from_input(
        current,
        outcome="passed",
        evidence_url="https://github.com/antonov-andrey/example/actions/runs/1",
        evidence_content_sha256=EVIDENCE_ONE,
        completed_at=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
    )
    parsed = VerificationReceipt.from_payload(
        VERIFICATION_RECEIPT_COMMENT_CODEC.payload_parse(VERIFICATION_RECEIPT_COMMENT_CODEC.render(receipt.payload()))
    )

    assert parsed == receipt
    assert ReceiptReuseEvaluator(current).decision_get(parsed).reusable
    changed_commit = ReceiptReuseEvaluator(_verification_input(commit=COMMIT_TWO)).decision_get(parsed)
    assert changed_commit.reason_list == ["checkout-set-changed"]
    changed_lock = ReceiptReuseEvaluator(_verification_input(lock=LOCK_TWO)).decision_get(parsed)
    assert changed_lock.reason_list == ["checkout-set-changed"]
    changed_corpus = ReceiptReuseEvaluator(_verification_input(corpus=CORPUS_TWO)).decision_get(parsed)
    assert changed_corpus.reason_list == ["corpus-content-changed"]
    changed_model = ReceiptReuseEvaluator(_verification_input(model="gpt-5.6-terra")).decision_get(parsed)
    assert changed_model.reason_list == ["model-identity-changed"]
    changed_model_configuration = ReceiptReuseEvaluator(_verification_input(reasoning_effort="high")).decision_get(
        parsed
    )
    assert changed_model_configuration.reason_list == ["model-configuration-changed"]
    changed_environment = ReceiptReuseEvaluator(
        _verification_input(environment="development:release-two")
    ).decision_get(parsed)
    assert changed_environment.reason_list == ["environment-identity-changed"]
    changed_repository_payload = _verification_input().payload()
    changed_repository_payload["checkout_list"][0]["repository_url"] = "git@github.com:antonov-andrey/other.git"
    changed_repository = ReceiptReuseEvaluator(VerificationInput.from_payload(changed_repository_payload)).decision_get(
        parsed
    )
    assert changed_repository.reason_list == ["checkout-set-changed"]

    changed_source_payload = _verification_input().payload()
    changed_source_payload["source_fingerprint"] = "0" * 64
    changed_source = ReceiptReuseEvaluator(VerificationInput.from_payload(changed_source_payload)).decision_get(parsed)
    assert changed_source.reason_list == ["source-fingerprint-changed"]


def test_external_evidence_receipt_can_bind_source_without_a_repository_commit() -> None:
    """A source-independent provider probe remains reusable only for its exact source."""

    value = VerificationInput(
        command_argument_list=["linear-provider-probe"],
        working_directory="/workspace",
        source_fingerprint="f" * 64,
        checkout_list=[],
        corpus_content_sha256="",
        model_identity="",
        model_configuration_by_name_map={},
        environment_identity="linear:workspace-one",
        release_identity="",
    )

    assert (
        ReceiptReuseEvaluator(value)
        .decision_get(
            VerificationReceipt.from_input(
                value,
                outcome="passed",
                evidence_url="https://linear.app/example",
                evidence_content_sha256=EVIDENCE_ONE,
            )
        )
        .reusable
    )


def test_checkout_list_represents_two_revisions_of_one_repository_without_collision() -> None:
    """Separate paths preserve distinct commits for one repeated repository URL."""

    payload = _verification_input().payload()
    second_checkout = dict(payload["checkout_list"][0])
    second_checkout["path"] = "/workspace/example"
    second_checkout["role_list"] = ["synchronized-main"]
    second_checkout["commit"] = COMMIT_TWO
    payload["checkout_list"].append(second_checkout)

    parsed = VerificationInput.from_payload(payload)

    assert [checkout.commit for checkout in parsed.checkout_list] == [COMMIT_TWO, COMMIT_ONE]
    assert {checkout.repository_url for checkout in parsed.checkout_list} == {
        "git@github.com:antonov-andrey/example.git"
    }

    payload["checkout_list"][1]["path"] = payload["checkout_list"][0]["path"]
    with pytest.raises(VerificationReceiptError, match="paths must be unique"):
        VerificationInput.from_payload(payload)


@pytest.mark.parametrize(
    ("payload_path", "value", "message"),
    [
        (("working_directory",), "workspace/example", "absolute POSIX"),
        (("checkout_list", 0, "path"), "/workspace/../example", "absolute POSIX"),
        (
            ("checkout_list", 0, "recursive_submodule_commit_by_path_map"),
            {"../provider": COMMIT_ONE},
            "repository-relative POSIX",
        ),
        (
            ("checkout_list", 0, "dependency_lock_sha256_by_path_map"),
            {"/workspace/requirements-dev.txt": LOCK_ONE},
            "repository-relative POSIX",
        ),
        (
            ("checkout_list", 0, "dependency_lock_sha256_by_path_map"),
            {".": LOCK_ONE},
            "repository-relative POSIX",
        ),
    ],
)
def test_verification_paths_are_canonical_and_unambiguous(
    payload_path: tuple[object, ...], value: object, message: str
) -> None:
    """Receipt paths cannot depend on an undeclared anchor or escape a checkout."""

    payload = _verification_input().payload()
    target = payload
    for part in payload_path[:-1]:
        target = target[part]
    target[payload_path[-1]] = value

    with pytest.raises(VerificationReceiptError, match=message):
        VerificationInput.from_payload(payload)


@pytest.mark.parametrize(
    ("payload_field", "pair_list"),
    [
        (
            "model_configuration_by_name_map",
            [["reasoning-effort", "medium"]],
        ),
        (
            "recursive_submodule_commit_by_path_map",
            [["module/provider", COMMIT_ONE]],
        ),
        (
            "dependency_lock_sha256_by_path_map",
            [["requirements-dev.txt", LOCK_ONE]],
        ),
    ],
)
def test_verification_mapping_boundaries_reject_pair_list_carriers(
    payload_field: str,
    pair_list: list[list[str]],
) -> None:
    """Verification identities use explicit JSON mappings, never positional pairs."""

    payload = _verification_input().payload()
    if payload_field == "model_configuration_by_name_map":
        payload[payload_field] = pair_list
    else:
        payload["checkout_list"][0][payload_field] = pair_list

    with pytest.raises(VerificationReceiptError, match="mapping"):
        VerificationInput.from_payload(payload)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("working_directory", "unsafe\npath"),
        ("model_identity", "gpt-5.6-sol\nother"),
        ("environment_identity", "development\nother"),
        ("release_identity", "release\rother"),
    ],
)
def test_verification_identity_fields_reject_multiline_values(field_name: str, value: str) -> None:
    """Receipt keys cannot hide multiple logical identity values in one text field."""

    payload = _verification_input().payload()
    payload[field_name] = value

    with pytest.raises(VerificationReceiptError, match="single-line"):
        VerificationInput.from_payload(payload)


def test_receipt_normalizes_utc_and_rejects_naive_instant() -> None:
    """Receipt instants preserve the exact UTC moment and reject timezone absence."""

    offset = timezone(timedelta(hours=4))
    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url="https://example.test/evidence",
        evidence_content_sha256=EVIDENCE_ONE,
        completed_at=datetime(2026, 8, 4, 16, 30, tzinfo=offset),
    )
    assert receipt.completed_at == datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)

    with pytest.raises(VerificationReceiptError, match="timezone-aware"):
        VerificationReceipt.from_input(
            _verification_input(),
            outcome="passed",
            evidence_url="https://example.test/evidence",
            evidence_content_sha256=EVIDENCE_ONE,
            completed_at=datetime(2026, 8, 4, 12, 30),
        )
    with pytest.raises(VerificationReceiptError, match="content identity"):
        VerificationReceipt.from_input(
            _verification_input(),
            outcome="passed",
            evidence_url="https://example.test/evidence",
            evidence_content_sha256="not-a-sha256",
        )


def test_receipt_cli_reuses_the_exact_linear_comment_shape(tmp_path: Path) -> None:
    """The CLI consumes the same provider comment body that its create operation emits."""

    script = PLUGIN_ROOT / "skills" / "task-implement" / "scripts" / "receipt.py"
    input_path = tmp_path / "input.json"
    comment_path = tmp_path / "comment.md"
    input_path.write_text(json.dumps(_verification_input().payload()), encoding="utf-8")

    created = subprocess.run(
        [
            str(script),
            "create",
            "--input",
            str(input_path),
            "--outcome",
            "passed",
            "--evidence-url",
            "https://example.test/ci/1",
            "--evidence-content-sha256",
            EVIDENCE_ONE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    comment_path.write_text(created.stdout.rstrip("\n"), encoding="utf-8")
    reused = subprocess.run(
        [
            str(script),
            "reuse",
            "--input",
            str(input_path),
            "--receipt-comment",
            str(comment_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert reused.returncode == 0
    assert json.loads(reused.stdout)["reusable"] is True
    assert created.stdout.startswith("<!-- linear-agent-tools-verification:v2 -->")


def test_receipt_rejects_prior_schema_without_a_compatibility_branch() -> None:
    """Only the current receipt schema and provider marker are accepted."""

    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url="https://example.test/evidence",
        evidence_content_sha256=EVIDENCE_ONE,
    )
    payload = receipt.payload()
    payload["schema_version"] = 1

    with pytest.raises(VerificationReceiptError, match="another shape"):
        VerificationReceipt.from_payload(payload)
    with pytest.raises(VerificationReceiptError, match="another shape"):
        VERIFICATION_RECEIPT_COMMENT_CODEC.payload_parse(
            VERIFICATION_RECEIPT_COMMENT_CODEC.render(receipt.payload()).replace(
                "linear-agent-tools-verification:v2",
                "linear-agent-tools-verification:v1",
                1,
            )
        )


def test_candidate_fingerprint_and_attempt_comment_bind_exact_external_state() -> None:
    """Human approval binds exact sorted heads and concise attempt telemetry round-trips."""

    candidate = CandidateInput(
        delivery_kind="code",
        pull_request_head_by_url_map={"https://github.com/antonov-andrey/example/pull/17": COMMIT_ONE},
        evidence_identity_by_kind_map={},
    )
    summary = AttemptSummary(
        attempt_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        issue_identifier="AND-17",
        role_label="task:implementation",
        delivery_kind="code",
        started_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
        outcome="human-review",
        changed_commit_by_repository_map={"antonov-andrey/example": COMMIT_ONE},
        receipt_hit_count=2,
        receipt_miss_count=1,
        external_wait_seconds=12.5,
        token_count=None,
        candidate_fingerprint=candidate.fingerprint(),
        evidence_url_list=["https://example.test/evidence/17"],
    )

    assert (
        AttemptSummary.from_payload(
            ATTEMPT_COMMENT_CODEC.payload_parse(ATTEMPT_COMMENT_CODEC.render(summary.payload()))
        )
        == summary
    )
    assert "token_count" not in summary.payload()
    assert (
        candidate.fingerprint()
        != CandidateInput(
            delivery_kind="code",
            pull_request_head_by_url_map={"https://github.com/antonov-andrey/example/pull/17": COMMIT_TWO},
            evidence_identity_by_kind_map={},
        ).fingerprint()
    )

    with pytest.raises(VerificationReceiptError, match="canonical GitHub PR"):
        CandidateInput(
            delivery_kind="code",
            pull_request_head_by_url_map={"https://example.test/pull/17": COMMIT_ONE},
            evidence_identity_by_kind_map={},
        )


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ({"candidate_fingerprint": ""}, "candidate fingerprint"),
        ({"outcome": "failed"}, "candidate fingerprint"),
        ({"role_label": "task:review"}, "role and delivery kind"),
        (
            {"role_label": "task:cleanup", "delivery_kind": "cleanup"},
            "role and outcome",
        ),
        ({"evidence_url_list": []}, "bounded evidence links"),
    ],
)
def test_attempt_summary_rejects_role_outcome_candidate_and_evidence_mismatch(
    replacement: dict[str, object],
    message: str,
) -> None:
    """Attempt telemetry cannot claim a semantically impossible role result."""

    argument_by_name: dict[str, object] = {
        "attempt_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "issue_identifier": "AND-17",
        "role_label": "task:implementation",
        "delivery_kind": "code",
        "started_at": datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
        "outcome": "human-review",
        "changed_commit_by_repository_map": {"antonov-andrey/example": COMMIT_ONE},
        "receipt_hit_count": 1,
        "receipt_miss_count": 0,
        "external_wait_seconds": 0.0,
        "token_count": None,
        "candidate_fingerprint": "f" * 64,
        "evidence_url_list": ["https://example.test/evidence/17"],
    }
    argument_by_name.update(replacement)

    with pytest.raises(VerificationReceiptError, match=message):
        AttemptSummary(**argument_by_name)


def test_attempt_summary_rejects_commits_for_evidence_delivery() -> None:
    """Evidence-only implementation telemetry cannot claim Product mutations."""

    with pytest.raises(VerificationReceiptError, match="Non-code attempt"):
        AttemptSummary(
            attempt_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            issue_identifier="AND-17",
            role_label="task:implementation",
            delivery_kind="evidence",
            started_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
            outcome="failed",
            changed_commit_by_repository_map={"antonov-andrey/example": COMMIT_ONE},
            receipt_hit_count=0,
            receipt_miss_count=1,
            external_wait_seconds=0.0,
            token_count=None,
            candidate_fingerprint="",
            evidence_url_list=[],
        )


def test_local_phase_baseline_requires_every_phase_and_round_trips() -> None:
    """The local acceptance baseline has one complete fixed phase set."""

    baseline = LocalPhaseBaseline(
        project_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        source_fingerprint="c" * 64,
        candidate_fingerprint="d" * 64,
        measured_at=datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc),
        duration_seconds_by_phase_map={
            "execution": 120.0,
            "merge": 10.0,
            "queue": 4.0,
            "review": 30.0,
            "startup": 2.0,
        },
        evidence_url="https://linear.app/example/project/acceptance",
    )

    assert (
        LocalPhaseBaseline.from_payload(
            LOCAL_PHASE_BASELINE_COMMENT_CODEC.payload_parse(
                LOCAL_PHASE_BASELINE_COMMENT_CODEC.render(baseline.payload())
            )
        )
        == baseline
    )
    with pytest.raises(VerificationReceiptError, match="queue, startup, execution, review and merge"):
        LocalPhaseBaseline(
            project_id=baseline.project_id,
            source_fingerprint=baseline.source_fingerprint,
            candidate_fingerprint=baseline.candidate_fingerprint,
            measured_at=baseline.measured_at,
            duration_seconds_by_phase_map={"execution": 1.0},
            evidence_url=baseline.evidence_url,
        )


def test_task_workspace_baseline_is_deterministic_linear_evidence() -> None:
    """First dispatch publishes the exact branch and repository baselines once."""

    baseline = TaskWorkspaceBaseline(
        issue_identifier="AND-17",
        source_fingerprint="c" * 64,
        branch_name="linear/and-17",
        baseline_commit_by_repository_url_map={"git@github.com:antonov-andrey/example.git": COMMIT_ONE},
    )

    assert (
        TaskWorkspaceBaseline.from_payload(
            TASK_WORKSPACE_BASELINE_COMMENT_CODEC.payload_parse(
                TASK_WORKSPACE_BASELINE_COMMENT_CODEC.render(baseline.payload())
            )
        )
        == baseline
    )
    with pytest.raises(VerificationReceiptError, match="branch differs"):
        TaskWorkspaceBaseline(
            issue_identifier=baseline.issue_identifier,
            source_fingerprint=baseline.source_fingerprint,
            branch_name="linear/and-18",
            baseline_commit_by_repository_url_map=baseline.baseline_commit_by_repository_url_map,
        )


def test_shared_evidence_cli_renders_candidate_without_persistent_state(
    tmp_path: Path,
) -> None:
    """Every role can use one deterministic owner CLI for candidate evidence."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"
    input_path = tmp_path / "candidate.json"
    candidate = CandidateInput(
        delivery_kind="evidence",
        pull_request_head_by_url_map={},
        evidence_identity_by_kind_map={"acceptance": "sha256:" + "e" * 64},
    )
    input_path.write_text(json.dumps(candidate.payload()), encoding="utf-8")

    rendered = subprocess.run(
        [sys.executable, str(script), "candidate", "--input", str(input_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(rendered.stdout)
    assert payload["candidate_fingerprint"] == candidate.fingerprint()
    assert payload["input"] == candidate.payload()


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
        self.command_list.append(list(argument_list))
        if argument_list[1:3] == ["pr", "checks"]:
            return subprocess.CompletedProcess(
                argument_list,
                8 if self.check_bucket == "pending" else 0,
                json.dumps(
                    [
                        {
                            "name": "test",
                            "bucket": self.check_bucket,
                            "link": "https://example.test/check",
                        }
                    ]
                ),
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
                "https://github.com/antonov-andrey/example/pull/17\n",
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
                "url": "https://github.com/antonov-andrey/example/pull/17",
                "title": "AND-17 Implement exact owner",
                "state": self.state,
                "isDraft": False,
                "baseRefName": self.base_branch,
                "headRefName": "linear/and-17",
                "headRefOid": self.head_commit,
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "APPROVED",
                "mergedAt": "2026-08-04T12:30:00Z" if self.state == "MERGED" else None,
                "mergeCommit": {"oid": COMMIT_TWO} if self.state == "MERGED" else None,
            }
            return subprocess.CompletedProcess(argument_list, 0, json.dumps(payload), "")
        raise AssertionError(f"Unexpected gh command: {argument_list}")


def test_github_merge_binds_exact_human_approved_head_and_required_checks() -> None:
    """The merge boundary uses --match-head-commit and verifies merged state."""

    runner = _GhRunner()
    boundary = GitHubPullRequestBoundary(runner)
    repository = RepositoryIdentity("antonov-andrey/example")

    merged = boundary.merge(
        repository=repository,
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        approved_head_commit=COMMIT_ONE,
        merge_method="merge",
    )

    assert merged.state == "MERGED"
    assert merged.merge_commit == COMMIT_TWO
    merge_command = next(item for item in runner.command_list if item[1:3] == ["pr", "merge"])
    assert merge_command[-2:] == ["--match-head-commit", COMMIT_ONE]


def test_github_merge_retry_adopts_exact_already_merged_candidate() -> None:
    """A crash after provider merge is recovered by exact merged-result read-back."""

    runner = _GhRunner()
    runner.state = "MERGED"
    boundary = GitHubPullRequestBoundary(runner)

    merged = boundary.merge(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        issue_identifier="AND-17",
        base_branch="main",
        head_branch="linear/and-17",
        approved_head_commit=COMMIT_ONE,
        merge_method="merge",
    )

    assert merged.state == "MERGED"
    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_github_pr_create_is_idempotent_for_exact_issue_branch(tmp_path: Path) -> None:
    """A retry adopts the one exact open PR instead of creating a duplicate."""

    runner = _GhRunner()
    boundary = GitHubPullRequestBoundary(runner)
    body = tmp_path / "body.md"
    body.write_text("# Candidate\n", encoding="utf-8")
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
    assert "--jq" not in lookup_command


@pytest.mark.parametrize(
    "payload",
    (
        [{"number": 17, "base": {"ref": "main"}, "head": {"ref": "linear/and-17"}}],
        [
            [
                {
                    "number": 17,
                    "base": {"ref": "release"},
                    "head": {"ref": "linear/and-17"},
                }
            ]
        ],
        [
            [{"number": 17, "base": {"ref": "main"}, "head": {"ref": "linear/and-17"}}],
            [{"number": 17, "base": {"ref": "main"}, "head": {"ref": "linear/and-17"}}],
        ],
    ),
)
def test_github_pr_lookup_rejects_malformed_or_conflicting_pages(
    payload: object,
) -> None:
    """Native paginated output cannot weaken exact PR identity or uniqueness."""

    def runner(argument_list: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argument_list, 0, json.dumps(payload), "")

    with pytest.raises(GitHubContractError, match="lookup"):
        GitHubPullRequestBoundary(runner).matching_number_list(
            repository=RepositoryIdentity("antonov-andrey/example"),
            base_branch="main",
            head_branch="linear/and-17",
        )


def test_github_pr_title_requires_exact_linear_identifier_token(tmp_path: Path) -> None:
    """A substring embedded in another token is not integration-compatible identity."""

    body = tmp_path / "body.md"
    body.write_text("# Candidate\n", encoding="utf-8")
    runner = _GhRunner()

    with pytest.raises(GitHubContractError, match="exact Linear issue token"):
        GitHubPullRequestBoundary(runner).create(
            repository=RepositoryIdentity("antonov-andrey/example"),
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            title="XAND-17Y is not the issue token",
            body_file=body,
        )

    assert not runner.command_list


def test_github_merge_rejects_candidate_mutation_before_external_merge() -> None:
    """A changed PR head forces Rework and no merge call occurs."""

    runner = _GhRunner(head_commit=COMMIT_TWO)
    boundary = GitHubPullRequestBoundary(runner)

    with pytest.raises(GitHubContractError, match="changed after human approval"):
        boundary.merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            approved_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_github_merge_reads_gh_check_bucket_and_rejects_pending_required_check() -> None:
    """gh exit 8 remains an inspectable pending check, never an allowed merge."""

    runner = _GhRunner()
    runner.check_bucket = "pending"
    boundary = GitHubPullRequestBoundary(runner)

    with pytest.raises(GitHubContractError, match="not passing"):
        boundary.merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            approved_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    check_command = next(item for item in runner.command_list if item[1:3] == ["pr", "checks"])
    assert check_command[-1] == "name,bucket,link"
    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_github_merge_rejects_wrong_base_before_external_merge() -> None:
    """A PR aimed at another base cannot be merged before the target mismatch is detected."""

    runner = _GhRunner(base_branch="release")
    boundary = GitHubPullRequestBoundary(runner)

    with pytest.raises(GitHubContractError, match="base or head differs"):
        boundary.merge(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
            approved_head_commit=COMMIT_ONE,
            merge_method="merge",
        )

    assert not any(item[1:3] == ["pr", "merge"] for item in runner.command_list)


def test_canceled_pull_request_close_is_idempotent() -> None:
    """An open linked PR closes once while terminal read-back is accepted."""

    runner = _GhRunner()
    boundary = GitHubPullRequestBoundary(runner)
    repository = RepositoryIdentity("antonov-andrey/example")

    arguments = {
        "repository": repository,
        "number": 17,
        "issue_identifier": "AND-17",
        "base_branch": "main",
        "head_branch": "linear/and-17",
    }
    first = boundary.close_if_open(**arguments)
    second = boundary.close_if_open(**arguments)

    assert first.state == "CLOSED"
    assert second.state == "CLOSED"
    assert sum(item[1:3] == ["pr", "close"] for item in runner.command_list) == 1


def test_canceled_pull_request_close_rejects_foreign_target_before_mutation() -> None:
    """Cancellation cannot close another PR from the same participating repository."""

    runner = _GhRunner(base_branch="release")
    boundary = GitHubPullRequestBoundary(runner)

    with pytest.raises(GitHubContractError, match="base or head differs"):
        boundary.close_if_open(
            repository=RepositoryIdentity("antonov-andrey/example"),
            number=17,
            issue_identifier="AND-17",
            base_branch="main",
            head_branch="linear/and-17",
        )

    assert not any(item[1:3] == ["pr", "close"] for item in runner.command_list)
