"""Behavior tests for atomic project-goals source authoring."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "agent-workflows"
LIBRARY_ROOT = PLUGIN_ROOT / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from goal_authoring import GoalAuthoringError, GoalAuthoringWorkflow
from goal_authoring.model import GoalSource
from goal_authoring.repository import ProjectGoalsRepository
from goal_authoring.transaction import GoalSourceTransaction

PREFIX = "2026-08-04-test-source"


@dataclass(frozen=True, slots=True)
class RepositoryFixture:
    """Contain one isolated project-goals checkout and its bare remote."""

    main_root: Path
    remote_root: Path


@dataclass(frozen=True, slots=True)
class SourceInputFixture:
    """Contain one complete goal and specification input pair."""

    goal_path: Path
    specification_path: Path


def _git(repository: Path, *argument_list: str) -> str:
    """Run one checked Git command in a test repository.

    Args:
        repository: Exact repository root.
        *argument_list: Direct Git arguments.

    Returns:
        Stripped standard output.
    """

    return subprocess.run(
        ["git", "-C", str(repository), *argument_list],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository_fixture_create(workspace: Path) -> RepositoryFixture:
    """Create one isolated canonical project-goals repository.

    Args:
        workspace: Temporary workspace.

    Returns:
        Main checkout and bare origin.
    """

    remote = workspace / "project-goals.git"
    root = workspace / "project-goals"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "clone", str(remote), str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "DESIGN.md").write_text("# Project Goals\n", encoding="utf-8")
    _git(root, "add", "DESIGN.md")
    _git(root, "commit", "-m", "Initialize project goals")
    _git(root, "push", "-u", "origin", "main")
    return RepositoryFixture(main_root=root, remote_root=remote)


def _source_input_fixture_create(workspace: Path, *, suffix: str = "one") -> SourceInputFixture:
    """Create one complete pair of source input files.

    Args:
        workspace: Temporary workspace.
        suffix: Distinguishing payload suffix.

    Returns:
        Goal and specification input paths.
    """

    goal = workspace / f"goal-{suffix}.md"
    specification = workspace / f"spec-{suffix}.md"
    goal.write_text(f"# Goal {suffix}\n", encoding="utf-8")
    specification.write_text(f"# Specification {suffix}\n", encoding="utf-8")
    return SourceInputFixture(goal_path=goal, specification_path=specification)


def test_write_and_revision_publish_only_complete_pair(tmp_path: Path) -> None:
    """Initial authoring and revision publish one atomic pair each."""

    repository = _repository_fixture_create(tmp_path)
    workflow = GoalAuthoringWorkflow(repository.main_root)
    source_one = _source_input_fixture_create(tmp_path, suffix="one")

    first = workflow.write(
        common_prefix=PREFIX,
        goal_input=source_one.goal_path,
        specification_input=source_one.specification_path,
    )

    assert first.commit == _git(repository.main_root, "rev-parse", "HEAD")
    assert first.commit == _git(repository.remote_root, "rev-parse", "refs/heads/main")
    assert sorted(path.name for path in (repository.main_root / PREFIX).iterdir()) == [
        "goal.md",
        "spec.md",
    ]
    assert (repository.main_root / PREFIX / "goal.md").read_bytes() == source_one.goal_path.read_bytes()
    assert (repository.main_root / PREFIX / "spec.md").read_bytes() == source_one.specification_path.read_bytes()

    source_two = _source_input_fixture_create(tmp_path, suffix="two")
    second = workflow.write(
        common_prefix=PREFIX,
        goal_input=source_two.goal_path,
        specification_input=source_two.specification_path,
    )

    assert second.commit != first.commit
    changed_path_list = _git(repository.main_root, "diff", "--name-only", first.commit, second.commit).splitlines()
    assert changed_path_list == [f"{PREFIX}/goal.md", f"{PREFIX}/spec.md"]
    assert workflow.validate(common_prefix=PREFIX) == second


def test_identical_write_is_idempotent(tmp_path: Path) -> None:
    """Retrying exact source bytes does not create another commit."""

    repository = _repository_fixture_create(tmp_path)
    source = _source_input_fixture_create(tmp_path)
    workflow = GoalAuthoringWorkflow(repository.main_root)

    first = workflow.write(
        common_prefix=PREFIX,
        goal_input=source.goal_path,
        specification_input=source.specification_path,
    )
    second = workflow.write(
        common_prefix=PREFIX,
        goal_input=source.goal_path,
        specification_input=source.specification_path,
    )

    assert second == first
    assert _git(repository.main_root, "rev-list", "--count", "HEAD") == "2"


def test_private_authoring_symlink_is_rejected_before_git_mutation(
    tmp_path: Path,
) -> None:
    """A predictable Git-admin path cannot redirect authoring recovery state outside the repository."""

    repository = _repository_fixture_create(tmp_path)
    source = _source_input_fixture_create(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository.main_root / ".git" / "agent-workflows").symlink_to(outside, target_is_directory=True)
    baseline = _git(repository.main_root, "rev-parse", "HEAD")

    with pytest.raises(GoalAuthoringError, match="user-owned and physical"):
        GoalAuthoringWorkflow(repository.main_root).write(
            common_prefix=PREFIX,
            goal_input=source.goal_path,
            specification_input=source.specification_path,
        )

    assert list(outside.iterdir()) == []
    assert _git(repository.main_root, "rev-parse", "HEAD") == baseline
    assert _git(repository.remote_root, "rev-parse", "refs/heads/main") == baseline


def test_validate_rejects_incomplete_or_legacy_directory_shape(tmp_path: Path) -> None:
    """A current source is never accepted with a missing or third artifact."""

    repository = _repository_fixture_create(tmp_path)
    directory = repository.main_root / PREFIX
    directory.mkdir()
    (directory / "spec.md").write_text("# Spec\n", encoding="utf-8")
    _git(repository.main_root, "add", ".")
    _git(repository.main_root, "commit", "-m", "Add incomplete source")
    _git(repository.main_root, "push", "origin", "main")

    with pytest.raises(GoalAuthoringError, match="exactly goal.md and spec.md"):
        GoalAuthoringWorkflow(repository.main_root).validate(common_prefix=PREFIX)

    (directory / "goal.md").write_text("# Goal\n", encoding="utf-8")
    (directory / "checkpoint.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    _git(repository.main_root, "add", ".")
    _git(repository.main_root, "commit", "-m", "Add legacy shape")
    _git(repository.main_root, "push", "origin", "main")

    with pytest.raises(GoalAuthoringError, match="exactly goal.md and spec.md"):
        GoalAuthoringWorkflow(repository.main_root).validate(common_prefix=PREFIX)


def test_successful_remote_push_is_recovered_after_local_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after push resumes without duplicating the publication commit.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest monkeypatch fixture.
    """

    repository = _repository_fixture_create(tmp_path)
    source_input = _source_input_fixture_create(tmp_path)
    source = GoalSource(PREFIX, source_input.goal_path.read_bytes(), source_input.specification_path.read_bytes())
    transaction = GoalSourceTransaction(ProjectGoalsRepository(repository.main_root))

    def interrupted_finish(commit: str, *, journal_path: Path) -> None:
        del commit, journal_path
        raise RuntimeError("simulated process loss")

    monkeypatch.setattr(transaction, "_finish_local", interrupted_finish)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        transaction.publish(source)
    remote_commit = _git(repository.remote_root, "rev-parse", "refs/heads/main")
    assert _git(repository.main_root, "rev-parse", "HEAD") != remote_commit

    recovered = GoalSourceTransaction(ProjectGoalsRepository(repository.main_root)).publish(source)

    assert recovered == remote_commit
    assert _git(repository.main_root, "rev-parse", "HEAD") == remote_commit
    assert _git(repository.main_root, "rev-list", "--count", "HEAD") == "2"


def test_cli_emits_machine_readable_snapshot(tmp_path: Path) -> None:
    """The direct entrypoint runs and emits one closed JSON object."""

    repository = _repository_fixture_create(tmp_path)
    source = _source_input_fixture_create(tmp_path)
    script = PLUGIN_ROOT / "skills" / "goal-brainstorm" / "scripts" / "source.py"

    result = subprocess.run(
        [
            str(script),
            "write",
            "--goals-repository",
            str(repository.main_root),
            "--common-prefix",
            PREFIX,
            "--goal-input",
            str(source.goal_path),
            "--specification-input",
            str(source.specification_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["common_prefix"] == PREFIX
    assert payload["commit"] == _git(repository.main_root, "rev-parse", "HEAD")
    assert payload["goal_path"] == f"{PREFIX}/goal.md"
    assert payload["specification_path"] == f"{PREFIX}/spec.md"
