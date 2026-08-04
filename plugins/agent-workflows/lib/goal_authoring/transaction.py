"""Crash-recoverable serialized direct-main source-pair publication."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from goal_authoring.model import (
    GoalAuthoringError,
    GoalSource,
    commit_validate,
    common_prefix_validate,
)
from goal_authoring.repository import ProjectGoalsRepository


def _directory_sync(path: Path) -> None:
    """Fsync one directory after a namespace mutation.

    Args:
        path: Directory path.
    """

    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json_write(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically write canonical private JSON and fsync it.

    Args:
        path: Destination path.
        payload: JSON-ready object.
    """

    temporary_path = path.parent / f".{path.name}.{secrets.token_hex(12)}.tmp"
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        _directory_sync(path.parent)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


class ExclusiveFileLock(AbstractContextManager["ExclusiveFileLock"]):
    """Hold one kernel-released non-blocking exclusive lock."""

    def __init__(self, path: Path) -> None:
        """Initialize the lock path.

        Args:
            path: Private lock path.
        """

        self._path = path
        self._descriptor: int | None = None

    def __enter__(self) -> "ExclusiveFileLock":
        """Acquire the lock without waiting.

        Returns:
            This held lock.
        """

        descriptor = os.open(
            self._path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise GoalAuthoringError("Authoring lock must be one private user-owned ordinary file")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise GoalAuthoringError("Another project-goals authoring transaction is active") from error
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Release the held lock.

        Args:
            exc_type: Exception type.
            exc_value: Exception value.
            traceback: Exception traceback.
        """

        del exc_type, exc_value, traceback
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None


class GoalSourceTransaction:
    """Publish one complete goal/spec pair through compare-and-swap main."""

    def __init__(self, repository: ProjectGoalsRepository) -> None:
        """Initialize one repository-bound transaction owner.

        Args:
            repository: Canonical project-goals boundary.
        """

        self._repository = repository
        self._git = repository.git

    def publish(self, source: GoalSource) -> str:
        """Commit and push exactly one complete pair, revising it atomically.

        Args:
            source: Complete source payload.

        Returns:
            The exact commit containing the pair.
        """

        payload_by_path = source.relative_payload_by_path()
        digest_by_path = {path: hashlib.sha256(payload).hexdigest() for path, payload in payload_by_path.items()}
        lock_path = self._repository.private_directory_require("lock") / "direct-main.lock"
        with ExclusiveFileLock(lock_path):
            recovered = self._recover(source.common_prefix, expected_digest_by_path=digest_by_path)
            if recovered is not None:
                return recovered
            base_commit = self._repository.synchronize_require()
            self._existing_directory_shape_require(source.common_prefix)
            for _attempt in range(4):
                commit = self._commit_build(base_commit=base_commit, source=source)
                if commit == base_commit:
                    self._repository.source_shape_require(source.common_prefix)
                    return base_commit
                journal_path = self._journal_path(source.common_prefix)
                _atomic_json_write(
                    journal_path,
                    {
                        "schema_version": 1,
                        "base_commit": base_commit,
                        "commit": commit,
                        "path_sha256_by_name": digest_by_path,
                        "phase": "commit-built",
                    },
                )
                push = self._git.run(("push", "origin", f"{commit}:refs/heads/main"), check=False)
                if push.returncode == 0:
                    self._finish_local(commit, journal_path=journal_path)
                    return commit
                self._git.run(("fetch", "--prune", "origin"))
                remote_commit = self._git.commit("refs/remotes/origin/main")
                self._ancestor_require(
                    base_commit,
                    remote_commit,
                    label="Concurrent project-goals main update",
                )
                if self._have_path_change(base_commit, remote_commit, sorted(payload_by_path)):
                    raise GoalAuthoringError("Concurrent project-goals update overlaps this source pair")
                self._git.run(("merge", "--ff-only", remote_commit))
                self._git.clean_require()
                journal_path.unlink(missing_ok=True)
                _directory_sync(journal_path.parent)
                base_commit = remote_commit
            raise GoalAuthoringError("Source publication exceeded bounded disjoint-update retries")

    def _existing_directory_shape_require(self, common_prefix: str) -> None:
        """Reject reuse of a historical or foreign-shaped source directory.

        Args:
            common_prefix: Exact source identity.
        """

        directory = self._repository.source_directory(common_prefix)
        if not directory.exists():
            return
        self._repository.source_shape_require(common_prefix)

    def _commit_build(self, *, base_commit: str, source: GoalSource) -> str:
        """Build one detached commit from the exact pair without touching files.

        Args:
            base_commit: Exact main base.
            source: Complete source payload.

        Returns:
            The new commit, or the base when content is unchanged.
        """

        index_path = self._repository.private_directory_require("staging") / f"index-{secrets.token_hex(16)}"
        environment = {"GIT_INDEX_FILE": str(index_path)}
        try:
            self._git.run(("read-tree", base_commit), extra_environment=environment)
            for relative_path, payload in sorted(source.relative_payload_by_path().items()):
                blob = self._git.run(("hash-object", "-w", "--stdin"), input_bytes=payload).stdout.decode().strip()
                self._git.run(
                    (
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        f"100644,{blob},{relative_path}",
                    ),
                    extra_environment=environment,
                )
            tree = self._git.run(("write-tree",), extra_environment=environment).stdout.decode().strip()
            base_tree = self._git.output(("show", "-s", "--format=%T", base_commit))
            if tree == base_tree:
                return base_commit
            commit = (
                self._git.run(
                    ("commit-tree", tree, "-p", base_commit, "-F", "-"),
                    input_bytes=(f"Publish {source.common_prefix} source contracts\n").encode(),
                )
                .stdout.decode()
                .strip()
            )
            return commit_validate(commit, label="Source publication commit")
        finally:
            index_path.unlink(missing_ok=True)

    def _recover(self, common_prefix: str, *, expected_digest_by_path: Mapping[str, str]) -> str | None:
        """Recover one interrupted exact transaction or reject ambiguity.

        Args:
            common_prefix: Exact source identity.
            expected_digest_by_path: Requested exact payload digests.

        Returns:
            Recovered commit, or ``None`` when a new transaction should begin.
        """

        journal_path = self._journal_path(common_prefix)
        if not journal_path.exists():
            return None
        try:
            descriptor = os.open(journal_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                metadata = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) & 0o077
                ):
                    raise GoalAuthoringError("Source publication journal must be one private ordinary file")
                payload = json.loads(handle.read().decode("utf-8"))
        except GoalAuthoringError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GoalAuthoringError("Source publication journal is malformed") from error
        expected_field_set = {
            "schema_version",
            "base_commit",
            "commit",
            "path_sha256_by_name",
            "phase",
        }
        if not isinstance(payload, dict) or set(payload) != expected_field_set:
            raise GoalAuthoringError("Source publication journal has another shape")
        if payload["schema_version"] != 1 or payload["phase"] != "commit-built":
            raise GoalAuthoringError("Source publication journal has unsupported state")
        base_commit = commit_validate(payload["base_commit"], label="Journal base commit")
        commit = commit_validate(payload["commit"], label="Journal pending commit")
        digest_by_path = payload["path_sha256_by_name"]
        if digest_by_path != dict(expected_digest_by_path):
            raise GoalAuthoringError("Interrupted transaction belongs to different source bytes")
        expected_path_set = {
            f"{common_prefix_validate(common_prefix)}/goal.md",
            f"{common_prefix}/spec.md",
        }
        if set(digest_by_path) != expected_path_set:
            raise GoalAuthoringError("Source publication journal path set is malformed")
        parent_list = self._git.output(("show", "-s", "--format=%P", commit)).split()
        if parent_list != [base_commit]:
            raise GoalAuthoringError("Source publication pending commit has another parent")
        if not self._match_tree_payload(commit, digest_by_path):
            raise GoalAuthoringError("Source publication pending commit differs from its journal")
        self._repository.checkout_shape_require()
        self._git.run(("fetch", "--prune", "origin"))
        local_commit = self._git.commit()
        remote_commit = self._git.commit("refs/remotes/origin/main")
        if self._is_ancestor(commit, remote_commit):
            if not self._match_tree_payload(remote_commit, digest_by_path):
                raise GoalAuthoringError("Published source bytes were replaced by a conflicting update")
            if local_commit != remote_commit:
                self._git.run(("merge", "--ff-only", remote_commit))
            journal_path.unlink()
            _directory_sync(journal_path.parent)
            self._repository.source_shape_require(common_prefix)
            return commit
        if local_commit == base_commit and remote_commit == base_commit:
            push = self._git.run(("push", "origin", f"{commit}:refs/heads/main"), check=False)
            if push.returncode == 0:
                self._finish_local(commit, journal_path=journal_path)
                return commit
        if self._is_ancestor(base_commit, remote_commit) and not self._have_path_change(
            base_commit, remote_commit, sorted(expected_path_set)
        ):
            if local_commit == base_commit:
                self._git.run(("merge", "--ff-only", remote_commit))
            journal_path.unlink()
            _directory_sync(journal_path.parent)
            return None
        raise GoalAuthoringError("Interrupted source publication cannot be resumed without ambiguity")

    def _finish_local(self, commit: str, *, journal_path: Path) -> None:
        """Fast-forward local main and retire one completed journal.

        Args:
            commit: Published exact commit.
            journal_path: Owned private journal path.
        """

        self._git.run(("merge", "--ff-only", commit))
        self._git.clean_require()
        if self._git.commit() != commit:
            raise GoalAuthoringError("Local project-goals main did not reach the published commit")
        journal_path.unlink(missing_ok=True)
        _directory_sync(journal_path.parent)

    def _journal_path(self, common_prefix: str) -> Path:
        """Return the exact private journal path for one source.

        Args:
            common_prefix: Exact source identity.

        Returns:
            The journal path.
        """

        return self._repository.private_directory_require("journal") / f"{common_prefix_validate(common_prefix)}.json"

    def _match_tree_payload(self, ref: str, digest_by_path: Mapping[str, str]) -> bool:
        """Return whether one tree retains every recorded source payload.

        Args:
            ref: Git ref.
            digest_by_path: Expected payload digests.

        Returns:
            Whether every payload matches.
        """

        for relative_path, expected_digest in digest_by_path.items():
            blob = self._git.run(("show", f"{ref}:{relative_path}"), check=False)
            if blob.returncode != 0 or hashlib.sha256(blob.stdout).hexdigest() != expected_digest:
                return False
        return True

    def _have_path_change(self, old: str, new: str, path_list: list[str]) -> bool:
        """Return whether one exact path set changed between commits.

        Args:
            old: Old commit.
            new: New commit.
            path_list: Exact relative paths.

        Returns:
            Whether a path changed.
        """

        return self._git.run(("diff", "--quiet", old, new, "--", *path_list), check=False).returncode != 0

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Return whether one commit is an ancestor of another.

        Args:
            ancestor: Candidate ancestor.
            descendant: Candidate descendant.

        Returns:
            The ancestry result.
        """

        return self._git.run(("merge-base", "--is-ancestor", ancestor, descendant), check=False).returncode == 0

    def _ancestor_require(self, ancestor: str, descendant: str, *, label: str) -> None:
        """Require one commit to be an ancestor of another.

        Args:
            ancestor: Candidate ancestor.
            descendant: Candidate descendant.
            label: Diagnostic owner label.
        """

        if not self._is_ancestor(ancestor, descendant):
            raise GoalAuthoringError(f"{label}: {ancestor} is not an ancestor of {descendant}")
