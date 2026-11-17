"""Test helpers for a local run on the owner's Mac. Mounted ONLY when
LOCAL_DEV=1 (and push is on); startup refuses LOCAL_DEV next to a production
database or a tunnel token (app/push_config.py). Never active in production.

They act on the local push database only: send a verse push now, pretend today
is a festival, and run the festival jobs by hand (they are off by default in a
local run).
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from app import festivals
from app.daily_verse import (
    fallback_quote, festival_pick, local_now, local_today, slots_for,
)
from app.festival_pick import prepare
from app.push_db import db, utcnow
from app.push_service import CONCURRENCY, Message, send_to_device, verse_message
from app.version import latest_release

router = APIRouter(prefix="/dev")


def _sched(request: Request):
    sched = getattr(request.app.state, "push_scheduler", None)
    if sched is None:
        raise HTTPException(503, "scheduler not running")
    return sched


async def _send_all(msg: Message) -> dict:
    """To every local device, as a fresh inbox entry each time."""
    key = f"{local_today().isoformat()}#{local_now().strftime('%H:%M')}-dev{datetime.now().strftime('%S%f')[:5]}"
    with db() as conn:
        devices = conn.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions "
                               "ORDER BY id").fetchall()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(row: sqlite3.Row) -> str:
        async with sem:
            return (await send_to_device(row, msg, key)).status

    results = await asyncio.gather(*(one(r) for r in devices))
    return {"devices": len(devices), "delivered": sum(r == "delivered" for r in results)}


@router.post("/test-push")
async def test_push(request: Request) -> dict:
    """A random verse, as a real daily-verse push (inbox entry, badge count)."""
    sched = _sched(request)
    verses = [v for v in sched.store.iter_all() if v.translation.strip()]
    if not verses:
        raise HTTPException(503, "no verses")
    verse = random.choice(verses)
    q = await sched.writer.write(verse)
    return {"verse_id": verse.verse_id, **await _send_all(verse_message("test", verse, q))}


@router.post("/festival-today")
async def festival_today(request: Request, name: str = "Diwali") -> dict:
    """Pretend today is `name`: date it today, pick its verse (mocked under
    MOCK_MODEL=1), and send the festival push now."""
    sched = _sched(request)
    f = festivals.by_name(name)
    if f is None:
        raise HTTPException(404, "unknown festival; names: "
                            + ", ".join(x.name for x in festivals.festivals()))
    today = local_today()
    with db() as conn:
        conn.execute("DELETE FROM festival_dates WHERE day = ?", (today.isoformat(),))
        conn.execute(
            "INSERT INTO festival_dates(festival, year, day, suspect, fetched_at) VALUES (?, ?, ?, 0, ?) "
            "ON CONFLICT(festival, year) DO UPDATE SET day = excluded.day, suspect = 0",
            (f.name, today.year, today.isoformat(), utcnow()))
        conn.execute("DELETE FROM festival_picks WHERE day = ?", (today.isoformat(),))
    await prepare(sched.picker, sched.store, today)
    with db() as conn:
        pick = festival_pick(conn, sched.store, slots_for(today)[0])
    if pick is None:
        return {"festival": f.name, "picked": False, "note": "no verse passed review"}
    festival, verse = pick
    q = await sched.writer.write(verse) if sched.writer else fallback_quote(verse)
    return {"festival": festival, "picked": True, "verse_id": verse.verse_id,
            **await _send_all(verse_message("test", verse, q, festival))}


@router.post("/release-all")
def release_all() -> dict:
    """Mark the latest release as announced to everyone on this local server,
    without sending a push, so the What's new pop-up and hints can be looked at."""
    latest = latest_release()
    if latest is None:
        raise HTTPException(404, "no release notes")
    with db() as conn:
        conn.execute("INSERT INTO release_stages(version, stage) VALUES (?, 'all') "
                     "ON CONFLICT(version) DO UPDATE SET stage = 'all'", (latest["version"],))
    return {"version": latest["version"], "stage": "all"}


@router.post("/festival-sync")
async def festival_sync(request: Request) -> dict:
    await _sched(request).festival_sync_job()
    with db() as conn:
        return {"dates": conn.execute("SELECT COUNT(*) FROM festival_dates WHERE suspect = 0").fetchone()[0]}


@router.post("/festival-pick")
async def festival_pick_now(request: Request) -> dict:
    await _sched(request).festival_pick_job()
    return {"ok": True}
