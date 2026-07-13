"""Claude answer provider — Sonnet 4.6, thinking off, streamed."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from app.config import settings

log = logging.getLogger("inneroracle.usage")


class ClaudeProvider:
    def __init__(self, model: str | None = None, max_tokens: int = 1400):
        self.model = model or settings.answer_model
        self.max_tokens = max_tokens
        # Explicit timeout so a hung upstream call can't pin an SSE connection open.
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=120.0
        )

    async def stream(self, system: Any, messages: list[dict]) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
        u = final.usage
        log.info(
            "answer model=%s input=%s output=%s cache_read=%s cache_write=%s",
            self.model,
            u.input_tokens,
            u.output_tokens,
            getattr(u, "cache_read_input_tokens", 0),
            getattr(u, "cache_creation_input_tokens", 0),
        )
