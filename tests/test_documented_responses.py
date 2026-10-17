"""Mutating tools against the response samples in MAL's API documentation.

These tools change the user's list, so their fixtures in
tests/fixtures/mal_docs/ come from the documentation, not from recordings.
Each request below is the documentation's own curl example.
"""

import json
from pathlib import Path

import httpx
import pytest

DOCS_DIR = Path(__file__).parent / "fixtures" / "mal_docs"


def load(name: str):
    return json.loads((DOCS_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "arguments", "endpoint", "form"),
    [
        (
            "update_myanimelist",
            {
                "anime_id": 17074,
                "status": "completed",
                "score": 8,
                "num_watched_episodes": 3,
            },
            "/anime/17074/my_list_status",
            {"status": "completed", "score": 8, "num_watched_episodes": 3},
        ),
        (
            "update_mymangalist",
            {"manga_id": 2, "status": "completed", "score": 8},
            "/manga/2/my_list_status",
            {"status": "completed", "score": 8},
        ),
    ],
)
def test_update_sends_documented_request_and_returns_documented_response(
    mal, name, arguments, endpoint, form
):
    documented = load(f"{name}.json")
    mal.client.put_authed.return_value = documented

    result = mal.call_over_mcp(name, arguments)

    mal.client.put_authed.assert_awaited_once_with(
        endpoint, "test-token", data=form
    )
    assert not result.is_error
    assert result.structured_content == documented


DELETE_TOOLS = [
    ("delete_myanimelist_item", "anime_id", "Anime"),
    ("delete_mymangalist_item", "manga_id", "Manga"),
]


@pytest.mark.parametrize(("name", "id_arg", "label"), DELETE_TOOLS)
def test_delete_treats_documented_null_response_as_success(
    mal, name, id_arg, label
):
    mal.client.delete_authed.return_value = load(f"{name}.json")

    result = mal.call_over_mcp(name, {id_arg: 21})

    assert result.structured_content == {
        "message": f"{label} ID 21 deleted successfully"
    }


@pytest.mark.parametrize(("name", "id_arg", "label"), DELETE_TOOLS)
def test_delete_reports_documented_404_for_title_not_on_list(
    mal, name, id_arg, label
):
    request = httpx.Request("DELETE", "https://api.myanimelist.net/v2/x")
    mal.client.delete_authed.side_effect = httpx.HTTPStatusError(
        "404", request=request, response=httpx.Response(404, request=request)
    )

    result = mal.call_over_mcp(name, {id_arg: 21})

    assert result.structured_content == {
        "error": "MAL API error: 404 Not Found",
        "status_code": 404,
    }
