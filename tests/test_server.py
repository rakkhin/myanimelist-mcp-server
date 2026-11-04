import asyncio
import json
import os
import runpy
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP
from payloads import tool_payload

from main import _handle_shutdown_signal, create_server, main
from utils.access_log import LOG_CONFIG, RPCMethodMiddleware
from utils.bearer_auth import BearerTokenMiddleware
from utils.cloudflare_access import AccessJWTMiddleware
from utils.debug_log import RequestDebugMiddleware
from utils.mal_client import MALAPIError, MALClient


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
    return create_server()


def call(server, name, arguments):
    async def run():
        async with Client(server) as client:
            return await client.call_tool(
                name, arguments, raise_on_error=False
            )

    return asyncio.run(run())


def test_unexpected_error_details_are_not_sent_to_client(server, monkeypatch):
    async def broken_request(*args, **kwargs):
        raise KeyError("Bearer secret-token")

    monkeypatch.setattr(MALClient, "request", broken_request)

    result = call(server, "get_anime", {"q": "naruto"})

    assert result.is_error
    assert "secret-token" not in result.content[0].text


def test_expected_mal_error_is_returned_as_payload(server, monkeypatch):
    async def rejected_request(*args, **kwargs):
        raise MALAPIError("MAL API returned an error: invalid q")

    monkeypatch.setattr(MALClient, "request", rejected_request)

    result = call(server, "get_anime", {"q": "naruto"})

    assert not result.is_error
    assert tool_payload(result)["error"] == (
        "MAL API returned an error: invalid q"
    )


def test_running_main_starts_stdio_server(monkeypatch):
    monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
    run = Mock()
    monkeypatch.setattr(FastMCP, "run", run)
    # Keep the handlers out of the pytest process.
    install_handler = Mock()
    monkeypatch.setattr(signal, "signal", install_handler)

    runpy.run_path(
        str(Path(__file__).parents[1] / "main.py"), run_name="__main__"
    )

    run.assert_called_once_with(transport="stdio")
    installed = {c.args[0]: c.args[1] for c in install_handler.call_args_list}
    assert set(installed) == {signal.SIGINT, signal.SIGTERM}
    assert all(
        handler.__name__ == "_handle_shutdown_signal"
        for handler in installed.values()
    )


def test_signal_exits_and_keeps_stdout_for_the_protocol(capsys, monkeypatch):
    exit_process = Mock()
    monkeypatch.setattr(os, "_exit", exit_process)

    _handle_shutdown_signal(signal.SIGTERM, None)

    exit_process.assert_called_once_with(128 + signal.SIGTERM)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Received SIGTERM, shutting down." in captured.err


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_stops_server_while_stdin_is_open(signum, tmp_path):
    env = {
        **os.environ,
        # The child imports utils.auth, which would load the real .env.
        "PYTHON_DOTENV_DISABLED": "1",
        "MAL_CLIENT_ID": "test-id",
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        "FASTMCP_CHECK_FOR_UPDATES": "off",
    }
    main_py = Path(__file__).parents[1] / "main.py"
    process = subprocess.Popen(
        [sys.executable, str(main_py)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=env,
    )
    try:
        # Wait until the server is reading stdin, where it used to hang.
        for line in process.stderr:
            if b"Starting MCP server" in line:
                break
        process.send_signal(signum)
        returncode = process.wait(timeout=10)
    finally:
        process.kill()
        process.communicate()

    assert returncode == 128 + signum


ACCESS_TEAM = "https://team.cloudflareaccess.com"
ACCESS_AUD = "0123abcd"
BEARER_TOKEN = "0123456789abcdef0123456789abcdef"


def set_http_env(monkeypatch):
    monkeypatch.setenv("MAL_TRANSPORT", "http")
    monkeypatch.setenv("MAL_CF_ACCESS_TEAM_DOMAIN", ACCESS_TEAM)
    monkeypatch.setenv("MAL_CF_ACCESS_AUD", ACCESS_AUD)


def split_http_logging(kwargs):
    """Check the access log setup and return the other run() arguments."""
    kwargs = dict(kwargs)
    assert kwargs.pop("uvicorn_config") == {"log_config": LOG_CONFIG}
    rpc_method, *middleware = kwargs.pop("middleware")
    assert rpc_method.cls is RPCMethodMiddleware
    return kwargs, middleware


def split_access_check(run):
    """Return run()'s keyword arguments and check its middleware."""
    kwargs, (middleware,) = split_http_logging(run.call_args.kwargs)
    assert middleware.cls is AccessJWTMiddleware
    verifier = middleware.kwargs["verifier"]
    assert verifier.issuer == ACCESS_TEAM
    assert verifier.audience == ACCESS_AUD
    assert verifier.jwks_uri == f"{ACCESS_TEAM}/cdn-cgi/access/certs"
    return kwargs


def run_main(monkeypatch):
    """Run main() with FastMCP.run and signal handler installation mocked."""
    monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
    run = Mock()
    monkeypatch.setattr(FastMCP, "run", run)
    install_handler = Mock()
    monkeypatch.setattr(signal, "signal", install_handler)
    main()
    return run, install_handler


def test_http_transport_listens_on_loopback_by_default(monkeypatch):
    set_http_env(monkeypatch)

    run, install_handler = run_main(monkeypatch)

    run.assert_called_once()
    assert split_access_check(run) == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 8000,
        "path": "/mcp",
    }
    install_handler.assert_not_called()


def test_http_transport_uses_configured_settings(monkeypatch):
    set_http_env(monkeypatch)
    monkeypatch.setenv("MAL_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("MAL_HTTP_PORT", "9000")
    monkeypatch.setenv("MAL_HTTP_PATH", "/mal")
    # A trailing slash would not match the issuer in Access JWTs.
    monkeypatch.setenv("MAL_CF_ACCESS_TEAM_DOMAIN", f"{ACCESS_TEAM}/")

    run, _ = run_main(monkeypatch)

    run.assert_called_once()
    assert split_access_check(run) == {
        "transport": "http",
        "host": "0.0.0.0",
        "port": 9000,
        "path": "/mal",
    }


def test_http_transport_without_auth_settings_has_no_check(
    monkeypatch, capsys
):
    monkeypatch.setenv("MAL_TRANSPORT", "http")

    run, _ = run_main(monkeypatch)

    run.assert_called_once()
    kwargs, middleware = split_http_logging(run.call_args.kwargs)
    assert middleware == []
    assert kwargs == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 8000,
        "path": "/mcp",
    }
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "without authentication" in captured.err


@pytest.mark.parametrize("access", [False, True])
def test_http_transport_with_bearer_token_checks_it(
    monkeypatch, capsys, access
):
    if access:
        set_http_env(monkeypatch)
    else:
        monkeypatch.setenv("MAL_TRANSPORT", "http")
    monkeypatch.setenv("MAL_HTTP_BEARER_TOKEN", BEARER_TOKEN)

    run, _ = run_main(monkeypatch)

    run.assert_called_once()
    _, middleware = split_http_logging(run.call_args.kwargs)
    *access_check, bearer = middleware
    assert [m.cls for m in access_check] == (
        [AccessJWTMiddleware] if access else []
    )
    assert bearer.cls is BearerTokenMiddleware
    assert bearer.kwargs == {"token": BEARER_TOKEN}
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("value", ["1", "true", "Yes", " ON "])
def test_http_debug_logs_requests_before_any_check(monkeypatch, capsys, value):
    set_http_env(monkeypatch)
    monkeypatch.setenv("MAL_HTTP_DEBUG", value)

    run, _ = run_main(monkeypatch)

    debug, rpc_method, access = run.call_args.kwargs["middleware"]
    assert debug.cls is RequestDebugMiddleware
    assert rpc_method.cls is RPCMethodMiddleware
    assert access.cls is AccessJWTMiddleware
    assert "MAL_HTTP_DEBUG is on" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["", "0", "false", "No", "off"])
def test_http_debug_off(monkeypatch, capsys, value):
    set_http_env(monkeypatch)
    monkeypatch.setenv("MAL_HTTP_DEBUG", value)

    run, _ = run_main(monkeypatch)

    assert split_access_check(run)["transport"] == "http"
    assert "MAL_HTTP_DEBUG" not in capsys.readouterr().err


def test_bearer_token_is_read_from_file(monkeypatch, tmp_path):
    monkeypatch.setenv("MAL_TRANSPORT", "http")
    token_file = tmp_path / "token"
    token_file.write_text(f"{BEARER_TOKEN}\n")
    monkeypatch.setenv("MAL_HTTP_BEARER_TOKEN_FILE", str(token_file))

    run, _ = run_main(monkeypatch)

    _, (bearer,) = split_http_logging(run.call_args.kwargs)
    assert bearer.kwargs == {"token": BEARER_TOKEN}


@pytest.mark.parametrize(
    ("env", "match"),
    [
        pytest.param(
            {"MAL_HTTP_BEARER_TOKEN": BEARER_TOKEN[:-1]},
            "MAL_HTTP_BEARER_TOKEN must",
            id="too-short",
        ),
        pytest.param(
            {"MAL_HTTP_BEARER_TOKEN": f"{BEARER_TOKEN} x"},
            "MAL_HTTP_BEARER_TOKEN must",
            id="space",
        ),
        pytest.param(
            {"MAL_HTTP_BEARER_TOKEN": f"={BEARER_TOKEN}"},
            "MAL_HTTP_BEARER_TOKEN must",
            id="padding-first",
        ),
        pytest.param(
            {"MAL_HTTP_BEARER_TOKEN": f"{BEARER_TOKEN}é"},
            "MAL_HTTP_BEARER_TOKEN must",
            id="not-ascii",
        ),
        pytest.param(
            {
                "MAL_HTTP_BEARER_TOKEN": BEARER_TOKEN,
                "MAL_HTTP_BEARER_TOKEN_FILE": "token",
            },
            "not both",
            id="both",
        ),
        pytest.param(
            {"MAL_HTTP_BEARER_TOKEN_FILE": "missing"},
            "MAL_HTTP_BEARER_TOKEN_FILE: cannot read",
            id="missing-file",
        ),
        pytest.param(
            {"MAL_HTTP_BEARER_TOKEN_FILE": "empty"},
            "MAL_HTTP_BEARER_TOKEN_FILE must",
            id="empty-file",
        ),
        pytest.param(
            {"MAL_HTTP_BEARER_TOKEN_FILE": "latin1"},
            "MAL_HTTP_BEARER_TOKEN_FILE: cannot read",
            id="not-utf8-file",
        ),
    ],
)
def test_invalid_bearer_token_fails_before_server_starts(
    monkeypatch, tmp_path, env, match
):
    monkeypatch.setenv("MAL_TRANSPORT", "http")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty").write_text("\n")
    (tmp_path / "latin1").write_bytes(f"{BEARER_TOKEN}\xe9".encode("latin-1"))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    run = Mock()
    monkeypatch.setattr(FastMCP, "run", run)

    with pytest.raises(ValueError, match=match) as raised:
        main()

    assert BEARER_TOKEN not in str(raised.value)
    run.assert_not_called()


def test_empty_transport_setting_means_stdio(monkeypatch):
    monkeypatch.setenv("MAL_TRANSPORT", "")

    run, _ = run_main(monkeypatch)

    run.assert_called_once_with(transport="stdio")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAL_TRANSPORT", "sse"),
        ("MAL_HTTP_PORT", "http"),
        ("MAL_HTTP_PORT", "0"),
        ("MAL_HTTP_PORT", "65536"),
        ("MAL_HTTP_PORT", "-1"),
        ("MAL_HTTP_PATH", "mal"),
        ("MAL_CF_ACCESS_TEAM_DOMAIN", ""),
        ("MAL_CF_ACCESS_TEAM_DOMAIN", "http://team.cloudflareaccess.com"),
        ("MAL_CF_ACCESS_TEAM_DOMAIN", "team.cloudflareaccess.com"),
        ("MAL_CF_ACCESS_AUD", ""),
        ("MAL_HTTP_DEBUG", "maybe"),
    ],
)
def test_invalid_setting_fails_before_server_starts(monkeypatch, name, value):
    set_http_env(monkeypatch)
    monkeypatch.setenv(name, value)
    run = Mock()
    monkeypatch.setattr(FastMCP, "run", run)

    with pytest.raises(ValueError, match=name):
        main()

    run.assert_not_called()


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _post_initialize(url):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, ""


# Run main.py on HTTP for real. MCP behind the Access check is tested with a
# signed JWT in test_cloudflare_access.py.
@pytest.mark.parametrize(
    ("auth_env", "expected"),
    [
        pytest.param(
            # Never contacted: requests without a JWT are refused before
            # any key is fetched.
            {
                "MAL_CF_ACCESS_TEAM_DOMAIN": ACCESS_TEAM,
                "MAL_CF_ACCESS_AUD": ACCESS_AUD,
            },
            {"/mal": 403, "/mcp": 403},
            id="access-check",
        ),
        pytest.param(
            {"MAL_HTTP_BEARER_TOKEN": BEARER_TOKEN, "MAL_HTTP_DEBUG": "1"},
            {"/mal": 401, "/mcp": 401},
            id="bearer-check-debug",
        ),
        pytest.param({}, {"/mal": 200, "/mcp": 404}, id="no-auth-check"),
    ],
)
def test_http_server_runs_main(tmp_path, auth_env, expected):
    port = _free_port()
    env = {
        **os.environ,
        # The child imports utils.auth, which would load the real .env.
        "PYTHON_DOTENV_DISABLED": "1",
        "MAL_CLIENT_ID": "test-id",
        "MAL_TRANSPORT": "http",
        "MAL_HTTP_PORT": str(port),
        "MAL_HTTP_PATH": "/mal",
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        "FASTMCP_CHECK_FOR_UPDATES": "off",
        **auth_env,
    }
    main_py = Path(__file__).parents[1] / "main.py"
    process = subprocess.Popen(
        [sys.executable, str(main_py)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=env,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                assert process.poll() is None, "server exited early"
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.1)

        responses = {
            path: _post_initialize(f"http://127.0.0.1:{port}{path}")
            for path in expected
        }
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)

    assert {path: status for path, (status, _) in responses.items()} == (
        expected
    )
    access_log = stdout.decode()
    if expected["/mal"] == 200:
        _, text = responses["/mal"]
        assert '"name":"myanimelist"' in text.replace(" ", "")
        assert '"POST /mal HTTP/1.1" 200 OK initialize\n' in access_log
        assert '"POST /mcp HTTP/1.1" 404 Not Found -\n' in access_log
    else:
        # Refused requests are logged without reading their bodies.
        status = HTTPStatus(expected["/mal"])
        line = f'"POST /mal HTTP/1.1" {status.value} {status.phrase} -\n'
        assert line in access_log
    # uvicorn shuts down, then re-raises the signal it caught.
    assert b"Finished server process" in stderr
    # The debug log has refused requests too, with their bodies.
    debug_lines = [
        line
        for line in stderr.decode().splitlines()
        if line.startswith("DEBUG:") and " Request {" in line
    ]
    if "MAL_HTTP_DEBUG" in auth_env:
        assert len(debug_lines) == len(expected)
        record = json.loads(debug_lines[0].partition(" Request ")[2])
        assert record["path"] == "/mal"
        assert json.loads(record["body"])["method"] == "initialize"
    else:
        assert debug_lines == []
    assert process.returncode == -signal.SIGTERM
