# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout — read this first

The **root of the repo is the single source of truth** for both backend and frontend. It holds
the newest code (capability router, video generation, media library, image gallery) and is where
all active development happens.

`archive_valleymind-backend/` and `archive_valleymind-frontend/` are retired copies of an older
backend/frontend split (renamed from `valleymind-backend/`/`valleymind-frontend/`, kept for
history — not deleted). They are **not imported by root code and not deployed**. Do not edit them
unless the user explicitly asks you to dig through history there.

**Deployment TODO (not yet done):** Render's start command was previously
`gunicorn --chdir valleymind-backend app:app`, i.e. it served the now-archived directory. Root's
own `render.yaml`/`Procfile` say `gunicorn app:app`, but the actual configured start command lives
in the Render dashboard and must be updated by the user to point at root (e.g. `gunicorn app:app`
with no `--chdir`) or the live deploy will break. This is a dashboard change outside the repo —
flag it, don't attempt to fix it via file edits.

The repo root also has many stray log files (`*.log`), an `archive/` folder, and two Python venv
directories (`env/`, `env311/`) left over from local dev — ignore these when exploring.

Architecture at a glance:
- **Backend**: single Flask app (`app.py`, ~2300 lines) serving both the JSON/streaming API and
  the static frontend.
- **Frontend**: `index.html` is a single-file vanilla-JS SPA (~3700 lines, no build step). It
  loads Tailwind, Lucide icons, Google Sign-In, `marked.js`, and `pdf.js` from CDNs, plus
  `/static/settings.js`. There is no npm/React/webpack pipeline for the actual product UI — the
  root `package.json` only pins the `opencode-ai` CLI dependency and is unrelated to the app.
- **`core/`**: all backend logic, imported by `app.py`.

## Running the app locally

```bash
pip install -r requirements.txt
python app.py          # or: gunicorn app:app
```

The app reads `.env` from the project root (`core/config.py` loads it via `python-dotenv`, and
`app.py` also manually parses `.env` at startup). Key env vars (see `.env` for the full list, no
values committed): `GROQ_API_KEY`/`GROQ_MODEL`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`,
`GEMINI_API_KEY`/`GOOGLE_GENERATIVE_AI_API_KEY`, `ALIBABA_MODEL_STUDIO_API_KEY`,
`PINECONE_API_KEY`/`PINECONE_API_KEY_MEMORY`/`PINECONE_API_KEY_KNOWLEDGE` (+ index/region vars),
`MONGODB_URI`, `GOOGLE_CLIENT_ID`, `GOOGLE_SEARCH_API_KEY`/`GOOGLE_SEARCH_CX`, `TINYFISH_API_KEY`,
news/sports API keys. Missing keys degrade gracefully (providers report unhealthy) rather than
crashing the app.

Deployment targets are Render (`render.yaml`, health check at `/auth/status`) and a
Hugging-Face-style Docker deploy (`Dockerfile`, binds `0.0.0.0:7860`). Both run via `gunicorn`,
configured by `gunicorn.conf.py`.

## Tests

There is no pytest suite. Testing is done via standalone scripts that exercise a **running**
server or brain instance directly:

```bash
# Full HTTP integration check against a running server (default http://127.0.0.1:8000)
python tests/http_integration_test.py --base-url http://127.0.0.1:8000

# Simulated single-turn chat flow through MarcusBrain directly (no server needed)
python test_flow.py

# Ad-hoc provider/API key verification
python verify_apis.py
python verify_openrouter.py
```

`tests/cleanup_test_memory.py` removes memory data left behind by test runs.

## Architecture

### Request flow

`index.html` talks to Flask JSON/SSE endpoints defined in `app.py`. The main chat endpoints are
`/chat` (JSON) and `/chat/stream` (SSE), plus capability-specific `/api/generate-image`, and
various `/api/settings/*`, `/chat/sessions*`, and `/auth/*` endpoints. Auth is session-cookie
based with a local `memory_data/auth_users.json` user store (plus Google Sign-In via
`/api/auth/google`) — there is no separate auth service.

For each incoming chat request, `app.py` dispatches through a **capability router** rather than
hardcoding logic per endpoint:

1. **`core/router.py`** (`CapabilityRouter.classify`) decides *what* the user wants —
   `text`/`image`/`video`/`audio`/`code`, possibly multiple at once — using a layered strategy:
   explicit UI hint (`source` field, e.g. `image_modal`) → cheap metadata checks → LLM
   classification only when genuinely ambiguous. It only classifies; it never generates content.
   New capabilities are added by extending the `Capability` enum in `core/provider_manager.py`,
   describing it in the router's classification prompt, and adding a dispatch branch in `app.py`
   (see the module docstring in `core/router.py`).
2. `app.py`'s `_dispatch_*_json` / `_dispatch_*_stream` helper functions take the `RouteDecision`
   and call into `core/brain.py`, `core/image_gen.py`, `core/video_dispatcher.py`, or
   `core/tts.py` accordingly, then persist the exchange via `_persist_chat_message`.
3. **`core/provider_manager.py`** defines `BaseProvider` and concrete providers (e.g.
   `PollinationsImageProvider`) per `Capability`, each tracking its own health/priority/quota so
   failing providers are skipped in favor of the next one. Provider identity must never leak into
   user-facing responses (see the module docstring).
4. **`core/brain.py`** (`MarcusBrain`) is the text-conversation engine: it pulls long-term/session
   memory, decides whether a request needs live data (news/sports/web search — dispatched to
   `core/external_apis.py`, which chains TinyFish Search → DuckDuckGo Lite → Wikipedia, see
   `AGENTS.md`), calls the LLM provider cluster (`_call_llm_cluster`), and filters output through
   `UI_RESPONSE_BLOCKLIST`/`MIDDLEWARE_OUTPUT_PATTERNS` so raw HTML/UI fragments or
   internal/provider details never reach the user.

### Character system

Assistants ("Marcus", "Elena", "Angelina", ...) are defined by JSON behavior files under
`character/<name>/behavior.json` (name, role, mood, system prompt, optional
`response_module`/`response_function` for fully scripted responses) and loaded via
`core/character.py:load_character_profile`. `character/<name>/memory.json` is a per-character
memory seed (actual runtime memory lives under `memory_data/`, which is gitignored).

### Memory

Two separate memory layers:
- **`core/memory.py`** (`MemorySystem`) — per-user JSON-backed short/long-term memory under
  `memory_data/`.
- **`core/memory_manager.py`** (`MemoryManager`) — semantic memory backed by Pinecone +
  OpenRouter embeddings, used for both a "memory" index and a separate "knowledge" index (see
  `_get_memory_mgr`/`_get_knowledge_mgr` in `core/brain.py`). Exposes both async
  (`save_to_memory`/`recall_from_memory`) and sync (`save_sync`/`recall_sync`) APIs.

### Media

`core/media_manager.py` gives each user permanent local image storage at
`memory_data/users/{user_id}/media/images/`, indexed by `media_index.json`, served through
`/api/media/*` and `/static/media/users/<user_id>/<path>`.

## Conventions from AGENTS.md

- Prefer the TinyFish Search REST API (`GET https://api.search.tinyfish.ai`) over hand-rolled
  scrapers, and TinyFish Fetch (`POST https://api.fetch.tinyfish.ai`, with `batch_create`/
  `batch_status` for multiple URLs) over raw `curl`/`urllib` for reading live pages.
  `core/external_apis.py` reads `TINYFISH_API_KEY` automatically.
