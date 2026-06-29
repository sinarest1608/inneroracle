"""Embedder selection. EMBEDDER=fastembed (default) | bge | stub."""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.embeddings.base import DenseEmbedder


@lru_cache(maxsize=1)
def get_embedder() -> DenseEmbedder:
    if settings.embedder == "stub":
        from app.embeddings.stub import StubEmbedder

        return StubEmbedder()
    if settings.embedder == "bge":
        from app.embeddings.bge import BGEM3Embedder

        return BGEM3Embedder()
    from app.embeddings.fastembed import FastEmbedEmbedder

    return FastEmbedEmbedder()
