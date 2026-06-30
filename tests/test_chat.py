"""Offline tests for the chat layer: citations, verification, orchestration."""

from __future__ import annotations

import asyncio

import pytest

from app.citations import parse_citations
from app.embeddings.bm25 import BM25Encoder
from app.embeddings.stub import StubEmbedder
from app.models import Verse
from app.retrieval.reranker import LexicalReranker
from app.retrieval.search import RetrievalService
from app.verify import GroundednessResult, find_invalid_citations, verify_answer


def _collect(agen):
    async def run():
        return [e async for e in agen]

    return asyncio.run(run())


class FakeProvider:
    def __init__(self, text: str):
        self.text = text
        self.seen_system = None
        self.seen_messages = None

    async def stream(self, system, messages):
        self.seen_system, self.seen_messages = system, messages
        for word in self.text.split(" "):
            yield word + " "


class FakeVerifier:
    def __init__(self, grounded=True, unsupported=None):
        self.result = GroundednessResult(
            grounded=grounded, unsupported_claims=unsupported or []
        )

    async def check(self, question, answer, context):
        return self.result


class FakeCondenser:
    """Records the call and returns a fixed standalone query."""

    def __init__(self, rewritten="rewritten standalone query"):
        self.rewritten = rewritten
        self.calls = []

    async def condense(self, query, history):
        self.calls.append((query, list(history)))
        return self.rewritten


# ---- citation parsing / deterministic check ----

def test_parse_citations_handles_both_forms():
    text = "As taught [BG 2.47] and also [AG-1.15], plus repeat [BG 2.47]."
    assert parse_citations(text) == ["BG-2.47", "AG-1.15"]


def test_parse_citations_handles_grouped_brackets():
    text = "Your nature is already free [AG 15.20, AG 2.12]; act anyway [BG 2.47; BG 3.1]."
    assert parse_citations(text) == ["AG-15.20", "AG-2.12", "BG-2.47", "BG-3.1"]


def test_find_invalid_citations():
    answer = "See [BG 2.47] and [BG 9.99]."
    invalid = find_invalid_citations(answer, {"BG-2.47", "AG-1.1"})
    assert invalid == ["BG-9.99"]


def test_verify_answer_combines_checks():
    v = asyncio.run(verify_answer(
        "q", "Answer citing [BG 2.47] and a fake [BG 9.99].",
        "context", {"BG-2.47"}, FakeVerifier(grounded=True),
    ))
    assert v.invalid_citations == ["BG-9.99"]
    assert v.grounded is False  # a bad citation forces grounded=False


# ---- end-to-end orchestration (stub index, fake provider/verifier) ----

@pytest.fixture
def service(tmp_path):
    from qdrant_client import QdrantClient

    from app.store import VerseStore
    from ingest.index import build_index

    store = VerseStore(str(tmp_path / "verses.db"))
    store.upsert([
        Verse(verse_id="BG-2.47", book="bhagavad_gita", chapter=2, verse_no="47",
              translation="You have a right to action alone, never to its fruits."),
        Verse(verse_id="AG-1.15", book="ashtavakra_gita", chapter=1, verse_no="15",
              translation="You are unbound and actionless, the witness of all."),
    ])
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    embedder = StubEmbedder()
    bm25_path = str(tmp_path / "bm25.json")
    build_index(store=store, embedder=embedder, qdrant=client,
                bm25_path=bm25_path, recreate=True)
    return RetrievalService(store=store, embedder=embedder, qdrant=client,
                            bm25=BM25Encoder.load(bm25_path), reranker=LexicalReranker())


def test_stream_answer_flow(service):
    from app.chat import stream_answer

    provider = FakeProvider("Act without attachment [BG 2.47] yet remain the witness [AG 1.15].")
    events = _collect(stream_answer(
        "how do I act without attachment?",
        retrieval=service, provider=provider, verifier=FakeVerifier(grounded=True),
        per_book=1,
    ))

    types = [e["type"] for e in events]
    assert types[0] == "token" and types[-1] == "done"
    assert "citations" in types and "verification" in types

    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "[BG 2.47]" in answer

    cites = next(e for e in events if e["type"] == "citations")["verse_ids"]
    assert set(cites) == {"BG-2.47", "AG-1.15"}

    verif = next(e for e in events if e["type"] == "verification")
    assert verif["grounded"] is True and verif["invalid_citations"] == []

    # balanced retrieval put BOTH books into the current-turn prompt context
    ctx = provider.seen_messages[-1]["content"]
    assert "Bhagavad Gita" in ctx and "Ashtavakra Gita" in ctx


def test_stream_answer_includes_history_and_condenses(service):
    from app.chat import stream_answer

    provider = FakeProvider("Stay the witness [AG 1.15].")
    condenser = FakeCondenser()
    history = [
        {"role": "user", "content": "what is non-attachment?"},
        {"role": "assistant", "content": "Act without clinging to fruits [BG 2.47]."},
    ]
    _collect(stream_answer(
        "tell me more", retrieval=service, provider=provider,
        verifier=FakeVerifier(grounded=True), history=history,
        condenser=condenser, per_book=1,
    ))

    # Follow-up was condensed before retrieval.
    assert condenser.calls and condenser.calls[0][0] == "tell me more"
    # Prior turns precede the current question, giving the model memory.
    msgs = provider.seen_messages
    assert [m["content"] for m in msgs[:2]] == [h["content"] for h in history]
    assert "tell me more" in msgs[-1]["content"]


def test_condenser_skipped_without_history(service):
    from app.chat import stream_answer

    provider = FakeProvider("Answer [BG 2.47].")
    condenser = FakeCondenser()
    _collect(stream_answer(
        "first question", retrieval=service, provider=provider,
        verifier=FakeVerifier(grounded=True), condenser=condenser, per_book=1,
    ))
    assert condenser.calls == []  # no history → no rewrite


# ---- carry-forward of earlier-cited verses ----

def test_carry_forward_refetches_prior_citations(service):
    from app.chat import _carry_forward

    history = [{"role": "assistant", "content": "As taught [BG 2.47]."}]
    carried = _carry_forward(service, history, fresh_ids=set())
    assert [rv.verse.verse_id for rv in carried] == ["BG-2.47"]


def test_carry_forward_skips_fresh_and_fabricated(service):
    from app.chat import _carry_forward

    history = [{"role": "assistant", "content": "See [BG 2.47] and fake [BG 9.99]."}]
    # BG-2.47 already retrieved this turn, BG-9.99 doesn't exist → nothing carried.
    carried = _carry_forward(service, history, fresh_ids={"BG-2.47"})
    assert carried == []


# ---- HaikuCondenser unit (offline, fake anthropic client) ----

class _Block:
    type = "text"
    def __init__(self, text): self.text = text

class _Resp:
    def __init__(self, text): self.content = [_Block(text)]

class _FakeMessages:
    def __init__(self, text): self._text = text
    async def create(self, **kw): return _Resp(self._text)

class _FakeAnthropic:
    def __init__(self, text): self.messages = _FakeMessages(text)


def test_haiku_condenser_no_history_short_circuits():
    from app.llm.condense import HaikuCondenser

    c = HaikuCondenser()
    c._client = _FakeAnthropic("should not be used")
    assert asyncio.run(c.condense("what is dharma?", [])) == "what is dharma?"


def test_haiku_condenser_rewrites_with_history():
    from app.llm.condense import HaikuCondenser

    c = HaikuCondenser()
    c._client = _FakeAnthropic("standalone: how to handle anger")
    out = asyncio.run(c.condense(
        "what about that?", [{"role": "user", "content": "I struggle with anger"}]
    ))
    assert out == "standalone: how to handle anger"


def test_stream_answer_book_filter_restricts_context(service):
    from app.chat import stream_answer

    provider = FakeProvider("Right to action, not its fruits [BG 2.47].")
    _collect(stream_answer(
        "how do I act?", retrieval=service, provider=provider,
        verifier=FakeVerifier(grounded=True), book="bhagavad_gita", per_book=1,
    ))
    # Only the Bhagavad Gita verse reached the prompt context; AG was filtered out.
    ctx = provider.seen_messages[-1]["content"]
    assert "Bhagavad Gita" in ctx and "Ashtavakra Gita" not in ctx


def test_stream_answer_flags_fabricated_citation(service):
    from app.chat import stream_answer

    provider = FakeProvider("A made-up teaching [BG 99.99].")
    events = _collect(stream_answer(
        "x", retrieval=service, provider=provider,
        verifier=FakeVerifier(grounded=True), per_book=1,
    ))
    verif = next(e for e in events if e["type"] == "verification")
    assert verif["invalid_citations"] == ["BG-99.99"]
    assert verif["grounded"] is False
