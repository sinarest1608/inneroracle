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

## Milestone 2 — Ingestion → verses.db
- [ ] EPUB parse (`ebooklib` + `bs4`), PDF fallback (`pymupdf`)
- [ ] Chapter splitting
- [ ] LLM-assisted structured extraction (Claude + json_schema) → Pydantic verse records
- [ ] Write canonical SQLite `verses.db`
- [ ] Validate counts + spot-check separation of shloka/translation/commentary

## Milestone 3 — Embeddings + Qdrant hybrid index
- [ ] `EmbeddingProvider` (BGE-M3 dense)
- [ ] Qdrant collection: named `dense` + BM25 `sparse` vectors
- [ ] Derive verse/commentary chunks → upsert with payload
- [ ] Verify chunk counts

## Milestone 4 — Retrieval
- [ ] Hybrid query (dense + BM25, RRF) over both namespaces/books
- [ ] Recombine chunks → full verse records (SQLite)
- [ ] Cross-encoder rerank (`bge-reranker-v2-m3`)
- [ ] Golden-set unit tests

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
