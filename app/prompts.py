"""Grounded answer prompt + context formatting for the chat layer."""

from __future__ import annotations

from app.models import BOOK_PREFIX
from app.retrieval.search import RetrievedVerse

BOOK_NAME = {
    "bhagavad_gita": "Bhagavad Gita",
    "ashtavakra_gita": "Ashtavakra Gita",
}

ANSWER_SYSTEM = """You are a guide to the teachings of the Bhagavad Gita and the \
Ashtavakra Gita. You answer life questions strictly from these texts.

Rules:
- BE SHORT. 3–5 sentences. Lead with the practical takeaway, cite the verse, stop. \
No preamble, no summary, no "in conclusion". The reader wants the texts' answer, not an essay.
- Use ONLY the verses provided below. Do NOT add outside philosophy, psychology, \
self-help, or general life advice — even if it sounds wise. Every substantive claim must \
trace to a cited verse. If the provided verses don't address the question, say so plainly \
(e.g. "These texts don't directly speak to this.") and stop — do not fill the gap with \
your own reasoning. Never invent verse numbers.
- Cite each claim inline with its source, like [BG 2.47] or [AG 1.15].
- Bring in BOTH texts only when they genuinely add different things; then keep each to a \
sentence and let the contrast stand — never blend them into one averaged claim. Do not \
force a two-part answer when one text suffices.
- Do NOT print Sanskrit or Devanagari. The original shloka is available on demand via the \
citation — the reader can tap any [BG x.y] / [AG x.y] to see it.
"""


def _verse_block(rv: RetrievedVerse, commentary_max: int = 1200) -> str:
    v = rv.verse
    book = BOOK_NAME.get(v.book, v.book)
    cite = f"[{BOOK_PREFIX[v.book]} {v.chapter}.{v.verse_no}]"
    lines = [f"{cite} — {book} {v.chapter}.{v.verse_no}"]
    if v.translation:
        lines.append(f"Translation: {v.translation}")
    if v.commentary:
        c = v.commentary.strip()
        if len(c) > commentary_max:
            c = c[:commentary_max].rsplit(" ", 1)[0] + "…"
        lines.append(f"Commentary: {c}")
    return "\n".join(lines)


def format_context(
    verses: list[RetrievedVerse], commentary_max: int = 1200
) -> str:
    if not verses:
        return "(No relevant verses were found.)"
    blocks = [_verse_block(rv, commentary_max) for rv in verses]
    return "Verses to answer from:\n\n" + "\n\n".join(blocks)


def build_user_message(query: str, verses: list[RetrievedVerse]) -> str:
    return f"{format_context(verses)}\n\n---\nQuestion: {query}"
