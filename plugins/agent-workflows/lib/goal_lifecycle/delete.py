"""Explicit resumable resources-to-artifacts goal deletion workflow."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess

from goal_lifecycle.bootstrap_exception import (
    CoordinationBootstrapException,
    coordination_bootstrap_exception_optional_get,
    coordination_bootstrap_exception_path_get,
    coordination_bootstrap_exception_validate,
)
from goal_lifecycle.cleanup_manifest import (
    BOOTSTRAP_MANIFEST_NAME,
    bootstrap_manifest_load,
    cleanup_binding_receipt_validate,
)
from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_bytes_write, atomic_json_write, directory_sync, json_object_load
from goal_lifecycle.model import CheckpointDocument, TaskState, common_prefix_validate
from goal_lifecycle.yaml_document import yaml_document_load


class GoalDeletionWorkflow:
    """Delete one exact accepted task through a durable ordered journal."""

    def __init__(self, goals_repository: Path, *, git: Git | None = None) -> None:
        self._git = git or Git()
        self._coordination = CoordinationRepository(goals_repository, git=self._git)

    def delete(self, *, common_prefix: str, unfinished_goal_absent: bool) -> dict[str, object]:
        common_prefix_validate(common_prefix)
        if not unfinished_goal_absent:
            raise GoalLifecycleError("Explicit current-harness proof of no unfinished bound goal is required")
        with self._coordination.task_lock(common_prefix):
            state_path = self._coordination.state_path_get(common_prefix)
            journal_path = self._coordination.journal_path_get(common_prefix, "delete")
            if journal_path.exists():
                journal = json_object_load(journal_path, label="goal deletion journal")
                state = TaskState.from_payload(journal.get("task_state"))
                if (
                    state_path.exists()
                    and TaskState.from_payload(json_object_load(state_path, label="task private state")) != state
                ):
                    raise GoalLifecycleError("Goal deletion journal and private task state differ")
                self._journal_validate(journal, state=state)
            else:
                state = TaskState.from_payload(json_object_load(state_path, label="task private state"))
                project_list = self._preconditions_validate(state)
                journal = {
                    "schema_version": 1,
                    "common_prefix": common_prefix,
                    "operation_identity": secrets.token_hex(16),
                    "phase": "external-resources",
                    "coordination_bootstrap_exception": self._bootstrap_exception_payload_get(state),
                    "project_list": project_list,
                    "repository_index": 0,
                    "task_state": state.payload_get(),
                }
                atomic_json_write(journal_path, journal)
            self._resume(state=state, journal=journal, journal_path=journal_path)
            return journal

    def _preconditions_validate(self, state: TaskState) -> list[dict[str, str]]:
        self._coordination.synchronize_require()
        checkpoint_path = self._coordination.task_directory_get(state.common_prefix) / "checkpoint.yaml"
        task_directory_entry_set = {item.name for item in checkpoint_path.parent.iterdir()}
        if task_directory_entry_set != {"checkpoint.yaml", "goal.md", "spec.md"}:
            raise GoalLifecycleError("Goal deletion requires one closed three-file task directory")
        document = CheckpointDocument.from_payload(yaml_document_load(checkpoint_path))
        if (
            not document.checkpoint_list
            or document.accepted_checkpoint_id != document.checkpoint_list[-1].checkpoint_id
        ):
            raise GoalLifecycleError("Goal deletion requires the latest full checkpoint to be accepted")
        accepted = document.checkpoint_list[-1]
        accepted_by_path_map = {item.project_path: item.git_commit_final for item in accepted.project_list}
        workspace_root = self._coordination.root.parent.resolve(strict=True)
        project_list: list[dict[str, str]] = []
        for repository in state.repository_list:
            main_root = Path(repository.main_root).resolve(strict=True)
            task_root = Path(repository.task_root)
            project_path = main_root.relative_to(workspace_root).as_posix()
            if accepted_by_path_map.get(project_path) is None:
                raise GoalLifecycleError(f"Accepted checkpoint omits task repository: {project_path}")
            expected_commit = accepted_by_path_map[project_path]
            self._git.clean_require(main_root)
            self._git.fetch(main_root)
            if self._git.branch_get(main_root) != "main":
                raise GoalLifecycleError(f"Goal deletion requires canonical main checkout: {main_root}")
            main_commit = self._git.commit_get(main_root)
            if main_commit != self._git.commit_get(main_root, "refs/remotes/origin/main"):
                raise GoalLifecycleError(f"Local and remote main differ: {main_root}")
            self._git.ancestor_require(
                main_root,
                accepted_by_path_map[project_path],
                main_commit,
                label=f"{project_path} accepted ancestry",
            )
            if task_root.exists():
                self._git.clean_require(task_root)
                if self._git.branch_get(task_root) != state.common_prefix:
                    raise GoalLifecycleError(f"Task worktree branch differs: {task_root}")
                task_commit = self._git.commit_get(task_root)
                if task_commit != expected_commit or task_commit != self._git.commit_get(
                    task_root,
                    f"refs/remotes/origin/{state.common_prefix}",
                ):
                    raise GoalLifecycleError(f"Task branch is not fully pushed: {task_root}")
                self._git.ancestor_require(task_root, task_commit, main_commit, label=f"{project_path} merged ancestry")
            else:
                raise GoalLifecycleError(f"Task worktree is absent before deletion was journaled: {task_root}")
            local_task_ref = f"refs/heads/{state.common_prefix}"
            remote_task_ref = f"refs/remotes/origin/{state.common_prefix}"
            for ref, label in (
                (local_task_ref, "local"),
                (remote_task_ref, "remote"),
            ):
                if self._git.run(main_root, ["show-ref", "--verify", ref], check=False).returncode != 0:
                    raise GoalLifecycleError(f"{label.capitalize()} task ref is absent before deletion: {main_root}")
                if self._git.commit_get(main_root, ref) != expected_commit:
                    raise GoalLifecycleError(f"{label.capitalize()} task ref changed before deletion: {main_root}")
            project_list.append(
                {
                    "git_commit_final": expected_commit,
                    "main_root": str(main_root),
                    "project_path": project_path,
                    "task_root": str(task_root),
                }
            )
        if set(accepted_by_path_map) != {item["project_path"] for item in project_list}:
            raise GoalLifecycleError("Accepted checkpoint and sealed participant set differ")
        return sorted(project_list, key=lambda item: item["project_path"])

    def _bootstrap_exception_payload_get(self, state: TaskState) -> dict[str, object] | None:
        exception = coordination_bootstrap_exception_optional_get(self._coordination.root, git=self._git)
        if exception is None:
            return None
        if exception.common_prefix != state.common_prefix:
            raise GoalLifecycleError("Another task owns the coordination bootstrap exception")
        coordination_bootstrap_exception_validate(self._coordination.root, exception, git=self._git)
        return exception.payload_get()

    def _resume(self, *, state: TaskState, journal: dict[str, object], journal_path: Path) -> None:
        phase = journal["phase"]
        if phase == "external-resources":
            self._external_cleanup_resume(state=state, journal=journal, journal_path=journal_path)
            phase = journal["phase"]
        if phase == "worktrees":
            for repository in state.repository_list:
                main_root = Path(repository.main_root)
                task_root = Path(repository.task_root)
                if task_root.exists():
                    self._git.run(main_root, ["worktree", "remove", str(task_root)])
            bootstrap_exception = self._bootstrap_exception_get(journal)
            if bootstrap_exception is not None:
                task_root = Path(bootstrap_exception.task_root)
                if task_root.exists():
                    self._git.run(self._coordination.root, ["worktree", "remove", str(task_root)])
            journal.update({"phase": "remote-refs", "repository_index": 0})
            atomic_json_write(journal_path, journal)
            phase = "remote-refs"
        if phase == "remote-refs":
            expected_by_main_root_map = {
                item["main_root"]: item["git_commit_final"] for item in journal["project_list"]
            }
            for index, repository in enumerate(
                state.repository_list[int(journal["repository_index"]) :], start=int(journal["repository_index"])
            ):
                main_root = Path(repository.main_root)
                self._git.fetch(main_root)
                remote_ref = f"refs/remotes/origin/{state.common_prefix}"
                exists = self._git.run(main_root, ["show-ref", "--verify", remote_ref], check=False).returncode == 0
                if exists:
                    if (
                        self._git.commit_get(main_root, remote_ref)
                        != expected_by_main_root_map[str(main_root.resolve(strict=True))]
                    ):
                        raise GoalLifecycleError(f"Remote task ref changed after deletion authorization: {main_root}")
                    self._git.run(main_root, ["push", "origin", f":refs/heads/{state.common_prefix}"])
                journal["repository_index"] = index + 1
                atomic_json_write(journal_path, journal)
            bootstrap_exception = self._bootstrap_exception_get(journal)
            if bootstrap_exception is not None:
                remote_ref = f"refs/remotes/origin/{bootstrap_exception.branch_name}"
                exists = (
                    self._git.run(
                        self._coordination.root,
                        ["show-ref", "--verify", remote_ref],
                        check=False,
                    ).returncode
                    == 0
                )
                if exists:
                    if (
                        self._git.commit_get(self._coordination.root, remote_ref)
                        != bootstrap_exception.coordination_bootstrap_commit
                    ):
                        raise GoalLifecycleError(
                            "Coordination bootstrap remote branch changed after deletion authorization"
                        )
                    self._git.run(
                        self._coordination.root,
                        ["push", "origin", f":refs/heads/{bootstrap_exception.branch_name}"],
                    )
            journal.update({"phase": "local-refs", "repository_index": 0})
            atomic_json_write(journal_path, journal)
            phase = "local-refs"
        if phase == "local-refs":
            expected_by_main_root_map = {
                item["main_root"]: item["git_commit_final"] for item in journal["project_list"]
            }
            for index, repository in enumerate(
                state.repository_list[int(journal["repository_index"]) :], start=int(journal["repository_index"])
            ):
                main_root = Path(repository.main_root)
                local_ref = f"refs/heads/{state.common_prefix}"
                if self._git.run(main_root, ["show-ref", "--verify", local_ref], check=False).returncode == 0:
                    if (
                        self._git.commit_get(main_root, local_ref)
                        != expected_by_main_root_map[str(main_root.resolve(strict=True))]
                    ):
                        raise GoalLifecycleError(f"Local task ref changed after deletion authorization: {main_root}")
                    self._git.run(main_root, ["branch", "-d", state.common_prefix])
                journal["repository_index"] = index + 1
                atomic_json_write(journal_path, journal)
            bootstrap_exception = self._bootstrap_exception_get(journal)
            if bootstrap_exception is not None:
                local_ref = f"refs/heads/{bootstrap_exception.branch_name}"
                if (
                    self._git.run(self._coordination.root, ["show-ref", "--verify", local_ref], check=False).returncode
                    == 0
                ):
                    if (
                        self._git.commit_get(self._coordination.root, local_ref)
                        != bootstrap_exception.coordination_bootstrap_commit
                    ):
                        raise GoalLifecycleError(
                            "Coordination bootstrap local branch changed after deletion authorization"
                        )
                    self._git.run(self._coordination.root, ["branch", "-d", bootstrap_exception.branch_name])
            journal.update({"phase": "provider-excludes", "repository_index": 0})
            atomic_json_write(journal_path, journal)
            phase = "provider-excludes"
        if phase == "provider-excludes":
            for repository in state.repository_list:
                self._provider_exclude_retire(Path(repository.main_root))
            journal.update({"phase": "bootstrap-carriers", "repository_index": len(state.repository_list)})
            atomic_json_write(journal_path, journal)
            phase = "bootstrap-carriers"
        if phase == "bootstrap-carriers":
            bootstrap_exception = self._bootstrap_exception_get(journal)
            if bootstrap_exception is not None:
                for path, expected_sha256 in (
                    (
                        Path(bootstrap_exception.specification_carrier_path),
                        bootstrap_exception.sealed_specification_sha256,
                    ),
                    (Path(bootstrap_exception.goal_carrier_path), bootstrap_exception.sealed_goal_sha256),
                ):
                    if path.exists():
                        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                            raise GoalLifecycleError(f"Bootstrap carrier identity changed: {path}")
                        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
                            raise GoalLifecycleError(f"Bootstrap carrier content changed: {path}")
                        path.unlink()
                        directory_sync(path.parent)
            journal.update({"phase": "coordination-bootstrap-retire"})
            atomic_json_write(journal_path, journal)
            phase = "coordination-bootstrap-retire"
        if phase == "coordination-bootstrap-retire":
            bootstrap_exception = self._bootstrap_exception_get(journal)
            if bootstrap_exception is not None:
                marker_path = coordination_bootstrap_exception_path_get(self._coordination.root, git=self._git)
                if marker_path.exists():
                    current = CoordinationBootstrapException.from_payload(
                        json_object_load(marker_path, label="coordination bootstrap exception")
                    )
                    if current != bootstrap_exception:
                        raise GoalLifecycleError("Coordination bootstrap exception changed during deletion")
                    marker_path.unlink()
                    directory_sync(marker_path.parent)
                worktree_container = self._coordination.root / ".worktree"
                if worktree_container.exists():
                    try:
                        worktree_container.rmdir()
                    except OSError as error:
                        raise GoalLifecycleError("Coordination worktree container is not empty") from error
                    directory_sync(worktree_container.parent)
                self._temporary_worktree_exclude_remove(self._coordination.root)
            journal.update({"phase": "coordination-delete"})
            atomic_json_write(journal_path, journal)
            phase = "coordination-delete"
        if phase == "coordination-delete":
            self._coordination.publish(
                common_prefix=state.common_prefix,
                message=f"Delete completed task {state.common_prefix}",
                relative_payload_by_path_map={
                    f"{state.common_prefix}/checkpoint.yaml": None,
                    f"{state.common_prefix}/goal.md": None,
                    f"{state.common_prefix}/spec.md": None,
                },
                task_lock_already_held=True,
            )
            journal["phase"] = "complete"
            atomic_json_write(journal_path, journal)
            phase = "complete"
        if phase == "complete":
            for repository in state.repository_list:
                receipt_path = (
                    self._git.common_directory_get(Path(repository.main_root))
                    / "agent-workflows"
                    / "cleanup-binding"
                    / f"{state.common_prefix}.json"
                )
                try:
                    receipt_path.unlink()
                except FileNotFoundError:
                    pass
                replica_path = (
                    self._git.common_directory_get(Path(repository.main_root))
                    / "agent-workflows"
                    / "task"
                    / state.common_prefix
                    / "state.json"
                )
                try:
                    replica_path.unlink()
                except FileNotFoundError:
                    pass
            try:
                self._coordination.state_path_get(state.common_prefix).unlink()
            except FileNotFoundError:
                pass
            journal_path.unlink()
            directory_sync(journal_path.parent)

    def _external_cleanup_resume(
        self,
        *,
        state: TaskState,
        journal: dict[str, object],
        journal_path: Path,
    ) -> None:
        start_index = int(journal["repository_index"])
        for index, repository in enumerate(state.repository_list[start_index:], start=start_index):
            main_root = Path(repository.main_root)
            manifest = bootstrap_manifest_load(main_root / BOOTSTRAP_MANIFEST_NAME)
            if manifest.sha256 != repository.manifest_sha256:
                raise GoalLifecycleError(f"Merged cleanup manifest differs from sealed binding: {main_root}")
            if manifest.cleanup is not None:
                cleanup_binding_receipt_validate(
                    main_root,
                    common_prefix=state.common_prefix,
                    provider_state_generation=state.cleanup_binding_generation,
                    sealed_specification_sha256=state.sealed_spec_sha256,
                    git=self._git,
                )
                command = manifest.cleanup.command_get(common_prefix=state.common_prefix)
                request = {
                    "schema_version": 1,
                    "common_prefix": state.common_prefix,
                    "operation_identity": journal["operation_identity"],
                }
                environment = {
                    "HOME": os.environ.get("HOME", ""),
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
                result = subprocess.run(
                    command,
                    cwd=main_root,
                    env=environment,
                    input=(json.dumps(request, separators=(",", ":"), sort_keys=True) + "\n").encode(),
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
                    raise GoalLifecycleError(f"External cleanup hook failed for {main_root}: {diagnostic}")
                try:
                    response = json.loads(result.stdout)
                except json.JSONDecodeError as error:
                    raise GoalLifecycleError(f"External cleanup hook returned invalid JSON: {main_root}") from error
                if response != {
                    **request,
                    "external_resources_absent": True,
                }:
                    raise GoalLifecycleError(f"External cleanup hook did not prove exact absence: {main_root}")
            journal["repository_index"] = index + 1
            atomic_json_write(journal_path, journal)
        journal.update({"phase": "worktrees", "repository_index": 0})
        atomic_json_write(journal_path, journal)

    @staticmethod
    def _journal_validate(journal: dict[str, object], *, state: TaskState) -> None:
        if (
            set(journal)
            != {
                "schema_version",
                "common_prefix",
                "operation_identity",
                "phase",
                "coordination_bootstrap_exception",
                "project_list",
                "repository_index",
                "task_state",
            }
            or journal.get("schema_version") != 1
            or journal.get("common_prefix") != state.common_prefix
        ):
            raise GoalLifecycleError("Goal deletion journal has another shape or task identity")
        if journal.get("phase") not in {
            "external-resources",
            "worktrees",
            "remote-refs",
            "local-refs",
            "provider-excludes",
            "bootstrap-carriers",
            "coordination-bootstrap-retire",
            "coordination-delete",
            "complete",
        }:
            raise GoalLifecycleError("Goal deletion journal phase is unsupported")
        operation_identity = journal.get("operation_identity")
        repository_index = journal.get("repository_index")
        project_list = journal.get("project_list")
        if (
            not isinstance(operation_identity, str)
            or len(operation_identity) != 32
            or not isinstance(repository_index, int)
            or isinstance(repository_index, bool)
            or not 0 <= repository_index <= len(state.repository_list)
        ):
            raise GoalLifecycleError("Goal deletion journal identity or position is malformed")
        expected_main_root_set = {str(Path(item.main_root).resolve(strict=True)) for item in state.repository_list}
        if (
            not isinstance(project_list, list)
            or len(project_list) != len(state.repository_list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"git_commit_final", "main_root", "project_path", "task_root"}
                for item in project_list
            )
            or {str(item["main_root"]) for item in project_list} != expected_main_root_set
        ):
            raise GoalLifecycleError("Goal deletion journal project snapshot is malformed")
        if TaskState.from_payload(journal.get("task_state")) != state:
            raise GoalLifecycleError("Goal deletion journal task-state snapshot differs")

        bootstrap_payload = journal.get("coordination_bootstrap_exception")
        if bootstrap_payload is not None:
            exception = CoordinationBootstrapException.from_payload(bootstrap_payload)
            if exception.common_prefix != state.common_prefix:
                raise GoalLifecycleError("Goal deletion bootstrap exception belongs to another task")

    @staticmethod
    def _bootstrap_exception_get(journal: dict[str, object]) -> CoordinationBootstrapException | None:
        payload = journal.get("coordination_bootstrap_exception")
        return None if payload is None else CoordinationBootstrapException.from_payload(payload)

    def _provider_exclude_retire(self, main_root: Path) -> None:
        """Remove only the provider-owned temporary worktree ignore after merge."""

        gitignore_path = main_root / ".gitignore"
        if not gitignore_path.is_file() or "/.worktree/" not in gitignore_path.read_text(encoding="utf-8").splitlines():
            return
        self._temporary_worktree_exclude_remove(main_root)

    def _temporary_worktree_exclude_remove(self, main_root: Path) -> None:
        """Remove one exact provider-owned common-directory exclude line."""

        exclude_path = self._git.common_directory_get(main_root) / "info" / "exclude"
        if not exclude_path.is_file():
            return
        line_list = exclude_path.read_text(encoding="utf-8").splitlines()
        if "/.worktree/" not in line_list:
            return
        remaining_line_list = [line for line in line_list if line != "/.worktree/"]
        payload = (("\n".join(remaining_line_list) + "\n") if remaining_line_list else "").encode()
        atomic_bytes_write(exclude_path, payload, mode=0o644)
