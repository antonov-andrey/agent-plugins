"""Behavior tests for atomic project-goals source authoring."""

from __future__ import annotations

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


def _repository_create(workspace: Path) -> tuple[Path, Path]:
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
    return root, remote


def _input_pair_create(workspace: Path, *, suffix: str = "one") -> tuple[Path, Path]:
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
    return goal, specification


def test_write_and_revision_publish_only_complete_pair(tmp_path: Path) -> None:
    """Initial authoring and revision publish one atomic pair each."""

    root, remote = _repository_create(tmp_path)
    workflow = GoalAuthoringWorkflow(root)
    goal_one, specification_one = _input_pair_create(tmp_path, suffix="one")

    first = workflow.write(
        common_prefix=PREFIX,
        goal_input=goal_one,
        specification_input=specification_one,
    )

    assert first.commit == _git(root, "rev-parse", "HEAD")
    assert first.commit == _git(remote, "rev-parse", "refs/heads/main")
    assert sorted(path.name for path in (root / PREFIX).iterdir()) == [
        "goal.md",
        "spec.md",
    ]
    assert (root / PREFIX / "goal.md").read_bytes() == goal_one.read_bytes()
    assert (root / PREFIX / "spec.md").read_bytes() == specification_one.read_bytes()

    goal_two, specification_two = _input_pair_create(tmp_path, suffix="two")
    second = workflow.write(
        common_prefix=PREFIX,
        goal_input=goal_two,
        specification_input=specification_two,
    )

    assert second.commit != first.commit
    changed_path_list = _git(root, "diff", "--name-only", first.commit, second.commit).splitlines()
    assert changed_path_list == [f"{PREFIX}/goal.md", f"{PREFIX}/spec.md"]
    assert workflow.validate(common_prefix=PREFIX) == second


def test_identical_write_is_idempotent(tmp_path: Path) -> None:
    """Retrying exact source bytes does not create another commit."""

    root, _remote = _repository_create(tmp_path)
    goal, specification = _input_pair_create(tmp_path)
    workflow = GoalAuthoringWorkflow(root)

    first = workflow.write(common_prefix=PREFIX, goal_input=goal, specification_input=specification)
    second = workflow.write(common_prefix=PREFIX, goal_input=goal, specification_input=specification)

    assert second == first
    assert _git(root, "rev-list", "--count", "HEAD") == "2"


def test_private_authoring_symlink_is_rejected_before_git_mutation(
    tmp_path: Path,
) -> None:
    """A predictable Git-admin path cannot redirect authoring recovery state outside the repository."""

    root, remote = _repository_create(tmp_path)
    goal, specification = _input_pair_create(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".git" / "agent-workflows").symlink_to(outside, target_is_directory=True)
    baseline = _git(root, "rev-parse", "HEAD")

    with pytest.raises(GoalAuthoringError, match="user-owned and physical"):
        GoalAuthoringWorkflow(root).write(
            common_prefix=PREFIX,
            goal_input=goal,
            specification_input=specification,
        )

    assert tuple(outside.iterdir()) == ()
    assert _git(root, "rev-parse", "HEAD") == baseline
    assert _git(remote, "rev-parse", "refs/heads/main") == baseline


def test_validate_rejects_incomplete_or_legacy_directory_shape(tmp_path: Path) -> None:
    """A current source is never accepted with a missing or third artifact."""

    root, _remote = _repository_create(tmp_path)
    directory = root / PREFIX
    directory.mkdir()
    (directory / "spec.md").write_text("# Spec\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Add incomplete source")
    _git(root, "push", "origin", "main")

    with pytest.raises(GoalAuthoringError, match="exactly goal.md and spec.md"):
        GoalAuthoringWorkflow(root).validate(common_prefix=PREFIX)

    (directory / "goal.md").write_text("# Goal\n", encoding="utf-8")
    (directory / "checkpoint.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Add legacy shape")
    _git(root, "push", "origin", "main")

    with pytest.raises(GoalAuthoringError, match="exactly goal.md and spec.md"):
        GoalAuthoringWorkflow(root).validate(common_prefix=PREFIX)


def test_successful_remote_push_is_recovered_after_local_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after push resumes without duplicating the publication commit.

    Args:
        tmp_path: Temporary directory path.
        monkeypatch: Pytest monkeypatch fixture.
    """

    root, remote = _repository_create(tmp_path)
    goal, specification = _input_pair_create(tmp_path)
    source = GoalSource(PREFIX, goal.read_bytes(), specification.read_bytes())
    transaction = GoalSourceTransaction(ProjectGoalsRepository(root))

    def interrupted_finish(commit: str, *, journal_path: Path) -> None:
        del commit, journal_path
        raise RuntimeError("simulated process loss")

    monkeypatch.setattr(transaction, "_finish_local", interrupted_finish)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        transaction.publish(source)
    remote_commit = _git(remote, "rev-parse", "refs/heads/main")
    assert _git(root, "rev-parse", "HEAD") != remote_commit

    recovered = GoalSourceTransaction(ProjectGoalsRepository(root)).publish(source)

    assert recovered == remote_commit
    assert _git(root, "rev-parse", "HEAD") == remote_commit
    assert _git(root, "rev-list", "--count", "HEAD") == "2"


def test_cli_emits_machine_readable_snapshot(tmp_path: Path) -> None:
    """The direct entrypoint runs and emits one closed JSON object."""

    root, _remote = _repository_create(tmp_path)
    goal, specification = _input_pair_create(tmp_path)
    script = PLUGIN_ROOT / "skills" / "goal-brainstorm" / "scripts" / "source.py"

    result = subprocess.run(
        [
            str(script),
            "write",
            "--goals-repository",
            str(root),
            "--common-prefix",
            PREFIX,
            "--goal-input",
            str(goal),
            "--specification-input",
            str(specification),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["common_prefix"] == PREFIX
    assert payload["commit"] == _git(root, "rev-parse", "HEAD")
    assert payload["goal_path"] == f"{PREFIX}/goal.md"
    assert payload["specification_path"] == f"{PREFIX}/spec.md"
