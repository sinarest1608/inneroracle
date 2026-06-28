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


# ---- citation parsing / deterministic check ----

def test_parse_citations_handles_both_forms():
    text = "As taught [BG 2.47] and also [AG-1.15], plus repeat [BG 2.47]."
    assert parse_citations(text) == ["BG-2.47", "AG-1.15"]


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

    # balanced retrieval put BOTH books into the prompt context
    ctx = provider.seen_messages[0]["content"]
    assert "Bhagavad Gita" in ctx and "Ashtavakra Gita" in ctx


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
