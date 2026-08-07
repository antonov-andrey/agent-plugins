"""Real Git HTTP authentication tests for the principal-bound merge transport."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools"
LIBRARY_ROOT = PLUGIN_ROOT / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_host.authentication import GitHubAuthenticationBoundary
from git_host.command import command_run
from git_host.model import GitHubContractError

APPROVED_USERNAME = "approved-principal"
APPROVED_PASSWORD = "approved-invocation-helper-password"
FOREIGN_USERNAME = "foreign-standard-home-principal"
FOREIGN_PASSWORD = "foreign-standard-home-password"


class AuthenticatedGitHttpBackendServer(ThreadingHTTPServer):
    """Serve one real Git smart-HTTP repository behind exact Basic authentication."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        project_root: Path,
        git_executable: Path,
    ) -> None:
        """Bind the test backend and its exact accepted principal.

        Args:
            server_address: Exact loopback host and ephemeral port.
            project_root: Parent containing the served bare repository.
            git_executable: Real behaviorally supported Git executable.
        """

        self.project_root = project_root
        self.git_executable = git_executable
        credential = b64encode(f"{APPROVED_USERNAME}:{APPROVED_PASSWORD}".encode("utf-8")).decode("ascii")
        self.expected_authorization = f"Basic {credential}"
        self.request_list_by_service: dict[str, list[tuple[str, str | None]]] = {
            "git-upload-pack": [],
            "git-receive-pack": [],
        }
        self.backend_request_count_by_service = {
            "git-upload-pack": 0,
            "git-receive-pack": 0,
        }
        super().__init__(server_address, AuthenticatedGitHttpBackendRequestHandler)


class AuthenticatedGitHttpBackendRequestHandler(BaseHTTPRequestHandler):
    """Authenticate one request and delegate accepted traffic to ``git http-backend``."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler owns this callback name.
        """Serve one authenticated smart-HTTP advertisement request."""

        self._request_serve()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler owns this callback name.
        """Serve one authenticated smart-HTTP RPC request."""

        self._request_serve()

    def log_message(self, format: str, *args: object) -> None:
        """Keep test HTTP traffic out of pytest output.

        Args:
            format: Ignored server-owned logging format.
            args: Ignored values for the server-owned format.
        """

    def _request_serve(self) -> None:
        """Require the approved credential before invoking the real Git backend."""

        server = self.server
        if not isinstance(server, AuthenticatedGitHttpBackendServer):
            raise RuntimeError("Authenticated Git HTTP backend has another shape")
        parsed = urlsplit(self.path)
        service = _git_http_service_get(parsed.path, parsed.query)
        authorization = self.headers.get("Authorization")
        server.request_list_by_service[service].append((self.command, authorization))
        if authorization != server.expected_authorization:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="linear-agent-test"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        request_body = self.rfile.read(content_length)
        environment_by_name_map = {
            "CONTENT_LENGTH": str(content_length),
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_HTTP_EXPORT_ALL": "1",
            "GIT_PROJECT_ROOT": str(server.project_root),
            "HOME": "/home/andrey",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PATH_INFO": parsed.path,
            "QUERY_STRING": parsed.query,
            "REMOTE_USER": APPROVED_USERNAME,
            "REQUEST_METHOD": self.command,
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": str(server.server_address[1]),
            "SERVER_PROTOCOL": self.request_version,
        }
        completed_process = subprocess.run(
            [str(server.git_executable), "http-backend"],
            check=False,
            capture_output=True,
            env=environment_by_name_map,
            input=request_body,
        )
        if completed_process.returncode != 0:
            raise RuntimeError("Real Git HTTP backend failed")
        server.backend_request_count_by_service[service] += 1
        header_payload, response_body = _cgi_response_split(completed_process.stdout)
        status, response_header_list = _cgi_header_parse(header_payload)
        self.send_response(status)
        for name, value in response_header_list:
            self.send_header(name, value)
        if not any(name.casefold() == "content-length" for name, _value in response_header_list):
            self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


def _git_http_service_get(path: str, query: str) -> str:
    """Return the exact smart-HTTP service named by one request.

    Args:
        path: Request path without query.
        query: Raw query string.

    Returns:
        Git smart-HTTP service name.
    """

    query_service_list = parse_qs(query).get("service", [])
    if len(query_service_list) == 1 and query_service_list[0] in {"git-upload-pack", "git-receive-pack"}:
        return query_service_list[0]
    for service in ("git-upload-pack", "git-receive-pack"):
        if path.endswith(f"/{service}"):
            return service
    raise RuntimeError("Git smart-HTTP request names another service")


def _cgi_response_split(payload: bytes) -> tuple[bytes, bytes]:
    """Split one CGI header block from its response body.

    Args:
        payload: Complete successful ``git http-backend`` output.

    Returns:
        Header bytes and response body bytes.
    """

    for separator in (b"\r\n\r\n", b"\n\n"):
        if separator in payload:
            header_payload, response_body = payload.split(separator, 1)
            return header_payload, response_body
    raise RuntimeError("Git HTTP backend returned no CGI header boundary")


def _cgi_header_parse(payload: bytes) -> tuple[int, list[tuple[str, str]]]:
    """Parse the minimal CGI headers emitted by ``git http-backend``.

    Args:
        payload: CGI header bytes without the terminating empty line.

    Returns:
        HTTP status and response headers.
    """

    status = 200
    response_header_list: list[tuple[str, str]] = []
    for line in payload.decode("latin-1").replace("\r\n", "\n").splitlines():
        name, separator, value = line.partition(":")
        if not separator:
            raise RuntimeError("Git HTTP backend returned a malformed CGI header")
        if name.casefold() == "status":
            status = int(value.strip().split(" ", 1)[0])
        else:
            response_header_list.append((name.strip(), value.strip()))
    return status, response_header_list


def _closed_test_environment_get() -> dict[str, str]:
    """Return the exact standard-home environment for namespaced Git clients."""

    return {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/home/andrey",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": "andrey",
        "PATH": "/usr/bin:/bin",
        "USER": "andrey",
    }


def _standard_home_overlay_git_run(
    git_executable: Path,
    argument_list: Sequence[str],
    *,
    standard_home_netrc_path: Path,
    environment_by_name_map: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git with only hostile netrc bytes overlaid on the actual standard home.

    Args:
        git_executable: Exact Git executable under test.
        argument_list: Git arguments after the executable.
        standard_home_netrc_path: Fixture mounted at the standard ``.netrc`` path.
        environment_by_name_map: Optional exact environment from the production boundary.

    Returns:
        Completed real Git process.
    """

    environment = dict(environment_by_name_map or _closed_test_environment_get())
    assert environment["HOME"] == "/home/andrey"
    assert "CODEX_HOME" not in environment
    with TemporaryDirectory(prefix="linear-agent-netrc-overlay-", dir="/tmp") as overlay_root_name:
        overlay_root = Path(overlay_root_name)
        upper = overlay_root / "upper"
        work = overlay_root / "work"
        merged = overlay_root / "merged"
        upper.mkdir()
        work.mkdir()
        merged.mkdir()
        shutil.copyfile(standard_home_netrc_path, upper / ".netrc")
        (upper / ".netrc").chmod(0o600)
        assert list(upper.iterdir()) == [upper / ".netrc"]
        namespace_script = (
            "upper=$1; work=$2; merged=$3; git_executable=$4; shift 4; "
            'mount -t overlay overlay -o "lowerdir=/home/andrey,upperdir=$upper,workdir=$work" "$merged" '
            "|| exit 125; "
            'mount --bind "$merged" /home/andrey || exit 125; '
            "test -e /home/andrey/.codex || exit 125; "
            'exec "$git_executable" "$@"'
        )
        return subprocess.run(
            [
                "/usr/bin/unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--",
                "/bin/sh",
                "-c",
                namespace_script,
                "linear-agent-standard-home-overlay",
                str(upper),
                str(work),
                str(merged),
                str(git_executable),
                *argument_list,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )


def _behaviorally_supported_git_get(standard_home_netrc_path: Path) -> Path:
    """Return a real Git executable only after the production behavior probe passes.

    Args:
        standard_home_netrc_path: Hostile fixture mounted at the standard ``.netrc`` path.

    Returns:
        Behaviorally supported Git executable.
    """

    configured_git_executable = os.environ.get("LINEAR_AGENT_TEST_PROACTIVE_AUTH_GIT")
    git_executable = Path(configured_git_executable or "/usr/bin/git")
    if not git_executable.is_absolute() or not git_executable.is_file():
        pytest.fail("LINEAR_AGENT_TEST_PROACTIVE_AUTH_GIT must name one absolute executable file")
    if not Path("/usr/bin/unshare").is_file():
        pytest.skip("real transport test requires the unshare isolation primitive")
    version_process = _standard_home_overlay_git_run(
        git_executable,
        ["--version"],
        standard_home_netrc_path=standard_home_netrc_path,
    )
    if version_process.returncode != 0 and (
        version_process.returncode == 125 or "Operation not permitted" in version_process.stderr
    ):
        pytest.skip("real transport test requires an unprivileged user and mount namespace with overlayfs")
    assert version_process.returncode == 0, version_process.stderr

    def runner(
        argument_list: Sequence[str],
        *,
        environment_by_name_map: Mapping[str, str],
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the production probe through the literal standard-home mount.

        Args:
            argument_list: Production direct Git argument vector.
            environment_by_name_map: Production closed environment.
            input_text: Optional standard input.

        Returns:
            Completed real Git process.
        """

        assert argument_list[0] == "git"
        assert input_text is None
        return _standard_home_overlay_git_run(
            git_executable,
            argument_list[1:],
            standard_home_netrc_path=standard_home_netrc_path,
            environment_by_name_map=environment_by_name_map,
        )

    try:
        GitHubAuthenticationBoundary(runner).git_http_proactive_authentication_require()
    except GitHubContractError as error:
        if configured_git_executable is not None:
            pytest.fail(f"configured real transport Git failed the production behavior probe: {error}")
        pytest.skip("real transport test requires Git with behaviorally supported http.proactiveAuth=basic")
    return git_executable


def _git_checked(
    git_executable: Path,
    argument_list: Sequence[str],
    *,
    home: Path,
) -> str:
    """Run one successful fixture Git command outside the authentication boundary.

    Args:
        git_executable: Exact Git executable used by the HTTP client and backend.
        argument_list: Arguments after the executable.
        home: Isolated fixture-only home.

    Returns:
        Standard output without trailing whitespace.
    """

    environment = _closed_test_environment_get()
    environment["HOME"] = str(home)
    completed_process = command_run(
        [str(git_executable), *argument_list],
        environment_by_name_map=environment,
    )
    assert completed_process.returncode == 0, completed_process.stderr
    return completed_process.stdout.strip()


def _repository_create(root: Path, git_executable: Path) -> tuple[Path, str, str]:
    """Create one bare repository with exact base and reviewed-head refs.

    Args:
        root: Empty test root.
        git_executable: Exact real Git executable.

    Returns:
        Bare repository path, base commit and reviewed-head commit.
    """

    home = root / "fixture-home"
    source = root / "source"
    project_root = root / "http-root"
    remote = project_root / "example.git"
    root.mkdir()
    home.mkdir()
    source.mkdir()
    project_root.mkdir()
    _git_checked(git_executable, ["init", "--initial-branch=main", str(source)], home=home)
    (source / "base.txt").write_text("base\n", encoding="utf-8")
    _git_checked(git_executable, ["-C", str(source), "add", "base.txt"], home=home)
    _git_checked(
        git_executable,
        [
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-m",
            "base",
        ],
        home=home,
    )
    base_commit = _git_checked(git_executable, ["-C", str(source), "rev-parse", "HEAD"], home=home)
    _git_checked(git_executable, ["-C", str(source), "checkout", "-b", "linear/and-17"], home=home)
    (source / "head.txt").write_text("head\n", encoding="utf-8")
    _git_checked(git_executable, ["-C", str(source), "add", "head.txt"], home=home)
    _git_checked(
        git_executable,
        [
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-m",
            "head",
        ],
        home=home,
    )
    head_commit = _git_checked(git_executable, ["-C", str(source), "rev-parse", "HEAD"], home=home)
    _git_checked(git_executable, ["clone", "--bare", str(source), str(remote)], home=home)
    _git_checked(git_executable, [f"--git-dir={remote}", "config", "http.receivepack", "true"], home=home)
    return remote, base_commit, head_commit


def _network_config_argument_list(username: str, password: str, *, proactive_authentication: bool = True) -> list[str]:
    """Return test credentials through the same proactive helper shape.

    Args:
        username: Principal login emitted by the invocation helper.
        password: Principal password emitted by the invocation helper.
        proactive_authentication: Whether to apply the remediating transport setting.

    Returns:
        Direct Git configuration arguments.
    """

    helper = (
        '!f() { if [ "$1" = get ]; then ' f"printf '%s\\n' 'username={username}' 'password={password}'; " "fi; }; f"
    )
    argument_list = [
        "-c",
        "credential.helper=",
        "-c",
        f"credential.helper={helper}",
        "-c",
        "credential.interactive=never",
        "-c",
        "credential.useHttpPath=true",
        "-c",
        f"credential.username={username}",
        "-c",
        "http.followRedirects=false",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.http.allow=always",
    ]
    if proactive_authentication:
        argument_list[10:10] = ["-c", "http.proactiveAuth=basic"]
    return argument_list


def test_real_http_git_helper_precedes_foreign_standard_home_netrc_and_wrong_principal_mutates_no_ref(
    tmp_path: Path,
) -> None:
    """Real upload/receive-pack use the helper first and reject a foreign atomic push."""

    netrc_path = tmp_path / "foreign-standard-home.netrc"
    netrc_path.write_text(
        f"machine 127.0.0.1\nlogin {FOREIGN_USERNAME}\npassword {FOREIGN_PASSWORD}\n",
        encoding="utf-8",
    )
    netrc_path.chmod(0o600)
    git_executable = _behaviorally_supported_git_get(netrc_path)
    remote, base_commit, head_commit = _repository_create(tmp_path / "repository", git_executable)
    client = tmp_path / "client.git"
    fixture_home = tmp_path / "client-fixture-home"
    fixture_home.mkdir()
    _git_checked(git_executable, ["init", "--bare", str(client)], home=fixture_home)
    server = AuthenticatedGitHttpBackendServer(
        ("127.0.0.1", 0),
        project_root=remote.parent,
        git_executable=git_executable,
    )
    server_thread = Thread(target=server.serve_forever, name="authenticated-git-http-backend", daemon=True)
    server_thread.start()
    remote_url = f"http://127.0.0.1:{server.server_address[1]}/example.git"
    approved_config = _network_config_argument_list(APPROVED_USERNAME, APPROVED_PASSWORD)
    try:
        control_process = _standard_home_overlay_git_run(
            git_executable,
            [
                f"--git-dir={client}",
                *_network_config_argument_list(
                    APPROVED_USERNAME,
                    APPROVED_PASSWORD,
                    proactive_authentication=False,
                ),
                "ls-remote",
                "--refs",
                remote_url,
                "refs/heads/main",
            ],
            standard_home_netrc_path=netrc_path,
        )
        foreign_credential = b64encode(f"{FOREIGN_USERNAME}:{FOREIGN_PASSWORD}".encode("utf-8")).decode("ascii")
        assert server.request_list_by_service["git-upload-pack"][:3] == [
            ("GET", None),
            ("GET", f"Basic {foreign_credential}"),
            ("GET", server.expected_authorization),
        ]
        assert control_process.returncode == 0, control_process.stderr
        server.request_list_by_service["git-upload-pack"].clear()

        fetch_process = _standard_home_overlay_git_run(
            git_executable,
            [
                f"--git-dir={client}",
                *approved_config,
                "fetch",
                "--no-tags",
                remote_url,
                "+refs/heads/main:refs/client/base",
                "+refs/heads/linear/and-17:refs/client/head",
            ],
            standard_home_netrc_path=netrc_path,
        )
        assert fetch_process.returncode == 0, fetch_process.stderr
        assert server.request_list_by_service["git-upload-pack"][0] == (
            "GET",
            server.expected_authorization,
        )
        assert all(
            authorization != f"Basic {foreign_credential}"
            for _method, authorization in server.request_list_by_service["git-upload-pack"]
        )

        approved_push_process = _standard_home_overlay_git_run(
            git_executable,
            [
                f"--git-dir={client}",
                *approved_config,
                "push",
                "--porcelain",
                remote_url,
                "refs/client/head:refs/heads/authentication-probe",
            ],
            standard_home_netrc_path=netrc_path,
        )
        assert approved_push_process.returncode == 0, approved_push_process.stderr
        assert server.request_list_by_service["git-receive-pack"][0] == (
            "GET",
            server.expected_authorization,
        )
        assert all(
            authorization == server.expected_authorization
            for _method, authorization in server.request_list_by_service["git-receive-pack"]
        )
        assert (
            _git_checked(
                git_executable,
                [f"--git-dir={remote}", "rev-parse", "refs/heads/main"],
                home=fixture_home,
            )
            == base_commit
        )
        assert (
            _git_checked(
                git_executable,
                [f"--git-dir={remote}", "rev-parse", "refs/heads/linear/and-17"],
                home=fixture_home,
            )
            == head_commit
        )
        assert (
            _git_checked(
                git_executable,
                [
                    f"--git-dir={remote}",
                    "for-each-ref",
                    "--format=%(objectname)",
                    "refs/heads/authentication-probe",
                ],
                home=fixture_home,
            )
            == head_commit
        )

        prior_backend_receive_count = server.backend_request_count_by_service["git-receive-pack"]
        server.request_list_by_service["git-receive-pack"].clear()
        wrong_config = _network_config_argument_list("wrong-principal", "wrong-principal-password")
        rejected_push_process = _standard_home_overlay_git_run(
            git_executable,
            [
                f"--git-dir={client}",
                *wrong_config,
                "push",
                "--atomic",
                "--porcelain",
                f"--force-with-lease=refs/heads/main:{base_commit}",
                f"--force-with-lease=refs/heads/linear/and-17:{head_commit}",
                remote_url,
                "refs/client/head:refs/heads/main",
                ":refs/heads/linear/and-17",
            ],
            standard_home_netrc_path=netrc_path,
        )
        assert rejected_push_process.returncode != 0
        wrong_credential = b64encode(b"wrong-principal:wrong-principal-password").decode("ascii")
        rejected_receive_request_list = server.request_list_by_service["git-receive-pack"]
        assert rejected_receive_request_list
        assert all(
            authorization == f"Basic {wrong_credential}" for _method, authorization in rejected_receive_request_list
        )
        assert all(
            authorization != f"Basic {foreign_credential}" for _method, authorization in rejected_receive_request_list
        )
        assert server.backend_request_count_by_service["git-receive-pack"] == prior_backend_receive_count
        assert (
            _git_checked(
                git_executable,
                [f"--git-dir={remote}", "rev-parse", "refs/heads/main"],
                home=fixture_home,
            )
            == base_commit
        )
        assert (
            _git_checked(
                git_executable,
                [f"--git-dir={remote}", "rev-parse", "refs/heads/linear/and-17"],
                home=fixture_home,
            )
            == head_commit
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
