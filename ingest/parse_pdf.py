"""PDF → chapters (fallback). Uses PyMuPDF (binary), imported lazily.

PDF extraction is noisy and often mangles Devanagari — prefer EPUB. This splits
on 'CHAPTER N' headings when present, else returns the whole document as one
chapter for the LLM extractor to segment.
"""

from __future__ import annotations

import re

from ingest.sources import Chapter, clean_text

_CHAPTER_RE = re.compile(r"\n(?=(?:CHAPTER|Chapter)\s+[\dIVXLCivxlc]+)")
_MIN_CHARS = 200


def parse_pdf(path: str) -> list[Chapter]:
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    full = clean_text("\n".join(page.get_text() for page in doc))
    parts = _CHAPTER_RE.split(full)
    if len(parts) <= 1:
        return [Chapter(title="full", raw_text=full, order=0)]

    chapters: list[Chapter] = []
    for i, part in enumerate(parts):
        part = part.strip()
        if len(part) < _MIN_CHARS:
            continue
        title = part.splitlines()[0][:80] if part else f"section-{i}"
        chapters.append(Chapter(title=title, raw_text=part, order=i))
    return chapters
