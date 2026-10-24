# LLMBook

Upload your documents, ask questions, get answers grounded in those documents with citations, in the spirit of NotebookLM.
Everything runs on your own machine. The language model, embeddings and data never leave it, and no API keys are needed.

> **Status: Phase 4 (source guides, notes, studio) is complete.** Add PDFs, Word documents, text or Markdown to a notebook, then ask questions. Answers stream in, cite the exact passages they come from, and say "I couldn't find this in your sources" rather than guess. Clicking a citation opens the document at the right page with the supporting sentence highlighted. Each source gets a summary, key topics and suggested questions. Answers can be saved as notes, and the studio writes study guides, FAQs, briefing docs and timelines.
> Not done yet: web pages and YouTube as sources (they need internet access and SSRF protection), audio overviews, sharing.

## Source guides, notes and the studio

- **Source guide.** Written in the background once a source is processed: a summary, 4–8 key topics and 4 suggested questions. Find it at the top of the source viewer; clicking a question asks it in chat.
  - **How it's made:** a whole document rarely fits in the 8K context, so it's map-reduce. Windows of about 4,500 tokens are condensed into notes, then the notes are combined into the guide using JSON-schema-constrained output.
  - **Long documents** are sampled evenly to 16 windows.
  - **Timing on the reference laptop:** a 4-page handbook took 15 s, the 15-page paper 60 s.
- **Suggested questions** from the selected sources appear in an empty chat. In a live check, all 12 suggested questions were answered from the sources.
- **Notes.** Write your own in Markdown, or use **Save to note** on any answer; saved answers keep their clickable citations.
- **Studio.** Study guide, FAQ, Briefing doc and Timeline, built from the selected sources and saved as notes.
  - **What they're built from:** the guides' section notes rather than full text, so they take 10–40 s however many documents there are.
  - **Too many sources to fit:** each source falls back to its summary and topics, and the note lists any left out.
  - **Timelines are checked, not trusted.** An 8B model invents dates rather than admit there are none: a policy document came back with every rule dated "2023-04-01". So every entry's date must appear in the source material, or the entry is dropped. With no dated events, the note says so.
- **Sharing the GPU.** Guides and studio outputs run on their own single-process worker (`llm-worker`) and wait while a chat answer is being written.
  - **The catch:** a generation already running still finishes first. With Ollama's default of one request at a time, a question asked mid-generation waited about 13 s for its first word.
  - **Recommended:** run Ollama with `OLLAMA_NUM_PARALLEL=2`, so chat never queues behind background work. On the RTX 4060 8 GB this raised `qwen3:8b` from 5.6 to 6.3 GB of video memory, still fully on the GPU.

## Seeing where an answer comes from

Clicking a citation chip shows the passage under the answer. On wide screens it also opens the document beside the chat; on phones, use **Show in document**.

- **PDFs** render in the browser with pdf.js and open on the cited page, with the supporting sentence highlighted and scrolled into view.
  - **Which sentence:** for each answer sentence that cites the passage, the viewer picks the sentences of the passage that best support it. Words that recur across the passage count for less than distinctive ones, so "Adam" decides over "Transformer".
  - **How it's found:** matching compares letters and digits only, after Unicode normalisation. That makes it insensitive to the different ways PDFium (server) and pdf.js (browser) split text: spacing, hyphenation, ligatures. Combining marks count as letters, so Indic scripts match too.
  - **Assets:** the pdf.js worker, fonts, character maps and decoders are copied into `public/pdfjs` at build time, so nothing loads from the internet.
  - **Hardening:** `isEvalSupported` is off, as defence in depth for uploaded files.
- **Word, text and Markdown sources** open as their extracted text, by section, with the same highlighting.
- **Scanned pages** have no text layer in the browser. The viewer still opens the cited page and shows the passage above it.
- **Clicking a source's name** in the Sources list opens it without a highlight.

**Checking highlights against a real PDF** (no browser needed): run this after an eval, from `apps/web`:

```
node scripts/check-highlights.mjs ../api/data/eval-cache/attention.pdf ../api/data/evals/<latest>.json
```

It replays every citation from the eval run through the viewer's logic using pdf.js.

Last run (2026-10-23):
- **All cited:** 14 of 14 citations into the paper got a highlight.
- **Evidence covered:** in all 9 cases where the citing answer sentence states the evidence, the highlight covers it.
- **Known imprecision:** where a PDF has no full stop between a heading or table and the next sentence, the highlight also takes in that heading or table text.

## How a question is answered

1. **Follow-ups are rewritten.** If the question continues a conversation, the chat model rewrites it as a standalone query ("and its beta values?" becomes "Adam optimizer beta values").
2. **Hybrid search.** The query is embedded with `bge-m3` on the CPU (about 45 ms).
   - Vector search (pgvector HNSW, with iterative scans so the per-notebook filter stays exact) and keyword search (Postgres full-text, any-word matching) each return 30 candidates.
   - Reciprocal Rank Fusion merges the two lists.
3. **Context.** The best 6 passages are kept, plus each one's neighbours, because answers often straddle a chunk boundary. Adjacent passages are merged, up to 3 per numbered source, so citations stay precise. Everything is sized conservatively to fit the 8K context.
4. **"Not found" without the model.** If even the best match is weak, the app answers without calling the model.
5. **Generation.** `qwen3:8b` answers from the numbered sources only, citing them as `[n]`, and streams token by token over Server-Sent Events.
   - Invented citation numbers are removed before the answer is saved.
   - Each answer keeps a copy of its passages, so citations still work after a source is deleted.
6. **Stop.** Pressing Stop (or closing the tab) cancels generation in Ollama and keeps the partial answer.

## Answer quality

`apps/api/evals/` holds 21 questions with known answers:
- 12 about the "Attention Is All You Need" paper
- 6 about a handbook PDF and a Word policy, including one that combines two passages
- 3 that the sources can't answer

Run it against the running stack:

```
cd apps/api
uv run python -m evals.run                   # ~2 minutes on an RTX 4060 laptop
uv run python -m evals.run --retrieval-only  # search quality only
```

Latest result (2026-10-23, `qwen3:8b` + `bge-m3`, RTX 4060 Laptop 8 GB):

| Measure | Result |
|---|---|
| Evidence retrieved | 18/18 (MRR 0.94) |
| Correct answers | 18/18 |
| Cites the passage that supports the answer | 18/18 |
| Declines unanswerable questions ("Who won the 2018 World Cup?") | 3/3 |
| Time to first word (median) | 2.5 s |
| Full answer (median) | 3.4 s |

The set is small and generation varies a little between runs, so rerun it after changing prompts, retrieval settings or models.
- **First answer after indexing:** it is slower (11 s in one run), because indexing uses the GPU and the chat model then has to reload.
- **Fixes the eval drove:** one huge merged passage caused a missing citation, which led to the 3-passages-per-source cap and an "always cite" rule.
- **Similarity cut-off:** answerable questions scored at least 0.50 similarity, while unanswerable ones scored up to 0.47. That is too close to raise `RETRIEVAL_MIN_SIMILARITY` above its conservative 0.35; the model's own refusal handles the rest.

The eval needs Ollama, so it isn't part of CI.

## What happens to a document

1. **Upload.** The file streams to disk while it's hashed, with a 50 MB limit. Its type is checked from its bytes, not its name. Adding the same file to the same notebook twice is rejected.
2. **Parse** (in the background worker):
   - **PDFs:** text is extracted page by page with PDFium. Pages without a text layer (scans) are read with Tesseract OCR. Running headers, footers and page numbers are removed.
   - **Word documents:** headings and tables are kept.
   - **Markdown:** headings become section paths.
3. **Chunk.** Paragraphs are packed into passages of about 400 tokens. Each overlaps the previous one by about 60 tokens, and passages never cross a top-level section. Each passage keeps its page range and heading path, for citations.
4. **Embed and index.** Passages are embedded with `bge-m3` on the GPU and stored in Postgres with a vector index (HNSW) and a full-text index.

Progress is shown live in the Sources panel.
- **Failures** carry a readable reason and a Retry button.
- **If Ollama is down,** jobs retry with backoff, up to 5 times.
- **A periodic sweep** re-queues jobs that were lost and fails jobs whose worker died.

**OCR languages:** English is built in. To add more, set `EXTRA_OCR_LANGS="hin kan"` and `OCR_LANGUAGES=eng+hin+kan` in `.env`, then rebuild with `docker compose build`.

## Architecture

```
Browser ──► Caddy :8080 ──┬─ /api/* ──► FastAPI (api) ──► Postgres + pgvector
                          │                 │         └─► Redis ◄── Celery (worker)
                          └─ everything else ► Next.js (web)
                                            │
                                            └──► Ollama on the host (GPU): chat + embedding models
```

| Part | Tech |
|---|---|
| Frontend | Next.js 16, React 19, Tailwind 4, TanStack Query |
| API | FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic |
| Background jobs | Celery + Redis |
| Database | PostgreSQL 17 + pgvector |
| Models | Ollama, running natively on the host. Defaults are `qwen3:8b` for chat and `bge-m3` for embeddings |
| Auth | Built in: email + password (argon2id), server-side sessions in httpOnly cookies |

Ollama runs on the host rather than in Docker so it gets direct GPU access. Containers reach it at `host.docker.internal:11434`.

## Requirements

- Docker Desktop
- [Ollama](https://ollama.com) with the models pulled:
  ```
  ollama pull qwen3:8b
  ollama pull bge-m3
  ```
- An NVIDIA GPU with 8 GB+ of video memory is recommended. CPU-only works, but slowly.
- Ollama settings that help on 8 GB GPUs (user environment variables; restart Ollama after setting them):
  `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, and `OLLAMA_NUM_PARALLEL=2`. The last one lets chat answers run alongside background generation instead of queueing behind it.

## Run it

```
docker compose up -d --build
```

Open http://localhost:8080 and create an account.
To check that every dependency is reachable, open http://localhost:8080/api/readyz.

Configuration is through environment variables. See [.env.example](.env.example); copying it to `.env` is optional for local use.

## Develop

Start the backing services:

```
docker compose up -d postgres redis
```

API (from `apps/api`):

```
uv sync
uv run alembic upgrade head
uv run uvicorn --factory app.main:create_app --reload --port 8000
```

API docs are served at http://localhost:8000/api/docs.

Web (from `apps/web`): run `npm install`, then `npm run dev`, then open http://localhost:3000. In development, `/api` is proxied to port 8000.

### Checks (the same ones CI runs)

```
# apps/api
uv run ruff format --check . && uv run ruff check . && uv run mypy app tests && uv run pytest

# apps/web
npm run lint && npm run typecheck && npm test && npm run build
```

The API tests use real Postgres and Redis (the `llmbook_test` database and Redis DB 15) and never call Ollama. The OCR test runs only where Tesseract is installed (CI and Docker); elsewhere it is skipped.

To run the worker outside Docker on Windows, use `uv run celery -A app.workers.celery_app worker -Q default,ingest --pool=solo`. Celery's default pool doesn't support Windows.

### Choosing a model

`scripts/bench_models.py` compares chat models on this machine. For each model it measures:
- time to first token on a realistic, uncached ~5K-token prompt
- generation speed and video memory use
- whether answers stay grounded: correct facts, valid citations, saying "not found" when the sources don't cover a question, and ignoring instructions planted inside a source

It also measures how fast the embedding model runs on the GPU and on the CPU.

```
uv run scripts/bench_models.py --models qwen3:8b llama3.1:8b   # add --think to test reasoning mode
```

Results on the reference laptop (RTX 4060 Laptop 8 GB, Ryzen 9 8945HS), 2026-10-23:

| | qwen3:8b | llama3.1:8b | qwen3:8b + reasoning |
|---|---|---|---|
| Grounding checks passed | 6/6 | 6/6 | 6/6 |
| First token, uncached ~5.5K-token prompt | 4.0 s | 3.7 s | 11.5 s |
| Generation speed | 39 tok/s | 39 tok/s | 38 tok/s |
| Video memory | 5.6 GB | 5.3 GB | 5.6 GB |

- **Multi-source reasoning is flaky.** Both models got the question that combines two sources wrong on one earlier run.
- **Embeddings:** `bge-m3` embeds 40 chunks/s on the GPU and 2 chunks/s on the CPU. Embedding one query takes 17 ms on the GPU and 80 ms on the CPU.
- **The two models don't fit on the GPU together.** On 8 GB, loading the chat model evicts the embedding model.

## Layout

```
apps/api/        FastAPI app, Celery worker, Alembic migrations, tests
  app/ingestion/   type detection, parsers + OCR, chunking, the ingestion pipeline
  app/retrieval/   hybrid search, rank fusion, neighbour expansion
  app/chat/        prompts, citation handling, answer service, SSE streaming
  app/generation/  source guides (map-reduce), studio outputs, GPU sharing with chat
  evals/           question set and runner for retrieval + answer quality
apps/web/        Next.js frontend
infra/           Caddy and Postgres init config
scripts/         Model benchmark
.github/         CI: lint, type-check, tests, builds
```

## Sharing with friends (this laptop as the server)

`scripts/share.ps1` gives friends anywhere a public HTTPS address for the app, with no router setup:
- **By default:** a permanent address with [Tailscale Funnel](https://tailscale.com/kb/1223/funnel), `https://<laptop>.<tailnet>.ts.net`. It's free, needs no domain, and friends need no app. TLS ends on this laptop, so Tailscale's relays can't read the traffic.
- **With `-QuickTunnel`:** a temporary [Cloudflare quick tunnel](https://try.cloudflare.com/) address, which changes on every start.

```
powershell -ExecutionPolicy Bypass -File scripts\share.ps1               # permanent address + invite code
powershell -ExecutionPolicy Bypass -File scripts\share.ps1 -Stop         # stop sharing
powershell -ExecutionPolicy Bypass -File scripts\share.ps1 -QuickTunnel  # temporary Cloudflare address
```

First-time setup for the permanent address:
1. Run `tailscale up` and sign in.
2. Open the Funnel approval link Tailscale prints. This also enables HTTPS certificates.

Funnel then keeps running in the background and comes back after a restart, once you've logged in to Windows and Tailscale has started.

`.env` (git-ignored) holds the sharing settings:
- `ENVIRONMENT=production` and `COOKIE_SECURE=true`: HTTPS-only cookies, and no API docs page.
- `REGISTRATION_CODE`: friends need it to sign up. Change it, then `docker compose up -d`, to stop new sign-ups with the old code.
- `ALLOWED_ORIGINS`: includes the permanent address. Requests from the page's own origin are accepted anyway, which is how the changing quick-tunnel address works without configuration.

**Visitors' real IP addresses** are taken from Cloudflare's `Cf-Connecting-Ip` header or Tailscale's `X-Forwarded-For` header, read right to left so they can't be faked. Rate limits therefore apply per person.

Worth knowing:
- **The laptop must stay on and awake**, with Docker Desktop and Ollama running, while shared.
- **Privacy:** the models run on this laptop. With Funnel, traffic stays encrypted until it reaches the laptop; the quick tunnel passes pages, uploads and questions through Cloudflare. Either way, as the server's owner you can technically read everything your friends upload.
- **Capacity:** one GPU answers about two questions at once with `OLLAMA_NUM_PARALLEL=2`; others wait their turn. Per-user rate limits apply.

## Phone app

- **Android:** friends open `/get-app` on the shared address to download **LLMBook.apk**.
  - **What it is:** a Trusted Web Activity built with Google's Bubblewrap. It opens the shared address full-screen, with no browser bar, because the site publishes `/.well-known/assetlinks.json`.
  - **Rebuilding, signing key and updates:** see [android/README.md](android/README.md).
  - **Back up the signing key:** Android only installs updates signed with the same key.
- **Any phone, including iPhone:** the site is an installable web app. Choose "Add to Home screen" or "Install app" in the browser.
- **When the server is off,** both versions show an "LLMBook is offline" page instead of a browser error.

## Production notes

- Set `ENVIRONMENT=production`, `COOKIE_SECURE=true` and a strong `POSTGRES_PASSWORD`. The API refuses to start in production without secure cookies.
- Put a real hostname in `infra/caddy/Caddyfile` in place of `:8080`, and Caddy provisions HTTPS automatically.
- Set `ALLOW_REGISTRATION=false` once the accounts you need exist.
- To serve more users, run the same stack on a Linux GPU server. vLLM support is planned (Phase 5); the model client is kept small so it can be swapped.
