"""Festival calendar: which festival (if any) a day is.

`app/resources/festivals.json` lists the festivals (name, aliases as spelled in
the feed, theme) and holds no dates: most follow the lunar calendar, so the
dates come from a public holiday feed and are stored in `festival_dates`.
A date that fails a sanity check is stored as suspect and never used, so that
day simply gets the normal rotation.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from app.push_db import utcnow, write_tx

FESTIVALS_PATH = Path(__file__).parent / "resources" / "festivals.json"
WINDOW_DAYS = 396     # "the next 13 months"
MAX_SHIFT_DAYS = 40   # lunar dates move by up to about a month year on year
PICK_DAYS_AHEAD = 3


@dataclass(frozen=True)
class Festival:
    name: str
    theme: str
    names: frozenset[str]  # normalised name + aliases
    notes: str = ""


def _norm(s: str) -> str:
    s = re.sub(r"\s*\(tentative\)\s*$", "", s, flags=re.I)
    return " ".join(s.casefold().split())


@lru_cache(maxsize=1)
def festivals() -> tuple[Festival, ...]:
    """In file order, which is also the priority when two share a day."""
    try:
        data = json.loads(FESTIVALS_PATH.read_text())
    except (OSError, ValueError):
        return ()
    out = []
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and e.get("name") and e.get("theme"):
            names = frozenset(_norm(n) for n in [e["name"], *e.get("aliases", [])])
            out.append(Festival(e["name"], e["theme"], names, e.get("notes", "")))
    return tuple(out)


def by_name(name: str) -> Festival | None:
    return next((f for f in festivals() if f.name == name), None)


def parse_ics(text: str) -> list[tuple[date, str]]:
    """(day, summary) for every all-day or timed event in an iCalendar feed."""
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold continuation lines
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        d = re.search(r"^DTSTART[^:\n]*:(\d{4})(\d{2})(\d{2})", block, re.M)
        s = re.search(r"^SUMMARY[^:\n]*:(.*)$", block, re.M)
        if not d or not s:
            continue
        try:
            day = date(int(d.group(1)), int(d.group(2)), int(d.group(3)))
        except ValueError:
            continue
        events.append((day, s.group(1).strip().replace("\\,", ",")))
    return events


def _same_time_last_year_gap(day: date, prev: date) -> int:
    """How far `day` is from the anniversary of `prev`, in days."""
    try:
        anniversary = prev.replace(year=day.year)
    except ValueError:  # 29 February
        anniversary = prev.replace(year=day.year, day=28)
    return abs((day - anniversary).days)


def sync(conn: sqlite3.Connection, events: list[tuple[date, str]], today: date) -> list[str]:
    """Store this year's and next year's date for every festival. Returns the
    names of festivals with a NEW problem (missing from the feed, twice in a
    year, or moved by more than MAX_SHIFT_DAYS), for one alert."""
    problems: list[str] = []
    horizon = today + timedelta(days=WINDOW_DAYS)
    now = utcnow()
    for f in festivals():
        days = sorted({d for d, s in events if _norm(s) in f.names})
        for year in (today.year, today.year + 1):
            found = [d for d in days if d.year == year]
            prev = [d for d in days if d.year == year - 1]
            row = conn.execute("SELECT day, suspect FROM festival_dates WHERE festival = ? "
                               "AND year = ?", (f.name, year)).fetchone()
            if not found:
                # Only a problem if the festival should fall inside the window.
                expected = len(prev) == 1 and prev[0] + timedelta(days=365) <= horizon
                if year == today.year or expected:
                    day, bad = None, True
                else:
                    continue
            elif len(found) > 1:
                day, bad = None, True
            else:
                day = found[0].isoformat()
                bad = len(prev) == 1 and _same_time_last_year_gap(found[0], prev[0]) > MAX_SHIFT_DAYS
                # A date the owner reported as wrong stays suspect until the feed changes it.
                if row is not None and row["suspect"] and row["day"] == day:
                    bad = True
            if bad and (row is None or not row["suspect"]):
                problems.append(f.name)
            with write_tx(conn):
                conn.execute(
                    "INSERT INTO festival_dates(festival, year, day, suspect, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(festival, year) DO UPDATE SET "
                    "day = excluded.day, suspect = excluded.suspect, fetched_at = excluded.fetched_at",
                    (f.name, year, day, int(bad), now))
    return list(dict.fromkeys(problems))


def on_day(conn: sqlite3.Connection, day: date) -> Festival | None:
    """The festival on `day` with a trusted date; the first in the file if several."""
    names = {r[0] for r in conn.execute(
        "SELECT festival FROM festival_dates WHERE day = ? AND suspect = 0", (day.isoformat(),))}
    return next((f for f in festivals() if f.name in names), None)


def upcoming(conn: sqlite3.Connection, today: date,
             ahead: int = PICK_DAYS_AHEAD) -> list[tuple[date, Festival]]:
    out = []
    for i in range(ahead + 1):
        day = today + timedelta(days=i)
        f = on_day(conn, day)
        if f is not None:
            out.append((day, f))
    return out


def mark_suspect(conn: sqlite3.Connection, festival: str, day: date) -> None:
    conn.execute("UPDATE festival_dates SET suspect = 1 WHERE festival = ? AND year = ?",
                 (festival, day.year))
