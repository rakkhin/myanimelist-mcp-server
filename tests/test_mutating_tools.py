from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from tools import tools
from utils.auth import MALAuthError
from utils.mal_client import MALAPIError

UPDATE_TOOLS = [
    ("update_myanimelist", "anime_id", "anime"),
    ("update_mymangalist", "manga_id", "manga"),
]
DELETE_TOOLS = [
    ("delete_myanimelist_item", "anime_id", "anime", "Anime"),
    ("delete_mymangalist_item", "manga_id", "manga", "Manga"),
]


class TestUpdateTools:
    def test_update_myanimelist_sends_only_provided_fields(self, mal):
        result = mal.call(
            "update_myanimelist",
            anime_id=21,
            status="watching",
            score=0,
            is_rewatching=False,
        )

        mal.client.patch_authed.assert_awaited_once_with(
            "/anime/21/my_list_status",
            "test-token",
            data={"status": "watching", "score": 0, "is_rewatching": False},
        )
        assert result == {"status": "watching"}

    def test_update_mymangalist_sends_only_provided_fields(self, mal):
        mal.call(
            "update_mymangalist",
            manga_id=2,
            num_chapters_read=150,
            tags="favorite",
        )

        mal.client.patch_authed.assert_awaited_once_with(
            "/manga/2/my_list_status",
            "test-token",
            data={"num_chapters_read": 150, "tags": "favorite"},
        )

    @pytest.mark.parametrize(("name", "id_arg", "kind"), UPDATE_TOOLS)
    def test_update_without_fields_returns_error_and_sends_nothing(
        self, mal, name, id_arg, kind
    ):
        result = mal.call(name, **{id_arg: 1})

        assert result == {
            "error": f"At least one field must be provided to update the {kind}"
        }
        mal.client.patch_authed.assert_not_awaited()

    @pytest.mark.parametrize(("name", "id_arg", "kind"), UPDATE_TOOLS)
    def test_update_returns_error_payload_when_mal_rejects(
        self, mal, name, id_arg, kind
    ):
        mal.client.patch_authed.side_effect = MALAPIError(
            "MAL API returned an error"
        )

        result = mal.call(name, **{id_arg: 1}, score=5)

        assert result["error"] == "MAL API returned an error"

    @pytest.mark.parametrize(("name", "id_arg", "kind"), UPDATE_TOOLS)
    def test_update_returns_error_payload_without_token(
        self, mal, monkeypatch, name, id_arg, kind
    ):
        monkeypatch.setattr(
            tools,
            "get_mal_access_token",
            AsyncMock(side_effect=MALAuthError("Call 'mal_auth_login' first")),
        )

        result = mal.call(name, **{id_arg: 1}, score=5)

        assert "mal_auth_login" in result["error"]
        mal.client.patch_authed.assert_not_awaited()


class TestDeleteTools:
    @pytest.mark.parametrize(("name", "id_arg", "kind", "label"), DELETE_TOOLS)
    def test_delete_targets_list_entry_of_given_id(
        self, mal, name, id_arg, kind, label
    ):
        result = mal.call(name, **{id_arg: 42})

        mal.client.delete_authed.assert_awaited_once_with(
            f"/{kind}/42/my_list_status", "test-token"
        )
        assert result == {"message": f"{label} ID 42 deleted successfully"}

    @pytest.mark.parametrize(("name", "id_arg", "kind", "label"), DELETE_TOOLS)
    def test_delete_returns_error_payload_when_mal_rejects(
        self, mal, name, id_arg, kind, label
    ):
        mal.client.delete_authed.side_effect = MALAPIError("not found")

        result = mal.call(name, **{id_arg: 42})

        assert result["error"] == "not found"
        assert "message" not in result


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
        with pytest.raises(ToolError):
            mal.call_over_mcp(name, arguments)

        mal.client.patch_authed.assert_not_awaited()

    def test_valid_arguments_reach_mal(self, mal):
        mal.call_over_mcp(
            "update_myanimelist",
            {"anime_id": 1, "status": "completed", "score": 10},
        )

        mal.client.patch_authed.assert_awaited_once_with(
            "/anime/1/my_list_status",
            "test-token",
            data={"status": "completed", "score": 10},
        )

    def test_delete_rejects_non_integer_id(self, mal):
        with pytest.raises(ToolError):
            mal.call_over_mcp("delete_myanimelist_item", {"anime_id": "abc"})

        mal.client.delete_authed.assert_not_awaited()
