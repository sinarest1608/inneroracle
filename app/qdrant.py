"""Qdrant client factory.

Returns a remote client when QDRANT_URL is set (docker-compose / VPS),
otherwise an embedded local-mode client so the app runs without Docker.
"""

from functools import lru_cache

from qdrant_client import QdrantClient

from app.config import settings


@lru_cache(maxsize=1)
def get_qdrant() -> QdrantClient:
    if settings.qdrant_url:
        return QdrantClient(url=settings.qdrant_url)
    return QdrantClient(path=settings.qdrant_local_path)
