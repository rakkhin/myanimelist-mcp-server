"""List-changing tools against MAL's documented and observed responses.

These tools change the user's list, so the update samples in
tests/fixtures/mal_docs/ come from MAL's API documentation, and each update
request below is the documentation's own curl example. A round trip on a
real list matched those samples exactly. It also showed that deletes answer
200 with "[]" (the documentation shows null), even for a title that is not
on the list, so the delete files hold that recorded body. MALClient turns it
into {"ok": True}, which is what the delete tools receive. MAL answers 404
only for an anime or manga id that does not exist; not_found.json holds
that recorded body.
"""

import json
from pathlib import Path

import httpx
import pytest
from payloads import tool_payload

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
    mal.client.patch_authed.return_value = documented

    result = mal.call_over_mcp(name, arguments)

    mal.client.patch_authed.assert_awaited_once_with(
        endpoint, "test-token", data=form
    )
    assert not result.is_error
    assert tool_payload(result) == documented


DELETE_TOOLS = [
    ("delete_myanimelist_item", "anime_id", "Anime"),
    ("delete_mymangalist_item", "manga_id", "Manga"),
]


@pytest.mark.parametrize(("name", "id_arg", "label"), DELETE_TOOLS)
def test_delete_reports_success_for_empty_client_result(
    mal, name, id_arg, label
):
    mal.client.delete_authed.return_value = {"ok": True}

    result = mal.call_over_mcp(name, {id_arg: 21})

    assert tool_payload(result) == {
        "message": f"{label} ID 21 deleted successfully"
    }


@pytest.mark.parametrize(("name", "id_arg", "label"), DELETE_TOOLS)
def test_delete_reports_nonexistent_title_as_error_payload(
    mal, name, id_arg, label
):
    request = httpx.Request("DELETE", "https://api.myanimelist.net/v2/x")
    response = httpx.Response(
        404, request=request, json=load("not_found.json")
    )
    mal.client.delete_authed.side_effect = httpx.HTTPStatusError(
        "404", request=request, response=response
    )

    result = mal.call_over_mcp(name, {id_arg: 21})

    assert tool_payload(result) == {
        "error": "MAL API error: 404 Not Found",
        "status_code": 404,
    }
