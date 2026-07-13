"""Answer verification: deterministic citation check + LLM groundedness.

Runs on every answer. The deterministic half (do all cited verse_ids actually
exist?) is free and catches fabricated citations; the Haiku half checks that
claims are supported by the retrieved verses.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.citations import parse_citations
from app.config import settings

log = logging.getLogger("inneroracle.usage")


class GroundednessResult(BaseModel):
    grounded: bool
    unsupported_claims: list[str] = []


class Verification(BaseModel):
    grounded: bool
    unsupported_claims: list[str]
    invalid_citations: list[str]


def find_invalid_citations(answer: str, valid_ids: Iterable[str]) -> list[str]:
    valid = set(valid_ids)
    return [c for c in parse_citations(answer) if c not in valid]


@runtime_checkable
class Verifier(Protocol):
    async def check(
        self, question: str, answer: str, context: str
    ) -> GroundednessResult:
        ...


VERIFIER_SYSTEM = """You check an ANSWER for faithfulness to the provided VERSES \
(each verse includes its translation and commentary).

A claim is SUPPORTED if it is a reasonable reading, paraphrase, or synthesis of one of \
the provided verses or its commentary — it does NOT need to use the same words, and \
interpretation is fine as long as a cited verse plausibly backs it.

Set grounded=true unless the answer does one of these:
- attributes a teaching to the texts that NONE of the provided verses support, or
- states something that CONTRADICTS the verses, or
- cites a verse that clearly doesn't say what the answer claims.

Only list genuine problems of those kinds in unsupported_claims. Do not flag ordinary \
framing, connective language, or fair interpretation of a cited verse."""


class HaikuVerifier:
    def __init__(self, model: str | None = None):
        import anthropic

        self.model = model or settings.verifier_model
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=30.0
        )

    async def check(
        self, question: str, answer: str, context: str
    ) -> GroundednessResult:
        payload = f"VERSES:\n{context}\n\nQUESTION: {question}\n\nANSWER:\n{answer}"
        resp = await self._client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=VERIFIER_SYSTEM,
            messages=[{"role": "user", "content": payload}],
            output_format=GroundednessResult,
        )
        u = getattr(resp, "usage", None)
        if u:
            log.info(
                "verify model=%s input=%s output=%s",
                self.model, u.input_tokens, u.output_tokens,
            )
        return resp.parsed_output or GroundednessResult(grounded=True)


async def verify_answer(
    question: str,
    answer: str,
    context: str,
    valid_ids: Iterable[str],
    verifier: Verifier,
) -> Verification:
    invalid = find_invalid_citations(answer, valid_ids)
    result = await verifier.check(question, answer, context)
    return Verification(
        grounded=result.grounded and not invalid,
        unsupported_claims=result.unsupported_claims,
        invalid_citations=invalid,
    )
