"""Sending web pushes: the one path every notification goes through.

Every send (daily, confirm, test, manual) writes one `push_deliveries` row and
one app-log line per device. Rows and logs never contain endpoints, keys,
secrets, IPs or raw exception messages (a requests ConnectionError message
contains the endpoint URL) — only the exception type and HTTP status.

Daily sends are exactly-once-or-missed: each device is claimed with a `pending`
row (unique per send-slot key + device; the key lives in the run_date column) before its push, so two workers can never
both send to it, and a crash leaves `pending` rows that are marked `unknown`
and not resent — a rare missed verse beats a duplicate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pywebpush import WebPushException, webpush

from app.config import settings
from app.daily_verse import (
    Slot, current_slot, local_today, notification_text, send_times, slot_from_key,
    stored_quote, verse_for, verse_url,
)
from app.models import Verse
from app.push_db import audit, db, utcnow, write_tx
from app.quote import QuoteWriter, store_quote
from app.store import VerseStore

log = logging.getLogger("inneroracle.push")

SEND_TIMEOUT = 10        # seconds per push request
CONCURRENCY = 10
TTL = 86400
MAX_FAILURES = 10
HEARTBEAT_EVERY = 20     # seconds (plan: at least every 30)
STALE_AFTER = timedelta(minutes=5)

CONFIRM_TITLE = "Daily verse is on"
CONFIRM_RETRY_WAITS = (1.5, 3.0, None)  # seconds before each retry; None = last try
TEST_TITLE = "InnerOracle test"
TEST_BODY = "Test notification. If you can read this, pushes reach this device."


@dataclass
class Outcome:
    status: str               # delivered / failed / expired_removed
    http_status: int | None
    error_type: str | None


@dataclass
class Message:
    kind: str
    title: str
    body: str
    url: str
    verse_id: str | None = None
    speaker: str | None = None
    speaker_source: str | None = None
    quote_source: str | None = None

    def payload(self) -> str:
        return json.dumps({"title": self.title, "body": self.body,
                           "url": self.url, "tag": "daily-verse"})


# ---- one push ----------------------------------------------------------------

def _push_sync(endpoint: str, p256dh: str, auth: str, data: str) -> Outcome:
    try:
        resp = webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=data,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},  # fresh dict: webpush mutates it
            ttl=TTL,
            timeout=SEND_TIMEOUT,
        )  # never verbose=True: it logs the VAPID headers
        return Outcome("delivered", getattr(resp, "status_code", None), None)
    except WebPushException as e:
        code = getattr(e.response, "status_code", None)
        status = "expired_removed" if code in (404, 410) else "failed"
        return Outcome(status, code, "WebPushException")
    except Exception as e:  # noqa: BLE001 — one bad device must never stop the loop
        return Outcome("failed", None, type(e).__name__)


async def push(endpoint: str, p256dh: str, auth: str, msg: Message) -> Outcome:
    return await asyncio.to_thread(_push_sync, endpoint, p256dh, auth, msg.payload())


# ---- logging + device bookkeeping ---------------------------------------------

def _log_line(msg: Message, device_id: int | None, o: Outcome) -> None:
    now = datetime.now(ZoneInfo(settings.push_timezone))
    log.info(
        "push.sent kind=%s date=%s time=%s tz=%s verse=%s speaker=%s speaker_source=%s "
        "device=%s status=%s http=%s%s",
        msg.kind, now.date().isoformat(), now.strftime("%H:%M:%S"), settings.push_timezone,
        msg.verse_id or "-", msg.speaker or "-", msg.speaker_source or "-",
        device_id if device_id is not None else "-", o.status, o.http_status or "-",
        f" error={o.error_type}" if o.error_type else "",
    )


def insert_delivery(conn: sqlite3.Connection, msg: Message, device_id: int | None,
                    status: str, run_date: str | None = None,
                    o: Outcome | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO push_deliveries(sent_at, kind, run_date, verse_id, speaker, "
        "speaker_source, quote_source, title, body, url, device_id, status, "
        "http_status, error_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (utcnow(), msg.kind, run_date, msg.verse_id, msg.speaker, msg.speaker_source,
         msg.quote_source, msg.title, msg.body, msg.url, device_id, status,
         o.http_status if o else None, o.error_type if o else None),
    )
    return cur.lastrowid


def apply_outcome(conn: sqlite3.Connection, device_id: int, o: Outcome) -> None:
    if o.status == "delivered":
        conn.execute("UPDATE push_subscriptions SET last_success_at = ?, failure_count = 0 "
                     "WHERE id = ?", (utcnow(), device_id))
    elif o.status == "expired_removed":
        conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (device_id,))
    else:
        conn.execute("UPDATE push_subscriptions SET failure_count = failure_count + 1 "
                     "WHERE id = ?", (device_id,))
        conn.execute("DELETE FROM push_subscriptions WHERE id = ? AND failure_count >= ?",
                     (device_id, MAX_FAILURES))


async def send_to_device(row: sqlite3.Row, msg: Message) -> Outcome:
    """Unclaimed send (test/manual): push, then log the row and update the device."""
    o = await push(row["endpoint"], row["p256dh"], row["auth"], msg)
    with db() as conn, write_tx(conn):
        insert_delivery(conn, msg, row["id"], o.status, o=o)
        apply_outcome(conn, row["id"], o)
    _log_line(msg, row["id"], o)
    return o


async def send_confirmation(endpoint: str, p256dh: str, auth: str) -> Outcome:
    """Sign-up check: only a subscription the push service accepts gets stored."""
    msg = Message("confirm", CONFIRM_TITLE,
                  f"{schedule_phrase()}. Turn it off anytime from the menu.", "/ui/")
    # A brand-new subscription can briefly get 404/410 from the push service
    # (seen with FCM right after subscribe), so retry those a couple of times.
    for wait in CONFIRM_RETRY_WAITS:
        o = await push(endpoint, p256dh, auth, msg)
        if o.status != "expired_removed" or wait is None:
            break
        await asyncio.sleep(wait)
    with db() as conn:
        insert_delivery(conn, msg, None, o.status, o=o)  # device row doesn't exist yet
    _log_line(msg, None, o)
    return o


def _clock(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


def schedule_phrase() -> str:
    """e.g. "A verse around 8:30 am and 5:30 pm, every day"."""
    times = [_clock(t) for t in send_times()]
    joined = times[0] if len(times) == 1 else ", ".join(times[:-1]) + " and " + times[-1]
    return f"A verse around {joined}, every day"


def test_message() -> Message:
    return Message("test", TEST_TITLE, TEST_BODY, "/ui/")


# ---- the daily verse -----------------------------------------------------------

def verse_message(kind: str, verse: Verse, q: dict) -> Message:
    title, body = notification_text(verse, q)
    return Message(kind, title, body, verse_url(verse.verse_id), verse.verse_id,
                   q["speaker"], q["speaker_source"], q["quote_source"])


async def ensure_quote(store: VerseStore, writer: QuoteWriter, slot: Slot,
                       verse: Verse) -> dict:
    """This send's quote, generating (once) if missing. Only the scheduler path calls this."""
    with db() as conn:
        q = stored_quote(conn, slot)
    if q is not None and q["verse_id"] == verse.verse_id:
        return q
    q = await writer.write(verse)
    with db() as conn:
        return store_quote(conn, slot, q)


async def _send_daily_unclaimed(slot: Slot, msg: Message) -> None:
    run_date = slot.key  # one run, quote and claim set per send slot
    with db() as conn:
        devices = conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions s WHERE NOT EXISTS ("
            "SELECT 1 FROM push_deliveries d WHERE d.kind = 'daily' AND d.run_date = ? "
            "AND d.device_id = s.id) ORDER BY id", (run_date,)
        ).fetchall()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(row: sqlite3.Row) -> None:
        async with sem:
            try:
                with db() as conn:
                    delivery_id = insert_delivery(conn, msg, row["id"], "pending", run_date)
            except sqlite3.IntegrityError:
                return  # another worker claimed this device
            o = await push(row["endpoint"], row["p256dh"], row["auth"], msg)
            with db() as conn, write_tx(conn):
                conn.execute("UPDATE push_deliveries SET status = ?, http_status = ?, "
                             "error_type = ?, sent_at = ? WHERE id = ?",
                             (o.status, o.http_status, o.error_type, utcnow(), delivery_id))
                apply_outcome(conn, row["id"], o)
            _log_line(msg, row["id"], o)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_EVERY)
            with db() as conn:
                conn.execute("UPDATE push_daily_runs SET heartbeat_at = ? WHERE run_date = ?",
                             (utcnow(), run_date))

    hb = asyncio.create_task(heartbeat())
    try:
        await asyncio.gather(*(one(r) for r in devices))
    finally:
        hb.cancel()
    with db() as conn:
        counts = dict(conn.execute(
            "SELECT status, COUNT(*) FROM push_deliveries WHERE kind = 'daily' "
            "AND run_date = ? GROUP BY status", (run_date,)
        ).fetchall())
        conn.execute("UPDATE push_daily_runs SET status = 'completed', completed_at = ?, "
                     "delivered = ?, failed = ? WHERE run_date = ?",
                     (utcnow(), counts.get("delivered", 0),
                      sum(v for k, v in counts.items() if k != "delivered"), run_date))
    log.info("push.run slot=%s devices=%d counts=%s", run_date, len(devices), counts)


async def run_daily(store: VerseStore, writer: QuoteWriter, slot: Slot | None = None) -> str:
    """Claim this send's run (unique run key: one process wins) and send.
    Defaults to the most recent send time that has come."""
    slot = slot or current_slot()
    with db() as conn:
        verse = verse_for(conn, store, slot)
        if verse is None:
            log.error("push.run slot=%s: no verse available (empty rotation?)", slot.key)
            return "no_verse"
        now = utcnow()
        try:
            conn.execute("INSERT INTO push_daily_runs(run_date, verse_id, status, started_at, "
                         "heartbeat_at) VALUES (?, ?, 'started', ?, ?)",
                         (slot.key, verse.verse_id, now, now))
        except sqlite3.IntegrityError:
            return "already_ran"
    q = await ensure_quote(store, writer, slot, verse)
    await _send_daily_unclaimed(slot, verse_message("daily", verse, q))
    return "sent"


async def resume_stale(store: VerseStore, writer: QuoteWriter, day: date | None = None) -> int:
    """Take over any of the day's runs whose worker stopped heart-beating
    (crash/kill). Returns how many runs were resumed."""
    day = day or local_today()
    cutoff = (datetime.now(UTC) - STALE_AFTER).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db() as conn:
        keys = [r[0] for r in conn.execute(
            "SELECT run_date FROM push_daily_runs WHERE run_date LIKE ? AND status = 'started' "
            "AND heartbeat_at < ?", (f"{day.isoformat()}#%", cutoff))]
    resumed = 0
    for key in keys:
        slot = slot_from_key(key)
        if slot is None:  # send times changed since this run started
            continue
        with db() as conn:
            cur = conn.execute("UPDATE push_daily_runs SET heartbeat_at = ? WHERE run_date = ? "
                               "AND status = 'started' AND heartbeat_at < ?",
                               (utcnow(), key, cutoff))
            if cur.rowcount != 1:
                continue  # another process took it
            # The push may have gone out just before the crash: don't resend these.
            conn.execute("UPDATE push_deliveries SET status = 'unknown' WHERE kind = 'daily' "
                         "AND run_date = ? AND status = 'pending'", (key,))
            verse = verse_for(conn, store, slot)
        log.warning("push.run slot=%s: resuming a stale run", key)
        if verse is None:
            continue
        q = await ensure_quote(store, writer, slot, verse)
        await _send_daily_unclaimed(slot, verse_message("daily", verse, q))
        resumed += 1
    return resumed


class ForcedAlreadyToday(Exception):
    pass


async def force_send(store: VerseStore, writer: QuoteWriter) -> int:
    """Re-send the latest verse to every device as kind='manual', outside the
    daily run row and claim index. At most once per day; not resumed after a crash."""
    slot = current_slot()
    detail = f"date={local_today().isoformat()}"
    with db() as conn, write_tx(conn):
        if conn.execute("SELECT 1 FROM admin_actions WHERE action = 'force_send' AND detail = ?",
                        (detail,)).fetchone():
            raise ForcedAlreadyToday
        audit(conn, "force_send", detail)
        verse = verse_for(conn, store, slot)
        devices = conn.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions "
                               "ORDER BY id").fetchall()
    if verse is None:
        return 0
    msg = verse_message("manual", verse, await ensure_quote(store, writer, slot, verse))
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(row: sqlite3.Row) -> None:
        async with sem:
            await send_to_device(row, msg)

    await asyncio.gather(*(one(r) for r in devices))
    return len(devices)
