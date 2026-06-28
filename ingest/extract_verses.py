"""LLM-assisted structured extraction: chapter text → separated verse records.

Claude reads each chapter and returns an array of verses, keeping shloka /
transliteration / translation / commentary distinct — robust to messy editions
where regex fails. Pure helpers (`chunk_text`, `to_verses`, `_merge`) are
import-safe and unit-tested without any API calls.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.models import Verse, make_verse_id

# Some editions print verse numbers as "chapter.verse" (e.g. the John Richards
# Ashtavakra: "1.1", "2.4"). The model then puts the whole thing in verse_no and
# defaults chapter=1. Normalize that back to (chapter, verse). Safe for BG, whose
# verse_no is "47" / "42-43" (no leading "<int>.") and won't match.
_CHAPTER_VERSE_RE = re.compile(r"^(\d+)\.(.+)$")


def _normalize_chapter_verse(chapter: int, verse_no: str) -> tuple[int, str]:
    m = _CHAPTER_VERSE_RE.match(verse_no)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return chapter, verse_no

EXTRACTION_SYSTEM = """You are a careful editor digitizing classical Indian scripture \
(the Bhagavad Gita and the Ashtavakra Gita).

From the passage, return EVERY verse you find. For each verse separate these fields:
- sanskrit: the original Devanagari shloka (empty string if not present)
- transliteration: romanized / IAST Sanskrit (empty string if not present)
- translation: the English meaning of the verse
- commentary: any purport, explanation, or notes attached to that verse (may be empty)

Rules:
- Use the chapter and verse numbers exactly as printed. verse_no may be a range like "42-43".
- Do NOT invent, summarize, paraphrase, or merge distinct verses.
- If a field is absent in the source, return an empty string for it — never guess.
- Front matter, tables of contents, prefaces, and indexes contain no verses; return an empty list for those.
"""


class VerseExtract(BaseModel):
    chapter: int
    verse_no: str
    sanskrit: str = ""
    transliteration: str = ""
    translation: str = ""
    commentary: str = ""


class ChapterVerses(BaseModel):
    verses: list[VerseExtract]


def chunk_text(text: str, max_chars: int = 8000) -> list[str]:
    """Split a chapter into paragraph-aligned windows to bound LLM output size."""
    paras = text.split("\n\n")
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def extract_chapter(text: str, client, model: str) -> list[VerseExtract]:
    """Call Claude (structured output) over each window of a chapter."""
    out: list[VerseExtract] = []
    for window in chunk_text(text):
        resp = client.messages.parse(
            model=model,
            max_tokens=16000,
            system=[
                {
                    "type": "text",
                    "text": EXTRACTION_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": window}],
            output_format=ChapterVerses,
        )
        if resp.parsed_output:
            out.extend(resp.parsed_output.verses)
    return out


def _merge(a: Verse, b: Verse) -> Verse:
    """Merge two records for the same verse_id, preferring the fuller field."""
    longer = lambda x, y: x if len(x) >= len(y) else y  # noqa: E731
    return Verse(
        verse_id=a.verse_id,
        book=a.book,
        chapter=a.chapter,
        verse_no=a.verse_no,
        sanskrit=longer(a.sanskrit, b.sanskrit),
        transliteration=longer(a.transliteration, b.transliteration),
        translation=longer(a.translation, b.translation),
        commentary=longer(a.commentary, b.commentary),
        source_edition=a.source_edition or b.source_edition,
    )


def to_verses(
    extracts: list[VerseExtract], book: str, source_edition: str
) -> list[Verse]:
    """Assign verse_id/book/source and de-dup by verse_id (merging windows)."""
    merged: dict[str, Verse] = {}
    for e in extracts:
        chapter, verse_no = _normalize_chapter_verse(e.chapter, e.verse_no.strip())
        vid = make_verse_id(book, chapter, verse_no)
        v = Verse(
            verse_id=vid,
            book=book,
            chapter=chapter,
            verse_no=verse_no,
            sanskrit=e.sanskrit.strip(),
            transliteration=e.transliteration.strip(),
            translation=e.translation.strip(),
            commentary=e.commentary.strip(),
            source_edition=source_edition,
        )
        merged[vid] = _merge(merged[vid], v) if vid in merged else v
    return list(merged.values())
