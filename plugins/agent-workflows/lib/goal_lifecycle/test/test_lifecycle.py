"""Behavior tests for tracked goal coordination and lifecycle transactions."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

LIBRARY_ROOT = Path(__file__).resolve().parents[2]
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

import goal_lifecycle.delete as delete_module
import goal_lifecycle.merge as merge_module
import goal_lifecycle.worktree as worktree_module
from goal_lifecycle.checkpoint import GoalCheckpointPublisher
from goal_lifecycle.bootstrap_exception import (
    coordination_bootstrap_exception_path_get,
    coordination_bootstrap_exception_write,
)
from goal_lifecycle.cleanup_manifest import (
    bootstrap_manifest_load,
    cleanup_binding_receipt_path_get,
    cleanup_binding_receipt_validate,
)
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.delete import GoalDeletionWorkflow
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.merge import GoalMergeWorkflow
from goal_lifecycle.model import CheckpointDocument
from goal_lifecycle.worktree import GoalWorktreeWorkflow
from goal_lifecycle.yaml_document import yaml_document_load

PREFIX = "2026-08-01-test-goal"


def _git(repository: Path, *argument_list: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *argument_list],
        check=True,
        capture_output=True,
        input=input_text,
        text=True,
    )
    return result.stdout.strip()


def _git_returncode(repository: Path, *argument_list: str) -> int:
    return subprocess.run(
        ["git", "-C", str(repository), *argument_list],
        check=False,
        capture_output=True,
    ).returncode


def _repository_create(workspace: Path, name: str) -> tuple[Path, Path]:
    remote = workspace / f"{name}.git"
    root = workspace / name
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "Initial")
    _git(root, "push", "-u", "origin", "main")
    return root, remote


def _active_task_create(
    workspace: Path,
    *,
    project_name_list: tuple[str, ...] = ("product-one",),
) -> tuple[Path, list[Path], GoalWorktreeWorkflow]:
    """Create one active task with clean implementation worktrees."""

    goals, _ = _repository_create(workspace, "project-goals")
    project_list = [_repository_create(workspace, name)[0] for name in project_name_list]
    spec_input = workspace / "spec-input.md"
    goal_input = workspace / "goal-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    goal_input.write_text("# Goal\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=project_list,
        specification_input=spec_input,
    )
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX, goal_input=goal_input)
    workflow.activate(common_prefix=PREFIX)
    return goals, [Path(item) for item in prepared["task_root_list"]], workflow


def _task_commit_push(task_root: Path, *, message: str = "Close task") -> str:
    """Commit every current task change and push the exact task branch."""

    _git(task_root, "add", "-A")
    _git(task_root, "commit", "-m", message)
    _git(task_root, "push", "-u", "origin", PREFIX)
    return _git(task_root, "rev-parse", "HEAD")


def test_strict_yaml_rejects_duplicate_anchor_tag_and_wrong_extension(tmp_path: Path) -> None:
    for index, text in enumerate(
        (
            "schema_version: 2\nschema_version: 2\n",
            "schema_version: &version 2\nresource: *version\n",
            "schema_version: !custom 2\n",
        )
    ):
        path = tmp_path / f"invalid-{index}.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(GoalLifecycleError):
            yaml_document_load(path)
    toml_path = tmp_path / "worktree-bootstrap.toml"
    toml_path.write_text("schema_version = 2\n", encoding="utf-8")
    with pytest.raises(GoalLifecycleError, match="must use .yaml"):
        yaml_document_load(toml_path)


def test_bootstrap_manifest_rejects_unknown_cleanup_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "worktree-bootstrap.yaml"
    path.write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  command_argument_list: [python, cleanup.py, "prefix={common_prefix}"]
""",
        encoding="utf-8",
    )
    with pytest.raises(GoalLifecycleError, match="placeholder"):
        bootstrap_manifest_load(path)


def test_coordination_publication_returns_clean_synchronized_main(tmp_path: Path) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    coordination = CoordinationRepository(goals)
    commit = coordination.publish(
        common_prefix=PREFIX,
        message="Prepare test goal",
        relative_payload_by_path_map={f"{PREFIX}/spec.md": b"# Spec\n"},
    )
    assert _git(goals, "rev-parse", "HEAD") == commit
    assert _git(goals, "rev-parse", "origin/main") == commit
    assert _git(goals, "status", "--porcelain") == ""
    assert (goals / PREFIX / "spec.md").read_bytes() == b"# Spec\n"


class _CrashAtCoordinationGitBoundary(Git):
    """Lose the process once at one exact direct-main publication boundary."""

    def __init__(self, *, boundary: str) -> None:
        self._boundary = boundary
        self.did_crash = False

    def run(self, repository: Path, argument_list: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if (
            not self.did_crash
            and self._boundary == "before-push"
            and len(argument_list) == 3
            and argument_list[:2] == ["push", "origin"]
            and argument_list[2].endswith(":refs/heads/main")
        ):
            self.did_crash = True
            raise RuntimeError("simulated crash before coordination push")
        result = super().run(repository, argument_list, **kwargs)
        if not self.did_crash and self._boundary == "after-local-merge" and argument_list[:2] == ["merge", "--ff-only"]:
            self.did_crash = True
            raise RuntimeError("simulated crash after coordination local merge")
        return result


@pytest.mark.parametrize("boundary", ["before-push", "after-local-merge"])
def test_coordination_publication_resumes_without_duplicate_commit(tmp_path: Path, boundary: str) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    baseline = _git(goals, "rev-parse", "HEAD")
    payload = {f"{PREFIX}/spec.md": b"stable bytes\n"}
    with pytest.raises(RuntimeError, match="simulated crash"):
        CoordinationRepository(goals, git=_CrashAtCoordinationGitBoundary(boundary=boundary)).publish(
            common_prefix=PREFIX,
            message="Publish once",
            relative_payload_by_path_map=payload,
        )

    commit = CoordinationRepository(goals).publish(
        common_prefix=PREFIX,
        message="Publish once",
        relative_payload_by_path_map=payload,
    )
    assert _git(goals, "rev-list", "--count", f"{baseline}..{commit}") == "1"
    assert _git(goals, "rev-parse", "origin/main") == commit
    assert _git(goals, "status", "--porcelain") == ""

    repeated = CoordinationRepository(goals).publish(
        common_prefix=PREFIX,
        message="Retry after an unknowable return boundary",
        relative_payload_by_path_map=payload,
    )
    assert repeated == commit


def test_complete_prepare_checkpoint_merge_accept_and_delete_lifecycle(tmp_path: Path) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    spec_input = tmp_path / "spec-input.md"
    goal_input = tmp_path / "goal-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    goal_input.write_text("# Goal\n", encoding="utf-8")

    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    task_root = Path(prepared["task_root_list"][0])
    assert task_root == project / ".worktree" / PREFIX
    assert not (goals / ".worktree").exists()
    assert not (goals / "worktree-bootstrap.yaml").exists()

    workflow.contracts_authored(common_prefix=PREFIX)
    sealed = workflow.seal(common_prefix=PREFIX, goal_input=goal_input)
    assert sealed["lifecycle_state"] == "goal_ready"
    assert not cleanup_binding_receipt_path_get(task_root, common_prefix=PREFIX).exists()
    workflow.revise(common_prefix=PREFIX)
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX)
    workflow.activate(common_prefix=PREFIX)
    receipt = cleanup_binding_receipt_validate(
        task_root,
        common_prefix=PREFIX,
        provider_state_generation=(
            json.loads(
                (
                    Path(_git(task_root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
                    / "agent-workflows"
                    / "task"
                    / PREFIX
                    / "state.json"
                ).read_text(encoding="utf-8")
            )["cleanup_binding_generation"]
        ),
        sealed_specification_sha256=(
            json.loads(
                (
                    Path(_git(task_root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
                    / "agent-workflows"
                    / "task"
                    / PREFIX
                    / "state.json"
                ).read_text(encoding="utf-8")
            )["sealed_spec_sha256"]
        ),
    )
    assert receipt["common_prefix"] == PREFIX

    _git(task_root, "add", ".gitignore", "worktree-bootstrap.yaml")
    _git(task_root, "commit", "-m", "Prepare task")
    _git(task_root, "push", "-u", "origin", PREFIX)
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=[task_root],
    )
    assert checkpoint_id == "checkpoint-0001"

    merge = GoalMergeWorkflow(goals)
    merge_result = merge.merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    assert merge_result["phase"] == "awaiting-acceptance"
    merge.accept(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    assert _git(project, "rev-parse", "HEAD") == _git(task_root, "rev-parse", "HEAD")

    result = GoalDeletionWorkflow(goals).delete(
        common_prefix=PREFIX,
        unfinished_goal_absent=True,
    )
    assert result["phase"] == "complete"
    assert not task_root.exists()
    assert _git_returncode(project, "show-ref", "--verify", f"refs/heads/{PREFIX}") != 0
    assert not (goals / PREFIX).exists()


def _accepted_task_create(tmp_path: Path) -> tuple[Path, list[Path]]:
    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    for task_root in task_root_list:
        _task_commit_push(task_root)
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    merge = GoalMergeWorkflow(goals)
    merge.merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    merge.accept(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    return goals, task_root_list


def test_state_replication_recovers_when_replica_write_precedes_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    task_root = Path(prepared["task_root_list"][0])
    coordination_state_path = CoordinationRepository(goals).state_path_get(PREFIX)
    real_atomic_json_write = worktree_module.atomic_json_write
    crashed = False

    def crash_after_replica(path: Path, payload: dict[str, object]) -> None:
        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path.name == "state.json" and path != coordination_state_path:
            crashed = True
            raise RuntimeError("simulated crash after replica write")

    monkeypatch.setattr(worktree_module, "atomic_json_write", crash_after_replica)
    with pytest.raises(RuntimeError, match="simulated crash"):
        workflow.contracts_authored(common_prefix=PREFIX)
    result = workflow.contracts_authored(common_prefix=PREFIX)
    assert result["lifecycle_state"] == "contracts_authored"
    assert json.loads(coordination_state_path.read_text(encoding="utf-8")) == json.loads(
        (
            Path(_git(task_root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
            / "agent-workflows"
            / "task"
            / PREFIX
            / "state.json"
        ).read_text(encoding="utf-8")
    )


def test_merge_acceptance_resumes_after_durable_accepted_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    _task_commit_push(task_root_list[0])
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    merge = GoalMergeWorkflow(goals)
    merge.merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    real_atomic_json_write = merge_module.atomic_json_write
    crashed = False

    def crash_after_accepted(path: Path, payload: dict[str, object]) -> None:
        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path.name == "merge-journal.json" and payload.get("phase") == "accepted":
            crashed = True
            raise RuntimeError("simulated crash after durable accepted phase")

    monkeypatch.setattr(merge_module, "atomic_json_write", crash_after_accepted)
    with pytest.raises(RuntimeError, match="simulated crash"):
        merge.accept(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    merge.accept(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    coordination = CoordinationRepository(goals)
    assert not coordination.journal_path_get(PREFIX, "merge").exists()
    assert not coordination.merge_owner_path_get().exists()


@pytest.mark.parametrize(
    "phase",
    [
        "external-resources",
        "worktrees",
        "remote-refs",
        "local-refs",
        "provider-excludes",
        "bootstrap-carriers",
        "coordination-bootstrap-retire",
        "coordination-delete",
        "complete",
    ],
)
def test_goal_delete_resumes_after_every_durable_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    goals, task_root_list = _accepted_task_create(tmp_path)
    real_atomic_json_write = delete_module.atomic_json_write
    crashed = False

    def crash_after_phase(path: Path, payload: dict[str, object]) -> None:
        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path.name == "delete-journal.json" and payload.get("phase") == phase:
            crashed = True
            raise RuntimeError(f"simulated crash after {phase}")

    monkeypatch.setattr(delete_module, "atomic_json_write", crash_after_phase)
    deletion = GoalDeletionWorkflow(goals)
    with pytest.raises(RuntimeError, match="simulated crash"):
        deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)
    result = deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)
    assert result["phase"] == "complete"
    assert not task_root_list[0].exists()
    assert not (goals / PREFIX).exists()


def test_goal_delete_rejects_absence_before_durable_journal(tmp_path: Path) -> None:
    goals, task_root_list = _accepted_task_create(tmp_path)
    _git(tmp_path / "product-one", "worktree", "remove", str(task_root_list[0]))
    with pytest.raises(GoalLifecycleError, match="absent before deletion was journaled"):
        GoalDeletionWorkflow(goals).delete(common_prefix=PREFIX, unfinished_goal_absent=True)


def test_coordination_rejects_unrelated_dirty_state(tmp_path: Path) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    (goals / "dirty.txt").write_text("user state\n", encoding="utf-8")
    with pytest.raises(GoalLifecycleError, match="clean"):
        CoordinationRepository(goals).publish(
            common_prefix=PREFIX,
            message="Forbidden",
            relative_payload_by_path_map={f"{PREFIX}/spec.md": b"# Spec\n"},
        )


def test_recover_main_leak_restores_only_complete_task_overlap(tmp_path: Path) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    task_root = Path(prepared["task_root_list"][0])
    (task_root / "README.md").write_text("task bytes\n", encoding="utf-8")
    (project / "README.md").write_text("task bytes\n", encoding="utf-8")
    (project / "unrelated.txt").write_text("preserve me\n", encoding="utf-8")

    workflow.recover_main_leak(
        common_prefix=PREFIX,
        main_repository=project,
        path_list=["README.md"],
    )

    assert (project / "README.md").read_text(encoding="utf-8") == "# product-one\n"
    assert (project / "unrelated.txt").read_text(encoding="utf-8") == "preserve me\n"
    assert (task_root / "README.md").read_text(encoding="utf-8") == "task bytes\n"


def test_accept_main_commit_drift_is_exact_and_does_not_cover_later_commit(tmp_path: Path) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    task_root = Path(prepared["task_root_list"][0])
    (task_root / "README.md").write_text("task bytes\n", encoding="utf-8")
    (project / "README.md").write_text("independent accepted bytes\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "Independent overlapping main change")
    _git(project, "push", "origin", "main")
    accepted_commit = _git(project, "rev-parse", "HEAD")

    workflow.accept_main_commit_drift(
        common_prefix=PREFIX,
        main_repository=project,
        commit=accepted_commit,
        path_list=["README.md"],
    )
    workflow.validate(common_prefix=PREFIX, required_state="repository_prepared")

    (project / "README.md").write_text("later bytes\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "Later overlapping main change")
    _git(project, "push", "origin", "main")
    with pytest.raises(GoalLifecycleError, match="exact user attestation"):
        workflow.validate(common_prefix=PREFIX, required_state="repository_prepared")


def test_sealed_candidate_rejects_prepare_before_coordination_mutation(tmp_path: Path) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    spec_input = tmp_path / "spec-input.md"
    goal_input = tmp_path / "goal-input.md"
    changed_spec_input = tmp_path / "changed-spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    goal_input.write_text("# Goal\n", encoding="utf-8")
    changed_spec_input.write_text("# Changed spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX, goal_input=goal_input)
    coordination_commit = _git(goals, "rev-parse", "HEAD")

    with pytest.raises(GoalLifecycleError, match="repository_prepared"):
        workflow.prepare(
            common_prefix=PREFIX,
            repository_root_list=[project],
            specification_input=changed_spec_input,
        )

    assert _git(goals, "rev-parse", "HEAD") == coordination_commit
    assert (goals / PREFIX / "spec.md").read_text(encoding="utf-8") == "# Spec\n"


def test_copy_resource_rejects_hardlinked_source_graph(tmp_path: Path) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    (project / ".gitignore").write_text("/secret/\n", encoding="utf-8")
    _git(project, "add", ".gitignore")
    _git(project, "commit", "-m", "Ignore local bootstrap source")
    _git(project, "push", "origin", "main")
    secret_root = project / "secret"
    secret_root.mkdir()
    (secret_root / "one").write_text("secret\n", encoding="utf-8")
    (secret_root / "two").hardlink_to(secret_root / "one")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    task_root = Path(prepared["task_root_list"][0])
    (task_root / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: [secret]
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )
    with pytest.raises(GoalLifecycleError, match="hardlink"):
        workflow.contracts_authored(common_prefix=PREFIX)


def test_active_receipt_generation_is_stable_and_stale_receipt_is_rejected(tmp_path: Path) -> None:
    goals, task_root_list, workflow = _active_task_create(tmp_path)
    task_root = task_root_list[0]
    state_path = (
        Path(_git(task_root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        / "agent-workflows"
        / "task"
        / PREFIX
        / "state.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    receipt_path = cleanup_binding_receipt_path_get(task_root, common_prefix=PREFIX)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider_state_generation"] == state["cleanup_binding_generation"]

    receipt["provider_state_generation"] += 1
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(GoalLifecycleError, match="stale"):
        workflow.validate(common_prefix=PREFIX, required_state="active")

    receipt["provider_state_generation"] -= 1
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    workflow.validate(common_prefix=PREFIX, required_state="active")
    assert goals.is_dir()


@pytest.mark.parametrize(
    "project_path",
    ["", "/absolute", "../escape", "nested/../escape", "project-goals", "nested/project-goals"],
)
def test_checkpoint_document_rejects_noncanonical_or_self_referential_project_path(
    project_path: str,
) -> None:
    with pytest.raises(GoalLifecycleError):
        CheckpointDocument.from_payload(
            {
                "schema_version": 1,
                "accepted_checkpoint_id": "",
                "checkpoint_list": [
                    {
                        "checkpoint_id": "checkpoint-0001",
                        "project_list": [
                            {
                                "project_path": project_path,
                                "git_commit_final": "a" * 40,
                            }
                        ],
                    }
                ],
            }
        )


class _CrashAfterImplementationMainPushGit(Git):
    """Simulate process loss after remote CAS succeeds but before local fast-forward."""

    def __init__(self, *, implementation_root: Path) -> None:
        self._implementation_root = implementation_root.resolve(strict=True)
        self.did_crash = False

    def run(self, repository: Path, argument_list: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = super().run(repository, argument_list, **kwargs)
        if (
            not self.did_crash
            and Path(repository).resolve(strict=True) == self._implementation_root
            and len(argument_list) == 3
            and argument_list[:2] == ["push", "origin"]
            and argument_list[2].endswith(":refs/heads/main")
        ):
            self.did_crash = True
            raise RuntimeError("simulated crash after remote main push")
        return result


def test_merge_resumes_after_remote_push_before_local_fast_forward(tmp_path: Path) -> None:
    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    task_root = task_root_list[0]
    _task_commit_push(task_root)
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    project_root = tmp_path / "product-one"
    crash_git = _CrashAfterImplementationMainPushGit(implementation_root=project_root)

    with pytest.raises(RuntimeError, match="simulated crash"):
        GoalMergeWorkflow(goals, git=crash_git).merge(
            common_prefix=PREFIX,
            checkpoint_id=checkpoint_id,
        )
    assert _git(project_root, "rev-parse", "HEAD") != _git(project_root, "rev-parse", "origin/main")

    result = GoalMergeWorkflow(goals).merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    assert result["phase"] == "awaiting-acceptance"
    assert _git(project_root, "rev-parse", "HEAD") == _git(project_root, "rev-parse", "origin/main")


def test_failed_acceptance_is_superseded_only_by_full_descendant_checkpoint(tmp_path: Path) -> None:
    goals, task_root_list, _workflow = _active_task_create(
        tmp_path,
        project_name_list=("product-one", "product-two"),
    )
    for task_root in task_root_list:
        _task_commit_push(task_root)
    first_checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    GoalMergeWorkflow(goals).merge(common_prefix=PREFIX, checkpoint_id=first_checkpoint_id)

    for task_root in task_root_list:
        (task_root / "fix.txt").write_text("fix-forward\n", encoding="utf-8")
        _task_commit_push(task_root, message="Fix acceptance")
    second_checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    result = GoalMergeWorkflow(goals).merge(
        common_prefix=PREFIX,
        checkpoint_id=second_checkpoint_id,
    )
    assert result["checkpoint_id"] == "checkpoint-0002"
    GoalMergeWorkflow(goals).accept(common_prefix=PREFIX, checkpoint_id=second_checkpoint_id)
    document = CheckpointDocument.from_payload(yaml_document_load(goals / PREFIX / "checkpoint.yaml"))
    assert document.accepted_checkpoint_id == "checkpoint-0002"


class _ConcurrentCoordinationPushGit(Git):
    """Inject one real remote main commit immediately before the tested CAS push."""

    def __init__(self, *, concurrent_root: Path, path: str) -> None:
        self._concurrent_root = concurrent_root
        self._path = path
        self._did_publish = False

    def run(self, repository: Path, argument_list: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if (
            not self._did_publish
            and len(argument_list) == 3
            and argument_list[:2] == ["push", "origin"]
            and argument_list[2].endswith(":refs/heads/main")
        ):
            self._did_publish = True
            path = self._concurrent_root / self._path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("concurrent\n", encoding="utf-8")
            _git(self._concurrent_root, "add", self._path)
            _git(self._concurrent_root, "commit", "-m", "Concurrent coordination update")
            _git(self._concurrent_root, "push", "origin", "main")
        return super().run(repository, argument_list, **kwargs)


def test_coordination_replays_disjoint_remote_update_and_rejects_same_path_conflict(tmp_path: Path) -> None:
    goals, remote = _repository_create(tmp_path, "project-goals")
    concurrent = tmp_path / "project-goals-concurrent"
    subprocess.run(["git", "clone", str(remote), str(concurrent)], check=True, capture_output=True)
    _git(concurrent, "config", "user.email", "test@example.com")
    _git(concurrent, "config", "user.name", "Test User")
    disjoint_git = _ConcurrentCoordinationPushGit(
        concurrent_root=concurrent,
        path="2026-08-01-other-goal/spec.md",
    )
    CoordinationRepository(goals, git=disjoint_git).publish(
        common_prefix=PREFIX,
        message="Publish tested task",
        relative_payload_by_path_map={f"{PREFIX}/spec.md": b"tested\n"},
    )
    assert (goals / PREFIX / "spec.md").read_text(encoding="utf-8") == "tested\n"
    assert (goals / "2026-08-01-other-goal" / "spec.md").read_text(encoding="utf-8") == "concurrent\n"

    _git(concurrent, "pull", "--ff-only")
    conflict_git = _ConcurrentCoordinationPushGit(
        concurrent_root=concurrent,
        path=f"{PREFIX}/goal.md",
    )
    with pytest.raises(GoalLifecycleError, match="overlaps"):
        CoordinationRepository(goals, git=conflict_git).publish(
            common_prefix=PREFIX,
            message="Conflicting tested task",
            relative_payload_by_path_map={f"{PREFIX}/goal.md": b"tested goal\n"},
        )


def test_self_hosting_bootstrap_exception_is_removed_with_carriers_only_by_goal_delete(tmp_path: Path) -> None:
    goals, _ = _repository_create(tmp_path, "project-goals")
    product, _ = _repository_create(tmp_path, "product-one")
    worktree_container = goals / ".worktree"
    bootstrap_task_root = worktree_container / PREFIX
    exclude_path = Path(_git(goals, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "info" / "exclude"
    exclude_path.write_text(exclude_path.read_text(encoding="utf-8") + "\n/.worktree/\n", encoding="utf-8")
    _git(goals, "worktree", "add", "-b", PREFIX, str(bootstrap_task_root), "main")

    carrier_root = tmp_path / "bootstrap-carrier"
    carrier_root.mkdir()
    specification_carrier = carrier_root / f"{PREFIX}-spec.md"
    goal_carrier = carrier_root / f"{PREFIX}-goal.md"
    specification_carrier.write_text("# Bootstrap spec\n", encoding="utf-8")
    goal_carrier.write_text("# Bootstrap goal\n", encoding="utf-8")
    (bootstrap_task_root / "AGENTS.md").write_text("# Goals instructions\n", encoding="utf-8")
    (bootstrap_task_root / "DESIGN.md").write_text("# Goals design\n", encoding="utf-8")
    task_directory = bootstrap_task_root / PREFIX
    task_directory.mkdir()
    (task_directory / "spec.md").write_bytes(specification_carrier.read_bytes())
    (task_directory / "goal.md").write_bytes(goal_carrier.read_bytes())
    (task_directory / "checkpoint.yaml").write_text(
        "schema_version: 1\naccepted_checkpoint_id: ''\ncheckpoint_list: []\n",
        encoding="utf-8",
    )
    _git(bootstrap_task_root, "add", "AGENTS.md", "DESIGN.md", PREFIX)
    _git(bootstrap_task_root, "commit", "-m", "Bootstrap project goals")
    _git(bootstrap_task_root, "push", "-u", "origin", PREFIX)
    bootstrap_commit = _git(bootstrap_task_root, "rev-parse", "HEAD")
    _git(goals, "push", "origin", f"{bootstrap_commit}:refs/heads/main")
    _git(goals, "merge", "--ff-only", bootstrap_commit)
    coordination_bootstrap_exception_write(
        goals,
        common_prefix=PREFIX,
        goal_carrier_path=goal_carrier,
        specification_carrier_path=specification_carrier,
        task_root=bootstrap_task_root,
    )

    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(common_prefix=PREFIX, repository_root_list=[product])
    task_root = Path(prepared["task_root_list"][0])
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX)
    workflow.activate(common_prefix=PREFIX)
    _task_commit_push(task_root)
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=[task_root],
    )
    merge = GoalMergeWorkflow(goals)
    merge.merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    merge.accept(common_prefix=PREFIX, checkpoint_id=checkpoint_id)

    result = GoalDeletionWorkflow(goals).delete(common_prefix=PREFIX, unfinished_goal_absent=True)

    assert result["phase"] == "complete"
    assert not specification_carrier.exists()
    assert not goal_carrier.exists()
    assert not bootstrap_task_root.exists()
    assert not worktree_container.exists()
    assert not coordination_bootstrap_exception_path_get(goals).exists()
    assert not (goals / PREFIX).exists()
    assert _git_returncode(goals, "show-ref", "--verify", f"refs/heads/{PREFIX}") != 0
    assert "/.worktree/" not in exclude_path.read_text(encoding="utf-8").splitlines()
