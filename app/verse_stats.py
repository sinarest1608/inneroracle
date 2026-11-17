"""Aggregate-only counts per daily verse: sent, opened, starred.

The table holds one row per send and nothing that could link a number back to
a device: no device id, IP, user agent or per-action timestamp. Counters move
in the same transaction as the inbox change that causes them, so they can't
drift, and they are never touched when a device (and its inbox) is deleted.
Kept indefinitely: there is no personal data here to expire.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.daily_verse import local_today

COUNTERS = ("sent", "opened", "starred")


def bump(conn: sqlite3.Connection, slot_key: str, verse_id: str, counter: str,
         delta: int = 1, festival: str | None = None) -> None:
    if counter not in COUNTERS:
        raise ValueError(counter)
    conn.execute(
        f"INSERT INTO verse_stats(slot_key, verse_id, festival, {counter}) "
        f"VALUES (?, ?, ?, MAX(0, ?)) ON CONFLICT(slot_key) DO UPDATE SET "
        f"{counter} = MAX(0, {counter} + ?), festival = COALESCE(festival, excluded.festival)",
        (slot_key, verse_id, festival, delta, delta),
    )


def rows(conn: sqlite3.Connection, days: int) -> list[dict]:
    """Newest first, with the quote that was sent (when it is still stored)."""
    since = (local_today() - timedelta(days=days)).isoformat()
    out = []
    for r in conn.execute(
        "SELECT s.slot_key, s.verse_id, s.festival, s.sent, s.opened, s.starred, q.quote "
        "FROM verse_stats s LEFT JOIN daily_quotes q ON q.quote_date = s.slot_key "
        "AND q.verse_id = s.verse_id WHERE s.slot_key >= ? ORDER BY s.slot_key DESC", (since,)
    ):
        day, _, time = r["slot_key"].partition("#")
        out.append({"date": day, "time": time, "verse_id": r["verse_id"],
                    "festival": r["festival"], "quote": r["quote"], "sent": r["sent"],
                    "opened": r["opened"], "starred": r["starred"]})
    return out
