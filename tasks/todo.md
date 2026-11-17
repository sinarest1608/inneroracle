# InnerOracle — Build Todo

Full design in [`../PLAN.md`](../PLAN.md). Config: Sonnet 4.6 (answers, thinking off),
Haiku 4.5 verifier every query, BGE-M3 dense + true BM25 sparse, hybrid RRF.

## Unread, History and Favourites (2026-11-16) — `plans/01-unread-history-favourites.md`
Branch `inbox-unread-history-favourites`. Plans 02 (stats) and 03 (festivals) build on this
and are NOT started: 02 needs the small-numbers privacy decision first.

Decisions on the plan's open questions:
1. Separate `device_inbox` table tied to the device (FK, `ON DELETE CASCADE`), so every way a
   device disappears (unsubscribe, 410, 10 failures, admin delete) removes its rows.
2. Unstarred rows purged after `PUSH_LOG_RETENTION_DAYS` (90); starred rows kept.
3. Per-device only (no sync).
4. Lost pushes: the row is written when the device is *claimed* for the send, before the
   push (the payload needs the entry id anyway), so a failed delivery still shows in History.
5. `setAppBadge` is feature-detected in page and worker; silent no-op when missing.

- [x] `push_db.py`: `device_inbox` table
- [x] `app/inbox.py`: add / list / open / open-all / star / purge
- [x] `push_service.py`: daily + forced sends write the row; payload carries `inbox_id`, `unread`
- [x] `push.py`: `GET /push/inbox`, `POST …/{id}/open`, `POST …/open-all`, `POST|DELETE …/{id}/star`
- [x] `push_scheduler.py`: purge old unstarred rows
- [x] `web/sw.js`: badge = unread count; tap marks the entry opened
- [x] `web/inbox.js` + sidebar nav (Unread (N) / History / Favourites) + star on the verse card
- [x] Tests + browser check

### Review (2026-11-16)
- 154 tests pass (9 new inbox tests). Headless Chrome at 390px against a local server with a
  seeded device: nav hidden until signed up, Unread (N) count, the three lists, star/unstar,
  open-from-list marks read, Mark all as read, `?verse=…&entry=…` deep link marks read,
  `sw.js` installs, safeUrl page 21/21, no page overflow, no JS errors.
- NOT verified: a real push on the phone (badge number on the iOS icon, tap → entry leaves
  Unread). Needs the deploy; the first real send after it is the test.
- Behaviour change: the badge is now the unread count, so opening the app no longer clears
  it by itself — reading the verse (or Mark all as read) does.
- Verses sent before this deploy are not back-filled into History.

## Auto-updates + What's new + local testing (2026-11-16) — `plans/04`, `plans/05`
NOT committed yet: plan 05's workflow is test locally first (`scripts/run_local.sh`).
- [x] 04: build id (hash of web/ + release notes), `/version`, `/ui/v/<build>/…` immutable
      assets, index stamped + no-cache, foreground check reloads quietly, SW skipWaiting + claim
- [x] 05: `app/resources/releases.json`, `/releases/latest` (stage-gated), tester devices,
      `/push/announce` (testers / all, once per stage, 24 h gap, audited), admin announce page
- [x] 05: What's new pop-up (once per device per release), hints (`data-hint` targets), sidebar item
- [x] 05: `LOCAL_DEV` + `MOCK_MODEL`, `/dev` helpers, `scripts/run_local.sh`,
      `docker-compose.local.yml`, `docs/local-testing.md`
- [x] 181 tests; headless Chrome against `run_local.sh`: gating, pop-up, hint walk, late star
      hint, replay, auto-reload on a new build, announce page

### Review
- Deviations: release notes in `app/resources/` (not `data/`: git-ignored + volume-mounted);
  testers kept in a `tester_devices` table rather than a column on `push_subscriptions`
  (no ALTER on the production table; rows go with the device).
- Not verified: Docker override (no Docker on this Mac); a real announce push; iPhone via
  quick tunnel; real-device update behaviour of an installed iOS app.

## Anonymous verse stats + Festival verses (2026-11-16) — `plans/02`, `plans/03`
- [x] `verse_stats` (totals only; small-numbers question answered: accept it, show everything)
- [x] `/push/stats?days=` + `/ui/admin/stats.html` (sortable by open rate / starred)
- [x] `app/festivals.py` + `app/resources/festivals.json` (10 festivals) + `festival_dates`
- [x] `app/festival_pick.py`: shortlist → code checks → blind reviewer → retry → fallback → `festival_picks`
- [x] `app/alerts.py` (optional SMTP), surprise-safe email, one-time "wrong date" link
- [x] Push title "Happy <festival>", inbox label, stats festival flag
- [x] Scheduler: monthly sync (+ first start), daily pick at 04:15
- [x] Tests (171 pass) + real dry run (feed: 20/20 dates; 2 real picks, 2–7 s per call)

### Review
- Deviations from the plans, each for a reason: stats endpoint is `/push/stats` (not
  `/admin/stats`) so the Cloudflare rate-limit rule and the access-log filter cover it;
  festival list lives in `app/resources/` (not `data/`, which is git-ignored and scp'd);
  the "wrong date" link shows a confirm button (mail scanners pre-open links);
  "Mark all as read" is not counted as an open.
- Push plan v6 deliberately REMOVED Gmail SMTP; plan 03 assumed it existed. Built as
  optional (off unless `ALERT_SMTP_USER`/`ALERT_SMTP_PASSWORD` are set).
- Festival verse replaces the MORNING send only; the evening send stays on the rotation.
- Not verified: a real SMTP send; the scheduler jobs firing on the server.

## Milestone 1 — Scaffold + /health  ✅ done
- [x] Project structure (`app/`, `ingest/`, `web/`, `data/`)
- [x] `requirements.txt` (core) + `requirements-ml.txt` (deferred ML deps)
- [x] `app/config.py` (pydantic-settings), `app/qdrant.py` (embedded/remote factory)
- [x] `app/main.py` with `/health` (pings Qdrant)
- [x] `Dockerfile` + `docker-compose.yml` (qdrant + app)
- [x] Verify `/health` returns ok locally (venv + embedded Qdrant) → HTTP 200, qdrant ok

## Milestone 2 — Ingestion → verses.db  ✅ done (live)
- [x] EPUB parse (`ebooklib` + `bs4`/html.parser), PDF fallback (`pymupdf`, lazy)
- [x] Chapter splitting (`ingest/sources.py` dispatch + parsers)
- [x] LLM-assisted structured extraction (Claude `messages.parse` + Pydantic `ChapterVerses`)
- [x] Canonical SQLite store (`app/store.py` `VerseStore`) + verse model (`app/models.py`)
- [x] Offline validation: 6/6 pytest + CLI `--dry-run` smoke test
- [x] **Live run done:** ingested into `verses.db` — **BG 695 + AG 298** verses.

## Milestone 3 — Embeddings + Qdrant hybrid index  ✅ done (live)
- [x] `DenseEmbedder` interface + StubEmbedder (no-torch dev)
- [x] Pure-Python BM25 sparse encoder (`app/embeddings/bm25.py`), persisted to JSON
- [x] Qdrant collection: named `dense` + `bm25` sparse vectors (`app/retrieval/vector_index.py`)
- [x] Chunk derivation (verse/commentary) + indexer (`ingest/index.py`)
- [x] Offline validation: hybrid index + RRF fusion query verified on embedded Qdrant
- [x] **Live embed done.** Default embedder swapped to **fastembed/ONNX
      `bge-large-en-v1.5`** (no torch) — see "Hardening" below.

## Milestone 4 — Retrieval  ✅ code built + validated offline
- [x] Hybrid query (dense + BM25, RRF) over both namespaces/books (done in M3)
- [x] Recombine chunks → full verse records (`RetrievalService.retrieve`, dedup by verse_id)
- [x] Cross-encoder rerank (`BGEReranker`) + `LexicalReranker`/`NoopReranker` for dev
- [x] Tests: recombination, rerank ordering, top_k, book filter (13/13 pytest pass)

## Milestone 5 — /chat  ✅ done
- [x] `LLMProvider` + `ClaudeProvider` (Sonnet 4.6, thinking off, SSE stream)
- [x] Grounded system prompt with `[BG x.y]` citations + prompt caching
- [x] Haiku verifier (every query) + deterministic citation-resolves check

## Milestone 6 — /verse/{id} + web UI  ✅ done
- [x] `/verse/{id}` returns full record (Devanagari + translit + translation)
- [x] Chat UI, streaming, shloka-on-demand on citation tap

## Hardening — fixes from live testing (`verifications.md`)  🚧 in progress
- [x] **#1 Memory:** `/chat` accepts `history`; follow-ups condensed via Haiku
      (`app/llm/condense.py`); UI persists conversation in `localStorage` + "New chat".
- [x] **#2 Source-only guardrail:** hardened `ANSWER_SYSTEM` (no outside knowledge,
      every claim cited, say-so-and-stop when uncovered).
- [x] **#3 Resource cut:** dropped cross-encoder reranker (RRF only) + dense embeddings
      moved to fastembed/ONNX → no torch. `reranker=none`, `embedder=fastembed` defaults.
- [x] **#4 Loaders:** animated "thinking" dots until first token.
- [x] **#5 Markdown:** vendored marked + DOMPurify; render + tappable citations; CSS polish.
- [x] **#6 Concise answers:** brevity rules in prompt + `max_tokens` 2048→800.
- [ ] Final live verification (cold-start time/RAM, /chat quality, follow-up, browser).

## Milestone 7 — Deploy
- [ ] Shared-token auth gate
- [ ] Deploy to VPS, expose via Cloudflare Tunnel
- [ ] End-to-end verification on a device

## Daily verse push notifications (plan: `tasks/inneroracle-push-plan-v6.md`)  🚧 started 2026-10-24
Phase 0 report done: facts confirmed; 30/30 tests pass. No commits until the user says so.
- [x] A. Ignore files: `state/` in `.gitignore` + `.dockerignore` (S5) — first
- [x] B. Backend core: config + startup validation, `app/push_db.py` (state/push.db, migrations, WAL, AUTOINCREMENT), verses.db read-only
- [x] C. Sign-up: validation, client_ip + two-tier limits + global budget, confirmation push, device secrets, check/update/unsubscribe
- [x] D. Sending: push_service (claims, semaphore, timeouts, resume, forced=manual), scheduler (quote, send, stale-run, purge/hash IPs, backup)
- [x] E. Rotation + `/verse-of-the-day` + quote client (standalone Haiku) + speaker-map loader
- [x] F. Admin endpoints (devices, bulk delete, log/CSV, signups, admin-actions) + scoped raw-ASGI headers + access-log filter
- [x] G. Frontend: manifest, icons, sw.js, sidebar opt-in + re-registration check, deep link, admin log page (palm-leaf identity)
- [x] H. Speaker map builder script + review CSV (user hand-reviews)
- [x] I. Deployment files: compose state mount + healthcheck (cloudflared fixed IP / forwarded-allow-ips DEFERRED by user)
- [x] J. Tests (132 pass: 71 push + 31 speaker map; safeUrl page 21/21) + README section

### Review (2026-10-24)
- Verified: fail-closed boot (real app), endpoints/headers/access-log filter on a live server, live Haiku 5.5 quote x2, UI + admin page in headless Chrome against the real backend (no CSP/JS errors), mutation checks caught.
- Deviations: no temperature (API rejects it since 2026-10-21; effort=low instead); cap-full sign-ups count as budget_exceeded; forwarded-allow-ips/cloudflared fixed IP deferred (user).
- Not verified: real iPhone install/push, CSV download in a browser, pushsubscriptionchange.
- User to do: speaker_review.csv + verify speaker_changes_reference.json; deploy (secrets, state/, Cloudflare rule).
- User decision (2026-10-24): speaker named only for confirmed BG verses; all AG + doubtful BG → neutral; model never picks a speaker (overrides plan's AI-speaker rule).
- User decision: run/deploy instructions removed from README → moved to tasks/deploy-notes.md (git-ignored).
- Committed cfb1b82 on daily-verse-push, merged to main, deployed 2026-10-24: confirmation, test push and app-icon badge verified on the user's iPhone. First scheduled daily send: next 08:30 America/Los_Angeles.

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

## UI revamp — "Tāla-patra" palm-leaf theme (2026-07-29)  🚧 in progress
User rejected the generic glassmorphism look (saw a clone in the wild). Picked the
palm-leaf manuscript direction from the mockup artifact, with their palette:
terracotta `#c18c64` · sand `#e9d7a2` · cream `#f5eed9` · slate `#4f5b67`.
Slate = ink, terracotta = structural accent (text-safe shade `#a96b41`).
- [x] Rewrite `web/styles.css`: kill blobs/blur/bubbles/pills-in-capsule; static sand
      wash, leaf-shaped answer cards (binding-hole motif), serif type (Iowan/Palatino),
      flat rubric tabs, circular terracotta seal "Ask" button, rubricated shloka panel
- [x] Dark mode = slate-night inversion of the same palette (no separate identity)
- [x] `web/index.html`: header wordmark `॥ InnerOracle ॥` (danda spans)
- [x] No JS changes; all existing class names/markup preserved
- [x] Verify: served via uvicorn, headless-Chrome screenshots of empty state +
      seeded conversation in BOTH themes (throwaway `_preview.html` seeder, deleted
      after). Citations linkify terracotta, leaf cards + hole render, seal button ok.
      30/30 pytest pass. NOT eyeballed: shloka tap-panel, drawer-open, <640px — check
      in the browser. Not committed/deployed yet.

- [x] UX: thinking label now cycles phrases every 3.5s (Consulting the texts →
      Unrolling the palm leaves → Searching the shlokas → Consulting higher powers →
      Building the answer → "First question takes longest…", then holds) so the slow
      cold-start first query doesn't look frozen. Self-stops via `node.isConnected`
      when the answer replaces the bubble. Verified in-browser (module parses,
      label cycles) via throwaway harness, deleted after.

- [x] README: retook all 5 `assets/` screenshots (hero, hero-dark, answer, answer-dark,
      mobile) with the new theme via a throwaway `_shot.html` harness (real app modules,
      seeded convo, real /verse shloka fetch, badge; deleted after). Same filenames, so
      no README path churn. Added "Run it locally" + 7-step "Deploy your own" sections
      (build index on workstation → scp data → .env → compose up app+cloudflared →
      edge protection → update flow). Note: headless Chrome clamps window width to
      ~500px — mobile shot taken at 500px (still ≤640 breakpoint).

### Review (2026-07-29 UI revamp)
- Pure re-skin: only `styles.css` (full rewrite, token-based day/night) and one `<h1>`
  line in `index.html`. Zero JS churn — all class names preserved.
- Theme mechanism unchanged (`data-theme` + prefers-color-scheme fallback pre-JS).
- Mockup artifact (4 directions) at:
  https://claude.ai/code/artifact/d8e8a3b3-9a7d-4782-a125-a286b05785c3

## Security fixes + enhancements (2026-07-12)  ✅ done
Selected from the security review: fixes 1,2,3,4,7,8 + enhancements 1–7.
- [x] F1: Bound `/chat` inputs — per_book 1..10, question ≤2000 chars, history truncated (last 20 msgs, 8000 chars each)
- [x] F2: `role: Literal["user","assistant"]` on Message
- [x] F3: Upgrade vendored DOMPurify 3.1.6 → 3.4.12
- [x] F4: Linkify on the parsed DOM (sanitizer keeps the last word on markup)
- [x] F7: Pin image tags — qdrant v1.18.2, cloudflared 2026.7.1
- [x] F8: /health returns bare ok/degraded (no internals / exception text)
- [x] E1: SSE error event + client try/catch & error rendering
- [x] E2: uvicorn --proxy-headers (real client IPs behind the tunnel)
- [x] E3: Docker log rotation (json-file, 10m × 3)
- [x] E4: "Clear all chats" button (confirm + wipe localStorage)
- [x] E5: Explicit timeouts on Anthropic clients (answer 120s, haiku 30s)
- [x] E6: Per-request token usage logging (answer/condense/verify)
- [x] E7: Favicon (SVG) + error UI styling
- [x] Verify: pytest passes + validation smoke test

### Review (2026-07-12)
- All 14 items implemented; 30/30 tests pass.
- Verified live: /health bare, 422s on per_book>10 / bad role / >2000-char question /
  bad book, SSE error event on upstream failure, real chat streams + usage log lines.
- Linkify rewrite verified in headless Chrome (single/grouped citations, XSS stripped).
- NOT verified here: Docker build (no local Docker) — validate on the VPS as usual.
