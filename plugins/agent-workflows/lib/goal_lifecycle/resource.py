"""Crash-safe bootstrap resource materialization, fingerprinting, and validation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

from goal_lifecycle.cleanup_manifest import BootstrapManifest
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write, directory_sync, json_object_load
from goal_lifecycle.task.model import BootstrapResourceState
from goal_lifecycle.task.repair import TaskRepairReport


class BootstrapResourceManager:
    """Materialize exact copy/link resources through durable per-path transactions."""

    def __init__(self, *, git: Git | None = None, repair_report: TaskRepairReport | None = None) -> None:
        """Initialize the bootstrap resource manager dependencies.

        Args:
            git: Git command boundary.
            repair_report: Repair report.
        """

        self._git = git or Git()
        self._repair_report = repair_report or TaskRepairReport()

    def materialize(
        self,
        *,
        main_root: Path,
        task_root: Path,
        manifest: BootstrapManifest,
        previous_state_list: tuple[BootstrapResourceState, ...] = (),
    ) -> tuple[BootstrapResourceState, ...]:
        """Apply one complete manifest without losing an interrupted destination mutation.

        Args:
            main_root: Main root.
            task_root: Task root.
            manifest: Manifest.
            previous_state_list: Ordered previous state values.

        Returns:
            Values in deterministic immutable order.
        """

        submodule_path_set = self._submodule_path_set_get(task_root)
        previous_by_path_map = {item.path: item for item in previous_state_list}
        desired_by_path_map: dict[str, tuple[str, bool, str, str]] = {}
        for resource_class, path_list in manifest.resource_by_key_map.items():
            strategy = resource_class.split("_", maxsplit=1)[0]
            required = "_required_" in resource_class
            for path_text in path_list:
                _submodule_crossing_reject(path_text, submodule_path_set)
                source = main_root / path_text
                source_present = source.exists() or source.is_symlink()
                if not source_present:
                    if required:
                        raise GoalLifecycleError(f"Required bootstrap source is absent: {source}")
                    desired_by_path_map[path_text] = (resource_class, True, "", "")
                    continue
                source_fingerprint = _object_fingerprint(source, declared_root=source)
                desired_fingerprint = (
                    _expected_link_fingerprint(source=source, destination=task_root / path_text)
                    if strategy == "link"
                    else source_fingerprint
                )
                desired_by_path_map[path_text] = (
                    resource_class,
                    False,
                    source_fingerprint,
                    desired_fingerprint,
                )

        known_path_set = set(desired_by_path_map) | set(previous_by_path_map)
        self._unknown_transaction_reject(task_root, known_path_set=known_path_set)
        for path_text, previous in previous_by_path_map.items():
            if path_text in desired_by_path_map:
                continue
            destination = task_root / path_text
            if destination.exists() or destination.is_symlink():
                current = _resource_destination_fingerprint(destination, resource_class=previous.resource_class)
                if current != previous.task_fingerprint:
                    raise GoalLifecycleError(
                        f"Former bootstrap destination contains independent content: {destination}"
                    )
                self._transaction_apply(
                    task_root=task_root,
                    path_text=path_text,
                    strategy="remove",
                    source=None,
                    source_fingerprint="",
                    desired_fingerprint="",
                    previous_fingerprint=previous.task_fingerprint,
                    previous_strategy=previous.resource_class.split("_", maxsplit=1)[0],
                )

        result: list[BootstrapResourceState] = []
        for path_text, (
            resource_class,
            skipped,
            source_fingerprint,
            desired_fingerprint,
        ) in desired_by_path_map.items():
            destination = task_root / path_text
            previous = previous_by_path_map.get(path_text)
            if skipped:
                if destination.exists() or destination.is_symlink():
                    if previous is None or previous.skipped:
                        raise GoalLifecycleError(f"Skipped bootstrap destination unexpectedly exists: {destination}")
                    current = _resource_destination_fingerprint(destination, resource_class=previous.resource_class)
                    if current != previous.task_fingerprint:
                        raise GoalLifecycleError(
                            f"Optional bootstrap destination contains independent content: {destination}"
                        )
                    self._transaction_apply(
                        task_root=task_root,
                        path_text=path_text,
                        strategy="remove",
                        source=None,
                        source_fingerprint="",
                        desired_fingerprint="",
                        previous_fingerprint=previous.task_fingerprint,
                        previous_strategy=previous.resource_class.split("_", maxsplit=1)[0],
                    )
                result.append(
                    BootstrapResourceState(
                        path=path_text,
                        resource_class=resource_class,
                        skipped=True,
                        source_fingerprint="",
                        task_fingerprint="",
                    )
                )
                continue
            strategy = resource_class.split("_", maxsplit=1)[0]
            source = main_root / path_text
            current_fingerprint = (
                _resource_destination_fingerprint(destination, resource_class=resource_class)
                if destination.exists() or destination.is_symlink()
                else ""
            )
            if current_fingerprint != desired_fingerprint:
                if previous is None or previous.skipped or current_fingerprint != previous.task_fingerprint:
                    if current_fingerprint:
                        raise GoalLifecycleError(f"Bootstrap destination contains independent content: {destination}")
                    previous_fingerprint = ""
                else:
                    previous_fingerprint = previous.task_fingerprint
                self._transaction_apply(
                    task_root=task_root,
                    path_text=path_text,
                    strategy=strategy,
                    source=source,
                    source_fingerprint=source_fingerprint,
                    desired_fingerprint=desired_fingerprint,
                    previous_fingerprint=previous_fingerprint,
                    previous_strategy=(
                        previous.resource_class.split("_", maxsplit=1)[0]
                        if previous is not None and not previous.skipped
                        else strategy
                    ),
                )
            actual_fingerprint = _resource_destination_fingerprint(destination, resource_class=resource_class)
            if actual_fingerprint != desired_fingerprint:
                raise GoalLifecycleError(f"Bootstrap destination differs after materialization: {destination}")
            result.append(
                BootstrapResourceState(
                    path=path_text,
                    resource_class=resource_class,
                    skipped=False,
                    source_fingerprint=source_fingerprint,
                    task_fingerprint=actual_fingerprint,
                )
            )
        return tuple(sorted(result, key=lambda item: (item.path, item.resource_class)))

    def validate(
        self,
        *,
        main_root: Path,
        task_root: Path,
        state_list: tuple[BootstrapResourceState, ...],
    ) -> None:
        """Prove every sealed source and destination without silently repairing drift.

        Args:
            main_root: Main root.
            task_root: Task root.
            state_list: Ordered state values.
        """

        self._unknown_transaction_reject(task_root, known_path_set={item.path for item in state_list})
        for item in state_list:
            source = main_root / item.path
            destination = task_root / item.path
            if item.skipped:
                if source.exists() or source.is_symlink() or destination.exists() or destination.is_symlink():
                    raise GoalLifecycleError(f"Skipped bootstrap resource state drifted: {item.path}")
                continue
            source_fingerprint = _object_fingerprint(source, declared_root=source)
            if source_fingerprint != item.source_fingerprint:
                raise GoalLifecycleError(f"Bootstrap source drifted after preparation: {source}")
            task_fingerprint = _resource_destination_fingerprint(destination, resource_class=item.resource_class)
            if task_fingerprint != item.task_fingerprint:
                raise GoalLifecycleError(f"Bootstrap task resource drifted after preparation: {destination}")
            if item.resource_class.startswith("link_"):
                expected_target = os.path.relpath(source, start=destination.parent)
                if not destination.is_symlink() or os.readlink(destination) != expected_target:
                    raise GoalLifecycleError(f"Bootstrap link drifted after preparation: {destination}")

    def _transaction_apply(
        self,
        *,
        task_root: Path,
        path_text: str,
        strategy: str,
        source: Path | None,
        source_fingerprint: str,
        desired_fingerprint: str,
        previous_fingerprint: str,
        previous_strategy: str,
    ) -> None:
        """Resume one durable transaction until the exact desired destination is exposed.

        Args:
            task_root: Task root.
            path_text: Path text.
            strategy: Strategy.
            source: Source.
            source_fingerprint: Source fingerprint.
            desired_fingerprint: Desired fingerprint.
            previous_fingerprint: Previous fingerprint.
            previous_strategy: Previous strategy.
        """

        transaction_root = self._transaction_path_get(task_root, path_text=path_text)
        transaction_preexisting = transaction_root.exists()
        metadata_path = transaction_root / "metadata.json"
        expected = {
            "schema_version": 1,
            "desired_fingerprint": desired_fingerprint,
            "path": path_text,
            "previous_fingerprint": previous_fingerprint,
            "previous_strategy": previous_strategy,
            "source_fingerprint": source_fingerprint,
            "strategy": strategy,
        }
        if transaction_root.exists():
            if transaction_root.is_symlink() or not transaction_root.is_dir():
                raise GoalLifecycleError(
                    f"Bootstrap resource transaction is not a private directory: {transaction_root}"
                )
            if not metadata_path.exists():
                if any(transaction_root.iterdir()):
                    raise GoalLifecycleError(
                        f"Bootstrap resource transaction lost its durable intent: {transaction_root}"
                    )
                transaction_root.rmdir()
        if not transaction_root.exists():
            transaction_root.mkdir(parents=True, mode=0o700)
            directory_sync(transaction_root.parent)
            atomic_json_write(metadata_path, expected)
        elif json_object_load(metadata_path, label="bootstrap resource transaction") != expected:
            raise GoalLifecycleError(
                f"Bootstrap resource transaction differs from requested intent: {transaction_root}"
            )

        destination = task_root / path_text
        destination.parent.mkdir(parents=True, exist_ok=True)
        if transaction_root.stat().st_dev != destination.parent.stat().st_dev:
            raise GoalLifecycleError(f"Bootstrap resource transaction cannot cross filesystems: {destination}")
        replacement = transaction_root / "replacement"
        previous = transaction_root / "previous"
        current_fingerprint = _destination_optional_fingerprint(
            destination,
            strategy=(previous_strategy if destination.exists() or destination.is_symlink() else strategy),
        )
        if current_fingerprint == desired_fingerprint:
            self._transaction_retire(transaction_root)
            if transaction_preexisting:
                self._repair_report.record(f"bootstrap-resource-transaction-recovered:{task_root}:{path_text}")
            return
        if current_fingerprint not in {"", previous_fingerprint}:
            raise GoalLifecycleError(f"Bootstrap destination changed during its transaction: {destination}")
        if source is not None and _object_fingerprint(source, declared_root=source) != source_fingerprint:
            raise GoalLifecycleError(f"Bootstrap source changed during its transaction: {source}")
        if strategy != "remove" and (replacement.exists() or replacement.is_symlink()):
            if _destination_optional_fingerprint(replacement, strategy=strategy) != desired_fingerprint:
                _path_remove(replacement)
        if strategy != "remove" and not (replacement.exists() or replacement.is_symlink()):
            if strategy == "copy":
                if source is None:
                    raise GoalLifecycleError("Copy transaction has no source")
                _copy(source, replacement)
                _tree_sync(replacement)
            elif strategy == "link":
                if source is None:
                    raise GoalLifecycleError("Link transaction has no source")
                replacement.symlink_to(os.path.relpath(source, start=destination.parent))
                directory_sync(replacement.parent)
            else:
                raise GoalLifecycleError(f"Unknown bootstrap transaction strategy: {strategy}")
        if (
            strategy != "remove"
            and _destination_optional_fingerprint(replacement, strategy=strategy) != desired_fingerprint
        ):
            raise GoalLifecycleError(f"Bootstrap replacement staging differs: {replacement}")
        if current_fingerprint == previous_fingerprint and current_fingerprint:
            if previous.exists() or previous.is_symlink():
                if _destination_optional_fingerprint(previous, strategy=previous_strategy) != previous_fingerprint:
                    raise GoalLifecycleError(f"Bootstrap previous staging differs: {previous}")
                _path_remove(destination)
            else:
                destination.replace(previous)
                directory_sync(destination.parent)
        if strategy != "remove":
            if destination.exists() or destination.is_symlink():
                raise GoalLifecycleError(f"Bootstrap destination reappeared during exposure: {destination}")
            replacement.replace(destination)
            directory_sync(destination.parent)
        if _destination_optional_fingerprint(destination, strategy=strategy) != desired_fingerprint:
            raise GoalLifecycleError(f"Bootstrap transaction did not expose the desired destination: {destination}")
        self._transaction_retire(transaction_root)
        if transaction_preexisting:
            self._repair_report.record(f"bootstrap-resource-transaction-recovered:{task_root}:{path_text}")

    def _transaction_retire(self, transaction_root: Path) -> None:
        """Retire one completed staging transaction after destination exposure proof.

        Args:
            transaction_root: Transaction root.
        """

        for name in ("replacement", "previous", "metadata.json"):
            path = transaction_root / name
            if path.exists() or path.is_symlink():
                _path_remove(path)
        unknown = list(transaction_root.iterdir())
        if unknown:
            raise GoalLifecycleError(f"Bootstrap transaction contains unknown content: {transaction_root}")
        parent = transaction_root.parent
        transaction_root.rmdir()
        directory_sync(parent)

    def _unknown_transaction_reject(self, task_root: Path, *, known_path_set: set[str]) -> None:
        """Reject a destination staging transaction not owned by the current declaration.

        Args:
            task_root: Task root.
            known_path_set: Unique known path values.
        """

        root = self._transaction_root_get(task_root)
        if not root.exists():
            return
        if root.is_symlink() or not root.is_dir():
            raise GoalLifecycleError(f"Bootstrap transaction owner is not a private directory: {root}")
        known_name_by_path_map = {hashlib.sha256(path.encode()).hexdigest(): path for path in known_path_set}
        for candidate in root.iterdir():
            if candidate.name not in known_name_by_path_map:
                raise GoalLifecycleError(f"Unknown bootstrap resource transaction blocks recovery: {candidate}")

    def _transaction_root_get(self, task_root: Path) -> Path:
        """Return the private staging root for one resource transaction.

        Args:
            task_root: Task root.

        Returns:
            The transaction root.
        """

        common_prefix = self._git.branch_get(task_root)
        return self._git.common_directory_get(task_root) / "agent-workflows" / "task" / common_prefix / "resource"

    def _transaction_path_get(self, task_root: Path, *, path_text: str) -> Path:
        """Return the private staged object path for one resource transaction.

        Args:
            task_root: Task root.
            path_text: Path text.

        Returns:
            The transaction path.
        """

        return self._transaction_root_get(task_root) / hashlib.sha256(path_text.encode()).hexdigest()

    def _submodule_path_set_get(self, task_root: Path) -> set[str]:
        """Return every committed submodule path below the resource source root.

        Args:
            task_root: Task root.

        Returns:
            The submodule path set.
        """

        payload = self._git.run(task_root, ["ls-files", "--stage", "-z"]).stdout
        result: set[str] = set()
        for entry in payload.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", maxsplit=1)
            if metadata.split(b" ", maxsplit=1)[0] == b"160000":
                try:
                    result.add(raw_path.decode("utf-8"))
                except UnicodeDecodeError as error:
                    raise GoalLifecycleError("Goal lifecycle requires UTF-8 submodule paths") from error
        return result


def _copy(source: Path, destination: Path) -> None:
    """Copy one validated resource graph through private atomic staging.

    Args:
        source: Source.
        destination: Destination.
    """

    if source.is_symlink():
        raise GoalLifecycleError(f"A top-level bootstrap copy may not be a symbolic link: {source}")
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    elif source.is_file():
        shutil.copy2(source, destination, follow_symlinks=False)
    else:
        raise GoalLifecycleError(f"Bootstrap source type is unsupported: {source}")


def _path_remove(path: Path) -> None:
    """Remove one task-owned path without following a replacement symlink.

    Args:
        path: Exact filesystem path.
    """

    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        raise GoalLifecycleError(f"Bootstrap transaction path is unavailable: {path}")


def _tree_sync(path: Path) -> None:
    """Fsync every copied ordinary file and directory before atomic exposure.

    Args:
        path: Exact filesystem path.
    """

    if path.is_file() and not path.is_symlink():
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
        return
    if not path.is_dir() or path.is_symlink():
        return
    for child in path.iterdir():
        _tree_sync(child)
    directory_sync(path)


def _submodule_crossing_reject(path_text: str, submodule_path_set: set[str]) -> None:
    """Reject a resource graph that crosses any committed submodule boundary.

    Args:
        path_text: Path text.
        submodule_path_set: Unique submodule path values.
    """

    resource_path = PurePosixPath(path_text)
    for submodule_text in submodule_path_set:
        submodule_path = PurePosixPath(submodule_text)
        if (
            resource_path == submodule_path
            or submodule_path in resource_path.parents
            or resource_path in submodule_path.parents
        ):
            raise GoalLifecycleError(f"Bootstrap resource crosses a submodule boundary: {path_text}")


def _resource_destination_fingerprint(path: Path, *, resource_class: str) -> str:
    """Fingerprint the exact destination graph created by one resource transaction.

    Args:
        path: Exact filesystem path.
        resource_class: Resource class.

    Returns:
        Resulting text value.
    """

    return (
        _link_fingerprint(path) if resource_class.startswith("link_") else _object_fingerprint(path, declared_root=path)
    )


def _destination_optional_fingerprint(path: Path, *, strategy: str) -> str:
    """Fingerprint an optional destination without treating absence as an error.

    Args:
        path: Exact filesystem path.
        strategy: Strategy.

    Returns:
        Resulting text value.
    """

    if not (path.exists() or path.is_symlink()):
        return ""
    if strategy == "link":
        return _link_fingerprint(path)
    if strategy == "remove":
        return "unexpected-present"
    return _object_fingerprint(path, declared_root=path)


def _expected_link_fingerprint(*, source: Path, destination: Path) -> str:
    """Fingerprint one expected symlink without following its target.

    Args:
        source: Source.
        destination: Destination.

    Returns:
        Resulting text value.
    """

    raw_target = os.fsencode(os.path.relpath(source, start=destination.parent))
    digest = hashlib.sha256()
    digest.update(b"L")
    digest.update(len(raw_target).to_bytes(8, "big"))
    digest.update(raw_target)
    return digest.hexdigest()


def _object_fingerprint(path: Path, *, declared_root: Path) -> str:
    """Fingerprint one ordinary file, directory, or symlink with ownership metadata.

    Args:
        path: Exact filesystem path.
        declared_root: Declared root.

    Returns:
        Resulting text value.
    """

    if path.is_symlink() and path == declared_root:
        raise GoalLifecycleError(f"A declared bootstrap object may not be a symbolic link: {path}")
    if not path.exists():
        raise GoalLifecycleError(f"Bootstrap object is unavailable: {path}")
    digest = hashlib.sha256()
    root = declared_root.resolve(strict=True)
    inode_set: set[tuple[int, int]] = set()

    def visit(current: Path, relative: bytes) -> None:
        """Traverse one object graph node exactly once while rejecting cycles.

        Args:
            current: Current.
            relative: Relative.
        """

        metadata = current.lstat()
        mode = metadata.st_mode
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(stat.S_IMODE(mode).to_bytes(4, "big"))
        if stat.S_ISLNK(mode):
            raw_target = os.readlink(current)
            if os.path.isabs(raw_target):
                raise GoalLifecycleError(f"Bootstrap symbolic link is absolute: {current}")
            resolved_target = (current.parent / raw_target).resolve(strict=True)
            if resolved_target != root and root not in resolved_target.parents:
                raise GoalLifecycleError(f"Bootstrap symbolic link escapes its declared object: {current}")
            target_bytes = os.fsencode(raw_target)
            digest.update(b"L")
            digest.update(len(target_bytes).to_bytes(8, "big"))
            digest.update(target_bytes)
            return
        if stat.S_ISREG(mode):
            inode = (metadata.st_dev, metadata.st_ino)
            if metadata.st_nlink != 1 or inode in inode_set:
                raise GoalLifecycleError(f"Bootstrap regular file has a hardlink: {current}")
            inode_set.add(inode)
            digest.update(b"F")
            with current.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(len(chunk).to_bytes(8, "big"))
                    digest.update(chunk)
            digest.update((0).to_bytes(8, "big"))
            return
        if stat.S_ISDIR(mode):
            digest.update(b"D")
            try:
                child_list = sorted(current.iterdir(), key=lambda item: os.fsencode(item.name))
            except UnicodeEncodeError as error:
                raise GoalLifecycleError("Goal lifecycle requires encodable bootstrap paths") from error
            for child in child_list:
                visit(child, os.fsencode(child.relative_to(declared_root).as_posix()))
            return
        raise GoalLifecycleError(f"Bootstrap object contains a special file: {current}")

    visit(path, b"")
    return digest.hexdigest()


def _link_fingerprint(path: Path) -> str:
    """Fingerprint the literal target and metadata of one symlink.

    Args:
        path: Exact filesystem path.

    Returns:
        Resulting text value.
    """

    if not path.is_symlink():
        raise GoalLifecycleError(f"Bootstrap link is unavailable: {path}")
    raw_target = os.fsencode(os.readlink(path))
    digest = hashlib.sha256()
    digest.update(b"L")
    digest.update(len(raw_target).to_bytes(8, "big"))
    digest.update(raw_target)
    return digest.hexdigest()
