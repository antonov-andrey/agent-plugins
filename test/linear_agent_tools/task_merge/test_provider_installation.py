"""Verify crash-recoverable installation of the merged lifecycle provider."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
from typing import Mapping, Sequence

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools" / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_origin.identity import origin_identity_get
from git_origin.transport import GitTransportDestination
import task_merge.provider_installation as provider_installation_module
from task_merge.provider_installation import (
    ProviderInstallationError,
    ProviderInstallationReconciler,
    ProviderInstallationRequest,
    standard_home_environment_get,
)
import task_workspace.repository as repository_module

BASE_BRANCH = "2026-08-04-agent-development-workflow"
ISSUE_IDENTIFIER = "AND-45"
OLD_VERSION = "0.1.0+codex.old"
EXPECTED_VERSION = "0.1.0+codex.test-new"
PLUGIN_RELATIVE_PATH = Path("plugins/linear-agent-tools")
INSTALL_SCRIPT = REPOSITORY_ROOT / "plugins/linear-agent-tools/skills/task-merge/scripts/provider_install.py"


@pytest.fixture(autouse=True)
def _local_repository_transport_test_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject local bare remotes only into this provider-installation test harness."""

    strict_destination_get = repository_module.git_transport_destination_get
    strict_relative_destination_get = repository_module.git_relative_transport_destination_get

    def destination_get(value: str) -> GitTransportDestination:
        path = Path(value)
        if path.is_absolute():
            return GitTransportDestination(
                identity=origin_identity_get(value),
                protocol="file",
                style="file",
                url=value,
            )
        return strict_destination_get(value)

    def relative_destination_get(
        parent: GitTransportDestination,
        value: str,
    ) -> GitTransportDestination:
        if parent.protocol == "file":
            return destination_get(value)
        return strict_relative_destination_get(parent, value)

    monkeypatch.setattr(repository_module, "git_transport_destination_get", destination_get)
    monkeypatch.setattr(repository_module, "git_relative_transport_destination_get", relative_destination_get)


def _git(repository_root: Path, *argument_list: str) -> str:
    """Run one deterministic local Git command and return stripped output."""

    completed_process = subprocess.run(
        ["git", "-C", str(repository_root), *argument_list],
        capture_output=True,
        check=True,
        text=True,
    )
    return completed_process.stdout.strip()


def _plugin_source_write(repository_root: Path, *, version: str, task_merge_text: str) -> None:
    """Write the minimal marketplace-backed lifecycle provider fixture."""

    plugin_root = repository_root / PLUGIN_RELATIVE_PATH
    (plugin_root / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / "skills/task-merge").mkdir(parents=True, exist_ok=True)
    (plugin_root / "skills/task-cleanup").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "linear-agent-tools", "version": version}, indent=2) + "\n",
        encoding="utf-8",
    )
    (plugin_root / "skills/task-merge/SKILL.md").write_text(task_merge_text, encoding="utf-8")
    (plugin_root / "skills/task-cleanup/SKILL.md").write_text("# Task Cleanup\n", encoding="utf-8")
    marketplace_path = repository_root / ".agents/plugins/marketplace.json"
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    marketplace_path.write_text(
        json.dumps(
            {
                "name": "agent-plugins",
                "interface": {"displayName": "Agent Plugins"},
                "plugins": [
                    {
                        "name": "linear-agent-tools",
                        "source": {"source": "local", "path": "./plugins/linear-agent-tools"},
                        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                        "category": "Productivity",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True, slots=True)
class _ProviderRepositoryFixture:
    """Expose exact Git and standard-home identities for one installation test."""

    bootstrap_root: Path
    home_root: Path
    marketplace_root: Path
    remote_root: Path
    reviewed_base_commit: str
    reviewed_head_commit: str
    merged_base_commit: str

    def request_get(self) -> ProviderInstallationRequest:
        """Return the exact reviewed and merged provider request."""

        return ProviderInstallationRequest(
            issue_identifier=ISSUE_IDENTIFIER,
            base_branch=BASE_BRANCH,
            reviewed_base_commit=self.reviewed_base_commit,
            reviewed_head_commit=self.reviewed_head_commit,
            merged_base_commit=self.merged_base_commit,
            expected_version=EXPECTED_VERSION,
        )


def _provider_repository_fixture_create(tmp_path: Path) -> _ProviderRepositoryFixture:
    """Create old base, reviewed head, exact merge, and stale marketplace checkout."""

    remote_root = tmp_path / "agent-plugins.git"
    subprocess.run(["git", "init", "--bare", str(remote_root)], check=True, capture_output=True)
    bootstrap_root = tmp_path / "bootstrap"
    subprocess.run(
        ["git", "init", "--initial-branch", BASE_BRANCH, str(bootstrap_root)],
        check=True,
        capture_output=True,
    )
    _git(bootstrap_root, "config", "user.name", "Provider Test")
    _git(bootstrap_root, "config", "user.email", "provider@example.com")
    _git(bootstrap_root, "remote", "add", "origin", str(remote_root))
    _plugin_source_write(bootstrap_root, version=OLD_VERSION, task_merge_text="# Old Task Merge\n")
    _git(bootstrap_root, "add", ".")
    _git(bootstrap_root, "commit", "-m", "Old provider")
    reviewed_base_commit = _git(bootstrap_root, "rev-parse", "HEAD")
    _git(bootstrap_root, "push", "origin", BASE_BRANCH)

    _git(bootstrap_root, "checkout", "-b", f"linear/{ISSUE_IDENTIFIER.lower()}")
    _plugin_source_write(bootstrap_root, version=EXPECTED_VERSION, task_merge_text="# Merged Task Merge\n")
    _git(bootstrap_root, "add", ".")
    _git(bootstrap_root, "commit", "-m", "Reviewed provider")
    reviewed_head_commit = _git(bootstrap_root, "rev-parse", "HEAD")
    _git(bootstrap_root, "push", "origin", f"linear/{ISSUE_IDENTIFIER.lower()}")

    marketplace_root = tmp_path / "marketplace"
    subprocess.run(
        ["git", "clone", "--branch", BASE_BRANCH, str(remote_root), str(marketplace_root)],
        check=True,
        capture_output=True,
    )
    integration_root = tmp_path / "integration"
    subprocess.run(
        ["git", "clone", "--branch", BASE_BRANCH, str(remote_root), str(integration_root)],
        check=True,
        capture_output=True,
    )
    _git(integration_root, "config", "user.name", "Provider Test")
    _git(integration_root, "config", "user.email", "provider@example.com")
    _git(integration_root, "merge", "--no-ff", f"origin/linear/{ISSUE_IDENTIFIER.lower()}", "-m", "Merge provider")
    merged_base_commit = _git(integration_root, "rev-parse", "HEAD")
    _git(integration_root, "push", "origin", BASE_BRANCH)

    home_root = tmp_path / "home"
    home_root.mkdir()
    old_cache_root = home_root / ".codex/plugins/cache/agent-plugins/linear-agent-tools" / OLD_VERSION
    old_cache_root.parent.mkdir(parents=True)
    shutil.copytree(marketplace_root / PLUGIN_RELATIVE_PATH, old_cache_root)
    return _ProviderRepositoryFixture(
        bootstrap_root=bootstrap_root.resolve(),
        home_root=home_root.resolve(),
        marketplace_root=marketplace_root.resolve(),
        remote_root=remote_root.resolve(),
        reviewed_base_commit=reviewed_base_commit,
        reviewed_head_commit=reviewed_head_commit,
        merged_base_commit=merged_base_commit,
    )


class _CodexRunner:
    """Expose local marketplace/list/add semantics without mutating the real Codex home."""

    def __init__(
        self,
        fixture: _ProviderRepositoryFixture,
        *,
        installed_version: str = OLD_VERSION,
        omitted_cache_file: str = "",
    ) -> None:
        """Initialize one stateful fake Codex installation."""

        self._fixture = fixture
        self._installed_version = installed_version
        self._omitted_cache_file = omitted_cache_file
        self.add_count = 0
        self.call_list: list[list[str]] = []

    def __call__(
        self,
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        """Return exact marketplace or plugin state and perform only fake-home installation."""

        assert environment_by_name_map["HOME"] == str(self._fixture.home_root)
        assert "CODEX_HOME" not in environment_by_name_map
        argument_list = list(argument_list)
        self.call_list.append(argument_list)
        if argument_list == ["codex", "plugin", "marketplace", "list", "--json"]:
            payload = {
                "marketplaces": [
                    {
                        "name": "agent-plugins",
                        "root": str(self._fixture.marketplace_root),
                        "marketplaceSource": {
                            "sourceType": "local",
                            "source": str(self._fixture.marketplace_root),
                        },
                    }
                ]
            }
        elif argument_list == [
            "codex",
            "plugin",
            "list",
            "--marketplace",
            "agent-plugins",
            "--available",
            "--json",
        ]:
            payload = {
                "installed": [self._plugin_entry_get()],
                "available": [],
            }
        elif argument_list == [
            "codex",
            "plugin",
            "add",
            "linear-agent-tools@agent-plugins",
            "--json",
        ]:
            self.add_count += 1
            source_root = self._fixture.marketplace_root / PLUGIN_RELATIVE_PATH
            version = json.loads((source_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
            cache_root = self._fixture.home_root / ".codex/plugins/cache/agent-plugins/linear-agent-tools" / version
            if cache_root.exists():
                shutil.rmtree(cache_root)
            shutil.copytree(source_root, cache_root)
            if self._omitted_cache_file:
                (cache_root / self._omitted_cache_file).unlink()
            self._installed_version = version
            payload = {"pluginId": "linear-agent-tools@agent-plugins", "version": version}
        else:
            raise AssertionError(argument_list)
        return subprocess.CompletedProcess(
            argument_list,
            0,
            stdout=(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"),
            stderr=b"",
        )

    def _plugin_entry_get(self) -> dict[str, object]:
        """Return the installed plugin readback shape owned by the Codex CLI."""

        return {
            "pluginId": "linear-agent-tools@agent-plugins",
            "name": "linear-agent-tools",
            "marketplaceName": "agent-plugins",
            "version": self._installed_version,
            "installed": True,
            "enabled": True,
            "source": {
                "source": "local",
                "path": str(self._fixture.marketplace_root / PLUGIN_RELATIVE_PATH),
            },
            "marketplaceSource": {
                "sourceType": "local",
                "source": str(self._fixture.marketplace_root),
            },
            "installPolicy": "AVAILABLE",
            "authPolicy": "ON_INSTALL",
        }


def _reconciler_get(
    fixture: _ProviderRepositoryFixture,
    runner: _CodexRunner,
) -> ProviderInstallationReconciler:
    """Return one test installation boundary with an isolated fake standard home."""

    return ProviderInstallationReconciler(
        bootstrap_repository_root=fixture.bootstrap_root,
        codex_runner=runner,
        environment_by_name_map={"HOME": str(fixture.home_root), "PATH": os.environ["PATH"]},
        repository=str(fixture.remote_root),
        standard_home=fixture.home_root,
    )


def test_provider_installation_fast_forwards_installs_and_recovers_from_exact_readback(tmp_path: Path) -> None:
    """Old source installs once; exact merged source and cache make the next recovery read-only."""

    fixture = _provider_repository_fixture_create(tmp_path)
    runner = _CodexRunner(fixture)
    reconciler = _reconciler_get(fixture, runner)

    first = reconciler.reconcile(fixture.request_get())
    second = reconciler.reconcile(fixture.request_get())

    assert _git(fixture.marketplace_root, "rev-parse", "HEAD") == fixture.merged_base_commit
    assert (
        _git(fixture.marketplace_root, "rev-parse", f"refs/remotes/origin/{BASE_BRANCH}") == fixture.merged_base_commit
    )
    assert runner.add_count == 1
    assert first.install_performed
    assert not second.install_performed
    assert first.installed_version == EXPECTED_VERSION
    assert first.marketplace_name == "agent-plugins"
    assert first.skill_name_list == [
        "linear-agent-tools:task-cleanup",
        "linear-agent-tools:task-merge",
    ]
    assert first.enabled
    assert first.marketplace_source_root == str(fixture.marketplace_root)
    assert first.installed_cache_root == str(
        fixture.home_root / ".codex/plugins/cache/agent-plugins/linear-agent-tools" / EXPECTED_VERSION
    )
    assert set(first.payload()) == {
        "base_branch",
        "enabled",
        "install_performed",
        "installed_cache_root",
        "installed_version",
        "marketplace_name",
        "marketplace_source_root",
        "merged_base_commit",
        "plugin_name",
        "previous_version",
        "repository_identity",
        "reviewed_base_commit",
        "reviewed_head_commit",
        "schema_version",
        "skill_name_list",
    }


@pytest.mark.parametrize(
    ("raw_origin", "canonical_origin"),
    [
        (
            "https://GITHUB.com/Antonov-Andrey/Agent-Plugins",
            "https://github.com/antonov-andrey/agent-plugins.git",
        ),
        (
            "https://github.com/Antonov-Andrey/Agent-Plugins.git",
            "https://github.com/antonov-andrey/agent-plugins.git",
        ),
        (
            "ssh://git@GITHUB.com/Antonov-Andrey/Agent-Plugins",
            "ssh://git@github.com/antonov-andrey/agent-plugins.git",
        ),
        (
            "git@github.com:Antonov-Andrey/Agent-Plugins",
            "git@github.com:antonov-andrey/agent-plugins.git",
        ),
    ],
)
def test_provider_installation_forwards_only_canonical_origin_to_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_origin: str,
    canonical_origin: str,
) -> None:
    """Accepted origin aliases normalize once before the canonical-only Git runner."""

    fixture = _provider_repository_fixture_create(tmp_path)
    _git(
        fixture.marketplace_root,
        "fetch",
        str(fixture.remote_root),
        f"+refs/heads/{BASE_BRANCH}:refs/remotes/origin/{BASE_BRANCH}",
    )
    _git(fixture.bootstrap_root, "remote", "set-url", "origin", raw_origin)
    _git(fixture.marketplace_root, "remote", "set-url", "origin", raw_origin)
    runner = _CodexRunner(fixture)
    reconciler = ProviderInstallationReconciler(
        bootstrap_repository_root=fixture.bootstrap_root,
        codex_runner=runner,
        environment_by_name_map={"HOME": str(fixture.home_root), "PATH": os.environ["PATH"]},
        repository="git@github.com:antonov-andrey/agent-plugins.git",
        standard_home=fixture.home_root,
    )
    original = provider_installation_module.git_command_run
    fetch_url_list: list[str] = []

    def run(
        repository_root: Path,
        argument_list: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if repository_root == fixture.marketplace_root and argument_list[0] == "fetch":
            fetch_url_list.append(argument_list[4])
            assert kwargs["transport_url_list"] == (canonical_origin,)
            return subprocess.CompletedProcess(argument_list, 0, stdout=b"", stderr=b"")
        return original(repository_root, argument_list, **kwargs)

    monkeypatch.setattr(provider_installation_module, "git_command_run", run)

    result = reconciler.reconcile(fixture.request_get())

    assert fetch_url_list == [canonical_origin]
    assert result.repository_identity == "github.com/antonov-andrey/agent-plugins"
    assert _git(fixture.marketplace_root, "rev-parse", "HEAD") == fixture.merged_base_commit


def test_provider_installation_rejects_repository_local_post_merge_hook_before_git(tmp_path: Path) -> None:
    """Merged-source recovery fails closed before a malicious repository-local hook can execute."""

    fixture = _provider_repository_fixture_create(tmp_path)
    hook_root = tmp_path / "malicious-hooks"
    hook_root.mkdir()
    marker = tmp_path / "post-merge-hook-ran"
    hook_path = hook_root / "post-merge"
    hook_path.write_text(f"#!/bin/sh\nprintf attacked > {marker}\n", encoding="utf-8")
    hook_path.chmod(0o755)
    _git(fixture.marketplace_root, "config", "core.hooksPath", str(hook_root))

    with pytest.raises(ProviderInstallationError, match="identity could not be read"):
        _reconciler_get(fixture, _CodexRunner(fixture)).reconcile(fixture.request_get())

    assert _git(fixture.marketplace_root, "rev-parse", "HEAD") == fixture.reviewed_base_commit
    assert not marker.exists()


def test_provider_installation_ignores_replace_ref_that_falsifies_reviewed_ancestry(tmp_path: Path) -> None:
    """A replace ref cannot authorize an unrelated branch head as reviewed provider history."""

    fixture = _provider_repository_fixture_create(tmp_path)
    reviewed_tree = _git(fixture.bootstrap_root, "rev-parse", f"{fixture.reviewed_head_commit}^{{tree}}")
    foreign_head = _git(
        fixture.bootstrap_root,
        "commit-tree",
        reviewed_tree,
        "-m",
        "Create unrelated provider candidate",
    )
    _git(fixture.bootstrap_root, "reset", "--hard", foreign_head)
    _git(fixture.bootstrap_root, "replace", foreign_head, fixture.reviewed_head_commit)
    ambient_ancestry = subprocess.run(
        [
            "git",
            "-C",
            str(fixture.bootstrap_root),
            "merge-base",
            "--is-ancestor",
            fixture.reviewed_base_commit,
            foreign_head,
        ],
        check=False,
        capture_output=True,
    )
    assert ambient_ancestry.returncode == 0
    runner = _CodexRunner(fixture)

    with pytest.raises(ProviderInstallationError, match="does not descend"):
        _reconciler_get(fixture, runner).reconcile(replace(fixture.request_get(), reviewed_head_commit=foreign_head))

    assert _git(fixture.marketplace_root, "rev-parse", "HEAD") == fixture.reviewed_base_commit
    assert runner.add_count == 0


def test_provider_installation_rejects_dirty_marketplace_before_fetch_or_install(tmp_path: Path) -> None:
    """A dirty configured source remains at the reviewed old base and no install starts."""

    fixture = _provider_repository_fixture_create(tmp_path)
    runner = _CodexRunner(fixture)
    (fixture.marketplace_root / "untracked.txt").write_text("foreign\n", encoding="utf-8")

    with pytest.raises(ProviderInstallationError, match="must be clean"):
        _reconciler_get(fixture, runner).reconcile(fixture.request_get())

    assert _git(fixture.marketplace_root, "rev-parse", "HEAD") == fixture.reviewed_base_commit
    assert runner.add_count == 0


def test_provider_installation_rejects_foreign_clean_marketplace_head_before_fetch_or_install(tmp_path: Path) -> None:
    """A clean local commit outside old-or-merged recovery is never replaced or installed."""

    fixture = _provider_repository_fixture_create(tmp_path)
    runner = _CodexRunner(fixture)
    (fixture.marketplace_root / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    _git(fixture.marketplace_root, "config", "user.name", "Provider Test")
    _git(fixture.marketplace_root, "config", "user.email", "provider@example.com")
    _git(fixture.marketplace_root, "add", "foreign.txt")
    _git(fixture.marketplace_root, "commit", "-m", "Foreign local commit")
    foreign_commit = _git(fixture.marketplace_root, "rev-parse", "HEAD")

    with pytest.raises(ProviderInstallationError, match="outside exact merge recovery"):
        _reconciler_get(fixture, runner).reconcile(fixture.request_get())

    assert _git(fixture.marketplace_root, "rev-parse", "HEAD") == foreign_commit
    assert runner.add_count == 0


def test_provider_installation_retries_only_incomplete_normal_install_phase(tmp_path: Path) -> None:
    """A failed exact cache readback stops, then ordinary add can complete from natural state."""

    fixture = _provider_repository_fixture_create(tmp_path)
    incomplete_runner = _CodexRunner(fixture, omitted_cache_file="skills/task-merge/SKILL.md")

    with pytest.raises(ProviderInstallationError, match="installation readback is incomplete"):
        _reconciler_get(fixture, incomplete_runner).reconcile(fixture.request_get())

    assert _git(fixture.marketplace_root, "rev-parse", "HEAD") == fixture.merged_base_commit
    assert incomplete_runner.add_count == 1
    recovery_runner = _CodexRunner(fixture, installed_version=EXPECTED_VERSION)
    result = _reconciler_get(fixture, recovery_runner).reconcile(fixture.request_get())
    assert result.install_performed
    assert recovery_runner.add_count == 1


def test_standard_home_boundary_rejects_codex_home_and_substitute_home() -> None:
    """The executable CLI has no alternate-home or CODEX_HOME compatibility path."""

    account = pwd.getpwuid(os.getuid())
    with pytest.raises(ProviderInstallationError, match="standard HOME"):
        standard_home_environment_get({"HOME": account.pw_dir, "CODEX_HOME": "/tmp/alternate"})
    with pytest.raises(ProviderInstallationError, match="standard HOME"):
        standard_home_environment_get({"HOME": "/tmp/substitute"})


def test_provider_installation_script_is_directly_reachable() -> None:
    """The branch-local task-merge owner exposes one executable closed parser."""

    assert os.access(INSTALL_SCRIPT, os.X_OK)
    completed_process = subprocess.run(
        [sys.executable, str(INSTALL_SCRIPT), "--help"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed_process.returncode == 0
    assert "--merged-base-commit" in completed_process.stdout
    assert "--expected-version" in completed_process.stdout
