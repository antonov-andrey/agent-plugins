"""Prepare and validate isolated goal-brainstorm Git worktrees."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import TypedDict, cast

import tomllib

EMPTY_MANIFEST_TEXT = """schema_version = 1

[resource]
copy_optional_path_list = []
copy_required_path_list = []
link_optional_path_list = []
link_required_path_list = []
"""
GOAL_SUFFIX = "-goal.md"
IGNORE_WORKTREE_PATTERN = "/.worktree/"
INITIAL_MANIFEST_OWNER_MARKER_FILENAME = "initial-manifest-owner-v1"
LIFECYCLE_INDEX_BY_NAME_MAP = {
    "designing": 0,
    "design_approved": 1,
    "worktree_created": 2,
    "repository_prepared": 3,
    "contracts_authored": 4,
    "goal_ready": 5,
    "active": 6,
}
SEALED_LIFECYCLE_STATE_SET = {"goal_ready", "active"}
MANIFEST_NAME = "worktree-bootstrap.toml"
MANIFEST_RESOURCE_KEY_SET = {
    "copy_optional_path_list",
    "copy_required_path_list",
    "link_optional_path_list",
    "link_required_path_list",
}
PRIVATE_STATE_DIRECTORY_NAME = "goal-brainstorm-worktree"
PRIVATE_CLONE_STAGING_PURPOSE_SET = {
    "copy-fingerprint-migration",
    "main-preimage",
    "resource-source-preimage",
}
PENDING_PARTICIPATING_SUBMODULE_DIRECTORY_NAME = "pending-participating-submodule-v1"
PENDING_WORKTREE_DIRECTORY_NAME = "pending-worktree-v1"
MAIN_LEAK_TRANSACTION_DIRECTORY_NAME = "main-leak-transaction-v1"
LEGACY_PRIVATE_STATE_FILENAME = "state-v1.json"
PRIVATE_STATE_FILENAME = "state-v2.json"
SPECIFICATION_SUFFIX = "-spec.md"
STATE_SCHEMA_VERSION = 2
TEMPORARY_EXCLUDE_MARKER_FILENAME = "temporary-exclude-owner-v1"
VALIDATION_REPAIR_PASS_LIMIT = 8
WORKTREE_CONTAINER_NAME = ".worktree"


def _hex_digest_is_valid(value: object, allowed_length_set: set[int]) -> bool:
    """Return whether one value is a lowercase hexadecimal digest.

    Args:
        value: Candidate digest value.
        allowed_length_set: Exact supported digest lengths.

    Returns:
        True only for one supported lowercase hexadecimal string.
    """

    return (
        isinstance(value, str)
        and len(value) in allowed_length_set
        and all(character in "0123456789abcdef" for character in value)
    )


def worktree_prepare(
    coordinating_repository: Path,
    specification: Path,
    repository_list: list[Path],
    participating_submodule_list: list[tuple[Path, Path]] | None = None,
) -> str:
    """Prepare one task worktree set and return its JSON result.

    Args:
        coordinating_repository: Main worktree that owns the physical specification.
        specification: Specification path relative to the coordinating repository.
        repository_list: Additional affected main-worktree roots.
        participating_submodule_list: Explicit main-root and recursive-submodule path pairs.

    Returns:
        One machine-readable JSON result.
    """

    return WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    ).prepare(repository_list, participating_submodule_list or [])


def worktree_activate(
    coordinating_repository: Path,
    specification: Path,
) -> str:
    """Record externally created persistent-goal activation.

    Args:
        coordinating_repository: Main worktree that owns the physical task pair.
        specification: Specification path relative to the coordinating repository.

    Returns:
        One machine-readable JSON result.
    """

    return WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    ).activate()


def worktree_contracts_authored(
    coordinating_repository: Path,
    specification: Path,
) -> str:
    """Record completion of validated stable-owner contract authoring.

    Args:
        coordinating_repository: Main worktree that owns the physical specification.
        specification: Specification path relative to the coordinating repository.

    Returns:
        One machine-readable JSON result.
    """

    return WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    ).contracts_authored()


def worktree_main_leak_recover(
    coordinating_repository: Path,
    specification: Path,
    main_repository: Path,
    path_list: list[Path],
) -> str:
    """Record agent provenance and recover exact duplicated task patches from main.

    Args:
        coordinating_repository: Main worktree that owns the physical specification.
        specification: Specification path relative to the coordinating repository.
        main_repository: Participating main worktree that received the leaked patch.
        path_list: Exact root-relative paths the calling agent confirms it leaked.

    Returns:
        One machine-readable JSON result.
    """

    return WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    ).main_leak_recover(main_repository, path_list)


def worktree_main_commit_drift_accept(
    coordinating_repository: Path,
    specification: Path,
    main_repository: Path,
    expected_commit: str,
    path_list: list[Path],
) -> str:
    """Accept caller-attested overlapping committed main drift.

    Args:
        coordinating_repository: Main worktree that owns the physical specification.
        specification: Specification path relative to the coordinating repository.
        main_repository: Participating top-level or task-owned-submodule main owner root.
        expected_commit: Exact full current main commit accepted by the caller.
        path_list: Exact owner-relative overlapping paths accepted by the caller.

    Returns:
        One machine-readable JSON result.
    """

    return WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    ).main_commit_drift_accept(main_repository, expected_commit, path_list)


def worktree_seal(
    coordinating_repository: Path,
    goal: Path,
    specification: Path,
) -> str:
    """Seal one prepared task worktree set and return its JSON result.

    Args:
        coordinating_repository: Main worktree that owns the physical task pair.
        goal: Goal path relative to the coordinating repository.
        specification: Specification path relative to the coordinating repository.

    Returns:
        One machine-readable JSON result.
    """

    return WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    ).seal(goal)


def worktree_validate(
    coordinating_repository: Path,
    required_state: str,
    specification: Path,
) -> str:
    """Validate one task worktree set and return its JSON result.

    Args:
        coordinating_repository: Main worktree that owns the physical specification.
        required_state: Minimum required lifecycle state.
        specification: Specification path relative to the coordinating repository.

    Returns:
        One machine-readable JSON result.
    """

    return WorktreeWorkflow(
        coordinating_repository=coordinating_repository,
        git_command=GitCommand(),
        specification=specification,
    ).validate(required_state)


class GitCommand:
    """Execute Git commands through one checked external boundary."""

    def _literal_pathspecs_required(self, argument_list: list[str]) -> bool:
        """Return whether one non-spawning Git command consumes pathspecs."""

        argument_index = 0
        while argument_index < len(argument_list):
            argument = argument_list[argument_index]
            if argument == "-c":
                argument_index += 2
                continue
            if argument.startswith("-"):
                argument_index += 1
                continue
            return argument in {
                "diff",
                "ls-files",
                "ls-tree",
                "restore",
                "status",
                "update-index",
            }
        return False

    def _environment_get(self, *, literal_pathspecs: bool) -> dict[str, str]:
        """Return a sanitized environment that cannot redirect repository state.

        Args:
            literal_pathspecs: Whether this Git subcommand supports forced literal pathspecs.
        """

        environment_by_name_map = os.environ.copy()
        for variable_name in (
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR",
            "GIT_CONFIG",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_PARAMETERS",
            "GIT_CONFIG_SYSTEM",
            "GIT_DIR",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_PREFIX",
            "GIT_QUARANTINE_PATH",
            "GIT_WORK_TREE",
        ):
            environment_by_name_map.pop(variable_name, None)
        for variable_name in list(environment_by_name_map):
            if variable_name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
                environment_by_name_map.pop(variable_name)
        if literal_pathspecs:
            environment_by_name_map["GIT_LITERAL_PATHSPECS"] = "1"
        else:
            environment_by_name_map.pop("GIT_LITERAL_PATHSPECS", None)
        environment_by_name_map["GIT_TERMINAL_PROMPT"] = "0"
        return environment_by_name_map

    def run(
        self,
        repository: Path,
        argument_list: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one Git command against an explicit repository root.

        Args:
            repository: Explicit Git working directory.
            argument_list: Git arguments after the executable name.
            check: Raise an error when Git returns a nonzero status.
            input_text: Optional standard-input text.

        Returns:
            The completed Git process.

        Raises:
            WorktreeError: Git failed while `check` was enabled.
        """

        result = subprocess.run(
            ["git", "-C", str(repository), *argument_list],
            capture_output=True,
            check=False,
            env=self._environment_get(
                literal_pathspecs=self._literal_pathspecs_required(argument_list),
            ),
            input=input_text,
            encoding="utf-8",
            errors="surrogateescape",
            text=True,
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
            raise WorktreeError(f"Git command failed in {repository}: git {' '.join(argument_list)}: {detail}")
        return result

    def run_bytes(
        self,
        repository: Path,
        argument_list: list[str],
        *,
        check: bool = True,
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run one binary-safe Git command against an explicit repository root.

        Args:
            repository: Explicit Git working directory.
            argument_list: Git arguments after the executable name.
            check: Raise an error when Git returns a nonzero status.
            input_bytes: Optional standard-input bytes.

        Returns:
            The completed Git process.

        Raises:
            WorktreeError: Git failed while `check` was enabled.
        """

        result = subprocess.run(
            ["git", "-C", str(repository), *argument_list],
            capture_output=True,
            check=False,
            env=self._environment_get(
                literal_pathspecs=self._literal_pathspecs_required(argument_list),
            ),
            input=input_bytes,
        )
        if check and result.returncode != 0:
            detail_bytes = result.stderr.strip() or result.stdout.strip()
            detail = (
                detail_bytes.decode("utf-8", errors="replace") if detail_bytes else f"exit status {result.returncode}"
            )
            raise WorktreeError(f"Git command failed in {repository}: git {' '.join(argument_list)}: {detail}")
        return result


class WorktreeError(RuntimeError):
    """Report one invalid or ambiguous worktree contract state."""


class ResourceState(TypedDict):
    """Describe one prepared bootstrap resource."""

    destination_fingerprint: str
    path: str
    required: bool
    skipped: bool
    source_fingerprint: str
    strategy: str


class ResourceTransaction(TypedDict):
    """Describe one durable pending resource materialization."""

    destination_fingerprint: str
    fingerprint_schema_version: int
    path: str
    previous_fingerprint: str
    previous_present: bool
    schema_version: int
    source_fingerprint: str
    strategy: str


class MainLeakTransaction(TypedDict):
    """Describe one durable main-worktree recovery transaction."""

    index_managed: bool
    fingerprint_schema_version: int
    index_previous_entry_list: list[str]
    index_target_entry_list: list[str]
    main_owner_root: str
    path: str
    phase: str
    schema_version: int
    top_level_path: str
    working_previous_fingerprint: str
    working_previous_present: bool
    working_target_fingerprint: str
    working_target_present: bool


class PendingWorktree(TypedDict):
    """Describe one provider-authorized task-worktree creation."""

    baseline_commit: str
    branch_name: str
    common_git_directory: str
    main_root: str
    schema_version: int
    task_root: str


class PendingParticipatingSubmodule(TypedDict):
    """Describe provider-authored bootstrap pending its first global state."""

    baseline_commit: str
    gitignore_expected_fingerprint: str
    gitignore_expected_text: str
    gitignore_mode: int
    gitignore_previous_fingerprint: str
    manifest_expected_fingerprint: str
    manifest_expected_text: str
    manifest_mode: int
    manifest_previous_fingerprint: str
    path: str
    schema_version: int


class ParticipatingSubmoduleState(TypedDict):
    """Describe one explicitly task-owned recursive submodule."""

    accepted_main_commit_drift_list: list[AcceptedMainCommitDrift]
    baseline_commit: str
    main_commit: str
    main_leak_fingerprint_by_path_map: dict[str, str]
    main_preimage_by_path_map: dict[str, MainPathPreimage]
    main_status_by_path_map: dict[str, str]
    main_status_fingerprint_by_path_map: dict[str, str]
    manifest_fingerprint: str
    path: str
    resource_state_list: list[ResourceState]


class MainPathPreimage(TypedDict):
    """Describe one private recoverable main-worktree path preimage."""

    index_entry_list: list[str]
    snapshot_name: str
    working_fingerprint: str
    working_present: bool


class AcceptedMainCommitDrift(TypedDict):
    """Record one explicit caller attestation for overlapping committed main drift."""

    commit: str
    path_list: list[str]


class RepositoryState(TypedDict):
    """Describe one participating top-level repository."""

    accepted_main_commit_drift_list: list[AcceptedMainCommitDrift]
    baseline_commit: str
    branch_name: str
    common_git_directory: str
    main_commit: str
    main_preimage_by_path_map: dict[str, MainPathPreimage]
    main_leak_fingerprint_by_path_map: dict[str, str]
    main_root: str
    main_status_by_path_map: dict[str, str]
    main_status_fingerprint_by_path_map: dict[str, str]
    manifest_fingerprint: str
    participating_submodule_state_list: list[ParticipatingSubmoduleState]
    resource_state_list: list[ResourceState]
    submodule_commit_by_path_map: dict[str, str]
    task_root: str
    temporary_exclude_list: list[str]


class WorktreeState(TypedDict):
    """Describe the complete prepared multi-repository task."""

    coordinating_repository: str
    fingerprint_schema_version: int
    goal_fingerprint: str
    lifecycle_state: str
    prefix: str
    repository_state_list: list[RepositoryState]
    schema_version: int
    specification_fingerprint: str
    specification_path: str


class WorktreeWorkflow:
    """Own the ordered task-worktree preparation and validation lifecycle."""

    def __init__(
        self,
        *,
        coordinating_repository: Path,
        git_command: GitCommand,
        specification: Path,
    ) -> None:
        """Initialize one workflow against explicit coordinating paths.

        Args:
            coordinating_repository: Main worktree that owns the physical specification.
            git_command: Checked Git command boundary.
            specification: Specification path relative to the coordinating repository.
        """

        self._git_command = git_command
        self._coordinating_repository = self._repository_root_validate(coordinating_repository)
        self._specification = self._relative_path_validate(specification, "specification")
        if self._specification.parent != Path(".spec"):
            raise WorktreeError(f"Specification must be a direct child of .spec: {self._specification}")
        if self._specification.name.endswith(SPECIFICATION_SUFFIX):
            self._prefix = self._specification.name.removesuffix(SPECIFICATION_SUFFIX)
        else:
            raise WorktreeError(f"Specification filename must end with {SPECIFICATION_SUFFIX}: {self._specification}")
        self._branch_name_validate()
        self._specification_path = self._coordinating_repository / self._specification
        if not self._specification_path.is_file():
            raise WorktreeError(f"Specification does not exist: {self._specification_path}")
        if self._specification_path.parent.is_symlink():
            raise WorktreeError(
                f"Coordinating repository must physically own the .spec directory: {self._specification_path.parent}"
            )
        self._task_artifact_validate(self._specification_path, "Specification")
        tracked_specification_path_list = self._git_command.run(
            self._coordinating_repository,
            ["ls-files", "-z", "--", ".spec"],
        ).stdout.split("\0")
        tracked_specification_path_list = [path_text for path_text in tracked_specification_path_list if path_text]
        if tracked_specification_path_list:
            raise WorktreeError(
                "Task-artifact directory must remain untracked by Git; tracked paths: "
                + ", ".join(sorted(tracked_specification_path_list))
            )
        self._task_root = self._coordinating_repository / WORKTREE_CONTAINER_NAME / self._prefix

    def prepare(
        self,
        repository_list: list[Path],
        participating_submodule_list: list[tuple[Path, Path]],
    ) -> str:
        """Create or adopt each task worktree and prepare its repository boundary.

        Args:
            repository_list: Additional affected main-worktree roots.
            participating_submodule_list: Explicit main-root and recursive-submodule path pairs.

        Returns:
            One machine-readable JSON result.
        """

        performed_repair_list: list[str] = []
        skipped_optional_resource_list: list[str] = []
        repository_root_list = self._repository_root_list_get(repository_list)
        participating_submodule_path_set_by_main_root_map = self._participating_submodule_path_set_by_main_root_get(
            repository_root_list,
            participating_submodule_list,
        )
        if not self._is_tracked_ignore_match(self._coordinating_repository, PurePosixPath(".spec")):
            raise WorktreeError(
                f"Tracked ignore rules do not cover the physical artifact directory "
                f"{self._coordinating_repository / '.spec'}"
            )
        coordinating_state_error: WorktreeError | None = None
        try:
            previous_state = self._state_optional_get(performed_repair_list)
        except WorktreeError as exc:
            coordinating_state_error = exc
            previous_state = None
        if previous_state is None:
            previous_state = self._state_secondary_replica_optional_get(
                repository_root_list,
                performed_repair_list,
            )
        if previous_state is None and coordinating_state_error is not None:
            raise coordinating_state_error
        goal_path = self._coordinating_repository / ".spec" / f"{self._prefix}{GOAL_SUFFIX}"
        if previous_state is None and os.path.lexists(goal_path):
            raise WorktreeError(
                f"Private state is absent while a paired goal exists; task lifecycle cannot be reconstructed: "
                f"{goal_path}"
            )
        if previous_state is not None:
            self._state_validate_observable(
                previous_state,
                performed_repair_list,
                allow_artifact_drift=previous_state["lifecycle_state"] not in SEALED_LIFECYCLE_STATE_SET,
            )
            self._state_write(previous_state, performed_repair_list)
        previous_repository_state_by_main_root_map = (
            {item["main_root"]: item for item in previous_state["repository_state_list"]}
            if previous_state is not None
            else {}
        )
        missing_previous_root_set = set(previous_repository_state_by_main_root_map) - {
            str(repository_root) for repository_root in repository_root_list
        }
        if missing_previous_root_set:
            raise WorktreeError(
                "Prepare cannot remove repositories from an existing task set: "
                + ", ".join(sorted(missing_previous_root_set))
            )
        added_root_set = {str(repository_root) for repository_root in repository_root_list} - set(
            previous_repository_state_by_main_root_map
        )
        if (
            previous_state is not None
            and previous_state["lifecycle_state"] in SEALED_LIFECYCLE_STATE_SET
            and added_root_set
        ):
            raise WorktreeError(
                "Prepare cannot add repositories to a sealed task set: " + ", ".join(sorted(added_root_set))
            )
        if previous_state is not None and previous_state["lifecycle_state"] != "repository_prepared" and added_root_set:
            raise WorktreeError(
                f"Prepare cannot add repositories after {previous_state['lifecycle_state']} was recorded: "
                + ", ".join(sorted(added_root_set))
            )
        previous_participating_submodule_path_set_by_main_root_map = (
            {
                repository_state["main_root"]: {
                    item["path"] for item in repository_state["participating_submodule_state_list"]
                }
                for repository_state in previous_state["repository_state_list"]
            }
            if previous_state is not None
            else {}
        )
        removed_participating_submodule_list: list[str] = []
        added_participating_submodule_list: list[str] = []
        for repository_root in repository_root_list:
            main_root_text = str(repository_root)
            requested_path_set = participating_submodule_path_set_by_main_root_map[main_root_text]
            previous_path_set = previous_participating_submodule_path_set_by_main_root_map.get(main_root_text, set())
            removed_participating_submodule_list.extend(
                f"{main_root_text}:{path_text}" for path_text in sorted(previous_path_set - requested_path_set)
            )
            added_participating_submodule_list.extend(
                f"{main_root_text}:{path_text}" for path_text in sorted(requested_path_set - previous_path_set)
            )
        if removed_participating_submodule_list:
            raise WorktreeError(
                "Prepare cannot remove task-owned submodules from an existing task set: "
                + ", ".join(removed_participating_submodule_list)
            )
        if (
            previous_state is not None
            and previous_state["lifecycle_state"] != "repository_prepared"
            and added_participating_submodule_list
        ):
            raise WorktreeError(
                f"Prepare cannot add task-owned submodules after "
                f"{previous_state['lifecycle_state']} was recorded: " + ", ".join(added_participating_submodule_list)
            )

        worktree_by_main_root_map: dict[str, Path] = {}
        baseline_commit_by_main_root_map: dict[str, str] = {}
        tool_less_adoption_by_main_root_map: dict[str, bool] = {}
        owns_temporary_exclude_by_main_root_map: dict[str, bool] = {}
        temporary_exclude_by_main_root_map: dict[str, list[str]] = {}
        for repository_root in repository_root_list:
            self._ordinary_text_atomic_write_list_reconcile(
                repository_root,
                performed_repair_list,
            )
            current_main_commit = self._git_command.run(repository_root, ["rev-parse", "HEAD"]).stdout.strip()
            pending_worktree_state = self._pending_worktree_optional_get(repository_root)
            previous_repository_state = previous_repository_state_by_main_root_map.get(str(repository_root))
            if pending_worktree_state is not None and previous_repository_state is None:
                baseline_commit = pending_worktree_state["baseline_commit"]
            elif previous_repository_state is None:
                expected_task_root = repository_root / WORKTREE_CONTAINER_NAME / self._prefix
                existing_record = self._worktree_by_path_map_get(repository_root).get(str(expected_task_root.resolve()))
                baseline_commit = (
                    existing_record["head"]
                    if existing_record is not None and existing_record["branch_name"] == self._prefix
                    else current_main_commit
                )
            else:
                baseline_commit = previous_repository_state["baseline_commit"]
            if (
                baseline_commit != current_main_commit
                and self._git_command.run(
                    repository_root,
                    ["merge-base", "--is-ancestor", baseline_commit, current_main_commit],
                    check=False,
                ).returncode
                != 0
            ):
                raise WorktreeError(f"Main history no longer descends from task baseline: {repository_root}")
            baseline_commit_by_main_root_map[str(repository_root)] = baseline_commit
            tool_less_adoption_by_main_root_map[str(repository_root)] = self._worktree_preflight_validate(
                baseline_commit,
                repository_root,
                previous_repository_state_by_main_root_map.get(str(repository_root)),
                participating_submodule_path_set_by_main_root_map[str(repository_root)],
                performed_repair_list,
            )
        for repository_root in repository_root_list:
            baseline_commit = baseline_commit_by_main_root_map[str(repository_root)]
            expected_task_root = repository_root / WORKTREE_CONTAINER_NAME / self._prefix
            if (
                str(repository_root) not in previous_repository_state_by_main_root_map
                and not tool_less_adoption_by_main_root_map[str(repository_root)]
            ):
                self._pending_worktree_create(
                    baseline_commit,
                    repository_root,
                )
            owns_temporary_exclude = self._temporary_exclude_precreate(
                repository_root,
                expected_task_root,
            )
            owns_temporary_exclude_by_main_root_map[str(repository_root)] = owns_temporary_exclude
            task_root = self._worktree_create_or_adopt(
                baseline_commit,
                repository_root,
                performed_repair_list,
                str(repository_root) in previous_repository_state_by_main_root_map,
                tool_less_adoption_by_main_root_map[str(repository_root)],
            )
            worktree_by_main_root_map[str(repository_root)] = task_root
            self._ordinary_text_atomic_write_list_reconcile(
                task_root,
                performed_repair_list,
            )
        for repository_root in repository_root_list:
            main_root_text = str(repository_root)
            self._specification_link_collision_preflight(
                worktree_by_main_root_map[main_root_text],
                allow_incorrect_link_repair=main_root_text in previous_repository_state_by_main_root_map,
            )
        for repository_root in repository_root_list:
            main_root_text = str(repository_root)
            task_root = worktree_by_main_root_map[main_root_text]
            temporary_exclude_by_main_root_map[str(repository_root)] = self._temporary_exclude_prepare(
                repository_root,
                task_root,
                owns_temporary_exclude_by_main_root_map[main_root_text],
            )

        repository_state_list: list[RepositoryState] = []
        for repository_root in repository_root_list:
            main_root_text = str(repository_root)
            task_root = worktree_by_main_root_map[main_root_text]
            previous_repository_state = previous_repository_state_by_main_root_map.get(main_root_text)
            manifest_path = task_root / MANIFEST_NAME
            if not os.path.lexists(manifest_path):
                if previous_repository_state is None:
                    self._initial_manifest_create(
                        task_root,
                        performed_repair_list,
                        report_text="created missing initial manifest",
                    )
                else:
                    self._initial_manifest_restore(
                        task_root,
                        performed_repair_list,
                        report_text="restored provider-owned initial manifest",
                    )
            else:
                self._initial_manifest_owner_retire_if_changed(
                    task_root,
                    performed_repair_list,
                )
            resource_by_class_map = self._manifest_get(manifest_path, task_root)
            required_ignore_path_list = [
                PurePosixPath(".spec"),
                PurePosixPath(WORKTREE_CONTAINER_NAME),
                *[
                    PurePosixPath(path_text)
                    for path_text in sorted(
                        {path_text for path_list in resource_by_class_map.values() for path_text in path_list}
                    )
                ],
            ]
            for added_pattern in self._tracked_ignore_prepare(task_root, required_ignore_path_list):
                performed_repair_list.append(f"authored tracked ignore pattern {added_pattern}: {task_root}")
            self._specification_link_prepare(
                task_root,
                performed_repair_list,
                allow_incorrect_link_repair=previous_repository_state is not None,
            )
            previous_participating_submodule_path_set = (
                {item["path"] for item in previous_repository_state["participating_submodule_state_list"]}
                if previous_repository_state is not None
                else set()
            )
            participating_submodule_path_set = participating_submodule_path_set_by_main_root_map[main_root_text]
            self._submodule_prepare(
                task_root,
                performed_repair_list,
                previous_participating_submodule_path_set | participating_submodule_path_set,
            )
            self._participating_submodule_path_set_validate(
                task_root,
                participating_submodule_path_set,
            )
            for participating_submodule_path_text in sorted(participating_submodule_path_set):
                self._ordinary_text_atomic_write_list_reconcile(
                    task_root / participating_submodule_path_text,
                    performed_repair_list,
                )
            resource_state_list = self._resource_state_list_prepare(
                repository_root,
                resource_by_class_map,
                task_root,
                performed_repair_list,
                skipped_optional_resource_list,
                previous_repository_state["resource_state_list"] if previous_repository_state is not None else [],
            )
            participating_submodule_state_list = self._participating_submodule_state_list_prepare(
                repository_root,
                task_root,
                participating_submodule_path_set,
                performed_repair_list,
                skipped_optional_resource_list,
                (
                    previous_repository_state["participating_submodule_state_list"]
                    if previous_repository_state is not None
                    else []
                ),
            )
            repository_state_list.append(
                self._repository_state_get(
                    baseline_commit_by_main_root_map[main_root_text],
                    repository_root,
                    participating_submodule_state_list,
                    resource_state_list,
                    task_root,
                    temporary_exclude_by_main_root_map[main_root_text],
                    previous_repository_state,
                    performed_repair_list,
                )
            )

        state: WorktreeState = {
            "coordinating_repository": str(self._coordinating_repository),
            "fingerprint_schema_version": 2,
            "goal_fingerprint": previous_state["goal_fingerprint"] if previous_state is not None else "",
            "lifecycle_state": (
                previous_state["lifecycle_state"] if previous_state is not None else "repository_prepared"
            ),
            "prefix": self._prefix,
            "repository_state_list": repository_state_list,
            "schema_version": STATE_SCHEMA_VERSION,
            "specification_fingerprint": (
                previous_state["specification_fingerprint"]
                if previous_state is not None and previous_state["lifecycle_state"] in SEALED_LIFECYCLE_STATE_SET
                else self._path_fingerprint_get(self._specification_path)
            ),
            "specification_path": self._specification.as_posix(),
        }
        self._state_write(state, performed_repair_list)
        self._state_validate_observable(
            state,
            performed_repair_list,
            allow_artifact_drift=state["lifecycle_state"] not in SEALED_LIFECYCLE_STATE_SET,
        )
        self._state_write(state, performed_repair_list)
        return self._result_json_get(
            state,
            performed_repair_list,
            skipped_optional_resource_list,
        )

    def contracts_authored(self) -> str:
        """Record validated completion of stable-owner contract authoring.

        Returns:
            One machine-readable JSON result.
        """

        performed_repair_list: list[str] = []
        state = self._state_get(performed_repair_list)
        if state["lifecycle_state"] not in {"repository_prepared", "contracts_authored"}:
            raise WorktreeError(
                "Stable contracts may be recorded only from repository_prepared or contracts_authored state"
            )
        self._state_validate_observable(
            state,
            performed_repair_list,
            allow_artifact_drift=True,
        )
        state["lifecycle_state"] = "contracts_authored"
        state["specification_fingerprint"] = self._path_fingerprint_get(self._specification_path)
        self._state_write(state, performed_repair_list)
        return self._result_json_get(state, performed_repair_list, [])

    def main_leak_recover(
        self,
        main_repository: Path,
        path_list: list[Path],
    ) -> str:
        """Recover caller-attested task patches that leaked into one main worktree.

        Args:
            main_repository: Participating main worktree that received the leaked patch.
            path_list: Root-relative paths whose agent provenance the caller attests.

        Returns:
            One machine-readable JSON result.
        """

        if not path_list:
            raise WorktreeError("Main-leak recovery requires at least one explicit path")
        performed_repair_list: list[str] = []
        state = self._state_get(performed_repair_list)
        main_root = self._repository_root_validate(main_repository)
        repository_state = next(
            (item for item in state["repository_state_list"] if item["main_root"] == str(main_root)),
            None,
        )
        if repository_state is None:
            raise WorktreeError(f"Main-leak recovery repository is not part of the task: {main_root}")
        task_root = Path(repository_state["task_root"])
        owner_state_by_prefix_map: dict[str, RepositoryState] = {"": repository_state}
        owner_submodule_state_by_prefix_map: dict[str, ParticipatingSubmoduleState] = {}
        for submodule_state in repository_state["participating_submodule_state_list"]:
            prefix_text = submodule_state["path"]
            owner_state_by_prefix_map[prefix_text] = self._participating_submodule_repository_state_view(
                repository_state,
                submodule_state,
            )
            owner_submodule_state_by_prefix_map[prefix_text] = submodule_state
        task_changed_path_set_by_prefix_map: dict[str, set[str]] = {}
        main_status_by_path_map_by_prefix_map: dict[str, dict[str, str]] = {}
        touched_owner_prefix_set: set[str] = set()
        normalized_path_text_list: list[str] = []
        for raw_path in path_list:
            path_text = self._relative_path_validate(raw_path, "main-leak path").as_posix()
            if path_text in normalized_path_text_list:
                raise WorktreeError(f"Main-leak path is duplicated: {path_text}")
            self._non_overlapping_path_set_validate(
                {*normalized_path_text_list, path_text},
                "Main-leak paths",
            )
            candidate_path = PurePosixPath(path_text)
            owner_prefix = next(
                (
                    prefix_text
                    for prefix_text in sorted(
                        owner_submodule_state_by_prefix_map,
                        key=lambda item: len(PurePosixPath(item).parts),
                        reverse=True,
                    )
                    if PurePosixPath(prefix_text) in candidate_path.parents
                ),
                "",
            )
            owner_repository_state = owner_state_by_prefix_map[owner_prefix]
            owner_relative_path_text = (
                candidate_path.relative_to(PurePosixPath(owner_prefix)).as_posix() if owner_prefix else path_text
            )
            owner_main_root = Path(owner_repository_state["main_root"])
            owner_task_root = Path(owner_repository_state["task_root"])
            task_changed_path_set = task_changed_path_set_by_prefix_map.setdefault(
                owner_prefix,
                self._task_changed_path_set_get(
                    owner_repository_state["baseline_commit"],
                    owner_task_root,
                ),
            )
            main_status_by_path_map = main_status_by_path_map_by_prefix_map.setdefault(
                owner_prefix,
                self._status_by_path_map_get(owner_main_root),
            )
            resource_binding = self._resource_binding_optional_get(
                owner_repository_state,
                owner_relative_path_text,
            )
            if owner_relative_path_text not in task_changed_path_set and resource_binding is None:
                raise WorktreeError(f"Main-leak path is not one duplicated changed task path: {main_root / path_text}")
            if resource_binding is None and owner_relative_path_text not in main_status_by_path_map:
                raise WorktreeError(f"Main-leak path is not one duplicated changed task path: {main_root / path_text}")
            if resource_binding is None:
                attribution_main_root = owner_main_root
                attribution_task_root = owner_task_root
                attribution_path_text = owner_relative_path_text
            else:
                (
                    attribution_main_root,
                    attribution_task_root,
                    attribution_path_text,
                    _,
                    _,
                ) = resource_binding
            if attribution_path_text in self._index_nondefault_flag_by_path_map_get(attribution_main_root) or (
                resource_binding is None and main_status_by_path_map[owner_relative_path_text] == " A"
            ):
                raise WorktreeError(
                    f"Main-leak recovery cannot preserve non-default index flags: {main_root / path_text}"
                )
            main_fingerprint = self._path_fingerprint_get(attribution_main_root / attribution_path_text)
            if resource_binding is not None:
                task_fingerprint = self._resource_task_path_fingerprint_get(
                    attribution_main_root,
                    attribution_task_root,
                    attribution_path_text,
                    resource_binding[3],
                    resource_binding[4],
                )
            else:
                task_fingerprint = self._path_fingerprint_get(attribution_task_root / attribution_path_text)
            if main_fingerprint != task_fingerprint:
                raise WorktreeError(f"Main-leak path content differs from the task patch: {main_root / path_text}")
            if resource_binding is None:
                preimage = owner_repository_state["main_preimage_by_path_map"].get(owner_relative_path_text)
                accepted_index_entry_list = (
                    preimage["index_entry_list"]
                    if preimage is not None
                    else self._commit_index_entry_list_get(
                        owner_main_root,
                        owner_repository_state["main_commit"],
                        owner_relative_path_text,
                    )
                )
            else:
                accepted_index_entry_list = []
            if any(self._index_entry_fields_get(entry_text)[0] == "160000" for entry_text in accepted_index_entry_list):
                raise WorktreeError(
                    f"Main-leak recovery cannot mutate a recorded submodule boundary: {main_root / path_text}"
                )
            current_index_entry_list = self._index_entry_list_get(
                attribution_main_root,
                attribution_path_text,
            )
            if resource_binding is not None and current_index_entry_list:
                raise WorktreeError(
                    f"Main-leak recovery cannot mutate a staged resource source: {main_root / path_text}"
                )
            if resource_binding is None:
                task_working_index_entry_list = self._working_object_index_entry_list_get(
                    attribution_task_root,
                    attribution_path_text,
                )
                if tuple(current_index_entry_list) not in {
                    tuple(accepted_index_entry_list),
                    tuple(task_working_index_entry_list),
                }:
                    raise WorktreeError(
                        f"Main-leak path index differs from both the accepted preimage and exact task object: "
                        f"{main_root / path_text}"
                    )
            owner_repository_state["main_leak_fingerprint_by_path_map"][owner_relative_path_text] = (
                self._path_git_state_fingerprint_get(attribution_main_root, attribution_path_text)
            )
            touched_owner_prefix_set.add(owner_prefix)
            normalized_path_text_list.append(path_text)
        for owner_prefix in sorted(touched_owner_prefix_set):
            owner_repository_state = owner_state_by_prefix_map[owner_prefix]
            self._main_leak_recovery_preflight(
                owner_repository_state,
                performed_repair_list,
            )
            if owner_prefix:
                self._participating_submodule_repository_state_view_apply(
                    owner_submodule_state_by_prefix_map[owner_prefix],
                    owner_repository_state,
                )
        self._state_write(state, performed_repair_list)
        try:
            self._state_validate_observable(
                state,
                performed_repair_list,
                allow_artifact_drift=state["lifecycle_state"] not in SEALED_LIFECYCLE_STATE_SET,
            )
        except WorktreeError:
            self._state_write(state, performed_repair_list)
            raise
        self._state_write(state, performed_repair_list)
        return self._result_json_get(state, performed_repair_list, [])

    def main_commit_drift_accept(
        self,
        main_repository: Path,
        expected_commit: str,
        path_list: list[Path],
    ) -> str:
        """Accept exact overlapping committed main drift attested by the caller.

        Args:
            main_repository: Participating top-level or task-owned-submodule main owner root.
            expected_commit: Exact full current main commit accepted by the caller.
            path_list: Exact owner-relative overlapping paths accepted by the caller.

        Returns:
            One machine-readable JSON result.
        """

        if not path_list:
            raise WorktreeError("Main commit-drift acceptance requires at least one explicit path")
        if not _hex_digest_is_valid(expected_commit, {40, 64}):
            raise WorktreeError("Main commit-drift acceptance requires one exact full commit identity")
        normalized_path_text_list: list[str] = []
        for raw_path in path_list:
            path_text = self._relative_path_validate(raw_path, "main commit-drift path").as_posix()
            self._git_path_text_validate(path_text, "Main commit-drift path")
            if path_text in normalized_path_text_list:
                raise WorktreeError(f"Main commit-drift path is duplicated: {path_text}")
            self._non_overlapping_path_set_validate(
                {*normalized_path_text_list, path_text},
                "Main commit-drift paths",
            )
            normalized_path_text_list.append(path_text)
        normalized_path_text_list.sort()

        performed_repair_list: list[str] = []
        state = self._state_get(performed_repair_list)
        (
            main_root,
            task_root,
            repository_state,
            owner_submodule_state,
            delegated_submodule_path_set,
        ) = self._main_commit_drift_owner_get(
            state,
            main_repository,
        )
        current_main_commit = self._git_command.run(main_root, ["rev-parse", "HEAD"]).stdout.strip()
        if current_main_commit != expected_commit:
            raise WorktreeError(
                f"Main commit changed before caller-attested drift acceptance: "
                f"expected {expected_commit}, observed {current_main_commit}"
            )
        if (
            self._git_command.run(
                main_root,
                ["merge-base", "--is-ancestor", repository_state["main_commit"], current_main_commit],
                check=False,
            ).returncode
            != 0
        ):
            raise WorktreeError(f"Main history no longer descends from its recorded commit: {main_root}")

        matching_attestation = next(
            (item for item in repository_state["accepted_main_commit_drift_list"] if item["commit"] == expected_commit),
            None,
        )
        if matching_attestation is None or not set(normalized_path_text_list).issubset(
            matching_attestation["path_list"]
        ):
            unaccepted_overlap_set = self._unaccepted_main_commit_task_overlap_set_get(
                main_root,
                repository_state,
                task_root,
                current_main_commit,
                delegated_submodule_path_set=delegated_submodule_path_set,
            )
            if set(normalized_path_text_list) != unaccepted_overlap_set:
                missing_path_set = unaccepted_overlap_set - set(normalized_path_text_list)
                unexpected_path_set = set(normalized_path_text_list) - unaccepted_overlap_set
                detail_list = []
                if missing_path_set:
                    detail_list.append("missing " + ", ".join(sorted(missing_path_set)))
                if unexpected_path_set:
                    detail_list.append("unexpected " + ", ".join(sorted(unexpected_path_set)))
                raise WorktreeError(
                    "Main commit-drift paths must exactly match the current unaccepted overlap"
                    + (f": {'; '.join(detail_list)}" if detail_list else "")
                )
            if matching_attestation is None:
                repository_state["accepted_main_commit_drift_list"].append(
                    {
                        "commit": current_main_commit,
                        "path_list": normalized_path_text_list,
                    }
                )
            else:
                matching_attestation["path_list"] = sorted(
                    set(matching_attestation["path_list"]) | set(normalized_path_text_list)
                )
            repository_state["main_commit"] = current_main_commit
            if owner_submodule_state is not None:
                self._participating_submodule_repository_state_view_apply(
                    owner_submodule_state,
                    repository_state,
                )
            performed_repair_list.append(
                f"accepted caller-attested main commit drift: "
                f"{main_root}@{current_main_commit} ({', '.join(normalized_path_text_list)})"
            )
            self._state_write(state, performed_repair_list)

        try:
            self._state_validate_observable(
                state,
                performed_repair_list,
                allow_artifact_drift=state["lifecycle_state"] not in SEALED_LIFECYCLE_STATE_SET,
            )
        except WorktreeError:
            self._state_write(state, performed_repair_list)
            raise
        self._state_write(state, performed_repair_list)
        return self._result_json_get(state, performed_repair_list, [])

    def _main_commit_drift_owner_get(
        self,
        state: WorktreeState,
        main_repository: Path,
    ) -> tuple[Path, Path, RepositoryState, ParticipatingSubmoduleState | None, set[str]]:
        """Resolve one exact top-level or participating-submodule main owner."""

        absolute_main_root = Path(os.path.abspath(main_repository))
        main_root = main_repository.resolve()
        if main_repository.is_symlink() or not main_repository.is_dir() or absolute_main_root != main_root:
            raise WorktreeError(f"Main commit-drift repository is not one exact physical root: {main_repository}")
        for top_level_state in state["repository_state_list"]:
            if Path(top_level_state["main_root"]) == main_root:
                if self._repository_root_validate(main_root) != main_root:
                    raise WorktreeError(f"Main commit-drift repository identity changed: {main_root}")
                return (
                    main_root,
                    Path(top_level_state["task_root"]),
                    top_level_state,
                    None,
                    {item["path"] for item in top_level_state["participating_submodule_state_list"]},
                )
            for submodule_state in top_level_state["participating_submodule_state_list"]:
                submodule_path = PurePosixPath(submodule_state["path"])
                if Path(top_level_state["main_root"]) / submodule_path.as_posix() != main_root:
                    continue
                if not self._repository_is_exact_physical_root(main_root):
                    raise WorktreeError(f"Main commit-drift repository identity changed: {main_root}")
                delegated_submodule_path_set = {
                    PurePosixPath(candidate_state["path"]).relative_to(submodule_path).as_posix()
                    for candidate_state in top_level_state["participating_submodule_state_list"]
                    if candidate_state is not submodule_state
                    and submodule_path in PurePosixPath(candidate_state["path"]).parents
                }
                return (
                    main_root,
                    Path(top_level_state["task_root"]) / submodule_path.as_posix(),
                    self._participating_submodule_repository_state_view(
                        top_level_state,
                        submodule_state,
                    ),
                    submodule_state,
                    delegated_submodule_path_set,
                )
        raise WorktreeError(f"Main commit-drift repository is not part of the task: {main_root}")

    def seal(self, goal: Path) -> str:
        """Validate and seal the complete task artifact pair.

        Args:
            goal: Goal path relative to the coordinating repository.

        Returns:
            One machine-readable JSON result.
        """

        goal_relative_path = self._relative_path_validate(goal, "goal")
        if goal_relative_path != Path(".spec") / f"{self._prefix}{GOAL_SUFFIX}":
            raise WorktreeError(f"Goal filename must use task prefix {self._prefix}: {goal_relative_path}")
        goal_path = self._coordinating_repository / goal_relative_path
        if not goal_path.is_file():
            raise WorktreeError(f"Goal does not exist: {goal_path}")
        self._task_artifact_validate(goal_path, "Goal")
        performed_repair_list: list[str] = []
        state = self._state_get(performed_repair_list)
        if state["lifecycle_state"] == "active":
            raise WorktreeError("Cannot reseal task artifacts while the persistent goal is active")
        if state["lifecycle_state"] not in {"contracts_authored", "goal_ready"}:
            raise WorktreeError("Stable contracts must be recorded as contracts_authored before sealing task artifacts")
        self._state_validate_observable(
            state,
            performed_repair_list,
            allow_artifact_drift=True,
        )
        self._tracked_ignore_complete_validate(state)
        state["goal_fingerprint"] = self._path_fingerprint_get(goal_path)
        state["lifecycle_state"] = "goal_ready"
        state["specification_fingerprint"] = self._path_fingerprint_get(self._specification_path)
        self._state_write(state, performed_repair_list)
        return self._result_json_get(state, performed_repair_list, [])

    def activate(self) -> str:
        """Record activation after the caller creates the persistent goal.

        Returns:
            One machine-readable JSON result.
        """

        performed_repair_list: list[str] = []
        state = self._state_get(performed_repair_list)
        if state["lifecycle_state"] not in SEALED_LIFECYCLE_STATE_SET:
            raise WorktreeError("Task artifacts must be sealed before persistent-goal activation")
        self._state_validate_observable(state, performed_repair_list, allow_artifact_drift=False)
        state["lifecycle_state"] = "active"
        self._state_write(state, performed_repair_list)
        return self._result_json_get(state, performed_repair_list, [])

    def validate(self, required_state: str) -> str:
        """Validate and repair one recorded task worktree set.

        Args:
            required_state: Minimum required lifecycle state.

        Returns:
            One machine-readable JSON result.
        """

        if required_state not in LIFECYCLE_INDEX_BY_NAME_MAP:
            raise WorktreeError(f"Unknown required lifecycle state: {required_state}")
        performed_repair_list: list[str] = []
        state = self._state_get(performed_repair_list)
        self._state_validate_observable(state, performed_repair_list, allow_artifact_drift=False)
        if LIFECYCLE_INDEX_BY_NAME_MAP[state["lifecycle_state"]] < LIFECYCLE_INDEX_BY_NAME_MAP[required_state]:
            raise WorktreeError(
                f"Lifecycle state {state['lifecycle_state']} does not satisfy required state {required_state}"
            )
        self._state_write(state, performed_repair_list)
        return self._result_json_get(state, performed_repair_list, [])

    def _branch_name_validate(self) -> None:
        """Validate the common prefix as one unchanged Git branch name."""

        result = self._git_command.run(
            self._coordinating_repository,
            ["check-ref-format", "--branch", self._prefix],
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != self._prefix:
            raise WorktreeError(f"Task prefix is not a valid unchanged Git branch name: {self._prefix}")
        if "/" in self._prefix or self._prefix in {"", ".", ".."}:
            raise WorktreeError(f"Task prefix must be one filesystem basename: {self._prefix}")

    def _git_common_directory_get(self, repository_root: Path) -> Path:
        """Resolve one repository's Git common directory.

        Args:
            repository_root: Repository root.

        Returns:
            Canonical Git common-directory path.
        """

        raw_path = Path(self._git_command.run(repository_root, ["rev-parse", "--git-common-dir"]).stdout.strip())
        if not raw_path.is_absolute():
            raw_path = repository_root / raw_path
        return raw_path.resolve()

    def _repository_is_exact_physical_root(self, repository_root: Path) -> bool:
        """Return whether Git and the filesystem identify one non-symlink root."""

        absolute_root = Path(os.path.abspath(repository_root))
        if repository_root.is_symlink() or not repository_root.is_dir() or absolute_root != repository_root.resolve():
            return False
        top_level_result = self._git_command.run(
            repository_root,
            ["rev-parse", "--show-toplevel"],
            check=False,
        )
        if top_level_result.returncode != 0 or not top_level_result.stdout.strip():
            return False
        raw_top_level = Path(top_level_result.stdout.strip())
        if not raw_top_level.is_absolute():
            raw_top_level = repository_root / raw_top_level
        return Path(os.path.abspath(raw_top_level)) == absolute_root

    def _git_path_get(self, task_root: Path, relative_path: Path) -> Path:
        """Resolve one path below a worktree Git administration directory.

        Args:
            task_root: Exact task-worktree root.
            relative_path: Git administration path to resolve.

        Returns:
            Canonical Git administration path.
        """

        raw_path = Path(
            self._git_command.run(
                task_root,
                ["rev-parse", "--git-path", relative_path.as_posix()],
            ).stdout.strip()
        )
        if not raw_path.is_absolute():
            raw_path = task_root / raw_path
        absolute_path = Path(os.path.abspath(raw_path))
        administration_root = Path(
            self._git_command.run(task_root, ["rev-parse", "--absolute-git-dir"]).stdout.strip()
        ).resolve()
        try:
            relative_administration_path = absolute_path.relative_to(administration_root)
        except ValueError as exc:
            raise WorktreeError(f"Private Git path escapes worktree administration: {absolute_path}") from exc
        current_path = administration_root
        for path_part in relative_administration_path.parts[:-1]:
            current_path /= path_part
            if os.path.lexists(current_path) and (current_path.is_symlink() or not current_path.is_dir()):
                raise WorktreeError(f"Private Git path has an unsafe parent: {current_path}")
        return absolute_path

    def _manifest_get(self, manifest_path: Path, task_root: Path) -> dict[str, list[str]]:
        """Load and validate one closed bootstrap manifest.

        Args:
            manifest_path: Manifest path.
            task_root: Owning task-worktree root.

        Returns:
            Validated resource paths by resource class.
        """

        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or manifest_path.stat(follow_symlinks=False).st_nlink != 1
        ):
            raise WorktreeError(f"Bootstrap manifest must be one physical ordinary file: {manifest_path}")
        try:
            payload = tomllib.loads(self._utf8_text_get(manifest_path, "Bootstrap manifest"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise WorktreeError(f"Cannot load bootstrap manifest {manifest_path}: {exc}") from exc
        if (
            set(payload) != {"resource", "schema_version"}
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
        ):
            raise WorktreeError(f"Bootstrap manifest has an unsupported root schema: {manifest_path}")
        resource_payload = payload.get("resource")
        if not isinstance(resource_payload, dict) or set(resource_payload) != MANIFEST_RESOURCE_KEY_SET:
            raise WorktreeError(f"Bootstrap manifest has an unsupported resource schema: {manifest_path}")
        resource_by_class_map: dict[str, list[str]] = {}
        all_path_list: list[PurePosixPath] = []
        submodule_path_list = self._submodule_path_list_get(task_root)
        for resource_class in sorted(MANIFEST_RESOURCE_KEY_SET):
            raw_path_list = resource_payload[resource_class]
            if not isinstance(raw_path_list, list) or any(not isinstance(item, str) for item in raw_path_list):
                raise WorktreeError(f"Manifest field {resource_class} must be a string list: {manifest_path}")
            validated_path_list: list[str] = []
            for path_text in raw_path_list:
                resource_path = self._manifest_resource_path_validate(path_text)
                if any(
                    resource_path == submodule_path
                    or submodule_path in resource_path.parents
                    or resource_path in submodule_path.parents
                    for submodule_path in submodule_path_list
                ):
                    raise WorktreeError(f"Manifest path crosses a submodule boundary: {path_text}")
                validated_path_list.append(resource_path.as_posix())
                all_path_list.append(resource_path)
            resource_by_class_map[resource_class] = validated_path_list
        if len(all_path_list) != len(set(all_path_list)):
            raise WorktreeError(f"Bootstrap manifest contains duplicate resource paths: {manifest_path}")
        for index, resource_path in enumerate(all_path_list):
            for other_path in all_path_list[index + 1 :]:
                if resource_path in other_path.parents or other_path in resource_path.parents:
                    raise WorktreeError(
                        f"Bootstrap manifest contains overlapping resource paths: {resource_path} and {other_path}"
                    )
        return resource_by_class_map

    def _empty_manifest_fingerprint_get(self) -> str:
        """Return the exact fingerprint of a provider-created empty manifest."""

        return self._regular_file_fingerprint_get(
            EMPTY_MANIFEST_TEXT.encode(),
            0o644,
        )

    def _regular_file_fingerprint_get(self, content: bytes, mode: int) -> str:
        """Return the object fingerprint for exact ordinary-file bytes and mode."""

        digest = hashlib.sha256()
        digest.update(b".")
        digest.update(str(mode).encode())
        digest.update(b"file")
        digest.update(content)
        return digest.hexdigest()

    def _empty_manifest_write(self, manifest_path: Path) -> None:
        """Write one deterministic provider-owned empty manifest."""

        self._ordinary_text_atomic_write(
            manifest_path.parent,
            manifest_path,
            EMPTY_MANIFEST_TEXT,
            forced_mode=0o644,
        )
        if self._path_fingerprint_get(manifest_path) != self._empty_manifest_fingerprint_get():
            raise WorktreeError(f"Cannot create an exact empty bootstrap manifest: {manifest_path}")

    def _initial_manifest_owner_marker_path_get(self, task_root: Path) -> Path:
        """Return the private ownership marker for one initial empty manifest."""

        return self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / INITIAL_MANIFEST_OWNER_MARKER_FILENAME,
        )

    def _initial_manifest_owner_fingerprint_get(self, task_root: Path) -> str | None:
        """Return validated provider ownership for one initial manifest."""

        marker_path = self._initial_manifest_owner_marker_path_get(task_root)
        if not os.path.lexists(marker_path):
            return None
        if marker_path.is_symlink() or not marker_path.is_file():
            raise WorktreeError(f"Initial-manifest ownership marker is damaged: {marker_path}")
        fingerprint = self._utf8_text_get(
            marker_path,
            "Initial-manifest ownership marker",
        ).strip()
        if fingerprint != self._empty_manifest_fingerprint_get():
            raise WorktreeError(f"Initial-manifest ownership marker is invalid: {marker_path}")
        return fingerprint

    def _initial_manifest_create(
        self,
        task_root: Path,
        performed_repair_list: list[str],
        *,
        report_text: str,
    ) -> None:
        """Create or resume one provider-owned first manifest."""

        manifest_path = task_root / MANIFEST_NAME
        expected_fingerprint = self._empty_manifest_fingerprint_get()
        marker_path = self._initial_manifest_owner_marker_path_get(task_root)
        observed_owner_fingerprint = self._initial_manifest_owner_fingerprint_get(task_root)
        if observed_owner_fingerprint is None:
            self._private_text_atomic_write(marker_path, f"{expected_fingerprint}\n")
        if not os.path.lexists(manifest_path):
            self._empty_manifest_write(manifest_path)
            performed_repair_list.append(f"{report_text}: {manifest_path}")
        elif self._path_fingerprint_get(manifest_path) != expected_fingerprint:
            raise WorktreeError(f"Provider-owned initial manifest contains independent content: {manifest_path}")

    def _initial_manifest_restore(
        self,
        task_root: Path,
        performed_repair_list: list[str],
        *,
        report_text: str,
    ) -> None:
        """Restore a missing manifest only from durable provider ownership."""

        manifest_path = task_root / MANIFEST_NAME
        if self._initial_manifest_owner_fingerprint_get(task_root) is None:
            raise WorktreeError(f"Recorded bootstrap manifest is missing and is not provider-owned: {manifest_path}")
        self._empty_manifest_write(manifest_path)
        performed_repair_list.append(f"{report_text}: {manifest_path}")

    def _initial_manifest_owner_retire_if_changed(
        self,
        task_root: Path,
        performed_repair_list: list[str],
    ) -> None:
        """Retire recreation authority after any manifest customization."""

        owner_fingerprint = self._initial_manifest_owner_fingerprint_get(task_root)
        if owner_fingerprint is None:
            return
        manifest_path = task_root / MANIFEST_NAME
        if self._path_fingerprint_get(manifest_path) == owner_fingerprint:
            return
        marker_path = self._initial_manifest_owner_marker_path_get(task_root)
        self._path_remove(marker_path)
        performed_repair_list.append(f"retired initial-manifest recreation ownership: {manifest_path}")

    def _initial_manifest_owner_backfill_if_proven(
        self,
        task_root: Path,
        baseline_commit: str,
        recorded_manifest_fingerprint: str,
        resource_state_list: list[ResourceState],
        performed_repair_list: list[str],
    ) -> None:
        """Backfill ownership for pre-marker canonical provider state only."""

        if self._initial_manifest_owner_fingerprint_get(task_root) is not None:
            return
        if (
            recorded_manifest_fingerprint != self._empty_manifest_fingerprint_get()
            or resource_state_list
            or self._commit_index_entry_list_get(task_root, baseline_commit, MANIFEST_NAME)
        ):
            return
        marker_path = self._initial_manifest_owner_marker_path_get(task_root)
        self._private_text_atomic_write(
            marker_path,
            f"{self._empty_manifest_fingerprint_get()}\n",
        )
        performed_repair_list.append(f"backfilled initial-manifest recreation ownership: {task_root / MANIFEST_NAME}")

    def _manifest_resource_path_validate(self, path_text: str) -> PurePosixPath:
        """Validate one manifest resource path.

        Args:
            path_text: Raw manifest path.

        Returns:
            Canonical relative POSIX path.
        """

        if (
            not path_text
            or "\0" in path_text
            or "\n" in path_text
            or "\r" in path_text
            or "\\" in path_text
            or path_text.startswith("/")
            or any(character in path_text for character in "*?[]")
        ):
            raise WorktreeError(f"Manifest resource path must be one relative POSIX path: {path_text!r}")
        raw_part_list = path_text.split("/")
        if any(part in {"", ".", ".."} for part in raw_part_list):
            raise WorktreeError(f"Manifest resource path contains a forbidden segment: {path_text!r}")
        resource_path = PurePosixPath(path_text)
        reserved_path_set = {
            PurePosixPath(".git"),
            PurePosixPath(".spec"),
            PurePosixPath(WORKTREE_CONTAINER_NAME),
            PurePosixPath(MANIFEST_NAME),
        }
        if any(
            resource_path == reserved_path or reserved_path in resource_path.parents
            for reserved_path in reserved_path_set
        ):
            raise WorktreeError(f"Manifest resource path is reserved: {path_text}")
        return resource_path

    def _path_copy(self, source_path: Path, destination_path: Path) -> None:
        """Copy one validated ordinary filesystem object.

        Args:
            source_path: Main-worktree source path.
            destination_path: Task-worktree destination path.
        """

        self._path_copy_source_validate(source_path)
        source_fingerprint = self._path_fingerprint_get(source_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_symlink():
            destination_path.symlink_to(os.readlink(source_path))
        elif source_path.is_dir():
            shutil.copytree(source_path, destination_path, symlinks=True)
            self._copied_absolute_link_rewrite(source_path, destination_path)
        else:
            shutil.copy2(source_path, destination_path, follow_symlinks=False)
        if self._path_fingerprint_get(source_path) != source_fingerprint:
            self._path_remove(destination_path)
            raise WorktreeError(f"Copy source changed during materialization: {source_path}")

    def _copied_absolute_link_rewrite(self, source_root: Path, destination_root: Path) -> None:
        """Rewrite safe absolute source links to isolated relative destination links.

        Args:
            source_root: Copied source-directory root.
            destination_root: Copied destination-directory root.
        """

        for source_candidate in sorted(source_root.rglob("*")):
            if not source_candidate.is_symlink():
                continue
            raw_target = Path(os.readlink(source_candidate))
            if not raw_target.is_absolute():
                continue
            try:
                target_relative_path = source_candidate.resolve(strict=True).relative_to(source_root.resolve())
            except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                raise WorktreeError(f"Copy source symbolic link is unresolved or escapes: {source_candidate}") from exc
            destination_candidate = destination_root / source_candidate.relative_to(source_root)
            destination_target = destination_root / target_relative_path
            destination_candidate.unlink()
            destination_candidate.symlink_to(os.path.relpath(destination_target, start=destination_candidate.parent))

    def _path_clone(self, source_path: Path, destination_path: Path) -> None:
        """Clone one ordinary object without following symbolic links.

        Args:
            source_path: Existing source object.
            destination_path: Absent destination object.
        """

        mode = source_path.lstat().st_mode
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(mode):
            destination_path.symlink_to(os.readlink(source_path))
        elif stat.S_ISDIR(mode):
            for candidate_path in [source_path, *sorted(source_path.rglob("*"))]:
                candidate_mode = candidate_path.lstat().st_mode
                if not (stat.S_ISREG(candidate_mode) or stat.S_ISDIR(candidate_mode) or stat.S_ISLNK(candidate_mode)):
                    raise WorktreeError(f"Cannot preserve special main-worktree object: {candidate_path}")
            shutil.copytree(source_path, destination_path, symlinks=True)
        elif stat.S_ISREG(mode):
            shutil.copy2(source_path, destination_path, follow_symlinks=False)
        else:
            raise WorktreeError(f"Cannot preserve special main-worktree object: {source_path}")

    def _index_entry_list_get(self, repository_root: Path, path_text: str) -> list[str]:
        """Return exact index entries for one root-relative path.

        Args:
            repository_root: Exact repository root.
            path_text: Root-relative path.

        Returns:
            Raw mode, object, and stage fields.
        """

        result = self._git_command.run(
            repository_root,
            ["ls-files", "--stage", "-z", "--", path_text],
        )
        entry_list: list[str] = []
        for item in result.stdout.split("\0"):
            if not item:
                continue
            entry_text, separator, observed_path_text = item.partition("\t")
            if separator != "\t" or observed_path_text != path_text or len(entry_text.split()) != 3:
                raise WorktreeError(f"Cannot preserve index state for {repository_root / path_text}")
            entry_list.append(entry_text)
        return entry_list

    def _index_entry_fields_get(self, entry_text: str) -> tuple[str, str, str]:
        """Parse and validate one persisted index entry.

        Args:
            entry_text: Raw mode, object, and stage fields.

        Returns:
            Validated mode, object ID, and stage.
        """

        field_list = entry_text.split()
        if len(field_list) != 3:
            raise WorktreeError(f"Invalid persisted index entry: {entry_text!r}")
        mode_text, object_id, stage_text = field_list
        if mode_text not in {"100644", "100755", "120000", "160000"}:
            raise WorktreeError(f"Unsupported persisted index mode: {entry_text!r}")
        if not _hex_digest_is_valid(object_id, {40, 64}):
            raise WorktreeError(f"Invalid persisted index object ID: {entry_text!r}")
        if stage_text not in {"0", "1", "2", "3"}:
            raise WorktreeError(f"Invalid persisted index stage: {entry_text!r}")
        return mode_text, object_id, stage_text

    def _index_entry_list_structure_validate(self, entry_list: list[str]) -> None:
        """Validate stage uniqueness and shape for one path's persisted index.

        Args:
            entry_list: Persisted mode, object, and stage fields.
        """

        stage_set: set[str] = set()
        for entry_text in entry_list:
            _, _, stage_text = self._index_entry_fields_get(entry_text)
            if stage_text in stage_set:
                raise WorktreeError(f"Persisted index contains a duplicate stage: {entry_text!r}")
            stage_set.add(stage_text)
        if "0" in stage_set and len(stage_set) != 1:
            raise WorktreeError("Persisted index mixes a stage-zero entry with conflict stages")

    def _blob_object_id_get(self, repository_root: Path, content: bytes) -> str:
        """Return the repository-format blob ID for exact bytes without writing it.

        Args:
            repository_root: Repository whose object format applies.
            content: Exact blob bytes.

        Returns:
            Lowercase object ID.
        """

        return (
            self._git_command.run_bytes(
                repository_root,
                ["hash-object", "--stdin"],
                input_bytes=content,
            )
            .stdout.decode("ascii")
            .strip()
        )

    def _index_preimage_blob_list_prepare(
        self,
        repository_root: Path,
        snapshot_directory: Path,
        entry_list: list[str],
        performed_repair_list: list[str],
    ) -> None:
        """Persist and, when needed, rehydrate every non-gitlink index blob.

        Args:
            repository_root: Repository whose object database owns the entries.
            snapshot_directory: Private preimage directory for one path.
            entry_list: Exact persisted index entries.
            performed_repair_list: Mutable repair report.
        """

        self._index_entry_list_structure_validate(entry_list)
        content_by_snapshot_name_map: dict[str, bytes] = {}
        for entry_text in entry_list:
            mode_text, object_id, stage_text = self._index_entry_fields_get(entry_text)
            if mode_text == "160000":
                continue
            snapshot_name = f"{stage_text}-{mode_text}-{object_id}.blob"
            snapshot_path = snapshot_directory / "index" / snapshot_name
            snapshot_content: bytes | None = None
            if snapshot_path.is_file() and not snapshot_path.is_symlink():
                candidate_content = snapshot_path.read_bytes()
                if self._blob_object_id_get(repository_root, candidate_content) == object_id:
                    snapshot_content = candidate_content
            object_result = self._git_command.run_bytes(
                repository_root,
                ["cat-file", "blob", object_id],
                check=False,
            )
            if snapshot_content is None:
                if object_result.returncode != 0:
                    raise WorktreeError(f"Persisted index blob and its private preimage are unavailable: {object_id}")
                snapshot_content = object_result.stdout
                if self._blob_object_id_get(repository_root, snapshot_content) != object_id:
                    raise WorktreeError(f"Git returned inconsistent persisted index blob content: {object_id}")
                performed_repair_list.append(f"reconstructed private index preimage blob: {object_id}")
            content_by_snapshot_name_map[snapshot_name] = snapshot_content
        index_snapshot_directory = snapshot_directory / "index"
        if os.path.lexists(index_snapshot_directory) and (
            index_snapshot_directory.is_symlink() or not index_snapshot_directory.is_dir()
        ):
            raise WorktreeError(f"Private index preimage is not one physical directory: {index_snapshot_directory}")
        index_snapshot_directory.mkdir(parents=True, exist_ok=True)
        for snapshot_name, snapshot_content in content_by_snapshot_name_map.items():
            snapshot_path = index_snapshot_directory / snapshot_name
            if (
                not snapshot_path.is_file()
                or snapshot_path.is_symlink()
                or snapshot_path.read_bytes() != snapshot_content
            ):
                self._private_bytes_atomic_write(snapshot_path, snapshot_content)
            object_id = snapshot_name.removesuffix(".blob").rsplit("-", 1)[-1]
            object_result = self._git_command.run_bytes(
                repository_root,
                ["cat-file", "-e", object_id],
                check=False,
            )
            if object_result.returncode != 0:
                restored_object_id = (
                    self._git_command.run_bytes(
                        repository_root,
                        ["hash-object", "-w", "--stdin"],
                        input_bytes=snapshot_content,
                    )
                    .stdout.decode("ascii")
                    .strip()
                )
                if restored_object_id != object_id:
                    raise WorktreeError(f"Cannot restore exact persisted index blob: {object_id}")
                performed_repair_list.append(f"repopulated missing persisted index blob: {object_id}")
        expected_snapshot_name_set = set(content_by_snapshot_name_map)
        for candidate_path in index_snapshot_directory.iterdir():
            if candidate_path.name not in expected_snapshot_name_set:
                raise WorktreeError(f"Private index preimage contains unknown content: {candidate_path}")

    def _commit_index_entry_list_get(
        self,
        repository_root: Path,
        commit: str,
        path_text: str,
    ) -> list[str]:
        """Return the stage-zero index representation of one committed path.

        Args:
            repository_root: Exact repository root.
            commit: Commit whose tree is authoritative.
            path_text: Root-relative Git path.

        Returns:
            Empty or one stage-zero entry.
        """

        result = self._git_command.run(
            repository_root,
            ["ls-tree", "-z", commit, "--", path_text],
        )
        item_list = [item for item in result.stdout.split("\0") if item]
        if not item_list:
            return []
        if len(item_list) != 1:
            raise WorktreeError(f"Committed path is not one exact index object: {repository_root / path_text}")
        metadata_text, separator, observed_path_text = item_list[0].partition("\t")
        metadata_field_list = metadata_text.split()
        if separator != "\t" or observed_path_text != path_text or len(metadata_field_list) != 3:
            raise WorktreeError(f"Cannot parse committed index state: {repository_root / path_text}")
        mode_text, _, object_id = metadata_field_list
        entry_text = f"{mode_text} {object_id} 0"
        self._index_entry_fields_get(entry_text)
        return [entry_text]

    def _working_object_index_entry_list_get(self, repository_root: Path, path_text: str) -> list[str]:
        """Return an index representation of one current working-tree object.

        Args:
            repository_root: Exact repository root.
            path_text: Root-relative path.

        Returns:
            Empty or one stage-zero regular-file or symbolic-link entry.
        """

        path = repository_root / path_text
        if not os.path.lexists(path):
            return []
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            mode_text = "100755" if mode & stat.S_IXUSR else "100644"
            content = path.read_bytes()
        elif stat.S_ISLNK(mode):
            mode_text = "120000"
            content = os.fsencode(os.readlink(path))
        else:
            raise WorktreeError(f"Main-leak index attribution does not support this task object: {path}")
        object_id = self._blob_object_id_get(repository_root, content)
        return [f"{mode_text} {object_id} 0"]

    def _path_git_state_fingerprint_get(self, repository_root: Path, path_text: str) -> str:
        """Fingerprint one path's index entries and working object.

        Args:
            repository_root: Exact repository root.
            path_text: Root-relative path.

        Returns:
            SHA-256 state fingerprint.
        """

        digest = hashlib.sha256()
        for index_entry in self._index_entry_list_get(repository_root, path_text):
            digest.update(index_entry.encode())
            digest.update(b"\0")
        index_flag = self._index_nondefault_flag_by_path_map_get(repository_root).get(path_text, "")
        digest.update(index_flag.encode())
        digest.update(b"\0")
        digest.update(self._path_fingerprint_get(repository_root / path_text).encode())
        return digest.hexdigest()

    def _legacy_path_git_state_fingerprint_get(
        self,
        repository_root: Path,
        path_text: str,
    ) -> str:
        """Return one schema-v1 Git-state fingerprint for state migration only."""

        digest = hashlib.sha256()
        for index_entry in self._index_entry_list_get(repository_root, path_text):
            digest.update(index_entry.encode())
            digest.update(b"\0")
        index_flag = self._index_nondefault_flag_by_path_map_get(repository_root).get(path_text, "")
        digest.update(index_flag.encode())
        digest.update(b"\0")
        digest.update(self._legacy_path_fingerprint_get(repository_root / path_text).encode())
        return digest.hexdigest()

    def _index_nondefault_flag_by_path_map_get(self, repository_root: Path) -> dict[str, str]:
        """Return assume-unchanged and skip-worktree tags by exact path.

        Args:
            repository_root: Exact repository root.

        Returns:
            Stable non-default index tag by root-relative path.
        """

        result = self._git_command.run(
            repository_root,
            ["ls-files", "-v", "-z"],
        )
        flag_by_path_map: dict[str, str] = {}
        for item in result.stdout.split("\0"):
            if not item:
                continue
            tag, separator, path_text = item.partition(" ")
            if separator != " " or len(tag) != 1 or not path_text:
                raise WorktreeError(f"Cannot parse index flag entry in {repository_root}: {item!r}")
            if tag.islower() or tag == "S":
                flag_by_path_map[path_text] = tag
        return flag_by_path_map

    def _index_entry_list_restore(
        self,
        repository_root: Path,
        path_text: str,
        index_entry_list: list[str],
    ) -> None:
        """Restore exact index entries for one root-relative path.

        Args:
            repository_root: Exact repository root.
            path_text: Root-relative path.
            index_entry_list: Recorded mode, object, and stage fields.
        """

        object_format = self._git_command.run(
            repository_root,
            ["rev-parse", "--show-object-format"],
        ).stdout.strip()
        zero_object_id_by_format_map = {
            "sha1": "0" * 40,
            "sha256": "0" * 64,
        }
        try:
            zero_object_id = zero_object_id_by_format_map[object_format]
        except KeyError as exc:
            raise WorktreeError(f"Unsupported Git object format in {repository_root}: {object_format}") from exc
        index_info_item_list: list[str] = [f"0 {zero_object_id}\t{path_text}\0"]
        for entry_text in index_entry_list:
            mode_text, object_id, stage_text = entry_text.split()
            if stage_text == "0":
                index_info_item_list.append(f"{mode_text} {object_id}\t{path_text}\0")
            else:
                index_info_item_list.append(f"{mode_text} {object_id} {stage_text}\t{path_text}\0")
        self._git_command.run(
            repository_root,
            ["update-index", "-z", "--index-info"],
            input_text="".join(index_info_item_list),
        )

    def _main_preimage_directory_get(self, task_root: Path) -> Path:
        """Return the private main-preimage directory for one task worktree.

        Args:
            task_root: Exact task-worktree root.

        Returns:
            Private provider-owned directory.
        """

        preimage_directory = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "main-preimage-v1",
        )
        if os.path.lexists(preimage_directory) and (preimage_directory.is_symlink() or not preimage_directory.is_dir()):
            raise WorktreeError(f"Private main-preimage path is not one directory: {preimage_directory}")
        return preimage_directory

    def _main_preimage_by_path_map_refresh(
        self,
        main_root: Path,
        task_root: Path,
        current_status_by_path_map: dict[str, str],
        current_fingerprint_by_path_map: dict[str, str],
        previous_status_by_path_map: dict[str, str],
        previous_fingerprint_by_path_map: dict[str, str],
        previous_preimage_by_path_map: dict[str, MainPathPreimage],
        performed_repair_list: list[str],
    ) -> dict[str, MainPathPreimage]:
        """Refresh private preimages for accepted unrelated main state.

        Args:
            main_root: Main-worktree root.
            task_root: Task-worktree root.
            current_status_by_path_map: Accepted current status.
            current_fingerprint_by_path_map: Accepted current Git-state fingerprints.
            previous_status_by_path_map: Previously accepted status.
            previous_fingerprint_by_path_map: Previously accepted Git-state fingerprints.
            previous_preimage_by_path_map: Previously captured preimages.
            performed_repair_list: Mutable repair report.

        Returns:
            Complete current preimage metadata.
        """

        preimage_directory = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "main-preimage-v1",
        )
        if os.path.lexists(preimage_directory) and (preimage_directory.is_symlink() or not preimage_directory.is_dir()):
            raise WorktreeError(f"Private main-preimage path is not one physical directory: {preimage_directory}")
        self._private_clone_staging_list_reconcile(
            task_root,
            "main-preimage",
            performed_repair_list,
        )
        current_preimage_by_path_map: dict[str, MainPathPreimage] = {}
        for path_text in sorted(current_status_by_path_map):
            previous_preimage = previous_preimage_by_path_map.get(path_text)
            if (
                previous_status_by_path_map.get(path_text) == current_status_by_path_map[path_text]
                and previous_fingerprint_by_path_map.get(path_text) == current_fingerprint_by_path_map[path_text]
                and previous_preimage is not None
            ):
                if (
                    self._path_git_state_fingerprint_get(main_root, path_text)
                    != current_fingerprint_by_path_map[path_text]
                ):
                    raise WorktreeError(
                        f"Main state changed while validating its private preimage: {main_root / path_text}"
                    )
                snapshot_directory = preimage_directory / previous_preimage["snapshot_name"]
                self._index_preimage_blob_list_prepare(
                    main_root,
                    snapshot_directory,
                    previous_preimage["index_entry_list"],
                    performed_repair_list,
                )
                snapshot_path = snapshot_directory / "working"
                snapshot_is_valid = (
                    previous_preimage["working_present"] == os.path.lexists(snapshot_path)
                    and (
                        not previous_preimage["working_present"]
                        or self._path_fingerprint_get(snapshot_path) == previous_preimage["working_fingerprint"]
                    )
                    and self._index_entry_list_get(main_root, path_text) == previous_preimage["index_entry_list"]
                    and self._path_fingerprint_get(main_root / path_text) == previous_preimage["working_fingerprint"]
                    and self._path_git_state_fingerprint_get(main_root, path_text)
                    == current_fingerprint_by_path_map[path_text]
                )
                if snapshot_is_valid:
                    current_preimage_by_path_map[path_text] = previous_preimage
                    continue
                performed_repair_list.append(f"reconstructed damaged private main preimage: {main_root / path_text}")
            snapshot_name = hashlib.sha256(os.fsencode(path_text)).hexdigest()
            snapshot_directory = preimage_directory / snapshot_name
            source_path = main_root / path_text
            if self._path_git_state_fingerprint_get(main_root, path_text) != current_fingerprint_by_path_map[path_text]:
                raise WorktreeError(f"Main state changed before private preimage capture: {source_path}")
            index_entry_list = self._index_entry_list_get(main_root, path_text)
            capture_directory, capture_marker_path = self._private_clone_staging_directory_create(
                task_root,
                "main-preimage",
                path_text,
            )
            capture_published = False
            try:
                working_present = os.path.lexists(source_path)
                if working_present:
                    self._path_clone(source_path, capture_directory / "working")
                working_fingerprint = self._path_fingerprint_get(source_path)
                if working_present and (
                    self._path_fingerprint_get(capture_directory / "working") != working_fingerprint
                ):
                    raise WorktreeError(f"Main working object changed during private preimage capture: {source_path}")
                self._index_preimage_blob_list_prepare(
                    main_root,
                    capture_directory,
                    index_entry_list,
                    performed_repair_list,
                )
                if (
                    self._path_git_state_fingerprint_get(main_root, path_text)
                    != current_fingerprint_by_path_map[path_text]
                    or self._status_by_path_map_get(main_root).get(path_text) != current_status_by_path_map[path_text]
                ):
                    raise WorktreeError(f"Main state changed during private preimage capture: {source_path}")
                if os.path.lexists(snapshot_directory):
                    if previous_preimage is None:
                        raise WorktreeError(f"Unrecorded private main preimage already exists: {snapshot_directory}")
                    self._main_preimage_staging_directory_discard(
                        main_root,
                        snapshot_directory,
                        previous_preimage["index_entry_list"],
                        previous_preimage["working_fingerprint"],
                        "Private main preimage",
                    )
                snapshot_directory.parent.mkdir(parents=True, exist_ok=True)
                capture_directory.replace(snapshot_directory)
                capture_published = True
                capture_marker_path.unlink()
                self._directory_fsync(snapshot_directory.parent)
            except BaseException:
                if not capture_published and os.path.lexists(capture_directory):
                    self._path_remove(capture_directory)
                if capture_marker_path.is_symlink():
                    capture_marker_path.unlink()
                raise
            current_preimage_by_path_map[path_text] = {
                "index_entry_list": index_entry_list,
                "snapshot_name": snapshot_name,
                "working_fingerprint": working_fingerprint,
                "working_present": working_present,
            }
        retained_snapshot_name_set = {item["snapshot_name"] for item in current_preimage_by_path_map.values()}
        previous_preimage_by_snapshot_name_map = {
            item["snapshot_name"]: item for item in previous_preimage_by_path_map.values()
        }
        owned_preimage_by_snapshot_name_map = {
            **previous_preimage_by_snapshot_name_map,
            **{item["snapshot_name"]: item for item in current_preimage_by_path_map.values()},
        }
        owned_snapshot_name_by_capture_name_map = {
            f"{snapshot_name}.capture": snapshot_name for snapshot_name in owned_preimage_by_snapshot_name_map
        }
        if preimage_directory.is_dir():
            for candidate_path in preimage_directory.iterdir():
                if candidate_path.name in retained_snapshot_name_set:
                    continue
                if candidate_path.name in previous_preimage_by_snapshot_name_map:
                    previous_preimage = previous_preimage_by_snapshot_name_map[candidate_path.name]
                    self._main_preimage_staging_directory_discard(
                        main_root,
                        candidate_path,
                        previous_preimage["index_entry_list"],
                        previous_preimage["working_fingerprint"],
                        "Obsolete private main preimage",
                    )
                    performed_repair_list.append(f"retired obsolete private main preimage: {candidate_path}")
                    continue
                owned_snapshot_name = owned_snapshot_name_by_capture_name_map.get(candidate_path.name)
                if owned_snapshot_name is not None:
                    owned_preimage = owned_preimage_by_snapshot_name_map[owned_snapshot_name]
                    self._main_preimage_staging_directory_discard(
                        main_root,
                        candidate_path,
                        owned_preimage["index_entry_list"],
                        owned_preimage["working_fingerprint"],
                        "Obsolete private main-preimage capture",
                    )
                    performed_repair_list.append(f"retired obsolete private main-preimage capture: {candidate_path}")
                    continue
                raise WorktreeError(f"Private main-preimage owner contains unknown content: {candidate_path}")
        return current_preimage_by_path_map

    def _main_path_preimage_restore(
        self,
        main_root: Path,
        path_text: str,
        preimage: MainPathPreimage,
        task_root: Path,
        performed_repair_list: list[str],
    ) -> None:
        """Restore one exact accepted dirty-main preimage.

        Args:
            main_root: Main-worktree root.
            path_text: Root-relative path.
            preimage: Recorded index and working state.
            task_root: Task-worktree that owns the private snapshot.
            performed_repair_list: Mutable repair report.
        """

        snapshot_path = self._main_path_preimage_restore_preflight(
            main_root,
            path_text,
            preimage,
            task_root,
            performed_repair_list,
        )
        destination_path = main_root / path_text
        self._index_entry_list_restore(main_root, path_text, preimage["index_entry_list"])
        self._path_remove(destination_path)
        if preimage["working_present"]:
            self._path_clone(snapshot_path, destination_path)

    def _main_path_preimage_restore_preflight(
        self,
        main_root: Path,
        path_text: str,
        preimage: MainPathPreimage,
        task_root: Path,
        performed_repair_list: list[str],
    ) -> Path:
        """Validate every private object needed to restore one dirty preimage.

        Args:
            main_root: Main-worktree root.
            path_text: Root-relative path.
            preimage: Recorded index and working state.
            task_root: Task worktree that owns the private snapshot.
            performed_repair_list: Mutable repair report.

        Returns:
            Validated private working-object path.
        """

        destination_path = main_root / path_text
        self._path_parent_boundary_validate(main_root, destination_path, "Main preimage destination")
        if any(self._index_entry_fields_get(entry_text)[0] == "160000" for entry_text in preimage["index_entry_list"]):
            raise WorktreeError(f"Cannot automatically restore a submodule preimage in main: {destination_path}")
        snapshot_directory = self._main_preimage_directory_get(task_root) / preimage["snapshot_name"]
        snapshot_path = snapshot_directory / "working"
        if preimage["working_present"]:
            if (
                not os.path.lexists(snapshot_path)
                or self._path_fingerprint_get(snapshot_path) != preimage["working_fingerprint"]
            ):
                raise WorktreeError(f"Private main preimage is unavailable or damaged: {snapshot_path}")
        elif os.path.lexists(snapshot_path):
            raise WorktreeError(f"Absent main preimage has an unexpected private working object: {snapshot_path}")
        self._index_preimage_blob_list_prepare(
            main_root,
            snapshot_directory,
            preimage["index_entry_list"],
            performed_repair_list,
        )
        return snapshot_path

    def _main_clean_path_restore(self, main_root: Path, path_text: str) -> None:
        """Restore one formerly clean main path from current HEAD.

        Args:
            main_root: Main-worktree root.
            path_text: Root-relative path.
        """

        head_object_exists = self._main_clean_path_restore_preflight(main_root, path_text)
        if head_object_exists:
            self._git_command.run(
                main_root,
                ["restore", "--source=HEAD", "--staged", "--worktree", "--", path_text],
            )
            return
        self._git_command.run(main_root, ["update-index", "--force-remove", "--", path_text])
        self._path_remove(main_root / path_text)

    def _main_clean_path_restore_preflight(self, main_root: Path, path_text: str) -> bool:
        """Validate whether one formerly clean path can be restored from HEAD.

        Args:
            main_root: Main-worktree root.
            path_text: Root-relative path.

        Returns:
            Whether current HEAD contains the path.
        """

        self._path_parent_boundary_validate(main_root, main_root / path_text, "Clean main recovery destination")
        head_index_entry_list = self._commit_index_entry_list_get(main_root, "HEAD", path_text)
        if any(self._index_entry_fields_get(entry_text)[0] == "160000" for entry_text in head_index_entry_list):
            raise WorktreeError(f"Cannot automatically restore a submodule path in main: {main_root / path_text}")
        head_object_result = self._git_command.run(
            main_root,
            ["cat-file", "-e", f"HEAD:{path_text}"],
            check=False,
        )
        return head_object_result.returncode == 0

    def _main_leak_transaction_directory_get(self, task_root: Path, top_level_path_text: str) -> Path:
        """Return one deterministic private main-leak transaction directory."""

        transaction_name = hashlib.sha256(os.fsencode(top_level_path_text)).hexdigest()
        return self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / MAIN_LEAK_TRANSACTION_DIRECTORY_NAME / transaction_name,
        )

    def _main_leak_transaction_metadata_write(
        self,
        task_root: Path,
        transaction: MainLeakTransaction,
    ) -> None:
        """Durably replace one main-leak transaction marker."""

        metadata_path = (
            self._main_leak_transaction_directory_get(task_root, transaction["top_level_path"]) / "metadata.json"
        )
        self._private_text_atomic_write(
            metadata_path,
            json.dumps(transaction, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        )

    def _main_leak_unexposed_staging_repair(
        self,
        *,
        main_owner_root: Path,
        path_text: str,
        recorded_fingerprint: str,
        task_root: Path,
        top_level_path_text: str,
        performed_repair_list: list[str],
    ) -> None:
        """Discard only pre-metadata staging owned by durable leak provenance.

        A transaction directory can exist before its first metadata write is
        atomically exposed.  The state marker and exact hashed directory prove
        ownership, while an unchanged main Git-state fingerprint proves that no
        recovery mutation has begun.
        """

        transaction_directory = self._main_leak_transaction_directory_get(
            task_root,
            top_level_path_text,
        )
        if not os.path.lexists(transaction_directory):
            return
        if transaction_directory.is_symlink() or not transaction_directory.is_dir():
            raise WorktreeError(f"Main-leak transaction path is damaged: {transaction_directory}")
        metadata_path = transaction_directory / "metadata.json"
        if os.path.lexists(metadata_path):
            return
        if self._path_git_state_fingerprint_get(main_owner_root, path_text) != recorded_fingerprint:
            raise WorktreeError(
                f"Recorded main-leak Git state changed before staging recovery: " f"{main_owner_root / path_text}"
            )
        allowed_entry_name_set = {"metadata.json.tmp", "replacement"}
        for candidate_path in transaction_directory.iterdir():
            if candidate_path.name not in allowed_entry_name_set:
                raise WorktreeError(
                    f"Main-leak transaction metadata is unavailable and staging is not "
                    f"provider-owned: {candidate_path}"
                )
            self._provider_owned_staging_object_validate(
                candidate_path,
                "Unexposed main-leak transaction staging",
            )
        self._path_remove(transaction_directory)
        performed_repair_list.append(
            f"removed interrupted unexposed main-leak transaction staging: " f"{main_owner_root / path_text}"
        )

    def _main_leak_transaction_optional_get(
        self,
        task_root: Path,
        top_level_path_text: str,
        performed_repair_list: list[str] | None = None,
    ) -> MainLeakTransaction | None:
        """Load and validate one durable main-leak transaction."""

        transaction_directory = self._main_leak_transaction_directory_get(task_root, top_level_path_text)
        if not os.path.lexists(transaction_directory):
            return None
        if transaction_directory.is_symlink() or not transaction_directory.is_dir():
            raise WorktreeError(f"Main-leak transaction path is damaged: {transaction_directory}")
        metadata_path = transaction_directory / "metadata.json"
        if not os.path.lexists(metadata_path):
            if any(transaction_directory.iterdir()):
                raise WorktreeError(f"Main-leak transaction metadata is unavailable: {metadata_path}")
            self._path_remove(transaction_directory)
            if performed_repair_list is not None:
                performed_repair_list.append(
                    f"removed unexposed main-leak transaction staging: {transaction_directory}"
                )
            return None
        metadata_temporary_path = transaction_directory / "metadata.json.tmp"
        if os.path.lexists(metadata_temporary_path):
            if (
                metadata_temporary_path.is_symlink()
                or not metadata_temporary_path.is_file()
                or metadata_temporary_path.stat(follow_symlinks=False).st_nlink != 1
            ):
                raise WorktreeError(f"Main-leak transaction metadata staging is damaged: " f"{metadata_temporary_path}")
            metadata_temporary_path.unlink()
            if performed_repair_list is not None:
                performed_repair_list.append(
                    f"removed interrupted main-leak metadata staging: " f"{metadata_temporary_path}"
                )
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise WorktreeError(f"Main-leak transaction metadata is unavailable: {metadata_path}")
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorktreeError(f"Main-leak transaction metadata is invalid: {metadata_path}") from exc
        expected_key_set = {
            "fingerprint_schema_version",
            "index_managed",
            "index_previous_entry_list",
            "index_target_entry_list",
            "main_owner_root",
            "path",
            "phase",
            "schema_version",
            "top_level_path",
            "working_previous_fingerprint",
            "working_previous_present",
            "working_target_fingerprint",
            "working_target_present",
        }
        legacy_fingerprint_key_set = expected_key_set - {"fingerprint_schema_version"}
        if isinstance(payload, dict) and set(payload) == legacy_fingerprint_key_set:
            payload["fingerprint_schema_version"] = 1
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_key_set
            or type(payload.get("fingerprint_schema_version")) is not int
            or payload.get("fingerprint_schema_version") not in {1, 2}
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
            or payload.get("top_level_path") != top_level_path_text
            or not isinstance(payload.get("phase"), str)
            or payload.get("phase") not in {"prepared", "applying", "complete"}
            or type(payload.get("index_managed")) is not bool
            or type(payload.get("working_previous_present")) is not bool
            or type(payload.get("working_target_present")) is not bool
            or not isinstance(payload.get("main_owner_root"), str)
            or not isinstance(payload.get("path"), str)
            or not isinstance(payload.get("index_previous_entry_list"), list)
            or not isinstance(payload.get("index_target_entry_list"), list)
            or any(
                not isinstance(entry_text, str)
                for entry_text in [
                    *payload.get("index_previous_entry_list", []),
                    *payload.get("index_target_entry_list", []),
                ]
            )
            or any(
                payload.get(field_name) != "absent" and not _hex_digest_is_valid(payload.get(field_name), {64})
                for field_name in ("working_previous_fingerprint", "working_target_fingerprint")
            )
            or (payload.get("working_previous_present") != (payload.get("working_previous_fingerprint") != "absent"))
            or payload.get("working_target_present") != (payload.get("working_target_fingerprint") != "absent")
        ):
            raise WorktreeError(f"Main-leak transaction metadata is invalid: {metadata_path}")
        self._relative_path_validate(Path(payload["path"]), "main-leak transaction path")
        for entry_text in [
            *payload["index_previous_entry_list"],
            *payload["index_target_entry_list"],
        ]:
            self._index_entry_fields_get(entry_text)
        allowed_entry_name_set = {
            "displaced",
            "exposure",
            "metadata.json",
            "replacement",
        }
        for candidate_path in transaction_directory.iterdir():
            if candidate_path.name not in allowed_entry_name_set:
                raise WorktreeError(f"Main-leak transaction contains unknown content: {candidate_path}")
        if payload["fingerprint_schema_version"] == 1:
            destination_path = Path(payload["main_owner_root"]) / payload["path"]
            previous_candidate_path_list = [
                transaction_directory / "displaced",
                destination_path,
            ]
            target_candidate_path_list = [
                transaction_directory / "replacement",
                destination_path,
            ]
            if payload["working_previous_present"]:
                payload["working_previous_fingerprint"], _ = self._recorded_path_fingerprint_candidate_list_upgrade(
                    previous_candidate_path_list,
                    payload["working_previous_fingerprint"],
                )
            if payload["working_target_present"]:
                payload["working_target_fingerprint"], _ = self._recorded_path_fingerprint_candidate_list_upgrade(
                    target_candidate_path_list,
                    payload["working_target_fingerprint"],
                )
            payload["fingerprint_schema_version"] = 2
            self._private_text_atomic_write(
                metadata_path,
                json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            )
            if performed_repair_list is not None:
                performed_repair_list.append(
                    f"upgraded collision-safe main-leak transaction fingerprints: {transaction_directory}"
                )
        replacement_path = transaction_directory / "replacement"
        if payload["working_target_present"]:
            if self._path_fingerprint_get(replacement_path) != payload["working_target_fingerprint"]:
                raise WorktreeError(f"Main-leak transaction replacement is unavailable or damaged: {replacement_path}")
        elif os.path.lexists(replacement_path):
            raise WorktreeError(f"Absent main-leak target has an unexpected replacement: {replacement_path}")
        for staged_name, allowed_fingerprint_set in (
            (
                "displaced",
                {
                    payload["working_previous_fingerprint"],
                    payload["working_target_fingerprint"],
                },
            ),
        ):
            staged_path = transaction_directory / staged_name
            if os.path.lexists(staged_path) and self._path_fingerprint_get(staged_path) not in allowed_fingerprint_set:
                raise WorktreeError(f"Main-leak transaction contains changed staging content: {staged_path}")
        return cast(MainLeakTransaction, payload)

    def _git_state_fingerprint_from_parts_get(
        self,
        index_entry_list: list[str],
        working_fingerprint: str,
    ) -> str:
        """Build the exact no-index-flag path-state fingerprint."""

        digest = hashlib.sha256()
        for index_entry in index_entry_list:
            digest.update(index_entry.encode())
            digest.update(b"\0")
        digest.update(b"\0")
        digest.update(working_fingerprint.encode())
        return digest.hexdigest()

    def _commit_working_object_stage(
        self,
        repository_root: Path,
        commit: str,
        path_text: str,
        destination_path: Path,
    ) -> bool:
        """Stage one exact committed file or link as a private filesystem object."""

        index_entry_list = self._commit_index_entry_list_get(repository_root, commit, path_text)
        if not index_entry_list:
            return False
        mode_text, object_id, stage_text = index_entry_list[0].split()
        if stage_text != "0" or mode_text not in {"100644", "100755", "120000"}:
            raise WorktreeError(
                f"Main-leak recovery supports only committed files and links: {repository_root / path_text}"
            )
        content = self._git_command.run_bytes(
            repository_root,
            ["cat-file", "blob", object_id],
        ).stdout
        if mode_text == "120000":
            try:
                link_target = os.fsdecode(content)
            except UnicodeDecodeError as exc:
                raise WorktreeError(
                    f"Committed symbolic-link target is not representable: {repository_root / path_text}"
                ) from exc
            if "\0" in link_target:
                raise WorktreeError(f"Committed symbolic-link target contains NUL: {repository_root / path_text}")
            destination_path.symlink_to(link_target)
        else:
            self._private_bytes_atomic_write(destination_path, content)
            destination_path.chmod(0o755 if mode_text == "100755" else 0o644)
        return True

    def _main_leak_transaction_expected_validate(
        self,
        transaction: MainLeakTransaction,
        *,
        index_managed: bool,
        index_target_entry_list: list[str],
        main_owner_root: Path,
        path_text: str,
        recorded_fingerprint: str,
        task_root: Path,
        target_source_path: Path | None,
        top_level_path_text: str,
    ) -> None:
        """Prove one existing transaction still represents the requested recovery."""

        if (
            transaction["main_owner_root"] != str(main_owner_root)
            or transaction["path"] != path_text
            or transaction["top_level_path"] != top_level_path_text
            or transaction["index_managed"] != index_managed
            or transaction["index_target_entry_list"] != index_target_entry_list
            or self._git_state_fingerprint_from_parts_get(
                transaction["index_previous_entry_list"],
                transaction["working_previous_fingerprint"],
            )
            != recorded_fingerprint
        ):
            raise WorktreeError(
                f"Main-leak transaction does not match recorded ownership: {main_owner_root / path_text}"
            )
        replacement_path = (
            self._main_leak_transaction_directory_get(
                task_root,
                top_level_path_text,
            )
            / "replacement"
        )
        if transaction["working_target_present"]:
            if (
                not os.path.lexists(replacement_path)
                or self._path_fingerprint_get(replacement_path) != transaction["working_target_fingerprint"]
            ):
                raise WorktreeError(f"Main-leak transaction replacement is unavailable or damaged: {replacement_path}")
        elif os.path.lexists(replacement_path):
            raise WorktreeError(f"Absent main-leak target has an unexpected replacement: {replacement_path}")
        if target_source_path is not None and (
            self._path_fingerprint_get(target_source_path) != transaction["working_target_fingerprint"]
        ):
            raise WorktreeError(f"Main-leak recovery target changed: {target_source_path}")
        destination_path = main_owner_root / path_text
        self._mutation_parent_boundary_validate(
            main_owner_root,
            destination_path,
            "Main-leak recovery destination",
        )
        current_working_fingerprint = self._path_fingerprint_get(destination_path)
        if current_working_fingerprint not in {
            transaction["working_previous_fingerprint"],
            transaction["working_target_fingerprint"],
        }:
            raise WorktreeError(f"Main-leak destination contains independent content: {destination_path}")
        current_index_entry_list = self._index_entry_list_get(main_owner_root, path_text)
        if index_managed and tuple(current_index_entry_list) not in {
            tuple(transaction["index_previous_entry_list"]),
            tuple(transaction["index_target_entry_list"]),
        }:
            raise WorktreeError(f"Main-leak index contains independent content: {destination_path}")
        if not index_managed and current_index_entry_list:
            raise WorktreeError(f"Resource main-leak recovery found staged content: {destination_path}")
        if transaction["phase"] == "prepared" and (
            current_working_fingerprint != transaction["working_previous_fingerprint"]
            or current_index_entry_list != transaction["index_previous_entry_list"]
        ):
            raise WorktreeError(f"Main-leak destination changed before recovery: {destination_path}")

    def _main_leak_transaction_prepare(
        self,
        *,
        index_managed: bool,
        index_target_entry_list: list[str],
        main_owner_root: Path,
        path_text: str,
        recorded_fingerprint: str,
        target_commit: str | None,
        target_source_path: Path | None,
        task_root: Path,
        top_level_path_text: str,
        performed_repair_list: list[str] | None = None,
    ) -> MainLeakTransaction:
        """Stage and record one recovery before any main-worktree mutation."""

        existing_transaction = self._main_leak_transaction_optional_get(
            task_root,
            top_level_path_text,
            performed_repair_list,
        )
        if existing_transaction is not None:
            self._main_leak_transaction_expected_validate(
                existing_transaction,
                index_managed=index_managed,
                index_target_entry_list=index_target_entry_list,
                main_owner_root=main_owner_root,
                path_text=path_text,
                recorded_fingerprint=recorded_fingerprint,
                task_root=task_root,
                target_source_path=target_source_path,
                top_level_path_text=top_level_path_text,
            )
            return existing_transaction
        current_index_entry_list = self._index_entry_list_get(main_owner_root, path_text)
        current_working_fingerprint = self._path_fingerprint_get(main_owner_root / path_text)
        if (
            self._git_state_fingerprint_from_parts_get(
                current_index_entry_list,
                current_working_fingerprint,
            )
            != recorded_fingerprint
        ):
            raise WorktreeError(f"Recorded main-leak Git state changed before staging: {main_owner_root / path_text}")
        transaction_directory = self._main_leak_transaction_directory_get(task_root, top_level_path_text)
        if os.path.lexists(transaction_directory):
            raise WorktreeError(f"Main-leak transaction already exists: {transaction_directory}")
        transaction_directory.mkdir(parents=True)
        replacement_path = transaction_directory / "replacement"
        try:
            if target_source_path is not None:
                if os.path.lexists(target_source_path):
                    self._path_clone(target_source_path, replacement_path)
                    working_target_present = True
                else:
                    working_target_present = False
            elif target_commit is not None:
                working_target_present = self._commit_working_object_stage(
                    main_owner_root,
                    target_commit,
                    path_text,
                    replacement_path,
                )
            else:
                working_target_present = False
            working_target_fingerprint = self._path_fingerprint_get(replacement_path)
            transaction: MainLeakTransaction = {
                "fingerprint_schema_version": 2,
                "index_managed": index_managed,
                "index_previous_entry_list": current_index_entry_list,
                "index_target_entry_list": index_target_entry_list,
                "main_owner_root": str(main_owner_root),
                "path": path_text,
                "phase": "prepared",
                "schema_version": 1,
                "top_level_path": top_level_path_text,
                "working_previous_fingerprint": current_working_fingerprint,
                "working_previous_present": current_working_fingerprint != "absent",
                "working_target_fingerprint": working_target_fingerprint,
                "working_target_present": working_target_present,
            }
            self._main_leak_transaction_metadata_write(task_root, transaction)
            return transaction
        except BaseException:
            if not (transaction_directory / "metadata.json").is_file():
                self._path_remove(transaction_directory)
            raise

    def _main_leak_transaction_apply(
        self,
        task_root: Path,
        transaction: MainLeakTransaction,
        performed_repair_list: list[str],
    ) -> None:
        """Idempotently expose a staged main recovery and atomically restore its index."""

        transaction_directory = self._main_leak_transaction_directory_get(
            task_root,
            transaction["top_level_path"],
        )
        destination_path = Path(transaction["main_owner_root"]) / transaction["path"]
        self._mutation_parent_boundary_validate(
            Path(transaction["main_owner_root"]),
            destination_path,
            "Main-leak recovery destination",
        )
        replacement_path = transaction_directory / "replacement"
        if transaction["working_target_present"]:
            if (
                not os.path.lexists(replacement_path)
                or self._path_fingerprint_get(replacement_path) != transaction["working_target_fingerprint"]
            ):
                raise WorktreeError(f"Main-leak transaction replacement is unavailable or damaged: {replacement_path}")
        elif os.path.lexists(replacement_path):
            raise WorktreeError(f"Absent main-leak target has an unexpected replacement: {replacement_path}")
        if transaction["phase"] == "prepared":
            if (
                self._path_fingerprint_get(destination_path) != transaction["working_previous_fingerprint"]
                or self._index_entry_list_get(Path(transaction["main_owner_root"]), transaction["path"])
                != transaction["index_previous_entry_list"]
            ):
                raise WorktreeError(f"Main-leak destination changed before recovery: {destination_path}")
            transaction["phase"] = "applying"
            self._main_leak_transaction_metadata_write(task_root, transaction)
        current_fingerprint = self._path_fingerprint_get(destination_path)
        allowed_current_fingerprint_set = {
            transaction["working_previous_fingerprint"],
            transaction["working_target_fingerprint"],
        }
        displaced_path = transaction_directory / "displaced"
        if (
            os.path.lexists(displaced_path)
            and self._path_fingerprint_get(displaced_path) == transaction["working_previous_fingerprint"]
        ):
            allowed_current_fingerprint_set.add("absent")
        if current_fingerprint not in allowed_current_fingerprint_set:
            raise WorktreeError(f"Main-leak destination contains independent content: {destination_path}")
        if os.path.lexists(displaced_path) and self._path_fingerprint_get(displaced_path) not in {
            transaction["working_previous_fingerprint"],
            transaction["working_target_fingerprint"],
        }:
            raise WorktreeError(f"Main-leak displaced object is damaged: {displaced_path}")
        if current_fingerprint != transaction["working_target_fingerprint"]:
            if destination_path.parent.is_symlink() or not destination_path.parent.is_dir():
                raise WorktreeError(f"Main-leak destination has no physical parent: {destination_path}")
            exposure_path = transaction_directory / "exposure"
            self._owned_staging_object_remove(
                exposure_path,
                transaction["working_target_fingerprint"],
                "Main-leak exposure staging",
            )
            if transaction["working_target_present"]:
                self._path_clone(replacement_path, exposure_path)
                if self._path_fingerprint_get(exposure_path) != transaction["working_target_fingerprint"]:
                    raise WorktreeError(f"Cannot stage exact main-leak recovery: {destination_path}")
                if exposure_path.stat(follow_symlinks=False).st_dev != destination_path.parent.stat().st_dev:
                    self._owned_staging_object_remove(
                        exposure_path,
                        transaction["working_target_fingerprint"],
                        "Main-leak exposure staging",
                    )
                    raise WorktreeError(f"Main-leak recovery cannot atomically cross filesystems: {destination_path}")
            if os.path.lexists(destination_path):
                if os.path.lexists(displaced_path):
                    raise WorktreeError(f"Main-leak transaction already has a displaced object: {displaced_path}")
                if destination_path.stat(follow_symlinks=False).st_dev != transaction_directory.stat().st_dev:
                    raise WorktreeError(f"Main-leak recovery cannot displace across filesystems: {destination_path}")
                destination_path.replace(displaced_path)
            elif transaction["working_previous_present"] and not os.path.lexists(displaced_path):
                raise WorktreeError(f"Main-leak recovery lost its previous object: {destination_path}")
            if transaction["working_target_present"]:
                exposure_path.replace(destination_path)
        if self._path_fingerprint_get(destination_path) != transaction["working_target_fingerprint"]:
            raise WorktreeError(f"Cannot expose exact main-leak recovery: {destination_path}")
        main_owner_root = Path(transaction["main_owner_root"])
        if transaction["index_managed"]:
            current_index_entry_list = self._index_entry_list_get(main_owner_root, transaction["path"])
            if tuple(current_index_entry_list) not in {
                tuple(transaction["index_previous_entry_list"]),
                tuple(transaction["index_target_entry_list"]),
            }:
                raise WorktreeError(f"Main-leak index contains independent content: {destination_path}")
            if current_index_entry_list != transaction["index_target_entry_list"]:
                self._index_entry_list_restore(
                    main_owner_root,
                    transaction["path"],
                    transaction["index_target_entry_list"],
                )
        elif self._index_entry_list_get(main_owner_root, transaction["path"]):
            raise WorktreeError(f"Resource main-leak recovery found staged content: {destination_path}")
        transaction["phase"] = "complete"
        self._main_leak_transaction_metadata_write(task_root, transaction)
        performed_repair_list.append(f"completed durable main-leak recovery: {destination_path}")

    def _main_leak_transaction_list_retire(
        self,
        state: WorktreeState,
        performed_repair_list: list[str] | None,
    ) -> None:
        """Retire completed recoveries only after every ownership replica is durable."""

        owner_list: list[tuple[Path, dict[str, str]]] = []
        for repository_state in state["repository_state_list"]:
            top_level_task_root = Path(repository_state["task_root"])
            owner_list.append(
                (
                    top_level_task_root,
                    repository_state["main_leak_fingerprint_by_path_map"],
                )
            )
            owner_list.extend(
                (
                    top_level_task_root / submodule_state["path"],
                    submodule_state["main_leak_fingerprint_by_path_map"],
                )
                for submodule_state in repository_state["participating_submodule_state_list"]
            )
        for task_root, marker_by_path_map in owner_list:
            transaction_root = self._git_path_get(
                task_root,
                Path(PRIVATE_STATE_DIRECTORY_NAME) / MAIN_LEAK_TRANSACTION_DIRECTORY_NAME,
            )
            if not os.path.lexists(transaction_root):
                continue
            if transaction_root.is_symlink() or not transaction_root.is_dir():
                raise WorktreeError(f"Main-leak transaction owner is damaged: {transaction_root}")
            for candidate_path in sorted(transaction_root.iterdir()):
                metadata_path = candidate_path / "metadata.json"
                if not metadata_path.is_file() or metadata_path.is_symlink():
                    matching_path_list = [
                        path_text
                        for path_text in marker_by_path_map
                        if candidate_path.name == hashlib.sha256(os.fsencode(path_text)).hexdigest()
                    ]
                    if (
                        not metadata_path.is_symlink()
                        and candidate_path.is_dir()
                        and not candidate_path.is_symlink()
                        and len(matching_path_list) == 1
                        and not any(candidate_path.iterdir())
                    ):
                        self._path_remove(candidate_path)
                        if performed_repair_list is not None:
                            performed_repair_list.append(
                                f"removed unexposed main-leak transaction staging: {candidate_path}"
                            )
                        continue
                    if (
                        not metadata_path.is_symlink()
                        and candidate_path.is_dir()
                        and not candidate_path.is_symlink()
                        and len(matching_path_list) == 1
                        and {item.name for item in candidate_path.iterdir()} <= {"metadata.json.tmp", "replacement"}
                    ):
                        for staging_path in candidate_path.iterdir():
                            self._provider_owned_staging_object_validate(
                                staging_path,
                                "Unexposed main-leak transaction staging",
                            )
                        continue
                    raise WorktreeError(f"Main-leak transaction metadata is unavailable: {metadata_path}")
                try:
                    raw_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise WorktreeError(f"Main-leak transaction metadata is invalid: {metadata_path}") from exc
                if not isinstance(raw_payload, dict) or not isinstance(raw_payload.get("top_level_path"), str):
                    raise WorktreeError(f"Main-leak transaction has no path owner: {metadata_path}")
                top_level_path_text = raw_payload["top_level_path"]
                transaction = self._main_leak_transaction_optional_get(
                    task_root,
                    top_level_path_text,
                    performed_repair_list,
                )
                if transaction is None:
                    raise WorktreeError(f"Main-leak transaction disappeared: {candidate_path}")
                if top_level_path_text in marker_by_path_map:
                    continue
                if transaction["phase"] != "complete":
                    raise WorktreeError(f"Cannot retire incomplete main-leak transaction: {candidate_path}")
                destination_path = Path(transaction["main_owner_root"]) / transaction["path"]
                if self._path_fingerprint_get(destination_path) != transaction["working_target_fingerprint"] or (
                    transaction["index_managed"]
                    and self._index_entry_list_get(Path(transaction["main_owner_root"]), transaction["path"])
                    != transaction["index_target_entry_list"]
                ):
                    raise WorktreeError(f"Completed main-leak recovery changed before retirement: {destination_path}")
                self._path_remove(candidate_path)
                if performed_repair_list is not None:
                    performed_repair_list.append(f"retired durable main-leak recovery: {destination_path}")

    def _path_parent_boundary_validate(self, boundary_root: Path, path: Path, label: str) -> None:
        """Verify that one object's parent remains inside its repository boundary.

        Args:
            boundary_root: Canonical repository boundary.
            path: Candidate object path.
            label: Diagnostic object name.
        """

        try:
            path.parent.resolve().relative_to(boundary_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorktreeError(f"{label} parent escapes repository boundary: {path}") from exc

    def _mutation_parent_boundary_validate(
        self,
        boundary_root: Path,
        path: Path,
        label: str,
        *,
        allow_missing: bool = False,
    ) -> None:
        """Require every existing mutation parent to be a physical directory in its owner."""

        self._path_parent_boundary_validate(boundary_root, path, label)
        try:
            relative_parent = Path(os.path.abspath(path.parent)).relative_to(boundary_root)
        except ValueError as exc:
            raise WorktreeError(f"{label} parent escapes repository boundary: {path}") from exc
        current_path = boundary_root
        if current_path.is_symlink() or not current_path.is_dir():
            raise WorktreeError(f"{label} owner is not one physical directory: {boundary_root}")
        for path_part in relative_parent.parts:
            current_path /= path_part
            if allow_missing and not os.path.lexists(current_path):
                continue
            if current_path.is_symlink() or not current_path.is_dir():
                raise WorktreeError(f"{label} has a non-physical parent: {current_path}")

    def _source_boundary_validate(self, boundary_root: Path, source_path: Path) -> None:
        """Verify that one source object resolves inside its repository boundary.

        Args:
            boundary_root: Canonical source repository boundary.
            source_path: Existing source object.
        """

        self._path_parent_boundary_validate(boundary_root, source_path, "Resource source")
        if source_path.is_symlink():
            raise WorktreeError(f"Resource source root cannot be a symbolic link: {source_path}")
        try:
            source_path.resolve(strict=True).relative_to(boundary_root)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise WorktreeError(f"Resource source escapes or is unresolved: {source_path}") from exc

    def _path_copy_source_validate(self, source_path: Path, label: str = "Copy source") -> None:
        """Validate one copy object tree and its symbolic links.

        Args:
            source_path: Source object to validate.
            label: Diagnostic object role.
        """

        if source_path.is_symlink():
            raise WorktreeError(f"{label} root cannot be a symbolic link: {source_path}")
        source_tree_root = source_path if source_path.is_dir() else source_path.parent
        path_list = [source_path]
        if source_path.is_dir() and not source_path.is_symlink():
            path_list.extend(sorted(source_path.rglob("*")))
        for candidate_path in path_list:
            mode = candidate_path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise WorktreeError(f"{label} contains a special filesystem object: {candidate_path}")
            if candidate_path.is_symlink():
                try:
                    resolved_target = candidate_path.resolve(strict=True)
                    resolved_target.relative_to(source_tree_root.resolve())
                except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                    raise WorktreeError(
                        f"{label} symbolic link is unresolved or escapes its object tree: {candidate_path}"
                    ) from exc

    def _path_fingerprint_get(self, path: Path) -> str:
        """Compute one deterministic filesystem-object fingerprint.

        Args:
            path: Filesystem object path.

        Returns:
            SHA-256 fingerprint or the literal absent marker.
        """

        if not os.path.lexists(path):
            return "absent"
        if path.is_symlink() or not path.is_dir():
            return self._legacy_path_fingerprint_get(path)
        digest = hashlib.sha256()
        root_path = path
        path_list = [root_path]
        path_list.extend(sorted(root_path.rglob("*"), key=lambda item: item.relative_to(root_path).as_posix()))
        for candidate_path in path_list:
            relative_text = "." if candidate_path == root_path else candidate_path.relative_to(root_path).as_posix()
            mode = candidate_path.lstat().st_mode
            digest.update(self._fingerprint_field_get(b"path", os.fsencode(relative_text)))
            digest.update(
                self._fingerprint_field_get(
                    b"mode",
                    stat.S_IMODE(mode).to_bytes(4, byteorder="big"),
                )
            )
            if candidate_path.is_symlink():
                digest.update(self._fingerprint_field_get(b"type", b"link"))
                digest.update(
                    self._fingerprint_field_get(
                        b"target",
                        os.fsencode(os.readlink(candidate_path)),
                    )
                )
            elif candidate_path.is_dir():
                digest.update(self._fingerprint_field_get(b"type", b"directory"))
            elif candidate_path.is_file():
                if candidate_path.stat(follow_symlinks=False).st_nlink != 1:
                    raise WorktreeError(
                        f"Filesystem fingerprint does not support hardlinked regular files: {candidate_path}"
                    )
                digest.update(self._fingerprint_field_get(b"type", b"file"))
                file_size = candidate_path.stat(follow_symlinks=False).st_size
                digest.update(b"content")
                digest.update(file_size.to_bytes(8, byteorder="big"))
                observed_size = 0
                with candidate_path.open("rb") as handle:
                    while content := handle.read(1024 * 1024):
                        digest.update(content)
                        observed_size += len(content)
                if observed_size != file_size:
                    raise WorktreeError(f"Filesystem object changed while fingerprinting: {candidate_path}")
            else:
                digest.update(self._fingerprint_field_get(b"type", b"special"))
            digest.update(b"entry-end")
        return digest.hexdigest()

    def _fingerprint_field_get(
        self,
        field_name: bytes,
        value: bytes,
    ) -> bytes:
        """Return one tagged, length-delimited fingerprint field."""

        return (
            len(field_name).to_bytes(2, byteorder="big") + field_name + len(value).to_bytes(8, byteorder="big") + value
        )

    def _legacy_path_fingerprint_get(self, path: Path) -> str:
        """Compute the schema-v1 ambiguous fingerprint for state migration only."""

        if not os.path.lexists(path):
            return "absent"
        digest = hashlib.sha256()
        root_path = path
        path_list = [root_path]
        if root_path.is_dir() and not root_path.is_symlink():
            path_list.extend(sorted(root_path.rglob("*"), key=lambda item: item.relative_to(root_path).as_posix()))
        for candidate_path in path_list:
            relative_text = "." if candidate_path == root_path else candidate_path.relative_to(root_path).as_posix()
            mode = candidate_path.lstat().st_mode
            digest.update(os.fsencode(relative_text))
            digest.update(str(stat.S_IMODE(mode)).encode())
            if candidate_path.is_symlink():
                digest.update(b"link")
                digest.update(os.fsencode(os.readlink(candidate_path)))
            elif candidate_path.is_dir():
                digest.update(b"directory")
            elif candidate_path.is_file():
                if candidate_path.stat(follow_symlinks=False).st_nlink != 1:
                    raise WorktreeError(
                        f"Filesystem fingerprint does not support hardlinked regular files: {candidate_path}"
                    )
                digest.update(b"file")
                with candidate_path.open("rb") as handle:
                    while content := handle.read(1024 * 1024):
                        digest.update(content)
            else:
                digest.update(b"special")
        return digest.hexdigest()

    def _recorded_path_fingerprint_upgrade(
        self,
        path: Path,
        recorded_fingerprint: str,
        label: str,
    ) -> tuple[str, bool]:
        """Upgrade one exact legacy object fingerprint after observable proof."""

        current_fingerprint = self._path_fingerprint_get(path)
        if recorded_fingerprint == current_fingerprint:
            return recorded_fingerprint, False
        if path.is_symlink() or not path.is_dir():
            return recorded_fingerprint, False
        if recorded_fingerprint != self._legacy_path_fingerprint_get(path):
            raise WorktreeError(f"{label} changed before fingerprint migration: {path}")
        return current_fingerprint, True

    def _recorded_path_fingerprint_candidate_list_upgrade(
        self,
        candidate_path_list: list[Path],
        recorded_fingerprint: str,
    ) -> tuple[str, bool]:
        """Upgrade one fingerprint from any exact durable transaction preimage."""

        for candidate_path in candidate_path_list:
            current_fingerprint = self._path_fingerprint_get(candidate_path)
            if recorded_fingerprint == current_fingerprint:
                return recorded_fingerprint, False
            if (
                candidate_path.is_dir()
                and not candidate_path.is_symlink()
                and recorded_fingerprint == self._legacy_path_fingerprint_get(candidate_path)
            ):
                return current_fingerprint, True
        return recorded_fingerprint, False

    def _state_fingerprint_list_upgrade(
        self,
        state: WorktreeState,
        state_path: Path,
        performed_repair_list: list[str] | None,
    ) -> bool:
        """Upgrade every persisted filesystem-tree fingerprint that has an exact preimage."""

        changed = False

        def resource_state_list_upgrade(
            main_owner_root: Path,
            task_owner_root: Path,
            resource_state_list: list[ResourceState],
        ) -> None:
            nonlocal changed
            for resource_state in resource_state_list:
                if resource_state["skipped"]:
                    continue
                path_text = resource_state["path"]
                source_snapshot_path = (
                    self._resource_source_preimage_directory_get(task_owner_root, path_text) / "source"
                )
                source_proof_path = (
                    source_snapshot_path if os.path.lexists(source_snapshot_path) else main_owner_root / path_text
                )
                upgraded_source_fingerprint, source_changed = self._recorded_path_fingerprint_upgrade(
                    source_proof_path,
                    resource_state["source_fingerprint"],
                    "Recorded resource source",
                )
                if (
                    resource_state["strategy"] == "copy"
                    and resource_state["destination_fingerprint"] == resource_state["source_fingerprint"]
                ):
                    upgraded_destination_fingerprint = upgraded_source_fingerprint
                    destination_changed = source_changed
                elif resource_state["strategy"] == "copy":
                    (
                        upgraded_destination_fingerprint,
                        destination_changed,
                    ) = self._recorded_copy_destination_fingerprint_upgrade(
                        task_owner_root,
                        main_owner_root / path_text,
                        source_proof_path,
                        resource_state["destination_fingerprint"],
                        performed_repair_list,
                    )
                else:
                    upgraded_destination_fingerprint, destination_changed = self._recorded_path_fingerprint_upgrade(
                        task_owner_root / path_text,
                        resource_state["destination_fingerprint"],
                        "Recorded resource destination",
                    )
                resource_state["source_fingerprint"] = upgraded_source_fingerprint
                resource_state["destination_fingerprint"] = upgraded_destination_fingerprint
                changed = changed or source_changed or destination_changed

        def main_state_upgrade(
            main_owner_root: Path,
            task_owner_root: Path,
            main_preimage_by_path_map: dict[str, MainPathPreimage],
            main_status_fingerprint_by_path_map: dict[str, str],
            main_leak_fingerprint_by_path_map: dict[str, str],
        ) -> None:
            nonlocal changed
            preimage_directory = self._main_preimage_directory_get(task_owner_root)
            for preimage in main_preimage_by_path_map.values():
                if not preimage["working_present"]:
                    continue
                working_fingerprint, working_changed = self._recorded_path_fingerprint_upgrade(
                    preimage_directory / preimage["snapshot_name"] / "working",
                    preimage["working_fingerprint"],
                    "Recorded private main preimage",
                )
                preimage["working_fingerprint"] = working_fingerprint
                changed = changed or working_changed
            for path_text, recorded_fingerprint in list(main_status_fingerprint_by_path_map.items()):
                current_fingerprint = self._path_git_state_fingerprint_get(
                    main_owner_root,
                    path_text,
                )
                if recorded_fingerprint == current_fingerprint:
                    continue
                if recorded_fingerprint == self._legacy_path_git_state_fingerprint_get(
                    main_owner_root,
                    path_text,
                ):
                    main_status_fingerprint_by_path_map[path_text] = current_fingerprint
                    changed = True
            for path_text, recorded_fingerprint in list(main_leak_fingerprint_by_path_map.items()):
                transaction = self._main_leak_transaction_optional_get(
                    task_owner_root,
                    path_text,
                )
                if transaction is not None:
                    upgraded_fingerprint = self._git_state_fingerprint_from_parts_get(
                        transaction["index_previous_entry_list"],
                        transaction["working_previous_fingerprint"],
                    )
                    if recorded_fingerprint == upgraded_fingerprint:
                        continue
                    destination_path = Path(transaction["main_owner_root"]) / transaction["path"]
                    for previous_path in (
                        self._main_leak_transaction_directory_get(task_owner_root, path_text) / "displaced",
                        destination_path,
                    ):
                        if self._path_fingerprint_get(previous_path) != transaction["working_previous_fingerprint"]:
                            continue
                        legacy_fingerprint = self._git_state_fingerprint_from_parts_get(
                            transaction["index_previous_entry_list"],
                            self._legacy_path_fingerprint_get(previous_path),
                        )
                        if recorded_fingerprint == legacy_fingerprint:
                            main_leak_fingerprint_by_path_map[path_text] = upgraded_fingerprint
                            changed = True
                        break
                    if main_leak_fingerprint_by_path_map[path_text] == upgraded_fingerprint:
                        continue
                current_fingerprint = self._path_git_state_fingerprint_get(
                    main_owner_root,
                    path_text,
                )
                if recorded_fingerprint == current_fingerprint:
                    continue
                if recorded_fingerprint == self._legacy_path_git_state_fingerprint_get(
                    main_owner_root,
                    path_text,
                ):
                    main_leak_fingerprint_by_path_map[path_text] = current_fingerprint
                    changed = True

        specification_fingerprint, specification_changed = self._recorded_path_fingerprint_upgrade(
            self._specification_path,
            state["specification_fingerprint"],
            "Recorded specification",
        )
        state["specification_fingerprint"] = specification_fingerprint
        changed = changed or specification_changed
        if state["goal_fingerprint"]:
            goal_path = self._coordinating_repository / ".spec" / f"{self._prefix}{GOAL_SUFFIX}"
            goal_fingerprint, goal_changed = self._recorded_path_fingerprint_upgrade(
                goal_path,
                state["goal_fingerprint"],
                "Recorded goal",
            )
            state["goal_fingerprint"] = goal_fingerprint
            changed = changed or goal_changed
        for repository_state in state["repository_state_list"]:
            main_root = Path(repository_state["main_root"])
            task_root = Path(repository_state["task_root"])
            manifest_fingerprint, manifest_changed = self._recorded_path_fingerprint_upgrade(
                task_root / MANIFEST_NAME,
                repository_state["manifest_fingerprint"],
                "Recorded bootstrap manifest",
            )
            repository_state["manifest_fingerprint"] = manifest_fingerprint
            changed = changed or manifest_changed
            main_state_upgrade(
                main_root,
                task_root,
                repository_state["main_preimage_by_path_map"],
                repository_state["main_status_fingerprint_by_path_map"],
                repository_state["main_leak_fingerprint_by_path_map"],
            )
            resource_state_list_upgrade(
                main_root,
                task_root,
                repository_state["resource_state_list"],
            )
            for submodule_state in repository_state["participating_submodule_state_list"]:
                path_text = submodule_state["path"]
                main_submodule_root = main_root / path_text
                task_submodule_root = task_root / path_text
                submodule_manifest_fingerprint, submodule_manifest_changed = self._recorded_path_fingerprint_upgrade(
                    task_submodule_root / MANIFEST_NAME,
                    submodule_state["manifest_fingerprint"],
                    "Recorded task-owned submodule bootstrap manifest",
                )
                submodule_state["manifest_fingerprint"] = submodule_manifest_fingerprint
                changed = changed or submodule_manifest_changed
                main_state_upgrade(
                    main_submodule_root,
                    task_submodule_root,
                    submodule_state["main_preimage_by_path_map"],
                    submodule_state["main_status_fingerprint_by_path_map"],
                    submodule_state["main_leak_fingerprint_by_path_map"],
                )
                resource_state_list_upgrade(
                    main_submodule_root,
                    task_submodule_root,
                    submodule_state["resource_state_list"],
                )
        return changed

    def _recorded_copy_destination_fingerprint_upgrade(
        self,
        task_owner_root: Path,
        main_source_path: Path,
        source_preimage_path: Path,
        recorded_fingerprint: str,
        performed_repair_list: list[str] | None,
    ) -> tuple[str, bool]:
        """Upgrade a copied-directory fingerprint without reading mutable task output."""

        if not source_preimage_path.is_dir() or source_preimage_path.is_symlink():
            return recorded_fingerprint, False
        repair_list = performed_repair_list if performed_repair_list is not None else []
        self._private_clone_staging_list_reconcile(
            task_owner_root,
            "copy-fingerprint-migration",
            repair_list,
        )
        staging_directory, staging_marker_path = self._private_clone_staging_directory_create(
            task_owner_root,
            "copy-fingerprint-migration",
            str(main_source_path),
        )
        staging_path = staging_directory / "copy"
        try:
            shutil.copytree(source_preimage_path, staging_path, symlinks=True)
            for source_candidate in sorted(source_preimage_path.rglob("*")):
                if not source_candidate.is_symlink():
                    continue
                raw_target = Path(os.readlink(source_candidate))
                if not raw_target.is_absolute():
                    continue
                try:
                    target_relative_path = raw_target.resolve(strict=True).relative_to(
                        main_source_path.resolve(strict=True)
                    )
                except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                    raise WorktreeError(f"Cannot prove copied-link fingerprint migration: {source_candidate}") from exc
                destination_candidate = staging_path / source_candidate.relative_to(source_preimage_path)
                destination_target = staging_path / target_relative_path
                destination_candidate.unlink()
                destination_candidate.symlink_to(
                    os.path.relpath(
                        destination_target,
                        start=destination_candidate.parent,
                    )
                )
            current_fingerprint = self._path_fingerprint_get(staging_path)
            if recorded_fingerprint == current_fingerprint:
                return recorded_fingerprint, False
            if recorded_fingerprint != self._legacy_path_fingerprint_get(staging_path):
                raise WorktreeError(
                    f"Recorded copied resource changed before fingerprint migration: {main_source_path}"
                )
            return current_fingerprint, True
        finally:
            if os.path.lexists(staging_directory):
                self._path_remove(staging_directory)
            if staging_marker_path.is_symlink():
                staging_marker_path.unlink()

    def _path_remove(self, path: Path) -> None:
        """Remove one exact provider-owned filesystem object.

        Args:
            path: Exact object path.
        """

        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def _utf8_text_get(self, path: Path, label: str) -> str:
        """Read one required UTF-8 text object through a normalized error boundary."""

        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise WorktreeError(f"{label} is not readable UTF-8 text: {path}") from exc

    def _owned_staging_object_remove(
        self,
        staging_path: Path,
        expected_fingerprint: str,
        label: str,
    ) -> None:
        """Discard one regenerable staging slot owned by durable metadata."""

        observed_fingerprint = self._path_fingerprint_get(staging_path)
        if observed_fingerprint == "absent":
            return
        if observed_fingerprint != expected_fingerprint:
            self._provider_owned_staging_object_validate(staging_path, label)
        self._path_remove(staging_path)

    def _provider_owned_staging_object_validate(self, staging_path: Path, label: str) -> None:
        """Require a safely removable ordinary object in one proven staging slot."""

        path_list = [staging_path]
        if staging_path.is_dir() and not staging_path.is_symlink():
            path_list.extend(staging_path.rglob("*"))
        for candidate_path in path_list:
            mode = candidate_path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise WorktreeError(f"{label} contains a special filesystem object: {candidate_path}")
            if stat.S_ISREG(mode) and candidate_path.stat(follow_symlinks=False).st_nlink != 1:
                raise WorktreeError(f"{label} contains a hardlinked regular file: {candidate_path}")

    def _private_clone_staging_directory_discard(
        self,
        staging_directory: Path,
        expected_fingerprint_by_name_map: dict[str, str],
        label: str,
    ) -> bool:
        """Discard only absent or exact provider clone objects."""

        if not os.path.lexists(staging_directory):
            return False
        if staging_directory.is_symlink() or not staging_directory.is_dir():
            raise WorktreeError(f"{label} is not one physical directory: {staging_directory}")
        for candidate_path in staging_directory.iterdir():
            expected_fingerprint = expected_fingerprint_by_name_map.get(candidate_path.name)
            if expected_fingerprint is None:
                raise WorktreeError(f"{label} contains unknown staging content: {candidate_path}")
            if self._path_fingerprint_get(candidate_path) != expected_fingerprint:
                raise WorktreeError(f"{label} contains changed staging content: {candidate_path}")
        self._path_remove(staging_directory)
        return True

    def _main_preimage_staging_directory_discard(
        self,
        repository_root: Path,
        staging_directory: Path,
        index_entry_list: list[str],
        working_fingerprint: str,
        label: str,
    ) -> bool:
        """Discard only an exact or incomplete provider main-preimage staging tree."""

        if not os.path.lexists(staging_directory):
            return False
        if staging_directory.is_symlink() or not staging_directory.is_dir():
            raise WorktreeError(f"{label} is not one physical directory: {staging_directory}")
        expected_object_id_by_snapshot_name_map: dict[str, str] = {}
        for entry_text in index_entry_list:
            mode_text, object_id, stage_text = self._index_entry_fields_get(entry_text)
            if mode_text != "160000":
                expected_object_id_by_snapshot_name_map[f"{stage_text}-{mode_text}-{object_id}.blob"] = object_id
        for candidate_path in staging_directory.iterdir():
            if candidate_path.name == "working":
                if self._path_fingerprint_get(candidate_path) != working_fingerprint:
                    raise WorktreeError(f"{label} contains changed working content: {candidate_path}")
                continue
            if candidate_path.name != "index":
                raise WorktreeError(f"{label} contains unknown staging content: {candidate_path}")
            if candidate_path.is_symlink() or not candidate_path.is_dir():
                raise WorktreeError(f"{label} contains an invalid index staging directory: {candidate_path}")
            for snapshot_path in candidate_path.iterdir():
                object_id = expected_object_id_by_snapshot_name_map.get(snapshot_path.name)
                if (
                    object_id is None
                    or snapshot_path.is_symlink()
                    or not snapshot_path.is_file()
                    or self._blob_object_id_get(repository_root, snapshot_path.read_bytes()) != object_id
                ):
                    raise WorktreeError(f"{label} contains changed index staging content: {snapshot_path}")
        self._path_remove(staging_directory)
        return True

    def _relative_path_validate(self, path: Path, label: str) -> Path:
        """Validate one repository-relative boundary path.

        Args:
            path: Raw path.
            label: Diagnostic field name.

        Returns:
            Canonical relative path.
        """

        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise WorktreeError(f"{label} must be one normalized root-relative path: {path}")
        return path

    def _git_path_text_validate(self, path_text: str, label: str) -> None:
        """Validate one Git-reported root-relative path from private state.

        Args:
            path_text: Raw root-relative path text.
            label: Diagnostic object name.
        """

        if (
            not path_text
            or "\0" in path_text
            or path_text.startswith("/")
            or any(part in {"", ".", ".."} for part in path_text.split("/"))
            or PurePosixPath(path_text).as_posix() != path_text
        ):
            raise WorktreeError(f"{label} is not one normalized root-relative path: {path_text!r}")

    def _repository_root_list_get(self, repository_list: list[Path]) -> list[Path]:
        """Return the coordinating and additional repository roots without duplicates.

        Args:
            repository_list: Additional repository roots.

        Returns:
            Canonical repository-root list.
        """

        repository_root_list: list[Path] = []
        observed_root_set: set[Path] = set()
        for raw_repository in [self._coordinating_repository, *repository_list]:
            repository_root = self._repository_root_validate(raw_repository)
            if repository_root in observed_root_set:
                continue
            observed_root_set.add(repository_root)
            repository_root_list.append(repository_root)
        return repository_root_list

    def _participating_submodule_path_set_by_main_root_get(
        self,
        repository_root_list: list[Path],
        participating_submodule_list: list[tuple[Path, Path]],
    ) -> dict[str, set[str]]:
        """Normalize explicit task-owned submodule declarations by main root.

        Args:
            repository_root_list: Complete participating top-level main roots.
            participating_submodule_list: Raw main-root and submodule-path pairs.

        Returns:
            Exact normalized recursive submodule paths by canonical main root.
        """

        canonical_root_by_text_map = {str(repository_root): repository_root for repository_root in repository_root_list}
        path_set_by_main_root_map = {root_text: set() for root_text in canonical_root_by_text_map}
        for raw_main_root, raw_submodule_path in participating_submodule_list:
            main_root_text = str(raw_main_root.expanduser().resolve())
            if main_root_text not in canonical_root_by_text_map:
                raise WorktreeError(
                    f"Task-owned submodule references a nonparticipating main repository: {main_root_text}"
                )
            submodule_path = self._relative_path_validate(raw_submodule_path, "participating submodule")
            submodule_path_text = submodule_path.as_posix()
            if submodule_path_text in path_set_by_main_root_map[main_root_text]:
                raise WorktreeError(
                    f"Task-owned submodule is declared more than once: {main_root_text}:{submodule_path_text}"
                )
            path_set_by_main_root_map[main_root_text].add(submodule_path_text)
        return path_set_by_main_root_map

    def _repository_root_validate(self, repository: Path) -> Path:
        """Validate and canonicalize one explicit Git repository root.

        Args:
            repository: Candidate repository root.

        Returns:
            Canonical repository root.
        """

        repository_root = repository.expanduser().resolve()
        if not repository_root.is_dir():
            raise WorktreeError(f"Repository root is not a directory: {repository_root}")
        result = self._git_command.run(repository_root, ["rev-parse", "--show-toplevel"], check=False)
        if result.returncode != 0 or Path(result.stdout.strip()).resolve() != repository_root:
            raise WorktreeError(f"Path is not an exact Git repository root: {repository_root}")
        raw_git_directory = Path(self._git_command.run(repository_root, ["rev-parse", "--git-dir"]).stdout.strip())
        if not raw_git_directory.is_absolute():
            raw_git_directory = repository_root / raw_git_directory
        if raw_git_directory.resolve() != self._git_common_directory_get(repository_root):
            raise WorktreeError(f"Repository path is not its main worktree: {repository_root}")
        branch_name = self._git_command.run(
            repository_root,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            check=False,
        ).stdout.strip()
        if branch_name != "main":
            raise WorktreeError(f"Main worktree must have branch main checked out: {repository_root}")
        return repository_root

    def _repository_state_get(
        self,
        baseline_commit: str,
        repository_root: Path,
        participating_submodule_state_list: list[ParticipatingSubmoduleState],
        resource_state_list: list[ResourceState],
        task_root: Path,
        temporary_exclude_list: list[str],
        previous_repository_state: RepositoryState | None,
        performed_repair_list: list[str],
    ) -> RepositoryState:
        """Build one current repository-state record.

        Args:
            baseline_commit: Selected task baseline commit.
            repository_root: Main-worktree root.
            participating_submodule_state_list: Explicit task-owned submodule state.
            resource_state_list: Prepared resource state.
            task_root: Task-worktree root.
            temporary_exclude_list: Provider-owned local excludes.
            previous_repository_state: Existing state for this root when present.
            performed_repair_list: Mutable repair report.

        Returns:
            Current repository state.
        """

        main_status_by_path_map = self._status_by_path_map_get(repository_root)
        main_status_fingerprint_by_path_map = {
            path_text: self._path_git_state_fingerprint_get(repository_root, path_text)
            for path_text in main_status_by_path_map
        }
        previous_status_by_path_map: dict[str, str] = {}
        previous_fingerprint_by_path_map: dict[str, str] = {}
        previous_preimage_by_path_map: dict[str, MainPathPreimage] = {}
        if previous_repository_state is not None:
            baseline_commit = previous_repository_state["baseline_commit"]
            preserved_status_by_path_map = previous_repository_state["main_status_by_path_map"]
            preserved_fingerprint_by_path_map = previous_repository_state["main_status_fingerprint_by_path_map"]
            previous_status_by_path_map = preserved_status_by_path_map
            previous_fingerprint_by_path_map = preserved_fingerprint_by_path_map
            previous_preimage_by_path_map = previous_repository_state["main_preimage_by_path_map"]
            temporary_exclude_list.extend(previous_repository_state["temporary_exclude_list"])
        main_preimage_by_path_map = self._main_preimage_by_path_map_refresh(
            repository_root,
            task_root,
            main_status_by_path_map,
            main_status_fingerprint_by_path_map,
            previous_status_by_path_map,
            previous_fingerprint_by_path_map,
            previous_preimage_by_path_map,
            performed_repair_list,
        )
        return {
            "accepted_main_commit_drift_list": (
                previous_repository_state["accepted_main_commit_drift_list"]
                if previous_repository_state is not None
                else []
            ),
            "baseline_commit": baseline_commit,
            "branch_name": self._prefix,
            "common_git_directory": str(self._git_common_directory_get(repository_root)),
            "main_commit": self._git_command.run(repository_root, ["rev-parse", "HEAD"]).stdout.strip(),
            "main_preimage_by_path_map": main_preimage_by_path_map,
            "main_leak_fingerprint_by_path_map": (
                previous_repository_state["main_leak_fingerprint_by_path_map"]
                if previous_repository_state is not None
                else {}
            ),
            "main_root": str(repository_root),
            "main_status_by_path_map": main_status_by_path_map,
            "main_status_fingerprint_by_path_map": main_status_fingerprint_by_path_map,
            "manifest_fingerprint": self._path_fingerprint_get(task_root / MANIFEST_NAME),
            "participating_submodule_state_list": participating_submodule_state_list,
            "resource_state_list": resource_state_list,
            "submodule_commit_by_path_map": (
                previous_repository_state["submodule_commit_by_path_map"]
                if previous_repository_state is not None
                else self._submodule_commit_by_path_map_get(task_root)
            ),
            "task_root": str(task_root),
            "temporary_exclude_list": sorted(set(temporary_exclude_list)),
        }

    def _resource_state_list_prepare(
        self,
        main_root: Path,
        resource_by_class_map: dict[str, list[str]],
        task_root: Path,
        performed_repair_list: list[str],
        skipped_optional_resource_list: list[str],
        previous_resource_state_list: list[ResourceState],
    ) -> list[ResourceState]:
        """Materialize manifest resources and return their state.

        Args:
            main_root: Main-worktree source root.
            resource_by_class_map: Validated resources by class.
            task_root: Task-worktree destination root.
            performed_repair_list: Mutable repair report.
            skipped_optional_resource_list: Mutable optional-resource report.
            previous_resource_state_list: Previously recorded resource ownership.

        Returns:
            Prepared resource-state list.
        """

        previous_resource_state_by_path_map = {item["path"]: item for item in previous_resource_state_list}
        current_path_set = {path_text for path_list in resource_by_class_map.values() for path_text in path_list}
        self._resource_transaction_owner_reconcile(
            task_root,
            previous_resource_state_by_path_map,
            performed_repair_list,
        )
        self._resource_source_preimage_owner_reconcile(
            task_root,
            previous_resource_state_list,
            performed_repair_list,
            allowed_path_set=current_path_set,
            retire_obsolete=False,
        )
        resource_state_list: list[ResourceState] = []
        for resource_class in sorted(resource_by_class_map):
            strategy = "copy" if resource_class.startswith("copy_") else "link"
            required = "_required_" in resource_class
            for path_text in resource_by_class_map[resource_class]:
                source_path = main_root / path_text
                destination_path = task_root / path_text
                self._path_parent_boundary_validate(task_root, destination_path, "Resource destination")
                previous_resource_state = previous_resource_state_by_path_map.get(path_text)
                self._resource_unexposed_staging_repair(
                    main_root=main_root,
                    task_root=task_root,
                    path_text=path_text,
                    strategy=strategy,
                    previous_resource_state=previous_resource_state,
                    performed_repair_list=performed_repair_list,
                )
                pending_transaction = self._resource_transaction_optional_get(
                    task_root,
                    path_text,
                    performed_repair_list,
                )
                if pending_transaction is not None:
                    expected_previous_present = (
                        previous_resource_state is not None and not previous_resource_state["skipped"]
                    )
                    previous_state_matches = pending_transaction["previous_present"] == expected_previous_present and (
                        not expected_previous_present
                        or (
                            previous_resource_state is not None
                            and pending_transaction["previous_fingerprint"]
                            == previous_resource_state["destination_fingerprint"]
                        )
                    )
                    current_source_fingerprint = self._path_fingerprint_get(source_path)
                    if (
                        previous_state_matches
                        and pending_transaction["strategy"] == strategy
                        and os.path.lexists(source_path)
                        and current_source_fingerprint == pending_transaction["source_fingerprint"]
                    ):
                        self._source_boundary_validate(main_root, source_path)
                        if (
                            self._git_command.run(
                                task_root,
                                ["ls-files", "--error-unmatch", "--", path_text],
                                check=False,
                            ).returncode
                            == 0
                        ):
                            raise WorktreeError(f"Bootstrap destination is tracked by Git: {destination_path}")
                        if strategy == "copy":
                            self._path_copy_source_validate(source_path)
                        self._resource_source_preimage_prepare(
                            task_root,
                            path_text,
                            source_path,
                            pending_transaction["source_fingerprint"],
                            performed_repair_list,
                        )
                        self._resource_transaction_expose(
                            task_root,
                            pending_transaction,
                            performed_repair_list,
                        )
                        resource_state_list.append(
                            {
                                "destination_fingerprint": pending_transaction["destination_fingerprint"],
                                "path": path_text,
                                "required": required,
                                "skipped": False,
                                "source_fingerprint": pending_transaction["source_fingerprint"],
                                "strategy": strategy,
                            }
                        )
                        continue
                    self._resource_transaction_rollback(
                        task_root,
                        pending_transaction,
                        performed_repair_list,
                    )
                if previous_resource_state is None and os.path.lexists(destination_path):
                    raise WorktreeError(
                        f"Resource destination contains unrecorded independent content: {destination_path}"
                    )
                if not os.path.lexists(source_path):
                    if required:
                        raise WorktreeError(f"Required {strategy} resource does not exist: {source_path}")
                    if os.path.lexists(destination_path):
                        raise WorktreeError(
                            f"Optional resource source is absent but destination exists: {destination_path}"
                        )
                    skipped_optional_resource_list.append(f"{main_root}:{path_text}")
                    resource_state_list.append(
                        {
                            "destination_fingerprint": "absent",
                            "path": path_text,
                            "required": False,
                            "skipped": True,
                            "source_fingerprint": "absent",
                            "strategy": strategy,
                        }
                    )
                    continue
                self._source_boundary_validate(main_root, source_path)
                if (
                    self._git_command.run(
                        task_root,
                        ["ls-files", "--error-unmatch", "--", path_text],
                        check=False,
                    ).returncode
                    == 0
                ):
                    raise WorktreeError(f"Bootstrap destination is tracked by Git: {destination_path}")
                source_fingerprint = self._path_fingerprint_get(source_path)
                strategy_changed = (
                    previous_resource_state is not None and previous_resource_state["strategy"] != strategy
                )
                if (
                    previous_resource_state is not None
                    and not previous_resource_state["skipped"]
                    and source_fingerprint != previous_resource_state["source_fingerprint"]
                ):
                    if strategy == "copy":
                        raise WorktreeError(f"Cannot recreate copy after its source changed: {source_path}")
                    raise WorktreeError(f"Shared link source changed during task execution: {source_path}")
                if strategy == "copy":
                    self._path_copy_source_validate(source_path)
                self._resource_source_preimage_prepare(
                    task_root,
                    path_text,
                    source_path,
                    source_fingerprint,
                    performed_repair_list,
                )
                if strategy_changed and os.path.lexists(destination_path):
                    if (
                        previous_resource_state is None
                        or previous_resource_state["skipped"]
                        or self._path_fingerprint_get(destination_path)
                        != previous_resource_state["destination_fingerprint"]
                    ):
                        raise WorktreeError(
                            f"Resource strategy changed after independent destination changes: {destination_path}"
                        )
                    if strategy == "copy":
                        self._path_copy_source_validate(source_path)
                    self._resource_transaction_create(
                        task_root,
                        path_text,
                        source_path,
                        source_fingerprint,
                        strategy,
                        performed_repair_list,
                    )
                    performed_repair_list.append(
                        f"replaced unmodified destination for resource strategy change: {destination_path}"
                    )
                if strategy == "copy":
                    if previous_resource_state is None or previous_resource_state["skipped"]:
                        self._path_copy_source_validate(source_path)
                    if not os.path.lexists(destination_path):
                        self._resource_transaction_create(
                            task_root,
                            path_text,
                            source_path,
                            source_fingerprint,
                            strategy,
                            performed_repair_list,
                        )
                        performed_repair_list.append(f"materialized copy resource: {destination_path}")
                    elif destination_path.is_symlink():
                        raise WorktreeError(f"Copy destination is an independent symbolic link: {destination_path}")
                    elif (
                        previous_resource_state is None
                        and self._path_fingerprint_get(destination_path) != source_fingerprint
                    ):
                        raise WorktreeError(f"Copy destination contains independent content: {destination_path}")
                else:
                    expected_target = os.path.relpath(source_path, start=destination_path.parent)
                    if destination_path.is_symlink() and os.readlink(destination_path) == expected_target:
                        pass
                    elif not os.path.lexists(destination_path):
                        self._resource_transaction_create(
                            task_root,
                            path_text,
                            source_path,
                            source_fingerprint,
                            strategy,
                            performed_repair_list,
                        )
                        performed_repair_list.append(f"materialized link resource: {destination_path}")
                    else:
                        raise WorktreeError(f"Link destination contains independent content: {destination_path}")
                resource_state_list.append(
                    {
                        "destination_fingerprint": (
                            previous_resource_state["destination_fingerprint"]
                            if (
                                previous_resource_state is not None
                                and not previous_resource_state["skipped"]
                                and not strategy_changed
                            )
                            else self._path_fingerprint_get(destination_path)
                        ),
                        "path": path_text,
                        "required": required,
                        "skipped": False,
                        "source_fingerprint": (
                            previous_resource_state["source_fingerprint"]
                            if previous_resource_state is not None and not previous_resource_state["skipped"]
                            else source_fingerprint
                        ),
                        "strategy": strategy,
                    }
                )
        for previous_resource_state in previous_resource_state_list:
            if previous_resource_state["path"] in current_path_set or previous_resource_state["skipped"]:
                continue
            destination_path = task_root / previous_resource_state["path"]
            self._path_parent_boundary_validate(task_root, destination_path, "Former resource destination")
            self._resource_unexposed_staging_repair(
                main_root=main_root,
                task_root=task_root,
                path_text=previous_resource_state["path"],
                strategy="remove",
                previous_resource_state=previous_resource_state,
                performed_repair_list=performed_repair_list,
            )
            pending_transaction = self._resource_transaction_optional_get(
                task_root,
                previous_resource_state["path"],
                performed_repair_list,
            )
            if pending_transaction is not None:
                if (
                    pending_transaction["strategy"] == "remove"
                    and pending_transaction["previous_present"]
                    and pending_transaction["previous_fingerprint"]
                    == previous_resource_state["destination_fingerprint"]
                ):
                    self._resource_transaction_expose(
                        task_root,
                        pending_transaction,
                        performed_repair_list,
                    )
                    continue
                self._resource_transaction_rollback(
                    task_root,
                    pending_transaction,
                    performed_repair_list,
                )
            if not os.path.lexists(destination_path):
                continue
            if self._path_fingerprint_get(destination_path) != previous_resource_state["destination_fingerprint"]:
                raise WorktreeError(f"Removed manifest resource contains independent task changes: {destination_path}")
            self._resource_removal_transaction_create(
                task_root,
                previous_resource_state["path"],
                previous_resource_state["destination_fingerprint"],
                performed_repair_list,
            )
            performed_repair_list.append(f"removed unmodified former resource: {destination_path}")
        return resource_state_list

    def _resource_transaction_directory_get(self, task_root: Path, path_text: str) -> Path:
        """Return one deterministic private transaction directory.

        Args:
            task_root: Exact resource-owning task root.
            path_text: Root-relative resource path.

        Returns:
            Path below the worktree's private Git administration.
        """

        transaction_name = hashlib.sha256(os.fsencode(path_text)).hexdigest()
        return self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "resource-transaction-v1" / transaction_name,
        )

    def _private_clone_staging_directory_create(
        self,
        task_root: Path,
        purpose: str,
        identity: str,
    ) -> tuple[Path, Path]:
        """Create one unpredictable clone stage after an atomic ownership intent."""

        if purpose not in PRIVATE_CLONE_STAGING_PURPOSE_SET:
            raise WorktreeError(f"Unsupported private clone-staging purpose: {purpose}")
        staging_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "private-clone-staging-v1" / purpose,
        )
        marker_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "private-clone-staging-owner-v1" / purpose,
        )
        for owner_path, label in (
            (staging_root, "Private clone-staging owner"),
            (marker_root, "Private clone-staging marker owner"),
        ):
            if os.path.lexists(owner_path) and (owner_path.is_symlink() or not owner_path.is_dir()):
                raise WorktreeError(f"{label} is not one physical directory: {owner_path}")
            owner_path.mkdir(parents=True, exist_ok=True)
        while True:
            staging_name = secrets.token_hex(32)
            staging_directory = staging_root / staging_name
            marker_path = marker_root / f"{staging_name}.intent"
            if not os.path.lexists(staging_directory) and not os.path.lexists(marker_path):
                break
        marker_path.symlink_to(
            json.dumps(
                {
                    "identity_sha256": hashlib.sha256(os.fsencode(identity)).hexdigest(),
                    "purpose": purpose,
                    "schema_version": 1,
                    "staging_name": staging_name,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        self._directory_fsync(marker_root)
        staging_directory.mkdir()
        return staging_directory, marker_path

    def _private_clone_staging_list_reconcile(
        self,
        task_root: Path,
        purpose: str,
        performed_repair_list: list[str],
    ) -> None:
        """Discard only unpublished clone stages with atomic random ownership."""

        if purpose not in PRIVATE_CLONE_STAGING_PURPOSE_SET:
            raise WorktreeError(f"Unsupported private clone-staging purpose: {purpose}")
        staging_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "private-clone-staging-v1" / purpose,
        )
        marker_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "private-clone-staging-owner-v1" / purpose,
        )
        for owner_path, label in (
            (staging_root, "Private clone-staging owner"),
            (marker_root, "Private clone-staging marker owner"),
        ):
            if os.path.lexists(owner_path) and (owner_path.is_symlink() or not owner_path.is_dir()):
                raise WorktreeError(f"{label} is not one physical directory: {owner_path}")
        if not os.path.lexists(staging_root) and not os.path.lexists(marker_root):
            return
        marker_by_staging_name_map: dict[str, Path] = {}
        if marker_root.is_dir():
            for marker_path in marker_root.iterdir():
                if not marker_path.name.endswith(".intent") or not marker_path.is_symlink():
                    raise WorktreeError(f"Private clone-staging intent is damaged: {marker_path}")
                staging_name = marker_path.name.removesuffix(".intent")
                if len(staging_name) != 64 or not _hex_digest_is_valid(staging_name, {64}):
                    raise WorktreeError(f"Private clone-staging identity is invalid: {marker_path}")
                try:
                    payload = json.loads(os.readlink(marker_path))
                except json.JSONDecodeError as exc:
                    raise WorktreeError(f"Private clone-staging intent is invalid: {marker_path}") from exc
                if (
                    not isinstance(payload, dict)
                    or set(payload)
                    != {
                        "identity_sha256",
                        "purpose",
                        "schema_version",
                        "staging_name",
                    }
                    or payload.get("purpose") != purpose
                    or type(payload.get("schema_version")) is not int
                    or payload.get("schema_version") != 1
                    or payload.get("staging_name") != staging_name
                    or not _hex_digest_is_valid(payload.get("identity_sha256"), {64})
                ):
                    raise WorktreeError(f"Private clone-staging intent is invalid: {marker_path}")
                marker_by_staging_name_map[staging_name] = marker_path
        staging_by_name_map: dict[str, Path] = {}
        if staging_root.is_dir():
            for staging_directory in staging_root.iterdir():
                if (
                    staging_directory.name not in marker_by_staging_name_map
                    or staging_directory.is_symlink()
                    or not staging_directory.is_dir()
                ):
                    raise WorktreeError(f"Private clone staging has no atomic ownership: {staging_directory}")
                staging_by_name_map[staging_directory.name] = staging_directory
        for staging_name, marker_path in marker_by_staging_name_map.items():
            staging_directory = staging_by_name_map.get(staging_name)
            if staging_directory is not None:
                self._provider_owned_staging_object_validate(
                    staging_directory,
                    "Owned private clone staging",
                )
                self._path_remove(staging_directory)
            marker_path.unlink()
            performed_repair_list.append(f"removed interrupted unpublished {purpose} staging: {task_root}")
        if marker_root.is_dir():
            self._directory_fsync(marker_root)

    def _resource_transaction_staging_directory_create(
        self,
        task_root: Path,
        path_text: str,
        intent_payload: dict[str, object],
    ) -> tuple[Path, Path]:
        """Create one atomically owned unpredictable staging directory."""

        staging_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "resource-transaction-staging-v1",
        )
        if os.path.lexists(staging_root) and (staging_root.is_symlink() or not staging_root.is_dir()):
            raise WorktreeError(f"Resource transaction staging owner is not one private directory: " f"{staging_root}")
        staging_root.mkdir(parents=True, exist_ok=True)
        marker_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "resource-transaction-staging-owner-v1",
        )
        if os.path.lexists(marker_root) and (marker_root.is_symlink() or not marker_root.is_dir()):
            raise WorktreeError(
                f"Resource transaction staging-marker owner is not one private directory: " f"{marker_root}"
            )
        marker_root.mkdir(parents=True, exist_ok=True)
        while True:
            staging_name = secrets.token_hex(32)
            staging_directory = staging_root / staging_name
            marker_path = marker_root / f"{staging_name}.intent"
            if not os.path.lexists(staging_directory) and not os.path.lexists(marker_path):
                break
        complete_payload = {
            **intent_payload,
            "path": path_text,
            "schema_version": 1,
            "staging_name": staging_name,
        }
        marker_path.symlink_to(
            json.dumps(
                complete_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        directory_descriptor = os.open(marker_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        staging_directory.mkdir()
        return staging_directory, marker_path

    def _resource_transaction_staging_reconcile(
        self,
        task_root: Path,
        performed_repair_list: list[str],
    ) -> None:
        """Discard only unpublished staging with one atomic intent marker."""

        staging_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "resource-transaction-staging-v1",
        )
        marker_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "resource-transaction-staging-owner-v1",
        )
        for owner_path, label in (
            (staging_root, "Resource transaction staging owner"),
            (marker_root, "Resource transaction staging-marker owner"),
        ):
            if os.path.lexists(owner_path) and (owner_path.is_symlink() or not owner_path.is_dir()):
                raise WorktreeError(f"{label} is not one private directory: {owner_path}")
        if not os.path.lexists(staging_root) and not os.path.lexists(marker_root):
            return
        marker_by_staging_name_map: dict[str, Path] = {}
        if marker_root.is_dir():
            for marker_path in marker_root.iterdir():
                if not marker_path.name.endswith(".intent") or not marker_path.is_symlink():
                    raise WorktreeError(f"Resource transaction staging marker is damaged: {marker_path}")
                staging_name = marker_path.name.removesuffix(".intent")
                if len(staging_name) != 64 or not _hex_digest_is_valid(staging_name, {64}):
                    raise WorktreeError(f"Resource transaction staging marker identity is invalid: {marker_path}")
                try:
                    payload = json.loads(os.readlink(marker_path))
                except json.JSONDecodeError as exc:
                    raise WorktreeError(f"Resource transaction staging marker is invalid: {marker_path}") from exc
                expected_key_set = {
                    "path",
                    "previous_fingerprint",
                    "previous_present",
                    "schema_version",
                    "source_fingerprint",
                    "staging_name",
                    "strategy",
                }
                if (
                    not isinstance(payload, dict)
                    or set(payload) != expected_key_set
                    or type(payload.get("schema_version")) is not int
                    or payload.get("schema_version") != 1
                    or payload.get("staging_name") != staging_name
                    or not isinstance(payload.get("path"), str)
                    or type(payload.get("previous_present")) is not bool
                    or payload.get("strategy") not in {"copy", "link", "remove"}
                    or any(
                        value != "absent" and not _hex_digest_is_valid(value, {64})
                        for value in (
                            payload.get("previous_fingerprint"),
                            payload.get("source_fingerprint"),
                        )
                    )
                    or payload.get("previous_present") != (payload.get("previous_fingerprint") != "absent")
                    or (payload.get("strategy") == "remove" and payload.get("source_fingerprint") != "absent")
                ):
                    raise WorktreeError(f"Resource transaction staging marker is invalid: {marker_path}")
                self._relative_path_validate(Path(payload["path"]), "resource transaction staging path")
                marker_by_staging_name_map[staging_name] = marker_path
        staging_by_name_map: dict[str, Path] = {}
        if staging_root.is_dir():
            for staging_directory in staging_root.iterdir():
                if (
                    staging_directory.name not in marker_by_staging_name_map
                    or staging_directory.is_symlink()
                    or not staging_directory.is_dir()
                ):
                    raise WorktreeError(f"Resource transaction staging has no atomic ownership: {staging_directory}")
                staging_by_name_map[staging_directory.name] = staging_directory
        for staging_name, marker_path in marker_by_staging_name_map.items():
            staging_directory = staging_by_name_map.get(staging_name)
            if staging_directory is not None:
                for candidate_path in staging_directory.iterdir():
                    if candidate_path.name not in {
                        "metadata.json",
                        "metadata.json.tmp",
                        "previous",
                        "replacement",
                    }:
                        raise WorktreeError(f"Resource transaction staging contains unknown content: {candidate_path}")
                    self._provider_owned_staging_object_validate(
                        candidate_path,
                        "Owned resource transaction staging",
                    )
                self._path_remove(staging_directory)
            marker_path.unlink()
            performed_repair_list.append(f"removed interrupted unpublished resource transaction staging: {task_root}")

    def _resource_unexposed_staging_repair(
        self,
        *,
        main_root: Path,
        task_root: Path,
        path_text: str,
        strategy: str,
        previous_resource_state: ResourceState | None,
        performed_repair_list: list[str],
    ) -> None:
        """Retire only an empty legacy staging directory without inferred ownership."""

        transaction_directory = self._resource_transaction_directory_get(task_root, path_text)
        if not os.path.lexists(transaction_directory):
            return
        if transaction_directory.is_symlink() or not transaction_directory.is_dir():
            raise WorktreeError(f"Resource transaction path is not one private directory: {transaction_directory}")
        metadata_path = transaction_directory / "metadata.json"
        if os.path.lexists(metadata_path):
            return
        if any(transaction_directory.iterdir()):
            raise WorktreeError(f"Resource transaction metadata is unavailable: {metadata_path}")
        self._path_remove(transaction_directory)
        performed_repair_list.append(
            f"removed unexposed resource transaction staging (empty): " f"{task_root / path_text}"
        )

    def _resource_source_preimage_directory_get(self, task_root: Path, path_text: str) -> Path:
        """Return one deterministic private source-preimage directory.

        Args:
            task_root: Exact resource-owning task root.
            path_text: Root-relative resource boundary.

        Returns:
            Path below the worktree's private Git administration.
        """

        snapshot_name = hashlib.sha256(os.fsencode(path_text)).hexdigest()
        return self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "resource-source-preimage-v1" / snapshot_name,
        )

    def _resource_source_preimage_metadata_get(
        self,
        snapshot_directory: Path,
        *,
        enforce_directory_identity: bool = True,
    ) -> dict[str, object]:
        """Load one closed resource-source preimage ownership record."""

        metadata_path = snapshot_directory / "metadata.json"
        if (
            metadata_path.is_symlink()
            or not metadata_path.is_file()
            or metadata_path.stat(follow_symlinks=False).st_nlink != 1
        ):
            raise WorktreeError(f"Resource source-preimage metadata is unavailable: {metadata_path}")
        try:
            payload = json.loads(self._utf8_text_get(metadata_path, "Resource source-preimage metadata"))
        except json.JSONDecodeError as exc:
            raise WorktreeError(f"Resource source-preimage metadata is invalid: {metadata_path}") from exc
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {
                "fingerprint_schema_version",
                "path",
                "schema_version",
                "source_fingerprint",
            }
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
            or type(payload.get("fingerprint_schema_version")) is not int
            or payload.get("fingerprint_schema_version") != 2
            or not isinstance(payload.get("path"), str)
            or not _hex_digest_is_valid(payload.get("source_fingerprint"), {64})
        ):
            raise WorktreeError(f"Resource source-preimage metadata is invalid: {metadata_path}")
        self._manifest_resource_path_validate(cast(str, payload["path"]))
        if (
            enforce_directory_identity
            and snapshot_directory.name != hashlib.sha256(os.fsencode(payload["path"])).hexdigest()
        ):
            raise WorktreeError(f"Resource source-preimage identity is inconsistent: {snapshot_directory}")
        return cast(dict[str, object], payload)

    def _resource_source_preimage_validate(
        self,
        snapshot_directory: Path,
        path_text: str,
        source_fingerprint: str,
        *,
        enforce_directory_identity: bool = True,
        migrate_legacy: bool,
    ) -> None:
        """Validate one exact source snapshot and optionally add closed metadata."""

        if snapshot_directory.is_symlink() or not snapshot_directory.is_dir():
            raise WorktreeError(f"Private resource source preimage is not one physical directory: {snapshot_directory}")
        allowed_entry_name_set = {"metadata.json", "source"}
        unknown_entry_list = [
            candidate_path
            for candidate_path in snapshot_directory.iterdir()
            if candidate_path.name not in allowed_entry_name_set
        ]
        if unknown_entry_list:
            raise WorktreeError(f"Private resource source preimage contains unknown content: {unknown_entry_list[0]}")
        snapshot_path = snapshot_directory / "source"
        if self._path_fingerprint_get(snapshot_path) != source_fingerprint:
            raise WorktreeError(f"Private resource source preimage contains changed content: {snapshot_path}")
        metadata_path = snapshot_directory / "metadata.json"
        if not os.path.lexists(metadata_path):
            if not migrate_legacy:
                raise WorktreeError(f"Resource source-preimage metadata is unavailable: {metadata_path}")
            self._private_text_atomic_write(
                metadata_path,
                json.dumps(
                    {
                        "fingerprint_schema_version": 2,
                        "path": path_text,
                        "schema_version": 1,
                        "source_fingerprint": source_fingerprint,
                    },
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
        payload = self._resource_source_preimage_metadata_get(
            snapshot_directory,
            enforce_directory_identity=enforce_directory_identity,
        )
        if payload["path"] != path_text or payload["source_fingerprint"] != source_fingerprint:
            raise WorktreeError(f"Resource source-preimage ownership changed: {snapshot_directory}")

    def _resource_source_preimage_owner_reconcile(
        self,
        task_root: Path,
        resource_state_list: list[ResourceState],
        performed_repair_list: list[str],
        *,
        allowed_path_set: set[str] | None = None,
        retire_obsolete: bool,
    ) -> None:
        """Close-scan source snapshots and retire only durable obsolete ownership."""

        self._private_clone_staging_list_reconcile(
            task_root,
            "resource-source-preimage",
            performed_repair_list,
        )
        preimage_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "resource-source-preimage-v1",
        )
        if not os.path.lexists(preimage_root):
            return
        if preimage_root.is_symlink() or not preimage_root.is_dir():
            raise WorktreeError(f"Resource source-preimage owner is not one physical directory: {preimage_root}")
        expected_fingerprint_by_path_map = {
            item["path"]: item["source_fingerprint"] for item in resource_state_list if not item["skipped"]
        }
        allowed_path_set = set(expected_fingerprint_by_path_map) | (allowed_path_set or set())
        expected_path_by_snapshot_name_map = {
            hashlib.sha256(os.fsencode(path_text)).hexdigest(): path_text for path_text in allowed_path_set
        }
        for candidate_path in sorted(preimage_root.iterdir()):
            candidate_name = candidate_path.name
            if candidate_name.endswith(".capture"):
                snapshot_name = candidate_name.removesuffix(".capture")
                path_text = expected_path_by_snapshot_name_map.get(snapshot_name)
                expected_fingerprint = (
                    expected_fingerprint_by_path_map.get(path_text) if path_text is not None else None
                )
                if path_text is None or expected_fingerprint is None:
                    raise WorktreeError(f"Resource source-preimage owner contains unknown content: {candidate_path}")
                self._private_clone_staging_directory_discard(
                    candidate_path,
                    {"source": expected_fingerprint},
                    "Legacy resource source-preimage capture",
                )
                performed_repair_list.append(f"retired exact legacy resource source-preimage capture: {candidate_path}")
                continue
            expected_path_text = expected_path_by_snapshot_name_map.get(candidate_name)
            if expected_path_text is not None:
                expected_fingerprint = expected_fingerprint_by_path_map.get(expected_path_text)
                if expected_fingerprint is None:
                    payload = self._resource_source_preimage_metadata_get(candidate_path)
                    if payload["path"] != expected_path_text:
                        raise WorktreeError(f"Resource source-preimage ownership changed: {candidate_path}")
                    self._resource_source_preimage_validate(
                        candidate_path,
                        expected_path_text,
                        cast(str, payload["source_fingerprint"]),
                        migrate_legacy=False,
                    )
                else:
                    self._resource_source_preimage_validate(
                        candidate_path,
                        expected_path_text,
                        expected_fingerprint,
                        migrate_legacy=True,
                    )
                continue
            if not retire_obsolete:
                raise WorktreeError(f"Resource source-preimage owner contains unknown content: {candidate_path}")
            payload = self._resource_source_preimage_metadata_get(candidate_path)
            self._resource_source_preimage_validate(
                candidate_path,
                cast(str, payload["path"]),
                cast(str, payload["source_fingerprint"]),
                migrate_legacy=False,
            )
            self._path_remove(candidate_path)
            performed_repair_list.append(
                f"retired obsolete private resource source preimage: {task_root / cast(str, payload['path'])}"
            )

    def _resource_source_preimage_prepare(
        self,
        task_root: Path,
        path_text: str,
        source_path: Path,
        source_fingerprint: str,
        performed_repair_list: list[str],
    ) -> None:
        """Persist an exact stable source preimage for one materialized resource.

        Args:
            task_root: Exact resource-owning task root.
            path_text: Root-relative resource boundary.
            source_path: Current main-worktree source.
            source_fingerprint: Expected stable source fingerprint.
            performed_repair_list: Mutable repair report.
        """

        self._private_clone_staging_list_reconcile(
            task_root,
            "resource-source-preimage",
            performed_repair_list,
        )
        snapshot_directory = self._resource_source_preimage_directory_get(task_root, path_text)
        if os.path.lexists(snapshot_directory):
            self._resource_source_preimage_validate(
                snapshot_directory,
                path_text,
                source_fingerprint,
                migrate_legacy=True,
            )
            return
        if self._path_fingerprint_get(source_path) != source_fingerprint:
            raise WorktreeError(f"Cannot reconstruct resource source preimage after source drift: {source_path}")
        capture_directory, capture_marker_path = self._private_clone_staging_directory_create(
            task_root,
            "resource-source-preimage",
            path_text,
        )
        capture_published = False
        try:
            self._path_clone(source_path, capture_directory / "source")
            if (
                self._path_fingerprint_get(capture_directory / "source") != source_fingerprint
                or self._path_fingerprint_get(source_path) != source_fingerprint
            ):
                raise WorktreeError(f"Resource source changed during private preimage capture: {source_path}")
            self._private_text_atomic_write(
                capture_directory / "metadata.json",
                json.dumps(
                    {
                        "fingerprint_schema_version": 2,
                        "path": path_text,
                        "schema_version": 1,
                        "source_fingerprint": source_fingerprint,
                    },
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            self._resource_source_preimage_validate(
                capture_directory,
                path_text,
                source_fingerprint,
                enforce_directory_identity=False,
                migrate_legacy=False,
            )
            snapshot_directory.parent.mkdir(parents=True, exist_ok=True)
            capture_directory.replace(snapshot_directory)
            capture_published = True
            capture_marker_path.unlink()
            self._directory_fsync(snapshot_directory.parent)
        except BaseException:
            if not capture_published and os.path.lexists(capture_directory):
                self._path_remove(capture_directory)
            if capture_marker_path.is_symlink():
                capture_marker_path.unlink()
            raise
        performed_repair_list.append(f"captured private resource source preimage: {source_path}")

    def _resource_transaction_optional_get(
        self,
        task_root: Path,
        path_text: str,
        performed_repair_list: list[str] | None = None,
    ) -> ResourceTransaction | None:
        """Load and validate one pending resource transaction when present.

        Args:
            task_root: Exact resource-owning task root.
            path_text: Root-relative resource path.

        Returns:
            Validated transaction or `None`.
        """

        transaction_directory = self._resource_transaction_directory_get(task_root, path_text)
        if not os.path.lexists(transaction_directory):
            return None
        if transaction_directory.is_symlink() or not transaction_directory.is_dir():
            raise WorktreeError(f"Resource transaction path is not one private directory: {transaction_directory}")
        metadata_path = transaction_directory / "metadata.json"
        if not os.path.lexists(metadata_path):
            if any(transaction_directory.iterdir()):
                raise WorktreeError(f"Resource transaction metadata is unavailable: {metadata_path}")
            self._path_remove(transaction_directory)
            if performed_repair_list is not None:
                performed_repair_list.append(f"removed unexposed resource transaction staging: {transaction_directory}")
            return None
        metadata_temporary_path = transaction_directory / "metadata.json.tmp"
        if os.path.lexists(metadata_temporary_path):
            if (
                metadata_temporary_path.is_symlink()
                or not metadata_temporary_path.is_file()
                or metadata_temporary_path.stat(follow_symlinks=False).st_nlink != 1
            ):
                raise WorktreeError(f"Resource transaction metadata staging is damaged: " f"{metadata_temporary_path}")
            metadata_temporary_path.unlink()
            if performed_repair_list is not None:
                performed_repair_list.append(
                    f"removed interrupted resource metadata staging: " f"{metadata_temporary_path}"
                )
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise WorktreeError(f"Resource transaction metadata is unavailable: {metadata_path}")
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorktreeError(f"Resource transaction metadata is invalid: {metadata_path}") from exc
        expected_key_set = {
            "destination_fingerprint",
            "fingerprint_schema_version",
            "path",
            "previous_fingerprint",
            "previous_present",
            "schema_version",
            "source_fingerprint",
            "strategy",
        }
        legacy_fingerprint_key_set = expected_key_set - {"fingerprint_schema_version"}
        if isinstance(payload, dict) and set(payload) == legacy_fingerprint_key_set:
            payload["fingerprint_schema_version"] = 1
        if not isinstance(payload, dict) or set(payload) != expected_key_set:
            raise WorktreeError(f"Resource transaction metadata has an unsupported schema: {metadata_path}")
        if (
            type(payload["fingerprint_schema_version"]) is not int
            or payload["fingerprint_schema_version"] not in {1, 2}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["path"] != path_text
            or type(payload["strategy"]) is not str
            or payload["strategy"] not in {"copy", "link", "remove"}
            or type(payload["previous_present"]) is not bool
            or (
                payload["source_fingerprint"] != "absent"
                and not _hex_digest_is_valid(payload["source_fingerprint"], {64})
            )
            or (
                payload["destination_fingerprint"] != "absent"
                and not _hex_digest_is_valid(payload["destination_fingerprint"], {64})
            )
            or (
                payload["previous_fingerprint"] != "absent"
                and not _hex_digest_is_valid(payload["previous_fingerprint"], {64})
            )
            or (payload["previous_present"] != (payload["previous_fingerprint"] != "absent"))
            or (
                payload["strategy"] == "remove"
                and (
                    payload["source_fingerprint"] != "absent"
                    or payload["destination_fingerprint"] != "absent"
                    or not payload["previous_present"]
                )
            )
            or (
                payload["strategy"] != "remove"
                and (payload["source_fingerprint"] == "absent" or payload["destination_fingerprint"] == "absent")
            )
        ):
            raise WorktreeError(f"Resource transaction metadata is invalid: {metadata_path}")
        allowed_entry_name_set = {
            "displaced",
            "exposure",
            "metadata.json",
            "previous",
            "replacement",
            "rollback",
            "rollback-displaced",
        }
        for candidate_path in transaction_directory.iterdir():
            if candidate_path.name not in allowed_entry_name_set:
                raise WorktreeError(f"Resource transaction contains unknown content: {candidate_path}")
        if payload["fingerprint_schema_version"] == 1:
            destination_path = task_root / path_text
            source_snapshot_path = self._resource_source_preimage_directory_get(task_root, path_text) / "source"
            if payload["source_fingerprint"] != "absent":
                payload["source_fingerprint"], _ = self._recorded_path_fingerprint_candidate_list_upgrade(
                    [source_snapshot_path],
                    payload["source_fingerprint"],
                )
            if payload["destination_fingerprint"] != "absent":
                payload["destination_fingerprint"], _ = self._recorded_path_fingerprint_candidate_list_upgrade(
                    [
                        transaction_directory / "replacement",
                        destination_path,
                    ],
                    payload["destination_fingerprint"],
                )
            if payload["previous_present"]:
                payload["previous_fingerprint"], _ = self._recorded_path_fingerprint_candidate_list_upgrade(
                    [
                        transaction_directory / "previous",
                        transaction_directory / "displaced",
                        destination_path,
                    ],
                    payload["previous_fingerprint"],
                )
            payload["fingerprint_schema_version"] = 2
            self._private_text_atomic_write(
                metadata_path,
                json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            )
            if performed_repair_list is not None:
                performed_repair_list.append(
                    f"upgraded collision-safe resource transaction fingerprints: {transaction_directory}"
                )
        staged_fingerprint_set_by_name_map = {
            "displaced": {
                payload["previous_fingerprint"],
                payload["destination_fingerprint"],
            },
            "rollback-displaced": {
                payload["previous_fingerprint"],
                payload["destination_fingerprint"],
            },
        }
        replacement_path = transaction_directory / "replacement"
        if payload["strategy"] == "remove":
            if os.path.lexists(replacement_path):
                raise WorktreeError(f"Removal transaction has an unexpected replacement: {replacement_path}")
        elif self._path_fingerprint_get(replacement_path) != payload["destination_fingerprint"]:
            raise WorktreeError(f"Resource transaction replacement is unavailable or damaged: {replacement_path}")
        previous_path = transaction_directory / "previous"
        if payload["previous_present"]:
            if self._path_fingerprint_get(previous_path) != payload["previous_fingerprint"]:
                raise WorktreeError(f"Resource transaction previous object is unavailable or damaged: {previous_path}")
        elif os.path.lexists(previous_path):
            raise WorktreeError(f"Resource transaction has an unexpected previous object: {previous_path}")
        for staged_name, allowed_fingerprint_set in staged_fingerprint_set_by_name_map.items():
            staged_path = transaction_directory / staged_name
            if os.path.lexists(staged_path) and self._path_fingerprint_get(staged_path) not in allowed_fingerprint_set:
                raise WorktreeError(f"Resource transaction contains changed staging content: {staged_path}")
        return cast(ResourceTransaction, payload)

    def _resource_transaction_expose(
        self,
        task_root: Path,
        transaction: ResourceTransaction,
        performed_repair_list: list[str],
    ) -> None:
        """Expose an exact staged replacement from one durable transaction.

        Args:
            task_root: Exact resource-owning task root.
            transaction: Validated pending transaction.
            performed_repair_list: Mutable repair report.
        """

        transaction_directory = self._resource_transaction_directory_get(task_root, transaction["path"])
        destination_path = task_root / transaction["path"]
        self._mutation_parent_boundary_validate(
            task_root,
            destination_path,
            "Resource transaction destination",
            allow_missing=True,
        )
        replacement_path = transaction_directory / "replacement"
        if transaction["strategy"] == "remove":
            if os.path.lexists(replacement_path):
                raise WorktreeError(f"Removal transaction has an unexpected replacement: {replacement_path}")
        elif (
            not os.path.lexists(replacement_path)
            or self._path_fingerprint_get(replacement_path) != transaction["destination_fingerprint"]
        ):
            raise WorktreeError(f"Resource transaction replacement is unavailable or damaged: {replacement_path}")
        current_fingerprint = self._path_fingerprint_get(destination_path)
        displaced_path = transaction_directory / "displaced"
        if os.path.lexists(displaced_path) and self._path_fingerprint_get(displaced_path) not in {
            transaction["previous_fingerprint"],
            transaction["destination_fingerprint"],
        }:
            raise WorktreeError(f"Resource transaction displaced object is damaged: {displaced_path}")
        if current_fingerprint == transaction["destination_fingerprint"]:
            if (
                transaction["strategy"] == "remove"
                and transaction["previous_present"]
                and not os.path.lexists(displaced_path)
            ):
                raise WorktreeError(f"Removal transaction lost its displaced object: {destination_path}")
            return
        allowed_fingerprint_set = {"absent", transaction["destination_fingerprint"]}
        if transaction["previous_present"]:
            allowed_fingerprint_set.add(transaction["previous_fingerprint"])
        if current_fingerprint not in allowed_fingerprint_set:
            raise WorktreeError(f"Pending resource destination contains independent content: {destination_path}")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        self._mutation_parent_boundary_validate(
            task_root,
            destination_path,
            "Resource transaction destination",
        )
        exposure_path = transaction_directory / "exposure"
        self._owned_staging_object_remove(
            exposure_path,
            transaction["destination_fingerprint"],
            "Resource exposure staging",
        )
        if transaction["strategy"] != "remove":
            self._path_clone(replacement_path, exposure_path)
            if self._path_fingerprint_get(exposure_path) != transaction["destination_fingerprint"]:
                raise WorktreeError(f"Cannot stage exact resource transaction exposure: {destination_path}")
            if exposure_path.stat(follow_symlinks=False).st_dev != destination_path.parent.stat().st_dev:
                self._owned_staging_object_remove(
                    exposure_path,
                    transaction["destination_fingerprint"],
                    "Resource exposure staging",
                )
                raise WorktreeError(f"Resource transaction cannot atomically cross filesystems: {destination_path}")
        if (
            os.path.lexists(destination_path)
            and destination_path.stat(follow_symlinks=False).st_dev != transaction_directory.stat().st_dev
        ):
            self._owned_staging_object_remove(
                exposure_path,
                transaction["destination_fingerprint"],
                "Resource exposure staging",
            )
            raise WorktreeError(
                f"Resource transaction cannot atomically displace across filesystems: {destination_path}"
            )
        if self._path_fingerprint_get(destination_path) != current_fingerprint:
            self._owned_staging_object_remove(
                exposure_path,
                transaction["destination_fingerprint"],
                "Resource exposure staging",
            )
            raise WorktreeError(f"Pending resource destination changed during exposure: {destination_path}")
        if os.path.lexists(destination_path):
            if os.path.lexists(displaced_path):
                raise WorktreeError(f"Resource transaction already has a displaced object: {displaced_path}")
            destination_path.replace(displaced_path)
        if transaction["strategy"] != "remove":
            exposure_path.replace(destination_path)
        if self._path_fingerprint_get(destination_path) != transaction["destination_fingerprint"]:
            raise WorktreeError(f"Cannot expose exact resource transaction destination: {destination_path}")
        performed_repair_list.append(f"exposed pending resource transaction: {destination_path}")

    def _resource_transaction_rollback(
        self,
        task_root: Path,
        transaction: ResourceTransaction,
        performed_repair_list: list[str],
    ) -> None:
        """Restore the exact pre-transaction destination and retire the marker.

        Args:
            task_root: Exact resource-owning task root.
            transaction: Validated pending transaction.
            performed_repair_list: Mutable repair report.
        """

        transaction_directory = self._resource_transaction_directory_get(task_root, transaction["path"])
        previous_path = transaction_directory / "previous"
        destination_path = task_root / transaction["path"]
        self._mutation_parent_boundary_validate(
            task_root,
            destination_path,
            "Resource transaction rollback destination",
            allow_missing=not transaction["previous_present"],
        )
        if transaction["previous_present"] and (
            not os.path.lexists(previous_path)
            or self._path_fingerprint_get(previous_path) != transaction["previous_fingerprint"]
        ):
            raise WorktreeError(f"Resource transaction previous object is unavailable or damaged: {previous_path}")
        current_fingerprint = self._path_fingerprint_get(destination_path)
        allowed_fingerprint_set = {
            "absent",
            transaction["destination_fingerprint"],
            transaction["previous_fingerprint"],
        }
        if current_fingerprint not in allowed_fingerprint_set:
            raise WorktreeError(f"Pending resource destination contains independent content: {destination_path}")
        displaced_path = transaction_directory / "displaced"
        if os.path.lexists(displaced_path) and self._path_fingerprint_get(displaced_path) not in {
            transaction["previous_fingerprint"],
            transaction["destination_fingerprint"],
        }:
            raise WorktreeError(f"Resource transaction displaced object is damaged: {displaced_path}")
        exposure_path = transaction_directory / "rollback"
        self._owned_staging_object_remove(
            exposure_path,
            transaction["previous_fingerprint"],
            "Resource rollback staging",
        )
        if transaction["previous_present"]:
            if current_fingerprint == transaction["previous_fingerprint"]:
                self._path_remove(transaction_directory)
                performed_repair_list.append(f"rolled back pending resource transaction: {destination_path}")
                return
            rollback_source_path = (
                displaced_path
                if os.path.lexists(displaced_path)
                and self._path_fingerprint_get(displaced_path) == transaction["previous_fingerprint"]
                else previous_path
            )
            self._path_clone(rollback_source_path, exposure_path)
            if self._path_fingerprint_get(exposure_path) != transaction["previous_fingerprint"]:
                raise WorktreeError(f"Cannot stage exact pre-transaction resource: {destination_path}")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            if exposure_path.stat(follow_symlinks=False).st_dev != destination_path.parent.stat().st_dev:
                self._owned_staging_object_remove(
                    exposure_path,
                    transaction["previous_fingerprint"],
                    "Resource rollback staging",
                )
                raise WorktreeError(f"Resource transaction cannot atomically cross filesystems: {destination_path}")
        if self._path_fingerprint_get(destination_path) != current_fingerprint:
            self._owned_staging_object_remove(
                exposure_path,
                transaction["previous_fingerprint"],
                "Resource rollback staging",
            )
            raise WorktreeError(f"Pending resource destination changed during rollback: {destination_path}")
        rollback_displaced_path = transaction_directory / "rollback-displaced"
        if os.path.lexists(destination_path):
            if os.path.lexists(rollback_displaced_path):
                if self._path_fingerprint_get(rollback_displaced_path) != current_fingerprint:
                    raise WorktreeError(f"Resource rollback displaced object is damaged: {rollback_displaced_path}")
                self._path_remove(destination_path)
            else:
                destination_path.replace(rollback_displaced_path)
        if transaction["previous_present"]:
            exposure_path.replace(destination_path)
            if self._path_fingerprint_get(destination_path) != transaction["previous_fingerprint"]:
                raise WorktreeError(f"Cannot restore exact pre-transaction resource: {destination_path}")
        self._path_remove(transaction_directory)
        performed_repair_list.append(f"rolled back pending resource transaction: {destination_path}")

    def _resource_transaction_create(
        self,
        task_root: Path,
        path_text: str,
        source_path: Path,
        source_fingerprint: str,
        strategy: str,
        performed_repair_list: list[str],
    ) -> ResourceTransaction:
        """Stage, record, and expose one recoverable resource replacement.

        Args:
            task_root: Exact resource-owning task root.
            path_text: Root-relative resource path.
            source_path: Validated main-worktree source.
            source_fingerprint: Stable source fingerprint.
            strategy: New `copy` or `link` strategy.
            performed_repair_list: Mutable repair report.

        Returns:
            Durable pending transaction.
        """

        transaction_directory = self._resource_transaction_directory_get(task_root, path_text)
        if os.path.lexists(transaction_directory):
            raise WorktreeError(f"Resource transaction already exists: {transaction_directory}")
        destination_path = task_root / path_text
        previous_present = os.path.lexists(destination_path)
        previous_fingerprint = self._path_fingerprint_get(destination_path)
        staging_directory, staging_marker_path = self._resource_transaction_staging_directory_create(
            task_root,
            path_text,
            {
                "previous_fingerprint": previous_fingerprint,
                "previous_present": previous_present,
                "source_fingerprint": source_fingerprint,
                "strategy": strategy,
            },
        )
        replacement_path = staging_directory / "replacement"
        transaction_published = False
        try:
            if strategy == "copy":
                self._path_copy(source_path, replacement_path)
            else:
                expected_target = os.path.relpath(source_path, start=destination_path.parent)
                replacement_path.symlink_to(expected_target)
            destination_fingerprint = self._path_fingerprint_get(replacement_path)
            if self._path_fingerprint_get(source_path) != source_fingerprint:
                raise WorktreeError(f"Resource source changed while staging materialization: {source_path}")
            if self._path_fingerprint_get(destination_path) != previous_fingerprint:
                raise WorktreeError(f"Resource destination changed while staging: {destination_path}")
            if previous_present:
                self._path_clone(destination_path, staging_directory / "previous")
                if self._path_fingerprint_get(staging_directory / "previous") != previous_fingerprint:
                    raise WorktreeError(f"Cannot preserve exact pre-transaction resource: {destination_path}")
            transaction: ResourceTransaction = {
                "destination_fingerprint": destination_fingerprint,
                "fingerprint_schema_version": 2,
                "path": path_text,
                "previous_fingerprint": previous_fingerprint,
                "previous_present": previous_present,
                "schema_version": 1,
                "source_fingerprint": source_fingerprint,
                "strategy": strategy,
            }
            self._private_text_atomic_write(
                staging_directory / "metadata.json",
                json.dumps(transaction, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            )
            if os.path.lexists(transaction_directory):
                raise WorktreeError(f"Resource transaction appeared during staging: {transaction_directory}")
            transaction_directory.parent.mkdir(parents=True, exist_ok=True)
            staging_directory.replace(transaction_directory)
            transaction_published = True
            staging_marker_path.unlink()
            directory_descriptor = os.open(
                transaction_directory.parent,
                os.O_RDONLY | os.O_DIRECTORY,
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            self._resource_transaction_expose(task_root, transaction, performed_repair_list)
            return transaction
        except BaseException:
            if not transaction_published and os.path.lexists(staging_directory):
                self._path_remove(staging_directory)
            if not transaction_published and os.path.lexists(staging_marker_path):
                staging_marker_path.unlink()
            raise

    def _resource_removal_transaction_create(
        self,
        task_root: Path,
        path_text: str,
        previous_fingerprint: str,
        performed_repair_list: list[str],
    ) -> ResourceTransaction:
        """Record and atomically expose one recoverable former-resource removal."""

        transaction_directory = self._resource_transaction_directory_get(task_root, path_text)
        if os.path.lexists(transaction_directory):
            raise WorktreeError(f"Resource transaction already exists: {transaction_directory}")
        destination_path = task_root / path_text
        if (
            not os.path.lexists(destination_path)
            or self._path_fingerprint_get(destination_path) != previous_fingerprint
        ):
            raise WorktreeError(f"Former resource changed before removal transaction: {destination_path}")
        staging_directory, staging_marker_path = self._resource_transaction_staging_directory_create(
            task_root,
            path_text,
            {
                "previous_fingerprint": previous_fingerprint,
                "previous_present": True,
                "source_fingerprint": "absent",
                "strategy": "remove",
            },
        )
        transaction_published = False
        try:
            self._path_clone(destination_path, staging_directory / "previous")
            if self._path_fingerprint_get(staging_directory / "previous") != previous_fingerprint:
                raise WorktreeError(f"Cannot preserve exact former resource: {destination_path}")
            transaction: ResourceTransaction = {
                "destination_fingerprint": "absent",
                "fingerprint_schema_version": 2,
                "path": path_text,
                "previous_fingerprint": previous_fingerprint,
                "previous_present": True,
                "schema_version": 1,
                "source_fingerprint": "absent",
                "strategy": "remove",
            }
            self._private_text_atomic_write(
                staging_directory / "metadata.json",
                json.dumps(transaction, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            )
            if os.path.lexists(transaction_directory):
                raise WorktreeError(f"Resource transaction appeared during staging: {transaction_directory}")
            transaction_directory.parent.mkdir(parents=True, exist_ok=True)
            staging_directory.replace(transaction_directory)
            transaction_published = True
            staging_marker_path.unlink()
            directory_descriptor = os.open(
                transaction_directory.parent,
                os.O_RDONLY | os.O_DIRECTORY,
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            self._resource_transaction_expose(task_root, transaction, performed_repair_list)
            return transaction
        except BaseException:
            if not transaction_published and os.path.lexists(staging_directory):
                self._path_remove(staging_directory)
            if not transaction_published and os.path.lexists(staging_marker_path):
                staging_marker_path.unlink()
            raise

    def _resource_transaction_list_get(
        self,
        task_root: Path,
        performed_repair_list: list[str],
        owned_resource_path_set: set[str],
    ) -> list[ResourceTransaction]:
        """Return all durable resource transactions for one owning root.

        Args:
            task_root: Exact resource-owning task root.
            performed_repair_list: Mutable repair report.
            owned_resource_path_set: Exact resource paths recorded for this owner.

        Returns:
            Validated transactions sorted by path.
        """

        transaction_root = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "resource-transaction-v1",
        )
        if not os.path.lexists(transaction_root):
            return []
        if transaction_root.is_symlink() or not transaction_root.is_dir():
            raise WorktreeError(f"Resource transaction owner is not one private directory: {transaction_root}")
        transaction_list: list[ResourceTransaction] = []
        for candidate_path in sorted(transaction_root.iterdir()):
            metadata_path = candidate_path / "metadata.json"
            if candidate_path.is_dir() and not candidate_path.is_symlink() and not os.path.lexists(metadata_path):
                matching_path_list = [
                    path_text
                    for path_text in owned_resource_path_set
                    if candidate_path.name == hashlib.sha256(os.fsencode(path_text)).hexdigest()
                ]
                if len(matching_path_list) != 1 or any(candidate_path.iterdir()):
                    raise WorktreeError(f"Resource transaction metadata is unavailable: {metadata_path}")
                self._path_remove(candidate_path)
                performed_repair_list.append(f"removed unexposed resource transaction staging: {candidate_path}")
                continue
            if candidate_path.is_symlink() or not candidate_path.is_dir():
                raise WorktreeError(f"Resource transaction entry is not one private directory: {candidate_path}")
            if metadata_path.is_symlink() or not metadata_path.is_file():
                raise WorktreeError(f"Resource transaction metadata is unavailable: {metadata_path}")
            try:
                raw_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
                raise WorktreeError(f"Resource transaction metadata is invalid: {metadata_path}") from exc
            if not isinstance(raw_payload, dict) or not isinstance(raw_payload.get("path"), str):
                raise WorktreeError(f"Resource transaction metadata has no path owner: {metadata_path}")
            path_text = self._relative_path_validate(
                Path(raw_payload["path"]),
                "resource transaction path",
            ).as_posix()
            transaction = self._resource_transaction_optional_get(
                task_root,
                path_text,
                performed_repair_list,
            )
            if transaction is None or candidate_path != self._resource_transaction_directory_get(task_root, path_text):
                raise WorktreeError(f"Resource transaction path identity is inconsistent: {candidate_path}")
            transaction_list.append(transaction)
        return sorted(transaction_list, key=lambda item: item["path"])

    def _resource_transaction_list_reconcile_to_state(
        self,
        state: WorktreeState,
        performed_repair_list: list[str],
    ) -> None:
        """Finalize committed transactions and roll back every uncommitted one.

        Args:
            state: Persisted resource ownership state.
            performed_repair_list: Mutable repair report.
        """

        resource_state_by_owner_root_map: dict[str, dict[str, ResourceState]] = {}
        for repository_state in state["repository_state_list"]:
            task_root = Path(repository_state["task_root"])
            resource_state_by_owner_root_map[str(task_root)] = {
                item["path"]: item for item in repository_state["resource_state_list"]
            }
            for submodule_state in repository_state["participating_submodule_state_list"]:
                submodule_root = task_root / submodule_state["path"]
                resource_state_by_owner_root_map[str(submodule_root)] = {
                    item["path"]: item for item in submodule_state["resource_state_list"]
                }
        for owner_root_text, resource_state_by_path_map in resource_state_by_owner_root_map.items():
            owner_root = Path(owner_root_text)
            if not owner_root.is_dir():
                continue
            if not self._repository_is_exact_physical_root(owner_root):
                continue
            self._resource_transaction_owner_reconcile(
                owner_root,
                resource_state_by_path_map,
                performed_repair_list,
            )

    def _resource_transaction_owner_reconcile(
        self,
        owner_root: Path,
        resource_state_by_path_map: dict[str, ResourceState],
        performed_repair_list: list[str],
    ) -> None:
        """Reconcile pending transactions for one initialized resource owner.

        Args:
            owner_root: Exact top-level or participating-submodule task root.
            resource_state_by_path_map: Persisted resources by owner-relative path.
            performed_repair_list: Mutable repair report.
        """

        self._resource_transaction_staging_reconcile(
            owner_root,
            performed_repair_list,
        )
        for transaction in self._resource_transaction_list_get(
            owner_root,
            performed_repair_list,
            set(resource_state_by_path_map),
        ):
            resource_state = resource_state_by_path_map.get(transaction["path"])
            if transaction["strategy"] == "remove" and resource_state is None:
                self._resource_transaction_expose(
                    owner_root,
                    transaction,
                    performed_repair_list,
                )
                self._path_remove(self._resource_transaction_directory_get(owner_root, transaction["path"]))
                performed_repair_list.append(
                    f"finalized committed former-resource removal: {owner_root / transaction['path']}"
                )
                continue
            if (
                resource_state is not None
                and not resource_state["skipped"]
                and resource_state["strategy"] == transaction["strategy"]
                and resource_state["source_fingerprint"] == transaction["source_fingerprint"]
                and resource_state["destination_fingerprint"] == transaction["destination_fingerprint"]
            ):
                self._resource_transaction_expose(
                    owner_root,
                    transaction,
                    performed_repair_list,
                )
                self._path_remove(self._resource_transaction_directory_get(owner_root, transaction["path"]))
                performed_repair_list.append(
                    f"finalized committed resource transaction: {owner_root / transaction['path']}"
                )
                continue
            self._resource_transaction_rollback(
                owner_root,
                transaction,
                performed_repair_list,
            )

    def _resource_transaction_repository_state_reconcile(
        self,
        repository_state: RepositoryState,
        performed_repair_list: list[str],
    ) -> None:
        """Reconcile initialized resource owners inside one repository state.

        Args:
            repository_state: Persisted top-level and submodule resource ownership.
            performed_repair_list: Mutable repair report.
        """

        task_root = Path(repository_state["task_root"])
        self._resource_transaction_owner_reconcile(
            task_root,
            {item["path"]: item for item in repository_state["resource_state_list"]},
            performed_repair_list,
        )
        for submodule_state in repository_state["participating_submodule_state_list"]:
            submodule_root = task_root / submodule_state["path"]
            if not submodule_root.is_dir():
                continue
            if not self._repository_is_exact_physical_root(submodule_root):
                continue
            self._resource_transaction_owner_reconcile(
                submodule_root,
                {item["path"]: item for item in submodule_state["resource_state_list"]},
                performed_repair_list,
            )

    def _result_json_get(
        self,
        state: WorktreeState,
        performed_repair_list: list[str],
        skipped_optional_resource_list: list[str],
    ) -> str:
        """Build one deterministic machine-readable command result.

        Args:
            state: Current workflow state.
            performed_repair_list: Repairs performed during the command.
            skipped_optional_resource_list: Optional resources skipped during the command.

        Returns:
            Compact JSON result.
        """

        return json.dumps(
            {
                "lifecycle_state": state["lifecycle_state"],
                "performed_repair_list": performed_repair_list,
                "participating_submodule_root_list": [
                    str(Path(repository_state["task_root"]) / submodule_state["path"])
                    for repository_state in state["repository_state_list"]
                    for submodule_state in repository_state["participating_submodule_state_list"]
                ],
                "skipped_optional_resource_list": skipped_optional_resource_list,
                "task_prefix": state["prefix"],
                "task_root_list": [item["task_root"] for item in state["repository_state_list"]],
            },
            ensure_ascii=True,
            sort_keys=True,
        )

    def _specification_link_collision_preflight(
        self,
        task_root: Path,
        *,
        allow_incorrect_link_repair: bool,
    ) -> None:
        """Reject an unowned `.spec` collision before any bootstrap write.

        Args:
            task_root: Exact task-worktree root.
            allow_incorrect_link_repair: Whether private state proves provider ownership.
        """

        link_path = task_root / ".spec"
        if self._git_command.run(task_root, ["ls-files", "-z", "--", ".spec"]).stdout:
            raise WorktreeError(f"Specification-link path is tracked by Git: {link_path}")
        expected_target = os.path.relpath(self._specification_path.parent, start=link_path.parent)
        if not os.path.lexists(link_path):
            return
        if link_path.is_symlink():
            if os.readlink(link_path) == expected_target or allow_incorrect_link_repair:
                return
            raise WorktreeError(f"Unrecorded specification link has an unexpected target: {link_path}")
        raise WorktreeError(f"Specification-link path contains independent content: {link_path}")

    def _specification_link_prepare(
        self,
        task_root: Path,
        performed_repair_list: list[str],
        *,
        allow_incorrect_link_repair: bool,
    ) -> None:
        """Create or repair one relative `.spec` link.

        Args:
            task_root: Task-worktree root.
            performed_repair_list: Mutable repair report.
            allow_incorrect_link_repair: Whether private state proves provider ownership.
        """

        link_path = task_root / ".spec"
        if self._git_command.run(task_root, ["ls-files", "-z", "--", ".spec"]).stdout:
            raise WorktreeError(f"Specification-link path is tracked by Git: {link_path}")
        expected_target = os.path.relpath(self._specification_path.parent, start=link_path.parent)
        if link_path.is_symlink() and os.readlink(link_path) == expected_target:
            return
        if os.path.lexists(link_path):
            if link_path.is_symlink():
                if not allow_incorrect_link_repair:
                    raise WorktreeError(f"Unrecorded specification link has an unexpected target: {link_path}")
                link_path.unlink()
            else:
                raise WorktreeError(f"Specification-link path contains independent content: {link_path}")
        link_path.symlink_to(expected_target)
        performed_repair_list.append(f"repaired specification link: {link_path}")

    def _state_get(self, performed_repair_list: list[str]) -> WorktreeState:
        """Load and validate the required private workflow state.

        Args:
            performed_repair_list: Mutable repair report.

        Returns:
            Validated workflow state.
        """

        state = self._state_optional_get(performed_repair_list)
        if state is None:
            raise WorktreeError("Private worktree state is absent; run prepare first")
        return state

    def _registered_worktree_administration_path_get(
        self,
        main_root: Path,
        task_root: Path,
    ) -> Path:
        """Resolve an exact registered administration directory without trusting `.git`."""

        common_git_directory = self._git_common_directory_get(main_root)
        worktree_administration_root = common_git_directory / "worktrees"
        if worktree_administration_root.is_symlink() or not worktree_administration_root.is_dir():
            raise WorktreeError(f"Linked-worktree administration is unavailable: {worktree_administration_root}")
        expected_git_pointer_path = Path(os.path.abspath(task_root / ".git"))
        candidate_list: list[Path] = []
        for candidate_path in sorted(worktree_administration_root.iterdir()):
            gitdir_path = candidate_path / "gitdir"
            commondir_path = candidate_path / "commondir"
            head_path = candidate_path / "HEAD"
            if (
                candidate_path.is_symlink()
                or not candidate_path.is_dir()
                or gitdir_path.is_symlink()
                or not gitdir_path.is_file()
                or commondir_path.is_symlink()
                or not commondir_path.is_file()
                or head_path.is_symlink()
                or not head_path.is_file()
            ):
                continue
            raw_git_pointer_path = Path(
                self._utf8_text_get(
                    gitdir_path,
                    "Linked-worktree gitdir record",
                ).strip()
            )
            if not raw_git_pointer_path.is_absolute():
                raw_git_pointer_path = candidate_path / raw_git_pointer_path
            raw_common_path = Path(
                self._utf8_text_get(
                    commondir_path,
                    "Linked-worktree common-directory record",
                ).strip()
            )
            if not raw_common_path.is_absolute():
                raw_common_path = candidate_path / raw_common_path
            if (
                Path(os.path.abspath(raw_git_pointer_path)) == expected_git_pointer_path
                and raw_common_path.resolve() == common_git_directory
                and self._utf8_text_get(
                    head_path,
                    "Linked-worktree HEAD record",
                ).strip()
                == f"ref: refs/heads/{self._prefix}"
            ):
                candidate_list.append(candidate_path.resolve())
        if len(candidate_list) != 1:
            raise WorktreeError(f"Cannot prove one exact linked-worktree administration owner for {task_root}")
        return candidate_list[0]

    def _coordinating_state_repair_preflight(
        self,
        administration_path: Path,
        worktree_record: dict[str, str],
    ) -> WorktreeState:
        """Load durable ownership directly and prove repair identity before mutation."""

        state_path = administration_path / PRIVATE_STATE_DIRECTORY_NAME / PRIVATE_STATE_FILENAME
        state = self._state_path_load(state_path)
        repository_state = next(
            (
                item
                for item in state["repository_state_list"]
                if item["main_root"] == str(self._coordinating_repository)
            ),
            None,
        )
        if (
            state["coordinating_repository"] != str(self._coordinating_repository)
            or repository_state is None
            or repository_state["task_root"] != str(self._task_root)
            or repository_state["branch_name"] != self._prefix
            or repository_state["common_git_directory"]
            != str(self._git_common_directory_get(self._coordinating_repository))
            or worktree_record["branch_name"] != self._prefix
            or (
                self._git_command.run(
                    self._coordinating_repository,
                    [
                        "merge-base",
                        "--is-ancestor",
                        repository_state["baseline_commit"],
                        worktree_record["head"],
                    ],
                    check=False,
                ).returncode
                != 0
            )
            or self._path_fingerprint_get(self._task_root / MANIFEST_NAME) != repository_state["manifest_fingerprint"]
        ):
            raise WorktreeError(f"Coordinating worktree repair ownership is inconsistent: {self._task_root}")
        expected_specification_target = os.path.relpath(
            self._specification_path.parent,
            start=self._task_root,
        )
        specification_link_path = self._task_root / ".spec"
        if (
            not specification_link_path.is_symlink()
            or os.readlink(specification_link_path) != expected_specification_target
        ):
            raise WorktreeError(f"Coordinating worktree repair has no exact specification link: {self._task_root}")
        return state

    def _state_optional_get(self, performed_repair_list: list[str]) -> WorktreeState | None:
        """Load optional private workflow state.

        Args:
            performed_repair_list: Mutable repair report.

        Returns:
            Validated workflow state when present.
        """

        if not self._task_root.is_dir():
            return None
        try:
            state_path = self._state_path_get(self._task_root)
        except WorktreeError:
            worktree_record = self._worktree_by_path_map_get(self._coordinating_repository).get(
                str(self._task_root.resolve())
            )
            if worktree_record is None or worktree_record["branch_name"] != self._prefix:
                raise
            administration_path = self._registered_worktree_administration_path_get(
                self._coordinating_repository,
                self._task_root,
            )
            self._coordinating_state_repair_preflight(
                administration_path,
                worktree_record,
            )
            git_pointer_path = self._task_root / ".git"
            if git_pointer_path.is_symlink() or not git_pointer_path.is_file():
                raise WorktreeError(f"Coordinating worktree Git pointer is not repairable: {git_pointer_path}")
            self._private_text_atomic_write(
                git_pointer_path,
                f"gitdir: {administration_path}\n",
                staging_owner_root=(administration_path / PRIVATE_STATE_DIRECTORY_NAME / "private-atomic-write-v1"),
            )
            self._git_command.run(
                self._coordinating_repository,
                ["worktree", "repair", str(self._task_root)],
            )
            state_path = self._state_path_get(self._task_root)
            performed_repair_list.append(f"repaired coordinating worktree administration: {self._task_root}")
        if not state_path.is_file():
            legacy_state_path = self._legacy_state_path_get(self._task_root)
            if not legacy_state_path.is_file():
                return None
            state = self._legacy_state_path_load(legacy_state_path, performed_repair_list)
            newer_state = self._state_secondary_replica_optional_get(
                [Path(item["main_root"]) for item in state["repository_state_list"]],
                performed_repair_list,
                allow_legacy=False,
            )
            if newer_state is not None:
                self._state_write(newer_state, performed_repair_list)
                return newer_state
            performed_repair_list.append(f"migrated private state schema: {legacy_state_path}")
            self._state_write(state, performed_repair_list)
            return state
        state = self._state_path_load(state_path, performed_repair_list)
        self._legacy_state_replica_list_retire(state, performed_repair_list)
        return state

    def _state_path_load(
        self,
        state_path: Path,
        performed_repair_list: list[str] | None = None,
    ) -> WorktreeState:
        """Load and validate one exact private-state replica.

        Args:
            state_path: Exact private-state replica path.

        Returns:
            Validated workflow state.
        """

        if state_path.is_symlink() or not state_path.is_file():
            raise WorktreeError(f"Private worktree state must be one ordinary file: {state_path}")
        try:
            raw_payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise WorktreeError(f"Cannot load private worktree state {state_path}: {exc}") from exc
        if not isinstance(raw_payload, dict):
            raise WorktreeError(f"Private worktree state must be a JSON object: {state_path}")
        expected_key_set = {
            "coordinating_repository",
            "fingerprint_schema_version",
            "goal_fingerprint",
            "lifecycle_state",
            "prefix",
            "repository_state_list",
            "schema_version",
            "specification_fingerprint",
            "specification_path",
        }
        legacy_fingerprint_key_set = expected_key_set - {"fingerprint_schema_version"}
        if isinstance(raw_payload, dict) and set(raw_payload) == legacy_fingerprint_key_set:
            raw_payload["fingerprint_schema_version"] = 1
        if (
            set(raw_payload) != expected_key_set
            or type(raw_payload.get("schema_version")) is not int
            or raw_payload.get("schema_version") != STATE_SCHEMA_VERSION
            or type(raw_payload.get("fingerprint_schema_version")) is not int
            or raw_payload.get("fingerprint_schema_version") not in {1, 2}
        ):
            raise WorktreeError(f"Private worktree state has an unsupported schema: {state_path}")
        if raw_payload.get("prefix") != self._prefix:
            raise WorktreeError(f"Private worktree state belongs to another task: {state_path}")
        if not isinstance(raw_payload.get("repository_state_list"), list):
            raise WorktreeError(f"Private worktree repository state must be a list: {state_path}")
        self._participating_submodule_main_state_shape_upgrade(
            raw_payload,
            state_path,
            performed_repair_list,
        )
        self._accepted_main_commit_drift_shape_upgrade(
            raw_payload,
            state_path,
            performed_repair_list,
        )
        self._state_shape_validate(raw_payload, state_path)
        state = cast(WorktreeState, raw_payload)
        if state["fingerprint_schema_version"] == 1:
            self._state_fingerprint_list_upgrade(state, state_path, performed_repair_list)
            state["fingerprint_schema_version"] = 2
            if performed_repair_list is not None:
                performed_repair_list.append(f"upgraded collision-safe filesystem fingerprints: {state_path}")
        return state

    def _accepted_main_commit_drift_shape_upgrade(
        self,
        raw_payload: dict[str, object],
        state_path: Path,
        performed_repair_list: list[str] | None,
    ) -> None:
        """Add empty caller-attested commit-drift state to earlier schema-v2 replicas."""

        repository_state_list = raw_payload.get("repository_state_list")
        if not isinstance(repository_state_list, list):
            return
        upgraded = False
        for raw_repository_state in repository_state_list:
            if not isinstance(raw_repository_state, dict):
                continue
            if "accepted_main_commit_drift_list" not in raw_repository_state:
                raw_repository_state["accepted_main_commit_drift_list"] = []
                upgraded = True
            raw_submodule_state_list = raw_repository_state.get("participating_submodule_state_list")
            if not isinstance(raw_submodule_state_list, list):
                continue
            for raw_submodule_state in raw_submodule_state_list:
                if (
                    isinstance(raw_submodule_state, dict)
                    and "accepted_main_commit_drift_list" not in raw_submodule_state
                ):
                    raw_submodule_state["accepted_main_commit_drift_list"] = []
                    upgraded = True
        if upgraded and performed_repair_list is not None:
            performed_repair_list.append(f"upgraded main commit-drift attestation state: {state_path}")

    def _participating_submodule_main_state_shape_upgrade(
        self,
        raw_payload: dict[str, object],
        state_path: Path,
        performed_repair_list: list[str] | None,
    ) -> None:
        """Add nested main-isolation state only when every old owner is clean."""

        repository_state_list = raw_payload.get("repository_state_list")
        if not isinstance(repository_state_list, list):
            return
        legacy_key_set = {
            "baseline_commit",
            "manifest_fingerprint",
            "path",
            "resource_state_list",
        }
        upgraded = False
        for raw_repository_state in repository_state_list:
            if not isinstance(raw_repository_state, dict):
                continue
            main_root_text = raw_repository_state.get("main_root")
            task_root_text = raw_repository_state.get("task_root")
            raw_submodule_state_list = raw_repository_state.get("participating_submodule_state_list")
            if (
                not isinstance(main_root_text, str)
                or not isinstance(task_root_text, str)
                or not isinstance(raw_submodule_state_list, list)
            ):
                continue
            raw_top_level_status_by_path_map = raw_repository_state.get("main_status_by_path_map")
            if not isinstance(raw_top_level_status_by_path_map, dict):
                continue
            for raw_submodule_state in raw_submodule_state_list:
                if not isinstance(raw_submodule_state, dict) or set(raw_submodule_state) != legacy_key_set:
                    continue
                path_text = raw_submodule_state.get("path")
                if not isinstance(path_text, str):
                    continue
                main_submodule_root = Path(main_root_text) / path_text
                task_submodule_root = Path(task_root_text) / path_text
                if not self._repository_is_exact_physical_root(
                    main_submodule_root
                ) or not self._repository_is_exact_physical_root(task_submodule_root):
                    raise WorktreeError(
                        f"Cannot migrate task-owned submodule main isolation from ambiguous state: "
                        f"{main_submodule_root}"
                    )
                recorded_boundary_overlap_set = self._path_boundary_overlap_set_get(
                    set(raw_top_level_status_by_path_map),
                    {path_text},
                )
                if recorded_boundary_overlap_set:
                    raise WorktreeError(
                        f"Cannot migrate task-owned submodule main isolation with a recorded dirty boundary: "
                        f"{main_submodule_root}"
                    )
                main_commit = self._git_command.run(
                    main_submodule_root,
                    ["rev-parse", "HEAD"],
                ).stdout.strip()
                baseline_commit = raw_submodule_state.get("baseline_commit")
                if (
                    not isinstance(baseline_commit, str)
                    or self._git_command.run(
                        main_submodule_root,
                        ["merge-base", "--is-ancestor", baseline_commit, main_commit],
                        check=False,
                    ).returncode
                    != 0
                ):
                    raise WorktreeError(f"Cannot migrate task-owned submodule main history: {main_submodule_root}")
                raw_submodule_state["main_commit"] = main_commit
                raw_submodule_state["main_leak_fingerprint_by_path_map"] = {}
                raw_submodule_state["main_preimage_by_path_map"] = {}
                raw_submodule_state["main_status_by_path_map"] = {}
                raw_submodule_state["main_status_fingerprint_by_path_map"] = {}
                upgraded = True
        if upgraded and performed_repair_list is not None:
            performed_repair_list.append(f"upgraded task-owned submodule main isolation state: {state_path}")

    def _legacy_state_path_load(
        self,
        state_path: Path,
        performed_repair_list: list[str] | None = None,
        *,
        upgrade_fingerprints: bool = True,
    ) -> WorktreeState:
        """Load and deterministically migrate one schema-v1 private-state replica.

        Args:
            state_path: Exact legacy private-state replica path.

        Returns:
            Validated schema-v2 workflow state.
        """

        if state_path.is_symlink() or not state_path.is_file():
            raise WorktreeError(f"Private worktree state must be one ordinary file: {state_path}")
        try:
            raw_payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise WorktreeError(f"Cannot load private worktree state {state_path}: {exc}") from exc
        expected_key_set = {
            "coordinating_repository",
            "goal_fingerprint",
            "lifecycle_state",
            "prefix",
            "repository_state_list",
            "schema_version",
            "specification_fingerprint",
            "specification_path",
        }
        legacy_repository_key_set = {
            "baseline_commit",
            "branch_name",
            "common_git_directory",
            "main_commit",
            "main_preimage_by_path_map",
            "main_root",
            "main_status_by_path_map",
            "main_status_fingerprint_by_path_map",
            "manifest_fingerprint",
            "resource_state_list",
            "submodule_commit_by_path_map",
            "task_root",
            "temporary_exclude_list",
        }
        if (
            not isinstance(raw_payload, dict)
            or set(raw_payload) != expected_key_set
            or type(raw_payload.get("schema_version")) is not int
            or raw_payload.get("schema_version") != 1
            or raw_payload.get("prefix") != self._prefix
            or not isinstance(raw_payload.get("repository_state_list"), list)
            or any(
                not isinstance(repository_state, dict) or set(repository_state) != legacy_repository_key_set
                for repository_state in raw_payload["repository_state_list"]
            )
        ):
            raise WorktreeError(f"Private worktree state has an unsupported legacy schema: {state_path}")
        for repository_state in raw_payload["repository_state_list"]:
            repository_state["accepted_main_commit_drift_list"] = []
            repository_state["main_leak_fingerprint_by_path_map"] = {}
            repository_state["participating_submodule_state_list"] = []
        raw_payload["schema_version"] = STATE_SCHEMA_VERSION
        raw_payload["fingerprint_schema_version"] = 1
        self._state_shape_validate(raw_payload, state_path)
        state = cast(WorktreeState, raw_payload)
        if upgrade_fingerprints:
            self._state_fingerprint_list_upgrade(state, state_path, performed_repair_list)
            state["fingerprint_schema_version"] = 2
            if performed_repair_list is not None:
                performed_repair_list.append(f"upgraded collision-safe filesystem fingerprints: {state_path}")
        return state

    def _state_secondary_replica_optional_get(
        self,
        repository_root_list: list[Path],
        performed_repair_list: list[str],
        *,
        allow_legacy: bool = True,
    ) -> WorktreeState | None:
        """Recover missing coordinating state from one agreeing secondary replica.

        Args:
            repository_root_list: Explicit participating main-worktree roots.
            performed_repair_list: Mutable repair report.
            allow_legacy: Whether schema-v1 replicas may be migration candidates.

        Returns:
            One validated agreeing state or `None` when no secondary replica exists.
        """

        valid_v2_replica_list: list[tuple[Path, WorktreeState]] = []
        valid_legacy_replica_list: list[tuple[Path, WorktreeState]] = []
        observed_v2_path_list: list[Path] = []
        invalid_replica_path_list: list[Path] = []
        for repository_root in repository_root_list:
            task_root = repository_root / WORKTREE_CONTAINER_NAME / self._prefix
            if task_root == self._task_root or not task_root.is_dir():
                continue
            top_level_result = self._git_command.run(
                task_root,
                ["rev-parse", "--show-toplevel"],
                check=False,
            )
            if (
                top_level_result.returncode != 0
                or not top_level_result.stdout.strip()
                or Path(top_level_result.stdout.strip()).resolve() != task_root
            ):
                continue
            state_path = self._state_path_get(task_root)
            legacy_state_path: Path | None = None
            if os.path.lexists(state_path):
                observed_v2_path_list.append(state_path)
            if not state_path.is_file():
                if os.path.lexists(state_path):
                    invalid_replica_path_list.append(state_path)
                    continue
                if not allow_legacy:
                    continue
                candidate_legacy_state_path = self._legacy_state_path_get(task_root)
                if not candidate_legacy_state_path.is_file():
                    if os.path.lexists(candidate_legacy_state_path):
                        invalid_replica_path_list.append(candidate_legacy_state_path)
                    continue
                legacy_state_path = candidate_legacy_state_path
            try:
                if legacy_state_path is None:
                    valid_v2_replica_list.append(
                        (
                            state_path,
                            self._state_path_load(state_path, performed_repair_list),
                        )
                    )
                else:
                    valid_legacy_replica_list.append(
                        (
                            legacy_state_path,
                            self._legacy_state_path_load(
                                legacy_state_path,
                                performed_repair_list,
                            ),
                        )
                    )
            except WorktreeError:
                invalid_replica_path_list.append(legacy_state_path or state_path)
        if observed_v2_path_list:
            valid_replica_list = valid_v2_replica_list
            invalid_replica_path_list = [path for path in invalid_replica_path_list if path in observed_v2_path_list]
        else:
            valid_replica_list = valid_legacy_replica_list
        if not valid_replica_list:
            if invalid_replica_path_list:
                raise WorktreeError(
                    "No valid private state replica remains: "
                    + ", ".join(str(path) for path in sorted(invalid_replica_path_list))
                )
            return None
        canonical_content = json.dumps(valid_replica_list[0][1], ensure_ascii=True, sort_keys=True)
        if any(
            json.dumps(state, ensure_ascii=True, sort_keys=True) != canonical_content
            for _, state in valid_replica_list[1:]
        ):
            raise WorktreeError(
                "Secondary private state replicas disagree: " + ", ".join(str(path) for path, _ in valid_replica_list)
            )
        recovered_state_path, recovered_state = valid_replica_list[0]
        performed_repair_list.append(f"recovered private state from secondary replica: {recovered_state_path}")
        return recovered_state

    def _accepted_main_commit_drift_list_shape_validate(
        self,
        raw_attestation_list: object,
        state_path: Path,
        label: str,
    ) -> None:
        """Validate one closed caller-attested committed-main drift list."""

        if not isinstance(raw_attestation_list, list):
            raise WorktreeError(f"{label} main commit-drift attestations are invalid: {state_path}")
        observed_accepted_commit_set: set[str] = set()
        for raw_attestation in raw_attestation_list:
            if (
                not isinstance(raw_attestation, dict)
                or set(raw_attestation) != {"commit", "path_list"}
                or not _hex_digest_is_valid(raw_attestation.get("commit"), {40, 64})
                or not isinstance(raw_attestation.get("path_list"), list)
                or not raw_attestation["path_list"]
                or any(not isinstance(path_text, str) for path_text in raw_attestation["path_list"])
                or raw_attestation["path_list"] != sorted(set(raw_attestation["path_list"]))
                or raw_attestation["commit"] in observed_accepted_commit_set
            ):
                raise WorktreeError(f"{label} main commit-drift attestation is invalid: {state_path}")
            attested_path_set = set(cast(list[str], raw_attestation["path_list"]))
            for path_text in attested_path_set:
                self._git_path_text_validate(path_text, f"{label} accepted main commit-drift path")
            self._non_overlapping_path_set_validate(
                attested_path_set,
                f"{label} accepted main commit-drift paths",
            )
            observed_accepted_commit_set.add(cast(str, raw_attestation["commit"]))

    def _state_shape_validate(self, raw_payload: dict[object, object], state_path: Path) -> None:
        """Validate the complete closed private-state shape.

        Args:
            raw_payload: Parsed JSON object.
            state_path: State path used for diagnostics.
        """

        string_key_list = [
            "coordinating_repository",
            "goal_fingerprint",
            "lifecycle_state",
            "prefix",
            "specification_fingerprint",
            "specification_path",
        ]
        if any(not isinstance(raw_payload.get(key), str) for key in string_key_list):
            raise WorktreeError(f"Private worktree state has invalid scalar fields: {state_path}")
        if type(raw_payload.get("fingerprint_schema_version")) is not int or raw_payload[
            "fingerprint_schema_version"
        ] not in {1, 2}:
            raise WorktreeError(f"Private worktree state has an invalid fingerprint schema: {state_path}")
        if raw_payload["lifecycle_state"] not in LIFECYCLE_INDEX_BY_NAME_MAP:
            raise WorktreeError(f"Private worktree state has an invalid lifecycle: {state_path}")
        repository_state_list = raw_payload["repository_state_list"]
        if not isinstance(repository_state_list, list) or not repository_state_list:
            raise WorktreeError(f"Private worktree repository state must be nonempty: {state_path}")
        repository_key_set = {
            "accepted_main_commit_drift_list",
            "baseline_commit",
            "branch_name",
            "common_git_directory",
            "main_commit",
            "main_leak_fingerprint_by_path_map",
            "main_preimage_by_path_map",
            "main_root",
            "main_status_by_path_map",
            "main_status_fingerprint_by_path_map",
            "manifest_fingerprint",
            "participating_submodule_state_list",
            "resource_state_list",
            "submodule_commit_by_path_map",
            "task_root",
            "temporary_exclude_list",
        }
        resource_key_set = {
            "destination_fingerprint",
            "path",
            "required",
            "skipped",
            "source_fingerprint",
            "strategy",
        }
        preimage_key_set = {
            "index_entry_list",
            "snapshot_name",
            "working_fingerprint",
            "working_present",
        }
        participating_submodule_key_set = {
            "accepted_main_commit_drift_list",
            "baseline_commit",
            "main_commit",
            "main_leak_fingerprint_by_path_map",
            "main_preimage_by_path_map",
            "main_status_by_path_map",
            "main_status_fingerprint_by_path_map",
            "manifest_fingerprint",
            "path",
            "resource_state_list",
        }
        observed_main_root_set: set[str] = set()
        observed_task_root_set: set[str] = set()
        for repository_index, raw_repository_state in enumerate(repository_state_list):
            if not isinstance(raw_repository_state, dict) or set(raw_repository_state) != repository_key_set:
                raise WorktreeError(f"Private repository state has an unsupported schema: {state_path}")
            repository_string_key_list = [
                "baseline_commit",
                "branch_name",
                "common_git_directory",
                "main_commit",
                "main_root",
                "manifest_fingerprint",
                "task_root",
            ]
            if any(not isinstance(raw_repository_state.get(key), str) for key in repository_string_key_list):
                raise WorktreeError(f"Private repository state has invalid scalar fields: {state_path}")
            main_root_text = cast(str, raw_repository_state["main_root"])
            task_root_text = cast(str, raw_repository_state["task_root"])
            common_git_directory_text = cast(str, raw_repository_state["common_git_directory"])
            if (
                not Path(main_root_text).is_absolute()
                or not Path(task_root_text).is_absolute()
                or not Path(common_git_directory_text).is_absolute()
                or str(Path(main_root_text).resolve()) != main_root_text
                or str(Path(task_root_text).resolve()) != task_root_text
                or str(Path(common_git_directory_text).resolve()) != common_git_directory_text
                or Path(task_root_text) != Path(main_root_text) / WORKTREE_CONTAINER_NAME / self._prefix
                or raw_repository_state["branch_name"] != self._prefix
                or not raw_repository_state["baseline_commit"]
                or not raw_repository_state["main_commit"]
                or main_root_text in observed_main_root_set
                or task_root_text in observed_task_root_set
            ):
                raise WorktreeError(f"Private repository state has invalid task identity: {state_path}")
            if not _hex_digest_is_valid(raw_repository_state["baseline_commit"], {40, 64}) or not _hex_digest_is_valid(
                raw_repository_state["main_commit"], {40, 64}
            ):
                raise WorktreeError(f"Private repository state has invalid commit identity: {state_path}")
            self._accepted_main_commit_drift_list_shape_validate(
                raw_repository_state.get("accepted_main_commit_drift_list"),
                state_path,
                "Private repository",
            )
            if not _hex_digest_is_valid(raw_repository_state["manifest_fingerprint"], {64}):
                raise WorktreeError(f"Private repository state has invalid manifest fingerprint: {state_path}")
            if repository_index == 0 and main_root_text != str(self._coordinating_repository):
                raise WorktreeError(f"Private state does not begin with its coordinating repository: {state_path}")
            observed_main_root_set.add(main_root_text)
            observed_task_root_set.add(task_root_text)
            for map_key in (
                "main_status_by_path_map",
                "main_status_fingerprint_by_path_map",
                "main_leak_fingerprint_by_path_map",
                "submodule_commit_by_path_map",
            ):
                candidate_map = raw_repository_state.get(map_key)
                if not isinstance(candidate_map, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str) for key, value in candidate_map.items()
                ):
                    raise WorktreeError(f"Private repository state has an invalid {map_key}: {state_path}")
                for path_text in candidate_map:
                    self._git_path_text_validate(path_text, f"Private repository {map_key} key")
            if any(
                not _hex_digest_is_valid(value, {64})
                for value in cast(dict[str, str], raw_repository_state["main_status_fingerprint_by_path_map"]).values()
            ) or any(
                not _hex_digest_is_valid(value, {40, 64})
                for value in cast(dict[str, str], raw_repository_state["submodule_commit_by_path_map"]).values()
            ):
                raise WorktreeError(f"Private repository state has invalid recorded digests: {state_path}")
            if any(
                value != "absent" and not _hex_digest_is_valid(value, {64})
                for value in cast(dict[str, str], raw_repository_state["main_leak_fingerprint_by_path_map"]).values()
            ):
                raise WorktreeError(f"Private repository main-leak provenance is invalid: {state_path}")
            raw_status_by_path_map = cast(dict[str, str], raw_repository_state["main_status_by_path_map"])
            raw_status_fingerprint_by_path_map = cast(
                dict[str, str],
                raw_repository_state["main_status_fingerprint_by_path_map"],
            )
            if set(raw_status_by_path_map) != set(raw_status_fingerprint_by_path_map) or any(
                len(status_text) != 2 for status_text in raw_status_by_path_map.values()
            ):
                raise WorktreeError(f"Private repository main status maps are inconsistent: {state_path}")
            raw_preimage_by_path_map = raw_repository_state.get("main_preimage_by_path_map")
            if not isinstance(raw_preimage_by_path_map, dict):
                raise WorktreeError(f"Private repository preimage state is invalid: {state_path}")
            if set(raw_preimage_by_path_map) != set(raw_status_by_path_map):
                raise WorktreeError(f"Private repository preimages do not match dirty paths: {state_path}")
            for path_text, raw_preimage in raw_preimage_by_path_map.items():
                if (
                    not isinstance(path_text, str)
                    or not isinstance(raw_preimage, dict)
                    or set(raw_preimage) != preimage_key_set
                    or not isinstance(raw_preimage.get("index_entry_list"), list)
                    or any(not isinstance(item, str) for item in raw_preimage["index_entry_list"])
                    or not isinstance(raw_preimage.get("snapshot_name"), str)
                    or not isinstance(raw_preimage.get("working_fingerprint"), str)
                    or not isinstance(raw_preimage.get("working_present"), bool)
                ):
                    raise WorktreeError(f"Private repository preimage entry is invalid: {state_path}")
                self._git_path_text_validate(path_text, "Private repository preimage path")
                snapshot_name = cast(str, raw_preimage["snapshot_name"])
                if (
                    not _hex_digest_is_valid(snapshot_name, {64})
                    or (
                        raw_preimage["working_present"]
                        and not _hex_digest_is_valid(raw_preimage["working_fingerprint"], {64})
                    )
                    or (not raw_preimage["working_present"] and raw_preimage["working_fingerprint"] != "absent")
                ):
                    raise WorktreeError(f"Private repository preimage snapshot name is invalid: {state_path}")
                for index_entry in raw_preimage["index_entry_list"]:
                    index_part_list = index_entry.split()
                    if (
                        len(index_part_list) != 3
                        or not index_part_list[0].isdigit()
                        or not _hex_digest_is_valid(index_part_list[1], {40, 64})
                        or index_part_list[2] not in {"0", "1", "2", "3"}
                    ):
                        raise WorktreeError(f"Private repository index preimage is invalid: {state_path}")
            raw_resource_state_list = raw_repository_state.get("resource_state_list")
            if not isinstance(raw_resource_state_list, list):
                raise WorktreeError(f"Private repository resource state is invalid: {state_path}")
            observed_resource_path_set: set[str] = set()
            for raw_resource_state in raw_resource_state_list:
                if (
                    not isinstance(raw_resource_state, dict)
                    or set(raw_resource_state) != resource_key_set
                    or any(
                        not isinstance(raw_resource_state.get(key), str)
                        for key in (
                            "destination_fingerprint",
                            "path",
                            "source_fingerprint",
                            "strategy",
                        )
                    )
                    or not isinstance(raw_resource_state.get("required"), bool)
                    or not isinstance(raw_resource_state.get("skipped"), bool)
                    or raw_resource_state.get("strategy") not in {"copy", "link"}
                ):
                    raise WorktreeError(f"Private repository resource entry is invalid: {state_path}")
                resource_path_text = cast(str, raw_resource_state["path"])
                if resource_path_text in observed_resource_path_set:
                    raise WorktreeError(f"Private repository resource paths are duplicated: {state_path}")
                self._manifest_resource_path_validate(resource_path_text)
                observed_resource_path_set.add(resource_path_text)
                if raw_resource_state["source_fingerprint"] != "absent" and not _hex_digest_is_valid(
                    raw_resource_state["source_fingerprint"], {64}
                ):
                    raise WorktreeError(f"Private repository resource source fingerprint is invalid: {state_path}")
                if raw_resource_state["destination_fingerprint"] != "absent" and not _hex_digest_is_valid(
                    raw_resource_state["destination_fingerprint"], {64}
                ):
                    raise WorktreeError(f"Private repository resource destination fingerprint is invalid: {state_path}")
                if raw_resource_state["skipped"] and (
                    raw_resource_state["required"]
                    or raw_resource_state["source_fingerprint"] != "absent"
                    or raw_resource_state["destination_fingerprint"] != "absent"
                ):
                    raise WorktreeError(f"Private skipped resource state is inconsistent: {state_path}")
            raw_participating_submodule_state_list = raw_repository_state.get("participating_submodule_state_list")
            if not isinstance(raw_participating_submodule_state_list, list):
                raise WorktreeError(f"Private task-owned submodule state is invalid: {state_path}")
            observed_participating_submodule_path_set: set[str] = set()
            recorded_submodule_commit_by_path_map = cast(
                dict[str, str],
                raw_repository_state["submodule_commit_by_path_map"],
            )
            for raw_participating_submodule_state in raw_participating_submodule_state_list:
                if (
                    not isinstance(raw_participating_submodule_state, dict)
                    or set(raw_participating_submodule_state) != participating_submodule_key_set
                    or not isinstance(raw_participating_submodule_state.get("path"), str)
                    or not _hex_digest_is_valid(
                        raw_participating_submodule_state.get("baseline_commit"),
                        {40, 64},
                    )
                    or not _hex_digest_is_valid(
                        raw_participating_submodule_state.get("main_commit"),
                        {40, 64},
                    )
                    or not _hex_digest_is_valid(
                        raw_participating_submodule_state.get("manifest_fingerprint"),
                        {64},
                    )
                ):
                    raise WorktreeError(f"Private task-owned submodule entry is invalid: {state_path}")
                self._accepted_main_commit_drift_list_shape_validate(
                    raw_participating_submodule_state.get("accepted_main_commit_drift_list"),
                    state_path,
                    "Private task-owned submodule",
                )
                participating_path_text = cast(str, raw_participating_submodule_state["path"])
                self._git_path_text_validate(
                    participating_path_text,
                    "Private task-owned submodule path",
                )
                if (
                    participating_path_text in observed_participating_submodule_path_set
                    or participating_path_text not in recorded_submodule_commit_by_path_map
                    or raw_participating_submodule_state["baseline_commit"]
                    != recorded_submodule_commit_by_path_map[participating_path_text]
                ):
                    raise WorktreeError(f"Private task-owned submodule identity is inconsistent: {state_path}")
                observed_participating_submodule_path_set.add(participating_path_text)
                raw_submodule_status_by_path_map = raw_participating_submodule_state.get("main_status_by_path_map")
                raw_submodule_status_fingerprint_by_path_map = raw_participating_submodule_state.get(
                    "main_status_fingerprint_by_path_map"
                )
                raw_submodule_leak_fingerprint_by_path_map = raw_participating_submodule_state.get(
                    "main_leak_fingerprint_by_path_map"
                )
                raw_submodule_preimage_by_path_map = raw_participating_submodule_state.get("main_preimage_by_path_map")
                if any(
                    not isinstance(candidate_map, dict)
                    or any(
                        not isinstance(key, str) or not isinstance(value, str) for key, value in candidate_map.items()
                    )
                    for candidate_map in (
                        raw_submodule_status_by_path_map,
                        raw_submodule_status_fingerprint_by_path_map,
                        raw_submodule_leak_fingerprint_by_path_map,
                    )
                ) or not isinstance(raw_submodule_preimage_by_path_map, dict):
                    raise WorktreeError(f"Private task-owned submodule main state is invalid: {state_path}")
                raw_submodule_status_by_path_map = cast(
                    dict[str, str],
                    raw_submodule_status_by_path_map,
                )
                raw_submodule_status_fingerprint_by_path_map = cast(
                    dict[str, str],
                    raw_submodule_status_fingerprint_by_path_map,
                )
                raw_submodule_leak_fingerprint_by_path_map = cast(
                    dict[str, str],
                    raw_submodule_leak_fingerprint_by_path_map,
                )
                raw_submodule_preimage_by_path_map = cast(
                    dict[str, object],
                    raw_submodule_preimage_by_path_map,
                )
                if (
                    set(raw_submodule_status_by_path_map) != set(raw_submodule_status_fingerprint_by_path_map)
                    or set(raw_submodule_status_by_path_map) != set(raw_submodule_preimage_by_path_map)
                    or any(len(status_text) != 2 for status_text in raw_submodule_status_by_path_map.values())
                    or any(
                        not _hex_digest_is_valid(value, {64})
                        for value in raw_submodule_status_fingerprint_by_path_map.values()
                    )
                    or any(
                        value != "absent" and not _hex_digest_is_valid(value, {64})
                        for value in raw_submodule_leak_fingerprint_by_path_map.values()
                    )
                ):
                    raise WorktreeError(f"Private task-owned submodule main state is inconsistent: {state_path}")
                for main_path_text in (
                    set(raw_submodule_status_by_path_map)
                    | set(raw_submodule_leak_fingerprint_by_path_map)
                    | set(raw_submodule_preimage_by_path_map)
                ):
                    self._git_path_text_validate(
                        main_path_text,
                        "Private task-owned submodule main path",
                    )
                for raw_submodule_preimage in raw_submodule_preimage_by_path_map.values():
                    if (
                        not isinstance(raw_submodule_preimage, dict)
                        or set(raw_submodule_preimage) != preimage_key_set
                        or not isinstance(raw_submodule_preimage.get("index_entry_list"), list)
                        or any(not isinstance(item, str) for item in raw_submodule_preimage["index_entry_list"])
                        or not _hex_digest_is_valid(raw_submodule_preimage.get("snapshot_name"), {64})
                        or not isinstance(raw_submodule_preimage.get("working_present"), bool)
                        or (
                            raw_submodule_preimage["working_present"]
                            and not _hex_digest_is_valid(
                                raw_submodule_preimage.get("working_fingerprint"),
                                {64},
                            )
                        )
                        or (
                            not raw_submodule_preimage["working_present"]
                            and raw_submodule_preimage.get("working_fingerprint") != "absent"
                        )
                    ):
                        raise WorktreeError(f"Private task-owned submodule main preimage is invalid: {state_path}")
                    for index_entry in raw_submodule_preimage["index_entry_list"]:
                        index_part_list = index_entry.split()
                        if (
                            len(index_part_list) != 3
                            or not index_part_list[0].isdigit()
                            or not _hex_digest_is_valid(index_part_list[1], {40, 64})
                            or index_part_list[2] not in {"0", "1", "2", "3"}
                        ):
                            raise WorktreeError(f"Private task-owned submodule index preimage is invalid: {state_path}")
                raw_submodule_resource_state_list = raw_participating_submodule_state.get("resource_state_list")
                if not isinstance(raw_submodule_resource_state_list, list):
                    raise WorktreeError(f"Private task-owned submodule resource state is invalid: {state_path}")
                observed_submodule_resource_path_set: set[str] = set()
                for raw_submodule_resource_state in raw_submodule_resource_state_list:
                    if (
                        not isinstance(raw_submodule_resource_state, dict)
                        or set(raw_submodule_resource_state) != resource_key_set
                        or any(
                            not isinstance(raw_submodule_resource_state.get(key), str)
                            for key in (
                                "destination_fingerprint",
                                "path",
                                "source_fingerprint",
                                "strategy",
                            )
                        )
                        or not isinstance(raw_submodule_resource_state.get("required"), bool)
                        or not isinstance(raw_submodule_resource_state.get("skipped"), bool)
                        or raw_submodule_resource_state.get("strategy") not in {"copy", "link"}
                    ):
                        raise WorktreeError(f"Private task-owned submodule resource entry is invalid: {state_path}")
                    resource_path_text = cast(str, raw_submodule_resource_state["path"])
                    if resource_path_text in observed_submodule_resource_path_set:
                        raise WorktreeError(f"Private task-owned submodule resource paths are duplicated: {state_path}")
                    self._manifest_resource_path_validate(resource_path_text)
                    observed_submodule_resource_path_set.add(resource_path_text)
                    if raw_submodule_resource_state["source_fingerprint"] != "absent" and not _hex_digest_is_valid(
                        raw_submodule_resource_state["source_fingerprint"],
                        {64},
                    ):
                        raise WorktreeError(
                            f"Private task-owned submodule resource source fingerprint is invalid: {state_path}"
                        )
                    if raw_submodule_resource_state["destination_fingerprint"] != "absent" and not _hex_digest_is_valid(
                        raw_submodule_resource_state["destination_fingerprint"],
                        {64},
                    ):
                        raise WorktreeError(
                            f"Private task-owned submodule resource destination fingerprint is invalid: {state_path}"
                        )
                    if raw_submodule_resource_state["skipped"] and (
                        raw_submodule_resource_state["required"]
                        or raw_submodule_resource_state["source_fingerprint"] != "absent"
                        or raw_submodule_resource_state["destination_fingerprint"] != "absent"
                    ):
                        raise WorktreeError(
                            f"Private skipped task-owned submodule resource state is inconsistent: {state_path}"
                        )
            temporary_exclude_list = raw_repository_state.get("temporary_exclude_list")
            if not isinstance(temporary_exclude_list, list) or any(
                item != IGNORE_WORKTREE_PATTERN for item in temporary_exclude_list
            ):
                raise WorktreeError(f"Private repository exclude state is invalid: {state_path}")
        if not _hex_digest_is_valid(raw_payload["specification_fingerprint"], {64}):
            raise WorktreeError(f"Private specification fingerprint is invalid: {state_path}")
        if raw_payload["lifecycle_state"] in SEALED_LIFECYCLE_STATE_SET:
            if not _hex_digest_is_valid(raw_payload["goal_fingerprint"], {64}):
                raise WorktreeError(f"Private goal fingerprint is invalid: {state_path}")
        elif raw_payload["goal_fingerprint"] and not _hex_digest_is_valid(raw_payload["goal_fingerprint"], {64}):
            raise WorktreeError(f"Private goal fingerprint is invalid: {state_path}")

    def _state_path_get(self, task_root: Path) -> Path:
        """Resolve one worktree's private state path.

        Args:
            task_root: Exact task-worktree root.

        Returns:
            Private state JSON path.
        """

        return self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / PRIVATE_STATE_FILENAME,
        )

    def _legacy_state_path_get(self, task_root: Path) -> Path:
        """Resolve one legacy schema-v1 private-state path.

        Args:
            task_root: Exact task-worktree root.

        Returns:
            Legacy private-state JSON path.
        """

        return self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / LEGACY_PRIVATE_STATE_FILENAME,
        )

    def _state_validate_observable(
        self,
        state: WorktreeState,
        performed_repair_list: list[str],
        *,
        allow_artifact_drift: bool,
    ) -> None:
        """Validate repeatedly until one complete pass needs no repair.

        Args:
            state: Recorded workflow state.
            performed_repair_list: Mutable repair report.
            allow_artifact_drift: Permit task-artifact changes before sealing.
        """

        for _ in range(VALIDATION_REPAIR_PASS_LIMIT):
            current_repair_list: list[str] = []
            self._state_validate_observable_once(
                state,
                current_repair_list,
                allow_artifact_drift=allow_artifact_drift,
            )
            performed_repair_list.extend(current_repair_list)
            if not current_repair_list:
                return
            self._state_write(state, performed_repair_list)
        raise WorktreeError(
            f"Validation did not stabilize after {VALIDATION_REPAIR_PASS_LIMIT} deterministic repair passes"
        )

    def _state_validate_observable_once(
        self,
        state: WorktreeState,
        performed_repair_list: list[str],
        *,
        allow_artifact_drift: bool,
    ) -> None:
        """Run one complete observable-state validation and repair pass.

        Args:
            state: Recorded workflow state.
            performed_repair_list: Mutable repair report.
            allow_artifact_drift: Permit task-artifact changes before sealing.
        """

        if state["coordinating_repository"] != str(self._coordinating_repository):
            raise WorktreeError("Private state coordinating repository does not match the command")
        if state["specification_path"] != self._specification.as_posix():
            raise WorktreeError("Private state specification path does not match the command")
        self._task_artifact_validate(self._specification_path, "Specification")
        goal_path = self._coordinating_repository / ".spec" / f"{self._prefix}{GOAL_SUFFIX}"
        if LIFECYCLE_INDEX_BY_NAME_MAP[state["lifecycle_state"]] < LIFECYCLE_INDEX_BY_NAME_MAP[
            "contracts_authored"
        ] and os.path.lexists(goal_path):
            raise WorktreeError(
                f"Paired goal must not exist before stable contracts are recorded as contracts_authored: {goal_path}"
            )
        if (
            not allow_artifact_drift
            and state["lifecycle_state"] in SEALED_LIFECYCLE_STATE_SET
            and self._path_fingerprint_get(self._specification_path) != state["specification_fingerprint"]
        ):
            raise WorktreeError(f"Sealed specification changed: {self._specification_path}")
        initial_state_content = json.dumps(state, ensure_ascii=True, sort_keys=True)
        for repository_state in state["repository_state_list"]:
            self._repository_state_validate(repository_state, performed_repair_list)
        self._tracked_ignore_complete_validate(state)
        if not allow_artifact_drift and state["lifecycle_state"] in SEALED_LIFECYCLE_STATE_SET:
            self._task_artifact_validate(goal_path, "Goal")
            if self._path_fingerprint_get(goal_path) != state["goal_fingerprint"]:
                raise WorktreeError(f"Sealed goal changed: {goal_path}")
        state_changed = json.dumps(state, ensure_ascii=True, sort_keys=True) != initial_state_content
        for repository_state in state["repository_state_list"]:
            replica_path = self._state_path_get(Path(repository_state["task_root"]))
            try:
                replica_payload = (
                    None
                    if replica_path.is_symlink() or not replica_path.is_file()
                    else json.loads(
                        self._utf8_text_get(
                            replica_path,
                            "Private worktree state replica",
                        )
                    )
                )
            except json.JSONDecodeError, OSError, UnicodeDecodeError, WorktreeError:
                replica_payload = None
            if replica_payload != state and not state_changed:
                performed_repair_list.append(f"restored private state replica: {replica_path}")

    def _state_write(
        self,
        state: WorktreeState,
        performed_repair_list: list[str] | None = None,
    ) -> None:
        """Write private state atomically below every worktree Git path.

        Args:
            state: Complete workflow state.
            performed_repair_list: Optional mutable repair report.
        """

        content = json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        for repository_state in state["repository_state_list"]:
            task_root = Path(repository_state["task_root"])
            state_path = self._state_path_get(task_root)
            self._private_text_atomic_write(
                state_path,
                content,
                allow_symlink_replacement=True,
            )
        self._resource_transaction_list_reconcile_to_state(
            state,
            performed_repair_list if performed_repair_list is not None else [],
        )
        self._resource_source_preimage_list_reconcile_to_state(
            state,
            performed_repair_list if performed_repair_list is not None else [],
        )
        self._legacy_state_replica_list_retire(state, performed_repair_list)
        self._pending_participating_submodule_marker_list_retire(
            state,
            performed_repair_list,
        )
        self._pending_worktree_marker_list_retire(
            state,
            performed_repair_list,
        )
        self._main_leak_transaction_list_retire(
            state,
            performed_repair_list,
        )

    def _resource_source_preimage_list_reconcile_to_state(
        self,
        state: WorktreeState,
        performed_repair_list: list[str],
    ) -> None:
        """Retire source snapshots only after every new state replica is durable."""

        for repository_state in state["repository_state_list"]:
            task_root = Path(repository_state["task_root"])
            self._resource_source_preimage_owner_reconcile(
                task_root,
                repository_state["resource_state_list"],
                performed_repair_list,
                retire_obsolete=True,
            )
            for submodule_state in repository_state["participating_submodule_state_list"]:
                submodule_root = task_root / submodule_state["path"]
                if not self._repository_is_exact_physical_root(submodule_root):
                    raise WorktreeError(
                        f"Task-owned submodule is not one physical repository boundary: {submodule_root}"
                    )
                self._resource_source_preimage_owner_reconcile(
                    submodule_root,
                    submodule_state["resource_state_list"],
                    performed_repair_list,
                    retire_obsolete=True,
                )

    def _legacy_state_replica_list_retire(
        self,
        state: WorktreeState,
        performed_repair_list: list[str] | None,
    ) -> None:
        """Atomically retire every obsolete schema-v1 private-state replica.

        Args:
            state: Authoritative schema-v2 state.
            performed_repair_list: Optional mutable repair report.
        """

        for repository_state in state["repository_state_list"]:
            legacy_state_path = self._legacy_state_path_get(Path(repository_state["task_root"]))
            if not os.path.lexists(legacy_state_path):
                continue
            legacy_state = self._legacy_state_path_load(
                legacy_state_path,
                upgrade_fingerprints=False,
            )
            legacy_repository_identity_list = sorted(
                (
                    item["main_root"],
                    item["task_root"],
                    item["branch_name"],
                    item["common_git_directory"],
                    item["baseline_commit"],
                )
                for item in legacy_state["repository_state_list"]
            )
            current_repository_identity_list = sorted(
                (
                    item["main_root"],
                    item["task_root"],
                    item["branch_name"],
                    item["common_git_directory"],
                    item["baseline_commit"],
                )
                for item in state["repository_state_list"]
            )
            if (
                legacy_state["prefix"] != state["prefix"]
                or legacy_state["coordinating_repository"] != state["coordinating_repository"]
                or legacy_state["specification_path"] != state["specification_path"]
                or legacy_repository_identity_list != current_repository_identity_list
            ):
                raise WorktreeError(f"Legacy private-state replica belongs to another task set: {legacy_state_path}")
            legacy_state_path.unlink()
            if performed_repair_list is not None:
                performed_repair_list.append(f"retired legacy private state replica: {legacy_state_path}")

    def _pending_participating_submodule_marker_list_retire(
        self,
        state: WorktreeState,
        performed_repair_list: list[str] | None,
    ) -> None:
        """Retire first-bootstrap markers only after every state replica is durable."""

        for repository_state in state["repository_state_list"]:
            task_root = Path(repository_state["task_root"])
            for submodule_state in repository_state["participating_submodule_state_list"]:
                path_text = submodule_state["path"]
                pending_state = self._pending_participating_submodule_optional_get(
                    task_root,
                    path_text,
                )
                if pending_state is None:
                    continue
                task_submodule_root = task_root / path_text
                if (
                    self._path_fingerprint_get(task_submodule_root / MANIFEST_NAME)
                    != pending_state["manifest_expected_fingerprint"]
                    or self._path_fingerprint_get(task_submodule_root / ".gitignore")
                    != pending_state["gitignore_expected_fingerprint"]
                ):
                    raise WorktreeError(
                        f"Cannot retire incomplete task-owned submodule bootstrap: {task_submodule_root}"
                    )
                marker_path = self._pending_participating_submodule_marker_path_get(
                    task_root,
                    path_text,
                )
                self._path_remove(marker_path)
                if performed_repair_list is not None:
                    performed_repair_list.append(
                        f"finalized initial task-owned submodule bootstrap: {task_submodule_root}"
                    )

    def _private_text_atomic_write(
        self,
        destination_path: Path,
        content: str,
        *,
        allow_symlink_replacement: bool = False,
        staging_owner_root: Path | None = None,
    ) -> None:
        """Atomically replace one exact provider-owned private text file.

        Args:
            destination_path: Exact file below validated worktree administration.
            content: Complete replacement content.
            allow_symlink_replacement: Replace only the link itself at a proven destination.
            staging_owner_root: Explicit private owner when the destination is outside private administration.
        """

        self._private_atomic_write(
            destination_path,
            content.encode(),
            "Private text",
            allow_symlink_replacement=allow_symlink_replacement,
            staging_owner_root=staging_owner_root,
        )

    def _ordinary_text_atomic_write(
        self,
        owner_root: Path,
        destination_path: Path,
        content: str,
        *,
        forced_mode: int | None = None,
    ) -> None:
        """Replace one project or Git-admin text file through a durable transaction.

        Args:
            owner_root: Exact Git worktree that owns private transaction state.
            destination_path: Ordinary destination file.
            content: Complete replacement text.
            forced_mode: Exact output mode, or the existing/default mode.
        """

        if destination_path.parent.is_symlink() or not destination_path.parent.is_dir():
            raise WorktreeError(f"Atomic text destination has no physical parent: {destination_path}")
        if os.path.lexists(destination_path) and (destination_path.is_symlink() or not destination_path.is_file()):
            raise WorktreeError(f"Atomic text destination is not an ordinary file: {destination_path}")
        current_fingerprint = self._path_fingerprint_get(destination_path)
        mode = (
            forced_mode
            if forced_mode is not None
            else (
                stat.S_IMODE(destination_path.stat(follow_symlinks=False).st_mode)
                if destination_path.is_file()
                else 0o644
            )
        )
        content_bytes = content.encode()
        expected_fingerprint = self._regular_file_fingerprint_get(content_bytes, mode)
        destination_identity = str(Path(os.path.abspath(destination_path)))
        transaction_name = hashlib.sha256(os.fsencode(destination_identity)).hexdigest()
        marker_path = self._git_path_get(
            owner_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "atomic-text-write-v1" / f"{transaction_name}.json",
        )
        private_staging_path = marker_path.with_name(f"{transaction_name}.content")
        temporary_path = destination_path.parent / f".{destination_path.name}.{transaction_name}.tmp"
        expected_payload = {
            "destination": destination_identity,
            "expected_content": content,
            "expected_fingerprint": expected_fingerprint,
            "mode": mode,
            "previous_fingerprint": current_fingerprint,
            "schema_version": 3,
            "staging_scope": "private",
        }
        marker_exists = os.path.lexists(marker_path)
        if marker_exists:
            if marker_path.is_symlink() or not marker_path.is_file():
                raise WorktreeError(f"Atomic text transaction marker is damaged: {marker_path}")
            try:
                payload = json.loads(
                    self._utf8_text_get(
                        marker_path,
                        "Atomic text transaction marker",
                    )
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorktreeError(f"Atomic text transaction marker is invalid: {marker_path}") from exc
            schema_1_key_set = set(expected_payload) - {
                "expected_content",
                "staging_scope",
            }
            schema_2_key_set = set(expected_payload) - {"staging_scope"}
            if (
                not isinstance(payload, dict)
                or set(payload)
                not in (
                    schema_1_key_set,
                    schema_2_key_set,
                    set(expected_payload),
                )
                or type(payload.get("schema_version")) is not int
                or payload.get("schema_version") not in {1, 2, 3}
                or (payload.get("schema_version") == 1 and set(payload) != schema_1_key_set)
                or (
                    payload.get("schema_version") == 2
                    and (set(payload) != schema_2_key_set or not isinstance(payload.get("expected_content"), str))
                )
                or (
                    payload.get("schema_version") == 3
                    and (
                        set(payload) != set(expected_payload)
                        or not isinstance(payload.get("expected_content"), str)
                        or payload.get("staging_scope") != "private"
                    )
                )
                or payload.get("destination") != destination_identity
                or type(payload.get("mode")) is not int
                or not isinstance(payload.get("previous_fingerprint"), str)
                or not isinstance(payload.get("expected_fingerprint"), str)
            ):
                raise WorktreeError(f"Atomic text transaction marker is invalid: {marker_path}")
            if (
                payload["expected_fingerprint"] != expected_fingerprint
                or payload["mode"] != mode
                or (payload["schema_version"] in {2, 3} and payload["expected_content"] != content)
            ):
                raise WorktreeError(f"Atomic text transaction requested different content: {destination_path}")
            previous_fingerprint = payload["previous_fingerprint"]
        else:
            if os.path.lexists(temporary_path):
                raise WorktreeError(f"Atomic text staging path contains independent content: {temporary_path}")
            self._private_text_atomic_write(
                marker_path,
                json.dumps(expected_payload, ensure_ascii=True, sort_keys=True) + "\n",
            )
            payload = expected_payload
            previous_fingerprint = current_fingerprint
        current_fingerprint = self._path_fingerprint_get(destination_path)
        if current_fingerprint not in {previous_fingerprint, expected_fingerprint}:
            raise WorktreeError(f"Atomic text destination contains independent content: {destination_path}")
        if marker_exists and payload["schema_version"] in {1, 2}:
            self._atomic_text_staging_file_remove(
                temporary_path,
                "Atomic text staging path",
                expected_fingerprint,
            )
            upgraded_payload = {
                **expected_payload,
                "previous_fingerprint": previous_fingerprint,
            }
            self._private_text_atomic_write(
                marker_path,
                json.dumps(upgraded_payload, ensure_ascii=True, sort_keys=True) + "\n",
            )
            payload = upgraded_payload
        if current_fingerprint == expected_fingerprint:
            self._atomic_text_private_staging_file_remove(
                private_staging_path,
                expected_fingerprint,
                content_bytes,
            )
            self._atomic_text_staging_file_remove(
                temporary_path,
                "Atomic text staging path",
                expected_fingerprint,
            )
            self._path_remove(marker_path)
            return
        self._atomic_text_private_staging_file_remove(
            private_staging_path,
            expected_fingerprint,
            content_bytes,
        )
        self._atomic_text_staging_file_remove(
            temporary_path,
            "Atomic text staging path",
            expected_fingerprint,
        )
        with private_staging_path.open("xb") as handle:
            handle.write(content_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        private_staging_path.chmod(mode)
        if self._path_fingerprint_get(private_staging_path) != expected_fingerprint:
            raise WorktreeError(f"Cannot stage exact atomic text content: {destination_path}")
        if private_staging_path.stat(follow_symlinks=False).st_dev != destination_path.parent.stat().st_dev:
            raise WorktreeError(f"Atomic text transaction cannot cross filesystems: {destination_path}")
        private_staging_path.replace(temporary_path)
        if self._path_fingerprint_get(destination_path) != current_fingerprint:
            raise WorktreeError(f"Atomic text destination changed during replacement: {destination_path}")
        temporary_path.replace(destination_path)
        directory_descriptor = os.open(destination_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        if self._path_fingerprint_get(destination_path) != expected_fingerprint:
            raise WorktreeError(f"Cannot expose exact atomic text content: {destination_path}")
        self._path_remove(marker_path)

    def _atomic_text_transaction_payload_validate(
        self,
        payload: object,
        marker_path: Path,
        label: str,
    ) -> dict[str, object]:
        """Return one closed legacy or current ordinary-text transaction payload."""

        schema_1_key_set = {
            "destination",
            "expected_fingerprint",
            "mode",
            "previous_fingerprint",
            "schema_version",
        }
        schema_2_key_set = schema_1_key_set | {"expected_content"}
        schema_3_key_set = schema_2_key_set | {"staging_scope"}
        if (
            not isinstance(payload, dict)
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") not in {1, 2, 3}
            or (payload.get("schema_version") == 1 and set(payload) != schema_1_key_set)
            or (
                payload.get("schema_version") == 2
                and (set(payload) != schema_2_key_set or not isinstance(payload.get("expected_content"), str))
            )
            or (
                payload.get("schema_version") == 3
                and (
                    set(payload) != schema_3_key_set
                    or not isinstance(payload.get("expected_content"), str)
                    or payload.get("staging_scope") != "private"
                )
            )
            or not isinstance(payload.get("destination"), str)
            or not Path(payload["destination"]).is_absolute()
            or type(payload.get("mode")) is not int
            or payload["mode"] < 0
            or payload["mode"] > 0o777
            or any(
                not isinstance(payload.get(field_name), str)
                or (payload[field_name] != "absent" and not _hex_digest_is_valid(payload[field_name], {64}))
                for field_name in ("expected_fingerprint", "previous_fingerprint")
            )
            or (
                payload.get("schema_version") in {2, 3}
                and self._regular_file_fingerprint_get(
                    payload["expected_content"].encode(),
                    payload["mode"],
                )
                != payload["expected_fingerprint"]
            )
        ):
            raise WorktreeError(f"{label} is invalid: {marker_path}")
        return cast(dict[str, object], payload)

    def _ordinary_text_atomic_write_list_reconcile(
        self,
        owner_root: Path,
        performed_repair_list: list[str],
    ) -> None:
        """Retire completed or unapplied ordinary-text transactions safely."""

        transaction_root = self._git_path_get(
            owner_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / "atomic-text-write-v1",
        )
        if not os.path.lexists(transaction_root):
            return
        if transaction_root.is_symlink() or not transaction_root.is_dir():
            raise WorktreeError(f"Atomic text transaction owner is damaged: {transaction_root}")
        allowed_root_list = [
            owner_root.resolve(),
            self._git_common_directory_get(owner_root),
        ]
        owned_destination_by_staging_name_map: dict[str, Path] = {}
        for owned_destination_path in (
            owner_root / ".gitignore",
            owner_root / MANIFEST_NAME,
            self._git_common_directory_get(owner_root) / "info" / "exclude",
        ):
            destination_identity = str(Path(os.path.abspath(owned_destination_path)))
            transaction_name = hashlib.sha256(os.fsencode(destination_identity)).hexdigest()
            owned_destination_by_staging_name_map[f"{transaction_name}.json.tmp"] = owned_destination_path
        transaction_entry_list = sorted(transaction_root.iterdir())
        for content_staging_path in (item for item in transaction_entry_list if item.name.endswith(".content")):
            marker_path = content_staging_path.with_name(f"{content_staging_path.name.removesuffix('.content')}.json")
            if not marker_path.is_file() or marker_path.is_symlink():
                raise WorktreeError(f"Atomic text private staging has no durable marker: " f"{content_staging_path}")
        for marker_path in transaction_entry_list:
            if marker_path.name.endswith(".content"):
                continue
            if marker_path.name.endswith(".json.tmp"):
                final_marker_path = marker_path.with_name(marker_path.name.removesuffix(".tmp"))
                if marker_path.is_symlink() or not marker_path.is_file() or os.path.lexists(final_marker_path):
                    raise WorktreeError(f"Atomic text transaction staging marker is damaged: {marker_path}")
                try:
                    payload = json.loads(
                        self._utf8_text_get(
                            marker_path,
                            "Atomic text transaction staging marker",
                        )
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, WorktreeError) as exc:
                    owned_destination_path = owned_destination_by_staging_name_map.get(marker_path.name)
                    if owned_destination_path is None:
                        raise WorktreeError(
                            f"Atomic text transaction staging marker is invalid: {marker_path}"
                        ) from exc
                    if marker_path.stat(follow_symlinks=False).st_nlink != 1:
                        raise WorktreeError(
                            f"Atomic text transaction staging marker is hardlinked: " f"{marker_path}"
                        ) from exc
                    transaction_name = marker_path.name.removesuffix(".json.tmp")
                    destination_temporary_path = (
                        owned_destination_path.parent / f".{owned_destination_path.name}.{transaction_name}.tmp"
                    )
                    private_staging_path = marker_path.with_name(f"{transaction_name}.content")
                    if os.path.lexists(destination_temporary_path) or os.path.lexists(private_staging_path):
                        raise WorktreeError(
                            f"Atomic text transaction staging marker has an unexpected "
                            f"content staging object: {destination_temporary_path}"
                        ) from exc
                    marker_path.unlink()
                    performed_repair_list.append(
                        f"removed partial unexposed atomic text transaction staging: " f"{owned_destination_path}"
                    )
                    continue
                payload = self._atomic_text_transaction_payload_validate(
                    payload,
                    marker_path,
                    "Atomic text transaction staging marker",
                )
                destination_path = Path(payload["destination"])
                try:
                    resolved_parent = destination_path.parent.resolve(strict=True)
                    if not any(
                        resolved_parent == allowed_root or allowed_root in resolved_parent.parents
                        for allowed_root in allowed_root_list
                    ):
                        raise ValueError
                except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                    raise WorktreeError(
                        f"Atomic text transaction staging destination escaped ownership: {destination_path}"
                    ) from exc
                transaction_name = hashlib.sha256(os.fsencode(destination_path)).hexdigest()
                if marker_path.name != f"{transaction_name}.json.tmp":
                    raise WorktreeError(f"Atomic text transaction staging identity is inconsistent: {marker_path}")
                destination_temporary_path = (
                    destination_path.parent / f".{destination_path.name}.{transaction_name}.tmp"
                )
                private_staging_path = marker_path.with_name(f"{transaction_name}.content")
                if (
                    self._path_fingerprint_get(destination_path) != payload["previous_fingerprint"]
                    or os.path.lexists(destination_temporary_path)
                    or os.path.lexists(private_staging_path)
                ):
                    raise WorktreeError(
                        f"Atomic text transaction staging has no exact pre-write state: {destination_path}"
                    )
                marker_path.unlink()
                performed_repair_list.append(f"removed unexposed atomic text transaction staging: {destination_path}")
                continue
            if marker_path.is_symlink() or not marker_path.is_file():
                raise WorktreeError(f"Atomic text transaction marker is damaged: {marker_path}")
            try:
                payload = json.loads(
                    self._utf8_text_get(
                        marker_path,
                        "Atomic text transaction marker",
                    )
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorktreeError(f"Atomic text transaction marker is invalid: {marker_path}") from exc
            payload = self._atomic_text_transaction_payload_validate(
                payload,
                marker_path,
                "Atomic text transaction marker",
            )
            destination_path = Path(payload["destination"])
            try:
                resolved_parent = destination_path.parent.resolve(strict=True)
                if not any(
                    resolved_parent == allowed_root or allowed_root in resolved_parent.parents
                    for allowed_root in allowed_root_list
                ):
                    raise ValueError
            except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                raise WorktreeError(
                    f"Atomic text transaction destination escaped ownership: {destination_path}"
                ) from exc
            transaction_name = hashlib.sha256(os.fsencode(destination_path)).hexdigest()
            if marker_path.name != f"{transaction_name}.json":
                raise WorktreeError(f"Atomic text transaction identity is inconsistent: {marker_path}")
            temporary_path = destination_path.parent / f".{destination_path.name}.{transaction_name}.tmp"
            private_staging_path = marker_path.with_name(f"{transaction_name}.content")
            current_fingerprint = self._path_fingerprint_get(destination_path)
            if current_fingerprint == payload["expected_fingerprint"]:
                outcome = "finalized"
            elif current_fingerprint == payload["previous_fingerprint"]:
                outcome = "rolled back"
            else:
                raise WorktreeError(f"Atomic text destination changed independently: {destination_path}")
            self._atomic_text_staging_file_remove(
                temporary_path,
                "Atomic text staging path",
                payload["expected_fingerprint"],
            )
            if payload["schema_version"] == 3:
                self._atomic_text_private_staging_file_remove(
                    private_staging_path,
                    payload["expected_fingerprint"],
                    payload["expected_content"].encode(),
                )
            elif os.path.lexists(private_staging_path):
                raise WorktreeError(
                    f"Legacy atomic text transaction has unknown private staging: " f"{private_staging_path}"
                )
            self._path_remove(marker_path)
            performed_repair_list.append(f"{outcome} interrupted atomic text write: {destination_path}")

    def _private_bytes_atomic_write(self, destination_path: Path, content: bytes) -> None:
        """Atomically replace one exact provider-owned private binary file.

        Args:
            destination_path: Exact file below validated private administration.
            content: Complete replacement bytes.
        """

        self._private_atomic_write(
            destination_path,
            content,
            "Private bytes",
            allow_symlink_replacement=False,
            staging_owner_root=None,
        )

    def _private_atomic_write(
        self,
        destination_path: Path,
        content: bytes,
        label: str,
        *,
        allow_symlink_replacement: bool,
        staging_owner_root: Path | None,
    ) -> None:
        """Replace one private file through an unpredictable, intent-owned stage."""

        if os.path.lexists(destination_path) and (
            (destination_path.is_symlink() and not allow_symlink_replacement)
            or (not destination_path.is_symlink() and not destination_path.is_file())
        ):
            raise WorktreeError(f"{label} destination is not one physical ordinary file: {destination_path}")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_temporary_path = destination_path.with_name(f"{destination_path.name}.tmp")
        if os.path.lexists(legacy_temporary_path):
            if (
                legacy_temporary_path.is_symlink()
                or not legacy_temporary_path.is_file()
                or legacy_temporary_path.stat(follow_symlinks=False).st_nlink != 1
            ):
                raise WorktreeError(f"{label} staging path is not one physical ordinary file: {legacy_temporary_path}")
            raise WorktreeError(f"{label} staging path contains independent content: {legacy_temporary_path}")
        owner_root = staging_owner_root or self._private_atomic_write_owner_root_get(destination_path)
        self._private_atomic_write_staging_reconcile(owner_root)
        owner_root.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(32)
        staging_path = owner_root / f"{token}.stage"
        marker_path = owner_root / f"{token}.intent"
        payload = {
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "destination": str(Path(os.path.abspath(destination_path))),
            "schema_version": 1,
            "staging_name": staging_path.name,
        }
        marker_path.symlink_to(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        self._directory_fsync(owner_root)
        try:
            with staging_path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if (
                staging_path.stat(follow_symlinks=False).st_nlink != 1
                or hashlib.sha256(staging_path.read_bytes()).hexdigest() != payload["content_sha256"]
            ):
                raise WorktreeError(f"Cannot stage exact {label.lower()} content: {destination_path}")
            if staging_path.stat(follow_symlinks=False).st_dev != destination_path.parent.stat().st_dev:
                raise WorktreeError(f"{label} replacement cannot cross filesystems: {destination_path}")
            staging_path.replace(destination_path)
            self._directory_fsync(destination_path.parent)
            marker_path.unlink()
            self._directory_fsync(owner_root)
        except BaseException:
            if os.path.lexists(staging_path):
                if (
                    staging_path.is_symlink()
                    or not staging_path.is_file()
                    or staging_path.stat(follow_symlinks=False).st_nlink != 1
                ):
                    raise
                staging_path.unlink()
            if marker_path.is_symlink():
                marker_path.unlink()
            raise

    def _private_atomic_write_owner_root_get(self, destination_path: Path) -> Path:
        """Return one central private atomic-write owner on the destination filesystem."""

        for ancestor_path in destination_path.parents:
            if ancestor_path.name == PRIVATE_STATE_DIRECTORY_NAME:
                return ancestor_path / "private-atomic-write-v1"
        raise WorktreeError(f"Private atomic-write destination requires an explicit staging owner: {destination_path}")

    def _private_atomic_write_staging_reconcile(self, owner_root: Path) -> None:
        """Discard only random stages proven by atomic intent symlinks."""

        if not os.path.lexists(owner_root):
            return
        if owner_root.is_symlink() or not owner_root.is_dir():
            raise WorktreeError(f"Private atomic-write owner is not one physical directory: {owner_root}")
        marker_by_staging_name_map: dict[str, Path] = {}
        staging_by_name_map: dict[str, Path] = {}
        for candidate_path in owner_root.iterdir():
            if candidate_path.name.endswith(".intent"):
                if not candidate_path.is_symlink():
                    raise WorktreeError(f"Private atomic-write intent is damaged: {candidate_path}")
                token = candidate_path.name.removesuffix(".intent")
                if len(token) != 64 or not _hex_digest_is_valid(token, {64}):
                    raise WorktreeError(f"Private atomic-write intent identity is invalid: {candidate_path}")
                try:
                    payload = json.loads(os.readlink(candidate_path))
                except json.JSONDecodeError as exc:
                    raise WorktreeError(f"Private atomic-write intent is invalid: {candidate_path}") from exc
                expected_key_set = {
                    "content_sha256",
                    "destination",
                    "schema_version",
                    "staging_name",
                }
                if (
                    not isinstance(payload, dict)
                    or set(payload) != expected_key_set
                    or payload.get("schema_version") != 1
                    or type(payload.get("schema_version")) is not int
                    or not _hex_digest_is_valid(payload.get("content_sha256"), {64})
                    or not isinstance(payload.get("destination"), str)
                    or not Path(payload["destination"]).is_absolute()
                    or payload.get("staging_name") != f"{token}.stage"
                ):
                    raise WorktreeError(f"Private atomic-write intent is invalid: {candidate_path}")
                marker_by_staging_name_map[f"{token}.stage"] = candidate_path
                continue
            if candidate_path.name.endswith(".stage"):
                staging_by_name_map[candidate_path.name] = candidate_path
                continue
            raise WorktreeError(f"Private atomic-write owner contains unknown content: {candidate_path}")
        unknown_staging_name_set = set(staging_by_name_map) - set(marker_by_staging_name_map)
        if unknown_staging_name_set:
            raise WorktreeError(
                f"Private atomic-write staging has no atomic ownership: "
                f"{staging_by_name_map[sorted(unknown_staging_name_set)[0]]}"
            )
        for staging_name, marker_path in marker_by_staging_name_map.items():
            staging_path = staging_by_name_map.get(staging_name)
            if staging_path is not None:
                if (
                    staging_path.is_symlink()
                    or not staging_path.is_file()
                    or staging_path.stat(follow_symlinks=False).st_nlink != 1
                ):
                    raise WorktreeError(f"Private atomic-write staging is damaged: {staging_path}")
                staging_path.unlink()
            marker_path.unlink()
        self._directory_fsync(owner_root)

    def _directory_fsync(self, directory_path: Path) -> None:
        """Synchronize one already validated physical directory."""

        directory_descriptor = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    def _atomic_text_staging_file_remove(
        self,
        temporary_path: Path,
        label: str,
        expected_fingerprint: str,
    ) -> None:
        """Remove only an exact project-directory ordinary-text stage."""

        if not os.path.lexists(temporary_path):
            return
        if (
            temporary_path.is_symlink()
            or not temporary_path.is_file()
            or temporary_path.stat(follow_symlinks=False).st_nlink != 1
        ):
            raise WorktreeError(f"{label} is not one physical ordinary file: {temporary_path}")
        if self._path_fingerprint_get(temporary_path) == expected_fingerprint:
            temporary_path.unlink()
            return
        raise WorktreeError(f"{label} contains independent content: {temporary_path}")

    def _atomic_text_private_staging_file_remove(
        self,
        private_staging_path: Path,
        expected_fingerprint: str,
        expected_content: bytes,
    ) -> None:
        """Discard an exact or partial provider-private content stage."""

        if not os.path.lexists(private_staging_path):
            return
        if (
            private_staging_path.is_symlink()
            or not private_staging_path.is_file()
            or private_staging_path.stat(follow_symlinks=False).st_nlink != 1
        ):
            raise WorktreeError(
                f"Atomic text private staging is not one physical ordinary file: " f"{private_staging_path}"
            )
        if self._path_fingerprint_get(private_staging_path) == expected_fingerprint:
            private_staging_path.unlink()
            return
        if expected_content.startswith(private_staging_path.read_bytes()):
            private_staging_path.unlink()
            return
        raise WorktreeError(f"Atomic text private staging contains independent content: " f"{private_staging_path}")

    def _atomic_staging_file_remove(self, temporary_path: Path, label: str) -> None:
        """Remove only one ordinary atomic-write staging file."""

        if not os.path.lexists(temporary_path):
            return
        if (
            temporary_path.is_symlink()
            or not temporary_path.is_file()
            or temporary_path.stat(follow_symlinks=False).st_nlink != 1
        ):
            raise WorktreeError(f"{label} is not one physical ordinary file: {temporary_path}")
        temporary_path.unlink()

    def _task_artifact_validate(self, artifact_path: Path, label: str) -> None:
        """Require one physical untracked task-artifact file.

        Args:
            artifact_path: Exact physical task-artifact path.
            label: Human-readable artifact kind.
        """

        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise WorktreeError(f"{label} must be one physical ordinary file: {artifact_path}")
        if artifact_path.stat(follow_symlinks=False).st_nlink != 1:
            raise WorktreeError(f"{label} must have one physical filesystem link: {artifact_path}")
        relative_path = artifact_path.relative_to(self._coordinating_repository)
        if (
            self._git_command.run(
                self._coordinating_repository,
                ["ls-files", "--error-unmatch", "--", relative_path.as_posix()],
                check=False,
            ).returncode
            == 0
        ):
            raise WorktreeError(f"{label} must remain untracked by Git: {artifact_path}")

    def _status_by_path_map_get(self, repository_root: Path) -> dict[str, str]:
        """Return one repository's non-ignored status by path.

        Args:
            repository_root: Exact repository root.

        Returns:
            Porcelain status by root-relative path.
        """

        result = self._git_command.run(
            repository_root,
            [
                "-c",
                "core.fileMode=true",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ],
        )
        item_list = result.stdout.split("\0")
        status_by_path_map: dict[str, str] = {}
        index = 0
        while index < len(item_list):
            item = item_list[index]
            index += 1
            if not item:
                continue
            if len(item) < 4 or item[2] != " ":
                raise WorktreeError(f"Cannot parse Git status entry in {repository_root}: {item!r}")
            status_text = item[:2]
            path_text = item[3:]
            status_by_path_map[path_text] = status_text
            if "R" in status_text or "C" in status_text:
                if index >= len(item_list) or not item_list[index]:
                    raise WorktreeError(f"Cannot parse renamed Git status entry in {repository_root}: {item!r}")
                status_by_path_map[item_list[index]] = status_text
                index += 1
        for path_text, tag in self._index_nondefault_flag_by_path_map_get(repository_root).items():
            status_by_path_map.setdefault(path_text, f"F{tag}")
        return status_by_path_map

    def _submodule_commit_by_path_map_get(self, task_root: Path) -> dict[str, str]:
        """Return recursive submodule commits by path.

        Args:
            task_root: Task-worktree root.

        Returns:
            Effective submodule commits by root-relative path.
        """

        submodule_commit_by_path_map = self._submodule_index_commit_by_path_map_get(task_root)
        for path_text, expected_commit in submodule_commit_by_path_map.items():
            submodule_root = task_root / path_text
            current_commit = self._git_command.run(submodule_root, ["rev-parse", "HEAD"]).stdout.strip()
            if current_commit != expected_commit:
                raise WorktreeError(
                    f"Submodule is not at its recorded initialized gitlink: "
                    f"{submodule_root} expected={expected_commit} current={current_commit}"
                )
        return submodule_commit_by_path_map

    def _submodule_index_commit_by_path_map_get(self, task_root: Path) -> dict[str, str]:
        """Return recursive stage-zero gitlinks without constraining effective HEADs.

        Args:
            task_root: Top-level task-worktree root.

        Returns:
            Recursive index gitlink commits by top-level root-relative path.
        """

        submodule_commit_by_path_map: dict[str, str] = {}
        self._submodule_index_commit_by_path_map_populate(
            task_root,
            "",
            submodule_commit_by_path_map,
        )
        return submodule_commit_by_path_map

    def _submodule_index_commit_by_path_map_populate(
        self,
        repository_root: Path,
        parent_path_text: str,
        submodule_commit_by_path_map: dict[str, str],
    ) -> None:
        """Populate recursive exact submodule paths and index gitlinks.

        Args:
            repository_root: Current parent repository boundary.
            parent_path_text: Current boundary path relative to the top-level task root.
            submodule_commit_by_path_map: Mutable exact recursive result.
        """

        for submodule_path in self._submodule_path_list_get(repository_root):
            path_text = submodule_path.as_posix()
            index_entry_list = self._index_entry_list_get(repository_root, path_text)
            if len(index_entry_list) != 1:
                raise WorktreeError(f"Submodule has no single recorded index gitlink: {repository_root / path_text}")
            mode_text, expected_commit, stage_text = index_entry_list[0].split()
            if mode_text != "160000" or stage_text != "0":
                raise WorktreeError(f"Submodule has no stage-zero index gitlink: {repository_root / path_text}")
            submodule_root = repository_root / path_text
            if not self._repository_is_exact_physical_root(submodule_root):
                raise WorktreeError(f"Submodule is not initialized at its exact root: {submodule_root}")
            full_path_text = f"{parent_path_text}/{path_text}" if parent_path_text else path_text
            submodule_commit_by_path_map[full_path_text] = expected_commit
            self._submodule_index_commit_by_path_map_populate(
                submodule_root,
                full_path_text,
                submodule_commit_by_path_map,
            )

    def _submodule_path_list_get(self, task_root: Path) -> list[PurePosixPath]:
        """Return direct submodule paths declared by one repository.

        Args:
            task_root: Repository root.

        Returns:
            Direct submodule path list.
        """

        gitmodules_path = task_root / ".gitmodules"
        if not gitmodules_path.is_file():
            return []
        result = self._git_command.run(
            task_root,
            ["config", "-z", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise WorktreeError(f"Cannot read submodule paths from {gitmodules_path}: {result.stderr.strip()}")
        submodule_path_list: list[PurePosixPath] = []
        for record_text in result.stdout.split("\0"):
            if not record_text:
                continue
            _, separator, path_text = record_text.partition("\n")
            if not separator:
                raise WorktreeError(f"Cannot parse one submodule path from {gitmodules_path}")
            self._git_path_text_validate(path_text, "Submodule path")
            submodule_path_list.append(PurePosixPath(path_text))
        if len(submodule_path_list) != len(set(submodule_path_list)):
            raise WorktreeError(f"Submodule paths are duplicated in {gitmodules_path}")
        return submodule_path_list

    def _submodule_prepare(
        self,
        task_root: Path,
        performed_repair_list: list[str],
        participating_submodule_path_set: set[str] | None = None,
    ) -> None:
        """Synchronize and initialize recursive submodules at recorded gitlinks.

        Args:
            task_root: Task-worktree root.
            performed_repair_list: Mutable repair report.
            participating_submodule_path_set: Previously recorded task-owned submodules.
        """

        if not (task_root / ".gitmodules").is_file():
            return
        participating_submodule_path_set = participating_submodule_path_set or set()
        self._submodule_dirty_validate(
            task_root,
            participating_submodule_path_set,
        )
        self._git_command.run(task_root, ["submodule", "sync", "--recursive"])
        self._submodule_checkout_prepare_recursive(
            task_root,
            "",
            participating_submodule_path_set,
        )
        performed_repair_list.append(f"synchronized recursive submodules: {task_root}")

    def _participating_submodule_path_set_validate(
        self,
        task_root: Path,
        participating_submodule_path_set: set[str],
    ) -> None:
        """Validate exact task-owned recursive submodule identities.

        Args:
            task_root: Exact top-level task-worktree root.
            participating_submodule_path_set: Requested recursive submodule paths.
        """

        submodule_commit_by_path_map = self._submodule_index_commit_by_path_map_get(task_root)
        unknown_path_set = participating_submodule_path_set - set(submodule_commit_by_path_map)
        if unknown_path_set:
            raise WorktreeError(
                f"Task-owned submodule path is not one initialized recursive gitlink in {task_root}: "
                + ", ".join(sorted(unknown_path_set))
            )
        for path_text in sorted(participating_submodule_path_set):
            path = PurePosixPath(path_text)
            missing_ancestor_list = [
                ancestor_path
                for ancestor_path in submodule_commit_by_path_map
                if PurePosixPath(ancestor_path) in path.parents
                and ancestor_path not in participating_submodule_path_set
            ]
            if missing_ancestor_list:
                raise WorktreeError(
                    f"Nested task-owned submodule requires every submodule ancestor to participate: "
                    f"{path_text}; missing={', '.join(sorted(missing_ancestor_list))}"
                )

    def _pending_participating_submodule_marker_path_get(
        self,
        task_root: Path,
        path_text: str,
    ) -> Path:
        """Return one top-level private pending-participant marker path."""

        marker_name = hashlib.sha256(os.fsencode(path_text)).hexdigest()
        return self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / PENDING_PARTICIPATING_SUBMODULE_DIRECTORY_NAME / f"{marker_name}.json",
        )

    def _pending_participating_submodule_optional_get(
        self,
        task_root: Path,
        path_text: str,
    ) -> PendingParticipatingSubmodule | None:
        """Load and validate pending bootstrap ownership for one submodule."""

        marker_path = self._pending_participating_submodule_marker_path_get(task_root, path_text)
        if not os.path.lexists(marker_path):
            return None
        if marker_path.is_symlink() or not marker_path.is_file():
            raise WorktreeError(f"Pending task-owned submodule marker is damaged: {marker_path}")
        try:
            payload = json.loads(
                self._utf8_text_get(
                    marker_path,
                    "Pending task-owned submodule marker",
                )
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorktreeError(f"Pending task-owned submodule marker is invalid: {marker_path}") from exc
        expected_key_set = {
            "baseline_commit",
            "gitignore_expected_fingerprint",
            "gitignore_expected_text",
            "gitignore_mode",
            "gitignore_previous_fingerprint",
            "manifest_expected_fingerprint",
            "manifest_expected_text",
            "manifest_mode",
            "manifest_previous_fingerprint",
            "path",
            "schema_version",
        }
        fingerprint_field_name_list = [
            "gitignore_expected_fingerprint",
            "gitignore_previous_fingerprint",
            "manifest_expected_fingerprint",
            "manifest_previous_fingerprint",
        ]
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_key_set
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
            or payload.get("path") != path_text
            or not _hex_digest_is_valid(payload.get("baseline_commit"), {40, 64})
            or any(
                not isinstance(payload.get(field_name), str)
                or (payload[field_name] != "absent" and not _hex_digest_is_valid(payload[field_name], {64}))
                for field_name in fingerprint_field_name_list
            )
            or any(
                type(payload.get(field_name)) is not int or payload[field_name] < 0 or payload[field_name] > 0o777
                for field_name in ("gitignore_mode", "manifest_mode")
            )
            or any(
                not isinstance(payload.get(field_name), str)
                for field_name in ("gitignore_expected_text", "manifest_expected_text")
            )
        ):
            raise WorktreeError(f"Pending task-owned submodule marker is invalid: {marker_path}")
        for prefix in ("gitignore", "manifest"):
            if (
                prefix == "gitignore"
                and payload["gitignore_previous_fingerprint"] == "absent"
                and payload["gitignore_expected_text"] == ""
            ):
                expected_fingerprint = "absent"
            else:
                expected_fingerprint = self._regular_file_fingerprint_get(
                    payload[f"{prefix}_expected_text"].encode(),
                    payload[f"{prefix}_mode"],
                )
            if expected_fingerprint != payload[f"{prefix}_expected_fingerprint"]:
                raise WorktreeError(f"Pending task-owned submodule marker is inconsistent: {marker_path}")
        return cast(PendingParticipatingSubmodule, payload)

    def _pending_participating_submodule_create(
        self,
        task_root: Path,
        path_text: str,
        baseline_commit: str,
    ) -> PendingParticipatingSubmodule:
        """Record exact bootstrap outputs before first participant mutation."""

        task_submodule_root = task_root / path_text
        manifest_path = task_submodule_root / MANIFEST_NAME
        gitignore_path = task_submodule_root / ".gitignore"
        if self._status_by_path_map_get(task_submodule_root):
            raise WorktreeError(f"New task-owned submodule contains unrecorded dirty state: {task_submodule_root}")
        if os.path.lexists(manifest_path) and (manifest_path.is_symlink() or not manifest_path.is_file()):
            raise WorktreeError(f"Bootstrap manifest must be one physical ordinary file: {manifest_path}")
        manifest_previous_fingerprint = self._path_fingerprint_get(manifest_path)
        manifest_expected_text = (
            self._utf8_text_get(
                manifest_path,
                "Task-owned submodule bootstrap manifest",
            )
            if manifest_path.is_file()
            else EMPTY_MANIFEST_TEXT
        )
        manifest_mode = (
            stat.S_IMODE(manifest_path.stat(follow_symlinks=False).st_mode) if manifest_path.is_file() else 0o644
        )
        manifest_expected_fingerprint = self._regular_file_fingerprint_get(
            manifest_expected_text.encode(),
            manifest_mode,
        )
        if manifest_path.is_file():
            resource_by_class_map = self._manifest_get(manifest_path, task_submodule_root)
        else:
            resource_by_class_map = {resource_class: [] for resource_class in MANIFEST_RESOURCE_KEY_SET}
        required_ignore_path_list = [
            PurePosixPath(resource_path_text)
            for resource_path_text in sorted(
                {path for path_list in resource_by_class_map.values() for path in path_list}
            )
        ]
        gitignore_previous_fingerprint = self._path_fingerprint_get(gitignore_path)
        _, gitignore_expected_text, _ = self._tracked_ignore_text_get(
            task_submodule_root,
            required_ignore_path_list,
        )
        gitignore_mode = (
            stat.S_IMODE(gitignore_path.stat(follow_symlinks=False).st_mode) if gitignore_path.is_file() else 0o644
        )
        gitignore_expected_fingerprint = (
            "absent"
            if gitignore_previous_fingerprint == "absent" and gitignore_expected_text == ""
            else self._regular_file_fingerprint_get(
                gitignore_expected_text.encode(),
                gitignore_mode,
            )
        )
        payload: PendingParticipatingSubmodule = {
            "baseline_commit": baseline_commit,
            "gitignore_expected_fingerprint": gitignore_expected_fingerprint,
            "gitignore_expected_text": gitignore_expected_text,
            "gitignore_mode": gitignore_mode,
            "gitignore_previous_fingerprint": gitignore_previous_fingerprint,
            "manifest_expected_fingerprint": manifest_expected_fingerprint,
            "manifest_expected_text": manifest_expected_text,
            "manifest_mode": manifest_mode,
            "manifest_previous_fingerprint": manifest_previous_fingerprint,
            "path": path_text,
            "schema_version": 1,
        }
        self._private_text_atomic_write(
            self._pending_participating_submodule_marker_path_get(task_root, path_text),
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        )
        return payload

    def _pending_participating_submodule_status_validate(
        self,
        task_root: Path,
        path_text: str,
        participating_submodule_path_set: set[str],
    ) -> None:
        """Reject dirty paths not attributable to pending provider bootstrap."""

        task_submodule_root = task_root / path_text
        allowed_path_set = {".gitignore", MANIFEST_NAME}
        for direct_submodule_path in self._submodule_path_list_get(task_submodule_root):
            full_child_path = (PurePosixPath(path_text) / direct_submodule_path).as_posix()
            if full_child_path in participating_submodule_path_set:
                allowed_path_set.add(direct_submodule_path.as_posix())
        unexpected_path_set = set(self._status_by_path_map_get(task_submodule_root)) - allowed_path_set
        if unexpected_path_set:
            raise WorktreeError(
                f"Pending task-owned submodule contains independent dirty state in "
                f"{task_submodule_root}: {', '.join(sorted(unexpected_path_set))}"
            )

    def _pending_participating_submodule_expose(
        self,
        task_root: Path,
        pending_state: PendingParticipatingSubmodule,
        participating_submodule_path_set: set[str],
        performed_repair_list: list[str],
    ) -> None:
        """Expose only exact pre-recorded participant bootstrap files."""

        path_text = pending_state["path"]
        task_submodule_root = task_root / path_text
        self._pending_participating_submodule_status_validate(
            task_root,
            path_text,
            participating_submodule_path_set,
        )
        manifest_path = task_submodule_root / MANIFEST_NAME
        current_manifest_fingerprint = self._path_fingerprint_get(manifest_path)
        if current_manifest_fingerprint not in {
            pending_state["manifest_previous_fingerprint"],
            pending_state["manifest_expected_fingerprint"],
        }:
            raise WorktreeError(f"Pending submodule manifest contains independent content: {manifest_path}")
        if current_manifest_fingerprint != pending_state["manifest_expected_fingerprint"]:
            if pending_state["manifest_previous_fingerprint"] == "absent":
                self._initial_manifest_create(
                    task_submodule_root,
                    performed_repair_list,
                    report_text="created missing initial task-owned submodule manifest",
                )
            else:
                self._ordinary_text_atomic_write(
                    task_submodule_root,
                    manifest_path,
                    pending_state["manifest_expected_text"],
                    forced_mode=pending_state["manifest_mode"],
                )
        gitignore_path = task_submodule_root / ".gitignore"
        current_gitignore_fingerprint = self._path_fingerprint_get(gitignore_path)
        if current_gitignore_fingerprint not in {
            pending_state["gitignore_previous_fingerprint"],
            pending_state["gitignore_expected_fingerprint"],
        }:
            raise WorktreeError(f"Pending submodule ignore file contains independent content: {gitignore_path}")
        if current_gitignore_fingerprint != pending_state["gitignore_expected_fingerprint"]:
            self._ordinary_text_atomic_write(
                task_submodule_root,
                gitignore_path,
                pending_state["gitignore_expected_text"],
                forced_mode=pending_state["gitignore_mode"],
            )
            performed_repair_list.append(f"authored pending task-owned submodule ignore rules: {task_submodule_root}")
        self._pending_participating_submodule_status_validate(
            task_root,
            path_text,
            participating_submodule_path_set,
        )

    def _participating_submodule_state_list_prepare(
        self,
        main_root: Path,
        task_root: Path,
        participating_submodule_path_set: set[str],
        performed_repair_list: list[str],
        skipped_optional_resource_list: list[str],
        previous_state_list: list[ParticipatingSubmoduleState],
    ) -> list[ParticipatingSubmoduleState]:
        """Prepare manifests and resources for explicit task-owned submodules.

        Args:
            main_root: Top-level main-worktree source root.
            task_root: Exact top-level task-worktree root.
            participating_submodule_path_set: Explicit recursive submodule paths.
            performed_repair_list: Mutable repair report.
            skipped_optional_resource_list: Mutable optional-resource report.
            previous_state_list: Previously recorded task-owned submodule state.

        Returns:
            Complete task-owned submodule state ordered by path depth and text.
        """

        previous_state_by_path_map = {item["path"]: item for item in previous_state_list}
        baseline_commit_by_path_map = self._submodule_index_commit_by_path_map_get(task_root)
        state_list: list[ParticipatingSubmoduleState] = []
        for path_text in sorted(
            participating_submodule_path_set,
            key=lambda item: (len(PurePosixPath(item).parts), item),
        ):
            task_submodule_root = task_root / path_text
            main_submodule_root = main_root / path_text
            previous_state = previous_state_by_path_map.get(path_text)
            pending_state = self._pending_participating_submodule_optional_get(
                task_root,
                path_text,
            )
            if previous_state is None and pending_state is None:
                pending_state = self._pending_participating_submodule_create(
                    task_root,
                    path_text,
                    baseline_commit_by_path_map[path_text],
                )
            if pending_state is not None:
                if pending_state["baseline_commit"] != baseline_commit_by_path_map[path_text]:
                    raise WorktreeError(f"Pending task-owned submodule baseline changed: {task_submodule_root}")
                self._pending_participating_submodule_expose(
                    task_root,
                    pending_state,
                    participating_submodule_path_set,
                    performed_repair_list,
                )
            manifest_path = task_submodule_root / MANIFEST_NAME
            if not os.path.lexists(manifest_path):
                if previous_state is None:
                    raise WorktreeError(f"Pending task-owned submodule did not expose its manifest: {manifest_path}")
                self._initial_manifest_restore(
                    task_submodule_root,
                    performed_repair_list,
                    report_text="restored provider-owned initial task-owned submodule manifest",
                )
            else:
                self._initial_manifest_owner_retire_if_changed(
                    task_submodule_root,
                    performed_repair_list,
                )
            resource_by_class_map = self._manifest_get(manifest_path, task_submodule_root)
            required_ignore_path_list = [
                PurePosixPath(path_text)
                for path_text in sorted(
                    {path_text for path_list in resource_by_class_map.values() for path_text in path_list}
                )
            ]
            for added_pattern in self._tracked_ignore_prepare(
                task_submodule_root,
                required_ignore_path_list,
            ):
                performed_repair_list.append(
                    f"authored task-owned submodule ignore pattern {added_pattern}: {task_submodule_root}"
                )
            resource_state_list = self._resource_state_list_prepare(
                main_submodule_root,
                resource_by_class_map,
                task_submodule_root,
                performed_repair_list,
                skipped_optional_resource_list,
                previous_state["resource_state_list"] if previous_state is not None else [],
            )
            current_main_status_by_path_map = self._status_by_path_map_get(main_submodule_root)
            delegated_descendant_path_set = {
                PurePosixPath(candidate_path_text).relative_to(PurePosixPath(path_text)).as_posix()
                for candidate_path_text in participating_submodule_path_set
                if candidate_path_text != path_text
                and PurePosixPath(path_text) in PurePosixPath(candidate_path_text).parents
            }
            for delegated_path_text in self._path_boundary_overlap_set_get(
                set(current_main_status_by_path_map),
                delegated_descendant_path_set,
            ):
                del current_main_status_by_path_map[delegated_path_text]
            current_main_status_fingerprint_by_path_map = {
                main_path_text: self._path_git_state_fingerprint_get(main_submodule_root, main_path_text)
                for main_path_text in current_main_status_by_path_map
            }
            previous_main_status_by_path_map = (
                {
                    main_path_text: status_text
                    for main_path_text, status_text in previous_state["main_status_by_path_map"].items()
                    if main_path_text
                    not in self._path_boundary_overlap_set_get(
                        {main_path_text},
                        delegated_descendant_path_set,
                    )
                }
                if previous_state is not None
                else {}
            )
            previous_main_status_fingerprint_by_path_map = (
                previous_state["main_status_fingerprint_by_path_map"] if previous_state is not None else {}
            )
            main_preimage_by_path_map = self._main_preimage_by_path_map_refresh(
                main_submodule_root,
                task_submodule_root,
                current_main_status_by_path_map,
                current_main_status_fingerprint_by_path_map,
                previous_main_status_by_path_map,
                previous_main_status_fingerprint_by_path_map,
                previous_state["main_preimage_by_path_map"] if previous_state is not None else {},
                performed_repair_list,
            )
            state_list.append(
                {
                    "accepted_main_commit_drift_list": (
                        previous_state["accepted_main_commit_drift_list"] if previous_state is not None else []
                    ),
                    "baseline_commit": (
                        previous_state["baseline_commit"]
                        if previous_state is not None
                        else baseline_commit_by_path_map[path_text]
                    ),
                    "main_commit": self._git_command.run(
                        main_submodule_root,
                        ["rev-parse", "HEAD"],
                    ).stdout.strip(),
                    "main_leak_fingerprint_by_path_map": (
                        previous_state["main_leak_fingerprint_by_path_map"] if previous_state is not None else {}
                    ),
                    "main_preimage_by_path_map": main_preimage_by_path_map,
                    "main_status_by_path_map": current_main_status_by_path_map,
                    "main_status_fingerprint_by_path_map": current_main_status_fingerprint_by_path_map,
                    "manifest_fingerprint": self._path_fingerprint_get(manifest_path),
                    "path": path_text,
                    "resource_state_list": resource_state_list,
                }
            )
        return state_list

    def _submodule_checkout_prepare_recursive(
        self,
        repository_root: Path,
        parent_path_text: str,
        participating_submodule_path_set: set[str],
    ) -> None:
        """Initialize read-only submodules without resetting task-owned descendants.

        Args:
            repository_root: Current initialized parent repository.
            parent_path_text: Parent path relative to the top-level task root.
            participating_submodule_path_set: Explicit task-owned recursive paths.
        """

        for submodule_path in self._submodule_path_list_get(repository_root):
            path_text = submodule_path.as_posix()
            full_path_text = f"{parent_path_text}/{path_text}" if parent_path_text else path_text
            submodule_root = repository_root / path_text
            index_entry_list = self._index_entry_list_get(repository_root, path_text)
            if len(index_entry_list) != 1:
                raise WorktreeError(f"Submodule has no single recorded index gitlink: {submodule_root}")
            mode_text, expected_commit, stage_text = index_entry_list[0].split()
            if mode_text != "160000" or stage_text != "0":
                raise WorktreeError(f"Submodule has no stage-zero index gitlink: {submodule_root}")
            initialized = self._repository_is_exact_physical_root(submodule_root)
            if full_path_text not in participating_submodule_path_set or not initialized:
                self._submodule_checkout_collision_validate(
                    submodule_root,
                    expected_commit,
                    initialized=bool(initialized),
                )
                self._git_command.run(
                    repository_root,
                    [
                        "-c",
                        "protocol.file.allow=always",
                        "submodule",
                        "update",
                        "--init",
                        "--checkout",
                        "--",
                        f"./{path_text}",
                    ],
                )
            if not self._repository_is_exact_physical_root(submodule_root):
                raise WorktreeError(f"Submodule is not initialized at its exact root: {submodule_root}")
            current_commit = self._git_command.run(submodule_root, ["rev-parse", "HEAD"]).stdout.strip()
            if full_path_text in participating_submodule_path_set:
                if (
                    self._git_command.run(
                        submodule_root,
                        ["merge-base", "--is-ancestor", expected_commit, current_commit],
                        check=False,
                    ).returncode
                    != 0
                ):
                    raise WorktreeError(
                        f"Task-owned submodule no longer descends from its recorded gitlink: {submodule_root}"
                    )
            elif current_commit != expected_commit:
                raise WorktreeError(
                    f"Submodule is not at its recorded initialized gitlink: "
                    f"{submodule_root} expected={expected_commit} current={current_commit}"
                )
            self._submodule_checkout_prepare_recursive(
                submodule_root,
                full_path_text,
                participating_submodule_path_set,
            )

    def _submodule_checkout_collision_validate(
        self,
        submodule_root: Path,
        expected_commit: str,
        *,
        initialized: bool,
    ) -> None:
        """Reject ignored or uninitialized objects that checkout could overwrite.

        Args:
            submodule_root: Exact recursive submodule path.
            expected_commit: Gitlink commit targeted by checkout.
            initialized: Whether the path is already its own Git worktree.
        """

        if not initialized:
            if (
                submodule_root.is_symlink()
                or (submodule_root.is_dir() and any(submodule_root.iterdir()))
                or (os.path.lexists(submodule_root) and not submodule_root.is_dir())
            ):
                raise WorktreeError(f"Uninitialized submodule path contains independent content: {submodule_root}")
            return
        ignored_path_set = {
            path_text
            for path_text in self._git_command.run(
                submodule_root,
                [
                    "ls-files",
                    "--others",
                    "--ignored",
                    "--exclude-standard",
                    "-z",
                ],
            ).stdout.split("\0")
            if path_text
        }
        if not ignored_path_set:
            return
        target_path_set = {
            path_text
            for path_text in self._git_command.run(
                submodule_root,
                ["ls-tree", "-r", "--name-only", "-z", expected_commit],
            ).stdout.split("\0")
            if path_text
        }
        collision_set = self._path_boundary_overlap_set_get(
            ignored_path_set,
            target_path_set,
        )
        if collision_set:
            raise WorktreeError(
                f"Ignored submodule objects would be overwritten by recorded gitlink checkout in "
                f"{submodule_root}: {', '.join(sorted(collision_set))}"
            )

    def _submodule_url_configuration_fingerprint_get(self, task_root: Path) -> str:
        """Fingerprint recursive initialized submodule URL configuration.

        Args:
            task_root: Exact top-level task-worktree root.

        Returns:
            Deterministic SHA-256 fingerprint of local submodule URL entries.
        """

        digest = hashlib.sha256()

        def repository_populate(repository_root: Path, parent_path_text: str) -> None:
            result = self._git_command.run(
                repository_root,
                ["config", "--local", "-z", "--get-regexp", r"^submodule\..*\.url$"],
                check=False,
            )
            if result.returncode not in {0, 1}:
                raise WorktreeError(
                    f"Cannot inspect submodule URL configuration at {repository_root}: {result.stderr.strip()}"
                )
            digest.update(os.fsencode(parent_path_text))
            digest.update(b"\0")
            digest.update(os.fsencode(result.stdout))
            digest.update(b"\0")
            for submodule_path in self._submodule_path_list_get(repository_root):
                submodule_root = repository_root / submodule_path.as_posix()
                if not self._repository_is_exact_physical_root(submodule_root):
                    continue
                path_text = submodule_path.as_posix()
                full_path_text = f"{parent_path_text}/{path_text}" if parent_path_text else path_text
                repository_populate(submodule_root, full_path_text)

        repository_populate(task_root, "")
        return digest.hexdigest()

    def _submodule_dirty_validate(
        self,
        task_root: Path,
        participating_submodule_path_set: set[str],
        parent_path_text: str = "",
    ) -> None:
        """Reject dirty initialized submodules before any checkout repair.

        Args:
            task_root: Task-worktree root.
            participating_submodule_path_set: Explicit task-owned recursive paths.
            parent_path_text: Current repository path relative to the top-level task root.
        """

        for submodule_path in self._submodule_path_list_get(task_root):
            path_text = submodule_path.as_posix()
            full_path_text = f"{parent_path_text}/{path_text}" if parent_path_text else path_text
            submodule_root = task_root / submodule_path.as_posix()
            if not self._repository_is_exact_physical_root(submodule_root):
                continue
            status_by_path_map = self._status_by_path_map_get(submodule_root)
            if status_by_path_map and full_path_text not in participating_submodule_path_set:
                self._dirty_submodule_error_raise(submodule_root, status_by_path_map)
            self._submodule_dirty_validate(
                submodule_root,
                participating_submodule_path_set,
                full_path_text,
            )

    def _dirty_submodule_error_raise(
        self,
        submodule_root: Path,
        status_by_path_map: dict[str, str],
    ) -> None:
        """Raise one content-free diagnostic for an ambiguous dirty submodule.

        Args:
            submodule_root: Exact initialized submodule root.
            status_by_path_map: NUL-safe porcelain status by path.

        Raises:
            WorktreeError: Always, with branch and changed paths.
        """

        branch_name = self._git_command.run(
            submodule_root,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            check=False,
        ).stdout.strip()
        raise WorktreeError(
            f"Dirty submodule drift is ambiguous: {submodule_root}; "
            f"branch={branch_name or 'detached'}; "
            f"changed_path_list={json.dumps(sorted(status_by_path_map), ensure_ascii=True)}"
        )

    def _pending_temporary_exclude_marker_path_get(self, main_root: Path, task_root: Path) -> Path:
        """Return one safe common-administration marker used before worktree creation.

        Args:
            main_root: Main-worktree root.
            task_root: Exact future or existing task-worktree root.

        Returns:
            Exact pending-marker path.
        """

        marker_identity = hashlib.sha256(os.fsencode(f"{self._prefix}\0{task_root}")).hexdigest()
        marker_path = (
            self._git_common_directory_get(main_root)
            / PRIVATE_STATE_DIRECTORY_NAME
            / "pending"
            / f"{marker_identity}-{TEMPORARY_EXCLUDE_MARKER_FILENAME}"
        )
        current_path = self._git_common_directory_get(main_root)
        for path_part in marker_path.relative_to(current_path).parts[:-1]:
            current_path /= path_part
            if os.path.lexists(current_path) and (current_path.is_symlink() or not current_path.is_dir()):
                raise WorktreeError(f"Pending private Git path has an unsafe parent: {current_path}")
        return marker_path

    def _temporary_exclude_precreate(self, main_root: Path, task_root: Path) -> bool:
        """Install recoverable local worktree-container ignore before `git worktree add`.

        Args:
            main_root: Main-worktree root.
            task_root: Exact future or existing task-worktree root.

        Returns:
            Whether provider-owned state requires the temporary exclude.
        """

        pending_marker_path = self._pending_temporary_exclude_marker_path_get(main_root, task_root)
        pending_marker_exists = os.path.lexists(pending_marker_path)
        if pending_marker_exists and (pending_marker_path.is_symlink() or not pending_marker_path.is_file()):
            raise WorktreeError(
                f"Pending temporary-exclude marker is not one physical ordinary file: {pending_marker_path}"
            )
        if (
            pending_marker_exists
            and self._utf8_text_get(
                pending_marker_path,
                "Pending temporary-exclude marker",
            )
            != f"{IGNORE_WORKTREE_PATTERN}\n"
        ):
            raise WorktreeError(f"Pending temporary-exclude marker is invalid: {pending_marker_path}")
        if not pending_marker_exists and self._is_tracked_ignore_match(
            main_root,
            PurePosixPath(WORKTREE_CONTAINER_NAME),
        ):
            return False
        exclude_path = self._local_exclude_path_get(main_root)
        existing_text = self._utf8_text_get(exclude_path, "Git exclude file") if exclude_path.is_file() else ""
        if pending_marker_exists:
            if IGNORE_WORKTREE_PATTERN not in set(existing_text.splitlines()):
                prefix = "" if not existing_text or existing_text.endswith("\n") else "\n"
                self._ordinary_text_atomic_write(
                    main_root,
                    exclude_path,
                    f"{existing_text}{prefix}{IGNORE_WORKTREE_PATTERN}\n",
                )
            return True
        if IGNORE_WORKTREE_PATTERN in set(existing_text.splitlines()):
            return False
        self._private_text_atomic_write(pending_marker_path, f"{IGNORE_WORKTREE_PATTERN}\n")
        prefix = "" if not existing_text or existing_text.endswith("\n") else "\n"
        self._ordinary_text_atomic_write(
            main_root,
            exclude_path,
            f"{existing_text}{prefix}{IGNORE_WORKTREE_PATTERN}\n",
        )
        return True

    def _temporary_exclude_prepare(
        self,
        main_root: Path,
        task_root: Path,
        owns_temporary_exclude: bool,
    ) -> list[str]:
        """Finalize and record the pre-created local exclude in task administration.

        Args:
            main_root: Main-worktree root.
            task_root: Exact task-worktree root that owns recovery metadata.
            owns_temporary_exclude: Whether pre-creation state proves provider ownership.

        Returns:
            Provider-owned temporary exclude list.
        """

        marker_path = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / TEMPORARY_EXCLUDE_MARKER_FILENAME,
        )
        marker_exists = os.path.lexists(marker_path)
        if marker_exists and (marker_path.is_symlink() or not marker_path.is_file()):
            raise WorktreeError(f"Temporary-exclude ownership marker is not one physical ordinary file: {marker_path}")
        if (
            marker_exists
            and self._utf8_text_get(
                marker_path,
                "Temporary-exclude ownership marker",
            )
            != f"{IGNORE_WORKTREE_PATTERN}\n"
        ):
            raise WorktreeError(f"Temporary-exclude ownership marker is invalid: {marker_path}")
        pending_marker_path = self._pending_temporary_exclude_marker_path_get(main_root, task_root)
        if os.path.lexists(pending_marker_path) and (
            pending_marker_path.is_symlink() or not pending_marker_path.is_file()
        ):
            raise WorktreeError(
                f"Pending temporary-exclude marker is not one physical ordinary file: {pending_marker_path}"
            )
        if (
            pending_marker_path.is_file()
            and self._utf8_text_get(
                pending_marker_path,
                "Pending temporary-exclude marker",
            )
            != f"{IGNORE_WORKTREE_PATTERN}\n"
        ):
            raise WorktreeError(f"Pending temporary-exclude marker is invalid: {pending_marker_path}")
        owns_temporary_exclude = owns_temporary_exclude or marker_exists or os.path.lexists(pending_marker_path)
        if not owns_temporary_exclude:
            return []
        exclude_path = self._local_exclude_path_get(main_root)
        existing_text = self._utf8_text_get(exclude_path, "Git exclude file") if exclude_path.is_file() else ""
        existing_line_set = set(existing_text.splitlines())
        self._private_text_atomic_write(marker_path, f"{IGNORE_WORKTREE_PATTERN}\n")
        if IGNORE_WORKTREE_PATTERN not in existing_line_set:
            prefix = "" if not existing_text or existing_text.endswith("\n") else "\n"
            self._ordinary_text_atomic_write(
                main_root,
                exclude_path,
                f"{existing_text}{prefix}{IGNORE_WORKTREE_PATTERN}\n",
            )
        self._path_remove(pending_marker_path)
        return [IGNORE_WORKTREE_PATTERN]

    def _local_exclude_path_get(self, main_root: Path) -> Path:
        """Return one physical repository-local Git exclude file path.

        Args:
            main_root: Exact main-worktree root.

        Returns:
            Validated common-Git-directory exclude path.
        """

        info_directory = self._git_common_directory_get(main_root) / "info"
        if os.path.lexists(info_directory) and (info_directory.is_symlink() or not info_directory.is_dir()):
            raise WorktreeError(f"Git exclude owner is not one physical directory: {info_directory}")
        info_directory.mkdir(parents=True, exist_ok=True)
        exclude_path = info_directory / "exclude"
        if os.path.lexists(exclude_path) and (exclude_path.is_symlink() or not exclude_path.is_file()):
            raise WorktreeError(f"Git exclude file is not one physical ordinary file: {exclude_path}")
        return exclude_path

    def _temporary_exclude_marker_restore(self, task_root: Path) -> bool:
        """Restore the private ownership marker for one recorded local exclude.

        Args:
            task_root: Exact task-worktree root.

        Returns:
            Whether the marker required a deterministic repair.
        """

        marker_path = self._git_path_get(
            task_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / TEMPORARY_EXCLUDE_MARKER_FILENAME,
        )
        if os.path.lexists(marker_path) and (marker_path.is_symlink() or not marker_path.is_file()):
            raise WorktreeError(f"Temporary-exclude ownership marker is not one physical ordinary file: {marker_path}")
        expected_text = f"{IGNORE_WORKTREE_PATTERN}\n"
        marker_text = (
            self._utf8_text_get(
                marker_path,
                "Temporary-exclude ownership marker",
            )
            if marker_path.is_file()
            else None
        )
        if marker_text is not None and marker_text != expected_text:
            raise WorktreeError(f"Temporary-exclude ownership marker is invalid: {marker_path}")
        if marker_text == expected_text:
            return False
        self._private_text_atomic_write(marker_path, expected_text)
        return True

    def _tracked_ignore_complete_validate(self, state: WorktreeState) -> None:
        """Verify durable tracked ignore behavior before sealing.

        Args:
            state: Complete workflow state.
        """

        if not self._is_tracked_ignore_match(self._coordinating_repository, PurePosixPath(".spec")):
            raise WorktreeError(
                f"Tracked ignore rules do not cover the physical artifact directory "
                f"{self._coordinating_repository / '.spec'}"
            )
        for repository_state in state["repository_state_list"]:
            task_root = Path(repository_state["task_root"])
            path_list = [
                PurePosixPath(".spec"),
                PurePosixPath(WORKTREE_CONTAINER_NAME),
                *[PurePosixPath(item["path"]) for item in repository_state["resource_state_list"]],
            ]
            for relative_path in path_list:
                if not self._is_tracked_ignore_match(task_root, relative_path):
                    raise WorktreeError(f"Tracked ignore rules do not cover {task_root / relative_path}")

    def _tracked_ignore_prepare(self, task_root: Path, path_list: list[PurePosixPath]) -> list[str]:
        """Author minimum durable ignore rules for prepared objects.

        Args:
            task_root: Task-worktree root.
            path_list: Root-relative objects that must be ignored.

        Returns:
            Exact patterns added to the project ignore file.
        """

        gitignore_path = task_root / ".gitignore"
        gitignore_text, expected_gitignore_text, pattern_list = self._tracked_ignore_text_get(
            task_root,
            path_list,
        )
        if not pattern_list:
            return []
        self._ordinary_text_atomic_write(
            task_root,
            gitignore_path,
            expected_gitignore_text,
        )
        for relative_path in path_list:
            if not self._is_tracked_ignore_match(task_root, relative_path):
                raise WorktreeError(f"Cannot author tracked ignore rule for {task_root / relative_path}")
        return pattern_list

    def _tracked_ignore_text_get(
        self,
        task_root: Path,
        path_list: list[PurePosixPath],
    ) -> tuple[str, str, list[str]]:
        """Return current text, exact provider result, and newly required patterns.

        Args:
            task_root: Task-worktree root.
            path_list: Root-relative objects that must be ignored.

        Returns:
            Current text, expected text, and ordered patterns to add.
        """

        gitignore_path = task_root / ".gitignore"
        if os.path.lexists(gitignore_path) and (gitignore_path.is_symlink() or not gitignore_path.is_file()):
            raise WorktreeError(f"Project ignore owner must be one physical ordinary file: {gitignore_path}")
        gitignore_text = self._utf8_text_get(gitignore_path, "Project ignore file") if gitignore_path.is_file() else ""
        gitignore_line_list = gitignore_text.splitlines()
        pattern_list: list[str] = []
        for relative_path in path_list:
            pattern = self._gitignore_pattern_get(relative_path)
            positive_line_index = max(
                (index for index, line_text in enumerate(gitignore_line_list) if line_text == pattern),
                default=-1,
            )
            negative_line_index = max(
                (index for index, line_text in enumerate(gitignore_line_list) if line_text == f"!{pattern}"),
                default=-1,
            )
            if negative_line_index <= positive_line_index and self._is_tracked_ignore_match(task_root, relative_path):
                continue
            if pattern not in pattern_list:
                pattern_list.append(pattern)
        if not pattern_list:
            return gitignore_text, gitignore_text, []
        prefix = "" if not gitignore_text or gitignore_text.endswith("\n") else "\n"
        appended_text = "".join(f"{pattern}\n" for pattern in pattern_list)
        return gitignore_text, f"{gitignore_text}{prefix}{appended_text}", pattern_list

    def _gitignore_pattern_get(self, relative_path: PurePosixPath) -> str:
        """Return one root-anchored literal pattern for a validated exact path.

        Args:
            relative_path: Validated repository-relative path.

        Returns:
            Gitignore pattern with terminal spaces escaped.
        """

        if relative_path == PurePosixPath(WORKTREE_CONTAINER_NAME):
            return IGNORE_WORKTREE_PATTERN
        path_text = relative_path.as_posix()
        trailing_space_count = len(path_text) - len(path_text.rstrip(" "))
        if not trailing_space_count:
            return f"/{path_text}"
        escaped_trailing_space_text = "\\ " * trailing_space_count
        return f"/{path_text[:-trailing_space_count]}{escaped_trailing_space_text}"

    def _is_tracked_ignore_match(self, task_root: Path, relative_path: PurePosixPath) -> bool:
        """Return whether a tracked ignore file covers one root-relative path.

        Args:
            task_root: Task-worktree root.
            relative_path: Path to inspect.

        Returns:
            True when one tracked ignore file supplies the effective match.
        """

        probe_path = relative_path
        if relative_path == PurePosixPath(WORKTREE_CONTAINER_NAME):
            probe_path = relative_path / "goal-brainstorm-ignore-probe"
        result = self._git_command.run(
            task_root,
            ["check-ignore", "-z", "-v", "--no-index", "--stdin"],
            check=False,
            input_text=f"./{probe_path.as_posix()}\0",
        )
        if result.returncode != 0 or not result.stdout:
            return False
        output_field_list = result.stdout.split("\0")
        if len(output_field_list) != 5 or output_field_list[-1] != "":
            raise WorktreeError(f"Cannot parse tracked ignore match for {task_root / relative_path}")
        source_text, _, pattern_text, _, _ = output_field_list
        if pattern_text.startswith("!"):
            return False
        source_path = Path(source_text)
        if not source_path.is_absolute():
            source_path = task_root / source_path
        if not source_path.is_file():
            return False
        try:
            source_relative_path = source_path.resolve().relative_to(task_root)
        except ValueError:
            return False
        if (
            self._git_command.run(
                task_root,
                ["ls-files", "--error-unmatch", "--", source_relative_path.as_posix()],
                check=False,
            ).returncode
            == 0
        ):
            return True
        if source_relative_path != Path(".gitignore"):
            return False
        status_text = self._git_command.run(
            task_root,
            [
                "-c",
                "core.fileMode=true",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                source_relative_path.as_posix(),
            ],
        ).stdout
        return status_text.startswith("?? .gitignore")

    def _repository_state_validate(
        self,
        repository_state: RepositoryState,
        performed_repair_list: list[str],
    ) -> None:
        """Validate and repair one participating repository.

        Args:
            repository_state: Recorded repository state.
            performed_repair_list: Mutable repair report.
        """

        main_root = Path(repository_state["main_root"]).resolve()
        task_root = Path(repository_state["task_root"]).resolve()
        previous_materialized_resource_path_set = self._materialized_resource_path_set_get(repository_state)
        if self._repository_root_validate(main_root) != main_root:
            raise WorktreeError(f"Main repository root changed: {main_root}")
        if str(self._git_common_directory_get(main_root)) != repository_state["common_git_directory"]:
            raise WorktreeError(f"Git common directory changed for {main_root}")
        worktree_by_path_map = self._worktree_by_path_map_get(main_root)
        worktree_record = worktree_by_path_map.get(str(task_root))
        if worktree_record is None:
            if task_root.is_dir():
                self._worktree_registration_repair_preflight(
                    repository_state,
                    task_root,
                )
                self._git_command.run(main_root, ["worktree", "repair", str(task_root)], check=False)
                performed_repair_list.append(f"repaired worktree registration: {task_root}")
                worktree_record = self._worktree_by_path_map_get(main_root).get(str(task_root))
            if worktree_record is None:
                raise WorktreeError(f"Task worktree is not registered: {task_root}")
        try:
            self._registered_worktree_identity_validate(
                main_root,
                task_root,
                worktree_record,
            )
        except WorktreeError:
            self._registered_worktree_identity_repair(
                main_root,
                task_root,
                worktree_record,
                repository_state,
                performed_repair_list,
            )
        if worktree_record["branch_name"] != repository_state["branch_name"]:
            raise WorktreeError(f"Task worktree branch changed: {task_root}")
        task_commit = self._git_command.run(task_root, ["rev-parse", "HEAD"]).stdout.strip()
        if task_commit != worktree_record["head"]:
            raise WorktreeError(f"Task worktree registration HEAD is inconsistent: {task_root}")
        if (
            self._git_command.run(
                task_root,
                ["merge-base", "--is-ancestor", repository_state["baseline_commit"], task_commit],
                check=False,
            ).returncode
            != 0
        ):
            raise WorktreeError(f"Task branch no longer descends from its recorded baseline: {task_root}")
        if (
            Path(self._git_command.run(task_root, ["rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
            != task_root
        ):
            raise WorktreeError(f"Task command root is not exact: {task_root}")
        self._specification_link_collision_preflight(
            task_root,
            allow_incorrect_link_repair=True,
        )
        self._ordinary_text_atomic_write_list_reconcile(
            main_root,
            performed_repair_list,
        )
        self._ordinary_text_atomic_write_list_reconcile(
            task_root,
            performed_repair_list,
        )
        self._resource_transaction_owner_reconcile(
            task_root,
            {item["path"]: item for item in repository_state["resource_state_list"]},
            performed_repair_list,
        )
        for temporary_exclude in repository_state["temporary_exclude_list"]:
            if self._temporary_exclude_marker_restore(task_root):
                performed_repair_list.append(f"restored temporary-exclude ownership marker: {task_root}")
            exclude_path = self._local_exclude_path_get(main_root)
            exclude_text = self._utf8_text_get(exclude_path, "Git exclude file") if exclude_path.is_file() else ""
            if temporary_exclude not in exclude_text.splitlines():
                prefix = "" if not exclude_text or exclude_text.endswith("\n") else "\n"
                self._ordinary_text_atomic_write(
                    main_root,
                    exclude_path,
                    f"{exclude_text}{prefix}{temporary_exclude}\n",
                )
                performed_repair_list.append(f"restored local exclude {temporary_exclude}: {main_root}")
        manifest_path = task_root / MANIFEST_NAME
        self._initial_manifest_owner_backfill_if_proven(
            task_root,
            repository_state["baseline_commit"],
            repository_state["manifest_fingerprint"],
            repository_state["resource_state_list"],
            performed_repair_list,
        )
        if not os.path.lexists(manifest_path):
            if repository_state["resource_state_list"]:
                raise WorktreeError(f"Classified bootstrap manifest is missing and cannot be inferred: {manifest_path}")
            self._initial_manifest_restore(
                task_root,
                performed_repair_list,
                report_text="restored provider-owned initial manifest",
            )
        else:
            self._initial_manifest_owner_retire_if_changed(
                task_root,
                performed_repair_list,
            )
        resource_by_class_map = self._manifest_get(manifest_path, task_root)
        current_resource_path_set = {
            path_text for path_list in resource_by_class_map.values() for path_text in path_list
        }
        self._resource_source_preimage_owner_reconcile(
            task_root,
            repository_state["resource_state_list"],
            performed_repair_list,
            allowed_path_set=current_resource_path_set,
            retire_obsolete=False,
        )
        required_ignore_path_list = [
            PurePosixPath(".spec"),
            PurePosixPath(WORKTREE_CONTAINER_NAME),
            *[
                PurePosixPath(path_text)
                for path_text in sorted(
                    {path_text for path_list in resource_by_class_map.values() for path_text in path_list}
                )
            ],
        ]
        for added_pattern in self._tracked_ignore_prepare(task_root, required_ignore_path_list):
            performed_repair_list.append(f"restored tracked ignore pattern {added_pattern}: {task_root}")
        self._specification_link_prepare(
            task_root,
            performed_repair_list,
            allow_incorrect_link_repair=True,
        )
        manifest_fingerprint = self._path_fingerprint_get(manifest_path)
        if manifest_fingerprint != repository_state["manifest_fingerprint"]:
            skipped_optional_resource_list: list[str] = []
            previous_resource_state_list = repository_state["resource_state_list"]
            repository_state["resource_state_list"] = self._resource_state_list_prepare(
                main_root,
                resource_by_class_map,
                task_root,
                performed_repair_list,
                skipped_optional_resource_list,
                previous_resource_state_list,
            )
            repository_state["manifest_fingerprint"] = manifest_fingerprint
            performed_repair_list.append(f"applied changed bootstrap manifest: {manifest_path}")
        self._resource_state_manifest_validate(resource_by_class_map, repository_state, manifest_path)
        self._resource_unexposed_staging_list_repair(
            main_root=main_root,
            task_root=task_root,
            resource_by_class_map=resource_by_class_map,
            resource_state_list=repository_state["resource_state_list"],
            performed_repair_list=performed_repair_list,
        )
        self._resource_main_leak_list_recover(
            repository_state,
            performed_repair_list,
        )
        self._resource_state_list_validate(
            main_root,
            repository_state["resource_state_list"],
            task_root,
            performed_repair_list,
        )
        self._submodule_state_validate(repository_state, task_root, performed_repair_list)
        self._participating_submodule_state_list_validate(
            main_root,
            repository_state,
            task_root,
            performed_repair_list,
        )
        newly_materialized_resource_path_set = (
            self._materialized_resource_path_set_get(repository_state) - previous_materialized_resource_path_set
        )
        self._main_state_validate(
            main_root,
            repository_state,
            task_root,
            performed_repair_list,
            delegated_submodule_path_set={
                item["path"] for item in repository_state["participating_submodule_state_list"]
            },
            newly_materialized_resource_path_set=newly_materialized_resource_path_set,
        )

    def _worktree_registration_repair_preflight(
        self,
        repository_state: RepositoryState,
        task_root: Path,
    ) -> None:
        """Prove an unregistered directory is the intact recorded task checkout.

        Args:
            repository_state: Durable ownership and branch identity.
            task_root: Formerly registered exact task path.
        """

        git_pointer_path = task_root / ".git"
        if git_pointer_path.is_symlink() or not git_pointer_path.is_file():
            raise WorktreeError(f"Unregistered task path is not an intact linked worktree: {task_root}")
        git_pointer_text = self._utf8_text_get(
            git_pointer_path,
            "Linked-worktree Git pointer",
        ).strip()
        prefix = "gitdir: "
        if not git_pointer_text.startswith(prefix):
            raise WorktreeError(f"Unregistered task path has no valid Git pointer: {task_root}")
        administration_path = Path(git_pointer_text.removeprefix(prefix))
        if not administration_path.is_absolute():
            administration_path = task_root / administration_path
        try:
            resolved_administration_path = administration_path.resolve(strict=True)
            resolved_administration_path.relative_to(Path(repository_state["common_git_directory"]) / "worktrees")
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise WorktreeError(f"Unregistered task Git pointer is not provider-owned: {task_root}") from exc
        top_level_result = self._git_command.run(
            task_root,
            ["rev-parse", "--show-toplevel"],
            check=False,
        )
        common_directory_result = self._git_command.run(
            task_root,
            ["rev-parse", "--git-common-dir"],
            check=False,
        )
        branch_result = self._git_command.run(
            task_root,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            check=False,
        )
        head_result = self._git_command.run(
            task_root,
            ["rev-parse", "HEAD"],
            check=False,
        )
        if (
            top_level_result.returncode != 0
            or Path(top_level_result.stdout.strip()).resolve() != task_root
            or common_directory_result.returncode != 0
            or (
                Path(common_directory_result.stdout.strip())
                if Path(common_directory_result.stdout.strip()).is_absolute()
                else task_root / common_directory_result.stdout.strip()
            ).resolve()
            != Path(repository_state["common_git_directory"])
            or branch_result.returncode != 0
            or branch_result.stdout.strip() != repository_state["branch_name"]
            or head_result.returncode != 0
            or (
                self._git_command.run(
                    task_root,
                    [
                        "merge-base",
                        "--is-ancestor",
                        repository_state["baseline_commit"],
                        head_result.stdout.strip(),
                    ],
                    check=False,
                ).returncode
                != 0
            )
        ):
            raise WorktreeError(f"Unregistered task path does not match recorded ownership: {task_root}")

    def _resource_task_path_fingerprint_get(
        self,
        main_owner_root: Path,
        task_owner_root: Path,
        owner_relative_path_text: str,
        resource_state: ResourceState,
        resource_boundary_text: str,
    ) -> str:
        """Fingerprint one task resource path using its effective object identity.

        Args:
            main_owner_root: Main root that owns the resource source.
            task_owner_root: Task root that owns the materialized destination.
            owner_relative_path_text: Candidate path relative to both owner roots.
            resource_state: Recorded resource classification.
            resource_boundary_text: Root-relative resource boundary.

        Returns:
            Fingerprint of the task's effective resource object.
        """

        task_path = task_owner_root / owner_relative_path_text
        if resource_state["strategy"] != "link":
            return self._path_fingerprint_get(task_path)
        main_boundary_path = main_owner_root / resource_boundary_text
        task_boundary_path = task_owner_root / resource_boundary_text
        expected_target = os.path.relpath(main_boundary_path, start=task_boundary_path.parent)
        if not task_boundary_path.is_symlink() or os.readlink(task_boundary_path) != expected_target:
            raise WorktreeError(f"Main-leak link resource target changed: {task_boundary_path}")
        if owner_relative_path_text != resource_boundary_text:
            return self._path_fingerprint_get(task_path)
        if not os.path.lexists(main_boundary_path):
            return "absent"
        try:
            resolved_task_path = task_boundary_path.resolve(strict=True)
            resolved_main_path = main_boundary_path.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise WorktreeError(f"Main-leak link resource target is unresolved: {task_boundary_path}") from exc
        if resolved_task_path != resolved_main_path:
            raise WorktreeError(f"Main-leak link resource target changed: {task_boundary_path}")
        return self._path_fingerprint_get(resolved_task_path)

    def _main_leak_recovery_preflight(
        self,
        repository_state: RepositoryState,
        performed_repair_list: list[str],
        *,
        prepare_transactions: bool = False,
    ) -> None:
        """Validate every recorded recovery before mutating any main path.

        Args:
            repository_state: Recorded repository and recovery provenance.
            performed_repair_list: Mutable repair report.
        """

        main_root = Path(repository_state["main_root"])
        task_root = Path(repository_state["task_root"])
        marker_by_path_map = repository_state["main_leak_fingerprint_by_path_map"]
        self._non_overlapping_path_set_validate(
            set(marker_by_path_map),
            "Recorded main-leak paths",
        )
        task_changed_path_set = self._task_changed_path_set_get(
            repository_state["baseline_commit"],
            task_root,
        )
        for path_text, recorded_fingerprint in sorted(marker_by_path_map.items()):
            resource_binding = self._resource_binding_optional_get(repository_state, path_text)
            if resource_binding is not None:
                (
                    main_owner_root,
                    task_owner_root,
                    owner_relative_path_text,
                    resource_state,
                    resource_boundary_text,
                ) = resource_binding
                self._main_leak_unexposed_staging_repair(
                    main_owner_root=main_owner_root,
                    path_text=owner_relative_path_text,
                    recorded_fingerprint=recorded_fingerprint,
                    task_root=task_root,
                    top_level_path_text=path_text,
                    performed_repair_list=performed_repair_list,
                )
                transaction = self._main_leak_transaction_optional_get(
                    task_root,
                    path_text,
                    performed_repair_list,
                )
                if transaction is None:
                    self._resource_main_leak_preflight(
                        path_text,
                        recorded_fingerprint,
                        resource_binding,
                    )
                elif self._index_entry_list_get(main_owner_root, owner_relative_path_text):
                    raise WorktreeError(
                        f"Main-leak recovery cannot mutate a staged resource source: "
                        f"{main_owner_root / owner_relative_path_text}"
                    )
                snapshot_root = (
                    self._resource_source_preimage_directory_get(
                        task_owner_root,
                        resource_boundary_text,
                    )
                    / "source"
                )
                if (
                    not os.path.lexists(snapshot_root)
                    or self._path_fingerprint_get(snapshot_root) != resource_state["source_fingerprint"]
                ):
                    raise WorktreeError(f"Private resource source preimage is unavailable or damaged: {snapshot_root}")
                descendant_path = PurePosixPath(owner_relative_path_text).relative_to(
                    PurePosixPath(resource_boundary_text)
                )
                target_source_path = (
                    snapshot_root
                    if descendant_path == PurePosixPath(".")
                    else snapshot_root / descendant_path.as_posix()
                )
                if prepare_transactions or transaction is not None:
                    self._main_leak_transaction_prepare(
                        index_managed=False,
                        index_target_entry_list=[],
                        main_owner_root=main_owner_root,
                        path_text=owner_relative_path_text,
                        recorded_fingerprint=recorded_fingerprint,
                        target_commit=None,
                        target_source_path=target_source_path,
                        task_root=task_root,
                        top_level_path_text=path_text,
                        performed_repair_list=performed_repair_list,
                    )
                continue
            if path_text not in task_changed_path_set:
                raise WorktreeError(
                    f"Recorded main-leak path is no longer changed by the task: {task_root / path_text}"
                )
            main_path = main_root / path_text
            task_path = task_root / path_text
            if path_text in self._index_nondefault_flag_by_path_map_get(main_root):
                raise WorktreeError(f"Main-leak recovery cannot preserve non-default index flags: {main_path}")
            preimage = repository_state["main_preimage_by_path_map"].get(path_text)
            accepted_index_entry_list = (
                preimage["index_entry_list"]
                if preimage is not None
                else self._commit_index_entry_list_get(
                    main_root,
                    repository_state["main_commit"],
                    path_text,
                )
            )
            if any(self._index_entry_fields_get(entry_text)[0] == "160000" for entry_text in accepted_index_entry_list):
                raise WorktreeError(f"Main-leak recovery cannot mutate a recorded submodule boundary: {main_path}")
            self._main_leak_unexposed_staging_repair(
                main_owner_root=main_root,
                path_text=path_text,
                recorded_fingerprint=recorded_fingerprint,
                task_root=task_root,
                top_level_path_text=path_text,
                performed_repair_list=performed_repair_list,
            )
            transaction = self._main_leak_transaction_optional_get(
                task_root,
                path_text,
                performed_repair_list,
            )
            if transaction is None:
                if (
                    self._path_fingerprint_get(main_path) != self._path_fingerprint_get(task_path)
                    or self._path_git_state_fingerprint_get(main_root, path_text) != recorded_fingerprint
                ):
                    raise WorktreeError(f"Recorded main-leak Git state changed before recovery: {main_path}")
                current_index_entry_list = self._index_entry_list_get(main_root, path_text)
                task_working_index_entry_list = self._working_object_index_entry_list_get(task_root, path_text)
                if tuple(current_index_entry_list) not in {
                    tuple(accepted_index_entry_list),
                    tuple(task_working_index_entry_list),
                }:
                    raise WorktreeError(
                        f"Main-leak path index differs from both the accepted preimage and exact task object: "
                        f"{main_path}"
                    )
            if path_text in repository_state["main_status_by_path_map"]:
                if preimage is None:
                    raise WorktreeError(f"Recorded main preimage is absent: {main_path}")
                target_source_path = self._main_path_preimage_restore_preflight(
                    main_root,
                    path_text,
                    preimage,
                    task_root,
                    performed_repair_list,
                )
                target_commit = None
            else:
                self._main_clean_path_restore_preflight(main_root, path_text)
                target_source_path = None
                target_commit = "HEAD"
                accepted_index_entry_list = self._commit_index_entry_list_get(
                    main_root,
                    target_commit,
                    path_text,
                )
            if prepare_transactions or transaction is not None:
                self._main_leak_transaction_prepare(
                    index_managed=True,
                    index_target_entry_list=accepted_index_entry_list,
                    main_owner_root=main_root,
                    path_text=path_text,
                    recorded_fingerprint=recorded_fingerprint,
                    target_commit=target_commit,
                    target_source_path=target_source_path,
                    task_root=task_root,
                    top_level_path_text=path_text,
                    performed_repair_list=performed_repair_list,
                )

    def _resource_main_leak_preflight(
        self,
        top_level_path_text: str,
        recorded_fingerprint: str,
        resource_binding: tuple[Path, Path, str, ResourceState, str],
    ) -> None:
        """Validate one resource-source recovery without changing its owner.

        Args:
            top_level_path_text: Path recorded in the top-level recovery map.
            recorded_fingerprint: Exact recorded main Git-state fingerprint.
            resource_binding: Resolved main/task resource ownership.
        """

        (
            main_owner_root,
            task_owner_root,
            owner_relative_path_text,
            resource_state,
            resource_boundary_text,
        ) = resource_binding
        main_path = main_owner_root / owner_relative_path_text
        task_fingerprint = self._resource_task_path_fingerprint_get(
            main_owner_root,
            task_owner_root,
            owner_relative_path_text,
            resource_state,
            resource_boundary_text,
        )
        if (
            self._path_fingerprint_get(main_path) != task_fingerprint
            or self._path_git_state_fingerprint_get(main_owner_root, owner_relative_path_text) != recorded_fingerprint
        ):
            raise WorktreeError(f"Recorded resource main-leak Git state changed before recovery: {main_path}")
        if self._index_entry_list_get(main_owner_root, owner_relative_path_text):
            raise WorktreeError(f"Main-leak recovery cannot mutate a staged resource source: {main_path}")
        snapshot_directory = self._resource_source_preimage_directory_get(
            task_owner_root,
            resource_boundary_text,
        )
        snapshot_root = snapshot_directory / "source"
        if (
            not os.path.lexists(snapshot_root)
            or self._path_fingerprint_get(snapshot_root) != resource_state["source_fingerprint"]
        ):
            raise WorktreeError(f"Private resource source preimage is unavailable or damaged: {snapshot_root}")
        resource_boundary_path = PurePosixPath(resource_boundary_text)
        owner_relative_path = PurePosixPath(owner_relative_path_text)
        try:
            owner_relative_path.relative_to(resource_boundary_path)
        except ValueError as exc:
            raise WorktreeError(
                f"Recorded resource main-leak path escaped its boundary: {top_level_path_text}"
            ) from exc
        self._path_parent_boundary_validate(
            main_owner_root,
            main_path,
            "Resource main-leak recovery destination",
        )

    def _resource_main_leak_list_recover(
        self,
        repository_state: RepositoryState,
        performed_repair_list: list[str],
    ) -> None:
        """Restore caller-attested leaks inside materialized resource boundaries.

        Args:
            repository_state: Recorded repository and resource ownership.
            performed_repair_list: Mutable repair report.
        """

        marker_by_path_map = repository_state["main_leak_fingerprint_by_path_map"]
        self._main_leak_recovery_preflight(
            repository_state,
            performed_repair_list,
            prepare_transactions=True,
        )
        for path_text in sorted(marker_by_path_map):
            resource_binding = self._resource_binding_optional_get(repository_state, path_text)
            if resource_binding is None:
                continue
            transaction = self._main_leak_transaction_optional_get(
                Path(repository_state["task_root"]),
                path_text,
                performed_repair_list,
            )
            if transaction is None:
                raise WorktreeError(f"Prepared resource main-leak transaction is missing: {path_text}")
            self._main_leak_transaction_apply(
                Path(repository_state["task_root"]),
                transaction,
                performed_repair_list,
            )
            main_path = Path(transaction["main_owner_root"]) / transaction["path"]
            del marker_by_path_map[path_text]
            performed_repair_list.append(f"restored resource source preimage for duplicated task patch: {main_path}")

    def _resource_state_manifest_validate(
        self,
        resource_by_class_map: dict[str, list[str]],
        repository_state: RepositoryState,
        manifest_path: Path,
    ) -> None:
        """Verify that private resource state exactly matches its manifest.

        Args:
            resource_by_class_map: Validated manifest paths by class.
            repository_state: Recorded repository state.
            manifest_path: Manifest path used for diagnostics.
        """

        self._resource_state_manifest_list_validate(
            resource_by_class_map,
            repository_state["resource_state_list"],
            manifest_path,
        )

    def _resource_unexposed_staging_list_repair(
        self,
        *,
        main_root: Path,
        task_root: Path,
        resource_by_class_map: dict[str, list[str]],
        resource_state_list: list[ResourceState],
        performed_repair_list: list[str],
    ) -> None:
        """Repair pre-metadata staging for every currently declared resource."""

        resource_state_by_path_map = {item["path"]: item for item in resource_state_list}
        for resource_class, path_list in resource_by_class_map.items():
            strategy = "copy" if resource_class.startswith("copy_") else "link"
            for path_text in path_list:
                self._resource_unexposed_staging_repair(
                    main_root=main_root,
                    task_root=task_root,
                    path_text=path_text,
                    strategy=strategy,
                    previous_resource_state=resource_state_by_path_map.get(path_text),
                    performed_repair_list=performed_repair_list,
                )

    def _resource_state_manifest_list_validate(
        self,
        resource_by_class_map: dict[str, list[str]],
        resource_state_list: list[ResourceState],
        manifest_path: Path,
    ) -> None:
        """Verify that one resource-state list exactly matches its manifest.

        Args:
            resource_by_class_map: Validated resources by class.
            resource_state_list: Recorded resource state.
            manifest_path: Manifest path used for diagnostics.
        """

        expected_class_by_path_map: dict[str, tuple[str, bool]] = {}
        for resource_class, path_list in resource_by_class_map.items():
            strategy = "copy" if resource_class.startswith("copy_") else "link"
            required = "_required_" in resource_class
            for path_text in path_list:
                expected_class_by_path_map[path_text] = (strategy, required)
        actual_state_by_path_map = {item["path"]: item for item in resource_state_list}
        if set(actual_state_by_path_map) != set(expected_class_by_path_map):
            raise WorktreeError(f"Private resource paths do not match bootstrap manifest: {manifest_path}")
        for path_text, (strategy, required) in expected_class_by_path_map.items():
            resource_state = actual_state_by_path_map[path_text]
            if resource_state["strategy"] != strategy or resource_state["required"] != required:
                raise WorktreeError(f"Private resource class does not match bootstrap manifest: {manifest_path}")

    def _participating_submodule_state_list_validate(
        self,
        main_root: Path,
        repository_state: RepositoryState,
        task_root: Path,
        performed_repair_list: list[str],
    ) -> None:
        """Validate manifests and resources inside explicit task-owned submodules.

        Args:
            main_root: Top-level main-worktree source root.
            repository_state: Recorded top-level repository state.
            task_root: Exact top-level task-worktree root.
            performed_repair_list: Mutable repair report.
        """

        participating_submodule_path_set = {
            item["path"] for item in repository_state["participating_submodule_state_list"]
        }
        self._participating_submodule_path_set_validate(
            task_root,
            participating_submodule_path_set,
        )
        for submodule_state in repository_state["participating_submodule_state_list"]:
            path_text = submodule_state["path"]
            main_submodule_root = main_root / path_text
            task_submodule_root = task_root / path_text
            previous_materialized_resource_path_set = {
                item["path"] for item in submodule_state["resource_state_list"] if not item["skipped"]
            }
            self._ordinary_text_atomic_write_list_reconcile(
                task_submodule_root,
                performed_repair_list,
            )
            self._resource_transaction_owner_reconcile(
                task_submodule_root,
                {item["path"]: item for item in submodule_state["resource_state_list"]},
                performed_repair_list,
            )
            manifest_path = task_submodule_root / MANIFEST_NAME
            self._initial_manifest_owner_backfill_if_proven(
                task_submodule_root,
                submodule_state["baseline_commit"],
                submodule_state["manifest_fingerprint"],
                submodule_state["resource_state_list"],
                performed_repair_list,
            )
            if not os.path.lexists(manifest_path):
                if submodule_state["resource_state_list"]:
                    raise WorktreeError(
                        f"Classified task-owned submodule manifest is missing and cannot be inferred: "
                        f"{manifest_path}"
                    )
                self._initial_manifest_restore(
                    task_submodule_root,
                    performed_repair_list,
                    report_text="restored provider-owned initial task-owned submodule manifest",
                )
            else:
                self._initial_manifest_owner_retire_if_changed(
                    task_submodule_root,
                    performed_repair_list,
                )
            resource_by_class_map = self._manifest_get(manifest_path, task_submodule_root)
            current_resource_path_set = {
                resource_path_text for path_list in resource_by_class_map.values() for resource_path_text in path_list
            }
            self._resource_source_preimage_owner_reconcile(
                task_submodule_root,
                submodule_state["resource_state_list"],
                performed_repair_list,
                allowed_path_set=current_resource_path_set,
                retire_obsolete=False,
            )
            required_ignore_path_list = [
                PurePosixPath(resource_path_text)
                for resource_path_text in sorted(
                    {path for path_list in resource_by_class_map.values() for path in path_list}
                )
            ]
            for added_pattern in self._tracked_ignore_prepare(
                task_submodule_root,
                required_ignore_path_list,
            ):
                performed_repair_list.append(
                    f"restored task-owned submodule ignore pattern {added_pattern}: " f"{task_submodule_root}"
                )
            manifest_fingerprint = self._path_fingerprint_get(manifest_path)
            if manifest_fingerprint != submodule_state["manifest_fingerprint"]:
                skipped_optional_resource_list: list[str] = []
                previous_resource_state_list = submodule_state["resource_state_list"]
                submodule_state["resource_state_list"] = self._resource_state_list_prepare(
                    main_submodule_root,
                    resource_by_class_map,
                    task_submodule_root,
                    performed_repair_list,
                    skipped_optional_resource_list,
                    previous_resource_state_list,
                )
                submodule_state["manifest_fingerprint"] = manifest_fingerprint
                performed_repair_list.append(f"applied changed task-owned submodule manifest: {manifest_path}")
            self._resource_state_manifest_list_validate(
                resource_by_class_map,
                submodule_state["resource_state_list"],
                manifest_path,
            )
            self._resource_unexposed_staging_list_repair(
                main_root=main_submodule_root,
                task_root=task_submodule_root,
                resource_by_class_map=resource_by_class_map,
                resource_state_list=submodule_state["resource_state_list"],
                performed_repair_list=performed_repair_list,
            )
            self._resource_state_list_validate(
                main_submodule_root,
                submodule_state["resource_state_list"],
                task_submodule_root,
                performed_repair_list,
            )
            nested_repository_state = self._participating_submodule_repository_state_view(
                repository_state,
                submodule_state,
            )
            delegated_descendant_path_set = {
                PurePosixPath(candidate_state["path"]).relative_to(PurePosixPath(path_text)).as_posix()
                for candidate_state in repository_state["participating_submodule_state_list"]
                if candidate_state["path"] != path_text
                and PurePosixPath(path_text) in PurePosixPath(candidate_state["path"]).parents
            }
            current_materialized_resource_path_set = {
                item["path"] for item in submodule_state["resource_state_list"] if not item["skipped"]
            }
            self._resource_main_leak_list_recover(
                nested_repository_state,
                performed_repair_list,
            )
            self._main_state_validate(
                main_submodule_root,
                nested_repository_state,
                task_submodule_root,
                performed_repair_list,
                delegated_submodule_path_set=delegated_descendant_path_set,
                newly_materialized_resource_path_set=(
                    current_materialized_resource_path_set - previous_materialized_resource_path_set
                ),
            )
            self._participating_submodule_repository_state_view_apply(
                submodule_state,
                nested_repository_state,
            )

    def _participating_submodule_repository_state_view(
        self,
        repository_state: RepositoryState,
        submodule_state: ParticipatingSubmoduleState,
    ) -> RepositoryState:
        """Build one mutable repository-state view for nested main isolation."""

        main_root = Path(repository_state["main_root"]) / submodule_state["path"]
        task_root = Path(repository_state["task_root"]) / submodule_state["path"]
        return cast(
            RepositoryState,
            {
                "accepted_main_commit_drift_list": submodule_state["accepted_main_commit_drift_list"],
                "baseline_commit": submodule_state["baseline_commit"],
                "branch_name": self._prefix,
                "common_git_directory": str(self._git_common_directory_get(task_root)),
                "main_commit": submodule_state["main_commit"],
                "main_leak_fingerprint_by_path_map": submodule_state["main_leak_fingerprint_by_path_map"],
                "main_preimage_by_path_map": submodule_state["main_preimage_by_path_map"],
                "main_root": str(main_root),
                "main_status_by_path_map": submodule_state["main_status_by_path_map"],
                "main_status_fingerprint_by_path_map": submodule_state["main_status_fingerprint_by_path_map"],
                "manifest_fingerprint": submodule_state["manifest_fingerprint"],
                "participating_submodule_state_list": [],
                "resource_state_list": submodule_state["resource_state_list"],
                "submodule_commit_by_path_map": {},
                "task_root": str(task_root),
                "temporary_exclude_list": [],
            },
        )

    def _participating_submodule_repository_state_view_apply(
        self,
        submodule_state: ParticipatingSubmoduleState,
        repository_state: RepositoryState,
    ) -> None:
        """Persist mutable nested-main fields from one repository-state view."""

        submodule_state["accepted_main_commit_drift_list"] = repository_state["accepted_main_commit_drift_list"]
        submodule_state["main_commit"] = repository_state["main_commit"]
        submodule_state["main_leak_fingerprint_by_path_map"] = repository_state["main_leak_fingerprint_by_path_map"]
        submodule_state["main_preimage_by_path_map"] = repository_state["main_preimage_by_path_map"]
        submodule_state["main_status_by_path_map"] = repository_state["main_status_by_path_map"]
        submodule_state["main_status_fingerprint_by_path_map"] = repository_state["main_status_fingerprint_by_path_map"]

    def _resource_state_list_validate(
        self,
        main_root: Path,
        resource_state_list: list[ResourceState],
        task_root: Path,
        performed_repair_list: list[str],
    ) -> None:
        """Validate and repair prepared resource objects.

        Args:
            main_root: Main-worktree root.
            resource_state_list: Recorded resource state.
            task_root: Task-worktree root.
            performed_repair_list: Mutable repair report.
        """

        for resource_state in resource_state_list:
            source_path = main_root / resource_state["path"]
            destination_path = task_root / resource_state["path"]
            self._path_parent_boundary_validate(task_root, destination_path, "Resource destination")
            if resource_state["skipped"]:
                if os.path.lexists(source_path) or os.path.lexists(destination_path):
                    raise WorktreeError(f"Skipped optional resource state changed: {source_path}")
                continue
            self._source_boundary_validate(main_root, source_path)
            self._resource_source_preimage_prepare(
                task_root,
                resource_state["path"],
                source_path,
                resource_state["source_fingerprint"],
                performed_repair_list,
            )
            if resource_state["strategy"] == "link":
                if self._path_fingerprint_get(source_path) != resource_state["source_fingerprint"]:
                    raise WorktreeError(f"Shared link source changed during task execution: {source_path}")
                expected_target = os.path.relpath(source_path, start=destination_path.parent)
                if destination_path.is_symlink() and os.readlink(destination_path) == expected_target:
                    continue
                if destination_path.is_symlink():
                    destination_path.unlink()
                    destination_path.symlink_to(expected_target)
                    performed_repair_list.append(f"repaired link resource: {destination_path}")
                    continue
                if not os.path.lexists(destination_path):
                    self._resource_transaction_create(
                        task_root,
                        resource_state["path"],
                        source_path,
                        resource_state["source_fingerprint"],
                        "link",
                        performed_repair_list,
                    )
                    performed_repair_list.append(f"repaired link resource: {destination_path}")
                    continue
                raise WorktreeError(f"Link resource contains independent destination content: {destination_path}")
            if not os.path.lexists(destination_path):
                if self._path_fingerprint_get(source_path) != resource_state["source_fingerprint"]:
                    raise WorktreeError(f"Copy resource source changed during task execution: {source_path}")
                continue
            if destination_path.is_symlink():
                raise WorktreeError(f"Copy resource became a symbolic link: {destination_path}")
            self._path_copy_source_validate(destination_path, "Copy destination")
            if self._path_fingerprint_get(source_path) != resource_state["source_fingerprint"]:
                raise WorktreeError(f"Copy resource source changed during task execution: {source_path}")

    def _submodule_state_validate(
        self,
        repository_state: RepositoryState,
        task_root: Path,
        performed_repair_list: list[str],
    ) -> None:
        """Validate recursive submodules and repair clean gitlink drift.

        Args:
            repository_state: Recorded repository state.
            task_root: Task-worktree root.
            performed_repair_list: Mutable repair report.
        """

        if not (task_root / ".gitmodules").is_file():
            if (
                repository_state["submodule_commit_by_path_map"]
                or repository_state["participating_submodule_state_list"]
            ):
                raise WorktreeError(f"Recorded submodules lost their .gitmodules owner: {task_root}")
            return
        participating_submodule_path_set = {
            item["path"] for item in repository_state["participating_submodule_state_list"]
        }
        for path_text in repository_state["submodule_commit_by_path_map"]:
            submodule_root = task_root / path_text
            if os.path.lexists(submodule_root) and (
                submodule_root.is_symlink()
                or not submodule_root.is_dir()
                or Path(os.path.abspath(submodule_root)) != submodule_root.resolve()
            ):
                raise WorktreeError(f"Submodule root is not one physical repository boundary: {submodule_root}")
        url_configuration_fingerprint = self._submodule_url_configuration_fingerprint_get(task_root)
        self._git_command.run(task_root, ["submodule", "sync", "--recursive"])
        if self._submodule_url_configuration_fingerprint_get(task_root) != url_configuration_fingerprint:
            performed_repair_list.append(f"synchronized recursive submodule URLs: {task_root}")
        missing_initialized_submodule = False
        for path_text in repository_state["submodule_commit_by_path_map"]:
            submodule_root = task_root / path_text
            if not self._repository_is_exact_physical_root(submodule_root):
                missing_initialized_submodule = True
                break
        if missing_initialized_submodule:
            self._submodule_prepare(
                task_root,
                performed_repair_list,
                participating_submodule_path_set,
            )
        current_index_commit_by_path_map = self._submodule_index_commit_by_path_map_get(task_root)
        if set(current_index_commit_by_path_map) != set(repository_state["submodule_commit_by_path_map"]):
            raise WorktreeError(f"Recursive submodule set changed without preparation: {task_root}")
        for path_text, expected_commit in repository_state["submodule_commit_by_path_map"].items():
            submodule_root = task_root / path_text
            current_index_commit = current_index_commit_by_path_map[path_text]
            current_commit = self._git_command.run(submodule_root, ["rev-parse", "HEAD"]).stdout.strip()
            status_by_path_map = self._status_by_path_map_get(submodule_root)
            if path_text in participating_submodule_path_set:
                for candidate_commit, candidate_role in (
                    (current_index_commit, "index gitlink"),
                    (current_commit, "effective commit"),
                ):
                    if (
                        self._git_command.run(
                            submodule_root,
                            ["merge-base", "--is-ancestor", expected_commit, candidate_commit],
                            check=False,
                        ).returncode
                        != 0
                    ):
                        raise WorktreeError(
                            f"Task-owned submodule {candidate_role} no longer descends from its "
                            f"recorded baseline: {submodule_root}"
                        )
                continue
            if current_index_commit != expected_commit:
                raise WorktreeError(
                    f"Read-only submodule gitlink changed without task-owned classification: {submodule_root}"
                )
            if status_by_path_map:
                self._dirty_submodule_error_raise(submodule_root, status_by_path_map)
            if current_commit == expected_commit:
                continue
            self._submodule_prepare(
                task_root,
                performed_repair_list,
                participating_submodule_path_set,
            )
            if self._git_command.run(submodule_root, ["rev-parse", "HEAD"]).stdout.strip() != expected_commit:
                raise WorktreeError(f"Cannot return submodule to recorded gitlink: {submodule_root}")

    def _main_state_validate(
        self,
        main_root: Path,
        repository_state: RepositoryState,
        task_root: Path,
        performed_repair_list: list[str],
        *,
        delegated_submodule_path_set: set[str] | None = None,
        newly_materialized_resource_path_set: set[str],
    ) -> None:
        """Preserve main state and accept only independent non-overlapping drift.

        Args:
            main_root: Main-worktree root.
            repository_state: Recorded repository state.
            task_root: Task-worktree root.
            performed_repair_list: Mutable repair report.
            newly_materialized_resource_path_set: Resource boundaries explicitly adopted this pass.
        """

        delegated_submodule_path_set = delegated_submodule_path_set or set()
        delegated_leak_path_set = self._path_boundary_overlap_set_get(
            set(repository_state["main_leak_fingerprint_by_path_map"]),
            delegated_submodule_path_set,
        )
        if delegated_leak_path_set:
            raise WorktreeError(
                f"Top-level main-leak provenance crosses a delegated submodule boundary in "
                f"{main_root}: {', '.join(sorted(delegated_leak_path_set))}"
            )
        accepted_main_commit_path_set = {
            path_text
            for attestation in repository_state["accepted_main_commit_drift_list"]
            for path_text in attestation["path_list"]
        }
        delegated_accepted_path_set = self._path_boundary_overlap_set_get(
            accepted_main_commit_path_set,
            delegated_submodule_path_set,
        )
        if delegated_accepted_path_set:
            raise WorktreeError(
                f"Top-level main commit-drift attestation crosses a delegated submodule boundary in "
                f"{main_root}: {', '.join(sorted(delegated_accepted_path_set))}"
            )
        task_changed_path_set = self._task_changed_path_set_get(repository_state["baseline_commit"], task_root)
        task_changed_path_set -= self._path_boundary_overlap_set_get(
            task_changed_path_set,
            delegated_submodule_path_set,
        )
        for path_text in sorted(list(repository_state["main_leak_fingerprint_by_path_map"])):
            if self._resource_binding_optional_get(repository_state, path_text) is not None:
                raise WorktreeError(f"Resource main-leak recovery did not run before main validation: {path_text}")
            transaction = self._main_leak_transaction_optional_get(
                task_root,
                path_text,
                performed_repair_list,
            )
            if transaction is None:
                raise WorktreeError(f"Prepared main-leak transaction is missing: {task_root / path_text}")
            self._main_leak_transaction_apply(
                task_root,
                transaction,
                performed_repair_list,
            )
            del repository_state["main_leak_fingerprint_by_path_map"][path_text]
            performed_repair_list.append(f"restored main preimage for duplicated task patch: {main_root / path_text}")
        materialized_resource_path_set = self._materialized_resource_path_set_get(repository_state)
        materialized_resource_path_set -= self._path_boundary_overlap_set_get(
            materialized_resource_path_set,
            delegated_submodule_path_set,
        )
        newly_materialized_resource_path_set -= self._path_boundary_overlap_set_get(
            newly_materialized_resource_path_set,
            delegated_submodule_path_set,
        )
        previously_materialized_resource_path_set = (
            materialized_resource_path_set - newly_materialized_resource_path_set
        )
        ignored_main_path_set = self._ignored_untracked_path_set_get(
            main_root,
            task_changed_path_set,
        )
        ignored_resource_path_set = self._path_boundary_overlap_set_get(
            ignored_main_path_set,
            materialized_resource_path_set,
        )
        unclassified_ignored_overlap_set = ignored_main_path_set - ignored_resource_path_set
        if unclassified_ignored_overlap_set:
            raise WorktreeError(
                f"Ignored untracked main objects overlap current task paths in "
                f"{main_root}: {', '.join(sorted(unclassified_ignored_overlap_set))}"
            )
        protected_task_path_set = self._protected_task_path_set_get(
            task_changed_path_set,
            previously_materialized_resource_path_set,
        )
        current_main_commit = self._git_command.run(main_root, ["rev-parse", "HEAD"]).stdout.strip()
        if (
            self._git_command.run(
                main_root,
                ["merge-base", "--is-ancestor", repository_state["baseline_commit"], current_main_commit],
                check=False,
            ).returncode
            != 0
        ):
            raise WorktreeError(f"Main history no longer descends from the task baseline: {main_root}")
        accepted_main_commit_path_set = self._accepted_main_commit_path_set_get(
            main_root,
            repository_state,
            current_main_commit,
        )
        accumulated_commit_path_set = self._commit_changed_path_set_get(
            main_root,
            repository_state["baseline_commit"],
            current_main_commit,
        )
        accumulated_commit_path_set -= self._path_boundary_overlap_set_get(
            accumulated_commit_path_set,
            delegated_submodule_path_set,
        )
        accumulated_task_overlap_set = self._path_boundary_overlap_set_get(
            accumulated_commit_path_set,
            task_changed_path_set,
        )
        accumulated_task_overlap_set -= self._path_boundary_overlap_set_get(
            accumulated_task_overlap_set,
            accepted_main_commit_path_set,
        )
        if accumulated_task_overlap_set:
            raise WorktreeError(
                f"Accumulated main commit history overlaps current task paths in "
                f"{main_root}: {', '.join(sorted(accumulated_task_overlap_set))}"
            )
        if current_main_commit != repository_state["main_commit"]:
            if (
                self._git_command.run(
                    main_root,
                    ["merge-base", "--is-ancestor", repository_state["main_commit"], current_main_commit],
                    check=False,
                ).returncode
                != 0
            ):
                raise WorktreeError(f"Main history no longer descends from its recorded commit: {main_root}")
            changed_path_set = self._commit_changed_path_set_get(
                main_root,
                repository_state["main_commit"],
                current_main_commit,
            )
            changed_path_set -= self._path_boundary_overlap_set_get(
                changed_path_set,
                delegated_submodule_path_set,
            )
            overlap_set = self._path_boundary_overlap_set_get(changed_path_set, protected_task_path_set)
            overlap_set -= self._path_boundary_overlap_set_get(
                overlap_set,
                accepted_main_commit_path_set,
            )
            if overlap_set:
                raise WorktreeError(
                    f"Main commit drift overlaps task paths in {main_root}: {', '.join(sorted(overlap_set))}"
                )
            repository_state["main_commit"] = current_main_commit
            performed_repair_list.append(f"recorded independent main commit drift: {main_root}")
        current_status_by_path_map = self._status_by_path_map_get(main_root)
        for delegated_path_text in self._path_boundary_overlap_set_get(
            set(current_status_by_path_map),
            delegated_submodule_path_set,
        ):
            del current_status_by_path_map[delegated_path_text]
        current_fingerprint_by_path_map = {
            path_text: self._path_git_state_fingerprint_get(main_root, path_text)
            for path_text in current_status_by_path_map
        }
        previous_status_by_path_map = {
            path_text: status_text
            for path_text, status_text in repository_state["main_status_by_path_map"].items()
            if path_text
            not in self._path_boundary_overlap_set_get(
                {path_text},
                delegated_submodule_path_set,
            )
        }
        previous_fingerprint_by_path_map = {
            path_text: fingerprint
            for path_text, fingerprint in repository_state["main_status_fingerprint_by_path_map"].items()
            if path_text in previous_status_by_path_map
        }
        changed_status_path_set = {
            path_text
            for path_text in set(previous_status_by_path_map) | set(current_status_by_path_map)
            if previous_status_by_path_map.get(path_text) != current_status_by_path_map.get(path_text)
            or previous_fingerprint_by_path_map.get(path_text) != current_fingerprint_by_path_map.get(path_text)
        }
        accepted_working_state_commit_transition_set = self._accepted_main_working_state_commit_transition_set_get(
            accepted_main_commit_path_set,
            changed_status_path_set,
            current_status_by_path_map,
            main_root,
            previous_status_by_path_map,
            repository_state,
        )
        changed_status_path_set -= accepted_working_state_commit_transition_set
        resource_overlap_set = self._path_boundary_overlap_set_get(
            changed_status_path_set,
            previously_materialized_resource_path_set,
        )
        if resource_overlap_set:
            raise WorktreeError(
                f"Main working-state drift overlaps prepared resource paths in "
                f"{main_root}: {', '.join(sorted(resource_overlap_set))}"
            )
        overlap_set = self._path_boundary_overlap_set_get(changed_status_path_set, task_changed_path_set)
        if overlap_set:
            for path_text in sorted(overlap_set):
                main_fingerprint = self._path_fingerprint_get(main_root / path_text)
                task_fingerprint = self._path_fingerprint_get(task_root / path_text)
                recorded_leak_state_fingerprint = repository_state["main_leak_fingerprint_by_path_map"].get(path_text)
                if recorded_leak_state_fingerprint is None:
                    raise WorktreeError(
                        f"Main working-state drift overlaps task paths without recorded agent provenance in "
                        f"{main_root}: {path_text}"
                    )
                if (
                    main_fingerprint != task_fingerprint
                    or self._path_git_state_fingerprint_get(main_root, path_text) != recorded_leak_state_fingerprint
                ):
                    raise WorktreeError(
                        f"Recorded main-leak Git state changed before recovery in " f"{main_root}: {path_text}"
                    )
                if path_text in previous_status_by_path_map:
                    preimage = repository_state["main_preimage_by_path_map"].get(path_text)
                    if preimage is None:
                        raise WorktreeError(f"Recorded main preimage is absent: {main_root / path_text}")
                    self._main_path_preimage_restore(
                        main_root,
                        path_text,
                        preimage,
                        task_root,
                        performed_repair_list,
                    )
                else:
                    self._main_clean_path_restore(main_root, path_text)
                performed_repair_list.append(
                    f"restored main preimage for duplicated task patch: {main_root / path_text}"
                )
                del repository_state["main_leak_fingerprint_by_path_map"][path_text]
            current_status_by_path_map = self._status_by_path_map_get(main_root)
            current_fingerprint_by_path_map = {
                path_text: self._path_git_state_fingerprint_get(main_root, path_text)
                for path_text in current_status_by_path_map
            }
            for path_text in overlap_set:
                if current_status_by_path_map.get(path_text) != previous_status_by_path_map.get(
                    path_text
                ) or current_fingerprint_by_path_map.get(path_text) != previous_fingerprint_by_path_map.get(path_text):
                    raise WorktreeError(f"Cannot restore exact recorded main preimage: {main_root / path_text}")
        for path_text in sorted(changed_status_path_set - overlap_set):
            performed_repair_list.append(f"recorded independent main working-state drift: {main_root / path_text}")
        for path_text in sorted(repository_state["main_leak_fingerprint_by_path_map"]):
            if current_status_by_path_map.get(path_text) == previous_status_by_path_map.get(
                path_text
            ) and current_fingerprint_by_path_map.get(path_text) == previous_fingerprint_by_path_map.get(path_text):
                del repository_state["main_leak_fingerprint_by_path_map"][path_text]
                performed_repair_list.append(
                    f"finalized previously restored main-leak provenance: {main_root / path_text}"
                )
                continue
            raise WorktreeError(
                f"Recorded main-leak provenance no longer matches a recoverable overlap: " f"{main_root / path_text}"
            )
        current_task_overlap_set = self._path_boundary_overlap_set_get(
            set(current_status_by_path_map),
            task_changed_path_set,
        )
        if current_task_overlap_set:
            raise WorktreeError(
                f"Current dirty main state overlaps current task paths in "
                f"{main_root}: {', '.join(sorted(current_task_overlap_set))}"
            )
        repository_state["main_preimage_by_path_map"] = self._main_preimage_by_path_map_refresh(
            main_root,
            task_root,
            current_status_by_path_map,
            current_fingerprint_by_path_map,
            previous_status_by_path_map,
            previous_fingerprint_by_path_map,
            repository_state["main_preimage_by_path_map"],
            performed_repair_list,
        )
        repository_state["main_status_by_path_map"] = current_status_by_path_map
        repository_state["main_status_fingerprint_by_path_map"] = current_fingerprint_by_path_map
        for path_text in sorted(accepted_working_state_commit_transition_set):
            performed_repair_list.append(
                f"reconciled caller-attested main working state into commit: {main_root / path_text}"
            )

    def _accepted_main_working_state_commit_transition_set_get(
        self,
        accepted_main_commit_path_set: set[str],
        changed_status_path_set: set[str],
        current_status_by_path_map: dict[str, str],
        main_root: Path,
        previous_status_by_path_map: dict[str, str],
        repository_state: RepositoryState,
    ) -> set[str]:
        """Return exact recorded dirty objects materialized by an accepted commit."""

        transition_path_set: set[str] = set()
        candidate_path_set = accepted_main_commit_path_set & changed_status_path_set & set(previous_status_by_path_map)
        for path_text in sorted(candidate_path_set):
            if path_text in current_status_by_path_map:
                continue
            previous_preimage = repository_state["main_preimage_by_path_map"].get(path_text)
            if previous_preimage is None:
                raise WorktreeError(f"Recorded main preimage is absent: {main_root / path_text}")
            if self._path_fingerprint_get(main_root / path_text) != previous_preimage["working_fingerprint"]:
                continue
            transition_path_set.add(path_text)
        return transition_path_set

    def _unaccepted_main_commit_task_overlap_set_get(
        self,
        main_root: Path,
        repository_state: RepositoryState,
        task_root: Path,
        current_main_commit: str,
        *,
        delegated_submodule_path_set: set[str] | None = None,
    ) -> set[str]:
        """Return exact committed main paths that still require caller attestation."""

        if (
            self._git_command.run(
                main_root,
                ["merge-base", "--is-ancestor", repository_state["baseline_commit"], current_main_commit],
                check=False,
            ).returncode
            != 0
        ):
            raise WorktreeError(f"Main history no longer descends from the task baseline: {main_root}")
        if delegated_submodule_path_set is None:
            delegated_submodule_path_set = {
                item["path"] for item in repository_state["participating_submodule_state_list"]
            }
        task_changed_path_set = self._task_changed_path_set_get(
            repository_state["baseline_commit"],
            task_root,
        )
        task_changed_path_set -= self._path_boundary_overlap_set_get(
            task_changed_path_set,
            delegated_submodule_path_set,
        )
        accumulated_commit_path_set = self._commit_changed_path_set_get(
            main_root,
            repository_state["baseline_commit"],
            current_main_commit,
        )
        accumulated_commit_path_set -= self._path_boundary_overlap_set_get(
            accumulated_commit_path_set,
            delegated_submodule_path_set,
        )
        overlap_set = self._path_boundary_overlap_set_get(
            accumulated_commit_path_set,
            task_changed_path_set,
        )
        accepted_path_set = self._accepted_main_commit_path_set_get(
            main_root,
            repository_state,
            current_main_commit,
        )
        overlap_set -= self._path_boundary_overlap_set_get(
            overlap_set,
            accepted_path_set,
        )
        return overlap_set

    def _accepted_main_commit_path_set_get(
        self,
        main_root: Path,
        repository_state: RepositoryState,
        current_main_commit: str,
    ) -> set[str]:
        """Return still-current exact paths covered by caller commit attestations."""

        accepted_path_set: set[str] = set()
        previous_commit = repository_state["baseline_commit"]
        for attestation in repository_state["accepted_main_commit_drift_list"]:
            accepted_commit = attestation["commit"]
            if (
                self._git_command.run(
                    main_root,
                    ["merge-base", "--is-ancestor", previous_commit, accepted_commit],
                    check=False,
                ).returncode
                != 0
                or self._git_command.run(
                    main_root,
                    ["merge-base", "--is-ancestor", accepted_commit, current_main_commit],
                    check=False,
                ).returncode
                != 0
            ):
                raise WorktreeError(
                    f"Accepted main commit-drift history is no longer linear in {main_root}: {accepted_commit}"
                )
            accumulated_path_set = self._commit_changed_path_set_get(
                main_root,
                repository_state["baseline_commit"],
                accepted_commit,
            )
            missing_path_set = set(attestation["path_list"]) - accumulated_path_set
            if missing_path_set:
                raise WorktreeError(
                    f"Accepted main commit-drift paths are absent from recorded history in "
                    f"{main_root}: {', '.join(sorted(missing_path_set))}"
                )
            changed_since_acceptance_set = self._commit_changed_path_set_get(
                main_root,
                accepted_commit,
                current_main_commit,
            )
            accepted_path_set.update(
                set(attestation["path_list"])
                - self._path_boundary_overlap_set_get(
                    set(attestation["path_list"]),
                    changed_since_acceptance_set,
                )
            )
            previous_commit = accepted_commit
        return accepted_path_set

    def _commit_changed_path_set_get(
        self,
        repository_root: Path,
        previous_commit: str,
        current_commit: str,
    ) -> set[str]:
        """Return exact net Git paths changed between two commits."""

        changed_path_set = set(
            self._git_command.run(
                repository_root,
                [
                    "diff",
                    "--ignore-submodules=none",
                    "--no-renames",
                    "--name-only",
                    "-z",
                    f"{previous_commit}..{current_commit}",
                    "--",
                ],
            ).stdout.split("\0")
        )
        changed_path_set.discard("")
        return changed_path_set

    def _protected_task_path_set_get(
        self,
        task_changed_path_set: set[str],
        materialized_resource_path_set: set[str],
    ) -> set[str]:
        """Return task and materialized-resource boundaries protected from main drift.

        Args:
            task_changed_path_set: Current top-level task paths changed from baseline.
            materialized_resource_path_set: Prepared non-skipped resource boundaries.

        Returns:
            Root-relative protected path boundaries.
        """

        return task_changed_path_set | materialized_resource_path_set

    def _materialized_resource_path_set_get(
        self,
        repository_state: RepositoryState,
    ) -> set[str]:
        """Return all non-skipped resource boundaries relative to the top-level root.

        Args:
            repository_state: Recorded top-level and task-owned-submodule resources.

        Returns:
            Root-relative materialized resource boundaries.
        """

        resource_path_set = {
            resource_state["path"]
            for resource_state in repository_state["resource_state_list"]
            if not resource_state["skipped"]
        }
        for submodule_state in repository_state["participating_submodule_state_list"]:
            submodule_path = PurePosixPath(submodule_state["path"])
            resource_path_set.update(
                (submodule_path / resource_state["path"]).as_posix()
                for resource_state in submodule_state["resource_state_list"]
                if not resource_state["skipped"]
            )
        return resource_path_set

    def _resource_binding_optional_get(
        self,
        repository_state: RepositoryState,
        path_text: str,
    ) -> tuple[Path, Path, str, ResourceState, str] | None:
        """Resolve one top-level path into a materialized resource owner.

        Args:
            repository_state: Recorded top-level and task-owned-submodule resources.
            path_text: Top-level root-relative candidate path.

        Returns:
            Main owner root, task owner root, owner-relative candidate path,
            resource state, and owner-relative resource boundary.
        """

        candidate_path = PurePosixPath(path_text)
        main_root = Path(repository_state["main_root"])
        task_root = Path(repository_state["task_root"])
        for resource_state in repository_state["resource_state_list"]:
            if resource_state["skipped"]:
                continue
            boundary_path = PurePosixPath(resource_state["path"])
            if candidate_path == boundary_path or boundary_path in candidate_path.parents:
                return (
                    main_root,
                    task_root,
                    candidate_path.as_posix(),
                    resource_state,
                    boundary_path.as_posix(),
                )
        for submodule_state in repository_state["participating_submodule_state_list"]:
            submodule_path = PurePosixPath(submodule_state["path"])
            for resource_state in submodule_state["resource_state_list"]:
                if resource_state["skipped"]:
                    continue
                resource_boundary_path = PurePosixPath(resource_state["path"])
                top_level_boundary_path = submodule_path / resource_boundary_path
                if candidate_path == top_level_boundary_path or top_level_boundary_path in candidate_path.parents:
                    owner_relative_candidate_path = candidate_path.relative_to(submodule_path)
                    return (
                        main_root / submodule_path.as_posix(),
                        task_root / submodule_path.as_posix(),
                        owner_relative_candidate_path.as_posix(),
                        resource_state,
                        resource_boundary_path.as_posix(),
                    )
        return None

    def _path_boundary_overlap_set_get(
        self,
        candidate_path_set: set[str],
        protected_path_set: set[str],
    ) -> set[str]:
        """Return candidate paths equal to, below, or above protected boundaries.

        Args:
            candidate_path_set: Changed root-relative Git paths.
            protected_path_set: Task-owned path boundaries.

        Returns:
            Candidate paths that overlap at least one protected boundary.
        """

        overlap_set: set[str] = set()
        protected_path_list = [PurePosixPath(path_text) for path_text in protected_path_set]
        for candidate_path_text in candidate_path_set:
            candidate_path = PurePosixPath(candidate_path_text)
            if any(
                candidate_path == protected_path
                or candidate_path in protected_path.parents
                or protected_path in candidate_path.parents
                for protected_path in protected_path_list
            ):
                overlap_set.add(candidate_path_text)
        return overlap_set

    def _non_overlapping_path_set_validate(self, path_set: set[str], label: str) -> None:
        """Reject a path set containing an ancestor and its descendant.

        Args:
            path_set: Normalized root-relative path strings.
            label: Diagnostic collection name.
        """

        for path_text in sorted(path_set):
            path = PurePosixPath(path_text)
            overlapping_ancestor = next(
                (
                    parent.as_posix()
                    for parent in path.parents
                    if parent != PurePosixPath(".") and parent.as_posix() in path_set
                ),
                None,
            )
            if overlapping_ancestor is not None:
                raise WorktreeError(f"{label} must not overlap: {overlapping_ancestor} and {path_text}")

    def _ignored_untracked_path_set_get(
        self,
        repository_root: Path,
        boundary_path_set: set[str],
    ) -> set[str]:
        """Return ignored untracked objects at or below task boundaries.

        Args:
            repository_root: Exact main-worktree root.
            boundary_path_set: Current task-owned root-relative paths.

        Returns:
            Ignored untracked paths that can collide with those boundaries.
        """

        pathspec_set: set[str] = set()
        for path_text in boundary_path_set:
            path = PurePosixPath(path_text)
            pathspec_set.add(path.as_posix())
            pathspec_set.update(parent.as_posix() for parent in path.parents if parent != PurePosixPath("."))
        if not pathspec_set:
            return set()
        result = self._git_command.run(
            repository_root,
            [
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                *sorted(pathspec_set),
            ],
        )
        ignored_path_set = {path_text for path_text in result.stdout.split("\0") if path_text}
        return self._path_boundary_overlap_set_get(ignored_path_set, boundary_path_set)

    def _task_changed_path_set_get(self, baseline_commit: str, task_root: Path) -> set[str]:
        """Return all tracked and untracked task paths changed from baseline.

        Args:
            baseline_commit: Selected task baseline.
            task_root: Task-worktree root.

        Returns:
            Changed root-relative path set.
        """

        changed_path_set = set(
            self._git_command.run(
                task_root,
                [
                    "-c",
                    "core.fileMode=true",
                    "diff",
                    "--ignore-submodules=none",
                    "--no-renames",
                    "--name-only",
                    "-z",
                    baseline_commit,
                    "--",
                ],
            ).stdout.split("\0")
        )
        changed_path_set.discard("")
        changed_path_set.update(self._status_by_path_map_get(task_root))
        return changed_path_set

    def _worktree_by_path_map_get(self, main_root: Path) -> dict[str, dict[str, str]]:
        """Return registered worktrees by canonical path.

        Args:
            main_root: Main-worktree root.

        Returns:
            Worktree records by canonical path.
        """

        result = self._git_command.run(main_root, ["worktree", "list", "--porcelain", "-z"])
        worktree_by_path_map: dict[str, dict[str, str]] = {}
        current_record: dict[str, str] = {}
        for item in [*result.stdout.split("\0"), ""]:
            if not item:
                if current_record:
                    path_text = current_record.get("worktree")
                    if path_text is None:
                        raise WorktreeError(f"Cannot parse worktree record in {main_root}")
                    worktree_by_path_map[str(Path(path_text).resolve())] = {
                        "branch_name": current_record.get("branch", "").removeprefix("refs/heads/"),
                        "head": current_record.get("HEAD", ""),
                    }
                    current_record = {}
                continue
            key, separator, value = item.partition(" ")
            if not separator:
                current_record[item] = ""
                continue
            current_record[key] = value
        return worktree_by_path_map

    def _registered_worktree_identity_validate(
        self,
        main_root: Path,
        task_root: Path,
        worktree_record: dict[str, str],
    ) -> None:
        """Prove a registered path is the exact linked worktree before any project write."""

        git_pointer_path = task_root / ".git"
        if (
            task_root.is_symlink()
            or not task_root.is_dir()
            or git_pointer_path.is_symlink()
            or not git_pointer_path.is_file()
        ):
            raise WorktreeError(f"Registered task path is not an intact linked worktree: {task_root}")
        top_level_result = self._git_command.run(
            task_root,
            ["rev-parse", "--show-toplevel"],
            check=False,
        )
        common_directory_result = self._git_command.run(
            task_root,
            ["rev-parse", "--git-common-dir"],
            check=False,
        )
        branch_result = self._git_command.run(
            task_root,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            check=False,
        )
        head_result = self._git_command.run(
            task_root,
            ["rev-parse", "HEAD"],
            check=False,
        )
        observed_common_directory = (
            Path(common_directory_result.stdout.strip())
            if common_directory_result.stdout.strip() and Path(common_directory_result.stdout.strip()).is_absolute()
            else task_root / common_directory_result.stdout.strip()
        )
        if (
            top_level_result.returncode != 0
            or not top_level_result.stdout.strip()
            or Path(top_level_result.stdout.strip()).resolve() != task_root
            or common_directory_result.returncode != 0
            or not common_directory_result.stdout.strip()
            or observed_common_directory.resolve() != self._git_common_directory_get(main_root)
            or branch_result.returncode != 0
            or branch_result.stdout.strip() != worktree_record["branch_name"]
            or head_result.returncode != 0
            or head_result.stdout.strip() != worktree_record["head"]
        ):
            raise WorktreeError(f"Registered task path identity is inconsistent: {task_root}")

    def _registered_worktree_identity_repair(
        self,
        main_root: Path,
        task_root: Path,
        worktree_record: dict[str, str],
        repository_state: RepositoryState,
        performed_repair_list: list[str],
    ) -> None:
        """Repair only a redirected `.git` pointer with complete durable ownership."""

        git_pointer_path = task_root / ".git"
        expected_specification_target = os.path.relpath(
            self._specification_path.parent,
            start=task_root,
        )
        specification_link_path = task_root / ".spec"
        if (
            task_root.is_symlink()
            or not task_root.is_dir()
            or git_pointer_path.is_symlink()
            or (os.path.lexists(git_pointer_path) and not git_pointer_path.is_file())
            or repository_state["main_root"] != str(main_root)
            or repository_state["task_root"] != str(task_root)
            or repository_state["branch_name"] != self._prefix
            or repository_state["common_git_directory"] != str(self._git_common_directory_get(main_root))
            or self._path_fingerprint_get(task_root / MANIFEST_NAME) != repository_state["manifest_fingerprint"]
            or not specification_link_path.is_symlink()
            or os.readlink(specification_link_path) != expected_specification_target
        ):
            raise WorktreeError(f"Registered task path is not safely repairable: {task_root}")
        administration_path = self._registered_worktree_administration_path_get(
            main_root,
            task_root,
        )
        state_path = administration_path / PRIVATE_STATE_DIRECTORY_NAME / PRIVATE_STATE_FILENAME
        administration_state = self._state_path_load(state_path)
        administration_repository_state = next(
            (item for item in administration_state["repository_state_list"] if item["main_root"] == str(main_root)),
            None,
        )
        if administration_repository_state != repository_state:
            raise WorktreeError(f"Registered task administration state is inconsistent: {task_root}")
        self._private_text_atomic_write(
            git_pointer_path,
            f"gitdir: {administration_path}\n",
            staging_owner_root=(administration_path / PRIVATE_STATE_DIRECTORY_NAME / "private-atomic-write-v1"),
        )
        self._git_command.run(
            main_root,
            ["worktree", "repair", str(task_root)],
        )
        self._registered_worktree_identity_validate(
            main_root,
            task_root,
            worktree_record,
        )
        performed_repair_list.append(f"repaired registered task worktree identity: {task_root}")

    def _pending_worktree_marker_path_get(self, main_root: Path) -> Path:
        """Return one common-Git marker written before worktree creation."""

        task_root = main_root / WORKTREE_CONTAINER_NAME / self._prefix
        marker_name = hashlib.sha256(os.fsencode(task_root.resolve())).hexdigest()
        return self._git_path_get(
            main_root,
            Path(PRIVATE_STATE_DIRECTORY_NAME) / PENDING_WORKTREE_DIRECTORY_NAME / f"{marker_name}.json",
        )

    def _pending_worktree_optional_get(self, main_root: Path) -> PendingWorktree | None:
        """Load and validate provider authorization for an unfinished worktree."""

        marker_path = self._pending_worktree_marker_path_get(main_root)
        if not os.path.lexists(marker_path):
            return None
        if marker_path.is_symlink() or not marker_path.is_file():
            raise WorktreeError(f"Pending worktree marker is damaged: {marker_path}")
        try:
            payload = json.loads(
                self._utf8_text_get(
                    marker_path,
                    "Pending worktree marker",
                )
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorktreeError(f"Pending worktree marker is invalid: {marker_path}") from exc
        expected_key_set = {
            "baseline_commit",
            "branch_name",
            "common_git_directory",
            "main_root",
            "schema_version",
            "task_root",
        }
        expected_task_root = main_root / WORKTREE_CONTAINER_NAME / self._prefix
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_key_set
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
            or not _hex_digest_is_valid(payload.get("baseline_commit"), {40, 64})
            or payload.get("branch_name") != self._prefix
            or payload.get("main_root") != str(main_root)
            or payload.get("task_root") != str(expected_task_root)
            or payload.get("common_git_directory") != str(self._git_common_directory_get(main_root))
        ):
            raise WorktreeError(f"Pending worktree marker is invalid: {marker_path}")
        return cast(PendingWorktree, payload)

    def _pending_worktree_create(
        self,
        baseline_commit: str,
        main_root: Path,
    ) -> PendingWorktree:
        """Record exact worktree identity before Git can create a branch or path."""

        existing_state = self._pending_worktree_optional_get(main_root)
        if existing_state is not None:
            if existing_state["baseline_commit"] != baseline_commit:
                raise WorktreeError(f"Pending worktree baseline changed: {main_root}")
            return existing_state
        task_root = main_root / WORKTREE_CONTAINER_NAME / self._prefix
        payload: PendingWorktree = {
            "baseline_commit": baseline_commit,
            "branch_name": self._prefix,
            "common_git_directory": str(self._git_common_directory_get(main_root)),
            "main_root": str(main_root),
            "schema_version": 1,
            "task_root": str(task_root),
        }
        self._private_text_atomic_write(
            self._pending_worktree_marker_path_get(main_root),
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        )
        return payload

    def _pending_worktree_marker_list_retire(
        self,
        state: WorktreeState,
        performed_repair_list: list[str] | None,
    ) -> None:
        """Retire creation markers only after all global state replicas exist."""

        for repository_state in state["repository_state_list"]:
            main_root = Path(repository_state["main_root"])
            pending_state = self._pending_worktree_optional_get(main_root)
            if pending_state is None:
                continue
            task_root = Path(repository_state["task_root"])
            worktree_record = self._worktree_by_path_map_get(main_root).get(str(task_root))
            if (
                pending_state["baseline_commit"] != repository_state["baseline_commit"]
                or pending_state["task_root"] != str(task_root)
                or worktree_record is None
                or worktree_record["branch_name"] != repository_state["branch_name"]
                or (
                    self._git_command.run(
                        task_root,
                        [
                            "merge-base",
                            "--is-ancestor",
                            pending_state["baseline_commit"],
                            worktree_record["head"],
                        ],
                        check=False,
                    ).returncode
                    != 0
                )
            ):
                raise WorktreeError(f"Cannot retire incomplete pending worktree ownership: {task_root}")
            marker_path = self._pending_worktree_marker_path_get(main_root)
            self._path_remove(marker_path)
            if performed_repair_list is not None:
                performed_repair_list.append(f"finalized pending worktree ownership: {task_root}")

    def _tool_less_worktree_adoption_validate(
        self,
        baseline_commit: str,
        main_root: Path,
        task_root: Path,
        participating_submodule_path_set: set[str],
    ) -> None:
        """Prove a complete markerless bootstrap without adopting unrelated content."""

        top_level_result = self._git_command.run(
            task_root,
            ["rev-parse", "--show-toplevel"],
            check=False,
        )
        common_directory_result = self._git_command.run(
            task_root,
            ["rev-parse", "--git-common-dir"],
            check=False,
        )
        branch_result = self._git_command.run(
            task_root,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            check=False,
        )
        head_result = self._git_command.run(
            task_root,
            ["rev-parse", "HEAD"],
            check=False,
        )
        observed_common_directory = (
            Path(common_directory_result.stdout.strip())
            if common_directory_result.stdout.strip() and Path(common_directory_result.stdout.strip()).is_absolute()
            else task_root / common_directory_result.stdout.strip()
        )
        if (
            top_level_result.returncode != 0
            or not top_level_result.stdout.strip()
            or Path(top_level_result.stdout.strip()).resolve() != task_root
            or common_directory_result.returncode != 0
            or not common_directory_result.stdout.strip()
            or observed_common_directory.resolve() != self._git_common_directory_get(main_root)
            or branch_result.returncode != 0
            or branch_result.stdout.strip() != self._prefix
            or head_result.returncode != 0
            or head_result.stdout.strip() != baseline_commit
            or self._git_command.run(
                task_root,
                ["diff", "--cached", "--quiet", baseline_commit, "--"],
                check=False,
            ).returncode
            != 0
        ):
            raise WorktreeError(f"Markerless task worktree identity is not exact: {task_root}")
        specification_link_path = task_root / ".spec"
        expected_specification_target = os.path.relpath(
            self._specification_path.parent,
            start=task_root,
        )
        if (
            not specification_link_path.is_symlink()
            or os.readlink(specification_link_path) != expected_specification_target
        ):
            raise WorktreeError(f"Markerless task worktree has no exact specification link: {task_root}")
        manifest_path = task_root / MANIFEST_NAME
        resource_by_class_map = self._manifest_get(manifest_path, task_root)
        resource_path_set = {path_text for path_list in resource_by_class_map.values() for path_text in path_list}
        required_ignore_path_list = [
            PurePosixPath(".spec"),
            PurePosixPath(WORKTREE_CONTAINER_NAME),
            *[PurePosixPath(path_text) for path_text in sorted(resource_path_set)],
        ]
        _, expected_gitignore_text, _ = self._tracked_ignore_text_get(
            task_root,
            required_ignore_path_list,
        )
        gitignore_path = task_root / ".gitignore"
        current_gitignore_text = (
            self._utf8_text_get(gitignore_path, "Project ignore file") if gitignore_path.is_file() else ""
        )
        if current_gitignore_text != expected_gitignore_text:
            raise WorktreeError(f"Markerless task worktree ignore rules are incomplete: {gitignore_path}")
        for resource_class, path_list in resource_by_class_map.items():
            strategy = "copy" if resource_class.startswith("copy_") else "link"
            required = "_required_" in resource_class
            for path_text in path_list:
                source_path = main_root / path_text
                destination_path = task_root / path_text
                if not os.path.lexists(source_path):
                    if required or os.path.lexists(destination_path):
                        raise WorktreeError(f"Markerless task worktree resource is not exact: {destination_path}")
                    continue
                self._source_boundary_validate(main_root, source_path)
                if strategy == "copy":
                    self._path_copy_source_validate(source_path)
                    if destination_path.is_symlink() or self._path_fingerprint_get(
                        destination_path
                    ) != self._path_fingerprint_get(source_path):
                        raise WorktreeError(
                            f"Markerless copy resource is not an exact source snapshot: {destination_path}"
                        )
                else:
                    expected_target = os.path.relpath(source_path, start=destination_path.parent)
                    if not destination_path.is_symlink() or os.readlink(destination_path) != expected_target:
                        raise WorktreeError(f"Markerless link resource is not exact: {destination_path}")
        allowed_boundary_set = {
            ".gitignore",
            MANIFEST_NAME,
            *resource_path_set,
            *participating_submodule_path_set,
            *self._preparation_submodule_status_boundary_set_get(
                task_root,
                participating_submodule_path_set,
            ),
        }
        unexpected_status_path_set = {
            path_text
            for path_text in self._status_by_path_map_get(task_root)
            if not self._path_boundary_overlap_set_get({path_text}, allowed_boundary_set)
        }
        if unexpected_status_path_set:
            raise WorktreeError(
                f"Markerless task worktree contains independent dirty state in "
                f"{task_root}: {', '.join(sorted(unexpected_status_path_set))}"
            )

    def _pending_worktree_checkout_validate(
        self,
        baseline_commit: str,
        main_root: Path,
        task_root: Path,
        participating_submodule_path_set: set[str],
    ) -> None:
        """Reject incomplete or independently changed checkout state before bootstrap."""

        if (
            self._git_command.run(task_root, ["rev-parse", "HEAD"]).stdout.strip() != baseline_commit
            or self._git_command.run(
                task_root,
                ["diff", "--cached", "--quiet", baseline_commit, "--"],
                check=False,
            ).returncode
            != 0
        ):
            raise WorktreeError(f"Pending task worktree index is not the selected baseline: {task_root}")
        specification_link_path = task_root / ".spec"
        expected_specification_target = os.path.relpath(
            self._specification_path.parent,
            start=task_root,
        )
        if os.path.lexists(specification_link_path) and (
            not specification_link_path.is_symlink()
            or os.readlink(specification_link_path) != expected_specification_target
        ):
            raise WorktreeError(f"Pending task worktree has an unexpected specification object: {task_root}")
        manifest_path = task_root / MANIFEST_NAME
        if os.path.lexists(manifest_path):
            resource_by_class_map = self._manifest_get(manifest_path, task_root)
        else:
            resource_by_class_map = {resource_class: [] for resource_class in MANIFEST_RESOURCE_KEY_SET}
        resource_path_set = {path_text for path_list in resource_by_class_map.values() for path_text in path_list}
        status_by_path_map = self._status_by_path_map_get(task_root)
        manifest_status = status_by_path_map.get(MANIFEST_NAME)
        if manifest_status is not None:
            manifest_is_tracked = (
                self._git_command.run(
                    task_root,
                    ["ls-files", "--error-unmatch", "--", MANIFEST_NAME],
                    check=False,
                ).returncode
                == 0
            )
            if manifest_is_tracked or (
                self._initial_manifest_owner_fingerprint_get(task_root) != self._path_fingerprint_get(manifest_path)
            ):
                raise WorktreeError(f"Pending task worktree manifest contains independent changes: {manifest_path}")
        if ".gitignore" in status_by_path_map:
            required_ignore_path_list = [
                PurePosixPath(".spec"),
                PurePosixPath(WORKTREE_CONTAINER_NAME),
                *[PurePosixPath(path_text) for path_text in sorted(resource_path_set)],
            ]
            _, expected_gitignore_text, _ = self._tracked_ignore_text_get(
                task_root,
                required_ignore_path_list,
            )
            gitignore_path = task_root / ".gitignore"
            if (
                not gitignore_path.is_file()
                or gitignore_path.is_symlink()
                or self._utf8_text_get(
                    gitignore_path,
                    "Project ignore file",
                )
                != expected_gitignore_text
            ):
                raise WorktreeError(f"Pending task worktree ignore file contains independent changes: {gitignore_path}")
        for resource_class, path_list in resource_by_class_map.items():
            strategy = "copy" if resource_class.startswith("copy_") else "link"
            required = "_required_" in resource_class
            for path_text in path_list:
                source_path = main_root / path_text
                destination_path = task_root / path_text
                if (
                    self._git_command.run(
                        task_root,
                        ["ls-files", "--error-unmatch", "--", path_text],
                        check=False,
                    ).returncode
                    == 0
                ):
                    raise WorktreeError(f"Pending task worktree resource is tracked by Git: {destination_path}")
                if not os.path.lexists(source_path):
                    if required or os.path.lexists(destination_path):
                        raise WorktreeError(f"Pending task worktree resource is inconsistent: {destination_path}")
                    continue
                if not os.path.lexists(destination_path):
                    continue
                self._source_boundary_validate(main_root, source_path)
                if strategy == "copy":
                    if destination_path.is_symlink() or self._path_fingerprint_get(
                        destination_path
                    ) != self._path_fingerprint_get(source_path):
                        raise WorktreeError(f"Pending copy resource contains independent changes: {destination_path}")
                else:
                    expected_target = os.path.relpath(source_path, start=destination_path.parent)
                    if not destination_path.is_symlink() or os.readlink(destination_path) != expected_target:
                        raise WorktreeError(f"Pending link resource contains independent changes: {destination_path}")
        allowed_boundary_set = {
            ".gitignore",
            MANIFEST_NAME,
            *resource_path_set,
            *participating_submodule_path_set,
            *self._preparation_submodule_status_boundary_set_get(
                task_root,
                participating_submodule_path_set,
            ),
        }
        unexpected_status_path_set = {
            path_text
            for path_text in status_by_path_map
            if not self._path_boundary_overlap_set_get({path_text}, allowed_boundary_set)
        }
        if unexpected_status_path_set:
            raise WorktreeError(
                f"Pending task worktree checkout is incomplete or independently changed in "
                f"{task_root}: {', '.join(sorted(unexpected_status_path_set))}"
            )

    def _preparation_submodule_status_boundary_set_get(
        self,
        task_root: Path,
        participating_submodule_path_set: set[str],
    ) -> set[str]:
        """Return direct submodule status boundaries after rejecting dirty read-only drift."""

        self._submodule_dirty_validate(
            task_root,
            participating_submodule_path_set,
        )
        return {submodule_path.as_posix() for submodule_path in self._submodule_path_list_get(task_root)}

    def _worktree_create_or_adopt(
        self,
        baseline_commit: str,
        main_root: Path,
        performed_repair_list: list[str],
        has_previous_state: bool,
        tool_less_adoption: bool,
    ) -> Path:
        """Create or adopt one exact same-prefix task worktree.

        Args:
            baseline_commit: Selected committed baseline.
            main_root: Main-worktree root.
            performed_repair_list: Mutable repair report.
            has_previous_state: Whether private state already owns this worktree.
            tool_less_adoption: Whether complete observable bootstrap proved ownership.

        Returns:
            Exact task-worktree root.
        """

        self._worktree_container_validate(main_root)
        task_root = main_root / WORKTREE_CONTAINER_NAME / self._prefix
        pending_state = self._pending_worktree_optional_get(main_root)
        if (
            not has_previous_state
            and not tool_less_adoption
            and (pending_state is None or pending_state["baseline_commit"] != baseline_commit)
        ):
            raise WorktreeError(f"Task worktree creation has no matching pending ownership: {task_root}")
        worktree_by_path_map = self._worktree_by_path_map_get(main_root)
        existing_record = worktree_by_path_map.get(str(task_root.resolve()))
        if existing_record is not None:
            if existing_record["branch_name"] != self._prefix:
                raise WorktreeError(f"Existing task path belongs to another branch: {task_root}")
            self._registered_worktree_identity_validate(
                main_root,
                task_root,
                existing_record,
            )
            if not has_previous_state and existing_record["head"] != baseline_commit:
                raise WorktreeError(f"Unrecorded task worktree is not at the selected baseline: {task_root}")
            return task_root.resolve()
        if os.path.lexists(task_root):
            if task_root.is_symlink() or not task_root.is_dir():
                raise WorktreeError(f"Existing task path is not one adoptable worktree: {task_root}")
            common_directory_result = self._git_command.run(
                task_root,
                ["rev-parse", "--git-common-dir"],
                check=False,
            )
            if (
                common_directory_result.returncode != 0
                or not common_directory_result.stdout.strip()
                or (
                    Path(common_directory_result.stdout.strip())
                    if Path(common_directory_result.stdout.strip()).is_absolute()
                    else task_root / common_directory_result.stdout.strip()
                ).resolve()
                != self._git_common_directory_get(main_root)
            ):
                raise WorktreeError(f"Existing task path belongs to another Git repository: {task_root}")
            self._git_command.run(main_root, ["worktree", "repair", str(task_root)], check=False)
            existing_record = self._worktree_by_path_map_get(main_root).get(str(task_root.resolve()))
            if existing_record is None or existing_record["branch_name"] != self._prefix:
                raise WorktreeError(f"Existing task path is not one adoptable worktree: {task_root}")
            self._registered_worktree_identity_validate(
                main_root,
                task_root,
                existing_record,
            )
            if not has_previous_state and existing_record["head"] != baseline_commit:
                raise WorktreeError(f"Unrecorded task worktree is not at the selected baseline: {task_root}")
            performed_repair_list.append(f"repaired worktree registration: {task_root}")
            return task_root.resolve()
        branch_result = self._git_command.run(
            main_root,
            ["show-ref", "--verify", f"refs/heads/{self._prefix}"],
            check=False,
        )
        task_root.parent.mkdir(parents=True, exist_ok=True)
        if branch_result.returncode == 0:
            branch_commit = self._git_command.run(main_root, ["rev-parse", self._prefix]).stdout.strip()
            if branch_commit != baseline_commit:
                raise WorktreeError(
                    f"Existing task branch does not match selected baseline: {self._prefix} at {branch_commit}"
                )
            self._git_command.run(main_root, ["worktree", "add", str(task_root), self._prefix])
        else:
            self._git_command.run(
                main_root,
                ["worktree", "add", "-b", self._prefix, str(task_root), baseline_commit],
            )
        performed_repair_list.append(f"created task worktree: {task_root}")
        return task_root.resolve()

    def _worktree_container_validate(self, main_root: Path) -> None:
        """Require one untracked physical project-local worktree container.

        Args:
            main_root: Exact main-worktree root.
        """

        worktree_container = main_root / WORKTREE_CONTAINER_NAME
        if os.path.lexists(worktree_container) and (worktree_container.is_symlink() or not worktree_container.is_dir()):
            raise WorktreeError(f"Task-worktree container is not one physical directory: {worktree_container}")
        tracked_path_list = self._git_command.run(
            main_root,
            ["ls-files", "-z", "--", WORKTREE_CONTAINER_NAME],
        ).stdout.split("\0")
        tracked_path_list = [path_text for path_text in tracked_path_list if path_text]
        if tracked_path_list:
            raise WorktreeError(
                "Task-worktree container contains tracked paths: " + ", ".join(sorted(tracked_path_list))
            )

    def _worktree_preflight_validate(
        self,
        baseline_commit: str,
        main_root: Path,
        previous_repository_state: RepositoryState | None,
        participating_submodule_path_set: set[str],
        performed_repair_list: list[str],
    ) -> bool:
        """Reject observable worktree collisions before any preparation write.

        Args:
            baseline_commit: Selected main-worktree baseline.
            main_root: Exact main-worktree root.
            previous_repository_state: Durable ownership for this root, when present.
            participating_submodule_path_set: Explicit task-owned recursive submodules.
            performed_repair_list: Mutable repair report.

        Returns:
            Whether a complete markerless worktree was proven adoptable.
        """

        self._worktree_container_validate(main_root)
        has_previous_state = previous_repository_state is not None
        task_root = main_root / WORKTREE_CONTAINER_NAME / self._prefix
        pending_state = self._pending_worktree_optional_get(main_root)
        if pending_state is not None and pending_state["baseline_commit"] != baseline_commit:
            raise WorktreeError(f"Pending worktree baseline changed: {task_root}")
        worktree_by_path_map = self._worktree_by_path_map_get(main_root)
        existing_record = worktree_by_path_map.get(str(task_root.resolve()))
        if existing_record is not None:
            if existing_record["branch_name"] != self._prefix:
                raise WorktreeError(f"Existing task path belongs to another branch: {task_root}")
            try:
                self._registered_worktree_identity_validate(
                    main_root,
                    task_root,
                    existing_record,
                )
            except WorktreeError:
                if previous_repository_state is None:
                    raise
                self._registered_worktree_identity_repair(
                    main_root,
                    task_root,
                    existing_record,
                    previous_repository_state,
                    performed_repair_list,
                )
            if not has_previous_state and pending_state is not None:
                self._pending_worktree_checkout_validate(
                    baseline_commit,
                    main_root,
                    task_root,
                    participating_submodule_path_set,
                )
            if not has_previous_state and pending_state is None:
                self._tool_less_worktree_adoption_validate(
                    baseline_commit,
                    main_root,
                    task_root,
                    participating_submodule_path_set,
                )
                return True
            if not has_previous_state and existing_record["head"] != baseline_commit:
                raise WorktreeError(f"Unrecorded task worktree is not at the selected baseline: {task_root}")
            return False
        if os.path.lexists(task_root) and (
            task_root.is_symlink() or not task_root.is_dir() or not os.path.lexists(task_root / ".git")
        ):
            raise WorktreeError(f"Existing task path is not one adoptable worktree: {task_root}")
        if os.path.lexists(task_root):
            top_level_result = self._git_command.run(
                task_root,
                ["rev-parse", "--show-toplevel"],
                check=False,
            )
            branch_name = self._git_command.run(
                task_root,
                ["symbolic-ref", "--quiet", "--short", "HEAD"],
                check=False,
            ).stdout.strip()
            task_commit = self._git_command.run(
                task_root,
                ["rev-parse", "HEAD"],
                check=False,
            ).stdout.strip()
            common_directory_result = self._git_command.run(
                task_root,
                ["rev-parse", "--git-common-dir"],
                check=False,
            )
            if (
                top_level_result.returncode != 0
                or not top_level_result.stdout.strip()
                or Path(top_level_result.stdout.strip()).resolve() != task_root.resolve()
                or branch_name != self._prefix
                or task_commit != baseline_commit
                or common_directory_result.returncode != 0
                or not common_directory_result.stdout.strip()
                or (
                    Path(common_directory_result.stdout.strip())
                    if Path(common_directory_result.stdout.strip()).is_absolute()
                    else task_root / common_directory_result.stdout.strip()
                ).resolve()
                != self._git_common_directory_get(main_root)
            ):
                raise WorktreeError(f"Existing task path is not one adoptable worktree: {task_root}")
            if not has_previous_state and pending_state is None:
                self._tool_less_worktree_adoption_validate(
                    baseline_commit,
                    main_root,
                    task_root,
                    participating_submodule_path_set,
                )
                return True
            if not has_previous_state and pending_state is not None:
                self._pending_worktree_checkout_validate(
                    baseline_commit,
                    main_root,
                    task_root,
                    participating_submodule_path_set,
                )
            return False
        branch_result = self._git_command.run(
            main_root,
            ["show-ref", "--verify", f"refs/heads/{self._prefix}"],
            check=False,
        )
        if branch_result.returncode != 0:
            return False
        branch_commit = self._git_command.run(main_root, ["rev-parse", self._prefix]).stdout.strip()
        if branch_commit != baseline_commit:
            raise WorktreeError(
                f"Existing task branch does not match selected baseline: {self._prefix} at {branch_commit}"
            )
        checked_out_path_list = [
            path_text
            for path_text, record in worktree_by_path_map.items()
            if record["branch_name"] == self._prefix and path_text != str(task_root.resolve())
        ]
        if checked_out_path_list:
            raise WorktreeError(
                f"Existing task branch is checked out at another path: "
                f"{self._prefix} at {', '.join(sorted(checked_out_path_list))}"
            )
        if not has_previous_state and pending_state is None:
            raise WorktreeError(f"Unrecorded task branch has no pending ownership: {self._prefix}")
        return False
