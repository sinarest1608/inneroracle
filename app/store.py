"""SQLite store for canonical verses.

Written by the ingestion pipeline; read by retrieval (recombination) and the
/verse/{id} endpoint. This is the source of truth that the vector store points
back into via `verse_id`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

from app.models import Verse

_SCHEMA = """
CREATE TABLE IF NOT EXISTS verses (
    verse_id        TEXT PRIMARY KEY,
    book            TEXT NOT NULL,
    chapter         INTEGER NOT NULL,
    verse_no        TEXT NOT NULL,
    sanskrit        TEXT DEFAULT '',
    transliteration TEXT DEFAULT '',
    translation     TEXT DEFAULT '',
    commentary      TEXT DEFAULT '',
    source_edition  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_verses_book ON verses(book);
"""

_COLS = (
    "verse_id", "book", "chapter", "verse_no", "sanskrit",
    "transliteration", "translation", "commentary", "source_edition",
)


class VerseStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the app can read it from request threads.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def upsert(self, verses: Iterable[Verse]) -> int:
        rows = [tuple(getattr(v, c) for c in _COLS) for v in verses]
        self._conn.executemany(
            f"INSERT INTO verses ({', '.join(_COLS)}) "
            f"VALUES ({', '.join('?' for _ in _COLS)}) "
            "ON CONFLICT(verse_id) DO UPDATE SET "
            + ", ".join(f"{c}=excluded.{c}" for c in _COLS if c != "verse_id"),
            rows,
        )
        self._conn.commit()
        return len(rows)

    def get(self, verse_id: str) -> Verse | None:
        row = self._conn.execute(
            "SELECT * FROM verses WHERE verse_id = ?", (verse_id,)
        ).fetchone()
        return Verse(**dict(row)) if row else None

    def get_many(self, verse_ids: list[str]) -> dict[str, Verse]:
        if not verse_ids:
            return {}
        placeholders = ", ".join("?" for _ in verse_ids)
        cur = self._conn.execute(
            f"SELECT * FROM verses WHERE verse_id IN ({placeholders})", verse_ids
        )
        return {r["verse_id"]: Verse(**dict(r)) for r in cur.fetchall()}

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM verses").fetchone()[0]

    def count_by_book(self) -> dict[str, int]:
        cur = self._conn.execute(
            "SELECT book, COUNT(*) AS c FROM verses GROUP BY book"
        )
        return {r["book"]: r["c"] for r in cur.fetchall()}

    def iter_all(self) -> Iterator[Verse]:
        cur = self._conn.execute(
            "SELECT * FROM verses ORDER BY book, chapter, verse_no"
        )
        for row in cur:
            yield Verse(**dict(row))

    def close(self) -> None:
        self._conn.close()
