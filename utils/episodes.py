from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from utils.anilist import AiringEpisode

# MAL title statuses that can still have episodes to come.
_AIRING_STATUSES = ("currently_airing", "not_yet_aired")


@dataclass(frozen=True)
class WatchQueueEntry:
    title: str
    mal_id: int
    watched_count: int
    latest_released_count: int | None
    unwatched_count: int | None
    status: str


def _utc_iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def airing_ids(anime_list_data: list[dict]) -> list[int]:
    """MAL ids of the list's titles that are airing or not yet aired."""
    return [
        node["id"]
        for node in (item.get("node", {}) for item in anime_list_data)
        if node.get("id") and node.get("status") in _AIRING_STATUSES
    ]


def upcoming_episodes(
    anime_list_data: list[dict],
    schedules: dict[int, list[AiringEpisode]],
    *,
    hours: int,
    now: datetime | None = None,
) -> dict:
    """Episodes of the list's titles that air in the next `hours`."""
    if now is None:
        now = datetime.now(UTC)
    end = now + timedelta(hours=hours)
    rows = []
    for item in anime_list_data:
        node = item.get("node", {})
        list_status = item.get("list_status", {}).get("status")
        for episode in schedules.get(node.get("id"), []):
            if now <= episode.airs_at <= end:
                rows.append((episode, node, list_status))
    rows.sort(key=lambda row: (row[0].airs_at, row[1].get("title", "")))

    if rows:
        lines = [f"Scheduled anime episodes in the next {hours} hours:"]
        for episode, node, list_status in rows:
            on_list = f" [{list_status}]" if list_status else ""
            lines.append(
                f"- {node.get('title', 'Unknown')} ep {episode.episode}"
                f"{on_list} at {_utc_iso(episode.airs_at)}"
                f" — MAL {node.get('id')}"
            )
        digest = "\n".join(lines)
    else:
        digest = f"No scheduled anime episodes in the next {hours} hours."
    return {"hours": hours, "from": _utc_iso(now), "digest": digest}


def _resolve_latest_episode(
    node: dict, schedules: dict[int, list[AiringEpisode]]
) -> int | None:
    status = node.get("status", "") or ""
    num_episodes = node.get("num_episodes", 0) or 0

    if status == "finished_airing" and num_episodes > 0:
        return num_episodes

    if status == "not_yet_aired":
        return 0

    # The schedule holds only episodes that have not aired yet.
    upcoming = schedules.get(node.get("id"))
    if upcoming:
        return max(0, min(e.episode for e in upcoming) - 1)

    return None


def build_watch_queue(
    anime_list_data: list[dict],
    schedules: dict[int, list[AiringEpisode]],
) -> dict:
    entries: list[WatchQueueEntry] = []
    total_watched = 0
    total_unwatched_calculable = 0
    unknown_latest_count = 0

    for item in anime_list_data:
        node = item.get("node", {})
        list_status = item.get("list_status", {})

        mal_id = node.get("id", 0) or 0
        title = node.get("title", "Unknown") or "Unknown"
        status = node.get("status", "") or ""
        watched = list_status.get("num_episodes_watched", 0) or 0
        total_watched += watched

        latest = _resolve_latest_episode(node, schedules)

        if latest is None:
            unwatched = None
            unknown_latest_count += 1
        else:
            unwatched = max(0, latest - watched)
            total_unwatched_calculable += unwatched

        entries.append(
            WatchQueueEntry(
                title=title,
                mal_id=mal_id,
                watched_count=watched,
                latest_released_count=latest,
                unwatched_count=unwatched,
                status=status,
            )
        )

    entries.sort(
        key=lambda e: (
            0 if e.unwatched_count is not None else 1,
            -(e.unwatched_count or 0),
            e.title.lower(),
        )
    )

    return {
        "entries": [
            {
                "title": entry.title,
                "mal_id": entry.mal_id,
                "watched_count": entry.watched_count,
                "latest_released_count": entry.latest_released_count,
                "unwatched_count": entry.unwatched_count,
                "status": entry.status,
            }
            for entry in entries
        ],
        "aggregate": {
            "total_watching": len(entries),
            "total_watched_episodes": total_watched,
            "total_unwatched_calculable": total_unwatched_calculable,
            "total_unknown_latest_count": unknown_latest_count,
        },
    }
