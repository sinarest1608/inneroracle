"""Source loading: dispatch a file to the right parser and return chapters.

EPUB is preferred (clean structured HTML, Devanagari survives). PDF is a noisy
fallback. Parser libs are imported lazily so this module imports without them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Chapter:
    title: str
    raw_text: str
    order: int


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


_CHAPTER_TITLE_RE = re.compile(r"chapter[-\s]*(\d+)", re.IGNORECASE)


def chapter_from_title(title: str) -> int | None:
    """Extract a chapter number from a section heading like 'Chapter-2'.

    Returns None for front matter or untitled continuation sections (the caller
    carries the previous chapter forward for those).
    """
    m = _CHAPTER_TITLE_RE.search(title or "")
    return int(m.group(1)) if m else None


def load_chapters(path: str) -> list[Chapter]:
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        from ingest.parse_epub import parse_epub

        return parse_epub(path)
    if suffix == ".pdf":
        from ingest.parse_pdf import parse_pdf

        return parse_pdf(path)
    raise ValueError(f"Unsupported source type {suffix!r} (use .epub or .pdf)")
