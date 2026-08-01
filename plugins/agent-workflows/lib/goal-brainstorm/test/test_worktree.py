"""Behavior tests for goal-brainstorm worktree preparation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIBRARY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve().parents[3] / "skills" / "goal-brainstorm" / "scripts" / "worktree.py"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from worktree import (
    GitCommand,
    WorktreeError,
    WorktreeWorkflow,
    worktree_activate,
    worktree_contracts_authored,
    worktree_main_commit_drift_accept,
    worktree_main_leak_recover,
    worktree_prepare,
    worktree_seal,
    worktree_validate,
)

TASK_PREFIX = "2026-07-30-example-isolation"


def _git_run(repository: Path, argument_list: list[str]) -> str:
    """Run one checked Git command for a test repository.

    Args:
        repository: Git working directory.
        argument_list: Git arguments after the executable.

    Returns:
        Standard output text.
    """

    result = subprocess.run(
        ["git", "-C", str(repository), *argument_list],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository_create(repository: Path) -> None:
    """Create one clean standalone test repository.

    Args:
        repository: Repository root to create.
    """

    repository.mkdir()
    _git_run(repository, ["init", "-b", "main"])
    _git_run(repository, ["config", "user.email", "test@example.com"])
    _git_run(repository, ["config", "user.name", "Worktree Test"])
    (repository / ".gitignore").write_text("/.spec/\n/local/\n", encoding="utf-8")
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    _git_run(repository, ["add", ".gitignore", "README.md"])
    _git_run(repository, ["commit", "-m", "Initial test state"])


def _specification_create(coordinating_repository: Path, task_prefix: str = TASK_PREFIX) -> Path:
    """Create one physical ignored task specification.

    Args:
        coordinating_repository: Coordinating main-worktree root.
        task_prefix: Exact common task prefix.

    Returns:
        Root-relative specification path.
    """

    specification = Path(".spec") / f"{task_prefix}-spec.md"
    specification_path = coordinating_repository / specification
    specification_path.parent.mkdir()
    specification_path.write_text("# Test specification\n", encoding="utf-8")
    return specification


def _task_root_get(repository: Path, task_prefix: str = TASK_PREFIX) -> Path:
    """Return one test task-worktree root.

    Args:
        repository: Main-worktree root.
        task_prefix: Exact common task prefix.

    Returns:
        Exact task-worktree root.
    """

    return repository / ".worktree" / task_prefix


def _private_git_path_get(task_root: Path, relative_path: str) -> Path:
    """Return one absolute private Git path for a test task worktree."""

    path = Path(_git_run(task_root, ["rev-parse", "--git-path", relative_path]))
    return path if path.is_absolute() else task_root / path


def _pending_worktree_create(
    coordinating_repository: Path,
    repository: Path,
    specification: Path,
) -> None:
    """Record provider ownership before a test simulates interrupted creation."""

    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    workflow._pending_worktree_create(  # noqa: SLF001 - white-box crash-recovery test
        _git_run(repository, ["rev-parse", "HEAD"]),
        repository,
    )


def test_prepare_validate_and_seal_multi_repository_task(tmp_path: Path) -> None:
    """Preparation must isolate two repositories and seal one physical task pair.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    specification = _specification_create(coordinating_repository)

    result = json.loads(
        worktree_prepare(
            coordinating_repository,
            specification,
            [other_repository],
        )
    )

    assert result["lifecycle_state"] == "repository_prepared"
    assert result["task_prefix"] == TASK_PREFIX
    assert result["task_root_list"] == [
        str(_task_root_get(coordinating_repository)),
        str(_task_root_get(other_repository)),
    ]
    for repository in (coordinating_repository, other_repository):
        task_root = _task_root_get(repository)
        assert _git_run(task_root, ["branch", "--show-current"]) == TASK_PREFIX
        assert (task_root / "worktree-bootstrap.toml").read_text(encoding="utf-8").startswith("schema_version = 1")
        assert (task_root / ".spec").is_symlink()
        assert (task_root / ".spec").resolve() == coordinating_repository / ".spec"
        gitignore_text = (task_root / ".gitignore").read_text(encoding="utf-8")
        assert "/.spec\n" in gitignore_text
        assert "/.worktree/\n" in gitignore_text
        assert _git_run(repository, ["branch", "--show-current"]) == "main"
        assert _git_run(repository, ["status", "--short"]) == ""

    validate_result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))
    assert validate_result["performed_repair_list"] == []

    contracts_result = json.loads(worktree_contracts_authored(coordinating_repository, specification))
    assert contracts_result["lifecycle_state"] == "contracts_authored"
    assert contracts_result["performed_repair_list"] == []
    assert (
        json.loads(worktree_contracts_authored(coordinating_repository, specification))["performed_repair_list"] == []
    )
    assert (
        json.loads(worktree_validate(coordinating_repository, "contracts_authored", specification))["lifecycle_state"]
        == "contracts_authored"
    )
    late_contract_repository = tmp_path / "late-contract"
    _repository_create(late_contract_repository)
    with pytest.raises(WorktreeError, match="cannot add repositories after contracts_authored"):
        worktree_prepare(
            coordinating_repository,
            specification,
            [other_repository, late_contract_repository],
        )
    assert not _task_root_get(late_contract_repository).exists()
    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    (coordinating_repository / goal).write_text("# Test goal\n", encoding="utf-8")
    seal_result = json.loads(worktree_seal(coordinating_repository, goal, specification))
    assert seal_result["lifecycle_state"] == "goal_ready"
    with pytest.raises(WorktreeError, match="only from repository_prepared or contracts_authored"):
        worktree_contracts_authored(coordinating_repository, specification)
    assert (
        json.loads(worktree_validate(coordinating_repository, "goal_ready", specification))["lifecycle_state"]
        == "goal_ready"
    )
    assert (
        json.loads(worktree_prepare(coordinating_repository, specification, [other_repository]))["lifecycle_state"]
        == "goal_ready"
    )
    late_repository = tmp_path / "late"
    _repository_create(late_repository)
    with pytest.raises(WorktreeError, match="cannot add repositories to a sealed task set"):
        worktree_prepare(coordinating_repository, specification, [other_repository, late_repository])
    assert not (_task_root_get(late_repository)).exists()

    (coordinating_repository / specification).write_text("# Changed after seal\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="Sealed specification changed"):
        worktree_validate(coordinating_repository, "goal_ready", specification)
    with pytest.raises(WorktreeError, match="Sealed specification changed"):
        worktree_prepare(coordinating_repository, specification, [other_repository])
    reseal_result = json.loads(worktree_seal(coordinating_repository, goal, specification))
    assert reseal_result["lifecycle_state"] == "goal_ready"
    assert (
        json.loads(worktree_validate(coordinating_repository, "goal_ready", specification))["performed_repair_list"]
        == []
    )


def test_prepare_authors_the_first_project_gitignore(tmp_path: Path) -> None:
    """A participating repository may adopt the contract without a prior ignore file.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    _git_run(other_repository, ["rm", ".gitignore"])
    _git_run(other_repository, ["commit", "-m", "Remove project ignore file"])
    specification = _specification_create(coordinating_repository)

    worktree_prepare(coordinating_repository, specification, [other_repository])

    other_task_root = _task_root_get(other_repository)
    assert (other_task_root / ".gitignore").read_text(encoding="utf-8") == "/.spec\n/.worktree/\n"
    assert _git_run(other_task_root, ["status", "--short", "--", ".gitignore"]) == "?? .gitignore"
    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    worktree_contracts_authored(coordinating_repository, specification)
    (coordinating_repository / goal).write_text("# Test goal\n", encoding="utf-8")
    worktree_seal(coordinating_repository, goal, specification)
    assert (
        json.loads(worktree_validate(coordinating_repository, "goal_ready", specification))["performed_repair_list"]
        == []
    )


def test_activate_records_persistent_goal_state_and_keeps_task_artifacts_sealed(tmp_path: Path) -> None:
    """Activation must be explicit, idempotent, and retain sealed inputs.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    with pytest.raises(WorktreeError, match="must be sealed"):
        worktree_activate(coordinating_repository, specification)

    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    goal_path = coordinating_repository / goal
    worktree_contracts_authored(coordinating_repository, specification)
    goal_path.write_text("# Test goal\n", encoding="utf-8")
    worktree_seal(coordinating_repository, goal, specification)

    activation_result = json.loads(worktree_activate(coordinating_repository, specification))

    assert activation_result["lifecycle_state"] == "active"
    assert activation_result["performed_repair_list"] == []
    assert json.loads(worktree_activate(coordinating_repository, specification))["lifecycle_state"] == "active"
    assert (
        json.loads(worktree_validate(coordinating_repository, "active", specification))["performed_repair_list"] == []
    )
    assert json.loads(worktree_prepare(coordinating_repository, specification, []))["lifecycle_state"] == "active"
    with pytest.raises(WorktreeError, match="persistent goal is active"):
        worktree_seal(coordinating_repository, goal, specification)

    for artifact_path in (coordinating_repository / specification, goal_path):
        original_content = artifact_path.read_text(encoding="utf-8")
        artifact_path.write_text(f"{original_content}changed\n", encoding="utf-8")
        with pytest.raises(WorktreeError, match="Sealed"):
            worktree_validate(coordinating_repository, "active", specification)
        with pytest.raises(WorktreeError, match="Sealed"):
            worktree_prepare(coordinating_repository, specification, [])
        artifact_path.write_text(original_content, encoding="utf-8")
        assert (
            json.loads(worktree_validate(coordinating_repository, "active", specification))["performed_repair_list"]
            == []
        )


def test_prepare_accepts_an_arbitrary_valid_exact_prefix(tmp_path: Path) -> None:
    """Task identity must derive from content-independent valid prefix text.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    task_prefix = "opaque-task-27"
    specification = _specification_create(coordinating_repository, task_prefix)

    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    task_root = _task_root_get(coordinating_repository, task_prefix)
    assert result["task_prefix"] == task_prefix
    assert result["task_root_list"] == [str(task_root)]
    assert _git_run(task_root, ["branch", "--show-current"]) == task_prefix


def test_prepare_rejects_invalid_or_non_physical_task_pair_paths(tmp_path: Path) -> None:
    """Task-pair paths and prefixes must fail before worktree mutation.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    invalid_specification = Path(".spec") / "invalid prefix-spec.md"
    invalid_specification_path = coordinating_repository / invalid_specification
    invalid_specification_path.parent.mkdir()
    invalid_specification_path.write_text("# Invalid prefix\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="valid unchanged Git branch name"):
        worktree_prepare(coordinating_repository, invalid_specification, [])

    outside_specification = Path("other") / f"{TASK_PREFIX}-spec.md"
    outside_specification_path = coordinating_repository / outside_specification
    outside_specification_path.parent.mkdir()
    outside_specification_path.write_text("# Wrong owner\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="direct child of .spec"):
        worktree_prepare(coordinating_repository, outside_specification, [])
    assert not (coordinating_repository / ".worktree").exists()


def test_prepare_requires_a_physical_untracked_specification(tmp_path: Path) -> None:
    """The coordinating artifact must not be a link or a forced tracked file.

    Args:
        tmp_path: Isolated filesystem root.
    """

    linked_repository = tmp_path / "linked"
    _repository_create(linked_repository)
    linked_specification = _specification_create(linked_repository)
    linked_specification_path = linked_repository / linked_specification
    external_specification_path = tmp_path / "external-specification.md"
    external_specification_path.write_text("# External\n", encoding="utf-8")
    linked_specification_path.unlink()
    linked_specification_path.symlink_to(external_specification_path)
    with pytest.raises(WorktreeError, match="physical ordinary file"):
        worktree_prepare(linked_repository, linked_specification, [])

    tracked_repository = tmp_path / "tracked"
    _repository_create(tracked_repository)
    tracked_specification = _specification_create(tracked_repository)
    _git_run(tracked_repository, ["add", "--force", tracked_specification.as_posix()])
    _git_run(tracked_repository, ["commit", "-m", "Force-track task specification"])
    with pytest.raises(WorktreeError, match="remain untracked by Git"):
        worktree_prepare(tracked_repository, tracked_specification, [])


def test_prepare_rejects_missing_physical_spec_ignore_before_worktree_mutation(tmp_path: Path) -> None:
    """Preparation must fail before creating a worktree when main does not ignore `.spec`.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / ".gitignore").write_text("/local/\n", encoding="utf-8")
    _git_run(coordinating_repository, ["commit", "-am", "Remove specification ignore"])
    specification = _specification_create(coordinating_repository)

    with pytest.raises(WorktreeError, match="physical artifact directory"):
        worktree_prepare(coordinating_repository, specification, [])

    assert not (coordinating_repository / ".worktree").exists()


def test_prepare_authors_task_ignore_before_a_spec_link_survives_later_failure(tmp_path: Path) -> None:
    """A dirty main-only ignore must not leave an exposed unignored task link.

    Args:
        tmp_path: Isolated filesystem root.
    """

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    (coordinating_repository / ".gitignore").write_text("/local/\n", encoding="utf-8")
    _git_run(coordinating_repository, ["commit", "-am", "Remove specification ignore"])
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    unavailable_commit = "a" * 40
    _git_run(
        coordinating_repository,
        ["update-index", "--cacheinfo", f"160000,{unavailable_commit},dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-m", "Record unavailable gitlink"])
    specification = _specification_create(coordinating_repository)
    (coordinating_repository / ".gitignore").write_text("/local/\n/.spec\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Git command failed.*submodule update"):
        worktree_prepare(coordinating_repository, specification, [])

    task_root = _task_root_get(coordinating_repository)
    spec_link = task_root / ".spec"
    assert spec_link.is_symlink()
    assert _git_run(task_root, ["check-ignore", "-v", "--no-index", ".spec"]).startswith(".gitignore:")
    assert "/.spec\n" in (task_root / ".gitignore").read_text(encoding="utf-8")


def test_prepare_materializes_arbitrary_copy_and_link_resources(tmp_path: Path) -> None:
    """Manifest classes must work without project-name or path-name knowledge.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    copy_source = coordinating_repository / "local" / "opaque-one"
    trailing_copy_source = coordinating_repository / "opaque cache "
    link_source = coordinating_repository / "local" / "opaque-two"
    copy_source.parent.mkdir()
    copy_source.write_text("copy source\n", encoding="utf-8")
    trailing_copy_source.write_text("trailing copy source\n", encoding="utf-8")
    link_source.write_text("link source\n", encoding="utf-8")
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        'copy_optional_path_list = ["local/absent"]\n'
        'copy_required_path_list = ["local/opaque-one", "opaque cache "]\n'
        "link_optional_path_list = []\n"
        'link_required_path_list = ["local/opaque-two"]\n',
        encoding="utf-8",
    )

    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    copy_destination = task_root / "local" / "opaque-one"
    trailing_copy_destination = task_root / "opaque cache "
    link_destination = task_root / "local" / "opaque-two"
    assert copy_destination.read_text(encoding="utf-8") == "copy source\n"
    assert not copy_destination.is_symlink()
    assert trailing_copy_destination.read_text(encoding="utf-8") == "trailing copy source\n"
    assert _git_run(task_root, ["check-ignore", "--no-index", trailing_copy_destination.name])
    assert link_destination.is_symlink()
    assert link_destination.resolve() == link_source
    assert result["skipped_optional_resource_list"] == [f"{coordinating_repository}:local/absent"]

    copy_destination.write_text("task-only change\n", encoding="utf-8")
    worktree_validate(coordinating_repository, "repository_prepared", specification)
    assert copy_source.read_text(encoding="utf-8") == "copy source\n"

    copy_destination.unlink()
    deletion_result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))
    assert not copy_destination.exists()
    assert not any("repaired copy resource" in item for item in deletion_result["performed_repair_list"])

    link_source.write_text("main drift\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="Shared link source changed"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)


def test_validate_rejects_ignored_main_drift_below_a_copy_resource_directory(tmp_path: Path) -> None:
    """A copy snapshot source remains a protected main path boundary.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "local" / "nested" / "value.txt"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("snapshot\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/nested"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    worktree_prepare(coordinating_repository, specification, [])
    source_path.write_text("main drift\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Copy resource source changed during task execution"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (task_root / "local" / "nested" / "value.txt").read_text(encoding="utf-8") == "snapshot\n"


def test_validate_rejects_a_same_content_main_commit_below_a_copy_resource_directory(tmp_path: Path) -> None:
    """Commit drift cannot bypass a resource boundary when bytes remain unchanged.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "runtime" / "nested" / "value.txt"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("snapshot\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["runtime/nested"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    worktree_prepare(coordinating_repository, specification, [])
    _git_run(coordinating_repository, ["add", "--force", "runtime/nested/value.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Commit protected resource source"])

    with pytest.raises(WorktreeError, match="Main commit drift overlaps task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (task_root / "runtime" / "nested" / "value.txt").read_text(encoding="utf-8") == "snapshot\n"


def test_main_leak_recovery_restores_a_copy_resource_source_preimage(tmp_path: Path) -> None:
    """Explicit provenance must recover a leaked ignored copy edit without losing task work.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "local" / "copy.txt"
    source_path.parent.mkdir()
    source_path.write_text("source A\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/copy.txt"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    worktree_prepare(coordinating_repository, specification, [])
    task_copy_path = task_root / "local" / "copy.txt"
    task_copy_path.write_text("task copy B\n", encoding="utf-8")
    source_path.write_text("task copy B\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Copy resource source changed during task execution"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    result = json.loads(
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("local/copy.txt")],
        )
    )

    assert source_path.read_text(encoding="utf-8") == "source A\n"
    assert task_copy_path.read_text(encoding="utf-8") == "task copy B\n"
    assert any("restored resource source preimage" in item for item in result["performed_repair_list"])


@pytest.mark.parametrize("leak_kind", ["edit", "delete"])
def test_main_leak_recovery_restores_an_exact_link_resource_source_preimage(
    tmp_path: Path,
    leak_kind: str,
) -> None:
    """Exact shared links must compare and recover through their effective target.

    Args:
        tmp_path: Isolated filesystem root.
        leak_kind: Whether the leaked task operation edited or deleted the source.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "local" / "shared.txt"
    source_path.parent.mkdir()
    source_path.write_text("source A\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        "copy_required_path_list = []\n"
        "link_optional_path_list = []\n"
        'link_required_path_list = ["local/shared.txt"]\n',
        encoding="utf-8",
    )
    worktree_prepare(coordinating_repository, specification, [])
    task_link_path = task_root / "local" / "shared.txt"
    assert task_link_path.is_symlink()
    if leak_kind == "edit":
        task_link_path.write_text("task B\n", encoding="utf-8")
    else:
        source_path.unlink()

    result = json.loads(
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("local/shared.txt")],
        )
    )

    assert source_path.read_text(encoding="utf-8") == "source A\n"
    assert task_link_path.is_symlink()
    assert task_link_path.resolve() == source_path
    assert task_link_path.read_text(encoding="utf-8") == "source A\n"
    assert any("restored resource source preimage" in item for item in result["performed_repair_list"])


def test_main_leak_recovery_rejects_a_staged_resource_source(tmp_path: Path) -> None:
    """Resource recovery must not discard a staged main-source object.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "local" / "copy.txt"
    source_path.parent.mkdir()
    source_path.write_text("source A\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/copy.txt"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    worktree_prepare(coordinating_repository, specification, [])
    (task_root / "local" / "copy.txt").write_text("task B\n", encoding="utf-8")
    source_path.write_text("task B\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "--force", "local/copy.txt"])
    staged_object_before = _git_run(
        coordinating_repository,
        ["ls-files", "--stage", "--", "local/copy.txt"],
    )

    with pytest.raises(WorktreeError, match="staged resource source"):
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("local/copy.txt")],
        )

    assert source_path.read_text(encoding="utf-8") == "task B\n"
    assert _git_run(coordinating_repository, ["ls-files", "--stage", "--", "local/copy.txt"]) == staged_object_before


def test_prepare_enforces_required_and_materializes_present_optional_resources(tmp_path: Path) -> None:
    """Required absence must fail while present optional resources materialize normally.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    manifest_path = task_root / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/required-absent"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    with pytest.raises(WorktreeError, match="Required copy resource does not exist"):
        worktree_prepare(coordinating_repository, specification, [])

    optional_source = coordinating_repository / "local" / "optional-present"
    optional_source.parent.mkdir(exist_ok=True)
    optional_source.write_text("optional\n", encoding="utf-8")
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        'copy_optional_path_list = ["local/optional-present"]\n'
        "copy_required_path_list = []\n"
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )

    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    assert (task_root / "local" / "optional-present").read_text(encoding="utf-8") == "optional\n"
    assert result["skipped_optional_resource_list"] == []


@pytest.mark.parametrize("strategy", ["copy", "link"])
def test_prepare_rejects_an_unrecorded_matching_resource_destination(tmp_path: Path, strategy: str) -> None:
    """Matching content or targets must not turn unknown task objects into provider state.

    Args:
        tmp_path: Isolated filesystem root.
        strategy: Resource class whose matching destination is still unrecorded.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "local" / "matching"
    source_path.parent.mkdir()
    source_path.write_text("same content\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    destination_path = task_root / "local" / "matching"
    destination_path.parent.mkdir()
    if strategy == "copy":
        destination_path.write_text("same content\n", encoding="utf-8")
    else:
        destination_path.symlink_to(os.path.relpath(source_path, start=destination_path.parent))
    copy_required_path_list = '["local/matching"]' if strategy == "copy" else "[]"
    link_required_path_list = '["local/matching"]' if strategy == "link" else "[]"
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        f"copy_required_path_list = {copy_required_path_list}\n"
        "link_optional_path_list = []\n"
        f"link_required_path_list = {link_required_path_list}\n",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeError, match="unrecorded independent content"):
        worktree_prepare(coordinating_repository, specification, [])

    if strategy == "copy":
        assert destination_path.read_text(encoding="utf-8") == "same content\n"
    else:
        assert destination_path.is_symlink()
        assert destination_path.resolve() == source_path


def test_prepare_applies_an_unmodified_resource_strategy_change(tmp_path: Path) -> None:
    """An explicit class change may replace only an untouched provider object.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "local" / "strategy"
    source_path.parent.mkdir()
    source_path.write_text("source\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    manifest_path = task_root / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/strategy"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    worktree_prepare(coordinating_repository, specification, [])
    destination_path = task_root / "local" / "strategy"
    assert not destination_path.is_symlink()
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        "copy_required_path_list = []\n"
        "link_optional_path_list = []\n"
        'link_required_path_list = ["local/strategy"]\n',
        encoding="utf-8",
    )

    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    assert destination_path.is_symlink()
    assert destination_path.resolve() == source_path
    assert any("resource strategy change" in item for item in result["performed_repair_list"])


@pytest.mark.parametrize(
    ("initial_strategy", "next_strategy", "error_pattern"),
    [
        ("copy", "link", "Shared link source changed"),
        ("link", "copy", "Cannot recreate copy after its source changed"),
    ],
)
def test_prepare_preserves_the_previous_resource_when_strategy_source_preflight_fails(
    tmp_path: Path,
    initial_strategy: str,
    next_strategy: str,
    error_pattern: str,
) -> None:
    """A strategy transition must not delete its last good destination before preflight.

    Args:
        tmp_path: Isolated filesystem root.
        initial_strategy: Initially recorded resource strategy.
        next_strategy: Requested replacement strategy.
        error_pattern: Expected source-drift diagnostic.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "local" / "strategy"
    source_path.parent.mkdir()
    source_path.write_text("source v1\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    manifest_path = task_root / "worktree-bootstrap.toml"

    def manifest_text(strategy: str) -> str:
        copy_path_list = '["local/strategy"]' if strategy == "copy" else "[]"
        link_path_list = '["local/strategy"]' if strategy == "link" else "[]"
        return (
            "schema_version = 1\n\n"
            "[resource]\n"
            "copy_optional_path_list = []\n"
            f"copy_required_path_list = {copy_path_list}\n"
            "link_optional_path_list = []\n"
            f"link_required_path_list = {link_path_list}\n"
        )

    manifest_path.write_text(manifest_text(initial_strategy), encoding="utf-8")
    worktree_prepare(coordinating_repository, specification, [])
    destination_path = task_root / "local" / "strategy"
    previous_link_target = os.readlink(destination_path) if destination_path.is_symlink() else None
    source_path.write_text("source v2\n", encoding="utf-8")
    manifest_path.write_text(manifest_text(next_strategy), encoding="utf-8")

    with pytest.raises(WorktreeError, match=error_pattern):
        worktree_prepare(coordinating_repository, specification, [])

    if initial_strategy == "copy":
        assert not destination_path.is_symlink()
        assert destination_path.read_text(encoding="utf-8") == "source v1\n"
    else:
        assert destination_path.is_symlink()
        assert os.readlink(destination_path) == previous_link_target


@pytest.mark.parametrize("strategy", ["copy", "link"])
def test_prepare_recovers_pending_initial_resource_materialization_across_repositories(
    tmp_path: Path,
    strategy: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later repository failure must leave durable ownership for an exposed resource.

    Args:
        tmp_path: Isolated filesystem root.
        strategy: Initial materialization strategy.
    """

    coordinating_repository = tmp_path / "coordinating"
    failing_repository = tmp_path / "failing"
    _repository_create(coordinating_repository)
    _repository_create(failing_repository)
    copy_path_list = '["local/pending"]' if strategy == "copy" else "[]"
    link_path_list = '["local/pending"]' if strategy == "link" else "[]"
    (coordinating_repository / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        f"copy_required_path_list = {copy_path_list}\n"
        "link_optional_path_list = []\n"
        f"link_required_path_list = {link_path_list}\n",
        encoding="utf-8",
    )
    (failing_repository / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = []
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )
    for repository in (coordinating_repository, failing_repository):
        _git_run(repository, ["add", "worktree-bootstrap.toml"])
        _git_run(repository, ["commit", "-m", "Add bootstrap manifest"])
    specification = _specification_create(coordinating_repository)
    source_path = coordinating_repository / "local" / "pending"
    source_path.parent.mkdir()
    source_path.write_text("pending source\n", encoding="utf-8")
    coordinating_task_root = _task_root_get(coordinating_repository)
    failing_task_root = _task_root_get(failing_repository)
    _pending_worktree_create(coordinating_repository, coordinating_repository, specification)
    _pending_worktree_create(coordinating_repository, failing_repository, specification)
    _git_run(
        coordinating_repository,
        ["worktree", "add", "-b", TASK_PREFIX, str(coordinating_task_root), "HEAD"],
    )
    _git_run(
        failing_repository,
        ["worktree", "add", "-b", TASK_PREFIX, str(failing_task_root), "HEAD"],
    )
    original_resource_prepare = WorktreeWorkflow._resource_state_list_prepare

    def resource_prepare_with_late_failure(
        workflow: WorktreeWorkflow,
        main_root: Path,
        *argument_list: object,
        **keyword_argument_map: object,
    ) -> object:
        if main_root == failing_repository:
            raise WorktreeError("injected later repository failure")
        return original_resource_prepare(
            workflow,
            main_root,
            *argument_list,
            **keyword_argument_map,
        )

    monkeypatch.setattr(
        WorktreeWorkflow,
        "_resource_state_list_prepare",
        resource_prepare_with_late_failure,
    )
    with pytest.raises(WorktreeError, match="injected later repository failure"):
        worktree_prepare(coordinating_repository, specification, [failing_repository])
    monkeypatch.setattr(
        WorktreeWorkflow,
        "_resource_state_list_prepare",
        original_resource_prepare,
    )

    destination_path = coordinating_task_root / "local" / "pending"
    assert os.path.lexists(destination_path)
    destination_path.unlink()

    result = json.loads(worktree_prepare(coordinating_repository, specification, [failing_repository]))

    assert result["lifecycle_state"] == "repository_prepared"
    if strategy == "copy":
        assert destination_path.read_text(encoding="utf-8") == "pending source\n"
        assert not destination_path.is_symlink()
    else:
        assert destination_path.is_symlink()
        assert destination_path.resolve() == source_path
    transaction_root = Path(
        _git_run(
            coordinating_task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/resource-transaction-v1"],
        )
    )
    if not transaction_root.is_absolute():
        transaction_root = coordinating_task_root / transaction_root
    assert not transaction_root.exists() or list(transaction_root.iterdir()) == []


@pytest.mark.parametrize(
    "resource_path",
    [
        "",
        "/absolute",
        "../escape",
        "nested/../escape",
        ".git/config",
        ".spec/task",
        ".worktree/task",
        "worktree-bootstrap.toml",
        "local/*.env",
        "local/[x]",
        "line\nbreak",
        "carriage\rreturn",
    ],
)
def test_prepare_rejects_invalid_manifest_resource_paths(tmp_path: Path, resource_path: str) -> None:
    """Closed manifest validation must reject reserved and escaping paths.

    Args:
        tmp_path: Isolated filesystem root.
        resource_path: Invalid manifest path.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        f"copy_required_path_list = [{json.dumps(resource_path)}]\n"
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeError, match="Manifest resource path|reserved"):
        worktree_prepare(coordinating_repository, specification, [])


@pytest.mark.parametrize(
    ("copy_path_list_text", "extra_text", "expected_message"),
    [
        ('["local/one", "local/one"]', "", "duplicate resource paths"),
        ('["local/one", "local/one/child"]', "", "overlapping resource paths"),
        ("[]", "unknown_path_list = []\n", "unsupported resource schema"),
    ],
)
def test_prepare_rejects_non_closed_or_overlapping_manifest_content(
    tmp_path: Path,
    copy_path_list_text: str,
    extra_text: str,
    expected_message: str,
) -> None:
    """Manifest validation must reject ambiguity before filesystem writes.

    Args:
        tmp_path: Isolated filesystem root.
        copy_path_list_text: TOML list for the tested copy class.
        extra_text: Optional unsupported manifest content.
        expected_message: Required diagnostic fragment.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        f"copy_required_path_list = {copy_path_list_text}\n"
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n"
        f"{extra_text}",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeError, match=expected_message):
        worktree_prepare(coordinating_repository, specification, [])


def test_prepare_rejects_boolean_manifest_schema_version(tmp_path: Path) -> None:
    """A TOML boolean must not pass the integer schema-version boundary.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    manifest_path = _task_root_get(coordinating_repository) / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = true\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        "copy_required_path_list = []\n"
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeError, match="unsupported root schema"):
        worktree_prepare(coordinating_repository, specification, [])


def test_prepare_rejects_a_symbolic_bootstrap_manifest(tmp_path: Path) -> None:
    """The project contract manifest must remain physically inside its task root.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    manifest_path = _task_root_get(coordinating_repository) / "worktree-bootstrap.toml"
    external_manifest_path = tmp_path / "external-manifest.toml"
    external_manifest_path.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
    manifest_path.unlink()
    manifest_path.symlink_to(external_manifest_path)

    with pytest.raises(WorktreeError, match="physical ordinary file"):
        worktree_prepare(coordinating_repository, specification, [])

    assert external_manifest_path.is_file()


def test_validate_rejects_a_symbolic_project_ignore_owner(tmp_path: Path) -> None:
    """Ignore-rule authoring must not follow a task-root symbolic link.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    specification_link_path = task_root / ".spec"
    specification_link_path.unlink()
    gitignore_path = task_root / ".gitignore"
    external_ignore_path = tmp_path / "external-ignore"
    external_ignore_path.write_text("preserve\n", encoding="utf-8")
    gitignore_path.unlink()
    gitignore_path.symlink_to(external_ignore_path)

    with pytest.raises(WorktreeError, match="physical ordinary file"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert external_ignore_path.read_text(encoding="utf-8") == "preserve\n"
    assert not os.path.lexists(specification_link_path)


def test_prepare_preserves_safe_internal_links_and_rejects_escaping_copy_links(tmp_path: Path) -> None:
    """Copy resources must preserve internal links without admitting escapes.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_root = coordinating_repository / "local" / "opaque-tree"
    source_root.mkdir(parents=True)
    (source_root / "value.txt").write_text("value\n", encoding="utf-8")
    (source_root / "internal-link").symlink_to("value.txt")
    (source_root / "absolute-internal-link").symlink_to(source_root / "value.txt")
    task_root = _task_root_get(coordinating_repository)
    manifest_path = task_root / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/opaque-tree"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )

    worktree_prepare(coordinating_repository, specification, [])

    destination_link = task_root / "local" / "opaque-tree" / "internal-link"
    assert destination_link.is_symlink()
    assert os.readlink(destination_link) == "value.txt"
    absolute_destination_link = task_root / "local" / "opaque-tree" / "absolute-internal-link"
    assert absolute_destination_link.is_symlink()
    assert not Path(os.readlink(absolute_destination_link)).is_absolute()
    assert absolute_destination_link.resolve() == task_root / "local" / "opaque-tree" / "value.txt"
    destination_link.unlink()
    destination_link.symlink_to(coordinating_repository / "README.md")
    with pytest.raises(WorktreeError, match="escapes its object tree"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)
    destination_link.unlink()
    destination_link.symlink_to("value.txt")
    escaping_source_root = coordinating_repository / "local" / "escaping-tree"
    escaping_source_root.mkdir()
    (escaping_source_root / "external-link").symlink_to(coordinating_repository / "README.md")
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/opaque-tree", "local/escaping-tree"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeError, match="escapes its object tree"):
        worktree_prepare(coordinating_repository, specification, [])


@pytest.mark.parametrize("link_kind", ["broken", "cycle"])
def test_prepare_normalizes_unresolved_copy_resource_links(tmp_path: Path, link_kind: str) -> None:
    """Broken and cyclic links must produce a safe contract error without partial output.

    Args:
        tmp_path: Isolated filesystem root.
        link_kind: Broken-target or cyclic-link fixture.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_root = coordinating_repository / "local" / "unresolved-tree"
    source_root.mkdir(parents=True)
    if link_kind == "broken":
        (source_root / "link").symlink_to("missing")
    else:
        (source_root / "first").symlink_to("second")
        (source_root / "second").symlink_to("first")
    task_root = _task_root_get(coordinating_repository)
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/unresolved-tree"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeError, match="symbolic link is unresolved or escapes"):
        worktree_prepare(coordinating_repository, specification, [])

    assert not os.path.lexists(task_root / "local" / "unresolved-tree")


def test_prepare_rejects_special_copy_objects_and_independent_destinations(tmp_path: Path) -> None:
    """Copy materialization must reject special sources and unrelated task content.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    source_root = coordinating_repository / "local" / "opaque-tree"
    source_root.mkdir(parents=True)
    os.mkfifo(source_root / "opaque-fifo")
    manifest_path = task_root / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/opaque-tree"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    with pytest.raises(WorktreeError, match="special filesystem object"):
        worktree_prepare(coordinating_repository, specification, [])

    (source_root / "opaque-fifo").unlink()
    (source_root / "value.txt").write_text("source\n", encoding="utf-8")
    destination_root = task_root / "local" / "opaque-tree"
    destination_root.mkdir(parents=True)
    (destination_root / "value.txt").write_text("independent\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="independent content"):
        worktree_prepare(coordinating_repository, specification, [])


def test_validate_repairs_missing_specification_link(tmp_path: Path) -> None:
    """Validation must recreate one provider-created missing specification link.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    link_path = _task_root_get(coordinating_repository) / ".spec"
    link_path.unlink()

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert link_path.is_symlink()
    assert any("repaired specification link" in item for item in result["performed_repair_list"])


def test_validate_repairs_recorded_link_and_ignore_rules(tmp_path: Path) -> None:
    """Validation must restore provider-owned links and effective project ignores.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    link_source = coordinating_repository / "local" / "opaque-link"
    link_source.parent.mkdir()
    link_source.write_text("shared\n", encoding="utf-8")
    manifest_path = task_root / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        "copy_required_path_list = []\n"
        "link_optional_path_list = []\n"
        'link_required_path_list = ["local/opaque-link"]\n',
        encoding="utf-8",
    )
    worktree_prepare(coordinating_repository, specification, [])
    link_destination = task_root / "local" / "opaque-link"
    link_destination.unlink()
    link_destination.symlink_to("../wrong-target")
    (task_root / ".gitignore").write_text("/.spec/\n/local/\n", encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert link_destination.is_symlink()
    assert link_destination.resolve() == link_source
    assert "/.spec\n" in (task_root / ".gitignore").read_text(encoding="utf-8")
    assert "/.worktree/\n" in (task_root / ".gitignore").read_text(encoding="utf-8")
    assert any("repaired link resource" in item for item in result["performed_repair_list"])
    assert any("restored tracked ignore pattern" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_preserves_independent_main_changes_and_rejects_overlap(tmp_path: Path) -> None:
    """Validation must preserve independent main work and reject overlapping drift.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "README.md").write_text("pre-existing user change\n", encoding="utf-8")
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    (coordinating_repository / "independent.txt").write_text("later user work\n", encoding="utf-8")

    independent_result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))
    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "pre-existing user change\n"
    assert (coordinating_repository / "independent.txt").read_text(encoding="utf-8") == "later user work\n"
    assert any(
        "recorded independent main working-state drift" in item for item in independent_result["performed_repair_list"]
    )
    assert not any("restored private state replica" in item for item in independent_result["performed_repair_list"])

    (coordinating_repository / ".gitignore").write_text("overlapping main change\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="overlaps task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)


def test_validate_records_an_independent_main_commit_without_replica_noise(tmp_path: Path) -> None:
    """Independent forward main history must be retained and reported once.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    (coordinating_repository / "independent.txt").write_text("main-only commit\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "independent.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Independent main work"])

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert any("recorded independent main commit drift" in item for item in result["performed_repair_list"])
    assert not any("restored private state replica" in item for item in result["performed_repair_list"])
    assert not (_task_root_get(coordinating_repository) / "independent.txt").exists()
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_accept_main_commit_drift_records_exact_caller_attestation(tmp_path: Path) -> None:
    """Exact caller attestation must preserve both overlapping committed states."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task work\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("independent committed main work\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    _git_run(coordinating_repository, ["commit", "-m", "Independent overlapping main work"])
    current_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])

    with pytest.raises(WorktreeError, match="Accumulated main commit history overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    result = json.loads(
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            current_main_commit,
            [Path("README.md")],
        )
    )

    assert any("accepted caller-attested main commit drift" in item for item in result["performed_repair_list"])
    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "independent committed main work\n"
    assert (task_root / "README.md").read_text(encoding="utf-8") == "task work\n"
    assert (
        json.loads(
            worktree_main_commit_drift_accept(
                coordinating_repository,
                specification,
                coordinating_repository,
                current_main_commit,
                [Path("README.md")],
            )
        )["performed_repair_list"]
        == []
    )
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_accept_main_commit_drift_reconciles_an_exact_recorded_dirty_preimage(
    tmp_path: Path,
) -> None:
    """A confirmed commit may materialize the exact dirty main object recorded at preparation."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "README.md").write_text(
        "pre-existing independent main work\n",
        encoding="utf-8",
    )
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task work\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    _git_run(coordinating_repository, ["commit", "-m", "Commit recorded independent main work"])
    current_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])

    result = json.loads(
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            current_main_commit,
            [Path("README.md")],
        )
    )

    assert any(
        "reconciled caller-attested main working state into commit" in item for item in result["performed_repair_list"]
    )
    assert any("retired obsolete private main preimage" in item for item in result["performed_repair_list"])
    assert _git_run(coordinating_repository, ["status", "--short"]) == ""
    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == (
        "pre-existing independent main work\n"
    )
    assert (task_root / "README.md").read_text(encoding="utf-8") == "task work\n"
    state_path = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/state-v2.json",
    )
    repository_state = json.loads(state_path.read_text(encoding="utf-8"))["repository_state_list"][0]
    assert "README.md" not in repository_state["main_status_by_path_map"]
    assert "README.md" not in repository_state["main_preimage_by_path_map"]
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_accept_main_commit_drift_rejects_a_different_recorded_dirty_preimage(
    tmp_path: Path,
) -> None:
    """Commit attestation must not reinterpret a different formerly dirty main object."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "README.md").write_text(
        "recorded dirty main work\n",
        encoding="utf-8",
    )
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task work\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text(
        "different committed main work\n",
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "README.md"])
    _git_run(coordinating_repository, ["commit", "-m", "Commit a different main object"])
    current_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])

    with pytest.raises(WorktreeError, match="Main working-state drift overlaps task paths"):
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            current_main_commit,
            [Path("README.md")],
        )

    assert _git_run(coordinating_repository, ["status", "--short"]) == ""
    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "different committed main work\n"
    assert (task_root / "README.md").read_text(encoding="utf-8") == "task work\n"
    state_path = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/state-v2.json",
    )
    repository_state = json.loads(state_path.read_text(encoding="utf-8"))["repository_state_list"][0]
    assert repository_state["main_status_by_path_map"]["README.md"] == " M"
    assert repository_state["accepted_main_commit_drift_list"] == [
        {
            "commit": current_main_commit,
            "path_list": ["README.md"],
        }
    ]


def test_accept_main_commit_drift_supports_task_owned_submodule_main(tmp_path: Path) -> None:
    """Exact caller attestation must apply at a participating submodule boundary."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    main_submodule = coordinating_repository / "dependency" / "leaf"
    (main_submodule / "README.md").write_text(
        "independent committed nested-main work\n",
        encoding="utf-8",
    )
    specification = _specification_create(coordinating_repository)
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )
    task_submodule = _task_root_get(coordinating_repository) / "dependency" / "leaf"
    (task_submodule / "README.md").write_text("task-owned nested work\n", encoding="utf-8")
    _git_run(main_submodule, ["config", "user.email", "test@example.com"])
    _git_run(main_submodule, ["config", "user.name", "Worktree Test"])
    _git_run(main_submodule, ["add", "README.md"])
    _git_run(main_submodule, ["commit", "-m", "Independent overlapping nested-main work"])
    current_main_commit = _git_run(main_submodule, ["rev-parse", "HEAD"])

    with pytest.raises(WorktreeError, match="Accumulated main commit history overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    with pytest.raises(WorktreeError, match=r"unexpected dependency/leaf/README\.md"):
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            _git_run(coordinating_repository, ["rev-parse", "HEAD"]),
            [Path("dependency/leaf/README.md")],
        )

    result = json.loads(
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            main_submodule,
            current_main_commit,
            [Path("README.md")],
        )
    )

    assert any(f"{main_submodule}@{current_main_commit}" in item for item in result["performed_repair_list"])
    assert any(
        "reconciled caller-attested main working state into commit" in item for item in result["performed_repair_list"]
    )
    assert (main_submodule / "README.md").read_text(encoding="utf-8") == ("independent committed nested-main work\n")
    assert (task_submodule / "README.md").read_text(encoding="utf-8") == "task-owned nested work\n"
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )
    task_root = _task_root_get(coordinating_repository)
    state_path = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/state-v2.json",
    )
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    submodule_state = state_payload["repository_state_list"][0]["participating_submodule_state_list"][0]
    assert submodule_state["accepted_main_commit_drift_list"] == [
        {
            "commit": current_main_commit,
            "path_list": ["README.md"],
        }
    ]


def test_accept_main_commit_drift_requires_exact_current_commit_and_overlap_set(tmp_path: Path) -> None:
    """Attestation must bind both the full current commit and complete overlap set."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    baseline_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task README\n", encoding="utf-8")
    (task_root / ".gitignore").write_text(
        (task_root / ".gitignore").read_text(encoding="utf-8") + "# task marker\n",
        encoding="utf-8",
    )
    (coordinating_repository / "README.md").write_text("main README\n", encoding="utf-8")
    (coordinating_repository / ".gitignore").write_text("/.spec/\n/local/\n# main marker\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md", ".gitignore"])
    _git_run(coordinating_repository, ["commit", "-m", "Two overlapping main paths"])
    current_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])

    with pytest.raises(WorktreeError, match="Main commit changed before caller-attested"):
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            baseline_commit,
            [Path(".gitignore"), Path("README.md")],
        )
    with pytest.raises(WorktreeError, match=r"missing README\.md"):
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            current_main_commit,
            [Path(".gitignore")],
        )
    with pytest.raises(WorktreeError, match=r"unexpected unrelated\.txt"):
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            current_main_commit,
            [Path(".gitignore"), Path("README.md"), Path("unrelated.txt")],
        )

    worktree_main_commit_drift_accept(
        coordinating_repository,
        specification,
        coordinating_repository,
        current_main_commit,
        [Path("README.md"), Path(".gitignore")],
    )
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_accept_main_commit_drift_does_not_cover_later_main_or_task_overlap(tmp_path: Path) -> None:
    """One attestation must not authorize another path or a later commit change."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "other.txt").write_text("baseline other\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "other.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Add second baseline path"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task README\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("first main README\n", encoding="utf-8")
    (coordinating_repository / "other.txt").write_text("independent main other\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md", "other.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Main changes two paths"])
    first_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])
    worktree_main_commit_drift_accept(
        coordinating_repository,
        specification,
        coordinating_repository,
        first_main_commit,
        [Path("README.md")],
    )

    (task_root / "other.txt").write_text("later task other\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match=r"other\.txt"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)
    worktree_main_commit_drift_accept(
        coordinating_repository,
        specification,
        coordinating_repository,
        first_main_commit,
        [Path("other.txt")],
    )
    assert (
        json.loads(
            worktree_main_commit_drift_accept(
                coordinating_repository,
                specification,
                coordinating_repository,
                first_main_commit,
                [Path("other.txt")],
            )
        )["performed_repair_list"]
        == []
    )
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )

    (coordinating_repository / "README.md").write_text("second main README\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    _git_run(coordinating_repository, ["commit", "-m", "Change accepted path again"])
    second_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])
    with pytest.raises(WorktreeError, match=r"README\.md"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    worktree_main_commit_drift_accept(
        coordinating_repository,
        specification,
        coordinating_repository,
        second_main_commit,
        [Path("README.md")],
    )
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_accept_main_commit_drift_rejects_rewritten_recorded_main_history(tmp_path: Path) -> None:
    """Caller attestation must not normalize a rewritten recorded main branch."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    baseline_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    (coordinating_repository / "independent.txt").write_text("recorded main work\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "independent.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Advance recorded main"])
    worktree_validate(coordinating_repository, "repository_prepared", specification)

    _git_run(coordinating_repository, ["reset", "--hard", baseline_commit])
    (_task_root_get(coordinating_repository) / "README.md").write_text("task README\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("rewritten main README\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    _git_run(coordinating_repository, ["commit", "-m", "Divergent main work"])
    rewritten_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])

    with pytest.raises(WorktreeError, match="no longer descends from its recorded commit"):
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            rewritten_main_commit,
            [Path("README.md")],
        )


def test_accept_main_commit_drift_persists_before_another_repository_blocks(tmp_path: Path) -> None:
    """One exact decision must survive validation blocked by a second repository."""

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    for repository in (coordinating_repository, other_repository):
        (_task_root_get(repository) / "README.md").write_text(
            f"task work in {repository.name}\n",
            encoding="utf-8",
        )
        (repository / "README.md").write_text(
            f"independent main work in {repository.name}\n",
            encoding="utf-8",
        )
        _git_run(repository, ["add", "README.md"])
        _git_run(repository, ["commit", "-m", f"Independent overlap in {repository.name}"])

    coordinating_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])
    with pytest.raises(WorktreeError, match=f"Accumulated main commit history overlaps.*{other_repository}"):
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            coordinating_repository,
            coordinating_main_commit,
            [Path("README.md")],
        )

    other_main_commit = _git_run(other_repository, ["rev-parse", "HEAD"])
    result = json.loads(
        worktree_main_commit_drift_accept(
            coordinating_repository,
            specification,
            other_repository,
            other_main_commit,
            [Path("README.md")],
        )
    )

    assert any(f"{other_repository}@{other_main_commit}" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )
    for repository in (coordinating_repository, other_repository):
        assert (repository / "README.md").read_text(encoding="utf-8") == (
            f"independent main work in {repository.name}\n"
        )
        assert (_task_root_get(repository) / "README.md").read_text(encoding="utf-8") == (
            f"task work in {repository.name}\n"
        )


def test_validate_rejects_forged_main_commit_drift_attestation(tmp_path: Path) -> None:
    """Private attestation paths must be observable in their recorded commit history."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    state_path = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/state-v2.json",
    )
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["repository_state_list"][0]["accepted_main_commit_drift_list"] = [
        {
            "commit": _git_run(coordinating_repository, ["rev-parse", "HEAD"]),
            "path_list": ["README.md"],
        }
    ]
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")

    with pytest.raises(WorktreeError, match="paths are absent from recorded history"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)


def test_validate_rechecks_preexisting_dirty_main_paths_against_the_evolving_task_diff(tmp_path: Path) -> None:
    """A formerly unrelated dirty path becomes ambiguous when the task later edits it.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "README.md").write_text("pre-existing user work\n", encoding="utf-8")
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    (_task_root_get(coordinating_repository) / "README.md").write_text("task work\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Current dirty main state overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "pre-existing user work\n"


def test_validate_rechecks_recorded_main_commit_history_against_the_evolving_task_diff(tmp_path: Path) -> None:
    """An accepted main commit cannot become overlapping after later task work.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    (coordinating_repository / "shared.txt").write_text("main work\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "shared.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Add independent main path"])
    worktree_validate(coordinating_repository, "repository_prepared", specification)
    (_task_root_get(coordinating_repository) / "shared.txt").write_text("task work\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Accumulated main commit history overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (coordinating_repository / "shared.txt").read_text(encoding="utf-8") == "main work\n"


def test_validate_protects_a_committed_task_rename_source_boundary(tmp_path: Path) -> None:
    """A committed task rename must continue to protect its deleted source path.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    old_path = coordinating_repository / "old.txt"
    old_path.write_text("baseline\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "old.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Add rename source"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    _git_run(task_root, ["mv", "old.txt", "new.txt"])
    _git_run(task_root, ["commit", "-m", "Rename task path"])
    old_path.write_text("independent main work\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="overlaps task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert old_path.read_text(encoding="utf-8") == "independent main work\n"


def test_validate_protects_a_recorded_main_rename_source_boundary(tmp_path: Path) -> None:
    """A recorded main rename must remain overlapping when task later touches its source.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "old.txt").write_text("baseline\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "old.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Add rename source"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    _git_run(coordinating_repository, ["mv", "old.txt", "new.txt"])
    _git_run(coordinating_repository, ["commit", "-m", "Rename main path"])
    worktree_validate(coordinating_repository, "repository_prepared", specification)
    (_task_root_get(coordinating_repository) / "old.txt").write_text("task work\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Accumulated main commit history overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (coordinating_repository / "new.txt").read_text(encoding="utf-8") == "baseline\n"


@pytest.mark.parametrize(
    ("flag_argument", "expected_tag"),
    [
        ("--assume-unchanged", "h"),
        ("--skip-worktree", "S"),
    ],
)
def test_validate_detects_hidden_index_flag_main_overlap(
    tmp_path: Path,
    flag_argument: str,
    expected_tag: str,
) -> None:
    """Assume-unchanged and skip-worktree paths must remain visible protection boundaries.

    Args:
        tmp_path: Isolated filesystem root.
        flag_argument: Non-default index flag to install.
        expected_tag: Expected `git ls-files -v` tag.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    _git_run(coordinating_repository, ["update-index", flag_argument, "README.md"])
    (coordinating_repository / "README.md").write_text("hidden main work\n", encoding="utf-8")
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    (_task_root_get(coordinating_repository) / "README.md").write_text("task work\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Current dirty main state overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "hidden main work\n"
    assert _git_run(coordinating_repository, ["ls-files", "-v", "--", "README.md"]).startswith(expected_tag)


def test_validate_detects_intent_to_add_main_overlap(tmp_path: Path) -> None:
    """An intent-to-add entry must remain a protected dirty-main boundary.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "intent.txt").write_text("main intent\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "-N", "intent.txt"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_intent_path = _task_root_get(coordinating_repository) / "intent.txt"
    task_intent_path.write_text("task work\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Current dirty main state overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (coordinating_repository / "intent.txt").read_text(encoding="utf-8") == "main intent\n"
    assert _git_run(coordinating_repository, ["status", "--short", "--", "intent.txt"]) == "A intent.txt"


@pytest.mark.parametrize("resource_path", ["secret.txt", "ignored-directory/value.txt"])
def test_validate_detects_ignored_untracked_main_overlap(tmp_path: Path, resource_path: str) -> None:
    """Ignored untracked main files must not be overwritten by a task-owned path.

    Args:
        tmp_path: Isolated filesystem root.
        resource_path: Ignored file path, directly or below an ignored directory.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    ignore_pattern = "/secret.txt\n" if resource_path == "secret.txt" else "/ignored-directory/\n"
    with (coordinating_repository / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write(ignore_pattern)
    _git_run(coordinating_repository, ["add", ".gitignore"])
    _git_run(coordinating_repository, ["commit", "-m", "Ignore local user object"])
    main_resource_path = coordinating_repository / resource_path
    main_resource_path.parent.mkdir(parents=True, exist_ok=True)
    main_resource_path.write_text("ignored main work\n", encoding="utf-8")
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_resource_path = _task_root_get(coordinating_repository) / resource_path
    task_resource_path.parent.mkdir(parents=True, exist_ok=True)
    task_resource_path.write_text("task work\n", encoding="utf-8")
    _git_run(_task_root_get(coordinating_repository), ["add", "--force", resource_path])

    with pytest.raises(WorktreeError, match="Ignored untracked main objects overlap current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert main_resource_path.read_text(encoding="utf-8") == "ignored main work\n"


def test_main_leak_recovery_requires_provenance_before_restoring_clean_main(tmp_path: Path) -> None:
    """Identical bytes remain ambiguous until the calling agent records provenance.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    task_text = (task_root / ".gitignore").read_text(encoding="utf-8")
    (coordinating_repository / ".gitignore").write_text(task_text, encoding="utf-8")

    with pytest.raises(WorktreeError, match="without recorded agent provenance"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)
    assert (coordinating_repository / ".gitignore").read_text(encoding="utf-8") == task_text

    result = json.loads(
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path(".gitignore")],
        )
    )

    assert (coordinating_repository / ".gitignore").read_text(encoding="utf-8") == "/.spec/\n/local/\n"
    assert _git_run(coordinating_repository, ["status", "--short"]) == ""
    assert any("restored main preimage for duplicated task patch" in item for item in result["performed_repair_list"])


def test_main_leak_recovery_restores_then_blocks_a_dirty_main_preimage_overlap(tmp_path: Path) -> None:
    """Recovery must restore dirty content before the underlying overlap blocks.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "README.md").write_text("pre-existing staged user state\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_readme = _task_root_get(coordinating_repository) / "README.md"
    task_readme.write_text("task implementation\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("task implementation\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Current dirty main state overlaps current task paths"):
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("README.md")],
        )

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "pre-existing staged user state\n"
    assert _git_run(coordinating_repository, ["diff", "--cached", "--", "README.md"])
    assert _git_run(coordinating_repository, ["diff", "--", "README.md"]) == ""
    with pytest.raises(WorktreeError, match="Current dirty main state overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)


def test_validate_restores_then_blocks_an_index_preimage_for_a_nul_delimited_git_path(tmp_path: Path) -> None:
    """Index recovery must restore a delimiter-bearing path before overlap blocks.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    unusual_path = Path("line\nand\ttab.txt")
    (coordinating_repository / unusual_path).write_text("pre-existing user state\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", unusual_path.as_posix()])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_path = _task_root_get(coordinating_repository) / unusual_path
    task_path.write_text("task implementation\n", encoding="utf-8")
    (coordinating_repository / unusual_path).write_text("task implementation\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Current dirty main state overlaps current task paths"):
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [unusual_path],
        )

    assert (coordinating_repository / unusual_path).read_text(encoding="utf-8") == "pre-existing user state\n"
    assert _git_run(coordinating_repository, ["diff", "--cached", "--", unusual_path.as_posix()])
    assert _git_run(coordinating_repository, ["diff", "--", unusual_path.as_posix()]) == ""
    with pytest.raises(WorktreeError, match="Current dirty main state overlaps current task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)


def test_main_leak_recovery_preflights_a_private_working_preimage_before_mutation(tmp_path: Path) -> None:
    """A damaged private snapshot must leave current main index and bytes untouched.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "README.md").write_text("accepted staged state\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    task_path = task_root / "README.md"
    task_path.write_text("task implementation\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("task implementation\n", encoding="utf-8")
    state_path = Path(
        _git_run(
            task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
        )
    )
    if not state_path.is_absolute():
        state_path = task_root / state_path
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    preimage = state_payload["repository_state_list"][0]["main_preimage_by_path_map"]["README.md"]
    preimage_root = Path(
        _git_run(
            task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/main-preimage-v1"],
        )
    )
    if not preimage_root.is_absolute():
        preimage_root = task_root / preimage_root
    (preimage_root / preimage["snapshot_name"] / "working").write_text("corrupt\n", encoding="utf-8")
    cached_diff_before = _git_run(coordinating_repository, ["diff", "--cached", "--", "README.md"])
    index_before = _git_run(coordinating_repository, ["ls-files", "--stage", "--", "README.md"])

    with pytest.raises(WorktreeError, match="Private main preimage is unavailable or damaged"):
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("README.md")],
        )

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "task implementation\n"
    assert _git_run(coordinating_repository, ["diff", "--cached", "--", "README.md"]) == cached_diff_before
    assert _git_run(coordinating_repository, ["ls-files", "--stage", "--", "README.md"]) == index_before
    assert task_path.read_text(encoding="utf-8") == "task implementation\n"


def test_main_leak_recovery_preflights_every_requested_path_before_any_mutation(tmp_path: Path) -> None:
    """A later corrupt preimage must leave every requested main path untouched.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    second_path = coordinating_repository / "SECOND.md"
    second_path.write_text("second baseline\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "SECOND.md"])
    _git_run(coordinating_repository, ["commit", "-m", "Add second recovery path"])
    (coordinating_repository / "README.md").write_text("accepted README\n", encoding="utf-8")
    second_path.write_text("accepted SECOND\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md", "SECOND.md"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    for path_text in ("README.md", "SECOND.md"):
        (task_root / path_text).write_text(f"task {path_text}\n", encoding="utf-8")
        (coordinating_repository / path_text).write_text(f"task {path_text}\n", encoding="utf-8")
    state_path = Path(
        _git_run(
            task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
        )
    )
    if not state_path.is_absolute():
        state_path = task_root / state_path
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    second_preimage = state_payload["repository_state_list"][0]["main_preimage_by_path_map"]["SECOND.md"]
    preimage_root = Path(
        _git_run(
            task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/main-preimage-v1"],
        )
    )
    if not preimage_root.is_absolute():
        preimage_root = task_root / preimage_root
    (preimage_root / second_preimage["snapshot_name"] / "working").write_text("corrupt\n", encoding="utf-8")
    index_before = {
        path_text: _git_run(coordinating_repository, ["ls-files", "--stage", "--", path_text])
        for path_text in ("README.md", "SECOND.md")
    }

    with pytest.raises(WorktreeError, match="Private main preimage is unavailable or damaged"):
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("README.md"), Path("SECOND.md")],
        )

    for path_text in ("README.md", "SECOND.md"):
        assert (coordinating_repository / path_text).read_text(encoding="utf-8") == f"task {path_text}\n"
        assert _git_run(coordinating_repository, ["ls-files", "--stage", "--", path_text]) == index_before[path_text]
    persisted_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted_state["repository_state_list"][0]["main_leak_fingerprint_by_path_map"] == {}


def test_main_leak_recovery_rejects_unattributed_staged_content(tmp_path: Path) -> None:
    """Matching working bytes cannot authorize deletion of a different staged object.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_path = _task_root_get(coordinating_repository) / "README.md"
    task_path.write_text("task implementation\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("unrelated staged content\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    (coordinating_repository / "README.md").write_text("task implementation\n", encoding="utf-8")
    cached_diff_before = _git_run(coordinating_repository, ["diff", "--cached", "--", "README.md"])
    index_before = _git_run(coordinating_repository, ["ls-files", "--stage", "--", "README.md"])

    with pytest.raises(WorktreeError, match="index differs from both"):
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("README.md")],
        )

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "task implementation\n"
    assert _git_run(coordinating_repository, ["diff", "--cached", "--", "README.md"]) == cached_diff_before
    assert _git_run(coordinating_repository, ["ls-files", "--stage", "--", "README.md"]) == index_before
    assert task_path.read_text(encoding="utf-8") == "task implementation\n"


def test_main_leak_recovery_rehydrates_a_pruned_staged_preimage_blob(tmp_path: Path) -> None:
    """Private index bytes must survive object pruning and restore staged plus working state.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    (coordinating_repository / "README.md").write_text("accepted staged A\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    staged_object_id = _git_run(
        coordinating_repository,
        ["ls-files", "--stage", "--", "README.md"],
    ).split()[1]
    (coordinating_repository / "README.md").write_text("accepted working B\n", encoding="utf-8")
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_path = _task_root_get(coordinating_repository) / "README.md"
    task_path.write_text("task implementation C\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("task implementation C\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    _git_run(coordinating_repository, ["reflog", "expire", "--expire=now", "--all"])
    _git_run(coordinating_repository, ["gc", "--prune=now"])
    missing_object_result = subprocess.run(
        ["git", "-C", str(coordinating_repository), "cat-file", "-e", staged_object_id],
        capture_output=True,
        check=False,
        text=True,
    )
    assert missing_object_result.returncode != 0

    with pytest.raises(WorktreeError, match="Current dirty main state overlaps current task paths"):
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("README.md")],
        )

    assert _git_run(coordinating_repository, ["show", ":README.md"]) == "accepted staged A"
    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "accepted working B\n"
    assert _git_run(coordinating_repository, ["cat-file", "-e", staged_object_id]) == ""
    _git_run(coordinating_repository, ["fsck", "--full", "--no-dangling"])
    assert task_path.read_text(encoding="utf-8") == "task implementation C\n"


def test_validate_matches_a_committed_task_patch_for_a_nul_delimited_git_path(tmp_path: Path) -> None:
    """Committed task paths must retain raw identity after porcelain status is clean.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    unusual_path = Path("committed\nand\ttab.txt")
    (coordinating_repository / unusual_path).write_text("baseline\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", unusual_path.as_posix()])
    _git_run(coordinating_repository, ["commit", "-m", "Add unusual path"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / unusual_path).write_text("task implementation\n", encoding="utf-8")
    _git_run(task_root, ["add", unusual_path.as_posix()])
    _git_run(task_root, ["commit", "-m", "Implement unusual task path"])
    (coordinating_repository / unusual_path).write_text("task implementation\n", encoding="utf-8")

    result = json.loads(
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [unusual_path],
        )
    )

    assert (coordinating_repository / unusual_path).read_text(encoding="utf-8") == "baseline\n"
    assert _git_run(coordinating_repository, ["status", "--short"]) == ""
    assert any("restored main preimage for duplicated task patch" in item for item in result["performed_repair_list"])


def test_main_leak_recovery_treats_pathspec_magic_as_a_literal_filename(tmp_path: Path) -> None:
    """A Git-magic-looking filename must never broaden recovery to sibling paths.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    magic_path = Path(":(glob)*")
    sibling_path = coordinating_repository / "sibling.txt"
    (coordinating_repository / magic_path).write_text("baseline magic\n", encoding="utf-8")
    sibling_path.write_text("baseline sibling\n", encoding="utf-8")
    _git_run(
        coordinating_repository,
        ["--literal-pathspecs", "add", "--", magic_path.as_posix(), "sibling.txt"],
    )
    _git_run(coordinating_repository, ["commit", "-m", "Add literal pathspec filename"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / magic_path).write_text("task magic\n", encoding="utf-8")
    (coordinating_repository / magic_path).write_text("task magic\n", encoding="utf-8")
    sibling_path.write_text("independent sibling\n", encoding="utf-8")

    result = json.loads(
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [magic_path],
        )
    )

    assert (coordinating_repository / magic_path).read_text(encoding="utf-8") == "baseline magic\n"
    assert sibling_path.read_text(encoding="utf-8") == "independent sibling\n"
    assert any("restored main preimage" in item for item in result["performed_repair_list"])


def test_validate_protects_executable_mode_when_repository_filemode_is_disabled(tmp_path: Path) -> None:
    """Repository-local `core.fileMode=false` must not hide executable task drift.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    script_path = coordinating_repository / "script.sh"
    script_path.write_text("#!/bin/sh\n", encoding="utf-8")
    script_path.chmod(0o644)
    _git_run(coordinating_repository, ["add", "script.sh"])
    _git_run(coordinating_repository, ["commit", "-m", "Add non-executable script"])
    _git_run(coordinating_repository, ["config", "core.fileMode", "false"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_script_path = _task_root_get(coordinating_repository) / "script.sh"
    task_script_path.chmod(0o755)
    script_path.chmod(0o755)

    with pytest.raises(WorktreeError, match="overlaps task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert script_path.stat().st_mode & 0o111
    assert task_script_path.stat().st_mode & 0o111


def test_validate_repairs_clean_submodule_drift_and_rejects_dirty_state(tmp_path: Path) -> None:
    """Validation must repair clean gitlink drift but preserve dirty submodule work.

    Args:
        tmp_path: Isolated filesystem root.
    """

    leaf_repository = tmp_path / "leaf"
    _repository_create(leaf_repository)
    first_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_submodule = _task_root_get(coordinating_repository) / "dependency" / "leaf"
    assert _git_run(task_submodule, ["rev-parse", "HEAD"]) == first_commit

    (leaf_repository / "README.md").write_text("new leaf commit\n", encoding="utf-8")
    _git_run(leaf_repository, ["commit", "-am", "Advance leaf"])
    second_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    _git_run(task_submodule, ["fetch", str(leaf_repository), second_commit])
    _git_run(task_submodule, ["checkout", "--detach", second_commit])

    repair_result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))
    assert _git_run(task_submodule, ["rev-parse", "HEAD"]) == first_commit
    assert any("synchronized recursive submodules" in item for item in repair_result["performed_repair_list"])

    (task_submodule / "README.md").write_text("dirty task submodule\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="Dirty submodule drift"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)


def test_validate_rejects_a_submodule_root_redirected_by_symlink(tmp_path: Path) -> None:
    """Recursive Git commands must never traverse outside the physical task root."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_submodule = _task_root_get(coordinating_repository) / "dependency" / "leaf"
    displaced_submodule = task_submodule.with_name("leaf-displaced")
    task_submodule.rename(displaced_submodule)
    task_submodule.symlink_to(leaf_repository, target_is_directory=True)

    with pytest.raises(WorktreeError, match="not one physical repository boundary"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert task_submodule.is_symlink()
    assert (leaf_repository / "README.md").read_text(encoding="utf-8") == "baseline\n"
    assert displaced_submodule.is_dir()


@pytest.mark.parametrize("recovery_kind", ["pending", "markerless"])
def test_prepare_repairs_clean_read_only_submodule_drift_during_recovery(
    tmp_path: Path,
    recovery_kind: str,
) -> None:
    """Interrupted and markerless preparation retain deterministic submodule repair."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    first_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    task_root = _task_root_get(coordinating_repository)
    if recovery_kind == "pending":
        _pending_worktree_create(coordinating_repository, coordinating_repository, specification)
        _git_run(
            coordinating_repository,
            ["worktree", "add", "-b", TASK_PREFIX, str(task_root), "HEAD"],
        )
        _git_run(
            task_root,
            ["-c", "protocol.file.allow=always", "submodule", "update", "--init", "--checkout"],
        )
    else:
        worktree_prepare(coordinating_repository, specification, [])
        state_path = Path(
            _git_run(
                task_root,
                ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
            )
        )
        if not state_path.is_absolute():
            state_path = task_root / state_path
        state_path.unlink()
    (leaf_repository / "README.md").write_text("clean drift\n", encoding="utf-8")
    _git_run(leaf_repository, ["commit", "-am", "Advance leaf"])
    second_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    task_submodule = task_root / "dependency" / "leaf"
    _git_run(task_submodule, ["fetch", str(leaf_repository), second_commit])
    _git_run(task_submodule, ["checkout", "--detach", second_commit])

    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    assert _git_run(task_submodule, ["rev-parse", "HEAD"]) == first_commit
    assert any("synchronized recursive submodules" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_ignores_repository_submodule_ignore_all_for_isolation(tmp_path: Path) -> None:
    """Repository-owned `ignore = all` must not hide main/task submodule overlap.

    Args:
        tmp_path: Isolated filesystem root.
    """

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(
        coordinating_repository,
        ["config", "-f", ".gitmodules", "submodule.dependency/leaf.ignore", "all"],
    )
    _git_run(coordinating_repository, ["add", ".gitmodules", "dependency/leaf"])
    _git_run(coordinating_repository, ["commit", "-m", "Add ignored leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )
    main_submodule = coordinating_repository / "dependency" / "leaf"
    task_submodule = _task_root_get(coordinating_repository) / "dependency" / "leaf"
    (main_submodule / "README.md").write_text("main submodule work\n", encoding="utf-8")
    (task_submodule / "README.md").write_text("task submodule work\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="overlaps task paths"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (main_submodule / "README.md").read_text(encoding="utf-8") == "main submodule work\n"
    assert (task_submodule / "README.md").read_text(encoding="utf-8") == "task submodule work\n"


def test_main_leak_recovery_restores_a_task_owned_submodule_path(tmp_path: Path) -> None:
    """Nested task patches are attributed below the superproject gitlink."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )
    main_submodule = coordinating_repository / "dependency" / "leaf"
    task_submodule = _task_root_get(coordinating_repository) / "dependency" / "leaf"
    (main_submodule / "README.md").write_text("task patch\n", encoding="utf-8")
    (task_submodule / "README.md").write_text("task patch\n", encoding="utf-8")

    result = json.loads(
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("dependency/leaf/README.md")],
        )
    )

    assert (main_submodule / "README.md").read_text(encoding="utf-8") == "baseline\n"
    assert (task_submodule / "README.md").read_text(encoding="utf-8") == "task patch\n"
    assert _git_run(main_submodule, ["status", "--short"]) == ""
    assert any("restored main preimage" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_main_leak_recovery_migrates_a_recorded_clean_legacy_submodule_boundary(
    tmp_path: Path,
) -> None:
    """Old clean superproject state can safely classify later nested drift."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )
    task_root = _task_root_get(coordinating_repository)
    state_path = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/state-v2.json",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    submodule_state = state["repository_state_list"][0]["participating_submodule_state_list"][0]
    for key in (
        "accepted_main_commit_drift_list",
        "main_commit",
        "main_leak_fingerprint_by_path_map",
        "main_preimage_by_path_map",
        "main_status_by_path_map",
        "main_status_fingerprint_by_path_map",
    ):
        del submodule_state[key]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    main_submodule = coordinating_repository / "dependency" / "leaf"
    task_submodule = task_root / "dependency" / "leaf"
    (main_submodule / "README.md").write_text("task patch\n", encoding="utf-8")
    (task_submodule / "README.md").write_text("task patch\n", encoding="utf-8")

    result = json.loads(
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("dependency/leaf/README.md")],
        )
    )

    assert (main_submodule / "README.md").read_text(encoding="utf-8") == "baseline\n"
    assert (task_submodule / "README.md").read_text(encoding="utf-8") == "task patch\n"
    assert any("upgraded task-owned submodule main isolation state" in item for item in result["performed_repair_list"])


def test_validate_records_nonoverlapping_task_owned_submodule_main_drift(
    tmp_path: Path,
) -> None:
    """Independent nested-main work remains valid beside task-submodule changes."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    (leaf_repository / "INDEPENDENT.md").write_text("baseline independent\n", encoding="utf-8")
    _git_run(leaf_repository, ["add", "INDEPENDENT.md"])
    _git_run(leaf_repository, ["commit", "-m", "Add independent path"])
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )
    main_submodule = coordinating_repository / "dependency" / "leaf"
    task_submodule = _task_root_get(coordinating_repository) / "dependency" / "leaf"
    (main_submodule / "INDEPENDENT.md").write_text("independent main work\n", encoding="utf-8")
    (task_submodule / "README.md").write_text("task work\n", encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert (main_submodule / "INDEPENDENT.md").read_text(encoding="utf-8") == "independent main work\n"
    assert (task_submodule / "README.md").read_text(encoding="utf-8") == "task work\n"
    assert any("recorded independent main working-state drift" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_migrates_task_owned_submodule_main_fingerprints(tmp_path: Path) -> None:
    """Collision-safe migration includes nested-main status and preimage state."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )
    task_root = _task_root_get(coordinating_repository)
    main_submodule = coordinating_repository / "dependency" / "leaf"
    task_submodule = task_root / "dependency" / "leaf"
    (main_submodule / "README.md").write_text("independent nested main work\n", encoding="utf-8")
    worktree_validate(coordinating_repository, "repository_prepared", specification)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    state_path = _private_git_path_get(task_root, "goal-brainstorm-worktree/state-v2.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    submodule_state = state["repository_state_list"][0]["participating_submodule_state_list"][0]
    preimage = submodule_state["main_preimage_by_path_map"]["README.md"]
    preimage_path = (
        workflow._main_preimage_directory_get(task_submodule) / preimage["snapshot_name"] / "working"  # noqa: SLF001
    )
    state["fingerprint_schema_version"] = 1
    submodule_state["main_status_fingerprint_by_path_map"]["README.md"] = (
        workflow._legacy_path_git_state_fingerprint_get(  # noqa: SLF001
            main_submodule,
            "README.md",
        )
    )
    preimage["working_fingerprint"] = workflow._legacy_path_fingerprint_get(preimage_path)  # noqa: SLF001
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))
    migrated_state = json.loads(state_path.read_text(encoding="utf-8"))
    migrated_submodule_state = migrated_state["repository_state_list"][0]["participating_submodule_state_list"][0]

    assert any("upgraded collision-safe filesystem fingerprints" in item for item in result["performed_repair_list"])
    assert migrated_submodule_state["main_status_fingerprint_by_path_map"]["README.md"] == (
        workflow._path_git_state_fingerprint_get(main_submodule, "README.md")  # noqa: SLF001
    )
    assert migrated_submodule_state["main_preimage_by_path_map"]["README.md"]["working_fingerprint"] == (
        workflow._path_fingerprint_get(preimage_path)  # noqa: SLF001
    )


def test_validate_ignores_submodule_ignore_all_for_committed_history_overlap(tmp_path: Path) -> None:
    """Committed task/main gitlink overlap remains visible through `ignore = all`."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(
        coordinating_repository,
        ["config", "-f", ".gitmodules", "submodule.dependency/leaf.ignore", "all"],
    )
    _git_run(coordinating_repository, ["add", ".gitmodules", "dependency/leaf"])
    _git_run(coordinating_repository, ["commit", "-m", "Add ignored leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )
    task_root = _task_root_get(coordinating_repository)
    task_submodule = task_root / "dependency" / "leaf"
    main_submodule = coordinating_repository / "dependency" / "leaf"
    (leaf_repository / "README.md").write_text("shared advanced leaf\n", encoding="utf-8")
    _git_run(leaf_repository, ["commit", "-am", "Advance leaf"])
    advanced_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    for submodule_root in (task_submodule, main_submodule):
        _git_run(submodule_root, ["fetch", str(leaf_repository), advanced_commit])
        _git_run(submodule_root, ["checkout", "--detach", advanced_commit])
    _git_run(task_root, ["add", "dependency/leaf"])
    _git_run(task_root, ["commit", "-m", "Advance task gitlink"])
    _git_run(coordinating_repository, ["add", "dependency/leaf"])
    _git_run(coordinating_repository, ["commit", "-m", "Advance main gitlink"])

    with pytest.raises(WorktreeError, match="Accumulated main commit history overlaps"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert _git_run(task_root, ["rev-parse", "HEAD"]) != _git_run(
        coordinating_repository,
        ["rev-parse", "HEAD"],
    )
    assert _git_run(task_submodule, ["rev-parse", "HEAD"]) == advanced_commit
    assert _git_run(main_submodule, ["rev-parse", "HEAD"]) == advanced_commit


def test_validate_preserves_ignored_submodule_collision_before_gitlink_repair(tmp_path: Path) -> None:
    """A read-only submodule repair must not overwrite an ignored untracked object."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    first_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    (leaf_repository / "future.txt").write_text("tracked future\n", encoding="utf-8")
    _git_run(leaf_repository, ["add", "future.txt"])
    _git_run(leaf_repository, ["commit", "-m", "Add future path"])
    second_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_submodule = _task_root_get(coordinating_repository) / "dependency" / "leaf"
    assert _git_run(task_submodule, ["rev-parse", "HEAD"]) == second_commit
    _git_run(task_submodule, ["checkout", "--detach", first_commit])
    exclude_path_text = _git_run(task_submodule, ["rev-parse", "--git-path", "info/exclude"])
    exclude_path = Path(exclude_path_text)
    if not exclude_path.is_absolute():
        exclude_path = task_submodule / exclude_path
    with exclude_path.open("a", encoding="utf-8") as handle:
        handle.write("/future.txt\n")
    collision_path = task_submodule / "future.txt"
    collision_path.write_text("independent ignored content\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Ignored submodule objects would be overwritten"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert collision_path.read_text(encoding="utf-8") == "independent ignored content\n"
    assert _git_run(task_submodule, ["rev-parse", "HEAD"]) == first_commit


def test_validate_repairs_stale_recursive_submodule_url_configuration(tmp_path: Path) -> None:
    """Correct gitlinks must not hide drift in effective recursive URLs.

    Args:
        tmp_path: Isolated filesystem root.
    """

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    config_key = "submodule.dependency/leaf.url"
    _git_run(task_root, ["config", "--local", config_key, str(tmp_path / "wrong-source")])

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert _git_run(task_root, ["config", "--local", "--get", config_key]) == str(leaf_repository)
    assert any("synchronized recursive submodule URLs" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_reinitializes_a_missing_submodule_checkout(tmp_path: Path) -> None:
    """An uninitialized checkout must not be mistaken for its superproject.

    Args:
        tmp_path: Isolated filesystem root.
    """

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    leaf_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    _git_run(task_root, ["submodule", "deinit", "--force", "--", "dependency/leaf"])

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    task_submodule = task_root / "dependency" / "leaf"
    assert _git_run(task_submodule, ["rev-parse", "--show-toplevel"]) == str(task_submodule)
    assert _git_run(task_submodule, ["rev-parse", "HEAD"]) == leaf_commit
    assert any("synchronized recursive submodules" in item for item in result["performed_repair_list"])


def test_prepare_initializes_nested_submodules_at_recorded_gitlinks(tmp_path: Path) -> None:
    """Preparation must initialize the complete recursive submodule graph.

    Args:
        tmp_path: Isolated filesystem root.
    """

    nested_repository = tmp_path / "nested"
    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(nested_repository)
    nested_commit = _git_run(nested_repository, ["rev-parse", "HEAD"])
    _repository_create(leaf_repository)
    _git_run(
        leaf_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(nested_repository), "nested dependency"],
    )
    _git_run(leaf_repository, ["commit", "-am", "Add nested submodule"])
    leaf_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add recursive submodule"])
    specification = _specification_create(coordinating_repository)

    worktree_prepare(coordinating_repository, specification, [])

    task_root = _task_root_get(coordinating_repository)
    assert _git_run(task_root / "dependency leaf", ["rev-parse", "HEAD"]) == leaf_commit
    assert _git_run(task_root / "dependency leaf" / "nested dependency", ["rev-parse", "HEAD"]) == nested_commit
    recursive_status = _git_run(task_root, ["submodule", "status", "--recursive"])
    normalized_status_line_list = [line.lstrip() for line in recursive_status.splitlines()]
    assert any(line.startswith(f"{leaf_commit} dependency leaf ") for line in normalized_status_line_list)
    assert any(
        line.startswith(f"{nested_commit} dependency leaf/nested dependency ") for line in normalized_status_line_list
    )


def test_prepare_treats_pathspec_magic_submodule_path_as_literal(tmp_path: Path) -> None:
    """Submodule update receives a literal path even when its name begins with pathspec magic."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    leaf_commit = _git_run(leaf_repository, ["rev-parse", "HEAD"])
    _repository_create(coordinating_repository)
    magic_path = ":(glob)leaf"
    _git_run(
        coordinating_repository.parent,
        ["clone", str(leaf_repository), str(coordinating_repository / magic_path)],
    )
    (coordinating_repository / ".gitmodules").write_text(
        '[submodule "literal-magic"]\n' f"\tpath = {magic_path}\n" f"\turl = {leaf_repository}\n",
        encoding="utf-8",
    )
    _git_run(
        coordinating_repository,
        ["--literal-pathspecs", "add", "--", ".gitmodules", magic_path],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add literal magic submodule path"])
    specification = _specification_create(coordinating_repository)

    worktree_prepare(coordinating_repository, specification, [])

    task_submodule = _task_root_get(coordinating_repository) / magic_path
    assert _git_run(task_submodule, ["rev-parse", "HEAD"]) == leaf_commit


def test_prepare_allows_explicit_task_owned_submodule_changes_and_bootstrap_resources(tmp_path: Path) -> None:
    """An explicit task-owned submodule must retain implementation and generic resources.

    Args:
        tmp_path: Isolated filesystem root.
    """

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    main_submodule = coordinating_repository / "dependency" / "leaf"
    local_directory = main_submodule / "local"
    local_directory.mkdir()
    (local_directory / "copied.txt").write_text("copy source\n", encoding="utf-8")
    (local_directory / "shared.txt").write_text("shared source\n", encoding="utf-8")
    specification = _specification_create(coordinating_repository)

    prepare_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "prepare",
            "--coordinating-repository",
            str(coordinating_repository),
            "--specification",
            specification.as_posix(),
            "--participating-submodule",
            str(coordinating_repository),
            "dependency/leaf",
        ],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        text=True,
    )

    assert prepare_result.returncode == 0, prepare_result.stderr
    task_root = _task_root_get(coordinating_repository)
    task_submodule = task_root / "dependency" / "leaf"
    assert json.loads(prepare_result.stdout)["participating_submodule_root_list"] == [str(task_submodule)]
    manifest_path = task_submodule / "worktree-bootstrap.toml"
    manifest_path.write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/copied.txt"]
link_optional_path_list = []
link_required_path_list = ["local/shared.txt"]
""",
        encoding="utf-8",
    )
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )

    copied_path = task_submodule / "local" / "copied.txt"
    linked_path = task_submodule / "local" / "shared.txt"
    assert copied_path.read_text(encoding="utf-8") == "copy source\n"
    assert not copied_path.is_symlink()
    assert linked_path.is_symlink()
    assert linked_path.resolve() == main_submodule / "local" / "shared.txt"

    (task_submodule / "README.md").write_text("task-owned implementation\n", encoding="utf-8")
    _git_run(task_submodule, ["config", "user.email", "test@example.com"])
    _git_run(task_submodule, ["config", "user.name", "Worktree Test"])
    _git_run(task_submodule, ["add", "README.md", "worktree-bootstrap.toml"])
    _git_run(task_submodule, ["commit", "-m", "Implement task-owned submodule change"])
    _git_run(task_root, ["add", "dependency/leaf"])

    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )
    assert (task_submodule / "README.md").read_text(encoding="utf-8") == "task-owned implementation\n"
    assert _git_run(coordinating_repository, ["status", "--short"]) == ""


def test_prepare_requires_every_task_owned_nested_submodule_ancestor(tmp_path: Path) -> None:
    """A nested task-owned submodule cannot bypass ownership of its dirty ancestor.

    Args:
        tmp_path: Isolated filesystem root.
    """

    nested_repository = tmp_path / "nested"
    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(nested_repository)
    _repository_create(leaf_repository)
    _git_run(
        leaf_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(nested_repository), "nested"],
    )
    _git_run(leaf_repository, ["commit", "-am", "Add nested submodule"])
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add recursive submodule"])
    specification = _specification_create(coordinating_repository)

    with pytest.raises(WorktreeError, match="requires every submodule ancestor to participate"):
        worktree_prepare(
            coordinating_repository,
            specification,
            [],
            [(coordinating_repository, Path("dependency/leaf/nested"))],
        )


def test_prepare_rejects_task_owned_submodule_addition_after_contracts_authored(tmp_path: Path) -> None:
    """Stable-owner review closes the participating-submodule set.

    Args:
        tmp_path: Isolated filesystem root.
    """

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    worktree_contracts_authored(coordinating_repository, specification)

    with pytest.raises(WorktreeError, match="cannot add task-owned submodules after contracts_authored"):
        worktree_prepare(
            coordinating_repository,
            specification,
            [],
            [(coordinating_repository, Path("dependency/leaf"))],
        )


@pytest.mark.parametrize("resource_path", ["dependency", "dependency/leaf/README.md"])
def test_prepare_rejects_manifest_paths_crossing_submodule_boundaries(
    tmp_path: Path,
    resource_path: str,
) -> None:
    """A parent manifest must not classify an ancestor or descendant of a submodule.

    Args:
        tmp_path: Isolated filesystem root.
        resource_path: Resource path crossing the submodule in either direction.
    """

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    manifest_path = _task_root_get(coordinating_repository) / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        f'copy_required_path_list = ["{resource_path}"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeError, match="crosses a submodule boundary"):
        worktree_prepare(coordinating_repository, specification, [])


def test_prepare_reports_an_unavailable_submodule_gitlink(tmp_path: Path) -> None:
    """Preparation must fail when the exact recorded gitlink object is unavailable.

    Args:
        tmp_path: Isolated filesystem root.
    """

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    unavailable_commit = "a" * 40
    _git_run(
        coordinating_repository,
        ["update-index", "--cacheinfo", f"160000,{unavailable_commit},dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-m", "Record unavailable gitlink"])
    specification = _specification_create(coordinating_repository)

    with pytest.raises(WorktreeError, match="Git command failed.*submodule update"):
        worktree_prepare(coordinating_repository, specification, [])


@pytest.mark.parametrize("damage_kind", ["deleted-baseline-file", "staged-provider-ignore"])
def test_prepare_blocks_an_incomplete_pending_checkout_before_bootstrap_writes(
    tmp_path: Path,
    damage_kind: str,
) -> None:
    """Pending ownership does not authorize writes into a partial baseline checkout."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    task_root = _task_root_get(coordinating_repository)
    _pending_worktree_create(coordinating_repository, coordinating_repository, specification)
    _git_run(
        coordinating_repository,
        ["worktree", "add", "-b", TASK_PREFIX, str(task_root), "HEAD"],
    )
    if damage_kind == "deleted-baseline-file":
        (task_root / "README.md").unlink()
        error_pattern = "checkout is incomplete"
    else:
        with (task_root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("/.spec\n/.worktree/\n")
        _git_run(task_root, ["add", ".gitignore"])
        error_pattern = "index is not the selected baseline"
    damaged_gitignore_text = (task_root / ".gitignore").read_text(encoding="utf-8")

    with pytest.raises(WorktreeError, match=error_pattern):
        worktree_prepare(coordinating_repository, specification, [])

    assert not os.path.lexists(task_root / ".spec")
    assert not os.path.lexists(task_root / "worktree-bootstrap.toml")
    assert (task_root / ".gitignore").read_text(encoding="utf-8") == damaged_gitignore_text
    assert (task_root / "README.md").exists() == (damage_kind != "deleted-baseline-file")


def test_prepare_reconstructs_absent_private_state_for_an_exact_inactive_worktree(tmp_path: Path) -> None:
    """Preparation must recover an exact baseline worktree after interrupted state writing.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    state_path_text = _git_run(
        task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
    )
    state_path = Path(state_path_text)
    if not state_path.is_absolute():
        state_path = task_root / state_path
    state_path.unlink()

    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    assert result["lifecycle_state"] == "repository_prepared"
    assert state_path.is_file()
    assert json.loads(state_path.read_text(encoding="utf-8"))["prefix"] == TASK_PREFIX


def test_prepare_rejects_total_private_state_loss_when_a_paired_goal_exists(tmp_path: Path) -> None:
    """A sealed or active task cannot be silently downgraded after all replicas disappear.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    worktree_contracts_authored(coordinating_repository, specification)
    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    (coordinating_repository / goal).write_text("# Persistent goal\n", encoding="utf-8")
    worktree_seal(coordinating_repository, goal, specification)
    worktree_activate(coordinating_repository, specification)
    task_root = _task_root_get(coordinating_repository)
    (task_root / "implementation.txt").write_text("active task work\n", encoding="utf-8")
    state_path = Path(
        _git_run(
            task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
        )
    )
    if not state_path.is_absolute():
        state_path = task_root / state_path
    state_path.unlink()

    with pytest.raises(WorktreeError, match="task lifecycle cannot be reconstructed"):
        worktree_prepare(coordinating_repository, specification, [])

    assert not state_path.exists()
    assert (task_root / "implementation.txt").read_text(encoding="utf-8") == "active task work\n"
    assert (coordinating_repository / goal).read_text(encoding="utf-8") == "# Persistent goal\n"


def test_validate_migrates_a_schema_v1_private_state_replica(tmp_path: Path) -> None:
    """The first schema-v2 command must migrate a valid private schema-v1 replica.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    state_v2_path = Path(
        _git_run(
            task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
        )
    )
    if not state_v2_path.is_absolute():
        state_v2_path = task_root / state_v2_path
    state_v1_path = Path(
        _git_run(
            task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v1.json"],
        )
    )
    if not state_v1_path.is_absolute():
        state_v1_path = task_root / state_v1_path
    state_payload = json.loads(state_v2_path.read_text(encoding="utf-8"))
    state_payload["schema_version"] = 1
    del state_payload["fingerprint_schema_version"]
    for repository_state in state_payload["repository_state_list"]:
        del repository_state["accepted_main_commit_drift_list"]
        del repository_state["main_leak_fingerprint_by_path_map"]
        del repository_state["participating_submodule_state_list"]
    state_v1_path.write_text(json.dumps(state_payload), encoding="utf-8")
    state_v2_path.unlink()

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert any("migrated private state schema" in item for item in result["performed_repair_list"])
    migrated_payload = json.loads(state_v2_path.read_text(encoding="utf-8"))
    assert migrated_payload["schema_version"] == 2
    assert migrated_payload["repository_state_list"][0]["accepted_main_commit_drift_list"] == []
    assert migrated_payload["repository_state_list"][0]["main_leak_fingerprint_by_path_map"] == {}
    assert migrated_payload["repository_state_list"][0]["participating_submodule_state_list"] == []
    assert not state_v1_path.exists()
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_upgrades_main_commit_drift_attestation_state_in_schema_v2(tmp_path: Path) -> None:
    """Earlier schema-v2 repository owners must gain the closed attestation field once."""

    leaf_repository = tmp_path / "leaf"
    coordinating_repository = tmp_path / "coordinating"
    _repository_create(leaf_repository)
    _repository_create(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["-c", "protocol.file.allow=always", "submodule", "add", str(leaf_repository), "dependency/leaf"],
    )
    _git_run(coordinating_repository, ["commit", "-am", "Add leaf submodule"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(
        coordinating_repository,
        specification,
        [],
        [(coordinating_repository, Path("dependency/leaf"))],
    )
    task_root = _task_root_get(coordinating_repository)
    state_path = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/state-v2.json",
    )
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    repository_state = state_payload["repository_state_list"][0]
    del repository_state["accepted_main_commit_drift_list"]
    del repository_state["participating_submodule_state_list"][0]["accepted_main_commit_drift_list"]
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert any("upgraded main commit-drift attestation state" in item for item in result["performed_repair_list"])
    upgraded_payload = json.loads(state_path.read_text(encoding="utf-8"))
    upgraded_repository_state = upgraded_payload["repository_state_list"][0]
    assert upgraded_repository_state["accepted_main_commit_drift_list"] == []
    assert upgraded_repository_state["participating_submodule_state_list"][0]["accepted_main_commit_drift_list"] == []
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_prefers_a_newer_secondary_v2_replica_over_stale_coordinating_v1(tmp_path: Path) -> None:
    """A missing coordinating v2 replica must not reactivate stale legacy state.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    coordinating_task_root = _task_root_get(coordinating_repository)
    other_task_root = _task_root_get(other_repository)
    coordinating_v2_path = Path(
        _git_run(
            coordinating_task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
        )
    )
    if not coordinating_v2_path.is_absolute():
        coordinating_v2_path = coordinating_task_root / coordinating_v2_path
    coordinating_v1_path = Path(
        _git_run(
            coordinating_task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v1.json"],
        )
    )
    if not coordinating_v1_path.is_absolute():
        coordinating_v1_path = coordinating_task_root / coordinating_v1_path
    other_v1_path = Path(
        _git_run(
            other_task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v1.json"],
        )
    )
    if not other_v1_path.is_absolute():
        other_v1_path = other_task_root / other_v1_path
    stale_payload = json.loads(coordinating_v2_path.read_text(encoding="utf-8"))
    stale_payload["schema_version"] = 1
    del stale_payload["fingerprint_schema_version"]
    for repository_state in stale_payload["repository_state_list"]:
        del repository_state["accepted_main_commit_drift_list"]
        del repository_state["main_leak_fingerprint_by_path_map"]
        del repository_state["participating_submodule_state_list"]
    worktree_contracts_authored(coordinating_repository, specification)
    coordinating_v1_path.write_text(json.dumps(stale_payload), encoding="utf-8")
    coordinating_v2_path.unlink()

    result = json.loads(worktree_validate(coordinating_repository, "contracts_authored", specification))

    assert result["lifecycle_state"] == "contracts_authored"
    assert any("recovered private state from secondary replica" in item for item in result["performed_repair_list"])
    assert json.loads(coordinating_v2_path.read_text(encoding="utf-8"))["lifecycle_state"] == "contracts_authored"
    assert not coordinating_v1_path.exists()
    assert not other_v1_path.exists()
    assert (
        json.loads(worktree_validate(coordinating_repository, "contracts_authored", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_prepare_recovers_temporary_exclude_ownership_after_partial_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted preparation must retain ownership of its local excludes.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    (other_repository / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = []
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )
    _git_run(other_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(other_repository, ["commit", "-m", "Add bootstrap manifest"])
    specification = _specification_create(coordinating_repository)
    other_task_root = _task_root_get(other_repository)
    _pending_worktree_create(coordinating_repository, other_repository, specification)
    _git_run(
        other_repository,
        ["worktree", "add", "-b", TASK_PREFIX, str(other_task_root), "HEAD"],
    )
    original_resource_prepare = WorktreeWorkflow._resource_state_list_prepare

    def resource_prepare_with_late_failure(
        workflow: WorktreeWorkflow,
        main_root: Path,
        *argument_list: object,
        **keyword_argument_map: object,
    ) -> object:
        if main_root == other_repository:
            raise WorktreeError("injected later repository failure")
        return original_resource_prepare(
            workflow,
            main_root,
            *argument_list,
            **keyword_argument_map,
        )

    monkeypatch.setattr(
        WorktreeWorkflow,
        "_resource_state_list_prepare",
        resource_prepare_with_late_failure,
    )
    with pytest.raises(WorktreeError, match="injected later repository failure"):
        worktree_prepare(coordinating_repository, specification, [other_repository])
    monkeypatch.setattr(
        WorktreeWorkflow,
        "_resource_state_list_prepare",
        original_resource_prepare,
    )

    coordinating_task_root = _task_root_get(coordinating_repository)
    for task_root in (coordinating_task_root, other_task_root):
        marker_path_text = _git_run(
            task_root,
            ["rev-parse", "--git-path", "goal-brainstorm-worktree/temporary-exclude-owner-v1"],
        )
        marker_path = Path(marker_path_text)
        if not marker_path.is_absolute():
            marker_path = task_root / marker_path
        assert marker_path.read_text(encoding="utf-8") == "/.worktree/\n"
    worktree_prepare(coordinating_repository, specification, [other_repository])

    state_path_text = _git_run(
        coordinating_task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
    )
    state_path = Path(state_path_text)
    if not state_path.is_absolute():
        state_path = coordinating_task_root / state_path
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert all(item["temporary_exclude_list"] == ["/.worktree/"] for item in state["repository_state_list"])


@pytest.mark.parametrize("marker_phase", ["pending", "recorded"])
@pytest.mark.parametrize("object_kind", ["directory", "symlink", "wrong-file"])
def test_temporary_exclude_marker_damage_preserves_unknown_content(
    tmp_path: Path,
    marker_phase: str,
    object_kind: str,
) -> None:
    """An object shape impossible for an atomic marker is never deleted as repair."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    task_root = _task_root_get(coordinating_repository)
    if marker_phase == "pending":
        marker_path = workflow._pending_temporary_exclude_marker_path_get(  # noqa: SLF001
            coordinating_repository,
            task_root,
        )
    else:
        worktree_prepare(coordinating_repository, specification, [])
        marker_path = Path(
            _git_run(
                task_root,
                ["rev-parse", "--git-path", "goal-brainstorm-worktree/temporary-exclude-owner-v1"],
            )
        )
        if not marker_path.is_absolute():
            marker_path = task_root / marker_path
        marker_path.unlink()
    external_directory = tmp_path / f"external-{marker_phase}-{object_kind}"
    if object_kind == "directory":
        marker_path.mkdir(parents=True)
        sentinel_path = marker_path / "user"
        error_pattern = "[Tt]emporary-exclude.*not one physical ordinary file"
    elif object_kind == "symlink":
        external_directory.mkdir()
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.symlink_to(external_directory, target_is_directory=True)
        sentinel_path = external_directory / "user"
        error_pattern = "[Tt]emporary-exclude.*not one physical ordinary file"
    else:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path = marker_path
        error_pattern = "[Tt]emporary-exclude.*invalid"
    sentinel_path.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match=error_pattern):
        if marker_phase == "pending":
            worktree_prepare(coordinating_repository, specification, [])
        else:
            worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"
    if object_kind == "directory":
        assert marker_path.is_dir()
    elif object_kind == "symlink":
        assert marker_path.is_symlink()
    else:
        assert marker_path.is_file()


def test_prepare_installs_a_recoverable_local_exclude_before_worktree_creation(tmp_path: Path) -> None:
    """The project-local container must be ignored when `git worktree add` checks out.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    observation_path = tmp_path / "post-checkout-ignore-result"
    hook_path = coordinating_repository / ".git" / "hooks" / "post-checkout"
    hook_path.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        f"result = subprocess.run(['git', '-C', {str(coordinating_repository)!r}, "
        "'check-ignore', '--no-index', '--quiet', '.worktree/probe'], check=False)\n"
        f"Path({str(observation_path)!r}).write_text(str(result.returncode), encoding='utf-8')\n",
        encoding="utf-8",
    )
    hook_path.chmod(0o755)

    worktree_prepare(coordinating_repository, specification, [])

    assert observation_path.read_text(encoding="utf-8") == "0"
    task_root = _task_root_get(coordinating_repository)
    marker_path_text = _git_run(
        task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/temporary-exclude-owner-v1"],
    )
    marker_path = Path(marker_path_text)
    if not marker_path.is_absolute():
        marker_path = task_root / marker_path
    assert marker_path.read_text(encoding="utf-8") == "/.worktree/\n"
    pending_directory = coordinating_repository / ".git" / "goal-brainstorm-worktree" / "pending"
    assert list(pending_directory.iterdir()) == []


def test_prepare_fails_before_bootstrap_writes_for_an_unrecorded_wrong_spec_link(tmp_path: Path) -> None:
    """An unrecorded identity collision must preserve its link and other content.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    task_root = _task_root_get(coordinating_repository)
    _git_run(
        coordinating_repository,
        ["worktree", "add", "-b", TASK_PREFIX, str(task_root), "HEAD"],
    )
    wrong_target = tmp_path / "other-spec"
    wrong_target.mkdir()
    (task_root / ".spec").symlink_to(wrong_target)

    with pytest.raises(WorktreeError, match="no exact specification link"):
        worktree_prepare(coordinating_repository, specification, [])

    assert (task_root / ".spec").resolve() == wrong_target
    assert not (task_root / "worktree-bootstrap.toml").exists()


@pytest.mark.parametrize("damage_kind", ["invalid-json", "symbolic-link"])
def test_validate_restores_a_damaged_secondary_private_state_replica(tmp_path: Path, damage_kind: str) -> None:
    """A valid coordinator replica must repair one incomplete secondary write.

    Args:
        tmp_path: Isolated filesystem root.
        damage_kind: Private replica damage to exercise.
    """

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    other_task_root = _task_root_get(other_repository)
    state_path_text = _git_run(
        other_task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
    )
    state_path = Path(state_path_text)
    if not state_path.is_absolute():
        state_path = other_task_root / state_path
    external_state_path = tmp_path / "external-state.json"
    external_state_path.write_text("preserve external content\n", encoding="utf-8")
    if damage_kind == "symbolic-link":
        state_path.unlink()
        state_path.symlink_to(external_state_path)
    else:
        state_path.write_text("{}\n", encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert any(
        f"restored private state replica: {state_path.resolve()}" == item for item in result["performed_repair_list"]
    )
    assert json.loads(state_path.read_text(encoding="utf-8"))["prefix"] == TASK_PREFIX
    assert not state_path.is_symlink()
    assert external_state_path.read_text(encoding="utf-8") == "preserve external content\n"
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


@pytest.mark.parametrize("coordinating_state_content", [None, "{}\n"])
def test_prepare_recovers_sealed_state_from_a_secondary_replica(
    tmp_path: Path,
    coordinating_state_content: str | None,
) -> None:
    """A missing or damaged coordinator replica must retain sealed lifecycle.

    Args:
        tmp_path: Isolated filesystem root.
        coordinating_state_content: Replacement state, or `None` to remove it.
    """

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    worktree_contracts_authored(coordinating_repository, specification)
    (coordinating_repository / goal).write_text("# Test goal\n", encoding="utf-8")
    worktree_seal(coordinating_repository, goal, specification)
    coordinating_task_root = _task_root_get(coordinating_repository)
    state_path_text = _git_run(
        coordinating_task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
    )
    state_path = Path(state_path_text)
    if not state_path.is_absolute():
        state_path = coordinating_task_root / state_path
    if coordinating_state_content is None:
        state_path.unlink()
    else:
        state_path.write_text(coordinating_state_content, encoding="utf-8")

    result = json.loads(worktree_prepare(coordinating_repository, specification, [other_repository]))

    assert result["lifecycle_state"] == "goal_ready"
    assert any("recovered private state from secondary replica" in item for item in result["performed_repair_list"])
    assert state_path.is_file()
    assert json.loads(state_path.read_text(encoding="utf-8"))["lifecycle_state"] == "goal_ready"
    assert (
        json.loads(worktree_validate(coordinating_repository, "goal_ready", specification))["performed_repair_list"]
        == []
    )


def test_validate_rejects_option_like_private_commit_identity(tmp_path: Path) -> None:
    """Private state must not transport arbitrary text into Git revision arguments.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    state_path_text = _git_run(
        task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
    )
    state_path = Path(state_path_text)
    if not state_path.is_absolute():
        state_path = task_root / state_path
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["repository_state_list"][0]["baseline_commit"] = "--help"
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")

    with pytest.raises(WorktreeError, match="invalid commit identity"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)


@pytest.mark.parametrize("owner_marker_present", [True, False])
def test_validate_repairs_missing_manifest_and_repeats_to_a_clean_pass(
    tmp_path: Path,
    owner_marker_present: bool,
) -> None:
    """One command must repair a missing manifest and finish fully stable.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    manifest_path = task_root / "worktree-bootstrap.toml"
    if not owner_marker_present:
        owner_marker_path = Path(
            _git_run(
                task_root,
                ["rev-parse", "--git-path", "goal-brainstorm-worktree/initial-manifest-owner-v1"],
            )
        )
        if not owner_marker_path.is_absolute():
            owner_marker_path = task_root / owner_marker_path
        owner_marker_path.unlink()
    manifest_path.unlink()

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert manifest_path.read_text(encoding="utf-8").startswith("schema_version = 1")
    assert any("restored provider-owned initial manifest" in item for item in result["performed_repair_list"])
    if not owner_marker_present:
        assert any(
            "backfilled initial-manifest recreation ownership" in item for item in result["performed_repair_list"]
        )
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_preserves_resources_when_a_classified_manifest_is_missing(tmp_path: Path) -> None:
    """Validation must not replace an unknown classified manifest with empty state.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    source_path = coordinating_repository / "local" / "classified"
    source_path.parent.mkdir()
    source_path.write_text("source\n", encoding="utf-8")
    task_root = _task_root_get(coordinating_repository)
    manifest_path = task_root / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/classified"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    worktree_prepare(coordinating_repository, specification, [])
    destination_path = task_root / "local" / "classified"
    manifest_path.unlink()

    with pytest.raises(WorktreeError, match="cannot be inferred"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert destination_path.read_text(encoding="utf-8") == "source\n"
    assert not manifest_path.exists()


def test_validate_repairs_an_unregistered_intact_secondary_worktree(tmp_path: Path) -> None:
    """Validation must repair secondary worktree administration from intact content.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    other_task_root = _task_root_get(other_repository)
    administration_path_text = _git_run(
        other_repository,
        ["rev-parse", "--git-path", f"worktrees/{TASK_PREFIX}"],
    )
    administration_path = Path(administration_path_text)
    if not administration_path.is_absolute():
        administration_path = other_repository / administration_path
    (administration_path / "gitdir").write_text(
        f"{tmp_path / 'wrong-location' / '.git'}\n",
        encoding="utf-8",
    )

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert _git_run(other_task_root, ["branch", "--show-current"]) == TASK_PREFIX
    assert any("repaired worktree registration" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_repairs_the_coordinating_worktree_git_pointer(tmp_path: Path) -> None:
    """Validation must recover coordinator state through its intact registration.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    coordinating_task_root = _task_root_get(coordinating_repository)
    git_pointer_path = coordinating_task_root / ".git"
    git_pointer_path.write_text(f"gitdir: {tmp_path / 'missing-administration'}\n", encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert _git_run(coordinating_task_root, ["branch", "--show-current"]) == TASK_PREFIX
    assert any("repaired coordinating worktree administration" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_cli_runs_from_an_unrelated_directory(tmp_path: Path) -> None:
    """Explicit roots must keep CLI behavior independent from the starting directory.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    unrelated_directory = tmp_path / "unrelated"
    _repository_create(coordinating_repository)
    unrelated_directory.mkdir()
    specification = _specification_create(coordinating_repository)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "prepare",
            "--coordinating-repository",
            str(coordinating_repository),
            "--specification",
            specification.as_posix(),
        ],
        capture_output=True,
        check=False,
        cwd=unrelated_directory,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["task_root_list"] == [str(_task_root_get(coordinating_repository))]
    assert not (unrelated_directory / ".worktree").exists()

    contracts_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "contracts-authored",
            "--coordinating-repository",
            str(coordinating_repository),
            "--specification",
            specification.as_posix(),
        ],
        capture_output=True,
        check=False,
        cwd=unrelated_directory,
        text=True,
    )
    assert contracts_result.returncode == 0, contracts_result.stderr
    assert json.loads(contracts_result.stdout)["lifecycle_state"] == "contracts_authored"
    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    (coordinating_repository / goal).write_text("# Test goal\n", encoding="utf-8")
    worktree_seal(coordinating_repository, goal, specification)
    activation_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "activate",
            "--coordinating-repository",
            str(coordinating_repository),
            "--specification",
            specification.as_posix(),
        ],
        capture_output=True,
        check=False,
        cwd=unrelated_directory,
        text=True,
    )

    assert activation_result.returncode == 0, activation_result.stderr
    assert json.loads(activation_result.stdout)["lifecycle_state"] == "active"
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task CLI work\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("independent CLI main work\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    _git_run(coordinating_repository, ["commit", "-m", "Independent CLI overlap"])
    current_main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])
    acceptance_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "accept-main-commit-drift",
            "--coordinating-repository",
            str(coordinating_repository),
            "--specification",
            specification.as_posix(),
            "--main-repository",
            str(coordinating_repository),
            "--commit",
            current_main_commit,
            "--path",
            "README.md",
        ],
        capture_output=True,
        check=False,
        cwd=unrelated_directory,
        text=True,
    )
    assert acceptance_result.returncode == 0, acceptance_result.stderr
    assert json.loads(acceptance_result.stdout)["lifecycle_state"] == "active"


def test_prepare_ignores_inherited_git_repository_redirection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit roots must defeat direct and config-injected Git redirection.

    Args:
        monkeypatch: Scoped environment mutation helper.
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    sentinel_repository = tmp_path / "sentinel"
    _repository_create(coordinating_repository)
    _repository_create(sentinel_repository)
    specification = _specification_create(coordinating_repository)
    injected_config_path = tmp_path / "injected.gitconfig"
    injected_config_path.write_text(
        f"[core]\n\tworktree = {sentinel_repository}\n",
        encoding="utf-8",
    )

    with monkeypatch.context() as redirected_environment:
        for variable_name, value in {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_GLOBAL": str(injected_config_path),
            "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_PARAMETERS": "'core.worktree'='{}'".format(sentinel_repository),
            "GIT_CONFIG_VALUE_0": str(sentinel_repository),
            "GIT_DIR": str(sentinel_repository / ".git"),
            "GIT_INDEX_FILE": str(sentinel_repository / ".git" / "index"),
            "GIT_WORK_TREE": str(sentinel_repository),
        }.items():
            redirected_environment.setenv(variable_name, value)
        result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    assert result["task_root_list"] == [str(_task_root_get(coordinating_repository))]
    assert _task_root_get(coordinating_repository).is_dir()
    assert not _task_root_get(sentinel_repository).exists()
    assert _git_run(sentinel_repository, ["status", "--short"]) == ""


def test_prepare_rejects_an_unrelated_same_name_branch(tmp_path: Path) -> None:
    """An unrelated task-branch collision must fail without overwrite.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    _git_run(coordinating_repository, ["branch", TASK_PREFIX])
    (coordinating_repository / "README.md").write_text("later main commit\n", encoding="utf-8")
    _git_run(coordinating_repository, ["commit", "-am", "Advance main"])

    with pytest.raises(WorktreeError, match="does not match selected baseline"):
        worktree_prepare(coordinating_repository, specification, [])


def test_prepare_rejects_an_unrecorded_same_name_branch_at_the_selected_baseline(tmp_path: Path) -> None:
    """A matching branch without task state or a worktree remains ambiguous.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    _git_run(coordinating_repository, ["branch", TASK_PREFIX])

    with pytest.raises(WorktreeError, match="no pending ownership"):
        worktree_prepare(coordinating_repository, specification, [])

    assert not _task_root_get(coordinating_repository).exists()
    assert _git_run(coordinating_repository, ["branch", "--list", TASK_PREFIX]) == TASK_PREFIX


def test_prepare_preserves_an_unrelated_same_path_collision(tmp_path: Path) -> None:
    """Preparation must not overwrite an unregistered filesystem collision.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    collision_root = _task_root_get(coordinating_repository)
    collision_root.mkdir(parents=True)
    collision_path = collision_root / "user-content.txt"
    collision_path.write_text("preserve me\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="not one adoptable worktree"):
        worktree_prepare(coordinating_repository, specification, [])

    assert collision_path.read_text(encoding="utf-8") == "preserve me\n"
    exclude_text = (coordinating_repository / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.worktree/" not in exclude_text


def test_prepare_rejects_a_symbolic_worktree_container_before_mutation(tmp_path: Path) -> None:
    """The project-local worktree container must not redirect outside its owner.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    external_directory = tmp_path / "external"
    _repository_create(coordinating_repository)
    external_directory.mkdir()
    specification = _specification_create(coordinating_repository)
    (coordinating_repository / ".worktree").symlink_to(external_directory, target_is_directory=True)

    with pytest.raises(WorktreeError, match="container is not one physical directory"):
        worktree_prepare(coordinating_repository, specification, [])

    assert list(external_directory.iterdir()) == []
    assert _git_run(coordinating_repository, ["branch", "--list", TASK_PREFIX]) == ""


def test_prepare_rejects_the_task_branch_checked_out_at_another_path(tmp_path: Path) -> None:
    """One same-name branch at another path must not be moved or reused.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    unrelated_worktree = tmp_path / "unrelated-worktree"
    _git_run(
        coordinating_repository,
        ["worktree", "add", "-b", TASK_PREFIX, str(unrelated_worktree), "HEAD"],
    )

    with pytest.raises(WorktreeError, match="checked out at another path"):
        worktree_prepare(coordinating_repository, specification, [])

    assert _git_run(unrelated_worktree, ["branch", "--show-current"]) == TASK_PREFIX
    assert not _task_root_get(coordinating_repository).exists()


def test_validate_rejects_changed_sealed_goal(tmp_path: Path) -> None:
    """Both sealed task artifacts must remain immutable.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    goal_path = coordinating_repository / goal
    worktree_contracts_authored(coordinating_repository, specification)
    goal_path.write_text("# Test goal\n", encoding="utf-8")
    worktree_seal(coordinating_repository, goal, specification)
    goal_path.write_text("# Changed goal\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="Sealed goal changed"):
        worktree_validate(coordinating_repository, "goal_ready", specification)

    reseal_result = json.loads(worktree_seal(coordinating_repository, goal, specification))
    assert reseal_result["lifecycle_state"] == "goal_ready"
    assert (
        json.loads(worktree_validate(coordinating_repository, "goal_ready", specification))["performed_repair_list"]
        == []
    )


def test_seal_rejects_a_forced_tracked_goal(tmp_path: Path) -> None:
    """Sealing must not accept a task goal added to the coordinating index.

    Args:
        tmp_path: Isolated filesystem root.
    """

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    worktree_contracts_authored(coordinating_repository, specification)
    (coordinating_repository / goal).write_text("# Test goal\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "--force", goal.as_posix()])

    with pytest.raises(WorktreeError, match="remain untracked by Git"):
        worktree_seal(coordinating_repository, goal, specification)


@pytest.mark.parametrize("damage_kind", ["missing-link", "wrong-link", "dirty-path"])
def test_prepare_rejects_inexact_markerless_worktree_adoption(
    tmp_path: Path,
    damage_kind: str,
) -> None:
    """Markerless reconstruction requires the complete observable bootstrap."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    state_path_text = _git_run(
        task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
    )
    state_path = Path(state_path_text)
    if not state_path.is_absolute():
        state_path = task_root / state_path
    state_path.unlink()
    if damage_kind == "missing-link":
        (task_root / ".spec").unlink()
        expected_error = "no exact specification link"
    elif damage_kind == "wrong-link":
        (task_root / ".spec").unlink()
        wrong_specification_root = tmp_path / "wrong-spec"
        wrong_specification_root.mkdir()
        (task_root / ".spec").symlink_to(wrong_specification_root)
        expected_error = "no exact specification link"
    else:
        (task_root / "independent.txt").write_text("independent\n", encoding="utf-8")
        expected_error = "independent dirty state"

    with pytest.raises(WorktreeError, match=expected_error):
        worktree_prepare(coordinating_repository, specification, [])

    if damage_kind == "dirty-path":
        assert (task_root / "independent.txt").read_text(encoding="utf-8") == "independent\n"


def test_prepare_resumes_a_pending_branch_without_a_registered_path(tmp_path: Path) -> None:
    """A durable pending marker must recover Git failure after branch creation."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    _pending_worktree_create(coordinating_repository, coordinating_repository, specification)
    _git_run(coordinating_repository, ["branch", TASK_PREFIX])

    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    task_root = _task_root_get(coordinating_repository)
    assert result["task_root_list"] == [str(task_root)]
    assert _git_run(task_root, ["branch", "--show-current"]) == TASK_PREFIX
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    assert workflow._pending_worktree_optional_get(coordinating_repository) is None  # noqa: SLF001


def test_prepare_rejects_a_redirected_pending_worktree_marker_parent(tmp_path: Path) -> None:
    """Pending ownership must never be written through a Git-admin symlink."""

    coordinating_repository = tmp_path / "coordinating"
    external_directory = tmp_path / "external"
    _repository_create(coordinating_repository)
    external_directory.mkdir()
    specification = _specification_create(coordinating_repository)
    private_owner_path = coordinating_repository / ".git" / "goal-brainstorm-worktree"
    private_owner_path.symlink_to(external_directory, target_is_directory=True)

    with pytest.raises(WorktreeError, match="unsafe parent"):
        worktree_prepare(coordinating_repository, specification, [])

    assert list(external_directory.iterdir()) == []
    assert not _task_root_get(coordinating_repository).exists()


def test_main_leak_recovery_resumes_after_worktree_restore_before_index_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A killed recovery may expose the target object before its atomic index update."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task patch\n", encoding="utf-8")
    _git_run(task_root, ["add", "README.md"])
    (coordinating_repository / "README.md").write_text("task patch\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    original_restore = WorktreeWorkflow._index_entry_list_restore

    def interrupted_restore(
        self: WorktreeWorkflow,
        repository_root: Path,
        path_text: str,
        index_entry_list: list[str],
    ) -> None:
        raise RuntimeError("simulated process death before index restore")

    with monkeypatch.context() as interrupted_process:
        interrupted_process.setattr(
            WorktreeWorkflow,
            "_index_entry_list_restore",
            interrupted_restore,
        )
        with pytest.raises(RuntimeError, match="simulated process death"):
            worktree_main_leak_recover(
                coordinating_repository,
                specification,
                coordinating_repository,
                [Path("README.md")],
            )

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "baseline\n"
    assert _git_run(coordinating_repository, ["diff", "--cached", "--name-only"]) == "README.md"
    assert WorktreeWorkflow._index_entry_list_restore is original_restore

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert _git_run(coordinating_repository, ["status", "--short"]) == ""
    assert any("completed durable main-leak recovery" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_recovers_main_leak_after_process_death_before_first_metadata(
    tmp_path: Path,
) -> None:
    """Durable leak provenance owns exact pre-metadata replacement staging."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task patch\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("task patch\n", encoding="utf-8")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"sys.path.insert(0, {str(LIBRARY_ROOT)!r})\n"
                "from pathlib import Path\n"
                "from worktree import WorktreeWorkflow, worktree_main_leak_recover\n"
                "def die(self, task_root, transaction):\n"
                "    os._exit(73)\n"
                "WorktreeWorkflow._main_leak_transaction_metadata_write = die\n"
                f"worktree_main_leak_recover(Path({str(coordinating_repository)!r}), "
                f"Path({str(specification)!r}), Path({str(coordinating_repository)!r}), "
                "[Path('README.md')])\n"
            ),
        ],
        check=False,
    )

    assert process.returncode == 73
    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "baseline\n"
    assert _git_run(coordinating_repository, ["status", "--short"]) == ""
    assert any("removed interrupted unexposed main-leak" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_recovers_main_leak_after_process_death_during_exposure_clone(
    tmp_path: Path,
) -> None:
    """A partial regenerable exposure is discarded under durable metadata."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("task patch\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("task patch\n", encoding="utf-8")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"sys.path.insert(0, {str(LIBRARY_ROOT)!r})\n"
                "from pathlib import Path\n"
                "from worktree import WorktreeWorkflow, worktree_main_leak_recover\n"
                "original = WorktreeWorkflow._path_clone\n"
                "def die(self, source_path, destination_path):\n"
                "    if destination_path.name == 'exposure':\n"
                "        destination_path.write_bytes(b'partial')\n"
                "        os._exit(74)\n"
                "    return original(self, source_path, destination_path)\n"
                "WorktreeWorkflow._path_clone = die\n"
                f"worktree_main_leak_recover(Path({str(coordinating_repository)!r}), "
                f"Path({str(specification)!r}), Path({str(coordinating_repository)!r}), "
                "[Path('README.md')])\n"
            ),
        ],
        check=False,
    )

    assert process.returncode == 74
    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "baseline\n"
    assert _git_run(coordinating_repository, ["status", "--short"]) == ""
    assert any("completed durable main-leak recovery" in item for item in result["performed_repair_list"])


def test_prepare_recovers_main_preimage_after_process_death_during_random_capture(
    tmp_path: Path,
) -> None:
    """Atomic random intent owns an interrupted dirty-main preimage clone."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    (coordinating_repository / "README.md").write_text("accepted dirty main\n", encoding="utf-8")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"sys.path.insert(0, {str(LIBRARY_ROOT)!r})\n"
                "from pathlib import Path\n"
                "from worktree import WorktreeWorkflow, worktree_prepare\n"
                "original = WorktreeWorkflow._path_clone\n"
                "def die(self, source_path, destination_path):\n"
                "    if destination_path.name == 'working' and "
                "'private-clone-staging-v1' in destination_path.parts:\n"
                "        destination_path.write_bytes(b'partial')\n"
                "        os._exit(79)\n"
                "    return original(self, source_path, destination_path)\n"
                "WorktreeWorkflow._path_clone = die\n"
                f"worktree_prepare(Path({str(coordinating_repository)!r}), "
                f"Path({str(specification)!r}), [])\n"
            ),
        ],
        check=False,
    )

    assert process.returncode == 79
    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "accepted dirty main\n"
    assert any(
        "removed interrupted unpublished main-preimage staging" in item for item in result["performed_repair_list"]
    )
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_recovers_resource_after_process_death_before_first_metadata(
    tmp_path: Path,
) -> None:
    """A manifest declaration owns its exact pre-metadata resource staging slot."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    source_path = coordinating_repository / "local" / "cache"
    source_path.parent.mkdir()
    source_path.write_text("source\n", encoding="utf-8")
    (task_root / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/cache"]
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"sys.path.insert(0, {str(LIBRARY_ROOT)!r})\n"
                "from pathlib import Path\n"
                "from worktree import WorktreeWorkflow, worktree_validate\n"
                "original = WorktreeWorkflow._private_text_atomic_write\n"
                "def die(self, destination_path, content, **kwargs):\n"
                "    if destination_path.name == 'metadata.json' and "
                "'resource-transaction-staging-v1' in destination_path.parts:\n"
                "        os._exit(75)\n"
                "    return original(self, destination_path, content, **kwargs)\n"
                "WorktreeWorkflow._private_text_atomic_write = die\n"
                f"worktree_validate(Path({str(coordinating_repository)!r}), "
                f"'repository_prepared', Path({str(specification)!r}))\n"
            ),
        ],
        check=False,
    )

    assert process.returncode == 75
    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert (task_root / "local" / "cache").read_text(encoding="utf-8") == "source\n"
    assert any(
        "removed interrupted unpublished resource transaction staging" in item
        for item in result["performed_repair_list"]
    )
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_recovers_resource_preimage_after_process_death_during_random_capture(
    tmp_path: Path,
) -> None:
    """Atomic random intent makes a partial source-preimage clone disposable."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    source_path = coordinating_repository / "local" / "cache"
    source_path.parent.mkdir()
    source_path.write_text("source\n", encoding="utf-8")
    (task_root / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/cache"]
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"sys.path.insert(0, {str(LIBRARY_ROOT)!r})\n"
                "from pathlib import Path\n"
                "from worktree import WorktreeWorkflow, worktree_validate\n"
                "original = WorktreeWorkflow._path_clone\n"
                "def die(self, source_path, destination_path):\n"
                "    if destination_path.name == 'source' and "
                "'private-clone-staging-v1' in destination_path.parts:\n"
                "        destination_path.write_bytes(b'partial')\n"
                "        os._exit(78)\n"
                "    return original(self, source_path, destination_path)\n"
                "WorktreeWorkflow._path_clone = die\n"
                f"worktree_validate(Path({str(coordinating_repository)!r}), "
                f"'repository_prepared', Path({str(specification)!r}))\n"
            ),
        ],
        check=False,
    )

    assert process.returncode == 78
    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert (task_root / "local" / "cache").read_text(encoding="utf-8") == "source\n"
    assert any(
        "removed interrupted unpublished resource-source-preimage staging" in item
        for item in result["performed_repair_list"]
    )


def test_validate_blocks_unknown_resource_source_preimage_content(tmp_path: Path) -> None:
    """The resource-preimage owner is a closed private namespace."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    sentinel_path = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/resource-source-preimage-v1",
        )
        / "unknown"
        / "source"
        / "USER"
    )
    sentinel_path.parent.mkdir(parents=True)
    sentinel_path.write_text("independent\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="source-preimage owner contains unknown content"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert sentinel_path.read_text(encoding="utf-8") == "independent\n"


def test_prepare_retires_an_obsolete_resource_preimage_before_readding_the_path(
    tmp_path: Path,
) -> None:
    """Removing ownership retires exact source bytes only after durable state."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    source_path = coordinating_repository / "local" / "data"
    source_path.parent.mkdir()
    source_path.write_text("source v1\n", encoding="utf-8")
    manifest_path = coordinating_repository / "worktree-bootstrap.toml"
    manifest_path.write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/data"]
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add copy resource"])
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    snapshot_directory = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/resource-source-preimage-v1",
        )
        / hashlib.sha256(os.fsencode("local/data")).hexdigest()
    )
    assert (snapshot_directory / "source").read_text(encoding="utf-8") == "source v1\n"
    task_manifest_path = task_root / "worktree-bootstrap.toml"
    task_manifest_path.write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = []
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )

    removal_result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    assert not snapshot_directory.exists()
    assert any(
        "retired obsolete private resource source preimage" in item for item in removal_result["performed_repair_list"]
    )
    source_path.write_text("source v2\n", encoding="utf-8")
    task_manifest_path.write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/data"]
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )

    worktree_prepare(coordinating_repository, specification, [])

    assert (task_root / "local" / "data").read_text(encoding="utf-8") == "source v2\n"
    assert (snapshot_directory / "source").read_text(encoding="utf-8") == "source v2\n"


def test_validate_preserves_a_matching_unowned_resource_transaction_directory(
    tmp_path: Path,
) -> None:
    """A manifest path and deterministic hash do not prove transaction provenance."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    source_path = coordinating_repository / "local" / "cache"
    source_path.parent.mkdir()
    source_path.write_text("source\n", encoding="utf-8")
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    sentinel_path = (
        workflow._resource_transaction_directory_get(  # noqa: SLF001
            task_root,
            "local/cache",
        )
        / "replacement"
        / "USER"
    )
    sentinel_path.parent.mkdir(parents=True)
    sentinel_path.write_text("independent\n", encoding="utf-8")
    (task_root / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/cache"]
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeError, match="metadata is unavailable"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert sentinel_path.read_text(encoding="utf-8") == "independent\n"
    assert not (task_root / "local" / "cache").exists()


def test_validate_reports_proven_metadata_less_resource_transaction_staging_once(
    tmp_path: Path,
) -> None:
    """A proven pre-marker crash is cleaned explicitly and reported once."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    source_path = coordinating_repository / "local" / "cache"
    source_path.parent.mkdir()
    source_path.write_text("source\n", encoding="utf-8")
    (coordinating_repository / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/cache"]
link_optional_path_list = []
link_required_path_list = []
        """,
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add bootstrap manifest"])
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    transaction_directory = workflow._resource_transaction_directory_get(  # noqa: SLF001
        task_root,
        "local/cache",
    )
    transaction_directory.mkdir(parents=True)

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert not transaction_directory.exists()
    assert any("removed unexposed resource transaction staging" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


@pytest.mark.parametrize("writer_kind", ["text", "bytes"])
@pytest.mark.parametrize("object_kind", ["directory", "symlink"])
def test_private_atomic_write_preserves_impossible_staging_object(
    tmp_path: Path,
    writer_kind: str,
    object_kind: str,
) -> None:
    """Private atomic writers never delete a staging shape they cannot produce."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    destination_path = workflow._git_path_get(  # noqa: SLF001
        task_root,
        Path("goal-brainstorm-worktree") / f"atomic-{writer_kind}-probe",
    )
    temporary_path = destination_path.with_name(f"{destination_path.name}.tmp")
    external_directory = tmp_path / f"external-{writer_kind}-{object_kind}"
    if object_kind == "directory":
        temporary_path.mkdir(parents=True)
        sentinel_path = temporary_path / "user"
    else:
        external_directory.mkdir()
        temporary_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.symlink_to(external_directory, target_is_directory=True)
        sentinel_path = external_directory / "user"
    sentinel_path.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="staging path is not one physical ordinary file"):
        if writer_kind == "text":
            workflow._private_text_atomic_write(destination_path, "target\n")  # noqa: SLF001
        else:
            workflow._private_bytes_atomic_write(destination_path, b"target\n")  # noqa: SLF001

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"
    assert not destination_path.exists()


@pytest.mark.parametrize("writer_kind", ["text", "bytes"])
def test_private_atomic_write_preserves_unowned_legacy_staging_bytes(
    tmp_path: Path,
    writer_kind: str,
) -> None:
    """A deterministic legacy temp name no longer supplies deletion provenance."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    destination_path = workflow._git_path_get(  # noqa: SLF001
        task_root,
        Path("goal-brainstorm-worktree") / f"atomic-{writer_kind}-probe",
    )
    temporary_path = destination_path.with_name(f"{destination_path.name}.tmp")
    temporary_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path.write_bytes(b"independent\n")

    with pytest.raises(WorktreeError, match="staging path contains independent content"):
        if writer_kind == "text":
            workflow._private_text_atomic_write(destination_path, "target\n")  # noqa: SLF001
        else:
            workflow._private_bytes_atomic_write(destination_path, b"target\n")  # noqa: SLF001

    assert temporary_path.read_bytes() == b"independent\n"
    assert not destination_path.exists()


@pytest.mark.parametrize("writer_kind", ["text", "bytes"])
def test_private_atomic_write_recovers_an_intent_owned_partial_random_stage(
    tmp_path: Path,
    writer_kind: str,
) -> None:
    """An atomic random intent proves a partial private stage is disposable."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    destination_path = workflow._git_path_get(  # noqa: SLF001
        task_root,
        Path("goal-brainstorm-worktree") / f"atomic-{writer_kind}-probe",
    )
    owner_root = workflow._private_atomic_write_owner_root_get(destination_path)  # noqa: SLF001
    owner_root.mkdir(parents=True, exist_ok=True)
    token = "a" * 64
    stage_path = owner_root / f"{token}.stage"
    marker_path = owner_root / f"{token}.intent"
    target_content = b"target\n"
    marker_path.symlink_to(
        json.dumps(
            {
                "content_sha256": hashlib.sha256(target_content).hexdigest(),
                "destination": str(destination_path.absolute()),
                "schema_version": 1,
                "staging_name": stage_path.name,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    stage_path.write_bytes(b"tar")

    if writer_kind == "text":
        workflow._private_text_atomic_write(destination_path, target_content.decode())  # noqa: SLF001
    else:
        workflow._private_bytes_atomic_write(destination_path, target_content)  # noqa: SLF001

    assert destination_path.read_bytes() == target_content
    assert not marker_path.exists()
    assert not stage_path.exists()


@pytest.mark.parametrize("crash_phase", ["before-replace", "after-replace"])
def test_validate_reconciles_interrupted_ordinary_text_write_once(
    tmp_path: Path,
    crash_phase: str,
) -> None:
    """Ordinary-text transactions preserve either the prior or exposed exact content."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    destination_path = task_root / ".gitignore"
    previous_content = destination_path.read_text(encoding="utf-8")
    expected_content = f"{previous_content}# preserved project rule\n"
    mode = destination_path.stat(follow_symlinks=False).st_mode & 0o777
    destination_identity = str(destination_path.absolute())
    transaction_name = hashlib.sha256(destination_identity.encode()).hexdigest()
    marker_path = workflow._git_path_get(  # noqa: SLF001
        task_root,
        Path("goal-brainstorm-worktree") / "atomic-text-write-v1" / f"{transaction_name}.json",
    )
    temporary_path = destination_path.parent / f".{destination_path.name}.{transaction_name}.tmp"
    payload = {
        "destination": destination_identity,
        "expected_fingerprint": workflow._regular_file_fingerprint_get(  # noqa: SLF001
            expected_content.encode(),
            mode,
        ),
        "mode": mode,
        "previous_fingerprint": workflow._path_fingerprint_get(destination_path),  # noqa: SLF001
        "schema_version": 1,
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(payload), encoding="utf-8")
    if crash_phase == "before-replace":
        temporary_path.write_text(expected_content, encoding="utf-8")
        temporary_path.chmod(mode)
    else:
        destination_path.write_text(expected_content, encoding="utf-8")
        destination_path.chmod(mode)

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    expected_outcome = "rolled back" if crash_phase == "before-replace" else "finalized"
    expected_surviving_content = previous_content if crash_phase == "before-replace" else expected_content
    assert destination_path.read_text(encoding="utf-8") == expected_surviving_content
    assert not marker_path.exists()
    assert not temporary_path.exists()
    assert any(f"{expected_outcome} interrupted atomic text write" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_recovers_a_partially_written_private_atomic_text_marker(
    tmp_path: Path,
) -> None:
    """A hash-owned partial marker predates all project-file mutation."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    gitignore_path = task_root / ".gitignore"
    gitignore_path.write_text(
        gitignore_path.read_text(encoding="utf-8").replace("/.spec\n", ""),
        encoding="utf-8",
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"sys.path.insert(0, {str(LIBRARY_ROOT)!r})\n"
                "from pathlib import Path\n"
                "from worktree import WorktreeWorkflow, worktree_validate\n"
                "original = WorktreeWorkflow._private_text_atomic_write\n"
                "def die(self, destination_path, content, **kwargs):\n"
                "    if destination_path.name.endswith('.json') and "
                "'atomic-text-write-v1' in destination_path.parts:\n"
                "        destination_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "        destination_path.with_name(destination_path.name + '.tmp').write_bytes(b'{\"dest')\n"
                "        os._exit(76)\n"
                "    return original(self, destination_path, content, **kwargs)\n"
                "WorktreeWorkflow._private_text_atomic_write = die\n"
                f"worktree_validate(Path({str(coordinating_repository)!r}), "
                f"'repository_prepared', Path({str(specification)!r}))\n"
            ),
        ],
        check=False,
    )

    assert process.returncode == 76
    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert "/.spec\n" in gitignore_path.read_text(encoding="utf-8")
    assert any("removed partial unexposed atomic text" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


@pytest.mark.parametrize(
    "independent_content",
    ["USER INDEPENDENT DATA\n", "TARGET"],
)
def test_validate_preserves_changed_ordinary_text_project_staging(
    tmp_path: Path,
    independent_content: str,
) -> None:
    """A durable marker does not authorize deletion of unrelated project bytes."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    destination_path = task_root / ".gitignore"
    previous_fingerprint = workflow._path_fingerprint_get(destination_path)  # noqa: SLF001
    expected_content = (
        "TARGET-LONG-CONTENT\n"
        if independent_content == "TARGET"
        else destination_path.read_text(encoding="utf-8") + "# provider target\n"
    )
    mode = destination_path.stat(follow_symlinks=False).st_mode & 0o777
    destination_identity = str(destination_path.absolute())
    transaction_name = hashlib.sha256(os.fsencode(destination_identity)).hexdigest()
    marker_path = workflow._git_path_get(  # noqa: SLF001
        task_root,
        Path("goal-brainstorm-worktree") / "atomic-text-write-v1" / f"{transaction_name}.json",
    )
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(
            {
                "destination": destination_identity,
                "expected_content": expected_content,
                "expected_fingerprint": workflow._regular_file_fingerprint_get(  # noqa: SLF001
                    expected_content.encode(),
                    mode,
                ),
                "mode": mode,
                "previous_fingerprint": previous_fingerprint,
                "schema_version": 2,
            }
        ),
        encoding="utf-8",
    )
    temporary_path = destination_path.parent / f".{destination_path.name}.{transaction_name}.tmp"
    temporary_path.write_text(independent_content, encoding="utf-8")

    with pytest.raises(WorktreeError, match="contains independent content"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert temporary_path.read_text(encoding="utf-8") == independent_content
    assert workflow._path_fingerprint_get(destination_path) == previous_fingerprint  # noqa: SLF001


@pytest.mark.parametrize("resource_kind", ["file", "directory"])
@pytest.mark.parametrize("mutation_kind", ["delete", "rename"])
def test_validate_preserves_committed_copy_resource_removal_as_a_task_mutation(
    tmp_path: Path,
    resource_kind: str,
    mutation_kind: str,
) -> None:
    """Once bootstrap commits a copy, its absence is isolated task state."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    source_path = coordinating_repository / "local" / "data"
    source_path.parent.mkdir()
    if resource_kind == "file":
        source_path.write_text("source\n", encoding="utf-8")
    else:
        source_path.mkdir()
        (source_path / "value.txt").write_text("source\n", encoding="utf-8")
    (coordinating_repository / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/data"]
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add copy resource"])
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    destination_path = task_root / "local" / "data"
    renamed_path = task_root / "local" / "renamed"
    if mutation_kind == "rename":
        destination_path.rename(renamed_path)
    elif resource_kind == "file":
        destination_path.unlink()
    else:
        (destination_path / "value.txt").unlink()
        destination_path.rmdir()

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert not destination_path.exists()
    if mutation_kind == "rename":
        expected_path = renamed_path if resource_kind == "file" else renamed_path / "value.txt"
        assert expected_path.read_text(encoding="utf-8") == "source\n"
    assert not any("repaired copy resource" in item for item in result["performed_repair_list"])


def test_prepare_resumes_an_interrupted_directory_resource_removal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Former resource directories remain recoverable until new state is durable."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    source_path = coordinating_repository / "local" / "cache"
    source_path.mkdir(parents=True)
    (source_path / "value.txt").write_text("source\n", encoding="utf-8")
    manifest_path = coordinating_repository / "worktree-bootstrap.toml"
    manifest_path.write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        'copy_required_path_list = ["local/cache"]\n'
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add bootstrap manifest"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    destination_path = task_root / "local" / "cache"
    (task_root / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        "copy_optional_path_list = []\n"
        "copy_required_path_list = []\n"
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    original_expose = WorktreeWorkflow._resource_transaction_expose

    def interrupted_expose(
        self: WorktreeWorkflow,
        owner_root: Path,
        transaction: dict[str, object],
        performed_repair_list: list[str],
    ) -> None:
        original_expose(self, owner_root, transaction, performed_repair_list)  # type: ignore[arg-type]
        if transaction["strategy"] == "remove":
            raise RuntimeError("simulated process death after directory removal")

    with monkeypatch.context() as interrupted_process:
        interrupted_process.setattr(
            WorktreeWorkflow,
            "_resource_transaction_expose",
            interrupted_expose,
        )
        with pytest.raises(RuntimeError, match="simulated process death"):
            worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert not destination_path.exists()

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert not destination_path.exists()
    assert any("former-resource removal" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


@pytest.mark.parametrize("strategy", ["copy", "link", "remove"])
def test_resource_transaction_resume_rejects_a_redirected_destination_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    strategy: str,
) -> None:
    """Every pending resource strategy must re-prove its physical task parent."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    task_root = _task_root_get(coordinating_repository)
    source_path = coordinating_repository / "redirected" / "item"
    source_path.parent.mkdir()
    source_path.write_text("source\n", encoding="utf-8")
    destination_path = task_root / "redirected" / "item"
    if strategy == "remove":
        destination_path.parent.mkdir()
        destination_path.write_text("source\n", encoding="utf-8")

    def stop_before_exposure(
        self: WorktreeWorkflow,
        owner_root: Path,
        transaction: dict[str, object],
        performed_repair_list: list[str],
    ) -> None:
        raise RuntimeError("simulated process death before resource exposure")

    with monkeypatch.context() as interrupted_process:
        interrupted_process.setattr(
            WorktreeWorkflow,
            "_resource_transaction_expose",
            stop_before_exposure,
        )
        with pytest.raises(RuntimeError, match="before resource exposure"):
            if strategy == "remove":
                workflow._resource_removal_transaction_create(  # noqa: SLF001
                    task_root,
                    "redirected/item",
                    workflow._path_fingerprint_get(destination_path),  # noqa: SLF001
                    [],
                )
            else:
                workflow._resource_transaction_create(  # noqa: SLF001
                    task_root,
                    "redirected/item",
                    source_path,
                    workflow._path_fingerprint_get(source_path),  # noqa: SLF001
                    strategy,
                    [],
                )
    saved_parent = task_root / "redirected-saved"
    if destination_path.parent.exists():
        destination_path.parent.replace(saved_parent)
    external_directory = tmp_path / f"external-{strategy}"
    external_directory.mkdir()
    sentinel_path = external_directory / "sentinel.txt"
    sentinel_path.write_text("preserve\n", encoding="utf-8")
    destination_path.parent.symlink_to(external_directory, target_is_directory=True)
    transaction = workflow._resource_transaction_optional_get(  # noqa: SLF001
        task_root,
        "redirected/item",
    )
    assert transaction is not None

    with pytest.raises(WorktreeError, match="non-physical parent|escapes repository boundary"):
        WorktreeWorkflow._resource_transaction_expose(
            workflow,
            task_root,
            transaction,
            [],
        )

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"


def test_main_leak_transaction_resume_rejects_a_redirected_main_parent(tmp_path: Path) -> None:
    """An existing recovery marker must not authorize writes through a new symlink."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    task_root = _task_root_get(coordinating_repository)
    leak_path = coordinating_repository / "redirected" / "item"
    leak_path.parent.mkdir()
    leak_path.write_text("leak\n", encoding="utf-8")
    target_path = tmp_path / "target"
    target_path.write_text("target\n", encoding="utf-8")
    recorded_fingerprint = workflow._path_git_state_fingerprint_get(  # noqa: SLF001
        coordinating_repository,
        "redirected/item",
    )
    transaction = workflow._main_leak_transaction_prepare(  # noqa: SLF001
        index_managed=False,
        index_target_entry_list=[],
        main_owner_root=coordinating_repository,
        path_text="redirected/item",
        recorded_fingerprint=recorded_fingerprint,
        target_commit=None,
        target_source_path=target_path,
        task_root=task_root,
        top_level_path_text="redirected/item",
    )
    saved_parent = coordinating_repository / "redirected-saved"
    leak_path.parent.replace(saved_parent)
    external_directory = tmp_path / "external-main"
    external_directory.mkdir()
    sentinel_path = external_directory / "sentinel.txt"
    sentinel_path.write_text("preserve\n", encoding="utf-8")
    leak_path.parent.symlink_to(external_directory, target_is_directory=True)

    with pytest.raises(WorktreeError, match="non-physical parent|escapes repository boundary"):
        workflow._main_leak_transaction_apply(task_root, transaction, [])  # noqa: SLF001

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"


def test_contracts_authored_rejects_a_premature_goal_without_mutating_lifecycle(tmp_path: Path) -> None:
    """A paired goal cannot appear before stable contract authoring completes."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    goal_path = coordinating_repository / ".spec" / f"{TASK_PREFIX}-goal.md"
    goal_path.write_text("# Premature goal\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="must not exist before stable contracts"):
        worktree_contracts_authored(coordinating_repository, specification)

    goal_path.unlink()
    result = json.loads(worktree_contracts_authored(coordinating_repository, specification))
    assert result["lifecycle_state"] == "contracts_authored"


def test_prepare_reconstructs_a_markerless_task_after_independent_main_advance(tmp_path: Path) -> None:
    """Markerless reconstruction retains its old baseline across independent main commits."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    baseline_commit = _git_run(task_root, ["rev-parse", "HEAD"])
    state_path_text = _git_run(
        task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
    )
    state_path = Path(state_path_text)
    if not state_path.is_absolute():
        state_path = task_root / state_path
    state_path.unlink()
    (coordinating_repository / "README.md").write_text("independent main\n", encoding="utf-8")
    _git_run(coordinating_repository, ["commit", "-am", "Advance independent main path"])

    result = json.loads(worktree_prepare(coordinating_repository, specification, []))

    assert result["task_root_list"] == [str(task_root)]
    assert _git_run(task_root, ["rev-parse", "HEAD"]) == baseline_commit
    assert _git_run(coordinating_repository, ["rev-parse", "HEAD"]) != baseline_commit
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_prepare_rejects_markerless_reconstruction_after_overlapping_main_advance(tmp_path: Path) -> None:
    """Accumulated main history still blocks an old markerless task on overlap."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    state_path_text = _git_run(
        task_root,
        ["rev-parse", "--git-path", "goal-brainstorm-worktree/state-v2.json"],
    )
    state_path = Path(state_path_text)
    if not state_path.is_absolute():
        state_path = task_root / state_path
    state_path.unlink()
    with (coordinating_repository / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("/independent-main-rule/\n")
    _git_run(coordinating_repository, ["commit", "-am", "Advance overlapping main path"])
    main_commit = _git_run(coordinating_repository, ["rev-parse", "HEAD"])

    with pytest.raises(WorktreeError, match="Accumulated main commit history overlaps"):
        worktree_prepare(coordinating_repository, specification, [])

    assert _git_run(coordinating_repository, ["rev-parse", "HEAD"]) == main_commit


def test_prepare_blocks_a_registered_task_path_replaced_by_an_ordinary_directory(tmp_path: Path) -> None:
    """Registration metadata alone must not authorize bootstrap writes to a replacement."""

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    other_task_root = _task_root_get(other_repository)
    saved_task_root = tmp_path / "saved-task-root"
    other_task_root.replace(saved_task_root)
    other_task_root.mkdir()

    with pytest.raises(WorktreeError, match="Registered task path is not safely repairable"):
        worktree_prepare(coordinating_repository, specification, [other_repository])

    assert list(other_task_root.iterdir()) == []
    assert (saved_task_root / "worktree-bootstrap.toml").is_file()
    assert (saved_task_root / ".spec").is_symlink()


def test_prepare_repairs_a_registered_secondary_git_pointer_only_from_durable_state(tmp_path: Path) -> None:
    """A redirected secondary `.git` file is repaired before project bootstrap writes."""

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    sentinel_repository = tmp_path / "sentinel"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    _repository_create(sentinel_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    other_task_root = _task_root_get(other_repository)
    manifest_fingerprint = (other_task_root / "worktree-bootstrap.toml").read_bytes()
    (other_task_root / ".git").write_text(f"gitdir: {sentinel_repository / '.git'}\n", encoding="utf-8")

    result = json.loads(worktree_prepare(coordinating_repository, specification, [other_repository]))

    assert _git_run(other_task_root, ["branch", "--show-current"]) == TASK_PREFIX
    assert (other_task_root / "worktree-bootstrap.toml").read_bytes() == manifest_fingerprint
    assert _git_run(sentinel_repository, ["status", "--short"]) == ""
    assert any("repaired registered task worktree identity" in item for item in result["performed_repair_list"])


@pytest.mark.parametrize("damage_kind", ["missing", "redirected"])
def test_validate_repairs_a_registered_secondary_git_pointer_only_from_durable_state(
    tmp_path: Path,
    damage_kind: str,
) -> None:
    """Validation repairs a missing or redirected secondary pointer from durable ownership."""

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    sentinel_repository = tmp_path / "sentinel"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    _repository_create(sentinel_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    other_task_root = _task_root_get(other_repository)
    git_pointer_path = other_task_root / ".git"
    if damage_kind == "missing":
        git_pointer_path.unlink()
    else:
        git_pointer_path.write_text(f"gitdir: {sentinel_repository / '.git'}\n", encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert _git_run(other_task_root, ["branch", "--show-current"]) == TASK_PREFIX
    assert _git_run(sentinel_repository, ["status", "--short"]) == ""
    assert any("repaired registered task worktree identity" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_repairs_effectively_negated_specification_ignore_rule(tmp_path: Path) -> None:
    """A later negation is repaired by appending the literal provider rule again."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    gitignore_path = task_root / ".gitignore"
    with gitignore_path.open("a", encoding="utf-8") as handle:
        handle.write("!/.spec\n")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert gitignore_path.read_text(encoding="utf-8").splitlines()[-1] == "/.spec"
    assert any("restored tracked ignore pattern /.spec" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


def test_validate_repairs_effectively_negated_resource_ignore_rule(tmp_path: Path) -> None:
    """Resource negations are repaired without deleting the project-owned negation."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    source_path = coordinating_repository / "local" / "cache"
    source_path.parent.mkdir()
    source_path.write_text("cache\n", encoding="utf-8")
    (coordinating_repository / "worktree-bootstrap.toml").write_text(
        "schema_version = 1\n\n"
        "[resource]\n"
        'copy_optional_path_list = ["local/cache"]\n'
        "copy_required_path_list = []\n"
        "link_optional_path_list = []\n"
        "link_required_path_list = []\n",
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add resource manifest"])
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    gitignore_path = task_root / ".gitignore"
    with gitignore_path.open("a", encoding="utf-8") as handle:
        handle.write("!/local/cache\n")

    worktree_validate(coordinating_repository, "repository_prepared", specification)

    gitignore_line_list = gitignore_path.read_text(encoding="utf-8").splitlines()
    assert "!/local/cache" in gitignore_line_list
    assert gitignore_line_list[-1] == "/local/cache"


def test_cli_help_uses_the_standard_startup_path() -> None:
    """The directly executable script must expose standard parser help."""

    result = subprocess.run(
        [str(SCRIPT_PATH), "--help"],
        capture_output=True,
        check=False,
        cwd=SCRIPT_PATH.parents[4],
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "{prepare,contracts-authored,recover-main-leak,accept-main-commit-drift,activate,validate,seal}"
        in result.stdout
    )
    assert result.stderr == ""
    assert not (LIBRARY_ROOT / "tool").exists()


def test_script_delegates_repository_behavior_to_the_direct_library_owner() -> None:
    """The executable boundary must stay thin and the library must stay reusable."""

    library_text = (LIBRARY_ROOT / "worktree.py").read_text(encoding="utf-8")
    script_text = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "import argparse" not in library_text
    assert 'if __name__ == "__main__"' not in library_text
    assert "import subprocess" not in script_text
    assert "from worktree import (" in script_text
    for public_function_name in (
        "worktree_activate",
        "worktree_contracts_authored",
        "worktree_main_commit_drift_accept",
        "worktree_main_leak_recover",
        "worktree_prepare",
        "worktree_seal",
        "worktree_validate",
    ):
        assert public_function_name in script_text


def test_tree_fingerprint_is_prefix_free_across_entry_boundaries(tmp_path: Path) -> None:
    """Distinct directory structures cannot share a serialization fingerprint."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "a").write_text("Xb420fileY", encoding="utf-8")
    (second_root / "a").write_text("X", encoding="utf-8")
    (second_root / "b").write_text("Y", encoding="utf-8")

    assert workflow._legacy_path_fingerprint_get(first_root) == workflow._legacy_path_fingerprint_get(  # noqa: SLF001
        second_root
    )
    assert workflow._path_fingerprint_get(first_root) != workflow._path_fingerprint_get(second_root)  # noqa: SLF001


def test_validate_migrates_an_old_mutable_copy_directory_fingerprint(tmp_path: Path) -> None:
    """Fingerprint migration proves the initial copy without rejecting task-only edits."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    source_root = coordinating_repository / "local" / "tree"
    source_root.mkdir(parents=True)
    (source_root / "value.txt").write_text("source\n", encoding="utf-8")
    (coordinating_repository / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/tree"]
link_optional_path_list = []
link_required_path_list = []
        """,
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add bootstrap manifest"])
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    state_path = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/state-v2.json",
    )
    source_preimage_path = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/resource-source-preimage-v1",
        )
        / hashlib.sha256(os.fsencode("local/tree")).hexdigest()
        / "source"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    resource_state = state["repository_state_list"][0]["resource_state_list"][0]
    legacy_fingerprint = workflow._legacy_path_fingerprint_get(source_preimage_path)  # noqa: SLF001
    state["fingerprint_schema_version"] = 1
    resource_state["source_fingerprint"] = legacy_fingerprint
    resource_state["destination_fingerprint"] = legacy_fingerprint
    state_path.write_text(json.dumps(state), encoding="utf-8")
    task_value_path = task_root / "local" / "tree" / "value.txt"
    task_value_path.write_text("task-only\n", encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert task_value_path.read_text(encoding="utf-8") == "task-only\n"
    assert any("upgraded collision-safe filesystem fingerprints" in item for item in result["performed_repair_list"])
    assert json.loads(state_path.read_text(encoding="utf-8"))["fingerprint_schema_version"] == 2


def test_validate_migrates_an_old_copy_directory_with_rewritten_absolute_link(
    tmp_path: Path,
) -> None:
    """Migration reconstructs the immutable initial copy even after task edits."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    source_root = coordinating_repository / "local" / "tree"
    source_root.mkdir(parents=True)
    source_value_path = source_root / "value.txt"
    source_value_path.write_text("source\n", encoding="utf-8")
    (source_root / "value-link").symlink_to(source_value_path)
    (coordinating_repository / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/tree"]
link_optional_path_list = []
link_required_path_list = []
        """,
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add bootstrap manifest"])
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    state_path = _private_git_path_get(task_root, "goal-brainstorm-worktree/state-v2.json")
    source_preimage_path = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/resource-source-preimage-v1",
        )
        / hashlib.sha256(os.fsencode("local/tree")).hexdigest()
        / "source"
    )
    task_destination_path = task_root / "local" / "tree"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    resource_state = state["repository_state_list"][0]["resource_state_list"][0]
    state["fingerprint_schema_version"] = 1
    resource_state["source_fingerprint"] = workflow._legacy_path_fingerprint_get(source_preimage_path)  # noqa: SLF001
    resource_state["destination_fingerprint"] = workflow._legacy_path_fingerprint_get(  # noqa: SLF001
        task_destination_path
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (task_destination_path / "value.txt").write_text("task-only\n", encoding="utf-8")

    worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (task_destination_path / "value.txt").read_text(encoding="utf-8") == "task-only\n"
    assert not Path(os.readlink(task_destination_path / "value-link")).is_absolute()


def test_validate_recovers_copy_fingerprint_migration_after_process_death(
    tmp_path: Path,
) -> None:
    """A random intent owns an interrupted reconstructed-copy migration stage."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    source_root = coordinating_repository / "local" / "tree"
    source_root.mkdir(parents=True)
    source_value_path = source_root / "value.txt"
    source_value_path.write_text("source\n", encoding="utf-8")
    (source_root / "value-link").symlink_to(source_value_path)
    (coordinating_repository / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/tree"]
link_optional_path_list = []
link_required_path_list = []
        """,
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add bootstrap manifest"])
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    state_path = _private_git_path_get(task_root, "goal-brainstorm-worktree/state-v2.json")
    source_preimage_path = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/resource-source-preimage-v1",
        )
        / hashlib.sha256(os.fsencode("local/tree")).hexdigest()
        / "source"
    )
    task_destination_path = task_root / "local" / "tree"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    resource_state = state["repository_state_list"][0]["resource_state_list"][0]
    state["fingerprint_schema_version"] = 1
    resource_state["source_fingerprint"] = workflow._legacy_path_fingerprint_get(source_preimage_path)  # noqa: SLF001
    resource_state["destination_fingerprint"] = workflow._legacy_path_fingerprint_get(  # noqa: SLF001
        task_destination_path
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"sys.path.insert(0, {str(LIBRARY_ROOT)!r})\n"
                "from pathlib import Path\n"
                "import worktree as worktree_module\n"
                "original = worktree_module.shutil.copytree\n"
                "def die(source_path, destination_path, *args, **kwargs):\n"
                "    result = original(source_path, destination_path, *args, **kwargs)\n"
                "    if 'copy-fingerprint-migration' in Path(destination_path).parts:\n"
                "        os._exit(80)\n"
                "    return result\n"
                "worktree_module.shutil.copytree = die\n"
                f"worktree_module.worktree_validate(Path({str(coordinating_repository)!r}), "
                f"'repository_prepared', Path({str(specification)!r}))\n"
            ),
        ],
        check=False,
    )

    assert process.returncode == 80
    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert any(
        "removed interrupted unpublished copy-fingerprint-migration staging" in item
        for item in result["performed_repair_list"]
    )
    assert json.loads(state_path.read_text(encoding="utf-8"))["fingerprint_schema_version"] == 2
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


@pytest.mark.parametrize("staging_kind", ["capture", "snapshot"])
def test_prepare_preserves_changed_resource_preimage_staging(
    tmp_path: Path,
    staging_kind: str,
) -> None:
    """A private-looking name cannot authorize deletion of changed clone content."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    source_path = coordinating_repository / "local" / "data"
    source_path.parent.mkdir()
    source_path.write_text("source\n", encoding="utf-8")
    (task_root / "worktree-bootstrap.toml").write_text(
        """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = ["local/data"]
link_optional_path_list = []
link_required_path_list = []
""",
        encoding="utf-8",
    )
    snapshot_name = hashlib.sha256(os.fsencode("local/data")).hexdigest()
    preimage_root = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/resource-source-preimage-v1",
    )
    staging_path = preimage_root / (f"{snapshot_name}.capture" if staging_kind == "capture" else snapshot_name)
    sentinel_path = staging_path / "source" / "user"
    sentinel_path.parent.mkdir(parents=True)
    sentinel_path.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="unknown content|metadata is unavailable"):
        worktree_prepare(coordinating_repository, specification, [])

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"


def test_prepare_preserves_changed_main_preimage_capture(tmp_path: Path) -> None:
    """A partial main-preimage capture is removed only when its object is exact."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    dirty_path = coordinating_repository / "dirty.txt"
    dirty_path.write_text("main work\n", encoding="utf-8")
    capture_path = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/main-preimage-v1",
        )
        / f"{hashlib.sha256(os.fsencode('dirty.txt')).hexdigest()}.capture"
    )
    sentinel_path = capture_path / "working" / "user"
    sentinel_path.parent.mkdir(parents=True)
    sentinel_path.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="changed working content"):
        worktree_prepare(coordinating_repository, specification, [])

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"


def test_validate_preserves_unknown_private_main_preimage_content(tmp_path: Path) -> None:
    """Unknown content below a provider owner is never retired by namespace alone."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    (coordinating_repository / "dirty.txt").write_text("main work\n", encoding="utf-8")
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    sentinel_path = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/main-preimage-v1",
        )
        / "user"
    )
    sentinel_path.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="contains unknown content"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"


def test_prepare_preserves_changed_main_preimage_index_blob(tmp_path: Path) -> None:
    """An expected private index filename does not own bytes with the wrong object ID."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (coordinating_repository / "README.md").write_text("staged\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "README.md"])
    index_metadata = _git_run(
        coordinating_repository,
        ["ls-files", "--stage", "--", "README.md"],
    ).split(
        "\t", 1
    )[0]
    mode_text, object_id, stage_text = index_metadata.split()
    capture_path = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/main-preimage-v1",
        )
        / f"{hashlib.sha256(os.fsencode('README.md')).hexdigest()}.capture"
    )
    sentinel_path = capture_path / "index" / f"{stage_text}-{mode_text}-{object_id}.blob"
    sentinel_path.parent.mkdir(parents=True)
    sentinel_path.write_bytes(b"wrong")

    with pytest.raises(WorktreeError, match="changed index staging content"):
        worktree_prepare(coordinating_repository, specification, [])

    assert sentinel_path.read_bytes() == b"wrong"


@pytest.mark.parametrize("legacy_content_kind", ["invalid-json", "boolean-schema"])
def test_validate_preserves_an_unproven_legacy_state_replica(
    tmp_path: Path,
    legacy_content_kind: str,
) -> None:
    """A v1 filename is retired only after complete same-task identity proof."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    state_v2_path = _private_git_path_get(task_root, "goal-brainstorm-worktree/state-v2.json")
    state_v1_path = _private_git_path_get(task_root, "goal-brainstorm-worktree/state-v1.json")
    if legacy_content_kind == "invalid-json":
        expected_content = b"preserve\n"
    else:
        payload = json.loads(state_v2_path.read_text(encoding="utf-8"))
        payload["schema_version"] = True
        del payload["fingerprint_schema_version"]
        for repository_state in payload["repository_state_list"]:
            del repository_state["main_leak_fingerprint_by_path_map"]
            del repository_state["participating_submodule_state_list"]
        expected_content = json.dumps(payload).encode()
    state_v1_path.write_bytes(expected_content)

    with pytest.raises(WorktreeError, match="Cannot load private worktree state|unsupported legacy schema"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert state_v1_path.read_bytes() == expected_content


def test_prepare_recovers_invalid_utf8_coordinating_state_from_secondary(
    tmp_path: Path,
) -> None:
    """Invalid UTF-8 is normalized and a valid agreeing secondary remains recoverable."""

    coordinating_repository = tmp_path / "coordinating"
    other_repository = tmp_path / "other"
    _repository_create(coordinating_repository)
    _repository_create(other_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [other_repository])
    coordinating_state_path = _private_git_path_get(
        _task_root_get(coordinating_repository),
        "goal-brainstorm-worktree/state-v2.json",
    )
    coordinating_state_path.write_bytes(b"\xff")

    result = json.loads(
        worktree_prepare(
            coordinating_repository,
            specification,
            [other_repository],
        )
    )

    assert any("recovered private state from secondary replica" in item for item in result["performed_repair_list"])
    assert json.loads(coordinating_state_path.read_text(encoding="utf-8"))["prefix"] == TASK_PREFIX


def test_prepare_rejects_a_hardlinked_specification_without_touching_its_peer(
    tmp_path: Path,
) -> None:
    """A task artifact cannot alias another main-worktree inode."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    specification_path = coordinating_repository / specification
    specification_path.unlink()
    os.link(coordinating_repository / "README.md", specification_path)

    with pytest.raises(WorktreeError, match="one physical filesystem link"):
        worktree_prepare(coordinating_repository, specification, [])

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "baseline\n"


def test_seal_rejects_a_hardlinked_goal_without_touching_its_peer(tmp_path: Path) -> None:
    """The physical goal must not alias tracked main-worktree content."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    worktree_contracts_authored(coordinating_repository, specification)
    goal = Path(".spec") / f"{TASK_PREFIX}-goal.md"
    os.link(coordinating_repository / "README.md", coordinating_repository / goal)

    with pytest.raises(WorktreeError, match="one physical filesystem link"):
        worktree_seal(coordinating_repository, goal, specification)

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "baseline\n"


def test_validate_rejects_a_hardlinked_bootstrap_manifest(tmp_path: Path) -> None:
    """A manifest cannot alias a different tracked task-worktree path."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    manifest_path = task_root / "worktree-bootstrap.toml"
    manifest_path.unlink()
    os.link(task_root / "README.md", manifest_path)

    with pytest.raises(WorktreeError, match="hardlinked regular files|one physical ordinary file"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert (task_root / "README.md").read_text(encoding="utf-8") == "baseline\n"


@pytest.mark.parametrize("object_kind", ["filename", "link-target"])
def test_prepare_preserves_non_utf8_git_filesystem_text(
    tmp_path: Path,
    object_kind: str,
) -> None:
    """Git paths and link targets round-trip through surrogateescape."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    repository_bytes = os.fsencode(coordinating_repository)
    if object_kind == "filename":
        raw_path = repository_bytes + b"/bad-\xff"
        descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT, 0o644)
        os.write(descriptor, b"preserve\n")
        os.close(descriptor)
    else:
        raw_path = repository_bytes + b"/bad-link"
        os.symlink(b"\xff", raw_path)

    worktree_prepare(coordinating_repository, specification, [])

    assert os.path.lexists(raw_path)
    assert _task_root_get(coordinating_repository).is_dir()


@pytest.mark.parametrize("transaction_kind", ["main-leak", "resource"])
def test_validate_preserves_unowned_metadata_less_transaction_content(
    tmp_path: Path,
    transaction_kind: str,
) -> None:
    """A hash-like private directory without recorded ownership is not deleted."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    owner_name = "main-leak-transaction-v1" if transaction_kind == "main-leak" else "resource-transaction-v1"
    transaction_path = (
        _private_git_path_get(
            task_root,
            f"goal-brainstorm-worktree/{owner_name}",
        )
        / hashlib.sha256(b"unknown").hexdigest()
    )
    sentinel_path = transaction_path / "replacement"
    sentinel_path.parent.mkdir(parents=True)
    sentinel_path.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="metadata is unavailable"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"


def test_validate_reconciles_a_complete_atomic_text_marker_staging_once(
    tmp_path: Path,
) -> None:
    """A complete pre-marker private temp is removable before project mutation."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    destination_path = task_root / ".gitignore"
    destination_identity = str(Path(os.path.abspath(destination_path)))
    transaction_name = hashlib.sha256(os.fsencode(destination_identity)).hexdigest()
    marker_path = (
        _private_git_path_get(
            task_root,
            "goal-brainstorm-worktree/atomic-text-write-v1",
        )
        / f"{transaction_name}.json.tmp"
    )
    payload = {
        "destination": destination_identity,
        "expected_fingerprint": workflow._regular_file_fingerprint_get(  # noqa: SLF001
            b"replacement\n",
            0o644,
        ),
        "mode": 0o644,
        "previous_fingerprint": workflow._path_fingerprint_get(destination_path),  # noqa: SLF001
        "schema_version": 1,
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(payload), encoding="utf-8")

    result = json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))

    assert not marker_path.exists()
    assert any("removed unexposed atomic text transaction staging" in item for item in result["performed_repair_list"])
    assert (
        json.loads(worktree_validate(coordinating_repository, "repository_prepared", specification))[
            "performed_repair_list"
        ]
        == []
    )


@pytest.mark.parametrize("object_kind", ["directory", "symlink"])
def test_validate_preserves_impossible_atomic_text_marker_staging(
    tmp_path: Path,
    object_kind: str,
) -> None:
    """A marker-temp name cannot authorize recursive or redirected deletion."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    transaction_root = _private_git_path_get(
        task_root,
        "goal-brainstorm-worktree/atomic-text-write-v1",
    )
    marker_path = transaction_root / f"{'a' * 64}.json.tmp"
    external_path = tmp_path / "external"
    if object_kind == "directory":
        sentinel_path = marker_path / "user"
        sentinel_path.parent.mkdir(parents=True)
    else:
        external_path.mkdir()
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.symlink_to(external_path, target_is_directory=True)
        sentinel_path = external_path / "user"
    sentinel_path.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="staging marker is damaged"):
        worktree_validate(coordinating_repository, "repository_prepared", specification)

    assert sentinel_path.read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.parametrize("strategy", ["copy", "link"])
def test_prepare_rejects_a_hardlinked_resource_source(
    tmp_path: Path,
    strategy: str,
) -> None:
    """Resource materialization cannot hide writes through a regular-file alias."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    source_path = coordinating_repository / "local" / "shared"
    source_path.parent.mkdir()
    os.link(coordinating_repository / "README.md", source_path)
    (coordinating_repository / "worktree-bootstrap.toml").write_text(
        f"""schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = {json.dumps(["local/shared"] if strategy == "copy" else [])}
link_optional_path_list = []
link_required_path_list = {json.dumps(["local/shared"] if strategy == "link" else [])}
""",
        encoding="utf-8",
    )
    _git_run(coordinating_repository, ["add", "worktree-bootstrap.toml"])
    _git_run(coordinating_repository, ["commit", "-m", "Add bootstrap manifest"])

    with pytest.raises(WorktreeError, match="does not support hardlinked regular files"):
        worktree_prepare(coordinating_repository, specification, [])

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "baseline\n"


def test_main_leak_recovery_rejects_a_hardlinked_working_object(tmp_path: Path) -> None:
    """Automatic restoration cannot leave a leaked patch through a tracked alias."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    alias_path = coordinating_repository / "ALIAS.md"
    alias_path.write_text("baseline\n", encoding="utf-8")
    _git_run(coordinating_repository, ["add", "ALIAS.md"])
    _git_run(coordinating_repository, ["commit", "-m", "Add alias candidate"])
    alias_path.unlink()
    os.link(coordinating_repository / "README.md", alias_path)
    assert _git_run(coordinating_repository, ["status", "--short"]) == ""
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    (task_root / "README.md").write_text("TASK\n", encoding="utf-8")
    (coordinating_repository / "README.md").write_text("TASK\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="does not support hardlinked regular files"):
        worktree_main_leak_recover(
            coordinating_repository,
            specification,
            coordinating_repository,
            [Path("README.md")],
        )

    assert (coordinating_repository / "README.md").read_text(encoding="utf-8") == "TASK\n"
    assert alias_path.read_text(encoding="utf-8") == "TASK\n"


@pytest.mark.parametrize("strategy", ["copy", "remove"])
def test_resource_transaction_migrates_old_directory_fingerprints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    strategy: str,
) -> None:
    """A durable pre-transition transaction remains exactly resumable."""

    coordinating_repository = tmp_path / "coordinating"
    _repository_create(coordinating_repository)
    specification = _specification_create(coordinating_repository)
    worktree_prepare(coordinating_repository, specification, [])
    task_root = _task_root_get(coordinating_repository)
    workflow = WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    )
    path_text = "runtime/tree"
    source_path = coordinating_repository / path_text
    source_path.mkdir(parents=True)
    (source_path / "value.txt").write_text("source\n", encoding="utf-8")
    destination_path = task_root / path_text
    if strategy == "remove":
        destination_path.mkdir(parents=True)
        (destination_path / "value.txt").write_text("previous\n", encoding="utf-8")
    else:
        workflow._resource_source_preimage_prepare(  # noqa: SLF001
            task_root,
            path_text,
            source_path,
            workflow._path_fingerprint_get(source_path),  # noqa: SLF001
            [],
        )

    def stop_before_exposure(
        self: WorktreeWorkflow,
        owner_root: Path,
        transaction: dict[str, object],
        performed_repair_list: list[str],
    ) -> None:
        raise RuntimeError("simulated process death")

    with monkeypatch.context() as interrupted_process:
        interrupted_process.setattr(
            WorktreeWorkflow,
            "_resource_transaction_expose",
            stop_before_exposure,
        )
        with pytest.raises(RuntimeError, match="simulated process death"):
            if strategy == "remove":
                workflow._resource_removal_transaction_create(  # noqa: SLF001
                    task_root,
                    path_text,
                    workflow._path_fingerprint_get(destination_path),  # noqa: SLF001
                    [],
                )
            else:
                workflow._resource_transaction_create(  # noqa: SLF001
                    task_root,
                    path_text,
                    source_path,
                    workflow._path_fingerprint_get(source_path),  # noqa: SLF001
                    "copy",
                    [],
                )
    transaction_path = workflow._resource_transaction_directory_get(  # noqa: SLF001
        task_root,
        path_text,
    )
    metadata_path = transaction_path / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    del payload["fingerprint_schema_version"]
    if payload["source_fingerprint"] != "absent":
        payload["source_fingerprint"] = workflow._legacy_path_fingerprint_get(  # noqa: SLF001
            _private_git_path_get(
                task_root,
                "goal-brainstorm-worktree/resource-source-preimage-v1",
            )
            / hashlib.sha256(os.fsencode(path_text)).hexdigest()
            / "source"
        )
    if payload["destination_fingerprint"] != "absent":
        payload["destination_fingerprint"] = workflow._legacy_path_fingerprint_get(  # noqa: SLF001
            transaction_path / "replacement"
        )
    if payload["previous_present"]:
        payload["previous_fingerprint"] = workflow._legacy_path_fingerprint_get(  # noqa: SLF001
            transaction_path / "previous"
        )
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")
    repair_list: list[str] = []

    transaction = workflow._resource_transaction_optional_get(  # noqa: SLF001
        task_root,
        path_text,
        repair_list,
    )

    assert transaction is not None
    assert transaction["fingerprint_schema_version"] == 2
    assert any("upgraded collision-safe resource transaction fingerprints" in item for item in repair_list)
