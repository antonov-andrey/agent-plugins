"""Preserve implementation-main state while task work stays isolated."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath
import shutil
from typing import Iterable

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.model import (
    MainCommitDriftAttestation,
    RepositoryState,
    commit_validate,
    repository_relative_path_validate,
)


class MainWorktreeIntegrity:
    """Classify main drift and perform only explicitly attested repairs."""

    def __init__(self, *, git: Git | None = None) -> None:
        self._git = git or Git()

    def refresh_if_independent(self, repository: RepositoryState) -> RepositoryState:
        """Advance recorded main identity only when all drift is task-disjoint."""

        main_root = Path(repository.main_root)
        current_commit = self._git.commit_get(main_root)
        dirty_path_set = self._dirty_path_set_get(main_root)
        task_path_set = self._task_path_set_get(repository)
        overlap_set = _overlap_set_get(dirty_path_set, task_path_set)
        if overlap_set:
            raise GoalLifecycleError(
                f"Ambiguous uncommitted main overlap in {main_root}: {', '.join(sorted(overlap_set))}"
            )
        if current_commit == repository.main_commit:
            return repository
        self._git.ancestor_require(
            main_root,
            repository.main_commit,
            current_commit,
            label=f"{main_root.name} independent main drift",
        )
        changed_path_set = self._commit_path_set_get(
            main_root,
            repository.main_commit,
            current_commit,
        )
        overlap_set = _overlap_set_get(changed_path_set, task_path_set)
        attestation = _attestation_get(repository, current_commit)
        if overlap_set and (attestation is None or set(attestation.path_list) != overlap_set):
            raise GoalLifecycleError(
                f"Committed main overlap requires exact user attestation in {main_root}: "
                + ", ".join(sorted(overlap_set))
            )
        return replace(repository, main_commit=current_commit)

    def leak_recover(self, repository: RepositoryState, *, path_list: Iterable[str]) -> RepositoryState:
        """Restore explicit caller-attested leaked paths from current main HEAD."""

        main_root = Path(repository.main_root)
        if self._git.commit_get(main_root) != repository.main_commit:
            raise GoalLifecycleError("Recover main leak only after classifying committed main drift")
        supplied_path_set = {repository_relative_path_validate(value, label="main-leak path") for value in path_list}
        if not supplied_path_set:
            raise GoalLifecycleError("Main-leak recovery requires at least one exact path")
        dirty_path_set = self._dirty_path_set_get(main_root)
        task_path_set = self._task_path_set_get(repository)
        overlap_set = _overlap_set_get(dirty_path_set, task_path_set)
        if supplied_path_set != overlap_set:
            raise GoalLifecycleError(
                "Main-leak path list must equal the complete current task-overlap set: "
                + ", ".join(sorted(overlap_set))
            )
        for path_text in sorted(supplied_path_set):
            tracked = (
                self._git.run(
                    main_root,
                    ["ls-files", "--error-unmatch", "--", path_text],
                    check=False,
                ).returncode
                == 0
            )
            if tracked:
                self._git.run(
                    main_root,
                    ["restore", "--source=HEAD", "--staged", "--worktree", "--", path_text],
                )
            else:
                _untracked_remove(main_root / path_text)
        if _overlap_set_get(self._dirty_path_set_get(main_root), task_path_set):
            raise GoalLifecycleError("Main-leak recovery left task-overlapping main state")
        return repository

    def commit_drift_accept(
        self,
        repository: RepositoryState,
        *,
        commit: str,
        path_list: Iterable[str],
    ) -> RepositoryState:
        """Record one exact user-approved committed overlap and no broader authority."""

        main_root = Path(repository.main_root)
        requested_commit = commit_validate(commit, label="accepted main commit")
        current_commit = self._git.commit_get(main_root)
        if requested_commit != current_commit:
            raise GoalLifecycleError("Accepted main commit must be the exact current main HEAD")
        self._git.ancestor_require(
            main_root,
            repository.main_commit,
            current_commit,
            label=f"{main_root.name} accepted main drift",
        )
        supplied_path_list = tuple(
            sorted({repository_relative_path_validate(item, label="accepted overlap path") for item in path_list})
        )
        changed_path_set = self._commit_path_set_get(main_root, repository.main_commit, current_commit)
        overlap_set = _overlap_set_get(changed_path_set, self._task_path_set_get(repository))
        if set(supplied_path_list) != overlap_set or not overlap_set:
            raise GoalLifecycleError(
                "Accepted path list must equal the complete committed task-overlap set: "
                + ", ".join(sorted(overlap_set))
            )
        attestation = MainCommitDriftAttestation(commit=current_commit, path_list=supplied_path_list)
        return replace(
            repository,
            accepted_main_commit_drift_list=repository.accepted_main_commit_drift_list + (attestation,),
            main_commit=current_commit,
        )

    def validate(self, repository: RepositoryState) -> None:
        """Prove current main has no unclassified overlap with task work."""

        refreshed = self.refresh_if_independent(repository)
        if refreshed.main_commit != repository.main_commit:
            raise GoalLifecycleError(
                f"Independent main commit drift must be recorded by a lifecycle transition: {repository.main_root}"
            )

    def _task_path_set_get(self, repository: RepositoryState) -> set[str]:
        task_root = Path(repository.task_root)
        return self._diff_path_set_get(task_root, repository.baseline_commit) | self._untracked_path_set_get(task_root)

    def _dirty_path_set_get(self, root: Path) -> set[str]:
        return self._diff_path_set_get(root, "HEAD") | self._untracked_path_set_get(root)

    def _diff_path_set_get(self, root: Path, base: str) -> set[str]:
        payload = self._git.run(root, ["diff", "--name-only", "-z", base, "--"]).stdout
        return _nul_path_set_decode(payload)

    def _untracked_path_set_get(self, root: Path) -> set[str]:
        payload = self._git.run(root, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout
        return _nul_path_set_decode(payload)

    def _commit_path_set_get(self, root: Path, older: str, newer: str) -> set[str]:
        payload = self._git.run(root, ["diff", "--name-only", "-z", older, newer, "--"]).stdout
        return _nul_path_set_decode(payload)


def _attestation_get(repository: RepositoryState, commit: str) -> MainCommitDriftAttestation | None:
    for item in reversed(repository.accepted_main_commit_drift_list):
        if item.commit == commit:
            return item
    return None


def _nul_path_set_decode(payload: bytes) -> set[str]:
    try:
        return {
            repository_relative_path_validate(item.decode("utf-8"), label="Git path")
            for item in payload.split(b"\0")
            if item
        }
    except UnicodeDecodeError as error:
        raise GoalLifecycleError("Goal lifecycle requires UTF-8 repository paths") from error


def _overlap_set_get(left_set: set[str], right_set: set[str]) -> set[str]:
    overlap_set: set[str] = set()
    for left in left_set:
        left_path = PurePosixPath(left)
        for right in right_set:
            right_path = PurePosixPath(right)
            if left_path == right_path or left_path in right_path.parents or right_path in left_path.parents:
                overlap_set.add(left)
                overlap_set.add(right)
    return overlap_set


def _untracked_remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        raise GoalLifecycleError(f"Untracked main-leak path is unavailable: {path}")
