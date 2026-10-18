import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from utils import anilist
from utils.anilist import (
    ANILIST_URL,
    AiringEpisode,
    AniListError,
    fetch_upcoming_episodes,
)


def _page(*media):
    return httpx.Response(200, json={"data": {"Page": {"media": list(media)}}})


def _media(mal_id, *episodes):
    nodes = [
        {"episode": episode, "airingAt": airing_at}
        for episode, airing_at in episodes
    ]
    return {"idMal": mal_id, "airingSchedule": {"nodes": nodes}}


@pytest.fixture
def anilist_http(monkeypatch):
    http = AsyncMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    http.post = AsyncMock(return_value=_page())
    monkeypatch.setattr(anilist.httpx, "AsyncClient", Mock(return_value=http))
    return http


def fetch(mal_ids):
    return asyncio.run(fetch_upcoming_episodes(mal_ids))


def test_returns_upcoming_episodes_by_mal_id_in_air_order(anilist_http):
    anilist_http.post.return_value = _page(
        _media(21, (1182, 1_800_000_000), (1181, 1_799_000_000)),
        _media(235),
    )

    schedules = fetch([21, 235])

    assert schedules == {
        21: [
            AiringEpisode(1181, datetime.fromtimestamp(1_799_000_000, UTC)),
            AiringEpisode(1182, datetime.fromtimestamp(1_800_000_000, UTC)),
        ]
    }


def test_queries_anime_by_mal_ids(anilist_http):
    fetch([3, 1, 3])

    anilist_http.post.assert_awaited_once()
    url = anilist_http.post.await_args.args[0]
    body = anilist_http.post.await_args.kwargs["json"]
    assert url == ANILIST_URL
    assert body["variables"] == {"ids": [1, 3]}
    assert "idMal_in: $ids, type: ANIME" in body["query"]


def test_splits_ids_into_pages_of_fifty(anilist_http):
    fetch(range(1, 121))

    pages = [
        call.kwargs["json"]["variables"]["ids"]
        for call in anilist_http.post.await_args_list
    ]
    assert [len(page) for page in pages] == [50, 50, 20]
    assert [mal_id for page in pages for mal_id in page] == list(range(1, 121))


def test_no_ids_make_no_request(anilist_http):
    assert fetch([]) == {}

    anilist_http.post.assert_not_awaited()


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(
                429,
                json={"data": None, "errors": [{"message": "Too Many"}]},
            ),
            "AniList error: 429 Too Many Requests: Too Many",
        ),
        (
            httpx.Response(502, text="<html>Bad gateway</html>"),
            "AniList error: 502 Bad Gateway",
        ),
        (
            httpx.Response(
                200, json={"data": None, "errors": [{"message": "Bad"}]}
            ),
            "AniList error: 200 OK: Bad",
        ),
        (httpx.Response(200, json=[]), "AniList error: 200 OK"),
        (
            httpx.Response(400, json={"errors": ["not a dict"]}),
            "AniList error: 400 Bad Request",
        ),
    ],
)
def test_failures_raise_anilist_error(anilist_http, response, error):
    anilist_http.post.return_value = response

    with pytest.raises(AniListError) as raised:
        fetch([1])

    assert str(raised.value) == error
    assert raised.value.payload() == {
        "error": error,
        "status_code": response.status_code,
    }
