"""InnerOracle FastAPI app: /health, /chat (SSE), /verse/{id}."""

from __future__ import annotations

import json
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.models import Verse
from app.qdrant import get_qdrant
from app.store import VerseStore

app = FastAPI(title="InnerOracle", version="0.1.0")


@lru_cache(maxsize=1)
def _store() -> VerseStore:
    return VerseStore(settings.sqlite_path)


@lru_cache(maxsize=1)
def _services():
    """Lazily build the retrieval + LLM stack (heavy: embedder, index, clients)."""
    from app.llm.claude import ClaudeProvider
    from app.retrieval.search import RetrievalService
    from app.verify import HaikuVerifier

    return RetrievalService.from_settings(), ClaudeProvider(), HaikuVerifier()


class ChatRequest(BaseModel):
    question: str
    per_book: int = 3


@app.get("/health")
def health() -> dict:
    result: dict = {
        "status": "ok",
        "service": "inneroracle",
        "answer_model": settings.answer_model,
        "qdrant_mode": settings.qdrant_mode,
    }
    try:
        get_qdrant().get_collections()
        result["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "degraded"
        result["qdrant"] = f"error: {exc}"
    return result


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    from app.chat import stream_answer

    retrieval, provider, verifier = _services()

    async def gen():
        async for event in stream_answer(
            req.question,
            retrieval=retrieval,
            provider=provider,
            verifier=verifier,
            per_book=req.per_book,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/verse/{verse_id}")
def verse(verse_id: str) -> Verse:
    """Full verse record (Devanagari + transliteration + translation + commentary)."""
    v = _store().get(verse_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"verse {verse_id} not found")
    return v
