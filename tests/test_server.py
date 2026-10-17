import asyncio
import importlib
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP

from utils.mal_client import MALAPIError, MALClient


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
    return importlib.import_module("main").mcp


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
    assert result.structured_content["error"] == (
        "MAL API returned an error: invalid q"
    )


def test_running_main_starts_stdio_server(monkeypatch):
    monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
    run = Mock()
    monkeypatch.setattr(FastMCP, "run", run)

    runpy.run_path(
        str(Path(__file__).parents[1] / "main.py"), run_name="__main__"
    )

    run.assert_called_once_with(transport="stdio")
