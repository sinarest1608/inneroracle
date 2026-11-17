"""Per-device inbox: every daily verse a device was sent, with opened / starred flags.

This is the only table that ties a verse to a device, so it is kept apart from
the delivery log and dies with the device: `device_id` cascades on delete, which
covers unsubscribe, expired subscriptions, repeated failures and admin deletes.
Unstarred rows are purged after the log retention period; starred rows stay.

A first open, a star and an unstar also move the anonymous per-verse totals
(app/verse_stats.py) in the same transaction.
"""

from __future__ import annotations

import sqlite3

from app import verse_stats
from app.push_db import utcnow, write_tx

FILTERS = {
    "all": "",
    "unread": " AND opened_at IS NULL",
    "starred": " AND starred_at IS NOT NULL",
}
MAX_ENTRIES = 500


def unread_count(conn: sqlite3.Connection, device_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM device_inbox WHERE device_id = ? "
                        "AND opened_at IS NULL", (device_id,)).fetchone()[0]


def add(conn: sqlite3.Connection, device_id: int, slot_key: str, verse_id: str,
        title: str, quote: str, festival: str | None = None) -> dict:
    """Record a send (once per device and slot; a re-send keeps the first row and
    its flags). Returns the fields the push payload carries. Raises
    sqlite3.IntegrityError if the device no longer exists."""
    conn.execute(
        "INSERT INTO device_inbox(device_id, slot_key, verse_id, title, quote, festival, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(device_id, slot_key) DO NOTHING",
        (device_id, slot_key, verse_id, title, quote, festival, utcnow()),
    )
    entry_id = conn.execute("SELECT id FROM device_inbox WHERE device_id = ? AND slot_key = ?",
                            (device_id, slot_key)).fetchone()[0]
    return {"inbox_id": entry_id, "unread": unread_count(conn, device_id)}


def entries(conn: sqlite3.Connection, device_id: int, flt: str = "all") -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT id, slot_key, verse_id, title, quote, festival, opened_at, starred_at "
        f"FROM device_inbox WHERE device_id = ?{FILTERS[flt]} "
        "ORDER BY slot_key DESC, id DESC LIMIT ?", (device_id, MAX_ENTRIES),
    ):
        day, _, time = r["slot_key"].partition("#")
        out.append({"id": r["id"], "date": day, "time": time, "verse_id": r["verse_id"],
                    "title": r["title"], "quote": r["quote"], "festival": r["festival"],
                    "opened": r["opened_at"] is not None,
                    "starred": r["starred_at"] is not None})
    return out


def _entry(conn: sqlite3.Connection, device_id: int, entry_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT slot_key, verse_id, festival, opened_at, starred_at FROM device_inbox "
        "WHERE id = ? AND device_id = ?", (entry_id, device_id)).fetchone()


def mark_open(conn: sqlite3.Connection, device_id: int, entry_id: int) -> bool | None:
    """True on the first open (counted once in the verse's totals), False if it
    was already open, None if the entry isn't this device's."""
    with write_tx(conn):
        e = _entry(conn, device_id, entry_id)
        if e is None:
            return None
        if e["opened_at"] is not None:
            return False
        conn.execute("UPDATE device_inbox SET opened_at = ? WHERE id = ?", (utcnow(), entry_id))
        verse_stats.bump(conn, e["slot_key"], e["verse_id"], "opened", 1, e["festival"])
        return True


def mark_all_open(conn: sqlite3.Connection, device_id: int) -> int:
    """Clears the unread list. Not counted as opens: dismissing isn't reading."""
    return conn.execute("UPDATE device_inbox SET opened_at = ? WHERE device_id = ? "
                        "AND opened_at IS NULL", (utcnow(), device_id)).rowcount


def set_star(conn: sqlite3.Connection, device_id: int, entry_id: int, on: bool) -> bool | None:
    """True if the star changed, False if it was already that way, None if the
    entry isn't this device's. Starring never touches opened_at."""
    with write_tx(conn):
        e = _entry(conn, device_id, entry_id)
        if e is None:
            return None
        if (e["starred_at"] is not None) == on:
            return False
        conn.execute("UPDATE device_inbox SET starred_at = ? WHERE id = ?",
                     (utcnow() if on else None, entry_id))
        verse_stats.bump(conn, e["slot_key"], e["verse_id"], "starred", 1 if on else -1,
                         e["festival"])
        return True


def purge(conn: sqlite3.Connection, before: str) -> int:
    return conn.execute("DELETE FROM device_inbox WHERE sent_at < ? AND starred_at IS NULL",
                        (before,)).rowcount
