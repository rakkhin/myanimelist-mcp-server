from datetime import UTC, datetime

from utils.anilist import AiringEpisode
from utils.episodes import airing_ids, build_watch_queue, upcoming_episodes


def _ep(episode: int, airs_at: str) -> AiringEpisode:
    return AiringEpisode(
        episode=episode, airs_at=datetime.fromisoformat(airs_at)
    )


def _item(mal_id, title, status, list_status="watching"):
    return {
        "node": {"id": mal_id, "title": title, "status": status},
        "list_status": {"status": list_status},
    }


def test_airing_ids_keep_airing_and_not_yet_aired_titles():
    data = [
        _item(1, "Airing", "currently_airing"),
        _item(2, "Finished", "finished_airing"),
        _item(3, "Upcoming", "not_yet_aired"),
        {"node": {"status": "currently_airing"}},
        {},
    ]

    assert airing_ids(data) == [1, 3]


class TestUpcomingEpisodes:
    _NOW = datetime(2026, 5, 24, tzinfo=UTC)

    def test_lists_episodes_in_window_by_air_time(self):
        data = [
            _item(1, "Show A", "currently_airing"),
            _item(2, "Show B", "not_yet_aired", "plan_to_watch"),
        ]
        schedules = {
            1: [
                _ep(4, "2026-05-25T13:30:00+00:00"),
                _ep(5, "2026-06-01T13:30:00+00:00"),
            ],
            2: [_ep(1, "2026-05-24T12:00:00+00:00")],
        }

        result = upcoming_episodes(data, schedules, hours=72, now=self._NOW)

        assert result == {
            "hours": 72,
            "from": "2026-05-24T00:00:00Z",
            "digest": "Scheduled anime episodes in the next 72 hours:\n"
            "- Show B ep 1 [plan_to_watch] at 2026-05-24T12:00:00Z — MAL 2\n"
            "- Show A ep 4 [watching] at 2026-05-25T13:30:00Z — MAL 1",
        }

    def test_window_includes_both_ends(self):
        data = [_item(1, "Show", "currently_airing")]
        schedules = {
            1: [
                _ep(1, "2026-05-24T00:00:00+00:00"),
                _ep(2, "2026-05-25T00:00:00+00:00"),
                _ep(3, "2026-05-25T00:00:01+00:00"),
            ]
        }

        result = upcoming_episodes(data, schedules, hours=24, now=self._NOW)

        assert "ep 1 " in result["digest"]
        assert "ep 2 " in result["digest"]
        assert "ep 3 " not in result["digest"]

    def test_reports_empty_window(self):
        result = upcoming_episodes([], {}, hours=72, now=self._NOW)

        assert result["digest"] == (
            "No scheduled anime episodes in the next 72 hours."
        )

    def test_missing_list_status_and_title_are_handled(self):
        data = [{"node": {"id": 1}}]
        schedules = {1: [_ep(1, "2026-05-24T01:00:00+00:00")]}

        result = upcoming_episodes(data, schedules, hours=1, now=self._NOW)

        assert result["digest"].endswith(
            "- Unknown ep 1 at 2026-05-24T01:00:00Z — MAL 1"
        )

    def test_defaults_to_current_time(self):
        result = upcoming_episodes([], {}, hours=1)

        started = datetime.fromisoformat(result["from"])
        assert abs((datetime.now(UTC) - started).total_seconds()) < 60


class TestBuildWatchQueue:
    def test_finished_airing_uses_num_episodes(self):
        data = [
            {
                "node": {
                    "id": 1,
                    "title": "Completed Show",
                    "num_episodes": 12,
                    "status": "finished_airing",
                },
                "list_status": {"num_episodes_watched": 5},
            },
        ]
        result = build_watch_queue(data, {})
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        assert entry["latest_released_count"] == 12
        assert entry["watched_count"] == 5
        assert entry["unwatched_count"] == 7
        assert entry["status"] == "finished_airing"

    def test_not_yet_aired_returns_zero(self):
        data = [
            {
                "node": {
                    "id": 2,
                    "title": "Upcoming Show",
                    "num_episodes": 0,
                    "status": "not_yet_aired",
                },
                "list_status": {"num_episodes_watched": 0},
            },
        ]
        result = build_watch_queue(data, {})
        assert result["entries"][0]["latest_released_count"] == 0
        assert result["entries"][0]["unwatched_count"] == 0

    def test_airing_uses_the_earliest_upcoming_episode(self):
        data = [
            {
                "node": {
                    "id": 10,
                    "title": "Airing",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 3},
            },
        ]
        schedules = {
            10: [
                _ep(7, "2026-07-20T00:00:00+00:00"),
                _ep(8, "2026-07-27T00:00:00+00:00"),
            ]
        }
        result = build_watch_queue(data, schedules)
        entry = result["entries"][0]
        assert entry["latest_released_count"] == 6
        assert entry["unwatched_count"] == 3

    def test_airing_with_only_upcoming_events_infers_latest(self):
        data = [
            {
                "node": {
                    "id": 11,
                    "title": "Airing New",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 3},
            },
        ]
        schedules = {11: [_ep(5, "2026-07-27T00:00:00+00:00")]}
        result = build_watch_queue(data, schedules)
        entry = result["entries"][0]
        assert entry["latest_released_count"] == 4
        assert entry["unwatched_count"] == 1

    def test_upcoming_episode_one_means_nothing_released(self):
        data = [
            {
                "node": {
                    "id": 12,
                    "title": "Premiere",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 0},
            },
        ]
        schedules = {12: [_ep(1, "2026-07-27T00:00:00+00:00")]}
        result = build_watch_queue(data, schedules)
        entry = result["entries"][0]
        assert entry["latest_released_count"] == 0
        assert entry["unwatched_count"] == 0

    def test_no_schedule_yields_unknown_latest(self):
        data = [
            {
                "node": {
                    "id": 20,
                    "title": "Missing",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 2},
            },
        ]
        result = build_watch_queue(data, {})
        entry = result["entries"][0]
        assert entry["latest_released_count"] is None
        assert entry["unwatched_count"] is None

    def test_aggregate_counts(self):
        data = [
            {
                "node": {
                    "id": 1,
                    "title": "A",
                    "num_episodes": 12,
                    "status": "finished_airing",
                },
                "list_status": {"num_episodes_watched": 5},
            },
            {
                "node": {
                    "id": 2,
                    "title": "B",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 2},
            },
        ]
        schedules = {2: [_ep(4, "2026-07-27T00:00:00+00:00")]}
        result = build_watch_queue(data, schedules)
        agg = result["aggregate"]
        assert agg["total_watching"] == 2
        assert agg["total_watched_episodes"] == 7
        assert agg["total_unwatched_calculable"] == 8
        assert agg["total_unknown_latest_count"] == 0

    def test_mixed_unknown_and_known_aggregate(self):
        data = [
            {
                "node": {
                    "id": 1,
                    "title": "A",
                    "num_episodes": 10,
                    "status": "finished_airing",
                },
                "list_status": {"num_episodes_watched": 10},
            },
            {
                "node": {
                    "id": 2,
                    "title": "B",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 1},
            },
        ]
        result = build_watch_queue(data, {})
        agg = result["aggregate"]
        assert agg["total_watching"] == 2
        assert agg["total_watched_episodes"] == 11
        assert agg["total_unwatched_calculable"] == 0
        assert agg["total_unknown_latest_count"] == 1

    def test_empty_list(self):
        result = build_watch_queue([], {})
        assert result["entries"] == []
        assert result["aggregate"]["total_watching"] == 0
        assert result["aggregate"]["total_watched_episodes"] == 0
        assert result["aggregate"]["total_unwatched_calculable"] == 0
        assert result["aggregate"]["total_unknown_latest_count"] == 0

    def test_zero_episodes_finished_returns_none(self):
        data = [
            {
                "node": {
                    "id": 99,
                    "title": "Zero Ep",
                    "num_episodes": 0,
                    "status": "finished_airing",
                },
                "list_status": {"num_episodes_watched": 0},
            },
        ]
        result = build_watch_queue(data, {})
        assert result["entries"][0]["latest_released_count"] is None
        assert result["entries"][0]["unwatched_count"] is None

    def test_watched_exceeds_latest_unwatched_floor_zero(self):
        data = [
            {
                "node": {
                    "id": 1,
                    "title": "Overtracked",
                    "num_episodes": 5,
                    "status": "finished_airing",
                },
                "list_status": {"num_episodes_watched": 12},
            },
        ]
        result = build_watch_queue(data, {})
        assert result["entries"][0]["watched_count"] == 12
        assert result["entries"][0]["latest_released_count"] == 5
        assert result["entries"][0]["unwatched_count"] == 0

    def test_sorting_puts_most_unwatched_first(self):
        data = [
            {
                "node": {
                    "id": 1,
                    "title": "B Show",
                    "num_episodes": 24,
                    "status": "finished_airing",
                },
                "list_status": {"num_episodes_watched": 10},
            },
            {
                "node": {
                    "id": 2,
                    "title": "A Show",
                    "num_episodes": 12,
                    "status": "finished_airing",
                },
                "list_status": {"num_episodes_watched": 2},
            },
            {
                "node": {
                    "id": 3,
                    "title": "C Show",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 5},
            },
        ]
        result = build_watch_queue(data, {})
        titles = [e["title"] for e in result["entries"]]
        assert titles[0] == "B Show"
        assert titles[1] == "A Show"
        assert titles[2] == "C Show"

    def test_missing_list_status_fields_default_to_zero(self):
        data = [
            {
                "node": {
                    "id": 1,
                    "title": "Bare",
                    "num_episodes": 12,
                    "status": "finished_airing",
                },
                "list_status": {},
            },
        ]
        result = build_watch_queue(data, {})
        entry = result["entries"][0]
        assert entry["watched_count"] == 0
        assert entry["latest_released_count"] == 12

    def test_missing_node_fields_are_handled(self):
        data = [
            {"node": {"id": 0}, "list_status": {"num_episodes_watched": 3}},
        ]
        result = build_watch_queue(data, {})
        entry = result["entries"][0]
        assert entry["title"] == "Unknown"
        assert entry["mal_id"] == 0
        assert entry["latest_released_count"] is None
