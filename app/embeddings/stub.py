"""Deterministic stub dense embedder.

Lets the full app + index path run/test without torch (e.g. on Python 3.14
where FlagEmbedding wheels may be unavailable). Hash-based, low-dimensional —
not semantically meaningful, but stable and dependency-free. BM25 carries the
keyword signal in this mode.
"""

from __future__ import annotations

import hashlib
import math

from app.embeddings.bm25 import tokenize


class StubEmbedder:
    def __init__(self, dim: int = 64):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in tokenize(text) or [text]:
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
