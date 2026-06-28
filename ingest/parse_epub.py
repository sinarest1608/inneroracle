"""EPUB → chapters. Pure-Python deps (ebooklib + BeautifulSoup/html.parser)."""

from __future__ import annotations

from ingest.sources import Chapter, clean_text

_MIN_CHARS = 200  # skip nav / title / copyright fragments


def parse_epub(path: str) -> list[Chapter]:
    import ebooklib
    from bs4 import BeautifulSoup
    from ebooklib import epub

    book = epub.read_epub(path)
    chapters: list[Chapter] = []
    order = 0
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        heading = soup.find(["h1", "h2", "h3"])
        title = (
            heading.get_text(" ", strip=True)
            if heading
            else (item.get_name() or f"section-{order}")
        )
        text = clean_text(soup.get_text("\n"))
        if len(text) < _MIN_CHARS:
            continue
        chapters.append(Chapter(title=title, raw_text=text, order=order))
        order += 1
    return chapters
