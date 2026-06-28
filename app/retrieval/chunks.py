"""Derive the two chunk types from a canonical verse.

This is the "separate sources" step: a verse chunk (shloka unit = transliteration
+ translation) and one or more commentary chunks, all linked back by `verse_id`
so retrieval can recombine them into the full verse at answer time.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Verse

VERSE = "verse"
COMMENTARY = "commentary"


@dataclass
class Chunk:
    chunk_id: str   # "BG-2.47::verse" / "BG-2.47::commentary::0"
    verse_id: str
    book: str
    chapter: int
    verse_no: str
    ctype: str      # VERSE | COMMENTARY
    text: str


def _split(text: str, max_chars: int) -> list[str]:
    """Pack paragraphs into windows of up to ~max_chars."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    out: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > max_chars:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out or ([text.strip()] if text.strip() else [])


def verse_chunks(v: Verse, commentary_max_chars: int = 1500) -> list[Chunk]:
    chunks: list[Chunk] = []

    verse_text = " ".join(t for t in (v.transliteration, v.translation) if t).strip()
    if verse_text:
        chunks.append(
            Chunk(f"{v.verse_id}::{VERSE}", v.verse_id, v.book, v.chapter,
                  v.verse_no, VERSE, verse_text)
        )

    if v.commentary.strip():
        for i, part in enumerate(_split(v.commentary, commentary_max_chars)):
            chunks.append(
                Chunk(f"{v.verse_id}::{COMMENTARY}::{i}", v.verse_id, v.book,
                      v.chapter, v.verse_no, COMMENTARY, part)
            )
    return chunks
