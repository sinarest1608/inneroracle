"""CLI: python -m ingest <file> --book bhagavad_gita [--dry-run]"""

from __future__ import annotations

import argparse

from app.models import BOOKS
from ingest.pipeline import run


def main() -> None:
    p = argparse.ArgumentParser(prog="ingest", description="Ingest a Gita source file.")
    p.add_argument("path", help="Path to a .epub or .pdf in data/sources/")
    p.add_argument("--book", required=True, choices=list(BOOKS))
    p.add_argument("--edition", default="", help="Edition label stored on each verse")
    p.add_argument("--out", default=None, help="Output SQLite path (default: settings)")
    p.add_argument("--limit-chapters", type=int, default=None)
    p.add_argument("--dry-run", action="store_true", help="Parse only; no LLM calls")
    a = p.parse_args()
    run(
        path=a.path,
        book=a.book,
        source_edition=a.edition or a.path,
        out_db=a.out,
        limit_chapters=a.limit_chapters,
        dry_run=a.dry_run,
    )


if __name__ == "__main__":
    main()
