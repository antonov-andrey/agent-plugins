"""Verify crash-recoverable installation of the merged lifecycle provider."""

from __future__ import annotations

from dataclasses import dataclass
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

from task_merge.provider_installation import (
    ProviderInstallationError,
    ProviderInstallationReconciler,
    ProviderInstallationRequest,
    standard_home_environment_get,
)

BASE_BRANCH = "2026-08-04-agent-development-workflow"
ISSUE_IDENTIFIER = "AND-45"
OLD_VERSION = "0.1.0+codex.old"
EXPECTED_VERSION = "0.1.0+codex.test-new"
PLUGIN_RELATIVE_PATH = Path("plugins/linear-agent-tools")
INSTALL_SCRIPT = REPOSITORY_ROOT / "plugins/linear-agent-tools/skills/task-merge/scripts/provider_install.py"


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
    assert first.expected_discovery_result == {
        "schema_version": 1,
        "plugin_name": "linear-agent-tools",
        "plugin_version": EXPECTED_VERSION,
        "installed_source_root": str(
            fixture.home_root / ".codex/plugins/cache/agent-plugins/linear-agent-tools" / EXPECTED_VERSION
        ),
        "skill_name_list": first.skill_name_list,
        "ready": True,
    }
    assert "Do not activate or follow any named skill" in first.discovery_prompt
    assert EXPECTED_VERSION in first.discovery_prompt
    assert str(fixture.home_root / ".codex/plugins/cache/agent-plugins/linear-agent-tools" / EXPECTED_VERSION) in (
        first.discovery_prompt
    )


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
