import asyncio
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from tools import tools
from utils.mal_client import MALAPIError


@pytest.fixture
def mal(monkeypatch):
    """Register tools against a mock MAL client with a valid access token."""
    mock_client = AsyncMock()
    mock_client.put_authed = AsyncMock(return_value={"status": "watching"})
    mock_client.delete_authed = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(tools, "MALClient", lambda: mock_client)
    monkeypatch.setattr(
        tools, "get_mal_access_token", AsyncMock(return_value="test-token")
    )

    mcp = FastMCP("test")
    tools.register_tools(mcp)
    return mcp, mock_client


def call_fn(mcp, name, **kwargs):
    tool = asyncio.run(mcp.get_tool(name))
    return asyncio.run(tool.fn(**kwargs))


def call_over_mcp(mcp, name, arguments):
    async def run():
        async with Client(mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


class TestUpdateTools:
    def test_update_myanimelist_sends_only_provided_fields(self, mal):
        mcp, client = mal

        result = call_fn(
            mcp,
            "update_myanimelist",
            anime_id=21,
            status="watching",
            score=0,
            is_rewatching=False,
        )

        client.put_authed.assert_awaited_once_with(
            "/anime/21/my_list_status",
            "test-token",
            data={"status": "watching", "score": 0, "is_rewatching": False},
        )
        assert result == {"status": "watching"}

    def test_update_mymangalist_sends_only_provided_fields(self, mal):
        mcp, client = mal

        call_fn(
            mcp,
            "update_mymangalist",
            manga_id=2,
            num_chapters_read=150,
            tags="favorite",
        )

        client.put_authed.assert_awaited_once_with(
            "/manga/2/my_list_status",
            "test-token",
            data={"num_chapters_read": 150, "tags": "favorite"},
        )

    @pytest.mark.parametrize(
        ("name", "id_arg", "kind"),
        [
            ("update_myanimelist", "anime_id", "anime"),
            ("update_mymangalist", "manga_id", "manga"),
        ],
    )
    def test_update_without_fields_returns_error_and_sends_nothing(
        self, mal, name, id_arg, kind
    ):
        mcp, client = mal

        result = call_fn(mcp, name, **{id_arg: 1})

        assert result == {
            "error": f"At least one field must be provided to update the {kind}"
        }
        client.put_authed.assert_not_awaited()

    def test_update_returns_error_payload_when_mal_rejects(self, mal):
        mcp, client = mal
        client.put_authed.side_effect = MALAPIError(
            "MAL API returned an error"
        )

        result = call_fn(mcp, "update_myanimelist", anime_id=1, score=5)

        assert "error" in result

    def test_update_returns_error_payload_without_token(
        self, mal, monkeypatch
    ):
        mcp, client = mal
        monkeypatch.setattr(
            tools,
            "get_mal_access_token",
            AsyncMock(side_effect=RuntimeError("Call 'mal_auth_login' first")),
        )

        result = call_fn(mcp, "update_myanimelist", anime_id=1, score=5)

        assert "mal_auth_login" in result["error"]
        client.put_authed.assert_not_awaited()


class TestDeleteTools:
    @pytest.mark.parametrize(
        ("name", "id_arg", "kind", "label"),
        [
            ("delete_myanimelist_item", "anime_id", "anime", "Anime"),
            ("delete_mymangalist_item", "manga_id", "manga", "Manga"),
        ],
    )
    def test_delete_targets_list_entry_of_given_id(
        self, mal, name, id_arg, kind, label
    ):
        mcp, client = mal

        result = call_fn(mcp, name, **{id_arg: 42})

        client.delete_authed.assert_awaited_once_with(
            f"/{kind}/42/my_list_status", "test-token"
        )
        assert result == {"message": f"{label} ID 42 deleted successfully"}

    def test_delete_returns_error_payload_when_mal_rejects(self, mal):
        mcp, client = mal
        client.delete_authed.side_effect = MALAPIError("not found")

        result = call_fn(mcp, "delete_myanimelist_item", anime_id=42)

        assert "error" in result
        assert "deleted successfully" not in str(result)


class TestArgumentValidationOverMcp:
    """Field constraints are enforced by FastMCP, not by the tool body.

    Calling ``tool.fn`` directly bypasses them, so these go through a client.
    """

    @pytest.mark.parametrize(
        ("name", "arguments"),
        [
            ("update_myanimelist", {"anime_id": 1, "score": 11}),
            ("update_myanimelist", {"anime_id": 1, "score": -1}),
            ("update_myanimelist", {"anime_id": 0, "score": 5}),
            ("update_myanimelist", {"anime_id": 1, "status": "reading"}),
            ("update_myanimelist", {"anime_id": 1, "priority": 3}),
            ("update_myanimelist", {"anime_id": 1, "rewatch_value": 6}),
            ("update_myanimelist", {"anime_id": 1, "comments": "x" * 1001}),
            ("update_mymangalist", {"manga_id": 1, "score": 11}),
            ("update_mymangalist", {"manga_id": 1, "status": "watching"}),
            ("update_mymangalist", {"manga_id": 1, "num_chapters_read": -1}),
            ("update_mymangalist", {"manga_id": 1, "reread_value": 6}),
        ],
    )
    def test_invalid_arguments_are_rejected_before_calling_mal(
        self, mal, name, arguments
    ):
        mcp, client = mal

        with pytest.raises(ToolError):
            call_over_mcp(mcp, name, arguments)

        client.put_authed.assert_not_awaited()

    def test_valid_arguments_reach_mal(self, mal):
        mcp, client = mal

        call_over_mcp(
            mcp,
            "update_myanimelist",
            {"anime_id": 1, "status": "completed", "score": 10},
        )

        client.put_authed.assert_awaited_once_with(
            "/anime/1/my_list_status",
            "test-token",
            data={"status": "completed", "score": 10},
        )

    def test_delete_rejects_non_integer_id(self, mal):
        mcp, client = mal

        with pytest.raises(ToolError):
            call_over_mcp(mcp, "delete_myanimelist_item", {"anime_id": "abc"})

        client.delete_authed.assert_not_awaited()
