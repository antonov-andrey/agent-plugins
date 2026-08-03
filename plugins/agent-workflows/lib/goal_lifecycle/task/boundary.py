"""Manifest, resource, and integrity owner for one prepared repository boundary."""

from __future__ import annotations

from pathlib import Path

from goal_lifecycle.cleanup_manifest import (
    BOOTSTRAP_MANIFEST_NAME,
    BootstrapManifest,
    bootstrap_manifest_load,
    cleanup_binding_receipt_path_get,
    cleanup_binding_receipt_validate,
    cleanup_binding_receipt_write,
)
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_bytes_write
from goal_lifecycle.main_integrity import MainWorktreeIntegrity
from goal_lifecycle.resource import BootstrapResourceManager
from goal_lifecycle.task.model import RepositoryBoundaryState, TaskState
from goal_lifecycle.task.repair import TaskRepairReport
from goal_lifecycle.yaml_document import yaml_document_bytes_get

_EMPTY_MANIFEST_PAYLOAD = {
    "schema_version": 2,
    "resource": {
        "copy_optional_path_list": [],
        "copy_required_path_list": [],
        "link_optional_path_list": [],
        "link_required_path_list": [],
    },
}


class RepositoryBoundaryManager:
    """Prepare and prove one top-level or task-owned repository boundary."""

    def __init__(self, *, git: Git, repair_report: TaskRepairReport | None = None) -> None:
        """Initialize the repository boundary manager dependencies.

        Args:
            git: Git command boundary.
            repair_report: Repair report.
        """

        self._git = git
        self._repair_report = repair_report or TaskRepairReport()
        self._main_integrity = MainWorktreeIntegrity(git=git)
        self._resource_manager = BootstrapResourceManager(git=git, repair_report=self._repair_report)

    def prepare(
        self,
        *,
        main_root: Path,
        task_root: Path,
        baseline_commit: str,
        common_prefix: str,
        previous_state: RepositoryBoundaryState | None,
    ) -> RepositoryBoundaryState:
        """Materialize the exact current manifest and return its bound state.

        Args:
            main_root: Main root.
            task_root: Task root.
            baseline_commit: Baseline commit.
            common_prefix: Exact task common prefix.
            previous_state: Previous state.

        Returns:
            Resulting repository boundary state.
        """

        if self._git.root_get(main_root) != main_root.resolve(strict=True):
            raise GoalLifecycleError(f"Main repository boundary is not an exact Git root: {main_root}")
        if self._git.root_get(task_root) != task_root.resolve(strict=True):
            raise GoalLifecycleError(f"Task repository boundary is not an exact Git root: {task_root}")
        if self._git.branch_get(task_root) != common_prefix:
            raise GoalLifecycleError(f"Task repository boundary has another branch: {task_root}")
        if previous_state is not None:
            previous_state = self._main_integrity.refresh_if_independent(previous_state)
            baseline_commit = previous_state.baseline_commit
        manifest_path = task_root / BOOTSTRAP_MANIFEST_NAME
        if not manifest_path.exists():
            atomic_bytes_write(
                manifest_path,
                yaml_document_bytes_get(_EMPTY_MANIFEST_PAYLOAD),
                mode=0o644,
            )
            self._repair_report.record(f"bootstrap-manifest-created:{task_root}")
        manifest = bootstrap_manifest_load(manifest_path)
        resource_state_list = self._resource_manager.materialize(
            main_root=main_root,
            task_root=task_root,
            manifest=manifest,
            previous_state_list=(previous_state.resource_state_list if previous_state else ()),
        )
        _tracked_ignore_ensure(task_root, manifest=manifest)
        return RepositoryBoundaryState(
            accepted_main_commit_drift_list=(previous_state.accepted_main_commit_drift_list if previous_state else ()),
            baseline_commit=baseline_commit,
            branch_name=common_prefix,
            cleanup_declaration_sha256=(manifest.cleanup.normalized_sha256_get() if manifest.cleanup else ""),
            main_commit=(previous_state.main_commit if previous_state else self._git.commit_get(main_root)),
            main_root=str(main_root),
            manifest_sha256=manifest.sha256,
            origin_url=self._git.origin_url_get(main_root),
            resource_state_list=resource_state_list,
            task_root=str(task_root),
        )

    def validate(
        self,
        boundary: RepositoryBoundaryState,
        *,
        task_state: TaskState,
        main_integrity_required: bool = True,
    ) -> Path:
        """Require exact Git identity, main isolation, manifest, and resources.

        Args:
            boundary: Boundary.
            task_state: Task state.
            main_integrity_required: Main integrity required.

        Returns:
            Resolved filesystem path.
        """

        main_root = Path(boundary.main_root).resolve(strict=True)
        task_root = Path(boundary.task_root).resolve(strict=True)
        if self._git.root_get(main_root) != main_root or self._git.root_get(task_root) != task_root:
            raise GoalLifecycleError(f"Recorded repository boundary is not an exact Git root: {task_root}")
        if self._git.branch_get(task_root) != task_state.common_prefix:
            raise GoalLifecycleError(f"Recorded task repository boundary has another branch: {task_root}")
        if (
            self._git.origin_url_get(main_root) != boundary.origin_url
            or self._git.origin_url_get(task_root) != boundary.origin_url
        ):
            raise GoalLifecycleError(f"Repository origin changed: {task_root}")
        if main_integrity_required:
            self._main_integrity.validate(boundary)
        self._git.ancestor_require(
            task_root,
            boundary.baseline_commit,
            self._git.commit_get(task_root),
            label=f"{task_root.name} baseline relation",
        )
        manifest = bootstrap_manifest_load(task_root / BOOTSTRAP_MANIFEST_NAME)
        if manifest.sha256 != boundary.manifest_sha256:
            raise GoalLifecycleError(f"Bootstrap manifest changed after lifecycle binding: {task_root}")
        self._resource_manager.validate(
            main_root=main_root,
            task_root=task_root,
            state_list=boundary.resource_state_list,
        )
        if task_state.lifecycle_state == "active":
            cleanup_binding_receipt_validate(
                task_root,
                common_prefix=task_state.common_prefix,
                provider_state_generation=task_state.cleanup_binding_generation,
                sealed_specification_sha256=task_state.sealed_spec_sha256,
                git=self._git,
            )
        return task_root

    def existing_state_capture(
        self,
        *,
        main_root: Path,
        task_root: Path,
        baseline_commit: str,
        common_prefix: str,
    ) -> RepositoryBoundaryState:
        """Bind one already committed task-owned boundary without authoring it.

        Args:
            main_root: Main root.
            task_root: Task root.
            baseline_commit: Baseline commit.
            common_prefix: Exact task common prefix.

        Returns:
            Captured repository boundary state.
        """

        main_root = main_root.resolve(strict=True)
        task_root = task_root.resolve(strict=True)
        if self._git.root_get(main_root) != main_root or self._git.root_get(task_root) != task_root:
            raise GoalLifecycleError(f"Recovered repository boundary is not an exact Git root: {task_root}")
        if self._git.branch_get(task_root) != common_prefix:
            raise GoalLifecycleError(f"Recovered task repository boundary has another branch: {task_root}")
        if self._git.origin_url_get(main_root) != self._git.origin_url_get(task_root):
            raise GoalLifecycleError(f"Recovered repository boundary origins differ: {task_root}")
        self._git.clean_require(task_root)
        task_commit = self._git.commit_get(task_root)
        main_commit = self._git.commit_get(main_root)
        self._git.ancestor_require(
            task_root,
            baseline_commit,
            task_commit,
            label=f"{task_root.name} recovered task boundary",
        )
        self._git.ancestor_require(
            main_root,
            baseline_commit,
            main_commit,
            label=f"{main_root.name} recovered main boundary",
        )
        manifest_path = task_root / BOOTSTRAP_MANIFEST_NAME
        _tracked_file_require(task_root, BOOTSTRAP_MANIFEST_NAME, git=self._git)
        manifest = bootstrap_manifest_load(manifest_path)
        _tracked_ignore_validate(task_root, manifest=manifest, git=self._git)
        resource_state_list = self._resource_manager.existing_state_capture(
            main_root=main_root,
            task_root=task_root,
            manifest=manifest,
        )
        return RepositoryBoundaryState(
            baseline_commit=baseline_commit,
            branch_name=common_prefix,
            cleanup_declaration_sha256=(manifest.cleanup.normalized_sha256_get() if manifest.cleanup else ""),
            main_commit=main_commit,
            main_root=str(main_root),
            manifest_sha256=manifest.sha256,
            origin_url=self._git.origin_url_get(main_root),
            resource_state_list=resource_state_list,
            task_root=str(task_root),
        )

    def cleanup_binding_receipt_ensure(self, boundary: RepositoryBoundaryState, *, task_state: TaskState) -> None:
        """Mirror the content-free cleanup binding into task and main Git storage.

        Args:
            boundary: Boundary.
            task_state: Task state.
        """

        for storage_root in {Path(boundary.task_root), Path(boundary.main_root)}:
            cleanup_binding_receipt_write(
                Path(boundary.task_root),
                common_prefix=task_state.common_prefix,
                provider_state_generation=task_state.cleanup_binding_generation,
                sealed_specification_sha256=task_state.sealed_spec_sha256,
                git=self._git,
                storage_repository_root=storage_root,
            )

    def cleanup_binding_receipt_retire(self, boundary: RepositoryBoundaryState, *, common_prefix: str) -> None:
        """Retire the task's content-free cleanup receipt after external absence proof.

        Args:
            boundary: Boundary.
            common_prefix: Exact task common prefix.
        """

        for storage_root in {Path(boundary.task_root), Path(boundary.main_root)}:
            path = cleanup_binding_receipt_path_get(storage_root, common_prefix=common_prefix, git=self._git)
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @property
    def main_integrity(self) -> MainWorktreeIntegrity:
        """Expose the single repository-integrity owner for explicit user-authorized operations.

        Returns:
            Resulting main worktree integrity.
        """

        return self._main_integrity


def _tracked_ignore_ensure(task_root: Path, *, manifest: BootstrapManifest) -> None:
    """Author the durable ignore contract for provider-owned task-local paths.

    Args:
        task_root: Task root.
        manifest: Manifest.
    """

    gitignore_path = task_root / ".gitignore"
    if gitignore_path.is_symlink() or (gitignore_path.exists() and not gitignore_path.is_file()):
        raise GoalLifecycleError(f"Tracked ignore owner must be one ordinary file: {gitignore_path}")
    text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.is_file() else ""
    line_list = text.splitlines()
    required_line_list = [
        "/.worktree/",
        *(f"/{path}" for values in manifest.resource_by_key_map.values() for path in values),
    ]
    changed = False
    for line in required_line_list:
        if line not in line_list:
            line_list.append(line)
            changed = True
    if changed:
        atomic_bytes_write(gitignore_path, ("\n".join(line_list).strip() + "\n").encode(), mode=0o644)


def _tracked_ignore_validate(task_root: Path, *, manifest: BootstrapManifest, git: Git) -> None:
    """Require the committed ignore contract without changing task source.

    Args:
        task_root: Task root.
        manifest: Manifest.
        git: Git command boundary.
    """

    _tracked_file_require(task_root, ".gitignore", git=git)
    gitignore_path = task_root / ".gitignore"
    if gitignore_path.is_symlink() or not gitignore_path.is_file():
        raise GoalLifecycleError(f"Tracked ignore owner must be one ordinary file: {gitignore_path}")
    line_set = set(gitignore_path.read_text(encoding="utf-8").splitlines())
    required_line_set = {
        "/.worktree/",
        *(f"/{path}" for values in manifest.resource_by_key_map.values() for path in values),
    }
    missing_line_set = required_line_set - line_set
    if missing_line_set:
        raise GoalLifecycleError(
            f"Recovered task boundary lacks committed ignore rules in {gitignore_path}: "
            + ", ".join(sorted(missing_line_set))
        )


def _tracked_file_require(task_root: Path, path_text: str, *, git: Git) -> None:
    """Require one ordinary path to exist in the exact current commit.

    Args:
        task_root: Task root.
        path_text: Root-relative path text.
        git: Git command boundary.
    """

    if git.run(task_root, ["ls-files", "--error-unmatch", "--", path_text], check=False).returncode != 0:
        raise GoalLifecycleError(f"Recovered task boundary file is not committed: {task_root / path_text}")
