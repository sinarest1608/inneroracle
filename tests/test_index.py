"""Offline tests for chunking, BM25, and the Qdrant hybrid index (embedded mode)."""

from __future__ import annotations

from app.embeddings.bm25 import BM25Encoder
from app.embeddings.stub import StubEmbedder
from app.models import Verse
from app.retrieval.chunks import COMMENTARY, VERSE, verse_chunks


def test_verse_chunks_separates_sources():
    v = Verse(
        verse_id="BG-2.47", book="bhagavad_gita", chapter=2, verse_no="47",
        transliteration="karmany evadhikaras te",
        translation="You have a right to action alone, never to its fruits.",
        commentary="Para one about duty.\n\nPara two about detachment.",
    )
    chunks = verse_chunks(v, commentary_max_chars=40)
    kinds = [c.ctype for c in chunks]
    assert kinds[0] == VERSE
    assert COMMENTARY in kinds
    # verse chunk merges transliteration + translation
    assert "karmany" in chunks[0].text and "right to action" in chunks[0].text
    # long commentary split into multiple chunks, all linked to the verse
    assert all(c.verse_id == "BG-2.47" for c in chunks)
    assert sum(k == COMMENTARY for k in kinds) >= 2


def test_verse_chunks_handles_missing_fields():
    only_translation = Verse(verse_id="AG-1.1", book="ashtavakra_gita", chapter=1,
                             verse_no="1", translation="How is knowledge acquired?")
    assert [c.ctype for c in verse_chunks(only_translation)] == [VERSE]

    only_commentary = Verse(verse_id="AG-1.2", book="ashtavakra_gita", chapter=1,
                            verse_no="2", commentary="A note with no verse text.")
    assert [c.ctype for c in verse_chunks(only_commentary)] == [COMMENTARY]


def test_bm25_ranks_matching_doc_higher():
    corpus = [
        "you have a right to action alone never to its fruits",
        "established in yoga perform action abandoning attachment",
        "the self is unborn eternal and indestructible",
    ]
    enc = BM25Encoder().fit(corpus)
    # Use content words (not stopwords); in a tiny corpus a rare stopword like
    # "to" gets a high IDF and would skew ranking — a non-issue at real scale.
    q = enc.encode_query("attachment action")

    def score(doc: str) -> float:
        sv = enc.encode_document(doc)
        weights = dict(zip(sv.indices, sv.values))
        return sum(weights.get(i, 0.0) * v for i, v in zip(q.indices, q.values))

    scores = [score(d) for d in corpus]
    assert scores[1] == max(scores)  # the "attachment ... action" doc wins
    assert scores[2] == 0.0          # no overlap


def test_hybrid_index_and_search(tmp_path):
    from qdrant_client import QdrantClient

    from app.retrieval.vector_index import hybrid_search
    from app.store import VerseStore
    from ingest.index import build_index

    store = VerseStore(str(tmp_path / "verses.db"))
    store.upsert([
        Verse(verse_id="BG-2.47", book="bhagavad_gita", chapter=2, verse_no="47",
              translation="You have a right to action alone, never to its fruits."),
        Verse(verse_id="BG-2.48", book="bhagavad_gita", chapter=2, verse_no="48",
              translation="Established in yoga, perform action abandoning attachment."),
        Verse(verse_id="AG-1.1", book="ashtavakra_gita", chapter=1, verse_no="1",
              translation="The self is the witness, ever free and unbound."),
    ])

    client = QdrantClient(path=str(tmp_path / "qdrant"))
    embedder = StubEmbedder()
    bm25_path = str(tmp_path / "bm25.json")
    n = build_index(store=store, embedder=embedder, qdrant=client,
                    bm25_path=bm25_path, recreate=True)
    assert n == 3  # three verse chunks (no commentary)

    bm25 = BM25Encoder.load(bm25_path)
    q = "attachment in action"
    points = hybrid_search(
        client,
        dense_query=embedder.embed_query(q),
        sparse_query=bm25.encode_query(q),
        limit=3,
    )
    assert points, "hybrid search returned no points"
    top_verse_ids = [p.payload["verse_id"] for p in points]
    assert "BG-2.48" in top_verse_ids  # the attachment/action verse is retrieved

    # book filter restricts results
    filtered = hybrid_search(
        client, embedder.embed_query(q), bm25.encode_query(q),
        limit=5, book="ashtavakra_gita",
    )
    assert all(p.payload["book"] == "ashtavakra_gita" for p in filtered)
