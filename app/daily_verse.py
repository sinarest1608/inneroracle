"""Verse of the day: a fixed rotation over every verse, plus notification text.

The rotation is shuffled once (constant seed) and stored in push.db, so today's
verse is a pure function of the date: the scheduler, /verse-of-the-day and the
widget always agree, and no verse repeats until all have been shown.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import Verse
from app.prompts import BOOK_NAME
from app.push_db import utcnow, write_tx
from app.store import VerseStore

SPEAKER_MAP_PATH = Path(__file__).parent / "resources" / "speaker_map.json"
FALLBACK_MAX = 140


def local_today() -> date:
    return datetime.now(ZoneInfo(settings.push_timezone)).date()


def sync_rotation(conn: sqlite3.Connection, store: VerseStore, seed: str) -> int:
    """Create the rotation on first run; append verses that are new since (each
    batch shuffled with the same seed). Never deletes, so positions stay stable.
    Returns the number of verses appended."""
    known = {r[0] for r in conn.execute("SELECT verse_id FROM verse_rotation")}
    new = sorted(v.verse_id for v in store.iter_all()
                 if v.translation.strip() and v.verse_id not in known)
    if not new:
        return 0
    random.Random(seed).shuffle(new)
    with write_tx(conn):
        start = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM verse_rotation"
        ).fetchone()[0]
        now = utcnow()
        conn.executemany(
            "INSERT OR IGNORE INTO verse_rotation(position, verse_id, added_at) VALUES (?, ?, ?)",
            [(start + i, vid, now) for i, vid in enumerate(new)],
        )
    return len(new)


def verse_for(conn: sqlite3.Connection, store: VerseStore, day: date) -> Verse | None:
    """rotation[(day − start) mod N], stepping forward past verses that no longer
    exist or lack a translation."""
    ids = [r[0] for r in conn.execute("SELECT verse_id FROM verse_rotation ORDER BY position")]
    if not ids:
        return None
    start = date.fromisoformat(settings.push_rotation_start)
    i = (day - start).days % len(ids)
    for step in range(len(ids)):
        v = store.get(ids[(i + step) % len(ids)])
        if v is not None and v.translation.strip():
            return v
    return None


# Titles name a speaker only for confirmed Bhagavad Gita verses. The Ashtavakra
# Gita has no Sanskrit in our data to check speakers against, so it is always
# neutral ("The Ashtavakra Gita says"), as is any Bhagavad Gita verse in doubt.
NAMED_SPEAKER_BOOKS = ("BG-",)


@lru_cache(maxsize=1)
def speaker_map() -> dict[str, str]:
    """verse_id → speaker, for hand-checked (`confirmed`) Bhagavad Gita entries only."""
    try:
        data = json.loads(SPEAKER_MAP_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return {vid: e["speaker"] for vid, e in data.items()
            if vid.startswith(NAMED_SPEAKER_BOOKS) and isinstance(e, dict)
            and e.get("status") == "confirmed" and e.get("speaker")}


def trim_translation(text: str, limit: int = FALLBACK_MAX) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


def notification_text(verse: Verse, quote: dict) -> tuple[str, str]:
    """(title, body) for a stored/fallback quote dict."""
    book = BOOK_NAME[verse.book]
    if quote["quote_source"] == "fallback":
        return f"{book} {verse.chapter}.{verse.verse_no}", quote["quote"]
    title = (f"{quote['speaker']} in the {book} says" if quote.get("speaker")
             else f"The {book} says")
    return title, f"{quote['quote']} Tap for the exact verse."


def fallback_quote(verse: Verse) -> dict:
    speaker = speaker_map().get(verse.verse_id)
    return {
        "verse_id": verse.verse_id,
        "speaker": speaker,
        "speaker_source": "map" if speaker else "neutral",
        "quote": trim_translation(verse.translation),
        "quote_source": "fallback",
        "model": None,
    }


def stored_quote(conn: sqlite3.Connection, day: date) -> dict | None:
    row = conn.execute(
        "SELECT verse_id, speaker, speaker_source, quote, quote_source, model "
        "FROM daily_quotes WHERE quote_date = ?", (day.isoformat(),)
    ).fetchone()
    return dict(row) if row else None


def verse_url(verse_id: str) -> str:
    from urllib.parse import quote as urlquote
    return f"/ui/?verse={urlquote(verse_id, safe='')}"


def verse_of_the_day(conn: sqlite3.Connection, store: VerseStore,
                     day: date | None = None) -> dict | None:
    """Read-only: today's stored quote, or the plain-verse fallback. Never calls
    the model and never writes."""
    day = day or local_today()
    verse = verse_for(conn, store, day)
    if verse is None:
        return None
    q = stored_quote(conn, day)
    if q is None or q["verse_id"] != verse.verse_id:
        q = fallback_quote(verse)
    title, _ = notification_text(verse, q)
    return {
        "verse_id": verse.verse_id, "book": verse.book, "chapter": verse.chapter,
        "verse_no": verse.verse_no, "speaker": q["speaker"],
        "speaker_source": q["speaker_source"], "translation": verse.translation,
        "sanskrit": verse.sanskrit, "quote": q["quote"],
        "quote_source": q["quote_source"], "title": title,
        "url": verse_url(verse.verse_id),
    }
