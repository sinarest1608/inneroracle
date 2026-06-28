"""Grounded answer prompt + context formatting for the chat layer."""

from __future__ import annotations

from app.models import BOOK_PREFIX
from app.retrieval.search import RetrievedVerse

BOOK_NAME = {
    "bhagavad_gita": "Bhagavad Gita",
    "ashtavakra_gita": "Ashtavakra Gita",
}

ANSWER_SYSTEM = """You are a thoughtful guide to the teachings of the Bhagavad Gita \
and the Ashtavakra Gita. You help people understand these texts and apply them to \
life questions.

Rules:
- Answer in plain, warm English. Be concise and practical, not preachy.
- Ground your answer ONLY in the verses provided below. Do not use outside knowledge \
or invent verse numbers. If the provided verses don't address the question, say so \
honestly (e.g. "These texts don't directly speak to this, but…") rather than guessing.
- Cite every claim with the verse it comes from, inline, like [BG 2.47] or [AG 1.15].
- Attribute teachings to their source. When the two texts emphasize different things, \
present BOTH perspectives explicitly and let the contrast stand — e.g. "The Bhagavad \
Gita frames this as… [BG 2.47], while the Ashtavakra Gita takes a more… view [AG 1.15]." \
Never blend them into a single averaged answer.
- Do NOT print Sanskrit or Devanagari in your answer. The original shloka is available \
on demand via the citation — the reader can open any [BG x.y] / [AG x.y] to see it.
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
