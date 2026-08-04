"""Behavior tests for Git worktree recovery, ownership and cleanup."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools" / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

import task_workspace.transaction as transaction_module
import task_workspace.lock as lock_module
from task_cleanup import (
    CleanupAuthority,
    CleanupRequest,
    TaskCleanupError,
    TaskCleanupReconciler,
)
from task_cleanup.reconciliation import _pull_request_contract_require
from task_cleanup.resource import ResourceCleaner
from task_graph.model import ResourceDeclaration, ResourceLifetime
from task_workspace import (
    IssueAttemptLock,
    RepositoryRequest,
    TaskWorkspaceError,
    TaskWorkspaceTransaction,
    WorkspaceConfig,
    WorkspaceRequest,
)
from task_workspace.bootstrap import manifest_parse
from task_workspace.lock import IssueWorkspaceLock
from task_workspace.repository import WorkspaceRepository, origin_identity_get
from task_workspace.submodule import recursive_submodule_snapshot_get


def _git(repository: Path, *argument_list: str) -> str:
    """Run one checked Git command.

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


def _repository_create(workspace: Path, *, resources: bool = True) -> tuple[Path, Path, str]:
    """Create one canonical checkout with a current YAML bootstrap contract.

    Args:
        workspace: Explicit workspace root.
        resources: Whether untracked bootstrap resources are declared.

    Returns:
        Checkout, bare origin and initial commit.
    """

    remote = workspace / "example-origin.git"
    root = workspace / "example"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "clone", str(remote), str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("# Example\n", encoding="utf-8")
    manifest = """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
"""
    if resources:
        manifest = """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list:
    - local-config.json
  link_optional_path_list:
    - secret.txt
  link_required_path_list: []
"""
    (root / "worktree-bootstrap.yaml").write_text(manifest, encoding="utf-8")
    _git(root, "add", "README.md", "worktree-bootstrap.yaml")
    _git(root, "commit", "-m", "Initialize repository")
    _git(root, "push", "-u", "origin", "main")
    initial = _git(root, "rev-parse", "HEAD")
    if resources:
        (root / "local-config.json").write_text('{"mode":"test"}\n', encoding="utf-8")
        (root / "secret.txt").write_text("secret\n", encoding="utf-8")
    return root, remote, initial


def test_bootstrap_manifest_parser_is_self_contained_and_schema_bounded(tmp_path: Path) -> None:
    """The installable plugin parses only its owned YAML schema without PyYAML."""

    (tmp_path / "config:file").write_text("value\n", encoding="utf-8")
    plan = manifest_parse(
        b"""schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list:
    - "config:file"
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  command_argument_list:
    - python
    - manage.py
    - destroy
    - --git-worktree
    - '{common_prefix}'
""",
        main_root=tmp_path,
    )

    assert [item.relative_path for item in plan.resource_list] == ["config:file"]
    assert plan.cleanup_argument_list == ("python", "manage.py", "destroy", "--git-worktree", "{common_prefix}")


@pytest.mark.parametrize(
    "payload",
    [
        b"schema_version: 2\nschema_version: 2\n",
        b"schema_version: 2\nresource:\n  copy_optional_path_list: []\n  copy_optional_path_list: []\n",
        b"---\nschema_version: 2\n",
        b"schema_version: &version 2\nresource: *version\n",
        b"schema_version: 2\nresource: {copy_optional_path_list: []}\n",
        b"schema_version: 2\nresource:\n    copy_optional_path_list: []\n",
        b"schema_version: 2\nresource:\n  copy_optional_path_list:\n    - 'broken'quote'\n",
        b"schema_version: 2\nresource:\n  copy_optional_path_list:\n    - #comment\n",
    ],
)
def test_bootstrap_manifest_parser_rejects_yaml_outside_owned_subset(payload: bytes, tmp_path: Path) -> None:
    """Ambiguous general-YAML features cannot enter the bootstrap contract."""

    with pytest.raises(TaskWorkspaceError):
        manifest_parse(payload, main_root=tmp_path)


def _request(remote: Path, *, issue: str = "AND-101", baseline: str = "") -> WorkspaceRequest:
    """Return one exact issue workspace request.

    Args:
        remote: Bare origin path.
        issue: Linear issue identifier.
        baseline: Optional recorded attempt baseline.

    Returns:
        Typed workspace request.
    """

    return WorkspaceRequest(
        issue_identifier=issue,
        repository_list=(RepositoryRequest(str(remote), "main", baseline),),
    )


def _submodule_add(workspace: Path, root: Path) -> str:
    """Add one local submodule and return its exact commit."""

    remote = workspace / "provider-origin.git"
    source = workspace / "provider-source"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "clone", str(remote), str(source)], check=True, capture_output=True)
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test User")
    (source / "provider.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(source, "add", "provider.py")
    _git(source, "commit", "-m", "Initialize provider")
    _git(source, "push", "-u", "origin", "main")
    commit = _git(source, "rev-parse", "HEAD")
    _git(
        root,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(remote),
        "vendor/provider",
    )
    _git(root, "commit", "-am", "Add provider submodule")
    _git(root, "push", "origin", "main")
    return commit


def _canceled_cleanup_request(request: WorkspaceRequest, *, issue: str) -> CleanupRequest:
    """Return terminal cancellation authority for one exact workspace request.

    Args:
        request: Exact repository request.
        issue: Exact Linear issue identifier.

    Returns:
        Typed cleanup request.
    """

    return CleanupRequest(
        issue_identifier=issue,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=(),
        resource_list=(),
    )


def test_prepare_preserves_dirty_main_and_rework_adopts_existing_workspace(
    tmp_path: Path,
) -> None:
    """Preparation never mutates main work and retry never resets task work."""

    root, remote, baseline = _repository_create(tmp_path)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote)

    state = TaskWorkspaceTransaction(config).prepare(request)[0]

    task_root = Path(state.task_root)
    assert state.baseline_commit == baseline
    assert state.branch_name == "linear/and-101"
    assert (task_root / "local-config.json").read_text(encoding="utf-8") == '{"mode":"test"}\n'
    assert (task_root / "secret.txt").is_symlink()
    assert (root / "local-config.json").exists()
    (task_root / "unfinished.txt").write_text("preserve me\n", encoding="utf-8")

    retried = TaskWorkspaceTransaction(config).prepare(request)[0]

    assert retried == state
    assert (task_root / "unfinished.txt").read_text(encoding="utf-8") == "preserve me\n"


def test_prepare_initializes_recursive_submodules_and_retry_never_resets_them(
    tmp_path: Path,
) -> None:
    """A task starts with exact detached gitlinks and reports drift without destroying it."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    provider_commit = _submodule_add(tmp_path, root)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-122")

    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    task_root = Path(state.task_root)
    provider_root = task_root / "vendor" / "provider"

    assert recursive_submodule_snapshot_get(task_root) == (("vendor/provider", provider_commit),)
    assert _git(provider_root, "branch", "--show-current") == ""
    (provider_root / "provider.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(TaskWorkspaceError, match="uncommitted state"):
        TaskWorkspaceTransaction(config).prepare(request)

    assert (provider_root / "provider.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_direct_workspace_and_cleanup_models_require_immutable_typed_collections(
    tmp_path: Path,
) -> None:
    """Internal callers cannot bypass the strict external collection boundary."""

    _root, remote, _baseline = _repository_create(tmp_path, resources=False)
    repository = RepositoryRequest(str(remote), "main", "")
    with pytest.raises(TaskWorkspaceError, match="repository list"):
        WorkspaceRequest("AND-121", [repository])

    authority = CleanupAuthority(
        scope="terminal-issue",
        issue_status="Canceled",
        project_status="Canceled",
        final_acceptance_done=False,
        all_other_project_nodes_terminal=False,
        unresolved_remediation_blocker_count=0,
    )
    with pytest.raises(TaskCleanupError, match="repository list"):
        CleanupRequest(
            issue_identifier="AND-121",
            authority=authority,
            repository_list=[repository],
            pull_request_list=(),
            resource_list=(),
        )

    resource = ResourceDeclaration(
        key="issue-environment",
        lifetime=ResourceLifetime.ISSUE,
        owner_identity="AND-121:environment",
        repository_url=str(remote),
        cleanup_argument_list=["python", "manage.py", "destroy"],
        consumer_node_key_list=[],
    )
    with pytest.raises(TaskCleanupError, match="independently approved declaration fingerprint"):
        CleanupRequest(
            issue_identifier="AND-121",
            authority=authority,
            repository_list=(repository,),
            pull_request_list=(),
            resource_list=(resource,),
        )

    shared_resource = ResourceDeclaration(
        key="review-environment",
        lifetime=ResourceLifetime.ISSUE,
        owner_identity="AND-121:review-environment",
        repository_url=str(remote),
        cleanup_argument_list=["python", "manage.py", "destroy"],
        consumer_node_key_list=["review"],
    )
    with pytest.raises(TaskCleanupError, match="consumer terminal"):
        CleanupRequest(
            issue_identifier="AND-121",
            authority=authority,
            repository_list=(repository,),
            pull_request_list=(),
            resource_list=(shared_resource,),
            approved_resource_fingerprint_list=(shared_resource.fingerprint(),),
        )

    request = CleanupRequest(
        issue_identifier="AND-121",
        authority=authority,
        repository_list=(repository,),
        pull_request_list=(),
        resource_list=(shared_resource,),
        approved_resource_fingerprint_list=(shared_resource.fingerprint(),),
        terminal_consumer_node_key_list=("review",),
    )
    assert request.terminal_consumer_node_key_list == ("review",)


def test_interrupted_bootstrap_recovers_from_durable_planned_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after worktree creation resumes exact materialization without a new branch."""

    _root, remote, _baseline = _repository_create(tmp_path)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-102")
    original = transaction_module.resource_materialize
    call_count = 0

    def fail_once(*args: object, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated interruption")
        original(*args, **kwargs)

    monkeypatch.setattr(transaction_module, "resource_materialize", fail_once)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        TaskWorkspaceTransaction(config).prepare(request)

    state = TaskWorkspaceTransaction(config).prepare(request)[0]

    assert state.phase == "bootstrap-ready"
    assert all(item.phase == "ready" for item in state.resource_list)
    assert Path(state.task_root).exists()


def test_validate_is_read_only_when_owned_worktree_is_missing(tmp_path: Path) -> None:
    """Validation reports an absent owned worktree without recreating it."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-109")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    task_root = Path(state.task_root)
    _git(root, "worktree", "remove", "--force", str(task_root))

    with pytest.raises(TaskWorkspaceError, match="absent or unavailable"):
        TaskWorkspaceTransaction(config).validate(request)

    assert not task_root.exists()
    assert _git(root, "branch", "--list", state.branch_name) == state.branch_name


def test_unowned_collision_and_concurrent_issue_lock_fail_closed(
    tmp_path: Path,
) -> None:
    """Path bytes and a second process are not accepted as ownership proof."""

    root, remote, _baseline = _repository_create(tmp_path)
    config = WorkspaceConfig(tmp_path.resolve())
    collision = root / ".worktree" / "and-103"
    collision.mkdir(parents=True)

    with pytest.raises(TaskWorkspaceError, match="without private ownership"):
        TaskWorkspaceTransaction(config).prepare(_request(remote, issue="AND-103"))

    with IssueWorkspaceLock(config, "AND-104"):
        with pytest.raises(TaskWorkspaceError, match="Another local session"):
            with IssueWorkspaceLock(config, "AND-104"):
                pass


def test_attempt_guard_holds_issue_ownership_for_its_process_lifetime(
    tmp_path: Path,
) -> None:
    """A second agent attempt is rejected until the first guard process exits."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = LIBRARY_ROOT / "task_workspace" / "tool" / "attempt.py"
    environment = {
        **os.environ,
        "LINEAR_AGENT_WORKSPACE_ROOT": str(workspace.resolve()),
    }
    first = subprocess.Popen(
        [sys.executable, str(script), "hold", "--issue-identifier", "AND-104"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        assert first.stdout is not None
        assert '"status":"held"' in first.stdout.readline()
        second = subprocess.run(
            [sys.executable, str(script), "hold", "--issue-identifier", "AND-104"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=environment,
        )
        assert second.returncode == 2
        assert "Another local session" in second.stderr
        with IssueWorkspaceLock(WorkspaceConfig(workspace.resolve()), "AND-104"):
            pass
    finally:
        first.terminate()
        first.wait(timeout=5)

    with IssueAttemptLock(WorkspaceConfig(workspace.resolve()), "AND-104"):
        pass


def test_issue_lock_rejects_attacker_symlink_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A predictable lock path cannot redirect lock creation outside its private root."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    private_root = tmp_path / f"linear-agent-tools-{os.getuid()}"
    private_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(lock_module.tempfile, "gettempdir", lambda: str(tmp_path))

    with pytest.raises(TaskWorkspaceError, match="private user-owned physical directory"):
        with IssueWorkspaceLock(WorkspaceConfig(workspace.resolve()), "AND-113"):
            pass

    assert tuple(outside.iterdir()) == ()


def test_workspace_discovery_rejects_symlink_checkout_outside_explicit_root(
    tmp_path: Path,
) -> None:
    """An approved origin does not authorize following a workspace child symlink."""

    outside = tmp_path / "outside"
    outside.mkdir()
    root, remote, _baseline = _repository_create(outside, resources=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "example").symlink_to(root, target_is_directory=True)
    request = RepositoryRequest(str(remote), "main", "")

    with pytest.raises(TaskWorkspaceError, match="found 0"):
        WorkspaceRepository.discover(WorkspaceConfig(workspace.resolve()), request)


def test_worktree_container_symlink_is_rejected_before_git_mutation(
    tmp_path: Path,
) -> None:
    """A repository-local path name cannot redirect task worktrees outside the checkout."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".worktree").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TaskWorkspaceError, match="physical repository-local directory"):
        TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(_request(remote, issue="AND-111"))

    assert not (outside / "and-111").exists()
    assert _git(root, "branch", "--list", "linear/and-111") == ""


def test_bootstrap_manifest_comes_from_exact_baseline_not_dirty_main(
    tmp_path: Path,
) -> None:
    """Uncommitted main manifest edits cannot change an already bound attempt contract."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    (root / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list:
    - uncommitted-required.txt
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )

    state = TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(_request(remote, issue="AND-118"))[0]

    assert state.phase == "bootstrap-ready"
    assert state.resource_list == ()
    assert not (Path(state.task_root) / "uncommitted-required.txt").exists()
    assert _git(root, "status", "--short", "worktree-bootstrap.yaml") == "M worktree-bootstrap.yaml"


def test_legacy_bootstrap_requires_adoption_then_uses_only_canonical_yaml(
    tmp_path: Path,
) -> None:
    """Legacy TOML blocks Product dispatch; a committed YAML adoption removes the block."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    (root / "worktree-bootstrap.yaml").unlink()
    (root / "worktree-bootstrap.toml").write_text("schema_version = 1\n", encoding="utf-8")
    _git(root, "add", "worktree-bootstrap.yaml", "worktree-bootstrap.toml")
    _git(root, "commit", "-m", "Represent stale consumer contract")
    _git(root, "push", "origin", "main")
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-120")

    with pytest.raises(TaskWorkspaceError, match="legacy worktree-bootstrap.toml"):
        TaskWorkspaceTransaction(config).prepare(request)

    assert _git(root, "branch", "--list", "linear/and-120") == ""
    (root / "worktree-bootstrap.toml").unlink()
    (root / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )
    _git(root, "add", "worktree-bootstrap.toml", "worktree-bootstrap.yaml")
    _git(root, "commit", "-m", "Adopt canonical Linear workspace contract")
    _git(root, "push", "origin", "main")

    state = TaskWorkspaceTransaction(config).prepare(request)[0]

    assert state.phase == "bootstrap-ready"
    assert state.manifest_sha256
    assert _git(Path(state.task_root), "branch", "--show-current") == "linear/and-120"


def test_bootstrap_destination_parent_symlink_cannot_escape_task_worktree(
    tmp_path: Path,
) -> None:
    """A tracked symlink ancestor cannot redirect bootstrap writes outside the task root."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "config.json"
    source.write_text("source\n", encoding="utf-8")
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    (root / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list:
    - redirect/config.json
  link_optional_path_list: []
  link_required_path_list: []
""",
        encoding="utf-8",
    )
    _git(root, "add", "redirect", "worktree-bootstrap.yaml")
    _git(root, "commit", "-m", "Declare nested bootstrap resource")
    _git(root, "push", "origin", "main")

    with pytest.raises(TaskWorkspaceError, match="destination parent is not a physical directory"):
        TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(_request(remote, issue="AND-119"))

    assert source.read_text(encoding="utf-8") == "source\n"


def test_origin_identity_preserves_security_relevant_url_components() -> None:
    """Ports and SSH users cannot collapse distinct repository origins."""

    assert origin_identity_get("git@github.com:owner/example.git") == "ssh://github.com/owner/example"
    assert origin_identity_get("ssh://git@github.com/owner/example.git") == "ssh://github.com/owner/example"
    assert origin_identity_get("ssh://git@github.com:2222/owner/example.git") == ("ssh://github.com:2222/owner/example")
    assert origin_identity_get("ssh://deploy@github.com/owner/example.git") == ("ssh://deploy@github.com/owner/example")
    with pytest.raises(TaskWorkspaceError, match="credentials"):
        origin_identity_get("https://token@github.com/owner/example.git")
    with pytest.raises(TaskWorkspaceError, match="suffixes"):
        origin_identity_get("https://github.com/owner/example.git?ref=main")


def test_discovery_ignores_unrelated_checkout_with_noncanonical_origin(
    tmp_path: Path,
) -> None:
    """An unrelated malformed sibling does not block the exact approved checkout."""

    _root, remote, baseline = _repository_create(tmp_path, resources=False)
    unrelated = tmp_path / "unrelated"
    _git(tmp_path, "init", "--initial-branch=main", str(unrelated))
    _git(unrelated, "remote", "add", "origin", "relative-repository")

    state = TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(
        _request(remote, issue="AND-124", baseline=baseline)
    )[0]

    assert state.issue_identifier == "AND-124"
    assert Path(state.task_root).is_dir()


def test_fresh_host_adopts_remote_rework_only_with_recorded_baseline(
    tmp_path: Path,
) -> None:
    """Remote branch adoption cannot silently replace the first-attempt baseline."""

    first_workspace = tmp_path / "first"
    first_workspace.mkdir()
    first_root, remote, baseline = _repository_create(first_workspace, resources=False)
    first_config = WorkspaceConfig(first_workspace.resolve())
    request = _request(remote, issue="AND-105")
    state = TaskWorkspaceTransaction(first_config).prepare(request)[0]
    task_root = Path(state.task_root)
    (task_root / "change.txt").write_text("candidate\n", encoding="utf-8")
    _git(task_root, "add", "change.txt")
    _git(task_root, "commit", "-m", "Prepare candidate")
    _git(task_root, "push", "-u", "origin", "linear/and-105")

    fresh_workspace = tmp_path / "fresh"
    fresh_workspace.mkdir()
    fresh_root = fresh_workspace / "example"
    subprocess.run(["git", "clone", str(remote), str(fresh_root)], check=True, capture_output=True)
    _git(fresh_root, "config", "user.email", "test@example.com")
    _git(fresh_root, "config", "user.name", "Test User")
    fresh_config = WorkspaceConfig(fresh_workspace.resolve())

    with pytest.raises(TaskWorkspaceError, match="requires its recorded Linear baseline"):
        TaskWorkspaceTransaction(fresh_config).prepare(_request(remote, issue="AND-105"))

    adopted = TaskWorkspaceTransaction(fresh_config).prepare(_request(remote, issue="AND-105", baseline=baseline))[0]
    assert (Path(adopted.task_root) / "change.txt").read_text(encoding="utf-8") == "candidate\n"


def test_unowned_local_branch_is_not_adopted_without_private_state(
    tmp_path: Path,
) -> None:
    """A matching local branch name alone never proves that Linear owns its commits."""

    root, remote, baseline = _repository_create(tmp_path, resources=False)
    _git(root, "branch", "linear/and-112", baseline)

    with pytest.raises(TaskWorkspaceError, match="without private ownership state"):
        TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(
            _request(remote, issue="AND-112", baseline=baseline)
        )

    state_path = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert not (state_path / "linear-agent-tools" / "task" / "and-112" / "workspace.json").exists()


def test_unowned_local_branch_is_rejected_even_when_exact_remote_exists(
    tmp_path: Path,
) -> None:
    """A matching remote branch cannot transfer ownership to an unrecorded local branch."""

    root, remote, baseline = _repository_create(tmp_path, resources=False)
    _git(root, "branch", "linear/and-114", baseline)
    _git(root, "push", "origin", "linear/and-114")

    with pytest.raises(TaskWorkspaceError, match="without private ownership state"):
        TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(
            _request(remote, issue="AND-114", baseline=baseline)
        )

    assert _git(root, "rev-parse", "linear/and-114") == baseline


def test_private_state_parent_symlink_is_rejected_before_git_mutation(
    tmp_path: Path,
) -> None:
    """A Git-admin symlink cannot redirect private ownership state outside the repository."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    outside = tmp_path / "outside-state"
    outside.mkdir()
    (root / ".git" / "linear-agent-tools").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TaskWorkspaceError, match="user-owned physical directory"):
        TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(_request(remote, issue="AND-115"))

    assert tuple(outside.iterdir()) == ()
    assert _git(root, "branch", "--list", "linear/and-115") == ""


def test_terminal_cleanup_removes_exact_workspace_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """Done cleanup removes branch/state while complete absence is subsequent success."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-106")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    cleanup_request = CleanupRequest(
        issue_identifier="AND-106",
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=(),
        resource_list=(),
    )

    first = TaskCleanupReconciler(config).cleanup(cleanup_request)
    second = TaskCleanupReconciler(config).cleanup(cleanup_request)

    assert first.removed_worktree_count == 1
    assert first.removed_local_branch_count == 1
    assert second.removed_worktree_count == 0
    assert not Path(state.task_root).exists()
    assert _git(root, "branch", "--list", "linear/and-106") == ""


def test_done_cleanup_rejects_unintegrated_branch_commits(tmp_path: Path) -> None:
    """Successful authority cannot discard commits that are absent from the remote base."""

    _root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-113")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    task_root = Path(state.task_root)
    (task_root / "candidate.txt").write_text("not merged\n", encoding="utf-8")
    _git(task_root, "add", "candidate.txt")
    _git(task_root, "commit", "-m", "Prepare unmerged candidate")
    cleanup_request = CleanupRequest(
        issue_identifier="AND-113",
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=(),
        resource_list=(),
    )

    with pytest.raises(TaskCleanupError, match="absent from its remote base"):
        TaskCleanupReconciler(config).cleanup(cleanup_request)

    assert task_root.exists()
    assert _git(task_root, "rev-parse", "HEAD") != _git(task_root, "rev-parse", "origin/main")


def test_canceled_cleanup_may_remove_dirty_exact_task_state(tmp_path: Path) -> None:
    """Explicit Canceled authority removes unmerged issue-owned work without touching main."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-107")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    (Path(state.task_root) / "uncommitted.txt").write_text("discard by cancellation\n", encoding="utf-8")
    cleanup_request = CleanupRequest(
        issue_identifier="AND-107",
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=(),
        resource_list=(),
    )

    result = TaskCleanupReconciler(config).cleanup(cleanup_request)

    assert result.removed_worktree_count == 1
    assert (root / "README.md").exists()


def test_cleanup_recovers_after_worktree_removal_before_state_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after Git removes the worktree resumes from its durable removal proof."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-115")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    cleanup_request = _canceled_cleanup_request(request, issue="AND-115")
    original_state_write = WorkspaceRepository.state_write
    interrupted = False

    def interrupt_after_removal(self: WorkspaceRepository, candidate: object) -> None:
        nonlocal interrupted
        if getattr(candidate, "worktree_removed", False) and not interrupted:
            interrupted = True
            raise RuntimeError("simulated post-worktree-removal interruption")
        original_state_write(self, candidate)

    monkeypatch.setattr(WorkspaceRepository, "state_write", interrupt_after_removal)

    with pytest.raises(RuntimeError, match="post-worktree-removal"):
        TaskCleanupReconciler(config).cleanup(cleanup_request)

    assert not Path(state.task_root).exists()
    recovered = TaskCleanupReconciler(config).cleanup(cleanup_request)

    assert recovered.removed_worktree_count == 0
    assert recovered.removed_local_branch_count == 1
    assert _git(root, "branch", "--list", state.branch_name) == ""


@pytest.mark.parametrize("interruption_field", ["remote_branch_removed", "local_branch_removed"])
def test_cleanup_recovers_after_branch_removal_before_state_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_field: str,
) -> None:
    """Exact branch deletion remains retryable across either state-write crash window."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-116")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    _git(Path(state.task_root), "push", "-u", "origin", state.branch_name)
    cleanup_request = _canceled_cleanup_request(request, issue="AND-116")
    original_state_write = WorkspaceRepository.state_write
    interrupted = False

    def interrupt_after_branch_removal(self: WorkspaceRepository, candidate: object) -> None:
        nonlocal interrupted
        if getattr(candidate, interruption_field, False) and not interrupted:
            interrupted = True
            raise RuntimeError(f"simulated post-{interruption_field} interruption")
        original_state_write(self, candidate)

    monkeypatch.setattr(WorkspaceRepository, "state_write", interrupt_after_branch_removal)

    with pytest.raises(RuntimeError, match=interruption_field):
        TaskCleanupReconciler(config).cleanup(cleanup_request)

    recovered = TaskCleanupReconciler(config).cleanup(cleanup_request)

    assert recovered.removed_worktree_count == 0
    assert not Path(state.task_root).exists()
    assert _git(root, "branch", "--list", state.branch_name) == ""
    assert _git(root, "ls-remote", "--heads", "origin", state.branch_name) == ""


def test_cleanup_rejects_remote_branch_mutation_after_durable_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-name remote branch cannot change after cleanup records its exact head."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root, remote, _baseline = _repository_create(workspace, resources=False)
    config = WorkspaceConfig(workspace.resolve())
    request = _request(remote, issue="AND-117")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    task_root = Path(state.task_root)
    _git(task_root, "push", "-u", "origin", state.branch_name)
    cleanup_request = _canceled_cleanup_request(request, issue="AND-117")
    original_state_write = WorkspaceRepository.state_write
    interrupted = False

    def interrupt_after_removal_proof(self: WorkspaceRepository, candidate: object) -> None:
        nonlocal interrupted
        if getattr(candidate, "cleanup_worktree_removal_ready", False) and not interrupted:
            interrupted = True
            raise RuntimeError("simulated pre-removal interruption")
        original_state_write(self, candidate)

    monkeypatch.setattr(WorkspaceRepository, "state_write", interrupt_after_removal_proof)
    with pytest.raises(RuntimeError, match="pre-removal"):
        TaskCleanupReconciler(config).cleanup(cleanup_request)

    competing_root = tmp_path / "competing"
    subprocess.run(
        ["git", "clone", str(remote), str(competing_root)],
        check=True,
        capture_output=True,
    )
    _git(competing_root, "config", "user.email", "test@example.com")
    _git(competing_root, "config", "user.name", "Test User")
    _git(competing_root, "checkout", state.branch_name)
    (competing_root / "foreign.txt").write_text("different head\n", encoding="utf-8")
    _git(competing_root, "add", "foreign.txt")
    _git(competing_root, "commit", "-m", "Move remote task branch")
    _git(competing_root, "push", "origin", state.branch_name)

    with pytest.raises(TaskCleanupError, match="Remote task branch changed"):
        TaskCleanupReconciler(config).cleanup(cleanup_request)

    assert task_root.exists()
    assert _git(root, "show-ref", "--verify", f"refs/heads/{state.branch_name}")


def test_cleanup_rejects_changed_resource_declaration_after_durable_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-key cleanup declaration cannot drift across crash recovery."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-120")
    TaskWorkspaceTransaction(config).prepare(request)
    captured: list[tuple[str, ...]] = []

    def runner(argument_list: tuple[str, ...], **_kwargs: object) -> object:
        captured.append(argument_list)
        return subprocess.CompletedProcess(argument_list, 0, b"", b"")

    resource = ResourceDeclaration(
        key="issue-environment",
        lifetime=ResourceLifetime.ISSUE,
        owner_identity="AND-120:environment",
        repository_url=str(remote),
        cleanup_argument_list=["python", "manage.py", "destroy", "--issue", "AND-120"],
        consumer_node_key_list=[],
    )
    cleanup_request = CleanupRequest(
        issue_identifier="AND-120",
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=(),
        resource_list=(resource,),
        approved_resource_fingerprint_list=(resource.fingerprint(),),
    )
    original_state_write = WorkspaceRepository.state_write
    interrupted = False

    def interrupt_after_resource_receipt(self: WorkspaceRepository, candidate: object) -> None:
        nonlocal interrupted
        original_state_write(self, candidate)
        if getattr(candidate, "cleaned_resource_fingerprint_by_key", ()) and not interrupted:
            interrupted = True
            raise RuntimeError("simulated post-resource interruption")

    monkeypatch.setattr(WorkspaceRepository, "state_write", interrupt_after_resource_receipt)
    with pytest.raises(RuntimeError, match="post-resource"):
        TaskCleanupReconciler(config, resources=ResourceCleaner(runner)).cleanup(cleanup_request)

    changed = ResourceDeclaration(
        key=resource.key,
        lifetime=resource.lifetime,
        owner_identity="AND-120:another-environment",
        repository_url=resource.repository_url,
        cleanup_argument_list=["python", "manage.py", "destroy", "--issue", "another"],
        consumer_node_key_list=[],
    )
    with pytest.raises(TaskCleanupError, match="declaration changed"):
        TaskCleanupReconciler(config, resources=ResourceCleaner(runner)).cleanup(
            CleanupRequest(
                issue_identifier=cleanup_request.issue_identifier,
                authority=cleanup_request.authority,
                repository_list=cleanup_request.repository_list,
                pull_request_list=(),
                resource_list=(changed,),
                approved_resource_fingerprint_list=(changed.fingerprint(),),
            )
        )

    assert captured == [("python", "manage.py", "destroy", "--issue", "AND-120")]
    assert (root / ".worktree" / "and-120").exists()


def test_cleanup_never_executes_project_command_through_replaced_worktree_symlink(
    tmp_path: Path,
) -> None:
    """Private state alone cannot redirect project-owned cleanup execution outside its Git worktree."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-121")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    task_root = Path(state.task_root)
    _git(root, "worktree", "remove", "--force", str(task_root))
    outside = tmp_path / "outside-cleanup"
    outside.mkdir()
    task_root.symlink_to(outside, target_is_directory=True)
    called = False

    def runner(argument_list: tuple[str, ...], **_kwargs: object) -> object:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(argument_list, 0, b"", b"")

    resource = ResourceDeclaration(
        key="issue-environment",
        lifetime=ResourceLifetime.ISSUE,
        owner_identity="AND-121:environment",
        repository_url=str(remote),
        cleanup_argument_list=["python", "manage.py", "destroy"],
        consumer_node_key_list=[],
    )
    cleanup_request = CleanupRequest(
        issue_identifier="AND-121",
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=(),
        resource_list=(resource,),
        approved_resource_fingerprint_list=(resource.fingerprint(),),
    )

    with pytest.raises(TaskCleanupError, match="unavailable before project-owned cleanup"):
        TaskCleanupReconciler(config, resources=ResourceCleaner(runner)).cleanup(cleanup_request)

    assert not called
    assert tuple(outside.iterdir()) == ()


def test_cleanup_requires_complete_exact_pull_request_set() -> None:
    """An omitted exact task PR cannot be hidden by an empty cleanup request list."""

    class Repository:
        origin_identity = "https://github.com/antonov-andrey/example"
        request = type("Request", (), {"base_branch": "main"})()

    class GitHub:
        @staticmethod
        def matching_number_list(**_kwargs: object) -> tuple[int, ...]:
            return (17,)

    request = CleanupRequest(
        issue_identifier="AND-121",
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=(RepositoryRequest("https://github.com/antonov-andrey/example.git", "main", ""),),
        pull_request_list=(),
        resource_list=(),
    )

    with pytest.raises(TaskCleanupError, match="omits or substitutes"):
        _pull_request_contract_require(request, (Repository(),), github=GitHub())


def test_project_final_cleanup_requires_acceptance_other_terminal_nodes_and_no_remediation(
    tmp_path: Path,
) -> None:
    """The one cleanup node closes an active Project only after every downstream gate."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-110")
    TaskWorkspaceTransaction(config).prepare(request)
    captured: list[tuple[tuple[str, ...], Path]] = []

    def runner(argument_list: tuple[str, ...], *, cwd: Path, check: bool, capture_output: bool) -> object:
        assert not check
        assert capture_output
        captured.append((argument_list, cwd))
        return subprocess.CompletedProcess(argument_list, 0, b"", b"")

    resource = ResourceDeclaration(
        key="project-environment",
        lifetime=ResourceLifetime.PROJECT,
        owner_identity="AND-project:environment",
        repository_url=str(remote),
        cleanup_argument_list=[
            "python",
            "manage.py",
            "destroy",
            "--project",
            "AND-project",
            "--task-branch",
            "{task_branch}",
            "--task-root",
            "{task_root}",
        ],
        consumer_node_key_list=[],
    )
    authority = CleanupAuthority(
        scope="project-final",
        issue_status="In Progress",
        project_status="In Progress",
        final_acceptance_done=True,
        all_other_project_nodes_terminal=True,
        unresolved_remediation_blocker_count=0,
    )
    cleanup_request = CleanupRequest(
        issue_identifier="AND-110",
        authority=authority,
        repository_list=request.repository_list,
        pull_request_list=(),
        resource_list=(resource,),
        approved_resource_fingerprint_list=(resource.fingerprint(),),
        project_issue_identifier_list=("AND-110",),
    )

    first = TaskCleanupReconciler(config, resources=ResourceCleaner(runner)).cleanup(cleanup_request)
    second = TaskCleanupReconciler(config, resources=ResourceCleaner(runner)).cleanup(cleanup_request)

    assert first.cleaned_resource_count == 1
    assert second.cleaned_resource_count == 1
    assert captured == [
        (
            (
                "python",
                "manage.py",
                "destroy",
                "--project",
                "AND-project",
                "--task-branch",
                "linear/and-110",
                "--task-root",
                str(root / ".worktree" / "and-110"),
            ),
            root / ".worktree" / "and-110",
        ),
        (
            (
                "python",
                "manage.py",
                "destroy",
                "--project",
                "AND-project",
                "--task-branch",
                "linear/and-110",
                "--task-root",
                str(root / ".worktree" / "and-110"),
            ),
            root,
        ),
    ]

    with pytest.raises(TaskCleanupError, match="prerequisites"):
        CleanupAuthority(
            scope="project-final",
            issue_status="In Progress",
            project_status="In Progress",
            final_acceptance_done=True,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        )


def test_canceled_project_cleanup_uses_terminal_issue_without_reactivation() -> None:
    """Canceled Project cleanup is authorized by its terminal cleanup issue state."""

    authority = CleanupAuthority(
        scope="project-final",
        issue_status="Canceled",
        project_status="Canceled",
        final_acceptance_done=False,
        all_other_project_nodes_terminal=True,
        unresolved_remediation_blocker_count=0,
    )

    assert authority.project_status == "Canceled"
    with pytest.raises(TaskCleanupError, match="terminal Canceled cleanup issue"):
        CleanupAuthority(
            scope="project-final",
            issue_status="In Progress",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=True,
            unresolved_remediation_blocker_count=0,
        )


def test_project_final_cleanup_proves_all_project_issue_workspaces_absent(
    tmp_path: Path,
) -> None:
    """A terminal status summary cannot hide another issue's owned local state."""

    _root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    other_request = _request(remote, issue="AND-120")
    cleanup_node_request = _request(remote, issue="AND-121")
    TaskWorkspaceTransaction(config).prepare(other_request)
    TaskWorkspaceTransaction(config).prepare(cleanup_node_request)
    project_cleanup = CleanupRequest(
        issue_identifier="AND-121",
        authority=CleanupAuthority(
            scope="project-final",
            issue_status="In Progress",
            project_status="In Progress",
            final_acceptance_done=True,
            all_other_project_nodes_terminal=True,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=cleanup_node_request.repository_list,
        pull_request_list=(),
        resource_list=(),
        project_issue_identifier_list=("AND-120", "AND-121"),
    )

    with pytest.raises(TaskCleanupError, match="AND-120.*private task-workspace state"):
        TaskCleanupReconciler(config).cleanup(project_cleanup)

    TaskCleanupReconciler(config).cleanup(_canceled_cleanup_request(other_request, issue="AND-120"))
    result = TaskCleanupReconciler(config).cleanup(project_cleanup)

    assert result.removed_worktree_count == 0


def test_attempt_cleanup_repeats_idempotent_resource_without_removing_workspace(
    tmp_path: Path,
) -> None:
    """Attempt cleanup owns transient resources but preserves issue-lifetime Git state."""

    root, remote, _baseline = _repository_create(tmp_path, resources=False)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-114")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    captured: list[tuple[tuple[str, ...], Path]] = []

    def runner(argument_list: tuple[str, ...], *, cwd: Path, check: bool, capture_output: bool) -> object:
        assert not check
        assert capture_output
        captured.append((argument_list, cwd))
        return subprocess.CompletedProcess(argument_list, 0, b"", b"")

    resource = ResourceDeclaration(
        key="attempt-environment",
        lifetime=ResourceLifetime.ATTEMPT,
        owner_identity="AND-114:attempt-one",
        repository_url=str(remote),
        cleanup_argument_list=[
            "python",
            "manage.py",
            "destroy",
            "--attempt",
            "attempt-one",
        ],
        consumer_node_key_list=[],
    )
    cleanup_request = CleanupRequest(
        issue_identifier="AND-114",
        authority=CleanupAuthority(
            scope="attempt",
            issue_status="In Progress",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=(),
        resource_list=(resource,),
        approved_resource_fingerprint_list=(resource.fingerprint(),),
    )

    first = TaskCleanupReconciler(config, resources=ResourceCleaner(runner)).cleanup(cleanup_request)
    second = TaskCleanupReconciler(config, resources=ResourceCleaner(runner)).cleanup(cleanup_request)

    assert first.cleaned_resource_count == second.cleaned_resource_count == 1
    assert len(captured) == 2
    assert Path(state.task_root).exists()
    assert _git(root, "show-ref", "--verify", f"refs/heads/{state.branch_name}")


def test_resource_cleaner_executes_direct_argv_without_shell() -> None:
    """Resource cleanup expands only known placeholders into direct arguments."""

    captured: list[tuple[tuple[str, ...], Path]] = []

    def runner(argument_list: tuple[str, ...], *, cwd: Path, check: bool, capture_output: bool) -> object:
        assert not check
        assert capture_output
        captured.append((argument_list, cwd))
        return subprocess.CompletedProcess(argument_list, 0, b"", b"")

    resource = ResourceDeclaration(
        key="environment",
        lifetime=ResourceLifetime.ISSUE,
        owner_identity="AND-108:environment",
        repository_url="git@github.com:antonov-andrey/example.git",
        cleanup_argument_list=[
            "python",
            "manage.py",
            "destroy",
            "--task",
            "{linear_issue_identifier}",
        ],
        consumer_node_key_list=[],
    )
    ResourceCleaner(runner).cleanup(
        resource,
        working_directory=Path("/tmp/task"),
        placeholder_by_name={"linear_issue_identifier": "AND-108"},
    )

    assert captured == [(("python", "manage.py", "destroy", "--task", "AND-108"), Path("/tmp/task"))]
