"""InnerOracle FastAPI app: /health, /chat (SSE), /verse/{id}."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.http_extras import PathHeaders, install_access_log_filter
from app.models import Verse
from app.qdrant import get_qdrant
from app.store import VerseStore


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Daily-verse push: refuse to start on weak/missing secrets (fail closed),
    then run the scheduler. Nothing push-related runs when PUSH_ENABLED is off."""
    sched = None
    if settings.push_enabled:
        from app.push_config import validate_push_settings
        from app.push_scheduler import PushScheduler

        problems = validate_push_settings(settings)
        if problems:
            raise RuntimeError("push config invalid: " + "; ".join(problems))
        install_access_log_filter()
        sched = PushScheduler(_store())
        await sched.startup()
    yield
    if sched is not None:
        sched.shutdown()


# Open access — no app-level auth. Abuse protection for /chat is handled at the
# Cloudflare edge (Bot Fight Mode + a rate-limiting rule) plus an Anthropic spend cap.
# Request bounds below cap the worst-case cost of any single request.
app = FastAPI(title="InnerOracle", version="0.1.0", lifespan=lifespan)
app.add_middleware(PathHeaders)  # raw ASGI: never buffers the /chat stream

# Root handler so app loggers (inneroracle.*, incl. token-usage lines) actually
# emit under uvicorn, whose default config only wires up its own loggers.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

logger = logging.getLogger("inneroracle")

# Server-side history bounds: rate limiting caps requests/minute but not
# cost/request — without these one request could carry megabytes of "history"
# straight into three paid LLM calls. Truncate rather than reject so long
# legitimate conversations keep working.
HISTORY_MAX_MESSAGES = 20
HISTORY_MAX_CHARS = 8000  # > longest real answer (~1400 tokens), so only abuse is cut


@lru_cache(maxsize=1)
def _store() -> VerseStore:
    return VerseStore(settings.sqlite_path, readonly=True)


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
    role: Literal["user", "assistant"]
    content: str


VerseId = Annotated[str, Field(pattern=r"^(BG|AG)-\d{1,2}\.\d{1,3}(-\d{1,3})?$")]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Message] = []
    book: Literal["bhagavad_gita", "ashtavakra_gita"] | None = None  # None = both
    per_book: int = Field(default=3, ge=1, le=10)
    # Verses the user explicitly asked about (from a verse card); always in context.
    verse_ids: list[VerseId] = Field(default=[], max_length=3)


@app.get("/health")
def health() -> dict:
    """Public liveness check. Deliberately bare — model names, storage mode and
    exception details stay in the logs, not on an unauthenticated endpoint."""
    try:
        get_qdrant().get_collections()
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        logger.exception("health: qdrant check failed")
        return {"status": "degraded"}


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    from app.chat import stream_answer

    retrieval, provider, verifier, condenser = _services()
    history = [
        {"role": m.role, "content": m.content[:HISTORY_MAX_CHARS]}
        for m in req.history[-HISTORY_MAX_MESSAGES:]
    ]

    async def gen():
        try:
            async for event in stream_answer(
                req.question,
                retrieval=retrieval,
                provider=provider,
                verifier=verifier,
                history=history,
                condenser=condenser,
                book=req.book,
                per_book=req.per_book,
                pinned_ids=req.verse_ids,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception:  # noqa: BLE001 — detail goes to logs, not to the client
            logger.exception("chat: stream failed")
            err = {
                "type": "error",
                "message": "Something went wrong while consulting the texts. "
                "Please try again.",
            }
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/verse/{verse_id}")
def verse(verse_id: str) -> Verse:
    """Full verse record (Devanagari + transliteration + translation + commentary)."""
    v = _store().get(verse_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"verse {verse_id} not found")
    return v


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


if settings.push_enabled:
    from app.push import router as push_router

    app.include_router(push_router)
    install_access_log_filter()


# Serve the chat web UI (mounted last so it doesn't shadow the API routes).
if os.path.isdir("web"):
    app.mount("/ui", StaticFiles(directory="web", html=True), name="ui")
