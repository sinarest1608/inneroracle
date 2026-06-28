"""Retrieval service: hybrid search → recombine into full verses → rerank.

This is where "separate sources, combined at answer time" happens: granular
verse/commentary chunks are retrieved, deduped by verse_id, resolved to full
canonical verse records, then reranked by a cross-encoder.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.embeddings.base import DenseEmbedder
from app.embeddings.bm25 import BM25Encoder
from app.models import Verse
from app.retrieval.reranker import Reranker
from app.retrieval.vector_index import hybrid_search


@dataclass
class RetrievedVerse:
    verse: Verse
    score: float


def verse_document(v: Verse) -> str:
    """The text a reranker/LLM sees for a verse — translation + commentary."""
    return "\n".join(t for t in (v.translation, v.commentary) if t).strip() or v.verse_id


class RetrievalService:
    def __init__(self, store, embedder: DenseEmbedder, qdrant, bm25: BM25Encoder,
                 reranker: Reranker):
        self.store = store
        self.embedder = embedder
        self.qdrant = qdrant
        self.bm25 = bm25
        self.reranker = reranker

    @classmethod
    def from_settings(cls) -> "RetrievalService":
        from app.embeddings import get_embedder
        from app.qdrant import get_qdrant
        from app.retrieval.reranker import get_reranker
        from app.store import VerseStore

        return cls(
            store=VerseStore(settings.sqlite_path),
            embedder=get_embedder(),
            qdrant=get_qdrant(),
            bm25=BM25Encoder.load(settings.bm25_path),
            reranker=get_reranker(),
        )

    def retrieve(
        self, query: str, *, top_k: int = 6, candidates: int = 20,
        book: str | None = None,
    ) -> list[RetrievedVerse]:
        points = hybrid_search(
            self.qdrant,
            dense_query=self.embedder.embed_query(query),
            sparse_query=self.bm25.encode_query(query),
            limit=candidates,
            book=book,
        )

        # Recombine: dedup chunk hits → unique verse_ids (keep first-seen order).
        verse_ids: list[str] = []
        for p in points:
            vid = p.payload["verse_id"]
            if vid not in verse_ids:
                verse_ids.append(vid)

        verses_by_id = self.store.get_many(verse_ids)
        ordered = [verses_by_id[v] for v in verse_ids if v in verses_by_id]
        if not ordered:
            return []

        scores = self.reranker.rerank(query, [verse_document(v) for v in ordered])
        ranked = sorted(zip(ordered, scores), key=lambda t: t[1], reverse=True)
        return [RetrievedVerse(verse=v, score=s) for v, s in ranked[:top_k]]
