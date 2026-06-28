"""Ingestion orchestration: source file → chapters → verses → SQLite."""

from __future__ import annotations

from app.config import settings
from app.store import VerseStore
from ingest.extract_verses import extract_chapter, to_verses
from ingest.sources import load_chapters


def run(
    path: str,
    book: str,
    source_edition: str,
    out_db: str | None = None,
    limit_chapters: int | None = None,
    sections: list[int] | None = None,
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
    for c in chapters:
        extracts = extract_chapter(c.raw_text, client, settings.ingest_model)
        verses = to_verses(extracts, book, source_edition)
        n = store.upsert(verses)
        total += n
        print(f"  chapter {c.order} {c.title[:40]!r}: {n} verses")
    print(f"Done. {total} verse rows written. Counts by book: {store.count_by_book()}")
    store.close()
