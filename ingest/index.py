"""Build the Qdrant hybrid index from the canonical verses in SQLite.

Usage: python -m ingest.index   (after verses.db has been populated)
"""

from __future__ import annotations

from app.config import settings
from app.embeddings import get_embedder
from app.embeddings.base import DenseEmbedder
from app.embeddings.bm25 import BM25Encoder
from app.qdrant import get_qdrant
from app.retrieval.chunks import verse_chunks
from app.retrieval.vector_index import ensure_collection, upsert_chunks
from app.store import VerseStore


def build_index(
    store: VerseStore | None = None,
    embedder: DenseEmbedder | None = None,
    qdrant=None,
    bm25_path: str | None = None,
    recreate: bool = True,
    batch: int = 64,
) -> int:
    store = store or VerseStore(settings.sqlite_path)
    embedder = embedder or get_embedder()
    qdrant = qdrant or get_qdrant()
    bm25_path = bm25_path or settings.bm25_path

    chunks = [c for v in store.iter_all() for c in verse_chunks(v)]
    if not chunks:
        print("No chunks found — is verses.db populated? Run `python -m ingest` first.")
        return 0

    # Fit BM25 over the whole corpus, then persist for the serving side.
    bm25 = BM25Encoder().fit([c.text for c in chunks])
    bm25.save(bm25_path)

    ensure_collection(qdrant, embedder.dim, recreate=recreate)

    total = 0
    for i in range(0, len(chunks), batch):
        part = chunks[i : i + batch]
        dvs = embedder.embed([c.text for c in part])
        items = [(c, dv, bm25.encode_document(c.text)) for c, dv in zip(part, dvs)]
        total += upsert_chunks(qdrant, items)

    print(
        f"Indexed {total} chunks into '{settings.qdrant_collection}' "
        f"(dense dim={embedder.dim}, bm25 vocab={len(bm25.vocab)})."
    )
    return total


if __name__ == "__main__":
    build_index()
