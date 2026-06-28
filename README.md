# InnerOracle

A hybrid RAG assistant that answers life questions and explains the teachings of the
**Bhagavad Gita** and **Ashtavakra Gita**, grounded only in those texts. Sanskrit
shlokas are kept separate from translation/commentary at ingest and recombined into
full verse context when answering. English-first answers; original shloka shown on
demand via citations.

See [`PLAN.md`](./PLAN.md) for the full design, cost model, and milestones.

## Stack
- **LLM:** Claude API — Sonnet 4.6 (answers) + Haiku 4.5 (verifier), behind a swappable
  provider interface (move to a self-hosted model later).
- **Retrieval:** Qdrant hybrid search — BGE-M3 dense + BM25 sparse, fused with RRF;
  cross-encoder rerank.
- **Store:** SQLite for canonical verses; Qdrant for chunk vectors.
- **App:** FastAPI (SSE streaming) + a minimal web chat UI.

## Local development (no Docker required)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your ANTHROPIC_API_KEY
uvicorn app.main:app --reload
curl localhost:8000/health
```

With `QDRANT_URL` empty, Qdrant runs in embedded local mode — no Docker needed for dev.

## Docker (deployment)

```bash
cp .env.example .env          # fill in secrets
docker compose up --build
curl localhost:8000/health
```

## Status
Milestone 1 (scaffold + `/health`) complete. See `tasks/todo.md`.
