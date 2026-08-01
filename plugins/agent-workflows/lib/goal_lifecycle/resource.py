"""Bootstrap resource materialization, fingerprinting, and validation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

from goal_lifecycle.cleanup_manifest import BootstrapManifest
from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.model import BootstrapResourceState


class BootstrapResourceManager:
    """Materialize and prove the exact copy/link resources of one worktree."""

    def __init__(self, *, git: Git | None = None) -> None:
        self._git = git or Git()

    def materialize(
        self,
        *,
        main_root: Path,
        task_root: Path,
        manifest: BootstrapManifest,
    ) -> tuple[BootstrapResourceState, ...]:
        submodule_path_set = self._submodule_path_set_get(task_root)
        result: list[BootstrapResourceState] = []
        for resource_class, path_list in manifest.resource_by_key_map.items():
            strategy = resource_class.split("_", maxsplit=1)[0]
            required = "_required_" in resource_class
            for path_text in path_list:
                self._submodule_crossing_reject(path_text, submodule_path_set)
                source = main_root / path_text
                destination = task_root / path_text
                source_present = source.exists() or source.is_symlink()
                if not source_present:
                    if required:
                        raise GoalLifecycleError(f"Required bootstrap source is absent: {source}")
                    if destination.exists() or destination.is_symlink():
                        raise GoalLifecycleError(f"Skipped bootstrap destination unexpectedly exists: {destination}")
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
                source_fingerprint = _object_fingerprint(source, declared_root=source)
                if strategy == "link":
                    expected_target = os.path.relpath(source, start=destination.parent)
                    if destination.exists() or destination.is_symlink():
                        if not destination.is_symlink() or os.readlink(destination) != expected_target:
                            raise GoalLifecycleError(f"Existing bootstrap link differs from declaration: {destination}")
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.symlink_to(expected_target)
                    task_fingerprint = _link_fingerprint(destination)
                else:
                    if destination.exists() or destination.is_symlink():
                        task_fingerprint = _object_fingerprint(destination, declared_root=destination)
                        if task_fingerprint != source_fingerprint:
                            raise GoalLifecycleError(f"Existing bootstrap copy differs from source: {destination}")
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        _copy(source, destination)
                        task_fingerprint = _object_fingerprint(destination, declared_root=destination)
                    if task_fingerprint != source_fingerprint:
                        raise GoalLifecycleError(
                            f"Bootstrap copy fingerprint differs after materialization: {destination}"
                        )
                result.append(
                    BootstrapResourceState(
                        path=path_text,
                        resource_class=resource_class,
                        skipped=False,
                        source_fingerprint=source_fingerprint,
                        task_fingerprint=task_fingerprint,
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
            if item.resource_class.startswith("link_"):
                expected_target = os.path.relpath(source, start=destination.parent)
                if not destination.is_symlink() or os.readlink(destination) != expected_target:
                    raise GoalLifecycleError(f"Bootstrap link drifted after preparation: {destination}")
                task_fingerprint = _link_fingerprint(destination)
            else:
                task_fingerprint = _object_fingerprint(destination, declared_root=destination)
            if task_fingerprint != item.task_fingerprint:
                raise GoalLifecycleError(f"Bootstrap task resource drifted after preparation: {destination}")

    def _submodule_path_set_get(self, task_root: Path) -> set[str]:
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

    @staticmethod
    def _submodule_crossing_reject(path_text: str, submodule_path_set: set[str]) -> None:
        resource_path = PurePosixPath(path_text)
        for submodule_text in submodule_path_set:
            submodule_path = PurePosixPath(submodule_text)
            if (
                resource_path == submodule_path
                or resource_path in submodule_path.parents
                or submodule_path in resource_path.parents
            ):
                raise GoalLifecycleError(f"Bootstrap resource crosses a submodule boundary: {path_text}")


def _copy(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise GoalLifecycleError(f"A top-level bootstrap copy may not be a symbolic link: {source}")
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    elif source.is_file():
        shutil.copy2(source, destination, follow_symlinks=False)
    else:
        raise GoalLifecycleError(f"Bootstrap source type is unsupported: {source}")


def _object_fingerprint(path: Path, *, declared_root: Path) -> str:
    if path.is_symlink() and path == declared_root:
        raise GoalLifecycleError(f"A declared bootstrap object may not be a symbolic link: {path}")
    if not path.exists():
        raise GoalLifecycleError(f"Bootstrap object is unavailable: {path}")
    digest = hashlib.sha256()
    root = declared_root.resolve(strict=True)
    inode_set: set[tuple[int, int]] = set()

    def visit(current: Path, relative: bytes) -> None:
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
                child_relative = os.fsencode(child.relative_to(declared_root).as_posix())
                visit(child, child_relative)
            return
        raise GoalLifecycleError(f"Bootstrap object contains a special file: {current}")

    visit(path, b"")
    return digest.hexdigest()


def _link_fingerprint(path: Path) -> str:
    if not path.is_symlink():
        raise GoalLifecycleError(f"Bootstrap link is unavailable: {path}")
    raw_target = os.fsencode(os.readlink(path))
    digest = hashlib.sha256()
    digest.update(b"L")
    digest.update(len(raw_target).to_bytes(8, "big"))
    digest.update(raw_target)
    return digest.hexdigest()
