"""Turn a follow-up question + chat history into a standalone retrieval query.

Follow-ups like "tell me more" or "what about anger?" retrieve poorly on their own.
This rewrites them into a self-contained query using the recent conversation, so
hybrid search pulls the right verses. Uses Haiku (`expansion_model`) — cheap + fast.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.config import settings

CONDENSE_SYSTEM = """Given a conversation and the user's latest message, rewrite the \
latest message as a standalone search query that captures what they're really asking, \
resolving references like "that", "it", "tell me more". Output ONLY the rewritten query, \
nothing else. If the latest message is already self-contained, return it unchanged."""


@runtime_checkable
class Condenser(Protocol):
    async def condense(self, query: str, history: list[dict]) -> str:
        ...


def _format_history(history: list[dict]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in history)


class HaikuCondenser:
    def __init__(self, model: str | None = None):
        import anthropic

        self.model = model or settings.expansion_model
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def condense(self, query: str, history: list[dict]) -> str:
        if not history:
            return query
        payload = f"Conversation:\n{_format_history(history)}\n\nLatest message: {query}"
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=128,
            system=CONDENSE_SYSTEM,
            messages=[{"role": "user", "content": payload}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or query
