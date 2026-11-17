"""Per-device inbox: every daily verse a device was sent, with opened / starred flags.

This is the only table that ties a verse to a device, so it is kept apart from
the delivery log and dies with the device: `device_id` cascades on delete, which
covers unsubscribe, expired subscriptions, repeated failures and admin deletes.
Unstarred rows are purged after the log retention period; starred rows stay.
"""

from __future__ import annotations

import sqlite3

from app.push_db import utcnow

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
        title: str, quote: str) -> dict:
    """Record a send (once per device and slot; a re-send keeps the first row and
    its flags). Returns the fields the push payload carries. Raises
    sqlite3.IntegrityError if the device no longer exists."""
    conn.execute(
        "INSERT INTO device_inbox(device_id, slot_key, verse_id, title, quote, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(device_id, slot_key) DO NOTHING",
        (device_id, slot_key, verse_id, title, quote, utcnow()),
    )
    entry_id = conn.execute("SELECT id FROM device_inbox WHERE device_id = ? AND slot_key = ?",
                            (device_id, slot_key)).fetchone()[0]
    return {"inbox_id": entry_id, "unread": unread_count(conn, device_id)}


def entries(conn: sqlite3.Connection, device_id: int, flt: str = "all") -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT id, slot_key, verse_id, title, quote, opened_at, starred_at FROM device_inbox "
        f"WHERE device_id = ?{FILTERS[flt]} ORDER BY slot_key DESC, id DESC LIMIT ?",
        (device_id, MAX_ENTRIES),
    ):
        day, _, time = r["slot_key"].partition("#")
        out.append({"id": r["id"], "date": day, "time": time, "verse_id": r["verse_id"],
                    "title": r["title"], "quote": r["quote"],
                    "opened": r["opened_at"] is not None,
                    "starred": r["starred_at"] is not None})
    return out


def _owned(conn: sqlite3.Connection, device_id: int, entry_id: int) -> bool:
    return conn.execute("SELECT 1 FROM device_inbox WHERE id = ? AND device_id = ?",
                        (entry_id, device_id)).fetchone() is not None


def mark_open(conn: sqlite3.Connection, device_id: int, entry_id: int) -> bool | None:
    """True on the first open, False if it was already open, None if the entry
    isn't this device's."""
    cur = conn.execute("UPDATE device_inbox SET opened_at = ? WHERE id = ? AND device_id = ? "
                       "AND opened_at IS NULL", (utcnow(), entry_id, device_id))
    if cur.rowcount:
        return True
    return False if _owned(conn, device_id, entry_id) else None


def mark_all_open(conn: sqlite3.Connection, device_id: int) -> int:
    return conn.execute("UPDATE device_inbox SET opened_at = ? WHERE device_id = ? "
                        "AND opened_at IS NULL", (utcnow(), device_id)).rowcount


def set_star(conn: sqlite3.Connection, device_id: int, entry_id: int, on: bool) -> bool | None:
    """True if the star changed, False if it was already that way, None if the
    entry isn't this device's. Starring never touches opened_at."""
    cur = conn.execute(
        "UPDATE device_inbox SET starred_at = ? WHERE id = ? AND device_id = ? AND starred_at IS "
        + ("NULL" if on else "NOT NULL"), (utcnow() if on else None, entry_id, device_id))
    if cur.rowcount:
        return True
    return False if _owned(conn, device_id, entry_id) else None


def purge(conn: sqlite3.Connection, before: str) -> int:
    return conn.execute("DELETE FROM device_inbox WHERE sent_at < ? AND starred_at IS NULL",
                        (before,)).rowcount
