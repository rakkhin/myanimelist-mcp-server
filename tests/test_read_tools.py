from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastmcp.exceptions import ToolError

from tools import tools
from utils.auth import MALAuthError
from utils.mal_client import MALAPIError
from utils.schemas import (
    AnimeFields,
    AnimeRanking,
    AnimeStatus,
    AnimeStatusSort,
    MangaFields,
    MangaRanking,
    MangaStatus,
    MangaStatusSort,
    Season,
    SeasonSort,
)

ALL_ANIME_FIELDS = ",".join(AnimeFields)
ALL_MANGA_FIELDS = ",".join(
    "authors{first_name,last_name}" if f == MangaFields.AUTHORS else f
    for f in MangaFields
)

DEFAULT_ANIME_FIELDS = (
    f"{AnimeFields.ID},{AnimeFields.TITLE},{AnimeFields.ALTERNATIVE_TITLES},"
    f"{AnimeFields.MAIN_PICTURE},{AnimeFields.NSFW},{AnimeFields.MEAN}"
)
DEFAULT_MANGA_FIELDS = (
    f"{MangaFields.ID},{MangaFields.TITLE},{MangaFields.ALTERNATIVE_TITLES},"
    f"{MangaFields.MAIN_PICTURE},{MangaFields.NSFW},{MangaFields.MEAN}"
)
DEFAULT_ANIME_LIST_FIELDS = f"{DEFAULT_ANIME_FIELDS},list_status"
DEFAULT_MANGA_LIST_FIELDS = f"{DEFAULT_MANGA_FIELDS},list_status"

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
        ("name", "endpoint", "default_fields"),
        [
            ("get_anime", "/anime", DEFAULT_ANIME_FIELDS),
            ("get_manga", "/manga", DEFAULT_MANGA_FIELDS),
        ],
    )
    def test_search_clamps_limit_and_offset(
        self, mal, name, endpoint, default_fields
    ):
        mal.call(name, q="one piece", limit=500, offset=-3)

        mal.client.get_public.assert_awaited_once_with(
            endpoint,
            params={
                "q": "one piece",
                "limit": 100,
                "offset": 0,
                "fields": default_fields,
            },
        )

    @pytest.mark.parametrize(
        ("name", "id_arg", "endpoint", "fields"),
        [
            ("get_anime_details", "anime_id", "/anime/5", ALL_ANIME_FIELDS),
            ("get_manga_details", "manga_id", "/manga/5", ALL_MANGA_FIELDS),
        ],
    )
    def test_details_request_all_fields(
        self, mal, name, id_arg, endpoint, fields
    ):
        mal.call(name, **{id_arg: 5})

        mal.client.get_public.assert_awaited_once_with(
            endpoint, params={"fields": fields}
        )

    @pytest.mark.parametrize(
        ("name", "ranking_type", "endpoint", "default_fields"),
        [
            (
                "get_anime_ranking",
                AnimeRanking.AIRING,
                "/anime/ranking",
                DEFAULT_ANIME_FIELDS,
            ),
            (
                "get_manga_ranking",
                MangaRanking.NOVEL,
                "/manga/ranking",
                DEFAULT_MANGA_FIELDS,
            ),
        ],
    )
    def test_ranking_passes_type_as_query_parameter(
        self, mal, name, ranking_type, endpoint, default_fields
    ):
        mal.call(name, ranking_type=ranking_type, limit=600, offset=-1)

        mal.client.get_public.assert_awaited_once_with(
            endpoint,
            params={
                "ranking_type": ranking_type,
                "limit": 500,
                "offset": 0,
                "fields": default_fields,
            },
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
            params={
                "limit": 10,
                "offset": 0,
                "sort": "anime_score",
                "fields": DEFAULT_ANIME_FIELDS,
            },
        )

    @pytest.mark.parametrize(
        ("name", "status", "sort", "endpoint", "list_fields"),
        [
            (
                "get_anime_list",
                AnimeStatus.WATCHING,
                AnimeStatusSort.LIST_SCORE,
                "/users/someone/animelist",
                DEFAULT_ANIME_LIST_FIELDS,
            ),
            (
                "get_manga_list",
                MangaStatus.READING,
                MangaStatusSort.LIST_SCORE,
                "/users/someone/mangalist",
                DEFAULT_MANGA_LIST_FIELDS,
            ),
        ],
    )
    def test_user_list_passes_status_sort_and_nsfw(
        self, mal, name, status, sort, endpoint, list_fields
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
                "status": status,
                "sort": sort,
                "nsfw": "true",
                "fields": list_fields,
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
        ("name", "endpoint", "list_fields"),
        [
            (
                "get_my_anime_list",
                "/users/@me/animelist",
                DEFAULT_ANIME_LIST_FIELDS,
            ),
            (
                "get_my_manga_list",
                "/users/@me/mangalist",
                DEFAULT_MANGA_LIST_FIELDS,
            ),
        ],
    )
    def test_own_list_passes_nsfw(self, mal, name, endpoint, list_fields):
        mal.call(name, nsfw=True)

        mal.client.get_authed.assert_awaited_once_with(
            endpoint,
            "test-token",
            params={
                "limit": 10,
                "offset": 0,
                "nsfw": "true",
                "fields": list_fields,
            },
        )

    @pytest.mark.parametrize(
        ("name", "field", "list_fields"),
        [
            (
                "get_my_anime_list",
                AnimeFields.NUM_EPISODES,
                DEFAULT_ANIME_LIST_FIELDS,
            ),
            (
                "get_my_manga_list",
                MangaFields.NUM_CHAPTERS,
                DEFAULT_MANGA_LIST_FIELDS,
            ),
        ],
    )
    def test_own_list_appends_list_status_to_requested_fields(
        self, mal, name, field, list_fields
    ):
        mal.call(name, fields=[field])

        fields = mal.client.get_authed.call_args.kwargs["params"]["fields"]
        assert fields == f"{field},{list_fields}"

    def test_suggested_anime_clamps_limit_and_offset(self, mal):
        mal.call("get_suggested_anime", limit=500, offset=-1)

        mal.client.get_authed.assert_awaited_once_with(
            "/anime/suggestions",
            "test-token",
            params={
                "limit": 100,
                "offset": 0,
                "fields": DEFAULT_ANIME_FIELDS,
            },
        )

    def test_user_profile_requests_anime_statistics_by_default(self, mal):
        mal.call_over_mcp("get_user_profile", {})

        mal.client.get_authed.assert_awaited_once_with(
            "/users/@me", "test-token", params={"fields": "anime_statistics"}
        )

    @pytest.mark.parametrize(
        ("fields", "params"),
        [(None, None), ("birthday", {"fields": "birthday"})],
    )
    def test_user_profile_explicit_fields_replace_default(
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


MANGA_FIELD_TOOLS = [
    ("get_manga_ranking", "get_public"),
    ("get_my_manga_list", "get_authed"),
]


class TestMangaFieldsOverMcp:
    @pytest.mark.parametrize(("name", "client_method"), MANGA_FIELD_TOOLS)
    def test_manga_tools_accept_manga_fields(self, mal, name, client_method):
        mal.call_over_mcp(name, {"fields": ["num_chapters", "authors"]})

        params = getattr(mal.client, client_method).call_args.kwargs["params"]
        assert params["fields"].startswith(
            "num_chapters,authors{first_name,last_name},"
        )

    @pytest.mark.parametrize(("name", "client_method"), MANGA_FIELD_TOOLS)
    def test_manga_tools_reject_anime_only_fields(
        self, mal, name, client_method
    ):
        with pytest.raises(ToolError):
            mal.call_over_mcp(name, {"fields": ["num_episodes"]})

        getattr(mal.client, client_method).assert_not_awaited()


class TestDefaultFields:
    @pytest.mark.parametrize(
        ("name", "kwargs", "client_method", "expected"),
        [
            ("get_anime", {"q": "x"}, "get_public", DEFAULT_ANIME_FIELDS),
            ("get_manga", {"q": "x"}, "get_public", DEFAULT_MANGA_FIELDS),
            ("get_anime_ranking", {}, "get_public", DEFAULT_ANIME_FIELDS),
            ("get_manga_ranking", {}, "get_public", DEFAULT_MANGA_FIELDS),
            (
                "get_seasonal_anime",
                {"season": Season.SPRING, "year": 2024},
                "get_public",
                DEFAULT_ANIME_FIELDS,
            ),
            (
                "get_anime_list",
                {"username": "someone"},
                "get_public",
                DEFAULT_ANIME_LIST_FIELDS,
            ),
            (
                "get_manga_list",
                {"username": "someone"},
                "get_public",
                DEFAULT_MANGA_LIST_FIELDS,
            ),
            ("get_my_anime_list", {}, "get_authed", DEFAULT_ANIME_LIST_FIELDS),
            ("get_my_manga_list", {}, "get_authed", DEFAULT_MANGA_LIST_FIELDS),
            ("get_suggested_anime", {}, "get_authed", DEFAULT_ANIME_FIELDS),
        ],
    )
    def test_tools_request_default_fields_without_fields_argument(
        self, mal, name, kwargs, client_method, expected
    ):
        mal.call(name, **kwargs)

        params = getattr(mal.client, client_method).call_args.kwargs["params"]
        assert params["fields"] == expected


CALENDAR_URL = "https://api.anime-calendar.com/v3/ical/myanimelist/{}"


def _authed_responses(endpoint, token, params=None):
    if endpoint == "/users/@me":
        return {"id": 1, "name": "own_name"}
    return {"data": []}


class TestCalendarUsername:
    @pytest.mark.parametrize(
        "name", ["get_upcoming_anime_episodes", "get_watch_queue"]
    )
    def test_defaults_to_logged_in_user(self, mal, calendar_http, name):
        http, _ = calendar_http
        mal.client.get_authed.side_effect = _authed_responses

        result = mal.call_over_mcp(name, {})

        assert not result.is_error
        http.get.assert_awaited_once_with(CALENDAR_URL.format("own_name"))
        mal.client.get_authed.assert_any_await("/users/@me", "test-token")

    @pytest.mark.parametrize(
        "name", ["get_upcoming_anime_episodes", "get_watch_queue"]
    )
    def test_explicit_username_is_used_without_profile_lookup(
        self, mal, calendar_http, name
    ):
        http, _ = calendar_http
        mal.client.get_authed.side_effect = _authed_responses

        mal.call_over_mcp(name, {"username": "someone"})

        http.get.assert_awaited_once_with(CALENDAR_URL.format("someone"))
        assert all(
            c.args[0] != "/users/@me"
            for c in mal.client.get_authed.await_args_list
        )

    def test_upcoming_episodes_with_username_need_no_login(
        self, mal, calendar_http, monkeypatch
    ):
        http, _ = calendar_http
        monkeypatch.setattr(
            tools,
            "get_mal_access_token",
            AsyncMock(side_effect=MALAuthError("not logged in")),
        )

        result = mal.call(
            "get_upcoming_anime_episodes", username="someone", hours=36
        )

        assert result["username"] == "someone"
        http.get.assert_awaited_once()

    def test_upcoming_episodes_without_username_or_login_ask_for_one(
        self, mal, calendar_http, monkeypatch
    ):
        http, _ = calendar_http
        monkeypatch.setattr(
            tools,
            "get_mal_access_token",
            AsyncMock(side_effect=MALAuthError("not logged in")),
        )

        result = mal.call_over_mcp("get_upcoming_anime_episodes", {})

        assert "username" in result.structured_content["error"]
        assert "mal_auth_login" in result.structured_content["error"]
        http.get.assert_not_awaited()


CALENDAR_403 = {
    "message": "Could not access MyAnimeList information for the given "
    "user. Make sure your currently watching and planed to watch lists are "
    "public.",
    "code": 403,
}


@pytest.mark.parametrize(
    "name", ["get_upcoming_anime_episodes", "get_watch_queue"]
)
def test_calendar_refusal_is_reported_with_its_own_message(
    mal, calendar_http, name
):
    _, response = calendar_http
    request = httpx.Request("GET", CALENDAR_URL.format("someone"))
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403",
        request=request,
        response=httpx.Response(403, json=CALENDAR_403, request=request),
    )

    result = mal.call_over_mcp(name, {"username": "someone"})

    assert result.structured_content == {
        "error": "Anime Calendar error: 403 Forbidden: "
        + CALENDAR_403["message"],
        "status_code": 403,
    }


def test_calendar_network_failure_is_returned_as_payload(mal, calendar_http):
    http, _ = calendar_http
    http.get.side_effect = httpx.ConnectError("calendar unreachable")

    result = mal.call("get_upcoming_anime_episodes", username="someone")

    assert result == {"error": "calendar unreachable"}
