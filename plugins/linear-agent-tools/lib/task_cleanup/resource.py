"""Fixed provider cleanup handlers for typed non-standard resources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

from json_contract import JsonContractError, json_load_strict
from task_cleanup.contract import (
    AcceptanceBaseBranchCleanupResource,
    CleanupResource,
    WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
)
from task_cleanup.model import TaskCleanupError
from task_workspace.bootstrap import BootstrapPlan
from task_workspace.model import TaskWorkspaceError
from task_workspace.repository import WorkspaceRepository, git_command_run, git_command_text_get


@dataclass(frozen=True, slots=True)
class CleanupResourceReadback:
    """Expose one exact typed resource after retention or deletion reconciliation."""

    resource: CleanupResource
    state: str

    def __post_init__(self) -> None:
        """Require the only two externally meaningful cleanup states."""

        if self.state not in {"retained", "absent"}:
            raise TaskCleanupError("Cleanup resource readback state is unsupported")

    def payload(self) -> dict[str, object]:
        """Return the natural resource identity with its direct current state."""

        return {**self.resource.payload(), "state": self.state}


class AcceptanceBaseBranchCleanupHandler:
    """Retain or delete one exact remote acceptance base branch with a Git lease."""

    resource_type = AcceptanceBaseBranchCleanupResource

    def reconcile(
        self,
        resource: AcceptanceBaseBranchCleanupResource,
        *,
        repository: WorkspaceRepository,
        delete: bool,
    ) -> CleanupResourceReadback:
        """Read or delete the exact retained remote branch idempotently."""

        if repository.origin_identity != resource.repository:
            raise TaskCleanupError("Acceptance base branch cleanup repository owner differs from its resource")
        try:
            repository.fetch()
            if not repository.exist_remote_branch(resource.branch):
                return CleanupResourceReadback(resource, "absent")
            if not delete:
                repository.commit_get(f"refs/remotes/origin/{resource.branch}")
                return CleanupResourceReadback(resource, "retained")
            expected_commit = repository.commit_get(f"refs/remotes/origin/{resource.branch}")
            repository.remote_branch_delete_exact(resource.branch, expected_commit=expected_commit)
            if repository.exist_remote_branch(resource.branch):
                raise TaskCleanupError("Acceptance base branch remained after exact cleanup")
            return CleanupResourceReadback(resource, "absent")
        except TaskWorkspaceError as error:
            raise TaskCleanupError("Acceptance base branch cleanup could not prove its exact Git identity") from error


class WorkflowInfrastructureDevelopmentEnvironmentCleanupHandler:
    """Invoke the fixed workflow-infrastructure cleanup boundary for one typed environment."""

    resource_type = WorkflowInfrastructureDevelopmentEnvironmentCleanupResource

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        """Bind the fixed closed-environment Product process boundary."""

        try:
            standard_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except KeyError as error:
            raise TaskCleanupError("Development environment cleanup requires one operating-system user") from error
        if not standard_home.is_absolute() or not standard_home.is_dir():
            raise TaskCleanupError("Development environment cleanup requires the operating-system user's home")
        self._environment_by_name_map = {
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
        self._runner = runner

    def reconcile(
        self,
        resource: WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
        *,
        repository: WorkspaceRepository,
        delete: bool,
    ) -> CleanupResourceReadback:
        """Run fixed inventory or deletion and require the Product's exact typed readback."""

        if repository.origin_identity != resource.repository:
            raise TaskCleanupError("Development environment cleanup repository owner differs from its resource")
        provider_root = self._provider_root_ready(repository, resource, delete=delete)
        operation = "destroy" if delete else "destroy-inventory"
        argument_list = [
            sys.executable,
            str(provider_root / "development_environment_manage.py"),
            operation,
            "--git-worktree",
            resource.common_prefix,
        ]
        request_bytes = (
            json.dumps(
                {"schema_version": 1, "common_prefix": resource.common_prefix},
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        try:
            completed_process = self._runner(
                argument_list,
                cwd=provider_root,
                check=False,
                capture_output=True,
                env=self._environment_by_name_map,
                input=request_bytes,
            )
        except OSError as error:
            raise TaskCleanupError("Development environment cleanup provider could not start") from error
        if completed_process.returncode != 0:
            raise TaskCleanupError("Development environment cleanup provider failed")
        try:
            payload = json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise TaskCleanupError("Development environment cleanup readback is malformed") from error
        if delete:
            expected = {"schema_version", "common_prefix", "external_resources_absent"}
            if (
                not isinstance(payload, dict)
                or set(payload) != expected
                or payload["schema_version"] != 1
                or payload["common_prefix"] != resource.common_prefix
                or payload["external_resources_absent"] is not True
            ):
                raise TaskCleanupError("Development environment absence readback differs from its typed identity")
            return CleanupResourceReadback(resource, "absent")
        expected = {
            "schema_version",
            "common_prefix",
            "environment_name",
            "external_resources_absent",
            "resource_identity_list",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or payload["schema_version"] != 1
            or payload["common_prefix"] != resource.common_prefix
            or not isinstance(payload["environment_name"], str)
            or not payload["environment_name"]
            or not isinstance(payload["external_resources_absent"], bool)
            or not isinstance(payload["resource_identity_list"], list)
            or any(not isinstance(item, str) or not item for item in payload["resource_identity_list"])
            or payload["resource_identity_list"] != sorted(set(payload["resource_identity_list"]))
        ):
            raise TaskCleanupError("Development environment retention readback differs from its typed identity")
        return CleanupResourceReadback(resource, "absent" if payload["external_resources_absent"] else "retained")

    def _provider_root_ready(
        self,
        repository: WorkspaceRepository,
        resource: WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
        *,
        delete: bool,
    ) -> Path:
        """Select the exact retained owner worktree or synchronized post-merge main."""

        try:
            repository.fetch()
            if not delete:
                return self._retained_provider_root_require(repository, resource.owner_issue_identifier)
            return self._canonical_provider_root_require(repository)
        except TaskWorkspaceError as error:
            raise TaskCleanupError("Workflow-infrastructure cleanup repository identity could not be proven") from error

    def _canonical_provider_root_require(self, repository: WorkspaceRepository) -> Path:
        """Return clean synchronized canonical main after owner-workspace retirement."""

        try:
            if git_command_text_get(repository.main_root, ("symbolic-ref", "--quiet", "--short", "HEAD")) != "main":
                raise TaskCleanupError("Workflow-infrastructure canonical checkout is not on main")
            if git_command_text_get(repository.main_root, ("status", "--porcelain=v1", "--untracked-files=normal")):
                raise TaskCleanupError("Workflow-infrastructure canonical main checkout is not clean")
            local_commit = repository.commit_get("refs/heads/main")
            remote_commit = repository.commit_get("refs/remotes/origin/main")
            self._handler_declaration_require(repository, remote_commit)
            if local_commit != remote_commit:
                ancestor = git_command_run(
                    repository.main_root,
                    ("merge-base", "--is-ancestor", local_commit, remote_commit),
                    check=False,
                )
                if ancestor.returncode != 0:
                    raise TaskCleanupError("Workflow-infrastructure main cannot fast-forward to current origin/main")
                git_command_run(
                    repository.main_root,
                    ("merge", "--ff-only", remote_commit),
                    mutation=True,
                )
            if repository.commit_get("refs/heads/main") != remote_commit or git_command_text_get(
                repository.main_root,
                ("status", "--porcelain=v1", "--untracked-files=normal"),
            ):
                raise TaskCleanupError("Workflow-infrastructure main synchronization readback failed")
            self._entrypoint_require(repository, repository.main_root, remote_commit)
            return repository.main_root
        except TaskWorkspaceError as error:
            raise TaskCleanupError("Workflow-infrastructure cleanup repository identity could not be proven") from error

    def _retained_provider_root_require(
        self,
        repository: WorkspaceRepository,
        owner_issue_identifier: str,
    ) -> Path:
        """Require the published owner boundary, reconstructing its worktree when needed."""

        state = repository.state_read(owner_issue_identifier)
        if state is None:
            return self._canonical_provider_root_require(repository)
        repository.state_identity_require(owner_issue_identifier, state)
        branch_name = f"linear/{owner_issue_identifier.lower()}"
        if not repository.exist_remote_branch(branch_name):
            if repository.exist_local_branch(branch_name):
                branch_commit = repository.commit_get(f"refs/heads/{branch_name}")
                remote_main_commit = repository.commit_get("refs/remotes/origin/main")
                if (
                    git_command_run(
                        repository.main_root,
                        ("merge-base", "--is-ancestor", branch_commit, remote_main_commit),
                        check=False,
                    ).returncode
                    != 0
                ):
                    raise TaskCleanupError("Workflow-infrastructure retired owner branch is absent from merged main")
            return self._canonical_provider_root_require(repository)
        task_root = repository.task_worktree_create_or_accept(owner_issue_identifier, state)
        if git_command_run(
            task_root,
            ("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=none"),
        ).stdout:
            raise TaskCleanupError("Workflow-infrastructure owner worktree is not clean")
        branch_commit = repository.commit_get(f"refs/heads/{branch_name}")
        if repository.commit_get(f"refs/remotes/origin/{branch_name}") != branch_commit:
            raise TaskCleanupError("Workflow-infrastructure owner worktree differs from its published branch")
        self._entrypoint_require(repository, task_root, branch_commit)
        return task_root

    def _entrypoint_require(self, repository: WorkspaceRepository, provider_root: Path, commit: str) -> None:
        """Require the invoked entrypoint to equal one ordinary file in the selected commit."""

        self._handler_declaration_require(repository, commit)
        script = provider_root / "development_environment_manage.py"
        tracked_bytes = repository.tracked_file_bytes_get(commit, script.name)
        if script.is_symlink() or not script.is_file() or tracked_bytes is None:
            raise TaskCleanupError("Workflow-infrastructure cleanup entrypoint is not one ordinary tracked file")
        try:
            current_bytes = script.read_bytes()
        except OSError as error:
            raise TaskCleanupError("Workflow-infrastructure cleanup entrypoint could not be read") from error
        if current_bytes != tracked_bytes:
            raise TaskCleanupError("Workflow-infrastructure cleanup entrypoint differs from its selected commit")

    def _handler_declaration_require(self, repository: WorkspaceRepository, commit: str) -> None:
        """Require the selected owner commit to declare this exact fixed handler."""

        manifest_bytes = repository.tracked_file_bytes_get(commit, "worktree-bootstrap.yaml")
        if manifest_bytes is None:
            raise TaskCleanupError("Workflow-infrastructure cleanup handler declaration is absent")
        plan = BootstrapPlan.from_manifest(manifest_bytes, main_root=repository.main_root)
        if self.resource_type.handler_key not in plan.cleanup_handler_key_list:
            raise TaskCleanupError("Workflow-infrastructure owner does not declare its cleanup handler")


class CleanupResourceRegistry:
    """Resolve only fixed installed provider handlers by their declared keys."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        """Construct the closed handler registry."""

        self._handler_by_key_map = {
            AcceptanceBaseBranchCleanupResource.handler_key: AcceptanceBaseBranchCleanupHandler(),
            WorkflowInfrastructureDevelopmentEnvironmentCleanupResource.handler_key: (
                WorkflowInfrastructureDevelopmentEnvironmentCleanupHandler(runner=runner)
            ),
        }

    def reconcile(
        self,
        resource: CleanupResource,
        *,
        repository: WorkspaceRepository,
        delete: bool,
    ) -> CleanupResourceReadback:
        """Dispatch one typed resource only through its fixed provider handler."""

        handler = self._handler_by_key_map.get(resource.handler_key)
        if handler is None or not isinstance(resource, handler.resource_type):
            raise TaskCleanupError("Cleanup resource handler is absent from the provider registry")
        if repository.origin_identity != resource.repository:
            raise TaskCleanupError("Cleanup resource repository owner differs from its participating repository")
        return handler.reconcile(resource, repository=repository, delete=delete)
