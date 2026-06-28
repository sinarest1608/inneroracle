"""Qdrant vector index: collection setup, upsert, and hybrid (dense+BM25) search.

Single place that knows the Qdrant schema — used by both the indexer (ingest)
and retrieval (app). Named vectors: `dense` (BGE-M3) + `bm25` (sparse).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from qdrant_client import QdrantClient, models

from app.config import settings
from app.embeddings.bm25 import SparseVec
from app.retrieval.chunks import Chunk

DENSE = "dense"
SPARSE = "bm25"


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def ensure_collection(client: QdrantClient, dim: int, recreate: bool = False) -> None:
    name = settings.qdrant_collection
    if client.collection_exists(name):
        if not recreate:
            return
        client.delete_collection(name)
    client.create_collection(
        name,
        vectors_config={
            DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={SPARSE: models.SparseVectorParams()},
    )


def upsert_chunks(
    client: QdrantClient,
    items: Iterable[tuple[Chunk, Sequence[float], SparseVec]],
) -> int:
    points = [
        models.PointStruct(
            id=point_id(chunk.chunk_id),
            vector={
                DENSE: list(dense),
                SPARSE: models.SparseVector(indices=sv.indices, values=sv.values),
            },
            payload={
                "chunk_id": chunk.chunk_id,
                "verse_id": chunk.verse_id,
                "book": chunk.book,
                "ctype": chunk.ctype,
                "chapter": chunk.chapter,
                "verse_no": chunk.verse_no,
                "text": chunk.text,
            },
        )
        for chunk, dense, sv in items
    ]
    if points:
        client.upsert(settings.qdrant_collection, points)
    return len(points)


def hybrid_search(
    client: QdrantClient,
    dense_query: Sequence[float],
    sparse_query: SparseVec,
    limit: int = 20,
    prefetch_limit: int = 40,
    book: str | None = None,
) -> list[models.ScoredPoint]:
    """Dense + BM25 retrieval fused with Reciprocal Rank Fusion."""
    flt = (
        models.Filter(
            must=[models.FieldCondition(key="book", match=models.MatchValue(value=book))]
        )
        if book
        else None
    )
    result = client.query_points(
        settings.qdrant_collection,
        prefetch=[
            models.Prefetch(
                query=list(dense_query), using=DENSE, limit=prefetch_limit, filter=flt
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=sparse_query.indices, values=sparse_query.values
                ),
                using=SPARSE,
                limit=prefetch_limit,
                filter=flt,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=limit,
        with_payload=True,
    )
    return result.points
