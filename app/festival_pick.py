"""Choosing the verse for a festival day, a few days ahead.

One model call shortlists three verses for the festival's theme; plain code
checks them (exists, not sent recently, not last year's pick); a second call,
which never sees the first one's reasons, approves one or rejects all. One
retry, then the day falls back to the normal rotation. The result is locked in
`festival_picks`, and the owner gets an email that never names the verse.

Runs from the scheduler only, with its own small Anthropic client (like
app/quote.py), never the chat services.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
from datetime import date, timedelta

import anthropic

from app import alerts
from app.config import settings
from app.daily_verse import local_today, rotation_verse, slots_for
from app.festivals import Festival, upcoming
from app.models import Verse
from app.prompts import BOOK_NAME
from app.push_db import db, utcnow, write_tx
from app.store import VerseStore

log = logging.getLogger("inneroracle.festival")

RECENT_DAYS = 60
SHORTLIST_SIZE = 3
ATTEMPTS = 2  # the shortlist, then one retry without the rejected verses

SHORTLIST_SYSTEM = f"""You choose verses for a daily-verse notification that goes out on a \
Hindu festival day. The readers are celebrating the festival.

You are given the festival's name, its core belief, and a catalogue of verses from the \
Bhagavad Gita (BG) and the Ashtavakra Gita (AG), one per line as `id: translation`.

Pick the {SHORTLIST_SIZE} verses whose own meaning best expresses the festival's core belief. \
A reader should see the connection without it being explained. Prefer a verse that stands on \
its own as a teaching; avoid verses that only set a scene, list names, or need the surrounding \
verses to make sense, and avoid anything that would feel grim or out of place on a day of \
celebration. Choose only ids that appear in the catalogue.

Respond with only a JSON object, no other text:
{{"picks": [{{"verse_id": "<id>", "reason": "<one line>"}}]}}"""

REVIEW_SYSTEM = """You review verses proposed for a daily-verse notification on a Hindu \
festival day. You are given the festival, its core belief, and the candidate verses.

Judge each verse on its own words:
- Is it a genuine match for the festival's core belief, not a loose or forced one?
- Is it appropriate and respectful for the occasion?
- Would it make sense to someone celebrating the festival, with no explanation?

Approve the single best verse that passes all three, or none if none does. It is better to \
approve nothing than to approve a weak match.

Respond with only a JSON object, no other text:
{"approved": "<verse_id>"} or {"approved": null}"""


def _json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _ref(v: Verse) -> str:
    return f"{BOOK_NAME[v.book]} {v.chapter}.{v.verse_no}"


def _festival_block(f: Festival) -> str:
    return f"Festival: {f.name}\nCore belief: {f.theme}" + (f"\nNotes: {f.notes}" if f.notes else "")


class FestivalPicker:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self._client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=90.0, max_retries=1
        )

    async def _ask(self, system: list[dict] | str, user: str) -> str:
        resp = await self._client.messages.create(
            model=settings.festival_model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    async def shortlist(self, f: Festival, catalogue: str, exclude: set[str]) -> list[str]:
        """Up to SHORTLIST_SIZE verse ids, best first. The reasons are logged
        nowhere and never reach the reviewer."""
        # The catalogue is identical on the retry, so it is cached; what varies
        # (festival, exclusions) comes after it.
        if settings.mock_model:  # local testing: the first usable verses, no API call
            ids = re.findall(r"^([A-Z]{2}-[\d.-]+): ", catalogue, re.M)
            return [i for i in ids if i not in exclude][:SHORTLIST_SIZE]
        system = [{"type": "text", "text": SHORTLIST_SYSTEM},
                  {"type": "text", "text": "Catalogue:\n" + catalogue,
                   "cache_control": {"type": "ephemeral"}}]
        user = _festival_block(f)
        if exclude:
            user += "\n\nDo not pick any of these: " + ", ".join(sorted(exclude))
        data = _json(await self._ask(system, user)) or {}
        picks = data.get("picks")
        ids = [p.get("verse_id") for p in picks if isinstance(p, dict)] if isinstance(picks, list) else []
        return [i for i in dict.fromkeys(ids) if isinstance(i, str)][:SHORTLIST_SIZE]

    async def review(self, f: Festival, verses: list[Verse]) -> str | None:
        if settings.mock_model:
            return verses[0].verse_id if verses else None
        lines = [_festival_block(f), "", "Candidates:"]
        for v in verses:
            lines.append(f"\n[{v.verse_id}] {_ref(v)}\nTranslation: {v.translation.strip()}")
            if v.commentary.strip():
                lines.append(f"Commentary (excerpt): {v.commentary.strip()[:600]}")
        data = _json(await self._ask(REVIEW_SYSTEM, "\n".join(lines))) or {}
        approved = data.get("approved")
        return approved if approved in {v.verse_id for v in verses} else None


# ---- code checks ---------------------------------------------------------------

def recently_sent(conn: sqlite3.Connection, store: VerseStore, day: date,
                  today: date) -> set[str]:
    """Verses pushed in the RECENT_DAYS before the festival, including the
    rotation's verses for the days still to come before it."""
    cutoff = (day - timedelta(days=RECENT_DAYS)).isoformat()
    ids = {r[0] for r in conn.execute(
        "SELECT verse_id FROM push_daily_runs WHERE run_date >= ? AND verse_id IS NOT NULL",
        (cutoff,))}
    d = today
    while d <= day:
        for slot in slots_for(d):
            if not (d == day and slot.index == 0):  # the festival send itself
                v = rotation_verse(conn, store, slot)
                if v is not None:
                    ids.add(v.verse_id)
        d += timedelta(days=1)
    return ids


def last_pick(conn: sqlite3.Connection, festival: str, day: date) -> str | None:
    row = conn.execute(
        "SELECT verse_id FROM festival_picks WHERE festival = ? AND day < ? AND verse_id IS NOT NULL "
        "ORDER BY day DESC LIMIT 1", (festival, day.isoformat())).fetchone()
    return row[0] if row else None


async def choose(picker: FestivalPicker, conn: sqlite3.Connection, store: VerseStore,
                 f: Festival, day: date, today: date) -> Verse | None:
    """The approved verse, or None when nothing passed review twice."""
    banned = recently_sent(conn, store, day, today)
    prev = last_pick(conn, f.name, day)
    if prev:
        banned.add(prev)
    usable = {v.verse_id: v for v in store.iter_all()
              if v.translation.strip() and v.verse_id not in banned}
    catalogue = "\n".join(f"{vid}: {' '.join(v.translation.split())}"
                          for vid, v in sorted(usable.items()))
    rejected: set[str] = set()
    for attempt in range(1, ATTEMPTS + 1):
        ids = await picker.shortlist(f, catalogue, rejected)
        # Code checks: in verses.db, not sent recently, not last year's pick,
        # and not already rejected. `usable` is exactly that set.
        passed = [usable[i] for i in ids if i in usable and i not in rejected]
        log.info("festival.pick festival=%s attempt=%d shortlisted=%d passed_checks=%d",
                 f.name, attempt, len(ids), len(passed))
        if passed:
            approved = await picker.review(f, passed)
            if approved:
                return usable[approved]
        rejected.update(ids)
    return None


# ---- lock in + tell the owner ----------------------------------------------------

def _nice(day: date) -> str:
    return f"{day.day} {day.strftime('%B')}"


def _base_url() -> str:
    base = settings.public_base_url or (
        settings.vapid_subject if settings.vapid_subject.startswith("https://") else "")
    return base.rstrip("/")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def lock_in(conn: sqlite3.Connection, f: Festival, day: date, verse: Verse | None) -> str | None:
    """Save the result (one row per day; first writer wins). Returns the one-time
    cancel token for a pick, or None for a fallback or if a row already existed."""
    token = secrets.token_urlsafe(32) if verse is not None else None
    cur = conn.execute(
        "INSERT INTO festival_picks(festival, day, verse_id, status, cancel_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(day) DO NOTHING",
        (f.name, day.isoformat(), verse.verse_id if verse else None,
         "picked" if verse else "fallback", hash_token(token) if token else None, utcnow()))
    return token if cur.rowcount else None


def success_email(f: Festival, day: date, token: str | None) -> tuple[str, str]:
    """Never names the verse, its reference or the quote."""
    subject = f"{f.name} on {_nice(day)}: verse picked and checked, all set."
    body = [f"{f.name} falls on {day.strftime('%A')} {_nice(day)} {day.year} according to the "
            "holiday calendar. A verse has been picked and reviewed for that morning. "
            "It stays a surprise until it arrives."]
    base = _base_url()
    if token and base:
        body += ["", "Wrong date? Open this link to cancel it, and that day will use the "
                     "normal verse instead (works once, until that morning's verse is sent):",
                 f"{base}/push/festival/cancel/{token}"]
    return subject, "\n".join(body)


async def prepare(picker: FestivalPicker, store: VerseStore, today: date | None = None) -> int:
    """Pick a verse for every festival in the next few days that has none yet.
    Returns how many were handled. An API failure raises, leaving no row, so the
    next daily run tries again; with no row by the festival morning, that day
    uses the normal rotation."""
    today = today or local_today()
    with db() as conn:
        todo = [(day, f) for day, f in upcoming(conn, today)
                if conn.execute("SELECT 1 FROM festival_picks WHERE day = ?",
                                (day.isoformat(),)).fetchone() is None]
    for day, f in todo:
        with db() as conn:
            verse = await choose(picker, conn, store, f, day, today)
            with write_tx(conn):
                token = lock_in(conn, f, day, verse)
        log.info("festival.pick festival=%s day=%s result=%s", f.name, day.isoformat(),
                 "picked" if verse else "fallback")
        if verse is not None:
            await alerts.notify(*success_email(f, day, token))
        else:
            await alerts.notify(
                f"{f.name}: no verse passed review, so it'll use the normal rotation.",
                f"{f.name} on {_nice(day)}: two rounds of picks were all rejected by the "
                "reviewer, so that day will send the normal verse.")
    return len(todo)


def sent(conn: sqlite3.Connection, day: date) -> bool:
    """Has that day's morning send (the festival one) already started? After
    that the pick must stay as it is: it is what people received."""
    return conn.execute("SELECT 1 FROM push_daily_runs WHERE run_date = ?",
                        (slots_for(day)[0].key,)).fetchone() is not None


def cancel(conn: sqlite3.Connection, token: str, today: date | None = None) -> tuple[str, sqlite3.Row | None]:
    """Use a "wrong date" link. Returns (result, pick row): result is
    'cancelled', 'expired' (the day has passed or its verse has already been
    sent) or 'invalid'. Cancelling also marks that year's date
    as suspect and drops a quote already written for the festival verse."""
    today = today or local_today()
    with write_tx(conn):
        row = conn.execute("SELECT id, festival, day FROM festival_picks WHERE cancel_hash = ? "
                           "AND status = 'picked'", (hash_token(token),)).fetchone()
        if row is None:
            return "invalid", None
        day = date.fromisoformat(row["day"])
        if today > day or sent(conn, day):
            return "expired", row
        conn.execute("UPDATE festival_picks SET status = 'cancelled', cancel_hash = NULL "
                     "WHERE id = ?", (row["id"],))
        conn.execute("UPDATE festival_dates SET suspect = 1 WHERE festival = ? AND year = ?",
                     (row["festival"], day.year))
        # Not sent yet (checked above), so a quote written for the festival verse goes too.
        conn.execute("DELETE FROM daily_quotes WHERE quote_date = ?", (slots_for(day)[0].key,))
        conn.execute("INSERT INTO admin_actions(at, action, detail) VALUES (?, 'festival_cancel', ?)",
                     (utcnow(), f"festival={row['festival']} day={row['day']}"))
        return "cancelled", row
