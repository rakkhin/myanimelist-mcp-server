import json
import math
from datetime import UTC, date, datetime, timedelta

import pytest

from scripts.anonymize_fixtures import (
    PLACEHOLDER_POST,
    PLACEHOLDER_SIGNATURE,
    REFERENCE_TIME,
    TOKEN_PATH,
    Anonymizer,
    LeakError,
    anonymize,
    check_for_leaks,
    main,
    split_name,
)
from utils.schemas import AnimeStatus, MangaStatus

SEEDS = range(50)


def _profile():
    return {
        "id": 1234567,
        "name": "RealPerson",
        "gender": "female",
        "birthday": "1991-02-03",
        "location": "Real City",
        "joined_at": "2012-05-06T07:08:09+00:00",
        "anime_statistics": {"num_items": 1},
    }


@pytest.mark.parametrize("seed", SEEDS)
def test_anime_statistics_totals_are_sums_of_their_parts(seed):
    stats = Anonymizer(seed).anime_statistics()

    statuses = ("watching", "completed", "on_hold", "dropped", "plan_to_watch")
    assert stats["num_items"] == sum(stats[f"num_items_{s}"] for s in statuses)
    day_parts = ("watching", "completed", "on_hold", "dropped")
    assert math.isclose(
        stats["num_days"],
        sum(stats[f"num_days_{s}"] for s in day_parts),
        abs_tol=1e-9,
    )
    assert stats["num_days_watched"] == stats["num_days"]


def test_same_real_value_gets_same_fake_and_different_values_differ():
    a = Anonymizer()

    assert a.user_name("alice") == a.user_name("alice")
    assert a.user_name("alice") != a.user_name("bob")
    assert a.user_id(42_000_000) == a.user_id(42_000_000)


def test_profile_fields_are_replaced_with_consistent_dates():
    a = Anonymizer()
    doc = _profile()

    anonymize({"03_get_user_profile.json": doc}, a)
    out = anonymize({"03_get_user_profile.json": _profile()}, Anonymizer())[
        "get_user_profile.json"
    ]

    birthday = date.fromisoformat(out["birthday"])
    joined = datetime.fromisoformat(out["joined_at"])
    age_years = (REFERENCE_TIME.date() - birthday).days / 365.25
    assert 18 <= age_years <= 40
    assert joined >= datetime.combine(birthday, datetime.min.time(), UTC) + (
        timedelta(days=13 * 365)
    )
    assert joined <= REFERENCE_TIME
    assert out["id"] != 1234567
    assert out["name"] != "RealPerson"
    assert out["location"] != "Real City"
    assert set(out) == set(_profile())


@pytest.mark.parametrize("seed", SEEDS)
def test_anime_list_status_stays_within_the_title(seed):
    status = {
        "status": "watching",
        "score": 9,
        "num_episodes_watched": 3,
        "is_rewatching": True,
        "updated_at": "2020-01-01T00:00:00+00:00",
    }

    out = Anonymizer(seed).list_status(status, {"num_episodes": 12}, "anime")

    assert list(out) == list(status)
    assert out["status"] in list(AnimeStatus)
    assert 0 <= out["num_episodes_watched"] <= 12
    if out["status"] == AnimeStatus.COMPLETED:
        assert out["num_episodes_watched"] == 12
    if out["status"] == AnimeStatus.PLANNING:
        assert out["num_episodes_watched"] == out["score"] == 0
    assert out["updated_at"] != status["updated_at"]


@pytest.mark.parametrize("seed", SEEDS)
def test_manga_list_status_stays_within_the_title(seed):
    status = {
        "status": "reading",
        "is_rereading": True,
        "num_volumes_read": 1,
        "num_chapters_read": 5,
        "score": 7,
    }

    out = Anonymizer(seed).list_status(
        status, {"num_chapters": 80, "num_volumes": 0}, "manga"
    )

    assert list(out) == list(status)
    assert out["status"] in list(MangaStatus)
    assert 0 <= out["num_chapters_read"] <= 80
    assert 0 <= out["num_volumes_read"] <= 20


def test_watch_queue_totals_and_order_follow_the_entries():
    doc = {
        "entries": [
            {
                "title": "B",
                "mal_id": 2,
                "watched_count": 0,
                "latest_released_count": None,
                "unwatched_count": None,
            },
            {
                "title": "A",
                "mal_id": 1,
                "watched_count": 0,
                "latest_released_count": 10,
                "unwatched_count": 10,
            },
            {
                "title": "C",
                "mal_id": 3,
                "watched_count": 0,
                "latest_released_count": 4,
                "unwatched_count": 4,
            },
        ],
        "aggregate": {},
    }

    out = anonymize({"12_get_watch_queue.json": doc}, Anonymizer())[
        "get_watch_queue.json"
    ]

    entries = out["entries"]
    known = [e["unwatched_count"] for e in entries[:2]]
    assert entries[-1]["title"] == "B"
    assert known == sorted(known, reverse=True)
    for e in entries:
        if e["latest_released_count"] is not None:
            assert e["unwatched_count"] == (
                e["latest_released_count"] - e["watched_count"]
            )
    assert out["aggregate"] == {
        "total_watching": 3,
        "total_watched_episodes": sum(e["watched_count"] for e in entries),
        "total_unwatched_calculable": sum(known),
        "total_unknown_latest_count": 1,
    }


def test_forum_users_and_post_content_are_replaced():
    user = {"id": 7654321, "name": "Poster", "forum_avator": "https://a/x.jpg"}
    topics = {
        "data": [{"created_by": dict(user), "last_post_created_by": None}]
    }
    topic = {
        "data": {
            "posts": [
                {
                    "created_by": dict(user),
                    "body": "Real text",
                    "signature": "Sig",
                },
                {"created_by": dict(user), "body": "More", "signature": ""},
            ]
        }
    }

    out = anonymize(
        {
            "19_get_forum_topics.json": topics,
            "20_get_forum_topic_9.json": topic,
        },
        Anonymizer(),
    )

    creator = out["get_forum_topics.json"]["data"][0]["created_by"]
    posts = out["get_forum_topic_9.json"]["data"]["posts"]
    assert creator["name"] == posts[0]["created_by"]["name"] != "Poster"
    assert creator["forum_avator"] == ""
    assert [p["body"] for p in posts] == [PLACEHOLDER_POST] * 2
    assert [p["signature"] for p in posts] == [PLACEHOLDER_SIGNATURE, ""]


def test_auth_files_share_one_fake_expiry_and_hide_the_token_path():
    a = Anonymizer()

    out = anonymize(
        {
            "01_mal_auth_status.json": {
                "token_path": "/home/real/.config/tokens.json",
                "expires_at": 1_800_000_000.5,
            },
            "02_mal_auth_login_status.json": {"expires_at": 1_800_000_000.5},
        },
        a,
    )

    status = out["mal_auth_status.json"]
    assert status["token_path"] == TOKEN_PATH
    assert (
        status["expires_at"] == out["mal_auth_login_status.json"]["expires_at"]
    )
    assert status["expires_at"] != 1_800_000_000.5


def test_user_name_suffix_is_replaced_in_file_name_and_paging():
    doc = {
        "data": [],
        "paging": {"next": "https://api/users/RealUser/animelist?offset=3"},
    }

    out = anonymize({"10_get_anime_list_RealUser.json": doc}, Anonymizer())

    [(name, fixture)] = out.items()
    fake = name.removeprefix("get_anime_list_").removesuffix(".json")
    assert fake != "RealUser"
    assert (
        fixture["paging"]["next"]
        == f"https://api/users/{fake}/animelist?offset=3"
    )


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("04_get_anime.json", ("get_anime", "")),
        ("05_get_anime_details_39534.json", ("get_anime_details", "39534")),
        ("10_get_anime_list_Someone.json", ("get_anime_list", "Someone")),
        ("08_get_my_anime_list.json", ("get_my_anime_list", "")),
    ],
)
def test_split_name_finds_the_tool(file_name, expected):
    assert split_name(file_name) == expected


def test_split_name_rejects_unknown_tools():
    with pytest.raises(ValueError, match="No anonymisation rule"):
        split_name("99_get_something_new.json")


def test_leak_check_fails_without_revealing_the_value():
    a = Anonymizer()
    a.secret("Real Secret Name", "user name")

    with pytest.raises(LeakError) as error:
        check_for_leaks({"x.json": {"title": "Real Secret Name"}}, a)

    assert "Real Secret Name" not in str(error.value)
    assert "user name" in str(error.value)


def test_short_values_are_not_treated_as_secrets():
    a = Anonymizer()
    a.secret("abc", "location")

    check_for_leaks({"x.json": {"title": "abc"}}, a)


def test_main_writes_fixtures_and_removes_stale_ones(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "fixtures"
    source.mkdir()
    target.mkdir()
    (target / "stale.json").write_text("{}")
    (source / "03_get_user_profile.json").write_text(json.dumps(_profile()))
    (source / "18_get_forum_boards.json").write_text('{"categories": []}')

    written = main(source, target)

    assert sorted(p.name for p in written) == [
        "get_forum_boards.json",
        "get_user_profile.json",
    ]
    assert sorted(p.name for p in target.iterdir()) == [
        "get_forum_boards.json",
        "get_user_profile.json",
    ]
    text = (target / "get_user_profile.json").read_text()
    assert "RealPerson" not in text
    assert text.endswith("}\n")


def test_main_refuses_an_empty_source(tmp_path):
    with pytest.raises(SystemExit, match="No captured responses"):
        main(tmp_path, tmp_path / "fixtures")


def test_fake_values_are_redrawn_when_they_collide():
    class RepeatingRandom:
        def __init__(self, values):
            self.values = iter(values)

        def randint(self, low, high):
            return next(self.values)

    a = Anonymizer()
    a.rng = RepeatingRandom([5000, 5000, 6000])

    assert (a.user_name("alice"), a.user_name("bob")) == (
        "user_5000",
        "user_6000",
    )


def test_user_list_items_get_fake_list_status():
    status = {"status": "watching", "updated_at": "2020-01-01T00:00:00+00:00"}
    doc = {
        "data": [
            {
                "node": {"id": 1, "num_episodes": 12},
                "list_status": dict(status),
            },
            {"node": {"id": 2}},
        ]
    }

    out = anonymize({"08_get_my_anime_list.json": doc}, Anonymizer())

    first, second = out["get_my_anime_list.json"]["data"]
    assert first["list_status"]["updated_at"] != status["updated_at"]
    assert second == {"node": {"id": 2}}


def _status_list(status):
    return {
        "data": [
            {
                "node": {"id": 7, "title": "Show", "num_episodes": 12},
                "list_status": {
                    "status": status,
                    "num_episodes_watched": 1,
                    "updated_at": "2020-01-01T00:00:00+00:00",
                },
            }
        ]
    }


def test_upcoming_episode_lists_keep_their_status_and_schedules_are_copied():
    output = {"hours": 36, "from": "2026-10-18T12:00:00Z", "digest": "-"}
    schedules = [{"data": {"Page": {"media": []}}}]

    out = anonymize(
        {
            "11_get_upcoming_anime_episodes.json": output,
            "11_get_upcoming_anime_episodes_anilist.json": schedules,
            "11_get_upcoming_anime_episodes_watching.json": _status_list(
                "watching"
            ),
            "11_get_upcoming_anime_episodes_plan_to_watch.json": (
                _status_list("plan_to_watch")
            ),
        },
        Anonymizer(),
    )

    assert out["get_upcoming_anime_episodes.json"] == output
    assert out["get_upcoming_anime_episodes_anilist.json"] == schedules
    for status in ("watching", "plan_to_watch"):
        name = f"get_upcoming_anime_episodes_{status}.json"
        list_status = out[name]["data"][0]["list_status"]
        assert list_status["status"] == status
        assert list_status["updated_at"] != "2020-01-01T00:00:00+00:00"


def test_watch_queue_output_follows_the_anonymised_list():
    real_list = _status_list("watching")
    output = {
        "entries": [
            {
                "title": "Show",
                "mal_id": 7,
                "watched_count": 1,
                "latest_released_count": 12,
                "unwatched_count": 11,
            }
        ],
        "aggregate": {},
    }

    # The output file sorts first, but the list must be processed first.
    out = anonymize(
        {
            "12_get_watch_queue.json": output,
            "12_get_watch_queue_animelist.json": real_list,
        },
        Anonymizer(),
    )

    status = out["get_watch_queue_animelist.json"]["data"][0]["list_status"]
    entry = out["get_watch_queue.json"]["entries"][0]
    assert status["status"] == "watching"
    assert entry["watched_count"] == status["num_episodes_watched"]
    assert entry["unwatched_count"] == 12 - status["num_episodes_watched"]


@pytest.mark.parametrize("seed", SEEDS)
def test_kept_manga_status_is_not_redrawn(seed):
    status = {"status": "reading", "num_chapters_read": 5}

    out = Anonymizer(seed).list_status(status, {}, "manga", keep_status=True)

    assert out["status"] == "reading"


def test_rss_is_copied_unchanged_and_keeps_its_extension():
    rss = "<rss><channel><item><title>News</title></item></channel></rss>\n"

    out = anonymize({"21_get_mal_news_rss.xml": rss}, Anonymizer())

    assert out == {"get_mal_news_rss.xml": rss}


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        (
            "11_get_upcoming_anime_episodes_plan_to_watch.json",
            ("get_upcoming_anime_episodes", "plan_to_watch"),
        ),
        ("21_get_mal_news_rss.xml", ("get_mal_news", "rss")),
    ],
)
def test_split_name_handles_raw_input_extensions(file_name, expected):
    assert split_name(file_name) == expected


def test_leak_check_covers_text_files():
    a = Anonymizer()
    a.secret("real-user-name", "user name")

    with pytest.raises(LeakError, match="user name"):
        check_for_leaks({"x.xml": "<name>real-user-name</name>\n"}, a)


def test_main_reads_and_writes_text_inputs(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "fixtures"
    source.mkdir()
    target.mkdir()
    (target / "stale.xml").write_text("old")
    (target / "notes.txt").write_text("not a fixture")
    (source / "21_get_mal_news_rss.xml").write_text("<rss/>\n")

    main(source, target)

    assert sorted(p.name for p in target.iterdir()) == [
        "get_mal_news_rss.xml",
        "notes.txt",
    ]
    assert (target / "get_mal_news_rss.xml").read_text() == "<rss/>\n"
