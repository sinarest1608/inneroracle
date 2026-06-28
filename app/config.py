"""Application settings, loaded from environment / .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Claude API
    anthropic_api_key: str = ""
    answer_model: str = "claude-sonnet-4-6"
    verifier_model: str = "claude-haiku-4-5"
    expansion_model: str = "claude-haiku-4-5"
    ingest_model: str = "claude-sonnet-4-6"  # structured verse extraction

    # Embeddings: bge (BGE-M3 local) | stub (dependency-free, for dev without torch)
    embedder: str = "bge"
    # Reranker: bge (cross-encoder) | lexical (token overlap, dev) | none
    reranker: str = "bge"

    # Qdrant — empty url => embedded local mode (dev without Docker)
    qdrant_url: str = ""
    qdrant_local_path: str = "data/qdrant_local"
    qdrant_collection: str = "verses"

    # Storage
    sqlite_path: str = "data/verses.db"
    bm25_path: str = "data/bm25.json"

    # Simple shared-token auth gate (enforced once exposed)
    access_token: str = ""

    @property
    def qdrant_mode(self) -> str:
        return "remote" if self.qdrant_url else "embedded"


settings = Settings()
