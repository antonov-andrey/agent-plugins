"""Crash-recoverable standard-home installation of the merged lifecycle provider."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
from typing import Protocol

from git_origin.identity import GitOriginError, origin_identity_get
from json_contract import JsonContractError, json_load_strict
from task_workspace.model import TaskWorkspaceError, issue_identifier_validate
from task_workspace.repository import git_command_run, git_command_text_get

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
_CACHEBUSTER_VERSION_PATTERN = re.compile(r"[^+\x00\r\n/]+\+codex\.[a-z0-9][a-z0-9-]*")
_PLUGIN_NAME = "linear-agent-tools"
_REPOSITORY = "git@github.com:antonov-andrey/agent-plugins.git"
_PLUGIN_RELATIVE_PATH = Path("plugins") / _PLUGIN_NAME
_MARKETPLACE_RELATIVE_PATH = Path(".agents/plugins/marketplace.json")


class ProviderInstallationError(RuntimeError):
    """Report an ambiguous or incomplete lifecycle-provider installation."""


class CodexCommandRunner(Protocol):
    """Run one ordinary Codex CLI command under the supplied standard-home environment."""

    def __call__(
        self,
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        """Return one completed direct-argv command."""


def codex_command_run(
    argument_list: Sequence[str],
    *,
    environment_by_name_map: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    """Run one ordinary Codex CLI command without a shell or alternate home."""

    return subprocess.run(
        list(argument_list),
        capture_output=True,
        check=False,
        env=dict(environment_by_name_map),
    )


@dataclass(frozen=True, slots=True)
class ProviderInstallationRequest:
    """Bind reviewed and merged Git identities to one lifecycle-provider version."""

    issue_identifier: str
    base_branch: str
    reviewed_base_commit: str
    reviewed_head_commit: str
    merged_base_commit: str
    expected_version: str

    def __post_init__(self) -> None:
        """Require canonical task, Git, and cachebuster identities."""

        try:
            issue_identifier_validate(self.issue_identifier)
        except TaskWorkspaceError as error:
            raise ProviderInstallationError("Provider installation issue identity is malformed") from error
        if (
            not isinstance(self.base_branch, str)
            or not self.base_branch
            or any(character in self.base_branch for character in ("\x00", "\n", "\r"))
        ):
            raise ProviderInstallationError("Provider installation base branch is malformed")
        for label, value in (
            ("reviewed base", self.reviewed_base_commit),
            ("reviewed head", self.reviewed_head_commit),
            ("merged base", self.merged_base_commit),
        ):
            if not isinstance(value, str) or _COMMIT_PATTERN.fullmatch(value) is None:
                raise ProviderInstallationError(f"Provider installation {label} commit is malformed")
        if len({self.reviewed_base_commit, self.reviewed_head_commit, self.merged_base_commit}) != 3:
            raise ProviderInstallationError("Provider installation Git identities must be distinct")
        if (
            not isinstance(self.expected_version, str)
            or _CACHEBUSTER_VERSION_PATTERN.fullmatch(self.expected_version) is None
        ):
            raise ProviderInstallationError("Provider installation version lacks one canonical cachebuster")


@dataclass(frozen=True, slots=True)
class ProviderInstallationResult:
    """Expose exact merged source, installed cache, and fresh discovery inputs."""

    repository_identity: str
    base_branch: str
    reviewed_base_commit: str
    reviewed_head_commit: str
    merged_base_commit: str
    marketplace_name: str
    marketplace_source_root: str
    plugin_name: str
    previous_version: str
    installed_version: str
    installed_cache_root: str
    skill_name_list: list[str]
    install_performed: bool
    discovery_prompt: str
    expected_discovery_result: dict[str, object]
    schema_version: int = 1

    def payload(self) -> dict[str, object]:
        """Return the direct semantic readback consumed before the fresh process."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class _MarketplaceSource:
    """Identify the one configured local marketplace source for this provider."""

    name: str
    root: Path


class ProviderInstallationReconciler:
    """Synchronize merged marketplace source and install its exact lifecycle provider."""

    def __init__(
        self,
        *,
        bootstrap_repository_root: Path,
        codex_runner: CodexCommandRunner = codex_command_run,
        environment_by_name_map: Mapping[str, str],
        repository: str = _REPOSITORY,
        standard_home: Path,
    ) -> None:
        """Bind the retained branch-local authority and standard-home command boundary."""

        self._bootstrap_repository_root = _canonical_directory_get(
            bootstrap_repository_root,
            label="Branch-local provider repository",
        )
        self._codex_runner = codex_runner
        self._environment_by_name_map = dict(environment_by_name_map)
        self._standard_home = _canonical_directory_get(standard_home, label="Standard home")
        if self._environment_by_name_map.get("HOME") != str(self._standard_home):
            raise ProviderInstallationError("Provider installation requires the standard HOME")
        if "CODEX_HOME" in self._environment_by_name_map:
            raise ProviderInstallationError("Provider installation requires CODEX_HOME to be unset")
        try:
            self._repository_identity = origin_identity_get(repository)
        except GitOriginError as error:
            raise ProviderInstallationError("Provider installation repository identity is malformed") from error

    def reconcile(self, request: ProviderInstallationRequest) -> ProviderInstallationResult:
        """Recover or complete source synchronization and ordinary plugin installation."""

        previous_version, skill_name_list = self._bootstrap_authority_require(request)
        marketplace = self._marketplace_source_get(request)
        self._marketplace_source_synchronize(marketplace, request)
        source_plugin_root = marketplace.root / _PLUGIN_RELATIVE_PATH
        installed_cache_root = (
            self._standard_home / ".codex/plugins/cache" / marketplace.name / _PLUGIN_NAME / request.expected_version
        )
        installed_state = self._plugin_state_get(
            marketplace,
            allowed_version_set={previous_version, request.expected_version},
            is_required=False,
        )
        install_performed = not (
            installed_state == request.expected_version
            and self._installed_cache_matches(
                marketplace.root,
                installed_cache_root,
                request.merged_base_commit,
            )
        )
        if install_performed:
            self._codex_run(
                [
                    "codex",
                    "plugin",
                    "add",
                    f"{_PLUGIN_NAME}@{marketplace.name}",
                    "--json",
                ]
            )

        final_marketplace = self._marketplace_source_get(request)
        if final_marketplace != marketplace:
            raise ProviderInstallationError("Configured marketplace identity changed during provider installation")
        self._marketplace_source_readback_require(final_marketplace, request)
        installed_version = self._plugin_state_get(
            final_marketplace,
            allowed_version_set={request.expected_version},
            is_required=True,
        )
        if installed_version != request.expected_version or not self._installed_cache_matches(
            final_marketplace.root,
            installed_cache_root,
            request.merged_base_commit,
        ):
            raise ProviderInstallationError("Merged lifecycle provider installation readback is incomplete")
        if self._skill_name_list_get(source_plugin_root) != skill_name_list:
            raise ProviderInstallationError("Merged lifecycle provider skill discovery source changed")

        expected_discovery_result = _expected_discovery_result_get(
            cache_root=installed_cache_root,
            skill_name_list=skill_name_list,
            version=request.expected_version,
        )
        discovery_prompt = _discovery_prompt_get(
            cache_root=installed_cache_root,
            marketplace_source_root=marketplace.root,
        )
        return ProviderInstallationResult(
            repository_identity=self._repository_identity,
            base_branch=request.base_branch,
            reviewed_base_commit=request.reviewed_base_commit,
            reviewed_head_commit=request.reviewed_head_commit,
            merged_base_commit=request.merged_base_commit,
            marketplace_name=marketplace.name,
            marketplace_source_root=str(marketplace.root),
            plugin_name=_PLUGIN_NAME,
            previous_version=previous_version,
            installed_version=installed_version,
            installed_cache_root=str(installed_cache_root),
            skill_name_list=skill_name_list,
            install_performed=install_performed,
            discovery_prompt=discovery_prompt,
            expected_discovery_result=expected_discovery_result,
        )

    def _bootstrap_authority_require(self, request: ProviderInstallationRequest) -> tuple[str, list[str]]:
        """Require the retained branch-local provider to be the exact reviewed candidate."""

        self._repository_identity_require(
            self._bootstrap_repository_root,
            branch=f"linear/{request.issue_identifier.lower()}",
        )
        head_commit = self._git_text(self._bootstrap_repository_root, ("rev-parse", "HEAD"))
        if head_commit != request.reviewed_head_commit:
            raise ProviderInstallationError("Branch-local provider is not the exact reviewed head")
        if (
            git_command_run(
                self._bootstrap_repository_root,
                ("merge-base", "--is-ancestor", request.reviewed_base_commit, request.reviewed_head_commit),
                check=False,
            ).returncode
            != 0
        ):
            raise ProviderInstallationError("Reviewed provider head does not descend from its reviewed base")
        try:
            current_manifest = (
                self._bootstrap_repository_root / _PLUGIN_RELATIVE_PATH / ".codex-plugin/plugin.json"
            ).read_bytes()
            previous_manifest = git_command_run(
                self._bootstrap_repository_root,
                (
                    "show",
                    f"{request.reviewed_base_commit}:{_PLUGIN_RELATIVE_PATH.as_posix()}/.codex-plugin/plugin.json",
                ),
            ).stdout
        except (OSError, TaskWorkspaceError) as error:
            raise ProviderInstallationError("Reviewed lifecycle provider manifests are unavailable") from error
        current_version = _plugin_version_get(current_manifest)
        if current_version != request.expected_version:
            raise ProviderInstallationError("Branch-local provider manifest is not the expected cachebuster")
        previous_version = _plugin_version_get(previous_manifest)
        if (
            previous_version == request.expected_version
            or previous_version.split("+", 1)[0] != request.expected_version.split("+", 1)[0]
        ):
            raise ProviderInstallationError("Reviewed provider candidate lacks one new cachebuster")
        return previous_version, self._skill_name_list_get(self._bootstrap_repository_root / _PLUGIN_RELATIVE_PATH)

    def _marketplace_source_get(self, request: ProviderInstallationRequest) -> _MarketplaceSource:
        """Discover one configured local marketplace by plugin and repository identity."""

        payload = self._codex_json_get(["codex", "plugin", "marketplace", "list", "--json"])
        if (
            not isinstance(payload, dict)
            or set(payload) != {"marketplaces"}
            or not isinstance(payload["marketplaces"], list)
        ):
            raise ProviderInstallationError("Codex marketplace list has another shape")
        candidate_list: list[_MarketplaceSource] = []
        for item in payload["marketplaces"]:
            if not isinstance(item, dict):
                raise ProviderInstallationError("Codex marketplace entry has another shape")
            source = item.get("marketplaceSource")
            if source is None:
                continue
            if not isinstance(source, dict):
                raise ProviderInstallationError("Codex marketplace source has another shape")
            if source.get("sourceType") != "local":
                continue
            if set(item) != {"name", "root", "marketplaceSource"} or set(source) != {"sourceType", "source"}:
                raise ProviderInstallationError("Local Codex marketplace entry has another shape")
            name = item["name"]
            root_text = item["root"]
            source_text = source["source"]
            if not isinstance(name, str) or not name or not isinstance(root_text, str) or source_text != root_text:
                raise ProviderInstallationError("Local Codex marketplace identity is malformed")
            root = _canonical_directory_get(Path(root_text), label="Local Codex marketplace root")
            if not self._marketplace_declares_plugin(root, name):
                continue
            if self._repository_origin_identity_get(root) != self._repository_identity:
                continue
            self._repository_identity_require(root, branch=request.base_branch)
            candidate_list.append(_MarketplaceSource(name=name, root=root))
        if len(candidate_list) != 1:
            raise ProviderInstallationError(
                "Exactly one configured local marketplace must own the lifecycle provider repository"
            )
        return candidate_list[0]

    def _marketplace_declares_plugin(self, root: Path, marketplace_name: str) -> bool:
        """Return whether one local marketplace declares the fixed lifecycle provider."""

        manifest_path = root / _MARKETPLACE_RELATIVE_PATH
        try:
            metadata = manifest_path.lstat()
            payload = json_load_strict(manifest_path.read_bytes())
        except (OSError, JsonContractError) as error:
            raise ProviderInstallationError("Local Codex marketplace manifest is unavailable") from error
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or not isinstance(payload, dict):
            raise ProviderInstallationError("Local Codex marketplace manifest is not one ordinary JSON object")
        if payload.get("name") != marketplace_name or not isinstance(payload.get("plugins"), list):
            raise ProviderInstallationError("Local Codex marketplace manifest identity differs from configuration")
        match_list = [
            item for item in payload["plugins"] if isinstance(item, dict) and item.get("name") == _PLUGIN_NAME
        ]
        if not match_list:
            return False
        if len(match_list) != 1 or match_list[0].get("source") != {
            "source": "local",
            "path": f"./{_PLUGIN_RELATIVE_PATH.as_posix()}",
        }:
            raise ProviderInstallationError("Lifecycle provider marketplace entry is ambiguous")
        return True

    def _marketplace_source_synchronize(
        self,
        marketplace: _MarketplaceSource,
        request: ProviderInstallationRequest,
    ) -> None:
        """Fast-forward only the exact clean reviewed base to the merged base commit."""

        local_commit = self._git_text(marketplace.root, ("rev-parse", "HEAD"))
        if local_commit not in {request.reviewed_base_commit, request.merged_base_commit}:
            raise ProviderInstallationError("Configured marketplace source HEAD is outside exact merge recovery")
        fetch_url = self._repository_fetch_url_get(marketplace.root)
        try:
            git_command_run(
                marketplace.root,
                (
                    "fetch",
                    "--no-write-fetch-head",
                    "--no-tags",
                    "--prune",
                    fetch_url,
                    f"+refs/heads/{request.base_branch}:refs/remotes/origin/{request.base_branch}",
                ),
                mutation=True,
            )
        except TaskWorkspaceError as error:
            raise ProviderInstallationError("Configured marketplace source could not fetch its merged base") from error
        self._merged_commit_require(marketplace.root, request)
        remote_commit = self._git_text(
            marketplace.root,
            ("rev-parse", f"refs/remotes/origin/{request.base_branch}"),
        )
        if remote_commit != request.merged_base_commit:
            raise ProviderInstallationError("Remote marketplace base is not the exact merged commit")
        if local_commit == request.reviewed_base_commit:
            try:
                git_command_run(
                    marketplace.root,
                    (
                        "merge",
                        "--ff-only",
                        request.merged_base_commit,
                    ),
                    mutation=True,
                )
            except TaskWorkspaceError as error:
                raise ProviderInstallationError(
                    "Configured marketplace source could not fast-forward exactly"
                ) from error
        self._marketplace_source_readback_require(marketplace, request)

    def _marketplace_source_readback_require(
        self,
        marketplace: _MarketplaceSource,
        request: ProviderInstallationRequest,
    ) -> None:
        """Require exact clean merged source and its reviewed plugin manifest."""

        self._repository_identity_require(marketplace.root, branch=request.base_branch)
        if self._git_text(marketplace.root, ("rev-parse", "HEAD")) != request.merged_base_commit:
            raise ProviderInstallationError("Configured marketplace source did not reach the merged commit")
        try:
            manifest_bytes = (marketplace.root / _PLUGIN_RELATIVE_PATH / ".codex-plugin/plugin.json").read_bytes()
        except OSError as error:
            raise ProviderInstallationError("Merged marketplace lifecycle-provider manifest is unavailable") from error
        version = _plugin_version_get(manifest_bytes)
        if version != request.expected_version:
            raise ProviderInstallationError("Merged marketplace source has another lifecycle-provider version")

    def _merged_commit_require(self, repository_root: Path, request: ProviderInstallationRequest) -> None:
        """Require the exact merge strategy result used by source synchronization recovery."""

        parent_list = self._git_text(
            repository_root,
            ("rev-list", "--parents", "-n", "1", request.merged_base_commit),
        ).split()
        if parent_list != [
            request.merged_base_commit,
            request.reviewed_base_commit,
            request.reviewed_head_commit,
        ]:
            raise ProviderInstallationError("Merged marketplace base has another ordered parent identity")
        merged_tree = self._git_text(repository_root, ("rev-parse", f"{request.merged_base_commit}^{{tree}}"))
        reviewed_tree = self._git_text(repository_root, ("rev-parse", f"{request.reviewed_head_commit}^{{tree}}"))
        if merged_tree != reviewed_tree:
            raise ProviderInstallationError("Merged marketplace base does not contain the exact reviewed tree")

    def _repository_identity_require(self, root: Path, *, branch: str) -> None:
        """Require one clean exact-branch checkout of the configured repository."""

        try:
            top_level = _canonical_directory_get(
                Path(git_command_text_get(root, ("rev-parse", "--show-toplevel"))),
                label="Provider repository top level",
            )
            current_branch = git_command_text_get(root, ("symbolic-ref", "--quiet", "--short", "HEAD"))
            status_text = git_command_text_get(root, ("status", "--porcelain=v1", "--untracked-files=normal"))
            git_command_run(root, ("check-ref-format", "--branch", branch))
        except (OSError, TaskWorkspaceError) as error:
            raise ProviderInstallationError("Provider repository identity could not be read") from error
        origin = self._repository_origin_identity_get(root)
        if top_level != root or origin != self._repository_identity or current_branch != branch:
            raise ProviderInstallationError("Provider repository identity differs from the exact recovery target")
        if status_text:
            raise ProviderInstallationError("Provider repository must be clean before installation recovery")

    @staticmethod
    def _repository_origin_identity_get(root: Path) -> str:
        """Return one canonical credential-free repository origin identity."""

        try:
            return origin_identity_get(git_command_text_get(root, ("remote", "get-url", "origin")))
        except (GitOriginError, TaskWorkspaceError) as error:
            raise ProviderInstallationError("Provider repository origin identity could not be read") from error

    def _repository_fetch_url_get(self, root: Path) -> str:
        """Return one exact effective fetch URL matching the provider repository."""

        try:
            output = git_command_run(
                root,
                ("remote", "get-url", "--all", "origin"),
                mutation=True,
            ).stdout.decode("utf-8", errors="strict")
        except (UnicodeDecodeError, TaskWorkspaceError) as error:
            raise ProviderInstallationError("Provider repository fetch destination could not be read") from error
        value_list = output.splitlines()
        if len(value_list) != 1 or not value_list[0]:
            raise ProviderInstallationError("Provider repository requires one exact fetch destination")
        try:
            identity = origin_identity_get(value_list[0])
        except GitOriginError as error:
            raise ProviderInstallationError("Provider repository fetch destination is malformed") from error
        if identity != self._repository_identity:
            raise ProviderInstallationError("Provider repository fetch destination differs from its owner")
        return value_list[0]

    def _plugin_state_get(
        self,
        marketplace: _MarketplaceSource,
        *,
        allowed_version_set: set[str],
        is_required: bool,
    ) -> str:
        """Return the current installed plugin version after strict CLI readback."""

        payload = self._codex_json_get(
            [
                "codex",
                "plugin",
                "list",
                "--marketplace",
                marketplace.name,
                "--available",
                "--json",
            ]
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != {"installed", "available"}
            or not isinstance(payload["installed"], list)
            or not isinstance(payload["available"], list)
        ):
            raise ProviderInstallationError("Codex plugin list has another shape")
        match_list = [
            item
            for item in [*payload["installed"], *payload["available"]]
            if isinstance(item, dict) and item.get("pluginId") == f"{_PLUGIN_NAME}@{marketplace.name}"
        ]
        if len(match_list) != 1:
            raise ProviderInstallationError("Codex plugin list lacks one exact lifecycle provider")
        item = match_list[0]
        source = item.get("source")
        marketplace_source = item.get("marketplaceSource")
        expected_source_root = marketplace.root / _PLUGIN_RELATIVE_PATH
        if (
            item.get("name") != _PLUGIN_NAME
            or item.get("marketplaceName") != marketplace.name
            or not isinstance(source, dict)
            or source.get("source") != "local"
            or source.get("path") != str(expected_source_root)
            or marketplace_source
            != {
                "sourceType": "local",
                "source": str(marketplace.root),
            }
        ):
            raise ProviderInstallationError("Codex lifecycle provider source identity differs from its marketplace")
        if item.get("installed") is not True:
            if is_required:
                raise ProviderInstallationError("Merged lifecycle provider is not installed")
            return ""
        if item.get("enabled") is not True:
            raise ProviderInstallationError("Lifecycle provider is installed but disabled")
        version = item.get("version")
        if not isinstance(version, str) or version not in allowed_version_set:
            raise ProviderInstallationError("Installed lifecycle provider has an unexpected version")
        return version

    def _installed_cache_matches(
        self,
        source_repository_root: Path,
        cache_root: Path,
        merged_commit: str,
    ) -> bool:
        """Return whether installed ordinary files exactly reproduce the merged tracked plugin source."""

        if not _optional_canonical_directory_matches(cache_root):
            return False
        tracked_file_by_relative_path_map = self._tracked_plugin_file_map_get(
            source_repository_root,
            merged_commit,
        )
        cache_file_by_relative_path_map = _ordinary_file_map_get(cache_root)
        if cache_file_by_relative_path_map is None:
            return False
        if set(cache_file_by_relative_path_map) != set(tracked_file_by_relative_path_map):
            return False
        for relative_path, (source_path, executable) in tracked_file_by_relative_path_map.items():
            cache_path = cache_file_by_relative_path_map[relative_path]
            try:
                if source_path.read_bytes() != cache_path.read_bytes():
                    return False
                cache_executable = bool(cache_path.stat().st_mode & stat.S_IXUSR)
            except OSError:
                return False
            if cache_executable != executable:
                return False
        return True

    def _tracked_plugin_file_map_get(
        self,
        source_repository_root: Path,
        merged_commit: str,
    ) -> dict[str, tuple[Path, bool]]:
        """Return every ordinary tracked provider file at the exact merged source."""

        if self._git_text(source_repository_root, ("rev-parse", "HEAD")) != merged_commit:
            raise ProviderInstallationError("Installed source comparison requires the exact merged checkout")
        try:
            output = git_command_run(
                source_repository_root,
                ("ls-files", "--stage", "-z", "--", _PLUGIN_RELATIVE_PATH.as_posix()),
            ).stdout
            entry_list = [entry for entry in output.split(b"\0") if entry]
            result: dict[str, tuple[Path, bool]] = {}
            prefix = f"{_PLUGIN_RELATIVE_PATH.as_posix()}/"
            for entry in entry_list:
                metadata, path_bytes = entry.split(b"\t", 1)
                mode_bytes, _object_id, stage_bytes = metadata.split(b" ")
                path_text = path_bytes.decode("utf-8", errors="strict")
                if stage_bytes != b"0" or mode_bytes not in {b"100644", b"100755"} or not path_text.startswith(prefix):
                    raise ProviderInstallationError("Merged lifecycle provider contains a non-ordinary tracked entry")
                relative_path = path_text.removeprefix(prefix)
                source_path = source_repository_root / path_text
                source_metadata = source_path.lstat()
                if not stat.S_ISREG(source_metadata.st_mode) or source_path.is_symlink():
                    raise ProviderInstallationError("Merged lifecycle provider working tree is not ordinary")
                result[relative_path] = (source_path, mode_bytes == b"100755")
        except (OSError, UnicodeDecodeError, ValueError, TaskWorkspaceError) as error:
            raise ProviderInstallationError("Merged lifecycle provider tracked source is unavailable") from error
        if not result:
            raise ProviderInstallationError("Merged lifecycle provider tracked source is empty")
        return result

    def _skill_name_list_get(self, plugin_root: Path) -> list[str]:
        """Return the exact expected installed skill names from reviewed ordinary files."""

        skills_root = plugin_root / "skills"
        try:
            skill_directory_list = sorted(
                path for path in skills_root.iterdir() if path.is_dir() and not path.is_symlink()
            )
        except OSError as error:
            raise ProviderInstallationError("Lifecycle provider skills are unavailable") from error
        result: list[str] = []
        for skill_directory in skill_directory_list:
            skill_path = skill_directory / "SKILL.md"
            try:
                metadata = skill_path.lstat()
            except OSError as error:
                raise ProviderInstallationError("Lifecycle provider skill contract is unavailable") from error
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ProviderInstallationError("Lifecycle provider skill contract is not one ordinary file")
            result.append(f"{_PLUGIN_NAME}:{skill_directory.name}")
        if not result:
            raise ProviderInstallationError("Lifecycle provider exposes no skills")
        return result

    def _codex_json_get(self, argument_list: list[str]) -> object:
        """Run one Codex CLI read and decode one strict JSON result."""

        completed_process = self._codex_run(argument_list)
        try:
            return json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise ProviderInstallationError("Codex command returned malformed JSON") from error

    def _codex_run(self, argument_list: list[str]) -> subprocess.CompletedProcess[bytes]:
        """Run one direct Codex command and require successful completion."""

        try:
            completed_process = self._codex_runner(
                argument_list,
                environment_by_name_map=self._environment_by_name_map,
            )
        except OSError as error:
            raise ProviderInstallationError("Codex command could not start") from error
        if completed_process.returncode != 0:
            raise ProviderInstallationError("Codex command failed")
        return completed_process

    @staticmethod
    def _git_text(repository_root: Path, argument_list: Sequence[str]) -> str:
        """Return strict Git text as a provider-installation error."""

        try:
            return git_command_text_get(repository_root, argument_list)
        except (UnicodeDecodeError, TaskWorkspaceError) as error:
            raise ProviderInstallationError("Provider installation Git read failed") from error


def standard_home_environment_get(
    environment_by_name_map: Mapping[str, str] | None = None,
) -> tuple[Path, dict[str, str]]:
    """Require the current OS user's standard HOME with CODEX_HOME absent."""

    source = os.environ if environment_by_name_map is None else environment_by_name_map
    account = pwd.getpwuid(os.getuid())
    if source.get("HOME") != account.pw_dir or "CODEX_HOME" in source:
        raise ProviderInstallationError("Provider installation requires standard HOME and unset CODEX_HOME")
    return Path(account.pw_dir).resolve(strict=True), dict(source)


def _plugin_version_get(payload_bytes: bytes) -> str:
    """Read one exact plugin version from an ordinary manifest payload."""

    try:
        payload = json_load_strict(payload_bytes)
    except JsonContractError as error:
        raise ProviderInstallationError("Lifecycle provider manifest is malformed") from error
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version:
        raise ProviderInstallationError("Lifecycle provider manifest lacks one version")
    return version


def _canonical_directory_get(path: Path, *, label: str) -> Path:
    """Return one physical canonical absolute directory."""

    if not path.is_absolute() or str(path).startswith("//") or path.is_symlink():
        raise ProviderInstallationError(f"{label} is not one canonical physical directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ProviderInstallationError(f"{label} is unavailable") from error
    if resolved != path or not resolved.is_dir():
        raise ProviderInstallationError(f"{label} is not one canonical physical directory")
    return resolved


def _optional_canonical_directory_matches(path: Path) -> bool:
    """Return whether an optional installed cache root is one physical directory."""

    try:
        return path.is_absolute() and not path.is_symlink() and path.resolve(strict=True) == path and path.is_dir()
    except OSError:
        return False


def _ordinary_file_map_get(root: Path) -> dict[str, Path] | None:
    """Return ordinary installed files, ignoring only Python bytecode cache artifacts."""

    result: dict[str, Path] = {}
    pending_directory_list = [root]
    while pending_directory_list:
        directory = pending_directory_list.pop()
        try:
            entry_list = sorted(directory.iterdir())
        except OSError:
            return None
        for path in entry_list:
            try:
                metadata = path.lstat()
            except OSError:
                return None
            relative_path = path.relative_to(root)
            if stat.S_ISDIR(metadata.st_mode):
                if path.is_symlink():
                    return None
                pending_directory_list.append(path)
                continue
            if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                return None
            if "__pycache__" in relative_path.parts:
                if path.suffix != ".pyc":
                    return None
                continue
            result[relative_path.as_posix()] = path
    return result


def _expected_discovery_result_get(
    *,
    cache_root: Path,
    skill_name_list: list[str],
    version: str,
) -> dict[str, object]:
    """Return the exact semantic result required from fresh generic discovery."""

    return {
        "schema_version": 1,
        "plugin_name": _PLUGIN_NAME,
        "plugin_version": version,
        "installed_source_root": str(cache_root),
        "skill_name_list": skill_name_list,
        "ready": True,
    }


def _discovery_prompt_get(
    *,
    cache_root: Path,
    marketplace_source_root: Path,
) -> str:
    """Return the complete read-only prompt for one fresh generic Codex process."""

    return (
        "Perform one read-only installation discovery without invoking, opening, or following any skill and without "
        "mutating files, configuration, plugins, Git, GitHub, or Linear. Use only the initial skill-catalog metadata "
        "supplied to this fresh process plus ordinary reads of these two exact plugin manifests: "
        f"{cache_root / '.codex-plugin/plugin.json'} and "
        f"{marketplace_source_root / _PLUGIN_RELATIVE_PATH / '.codex-plugin/plugin.json'}. Require both manifests to "
        "have the same nonempty name and version. Select catalog entries solely because their absolute source locator "
        f"has the exact shape {cache_root}/skills/<directory>/SKILL.md; do not select them from expected names. Require "
        "at least one selected entry, each selected catalog name to start with the manifest name followed by a colon, "
        "and no catalog entry with that prefix to have a locator outside that installed skills directory. On success "
        "return only one JSON object with exactly these keys: schema_version=1, plugin_name from the manifest, "
        "plugin_version from the manifest, installed_source_root equal to the installed cache root above, "
        "skill_name_list equal to the lexicographically sorted selected catalog names, and ready=true. On any failure "
        'return only {"ready":false,"schema_version":1}. Emit no Markdown or other text.'
    )
