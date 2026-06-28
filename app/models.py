"""Canonical data models shared across ingestion, retrieval, and the API."""

from __future__ import annotations

from pydantic import BaseModel

BOOKS = ("bhagavad_gita", "ashtavakra_gita")
BOOK_PREFIX = {"bhagavad_gita": "BG", "ashtavakra_gita": "AG"}


def make_verse_id(book: str, chapter: int, verse_no: str) -> str:
    """Stable ID like 'BG-2.47' / 'AG-1.12'. verse_no may be a range ('42-43')."""
    return f"{BOOK_PREFIX[book]}-{chapter}.{verse_no}"


class Verse(BaseModel):
    """One canonical verse, with shloka kept distinct from translation/commentary."""

    verse_id: str
    book: str
    chapter: int
    verse_no: str
    sanskrit: str = ""          # Devanagari shloka
    transliteration: str = ""   # IAST / romanized
    translation: str = ""       # English meaning
    commentary: str = ""        # purport / explanation
    source_edition: str = ""
