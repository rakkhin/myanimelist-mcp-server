import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payloads import tool_payload

from tools import tools
from utils import auth


@pytest.fixture(autouse=True)
def isolate_from_local_environment(monkeypatch, tmp_path):
    """Keep values from .env, the real token store and the network out of tests.

    load_dotenv() runs when tools and auth are imported, so .env values are in
    os.environ and in auth's module-level settings for the whole session.
    """
    for name in list(os.environ):
        if name.startswith("MAL_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))

    # Module-level copies taken at import; values match utils/auth.py defaults.
    monkeypatch.setattr(auth, "CLIENT_ID", None)
    monkeypatch.setattr(auth, "CLIENT_SECRET", None)
    monkeypatch.setattr(auth, "REDIRECT_URI", "http://localhost:8080/callback")
    monkeypatch.setattr(auth, "CALLBACK_HOST", "127.0.0.1")
    monkeypatch.setattr(auth, "CALLBACK_PORT", 8080)
    monkeypatch.setattr(auth, "CALLBACK_TIMEOUT_SECONDS", 300.0)
    monkeypatch.setattr(auth, "PKCE_METHOD", "plain")

    async def no_network(self, request, **kwargs):
        raise AssertionError(
            f"Test attempted a real HTTP request to {request.url}"
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)


@dataclass
class ToolHarness:
    mcp: FastMCP
    client: AsyncMock

    def call(self, name, **kwargs):
        """Call the tool function directly and return its decoded payload.

        FastMCP argument validation is skipped. See tool_payload for what
        the result must look like.
        """
        tool = asyncio.run(self.mcp.get_tool(name))
        result = asyncio.run(tool.fn(**kwargs))
        assert isinstance(result, ToolResult)
        return tool_payload(result)

    def call_over_mcp(self, name, arguments):
        async def run():
            async with Client(self.mcp) as client:
                return await client.call_tool(name, arguments)

        return asyncio.run(run())


@pytest.fixture
def mal(monkeypatch):
    """Register tools against a mock MAL client with a valid access token."""
    mock_client = AsyncMock()
    mock_client.get_public = AsyncMock(return_value={"data": []})
    mock_client.get_authed = AsyncMock(return_value={"data": []})
    mock_client.patch_authed = AsyncMock(return_value={"status": "watching"})
    mock_client.delete_authed = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(tools, "MALClient", lambda: mock_client)
    monkeypatch.setattr(
        tools, "get_mal_access_token", AsyncMock(return_value="test-token")
    )

    mcp = FastMCP("test")
    tools.register_tools(mcp)
    return ToolHarness(mcp, mock_client)
