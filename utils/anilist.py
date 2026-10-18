"""Upcoming episode air times from AniList, looked up by MyAnimeList id.

AniList's public GraphQL API needs no login and is queried by MAL ids, so
it works for private MAL lists. See https://docs.anilist.co/.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from utils.mal_client import safe_error

ANILIST_URL = "https://graphql.anilist.co"
# AniList's largest page size.
_PAGE_SIZE = 50
# Enough for the longest digest window (14 days) of a daily show.
_EPISODES_PER_TITLE = 25
# type: ANIME matters: MAL anime and manga ids overlap.
_QUERY = f"""
query ($ids: [Int]) {{
  Page(perPage: {_PAGE_SIZE}) {{
    media(idMal_in: $ids, type: ANIME) {{
      idMal
      airingSchedule(notYetAired: true, perPage: {_EPISODES_PER_TITLE}) {{
        nodes {{ episode airingAt }}
      }}
    }}
  }}
}}
"""


@dataclass(frozen=True)
class AiringEpisode:
    episode: int
    airs_at: datetime


class AniListError(Exception):
    """AniList did not return the airing schedules."""

    def __init__(self, response: httpx.Response, body: Any) -> None:
        self.status_code = response.status_code
        detail = f"{response.status_code} {response.reason_phrase}"
        errors = body.get("errors") if isinstance(body, dict) else None
        # E.g. a 429 when the rate limit is used up.
        if errors and isinstance(errors[0], dict) and errors[0].get("message"):
            detail = f"{detail}: {errors[0]['message']}"
        super().__init__(f"AniList error: {detail}")

    def payload(self) -> dict:
        return {
            "error": safe_error(str(self)),
            "status_code": self.status_code,
        }


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


async def _query_page(
    client: httpx.AsyncClient, mal_ids: list[int]
) -> list[dict]:
    response = await client.post(
        ANILIST_URL,
        json={"query": _QUERY, "variables": {"ids": mal_ids}},
    )
    body = _json_or_none(response)
    if response.is_error or not isinstance(body, dict) or body.get("errors"):
        raise AniListError(response, body)
    return body["data"]["Page"]["media"]


async def fetch_upcoming_episodes(
    mal_ids: Iterable[int],
) -> dict[int, list[AiringEpisode]]:
    """Return the not yet aired episodes of each title, by MAL id.

    Titles AniList does not know, or without a schedule, are left out.
    """
    ids = sorted(set(mal_ids))
    schedules: dict[int, list[AiringEpisode]] = {}
    if not ids:
        return schedules
    async with httpx.AsyncClient(timeout=30) as client:
        for start in range(0, len(ids), _PAGE_SIZE):
            page = ids[start : start + _PAGE_SIZE]
            for media in await _query_page(client, page):
                episodes = [
                    AiringEpisode(
                        episode=node["episode"],
                        airs_at=datetime.fromtimestamp(
                            node["airingAt"], tz=UTC
                        ),
                    )
                    for node in media["airingSchedule"]["nodes"]
                ]
                if episodes:
                    schedules.setdefault(media["idMal"], []).extend(episodes)
    for episodes in schedules.values():
        episodes.sort(key=lambda e: e.airs_at)
    return schedules
