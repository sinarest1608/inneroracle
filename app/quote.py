"""Daily AI paraphrase of the verse of the day — called only by the scheduler.

Uses its own small Anthropic client, never `_services()` (which would load the
~1.3 GB embedder on the 4 GB box just to write one sentence). Any failure falls
back to the trimmed plain translation, which is stored too, so nothing retries
until the next day.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date

import anthropic

from app.config import settings
from app.daily_verse import fallback_quote, speaker_map
from app.models import Verse
from app.prompts import BOOK_NAME
from app.push_db import utcnow

log = logging.getLogger("inneroracle.quote")

MAX_QUOTE = 140
_BAD = re.compile(r"https?://|www\.|<[^>]+>|[\"“”]")

QUOTE_SYSTEM = """You restate one verse from a classical text in plain modern English.

Write exactly one sentence of at most about 110 characters that restates THIS verse's \
meaning. Add no ideas, advice or claims beyond the verse. No quotation marks, emojis, \
hashtags or names in the sentence.

Respond with only a JSON object, no other text:
{"quote": "<the sentence>"}"""


def _prompt(verse: Verse, known_speaker: str | None) -> str:
    book = BOOK_NAME[verse.book]
    lines = [f"Book: {book}", f"Reference: {verse.chapter}.{verse.verse_no}"]
    if known_speaker:
        lines.append(f"Speaker (known): {known_speaker}")
    lines.append(f"Translation: {verse.translation.strip()}")
    if verse.commentary.strip():
        lines.append(f"Commentary (excerpt): {verse.commentary.strip()[:600]}")
    return "\n".join(lines)


def parse_reply(text: str, verse: Verse, known_speaker: str | None) -> dict | None:
    """Validate the model's JSON; None if anything is off."""
    try:
        data = json.loads(text.strip())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    q = data.get("quote")
    if not isinstance(q, str):
        return None
    q = q.strip()
    if not q or len(q) > MAX_QUOTE or "\n" in q or _BAD.search(q):
        return None
    # The speaker never comes from the model: only the hand-checked map names one.
    speaker, source = (known_speaker, "map") if known_speaker else (None, "neutral")
    return {"verse_id": verse.verse_id, "speaker": speaker, "speaker_source": source,
            "quote": q, "quote_source": "ai", "model": settings.quote_model}


class QuoteWriter:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self._client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=20.0, max_retries=0
        )

    async def _ask(self, verse: Verse, known: str | None) -> str:
        resp = await self._client.messages.create(
            model=settings.quote_model,
            max_tokens=2000,
            output_config={"effort": "low"},
            system=QUOTE_SYSTEM,
            messages=[{"role": "user", "content": _prompt(verse, known)}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    async def write(self, verse: Verse) -> dict:
        known = speaker_map().get(verse.verse_id)
        if not settings.quote_ai_enabled:
            return fallback_quote(verse)
        for attempt in (1, 2):  # one retry, then fall back
            try:
                q = parse_reply(await self._ask(verse, known), verse, known)
            except Exception as e:  # noqa: BLE001 — API error or timeout
                log.warning("quote: attempt %d failed: %s", attempt, type(e).__name__)
                continue
            if q is not None:
                return q
            log.warning("quote: attempt %d returned invalid output", attempt)
        return fallback_quote(verse)


def store_quote(conn: sqlite3.Connection, day: date, q: dict) -> dict:
    """Insert today's quote once; if another process won, return theirs."""
    conn.execute(
        "INSERT OR IGNORE INTO daily_quotes(quote_date, verse_id, speaker, speaker_source, "
        "quote, quote_source, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (day.isoformat(), q["verse_id"], q["speaker"], q["speaker_source"], q["quote"],
         q["quote_source"], q["model"], utcnow()),
    )
    row = conn.execute(
        "SELECT verse_id, speaker, speaker_source, quote, quote_source, model "
        "FROM daily_quotes WHERE quote_date = ?", (day.isoformat(),)
    ).fetchone()
    return dict(row)
