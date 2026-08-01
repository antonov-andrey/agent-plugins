"""Exclusive resumable one-checkpoint fast-forward merge workflow."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from goal_lifecycle.coordination import CoordinationRepository
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write, directory_sync, json_object_load
from goal_lifecycle.model import Checkpoint, CheckpointDocument, common_prefix_validate, workspace_repository_resolve
from goal_lifecycle.yaml_document import yaml_document_bytes_get, yaml_document_load


class GoalMergeWorkflow:
    """Fast-forward one complete checkpoint and publish acceptance only after proof."""

    def __init__(self, goals_repository: Path, *, git: Git | None = None) -> None:
        self._git = git or Git()
        self._coordination = CoordinationRepository(goals_repository, git=self._git)

    def merge(self, *, common_prefix: str, checkpoint_id: str) -> dict[str, object]:
        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix), self._coordination.merge_lock():
            document, checkpoint = self._checkpoint_get(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
            self._selection_validate(document=document, checkpoint=checkpoint)
            journal_path = self._coordination.journal_path_get(common_prefix, "merge")
            if journal_path.exists():
                journal = json_object_load(journal_path, label="goal merge journal")
                try:
                    self._journal_validate(journal, common_prefix=common_prefix, checkpoint=checkpoint)
                except GoalLifecycleError:
                    journal = self._journal_supersede(
                        journal=journal,
                        common_prefix=common_prefix,
                        checkpoint=checkpoint,
                    )
                    self._preflight(checkpoint, common_prefix=common_prefix)
                    self._merge_owner_require(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
                    atomic_json_write(journal_path, journal)
                    self._merge_owner_update(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
            else:
                journal = {
                    "schema_version": 1,
                    "common_prefix": common_prefix,
                    "checkpoint_id": checkpoint_id,
                    "phase": "merging",
                    "project_list": [{**asdict(project), "merged": False} for project in checkpoint.project_list],
                }
                self._preflight(checkpoint, common_prefix=common_prefix)
                self._merge_owner_require(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
                atomic_json_write(journal_path, journal)
            self._merge_owner_require(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
            self._merge_resume(journal=journal, journal_path=journal_path)
            journal["phase"] = "awaiting-acceptance"
            atomic_json_write(journal_path, journal)
            return journal

    def accept(self, *, common_prefix: str, checkpoint_id: str) -> str:
        """Publish accepted pointer after the caller completed exact primary acceptance."""

        common_prefix_validate(common_prefix)
        with self._coordination.task_lock(common_prefix), self._coordination.merge_lock():
            document, checkpoint = self._checkpoint_get(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
            journal_path = self._coordination.journal_path_get(common_prefix, "merge")
            journal = json_object_load(journal_path, label="goal merge journal")
            self._journal_validate(journal, common_prefix=common_prefix, checkpoint=checkpoint)
            if journal.get("phase") not in {"awaiting-acceptance", "accepted"}:
                raise GoalLifecycleError("Checkpoint cannot be accepted before every merge completes")
            self._merged_exact_require(checkpoint)
            if document.accepted_checkpoint_id == checkpoint_id:
                commit = self._coordination.synchronize_require()
            else:
                accepted_index = (
                    next(
                        index
                        for index, item in enumerate(document.checkpoint_list)
                        if item.checkpoint_id == document.accepted_checkpoint_id
                    )
                    if document.accepted_checkpoint_id
                    else -1
                )
                if document.checkpoint_list.index(checkpoint) <= accepted_index:
                    raise GoalLifecycleError("Accepted pointer already passed the selected checkpoint")
                updated = CheckpointDocument(
                    accepted_checkpoint_id=checkpoint_id,
                    checkpoint_list=document.checkpoint_list,
                )
                commit = self._coordination.publish(
                    common_prefix=common_prefix,
                    message=f"Accept {common_prefix} {checkpoint_id}",
                    relative_payload_by_path_map={
                        f"{common_prefix}/checkpoint.yaml": yaml_document_bytes_get(updated.payload_get())
                    },
                    task_lock_already_held=True,
                )
            journal["phase"] = "accepted"
            atomic_json_write(journal_path, journal)
            owner_path = self._coordination.merge_owner_path_get()
            if owner_path.exists():
                self._merge_owner_require(common_prefix=common_prefix, checkpoint_id=checkpoint_id)
                owner_path.unlink()
                directory_sync(owner_path.parent)
            try:
                journal_path.unlink()
            except FileNotFoundError:
                pass
            directory_sync(journal_path.parent)
            return commit

    def _checkpoint_get(self, *, common_prefix: str, checkpoint_id: str) -> tuple[CheckpointDocument, Checkpoint]:
        document = CheckpointDocument.from_payload(
            yaml_document_load(self._coordination.task_directory_get(common_prefix) / "checkpoint.yaml")
        )
        for checkpoint in document.checkpoint_list:
            if checkpoint.checkpoint_id == checkpoint_id:
                return document, checkpoint
        raise GoalLifecycleError(f"Checkpoint does not exist: {checkpoint_id}")

    @staticmethod
    def _selection_validate(*, document: CheckpointDocument, checkpoint: Checkpoint) -> None:
        if document.accepted_checkpoint_id == checkpoint.checkpoint_id:
            raise GoalLifecycleError("Checkpoint is already accepted")
        accepted_index = (
            next(
                index
                for index, item in enumerate(document.checkpoint_list)
                if item.checkpoint_id == document.accepted_checkpoint_id
            )
            if document.accepted_checkpoint_id
            else -1
        )
        selected_index = document.checkpoint_list.index(checkpoint)
        if selected_index <= accepted_index:
            raise GoalLifecycleError("Selected checkpoint precedes the accepted pointer")
        # A later fix-forward snapshot is allowed because checkpoint publication already
        # proves ancestry against every prior snapshot for each project.

    def _preflight(self, checkpoint: Checkpoint, *, common_prefix: str) -> None:
        workspace_root = self._coordination.root.parent
        for project in checkpoint.project_list:
            root = workspace_repository_resolve(workspace_root, project.project_path)
            self._git.clean_require(root)
            if self._git.branch_get(root) != "main":
                raise GoalLifecycleError(f"Merge requires canonical main checkout: {project.project_path}")
            self._git.fetch(root)
            local = self._git.commit_get(root)
            remote = self._git.commit_get(root, "refs/remotes/origin/main")
            if local != remote:
                raise GoalLifecycleError(f"Local and origin main differ before merge: {project.project_path}")
            target = self._git.commit_get(root, project.git_commit_final)
            if target != project.git_commit_final:
                raise GoalLifecycleError(f"Checkpoint commit is unavailable: {project.project_path}")
            self._git.ancestor_require(root, remote, target, label=f"{project.project_path} fast-forward preflight")
            remote_task = self._git.commit_get(root, f"refs/remotes/origin/{common_prefix}")
            if remote_task != target:
                raise GoalLifecycleError(
                    f"Checkpoint target is no longer the exact published task ref: {project.project_path}"
                )

    def _merge_resume(self, *, journal: dict[str, object], journal_path: Path) -> None:
        workspace_root = self._coordination.root.parent
        project_payload_list = journal["project_list"]
        if not isinstance(project_payload_list, list):
            raise GoalLifecycleError("Goal merge journal project_list is malformed")
        for project in project_payload_list:
            if not isinstance(project, dict):
                raise GoalLifecycleError("Goal merge journal project entry is malformed")
            root = workspace_repository_resolve(workspace_root, str(project["project_path"]))
            target = str(project["git_commit_final"])
            self._git.clean_require(root)
            if self._git.branch_get(root) != "main":
                raise GoalLifecycleError(f"Merge resume requires canonical main checkout: {root}")
            self._git.fetch(root)
            local = self._git.commit_get(root)
            remote = self._git.commit_get(root, "refs/remotes/origin/main")
            if remote == target:
                if local != target:
                    self._git.ancestor_require(root, local, target, label=f"{root.name} local merge recovery")
                    self._git.run(root, ["merge", "--ff-only", target])
            elif local == remote:
                self._git.ancestor_require(root, remote, target, label=f"{root.name} merge resume")
                result = self._git.run(root, ["push", "origin", f"{target}:refs/heads/main"], check=False)
                if result.returncode != 0:
                    raise GoalLifecycleError(f"Concurrent remote main update interrupted merge: {root}")
                self._git.run(root, ["merge", "--ff-only", target])
            else:
                raise GoalLifecycleError(f"Local and origin main diverged during merge: {root}")
            if self._git.commit_get(root) != target:
                raise GoalLifecycleError(f"Local main did not reach checkpoint target during merge: {root}")
            project["merged"] = True
            atomic_json_write(journal_path, journal)

    def _merged_exact_require(self, checkpoint: Checkpoint) -> None:
        workspace_root = self._coordination.root.parent
        for project in checkpoint.project_list:
            root = workspace_repository_resolve(workspace_root, project.project_path)
            self._git.clean_require(root)
            self._git.fetch(root)
            if (
                self._git.branch_get(root) != "main"
                or self._git.commit_get(root) != project.git_commit_final
                or self._git.commit_get(root, "refs/remotes/origin/main") != project.git_commit_final
            ):
                raise GoalLifecycleError(f"Merged main is not the exact selected checkpoint: {project.project_path}")

    @staticmethod
    def _journal_validate(journal: dict[str, object], *, common_prefix: str, checkpoint: Checkpoint) -> None:
        if (
            set(journal)
            != {
                "schema_version",
                "common_prefix",
                "checkpoint_id",
                "phase",
                "project_list",
            }
            or journal.get("schema_version") != 1
            or journal.get("common_prefix") != common_prefix
        ):
            raise GoalLifecycleError("Goal merge journal has another identity or shape")
        if journal.get("checkpoint_id") != checkpoint.checkpoint_id:
            raise GoalLifecycleError("Another checkpoint already owns the resumable merge journal")
        if journal.get("phase") not in {"merging", "awaiting-acceptance", "accepted"}:
            raise GoalLifecycleError("Goal merge journal phase is unsupported")
        expected = [
            {**asdict(project), "merged": item.get("merged")}
            for project, item in zip(checkpoint.project_list, journal.get("project_list", []), strict=False)
            if isinstance(item, dict)
        ]
        if len(expected) != len(checkpoint.project_list) or any(
            item["merged"] not in {True, False} for item in expected
        ):
            raise GoalLifecycleError("Goal merge journal project snapshot is malformed")
        for expected_item, actual_item in zip(expected, journal["project_list"], strict=True):
            if expected_item != actual_item:
                raise GoalLifecycleError("Goal merge journal differs from the selected checkpoint")

    def _journal_supersede(
        self,
        *,
        journal: dict[str, object],
        common_prefix: str,
        checkpoint: Checkpoint,
    ) -> dict[str, object]:
        """Replace only one failed awaiting-acceptance snapshot with a full fix-forward."""

        if (
            journal.get("schema_version") != 1
            or journal.get("common_prefix") != common_prefix
            or journal.get("phase") not in {"merging", "awaiting-acceptance"}
            or not isinstance(journal.get("project_list"), list)
        ):
            raise GoalLifecycleError("Existing merge journal cannot be superseded")
        if journal.get("checkpoint_id") == checkpoint.checkpoint_id:
            raise GoalLifecycleError("Malformed merge journal cannot supersede itself")
        previous_by_path_map: dict[str, str] = {}
        for raw_project in journal["project_list"]:
            if (
                not isinstance(raw_project, dict)
                or set(raw_project)
                != {
                    "project_path",
                    "git_commit_final",
                    "merged",
                }
                or raw_project.get("merged") not in {True, False}
            ):
                raise GoalLifecycleError("Existing merge journal is not one resumable failed checkpoint")
            previous_by_path_map[str(raw_project["project_path"])] = str(raw_project["git_commit_final"])
        if set(previous_by_path_map) != {item.project_path for item in checkpoint.project_list}:
            raise GoalLifecycleError("Fix-forward checkpoint changes the merge participant set")
        workspace_root = self._coordination.root.parent
        for project in checkpoint.project_list:
            root = workspace_repository_resolve(workspace_root, project.project_path)
            self._git.ancestor_require(
                root,
                previous_by_path_map[project.project_path],
                project.git_commit_final,
                label=f"{project.project_path} fix-forward ancestry",
            )
        return {
            "schema_version": 1,
            "common_prefix": common_prefix,
            "checkpoint_id": checkpoint.checkpoint_id,
            "phase": "merging",
            "project_list": [{**asdict(project), "merged": False} for project in checkpoint.project_list],
        }

    def _merge_owner_require(self, *, common_prefix: str, checkpoint_id: str) -> None:
        owner_path = self._coordination.merge_owner_path_get()
        expected = {
            "schema_version": 1,
            "common_prefix": common_prefix,
            "checkpoint_id": checkpoint_id,
        }
        if not owner_path.exists():
            atomic_json_write(owner_path, expected)
            return
        owner = json_object_load(owner_path, label="workspace merge owner")
        if owner == expected:
            return
        if isinstance(owner, dict) and owner.get("schema_version") == 1 and owner.get("common_prefix") == common_prefix:
            return
        raise GoalLifecycleError("Another goal owns the exclusive workspace merge lifecycle")

    def _merge_owner_update(self, *, common_prefix: str, checkpoint_id: str) -> None:
        atomic_json_write(
            self._coordination.merge_owner_path_get(),
            {
                "schema_version": 1,
                "common_prefix": common_prefix,
                "checkpoint_id": checkpoint_id,
            },
        )
