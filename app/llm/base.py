"""Swappable LLM provider interface (Claude now; self-hosted later)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    def stream(
        self, system: Any, messages: list[dict]
    ) -> AsyncIterator[str]:
        """Stream the answer token-by-token for the given system + messages."""
        ...
