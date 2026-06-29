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
from app.retrieval.search import RetrievalService
from app.verify import Verifier, verify_answer


async def stream_answer(
    query: str,
    *,
    retrieval: RetrievalService,
    provider: LLMProvider,
    verifier: Verifier,
    history: Sequence[dict] = (),
    condenser: Condenser | None = None,
    per_book: int = 3,
) -> AsyncIterator[dict]:
    history = list(history)
    # Follow-ups ("tell me more") retrieve poorly alone — rewrite to a standalone
    # query using the conversation before searching.
    search_query = query
    if history and condenser is not None:
        search_query = await condenser.condense(query, history)

    verses = retrieval.retrieve_balanced(search_query, per_book=per_book)
    system = [
        {"type": "text", "text": ANSWER_SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    # Prior turns (plain Q/A text) give the model memory; the current turn carries the
    # freshly retrieved verse context.
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
