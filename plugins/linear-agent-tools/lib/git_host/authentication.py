"""GitHub principal and invocation-local Git credential ownership."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
from secrets import token_urlsafe
import shlex
from threading import Thread
from urllib.parse import quote

from json_contract import JsonContractError, json_load_strict

from git_host.command import CommandRunner, command_closed_run, command_run
from git_host.model import GitHubContractError, RepositoryIdentity

_LOGIN_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_PROACTIVE_AUTHENTICATION_CONFIG_ARGUMENT_LIST = ("-c", "http.proactiveAuth=basic")
_PROBE_USERNAME = "linear-agent-proactive-authentication-probe"


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
            or any(character in self.node_id for character in ("\x00", "\t", "\n", "\r"))
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

    def principal_identity_require(self, *, login: str, user_id: int, node_id: str) -> GitHubPrincipal:
        """Require current authentication to match immutable provider terminal identity.

        Args:
            login: Provider-confirmed terminal login.
            user_id: Provider-confirmed terminal numeric database identity.
            node_id: Provider-confirmed terminal GraphQL user identity.

        Returns:
            Fresh matching principal including its numeric database ID.
        """
        expected = GitHubPrincipal(login=login, user_id=user_id, node_id=node_id)
        current = self.principal_get()
        if current != expected:
            raise GitHubContractError("Authenticated GitHub identity differs from merged provider identity")
        return current

    def credential_validate(self, principal: GitHubPrincipal, repository: RepositoryIdentity) -> None:
        """Validate the helper's actual token principal without returning its token.

        Args:
            principal: Exact protection and write-authority principal.
            repository: Sole HTTPS credential destination.
        """

        if not isinstance(principal, GitHubPrincipal) or not isinstance(repository, RepositoryIdentity):
            raise GitHubContractError("Git credential identity has another shape")
        completed_process = command_closed_run(
            self._runner,
            ["/bin/sh", "-c", f"{_credential_helper_shell_get(principal, repository)} get >/dev/null"],
            input_text=_credential_request_get(repository),
        )
        if completed_process.returncode != 0 or completed_process.stdout:
            raise GitHubContractError("Git credential token differs from the approved GitHub principal")

    def git_http_proactive_authentication_require(self) -> None:
        """Require Git to fill the invocation helper before its first HTTP request.

        Git versions that ignore ``http.proactiveAuth`` fail this behavioral
        probe.  The loopback credential is a single-use non-authoritative
        marker; the approved GitHub token remains confined to its
        credential-helper subprocess.
        """

        probe_password = token_urlsafe(32)
        expected_authorization = "Basic " + b64encode(f"{_PROBE_USERNAME}:{probe_password}".encode("utf-8")).decode(
            "ascii"
        )
        server = GitHttpProactiveAuthenticationProbeServer(("127.0.0.1", 0))
        server_thread = Thread(target=server.serve_forever, name="git-http-authentication-probe", daemon=True)
        server_thread.start()
        try:
            port = server.server_address[1]
            helper = (
                '!f() { if [ "$1" = get ]; then '
                f"printf '%s\\n' 'username={_PROBE_USERNAME}' 'password={probe_password}'; "
                "fi; }; f"
            )
            command_closed_run(
                self._runner,
                [
                    "git",
                    "-c",
                    "credential.helper=",
                    "-c",
                    f"credential.helper={helper}",
                    "-c",
                    "credential.interactive=never",
                    "-c",
                    "credential.useHttpPath=true",
                    *_PROACTIVE_AUTHENTICATION_CONFIG_ARGUMENT_LIST,
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.http.allow=always",
                    "ls-remote",
                    "--refs",
                    f"http://127.0.0.1:{port}/linear-agent-proactive-authentication-probe.git",
                    "refs/heads/probe",
                ],
            )
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()
        if not server.authorization_list or server.authorization_list[0] != expected_authorization:
            raise GitHubContractError("Git HTTP transport cannot prove proactive invocation-helper authentication")

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

    def _json_get(self, argument_list: Sequence[str], *, label: str) -> object:
        """Run one successful ``gh`` read and require strict JSON."""

        completed_process = command_closed_run(self._runner, ["gh", *argument_list])
        if completed_process.returncode != 0 or not completed_process.stdout:
            raise GitHubContractError(f"{label} failed")
        try:
            return json_load_strict(completed_process.stdout)
        except JsonContractError as error:
            raise GitHubContractError(f"{label} response is malformed") from error


def git_credential_config_argument_list_get(
    principal: GitHubPrincipal,
    repository: RepositoryIdentity,
) -> tuple[str, ...]:
    """Return the sole invocation-local helper for an exact approved principal.

    The helper resolves and validates the token entirely within its subprocess.
    No token enters Python, process arguments or environments, logs, or
    persistent configuration.

    Args:
        principal: Exact authenticated account selected for the operation.
        repository: Sole HTTPS destination allowed to receive the credential.

    Returns:
        Direct Git ``-c`` arguments that add one account-bound helper.
    """

    if not isinstance(principal, GitHubPrincipal) or not isinstance(repository, RepositoryIdentity):
        raise GitHubContractError("Git credential identity has another shape")
    return (
        *_PROACTIVE_AUTHENTICATION_CONFIG_ARGUMENT_LIST,
        "-c",
        "credential.helper=",
        "-c",
        f"credential.helper=!{_credential_helper_shell_get(principal, repository)}",
        "-c",
        "credential.useHttpPath=true",
        "-c",
        "credential.username=x-access-token",
    )


class GitHttpProactiveAuthenticationProbeServer(ThreadingHTTPServer):
    """Capture the first loopback request made by the probed Git executable."""

    daemon_threads = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        """Bind one private loopback listener without any external credential.

        Args:
            server_address: Exact loopback host and ephemeral port.
        """

        self.authorization_list: list[str | None] = []
        super().__init__(server_address, GitHttpProactiveAuthenticationProbeRequestHandler)


class GitHttpProactiveAuthenticationProbeRequestHandler(BaseHTTPRequestHandler):
    """Reject the probe request after recording whether authentication preceded it."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler owns this callback name.
        """Record one request and finish it without serving repository data."""

        server = self.server
        if not isinstance(server, GitHttpProactiveAuthenticationProbeServer):
            raise RuntimeError("Git HTTP authentication probe server has another shape")
        server.authorization_list.append(self.headers.get("Authorization"))
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Keep probe URLs and HTTP diagnostics out of task output.

        Args:
            format: Ignored server-owned logging format.
            args: Ignored values for the server-owned format.
        """


def _credential_helper_shell_get(principal: GitHubPrincipal, repository: RepositoryIdentity) -> str:
    """Build one helper that proves the token identity without exporting it."""

    login = shlex.quote(principal.login)
    user_id = shlex.quote(str(principal.user_id))
    node_id = shlex.quote(principal.node_id)
    repository_path = shlex.quote(f"{repository.value}.git")
    jq_filter = shlex.quote("[.login, (.id|tostring), .node_id] | @tsv")
    return (
        'f() { if [ "$1" = get ]; then '
        "protocol=; host=; path=; protocol_seen=; host_seen=; path_seen=; "
        "while IFS='=' read -r key value; do [ -n \"$key\" ] || break; "
        'case "$key" in '
        'protocol) [ -z "$protocol_seen" ] || exit 1; protocol_seen=1; protocol=$value ;; '
        'host) [ -z "$host_seen" ] || exit 1; host_seen=1; host=$value ;; '
        'path) [ -z "$path_seen" ] || exit 1; path_seen=1; path=$value ;; '
        "esac; done; "
        f'[ "$protocol" = https ] && [ "$host" = github.com ] && [ "$path" = {repository_path} ] || exit 1; '
        f'token="$(/usr/bin/gh auth token --hostname github.com --user {login} 2>/dev/null)" || exit 1; '
        '[ -n "$token" ] || exit 1; '
        'case "$token" in *[!A-Za-z0-9_]*) exit 1 ;; esac; '
        'response="$({ '
        "printf '%s\\n' 'url = \"https://api.github.com/user\"'; "
        "printf '%s\\n' 'header = \"Accept: application/vnd.github+json\"'; "
        "printf '%s\\n' 'header = \"X-GitHub-Api-Version: 2022-11-28\"'; "
        'printf \'header = "Authorization: Bearer %s"\\n\' "$token"; '
        "} | /usr/bin/curl -q --fail --silent --show-error --proto '=https' "
        '--netrc-file /dev/null --config - 2>/dev/null)" || exit 1; '
        f'actual="$(printf \'%s\' "$response" | /usr/bin/jq -er {jq_filter} 2>/dev/null)" || exit 1; '
        f"expected=\"$(printf '%s\\t%s\\t%s' {login} {user_id} {node_id})\"; "
        '[ "$actual" = "$expected" ] || exit 1; unset response actual; '
        "printf '%s\\n' 'username=x-access-token'; "
        "printf '%s\\n' \"password=$token\"; "
        "fi; }; f"
    )


def _credential_request_get(repository: RepositoryIdentity) -> str:
    """Return one exact Git credential protocol request for validation."""

    return f"protocol=https\nhost=github.com\npath={repository.value}.git\n\n"
