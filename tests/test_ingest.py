"""Offline tests for the ingestion pipeline (no Claude API calls)."""

from __future__ import annotations

import pytest

from app.models import Verse, make_verse_id
from app.store import VerseStore
from ingest.extract_verses import (
    ChapterVerses,
    VerseExtract,
    chunk_text,
    extract_chapter,
    to_verses,
)


def test_make_verse_id():
    assert make_verse_id("bhagavad_gita", 2, "47") == "BG-2.47"
    assert make_verse_id("ashtavakra_gita", 1, "12") == "AG-1.12"
    assert make_verse_id("bhagavad_gita", 1, "42-43") == "BG-1.42-43"


def test_chunk_text_bounds_and_preserves():
    text = "\n\n".join(f"paragraph number {i} " + "x" * 500 for i in range(20))
    chunks = chunk_text(text, max_chars=2000)
    assert len(chunks) > 1
    assert all(len(c) <= 2000 + 600 for c in chunks)  # one para may overflow a window
    # every paragraph survives somewhere
    for i in range(20):
        assert any(f"paragraph number {i} " in c for c in chunks)


def test_to_verses_assigns_id_and_merges_windows():
    extracts = [
        VerseExtract(chapter=2, verse_no="47", translation="short"),
        # same verse from an overlapping window, with fuller fields
        VerseExtract(
            chapter=2,
            verse_no=" 47 ",
            translation="a much longer translation of the verse",
            commentary="purport text",
            sanskrit="कर्मण्येवाधिकारस्ते",
        ),
        VerseExtract(chapter=2, verse_no="48", translation="yoga is equanimity"),
    ]
    verses = to_verses(extracts, "bhagavad_gita", "Test Edition")
    by_id = {v.verse_id: v for v in verses}
    assert set(by_id) == {"BG-2.47", "BG-2.48"}
    merged = by_id["BG-2.47"]
    assert merged.translation == "a much longer translation of the verse"  # longer wins
    assert merged.commentary == "purport text"
    assert merged.sanskrit == "कर्मण्येवाधिकारस्ते"
    assert merged.book == "bhagavad_gita"
    assert merged.source_edition == "Test Edition"


def test_chapter_verse_normalization():
    # Ashtavakra-style "C.V" numbering -> split into chapter + verse.
    extracts = [
        VerseExtract(chapter=1, verse_no="1.1", translation="Janaka asks."),
        VerseExtract(chapter=1, verse_no="2.4", translation="On the self."),
        VerseExtract(chapter=1, verse_no="20.1", translation="Final chapter."),
    ]
    by_id = {v.verse_id: v for v in to_verses(extracts, "ashtavakra_gita", "JR")}
    assert set(by_id) == {"AG-1.1", "AG-2.4", "AG-20.1"}
    assert by_id["AG-2.4"].chapter == 2 and by_id["AG-2.4"].verse_no == "4"

    # BG-style numbering is untouched (no leading "<int>.").
    bg = to_verses(
        [VerseExtract(chapter=2, verse_no="47", translation="x"),
         VerseExtract(chapter=1, verse_no="42-43", translation="y")],
        "bhagavad_gita", "AsItIs",
    )
    bg_ids = {v.verse_id for v in bg}
    assert bg_ids == {"BG-2.47", "BG-1.42-43"}


def test_chapter_from_title():
    from ingest.sources import chapter_from_title
    assert chapter_from_title("Chapter-2") == 2
    assert chapter_from_title("Chapter 18") == 18
    assert chapter_from_title("CHAPTER-7") == 7
    assert chapter_from_title("Introduction") is None
    assert chapter_from_title("") is None
    assert chapter_from_title("text/part0015.html") is None  # continuation section


def test_force_chapter_overrides_model():
    # Model mislabels the chapter (says 1); force it to 2 from the section heading.
    extracts = [
        VerseExtract(chapter=1, verse_no="3", translation="a"),     # wrong chapter
        VerseExtract(chapter=1, verse_no="1.4", translation="b"),   # wrong, C.V form
    ]
    out = to_verses(extracts, "bhagavad_gita", "AsItIs", force_chapter=2)
    ids = {v.verse_id for v in out}
    assert ids == {"BG-2.3", "BG-2.4"}
    assert all(v.chapter == 2 for v in out)


class _FakeMessages:
    def parse(self, **kwargs):
        class _Resp:
            parsed_output = ChapterVerses(
                verses=[VerseExtract(chapter=1, verse_no="1", translation="hi")]
            )

        return _Resp()


class _FakeClient:
    messages = _FakeMessages()


def test_extract_chapter_wiring_without_api():
    # Two windows -> two parse calls -> aggregated verses.
    text = "\n\n".join("y" * 6000 for _ in range(2))
    out = extract_chapter(text, _FakeClient(), "claude-sonnet-4-6")
    assert len(out) == 2
    assert all(isinstance(v, VerseExtract) for v in out)


def test_verse_store_roundtrip(tmp_path):
    db = str(tmp_path / "verses.db")
    store = VerseStore(db)
    store.upsert(
        [
            Verse(verse_id="BG-2.47", book="bhagavad_gita", chapter=2, verse_no="47",
                  translation="orig", sanskrit="श्लोक"),
            Verse(verse_id="AG-1.1", book="ashtavakra_gita", chapter=1, verse_no="1",
                  translation="freedom"),
        ]
    )
    assert store.count() == 2
    assert store.count_by_book() == {"ashtavakra_gita": 1, "bhagavad_gita": 1}

    got = store.get("BG-2.47")
    assert got is not None and got.sanskrit == "श्लोक"
    assert store.get("missing") is None
    assert set(store.get_many(["BG-2.47", "AG-1.1"]).keys()) == {"BG-2.47", "AG-1.1"}

    # upsert conflict updates in place (no duplicate row)
    store.upsert([Verse(verse_id="BG-2.47", book="bhagavad_gita", chapter=2,
                        verse_no="47", translation="updated")])
    assert store.count() == 2
    assert store.get("BG-2.47").translation == "updated"

    assert [v.verse_id for v in store.iter_all()] == ["AG-1.1", "BG-2.47"]
    store.close()


def test_parse_epub_on_synthetic_book(tmp_path):
    pytest.importorskip("ebooklib")
    pytest.importorskip("bs4")
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("test-id")
    book.set_title("Test Gita")
    book.set_language("en")

    ch = epub.EpubHtml(title="Chapter 2", file_name="ch2.xhtml", lang="en")
    ch.content = (
        "<h1>Chapter 2</h1>"
        "<p>2.47 You have a right to action alone, never to its fruits. "
        "Let not the fruits of action be your motive, nor let your attachment "
        "be to inaction.</p>"
        "<p>2.48 Established in yoga, perform actions abandoning attachment, "
        "remaining even-minded in success and failure. Such equanimity is yoga.</p>"
    )
    book.add_item(ch)
    book.toc = (ch,)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", ch]

    path = str(tmp_path / "test.epub")
    epub.write_epub(path, book)

    from ingest.parse_epub import parse_epub

    chapters = parse_epub(path)
    assert len(chapters) >= 1
    joined = "\n".join(c.raw_text for c in chapters)
    assert "right to action alone" in joined
    assert "equanimity is yoga" in joined
