"""Rerankers: cross-encoder (BGE) for production, lightweight stubs for dev/tests."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.config import settings
from app.embeddings.bm25 import tokenize


@runtime_checkable
class Reranker(Protocol):
    def rerank(self, query: str, docs: list[str]) -> list[float]:
        """Return a relevance score per doc (higher = more relevant)."""
        ...


class NoopReranker:
    """Preserve incoming (fusion) order — descending scores by position."""

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        return [float(len(docs) - i) for i in range(len(docs))]


class LexicalReranker:
    """Deterministic token-overlap reranker — no heavy deps, used in tests/dev."""

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        q = set(tokenize(query))
        return [float(len(q & set(tokenize(d)))) for d in docs]


class BGEReranker:
    """Cross-encoder reranker (bge-reranker-v2-m3).

    Uses sentence-transformers' CrossEncoder (compatible with current
    transformers) rather than FlagEmbedding's FlagReranker, whose slow-tokenizer
    path breaks on transformers 5.x. Same model weights. Imported lazily.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self._model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)
        return self._model

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        scores = self._load().predict([(query, d) for d in docs])
        return [float(s) for s in scores]


def get_reranker() -> Reranker:
    choice = settings.reranker
    if choice == "bge":
        return BGEReranker()
    if choice in ("lexical", "stub"):
        return LexicalReranker()
    return NoopReranker()
