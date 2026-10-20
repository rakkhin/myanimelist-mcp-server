import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest
from fastmcp.exceptions import ToolError
from payloads import tool_payload

from tools import tools
from utils.anilist import AiringEpisode, AniListError
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


SCHEDULE_TOOLS = ["get_upcoming_anime_episodes", "get_watch_queue"]
SCHEDULE_LIST_FIELDS = "num_episodes,status,list_status"
SCHEDULE = {1: [AiringEpisode(4, datetime(2026, 5, 25, tzinfo=UTC))]}


def _node(mal_id, status):
    return {
        "node": {"id": mal_id, "title": f"Show {mal_id}", "status": status}
    }


LISTS = {
    AnimeStatus.WATCHING: [
        _node(1, "currently_airing"),
        _node(2, "finished_airing"),
    ],
    AnimeStatus.PLANNING: [_node(3, "not_yet_aired")],
}


@pytest.fixture
def schedules(mal, monkeypatch):
    def own_list(endpoint, token, params):
        return {"data": LISTS[params["status"]]}

    mal.client.get_authed.side_effect = own_list
    fetch = AsyncMock(return_value=SCHEDULE)
    monkeypatch.setattr(tools, "fetch_upcoming_episodes", fetch)
    return fetch


def _list_request(status):
    params = {
        "status": status,
        "limit": 1000,
        "offset": 0,
        "fields": SCHEDULE_LIST_FIELDS,
    }
    return call("/users/@me/animelist", "test-token", params=params)


class TestUpcomingAnimeEpisodes:
    def test_covers_watching_and_planned_airing_titles(
        self, mal, monkeypatch, schedules
    ):
        digest = Mock(return_value={"digest": "..."})
        monkeypatch.setattr(tools, "upcoming_episodes", digest)

        result = mal.call("get_upcoming_anime_episodes", hours=1000)

        assert mal.client.get_authed.await_args_list == [
            _list_request(AnimeStatus.WATCHING),
            _list_request(AnimeStatus.PLANNING),
        ]
        schedules.assert_awaited_once_with([1, 3])
        anime = [*LISTS[AnimeStatus.WATCHING], *LISTS[AnimeStatus.PLANNING]]
        digest.assert_called_once_with(anime, SCHEDULE, hours=24 * 14)
        assert result == {"digest": "..."}

    def test_returns_digest_over_mcp(self, mal, schedules):
        result = mal.call_over_mcp("get_upcoming_anime_episodes", {})

        assert tool_payload(result)["hours"] == 36
        assert "digest" in tool_payload(result)


class TestGetWatchQueue:
    def test_combines_watching_list_with_schedules(
        self, mal, monkeypatch, schedules
    ):
        queue = Mock(return_value={"entries": []})
        monkeypatch.setattr(tools, "build_watch_queue", queue)

        result = mal.call("get_watch_queue")

        assert mal.client.get_authed.await_args_list == [
            _list_request(AnimeStatus.WATCHING)
        ]
        schedules.assert_awaited_once_with([1])
        queue.assert_called_once_with(LISTS[AnimeStatus.WATCHING], SCHEDULE)
        assert result == {"entries": []}

    def test_unexpected_list_body_counts_as_empty(
        self, mal, monkeypatch, schedules
    ):
        mal.client.get_authed.side_effect = None
        mal.client.get_authed.return_value = {"ok": True}

        result = mal.call("get_watch_queue")

        schedules.assert_awaited_once_with([])
        assert result["entries"] == []


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


class TestScheduleTools:
    @pytest.mark.parametrize("name", SCHEDULE_TOOLS)
    def test_takes_no_username(self, mal, name):
        tool = asyncio.run(mal.mcp.get_tool(name))

        assert "username" not in tool.parameters["properties"]

    @pytest.mark.parametrize("name", SCHEDULE_TOOLS)
    def test_requires_login(self, mal, schedules, monkeypatch, name):
        monkeypatch.setattr(
            tools,
            "get_mal_access_token",
            AsyncMock(side_effect=MALAuthError("call 'mal_auth_login'")),
        )

        result = mal.call_over_mcp(name, {})

        assert "mal_auth_login" in tool_payload(result)["error"]
        mal.client.get_authed.assert_not_awaited()
        schedules.assert_not_awaited()

    @pytest.mark.parametrize("name", SCHEDULE_TOOLS)
    def test_anilist_error_is_returned_as_payload(self, mal, schedules, name):
        response = httpx.Response(
            429,
            json={"errors": [{"message": "Too Many Requests."}]},
            request=httpx.Request("POST", "https://graphql.anilist.co"),
        )
        schedules.side_effect = AniListError(response, response.json())

        result = mal.call_over_mcp(name, {})

        assert tool_payload(result) == {
            "error": "AniList error: 429 Too Many Requests: "
            "Too Many Requests.",
            "status_code": 429,
        }

    @pytest.mark.parametrize("name", SCHEDULE_TOOLS)
    def test_network_failure_is_returned_as_payload(
        self, mal, schedules, name
    ):
        schedules.side_effect = httpx.ConnectError("anilist unreachable")

        result = mal.call(name)

        assert result == {"error": "anilist unreachable"}
