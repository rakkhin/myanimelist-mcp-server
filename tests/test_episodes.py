from datetime import UTC, datetime

import pytest

from utils.episodes import (
    CalendarEpisodeEvent,
    build_watch_queue,
    parse_anime_calendar_events,
    upcoming_events_digest,
)


def test_parse_anime_calendar_events_extracts_episode_metadata():
    ical = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
DTSTART:20260525T133000Z\r
SUMMARY:[org] Isekai Nonbiri Nouka 2 (8)\r
DESCRIPTION:Link: https://myanimelist.net/anime/62146\r
UID:abc@anime-calendar.com\r
END:VEVENT\r
END:VCALENDAR\r
"""

    events = parse_anime_calendar_events(ical)

    assert len(events) == 1
    assert events[0].title == "Isekai Nonbiri Nouka 2"
    assert events[0].episode == 8
    assert events[0].mal_id == 62146
    assert events[0].stream_type == "org"


def test_upcoming_events_digest_filters_to_window():
    ical = """BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20260525T133000Z\nSUMMARY:[sub] Show A (1)\nDESCRIPTION:Link: https://myanimelist.net/anime/1\nEND:VEVENT\nBEGIN:VEVENT\nDTSTART:20260725T133000Z\nSUMMARY:[sub] Show B (9)\nDESCRIPTION:Link: https://myanimelist.net/anime/2\nEND:VEVENT\nEND:VCALENDAR\n"""

    digest = upcoming_events_digest(
        ical, now_iso="2026-05-24T00:00:00+00:00", hours=72
    )

    assert "Show A ep 1" in digest
    assert "Show B" not in digest


def _cevent(mal_id: int, episode: int, starts_at: str) -> CalendarEpisodeEvent:
    return CalendarEpisodeEvent(
        title="",
        episode=episode,
        starts_at=datetime.fromisoformat(starts_at).replace(tzinfo=UTC),
        mal_id=mal_id,
    )


class TestBuildWatchQueue:
    _NOW = datetime(2026, 7, 20, 0, 0, 0, tzinfo=UTC)

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
        result = build_watch_queue(data, [], now=self._NOW)
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
        result = build_watch_queue(data, [], now=self._NOW)
        assert result["entries"][0]["latest_released_count"] == 0
        assert result["entries"][0]["unwatched_count"] == 0

    def test_airing_with_calendar_past_events_uses_highest_past(self):
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
        events = [
            _cevent(10, 5, "2026-07-13T00:00:00+00:00"),
            _cevent(10, 6, "2026-07-19T00:00:00+00:00"),
            _cevent(10, 7, "2026-07-27T00:00:00+00:00"),
        ]
        result = build_watch_queue(data, events, now=self._NOW)
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
        events = [
            _cevent(11, 5, "2026-07-27T00:00:00+00:00"),
        ]
        result = build_watch_queue(data, events, now=self._NOW)
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
        events = [
            _cevent(12, 1, "2026-07-27T00:00:00+00:00"),
        ]
        result = build_watch_queue(data, events, now=self._NOW)
        entry = result["entries"][0]
        assert entry["latest_released_count"] == 0
        assert entry["unwatched_count"] == 0

    def test_no_calendar_data_yields_unknown_latest(self):
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
        result = build_watch_queue(data, [], now=self._NOW)
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
        events = [
            _cevent(2, 4, "2026-07-27T00:00:00+00:00"),
        ]
        result = build_watch_queue(data, events, now=self._NOW)
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
        result = build_watch_queue(data, [], now=self._NOW)
        agg = result["aggregate"]
        assert agg["total_watching"] == 2
        assert agg["total_watched_episodes"] == 11
        assert agg["total_unwatched_calculable"] == 0
        assert agg["total_unknown_latest_count"] == 1

    def test_empty_list(self):
        result = build_watch_queue([], [], now=self._NOW)
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
        result = build_watch_queue(data, [], now=self._NOW)
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
        result = build_watch_queue(data, [], now=self._NOW)
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
        result = build_watch_queue(data, [], now=self._NOW)
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
        result = build_watch_queue(data, [], now=self._NOW)
        entry = result["entries"][0]
        assert entry["watched_count"] == 0
        assert entry["latest_released_count"] == 12

    def test_missing_node_fields_are_handled(self):
        data = [
            {"node": {"id": 0}, "list_status": {"num_episodes_watched": 3}},
        ]
        result = build_watch_queue(data, [], now=self._NOW)
        entry = result["entries"][0]
        assert entry["title"] == "Unknown"
        assert entry["mal_id"] == 0
        assert entry["latest_released_count"] is None

    def test_calendar_event_without_episode_is_skipped(self):
        data = [
            {
                "node": {
                    "id": 10,
                    "title": "Airing",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 2},
            },
        ]
        events = [
            CalendarEpisodeEvent(
                title="",
                episode=None,
                starts_at=datetime(2026, 7, 21, 0, 0, 0, tzinfo=UTC),
                mal_id=10,
            ),
            CalendarEpisodeEvent(
                title="",
                episode=6,
                starts_at=datetime(2026, 7, 19, 0, 0, 0, tzinfo=UTC),
                mal_id=10,
            ),
        ]
        result = build_watch_queue(data, events, now=self._NOW)
        assert result["entries"][0]["latest_released_count"] == 6

    def test_calendar_event_without_mal_id_not_matched(self):
        data = [
            {
                "node": {
                    "id": 10,
                    "title": "Airing",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 1},
            },
        ]
        events = [
            CalendarEpisodeEvent(
                title="",
                episode=5,
                starts_at=datetime(2026, 7, 19, 0, 0, 0, tzinfo=UTC),
                mal_id=None,
            ),
        ]
        result = build_watch_queue(data, events, now=self._NOW)
        assert result["entries"][0]["latest_released_count"] is None

    def test_past_calendar_event_at_exact_now_is_counted(self):
        data = [
            {
                "node": {
                    "id": 10,
                    "title": "Airing",
                    "num_episodes": 0,
                    "status": "currently_airing",
                },
                "list_status": {"num_episodes_watched": 1},
            },
        ]
        events = [
            CalendarEpisodeEvent(
                title="",
                episode=4,
                starts_at=self._NOW,
                mal_id=10,
            ),
        ]
        result = build_watch_queue(data, events, now=self._NOW)
        assert result["entries"][0]["latest_released_count"] == 4


def _ical(*events: str) -> str:
    body = "".join(f"BEGIN:VEVENT\n{event}\nEND:VEVENT\n" for event in events)
    return f"BEGIN:VCALENDAR\n{body}END:VCALENDAR\n"


def test_parse_events_treats_floating_dtstart_as_utc():
    events = parse_anime_calendar_events(
        _ical("DTSTART:20260525T133000\nSUMMARY:Show (1)")
    )

    assert events[0].starts_at == datetime(2026, 5, 25, 13, 30, tzinfo=UTC)


def test_parse_events_rejects_unsupported_dtstart():
    with pytest.raises(ValueError, match="Unsupported iCal DTSTART"):
        parse_anime_calendar_events(
            _ical("DTSTART:2026-05-25 13:30\nSUMMARY:Show (1)")
        )


def test_parse_events_unfolds_continuation_lines():
    events = parse_anime_calendar_events(
        _ical(
            "DTSTART:20260525T133000Z\n"
            "SUMMARY:Show (1)\n"
            "DESCRIPTION:Link: https://myanimelist.net/an\n"
            " ime/62146"
        )
    )

    assert events[0].mal_id == 62146


def test_parse_events_skips_events_without_start_or_summary():
    events = parse_anime_calendar_events(
        _ical(
            "SUMMARY:No start (1)",
            "DTSTART:20260525T133000Z",
            "DTSTART:20260526T133000Z\nSUMMARY:Kept (2)",
        )
    )

    assert [event.title for event in events] == ["Kept"]


def test_upcoming_events_digest_treats_naive_now_as_utc():
    ical = _ical("DTSTART:20260525T133000Z\nSUMMARY:Show A (1)")

    naive = upcoming_events_digest(
        ical, now_iso="2026-05-24T00:00:00", hours=72
    )
    aware = upcoming_events_digest(
        ical, now_iso="2026-05-24T00:00:00+00:00", hours=72
    )

    assert naive == aware
    assert "Show A ep 1" in naive


def test_upcoming_events_digest_reports_empty_window():
    digest = upcoming_events_digest(
        _ical(), now_iso="2026-05-24T00:00:00+00:00", hours=72
    )

    assert digest == "No scheduled anime episodes in the next 72 hours."


def test_build_watch_queue_treats_naive_now_as_utc():
    data = [
        {
            "node": {
                "id": 7,
                "title": "Airing Show",
                "num_episodes": 12,
                "status": "currently_airing",
            },
            "list_status": {"num_episodes_watched": 1},
        },
    ]
    events = [
        _cevent(7, 3, "2026-07-19T12:00:00"),
        _cevent(7, 4, "2026-07-21T12:00:00"),
    ]

    naive = build_watch_queue(data, events, now=datetime(2026, 7, 20))
    aware = build_watch_queue(
        data, events, now=datetime(2026, 7, 20, tzinfo=UTC)
    )

    assert naive == aware
    assert naive["entries"][0]["latest_released_count"] == 3


@pytest.mark.parametrize(
    ("summary", "title", "episode"),
    [
        (r"[sub] Show A\nPart 2 (3)", "Show A Part 2", 3),
        (r"[sub] Show A\NPart 2 (3)", "Show A Part 2", 3),
        (r"[sub] Show\, Part\; Two (2)", "Show, Part; Two", 2),
        (r"[sub] C:\\new (1)", r"C:\new", 1),
    ],
)
def test_parse_events_decodes_summary_escapes(summary, title, episode):
    events = parse_anime_calendar_events(
        _ical(f"DTSTART:20260525T133000Z\nSUMMARY:{summary}")
    )

    assert [(e.title, e.episode, e.stream_type) for e in events] == [
        (title, episode, "sub")
    ]
