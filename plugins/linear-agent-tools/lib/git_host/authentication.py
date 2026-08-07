"""GitHub principal and invocation-local Git credential ownership."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
from urllib.parse import quote

from json_contract import JsonContractError, json_load_strict

from git_host.command import CommandRunner, command_run
from git_host.model import GitHubContractError, RepositoryIdentity

_LOGIN_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")


@dataclass(frozen=True, slots=True)
class GitHubPrincipal:
    """Bind one authenticated GitHub account across API and Git operations."""

    login: str
    user_id: int
    node_id: str

    def __post_init__(self) -> None:
        """Validate one exact human account identity."""

        if not isinstance(self.login, str) or _LOGIN_PATTERN.fullmatch(self.login) is None:
            raise GitHubContractError("GitHub executing login has another shape")
        if isinstance(self.user_id, bool) or not isinstance(self.user_id, int) or self.user_id < 1:
            raise GitHubContractError("GitHub executing user ID has another shape")
        if (
            not isinstance(self.node_id, str)
            or not self.node_id
            or any(character in self.node_id for character in ("\x00", "\n", "\r"))
        ):
            raise GitHubContractError("GitHub executing node ID has another shape")


class GitHubAuthenticationBoundary:
    """Bind API authority and an ephemeral Git credential helper to one principal."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        """Initialize one authenticated direct-command dependency.

        Args:
            runner: Optional deterministic command runner.
        """

        self._runner = runner or command_run

    def principal_get(self) -> GitHubPrincipal:
        """Read the exact account selected by authenticated ``gh`` resolution."""

        payload = self._json_get(
            ("api", "--hostname", "github.com", "user"),
            label="GitHub executing identity",
        )
        if not isinstance(payload, dict):
            raise GitHubContractError("GitHub executing identity has another shape")
        try:
            return GitHubPrincipal(
                login=payload["login"],
                user_id=payload["id"],
                node_id=payload["node_id"],
            )
        except KeyError as error:
            raise GitHubContractError("GitHub executing identity has another shape") from error

    def principal_require(self, expected: GitHubPrincipal) -> GitHubPrincipal:
        """Require current ``gh`` authentication to remain the inspected principal.

        Args:
            expected: Principal used for repository authority inspection.

        Returns:
            Fresh matching principal.
        """

        if not isinstance(expected, GitHubPrincipal):
            raise GitHubContractError("Expected GitHub principal has another shape")
        current = self.principal_get()
        if current != expected:
            raise GitHubContractError("Authenticated GitHub identity changed before Git mutation")
        return current

    def principal_identity_require(self, *, login: str, node_id: str) -> GitHubPrincipal:
        """Require current authentication to match immutable provider terminal identity.

        Args:
            login: Provider-confirmed terminal login.
            node_id: Provider-confirmed terminal GraphQL user identity.

        Returns:
            Fresh matching principal including its numeric database ID.
        """

        current = self.principal_get()
        if current.login.casefold() != login.casefold() or current.node_id != node_id:
            raise GitHubContractError("Authenticated GitHub identity differs from merged provider identity")
        return current

    def repository_permission_get(
        self,
        *,
        repository: RepositoryIdentity,
        principal: GitHubPrincipal,
    ) -> str:
        """Read exact repository permission for the authenticated principal.

        Args:
            repository: Exact GitHub repository.
            principal: Fresh authenticated identity.

        Returns:
            GitHub repository permission name.
        """

        if not isinstance(repository, RepositoryIdentity) or not isinstance(principal, GitHubPrincipal):
            raise GitHubContractError("GitHub repository access identity has another shape")
        payload = self._json_get(
            (
                "api",
                "--hostname",
                "github.com",
                f"repos/{repository.value}/collaborators/{quote(principal.login, safe='')}/permission",
            ),
            label="GitHub executing repository permission",
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("user"), dict):
            raise GitHubContractError("GitHub executing repository permission has another shape")
        user = payload["user"]
        if (
            payload.get("permission") not in {"admin", "maintain", "write", "triage", "read"}
            or user.get("login", "").casefold() != principal.login.casefold()
            or user.get("id") != principal.user_id
            or user.get("node_id") != principal.node_id
        ):
            raise GitHubContractError("GitHub executing repository permission has another shape")
        return payload["permission"]

    @staticmethod
    def git_credential_config_argument_list(principal: GitHubPrincipal) -> tuple[str, ...]:
        """Return invocation-local Git config backed by the exact ``gh`` account.

        The helper resolves the token inside Git's credential subprocess. No token
        enters this Python process, its command arguments, logs, or persistent Git
        configuration.

        Args:
            principal: Exact authenticated account selected for the operation.

        Returns:
            Direct Git ``-c`` arguments that clear inherited helpers and add one
            account-bound helper.
        """

        if not isinstance(principal, GitHubPrincipal):
            raise GitHubContractError("Git credential principal has another shape")
        helper = (
            '!f() { if [ "$1" = get ]; then '
            "printf '%s\\n' 'username=x-access-token'; "
            "printf '%s' 'password='; "
            f"gh auth token --hostname github.com --user '{principal.login}'; "
            "fi; }; f"
        )
        return (
            "-c",
            "credential.helper=",
            "-c",
            f"credential.helper={helper}",
            "-c",
            "credential.useHttpPath=true",
        )

    def _json_get(self, argument_list: Sequence[str], *, label: str) -> object:
        """Run one successful ``gh`` read and require strict JSON."""

        completed_process = self._runner(["gh", *argument_list])
        if completed_process.returncode != 0 or not completed_process.stdout:
            raise GitHubContractError(f"{label} failed")
        try:
            return json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise GitHubContractError(f"{label} response is malformed") from error
