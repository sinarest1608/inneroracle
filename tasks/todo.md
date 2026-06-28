# InnerOracle — Build Todo

Full design in [`../PLAN.md`](../PLAN.md). Config: Sonnet 4.6 (answers, thinking off),
Haiku 4.5 verifier every query, BGE-M3 dense + true BM25 sparse, hybrid RRF.

## Milestone 1 — Scaffold + /health  ✅ done
- [x] Project structure (`app/`, `ingest/`, `web/`, `data/`)
- [x] `requirements.txt` (core) + `requirements-ml.txt` (deferred ML deps)
- [x] `app/config.py` (pydantic-settings), `app/qdrant.py` (embedded/remote factory)
- [x] `app/main.py` with `/health` (pings Qdrant)
- [x] `Dockerfile` + `docker-compose.yml` (qdrant + app)
- [x] Verify `/health` returns ok locally (venv + embedded Qdrant) → HTTP 200, qdrant ok

## Milestone 2 — Ingestion → verses.db  ✅ code built (live run pending user files)
- [x] EPUB parse (`ebooklib` + `bs4`/html.parser), PDF fallback (`pymupdf`, lazy)
- [x] Chapter splitting (`ingest/sources.py` dispatch + parsers)
- [x] LLM-assisted structured extraction (Claude `messages.parse` + Pydantic `ChapterVerses`)
- [x] Canonical SQLite store (`app/store.py` `VerseStore`) + verse model (`app/models.py`)
- [x] Offline validation: 6/6 pytest (store round-trip, id/merge, chunking, fake-client
      extraction wiring, synthetic-EPUB parse) + CLI `--dry-run` smoke test
- [ ] **Pending you:** drop real `.epub`/`.pdf` in `data/sources/`, set `ANTHROPIC_API_KEY`,
      then run `python -m ingest <file> --book bhagavad_gita --edition "<name>"`
- [ ] After live run: assert counts (BG ~700, AG ~300) + spot-check field separation

## Milestone 3 — Embeddings + Qdrant hybrid index  ✅ code built (live embed run pending)
- [x] `DenseEmbedder` interface + BGE-M3 (lazy) + StubEmbedder (no-torch dev)
- [x] Pure-Python BM25 sparse encoder (`app/embeddings/bm25.py`), persisted to JSON
- [x] Qdrant collection: named `dense` + `bm25` sparse vectors (`app/retrieval/vector_index.py`)
- [x] Chunk derivation (verse/commentary) + indexer (`ingest/index.py`)
- [x] Offline validation: hybrid index + RRF fusion query verified on embedded Qdrant
- [ ] **Pending:** run `python -m ingest.index` with real verses.db (+ BGE-M3 / `EMBEDDER=bge`)

## Milestone 4 — Retrieval  ✅ code built + validated offline
- [x] Hybrid query (dense + BM25, RRF) over both namespaces/books (done in M3)
- [x] Recombine chunks → full verse records (`RetrievalService.retrieve`, dedup by verse_id)
- [x] Cross-encoder rerank (`BGEReranker`) + `LexicalReranker`/`NoopReranker` for dev
- [x] Tests: recombination, rerank ordering, top_k, book filter (13/13 pytest pass)

## Milestone 5 — /chat
- [ ] `LLMProvider` + `ClaudeProvider` (Sonnet 4.6, thinking off, SSE stream)
- [ ] Grounded system prompt with `[BG x.y]` citations + prompt caching
- [ ] Haiku verifier (every query) + deterministic citation-resolves check

## Milestone 6 — /verse/{id} + web UI
- [ ] `/verse/{id}` returns full record (Devanagari + translit + translation)
- [ ] Minimal responsive chat UI, streaming, shloka-on-demand on citation tap

## Milestone 7 — Deploy
- [ ] Shared-token auth gate
- [ ] Deploy to VPS, expose via Cloudflare Tunnel
- [ ] End-to-end verification on a device

## Review
_(filled in as milestones complete)_
- **M1:** scaffold + /health — ✅ verified locally on Python 3.14 (venv + embedded
  Qdrant); `/health` → 200 `{status: ok, qdrant: ok}`. Core deps install clean.
  ML/ingestion deps deferred to `requirements-ml.txt`. Docker artifacts written but
  not run locally (Docker not installed on this machine).
- **M2:** ingestion code — ✅ built + validated offline (6/6 pytest, CLI dry-run).
  EPUB path is pure-Python (no lxml; `html.parser`). PDF parser lazy-imports pymupdf
  (binary not installed locally; runs in container / when pymupdf present). Extraction
  uses `messages.parse` structured outputs (Sonnet 4.6) with a cached system prompt.
  **Live extraction run deferred to user** (needs real books + API key) per choice (b).
- **M3:** embeddings + hybrid index — ✅ built + validated offline (10/10 pytest).
  Decision: implemented a self-contained **pure-Python BM25** sparse encoder (true BM25,
  IDF-weighted) instead of FastEmbed/Qdrant-native, so the index path is testable without
  onnxruntime/torch; swappable later. Dense via BGE-M3 (lazy) with a StubEmbedder for
  no-torch dev. **RRF fusion verified working in Qdrant embedded mode.** Live BGE-M3
  embedding run deferred (needs verses.db + torch).
- **M4:** retrieval — ✅ built + validated offline (13/13 pytest). `RetrievalService`
  does hybrid search → dedup chunks to unique verse_ids → fetch full Verse records →
  cross-encoder rerank → top_k. Stub embedder + lexical reranker make the whole path
  testable without any heavy deps or API calls.
