"""End-to-end retrieval test: hybrid search → recombine → rerank (embedded Qdrant)."""

from __future__ import annotations

import pytest

from app.embeddings.bm25 import BM25Encoder
from app.embeddings.stub import StubEmbedder
from app.models import Verse
from app.retrieval.reranker import LexicalReranker
from app.retrieval.search import RetrievalService


@pytest.fixture
def service(tmp_path):
    from qdrant_client import QdrantClient

    from app.store import VerseStore
    from ingest.index import build_index

    store = VerseStore(str(tmp_path / "verses.db"))
    store.upsert([
        Verse(verse_id="BG-2.47", book="bhagavad_gita", chapter=2, verse_no="47",
              transliteration="karmany evadhikaras te",
              translation="You have a right to action alone, never to its fruits.",
              commentary="On duty without attachment to results.\n\n"
                         "Krishna instructs Arjuna to act without craving outcomes."),
        Verse(verse_id="BG-2.48", book="bhagavad_gita", chapter=2, verse_no="48",
              translation="Established in yoga, perform action abandoning attachment.",
              commentary="Equanimity in success and failure is called yoga."),
        Verse(verse_id="AG-1.1", book="ashtavakra_gita", chapter=1, verse_no="1",
              translation="The self is the witness, ever free and unbound."),
    ])

    client = QdrantClient(path=str(tmp_path / "qdrant"))
    embedder = StubEmbedder()
    bm25_path = str(tmp_path / "bm25.json")
    build_index(store=store, embedder=embedder, qdrant=client,
                bm25_path=bm25_path, recreate=True)

    return RetrievalService(
        store=store, embedder=embedder, qdrant=client,
        bm25=BM25Encoder.load(bm25_path), reranker=LexicalReranker(),
    )


def test_recombines_chunks_into_full_verses(service):
    results = service.retrieve("attachment and action", top_k=3)
    assert results, "no results"

    # Each result is a full canonical Verse (commentary recombined from chunks),
    # and verses are deduped even though commentary produced multiple chunks.
    ids = [r.verse.verse_id for r in results]
    assert len(ids) == len(set(ids)), "verses should be deduped"
    bg247 = next((r.verse for r in results if r.verse.verse_id == "BG-2.47"), None)
    assert bg247 is not None
    assert "without attachment" in bg247.commentary
    assert bg247.transliteration == "karmany evadhikaras te"


def test_rerank_orders_by_relevance(service):
    results = service.retrieve("abandoning attachment in action yoga", top_k=3)
    # BG-2.48 has the most query-term overlap (yoga, action, abandoning, attachment).
    assert results[0].verse.verse_id == "BG-2.48"


def test_top_k_and_book_filter(service):
    assert len(service.retrieve("action", top_k=1)) == 1
    only_ag = service.retrieve("self witness free", book="ashtavakra_gita", top_k=5)
    assert only_ag and all(r.verse.book == "ashtavakra_gita" for r in only_ag)
