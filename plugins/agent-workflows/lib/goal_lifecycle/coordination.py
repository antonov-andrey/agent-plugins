"""Serialized compare-and-swap publication to canonical project-goals/main."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
import secrets
from typing import Mapping

from goal_lifecycle.bootstrap_exception import (
    coordination_bootstrap_exception_optional_get,
    coordination_bootstrap_exception_validate,
)
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write, directory_sync, json_object_load
from goal_lifecycle.lock import ExclusiveFileLock
from goal_lifecycle.identity import commit_validate, common_prefix_validate

TASK_ARTIFACT_NAME_SET = frozenset({"checkpoint.yaml", "goal.md", "spec.md"})


class CoordinationRepository:
    """Own short exact-path direct-main transactions in project-goals."""

    def __init__(self, root: Path, *, git: Git | None = None) -> None:
        """Initialize the coordination repository dependencies.

        Args:
            root: Exact owner root path.
            git: Git command boundary.
        """

        self._git = git or Git()
        self.root = self._git.root_get(root)
        if self.root.name != "project-goals":
            raise GoalLifecycleError("Coordination repository must be the canonical project-goals checkout")
        self._common_directory = self._git.common_directory_get(self.root)
        self._private_root = self._common_directory / "agent-workflows"

    def task_directory_get(self, common_prefix: str) -> Path:
        """Return the canonical tracked coordination directory for one task.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            The task directory.
        """

        common_prefix_validate(common_prefix)
        return self.root / common_prefix

    def task_lock(self, common_prefix: str) -> ExclusiveFileLock:
        """Return the exclusive coordination lock for one task directory.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            Resulting exclusive file lock.
        """

        common_prefix_validate(common_prefix)
        return ExclusiveFileLock(self._private_root / "lock" / f"task-{common_prefix}.lock")

    def merge_lock(self) -> ExclusiveFileLock:
        """Return the coordination-repository lock that serializes main acceptance.

        Returns:
            Resulting exclusive file lock.
        """

        return ExclusiveFileLock(self._private_root / "lock" / "workspace-merge.lock")

    def merge_owner_path_get(self) -> Path:
        """Return the durable owner marker that spans external primary acceptance.

        Returns:
            The durable owner marker that spans external primary acceptance.
        """

        return self._private_root / "merge-owner.json"

    def state_path_get(self, common_prefix: str) -> Path:
        """Return the private replicated-state path for one coordinated task.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            The state path.
        """

        common_prefix_validate(common_prefix)
        return self._private_root / "task" / common_prefix / "state.json"

    def journal_path_get(self, common_prefix: str, operation: str) -> Path:
        """Return the durable operation-journal path for one coordinated task.

        Args:
            common_prefix: Exact task common prefix.
            operation: Lifecycle operation name.

        Returns:
            The journal path.
        """

        common_prefix_validate(common_prefix)
        if not operation or any(character not in "abcdefghijklmnopqrstuvwxyz-" for character in operation):
            raise GoalLifecycleError("Coordination journal operation name is invalid")
        return self._private_root / "task" / common_prefix / f"{operation}-journal.json"

    def synchronize_require(self) -> str:
        """Require one clean canonical main checkout equal to fetched origin/main.

        Returns:
            Resulting text value.
        """

        if self._git.branch_get(self.root) != "main":
            raise GoalLifecycleError("project-goals canonical checkout must have main checked out")
        worktree_payload = self._git.run(self.root, ["worktree", "list", "--porcelain", "-z"]).stdout
        worktree_root_set = {
            Path(item.removeprefix(b"worktree ").decode("utf-8")).resolve(strict=True)
            for item in worktree_payload.split(b"\0")
            if item.startswith(b"worktree ")
        }
        bootstrap_exception = coordination_bootstrap_exception_optional_get(self.root, git=self._git)
        if bootstrap_exception is None:
            if worktree_root_set != {self.root}:
                raise GoalLifecycleError("project-goals must have exactly one canonical main worktree")
            if (self.root / ".worktree").exists():
                raise GoalLifecycleError("project-goals may not contain a worktree container")
        else:
            coordination_bootstrap_exception_validate(self.root, bootstrap_exception, git=self._git)
        if (self.root / "worktree-bootstrap.yaml").exists() or (self.root / "worktree-bootstrap.toml").exists():
            raise GoalLifecycleError("project-goals may not contain a bootstrap manifest")
        self._git.clean_require(self.root)
        self._git.fetch(self.root)
        local = self._git.commit_get(self.root)
        remote = self._git.commit_get(self.root, "refs/remotes/origin/main")
        if local != remote:
            raise GoalLifecycleError("project-goals local main must equal origin/main before coordination mutation")
        return local

    def file_bytes_get(self, common_prefix: str, name: str) -> bytes:
        """Read exact task-artifact bytes from the clean coordination checkout.

        Args:
            common_prefix: Exact task common prefix.
            name: Canonical name.

        Returns:
            The file bytes.
        """

        if name not in TASK_ARTIFACT_NAME_SET:
            raise GoalLifecycleError("Unknown task artifact name")
        path = self.task_directory_get(common_prefix) / name
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise GoalLifecycleError(f"Tracked task artifact is unavailable: {path}")
        return path.read_bytes()

    def task_directory_shape_require(self, common_prefix: str, *, complete: bool) -> set[str]:
        """Require one physical task directory with no unknown or non-file entries.

        Args:
            common_prefix: Exact task common prefix.
            complete: Complete.

        Returns:
            Unique values.
        """

        task_directory = self.task_directory_get(common_prefix)
        if task_directory.is_symlink() or not task_directory.is_dir():
            raise GoalLifecycleError(f"Tracked task directory is unavailable: {task_directory}")
        entry_by_name_map = {entry.name: entry for entry in task_directory.iterdir()}
        entry_name_set = set(entry_by_name_map)
        if "spec.md" not in entry_name_set or not entry_name_set <= TASK_ARTIFACT_NAME_SET:
            raise GoalLifecycleError("Tracked task directory has an invalid artifact set")
        if complete and entry_name_set != TASK_ARTIFACT_NAME_SET:
            raise GoalLifecycleError("Tracked task directory must contain exactly three task artifacts")
        for name in sorted(entry_name_set):
            path = entry_by_name_map[name]
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise GoalLifecycleError(f"Tracked task artifact is unavailable: {path}")
        return entry_name_set

    def publish(
        self,
        *,
        common_prefix: str,
        message: str,
        relative_payload_by_path_map: Mapping[str, bytes | None],
        task_lock_already_held: bool = False,
    ) -> str:
        """Publish only an exact closed task/owner path set to main without force.

        Args:
            common_prefix: Exact task common prefix.
            message: Message.
            relative_payload_by_path_map: Relative payload by path mapping.
            task_lock_already_held: Task lock already held.

        Returns:
            Resulting text value.
        """

        common_prefix_validate(common_prefix)
        if not relative_payload_by_path_map:
            raise GoalLifecycleError("Coordination publication has no path delta")
        path_list = sorted(relative_payload_by_path_map)
        _coordination_path_list_validate(path_list, common_prefix=common_prefix)
        task_lock = nullcontext() if task_lock_already_held else self.task_lock(common_prefix)
        with task_lock, ExclusiveFileLock(self._private_root / "lock" / "coordination-write.lock"):
            requested_sha256_by_name_map = {
                path: hashlib.sha256(payload).hexdigest() if payload is not None else ""
                for path, payload in sorted(relative_payload_by_path_map.items())
            }
            recovered = self._publication_recover(common_prefix=common_prefix)
            if recovered is not None and recovered[1] == requested_sha256_by_name_map:
                return recovered[0]
            base_commit = self.synchronize_require()
            for _ in range(4):
                commit = self._commit_build(
                    base_commit=base_commit,
                    message=message,
                    relative_payload_by_path_map=relative_payload_by_path_map,
                )
                if commit == base_commit:
                    return base_commit
                journal_path = self.journal_path_get(common_prefix, "coordination")
                atomic_json_write(
                    journal_path,
                    {
                        "schema_version": 1,
                        "base_commit": base_commit,
                        "commit": commit,
                        "path_sha256_by_name_map": requested_sha256_by_name_map,
                        "phase": "commit-built",
                    },
                )
                result = self._git.run(
                    self.root,
                    ["push", "origin", f"{commit}:refs/heads/main"],
                    check=False,
                )
                if result.returncode == 0:
                    self._git.run(self.root, ["merge", "--ff-only", commit])
                    self._git.clean_require(self.root)
                    if self._git.commit_get(self.root) != commit:
                        raise GoalLifecycleError("Coordination local main did not reach its published commit")
                    journal_path.unlink()
                    directory_sync(journal_path.parent)
                    return commit
                self._git.fetch(self.root)
                remote_commit = self._git.commit_get(self.root, "refs/remotes/origin/main")
                self._git.ancestor_require(
                    self.root,
                    base_commit,
                    remote_commit,
                    label="Concurrent project-goals main update",
                )
                changed = self._git.run(
                    self.root,
                    ["diff", "--quiet", base_commit, remote_commit, "--", *path_list],
                    check=False,
                )
                if changed.returncode != 0:
                    raise GoalLifecycleError("Concurrent project-goals update overlaps this exact path set")
                self._git.run(self.root, ["merge", "--ff-only", remote_commit])
                self._git.clean_require(self.root)
                journal_path.unlink()
                directory_sync(journal_path.parent)
                base_commit = remote_commit
            raise GoalLifecycleError("Coordination publication exceeded bounded disjoint-update retries")

    def _commit_build(
        self,
        *,
        base_commit: str,
        message: str,
        relative_payload_by_path_map: Mapping[str, bytes | None],
    ) -> str:
        """Create one detached coordination commit from a closed relative payload map.

        Args:
            base_commit: Base commit.
            message: Message.
            relative_payload_by_path_map: Relative payload by path mapping.

        Returns:
            The commit.
        """

        index_path = self._private_root / "staging" / f"index-{secrets.token_hex(16)}"
        index_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment = {"GIT_INDEX_FILE": str(index_path)}
        try:
            self._git.run(self.root, ["read-tree", base_commit], extra_environment=environment)
            for path, payload in sorted(relative_payload_by_path_map.items()):
                if payload is None:
                    self._git.run(
                        self.root,
                        ["update-index", "--force-remove", "--", path],
                        check=False,
                        extra_environment=environment,
                    )
                    continue
                blob = (
                    self._git.run(self.root, ["hash-object", "-w", "--stdin"], input_bytes=payload)
                    .stdout.decode()
                    .strip()
                )
                self._git.run(
                    self.root,
                    ["update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
                    extra_environment=environment,
                )
            tree = self._git.text_with_environment(self.root, ["write-tree"], extra_environment=environment)
            base_tree = self._git.text(self.root, ["show", "-s", "--format=%T", base_commit])
            if tree == base_tree:
                return base_commit
            commit = (
                self._git.run(
                    self.root,
                    ["commit-tree", tree, "-p", base_commit, "-F", "-"],
                    input_bytes=(message.strip() + "\n").encode(),
                )
                .stdout.decode()
                .strip()
            )
            return commit_validate(commit, label="coordination commit")
        finally:
            try:
                index_path.unlink()
            except FileNotFoundError:
                pass

    def _publication_recover(self, *, common_prefix: str) -> tuple[str, dict[str, str]] | None:
        """Recover an interrupted direct-main publication without duplicating its commit.

        Args:
            common_prefix: Exact task common prefix.

        Returns:
            Values in deterministic immutable order.
        """

        journal_path = self.journal_path_get(common_prefix, "coordination")
        if not journal_path.exists():
            return None
        payload = json_object_load(journal_path, label="coordination publication journal")
        if (
            set(payload)
            != {
                "schema_version",
                "base_commit",
                "commit",
                "path_sha256_by_name_map",
                "phase",
            }
            or payload.get("schema_version") != 1
            or payload.get("phase") != "commit-built"
        ):
            raise GoalLifecycleError("Coordination publication journal is malformed")
        base = commit_validate(payload["base_commit"], label="coordination base")
        commit = commit_validate(payload["commit"], label="coordination pending commit")
        path_sha256_by_name_map = payload["path_sha256_by_name_map"]
        if (
            not isinstance(path_sha256_by_name_map, dict)
            or not path_sha256_by_name_map
            or any(
                not isinstance(path, str)
                or not isinstance(digest, str)
                or (digest and (len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)))
                for path, digest in path_sha256_by_name_map.items()
            )
        ):
            raise GoalLifecycleError("Coordination publication journal path inventory is malformed")
        _coordination_path_list_validate(sorted(path_sha256_by_name_map), common_prefix=common_prefix)
        parent_list = self._git.text(self.root, ["show", "-s", "--format=%P", commit]).split()
        if parent_list != [base]:
            raise GoalLifecycleError("Coordination pending commit has another parent")
        changed_path_set = {
            item.decode("utf-8")
            for item in self._git.run(self.root, ["diff", "--name-only", "-z", base, commit]).stdout.split(b"\0")
            if item
        }
        if changed_path_set != set(path_sha256_by_name_map):
            raise GoalLifecycleError("Coordination pending commit differs from its recorded path set")
        for path, expected_sha256 in path_sha256_by_name_map.items():
            blob = self._git.run(self.root, ["show", f"{commit}:{path}"], check=False)
            if expected_sha256:
                if blob.returncode != 0 or hashlib.sha256(blob.stdout).hexdigest() != expected_sha256:
                    raise GoalLifecycleError("Coordination pending commit payload differs from its journal")
            elif blob.returncode == 0:
                raise GoalLifecycleError("Coordination pending deletion still contains its recorded path")
        self._git.clean_require(self.root)
        self._git.fetch(self.root)
        local = self._git.commit_get(self.root)
        remote = self._git.commit_get(self.root, "refs/remotes/origin/main")
        if local == commit and remote == commit:
            journal_path.unlink()
            directory_sync(journal_path.parent)
            return commit, path_sha256_by_name_map
        if self._is_ancestor(commit, remote) and self._is_ancestor(local, remote):
            if not self._recorded_payload_matches(remote, path_sha256_by_name_map):
                raise GoalLifecycleError("Concurrent project-goals update overlaps this exact path set")
            if local != remote:
                self._git.run(self.root, ["merge", "--ff-only", remote])
            journal_path.unlink()
            directory_sync(journal_path.parent)
            return commit, path_sha256_by_name_map
        if local == base and remote == base:
            result = self._git.run(self.root, ["push", "origin", f"{commit}:refs/heads/main"], check=False)
            if result.returncode == 0:
                self._git.run(self.root, ["merge", "--ff-only", commit])
                journal_path.unlink()
                directory_sync(journal_path.parent)
                return commit, path_sha256_by_name_map
        if self._is_ancestor(base, remote) and not self._is_ancestor(commit, remote):
            changed = self._git.run(
                self.root,
                [
                    "diff",
                    "--quiet",
                    base,
                    remote,
                    "--",
                    *sorted(path_sha256_by_name_map),
                ],
                check=False,
            )
            if changed.returncode == 0 and local in {base, remote}:
                if local == base:
                    self._git.run(self.root, ["merge", "--ff-only", remote])
                journal_path.unlink()
                directory_sync(journal_path.parent)
                return None
        raise GoalLifecycleError("Interrupted coordination publication cannot be resumed without ambiguity")

    def _recorded_payload_matches(self, ref: str, path_sha256_by_name_map: Mapping[str, str]) -> bool:
        """Return whether one tree still contains the operation's exact recorded delta.

        Args:
            ref: Ref.
            path_sha256_by_name_map: Path sha-256 by name mapping.

        Returns:
            Whether one tree still contains the operation's exact recorded delta.
        """

        for path, expected_sha256 in path_sha256_by_name_map.items():
            blob = self._git.run(self.root, ["show", f"{ref}:{path}"], check=False)
            if expected_sha256:
                if blob.returncode != 0 or hashlib.sha256(blob.stdout).hexdigest() != expected_sha256:
                    return False
            elif blob.returncode == 0:
                return False
        return True

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Report whether one exact commit is an ancestor of another.

        Args:
            ancestor: Ancestor.
            descendant: Descendant.

        Returns:
            Whether ancestor.
        """

        return (
            self._git.run(
                self.root,
                ["merge-base", "--is-ancestor", ancestor, descendant],
                check=False,
            ).returncode
            == 0
        )


def _coordination_path_list_validate(path_list: list[str], *, common_prefix: str) -> None:
    """Require every direct-main mutation path to belong to the closed owner set.

    Args:
        path_list: Ordered path values.
        common_prefix: Exact task common prefix.
    """

    allowed_path_set = {
        *(f"{common_prefix}/{name}" for name in TASK_ARTIFACT_NAME_SET),
        "AGENTS.md",
        "DESIGN.md",
        "README.md",
        ".gitignore",
    }
    for value in path_list:
        path = PurePosixPath(value)
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or any(part in {"", ".", ".."} for part in path.parts)
            or value not in allowed_path_set
        ):
            raise GoalLifecycleError(f"Coordination publication path is outside its closed owner set: {value}")
