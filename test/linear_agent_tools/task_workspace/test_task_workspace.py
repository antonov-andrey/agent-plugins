"""Behavior tests for Git worktree recovery, ownership and cleanup."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools" / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

import task_workspace.lock as lock_module
from git_host.model import PullRequestSnapshot, RepositoryIdentity
from git_host.pull_request import GitHubPullRequestBoundary
from task_cleanup.contract import (
    AcceptanceBaseBranchCleanupResource,
    CleanupResourceContractError,
    WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
    cleanup_resource_from_payload,
)
from task_cleanup.model import (
    CleanupAuthority,
    CleanupRequest,
    PullRequestReference,
    TaskCleanupError,
)
from task_cleanup.reconciliation import CleanupState, TaskCleanupReconciler
from task_cleanup.resource import CleanupResourceRegistry
from task_workspace.lock import IssueAttemptLock, IssueWorkspaceLock
from task_workspace.model import (
    RepositoryRequest,
    RepositoryWorkspaceState,
    TaskWorkspaceError,
    WorkspaceConfig,
    WorkspaceRequest,
)
from task_workspace.bootstrap import BootstrapPlan, BootstrapResource
from task_workspace.repository import WorkspaceRepository
from task_workspace.submodule import WorkspaceSubmoduleReader
from task_workspace.transaction import TaskWorkspaceTransaction

PROJECT_ID = "70000000-0000-4000-8000-000000000001"


@dataclass(frozen=True, slots=True)
class RepositoryFixture:
    """Expose one test repository setup through named fields."""

    root: Path
    remote: Path
    baseline_commit: str


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


def _task_cleanup_reconciler(
    config: WorkspaceConfig,
    *,
    github: GitHubPullRequestBoundary | None = None,
    resources: CleanupResourceRegistry | None = None,
) -> TaskCleanupReconciler:
    """Build the cleanup workflow with explicit production-equivalent boundaries."""

    return TaskCleanupReconciler(
        config,
        github=github or GitHubPullRequestBoundary(),
        resources=resources,
    )


def _repository_create(
    workspace: Path,
    *,
    resources: bool = True,
    repository_name: str = "example",
    remote_name: str = "example-origin.git",
) -> RepositoryFixture:
    """Create one canonical checkout with a current YAML bootstrap contract.

    Args:
        workspace: Explicit workspace root.
        resources: Whether untracked bootstrap resources are declared.

    Returns:
        Checkout, bare origin and initial commit.
    """

    remote = workspace / remote_name
    root = workspace / repository_name
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "clone", str(remote), str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("# Example\n", encoding="utf-8")
    manifest = """schema_version: 3
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  handler_key_list: []
"""
    if resources:
        manifest = """schema_version: 3
resource:
  copy_optional_path_list: []
  copy_required_path_list:
    - local-config.json
  link_optional_path_list:
    - secret.txt
  link_required_path_list: []
cleanup:
  handler_key_list: []
"""
    (root / "worktree-bootstrap.yaml").write_text(manifest, encoding="utf-8")
    _git(root, "add", "README.md", "worktree-bootstrap.yaml")
    _git(root, "commit", "-m", "Initialize repository")
    _git(root, "push", "-u", "origin", "main")
    initial = _git(root, "rev-parse", "HEAD")
    if resources:
        (root / "local-config.json").write_text('{"mode":"test"}\n', encoding="utf-8")
        (root / "secret.txt").write_text("secret\n", encoding="utf-8")
    return RepositoryFixture(root=root, remote=remote, baseline_commit=initial)


def test_bootstrap_manifest_parser_is_self_contained_and_schema_bounded(
    tmp_path: Path,
) -> None:
    """The installable plugin parses only its owned YAML schema without PyYAML."""

    (tmp_path / "config:file").write_text("value\n", encoding="utf-8")
    plan = BootstrapPlan.from_manifest(
        b"""schema_version: 3
resource:
  copy_optional_path_list: []
  copy_required_path_list:
    - "config:file"
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  handler_key_list:
    - workflow-infrastructure-development-environment
""",
        main_root=tmp_path,
    )

    assert [item.relative_path for item in plan.resource_list] == ["config:file"]
    assert plan.cleanup_handler_key_list == ["workflow-infrastructure-development-environment"]


def test_retained_attempt_parser_accepts_only_inert_version_2_baselines(tmp_path: Path) -> None:
    """Immutable owned attempts may recover v2 resources without restoring cleanup argv authority."""

    payload_list = [
        b"""schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
""",
        b"""schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  command_argument_list:
    - python
    - development_environment_manage.py
    - destroy
    - --git-worktree
    - "{common_prefix}"
""",
    ]

    for payload in payload_list:
        plan = BootstrapPlan.from_retained_attempt_manifest(payload, main_root=tmp_path)
        assert plan.resource_list == []
        assert plan.cleanup_handler_key_list == []
        with pytest.raises(TaskWorkspaceError):
            BootstrapPlan.from_manifest(payload, main_root=tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        b"""schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  handler_key_list: []
""",
        b"""schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  command_argument_list:
    - null
""",
    ],
)
def test_retained_attempt_parser_rejects_nonhistorical_version_2_shapes(payload: bytes, tmp_path: Path) -> None:
    """The recovery parser admits only the two exact historical v2 shapes."""

    with pytest.raises(TaskWorkspaceError):
        BootstrapPlan.from_retained_attempt_manifest(payload, main_root=tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        b"schema_version: 3\nschema_version: 3\n",
        b"schema_version: 3\nresource:\n  copy_optional_path_list: []\n  copy_optional_path_list: []\n",
        b"---\nschema_version: 3\n",
        b"schema_version: &version 3\nresource: *version\n",
        b"schema_version: 3\nresource: {copy_optional_path_list: []}\n",
        b"schema_version: 3\nresource:\n    copy_optional_path_list: []\n",
        b"schema_version: 3\nresource:\n  copy_optional_path_list:\n    - 'broken'quote'\n",
        b"schema_version: 3\nresource:\n  copy_optional_path_list:\n    - #comment\n",
        b"schema_version: 3\nresource:\n  copy_optional_path_list: []\n  copy_required_path_list: []\n  link_optional_path_list: []\n  link_required_path_list: []\ncleanup:\n  command_argument_list:\n    - destroy\n",
        b"schema_version: 3\nresource:\n  copy_optional_path_list: []\n  copy_required_path_list: []\n  link_optional_path_list: []\n  link_required_path_list: []\ncleanup:\n  handler_key_list:\n    - unknown-handler\n",
    ],
)
def test_bootstrap_manifest_parser_rejects_yaml_outside_owned_subset(payload: bytes, tmp_path: Path) -> None:
    """Ambiguous general-YAML features cannot enter the bootstrap contract."""

    with pytest.raises(TaskWorkspaceError):
        BootstrapPlan.from_manifest(payload, main_root=tmp_path)


def test_copy_bootstrap_rejects_source_root_symlink_before_destination_creation(tmp_path: Path) -> None:
    """A copy root cannot import bytes through a symlink or create its destination parent."""

    main_root = tmp_path / "main"
    task_root = tmp_path / "task"
    foreign_root = tmp_path / "foreign"
    (main_root / "owned").mkdir(parents=True)
    task_root.mkdir()
    temporary_root = tmp_path / "private"
    temporary_root.mkdir(mode=0o700)
    foreign_root.mkdir()
    (foreign_root / "secret.txt").write_text("foreign\n", encoding="utf-8")
    (main_root / "owned" / "resource").symlink_to(foreign_root, target_is_directory=True)
    existing_destination = task_root / "owned" / "resource"
    existing_destination.mkdir(parents=True)
    (existing_destination / "preserved.txt").write_text("preserved\n", encoding="utf-8")
    resource = BootstrapResource(relative_path="owned/resource", kind="copy", skipped=False)

    with pytest.raises(TaskWorkspaceError, match="source may not be a symlink"):
        resource.materialize(
            main_root=main_root.resolve(),
            task_root=task_root.resolve(),
            temporary_root=temporary_root.resolve(),
        )

    assert [path.name for path in existing_destination.iterdir()] == ["preserved.txt"]
    assert (existing_destination / "preserved.txt").read_text(encoding="utf-8") == "preserved\n"
    assert (foreign_root / "secret.txt").read_text(encoding="utf-8") == "foreign\n"


def test_copy_bootstrap_rejects_nested_symlink_before_destination_creation(tmp_path: Path) -> None:
    """A nested copy symlink is rejected before any owned destination exists."""

    main_root = tmp_path / "main"
    task_root = tmp_path / "task"
    source_root = main_root / "owned" / "resource"
    source_root.mkdir(parents=True)
    task_root.mkdir()
    temporary_root = tmp_path / "private"
    temporary_root.mkdir(mode=0o700)
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("foreign\n", encoding="utf-8")
    (source_root / "nested-link").symlink_to(foreign)
    resource = BootstrapResource(relative_path="owned/resource", kind="copy", skipped=False)

    with pytest.raises(TaskWorkspaceError, match="source may not be a symlink"):
        resource.materialize(
            main_root=main_root.resolve(),
            task_root=task_root.resolve(),
            temporary_root=temporary_root.resolve(),
        )

    assert list(task_root.iterdir()) == []
    assert foreign.read_text(encoding="utf-8") == "foreign\n"


def test_copy_bootstrap_rejects_unsupported_source_type_before_destination_creation(tmp_path: Path) -> None:
    """A special source entry is rejected before any owned destination exists."""

    main_root = tmp_path / "main"
    task_root = tmp_path / "task"
    source_root = main_root / "owned" / "resource"
    source_root.mkdir(parents=True)
    task_root.mkdir()
    temporary_root = tmp_path / "private"
    temporary_root.mkdir(mode=0o700)
    os.mkfifo(source_root / "pipe")
    resource = BootstrapResource(relative_path="owned/resource", kind="copy", skipped=False)

    with pytest.raises(TaskWorkspaceError, match="source has an unsupported type"):
        resource.materialize(
            main_root=main_root.resolve(),
            task_root=task_root.resolve(),
            temporary_root=temporary_root.resolve(),
        )

    assert list(task_root.iterdir()) == []


def test_copy_bootstrap_materializes_and_reads_back_one_valid_tree(tmp_path: Path) -> None:
    """A physical regular-file tree is copied exactly and remains ready on readback."""

    main_root = tmp_path / "main"
    task_root = tmp_path / "task"
    source_root = main_root / "owned" / "resource"
    (source_root / "nested").mkdir(parents=True)
    task_root.mkdir()
    temporary_root = tmp_path / "private"
    temporary_root.mkdir(mode=0o700)
    source_file = source_root / "nested" / "config.json"
    source_file.write_text('{"mode":"test"}\n', encoding="utf-8")
    source_file.chmod(0o640)
    resource = BootstrapResource(relative_path="owned/resource", kind="copy", skipped=False)

    resource.materialize(
        main_root=main_root.resolve(),
        task_root=task_root.resolve(),
        temporary_root=temporary_root.resolve(),
    )
    resource.ready_require(main_root=main_root.resolve(), task_root=task_root.resolve())

    destination_file = task_root / "owned" / "resource" / "nested" / "config.json"
    assert destination_file.read_text(encoding="utf-8") == '{"mode":"test"}\n'
    assert destination_file.stat().st_mode & 0o777 == 0o640
    assert not destination_file.is_symlink()


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
        repository_list=[RepositoryRequest(str(remote), "main", baseline)],
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


def _nested_submodule_add(workspace: Path, root: Path) -> tuple[str, str]:
    """Extend the provider fixture with one nested gitlink and return both commits."""

    nested_remote = workspace / "nested-origin.git"
    nested_source = workspace / "nested-source"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(nested_remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "clone", str(nested_remote), str(nested_source)], check=True, capture_output=True)
    _git(nested_source, "config", "user.email", "test@example.com")
    _git(nested_source, "config", "user.name", "Test User")
    (nested_source / "nested.py").write_text("NESTED = 1\n", encoding="utf-8")
    _git(nested_source, "add", "nested.py")
    _git(nested_source, "commit", "-m", "Initialize nested provider")
    _git(nested_source, "push", "-u", "origin", "main")
    nested_commit = _git(nested_source, "rev-parse", "HEAD")

    provider_source = workspace / "provider-source"
    _git(
        provider_source,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(nested_remote),
        "nested/provider",
    )
    _git(provider_source, "commit", "-am", "Add nested provider")
    _git(provider_source, "push", "origin", "main")
    provider_commit = _git(provider_source, "rev-parse", "HEAD")

    provider_checkout = root / "vendor" / "provider"
    _git(provider_checkout, "fetch", "origin")
    _git(provider_checkout, "checkout", "--detach", provider_commit)
    _git(root, "add", "vendor/provider")
    _git(root, "commit", "-m", "Advance provider gitlink")
    _git(root, "push", "origin", "main")
    return provider_commit, nested_commit


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
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=[],
        resource_list=[],
    )


def test_prepare_preserves_dirty_main_and_rework_adopts_existing_workspace(
    tmp_path: Path,
) -> None:
    """Preparation never mutates main work and retry never resets task work."""

    repository_fixture = _repository_create(tmp_path)

    root = repository_fixture.root

    remote = repository_fixture.remote

    baseline = repository_fixture.baseline_commit
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote)

    state = TaskWorkspaceTransaction(config).prepare(request)[0]

    task_root = root / ".worktree" / request.basename
    assert state.baseline_commit == baseline
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

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    _submodule_add(tmp_path, root)
    provider_commit, nested_commit = _nested_submodule_add(tmp_path, root)
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-122")

    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    task_root = root / ".worktree" / request.basename
    provider_root = task_root / "vendor" / "provider"

    submodule_state_list = WorkspaceSubmoduleReader(task_root).read()
    assert [item.payload() for item in submodule_state_list] == [
        {"relative_path": "vendor/provider", "commit": provider_commit},
        {"relative_path": "vendor/provider/nested/provider", "commit": nested_commit},
    ]
    assert _git(provider_root, "branch", "--show-current") == ""
    _git(provider_root, "submodule", "deinit", "-f", "nested/provider")
    (provider_root / "provider.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(TaskWorkspaceError, match="uncommitted state"):
        TaskWorkspaceTransaction(config).prepare(request)

    assert (provider_root / "provider.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert _git(provider_root / "nested" / "provider", "rev-parse", "HEAD") == nested_commit


def test_direct_workspace_and_cleanup_models_require_strict_typed_lists(
    tmp_path: Path,
) -> None:
    """Internal callers cannot bypass the strict external collection boundary."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    remote = repository_fixture.remote
    repository = RepositoryRequest(str(remote), "main", "")
    with pytest.raises(TaskWorkspaceError, match="repository list"):
        WorkspaceRequest("AND-121", (repository,))  # type: ignore[arg-type]

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
            project_id=PROJECT_ID,
            authority=authority,
            repository_list=(repository,),  # type: ignore[arg-type]
            pull_request_list=[],
            resource_list=[],
        )

    with pytest.raises(TaskCleanupError, match="another shape"):
        CleanupRequest.from_payload(
            {
                "schema_version": 1,
                "issue_identifier": "AND-121",
                "project_id": PROJECT_ID,
                "authority": {
                    "scope": "terminal-issue",
                    "issue_status": "Canceled",
                    "project_status": "Canceled",
                    "final_acceptance_done": False,
                    "all_other_project_nodes_terminal": False,
                    "unresolved_remediation_blocker_count": 0,
                },
                "repository_list": [],
                "pull_request_list": [],
                "project_issue_identifier_list": [],
                "resource_list": [],
                "compatibility": [],
            }
        )


def test_workspace_request_rejects_duplicate_normalized_repository_identity() -> None:
    """Equivalent GitHub SCP and HTTPS origins cannot create two task owners."""

    with pytest.raises(TaskWorkspaceError, match="repeats one repository origin"):
        WorkspaceRequest(
            "AND-121",
            [
                RepositoryRequest("git@github.com:antonov-andrey/example.git", "main", ""),
                RepositoryRequest("https://github.com/antonov-andrey/example.git", "main", ""),
            ],
        )


def test_cleanup_request_rejects_duplicate_normalized_repository_identity() -> None:
    """Terminal cleanup cannot address one GitHub repository twice through transport aliases."""

    authority = CleanupAuthority(
        scope="terminal-issue",
        issue_status="Canceled",
        project_status="Canceled",
        final_acceptance_done=False,
        all_other_project_nodes_terminal=False,
        unresolved_remediation_blocker_count=0,
    )
    with pytest.raises(TaskCleanupError, match="repeats one repository"):
        CleanupRequest(
            issue_identifier="AND-121",
            project_id=PROJECT_ID,
            authority=authority,
            repository_list=[
                RepositoryRequest("git@github.com:antonov-andrey/example.git", "main", ""),
                RepositoryRequest("https://github.com/antonov-andrey/example.git", "main", ""),
            ],
            pull_request_list=[],
            resource_list=[],
        )


def test_workspace_discovery_rejects_two_checkouts_for_github_transport_aliases(tmp_path: Path) -> None:
    """SCP and HTTPS checkout aliases cannot split one repository's workspace ownership."""

    for name, origin in (
        ("scp-checkout", "git@github.com:antonov-andrey/example.git"),
        ("https-checkout", "https://github.com/antonov-andrey/example.git"),
    ):
        checkout = tmp_path / name
        checkout.mkdir()
        _git(checkout, "init", "--initial-branch=main")
        _git(checkout, "remote", "add", "origin", origin)

    request = RepositoryRequest("github.com/antonov-andrey/example", "main", "")

    with pytest.raises(TaskWorkspaceError, match="found 2"):
        WorkspaceRepository.from_config(WorkspaceConfig(tmp_path.resolve()), request)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "handler_key": "unknown-handler",
            "lifetime": "project",
            "identity": {},
        },
        {
            "handler_key": "development-infrastructure-acceptance-base-branch",
            "lifetime": "project",
            "command_argument_list": ["delete"],
            "identity": {},
        },
        {
            "handler_key": "development-infrastructure-acceptance-base-branch",
            "lifetime": "issue",
            "identity": {
                "project_id": PROJECT_ID,
                "owner_issue_identifier": "AND-16",
                "repository": "git@github.com:antonov-andrey/development-infrastructure.git",
                "branch": "acceptance/agent-development-workflow-complete-base",
            },
        },
        {
            "handler_key": "development-infrastructure-acceptance-base-branch",
            "lifetime": "project",
            "identity": {
                "project_id": PROJECT_ID,
                "owner_issue_identifier": "AND-16",
                "repository": "git@github.com:antonov-andrey/development-infrastructure.git",
                "branch": "acceptance/agent-development-workflow-complete-base",
                "approval_fingerprint": "forbidden",
            },
        },
    ],
)
def test_cleanup_resource_parser_rejects_unregistered_or_free_form_authority(payload: object) -> None:
    """Only fixed provider handlers and their typed natural identities are accepted."""

    with pytest.raises(CleanupResourceContractError):
        cleanup_resource_from_payload(payload)


def test_cleanup_request_requires_project_and_issue_owned_sorted_resources() -> None:
    """Typed cleanup resources cannot cross Project ownership or arrive in unstable order."""

    acceptance = AcceptanceBaseBranchCleanupResource(
        project_id=PROJECT_ID,
        owner_issue_identifier="AND-16",
        repository="git@github.com:antonov-andrey/development-infrastructure.git",
        branch="acceptance/agent-development-workflow-complete-base",
    )
    environment = WorkflowInfrastructureDevelopmentEnvironmentCleanupResource(
        project_id=PROJECT_ID,
        owner_issue_identifier="AND-45",
        repository="git@github.com:antonov-andrey/workflow-infrastructure.git",
        common_prefix="2026-08-08-and-45",
    )
    authority = CleanupAuthority(
        scope="project-final",
        issue_status="In Progress",
        project_status="In Progress",
        final_acceptance_done=True,
        all_other_project_nodes_terminal=True,
        unresolved_remediation_blocker_count=0,
    )

    with pytest.raises(TaskCleanupError, match="unique and sorted"):
        CleanupRequest(
            issue_identifier="AND-16",
            project_id=PROJECT_ID,
            authority=authority,
            repository_list=[
                RepositoryRequest(acceptance.repository, "main", ""),
                RepositoryRequest(environment.repository, "main", ""),
            ],
            pull_request_list=[],
            resource_list=[environment, acceptance],
            project_issue_identifier_list=["AND-16", "AND-45"],
        )

    with pytest.raises(TaskCleanupError, match="absent from the complete issue set"):
        CleanupRequest(
            issue_identifier="AND-16",
            project_id=PROJECT_ID,
            authority=authority,
            repository_list=[RepositoryRequest(environment.repository, "main", "")],
            pull_request_list=[],
            resource_list=[environment],
            project_issue_identifier_list=["AND-16"],
        )


def test_acceptance_cleanup_resource_rejects_a_lookalike_repository_owner() -> None:
    """An acceptance-shaped repository name cannot replace its participating identity."""

    with pytest.raises(TaskCleanupError, match="exact participating repository"):
        CleanupRequest(
            issue_identifier="AND-16",
            project_id=PROJECT_ID,
            authority=CleanupAuthority(
                scope="terminal-issue",
                issue_status="Done",
                project_status="In Progress",
                final_acceptance_done=False,
                all_other_project_nodes_terminal=False,
                unresolved_remediation_blocker_count=0,
            ),
            repository_list=[
                RepositoryRequest("git@github.com:antonov-andrey/development-infrastructure.git", "main", "")
            ],
            pull_request_list=[],
            resource_list=[
                AcceptanceBaseBranchCleanupResource(
                    project_id=PROJECT_ID,
                    owner_issue_identifier="AND-16",
                    repository="https://attacker.example/antonov-andrey/development-infrastructure",
                    branch="acceptance/agent-development-workflow-complete-base",
                )
            ],
        )


def test_environment_cleanup_resource_rejects_a_lookalike_repository_owner() -> None:
    """A Product-shaped repository name cannot replace its participating identity."""

    with pytest.raises(TaskCleanupError, match="exact participating repository"):
        CleanupRequest(
            issue_identifier="AND-45",
            project_id=PROJECT_ID,
            authority=CleanupAuthority(
                scope="terminal-issue",
                issue_status="Done",
                project_status="In Progress",
                final_acceptance_done=False,
                all_other_project_nodes_terminal=False,
                unresolved_remediation_blocker_count=0,
            ),
            repository_list=[
                RepositoryRequest("git@github.com:antonov-andrey/workflow-infrastructure.git", "main", "")
            ],
            pull_request_list=[],
            resource_list=[
                WorkflowInfrastructureDevelopmentEnvironmentCleanupResource(
                    project_id=PROJECT_ID,
                    owner_issue_identifier="AND-45",
                    repository="https://attacker.example/antonov-andrey/workflow-infrastructure",
                    common_prefix="2026-08-08-and-45",
                )
            ],
        )


def test_interrupted_bootstrap_recovers_from_minimal_baseline_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after worktree creation resumes exact materialization without a new branch."""

    repository_fixture = _repository_create(tmp_path)

    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-102")
    original = BootstrapResource.materialize
    call_count = 0

    def fail_once(resource: BootstrapResource, *args: object, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated interruption")
        original(resource, *args, **kwargs)

    monkeypatch.setattr(BootstrapResource, "materialize", fail_once)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        TaskWorkspaceTransaction(config).prepare(request)

    root = repository_fixture.root
    state_path = root / ".git" / "linear-agent-tools" / "task" / "and-102" / "workspace.json"
    assert state_path.read_text(encoding="utf-8") == (
        '{"baseline_commit":"' + repository_fixture.baseline_commit + '","schema_version":1}\n'
    )

    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    task_root = root / ".worktree" / request.basename

    assert state.payload() == {"schema_version": 1, "baseline_commit": repository_fixture.baseline_commit}
    assert (task_root / "local-config.json").read_text(encoding="utf-8") == '{"mode":"test"}\n'
    assert (task_root / "secret.txt").is_symlink()


def test_private_state_write_recovers_deterministic_stale_temp_and_preserves_foreign_sibling(
    tmp_path: Path,
) -> None:
    """A killed state write cannot block retry or authorize unrelated-file removal."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    request = _request(repository_fixture.remote, issue="AND-136")
    config = WorkspaceConfig(tmp_path.resolve())
    repository = WorkspaceRepository.from_config(config, request.repository_list[0])
    temporary_root = repository.bootstrap_temporary_root_get(request.issue_identifier, create=True)
    assert temporary_root is not None
    private_parent = temporary_root.parent
    repository.bootstrap_temporary_root_cleanup(request.issue_identifier)
    stale_state = private_parent / ".workspace.json.tmp"
    stale_state.write_text("stale private state\n", encoding="utf-8")
    stale_state.chmod(0o600)
    foreign_sibling = private_parent / "foreign.txt"
    foreign_sibling.write_text("preserve foreign\n", encoding="utf-8")
    foreign_sibling.chmod(0o600)

    state = TaskWorkspaceTransaction(config).prepare(request)[0]

    assert state.baseline_commit == repository_fixture.baseline_commit
    assert not stale_state.exists()
    assert foreign_sibling.read_text(encoding="utf-8") == "preserve foreign\n"
    assert not (private_parent / "bootstrap").exists()
    assert (private_parent / "workspace.json").is_file()


def test_bootstrap_retry_replaces_only_deterministic_private_crash_residue(tmp_path: Path) -> None:
    """Partial copy/link residue is removed without duplicating secrets or touching a foreign sibling."""

    repository_fixture = _repository_create(tmp_path)
    request = _request(repository_fixture.remote, issue="AND-137")
    config = WorkspaceConfig(tmp_path.resolve())
    TaskWorkspaceTransaction(config).prepare(request)
    repository = WorkspaceRepository.from_config(config, request.repository_list[0])
    temporary_root = repository.bootstrap_temporary_root_get(request.issue_identifier, create=True)
    assert temporary_root is not None
    (temporary_root / "local-config.json").write_text("stale copied secret\n", encoding="utf-8")
    foreign_target = tmp_path / "foreign-target.txt"
    foreign_target.write_text("foreign target\n", encoding="utf-8")
    (temporary_root / "secret.txt").symlink_to(foreign_target)
    foreign_sibling = temporary_root / "foreign.txt"
    foreign_sibling.write_text("preserve foreign\n", encoding="utf-8")

    TaskWorkspaceTransaction(config).prepare(request)

    task_root = repository_fixture.root / ".worktree" / request.basename
    assert (task_root / "local-config.json").read_text(encoding="utf-8") == '{"mode":"test"}\n'
    assert (task_root / "secret.txt").is_symlink()
    assert (task_root / "secret.txt").resolve(strict=True) == (repository_fixture.root / "secret.txt").resolve()
    assert not (temporary_root / "local-config.json").exists()
    assert not (temporary_root / "secret.txt").exists()
    assert foreign_sibling.read_text(encoding="utf-8") == "preserve foreign\n"
    assert foreign_target.read_text(encoding="utf-8") == "foreign target\n"


def test_terminal_cleanup_removes_owned_stale_bootstrap_state_but_not_foreign_files(tmp_path: Path) -> None:
    """Terminal recovery leaves neither owned temp content nor private state after process loss."""

    repository_fixture = _repository_create(tmp_path)
    request = _request(repository_fixture.remote, issue="AND-138")
    config = WorkspaceConfig(tmp_path.resolve())
    TaskWorkspaceTransaction(config).prepare(request)
    repository = WorkspaceRepository.from_config(config, request.repository_list[0])
    temporary_root = repository.bootstrap_temporary_root_get(request.issue_identifier, create=True)
    assert temporary_root is not None
    stale_copy = temporary_root / "local-config.json"
    stale_copy.mkdir()
    (stale_copy / "partial.txt").write_text("partial secret\n", encoding="utf-8")
    foreign_sibling = temporary_root / "foreign.txt"
    foreign_sibling.write_text("preserve foreign\n", encoding="utf-8")
    private_parent = temporary_root.parent

    _task_cleanup_reconciler(config).cleanup(_canceled_cleanup_request(request, issue="AND-138"))

    assert not stale_copy.exists()
    assert foreign_sibling.read_text(encoding="utf-8") == "preserve foreign\n"
    assert not (private_parent / ".workspace.json.tmp").exists()
    assert not (private_parent / "workspace.json").exists()
    assert not (repository_fixture.root / ".worktree" / request.basename).exists()


def test_private_state_recovery_rejects_foreign_temp_alias_without_deleting_it(tmp_path: Path) -> None:
    """A deterministic name alone cannot make a foreign symlink owned cleanup state."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    request = _request(repository_fixture.remote, issue="AND-139")
    config = WorkspaceConfig(tmp_path.resolve())
    repository = WorkspaceRepository.from_config(config, request.repository_list[0])
    temporary_root = repository.bootstrap_temporary_root_get(request.issue_identifier, create=True)
    assert temporary_root is not None
    private_parent = temporary_root.parent
    repository.bootstrap_temporary_root_cleanup(request.issue_identifier)
    foreign_target = tmp_path / "foreign-state.txt"
    foreign_target.write_text("foreign state\n", encoding="utf-8")
    foreign_alias = private_parent / ".workspace.json.tmp"
    foreign_alias.symlink_to(foreign_target)

    with pytest.raises(TaskWorkspaceError, match="not one owned private ordinary file"):
        TaskWorkspaceTransaction(config).prepare(request)

    assert foreign_alias.is_symlink()
    assert foreign_target.read_text(encoding="utf-8") == "foreign state\n"
    assert _git(repository_fixture.root, "branch", "--list", request.branch_name) == ""


def test_validate_is_read_only_when_owned_worktree_is_missing(tmp_path: Path) -> None:
    """Validation reports an absent owned worktree without recreating it."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-109")
    state = TaskWorkspaceTransaction(config).prepare(request)[0]
    task_root = root / ".worktree" / request.basename
    _git(root, "worktree", "remove", "--force", str(task_root))

    with pytest.raises(TaskWorkspaceError, match="absent or unavailable"):
        TaskWorkspaceTransaction(config).validate(request)

    assert not task_root.exists()
    assert _git(root, "branch", "--list", request.branch_name) == request.branch_name


def test_unowned_collision_and_concurrent_issue_lock_fail_closed(
    tmp_path: Path,
) -> None:
    """Path bytes and a second process are not accepted as ownership proof."""

    repository_fixture = _repository_create(tmp_path)

    root = repository_fixture.root

    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    collision = root / ".worktree" / "and-103"
    collision.mkdir(parents=True)

    with pytest.raises(TaskWorkspaceError, match="without private ownership"):
        TaskWorkspaceTransaction(config).prepare(_request(remote, issue="AND-103"))

    with IssueWorkspaceLock(config, "AND-104"):
        with pytest.raises(TaskWorkspaceError, match="Another local session"):
            with IssueWorkspaceLock(config, "AND-104"):
                pass


def test_attempt_guard_is_canonical_across_cwds_and_prevents_overlapping_mutation(
    tmp_path: Path,
) -> None:
    """One explicit workspace and issue map to one lock across process CWDs."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first_cwd = tmp_path / "first-cwd"
    second_cwd = tmp_path / "second-cwd"
    first_temp = tmp_path / "first-temp"
    second_temp = tmp_path / "second-temp"
    first_cwd.mkdir()
    second_cwd.mkdir()
    first_temp.mkdir()
    second_temp.mkdir()
    mutation_log = tmp_path / "mutation.log"
    script = LIBRARY_ROOT / "task_workspace" / "tool" / "attempt.py"
    environment = {
        **os.environ,
        "LINEAR_AGENT_WORKSPACE_ROOT": str(workspace.resolve()),
        "TMPDIR": str(first_temp.resolve()),
    }
    first = subprocess.Popen(
        [sys.executable, str(script), "hold", "--issue-identifier", "AND-104"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        cwd=first_cwd,
    )
    try:
        assert first.stdout is not None
        assert '"status":"held"' in first.stdout.readline()
        mutation_log.write_text("first-attempt\n", encoding="utf-8")
        second = subprocess.Popen(
            [sys.executable, str(script), "hold", "--issue-identifier", "AND-104"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**environment, "TMPDIR": str(second_temp.resolve())},
            cwd=second_cwd,
        )
        assert second.stdout is not None
        second_stdout = second.stdout.readline()
        if '"status":"held"' in second_stdout:
            mutation_log.write_text(
                mutation_log.read_text(encoding="utf-8") + "second-attempt\n",
                encoding="utf-8",
            )
            second.terminate()
        second.wait()
        assert second.returncode == 2
        assert second.stderr is not None
        assert "Another local session" in second.stderr.read()
        assert mutation_log.read_text(encoding="utf-8") == "first-attempt\n"
        with IssueWorkspaceLock(WorkspaceConfig(workspace.resolve()), "AND-104"):
            pass
    finally:
        first.terminate()
        first.wait()

    with IssueAttemptLock(WorkspaceConfig(workspace.resolve()), "AND-104"):
        pass


@pytest.mark.parametrize("git_marker_kind", ("directory", "file"))
def test_workspace_root_rejects_repository_and_linked_worktree_namespaces(
    tmp_path: Path,
    git_marker_kind: str,
) -> None:
    """A repository root cannot split the canonical multi-repository guard."""

    workspace = tmp_path / git_marker_kind
    workspace.mkdir()
    git_marker = workspace / ".git"
    if git_marker_kind == "directory":
        git_marker.mkdir()
    else:
        git_marker.write_text("gitdir: ../private/worktree\n", encoding="utf-8")

    with pytest.raises(TaskWorkspaceError, match="contain canonical checkouts"):
        WorkspaceConfig.from_environment({"LINEAR_AGENT_WORKSPACE_ROOT": str(workspace.resolve())})


def test_workspace_paths_are_canonical_and_private_state_has_only_a_baseline(tmp_path: Path) -> None:
    """External paths stay canonical while private state rejects derivable identities."""

    workspace_path = str(tmp_path.resolve())
    for substituted_path in (
        "//" + workspace_path.lstrip("/"),
        workspace_path + "/",
        str(tmp_path.parent.resolve()) + "/./" + tmp_path.name,
    ):
        with pytest.raises(TaskWorkspaceError, match="canonical absolute path"):
            WorkspaceConfig.from_environment({"LINEAR_AGENT_WORKSPACE_ROOT": substituted_path})

    repository_fixture = _repository_create(tmp_path)
    state = TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(
        _request(repository_fixture.remote, issue="AND-124")
    )[0]
    payload = state.payload()
    payload["task_root"] = str(repository_fixture.root / ".worktree" / "and-124")
    with pytest.raises(TaskWorkspaceError, match="another shape"):
        RepositoryWorkspaceState.from_payload(payload)

    with pytest.raises(TaskWorkspaceError, match="full Git commit"):
        RepositoryWorkspaceState.from_payload({"schema_version": 1, "baseline_commit": "not-a-commit"})


def test_issue_lock_rejects_attacker_symlink_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A predictable lock path cannot redirect lock creation outside its private root."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    private_root = tmp_path / f"linear-agent-tools-{os.getuid()}"
    private_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(lock_module, "_HOST_LOCK_CONTAINER", tmp_path)

    with pytest.raises(TaskWorkspaceError, match="private user-owned physical directory"):
        with IssueWorkspaceLock(WorkspaceConfig(workspace.resolve()), "AND-113"):
            pass

    assert list(outside.iterdir()) == []


def test_workspace_discovery_rejects_symlink_checkout_outside_explicit_root(
    tmp_path: Path,
) -> None:
    """An approved origin does not authorize following a workspace child symlink."""

    outside = tmp_path / "outside"
    outside.mkdir()
    repository_fixture = _repository_create(outside, resources=False)
    root = repository_fixture.root
    remote = repository_fixture.remote
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "example").symlink_to(root, target_is_directory=True)
    request = RepositoryRequest(str(remote), "main", "")

    with pytest.raises(TaskWorkspaceError, match="found 0"):
        WorkspaceRepository.from_config(WorkspaceConfig(workspace.resolve()), request)


def test_worktree_container_symlink_is_rejected_before_git_mutation(
    tmp_path: Path,
) -> None:
    """A repository-local path name cannot redirect task worktrees outside the checkout."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".worktree").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TaskWorkspaceError, match="physical repository-local directory"):
        TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(_request(remote, issue="AND-111"))

    assert not (outside / "and-111").exists()
    assert _git(root, "branch", "--list", "linear/and-111") == ""


def test_issue_worktree_symlink_alias_cannot_mutate_the_registered_worktree(tmp_path: Path) -> None:
    """Validation, adoption, and cleanup reject an alias without changing its target."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    root = repository_fixture.root
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(repository_fixture.remote, issue="AND-132")
    TaskWorkspaceTransaction(config).prepare(request)
    task_root = root / ".worktree" / request.basename
    foreign_root = tmp_path / "foreign-registered-worktree"
    _git(root, "worktree", "move", str(task_root), str(foreign_root))
    task_root.symlink_to(foreign_root, target_is_directory=True)
    marker = foreign_root / "foreign-uncommitted.txt"
    marker.write_text("preserve foreign work\n", encoding="utf-8")
    state_path = root / ".git" / "linear-agent-tools" / "task" / request.basename / "workspace.json"
    branch_commit = _git(foreign_root, "rev-parse", "HEAD")
    cleanup_request = _canceled_cleanup_request(request, issue=request.issue_identifier)

    with pytest.raises(TaskWorkspaceError, match="physical canonical directory"):
        _task_cleanup_reconciler(config).cleanup(cleanup_request)
    assert task_root.is_symlink()
    assert foreign_root.is_dir()
    assert marker.read_text(encoding="utf-8") == "preserve foreign work\n"
    assert _git(foreign_root, "rev-parse", "HEAD") == branch_commit
    assert state_path.is_file()

    with pytest.raises(TaskWorkspaceError, match="physical canonical directory"):
        TaskWorkspaceTransaction(config).validate(request)
    assert task_root.is_symlink()
    assert foreign_root.is_dir()
    assert marker.read_text(encoding="utf-8") == "preserve foreign work\n"
    assert _git(foreign_root, "rev-parse", "HEAD") == branch_commit
    assert state_path.is_file()

    with pytest.raises(TaskWorkspaceError, match="physical canonical directory"):
        TaskWorkspaceTransaction(config).prepare(request)
    assert task_root.is_symlink()
    assert foreign_root.is_dir()
    assert marker.read_text(encoding="utf-8") == "preserve foreign work\n"
    assert _git(foreign_root, "rev-parse", "HEAD") == branch_commit
    assert state_path.is_file()


def test_bootstrap_manifest_comes_from_exact_baseline_not_dirty_main(
    tmp_path: Path,
) -> None:
    """Uncommitted main manifest edits cannot change an already bound attempt contract."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    (root / "worktree-bootstrap.yaml").write_text(
        """schema_version: 3
resource:
  copy_optional_path_list: []
  copy_required_path_list:
    - uncommitted-required.txt
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  handler_key_list: []
""",
        encoding="utf-8",
    )

    request = _request(remote, issue="AND-118")
    state = TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(request)[0]

    assert state.payload() == {"schema_version": 1, "baseline_commit": repository_fixture.baseline_commit}
    assert not (root / ".worktree" / request.basename / "uncommitted-required.txt").exists()
    assert _git(root, "status", "--short", "worktree-bootstrap.yaml") == "M worktree-bootstrap.yaml"


def test_legacy_bootstrap_requires_adoption_then_uses_only_canonical_yaml(
    tmp_path: Path,
) -> None:
    """Legacy TOML blocks Product dispatch; a committed YAML adoption removes the block."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
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
        """schema_version: 3
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  handler_key_list: []
""",
        encoding="utf-8",
    )
    _git(root, "add", "worktree-bootstrap.toml", "worktree-bootstrap.yaml")
    _git(root, "commit", "-m", "Adopt canonical Linear workspace contract")
    _git(root, "push", "origin", "main")

    state = TaskWorkspaceTransaction(config).prepare(request)[0]

    assert state.baseline_commit == _git(root, "rev-parse", "HEAD")
    assert _git(root / ".worktree" / request.basename, "branch", "--show-current") == request.branch_name


def test_owned_version_2_attempt_recovers_while_new_attempt_requires_version_3(tmp_path: Path) -> None:
    """A schema upgrade preserves only already-owned immutable attempt baselines."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    root = repository_fixture.root
    remote = repository_fixture.remote
    (root / "worktree-bootstrap.yaml").write_text(
        """schema_version: 2
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  command_argument_list:
    - python
    - development_environment_manage.py
    - destroy
    - --git-worktree
    - "{common_prefix}"
""",
        encoding="utf-8",
    )
    _git(root, "add", "worktree-bootstrap.yaml")
    _git(root, "commit", "-m", "Represent retained version two attempt baseline")
    _git(root, "push", "origin", "main")
    baseline_commit = _git(root, "rev-parse", "HEAD")
    config = WorkspaceConfig(tmp_path.resolve())

    new_request = _request(remote, issue="AND-125", baseline=baseline_commit)
    with pytest.raises(TaskWorkspaceError, match="schema_version must be 3"):
        TaskWorkspaceTransaction(config).prepare(new_request)
    assert _git(root, "branch", "--list", new_request.branch_name) == ""

    retained_request = _request(remote, issue="AND-126", baseline=baseline_commit)
    repository = WorkspaceRepository.from_config(config, retained_request.repository_list[0])
    retained_state = RepositoryWorkspaceState(baseline_commit)
    repository.state_write(retained_request.issue_identifier, retained_state)
    repository.task_worktree_create_or_accept(retained_request.issue_identifier, retained_state)

    validated_state = TaskWorkspaceTransaction(config).validate(retained_request)
    recovered_state = TaskWorkspaceTransaction(config).prepare(retained_request)

    assert validated_state == [retained_state]
    assert recovered_state == [retained_state]
    assert _git(root / ".worktree" / retained_request.basename, "rev-parse", "HEAD") == baseline_commit


def test_bootstrap_destination_parent_symlink_cannot_escape_task_worktree(
    tmp_path: Path,
) -> None:
    """A tracked symlink ancestor cannot redirect bootstrap writes outside the task root."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "config.json"
    source.write_text("source\n", encoding="utf-8")
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    (root / "worktree-bootstrap.yaml").write_text(
        """schema_version: 3
resource:
  copy_optional_path_list: []
  copy_required_path_list:
    - redirect/config.json
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  handler_key_list: []
""",
        encoding="utf-8",
    )
    _git(root, "add", "redirect", "worktree-bootstrap.yaml")
    _git(root, "commit", "-m", "Declare nested bootstrap resource")
    _git(root, "push", "origin", "main")

    with pytest.raises(TaskWorkspaceError, match="destination parent is not a physical directory"):
        TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(_request(remote, issue="AND-119"))

    assert source.read_text(encoding="utf-8") == "source\n"


def test_discovery_ignores_unrelated_checkout_with_noncanonical_origin(
    tmp_path: Path,
) -> None:
    """An unrelated malformed sibling does not block the exact approved checkout."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    remote = repository_fixture.remote

    baseline = repository_fixture.baseline_commit
    unrelated = tmp_path / "unrelated"
    _git(tmp_path, "init", "--initial-branch=main", str(unrelated))
    _git(unrelated, "remote", "add", "origin", "relative-repository")

    request = _request(remote, issue="AND-124", baseline=baseline)
    state = TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(request)[0]

    assert state.baseline_commit == baseline
    assert (repository_fixture.root / ".worktree" / request.basename).is_dir()


def test_fresh_host_adopts_remote_rework_only_with_recorded_baseline(
    tmp_path: Path,
) -> None:
    """Remote branch adoption cannot silently replace the first-attempt baseline."""

    first_workspace = tmp_path / "first"
    first_workspace.mkdir()
    repository_fixture = _repository_create(first_workspace, resources=False)
    first_root = repository_fixture.root
    remote = repository_fixture.remote
    baseline = repository_fixture.baseline_commit
    first_config = WorkspaceConfig(first_workspace.resolve())
    request = _request(remote, issue="AND-105")
    TaskWorkspaceTransaction(first_config).prepare(request)
    task_root = first_root / ".worktree" / request.basename
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

    adopted_request = _request(remote, issue="AND-105", baseline=baseline)
    adopted = TaskWorkspaceTransaction(fresh_config).prepare(adopted_request)[0]
    assert adopted.baseline_commit == baseline
    assert (fresh_root / ".worktree" / adopted_request.basename / "change.txt").read_text(
        encoding="utf-8"
    ) == "candidate\n"


def test_unowned_local_branch_is_not_adopted_without_private_state(
    tmp_path: Path,
) -> None:
    """A matching local branch name alone never proves that Linear owns its commits."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote

    baseline = repository_fixture.baseline_commit
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

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote

    baseline = repository_fixture.baseline_commit
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

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    outside = tmp_path / "outside-state"
    outside.mkdir()
    (root / ".git" / "linear-agent-tools").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TaskWorkspaceError, match="user-owned physical directory"):
        TaskWorkspaceTransaction(WorkspaceConfig(tmp_path.resolve())).prepare(_request(remote, issue="AND-115"))

    assert list(outside.iterdir()) == []
    assert _git(root, "branch", "--list", "linear/and-115") == ""


def test_terminal_cleanup_removes_exact_workspace_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """Done cleanup removes branch/state while complete absence is subsequent success."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-106")
    TaskWorkspaceTransaction(config).prepare(request)
    cleanup_request = CleanupRequest(
        issue_identifier="AND-106",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=[],
        resource_list=[],
    )

    first = _task_cleanup_reconciler(config).cleanup(cleanup_request)
    second = _task_cleanup_reconciler(config).cleanup(cleanup_request)

    assert first.removed_worktree_count == 1
    assert first.removed_local_branch_count == 1
    assert second.removed_worktree_count == 0
    assert not (root / ".worktree" / request.basename).exists()
    assert _git(root, "branch", "--list", "linear/and-106") == ""


def test_terminal_cleanup_recovers_from_live_state_after_partial_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry continues from current targets after a failure without durable cleanup phases."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    root = repository_fixture.root
    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-130")
    TaskWorkspaceTransaction(config).prepare(request)
    task_root = root / ".worktree" / request.basename
    _git(task_root, "push", "-u", "origin", request.branch_name)
    cleanup_request = CleanupRequest(
        issue_identifier="AND-130",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=[],
        resource_list=[],
    )
    original = WorkspaceRepository.remote_branch_delete_exact
    fail_once = True

    def interrupted_delete(
        repository: WorkspaceRepository,
        branch_name: str,
        *,
        expected_commit: str,
    ) -> None:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise TaskWorkspaceError("simulated remote interruption")
        original(repository, branch_name, expected_commit=expected_commit)

    monkeypatch.setattr(WorkspaceRepository, "remote_branch_delete_exact", interrupted_delete)

    with pytest.raises(TaskWorkspaceError, match="simulated remote interruption"):
        _task_cleanup_reconciler(config).cleanup(cleanup_request)

    state_path = root / ".git" / "linear-agent-tools" / "task" / "and-130" / "workspace.json"
    assert not task_root.exists()
    assert state_path.exists()
    assert _git(root, "branch", "--list", request.branch_name) == request.branch_name
    assert _git(root, "ls-remote", "--heads", "origin", request.branch_name)

    result = _task_cleanup_reconciler(config).cleanup(cleanup_request)

    assert result.removed_worktree_count == 0
    assert result.removed_remote_branch_count == 1
    assert result.removed_local_branch_count == 1
    assert not state_path.exists()


def test_remote_branch_cleanup_rejects_divergent_push_destination_without_foreign_mutation(
    tmp_path: Path,
) -> None:
    """A fetch-owned lease cannot authorize deletion in another push repository."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    root = repository_fixture.root
    request = _request(repository_fixture.remote, issue="AND-133")
    config = WorkspaceConfig(tmp_path.resolve())
    TaskWorkspaceTransaction(config).prepare(request)
    task_root = root / ".worktree" / request.basename
    _git(task_root, "push", "-u", "origin", request.branch_name)
    expected_commit = _git(task_root, "rev-parse", "HEAD")
    foreign_remote = tmp_path / "foreign.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(foreign_remote)],
        check=True,
        capture_output=True,
    )
    _git(task_root, "push", str(foreign_remote), f"{request.branch_name}:{request.branch_name}")
    _git(root, "config", "remote.origin.pushurl", str(foreign_remote))

    with pytest.raises(TaskWorkspaceError, match="fetch and push destinations differ"):
        _task_cleanup_reconciler(config).cleanup(_canceled_cleanup_request(request, issue="AND-133"))

    assert _git(root, "ls-remote", "--heads", str(repository_fixture.remote), request.branch_name).split()[0] == (
        expected_commit
    )
    assert _git(root, "ls-remote", "--heads", str(foreign_remote), request.branch_name).split()[0] == expected_commit


def test_remote_branch_cleanup_rejects_unknown_effective_push_destination(tmp_path: Path) -> None:
    """A relative or otherwise unknown effective push target has no mutation authority."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    root = repository_fixture.root
    request = _request(repository_fixture.remote, issue="AND-134")
    config = WorkspaceConfig(tmp_path.resolve())
    TaskWorkspaceTransaction(config).prepare(request)
    task_root = root / ".worktree" / request.basename
    _git(task_root, "push", "-u", "origin", request.branch_name)
    expected_commit = _git(task_root, "rev-parse", "HEAD")
    _git(root, "config", "remote.origin.pushurl", "relative-target")

    with pytest.raises(TaskWorkspaceError, match="unsupported or relative form"):
        _task_cleanup_reconciler(config).cleanup(_canceled_cleanup_request(request, issue="AND-134"))

    assert _git(root, "ls-remote", "--heads", str(repository_fixture.remote), request.branch_name).split()[0] == (
        expected_commit
    )


def test_remote_branch_cleanup_leases_the_current_validated_push_target_head(tmp_path: Path) -> None:
    """A stale tracking snapshot cannot delete a newer head at the validated push target."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    root = repository_fixture.root
    request = _request(repository_fixture.remote, issue="AND-135")
    config = WorkspaceConfig(tmp_path.resolve())
    TaskWorkspaceTransaction(config).prepare(request)
    task_root = root / ".worktree" / request.basename
    _git(task_root, "push", "-u", "origin", request.branch_name)
    repository = WorkspaceRepository.from_config(config, request.repository_list[0])
    repository.fetch()
    stale_commit = repository.commit_get(f"refs/remotes/origin/{request.branch_name}")
    (task_root / "later.txt").write_text("later\n", encoding="utf-8")
    _git(task_root, "add", "later.txt")
    _git(task_root, "commit", "-m", "Advance remote task branch")
    _git(task_root, "push", "origin", request.branch_name)
    current_commit = _git(task_root, "rev-parse", "HEAD")

    with pytest.raises(TaskWorkspaceError, match="durable cleanup snapshot"):
        repository.remote_branch_delete_exact(request.branch_name, expected_commit=stale_commit)

    assert _git(root, "ls-remote", "--heads", str(repository_fixture.remote), request.branch_name).split()[0] == (
        current_commit
    )


def test_successful_cleanup_retires_branch_only_after_terminal_merged_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue cleanup, not merge mutation, owns branch deletion after exact MERGED state."""

    repository_fixture = _repository_create(tmp_path, resources=False)
    root = repository_fixture.root
    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    workspace_request = _request(remote, issue="AND-129")
    TaskWorkspaceTransaction(config).prepare(workspace_request)
    task_root = root / ".worktree" / workspace_request.basename
    (task_root / "change.txt").write_text("reviewed change\n", encoding="utf-8")
    _git(task_root, "add", "change.txt")
    _git(task_root, "commit", "-m", "Implement reviewed change")
    head_commit = _git(task_root, "rev-parse", "HEAD")
    _git(task_root, "push", "-u", "origin", workspace_request.branch_name)
    _git(root, "merge", "--ff-only", workspace_request.branch_name)
    _git(root, "push", "origin", "main")

    repository_identity = RepositoryIdentity("antonov-andrey/example")
    monkeypatch.setattr(
        RepositoryIdentity,
        "from_origin_identity",
        classmethod(lambda _cls, _origin_identity: repository_identity),
    )

    class GitHub:
        state = "OPEN"

        @staticmethod
        def matching_number_list(**_kwargs: object) -> list[int]:
            return [17]

        def inspect(self, **_kwargs: object) -> PullRequestSnapshot:
            merged = self.state == "MERGED"
            return PullRequestSnapshot(
                repository=repository_identity,
                number=17,
                url="https://github.com/antonov-andrey/example/pull/17",
                title="AND-129 terminal cleanup ownership",
                state=self.state,
                draft=False,
                base_branch="main",
                base_commit=repository_fixture.baseline_commit,
                head_branch="linear/and-129",
                head_commit=head_commit,
                merge_state="CLEAN",
                merged_at=datetime.now(timezone.utc) if merged else None,
                merge_commit=head_commit if merged else "",
                merged_by_login="octocat" if merged else "",
                merged_by_user_id=7 if merged else 0,
                merged_by_node_id="U_octocat" if merged else "",
                required_check_list=[],
            )

    cleanup_request = CleanupRequest(
        issue_identifier="AND-129",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=workspace_request.repository_list,
        pull_request_list=[PullRequestReference(repository=repository_identity, number=17)],
        resource_list=[],
    )
    github = GitHub()
    reconciler = _task_cleanup_reconciler(config, github=github)  # type: ignore[arg-type]

    with pytest.raises(TaskCleanupError, match="requires every linked pull request to be merged"):
        reconciler.cleanup(cleanup_request)

    assert task_root.exists()
    assert _git(root, "ls-remote", "--heads", "origin", workspace_request.branch_name)

    github.state = "MERGED"
    result = reconciler.cleanup(cleanup_request)

    assert result.removed_worktree_count == 1
    assert result.removed_local_branch_count == 1
    assert result.removed_remote_branch_count == 1
    assert not task_root.exists()
    assert _git(root, "ls-remote", "--heads", "origin", workspace_request.branch_name) == ""


def test_done_cleanup_rejects_unintegrated_branch_commits(tmp_path: Path) -> None:
    """Successful authority cannot discard commits that are absent from the remote base."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-113")
    TaskWorkspaceTransaction(config).prepare(request)
    task_root = repository_fixture.root / ".worktree" / request.basename
    (task_root / "candidate.txt").write_text("not merged\n", encoding="utf-8")
    _git(task_root, "add", "candidate.txt")
    _git(task_root, "commit", "-m", "Prepare unmerged candidate")
    cleanup_request = CleanupRequest(
        issue_identifier="AND-113",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=[],
        resource_list=[],
    )

    with pytest.raises(TaskCleanupError, match="absent from its remote base"):
        _task_cleanup_reconciler(config).cleanup(cleanup_request)

    assert task_root.exists()
    assert _git(task_root, "rev-parse", "HEAD") != _git(task_root, "rev-parse", "origin/main")


def test_canceled_cleanup_may_remove_dirty_exact_task_state(tmp_path: Path) -> None:
    """Explicit Canceled authority removes unmerged issue-owned work without touching main."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-107")
    TaskWorkspaceTransaction(config).prepare(request)
    (root / ".worktree" / request.basename / "uncommitted.txt").write_text(
        "discard by cancellation\n", encoding="utf-8"
    )
    cleanup_request = CleanupRequest(
        issue_identifier="AND-107",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=request.repository_list,
        pull_request_list=[],
        resource_list=[],
    )

    result = _task_cleanup_reconciler(config).cleanup(cleanup_request)

    assert result.removed_worktree_count == 1
    assert (root / "README.md").exists()


def test_cleanup_requires_complete_exact_pull_request_set(tmp_path: Path) -> None:
    """An omitted exact task PR cannot be hidden by an empty cleanup request list."""

    snapshot = PullRequestSnapshot(
        repository=RepositoryIdentity("antonov-andrey/example"),
        number=17,
        url="https://github.com/antonov-andrey/example/pull/17",
        title="AND-121 exact task pull request",
        state="OPEN",
        draft=False,
        base_branch="main",
        base_commit="c" * 40,
        head_branch="linear/and-121",
        head_commit="a" * 40,
        merge_state="CLEAN",
        merged_at=None,
        merge_commit="",
        merged_by_login="",
        merged_by_user_id=0,
        merged_by_node_id="",
        required_check_list=[],
    )

    class Repository:
        origin_identity = "github.com/antonov-andrey/example"
        request = type("Request", (), {"base_branch": "main"})()

    class GitHub:
        @staticmethod
        def matching_number_list(**_kwargs: object) -> list[int]:
            return [17]

        @staticmethod
        def inspect(**_kwargs: object) -> PullRequestSnapshot:
            return snapshot

    request = CleanupRequest(
        issue_identifier="AND-121",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[RepositoryRequest("https://github.com/antonov-andrey/example.git", "main", "")],
        pull_request_list=[],
        resource_list=[],
    )

    with pytest.raises(TaskCleanupError, match="omits or substitutes"):
        _task_cleanup_reconciler(
            WorkspaceConfig(tmp_path.resolve()),
            github=GitHub(),  # type: ignore[arg-type]
        )._pull_request_contract_require(
            CleanupState(
                request=request,
                repository_by_origin_identity_map={
                    request.repository_list[0].origin_identity: Repository(),  # type: ignore[dict-item]
                },
            )
        )


def test_successful_cleanup_rejects_closed_unmerged_only_history(tmp_path: Path) -> None:
    """A historical CLOSED PR can never authorize successful branch retirement."""

    repository_identity = RepositoryIdentity("antonov-andrey/example")
    snapshot = PullRequestSnapshot(
        repository=repository_identity,
        number=8,
        url="https://github.com/antonov-andrey/example/pull/8",
        title="Historical candidate title was edited",
        state="CLOSED",
        draft=False,
        base_branch="main",
        base_commit="c" * 40,
        head_branch="linear/and-121",
        head_commit="a" * 40,
        merge_state="UNKNOWN",
        merged_at=None,
        merge_commit="",
        merged_by_login="",
        merged_by_user_id=0,
        merged_by_node_id="",
        required_check_list=[],
    )

    class Repository:
        origin_identity = "github.com/antonov-andrey/example"
        request = RepositoryRequest("https://github.com/antonov-andrey/example.git", "main", "")

    class GitHub:
        @staticmethod
        def matching_number_list(**_kwargs: object) -> list[int]:
            return [8]

        @staticmethod
        def inspect(**_kwargs: object) -> PullRequestSnapshot:
            return snapshot

    request = CleanupRequest(
        issue_identifier="AND-121",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[Repository.request],
        pull_request_list=[PullRequestReference(repository=repository_identity, number=8)],
        resource_list=[],
    )

    with pytest.raises(TaskCleanupError, match="Closed unmerged.*never successful merge evidence"):
        _task_cleanup_reconciler(
            WorkspaceConfig(tmp_path.resolve()),
            github=GitHub(),  # type: ignore[arg-type]
        )._pull_request_contract_require(
            CleanupState(
                request=request,
                repository_by_origin_identity_map={
                    Repository.request.origin_identity: Repository(),  # type: ignore[dict-item]
                },
            )
        )


def test_successful_cleanup_selects_merged_candidate_over_closed_unmerged_history(tmp_path: Path) -> None:
    """Replacement MERGED proof supersedes, but never converts, closed-unmerged history."""

    repository_identity = RepositoryIdentity("antonov-andrey/example")
    closed_snapshot = PullRequestSnapshot(
        repository=repository_identity,
        number=8,
        url="https://github.com/antonov-andrey/example/pull/8",
        title="Historical candidate title was edited",
        state="CLOSED",
        draft=False,
        base_branch="main",
        base_commit="c" * 40,
        head_branch="linear/and-121",
        head_commit="a" * 40,
        merge_state="UNKNOWN",
        merged_at=None,
        merge_commit="",
        merged_by_login="",
        merged_by_user_id=0,
        merged_by_node_id="",
        required_check_list=[],
    )
    merged_snapshot = replace(
        closed_snapshot,
        number=17,
        url="https://github.com/antonov-andrey/example/pull/17",
        title="Merged candidate title was edited",
        state="MERGED",
        merged_at=datetime.now(timezone.utc),
        merge_commit="b" * 40,
        merged_by_login="octocat",
        merged_by_user_id=7,
        merged_by_node_id="U_octocat",
    )

    class Repository:
        origin_identity = "github.com/antonov-andrey/example"
        request = RepositoryRequest("https://github.com/antonov-andrey/example.git", "main", "")

    class GitHub:
        @staticmethod
        def matching_number_list(**_kwargs: object) -> list[int]:
            return [8, 17]

        @staticmethod
        def inspect(*, number: int, **_kwargs: object) -> PullRequestSnapshot:
            return {8: closed_snapshot, 17: merged_snapshot}[number]

    request = CleanupRequest(
        issue_identifier="AND-121",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[Repository.request],
        pull_request_list=[PullRequestReference(repository=repository_identity, number=17)],
        resource_list=[],
    )

    _task_cleanup_reconciler(
        WorkspaceConfig(tmp_path.resolve()),
        github=GitHub(),  # type: ignore[arg-type]
    )._pull_request_contract_require(
        CleanupState(
            request=request,
            repository_by_origin_identity_map={
                Repository.request.origin_identity: Repository(),  # type: ignore[dict-item]
            },
        )
    )


def test_canceled_cleanup_accepts_closed_history_without_issue_title(tmp_path: Path) -> None:
    """Canceled cleanup selects exact closed history without mutable title identity."""

    repository_identity = RepositoryIdentity("antonov-andrey/example")
    snapshot = PullRequestSnapshot(
        repository=repository_identity,
        number=8,
        url="https://github.com/antonov-andrey/example/pull/8",
        title="Historical candidate title was edited",
        state="CLOSED",
        draft=False,
        base_branch="main",
        base_commit="c" * 40,
        head_branch="linear/and-121",
        head_commit="a" * 40,
        merge_state="UNKNOWN",
        merged_at=None,
        merge_commit="",
        merged_by_login="",
        merged_by_user_id=0,
        merged_by_node_id="",
        required_check_list=[],
    )

    class Repository:
        origin_identity = "github.com/antonov-andrey/example"
        request = RepositoryRequest("https://github.com/antonov-andrey/example.git", "main", "")

    class GitHub:
        close_count = 0

        @staticmethod
        def matching_number_list(**_kwargs: object) -> list[int]:
            return [8]

        @staticmethod
        def inspect(**_kwargs: object) -> PullRequestSnapshot:
            return snapshot

        def close_if_open(self, **_kwargs: object) -> PullRequestSnapshot:
            self.close_count += 1
            return snapshot

    request = CleanupRequest(
        issue_identifier="AND-121",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Canceled",
            project_status="Canceled",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[Repository.request],
        pull_request_list=[PullRequestReference(repository=repository_identity, number=8)],
        resource_list=[],
    )
    github = GitHub()
    reconciler = _task_cleanup_reconciler(
        WorkspaceConfig(tmp_path.resolve()),
        github=github,  # type: ignore[arg-type]
    )
    state = CleanupState(
        request=request,
        repository_by_origin_identity_map={
            Repository.request.origin_identity: Repository(),  # type: ignore[dict-item]
        },
    )

    reconciler._pull_request_contract_require(state)
    reconciler._pull_request_reconcile(state)

    assert state.closed_pull_request_count == 0
    assert github.close_count == 1


@pytest.mark.parametrize(
    ("issue_status", "project_status", "pull_request_state", "expected_close_count"),
    [
        ("Done", "In Progress", "MERGED", 0),
        ("Canceled", "Canceled", "OPEN", 1),
    ],
)
def test_cleanup_reconciles_complete_exact_pull_request_set(
    tmp_path: Path,
    issue_status: str,
    project_status: str,
    pull_request_state: str,
    expected_close_count: int,
) -> None:
    """Exact terminal PR targets are validated, proved merged or closed."""

    repository_identity = RepositoryIdentity("antonov-andrey/example")
    merged = pull_request_state == "MERGED"
    snapshot = PullRequestSnapshot(
        repository=repository_identity,
        number=17,
        url="https://github.com/antonov-andrey/example/pull/17",
        title="AND-121 reconcile terminal pull request",
        state=pull_request_state,
        draft=False,
        base_branch="main",
        base_commit="c" * 40,
        head_branch="linear/and-121",
        head_commit="a" * 40,
        merge_state="CLEAN",
        merged_at=datetime.now(timezone.utc) if merged else None,
        merge_commit="b" * 40 if merged else "",
        merged_by_login="octocat" if merged else "",
        merged_by_user_id=7 if merged else 0,
        merged_by_node_id="U_octocat" if merged else "",
        required_check_list=[],
    )

    class Repository:
        origin_identity = "github.com/antonov-andrey/example"
        request = RepositoryRequest(
            "https://github.com/antonov-andrey/example.git",
            "main",
            "",
        )

    class GitHub:
        close_count = 0

        @staticmethod
        def matching_number_list(**_kwargs: object) -> list[int]:
            return [17]

        @staticmethod
        def inspect(**_kwargs: object) -> PullRequestSnapshot:
            return snapshot

        def close_if_open(self, **_kwargs: object) -> PullRequestSnapshot:
            self.close_count += 1
            return replace(snapshot, state="CLOSED")

    request = CleanupRequest(
        issue_identifier="AND-121",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status=issue_status,
            project_status=project_status,
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[Repository.request],
        pull_request_list=[PullRequestReference(repository=repository_identity, number=17)],
        resource_list=[],
    )
    github = GitHub()
    reconciler = _task_cleanup_reconciler(
        WorkspaceConfig(tmp_path.resolve()),
        github=github,  # type: ignore[arg-type]
    )
    state = CleanupState(
        request=request,
        repository_by_origin_identity_map={
            Repository.request.origin_identity: Repository(),  # type: ignore[dict-item]
        },
    )

    reconciler._pull_request_contract_require(state)
    reconciler._pull_request_reconcile(state)

    assert state.closed_pull_request_count == expected_close_count
    assert github.close_count == expected_close_count


def test_project_final_cleanup_requires_acceptance_other_terminal_nodes_and_no_remediation(
    tmp_path: Path,
) -> None:
    """The final cleanup node retires its exact workspace only after every downstream gate."""

    repository_fixture = _repository_create(tmp_path, resources=False)

    root = repository_fixture.root

    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    request = _request(remote, issue="AND-110")
    TaskWorkspaceTransaction(config).prepare(request)
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
        project_id=PROJECT_ID,
        authority=authority,
        repository_list=request.repository_list,
        pull_request_list=[],
        resource_list=[],
        project_issue_identifier_list=["AND-110"],
    )

    first = _task_cleanup_reconciler(config).cleanup(cleanup_request)
    second = _task_cleanup_reconciler(config).cleanup(cleanup_request)

    assert first.removed_worktree_count == 1
    assert first.removed_local_branch_count == 1
    assert second.removed_worktree_count == 0
    assert not (root / ".worktree" / request.basename).exists()

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

    repository_fixture = _repository_create(tmp_path, resources=False)

    remote = repository_fixture.remote
    config = WorkspaceConfig(tmp_path.resolve())
    other_request = _request(remote, issue="AND-120")
    cleanup_node_request = _request(remote, issue="AND-121")
    TaskWorkspaceTransaction(config).prepare(other_request)
    TaskWorkspaceTransaction(config).prepare(cleanup_node_request)
    project_cleanup = CleanupRequest(
        issue_identifier="AND-121",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="project-final",
            issue_status="In Progress",
            project_status="In Progress",
            final_acceptance_done=True,
            all_other_project_nodes_terminal=True,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=cleanup_node_request.repository_list,
        pull_request_list=[],
        resource_list=[],
        project_issue_identifier_list=["AND-120", "AND-121"],
    )

    with pytest.raises(TaskCleanupError, match="AND-120.*private task-workspace state"):
        _task_cleanup_reconciler(config).cleanup(project_cleanup)

    _task_cleanup_reconciler(config).cleanup(_canceled_cleanup_request(other_request, issue="AND-120"))
    result = _task_cleanup_reconciler(config).cleanup(project_cleanup)

    assert result.removed_worktree_count == 0


def test_acceptance_base_resource_is_retained_then_deleted_idempotently(tmp_path: Path) -> None:
    """Project cleanup retains, then exactly deletes, the real acceptance-base resource shape."""

    repository_fixture = _repository_create(
        tmp_path,
        resources=False,
        repository_name="development-infrastructure",
        remote_name="development-infrastructure.git",
    )
    branch = "acceptance/agent-development-workflow-6f750a05-complete-base"
    _git(repository_fixture.root, "branch", branch)
    _git(repository_fixture.root, "push", "origin", branch)
    config = WorkspaceConfig(tmp_path.resolve())
    resource = AcceptanceBaseBranchCleanupResource(
        project_id=PROJECT_ID,
        owner_issue_identifier="AND-16",
        repository=str(repository_fixture.remote),
        branch=branch,
    )
    repository_request = RepositoryRequest(str(repository_fixture.remote), "main", "")
    retained_request = CleanupRequest(
        issue_identifier="AND-16",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[repository_request],
        pull_request_list=[],
        resource_list=[resource],
    )
    project_request = CleanupRequest(
        issue_identifier="AND-16",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="project-final",
            issue_status="In Progress",
            project_status="In Progress",
            final_acceptance_done=True,
            all_other_project_nodes_terminal=True,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[repository_request],
        pull_request_list=[],
        resource_list=[resource],
        project_issue_identifier_list=["AND-16"],
    )

    retained = _task_cleanup_reconciler(config).cleanup(retained_request)
    first = _task_cleanup_reconciler(config).cleanup(project_request)
    second = _task_cleanup_reconciler(config).cleanup(project_request)

    assert [item.state for item in retained.resource_readback_list] == ["retained"]
    assert [item.state for item in first.resource_readback_list] == ["absent"]
    assert [item.state for item in second.resource_readback_list] == ["absent"]
    assert _git(repository_fixture.root, "ls-remote", "--heads", "origin", branch) == ""


def test_workflow_infrastructure_resource_uses_only_fixed_typed_provider_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry uses the published owner worktree before merge and canonical main after merge."""

    repository_fixture = _repository_create(
        tmp_path,
        resources=False,
        repository_name="workflow-infrastructure",
        remote_name="workflow-infrastructure.git",
    )
    (repository_fixture.root / ".gitignore").write_text(".worktree/\n", encoding="utf-8")
    _git(repository_fixture.root, "add", ".gitignore")
    _git(repository_fixture.root, "commit", "-m", "Ignore task worktrees")
    _git(repository_fixture.root, "push", "origin", "main")
    baseline_commit = _git(repository_fixture.root, "rev-parse", "HEAD")
    config = WorkspaceConfig(tmp_path.resolve())
    repository = WorkspaceRepository.from_config(
        config,
        RepositoryRequest(str(repository_fixture.remote), "main", baseline_commit),
    )
    workspace_state = RepositoryWorkspaceState(baseline_commit)
    repository.state_write("AND-45", workspace_state)
    repository.task_worktree_create_or_accept("AND-45", workspace_state)
    task_root = repository_fixture.root / ".worktree" / "and-45"
    script = task_root / "development_environment_manage.py"
    script.write_text("raise SystemExit('test boundary is injected')\n", encoding="utf-8")
    _git(task_root, "add", script.name)
    _git(task_root, "commit", "-m", "Add cleanup entrypoint")
    _git(task_root, "push", "-u", "origin", "linear/and-45")
    call_list: list[tuple[list[str], Path, bytes, dict[str, str]]] = []
    inventory_absent = False

    def runner(argument_list: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        input_bytes = kwargs["input"]
        cwd = kwargs["cwd"]
        environment_by_name_map = kwargs["env"]
        assert isinstance(input_bytes, bytes)
        assert isinstance(cwd, Path)
        assert isinstance(environment_by_name_map, dict)
        call_list.append((argument_list, cwd, input_bytes, environment_by_name_map))
        operation = argument_list[2]
        if operation == "destroy":
            payload = {
                "schema_version": 1,
                "common_prefix": "2026-08-08-and-45",
                "external_resources_absent": True,
            }
        else:
            payload = {
                "schema_version": 1,
                "common_prefix": "2026-08-08-and-45",
                "environment_name": "2026-08-08-and-45",
                "external_resources_absent": inventory_absent,
                "resource_identity_list": ["compute-2026-08-08-and-45"],
            }
        return subprocess.CompletedProcess(
            argument_list,
            0,
            stdout=(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setenv("HOME", "/tmp/foreign-home")
    monkeypatch.setenv("CODEX_HOME", "/tmp/foreign-codex-home")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "foreign-aws-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "foreign-aws-secret-key")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "foreign-aws-session-token")
    monkeypatch.setenv("GH_TOKEN", "foreign-github-secret")
    monkeypatch.setenv("LINEAR_API_KEY", "foreign-linear-secret")
    monkeypatch.setenv("UNRELATED_SECRET", "foreign-secret")
    registry = CleanupResourceRegistry(runner=runner)
    resource = WorkflowInfrastructureDevelopmentEnvironmentCleanupResource(
        project_id=PROJECT_ID,
        owner_issue_identifier="AND-45",
        repository=str(repository_fixture.remote),
        common_prefix="2026-08-08-and-45",
    )
    retained_request = CleanupRequest(
        issue_identifier="AND-45",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="attempt",
            issue_status="In Progress",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[repository.request],
        pull_request_list=[],
        resource_list=[resource],
    )
    terminal_request = CleanupRequest(
        issue_identifier="AND-45",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="terminal-issue",
            issue_status="Done",
            project_status="In Progress",
            final_acceptance_done=False,
            all_other_project_nodes_terminal=False,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[repository.request],
        pull_request_list=[],
        resource_list=[resource],
    )
    project_request = CleanupRequest(
        issue_identifier="AND-45",
        project_id=PROJECT_ID,
        authority=CleanupAuthority(
            scope="project-final",
            issue_status="In Progress",
            project_status="In Progress",
            final_acceptance_done=True,
            all_other_project_nodes_terminal=True,
            unresolved_remediation_blocker_count=0,
        ),
        repository_list=[repository.request],
        pull_request_list=[],
        resource_list=[resource],
        project_issue_identifier_list=["AND-45"],
    )
    reconciler = _task_cleanup_reconciler(config, resources=registry)

    with pytest.raises(TaskCleanupError, match="does not declare its cleanup handler"):
        reconciler.cleanup(retained_request)
    assert call_list == []

    (task_root / "worktree-bootstrap.yaml").write_text(
        """schema_version: 3
resource:
  copy_optional_path_list: []
  copy_required_path_list: []
  link_optional_path_list: []
  link_required_path_list: []
cleanup:
  handler_key_list:
    - workflow-infrastructure-development-environment
""",
        encoding="utf-8",
    )
    _git(task_root, "add", "worktree-bootstrap.yaml")
    _git(task_root, "commit", "-m", "Declare cleanup handler")
    _git(task_root, "push", "origin", "linear/and-45")

    retained = reconciler.cleanup(retained_request)
    inventory_absent = True
    absent = registry.reconcile(resource, repository=repository, delete=False)
    inventory_absent = False
    _git(repository_fixture.root, "merge", "--ff-only", "linear/and-45")
    _git(repository_fixture.root, "push", "origin", "main")
    original_remote_delete = WorkspaceRepository.remote_branch_delete_exact
    fail_once = True

    def interrupted_remote_delete(
        current_repository: WorkspaceRepository,
        branch_name: str,
        *,
        expected_commit: str,
    ) -> None:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise TaskWorkspaceError("simulated terminal retirement interruption")
        original_remote_delete(current_repository, branch_name, expected_commit=expected_commit)

    monkeypatch.setattr(WorkspaceRepository, "remote_branch_delete_exact", interrupted_remote_delete)
    with pytest.raises(TaskWorkspaceError, match="simulated terminal retirement interruption"):
        reconciler.cleanup(terminal_request)
    assert not task_root.exists()
    terminal = reconciler.cleanup(terminal_request)
    inventory_absent = True
    first = reconciler.cleanup(project_request)
    second = reconciler.cleanup(project_request)

    assert [item.state for item in retained.resource_readback_list] == ["retained"]
    assert absent.state == "absent"
    assert [item.state for item in terminal.resource_readback_list] == ["retained"]
    assert terminal.removed_worktree_count == 1
    assert not task_root.exists()
    assert [item.state for item in first.resource_readback_list] == ["absent"]
    assert [item.state for item in second.resource_readback_list] == ["absent"]
    assert [item[0][2] for item in call_list] == [
        "destroy-inventory",
        "destroy-inventory",
        "destroy-inventory",
        "destroy-inventory",
        "destroy",
        "destroy",
    ]
    standard_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    expected_environment_by_name_map = {
        "HOME": str(standard_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.pathsep.join(
            (
                str(standard_home / ".local" / "bin"),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            )
        ),
    }
    for index, (argument_list, cwd, input_bytes, environment_by_name_map) in enumerate(call_list):
        expected_root = task_root if index < 4 else repository_fixture.root
        assert argument_list == [
            sys.executable,
            str(expected_root / "development_environment_manage.py"),
            argument_list[2],
            "--git-worktree",
            "2026-08-08-and-45",
        ]
        assert cwd == expected_root
        assert json.loads(input_bytes) == {"schema_version": 1, "common_prefix": "2026-08-08-and-45"}
        assert environment_by_name_map == expected_environment_by_name_map
