"""Claude answer provider — Sonnet 4.6, thinking off, streamed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic

from app.config import settings


class ClaudeProvider:
    def __init__(self, model: str | None = None, max_tokens: int = 1400):
        self.model = model or settings.answer_model
        self.max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def stream(self, system: Any, messages: list[dict]) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
