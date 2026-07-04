"""InnerOracle FastAPI app: /health, /chat (SSE), /verse/{id}."""

from __future__ import annotations

import json
import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.auth import HumanGateMiddleware
from app.config import settings
from app.models import Verse
from app.qdrant import get_qdrant
from app.store import VerseStore
from app.turnstile import COOKIE, sign_cookie, verify_token

app = FastAPI(title="InnerOracle", version="0.1.0")
app.add_middleware(HumanGateMiddleware, enabled=bool(settings.turnstile_secret))


@lru_cache(maxsize=1)
def _store() -> VerseStore:
    return VerseStore(settings.sqlite_path)


@lru_cache(maxsize=1)
def _services():
    """Lazily build the retrieval + LLM stack (heavy: embedder, index, clients)."""
    from app.llm.claude import ClaudeProvider
    from app.llm.condense import HaikuCondenser
    from app.retrieval.search import RetrievalService
    from app.verify import HaikuVerifier

    return (
        RetrievalService.from_settings(),
        ClaudeProvider(),
        HaikuVerifier(),
        HaikuCondenser(),
    )


class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    question: str
    history: list[Message] = []
    book: str | None = None  # "bhagavad_gita" | "ashtavakra_gita" | None (both)
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

    retrieval, provider, verifier, condenser = _services()
    history = [m.model_dump() for m in req.history]

    async def gen():
        async for event in stream_answer(
            req.question,
            retrieval=retrieval,
            provider=provider,
            verifier=verifier,
            history=history,
            condenser=condenser,
            book=req.book,
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


@app.get("/pubconfig")
def pubconfig() -> dict:
    """Public front-end config (Turnstile site key is not a secret)."""
    return {"turnstile_site_key": settings.turnstile_site_key}


class VerifyRequest(BaseModel):
    token: str = ""


@app.post("/verify-human")
async def verify_human(req: VerifyRequest, request: Request):
    """Exchange a Turnstile token for a short-lived signed human cookie."""
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else None)
    if not await verify_token(req.token, ip):
        raise HTTPException(status_code=403, detail="verification failed")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE, sign_cookie(), max_age=12 * 3600,
        httponly=True, samesite="lax", secure=settings.cookie_secure,
    )
    return resp


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


# Serve the chat web UI (mounted last so it doesn't shadow the API routes).
if os.path.isdir("web"):
    app.mount("/ui", StaticFiles(directory="web", html=True), name="ui")
