"""Behavior tests for evidence reuse and exact GitHub candidate binding."""

from __future__ import annotations

from dataclasses import replace
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
from verification.candidate import CandidateIdentity, CandidateInput
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
CONTRACT_ONE = "5" * 64
CONTRACT_TWO = "6" * 64
CORPUS_ONE = "1" * 64
CORPUS_TWO = "2" * 64
EVIDENCE_ONE = "3" * 64
EVIDENCE_TWO = "4" * 64
LINEAR_ATTACHMENT_URL = "https://uploads.linear.app/workspace/asset/artifact"
LOCK_ONE = "c" * 64
LOCK_TWO = "d" * 64


def _verification_input(
    *,
    commit: str = COMMIT_ONE,
    lock: str = LOCK_ONE,
    environment: str = "development:release-one",
    contract: str = CONTRACT_ONE,
    corpus: str = CORPUS_ONE,
    model: str = "gpt-5.6-sol",
    reasoning_effort: str = "medium",
) -> VerificationInput:
    """Return one complete deterministic verification input.

    Args:
        commit: Repository commit.
        lock: Dependency lock fingerprint.
        environment: Exact external environment identity.
        contract: Exact semantic verification contract identity.
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
        verification_contract_fingerprint=contract,
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
    changed_evidence = VerificationReceipt.from_input(
        current,
        outcome="passed",
        evidence_url=LINEAR_ATTACHMENT_URL,
        evidence_content_sha256=EVIDENCE_TWO,
        completed_at=receipt.completed_at,
    )
    assert changed_evidence.verification_key == receipt.verification_key
    assert changed_evidence.receipt_key != receipt.receipt_key
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
    changed_contract = ReceiptReuseEvaluator(_verification_input(contract=CONTRACT_TWO)).decision_get(parsed)
    assert changed_contract.reason_list == ["verification-contract-changed"]


@pytest.mark.parametrize("value", [None, False, 0, [], {}])
def test_verification_input_rejects_non_string_corpus_identity(value: object) -> None:
    """The empty corpus identity is one explicit string, not another falsy JSON type."""

    current = _verification_input()
    payload = current.payload()
    payload["corpus_content_sha256"] = value

    with pytest.raises(VerificationReceiptError, match="empty or SHA-256 text"):
        VerificationInput.from_payload(payload)
    with pytest.raises(VerificationReceiptError, match="empty or SHA-256 text"):
        replace(current, corpus_content_sha256=value)


@pytest.mark.parametrize("value", ["", "not-a-sha256", None, False, 0])
def test_verification_input_requires_semantic_contract_fingerprint(value: object) -> None:
    """Receipt reuse cannot outlive the prompt, expectations, invariants, or schema it verifies."""

    payload = _verification_input().payload()
    payload["verification_contract_fingerprint"] = value

    with pytest.raises(VerificationReceiptError, match="contract fingerprint must be SHA-256"):
        VerificationInput.from_payload(payload)


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://token@github.com/antonov-andrey/example.git",
        "https://token:secret@github.com/antonov-andrey/example.git",
        "https://github.com/antonov-andrey/example.git?token=secret",
        "relative-repository",
    ],
)
def test_verification_checkout_rejects_unsafe_repository_url_without_echo(repository_url: str) -> None:
    """Receipt construction rejects secret-bearing or unsupported Git origins without reflecting them."""

    with pytest.raises(VerificationReceiptError, match="unsafe or unsupported") as error:
        replace(_verification_input().checkout_list[0], repository_url=repository_url)

    assert "token" not in str(error.value)
    assert "secret" not in str(error.value)


def test_shared_evidence_cli_rejects_credential_bearing_repository_without_echo(tmp_path: Path) -> None:
    """The public receipt boundary rejects a repository secret before rendering any comment."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"
    input_path = tmp_path / "input.json"
    payload = _verification_input().payload()
    payload["checkout_list"][0]["repository_url"] = "https://token:secret@github.com/example/repository.git"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    created = subprocess.run(
        [
            str(script),
            "receipt-create",
            "--input",
            str(input_path),
            "--outcome",
            "passed",
            "--completed-at",
            "2026-08-04T12:30:00Z",
            "--evidence-url",
            LINEAR_ATTACHMENT_URL,
            "--evidence-content-sha256",
            EVIDENCE_ONE,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert created.returncode == 2
    assert created.stdout == ""
    assert "unsafe or unsupported" in created.stderr
    assert "token" not in created.stderr
    assert "secret" not in created.stderr


def test_external_evidence_receipt_can_bind_source_without_a_repository_commit() -> None:
    """A source-independent provider probe remains reusable only for its exact source."""

    value = VerificationInput(
        command_argument_list=["linear-provider-probe"],
        working_directory="/workspace",
        source_fingerprint="f" * 64,
        verification_contract_fingerprint=CONTRACT_ONE,
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
        (("working_directory",), "//workspace/example/.worktree/and-17", "absolute POSIX"),
        (("checkout_list", 0, "path"), "/workspace/../example", "absolute POSIX"),
        (("checkout_list", 0, "path"), "//workspace/example/.worktree/and-17", "absolute POSIX"),
        (
            ("checkout_list", 0, "recursive_submodule_commit_by_path_map"),
            {"../provider": COMMIT_ONE},
            "repository-relative POSIX",
        ),
        (
            ("checkout_list", 0, "recursive_submodule_commit_by_path_map"),
            {"module//provider": COMMIT_ONE},
            "repository-relative POSIX",
        ),
        (
            ("checkout_list", 0, "dependency_lock_sha256_by_path_map"),
            {"/workspace/requirements-dev.txt": LOCK_ONE},
            "repository-relative POSIX",
        ),
        (
            ("checkout_list", 0, "dependency_lock_sha256_by_path_map"),
            {"config/./requirements-dev.txt": LOCK_ONE},
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


def test_verification_models_reject_double_root_path_substitution_directly() -> None:
    """Direct model construction cannot create a second identity for one Linux path."""

    current = _verification_input()

    with pytest.raises(VerificationReceiptError, match="absolute POSIX"):
        replace(current, working_directory="//workspace/example/.worktree/and-17")
    with pytest.raises(VerificationReceiptError, match="absolute POSIX"):
        replace(current.checkout_list[0], path="//workspace/example/.worktree/and-17")


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


@pytest.mark.parametrize(
    ("evidence_url", "evidence_content_sha256"),
    [
        ("https://attacker.invalid/evidence", EVIDENCE_ONE),
        (LINEAR_ATTACHMENT_URL, EVIDENCE_TWO),
        ("https://attacker.invalid/evidence", EVIDENCE_TWO),
    ],
)
def test_receipt_rejects_evidence_identity_substitution(
    evidence_url: str,
    evidence_content_sha256: str,
) -> None:
    """URL-only, SHA-only, and combined substitutions invalidate the issued receipt key."""

    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url=LINEAR_ATTACHMENT_URL,
        evidence_content_sha256=EVIDENCE_ONE,
        completed_at=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
    )
    payload = receipt.payload()
    payload["evidence_url"] = evidence_url
    payload["evidence_content_sha256"] = evidence_content_sha256

    with pytest.raises(VerificationReceiptError, match="receipt key differs"):
        VerificationReceipt.from_payload(payload)
    with pytest.raises(VerificationReceiptError, match="receipt key differs"):
        replace(
            receipt,
            evidence_url=evidence_url,
            evidence_content_sha256=evidence_content_sha256,
        )


@pytest.mark.parametrize(
    "evidence_url",
    [
        LINEAR_ATTACHMENT_URL + "?signature=short-lived",
        LINEAR_ATTACHMENT_URL + "#download",
        LINEAR_ATTACHMENT_URL + "?",
        LINEAR_ATTACHMENT_URL + "#",
        "http://uploads.linear.app/workspace/asset/artifact",
        "HTTPS://uploads.linear.app/workspace/asset/artifact",
        "https://uploads.linear.app./workspace/asset/artifact",
        "https://user@uploads.linear.app/workspace/asset/artifact",
        "https://uploads.linear.app:443/workspace/asset/artifact",
        "https://uploads.linear.app/workspace/../asset/artifact",
        "https://uploads.linear.app/workspace/asset/not an artifact",
        "https://uploads.linear.app/workspace/asset/%61rtifact",
        "https://uploads.linear.app/workspace/asset/%2fartifact",
        "https://uploads.linear.app/workspace/asset/%ZZ",
        "https://uploads.linear.app/workspace/asset/artifact\\download",
        "https://uploads.linear.app/workspace/asset/artifact\tother",
        "https://uploads.linear.app/workspace/asset/артефакт",
        "https://127.1/workspace/asset/artifact",
        "https://0177.0.0.1/workspace/asset/artifact",
        "https://2130706433/workspace/asset/artifact",
        "https://0x7f000001/workspace/asset/artifact",
        " https://uploads.linear.app/workspace/asset/artifact",
    ],
)
def test_receipt_rejects_noncanonical_evidence_url(evidence_url: str) -> None:
    """A receipt never stores an expiring or authority-ambiguous artifact URL."""

    with pytest.raises(VerificationReceiptError, match="canonical HTTPS provider URL"):
        VerificationReceipt.from_input(
            _verification_input(),
            outcome="passed",
            evidence_url=evidence_url,
            evidence_content_sha256=EVIDENCE_ONE,
        )


def test_receipt_accepts_exact_canonical_percent_encoded_path() -> None:
    """A canonical encoded reserved path byte remains one exact artifact identity."""

    evidence_url = LINEAR_ATTACHMENT_URL + "%2Fidentity"
    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url=evidence_url,
        evidence_content_sha256=EVIDENCE_ONE,
    )

    assert receipt.evidence_url == evidence_url


@pytest.mark.parametrize(
    "evidence_url",
    [
        "https://uploads.linear.app/workspace/asset/artifact",
        "https://123.example.test/workspace/asset/artifact",
        "https://0x7f.example.test/workspace/asset/artifact",
        "https://127.0.0.1/workspace/asset/artifact",
    ],
)
def test_receipt_accepts_canonical_dns_and_ipv4_provider_hosts(evidence_url: str) -> None:
    """Numeric-label DNS and canonical dotted IPv4 remain explicit provider identities."""

    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url=evidence_url,
        evidence_content_sha256=EVIDENCE_ONE,
    )

    assert receipt.evidence_url == evidence_url


@pytest.mark.parametrize(
    "evidence_url",
    [
        LINEAR_ATTACHMENT_URL + "?",
        LINEAR_ATTACHMENT_URL + "#",
        "https://uploads.linear.app/workspace/asset/not an artifact",
        "https://127.1/workspace/asset/artifact",
        "https://0177.0.0.1/workspace/asset/artifact",
        "https://2130706433/workspace/asset/artifact",
        "https://0x7f000001/workspace/asset/artifact",
        "https://4294967296/workspace/asset/artifact",
        "https://999999999999999999999/workspace/asset/artifact",
        "https://1.2.3.999/workspace/asset/artifact",
        "https://1.2.3.4.5/workspace/asset/artifact",
        "https://0x100000000/workspace/asset/artifact",
        "https://0xffffffffffffffff/workspace/asset/artifact",
    ],
)
def test_receipt_comment_parse_rejects_noncanonical_evidence_url(evidence_url: str) -> None:
    """A provider comment cannot restore a malformed artifact identity as one receipt."""

    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url=LINEAR_ATTACHMENT_URL,
        evidence_content_sha256=EVIDENCE_ONE,
    )
    payload = receipt.payload()
    payload["evidence_url"] = evidence_url
    rendered = VERIFICATION_RECEIPT_COMMENT_CODEC.render(payload)

    with pytest.raises(VerificationReceiptError, match="canonical HTTPS provider URL"):
        VerificationReceipt.from_payload(VERIFICATION_RECEIPT_COMMENT_CODEC.payload_parse(rendered))


def test_receipt_comment_protects_canonical_evidence_url_from_provider_rewrite() -> None:
    """JSON slash escapes preserve the URL value without exposing one Linear autolink target."""

    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url=LINEAR_ATTACHMENT_URL,
        evidence_content_sha256=EVIDENCE_ONE,
    )
    rendered = VERIFICATION_RECEIPT_COMMENT_CODEC.render(receipt.payload())

    assert LINEAR_ATTACHMENT_URL not in rendered
    assert r"https:\/\/uploads.linear.app\/workspace\/asset\/artifact" in rendered
    assert VerificationReceipt.from_payload(VERIFICATION_RECEIPT_COMMENT_CODEC.payload_parse(rendered)) == receipt


def test_shared_evidence_cli_is_directly_executable() -> None:
    """The shared replacement CLI launches through its documented direct path."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"

    result = subprocess.run([str(script), "--help"], check=False, capture_output=True, text=True)

    assert result.returncode == 0
    assert "receipt-create" in result.stdout
    assert result.stderr == ""


def test_shared_evidence_cli_creates_and_reuses_the_exact_linear_comment_shape(tmp_path: Path) -> None:
    """Every workflow role uses the shared owner for codec creation and provider readback."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"
    input_path = tmp_path / "input.json"
    comment_path = tmp_path / "comment.md"
    input_path.write_text(json.dumps(_verification_input().payload()), encoding="utf-8")

    created = subprocess.run(
        [
            str(script),
            "receipt-create",
            "--input",
            str(input_path),
            "--outcome",
            "passed",
            "--completed-at",
            "2026-08-04T12:30:00Z",
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
            "receipt-reuse",
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
    assert created.stdout.startswith("<!-- linear-agent-tools-verification:v4 -->")
    assert "https://example.test/ci/1" not in created.stdout
    assert r"https:\/\/example.test\/ci\/1" in created.stdout


@pytest.mark.parametrize(
    "evidence_url",
    [
        LINEAR_ATTACHMENT_URL + "?",
        LINEAR_ATTACHMENT_URL + "#",
        "https://uploads.linear.app/workspace/asset/not an artifact",
        "https://127.1/workspace/asset/artifact",
        "https://0177.0.0.1/workspace/asset/artifact",
        "https://2130706433/workspace/asset/artifact",
        "https://0x7f000001/workspace/asset/artifact",
        "https://4294967296/workspace/asset/artifact",
        "https://1.2.3.999/workspace/asset/artifact",
        "https://1.2.3.4.5/workspace/asset/artifact",
        "https://0xffffffffffffffff/workspace/asset/artifact",
    ],
)
def test_shared_evidence_cli_rejects_noncanonical_evidence_url(tmp_path: Path, evidence_url: str) -> None:
    """The shared evidence owner refuses to issue a receipt for a malformed URL."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(_verification_input().payload()), encoding="utf-8")

    created = subprocess.run(
        [
            sys.executable,
            str(script),
            "receipt-create",
            "--input",
            str(input_path),
            "--outcome",
            "passed",
            "--completed-at",
            "2026-08-04T12:30:00Z",
            "--evidence-url",
            evidence_url,
            "--evidence-content-sha256",
            EVIDENCE_ONE,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert created.returncode == 2
    assert "canonical HTTPS provider URL" in created.stderr
    assert "linear-agent-tools-verification:v4" not in created.stdout


@pytest.mark.parametrize(
    ("input_working_directory", "comment_evidence_url"),
    [
        ("/workspace/example/.worktree/and-17", LINEAR_ATTACHMENT_URL + "?"),
        ("/workspace/example/.worktree/and-17", LINEAR_ATTACHMENT_URL + "#"),
        ("//workspace/example/.worktree/and-17", LINEAR_ATTACHMENT_URL),
    ],
)
def test_shared_evidence_cli_reuse_rejects_noncanonical_identity_substitution(
    tmp_path: Path,
    input_working_directory: str,
    comment_evidence_url: str,
) -> None:
    """Reuse fails closed when comment or current path identity has another spelling."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"
    input_path = tmp_path / "input.json"
    comment_path = tmp_path / "comment.md"
    input_payload = _verification_input().payload()
    input_payload["working_directory"] = input_working_directory
    input_path.write_text(json.dumps(input_payload), encoding="utf-8")
    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url=LINEAR_ATTACHMENT_URL,
        evidence_content_sha256=EVIDENCE_ONE,
    )
    receipt_payload = receipt.payload()
    receipt_payload["evidence_url"] = comment_evidence_url
    comment_path.write_text(VERIFICATION_RECEIPT_COMMENT_CODEC.render(receipt_payload), encoding="utf-8")

    reused = subprocess.run(
        [
            sys.executable,
            str(script),
            "receipt-reuse",
            "--input",
            str(input_path),
            "--receipt-comment",
            str(comment_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert reused.returncode == 2
    assert "reusable" not in reused.stdout


def test_receipt_rejects_prior_schema_without_a_compatibility_branch() -> None:
    """Only the current receipt schema and provider marker are accepted."""

    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        evidence_url="https://example.test/evidence",
        evidence_content_sha256=EVIDENCE_ONE,
    )
    payload = receipt.payload()
    payload["schema_version"] = 3

    with pytest.raises(VerificationReceiptError, match="another shape"):
        VerificationReceipt.from_payload(payload)
    with pytest.raises(VerificationReceiptError, match="another shape"):
        VERIFICATION_RECEIPT_COMMENT_CODEC.payload_parse(
            VERIFICATION_RECEIPT_COMMENT_CODEC.render(receipt.payload()).replace(
                "linear-agent-tools-verification:v4",
                "linear-agent-tools-verification:v3",
                1,
            )
        )


def test_candidate_fingerprint_and_attempt_comment_bind_exact_external_state() -> None:
    """Human approval binds exact sorted heads and concise attempt telemetry round-trips."""

    candidate = CandidateInput(
        delivery_kind="code",
        pull_request_head_by_url_map={"https://github.com/antonov-andrey/example/pull/17": COMMIT_ONE},
        evidence_receipt_by_kind_map={},
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
        candidate_identity=candidate.identity_get(),
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
            evidence_receipt_by_kind_map={},
        ).fingerprint()
    )

    with pytest.raises(VerificationReceiptError, match="canonical GitHub PR"):
        CandidateInput(
            delivery_kind="code",
            pull_request_head_by_url_map={"https://example.test/pull/17": COMMIT_ONE},
            evidence_receipt_by_kind_map={},
        )


def test_evidence_candidate_uses_validated_receipt_keys_for_every_result_identity() -> None:
    """Receipt transitions invalidate approval, while only passed evidence is eligible."""

    verification_input = _verification_input()
    completed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    passed_receipt_list = [
        VerificationReceipt.from_input(
            verification_input,
            outcome="passed",
            completed_at=instant,
            evidence_url=evidence_url,
            evidence_content_sha256=evidence_sha256,
        )
        for instant, evidence_url, evidence_sha256 in (
            (completed_at, LINEAR_ATTACHMENT_URL, EVIDENCE_ONE),
            (completed_at + timedelta(seconds=1), LINEAR_ATTACHMENT_URL, EVIDENCE_ONE),
            (completed_at, LINEAR_ATTACHMENT_URL + "-two", EVIDENCE_ONE),
            (completed_at, LINEAR_ATTACHMENT_URL, EVIDENCE_TWO),
        )
    ]
    candidate_list = [
        CandidateInput(
            delivery_kind="evidence",
            pull_request_head_by_url_map={},
            evidence_receipt_by_kind_map={"acceptance": receipt},
        )
        for receipt in passed_receipt_list
    ]
    failed_receipt = VerificationReceipt.from_input(
        verification_input,
        outcome="failed",
        completed_at=completed_at,
        evidence_url=LINEAR_ATTACHMENT_URL,
        evidence_content_sha256=EVIDENCE_ONE,
    )

    assert {receipt.verification_key for receipt in [*passed_receipt_list, failed_receipt]} == {
        verification_input.key()
    }
    assert failed_receipt.receipt_key != passed_receipt_list[0].receipt_key
    assert len({receipt.receipt_key for receipt in passed_receipt_list}) == len(passed_receipt_list)
    assert len({candidate.fingerprint() for candidate in candidate_list}) == len(candidate_list)
    for candidate, receipt in zip(candidate_list, passed_receipt_list, strict=True):
        assert candidate.identity_get().evidence_receipt_key_by_kind_map == {"acceptance": receipt.receipt_key}
    with pytest.raises(VerificationReceiptError, match="passed outcome"):
        CandidateInput(
            delivery_kind="evidence",
            pull_request_head_by_url_map={},
            evidence_receipt_by_kind_map={"acceptance": failed_receipt},
        )


def test_attempt_comment_persists_and_validates_complete_evidence_candidate_identity() -> None:
    """Fresh review reconstructs the exact evidence-kind-to-receipt-key map from Linear telemetry."""

    completed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    candidate = CandidateInput(
        delivery_kind="evidence",
        pull_request_head_by_url_map={},
        evidence_receipt_by_kind_map={
            "acceptance": VerificationReceipt.from_input(
                _verification_input(),
                outcome="passed",
                completed_at=completed_at,
                evidence_url=LINEAR_ATTACHMENT_URL,
                evidence_content_sha256=EVIDENCE_ONE,
            ),
            "semantic-review": VerificationReceipt.from_input(
                _verification_input(),
                outcome="passed",
                completed_at=completed_at + timedelta(seconds=1),
                evidence_url=LINEAR_ATTACHMENT_URL + "-review",
                evidence_content_sha256=EVIDENCE_TWO,
            ),
        },
    )
    summary = AttemptSummary(
        attempt_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        issue_identifier="AND-17",
        role_label="task:review",
        delivery_kind="evidence",
        started_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        completed_at=completed_at + timedelta(seconds=2),
        outcome="human-review",
        changed_commit_by_repository_map={},
        receipt_hit_count=0,
        receipt_miss_count=2,
        external_wait_seconds=0.0,
        token_count=None,
        candidate_identity=candidate.identity_get(),
        candidate_fingerprint=candidate.fingerprint(),
        evidence_url_list=["https://linear.app/example/issue/AND-17"],
    )
    roundtrip_payload = ATTEMPT_COMMENT_CODEC.payload_parse(ATTEMPT_COMMENT_CODEC.render(summary.payload()))

    assert AttemptSummary.from_payload(roundtrip_payload) == summary
    assert roundtrip_payload["candidate_identity"] == candidate.identity_get().payload()
    roundtrip_payload["candidate_identity"]["evidence_receipt_key_by_kind_map"].pop("semantic-review")
    with pytest.raises(VerificationReceiptError, match="fingerprint differs"):
        AttemptSummary.from_payload(roundtrip_payload)


def test_evidence_candidate_rejects_verification_key_substitution_and_prior_shape() -> None:
    """A stable reuse key cannot masquerade as receipt-bearing approval evidence."""

    receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="passed",
        completed_at=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
        evidence_url=LINEAR_ATTACHMENT_URL,
        evidence_content_sha256=EVIDENCE_ONE,
    )
    candidate = CandidateInput(
        delivery_kind="evidence",
        pull_request_head_by_url_map={},
        evidence_receipt_by_kind_map={"acceptance": receipt},
    )
    substituted_payload = candidate.payload()
    substituted_payload["evidence_receipt_by_kind_map"]["acceptance"]["receipt_key"] = receipt.verification_key

    with pytest.raises(VerificationReceiptError, match="receipt key differs from its exact result"):
        CandidateInput.from_payload(substituted_payload)
    with pytest.raises(VerificationReceiptError, match="current receipt schema"):
        CandidateInput(
            delivery_kind="evidence",
            pull_request_head_by_url_map={},
            evidence_receipt_by_kind_map={"acceptance": receipt.verification_key},
        )

    prior_payload = candidate.payload()
    prior_payload["evidence_identity_by_kind_map"] = {
        "acceptance": prior_payload.pop("evidence_receipt_by_kind_map")["acceptance"]["verification_key"]
    }
    with pytest.raises(VerificationReceiptError, match="another shape"):
        CandidateInput.from_payload(prior_payload)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ({"candidate_fingerprint": ""}, "candidate fingerprint"),
        ({"outcome": "failed"}, "candidate identity"),
        ({"candidate_identity": None}, "candidate identity"),
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

    candidate_identity = CandidateIdentity(
        delivery_kind="code",
        evidence_receipt_key_by_kind_map={},
        pull_request_head_by_url_map={"https://github.com/antonov-andrey/example/pull/17": COMMIT_ONE},
    )
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
        "candidate_identity": candidate_identity,
        "candidate_fingerprint": candidate_identity.fingerprint(),
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
            candidate_identity=None,
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
        evidence_receipt_by_kind_map={
            "acceptance": VerificationReceipt.from_input(
                _verification_input(),
                outcome="passed",
                evidence_url=LINEAR_ATTACHMENT_URL,
                evidence_content_sha256=EVIDENCE_ONE,
                completed_at=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
            )
        },
    )
    input_path.write_text(json.dumps(candidate.payload()), encoding="utf-8")

    rendered = subprocess.run(
        [sys.executable, str(script), "candidate", "--input", str(input_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(rendered.stdout)
    assert set(payload) == {"candidate_fingerprint", "candidate_identity", "input", "schema_version"}
    assert payload["schema_version"] == 3
    assert payload["candidate_fingerprint"] == candidate.fingerprint()
    assert payload["candidate_identity"] == candidate.identity_get().payload()
    assert payload["candidate_identity"]["evidence_receipt_key_by_kind_map"] == {
        "acceptance": candidate.evidence_receipt_by_kind_map["acceptance"].receipt_key
    }
    assert payload["input"] == candidate.payload()


def test_shared_evidence_cli_rejects_failed_receipt_candidate(tmp_path: Path) -> None:
    """A failed result cannot cross the shared Human Review candidate boundary."""

    script = LIBRARY_ROOT / "verification" / "tool" / "evidence.py"
    input_path = tmp_path / "candidate.json"
    failed_receipt = VerificationReceipt.from_input(
        _verification_input(),
        outcome="failed",
        evidence_url=LINEAR_ATTACHMENT_URL,
        evidence_content_sha256=EVIDENCE_ONE,
        completed_at=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
    )
    input_path.write_text(
        json.dumps(
            {
                "delivery_kind": "evidence",
                "evidence_receipt_by_kind_map": {"acceptance": failed_receipt.payload()},
                "pull_request_head_by_url_map": {},
            }
        ),
        encoding="utf-8",
    )

    rendered = subprocess.run(
        [sys.executable, str(script), "candidate", "--input", str(input_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert rendered.returncode == 2
    assert "passed outcome" in rendered.stderr
    assert rendered.stdout == ""


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
