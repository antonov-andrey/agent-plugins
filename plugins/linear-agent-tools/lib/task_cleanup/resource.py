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

        repository = self._repository_ready(resource)
        operation = "destroy" if delete else "destroy-inventory"
        argument_list = [
            sys.executable,
            str(repository.main_root / "development_environment_manage.py"),
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
                cwd=repository.main_root,
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
        if payload["external_resources_absent"] != (not payload["resource_identity_list"]):
            raise TaskCleanupError("Development environment inventory contradicts its exact absence state")
        return CleanupResourceReadback(resource, "absent" if payload["external_resources_absent"] else "retained")

    def _repository_ready(
        self,
        resource: WorkflowInfrastructureDevelopmentEnvironmentCleanupResource,
    ) -> WorkspaceRepository:
        """Fast-forward one clean canonical main checkout before invoking Product code."""

        try:
            repository = WorkspaceRepository.from_config(
                self._config,
                RepositoryRequest(resource.repository, "main", ""),
            )
            repository.fetch()
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
            script = repository.main_root / "development_environment_manage.py"
            if script.is_symlink() or not script.is_file():
                raise TaskCleanupError("Workflow-infrastructure cleanup entrypoint is not one ordinary file")
            return repository
        except TaskWorkspaceError as error:
            raise TaskCleanupError("Workflow-infrastructure cleanup repository identity could not be proven") from error


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
