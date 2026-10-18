"""Turn captured tool responses into anonymised test fixtures.

Reads real responses from the git-ignored tests/tool_responses/ and writes
fixtures without personal data to tests/fixtures/mal/. Files are named
NN_<tool>[_<suffix>].json for tool output, and NN_<tool>_<input>.<ext> for
the raw list, AniList and RSS responses the derived tools receive:

    uv run python scripts/anonymize_fixtures.py

Fake values come from a seeded random generator in the order real values
are met, so the output is repeatable but cannot be traced back to the real
values. The same real value always maps to the same fake one. Before
anything is written, every file is checked for real values that survived.
"""

from __future__ import annotations

import copy
import json
import random
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.schemas import AnimeStatus, MangaStatus

SOURCE_DIR = ROOT / "tests" / "tool_responses"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "mal"
SEED = 20261017
# Fixed "now", so generated dates do not depend on when the script runs.
REFERENCE_TIME = datetime(2026, 10, 17, tzinfo=UTC)
TOKEN_PATH = "/home/user/.config/mal_tokens.json"
PLACEHOLDER_POST = "Placeholder post text."
PLACEHOLDER_SIGNATURE = "Placeholder signature."
LOCATIONS = ("Springfield", "Riverside", "Lakeside", "Greenville")
GENDERS = ("male", "female")
_PLANNED = (AnimeStatus.PLANNING, MangaStatus.PLANNING)
_COMPLETED = (AnimeStatus.COMPLETED, MangaStatus.COMPLETED)
SOURCE_SUFFIXES = (".json", ".xml")
# Shorter strings are too likely to occur by chance in public data.
MIN_SECRET_LENGTH = 4

Document = dict[str, Any]


class LeakError(Exception):
    """A real value survived anonymisation."""


def _number(value: float) -> int | float:
    # MAL returns whole numbers of days as integers.
    return int(value) if float(value).is_integer() else value


def _timestamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


class Anonymizer:
    def __init__(self, seed: int = SEED) -> None:
        self.rng = random.Random(seed)
        self.secrets: dict[str, str] = {}
        self._fakes: dict[tuple[str, Any], Any] = {}
        self._used: dict[str, set[Any]] = {}
        self.joined_at: datetime | None = None
        # Watched counts from the anonymised watch-queue list, by anime id.
        self.watched_by_id: dict[int, int] = {}

    def secret(self, value: Any, kind: str) -> None:
        text = str(value)
        if len(text) >= MIN_SECRET_LENGTH:
            self.secrets[text] = kind

    def _fake(self, kind: str, real: Any, make: Callable[[], Any]) -> Any:
        self.secret(real, kind)
        key = (kind, real)
        if key not in self._fakes:
            used = self._used.setdefault(kind, set())
            value = make()
            while value in used:
                value = make()
            used.add(value)
            self._fakes[key] = value
        return self._fakes[key]

    def user_id(self, real: int) -> int:
        return self._fake(
            "user id", real, lambda: self.rng.randint(1_000_000, 9_999_999)
        )

    def user_name(self, real: str) -> str:
        return self._fake(
            "user name", real, lambda: f"user_{self.rng.randint(1000, 9999)}"
        )

    def expires_at(self, real: float) -> float:
        def make() -> float:
            offset = self.rng.uniform(1, 30) * 86_400
            return round(REFERENCE_TIME.timestamp() + offset, 6)

        return self._fake("token expiry", real, make)

    def moment_between(self, start: datetime, end: datetime) -> datetime:
        seconds = self.rng.uniform(0, (end - start).total_seconds())
        return (start + timedelta(seconds=seconds)).replace(microsecond=0)

    def updated_at(self, real: str) -> str:
        self.secret(real, "list update time")
        start = self.joined_at or datetime(2010, 1, 1, tzinfo=UTC)
        return _timestamp(self.moment_between(start, REFERENCE_TIME))

    def anime_statistics(self) -> Document:
        rng = self.rng
        items = {
            "watching": rng.randint(0, 30),
            "completed": rng.randint(20, 600),
            "on_hold": rng.randint(0, 30),
            "dropped": rng.randint(0, 40),
            "plan_to_watch": rng.randint(0, 200),
        }
        raw_days = {
            "watching": rng.uniform(0, 5),
            "completed": items["completed"] * rng.uniform(0.2, 0.6),
            "on_hold": rng.uniform(0, 5),
            "dropped": rng.uniform(0, 5),
        }
        days = {k: _number(round(v, 2)) for k, v in raw_days.items()}
        # num_days_watched is the same total as num_days, not one of its parts.
        total_days = _number(round(sum(days.values()), 2))
        return {
            **{f"num_items_{k}": v for k, v in items.items()},
            "num_items": sum(items.values()),
            "num_days_watched": total_days,
            **{f"num_days_{k}": v for k, v in days.items()},
            "num_days": total_days,
            "num_episodes": round(total_days * 60),
            "num_times_rewatched": rng.randint(0, 20),
            "mean_score": round(rng.uniform(6, 9), 2),
        }

    def _progress(self, status: str, total: int, unknown_max: int) -> int:
        if status in _PLANNED:
            return 0
        if status in _COMPLETED and total:
            return total
        return self.rng.randint(0, total or unknown_max)

    def list_status(
        self,
        status: Document,
        node: Document,
        kind: str,
        keep_status: bool = False,
    ) -> Document:
        rng = self.rng
        if kind == "anime":
            new_status = (
                status["status"]
                if keep_status
                else rng.choice(list(AnimeStatus))
            )
            replacements = {
                "num_episodes_watched": self._progress(
                    new_status, node.get("num_episodes") or 0, 24
                ),
                "is_rewatching": False,
            }
        else:
            new_status = (
                status["status"]
                if keep_status
                else rng.choice(list(MangaStatus))
            )
            replacements = {
                "num_chapters_read": self._progress(
                    new_status, node.get("num_chapters") or 0, 200
                ),
                "num_volumes_read": self._progress(
                    new_status, node.get("num_volumes") or 0, 20
                ),
                "is_rereading": False,
            }
        is_planned = new_status in _PLANNED
        replacements["status"] = new_status
        replacements["score"] = 0 if is_planned else rng.randint(0, 10)
        if "updated_at" in status:
            replacements["updated_at"] = self.updated_at(status["updated_at"])
        # Keep MAL's key set and order; replace only what is present.
        return {k: replacements.get(k, v) for k, v in status.items()}


# ---------------------------------------------------------------- per tool


def _auth_status(doc: Document, a: Anonymizer) -> None:
    a.secret(doc["token_path"], "token path")
    doc["token_path"] = TOKEN_PATH
    doc["expires_at"] = a.expires_at(doc["expires_at"])


def _login_status(doc: Document, a: Anonymizer) -> None:
    if "expires_at" in doc:
        doc["expires_at"] = a.expires_at(doc["expires_at"])


def _user_profile(doc: Document, a: Anonymizer) -> None:
    doc["id"] = a.user_id(doc["id"])
    doc["name"] = a.user_name(doc["name"])
    if "gender" in doc:
        doc["gender"] = a.rng.choice(GENDERS)
    if "location" in doc:
        a.secret(doc["location"], "location")
        doc["location"] = a.rng.choice(LOCATIONS)
    birthday = None
    if "birthday" in doc:
        a.secret(doc["birthday"], "birthday")
        today = REFERENCE_TIME.date()
        oldest = today.replace(year=today.year - 40)
        youngest = today.replace(year=today.year - 18)
        birthday = oldest + timedelta(
            days=a.rng.randint(0, (youngest - oldest).days)
        )
        doc["birthday"] = birthday.isoformat()
    if "joined_at" in doc:
        a.secret(doc["joined_at"], "join date")
        born = datetime.combine(birthday or date(1990, 1, 1), time(), UTC)
        earliest = born + timedelta(days=13 * 365)
        a.joined_at = a.moment_between(earliest, REFERENCE_TIME)
        doc["joined_at"] = _timestamp(a.joined_at)
    if "anime_statistics" in doc:
        doc["anime_statistics"] = a.anime_statistics()


def _user_list(kind: str) -> Callable[[Document, Anonymizer], None]:
    def handle(doc: Document, a: Anonymizer) -> None:
        for item in doc.get("data", []):
            if "list_status" in item:
                item["list_status"] = a.list_status(
                    item["list_status"], item.get("node", {}), kind
                )

    return handle


def _watch_queue(doc: Document, a: Anonymizer) -> None:
    # Same rules as utils.episodes.build_watch_queue.
    for entry in doc["entries"]:
        latest = entry["latest_released_count"]
        watched = a.watched_by_id.get(entry["mal_id"])
        if watched is None:
            watched = a.rng.randint(0, latest if latest is not None else 12)
        entry["watched_count"] = watched
        entry["unwatched_count"] = (
            None if latest is None else max(0, latest - watched)
        )
    entries = doc["entries"]
    entries.sort(
        key=lambda e: (
            0 if e["unwatched_count"] is not None else 1,
            -(e["unwatched_count"] or 0),
            e["title"].lower(),
        )
    )
    known = [
        e["unwatched_count"]
        for e in entries
        if e["unwatched_count"] is not None
    ]
    doc["aggregate"] = {
        "total_watching": len(entries),
        "total_watched_episodes": sum(e["watched_count"] for e in entries),
        "total_unwatched_calculable": sum(known),
        "total_unknown_latest_count": len(entries) - len(known),
    }


def _forum_user(user: Document, a: Anonymizer) -> None:
    user["id"] = a.user_id(user["id"])
    user["name"] = a.user_name(user["name"])
    if "forum_avator" in user:
        a.secret(user["forum_avator"], "avatar URL")
        user["forum_avator"] = ""


def _forum_topics(doc: Document, a: Anonymizer) -> None:
    for topic in doc.get("data", []):
        for key in ("created_by", "last_post_created_by"):
            if topic.get(key):
                _forum_user(topic[key], a)


def _forum_topic(doc: Document, a: Anonymizer) -> None:
    for post in doc.get("data", {}).get("posts", []):
        if post.get("created_by"):
            _forum_user(post["created_by"], a)
        a.secret(post.get("body", ""), "post body")
        post["body"] = PLACEHOLDER_POST
        if post.get("signature"):
            a.secret(post["signature"], "signature")
            post["signature"] = PLACEHOLDER_SIGNATURE


def _status_list(doc: Document, a: Anonymizer) -> Document:
    # The tools request one list status at a time, so the status stays.
    for item in doc.get("data", []):
        if "list_status" in item:
            item["list_status"] = a.list_status(
                item["list_status"], item.get("node", {}), "anime", True
            )
    return doc


def _watch_queue_list(doc: Document, a: Anonymizer) -> Document:
    _status_list(doc, a)
    for item in doc.get("data", []):
        if "list_status" in item:
            a.watched_by_id[item["node"]["id"]] = item["list_status"].get(
                "num_episodes_watched", 0
            )
    return doc


HANDLERS: dict[str, Callable[[Document, Anonymizer], None]] = {
    "mal_auth_status": _auth_status,
    "mal_auth_login_status": _login_status,
    "get_user_profile": _user_profile,
    "get_my_anime_list": _user_list("anime"),
    "get_anime_list": _user_list("anime"),
    "get_my_manga_list": _user_list("manga"),
    "get_manga_list": _user_list("manga"),
    "get_watch_queue": _watch_queue,
    "get_forum_topics": _forum_topics,
    "get_forum_topic": _forum_topic,
}
# Public catalogue, schedule, news and forum board data, copied unchanged.
PASSTHROUGH = {
    "get_upcoming_anime_episodes",
    "get_anime",
    "get_anime_details",
    "get_anime_ranking",
    "get_seasonal_anime",
    "get_suggested_anime",
    "get_manga",
    "get_manga_details",
    "get_manga_ranking",
    "get_forum_boards",
    "get_mal_news",
}
# Raw responses the derived tools receive. These handlers return the
# anonymised content; None copies it unchanged.
RAW_INPUTS: dict[tuple[str, str], Callable[[Any, Anonymizer], Any] | None] = {
    ("get_upcoming_anime_episodes", "watching"): _status_list,
    ("get_upcoming_anime_episodes", "plan_to_watch"): _status_list,
    ("get_upcoming_anime_episodes", "anilist"): None,
    ("get_watch_queue", "animelist"): _watch_queue_list,
    ("get_watch_queue", "anilist"): None,
    ("get_mal_news", "rss"): None,
}
# Tools whose file name suffix is a MAL user name.
USERNAME_SUFFIX = {"get_anime_list", "get_manga_list"}


def split_name(file_name: str) -> tuple[str, str]:
    """Return (tool, suffix) for a file named NN_<tool>[_<suffix>].<ext>."""
    stem = Path(file_name).stem.split("_", 1)[1]
    for tool in sorted(HANDLERS.keys() | PASSTHROUGH, key=len, reverse=True):
        if stem == tool:
            return tool, ""
        if stem.startswith(f"{tool}_"):
            return tool, stem[len(tool) + 1 :]
    raise ValueError(f"No anonymisation rule for {file_name}")


def _replace_in_paging(doc: Any, names: dict[str, str]) -> None:
    paging = doc.get("paging") if isinstance(doc, dict) else None
    for key, url in (paging or {}).items():
        for real, fake in names.items():
            url = url.replace(real, fake)
        paging[key] = url


def _is_raw_input(file_name: str) -> bool:
    return split_name(file_name) in RAW_INPUTS


def anonymize(source: dict[str, Any], a: Anonymizer) -> dict[str, Any]:
    """Map {file name: real response} to {fixture name: anonymised response}.

    Raw inputs go first, so tool output can follow their anonymised values.
    """
    out: dict[str, Any] = {}
    names: dict[str, str] = {}
    order = sorted(source, key=lambda name: (not _is_raw_input(name), name))
    for file_name in order:
        tool, suffix = split_name(file_name)
        extension = Path(file_name).suffix
        doc = copy.deepcopy(source[file_name])
        if (tool, suffix) in RAW_INPUTS:
            handler = RAW_INPUTS[(tool, suffix)]
            if handler is not None:
                doc = handler(doc, a)
        else:
            if tool in USERNAME_SUFFIX and suffix:
                fake_name = a.user_name(suffix)
                names[suffix] = fake_name
                suffix = fake_name
            if tool in HANDLERS:
                HANDLERS[tool](doc, a)
        out[
            f"{tool}_{suffix}{extension}" if suffix else f"{tool}{extension}"
        ] = doc
    for doc in out.values():
        _replace_in_paging(doc, names)
    return out


def check_for_leaks(fixtures: dict[str, Any], a: Anonymizer) -> None:
    for name, doc in fixtures.items():
        body = (
            doc
            if isinstance(doc, str)
            else json.dumps(doc, ensure_ascii=False)
        )
        text = name + body
        for value, kind in a.secrets.items():
            if value in text:
                # Never print the real value itself.
                raise LeakError(f"{name} still contains a real {kind}")


def _read(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


def main(
    source_dir: Path = SOURCE_DIR, fixture_dir: Path = FIXTURE_DIR
) -> list[Path]:
    source = {
        path.name: _read(path)
        for path in sorted(source_dir.iterdir())
        if path.suffix in SOURCE_SUFFIXES
    }
    if not source:
        raise SystemExit(f"No captured responses in {source_dir}")
    anonymizer = Anonymizer()
    fixtures = anonymize(source, anonymizer)
    check_for_leaks(fixtures, anonymizer)

    fixture_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, doc in fixtures.items():
        path = fixture_dir / name
        text = (
            doc
            if isinstance(doc, str)
            else json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        )
        path.write_text(text, encoding="utf-8")
        written.append(path)
    # Fixtures are generated; drop ones that no longer have a source.
    existing = {
        p for p in fixture_dir.iterdir() if p.suffix in SOURCE_SUFFIXES
    }
    for stale in existing - set(written):
        stale.unlink()
    return written


if __name__ == "__main__":
    for path in main():
        print(path.relative_to(ROOT))
