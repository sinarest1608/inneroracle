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

    # Open access — abuse protection is at the Cloudflare edge (Bot Fight Mode +
    # rate limiting on /chat), not in the app.

    # Daily verse push notifications (off unless explicitly enabled; see
    # app/push_config.py for the fail-closed startup checks).
    push_enabled: bool = False
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""
    push_admin_token: str = ""
    push_hmac_key: str = ""
    push_db_path: str = "state/push.db"
    push_daily_times: str = "08:30,17:30"  # local send times, comma-separated
    push_timezone: str = "America/Los_Angeles"
    push_rotation_start: str = "2026-11-01"
    push_rotation_seed: str = ""
    push_max_devices: int = 5000
    push_subscribe_rate: int = 5        # per IPv4 address or IPv6 /64, per hour
    push_subscribe_rate_wide: int = 30  # per IPv6 /48, per hour
    push_signup_budget_per_hour: int = 100
    push_signup_ip_days: int = 7
    push_log_retention_days: int = 90
    quote_ai_enabled: bool = True
    quote_model: str = "claude-haiku-5-5"

    @property
    def qdrant_mode(self) -> str:
        return "remote" if self.qdrant_url else "embedded"


settings = Settings()
