"""Ingestion orchestration: source file → chapters → verses → SQLite."""

from __future__ import annotations

from app.config import settings
from app.store import VerseStore
from ingest.extract_verses import extract_chapter, to_verses
from ingest.sources import chapter_from_title, load_chapters


def run(
    path: str,
    book: str,
    source_edition: str,
    out_db: str | None = None,
    limit_chapters: int | None = None,
    sections: list[int] | None = None,
    chapter_from_title_heading: bool = False,
    dry_run: bool = False,
) -> None:
    chapters = load_chapters(path)
    if sections:
        wanted = set(sections)
        chapters = [c for c in chapters if c.order in wanted]
    if limit_chapters:
        chapters = chapters[:limit_chapters]
    print(f"Parsed {len(chapters)} chapter section(s) from {path}")

    if dry_run:
        for c in chapters:
            print(f"  [{c.order:>3}] {len(c.raw_text):>7} chars  {c.title[:60]!r}")
        print("(dry-run: no LLM calls made)")
        return

    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set — add it to .env before extracting.")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    store = VerseStore(out_db or settings.sqlite_path)
    total = 0
    current_chapter: int | None = None
    for c in chapters:
        force_chapter = None
        if chapter_from_title_heading:
            current_chapter = chapter_from_title(c.title) or current_chapter
            force_chapter = current_chapter
        extracts = extract_chapter(c.raw_text, client, settings.ingest_model)
        verses = to_verses(extracts, book, source_edition, force_chapter=force_chapter)
        n = store.upsert(verses)
        total += n
        ch = f"ch{force_chapter}" if force_chapter else c.title[:30]
        print(f"  section {c.order} [{ch}]: {n} verses")
    print(f"Done. {total} verse rows written. Counts by book: {store.count_by_book()}")
    store.close()
