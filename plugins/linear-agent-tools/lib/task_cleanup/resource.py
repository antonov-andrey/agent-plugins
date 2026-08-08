"""Fixed provider cleanup handlers for typed non-standard resources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

from json_contract import JsonContractError, json_load_strict
from task_cleanup.contract import (
    AcceptanceBaseBranchCleanupResource,
    CleanupResource,
    WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
)
from task_cleanup.model import TaskCleanupError
from task_workspace.model import RepositoryRequest, TaskWorkspaceError, WorkspaceConfig
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

    def __init__(self, config: WorkspaceConfig) -> None:
        """Bind the canonical workspace root used for repository discovery."""

        self._config = config

    def reconcile(
        self,
        resource: AcceptanceBaseBranchCleanupResource,
        *,
        delete: bool,
    ) -> CleanupResourceReadback:
        """Read or delete the exact retained remote branch idempotently."""

        try:
            repository = WorkspaceRepository.from_config(
                self._config,
                RepositoryRequest(resource.repository, resource.branch, ""),
            )
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
        config: WorkspaceConfig,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        """Bind canonical repository discovery and the injectable fixed process boundary."""

        self._config = config
        self._runner = runner

    def reconcile(
        self,
        resource: WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
        *,
        delete: bool,
    ) -> CleanupResourceReadback:
        """Run fixed inventory or deletion and require the Product's exact typed readback."""

        provider_root = self._provider_root_ready(resource, delete=delete)
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
        resource: WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
        *,
        delete: bool,
    ) -> Path:
        """Select the exact retained owner worktree or synchronized post-merge main."""

        try:
            repository = WorkspaceRepository.from_config(
                self._config,
                RepositoryRequest(resource.repository, "main", ""),
            )
            repository.fetch()
            if not delete:
                return self._retained_provider_root_require(repository, resource.owner_issue_identifier)
            if git_command_text_get(repository.main_root, ("symbolic-ref", "--quiet", "--short", "HEAD")) != "main":
                raise TaskCleanupError("Workflow-infrastructure canonical checkout is not on main")
            if git_command_text_get(repository.main_root, ("status", "--porcelain=v1", "--untracked-files=normal")):
                raise TaskCleanupError("Workflow-infrastructure canonical main checkout is not clean")
            local_commit = repository.commit_get("refs/heads/main")
            remote_commit = repository.commit_get("refs/remotes/origin/main")
            if local_commit != remote_commit:
                ancestor = git_command_run(
                    repository.main_root,
                    ("merge-base", "--is-ancestor", local_commit, remote_commit),
                    check=False,
                )
                if ancestor.returncode != 0:
                    raise TaskCleanupError("Workflow-infrastructure main cannot fast-forward to current origin/main")
                git_command_run(repository.main_root, ("merge", "--ff-only", remote_commit))
            if repository.commit_get("refs/heads/main") != remote_commit or git_command_text_get(
                repository.main_root,
                ("status", "--porcelain=v1", "--untracked-files=normal"),
            ):
                raise TaskCleanupError("Workflow-infrastructure main synchronization readback failed")
            self._entrypoint_require(repository, repository.main_root, remote_commit)
            return repository.main_root
        except TaskWorkspaceError as error:
            raise TaskCleanupError("Workflow-infrastructure cleanup repository identity could not be proven") from error

    @staticmethod
    def _retained_provider_root_require(repository: WorkspaceRepository, owner_issue_identifier: str) -> Path:
        """Require the pushed clean owner worktree that contains the unmerged inventory boundary."""

        state = repository.state_read(owner_issue_identifier)
        if state is None:
            raise TaskCleanupError("Workflow-infrastructure owner worktree state is absent before merge")
        repository.state_identity_require(owner_issue_identifier, state)
        repository.task_worktree_require(owner_issue_identifier, state)
        task_root = repository.main_root / ".worktree" / owner_issue_identifier.lower()
        if git_command_run(
            task_root,
            ("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=none"),
        ).stdout:
            raise TaskCleanupError("Workflow-infrastructure owner worktree is not clean")
        branch_name = f"linear/{owner_issue_identifier.lower()}"
        if not repository.exist_remote_branch(branch_name):
            raise TaskCleanupError("Workflow-infrastructure owner branch is not published")
        branch_commit = repository.commit_get(f"refs/heads/{branch_name}")
        if repository.commit_get(f"refs/remotes/origin/{branch_name}") != branch_commit:
            raise TaskCleanupError("Workflow-infrastructure owner worktree differs from its published branch")
        WorkflowInfrastructureDevelopmentEnvironmentCleanupHandler._entrypoint_require(
            repository,
            task_root,
            branch_commit,
        )
        return task_root

    @staticmethod
    def _entrypoint_require(repository: WorkspaceRepository, provider_root: Path, commit: str) -> None:
        """Require the invoked entrypoint to equal one ordinary file in the selected commit."""

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


class CleanupResourceRegistry:
    """Resolve only fixed installed provider handlers by their declared keys."""

    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        """Construct the closed handler registry."""

        self._handler_by_key_map = {
            AcceptanceBaseBranchCleanupResource.handler_key: AcceptanceBaseBranchCleanupHandler(config),
            WorkflowInfrastructureDevelopmentEnvironmentCleanupResource.handler_key: (
                WorkflowInfrastructureDevelopmentEnvironmentCleanupHandler(config, runner=runner)
            ),
        }

    def reconcile(self, resource: CleanupResource, *, delete: bool) -> CleanupResourceReadback:
        """Dispatch one typed resource only through its fixed provider handler."""

        handler = self._handler_by_key_map.get(resource.handler_key)
        if handler is None or not isinstance(resource, handler.resource_type):
            raise TaskCleanupError("Cleanup resource handler is absent from the provider registry")
        return handler.reconcile(resource, delete=delete)
