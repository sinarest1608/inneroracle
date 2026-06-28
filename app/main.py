"""InnerOracle FastAPI app.

Milestone 1: scaffold + /health. Later milestones add /chat and /verse/{id}.
"""

from fastapi import FastAPI

from app.config import settings
from app.qdrant import get_qdrant

app = FastAPI(title="InnerOracle", version="0.1.0")


@app.get("/health")
def health() -> dict:
    """Liveness + dependency check (currently: Qdrant reachability)."""
    result: dict = {
        "status": "ok",
        "service": "inneroracle",
        "answer_model": settings.answer_model,
        "qdrant_mode": settings.qdrant_mode,
    }
    try:
        get_qdrant().get_collections()
        result["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001 - surface any connectivity issue
        result["status"] = "degraded"
        result["qdrant"] = f"error: {exc}"
    return result
