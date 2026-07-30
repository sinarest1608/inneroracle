<div align="center">

# 🪔 InnerOracle

**Life questions, answered only from the Bhagavad Gita & the Ashtavakra Gita — with every claim cited to the verse it came from.**

### [→ Try it live at inneroracle.cc](https://inneroracle.cc)

</div>

![InnerOracle — welcome screen](assets/hero.png)

---

## What it is

InnerOracle is a grounded AI assistant for life questions. Instead of answering from a
model's general knowledge, it retrieves relevant verses from two classical wisdom texts —
the **Bhagavad Gita** and the **Ashtavakra Gita** — and answers *strictly* from them, citing
each claim inline. When the two texts disagree, it shows both perspectives rather than
blending them. When the texts don't cover something, it says so instead of guessing.

Ask anything — *"How do I deal with anxiety about results?"*, *"What is the self?"*,
*"How should I handle anger?"* — and get a concise, sourced answer you can trace back to the
original Sanskrit.

## What makes it different

- **📖 Grounded, not guessed** — answers come only from the retrieved verses. Every
  substantive claim carries an inline citation like `[BG 2.47]` or `[AG 1.15]`.
- **✅ Self-checking** — a separate model verifies each answer against the source verses and
  shows a *"grounded in the texts"* / *"check sources"* badge, so you know how trustworthy it is.
- **🔍 Tap to see the shloka** — tap any citation to reveal the original Devanagari verse,
  transliteration, and translation.
- **⚖️ Honest contrast** — the Gita's path of selfless action vs. the Ashtavakra Gita's
  non-dual "you were never the doer" — presented side by side. Focus on one text or compare both.
- **💬 Remembers the conversation** — ask follow-ups like *"tell me more"* or *"what about anger?"*
  and it keeps context.
- **🔒 Private by design** — conversations live only in your browser; nothing is stored on a server.

## See it in action

**A grounded answer** — inline citations to each verse and a *"grounded in the texts"*
verification badge. Light and dark themes:

<table>
  <tr>
    <td width="50%"><img src="assets/answer.png" alt="Grounded answer — light theme" /></td>
    <td width="50%"><img src="assets/answer-dark.png" alt="Grounded answer — dark theme" /></td>
  </tr>
</table>

**Welcome screen** — dark theme:

![Welcome screen — dark theme](assets/hero-dark.png)

**On mobile:**

<img src="assets/mobile.png" alt="InnerOracle on mobile" width="320" />

## How it works

```
your question
   → (for follow-ups) rewritten into a standalone search query
      → hybrid retrieval: dense semantic + keyword search, fused and balanced across both texts
         → the model writes a concise answer grounded in those verses, streamed live
            → a verifier checks the answer against the verses → "grounded" / "check sources" badge
```

Sanskrit shlokas are kept separate from translations and commentary when the texts are
ingested, then recombined into full verse context at answer time — so retrieval stays precise
while answers stay readable.

## Built with

**Retrieval-Augmented Generation** · **Anthropic Claude** (answers + verification) ·
**FastAPI** with live token streaming · **Qdrant** hybrid vector search (semantic + keyword,
RRF fusion) · **ONNX** embeddings · **SQLite** · a dependency-light **vanilla-JS** web app ·
deployed with **Docker** behind a **Cloudflare Tunnel**.

## Run it locally

```bash
git clone <this repo> && cd inneroracle
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set ANTHROPIC_API_KEY
uvicorn app.main:app --reload # → http://localhost:8000
```

This assumes the knowledge base already exists under `data/` (`verses.db`, `qdrant_local/`,
`bm25.json`). If you're starting from scratch, build it first — see step 1 below.

## Deploy your own

**Prerequisites:** a VPS with **≥ 4 GB RAM** and Docker, a domain on **Cloudflare**, an
**Anthropic API key**, and a workstation (your laptop) for the one-time ingestion/indexing.

1. **Build the knowledge base on your workstation — never on a small VPS** (indexing loads
   the embedding model and will OOM a 4 GB server):

   ```bash
   pip install -r requirements.txt -r requirements-ingest.txt -r requirements-embed.txt
   # one-time extraction: EPUB/PDF → structured verses in data/verses.db
   python -m ingest data/sources/<gita>.epub --book bhagavad_gita
   python -m ingest data/sources/<ashtavakra>.epub --book ashtavakra_gita
   # embed + index: builds data/qdrant_local/ (vectors) and data/bm25.json (keyword index)
   python -m ingest.index
   ```

2. **Clone on the server** (a read-only deploy key is enough):

   ```bash
   git clone <this repo> /opt/inneroracle && cd /opt/inneroracle
   ```

3. **Copy the prebuilt data over** (it is not in git):

   ```bash
   scp -r data/verses.db data/qdrant_local data/bm25.json your-server:/opt/inneroracle/data/
   ```

4. **Configure** — `cp .env.example .env`, then set:
   - `ANTHROPIC_API_KEY`
   - `QDRANT_URL=` — leave **empty**: the app runs Qdrant embedded from `data/qdrant_local`,
     so no Qdrant container is needed (the compose `qdrant` service sits behind the
     `server-index` profile and stays off).
   - `TUNNEL_TOKEN` — create a tunnel in Cloudflare Zero Trust → Networks → Tunnels, point
     its public hostname (your apex domain) at `http://app:8000`, and paste its token.

5. **Start it:**

   ```bash
   docker compose up -d --build app cloudflared
   ```

   The app binds to localhost only; the Cloudflare tunnel is the sole way in. Smoke-test:
   your domain loads, a question streams an answer, citations open the shloka, and
   `https://your-domain/health` returns `{"status":"ok"}`.

6. **Protect the edge** — the app itself ships with no login, so put the guardrails at
   Cloudflare: enable **Bot Fight Mode**, add a **rate-limiting rule** on `/chat`, and set a
   **monthly spend cap** in the Anthropic console.

7. **Updating later:**

   ```bash
   cd /opt/inneroracle && git pull && docker compose up -d --build app cloudflared
   ```

   Re-run step 1 + 3 only when the texts or the index change. Logs:
   `docker compose logs -f app`.

---

<div align="center">
<sub>Made with care for the texts. Answers are for reflection, not a substitute for a teacher.</sub>
</div>
