"""Dense embedding provider interface (swappable: BGE-M3 local, Voyage API, …)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DenseEmbedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        ...
