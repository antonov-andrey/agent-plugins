"""Exact private ownership of the one self-hosting coordination worktree."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path

from goal_lifecycle.error import GoalLifecycleError
from goal_lifecycle.git import Git
from goal_lifecycle.io import atomic_json_write, json_object_load
from goal_lifecycle.model import commit_validate, common_prefix_validate


@dataclass(frozen=True, slots=True)
class CoordinationBootstrapException:
    """Bind the one pre-cutover project-goals worktree and physical carriers."""

    branch_name: str
    common_prefix: str
    coordination_bootstrap_commit: str
    goal_carrier_path: str
    sealed_goal_sha256: str
    sealed_specification_sha256: str
    specification_carrier_path: str
    task_root: str
    schema_version: int = 1

    @classmethod
    def from_payload(cls, payload: object) -> "CoordinationBootstrapException":
        expected = {
            "schema_version",
            "branch_name",
            "common_prefix",
            "coordination_bootstrap_commit",
            "goal_carrier_path",
            "sealed_goal_sha256",
            "sealed_specification_sha256",
            "specification_carrier_path",
            "task_root",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload.get("schema_version") != 1:
            raise GoalLifecycleError("Coordination bootstrap exception has another shape")
        common_prefix = common_prefix_validate(str(payload["common_prefix"]))
        if payload["branch_name"] != common_prefix:
            raise GoalLifecycleError("Coordination bootstrap branch differs from its task identity")
        for field_name in ("sealed_goal_sha256", "sealed_specification_sha256"):
            value = payload[field_name]
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise GoalLifecycleError(f"Coordination bootstrap {field_name} is malformed")
        for field_name in ("goal_carrier_path", "specification_carrier_path", "task_root"):
            value = payload[field_name]
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise GoalLifecycleError(f"Coordination bootstrap {field_name} must be absolute")
        return cls(
            branch_name=common_prefix,
            common_prefix=common_prefix,
            coordination_bootstrap_commit=commit_validate(
                payload["coordination_bootstrap_commit"],
                label="coordination bootstrap commit",
            ),
            goal_carrier_path=str(payload["goal_carrier_path"]),
            sealed_goal_sha256=str(payload["sealed_goal_sha256"]),
            sealed_specification_sha256=str(payload["sealed_specification_sha256"]),
            specification_carrier_path=str(payload["specification_carrier_path"]),
            task_root=str(payload["task_root"]),
        )

    def payload_get(self) -> dict[str, object]:
        return asdict(self)


def coordination_bootstrap_exception_path_get(goals_root: Path, *, git: Git | None = None) -> Path:
    command = git or Git()
    return command.common_directory_get(goals_root) / "agent-workflows" / "coordination-bootstrap-exception.json"


def coordination_bootstrap_exception_optional_get(
    goals_root: Path,
    *,
    git: Git | None = None,
) -> CoordinationBootstrapException | None:
    command = git or Git()
    path = coordination_bootstrap_exception_path_get(goals_root, git=command)
    if not path.exists():
        return None
    return CoordinationBootstrapException.from_payload(json_object_load(path, label="coordination bootstrap exception"))


def coordination_bootstrap_exception_write(
    goals_root: Path,
    *,
    common_prefix: str,
    goal_carrier_path: Path,
    specification_carrier_path: Path,
    task_root: Path,
    git: Git | None = None,
) -> CoordinationBootstrapException:
    """Bind an already published one-time bootstrap without reading legacy state."""

    command = git or Git()
    common_prefix_validate(common_prefix)
    root = command.root_get(goals_root)
    task = command.root_get(task_root)
    if task != task_root.resolve(strict=True) or task.parent != root / ".worktree":
        raise GoalLifecycleError("Coordination bootstrap task root is outside its exact worktree container")
    if command.branch_get(root) != "main" or command.branch_get(task) != common_prefix:
        raise GoalLifecycleError("Coordination bootstrap checkouts have another branch identity")
    command.clean_require(root)
    command.clean_require(task)
    command.fetch(root)
    bootstrap_commit = command.commit_get(task)
    if command.commit_get(root) != command.commit_get(root, "refs/remotes/origin/main"):
        raise GoalLifecycleError("Coordination bootstrap main is not synchronized")
    if command.commit_get(task, f"refs/remotes/origin/{common_prefix}") != bootstrap_commit:
        raise GoalLifecycleError("Coordination bootstrap task branch is not fully pushed")
    command.ancestor_require(
        root, bootstrap_commit, command.commit_get(root), label="coordination bootstrap publication"
    )
    specification_path, specification_sha256 = _carrier_get(specification_carrier_path, label="specification")
    goal_path, goal_sha256 = _carrier_get(goal_carrier_path, label="goal")
    if hashlib.sha256((root / common_prefix / "spec.md").read_bytes()).hexdigest() != specification_sha256:
        raise GoalLifecycleError("Tracked specification differs from its bootstrap carrier")
    if hashlib.sha256((root / common_prefix / "goal.md").read_bytes()).hexdigest() != goal_sha256:
        raise GoalLifecycleError("Tracked goal differs from its bootstrap carrier")
    exception = CoordinationBootstrapException(
        branch_name=common_prefix,
        common_prefix=common_prefix,
        coordination_bootstrap_commit=bootstrap_commit,
        goal_carrier_path=str(goal_path),
        sealed_goal_sha256=goal_sha256,
        sealed_specification_sha256=specification_sha256,
        specification_carrier_path=str(specification_path),
        task_root=str(task),
    )
    path = coordination_bootstrap_exception_path_get(root, git=command)
    if path.exists():
        existing = CoordinationBootstrapException.from_payload(
            json_object_load(path, label="coordination bootstrap exception")
        )
        if existing != exception:
            raise GoalLifecycleError("Existing coordination bootstrap exception differs")
        return existing
    atomic_json_write(path, exception.payload_get())
    return exception


def coordination_bootstrap_exception_validate(
    goals_root: Path,
    exception: CoordinationBootstrapException,
    *,
    git: Git | None = None,
) -> None:
    """Prove the exact clean bootstrap worktree, branch, publication, and carriers."""

    command = git or Git()
    root = command.root_get(goals_root)
    task_root = Path(exception.task_root).resolve(strict=True)
    if task_root.parent != root / ".worktree" or command.root_get(task_root) != task_root:
        raise GoalLifecycleError("Coordination bootstrap worktree identity differs")
    worktree_root_set = _worktree_root_set_get(root, git=command)
    if worktree_root_set != {root, task_root}:
        raise GoalLifecycleError("Coordination repository contains an unowned worktree")
    if set((root / ".worktree").iterdir()) != {task_root}:
        raise GoalLifecycleError("Coordination worktree container contains an unowned entry")
    if (
        (root / "worktree-bootstrap.yaml").exists()
        or (task_root / "worktree-bootstrap.yaml").exists()
        or (task_root / "worktree-bootstrap.toml").exists()
    ):
        raise GoalLifecycleError("Coordination repository may not retain a bootstrap manifest")
    command.clean_require(task_root)
    if command.branch_get(task_root) != exception.branch_name:
        raise GoalLifecycleError("Coordination bootstrap worktree branch changed")
    if command.commit_get(task_root) != exception.coordination_bootstrap_commit:
        raise GoalLifecycleError("Coordination bootstrap worktree commit changed")
    if (
        command.commit_get(task_root, f"refs/remotes/origin/{exception.branch_name}")
        != exception.coordination_bootstrap_commit
    ):
        raise GoalLifecycleError("Coordination bootstrap remote branch changed")
    command.ancestor_require(
        root,
        exception.coordination_bootstrap_commit,
        command.commit_get(root),
        label="coordination bootstrap main ancestry",
    )
    for carrier_path, expected_sha256, label in (
        (Path(exception.specification_carrier_path), exception.sealed_specification_sha256, "specification"),
        (Path(exception.goal_carrier_path), exception.sealed_goal_sha256, "goal"),
    ):
        _path, actual_sha256 = _carrier_get(carrier_path, label=label)
        if actual_sha256 != expected_sha256:
            raise GoalLifecycleError(f"Coordination bootstrap {label} carrier changed")
    if (
        hashlib.sha256((root / exception.common_prefix / "spec.md").read_bytes()).hexdigest()
        != exception.sealed_specification_sha256
    ):
        raise GoalLifecycleError("Tracked bootstrap specification changed")
    if (
        hashlib.sha256((root / exception.common_prefix / "goal.md").read_bytes()).hexdigest()
        != exception.sealed_goal_sha256
    ):
        raise GoalLifecycleError("Tracked bootstrap goal changed")


def _carrier_get(path: Path, *, label: str) -> tuple[Path, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise GoalLifecycleError(f"Coordination bootstrap {label} carrier must be one ordinary file")
    resolved = path.resolve(strict=True)
    return resolved, hashlib.sha256(resolved.read_bytes()).hexdigest()


def _worktree_root_set_get(root: Path, *, git: Git) -> set[Path]:
    payload = git.run(root, ["worktree", "list", "--porcelain", "-z"]).stdout
    return {
        Path(item.removeprefix(b"worktree ").decode("utf-8")).resolve(strict=True)
        for item in payload.split(b"\0")
        if item.startswith(b"worktree ")
    }
