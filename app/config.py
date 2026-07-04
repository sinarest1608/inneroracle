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

    # Embeddings: fastembed (BGE-M3 via ONNX, default) | bge (torch) | stub
    embedder: str = "fastembed"
    # Reranker: none (rely on RRF fusion, default) | bge (cross-encoder) | lexical
    reranker: str = "none"

    # Qdrant — empty url => embedded local mode (dev without Docker)
    qdrant_url: str = ""
    qdrant_local_path: str = "data/qdrant_local"
    qdrant_collection: str = "verses"

    # Storage
    sqlite_path: str = "data/verses.db"
    bm25_path: str = "data/bm25.json"

    # Cloudflare Turnstile human-check on /chat (empty secret => gate off, dev).
    turnstile_site_key: str = ""   # public — served to the page
    turnstile_secret: str = ""     # private — server-side siteverify
    session_secret: str = ""       # HMAC key for the signed io_human cookie
    # Mark the human cookie Secure (HTTPS-only). Keep True in prod (behind
    # Cloudflare HTTPS); set False only for local http testing.
    cookie_secure: bool = True

    @property
    def qdrant_mode(self) -> str:
        return "remote" if self.qdrant_url else "embedded"


settings = Settings()
