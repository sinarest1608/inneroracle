"""Answer verification: deterministic citation check + LLM groundedness.

Runs on every answer. The deterministic half (do all cited verse_ids actually
exist?) is free and catches fabricated citations; the Haiku half checks that
claims are supported by the retrieved verses.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.citations import parse_citations
from app.config import settings


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


VERIFIER_SYSTEM = """You verify whether an ANSWER is faithful to the provided VERSES.
Set grounded=true only if every substantive claim in the answer is supported by the \
verses. Put any claim that is not supported by the verses into unsupported_claims. \
Ordinary connective or framing language does not need a citation; focus on factual \
claims about what the texts teach."""


class HaikuVerifier:
    def __init__(self, model: str | None = None):
        import anthropic

        self.model = model or settings.verifier_model
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

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
