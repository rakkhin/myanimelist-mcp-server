from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations
from pydantic import Field

from utils.anilist import AniListError, fetch_upcoming_episodes
from utils.auth import (
    MALAuthError,
    expire_access_token,
    get_auth_status,
    get_mal_access_token,
    login_initiate,
    login_status,
    revoke_auth,
)
from utils.episodes import airing_ids, build_watch_queue, upcoming_episodes
from utils.mal_client import (
    MALAPIError,
    MALClient,
    api_error_payload,
    build_fields,
    clamp_limit,
)
from utils.schemas import *
from utils.serialization import format_tool_payload

load_dotenv()

_MAL_NEWS_RSS_URL = "https://myanimelist.net/rss/news.xml"
_MAL_NEWS_LIMIT = 20
_TRACKING_QUERY_RE = re.compile(r"[?&]_location=rss\b")
_MEDIA_NS = "http://search.yahoo.com/mrss/"

# Errors tools report back as an error payload. Anything else is a bug and is
# left to FastMCP, which logs it and returns a generic tool error.
_TOOL_ERRORS = (MALAPIError, MALAuthError, httpx.HTTPError, ValueError)

READ_ONLY = ToolAnnotations(read_only_hint=True)
MUTATING = ToolAnnotations(read_only_hint=False)


def _tool_result(data: dict) -> ToolResult:
    """Wrap a payload as the result's only content: TOON or JSON text.

    No structured content: hosts such as Claude Code show the model that
    instead of the text, and the spec expects JSON text next to it.
    """
    return ToolResult(content=format_tool_payload(data))


# User list items hold the user's entry (status, score, progress) in
# list_status, next to the title's node, and MAL only returns it on request.
_LIST_STATUS = "list_status"


def _with_list_status(node_fields: str) -> str:
    return f"{node_fields},{_LIST_STATUS}"


# Fields every anime and manga response includes, so it has the same shape
# whether or not the caller asks for more. Requested fields come first.
_DEFAULT_ANIME_FIELDS = (
    AnimeFields.ID,
    AnimeFields.TITLE,
    AnimeFields.ALTERNATIVE_TITLES,
    AnimeFields.MAIN_PICTURE,
    AnimeFields.NSFW,
    AnimeFields.MEAN,
)
_DEFAULT_MANGA_FIELDS = (
    MangaFields.ID,
    MangaFields.TITLE,
    MangaFields.ALTERNATIVE_TITLES,
    MangaFields.MAIN_PICTURE,
    MangaFields.NSFW,
    MangaFields.MEAN,
)


def normalize_episode_window(hours: int) -> int:
    return max(1, min(int(hours), 24 * 14))


def _clean_news_url(url: str | None) -> str | None:
    if url is None:
        return None
    return _TRACKING_QUERY_RE.sub("", url)


def parse_mal_news_rss(xml_text: str, limit: int) -> dict:
    root = ElementTree.fromstring(xml_text)

    channel = root.find("channel")
    if channel is None:
        return {"error": "RSS feed missing channel element"}

    articles: list[dict[str, str | None]] = []
    for item in channel.findall("item"):
        if len(articles) >= limit:
            break

        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        pub_el = item.find("pubDate")
        thumb_el = item.find(f"{{{_MEDIA_NS}}}thumbnail")

        title = (
            title_el.text if title_el is not None and title_el.text else None
        )
        url = _clean_news_url(link_el.text) if link_el is not None else None
        description = (
            desc_el.text if desc_el is not None and desc_el.text else None
        )
        published = pub_el.text if pub_el is not None and pub_el.text else None
        thumbnail = None
        if thumb_el is not None:
            # Media RSS puts the URL in a url attribute; MAL uses the text.
            thumbnail = (
                thumb_el.get("url") or (thumb_el.text or "").strip() or None
            )

        if title is None and url is None:
            continue

        articles.append(
            {
                "title": title,
                "url": url,
                "description": description,
                "published": published,
                "thumbnail": thumbnail,
            }
        )

    return {"articles": articles}


def register_tools(mcp: FastMCP):
    mal_client = MALClient()

    def client() -> MALClient:
        return mal_client

    async def token() -> str:
        access_token = await get_mal_access_token()
        if not access_token:
            raise ValueError("No valid MyAnimeList access token available")
        return access_token

    async def authed(request: Callable[[str], Awaitable[dict]]) -> dict:
        """Send an authenticated request, retrying once if MAL rejects the token.

        MAL can revoke a token before its stored expiry. It answers 401
        without running the request, so retrying updates and deletes is safe.
        """
        access_token = await token()
        try:
            return await request(access_token)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != httpx.codes.UNAUTHORIZED:
                raise
        expire_access_token(access_token)
        return await request(await token())

    async def own_anime_list(status: AnimeStatus) -> list[dict]:
        # Fields the episode schedule tools need; MAL always adds id, title.
        fields = _with_list_status(
            build_fields([AnimeFields.NUM_EPISODES, AnimeFields.STATUS], [])
        )
        response = await authed(
            lambda access_token: client().get_authed(
                "/users/@me/animelist",
                access_token,
                params={
                    "status": status,
                    "limit": 1000,
                    "offset": 0,
                    "fields": fields,
                },
            )
        )
        return response.get("data", []) if isinstance(response, dict) else []

    # Anime
    @mcp.tool(annotations=READ_ONLY)
    async def get_anime(
        q: str, limit: int = 10, offset: int = 0
    ) -> ToolResult:
        """Search anime on MyAnimeList."""
        try:
            return _tool_result(
                await client().get_public(
                    "/anime",
                    params={
                        "q": q,
                        "limit": clamp_limit(limit, 100),
                        "offset": max(0, offset),
                        "fields": build_fields(None, _DEFAULT_ANIME_FIELDS),
                    },
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_anime_details(anime_id: int) -> ToolResult:
        """Fetch anime details by MAL ID."""
        try:
            fields_param = build_fields(None, AnimeFields)
            return _tool_result(
                await client().get_public(
                    f"/anime/{anime_id}", params={"fields": fields_param}
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_anime_ranking(
        ranking_type: AnimeRanking = AnimeRanking.ALL,
        limit: int = 10,
        offset: int = 0,
        fields: list[AnimeFields] | None = None,
    ) -> ToolResult:
        """Fetch anime rankings from MyAnimeList."""
        try:
            params: dict[str, object] = {
                "ranking_type": ranking_type,
                "limit": clamp_limit(limit, 500),
                "offset": max(0, offset),
            }
            params["fields"] = build_fields(fields, _DEFAULT_ANIME_FIELDS)
            return _tool_result(
                await client().get_public("/anime/ranking", params=params)
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_seasonal_anime(
        season: Season,
        year: int,
        sort: SeasonSort | None = None,
        limit: int = 10,
        offset: int = 0,
        fields: list[AnimeFields] | None = None,
    ) -> ToolResult:
        """Fetch seasonal anime from MyAnimeList."""
        try:
            params: dict[str, object] = {
                "limit": clamp_limit(limit, 500),
                "offset": max(0, offset),
            }
            if sort:
                params["sort"] = sort
            params["fields"] = build_fields(fields, _DEFAULT_ANIME_FIELDS)
            return _tool_result(
                await client().get_public(
                    f"/anime/season/{year}/{season}", params=params
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_anime_list(
        username: str,
        status: AnimeStatus | None = None,
        sort: AnimeStatusSort | None = None,
        limit: int = 10,
        offset: int = 0,
        nsfw: bool = False,
    ) -> ToolResult:
        """Fetch a public MyAnimeList anime list for a user."""
        try:
            params: dict[str, object] = {
                "limit": clamp_limit(limit, 1000),
                "offset": max(0, offset),
            }
            if status:
                params["status"] = status
            if sort:
                params["sort"] = sort
            if nsfw:
                params["nsfw"] = "true"
            params["fields"] = _with_list_status(
                build_fields(None, _DEFAULT_ANIME_FIELDS)
            )
            return _tool_result(
                await client().get_public(
                    f"/users/{quote(username, safe='')}/animelist",
                    params=params,
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    # Manga
    @mcp.tool(annotations=READ_ONLY)
    async def get_manga(
        q: str, limit: int = 10, offset: int = 0
    ) -> ToolResult:
        """Search manga on MyAnimeList."""
        try:
            return _tool_result(
                await client().get_public(
                    "/manga",
                    params={
                        "q": q,
                        "limit": clamp_limit(limit, 100),
                        "offset": max(0, offset),
                        "fields": build_fields(None, _DEFAULT_MANGA_FIELDS),
                    },
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_manga_details(manga_id: int) -> ToolResult:
        """Fetch manga details by MAL ID."""
        try:
            fields_param = build_fields(None, MangaFields)
            return _tool_result(
                await client().get_public(
                    f"/manga/{manga_id}", params={"fields": fields_param}
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_manga_ranking(
        ranking_type: MangaRanking = MangaRanking.ALL,
        limit: int = 100,
        offset: int = 0,
        fields: list[MangaFields] | None = None,
    ) -> ToolResult:
        """Fetch manga rankings from MyAnimeList."""
        try:
            params: dict[str, object] = {
                "ranking_type": ranking_type,
                "limit": clamp_limit(limit, 500),
                "offset": max(0, offset),
            }
            params["fields"] = build_fields(fields, _DEFAULT_MANGA_FIELDS)
            return _tool_result(
                await client().get_public("/manga/ranking", params=params)
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_manga_list(
        username: str,
        status: MangaStatus | None = None,
        sort: MangaStatusSort | None = None,
        limit: int = 10,
        offset: int = 0,
        nsfw: bool = False,
    ) -> ToolResult:
        """Fetch a public MyAnimeList manga list for a user."""
        try:
            params: dict[str, object] = {
                "limit": clamp_limit(limit, 1000),
                "offset": max(0, offset),
            }
            if status:
                params["status"] = status
            if sort:
                params["sort"] = sort
            if nsfw:
                params["nsfw"] = "true"
            params["fields"] = _with_list_status(
                build_fields(None, _DEFAULT_MANGA_FIELDS)
            )
            return _tool_result(
                await client().get_public(
                    f"/users/{quote(username, safe='')}/mangalist",
                    params=params,
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_my_anime_list(
        status: AnimeStatus | None = None,
        sort: AnimeStatusSort | None = None,
        limit: int = 10,
        offset: int = 0,
        fields: list[AnimeFields] | None = None,
        nsfw: bool = False,
    ) -> ToolResult:
        """Fetch the authenticated user's MyAnimeList anime list."""
        try:
            params: dict[str, object] = {
                "limit": clamp_limit(limit, 1000),
                "offset": max(0, offset),
            }
            if status:
                params["status"] = status
            if sort:
                params["sort"] = sort
            params["fields"] = _with_list_status(
                build_fields(fields, _DEFAULT_ANIME_FIELDS)
            )
            if nsfw:
                params["nsfw"] = "true"
            return _tool_result(
                await authed(
                    lambda access_token: client().get_authed(
                        "/users/@me/animelist", access_token, params=params
                    )
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_my_manga_list(
        status: MangaStatus | None = None,
        sort: MangaStatusSort | None = None,
        limit: int = 10,
        offset: int = 0,
        fields: list[MangaFields] | None = None,
        nsfw: bool = False,
    ) -> ToolResult:
        """Fetch the authenticated user's MyAnimeList manga list."""
        try:
            params: dict[str, object] = {
                "limit": clamp_limit(limit, 1000),
                "offset": max(0, offset),
            }
            if status:
                params["status"] = status
            if sort:
                params["sort"] = sort
            params["fields"] = _with_list_status(
                build_fields(fields, _DEFAULT_MANGA_FIELDS)
            )
            if nsfw:
                params["nsfw"] = "true"
            return _tool_result(
                await authed(
                    lambda access_token: client().get_authed(
                        "/users/@me/mangalist", access_token, params=params
                    )
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    # Forum
    @mcp.tool(annotations=READ_ONLY)
    async def get_forum_boards() -> ToolResult:
        """Fetch MyAnimeList forum boards."""
        try:
            return _tool_result(await client().get_public("/forum/boards"))
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_forum_topic(
        topic_id: int, limit: int = 100, offset: int = 0
    ) -> ToolResult:
        """Fetch a MyAnimeList forum topic by ID."""
        try:
            return _tool_result(
                await client().get_public(
                    f"/forum/topic/{topic_id}",
                    params={
                        "limit": clamp_limit(limit, 100),
                        "offset": max(0, offset),
                    },
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_forum_topics(
        board_id: int | None = None,
        subboard_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "recent",
        q: str | None = None,
        topic_user_name: str | None = None,
        user_name: str | None = None,
    ) -> ToolResult:
        """Fetch MyAnimeList forum topics with optional filters."""
        try:
            params: dict[str, object] = {
                "limit": clamp_limit(limit, 100),
                "offset": max(0, offset),
                "sort": sort,
            }
            if board_id is not None:
                params["board_id"] = board_id
            if subboard_id is not None:
                params["subboard_id"] = subboard_id
            if q:
                params["q"] = q
            if topic_user_name:
                params["topic_user_name"] = topic_user_name
            if user_name:
                params["user_name"] = user_name
            return _tool_result(
                await client().get_public("/forum/topics", params=params)
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    # Auth/account helpers
    @mcp.tool(annotations=READ_ONLY)
    async def mal_auth_status() -> ToolResult:
        """Check whether this MCP has stored MyAnimeList OAuth tokens."""
        return _tool_result(await get_auth_status())

    @mcp.tool(annotations=MUTATING)
    async def mal_auth_revoke() -> ToolResult:
        """Clear locally stored MyAnimeList OAuth tokens."""
        return _tool_result(revoke_auth())

    @mcp.tool(annotations=MUTATING)
    async def mal_auth_login() -> ToolResult:
        """Start a non-blocking MyAnimeList OAuth login and return the authorization URL."""
        return _tool_result(login_initiate())

    @mcp.tool(annotations=READ_ONLY)
    async def mal_auth_login_status() -> ToolResult:
        """Check whether a MyAnimeList OAuth login is pending, completed, or not authenticated."""
        return _tool_result(await login_status())

    @mcp.tool(annotations=READ_ONLY)
    async def get_suggested_anime(
        limit: int = 10, offset: int = 0
    ) -> ToolResult:
        """Fetch suggested anime for the authenticated user."""
        try:
            return _tool_result(
                await authed(
                    lambda access_token: client().get_authed(
                        "/anime/suggestions",
                        access_token,
                        params={
                            "limit": clamp_limit(limit, 100),
                            "offset": max(0, offset),
                            "fields": build_fields(
                                None, _DEFAULT_ANIME_FIELDS
                            ),
                        },
                    )
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_user_profile(
        fields: str | None = "anime_statistics",
    ) -> ToolResult:
        """Fetch the authenticated user's MyAnimeList profile, including anime statistics by default."""
        try:
            params = {"fields": fields} if fields else None
            return _tool_result(
                await authed(
                    lambda access_token: client().get_authed(
                        "/users/@me", access_token, params=params
                    )
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=MUTATING)
    async def delete_myanimelist_item(anime_id: int) -> ToolResult:
        """Delete an anime from the authenticated user's MyAnimeList."""
        try:
            await authed(
                lambda access_token: client().delete_authed(
                    f"/anime/{anime_id}/my_list_status", access_token
                )
            )
            return _tool_result(
                {"message": f"Anime ID {anime_id} deleted successfully"}
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=MUTATING)
    async def delete_mymangalist_item(manga_id: int) -> ToolResult:
        """Delete a manga from the authenticated user's MyAnimeList."""
        try:
            await authed(
                lambda access_token: client().delete_authed(
                    f"/manga/{manga_id}/my_list_status", access_token
                )
            )
            return _tool_result(
                {"message": f"Manga ID {manga_id} deleted successfully"}
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=MUTATING)
    async def update_myanimelist(
        anime_id: Annotated[
            int, Field(description="ID of the anime to update", ge=1)
        ],
        status: Annotated[
            str | None,
            Field(
                description="Anime list status",
                pattern="^(watching|completed|on_hold|dropped|plan_to_watch)$",
            ),
        ] = None,
        score: Annotated[
            int | None,
            Field(description="Score for the anime (0-10)", ge=0, le=10),
        ] = None,
        num_watched_episodes: Annotated[
            int | None, Field(description="Number of episodes watched", ge=0)
        ] = None,
        is_rewatching: Annotated[
            bool | None,
            Field(description="Whether the anime is being rewatched"),
        ] = None,
        priority: Annotated[
            int | None, Field(description="Priority (0-2)", ge=0, le=2)
        ] = None,
        num_times_rewatched: Annotated[
            int | None, Field(description="Number of times rewatched", ge=0)
        ] = None,
        rewatch_value: Annotated[
            int | None, Field(description="Rewatch value (0-5)", ge=0, le=5)
        ] = None,
        tags: Annotated[
            str | None, Field(description="Comma-separated tags")
        ] = None,
        comments: Annotated[
            str | None,
            Field(description="Comments about the anime", max_length=1000),
        ] = None,
    ) -> ToolResult:
        """Update an anime's status in the authenticated user's MyAnimeList."""
        try:
            fields = {
                "status": status,
                "score": score,
                "num_watched_episodes": num_watched_episodes,
                "is_rewatching": is_rewatching,
                "priority": priority,
                "num_times_rewatched": num_times_rewatched,
                "rewatch_value": rewatch_value,
                "tags": tags,
                "comments": comments,
            }
            fields = {k: v for k, v in fields.items() if v is not None}
            if not fields:
                return _tool_result(
                    {
                        "error": "At least one field must be provided to update the anime"
                    }
                )
            return _tool_result(
                await authed(
                    lambda access_token: client().patch_authed(
                        f"/anime/{anime_id}/my_list_status",
                        access_token,
                        data=fields,
                    )
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=MUTATING)
    async def update_mymangalist(
        manga_id: Annotated[
            int, Field(description="ID of the manga to update", ge=1)
        ],
        status: Annotated[
            str | None,
            Field(
                description="Manga list status",
                pattern="^(reading|completed|on_hold|dropped|plan_to_read)$",
            ),
        ] = None,
        is_rereading: Annotated[
            bool | None, Field(description="Whether the manga is being reread")
        ] = None,
        score: Annotated[
            int | None,
            Field(description="Score for the manga (0-10)", ge=0, le=10),
        ] = None,
        num_volumes_read: Annotated[
            int | None, Field(description="Number of volumes read", ge=0)
        ] = None,
        num_chapters_read: Annotated[
            int | None, Field(description="Number of chapters read", ge=0)
        ] = None,
        priority: Annotated[
            int | None, Field(description="Priority (0-2)", ge=0, le=2)
        ] = None,
        num_times_reread: Annotated[
            int | None, Field(description="Number of times reread", ge=0)
        ] = None,
        reread_value: Annotated[
            int | None, Field(description="Reread value (0-5)", ge=0, le=5)
        ] = None,
        tags: Annotated[
            str | None, Field(description="Comma-separated tags")
        ] = None,
        comments: Annotated[
            str | None,
            Field(description="Comments about the manga", max_length=1000),
        ] = None,
    ) -> ToolResult:
        """Update a manga's status in the authenticated user's MyAnimeList."""
        try:
            fields = {
                "status": status,
                "is_rereading": is_rereading,
                "score": score,
                "num_volumes_read": num_volumes_read,
                "num_chapters_read": num_chapters_read,
                "priority": priority,
                "num_times_reread": num_times_reread,
                "reread_value": reread_value,
                "tags": tags,
                "comments": comments,
            }
            fields = {k: v for k, v in fields.items() if v is not None}
            if not fields:
                return _tool_result(
                    {
                        "error": "At least one field must be provided to update the manga"
                    }
                )
            return _tool_result(
                await authed(
                    lambda access_token: client().patch_authed(
                        f"/manga/{manga_id}/my_list_status",
                        access_token,
                        data=fields,
                    )
                )
            )
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_upcoming_anime_episodes(hours: int = 36) -> ToolResult:
        """Fetch upcoming episode air times for the authenticated user's 'watching' and 'plan to watch' anime, from AniList.

        Times are the original (Japanese) broadcast, not streaming releases."""
        try:
            anime = [
                *await own_anime_list(AnimeStatus.WATCHING),
                *await own_anime_list(AnimeStatus.PLANNING),
            ]
            schedules = await fetch_upcoming_episodes(airing_ids(anime))
            return _tool_result(
                upcoming_episodes(
                    anime, schedules, hours=normalize_episode_window(hours)
                )
            )
        except AniListError as e:
            return _tool_result(e.payload())
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_watch_queue() -> ToolResult:
        """Build a watch queue digest from the authenticated user's 'watching' anime list and AniList airing schedules.

        Returns per-anime watched episode counts against latest released episodes (from AniList or total episode metadata)
        plus aggregate totals. Latest released is unknown for titles that are neither finished nor scheduled on AniList."""
        try:
            anime = await own_anime_list(AnimeStatus.WATCHING)
            schedules = await fetch_upcoming_episodes(airing_ids(anime))
            return _tool_result(build_watch_queue(anime, schedules))
        except AniListError as e:
            return _tool_result(e.payload())
        except _TOOL_ERRORS as e:
            return _tool_result(api_error_payload(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_mal_news(limit: int = 10) -> ToolResult:
        """Fetch recent anime/manga news articles from MyAnimeList RSS feed."""
        try:
            limit = clamp_limit(limit, _MAL_NEWS_LIMIT)
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(_MAL_NEWS_RSS_URL)
                response.raise_for_status()
            return _tool_result(parse_mal_news_rss(response.text, limit))
        except (*_TOOL_ERRORS, ElementTree.ParseError) as e:
            return _tool_result(api_error_payload(e))
