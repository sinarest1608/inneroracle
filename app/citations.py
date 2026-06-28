"""Parse inline verse citations like [BG 2.47] / [AG-1.15] into verse_ids."""

from __future__ import annotations

import re

_CITATION_RE = re.compile(r"\[(BG|AG)[ \-]?(\d+\.[\w\-]+)\]")


def parse_citations(text: str) -> list[str]:
    """Return unique verse_ids (e.g. 'BG-2.47') cited in the text, in order."""
    seen: list[str] = []
    for book, num in _CITATION_RE.findall(text):
        vid = f"{book}-{num}"
        if vid not in seen:
            seen.append(vid)
    return seen
