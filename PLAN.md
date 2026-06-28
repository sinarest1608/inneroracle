# InnerOracle — Hybrid RAG over the Bhagavad Gita & Ashtavakra Gita

## Context

You want a self-hostable assistant that answers life questions and explains
spiritual teachings, grounded **only** in the Bhagavad Gita and Ashtavakra Gita.
It must be reachable from any device (a web chat app), and the LLM layer must be
swappable so you can start on the Claude API now and move to a self-hosted open
model later. A core requirement shapes the data model: **Sanskrit shlokas must be
separated from translation/commentary at ingest and treated as distinct retrieval
sources, but recombined into full verse context when answering.**

Decisions locked from our chat:
- **LLM:** Claude API now — **Sonnet 4.6, thinking off** — behind a provider interface for
  later self-hosting (swap to Opus 4.8 or a local model anytime).
- **Hosting:** undecided → plan recommends the simplest viable setup (small VPS, no GPU, Docker).
- **Interface:** responsive web chat app, any device.
- **Shlokas:** English-first answers; original shloka shown on demand via a citations panel.

"Hybrid" here means two things at once, both satisfied by this design:
1. **Hybrid retrieval** — dense (semantic) + sparse (BM25/lexical) search fused with RRF.
2. **Hybrid sources** — verse vs. commentary indexed separately, recombined at answer time.

---

## Architecture (everything self-hostable, LLM via API)

```
                  ┌─────────── Browser (any device) ───────────┐
                  │  Chat UI  +  "show shloka" citation panel   │
                  └───────────────────┬─────────────────────────┘
                                      │ HTTPS (Cloudflare Tunnel) + token auth
                  ┌───────────────────▼─────────────────────────┐
                  │  FastAPI app (Docker)                        │
                  │   /chat (SSE stream)  /verse/{id}  /health   │
                  │                                              │
                  │  Query flow:                                 │
                  │   1. expand query (optional, Claude/Haiku)   │
                  │   2. hybrid retrieve (dense+sparse, RRF)     │
                  │      over BOTH namespaces, BOTH books        │
                  │   3. resolve hits → full verse records       │  ← "separate then combine"
                  │   4. rerank top-N (cross-encoder)            │
                  │   5. build grounded prompt → LLM provider    │
                  │   6. stream answer w/ [BG 2.47] citations    │
                  └──────┬───────────────────────┬──────────────┘
                         │                        │
                ┌────────▼─────────┐     ┌────────▼──────────┐
                │ Qdrant (Docker)  │     │ SQLite            │
                │ dense+sparse     │     │ canonical verses  │
                │ chunk vectors    │     │ (shloka, translit,│
                │ + payload meta   │     │  translation,     │
                └──────────────────┘     │  commentary)      │
                                         └───────────────────┘
   LLM provider interface ──▶ Claude API (Sonnet 4.6) now │ vLLM/Ollama later
   Embeddings provider     ──▶ BGE-M3 (dense+sparse)    │ or Voyage API
```

---

## Data model — the heart of the "separate, then combine" requirement

Canonical unit = **one verse**, with a stable ID: `BG-2.47`, `AG-1.12`.
Each verse record (SQLite row) holds all parts, kept distinct:

| field | example |
|---|---|
| `verse_id` | `BG-2.47` |
| `book` | `bhagavad_gita` \| `ashtavakra_gita` |
| `chapter`, `verse_no` | `2`, `47` |
| `sanskrit` | Devanagari shloka |
| `transliteration` | IAST/romanized (if source has it) |
| `translation` | English meaning |
| `commentary` | purport / explanation (may be long) |
| `source_edition` | which PDF/EPUB it came from |

From each verse we derive **two chunk types** for the vector store (the "separate
sources"), linked back by `verse_id`:
- `type=verse` chunk → transliteration + translation (the shloka unit)
- `type=commentary` chunk → the commentary text (split if long, ~300–500 tokens)

At query time we retrieve granular chunks, then **pull the full verse record** for
every hit and assemble complete context for the LLM — that is the recombination step.

---

## Ingestion pipeline (one-time, scriptable)

Run as `python -m ingest` against files in `data/sources/`.

1. **Prefer EPUB over PDF.** EPUB is structured HTML — far cleaner extraction, and
   Devanagari survives. PDFs mangle Sanskrit. Use PDF only as a fallback/cross-check.
   - EPUB parse: `ebooklib` + `BeautifulSoup`. PDF fallback: `pymupdf` (fitz).
2. **Split into chapters** using structural cues (headings, `BG 2.x` / verse-number patterns).
3. **LLM-assisted structured extraction** (elegant, robust to messy editions): feed each
   chapter's raw text to Claude with `output_config.format` (json_schema) and get back an
   array of verse records matching the schema above. Claude reliably separates shloka /
   transliteration / translation / commentary where brittle regex fails. Validate with
   Pydantic. One-time cost over ~2 books (~1000 verses) is trivial.
4. **Write canonical verses** to SQLite (`data/verses.db`).
5. **Build embeddings & index** into Qdrant:
   - Embed each chunk with **BGE-M3** (emits dense + sparse lexical vectors in one pass).
   - Upsert into a Qdrant collection with named vectors `dense` + `sparse`, payload =
     `{verse_id, book, type, chapter, verse_no, text}`.

Idempotent: re-running rebuilds from `data/verses.db` without re-calling the LLM.

---

## Query-time flow

1. **(Optional) query expansion** — a cheap Claude Haiku call to add relevant Sanskrit
   concept terms (e.g. "anxiety about outcomes" → also "karma yoga", "phala", "nishkama").
   Skip in v1 if you want to keep it lean; the hook stays in the interface.
2. **Hybrid retrieve** — Qdrant Query API with dense + sparse, fused via **RRF**, over
   *both* chunk namespaces and *both* books. Top ~20 chunks.
3. **Recombine** — map each chunk → `verse_id` → full verse record from SQLite. De-dup by
   `verse_id`. This assembles shloka + translation + commentary per cited verse.
4. **Rerank** — cross-encoder (`BAAI/bge-reranker-v2-m3`) over the assembled verses, keep top ~6.
5. **Prompt** — system prompt instructs: thoughtful guide, answer in plain English grounded
   ONLY in provided verses, cite as `[BG 2.47]`, never invent verses, offer the shloka only
   via citation. Pass the assembled verse contexts as the user-turn context block.
   - **Prompt caching:** put the (stable) system prompt + persona behind a `cache_control`
     breakpoint so it's cached across requests (~0.1× read cost). Do NOT cache the retrieved
     verses — they vary per query and would never get a cache hit.
6. **Generate** — `claude-sonnet-4-6` via the provider interface, **streamed** (SSE) to the UI.
   Thinking off (cost/latency); can be flipped on per-query later for deeper reasoning.
7. **Citations on demand** — the answer carries `[BG 2.47]` tags; `GET /verse/{id}` returns
   the full record (Devanagari + transliteration + translation) so the UI can reveal the
   shloka when the user taps a citation.

Short session memory (last few turns) is kept so follow-ups work.

---

## Swappable provider interfaces (key to "start API, move later")

```
app/llm/base.py        # LLMProvider: .stream(messages, system) -> async tokens
app/llm/claude.py      # ClaudeProvider  (anthropic SDK, model=claude-sonnet-4-6)
app/llm/local.py       # (later) vLLM/Ollama OpenAI-compatible endpoint

app/embeddings/base.py # EmbeddingProvider: .embed(texts) -> {dense, sparse}
app/embeddings/bge.py  # BGEM3Provider (local, default)
app/embeddings/voyage.py # (alt) VoyageProvider — zero-ops API embeddings
```

Model id, pricing, and call shape grounded against the `claude-api` skill:
- **Sonnet 4.6** `claude-sonnet-4-6` — $3 / $15 per 1M (in/out), 1M context. **Default answer model.**
- **Haiku 4.5** `claude-haiku-4-5` — $1 / $5 — the **verifier** (every query) + optional query-expansion.
- **Opus 4.8** `claude-opus-4-8` — $5 / $25 — drop-in upgrade via the provider interface for more depth.
- Thinking off by default; stream via `client.messages.stream`.

---

## Serving & deployment (recommended simplest path)

- **Backend:** FastAPI + `uvicorn`; endpoints `/chat` (SSE), `/verse/{id}`, `/health`.
- **Frontend:** minimal responsive single-page chat (Vite + React, or plain HTML+htmx to
  start). Streams tokens; renders citations with a "show shloka" toggle.
- **Containers:** `docker-compose.yml` with two services — `qdrant` and `app` (SQLite is a
  file volume; BGE-M3 + reranker load in-process, CPU is fine for this small corpus).
- **Host:** small always-on VPS (e.g. Hetzner CX22 ~€4/mo or DigitalOcean). No GPU needed
  because the LLM is an API call.
- **Access from any device:** expose via **Cloudflare Tunnel** (free HTTPS + hostname, no
  open ports). Add a simple shared-token / password gate since it's personal.
- `ANTHROPIC_API_KEY` and the access token live in a `.env` / secrets, never in code.

---

## Proposed project structure

```
inneroracle/
  data/
    sources/            # your .epub / .pdf files
    verses.db           # canonical SQLite (generated)
  ingest/
    parse_epub.py  parse_pdf.py  extract_verses.py  index.py  __main__.py
  app/
    main.py             # FastAPI
    retrieval/          # qdrant hybrid search, rerank, assembly
    llm/  embeddings/   # swappable providers (above)
    prompts.py
  web/                  # chat frontend
  docker-compose.yml
  .env.example
  tasks/todo.md
```

---

## Retrieval internals — where BM25 lives

"Sparse" in the hybrid design = the keyword/lexical arm, and that is the BM25 piece.
Two implementation options:

- **True BM25 (recommended)** — Qdrant supports BM25 natively via sparse vectors with an
  IDF modifier (`models.Document` + `Modifier.IDF`, or a FastEmbed BM25 model). Classic
  BM25 shines on *rare exact tokens* — proper names (Arjuna, Janaka) and transliterated
  Sanskrit (`sthitaprajna`, `nishkama`). For this corpus that exactness is valuable.
- **BGE-M3 sparse weights** — learned lexical weights BGE-M3 emits alongside its dense
  vector; "BM25-like" and free, but less crisp on rare tokens.

**Recommendation:** sparse arm = **true BM25** (Qdrant native), dense arm = BGE-M3 dense.
Both stored as named vectors in one Qdrant collection; combine with the **Query API +
RRF/`Fusion.RRF`** in a single request. (BGE-M3 still does the dense embeddings; we just
use Qdrant's BM25 for sparse instead of BGE-M3's sparse output.)

## Answer verification (second-model groundedness check)

A second model verifies the draft before it reaches the user — critical here, since
inventing a shloka or mis-citing a verse is unacceptable for spiritual content.

- After step 6 (generate), pass `{retrieved verses, drafted answer}` to a **verifier**
  (Haiku 4.5, cheap) with `output_config.format` →
  `{grounded: bool, unsupported_claims: [...], invalid_citations: [...]}`.
- We also do a deterministic check: every `[BG x.y]` / `[AG x.y]` citation must resolve
  to a real `verse_id` in SQLite (catches fabricated citations with zero model cost).
- On failure: regenerate once with the flagged issues fed back, else append an honest
  caveat ("the texts don't directly address this"). 
- **Policy: run on every query** (chosen) — Haiku verifier on every answer, +~$0.004/q
  (~$6/mo). Lives behind the same `LLMProvider` interface; switchable to sampled later.

## How the components communicate (deployment topology)

Most components are **co-located on one VPS**, not spread across platforms — the only
external hop is the Claude API.

| From → To | Transport | Notes |
|---|---|---|
| Browser → FastAPI | HTTPS via Cloudflare Tunnel | + shared-token auth |
| FastAPI → Qdrant | HTTP on internal Docker network (`qdrant:6333`) | same host |
| FastAPI → SQLite | local file read (`data/verses.db`) | same process/volume |
| FastAPI → BGE-M3 / reranker | in-process Python | loaded in the app container (CPU) |
| FastAPI → Claude API | HTTPS to `api.anthropic.com` | API key in secrets |

**If you later split components across platforms** (e.g. managed Qdrant Cloud, embeddings
on a separate GPU box, a self-hosted LLM): every boundary just becomes HTTP/REST + an
API key or token, and the existing provider interfaces (`LLMProvider`,
`EmbeddingProvider`) mean the app doesn't care whether a component is in-process or a
remote endpoint — you swap a local class for an HTTP-client class. Nothing else changes.

## Cost estimate (Claude API)

**Chosen config:** Sonnet 4.6 ($3 / $15 per 1M in/out) for answers, **thinking off**,
Haiku 4.5 verifier **on every query**; ~50 questions/day, context varies.

| Part | Tokens | Cost |
|---|---|---|
| Retrieved verse context + question/history | ~2,900 | ~$0.0087 |
| System prompt (cached after 1st call) | ~800 | ~$0.0004 |
| Output: answer (no thinking) | ~500 | ~$0.0075 |
| Haiku verifier (every query) | ~3,000 in / 150 out | ~$0.0038 |
| **Per query** | | **~$0.020** |

- **50/day → ~$1.00/day → ~$25–35/month.**
- **Cost levers from here:** upgrade answers to Opus 4.8 → roughly +$40/mo (~$70 total);
  turn thinking on → adds output cost; trim how much commentary you pack into context →
  direct input savings; the Haiku verifier is already near-free.
- **Honest note on caching:** the dominant input is the *per-query* retrieved verses, which
  vary and can't be cached — so caching the small fixed system prompt is only a minor win.

## Build order (milestones)

1. Scaffold repo + `docker-compose` (Qdrant up, FastAPI `/health`).
2. Ingestion: EPUB→chapters→LLM extraction→`verses.db` (validate on a few chapters first).
3. Embedding + Qdrant indexing (dense+sparse), verify counts.
4. Retrieval module: hybrid search → recombine → rerank; unit-test with sample questions.
5. `/chat` with ClaudeProvider streaming + grounded prompt + citations.
6. `/verse/{id}` + web chat UI with shloka-on-demand.
7. Containerize, deploy to VPS, expose via Cloudflare Tunnel + token auth.

---

## Verification

- **Ingestion:** assert verse counts per book (BG ~700; Ashtavakra ~300); spot-check 5
  random `verse_id`s in `verses.db` for correctly separated shloka/translation/commentary.
- **Retrieval:** golden-set of ~10 questions (e.g. "how do I act without attachment to
  results?") → assert the expected verse (`BG-2.47`) appears in top-k; compare dense-only
  vs. hybrid to confirm hybrid wins on Sanskrit-term queries.
- **Grounding:** assert every `[BG x.y]` citation in an answer resolves to a real verse;
  prompt-test that off-corpus questions get a graceful "the texts don't directly address…".
- **End-to-end:** run the app (`docker compose up`), ask via the web UI, confirm streaming,
  a cited answer, and that tapping a citation reveals the Devanagari shloka.
- **Caching:** confirm `usage.cache_read_input_tokens > 0` on the 2nd request (system prompt
  cached). **Provider swap:** smoke-test that swapping the embeddings provider to a stub
  still serves retrieval, proving the interface boundary holds.

## Open items to confirm during build
- Which EPUB editions you have (affects whether transliteration is present in the data).
- Whether to ship query-expansion (Haiku) in v1 or defer.
