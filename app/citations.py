"""Parse inline verse citations into verse_ids.

Handles single brackets ([BG 2.47], [AG-1.15]) and grouped ones the model
sometimes emits ([AG 15.20, AG 2.12], [BG 2.47; BG 3.1]).
"""

from __future__ import annotations

import re

_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_TOKEN_RE = re.compile(r"(BG|AG)[ \-]?(\d+\.[\w\-]+)")


def parse_citations(text: str) -> list[str]:
    """Return unique verse_ids (e.g. 'BG-2.47') cited in the text, in order."""
    seen: list[str] = []
    for group in _BRACKET_RE.findall(text):
        for book, num in _TOKEN_RE.findall(group):
            vid = f"{book}-{num}"
            if vid not in seen:
                seen.append(vid)
    return seen
