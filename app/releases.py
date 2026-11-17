"""Announcing a release: tester devices, release stages, the "update" push.

Deploying announces nothing. A release in app/resources/releases.json is a
`draft` until the owner sends it from the admin page: first to the tester
devices (`testers`), then to everyone (`all`). A device only ever sees a
release (pop-up and hints) that has reached it, so undeployed-looking code stays
quiet for everyone else. The announcement is a plain push: no inbox entry, no
verse stats.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from app.push_db import audit, db, utcnow, write_tx
from app.push_service import CONCURRENCY, Message, send_to_device
from app.version import latest_release, releases

ANNOUNCE_GAP = timedelta(hours=24)  # between "All devices" announcements
ANNOUNCE_TITLE = "InnerOracle"
ANNOUNCE_BODY = "New update: open InnerOracle to see what's new."


class Refused(Exception):
    def __init__(self, status: int, message: str, needs_confirm: bool = False):
        super().__init__(message)
        self.status, self.message, self.needs_confirm = status, message, needs_confirm


# ---- tester devices ---------------------------------------------------------------

def testers(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT device_id FROM tester_devices ORDER BY device_id")]


def set_testers(conn: sqlite3.Connection, ids: list[int]) -> list[int]:
    """Replace the tester list. Numbers that aren't current devices are refused."""
    ids = sorted(set(ids))
    with write_tx(conn):
        known = {r[0] for r in conn.execute("SELECT id FROM push_subscriptions")}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise Refused(422, "Unknown device number" + ("s" if len(unknown) > 1 else "") + ": "
                          + ", ".join(f"#{i}" for i in unknown))
        conn.execute("DELETE FROM tester_devices")
        conn.executemany("INSERT INTO tester_devices(device_id) VALUES (?)", [(i,) for i in ids])
        audit(conn, "set_testers", "devices=" + ",".join(map(str, ids)))
    return ids


def is_tester(conn: sqlite3.Connection, device_id: int) -> bool:
    return conn.execute("SELECT 1 FROM tester_devices WHERE device_id = ?",
                        (device_id,)).fetchone() is not None


# ---- stages -----------------------------------------------------------------------

def stage(conn: sqlite3.Connection, version: str) -> dict:
    row = conn.execute("SELECT stage, testers_sent_at, all_sent_at FROM release_stages "
                       "WHERE version = ?", (version,)).fetchone()
    return dict(row) if row else {"stage": "draft", "testers_sent_at": None, "all_sent_at": None}


def visible_release(conn: sqlite3.Connection, device_id: int | None) -> dict | None:
    """The newest release that has reached this device (None = nothing to show)."""
    tester = device_id is not None and is_tester(conn, device_id)
    stages = {r["version"]: r["stage"] for r in conn.execute("SELECT version, stage FROM release_stages")}
    for r in releases():
        s = stages.get(r["version"], "draft")
        if s == "all" or (s == "testers" and tester):
            return r
    return None


def next_all_allowed_at(conn: sqlite3.Connection) -> str | None:
    """When the next "All devices" announcement may go out (None = now)."""
    last = conn.execute("SELECT MAX(all_sent_at) FROM release_stages").fetchone()[0]
    if not last:
        return None
    at = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) + ANNOUNCE_GAP
    return at.strftime("%Y-%m-%dT%H:%M:%SZ") if at > datetime.now(UTC) else None


def _reserve(conn: sqlite3.Connection, version: str, audience: str, repeat: bool) -> list[sqlite3.Row]:
    """Check the protections and record the send, atomically, before any push
    goes out — a crash mid-send can then never allow a second "all" send."""
    latest = latest_release()
    if latest is None or latest["version"] != version:
        raise Refused(409, "That isn't the latest release. Reload the page.")
    with write_tx(conn):
        cur, now = stage(conn, version), utcnow()
        if audience == "testers":
            ids = testers(conn)
            if not ids:
                raise Refused(422, "No tester devices are set.")
            if cur["testers_sent_at"] and not repeat:
                raise Refused(409, "This release was already sent to testers.", needs_confirm=True)
            devices = conn.execute(
                "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE id IN "
                "(SELECT device_id FROM tester_devices) ORDER BY id").fetchall()
            new_stage = "all" if cur["stage"] == "all" else "testers"  # never moves backwards
            conn.execute(
                "INSERT INTO release_stages(version, stage, testers_sent_at) VALUES (?, ?, ?) "
                "ON CONFLICT(version) DO UPDATE SET stage = excluded.stage, "
                "testers_sent_at = excluded.testers_sent_at", (version, new_stage, now))
        else:
            if cur["all_sent_at"]:
                raise Refused(409, "This release was already announced to all devices.")
            wait = next_all_allowed_at(conn)
            if wait:
                raise Refused(429, "Another release was announced to all devices less than "
                                   f"24 hours ago. Try again after {wait} (UTC).")
            devices = conn.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions "
                                   "ORDER BY id").fetchall()
            conn.execute(
                "INSERT INTO release_stages(version, stage, all_sent_at) VALUES (?, 'all', ?) "
                "ON CONFLICT(version) DO UPDATE SET stage = 'all', all_sent_at = excluded.all_sent_at",
                (version, now))
        audit(conn, "announce", f"version={version} audience={audience} devices={len(devices)}"
                                + (" repeat=1" if repeat and cur["testers_sent_at"] else ""))
    return devices


async def announce(version: str, audience: str, repeat: bool = False) -> dict:
    with db() as conn:
        devices = _reserve(conn, version, audience, repeat)
    msg = Message("announce", ANNOUNCE_TITLE, ANNOUNCE_BODY, "/ui/", tag="update")
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(row: sqlite3.Row) -> str:
        async with sem:
            return (await send_to_device(row, msg)).status

    results = await asyncio.gather(*(one(r) for r in devices))
    with db() as conn:
        return {"version": version, "audience": audience, "devices": len(devices),
                "delivered": sum(r == "delivered" for r in results), **stage(conn, version)}
