"""Chat orchestration: balanced retrieve → grounded prompt → stream → verify.

Yields event dicts the API serializes as SSE:
  {"type": "token", "text": ...}       streamed answer text
  {"type": "citations", "verse_ids":}  resolved verse_ids cited in the answer
  {"type": "verification", ...}        groundedness + invalid citations
  {"type": "done"}
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.citations import parse_citations
from app.llm.base import LLMProvider
from app.llm.condense import Condenser
from app.prompts import ANSWER_SYSTEM, build_user_message, format_context
from app.retrieval.search import RetrievalService, RetrievedVerse
from app.verify import Verifier, verify_answer

# Cap on verses carried over from earlier turns, to bound context growth.
CARRY_LIMIT = 8


def _carry_forward(
    retrieval: RetrievalService, history: list[dict], fresh_ids: set[str]
) -> list[RetrievedVerse]:
    """Re-fetch verses the assistant cited earlier so the model can elaborate on
    and re-cite them this turn (each turn otherwise sees only its own retrieval)."""
    cited: list[str] = []
    for m in history:
        if m.get("role") == "assistant":
            for cid in parse_citations(m["content"]):
                if cid not in fresh_ids and cid not in cited:
                    cited.append(cid)
    cited = cited[-CARRY_LIMIT:]
    by_id = retrieval.store.get_many(cited)
    return [RetrievedVerse(verse=by_id[c], score=0.0) for c in cited if c in by_id]


async def stream_answer(
    query: str,
    *,
    retrieval: RetrievalService,
    provider: LLMProvider,
    verifier: Verifier,
    history: Sequence[dict] = (),
    condenser: Condenser | None = None,
    book: str | None = None,
    per_book: int = 3,
) -> AsyncIterator[dict]:
    history = list(history)
    # Follow-ups ("tell me more") retrieve poorly alone — rewrite to a standalone
    # query using the conversation before searching.
    search_query = query
    if history and condenser is not None:
        search_query = await condenser.condense(query, history)

    # Restrict to one book when the user picked one; otherwise balance across both.
    if book:
        fresh = retrieval.retrieve(search_query, book=book, top_k=per_book * 2)
    else:
        fresh = retrieval.retrieve_balanced(search_query, per_book=per_book)
    fresh_ids = {rv.verse.verse_id for rv in fresh}
    # Add back verses cited earlier in this conversation so "elaborate on that"
    # has the text to work from and prior citations stay valid.
    verses = fresh + _carry_forward(retrieval, history, fresh_ids)

    system = [
        {"type": "text", "text": ANSWER_SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    # Prior turns (plain Q/A text) give the model memory; the current turn carries the
    # verse context (freshly retrieved + carried-forward).
    messages = [*history, {"role": "user", "content": build_user_message(query, verses)}]

    chunks: list[str] = []
    async for token in provider.stream(system, messages):
        chunks.append(token)
        yield {"type": "token", "text": token}
    answer = "".join(chunks)

    valid_ids = {rv.verse.verse_id for rv in verses}
    # Give the verifier fuller commentary than the answer model saw, so it can
    # confirm paraphrased claims instead of false-flagging them.
    verifier_context = format_context(verses, commentary_max=4000)
    verification = await verify_answer(
        query, answer, verifier_context, valid_ids, verifier
    )

    yield {"type": "citations", "verse_ids": parse_citations(answer)}
    yield {
        "type": "verification",
        "grounded": verification.grounded,
        "invalid_citations": verification.invalid_citations,
        "unsupported_claims": verification.unsupported_claims,
    }
    yield {"type": "done"}
