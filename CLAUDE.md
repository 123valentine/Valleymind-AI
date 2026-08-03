# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Two separate projects — read this first

ValleyMind is **two repos, two deployments** that link to each other but share no code:

| | Public website (marketing) | The app (product) |
|---|---|---|
| **Repo** | `github.com/123valentine/valley-mind-website` (branch `master`) | `github.com/123valentine/Valleymind-AI` (branch `main`) — **this repo** |
| **Local folder** | `…/Desktop/valley mind website` (a second working dir on the dev machine) | `…/Desktop/Valleymind-AI` |
| **Type** | Static site — plain HTML/CSS/JS, no backend, no build step | Flask app serving a single-file SPA + JSON/SSE API |
| **Live URL** | https://valley-mind-website-o3l8.onrender.com | https://valleymind-ai.onrender.com |
| **Deploy** | Render (auto-deploys on push to `master`) | Render (`render.yaml`, `gunicorn app:app`) + a Hugging-Face Docker Space mirror (`ValentineEgbujie/valleymindai`, `Dockerfile` binds `0.0.0.0:7860`) |

A custom domain `valleymind.ai` appears in the website's meta/canonical tags but is **not wired up yet** (aspirational).

### How the website and the app connect
- **Website → app:** `script.js` defines one constant `VALLEYMIND_APP_URL` and, on load, wires every
  element with the `data-app-link` attribute to it. All primary CTAs (nav **Log In** / **Get Started**,
  hero **Launch ValleyMind**, CTA **Get Started Free**) carry `data-app-link`, so repointing the whole
  site at a new app URL (e.g. `valleymind.ai`) is a **one-line change** to that constant.
- **App → website:** the app's login view (`#loginView` in this repo's `index.html`) has a
  "← Back to the ValleyMind website" link back to the marketing site.
- **Marketing routes:** the website is otherwise a one-page site with in-page anchor sections
  (`#hero`, `#features`, `#products`, `#capabilities`, `#about`, `#contact`, `#cta`). The nav/footer
  **About** links point to a dedicated `about.html` page (multi-page structure; see that repo).

> Editing the website means editing files in the **other** repo/folder. Those changes go live only
> when committed and pushed to `valley-mind-website` (Render auto-deploys). This repo's changes deploy
> independently.

## This repo's layout

The **root of the repo is the single source of truth** for the app's backend and frontend. It holds
all active development (capability router, chat, image/video generation, Studio, Round Table, Massive
Editing, media library).

`archive_valleymind-backend/` and `archive_valleymind-frontend/` are retired copies of an older
backend/frontend split (kept for history — **not imported and not deployed**). Do not edit them unless
explicitly asked to dig through history. The root also has stray `*.log` files, an `archive/` folder,
and two local venvs (`env/`, `env311/`) — ignore these when exploring.

Architecture at a glance:
- **Backend**: single Flask app (`app.py`, ~3,700 lines) serving both the JSON/SSE API and the SPA.
- **Frontend (app)**: `index.html` is a single-file vanilla-JS SPA (~6,900 lines, no build step). It
  loads Tailwind, Lucide icons, Google Sign-In, `marked.js`, and `pdf.js` from CDNs, plus
  `/static/settings.js`. There is no npm/React/webpack pipeline for the product UI — the root
  `package.json` only pins the `opencode-ai` CLI and is unrelated to the app.
- **`core/`**: all backend logic, imported by `app.py`.

## Running the app locally

```bash
pip install -r requirements.txt
python app.py          # or: gunicorn app:app   (serves http://127.0.0.1:8000)
```

The app reads `.env` from the project root (`core/config.py` loads it via `python-dotenv`, and
`app.py` also manually parses `.env` at startup). See `render.yaml` for the authoritative list of every
env var the code depends on. Highlights: `MONGODB_URI` (persistence), the LLM cluster keys
(`GROQ_API_KEY`/`OPENROUTER_API_KEY`/`NVIDIA_API_KEY`/`GEMINI_API_KEY`), Pinecone keys, the five
`R2_*` media-storage vars, `ALIBABA_MODEL_STUDIO_API_KEY`/`FAL_KEY` (video), the `EDIT_*` Massive
Editing caps, `TINYFISH_API_KEY` (web search), and `GOOGLE_CLIENT_ID` (Sign-In). Missing keys degrade
gracefully (providers report unhealthy) rather than crashing the app.

## Tests

There is no pytest suite. Testing is done via standalone scripts that exercise a **running** server or
brain instance directly:

```bash
python tests/http_integration_test.py --base-url http://127.0.0.1:8000   # full HTTP integration check
python test_flow.py            # simulated single-turn chat through MarcusBrain (no server needed)
python verify_apis.py          # ad-hoc provider/API key verification
python verify_openrouter.py
```

`tests/cleanup_test_memory.py` removes memory data left behind by test runs.

## Architecture

### Request flow & capability router

`index.html` talks to Flask JSON/SSE endpoints in `app.py`. Main chat endpoints are `/chat` (JSON) and
`/chat/stream` (SSE), plus capability-specific routes (`/api/generate-image`, `/api/studio/*`,
`/api/editing/*`, `/api/roundtable`, `/api/video/analyze`), and `/api/settings/*`, `/chat/sessions*`,
`/auth/*`. Auth is session-cookie based (email/password + Google Sign-In via `/api/auth/google`) — no
separate auth service.

For each chat request, `app.py` dispatches through a **capability router**:

1. **`core/router.py`** (`CapabilityRouter.classify`) decides *what* the user wants —
   `text`/`image`/`video`/`audio`/`code`, possibly several at once — via a layered strategy: explicit UI
   hint (`source` field, e.g. `image_modal`) → cheap metadata checks → LLM classification only when
   genuinely ambiguous. It only classifies; it never generates. New capabilities: extend the
   `Capability` enum in `core/provider_manager.py`, describe it in the router prompt, add a dispatch
   branch in `app.py` (see `core/router.py` docstring).
2. `app.py`'s `_dispatch_*_json` / `_dispatch_*_stream` helpers take the `RouteDecision` and call
   `core/brain.py`, `core/image_gen.py`, `core/video_dispatcher.py`, or `core/tts.py`, then persist via
   `_persist_chat_message`.
3. **`core/provider_manager.py`** defines `BaseProvider` and concrete providers per `Capability`, each
   tracking health/priority/quota so failing providers are skipped. Provider identity must never leak
   into user-facing responses (see the module docstring).
4. **`core/brain.py`** (`MarcusBrain`) is the text engine: pulls long-term/session memory, decides
   whether a request needs live data (news/sports/web search via `core/external_apis.py`, which chains
   TinyFish Search → DuckDuckGo Lite → Wikipedia), calls the LLM cluster (`_call_llm_cluster`), and
   filters output through `UI_RESPONSE_BLOCKLIST`/`MIDDLEWARE_OUTPUT_PATTERNS` so raw HTML/UI fragments
   or internal/provider details never reach the user.

**LLM cluster**: Groq (primary) → OpenRouter → NVIDIA → Gemini, all 70b-class so no persona is weaker
than another. Every feature that calls an LLM keeps this full fallback chain behind it.

### Character system

Assistants ("Marcus", "Elena", "Angelina", …) are defined by `character/<name>/behavior.json` (name,
role, mood, system prompt, optional `response_module`/`response_function` for scripted responses),
loaded via `core/character.py:load_character_profile`. `character/<name>/memory.json` is a per-character
seed (runtime memory lives under `memory_data/`, gitignored).

### Round Table — `core/roundtable.py`

A multi-persona discussion between Angelina, Marcus, Elena and the user, in voice and/or text (route
`/api/roundtable`). A lightweight **director** decides only *who* speaks and in what *order*; each chosen
persona then speaks in **plain text** with its **own assigned provider/model** so distinct models give
genuinely distinct voices and a persona can never "fail to parse" into silence. Provider-per-persona is
env-configurable via `ROUNDTABLE_PROVIDER_MARCUS` / `_ELENA` / `_ANGELINA` (`groq|openrouter|nvidia|
gemini`). Speech is synthesized by `core/tts.py` (`voice_for_persona`).

### Studio — single-video generation (`core/studio.py`, `core/studio_jobs.py`)

One user idea drives a three-stage crew pipeline: **Angelina** (script + a character sheet) → **Marcus**
(scene breakdown) → storyboard image prompts, with the character sheet threaded through every stage so
names/appearance/wardrobe don't drift between scenes. Scenes become storyboards and then clips assembled
into one video. Routes: `/api/studio/intake`, `/api/studio/run`, `/api/studio/estimate`,
`/api/studio/job/<id>` (status), `.../regenerate/<scene>`, `.../assemble`, `/api/studio/assemble-uploads`,
`/api/studio/last`, `/api/studio/note`. Video generation is handled by `core/video_dispatcher.py`
(Alibaba **wan2.7** primary → **FAL** fallback), assembled by `core/video_assembly.py`; vision analysis
in `core/video_vision.py`, image-to-video in `core/video_i2v.py`. A `VIDEO_BUDGET_USD` app-side spend cap
and the `VIDEO_GENERATION_ENABLED` kill switch stay authoritative.

### Massive Editing — auto short-form editor (`core/video_edit.py`, `core/transcription.py`)

Turns a raw clip into a vertical 9:16 short using **CPU/ffmpeg only + Groq Whisper** — no paid
video-gen spend. Flow: transcribe (word-level) → decide which parts to KEEP (drop silences and filler
words) → build an animated word-by-word caption track (ASS/libass) → render with ffmpeg
(`imageio-ffmpeg`), optionally adding free AI B-roll (Pollinations). Routes: `/api/editing/run` and
`/api/editing/assets` (a **per-user asset library** of the user's own funny sounds / music / reactions,
with `/api/editing/assets/<media_id>/delete`). Caps live in `render.yaml` (`EDIT_ENABLED`,
`EDIT_MAX_SECONDS`, `EDIT_OUTPUT`, `EDIT_MAX_BROLL`, `GROQ_WHISPER_MODEL`) so a render fits the free
instance's RAM/CPU; raise them after upgrading the plan with no code change.

### Document reader (grounded chat)

Users upload PDFs/docs in the app; text is extracted **client-side via pdf.js** and POSTed to
`/api/settings/knowledge` (stored per-user as `knowledge_items`, docs capped at 40k chars). A
best-effort `_sync_knowledge_to_memory` mirrors them into `marcus.memory.long_term["documents"]`, and
`MarcusBrain._user_documents_context` grounds chat answers in that content.

### Persistence & memory

- **MongoDB** (via `pymongo`, `core/db.py`) is the source of truth for chats, sessions, users, and media
  metadata. Without `MONGODB_URI` the app falls back to the ephemeral container disk (the original
  disappearing-chats bug). Render's free tier has **no persistent disk**, so `memory_data/` local JSON is
  an ephemeral fallback only.
- **`core/memory.py`** (`MemorySystem`) — per-user JSON short/long-term memory under `memory_data/`.
- **`core/memory_manager.py`** (`MemoryManager`) — semantic memory backed by Pinecone + OpenRouter
  embeddings (`EMBEDDING_DIMS` **must** be 768 to match the index), used for both a "memory" and a
  separate "knowledge" index (`_get_memory_mgr`/`_get_knowledge_mgr` in `core/brain.py`). Async
  (`save_to_memory`/`recall_from_memory`) and sync (`save_sync`/`recall_sync`) APIs.

### Media storage — Cloudflare R2 (`core/r2_storage.py`)

Media **bytes** (images, video clips, trailers) live in **Cloudflare R2** (S3-compatible; boto3 with
region `auto`), **not** MongoDB GridFS — GridFS on a 512MB Atlas M0 filled the cluster and streaming
files back through Flask exhausted the Render instance's RAM. Serving is by **presigned URL**: the
browser fetches bytes straight from Cloudflare and Flask only issues a tiny redirect; the bucket stays
private. **HARD-FAIL policy**: every write raises on failure — there is deliberately no silent local-disk
fallback (that fallback is what hid the last outage). Requires all five `R2_*` vars
(`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`, `R2_BUCKET`).
`core/media_manager.py` indexes each user's media and serves it via `/api/media/*` and
`/static/media/users/<user_id>/<path>` (which presigns/redirects to R2).

## Tech stack (real)

- **Backend**: Python + Flask + gunicorn (`gunicorn.conf.py`; `GUNICORN_TIMEOUT=900` because video
  generation holds a request for minutes). Key deps (`requirements.txt`): `boto3` (R2), `pymongo`
  (MongoDB), `pinecone==5.4.1`, `groq`, `google-auth` (Sign-In), `imageio-ffmpeg` (video + editing),
  `Pillow`, `requests`/`aiohttp`/`httpx`, `duckduckgo_search`/`feedparser`/`wikipedia` (live data).
- **App frontend**: single-file vanilla-JS SPA (`index.html`), no build step; Tailwind + Lucide +
  `marked.js` + `pdf.js` + Google Sign-In loaded from CDNs.
- **Marketing site**: plain static HTML/CSS/JS (`index.html` + `style.css` + `script.js` + `assets/`),
  no framework, in the separate `valley-mind-website` repo.

## Conventions from AGENTS.md

- Prefer the TinyFish Search REST API (`GET https://api.search.tinyfish.ai`) over hand-rolled scrapers,
  and TinyFish Fetch (`POST https://api.fetch.tinyfish.ai`, with `batch_create`/`batch_status` for
  multiple URLs) over raw `curl`/`urllib` for reading live pages. `core/external_apis.py` reads
  `TINYFISH_API_KEY` automatically.
