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
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP
from payloads import tool_payload

from main import _handle_shutdown_signal, create_server, main
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
    monkeypatch.setenv("MAL_TRANSPORT", "http")

    run, install_handler = run_main(monkeypatch)

    run.assert_called_once_with(
        transport="http", host="127.0.0.1", port=8000, path="/mcp"
    )
    install_handler.assert_not_called()


def test_http_transport_uses_configured_host_port_and_path(monkeypatch):
    monkeypatch.setenv("MAL_TRANSPORT", "http")
    monkeypatch.setenv("MAL_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("MAL_HTTP_PORT", "9000")
    monkeypatch.setenv("MAL_HTTP_PATH", "/mal")

    run, _ = run_main(monkeypatch)

    run.assert_called_once_with(
        transport="http", host="0.0.0.0", port=9000, path="/mal"
    )


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
    ],
)
def test_invalid_setting_fails_before_server_starts(monkeypatch, name, value):
    monkeypatch.setenv("MAL_TRANSPORT", "http")
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


def test_http_server_answers_mcp_on_configured_path(tmp_path):
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
    }
    main_py = Path(__file__).parents[1] / "main.py"
    process = subprocess.Popen(
        [sys.executable, str(main_py)],
        stdout=subprocess.DEVNULL,
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

        status, text = _post_initialize(f"http://127.0.0.1:{port}/mal")
        other_status, _ = _post_initialize(f"http://127.0.0.1:{port}/mcp")
    finally:
        process.terminate()
        _, stderr = process.communicate(timeout=10)

    assert status == 200
    assert '"name":"myanimelist"' in text.replace(" ", "")
    assert other_status == 404
    # uvicorn shuts down, then re-raises the signal it caught.
    assert b"Finished server process" in stderr
    assert process.returncode == -signal.SIGTERM
