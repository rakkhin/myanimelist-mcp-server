import asyncio
import os
import runpy
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP
from payloads import tool_payload

from main import _handle_shutdown_signal, create_server
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
