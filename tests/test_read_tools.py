from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from tools import tools
from utils.auth import MALAuthError
from utils.mal_client import MALAPIError
from utils.schemas import (
    AnimeFields,
    AnimeStatus,
    AnimeStatusSort,
    MangaStatus,
    MangaStatusSort,
    Season,
    SeasonSort,
)

ALL_FIELDS = ",".join(field.value for field in AnimeFields)

PUBLIC_TOOLS = [
    ("get_anime", {"q": "naruto"}),
    ("get_anime_details", {"anime_id": 1}),
    ("get_anime_ranking", {}),
    ("get_seasonal_anime", {"season": Season.SPRING, "year": 2024}),
    ("get_anime_list", {"username": "someone"}),
    ("get_manga", {"q": "berserk"}),
    ("get_manga_details", {"manga_id": 2}),
    ("get_manga_ranking", {}),
    ("get_manga_list", {"username": "someone"}),
    ("get_forum_boards", {}),
    ("get_forum_topic", {"topic_id": 3}),
    ("get_forum_topics", {}),
]
AUTHED_TOOLS = [
    ("get_my_anime_list", {}),
    ("get_my_manga_list", {}),
    ("get_suggested_anime", {}),
    ("get_user_profile", {}),
]


class TestPublicTools:
    @pytest.mark.parametrize(
        ("name", "endpoint"),
        [("get_anime", "/anime"), ("get_manga", "/manga")],
    )
    def test_search_clamps_limit_and_offset(self, mal, name, endpoint):
        mal.call(name, q="one piece", limit=500, offset=-3)

        mal.client.get_public.assert_awaited_once_with(
            endpoint, params={"q": "one piece", "limit": 100, "offset": 0}
        )

    @pytest.mark.parametrize(
        ("name", "id_arg", "endpoint"),
        [
            ("get_anime_details", "anime_id", "/anime/5"),
            ("get_manga_details", "manga_id", "/manga/5"),
        ],
    )
    def test_details_request_all_fields(self, mal, name, id_arg, endpoint):
        mal.call(name, **{id_arg: 5})

        mal.client.get_public.assert_awaited_once_with(
            endpoint, params={"fields": ALL_FIELDS}
        )

    def test_seasonal_anime_passes_sort(self, mal):
        mal.call(
            "get_seasonal_anime",
            season=Season.WINTER,
            year=2025,
            sort=SeasonSort.SCORE,
        )

        mal.client.get_public.assert_awaited_once_with(
            "/anime/season/2025/winter",
            params={"limit": 10, "offset": 0, "sort": "anime_score"},
        )

    @pytest.mark.parametrize(
        ("name", "status", "sort", "endpoint"),
        [
            (
                "get_anime_list",
                AnimeStatus.WATCHING,
                AnimeStatusSort.LIST_SCORE,
                "/users/someone/animelist",
            ),
            (
                "get_manga_list",
                MangaStatus.READING,
                MangaStatusSort.LIST_SCORE,
                "/users/someone/mangalist",
            ),
        ],
    )
    def test_user_list_passes_status_sort_and_nsfw(
        self, mal, name, status, sort, endpoint
    ):
        mal.call(
            name,
            username="someone",
            status=status,
            sort=sort,
            limit=5000,
            nsfw=True,
        )

        mal.client.get_public.assert_awaited_once_with(
            endpoint,
            params={
                "limit": 1000,
                "offset": 0,
                "status": status.value,
                "sort": sort.value,
                "nsfw": "true",
            },
        )

    @pytest.mark.parametrize(
        ("name", "endpoint"),
        [
            ("get_anime_list", "/users/a%2Fb%3Fx%3D1/animelist"),
            ("get_manga_list", "/users/a%2Fb%3Fx%3D1/mangalist"),
        ],
    )
    def test_user_list_encodes_username_in_path(self, mal, name, endpoint):
        mal.call(name, username="a/b?x=1")

        assert mal.client.get_public.call_args.args == (endpoint,)

    def test_forum_boards(self, mal):
        mal.call("get_forum_boards")

        mal.client.get_public.assert_awaited_once_with("/forum/boards")

    def test_forum_topic_clamps_limit_and_offset(self, mal):
        mal.call("get_forum_topic", topic_id=3, limit=500, offset=-1)

        mal.client.get_public.assert_awaited_once_with(
            "/forum/topic/3", params={"limit": 100, "offset": 0}
        )

    def test_forum_topics_passes_user_filters(self, mal):
        mal.call(
            "get_forum_topics", topic_user_name="author", user_name="poster"
        )

        mal.client.get_public.assert_awaited_once_with(
            "/forum/topics",
            params={
                "limit": 100,
                "offset": 0,
                "sort": "recent",
                "topic_user_name": "author",
                "user_name": "poster",
            },
        )

    def test_unexpected_error_is_not_turned_into_payload(self, mal):
        mal.client.get_public.side_effect = KeyError("data")

        with pytest.raises(KeyError):
            mal.call("get_anime", q="naruto")

    @pytest.mark.parametrize(("name", "kwargs"), PUBLIC_TOOLS)
    def test_mal_error_is_returned_as_payload(self, mal, name, kwargs):
        mal.client.get_public.side_effect = MALAPIError("MAL is down")

        result = mal.call(name, **kwargs)

        assert result["error"] == "MAL is down"


class TestAuthenticatedTools:
    @pytest.mark.parametrize(
        ("name", "endpoint"),
        [
            ("get_my_anime_list", "/users/@me/animelist"),
            ("get_my_manga_list", "/users/@me/mangalist"),
        ],
    )
    def test_own_list_passes_nsfw(self, mal, name, endpoint):
        mal.call(name, nsfw=True)

        mal.client.get_authed.assert_awaited_once_with(
            endpoint,
            "test-token",
            params={"limit": 10, "offset": 0, "nsfw": "true"},
        )

    def test_suggested_anime_clamps_limit_and_offset(self, mal):
        mal.call("get_suggested_anime", limit=500, offset=-1)

        mal.client.get_authed.assert_awaited_once_with(
            "/anime/suggestions",
            "test-token",
            params={"limit": 100, "offset": 0},
        )

    @pytest.mark.parametrize(
        ("fields", "params"),
        [(None, None), ("anime_statistics", {"fields": "anime_statistics"})],
    )
    def test_user_profile_passes_fields_only_when_given(
        self, mal, fields, params
    ):
        mal.call("get_user_profile", fields=fields)

        mal.client.get_authed.assert_awaited_once_with(
            "/users/@me", "test-token", params=params
        )

    @pytest.mark.parametrize(("name", "kwargs"), AUTHED_TOOLS)
    def test_mal_error_is_returned_as_payload(self, mal, name, kwargs):
        mal.client.get_authed.side_effect = MALAPIError("token rejected")

        result = mal.call(name, **kwargs)

        assert result["error"] == "token rejected"

    @pytest.mark.parametrize(("name", "kwargs"), AUTHED_TOOLS)
    def test_missing_login_is_returned_as_payload(
        self, mal, monkeypatch, name, kwargs
    ):
        monkeypatch.setattr(
            tools,
            "get_mal_access_token",
            AsyncMock(side_effect=MALAuthError("Call 'mal_auth_login' first")),
        )

        result = mal.call(name, **kwargs)

        assert "mal_auth_login" in result["error"]
        mal.client.get_authed.assert_not_awaited()


class TestAuthTools:
    @pytest.mark.parametrize(
        ("name", "function", "mock_cls"),
        [
            ("mal_auth_status", "get_auth_status", AsyncMock),
            ("mal_auth_login_status", "login_status", AsyncMock),
            ("mal_auth_login", "login_initiate", Mock),
            ("mal_auth_revoke", "revoke_auth", Mock),
        ],
    )
    def test_tool_returns_auth_module_result(
        self, mal, monkeypatch, name, function, mock_cls
    ):
        expected = {"status": f"from {function}"}
        delegate = mock_cls(return_value=expected)
        monkeypatch.setattr(tools, function, delegate)

        assert mal.call(name) == expected
        delegate.assert_called_once_with()


@pytest.fixture
def calendar_http(monkeypatch):
    response = Mock()
    response.text = "BEGIN:VCALENDAR\nEND:VCALENDAR"
    response.raise_for_status = Mock()

    http = AsyncMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    http.get = AsyncMock(return_value=response)
    monkeypatch.setattr(tools.httpx, "AsyncClient", Mock(return_value=http))
    return http, response


class TestUpcomingAnimeEpisodes:
    def test_fetches_user_calendar_and_clamps_hours(
        self, mal, monkeypatch, calendar_http
    ):
        http, response = calendar_http
        digest = Mock(return_value={"events": []})
        monkeypatch.setattr(tools, "upcoming_events_digest", digest)

        result = mal.call(
            "get_upcoming_anime_episodes", username="some one", hours=1000
        )

        http.get.assert_awaited_once_with(
            "https://api.anime-calendar.com/v3/ical/myanimelist/some%20one"
        )
        digest.assert_called_once_with(response.text, hours=24 * 14)
        assert result == {
            "username": "some one",
            "hours": 24 * 14,
            "digest": {"events": []},
        }

    def test_calendar_http_error_is_returned_as_payload(
        self, mal, calendar_http
    ):
        _, response = calendar_http
        request = httpx.Request("GET", "https://api.anime-calendar.com")
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404",
            request=request,
            response=httpx.Response(404, request=request),
        )

        result = mal.call("get_upcoming_anime_episodes", username="nobody")

        assert result["status_code"] == 404
        assert "error" in result
