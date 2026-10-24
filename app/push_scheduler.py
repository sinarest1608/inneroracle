"""Scheduled push jobs: quote, daily send, stale-run resume, purges, backups.

Runs inside the app (AsyncIOScheduler, started from the lifespan handler).
misfire_grace_time=3600 + coalesce: /chat does synchronous retrieval on the
event loop, and APScheduler's 1 s default would silently skip the day when the
loop is busy at send time.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.daily_verse import local_today, sync_rotation, verse_for
from app.push_db import db, migrate
from app.push_service import ensure_quote, resume_stale, run_daily
from app.quote import QuoteWriter
from app.store import VerseStore

log = logging.getLogger("inneroracle.push")

BACKUPS_KEPT = 7
ADMIN_ACTIONS_DAYS = 365


def _ts(delta: timedelta) -> str:
    return (datetime.now(UTC) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _send_time() -> tuple[int, int]:
    h, m = settings.push_daily_time.split(":")
    return int(h), int(m)


def purge() -> None:
    """Hash-only after PUSH_SIGNUP_IP_DAYS; delete logs after retention."""
    keep = timedelta(days=settings.push_log_retention_days)
    with db() as conn:
        conn.execute("UPDATE signup_attempts SET ip = NULL WHERE ip IS NOT NULL AND at < ?",
                     (_ts(timedelta(days=settings.push_signup_ip_days)),))
        conn.execute("DELETE FROM signup_attempts WHERE at < ?", (_ts(keep),))
        conn.execute("DELETE FROM signup_rejections WHERE hour < ?", (_ts(keep),))
        conn.execute("DELETE FROM push_deliveries WHERE sent_at < ?", (_ts(keep),))
        conn.execute("DELETE FROM admin_actions WHERE at < ?",
                     (_ts(timedelta(days=ADMIN_ACTIONS_DAYS)),))


def backup(db_path: str | None = None) -> Path | None:
    """Nightly copy without raw IPs: temp file → leave WAL → scrub → VACUUM →
    close → rename. A crash midway leaves only the temp file (removed next run)."""
    src_path = Path(db_path or settings.push_db_path)
    out_dir = src_path.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.tmp"):
        stale.unlink()
    day = local_today().isoformat()
    tmp, final = out_dir / f"push-{day}.db.tmp", out_dir / f"push-{day}.db"
    try:
        with db(str(src_path)) as src:
            dst = sqlite3.connect(tmp, isolation_level=None)
            try:
                src.backup(dst)
                dst.execute("PRAGMA journal_mode=DELETE")  # the copy inherits WAL
                dst.execute("UPDATE signup_attempts SET ip = NULL")
                dst.execute("VACUUM")
            finally:
                dst.close()
        tmp.replace(final)
    except Exception:  # noqa: BLE001 — logged; no other alerting by design
        log.exception("push.backup failed")
        return None
    for old in sorted(out_dir.glob("push-*.db"))[:-BACKUPS_KEPT]:
        old.unlink()
    return final


class PushScheduler:
    def __init__(self, store: VerseStore, writer: QuoteWriter | None = None):
        self.store = store
        self.writer = writer or QuoteWriter()
        self.sched = AsyncIOScheduler(
            timezone=ZoneInfo(settings.push_timezone),
            job_defaults={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1},
        )

    async def quote_job(self) -> None:
        day = local_today()
        with db() as conn:
            verse = verse_for(conn, self.store, day)
        if verse is not None:
            await ensure_quote(self.store, self.writer, day, verse)

    async def send_job(self) -> None:
        log.info("push.run result=%s", await run_daily(self.store, self.writer))

    async def stale_job(self) -> None:
        await resume_stale(self.store, self.writer)

    async def startup(self) -> None:
        migrate()
        with db() as conn:
            added = sync_rotation(conn, self.store, settings.push_rotation_seed)
        if added:
            log.info("push.rotation appended %d verses", added)
        h, m = _send_time()
        q_at = datetime(2000, 1, 1, h, m) - timedelta(minutes=10)
        add = self.sched.add_job
        add(self.quote_job, CronTrigger(hour=q_at.hour, minute=q_at.minute), id="quote")
        add(self.send_job, CronTrigger(hour=h, minute=m), id="send")
        add(self.stale_job, CronTrigger(minute="*/15"), id="stale")
        add(purge, CronTrigger(hour=3, minute=30), id="purge")
        add(backup, CronTrigger(hour=3, minute=0), id="backup")
        self.sched.start()

        # Catch-up (server was down at send time) and resume (crashed mid-send).
        now = datetime.now(ZoneInfo(settings.push_timezone))
        if (now.hour, now.minute) >= (h, m):
            with db() as conn:
                ran = conn.execute("SELECT 1 FROM push_daily_runs WHERE run_date = ?",
                                   (now.date().isoformat(),)).fetchone()
            if ran is None:
                self.sched.add_job(self.send_job, id="catchup")
        self.sched.add_job(self.stale_job, id="resume-at-start")

    def shutdown(self) -> None:
        if self.sched.running:
            self.sched.shutdown(wait=False)
