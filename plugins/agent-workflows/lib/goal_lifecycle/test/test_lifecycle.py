"""Behavior tests for tracked goal coordination and lifecycle transactions."""

from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

LIBRARY_ROOT = Path(__file__).resolve().parents[2]
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

import goal_lifecycle.deletion.workflow as delete_module
import goal_lifecycle.merge.owner as merge_owner_module
import goal_lifecycle.merge.workflow as merge_module
import goal_lifecycle.task.state as task_state_module
import goal_lifecycle.resource as resource_module
from goal_lifecycle.checkpoint.model import CheckpointDocument
from goal_lifecycle.checkpoint.publisher import GoalCheckpointPublisher
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
from goal_lifecycle.deletion.workflow import GoalDeletionWorkflow
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.merge.workflow import GoalMergeWorkflow
from goal_lifecycle.task.model import TaskState
from goal_lifecycle.task.state import TaskStateStore
from goal_lifecycle.task.workflow import GoalWorktreeWorkflow
from goal_lifecycle.yaml_document import yaml_document_bytes_get, yaml_document_load

PREFIX = "2026-08-01-test-goal"


def _git(repository: Path, *argument_list: str, input_text: str | None = None) -> str:
    """Run one checked Git command inside an isolated test repository.

    Args:
        repository: Exact Git repository root.
        *argument_list: Exact command arguments.
        input_text: Optional standard input text.

    Returns:
        Resulting text value.
    """

    result = subprocess.run(
        ["git", "-C", str(repository), *argument_list],
        check=True,
        capture_output=True,
        input=input_text,
        text=True,
    )
    return result.stdout.strip()


def _git_returncode(repository: Path, *argument_list: str) -> int:
    """Run one Git command and return its exit status without raising.

    Args:
        repository: Exact Git repository root.
        *argument_list: Exact command arguments.

    Returns:
        Git subprocess exit status.
    """

    return subprocess.run(
        ["git", "-C", str(repository), *argument_list],
        check=False,
        capture_output=True,
    ).returncode


def _repository_create(workspace: Path, name: str) -> tuple[Path, Path]:
    """Create one isolated Git repository with a published main branch.

    Args:
        workspace: Workspace.
        name: Canonical name.

    Returns:
        The repository.
    """

    remote = workspace / f"{name}.git"
    root = workspace / name
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "clone", str(remote), str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "Initial")
    _git(root, "push", "-u", "origin", "main")
    return root, remote


def _submodule_attach(parent: Path, *, remote: Path, path: str) -> None:
    """Attach and publish one local test submodule at an exact recursive path.

    Args:
        parent: Parent.
        remote: Remote.
        path: Exact filesystem path.
    """

    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(remote),
        path,
    )
    _git(parent, "commit", "-m", f"Add {path} submodule")
    _git(parent, "push", "origin", "main")


def _active_task_create(
    workspace: Path,
    *,
    project_name_list: tuple[str, ...] = ("product-one",),
) -> tuple[Path, list[Path], GoalWorktreeWorkflow]:
    """Create one active task with clean implementation worktrees.

    Args:
        workspace: Workspace.
        project_name_list: Ordered project name values.

    Returns:
        One active task with clean implementation worktrees.
    """

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
    """Commit every current task change and push the exact task branch.

    Args:
        task_root: Task root.
        message: Message.

    Returns:
        Resulting text value.
    """

    _git(task_root, "add", "-A")
    _git(task_root, "commit", "-m", message)
    _git(task_root, "push", "-u", "origin", PREFIX)
    return _git(task_root, "rev-parse", "HEAD")


def _active_task_with_omitted_changed_submodule_create(
    workspace: Path,
    *,
    push_submodule_branch: bool,
) -> tuple[Path, Path, Path, GoalWorktreeWorkflow, TaskState]:
    """Create an active legacy-shaped task whose committed submodule change lacks inventory.

    Args:
        workspace: Workspace.
        push_submodule_branch: Whether to publish the exact delegated task branch.

    Returns:
        Goals root, task root, task submodule, workflow, and omitted state.
    """

    goals, _ = _repository_create(workspace, "project-goals")
    project, _ = _repository_create(workspace, "product-one")
    _provider, provider_remote = _repository_create(workspace, "provider")
    _submodule_attach(project, remote=provider_remote, path="module/provider")
    spec_input = workspace / "spec-input.md"
    goal_input = workspace / "goal-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    goal_input.write_text("# Goal\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX, goal_input=goal_input)
    workflow.activate(common_prefix=PREFIX)

    coordination = CoordinationRepository(goals)
    state_store = TaskStateStore(coordination, git=Git())
    state = state_store.get(PREFIX)
    omitted_state = replace(
        state,
        provider_state_generation=state.provider_state_generation + 1,
        repository_list=tuple(
            replace(
                repository,
                submodule_gitlink_list=(),
                task_owned_submodule_list=(),
            )
            for repository in state.repository_list
        ),
    )
    state_store.write(omitted_state)

    task_root = Path(prepared["task_root_list"][0])
    task_submodule = task_root / "module" / "provider"
    _git(task_submodule, "switch", "-c", PREFIX)
    (task_submodule / ".gitignore").write_text("/.worktree/\n", encoding="utf-8")
    (task_submodule / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )
    (task_submodule / "task-change.txt").write_text("delegated task\n", encoding="utf-8")
    _git(task_submodule, "add", ".gitignore", "worktree-bootstrap.yaml", "task-change.txt")
    _git(task_submodule, "commit", "-m", "Complete delegated task")
    if push_submodule_branch:
        _git(task_submodule, "push", "-u", "origin", PREFIX)
    _git(task_root, "add", "-A")
    _git(task_root, "commit", "-m", "Record delegated task and bootstrap contracts")
    _git(task_root, "push", "-u", "origin", PREFIX)
    return goals, task_root, task_submodule, workflow, omitted_state


def test_strict_yaml_rejects_duplicate_anchor_tag_and_wrong_extension(
    tmp_path: Path,
) -> None:
    """Verify that strict YAML rejects duplicate anchor tag and wrong extension.

    Args:
        tmp_path: Temporary directory path.
    """

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


def test_strict_yaml_uses_yaml_1_2_core_scalars_without_global_loader_mutation(
    tmp_path: Path,
) -> None:
    """Lifecycle YAML must not inherit YAML 1.1 scalars or alter other loaders.

    Args:
        tmp_path: Temporary directory path.
    """

    path = tmp_path / "scalars.yaml"
    path.write_text(
        """boolean: true
legacy_boolean: yes
date_like: 2026-08-01
decimal_with_leading_zero: 0123
octal: 0o17
hexadecimal: 0x10
numeric_with_separator: 1_000
sexagesimal_like: 1:20
scientific: 1e3
""",
        encoding="utf-8",
    )

    assert yaml_document_load(path) == {
        "boolean": True,
        "legacy_boolean": "yes",
        "date_like": "2026-08-01",
        "decimal_with_leading_zero": 123,
        "octal": 15,
        "hexadecimal": 16,
        "numeric_with_separator": "1_000",
        "sexagesimal_like": "1:20",
        "scientific": 1000.0,
    }
    assert yaml.safe_load("value: yes\n") == {"value": True}


def test_bootstrap_manifest_rejects_unknown_cleanup_placeholder(tmp_path: Path) -> None:
    """Verify that bootstrap manifest rejects unknown cleanup placeholder.

    Args:
        tmp_path: Temporary directory path.
    """

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


def test_coordination_publication_returns_clean_synchronized_main(
    tmp_path: Path,
) -> None:
    """Verify that coordination publication returns clean synchronized main.

    Args:
        tmp_path: Temporary directory path.
    """

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


def test_coordination_publication_rejects_unknown_task_artifact(tmp_path: Path) -> None:
    """The direct-main transaction must not widen the closed task-directory schema.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")

    with pytest.raises(GoalLifecycleError, match="closed owner set"):
        CoordinationRepository(goals).publish(
            common_prefix=PREFIX,
            message="Publish unknown artifact",
            relative_payload_by_path_map={f"{PREFIX}/evidence.md": b"not a task artifact\n"},
        )


def test_prepare_rejects_duplicate_repository_before_publication(
    tmp_path: Path,
) -> None:
    """One participant may appear only once in task identity.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")

    with pytest.raises(GoalLifecycleError, match="repeats an implementation repository"):
        GoalWorktreeWorkflow(goals).prepare(
            common_prefix=PREFIX,
            repository_root_list=[project, project],
            specification_input=spec_input,
        )

    assert not (goals / PREFIX).exists()


def test_prepare_expands_but_never_implicitly_removes_the_top_level_set(
    tmp_path: Path,
) -> None:
    """Verify that prepare expands but never implicitly removes the top level set.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    first, _ = _repository_create(tmp_path, "product-one")
    second, _ = _repository_create(tmp_path, "product-two")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)

    workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[first],
        specification_input=spec_input,
    )
    expanded = workflow.prepare(common_prefix=PREFIX, repository_root_list=[first, second])
    assert expanded["task_root_list"] == sorted([str(first / ".worktree" / PREFIX), str(second / ".worktree" / PREFIX)])

    with pytest.raises(GoalLifecycleError, match="cannot remove or omit"):
        workflow.prepare(common_prefix=PREFIX, repository_root_list=[second])


def test_task_owned_submodule_has_its_own_branch_manifest_state_replica_and_no_silent_removal(
    tmp_path: Path,
) -> None:
    """Verify that task owned submodule has its own branch manifest state replica and no silent removal.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    provider, provider_remote = _repository_create(tmp_path, "provider")
    (provider / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )
    _git(provider, "add", "worktree-bootstrap.yaml")
    _git(provider, "commit", "-m", "Add bootstrap contract")
    _git(provider, "push", "origin", "main")
    _submodule_attach(project, remote=provider_remote, path="module/provider")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)

    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        participating_submodule_list=[(project, Path("module/provider"))],
        specification_input=spec_input,
    )
    task_submodule = project / ".worktree" / PREFIX / "module" / "provider"
    assert prepared["participating_submodule_root_list"] == [str(task_submodule)]
    assert _git(project / "module" / "provider", "branch", "--show-current") == "main"
    assert _git(task_submodule, "branch", "--show-current") == PREFIX
    state = TaskState.from_payload(
        json.loads(CoordinationRepository(goals).state_path_get(PREFIX).read_text(encoding="utf-8"))
    )
    replica_path = (
        Path(
            _git(
                task_submodule,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        )
        / "agent-workflows"
        / "task"
        / PREFIX
        / "state.json"
    )
    assert TaskState.from_payload(json.loads(replica_path.read_text(encoding="utf-8"))) == state

    with pytest.raises(GoalLifecycleError, match="cannot remove or omit"):
        workflow.prepare(common_prefix=PREFIX, repository_root_list=[project])


def test_checkpoint_merge_and_delete_publish_task_owned_submodule_before_its_parent(
    tmp_path: Path,
) -> None:
    """Verify that checkpoint merge and delete publish task owned submodule before its parent.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    provider, provider_remote = _repository_create(tmp_path, "provider")
    _submodule_attach(project, remote=provider_remote, path="module/provider")
    spec_input = tmp_path / "spec-input.md"
    goal_input = tmp_path / "goal-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    goal_input.write_text("# Goal\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        participating_submodule_list=[(project, Path("module/provider"))],
        specification_input=spec_input,
    )
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX, goal_input=goal_input)
    workflow.activate(common_prefix=PREFIX)
    task_root = Path(prepared["task_root_list"][0])
    task_submodule = Path(prepared["participating_submodule_root_list"][0])
    (task_submodule / "task-change.txt").write_text("submodule task\n", encoding="utf-8")
    submodule_commit = _task_commit_push(task_submodule, message="Close submodule task")
    _git(task_root, "add", "module/provider")
    parent_commit = _task_commit_push(task_root, message="Close parent task")
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=[task_root],
    )

    merge = GoalMergeWorkflow(goals)
    merge.merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    assert _git(provider, "fetch", "origin") == ""
    assert _git(provider, "rev-parse", "origin/main") == submodule_commit
    assert _git(project / "module" / "provider", "branch", "--show-current") == "main"
    assert _git(project / "module" / "provider", "rev-parse", "HEAD") == submodule_commit
    assert _git(project, "rev-parse", "origin/main") == parent_commit
    merge.accept(common_prefix=PREFIX, checkpoint_id=checkpoint_id)

    GoalDeletionWorkflow(goals).delete(common_prefix=PREFIX, unfinished_goal_absent=True)
    assert not task_root.exists()
    _git(provider, "fetch", "--prune", "origin")
    assert _git_returncode(provider, "show-ref", "--verify", f"refs/remotes/origin/{PREFIX}") != 0


def test_nested_task_owned_submodule_requires_every_submodule_ancestor(
    tmp_path: Path,
) -> None:
    """Verify that nested task owned submodule requires every submodule ancestor.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    parent, parent_remote = _repository_create(tmp_path, "provider-parent")
    _nested, nested_remote = _repository_create(tmp_path, "provider-nested")
    _submodule_attach(parent, remote=nested_remote, path="nested")
    _submodule_attach(project, remote=parent_remote, path="module/provider")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")

    with pytest.raises(GoalLifecycleError, match="requires every submodule ancestor"):
        GoalWorktreeWorkflow(goals).prepare(
            common_prefix=PREFIX,
            repository_root_list=[project],
            participating_submodule_list=[(project, Path("module/provider/nested"))],
            specification_input=spec_input,
        )


def test_dirty_read_only_submodule_is_preserved_and_clean_commit_drift_is_repaired(
    tmp_path: Path,
) -> None:
    """Verify that dirty read only submodule is preserved and clean commit drift is repaired.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    _provider, provider_remote = _repository_create(tmp_path, "provider")
    _submodule_attach(project, remote=provider_remote, path="module/provider")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    task_submodule = Path(prepared["task_root_list"][0]) / "module" / "provider"
    baseline = _git(task_submodule, "rev-parse", "HEAD")
    _git(task_submodule, "switch", "-c", "clean-drift")
    (task_submodule / "clean-drift.txt").write_text("committed\n", encoding="utf-8")
    _git(task_submodule, "add", "clean-drift.txt")
    _git(task_submodule, "commit", "-m", "Clean drift")

    workflow.validate(common_prefix=PREFIX, required_state="repository_prepared")
    assert _git(task_submodule, "rev-parse", "HEAD") == baseline
    assert _git(task_submodule, "branch", "--show-current") == ""

    (task_submodule / "README.md").write_text("preserve dirty state\n", encoding="utf-8")
    with pytest.raises(GoalLifecycleError, match="must be clean"):
        workflow.validate(common_prefix=PREFIX, required_state="repository_prepared")
    assert (task_submodule / "README.md").read_text(encoding="utf-8") == "preserve dirty state\n"


def test_validation_recovers_provider_omitted_submodule_inventory_from_pushed_commits(
    tmp_path: Path,
) -> None:
    """An exact pushed delegated branch repairs one wholly omitted recursive inventory.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, task_root, task_submodule, workflow, omitted_state = _active_task_with_omitted_changed_submodule_create(
        tmp_path,
        push_submodule_branch=True,
    )

    result = workflow.validate(common_prefix=PREFIX, required_state="active")

    state = TaskState.from_payload(
        json.loads(CoordinationRepository(goals).state_path_get(PREFIX).read_text(encoding="utf-8"))
    )
    repository = state.repository_list[0]
    assert state.provider_state_generation == omitted_state.provider_state_generation + 1
    assert [item.path for item in repository.submodule_gitlink_list] == ["module/provider"]
    assert [item.path for item in repository.task_owned_submodule_list] == ["module/provider"]
    assert result["participating_submodule_root_list"] == [str(task_submodule)]
    assert result["performed_repair_list"] == [
        f"provider-omitted-submodule-inventory-recovered:{task_root}",
        f"recovered-submodule-cleanup-binding-ensured:{task_submodule}",
    ]
    assert _git(task_submodule, "branch", "--show-current") == PREFIX
    cleanup_binding_receipt_validate(
        task_submodule,
        common_prefix=PREFIX,
        provider_state_generation=state.cleanup_binding_generation,
        sealed_specification_sha256=state.sealed_spec_sha256,
    )
    replica_path = (
        Path(_git(task_submodule, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        / "agent-workflows"
        / "task"
        / PREFIX
        / "state.json"
    )
    assert TaskState.from_payload(json.loads(replica_path.read_text(encoding="utf-8"))) == state

    repeated = workflow.validate(common_prefix=PREFIX, required_state="active")
    assert repeated["performed_repair_list"] == []


def test_validation_rejects_unpushed_submodule_inventory_recovery_without_state_change(
    tmp_path: Path,
) -> None:
    """A local-only delegated commit never becomes durable task ownership.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _task_root, _task_submodule, workflow, omitted_state = _active_task_with_omitted_changed_submodule_create(
        tmp_path,
        push_submodule_branch=False,
    )

    with pytest.raises(GoalLifecycleError, match="not fully pushed"):
        workflow.validate(common_prefix=PREFIX, required_state="active")

    state = TaskState.from_payload(
        json.loads(CoordinationRepository(goals).state_path_get(PREFIX).read_text(encoding="utf-8"))
    )
    assert state == omitted_state
    assert state.repository_list[0].submodule_gitlink_list == ()
    assert state.repository_list[0].task_owned_submodule_list == ()


def test_validation_resumes_inventory_recovery_after_state_write_before_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A surviving branch marker completes active receipt publication after process loss.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

    goals, _task_root, task_submodule, workflow, omitted_state = _active_task_with_omitted_changed_submodule_create(
        tmp_path,
        push_submodule_branch=True,
    )

    def fail_before_receipt(_state: TaskState) -> None:
        """Simulate process loss after replicated state publication."""

        raise RuntimeError("simulated crash before recovered cleanup receipt")

    monkeypatch.setattr(
        workflow._repository_manager,
        "pending_submodule_recovery_receipt_ensure",
        fail_before_receipt,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        workflow.validate(common_prefix=PREFIX, required_state="active")

    interrupted_state = TaskState.from_payload(
        json.loads(CoordinationRepository(goals).state_path_get(PREFIX).read_text(encoding="utf-8"))
    )
    assert interrupted_state.provider_state_generation == omitted_state.provider_state_generation + 1
    assert interrupted_state.repository_list[0].task_owned_submodule_list
    assert not cleanup_binding_receipt_path_get(task_submodule, common_prefix=PREFIX).exists()

    result = GoalWorktreeWorkflow(goals).validate(common_prefix=PREFIX, required_state="active")
    assert result["performed_repair_list"] == [
        f"recovered-submodule-cleanup-binding-ensured:{task_submodule}",
    ]
    cleanup_binding_receipt_validate(
        task_submodule,
        common_prefix=PREFIX,
        provider_state_generation=interrupted_state.cleanup_binding_generation,
        sealed_specification_sha256=interrupted_state.sealed_spec_sha256,
    )


def test_validation_recovers_omitted_read_only_submodule_inventory(
    tmp_path: Path,
) -> None:
    """An unchanged committed graph is reconstructed as read-only without delegation.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    _provider, provider_remote = _repository_create(tmp_path, "provider")
    _submodule_attach(project, remote=provider_remote, path="module/provider")
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
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX, goal_input=goal_input)
    workflow.activate(common_prefix=PREFIX)
    task_root = Path(prepared["task_root_list"][0])
    _task_commit_push(task_root, message="Commit generated bootstrap contract")

    state_store = TaskStateStore(CoordinationRepository(goals), git=Git())
    state = state_store.get(PREFIX)
    omitted_state = replace(
        state,
        provider_state_generation=state.provider_state_generation + 1,
        repository_list=tuple(
            replace(repository, submodule_gitlink_list=(), task_owned_submodule_list=())
            for repository in state.repository_list
        ),
    )
    state_store.write(omitted_state)

    workflow.validate(common_prefix=PREFIX, required_state="active")
    recovered = state_store.get(PREFIX)
    assert [item.path for item in recovered.repository_list[0].submodule_gitlink_list] == ["module/provider"]
    assert recovered.repository_list[0].task_owned_submodule_list == ()


def test_seal_rejects_prepopulated_checkpoint_before_goal_publication(
    tmp_path: Path,
) -> None:
    """No checkpoint may predate successful persistent-goal activation.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    spec_input = tmp_path / "spec-input.md"
    goal_input = tmp_path / "goal-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    goal_input.write_text("# Goal\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    workflow.contracts_authored(common_prefix=PREFIX)
    CoordinationRepository(goals).publish(
        common_prefix=PREFIX,
        message="Inject premature checkpoint",
        relative_payload_by_path_map={
            f"{PREFIX}/checkpoint.yaml": yaml_document_bytes_get(
                {
                    "schema_version": 1,
                    "accepted_checkpoint_id": "",
                    "checkpoint_list": [
                        {
                            "checkpoint_id": "checkpoint-0001",
                            "project_list": [
                                {
                                    "project_path": "product-one",
                                    "git_commit_final": "a" * 40,
                                }
                            ],
                        }
                    ],
                }
            )
        },
    )

    with pytest.raises(GoalLifecycleError, match="inactive goal candidate"):
        workflow.seal(common_prefix=PREFIX, goal_input=goal_input)

    assert not (goals / PREFIX / "goal.md").exists()


class _CrashAtCoordinationGitBoundary(Git):
    """Lose the process once at one exact direct-main publication boundary."""

    def __init__(self, *, boundary: str) -> None:
        """Initialize the crash at coordination Git boundary dependencies.

        Args:
            boundary: Boundary.
        """

        self._boundary = boundary
        self.did_crash = False

    def run(self, repository: Path, argument_list: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        """Run the crash at coordination Git boundary operation.

        Args:
            repository: Exact Git repository root.
            argument_list: Exact command arguments.
            **kwargs: Provider keyword arguments.

        Returns:
            Completed binary-mode subprocess result.
        """

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
    """Verify that coordination publication resumes without duplicate commit.

    Args:
        tmp_path: Temporary directory path.
        boundary: Boundary.
    """

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


def test_coordination_recovery_fast_forwards_after_a_later_disjoint_publication(
    tmp_path: Path,
) -> None:
    """A pushed operation remains resumable when another task advances main after the local merge.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, remote = _repository_create(tmp_path, "project-goals")
    payload = {f"{PREFIX}/spec.md": b"stable bytes\n"}
    with pytest.raises(RuntimeError, match="simulated crash"):
        CoordinationRepository(
            goals,
            git=_CrashAtCoordinationGitBoundary(boundary="after-local-merge"),
        ).publish(
            common_prefix=PREFIX,
            message="Publish once",
            relative_payload_by_path_map=payload,
        )
    published_commit = _git(goals, "rev-parse", "HEAD")

    concurrent = tmp_path / "project-goals-concurrent-after-crash"
    subprocess.run(["git", "clone", str(remote), str(concurrent)], check=True, capture_output=True)
    _git(concurrent, "config", "user.email", "test@example.com")
    _git(concurrent, "config", "user.name", "Test User")
    disjoint_path = concurrent / "2026-08-01-other-goal" / "spec.md"
    disjoint_path.parent.mkdir(parents=True)
    disjoint_path.write_text("other task\n", encoding="utf-8")
    _git(concurrent, "add", disjoint_path.relative_to(concurrent).as_posix())
    _git(concurrent, "commit", "-m", "Publish another task")
    _git(concurrent, "push", "origin", "main")

    recovered_commit = CoordinationRepository(goals).publish(
        common_prefix=PREFIX,
        message="Publish once",
        relative_payload_by_path_map=payload,
    )

    assert recovered_commit == published_commit
    assert _git(goals, "rev-parse", "HEAD") == _git(goals, "rev-parse", "origin/main")
    assert (goals / "2026-08-01-other-goal" / "spec.md").read_text(encoding="utf-8") == "other task\n"


def test_coordination_recovery_rejects_a_later_same_task_publication(
    tmp_path: Path,
) -> None:
    """A pushed-but-interrupted delta cannot hide a later same-path replacement.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, remote = _repository_create(tmp_path, "project-goals")
    task_path = f"{PREFIX}/spec.md"
    original_payload = {task_path: b"stable bytes\n"}
    with pytest.raises(RuntimeError, match="simulated crash"):
        CoordinationRepository(
            goals,
            git=_CrashAtCoordinationGitBoundary(boundary="after-local-merge"),
        ).publish(
            common_prefix=PREFIX,
            message="Publish once",
            relative_payload_by_path_map=original_payload,
        )

    concurrent = tmp_path / "project-goals-concurrent-same-task"
    subprocess.run(["git", "clone", str(remote), str(concurrent)], check=True, capture_output=True)
    _git(concurrent, "config", "user.email", "test@example.com")
    _git(concurrent, "config", "user.name", "Test User")
    concurrent_path = concurrent / task_path
    concurrent_path.write_text("replacement bytes\n", encoding="utf-8")
    _git(concurrent, "add", task_path)
    _git(concurrent, "commit", "-m", "Replace the same task artifact")
    _git(concurrent, "push", "origin", "main")

    with pytest.raises(GoalLifecycleError, match="overlaps this exact path set"):
        CoordinationRepository(goals).publish(
            common_prefix=PREFIX,
            message="Publish once",
            relative_payload_by_path_map=original_payload,
        )


def test_complete_prepare_checkpoint_merge_accept_and_delete_lifecycle(
    tmp_path: Path,
) -> None:
    """Verify that complete prepare checkpoint merge accept and delete lifecycle.

    Args:
        tmp_path: Temporary directory path.
    """

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
                    Path(
                        _git(
                            task_root,
                            "rev-parse",
                            "--path-format=absolute",
                            "--git-common-dir",
                        )
                    )
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
                    Path(
                        _git(
                            task_root,
                            "rev-parse",
                            "--path-format=absolute",
                            "--git-common-dir",
                        )
                    )
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

    coordination_private_task_directory = CoordinationRepository(goals).state_path_get(PREFIX).parent
    project_private_task_directory = (
        Path(_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        / "agent-workflows"
        / "task"
        / PREFIX
    )
    assert (coordination_private_task_directory / "replica-index.json").is_file()

    result = GoalDeletionWorkflow(goals).delete(
        common_prefix=PREFIX,
        unfinished_goal_absent=True,
    )
    assert result["phase"] == "complete"
    assert not task_root.exists()
    assert _git_returncode(project, "show-ref", "--verify", f"refs/heads/{PREFIX}") != 0
    assert not (goals / PREFIX).exists()
    assert not coordination_private_task_directory.exists()
    assert not project_private_task_directory.exists()


def test_goal_delete_rejects_a_cleanup_manifest_changed_in_main_after_acceptance(
    tmp_path: Path,
) -> None:
    """Deletion stops when clean synchronized main no longer carries the sealed hook.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    cleanup_script = """import json, sys
request = json.load(sys.stdin)
print(json.dumps({**request, "external_resources_absent": True}, sort_keys=True))
"""
    (project / "cleanup.py").write_text(cleanup_script, encoding="utf-8")
    (project / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  command_argument_list: [python, cleanup.py, "{common_prefix}"]
""",
        encoding="utf-8",
    )
    _git(project, "add", "cleanup.py", "worktree-bootstrap.yaml")
    _git(project, "commit", "-m", "Add task cleanup")
    _git(project, "push", "origin", "main")
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
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX, goal_input=goal_input)
    workflow.activate(common_prefix=PREFIX)
    (task_root / "task.txt").write_text("task\n", encoding="utf-8")
    _task_commit_push(task_root)
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=[task_root],
    )
    merge = GoalMergeWorkflow(goals)
    merge.merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    merge.accept(common_prefix=PREFIX, checkpoint_id=checkpoint_id)

    (project / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: [future-local-state]
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  command_argument_list: [python, cleanup.py, "{common_prefix}"]
""",
        encoding="utf-8",
    )
    _git(project, "add", "worktree-bootstrap.yaml")
    _git(project, "commit", "-m", "Evolve cleanup manifest after acceptance")
    _git(project, "push", "origin", "main")

    with pytest.raises(GoalLifecycleError, match="Main cleanup manifest differs from sealed binding"):
        GoalDeletionWorkflow(goals).delete(
            common_prefix=PREFIX,
            unfinished_goal_absent=True,
        )

    assert task_root.exists()


def _accepted_task_create(tmp_path: Path) -> tuple[Path, list[Path]]:
    """Create one fully checkpointed and accepted multi-repository task fixture.

    Args:
        tmp_path: Temporary directory path.

    Returns:
        The accepted task.
    """

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


def test_goal_merge_keeps_selected_commit_when_task_ref_advances(
    tmp_path: Path,
) -> None:
    """A later descendant task commit cannot replace an already selected checkpoint.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    task_root = task_root_list[0]
    selected_commit = _task_commit_push(task_root, message="Selected checkpoint")
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    (task_root / "later.txt").write_text("later task work\n", encoding="utf-8")
    later_commit = _task_commit_push(task_root, message="Later task commit")
    assert later_commit != selected_commit

    result = GoalMergeWorkflow(goals).merge(
        common_prefix=PREFIX,
        checkpoint_id=checkpoint_id,
    )

    assert result["phase"] == "awaiting-acceptance"
    assert _git(tmp_path / "product-one", "rev-parse", "HEAD") == selected_commit
    assert _git(task_root, "rev-parse", "HEAD") == later_commit


def test_goal_merge_and_accept_require_synchronized_coordination_main(
    tmp_path: Path,
) -> None:
    """Never select or accept a checkpoint from a stale local coordination tree.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    _task_commit_push(task_root_list[0])
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    dirty_path = goals / "uncommitted.txt"
    dirty_path.write_text("do not read stale checkpoint state\n", encoding="utf-8")
    merge = GoalMergeWorkflow(goals)

    with pytest.raises(GoalLifecycleError, match="must be clean"):
        merge.merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)

    dirty_path.unlink()
    merge.merge(common_prefix=PREFIX, checkpoint_id=checkpoint_id)
    dirty_path.write_text("do not accept stale checkpoint state\n", encoding="utf-8")

    with pytest.raises(GoalLifecycleError, match="must be clean"):
        merge.accept(common_prefix=PREFIX, checkpoint_id=checkpoint_id)


def test_goal_checkpoint_rejects_a_changed_repository_origin(tmp_path: Path) -> None:
    """Checkpoint publication remains bound to the sealed full origin identity.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    task_root = task_root_list[0]
    _task_commit_push(task_root)
    alternate_remote = tmp_path / "alternate-checkpoint-origin.git"
    subprocess.run(
        [
            "git",
            "clone",
            "--bare",
            _git(task_root, "remote", "get-url", "origin"),
            str(alternate_remote),
        ],
        check=True,
        capture_output=True,
    )
    _git(task_root, "remote", "set-url", "origin", str(alternate_remote))

    with pytest.raises(GoalLifecycleError, match="origin changed"):
        GoalCheckpointPublisher(goals).publish(
            common_prefix=PREFIX,
            project_root_list=task_root_list,
        )


def test_goal_checkpoint_rejects_drifted_ignored_bootstrap_resource(
    tmp_path: Path,
) -> None:
    """A clean pushed Git ref cannot hide drift in its active task resources.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    (project / ".gitignore").write_text("/local-state/\n", encoding="utf-8")
    (project / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: [local-state]
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )
    _git(project, "add", ".gitignore", "worktree-bootstrap.yaml")
    _git(project, "commit", "-m", "Declare task-local state")
    _git(project, "push", "origin", "main")
    source_path = project / "local-state/config.txt"
    source_path.parent.mkdir()
    source_path.write_text("sealed bytes\n", encoding="utf-8")
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
    workflow.contracts_authored(common_prefix=PREFIX)
    workflow.seal(common_prefix=PREFIX, goal_input=goal_input)
    workflow.activate(common_prefix=PREFIX)
    (task_root / "checkpoint.txt").write_text("candidate\n", encoding="utf-8")
    _task_commit_push(task_root)
    (task_root / "local-state/config.txt").write_text("drifted bytes\n", encoding="utf-8")

    with pytest.raises(GoalLifecycleError, match="resource drifted"):
        GoalCheckpointPublisher(goals).publish(
            common_prefix=PREFIX,
            project_root_list=[task_root],
        )


def test_goal_merge_rejects_a_changed_repository_origin(tmp_path: Path) -> None:
    """Merge compare-and-swap must never publish into a replacement remote.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    task_root = task_root_list[0]
    _task_commit_push(task_root)
    checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    alternate_remote = tmp_path / "alternate-merge-origin.git"
    subprocess.run(
        [
            "git",
            "clone",
            "--bare",
            _git(task_root, "remote", "get-url", "origin"),
            str(alternate_remote),
        ],
        check=True,
        capture_output=True,
    )
    _git(task_root, "remote", "set-url", "origin", str(alternate_remote))

    with pytest.raises(GoalLifecycleError, match="origin changed"):
        GoalMergeWorkflow(goals).merge(
            common_prefix=PREFIX,
            checkpoint_id=checkpoint_id,
        )


def test_state_replication_recovers_when_replica_write_precedes_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that state replication recovers when replica write precedes commit marker.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

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
    real_atomic_json_write = task_state_module.atomic_json_write
    crashed = False

    def crash_after_replica(path: Path, payload: dict[str, object]) -> None:
        """Inject a crash after the first non-coordination replica write.

        Args:
            path: Exact filesystem path.
            payload: Structured operation payload.
        """

        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path.name == "state.json" and path != coordination_state_path:
            crashed = True
            raise RuntimeError("simulated crash after replica write")

    monkeypatch.setattr(task_state_module, "atomic_json_write", crash_after_replica)
    with pytest.raises(RuntimeError, match="simulated crash"):
        workflow.contracts_authored(common_prefix=PREFIX)
    result = workflow.contracts_authored(common_prefix=PREFIX)
    assert result["lifecycle_state"] == "contracts_authored"
    assert result["performed_repair_list"] == [f"private-state-write-recovered:{PREFIX}"]
    assert json.loads(coordination_state_path.read_text(encoding="utf-8")) == json.loads(
        (
            Path(_git(task_root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
            / "agent-workflows"
            / "task"
            / PREFIX
            / "state.json"
        ).read_text(encoding="utf-8")
    )


def test_state_replication_recovers_one_partial_multi_repository_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One durable journal closes a crash between distinct repository replicas.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    first, _ = _repository_create(tmp_path, "product-one")
    second, _ = _repository_create(tmp_path, "product-two")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[first, second],
        specification_input=spec_input,
    )
    coordination_state_path = CoordinationRepository(goals).state_path_get(PREFIX)
    replica_path_list = sorted(
        {
            Path(
                _git(
                    Path(root),
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
            )
            / "agent-workflows"
            / "task"
            / PREFIX
            / "state.json"
            for root in prepared["task_root_list"]
        }
    )
    real_atomic_json_write = task_state_module.atomic_json_write
    crashed = False

    def crash_between_repositories(path: Path, payload: dict[str, object]) -> None:
        """Inject a crash between two repository replica writes.

        Args:
            path: Exact filesystem path.
            payload: Structured operation payload.
        """

        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path == replica_path_list[0]:
            crashed = True
            raise RuntimeError("simulated crash between repository replicas")

    monkeypatch.setattr(task_state_module, "atomic_json_write", crash_between_repositories)
    with pytest.raises(RuntimeError, match="between repository replicas"):
        workflow.contracts_authored(common_prefix=PREFIX)

    result = workflow.contracts_authored(common_prefix=PREFIX)

    assert result["performed_repair_list"] == [f"private-state-write-recovered:{PREFIX}"]
    expected = json.loads(coordination_state_path.read_text(encoding="utf-8"))
    assert all(json.loads(path.read_text(encoding="utf-8")) == expected for path in replica_path_list)
    assert not CoordinationRepository(goals).journal_path_get(PREFIX, "private-write").exists()


def test_state_replication_restores_a_missing_authoritative_state_from_exact_index(
    tmp_path: Path,
) -> None:
    """The private replica index permits recovery without a workspace scan.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    coordination_state_path = CoordinationRepository(goals).state_path_get(PREFIX)
    coordination_state_path.unlink()

    result = workflow.validate(common_prefix=PREFIX, required_state="repository_prepared")

    assert result["performed_repair_list"] == [f"private-state-authoritative-restored:{PREFIX}"]
    assert coordination_state_path.is_file()


def test_private_task_state_rejects_empty_or_unsorted_repository_set(
    tmp_path: Path,
) -> None:
    """Closed private state never accepts an ambiguous participant identity.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _task_root_list, _workflow = _active_task_create(
        tmp_path,
        project_name_list=("product-one", "product-two"),
    )
    payload = json.loads(CoordinationRepository(goals).state_path_get(PREFIX).read_text(encoding="utf-8"))
    empty_payload = {**payload, "repository_list": []}
    with pytest.raises(GoalLifecycleError, match="must be non-empty"):
        TaskState.from_payload(empty_payload)
    unsorted_payload = {
        **payload,
        "repository_list": list(reversed(payload["repository_list"])),
    }
    with pytest.raises(GoalLifecycleError, match="sorted by main_root"):
        TaskState.from_payload(unsorted_payload)

    legacy_payload = {**payload, "schema_version": 2}
    for repository in legacy_payload["repository_list"]:
        repository.pop("submodule_gitlink_list")
        repository.pop("task_owned_submodule_list")
    with pytest.raises(GoalLifecycleError, match="another shape"):
        TaskState.from_payload(legacy_payload)


def test_merge_acceptance_resumes_after_durable_accepted_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that merge acceptance resumes after durable accepted phase.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

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
        """Inject a crash after the merge journal durably reaches accepted.

        Args:
            path: Exact filesystem path.
            payload: Structured operation payload.
        """

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
    """Verify that goal delete resumes after every durable phase.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
        phase: Phase.
    """

    goals, task_root_list = _accepted_task_create(tmp_path)
    real_atomic_json_write = delete_module.atomic_json_write
    crashed = False

    def crash_after_phase(path: Path, payload: dict[str, object]) -> None:
        """Inject a crash after the selected deletion phase is durable.

        Args:
            path: Exact filesystem path.
            payload: Structured operation payload.
        """

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


def test_goal_delete_retires_project_external_cleanup_journal_only_in_final_state_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that goal delete retires project external cleanup journal only in final state phase.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

    goals, _task_root_list = _accepted_task_create(tmp_path)
    main_root = tmp_path / "product-one"
    external_journal = (
        Path(_git(main_root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        / "agent-workflows"
        / "external-cleanup"
        / f"{PREFIX}.json"
    )
    external_journal.parent.mkdir(parents=True)
    external_journal.write_text('{"phase":"complete"}\n', encoding="utf-8")
    real_atomic_json_write = delete_module.atomic_json_write
    crashed = False

    def crash_before_worktrees(path: Path, payload: dict[str, object]) -> None:
        """Inject a crash immediately before Git worktree retirement.

        Args:
            path: Exact filesystem path.
            payload: Structured operation payload.
        """

        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path.name == "delete-journal.json" and payload.get("phase") == "worktrees":
            crashed = True
            raise RuntimeError("simulated crash before Git retirement")

    monkeypatch.setattr(delete_module, "atomic_json_write", crash_before_worktrees)
    deletion = GoalDeletionWorkflow(goals)
    with pytest.raises(RuntimeError, match="before Git retirement"):
        deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)
    assert external_journal.exists()

    deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)
    assert not external_journal.exists()


def test_goal_delete_rejects_absence_before_durable_journal(tmp_path: Path) -> None:
    """Verify that goal delete rejects absence before durable journal.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, task_root_list = _accepted_task_create(tmp_path)
    _git(tmp_path / "product-one", "worktree", "remove", str(task_root_list[0]))
    with pytest.raises(GoalLifecycleError, match="absent before deletion was journaled"):
        GoalDeletionWorkflow(goals).delete(common_prefix=PREFIX, unfinished_goal_absent=True)


def test_goal_delete_resume_rechecks_clean_synchronized_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable journal never bypasses the main checkout execution boundary.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

    goals, _task_root_list = _accepted_task_create(tmp_path)
    main_root = tmp_path / "product-one"
    real_atomic_json_write = delete_module.atomic_json_write
    crashed = False

    def crash_after_journal(path: Path, payload: dict[str, object]) -> None:
        """Inject a crash immediately after the durable deletion journal.

        Args:
            path: Exact filesystem path.
            payload: Structured operation payload.
        """

        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path.name == "delete-journal.json":
            crashed = True
            raise RuntimeError("simulated crash after durable delete journal")

    monkeypatch.setattr(delete_module, "atomic_json_write", crash_after_journal)
    deletion = GoalDeletionWorkflow(goals)
    with pytest.raises(RuntimeError, match="simulated crash"):
        deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)

    (main_root / "uncommitted.txt").write_text("do not execute cleanup here\n", encoding="utf-8")
    with pytest.raises(GoalLifecycleError, match="must be clean"):
        deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)


def test_goal_delete_resume_rechecks_task_commit_before_worktree_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-journal task commit must stop deletion before its worktree is removed.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

    goals, task_root_list = _accepted_task_create(tmp_path)
    task_root = task_root_list[0]
    real_atomic_json_write = delete_module.atomic_json_write
    crashed = False

    def crash_at_worktree_phase(path: Path, payload: dict[str, object]) -> None:
        """Inject a crash while the deletion journal owns the worktree phase.

        Args:
            path: Exact filesystem path.
            payload: Structured operation payload.
        """

        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path.name == "delete-journal.json" and payload.get("phase") == "worktrees":
            crashed = True
            raise RuntimeError("simulated crash before worktree retirement")

    monkeypatch.setattr(delete_module, "atomic_json_write", crash_at_worktree_phase)
    deletion = GoalDeletionWorkflow(goals)
    with pytest.raises(RuntimeError, match="simulated crash"):
        deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)

    (task_root / "late-user-work.txt").write_text("preserve this commit\n", encoding="utf-8")
    _git(task_root, "add", "late-user-work.txt")
    _git(task_root, "commit", "-m", "Late user work")
    with pytest.raises(GoalLifecycleError, match="Local task ref changed"):
        deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)

    assert task_root.exists()


def test_goal_delete_remote_ref_removal_is_compare_and_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent remote task update must survive the destructive push boundary.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

    goals, _task_root_list = _accepted_task_create(tmp_path)
    main_root = tmp_path / "product-one"
    competitor_root = tmp_path / "competitor"
    subprocess.run(
        [
            "git",
            "clone",
            _git(main_root, "remote", "get-url", "origin"),
            str(competitor_root),
        ],
        check=True,
        capture_output=True,
    )
    _git(competitor_root, "config", "user.email", "test@example.com")
    _git(competitor_root, "config", "user.name", "Test User")
    _git(competitor_root, "checkout", "-b", PREFIX, f"origin/{PREFIX}")
    (competitor_root / "concurrent.txt").write_text("preserve concurrent remote work\n", encoding="utf-8")
    _git(competitor_root, "add", "concurrent.txt")
    _git(competitor_root, "commit", "-m", "Concurrent task work")
    concurrent_commit = _git(competitor_root, "rev-parse", "HEAD")

    deletion = GoalDeletionWorkflow(goals)
    real_run = deletion._git.run
    remote_advanced = False

    def advance_remote_before_delete(
        repository: Path,
        argument_list: list[str],
        **keyword_by_name_map: object,
    ) -> subprocess.CompletedProcess[bytes]:
        """Advance the simulated remote immediately before deletion authorization.

        Args:
            repository: Exact Git repository root.
            argument_list: Exact command arguments.
            **keyword_by_name_map: Additional keyword arguments.

        Returns:
            Completed binary-mode subprocess result.
        """

        nonlocal remote_advanced
        if (
            not remote_advanced
            and argument_list[:1] == ["push"]
            and any(argument.startswith(f"--force-with-lease=refs/heads/{PREFIX}:") for argument in argument_list)
        ):
            _git(competitor_root, "push", "origin", PREFIX)
            remote_advanced = True
        return real_run(repository, argument_list, **keyword_by_name_map)

    monkeypatch.setattr(deletion._git, "run", advance_remote_before_delete)

    with pytest.raises(GoalLifecycleError, match="stale info"):
        deletion.delete(common_prefix=PREFIX, unfinished_goal_absent=True)

    _git(main_root, "fetch", "origin")
    assert remote_advanced
    assert _git(main_root, "rev-parse", f"origin/{PREFIX}") == concurrent_commit
    assert (goals / PREFIX).is_dir()


def test_coordination_rejects_unrelated_dirty_state(tmp_path: Path) -> None:
    """Verify that coordination rejects unrelated dirty state.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    (goals / "dirty.txt").write_text("user state\n", encoding="utf-8")
    with pytest.raises(GoalLifecycleError, match="clean"):
        CoordinationRepository(goals).publish(
            common_prefix=PREFIX,
            message="Forbidden",
            relative_payload_by_path_map={f"{PREFIX}/spec.md": b"# Spec\n"},
        )


def test_recover_main_leak_restores_only_complete_task_overlap(tmp_path: Path) -> None:
    """Verify that recover main leak restores only complete task overlap.

    Args:
        tmp_path: Temporary directory path.
    """

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


@pytest.mark.parametrize(
    ("task_path", "main_path"),
    [
        ("nested/task.txt", "nested"),
        ("nested", "nested/main.txt"),
    ],
)
def test_recover_main_leak_uses_only_changed_main_paths_for_tree_overlap(
    tmp_path: Path,
    task_path: str,
    main_path: str,
) -> None:
    """Ancestor overlap must never manufacture task-only paths for main recovery.

    Args:
        tmp_path: Temporary directory path.
        task_path: Exact filesystem path for task.
        main_path: Exact filesystem path for main.
    """

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
    task_change = task_root / task_path
    task_change.parent.mkdir(parents=True, exist_ok=True)
    task_change.write_text("task bytes\n", encoding="utf-8")
    main_change = project / main_path
    if main_path == "nested":
        main_change.write_text("main bytes\n", encoding="utf-8")
    else:
        main_change.parent.mkdir(parents=True)
        main_change.write_text("main bytes\n", encoding="utf-8")

    workflow.recover_main_leak(
        common_prefix=PREFIX,
        main_repository=project,
        path_list=[main_path],
    )

    assert not main_change.exists()
    assert task_change.read_text(encoding="utf-8") == "task bytes\n"


def test_accept_main_commit_drift_is_exact_and_does_not_cover_later_commit(
    tmp_path: Path,
) -> None:
    """Verify that accept main commit drift is exact and does not cover later commit.

    Args:
        tmp_path: Temporary directory path.
    """

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


def test_sealed_candidate_rejects_prepare_before_coordination_mutation(
    tmp_path: Path,
) -> None:
    """Verify that sealed candidate rejects prepare before coordination mutation.

    Args:
        tmp_path: Temporary directory path.
    """

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
    """Verify that copy resource rejects hardlinked source graph.

    Args:
        tmp_path: Temporary directory path.
    """

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


def test_copy_resource_recovers_an_interrupted_private_staging_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that copy resource recovers an interrupted private staging copy.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    (project / ".gitignore").write_text("/private/\n", encoding="utf-8")
    (project / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: [private/config]
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )
    _git(project, "add", ".gitignore", "worktree-bootstrap.yaml")
    _git(project, "commit", "-m", "Declare bootstrap copy")
    _git(project, "push", "origin", "main")
    source = project / "private" / "config"
    source.mkdir(parents=True)
    (source / "one.txt").write_text("one\n", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "two.txt").write_text("two\n", encoding="utf-8")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    real_copy = resource_module._copy
    crashed = False

    def interrupted_copy(_source: Path, destination: Path) -> None:
        """Leave one partial private staging tree and inject copy failure.

        Args:
            _source: Source filesystem path.
            destination: Destination.
        """

        nonlocal crashed
        if not crashed:
            crashed = True
            destination.mkdir()
            (destination / "partial.txt").write_text("partial\n", encoding="utf-8")
            raise RuntimeError("simulated interrupted resource copy")
        real_copy(_source, destination)

    monkeypatch.setattr(resource_module, "_copy", interrupted_copy)
    workflow = GoalWorktreeWorkflow(goals)
    with pytest.raises(RuntimeError, match="interrupted resource copy"):
        workflow.prepare(
            common_prefix=PREFIX,
            repository_root_list=[project],
            specification_input=spec_input,
        )

    prepared = workflow.prepare(common_prefix=PREFIX, repository_root_list=[project])
    task_copy = Path(prepared["task_root_list"][0]) / "private" / "config"
    assert (task_copy / "one.txt").read_text(encoding="utf-8") == "one\n"
    assert (task_copy / "nested" / "two.txt").read_text(encoding="utf-8") == "two\n"
    assert not (task_copy / "partial.txt").exists()
    transaction_root = (
        Path(
            _git(
                Path(prepared["task_root_list"][0]),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        )
        / "agent-workflows"
        / "task"
        / PREFIX
        / "resource"
    )
    assert not transaction_root.exists() or not any(transaction_root.iterdir())


def test_bootstrap_copy_preserves_internal_links_and_rejects_escape(
    tmp_path: Path,
) -> None:
    """Verify that bootstrap copy preserves internal links and rejects escape.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, _ = _repository_create(tmp_path, "project-goals")
    project, _ = _repository_create(tmp_path, "product-one")
    (project / ".gitignore").write_text("/private/\n", encoding="utf-8")
    (project / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: [private/config]
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )
    _git(project, "add", ".gitignore", "worktree-bootstrap.yaml")
    _git(project, "commit", "-m", "Declare bootstrap copy")
    _git(project, "push", "origin", "main")
    source = project / "private" / "config"
    source.mkdir(parents=True)
    (source / "value.txt").write_text("value\n", encoding="utf-8")
    (source / "alias.txt").symlink_to("value.txt")
    spec_input = tmp_path / "spec-input.md"
    spec_input.write_text("# Spec\n", encoding="utf-8")
    workflow = GoalWorktreeWorkflow(goals)
    prepared = workflow.prepare(
        common_prefix=PREFIX,
        repository_root_list=[project],
        specification_input=spec_input,
    )
    task_alias = Path(prepared["task_root_list"][0]) / "private" / "config" / "alias.txt"
    assert task_alias.is_symlink() and task_alias.readlink() == Path("value.txt")

    task_root = Path(prepared["task_root_list"][0])
    (source / "escape.txt").symlink_to("../../README.md")
    with pytest.raises(GoalLifecycleError, match="escapes"):
        workflow.prepare(common_prefix=PREFIX, repository_root_list=[project])
    assert task_alias.is_symlink()


def test_active_receipt_generation_is_stable_and_stale_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    """Verify that active receipt generation is stable and stale receipt is rejected.

    Args:
        tmp_path: Temporary directory path.
    """

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
    [
        "",
        "/absolute",
        "../escape",
        "nested/../escape",
        "project-goals",
        "nested/project-goals",
    ],
)
def test_checkpoint_document_rejects_noncanonical_or_self_referential_project_path(
    project_path: str,
) -> None:
    """Verify that checkpoint document rejects noncanonical or self referential project path.

    Args:
        project_path: Exact filesystem path for project.
    """

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


def test_checkpoint_document_rejects_a_changed_participant_set() -> None:
    """Every append-only checkpoint must remain one complete fixed project snapshot."""

    with pytest.raises(GoalLifecycleError, match="same complete participant set"):
        CheckpointDocument.from_payload(
            {
                "schema_version": 1,
                "accepted_checkpoint_id": "",
                "checkpoint_list": [
                    {
                        "checkpoint_id": "checkpoint-0001",
                        "project_list": [
                            {"project_path": "one", "git_commit_final": "a" * 40},
                            {"project_path": "two", "git_commit_final": "b" * 40},
                        ],
                    },
                    {
                        "checkpoint_id": "checkpoint-0002",
                        "project_list": [
                            {"project_path": "one", "git_commit_final": "c" * 40},
                        ],
                    },
                ],
            }
        )


class _CrashAfterImplementationMainPushGit(Git):
    """Simulate process loss after remote CAS succeeds but before local fast-forward."""

    def __init__(self, *, implementation_root: Path) -> None:
        """Initialize the crash after implementation main push Git dependencies.

        Args:
            implementation_root: Implementation root.
        """

        self._implementation_root = implementation_root.resolve(strict=True)
        self.did_crash = False

    def run(self, repository: Path, argument_list: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        """Run the crash after implementation main push Git operation.

        Args:
            repository: Exact Git repository root.
            argument_list: Exact command arguments.
            **kwargs: Provider keyword arguments.

        Returns:
            Completed binary-mode subprocess result.
        """

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


def test_merge_resumes_after_remote_push_before_local_fast_forward(
    tmp_path: Path,
) -> None:
    """Verify that merge resumes after remote push before local fast forward.

    Args:
        tmp_path: Temporary directory path.
    """

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


def test_failed_acceptance_is_superseded_only_by_full_descendant_checkpoint(
    tmp_path: Path,
) -> None:
    """Verify that failed acceptance is superseded only by full descendant checkpoint.

    Args:
        tmp_path: Temporary directory path.
    """

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


def test_fix_forward_rejects_a_corrupted_previous_checkpoint_journal(
    tmp_path: Path,
) -> None:
    """Only the exact tracked interrupted checkpoint may authorize fix-forward.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    task_root = task_root_list[0]
    _task_commit_push(task_root)
    first_checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    GoalMergeWorkflow(goals).merge(
        common_prefix=PREFIX,
        checkpoint_id=first_checkpoint_id,
    )
    (task_root / "fix.txt").write_text("fix-forward\n", encoding="utf-8")
    _task_commit_push(task_root, message="Fix acceptance")
    second_checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    journal_path = CoordinationRepository(goals).journal_path_get(PREFIX, "merge")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["project_list"][0]["git_commit_final"] = "0" * 40
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(GoalLifecycleError, match="differs from the selected checkpoint"):
        GoalMergeWorkflow(goals).merge(
            common_prefix=PREFIX,
            checkpoint_id=second_checkpoint_id,
        )


def test_fix_forward_rejects_a_checkpoint_older_than_the_interrupted_checkpoint(
    tmp_path: Path,
) -> None:
    """A resumable partial merge can move only to a newer full checkpoint.

    Args:
        tmp_path: Temporary directory path.
    """

    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    task_root = task_root_list[0]
    _task_commit_push(task_root)
    first_checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    GoalMergeWorkflow(goals).merge(
        common_prefix=PREFIX,
        checkpoint_id=first_checkpoint_id,
    )
    (task_root / "fix.txt").write_text("fix-forward\n", encoding="utf-8")
    _task_commit_push(task_root, message="Fix acceptance")
    second_checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    GoalMergeWorkflow(goals).merge(
        common_prefix=PREFIX,
        checkpoint_id=second_checkpoint_id,
    )

    with pytest.raises(GoalLifecycleError, match="must follow the interrupted checkpoint"):
        GoalMergeWorkflow(goals).merge(
            common_prefix=PREFIX,
            checkpoint_id=first_checkpoint_id,
        )


def test_fix_forward_merge_recovers_after_owner_advance_before_journal_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry accepts an already-advanced exact owner with the previous journal.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest mutation fixture.
    """

    goals, task_root_list, _workflow = _active_task_create(tmp_path)
    task_root = task_root_list[0]
    _task_commit_push(task_root)
    first_checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    GoalMergeWorkflow(goals).merge(common_prefix=PREFIX, checkpoint_id=first_checkpoint_id)
    (task_root / "fix.txt").write_text("fix-forward\n", encoding="utf-8")
    _task_commit_push(task_root, message="Fix acceptance")
    second_checkpoint_id, _ = GoalCheckpointPublisher(goals).publish(
        common_prefix=PREFIX,
        project_root_list=task_root_list,
    )
    real_atomic_json_write = merge_owner_module.atomic_json_write
    crashed = False

    def crash_after_owner_advance(path: Path, payload: dict[str, object]) -> None:
        """Inject a crash after the accepted-checkpoint owner advances.

        Args:
            path: Exact filesystem path.
            payload: Structured operation payload.
        """

        nonlocal crashed
        real_atomic_json_write(path, payload)
        if not crashed and path.name == "merge-owner.json" and payload.get("checkpoint_id") == second_checkpoint_id:
            crashed = True
            raise RuntimeError("simulated crash after owner advance")

    monkeypatch.setattr(merge_owner_module, "atomic_json_write", crash_after_owner_advance)
    with pytest.raises(RuntimeError, match="simulated crash after owner advance"):
        GoalMergeWorkflow(goals).merge(common_prefix=PREFIX, checkpoint_id=second_checkpoint_id)
    monkeypatch.setattr(merge_owner_module, "atomic_json_write", real_atomic_json_write)

    result = GoalMergeWorkflow(goals).merge(common_prefix=PREFIX, checkpoint_id=second_checkpoint_id)
    assert result["checkpoint_id"] == second_checkpoint_id
    assert result["phase"] == "awaiting-acceptance"


class _ConcurrentCoordinationPushGit(Git):
    """Inject one real remote main commit immediately before the tested CAS push."""

    def __init__(self, *, concurrent_root: Path, path: str) -> None:
        """Initialize the concurrent coordination push Git dependencies.

        Args:
            concurrent_root: Concurrent root.
            path: Exact filesystem path.
        """

        self._concurrent_root = concurrent_root
        self._path = path
        self._did_publish = False

    def run(self, repository: Path, argument_list: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        """Run the concurrent coordination push Git operation.

        Args:
            repository: Exact Git repository root.
            argument_list: Exact command arguments.
            **kwargs: Provider keyword arguments.

        Returns:
            Completed binary-mode subprocess result.
        """

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


def test_coordination_replays_disjoint_remote_update_and_rejects_same_path_conflict(
    tmp_path: Path,
) -> None:
    """Verify that coordination replays disjoint remote update and rejects same path conflict.

    Args:
        tmp_path: Temporary directory path.
    """

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


def test_self_hosting_bootstrap_exception_is_removed_with_carriers_only_by_goal_delete(
    tmp_path: Path,
) -> None:
    """Verify that self hosting bootstrap exception is removed with carriers only by goal delete.

    Args:
        tmp_path: Temporary directory path.
    """

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
