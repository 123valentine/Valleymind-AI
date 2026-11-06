# vm-fix20 NOTES (read first on resume)

## Setup
- Worktree `..\vm-fix20` from `origin/main` @ `9da83d2` (detached HEAD). Clean.
- Constraints: no force, no --force-with-lease, no reset/clean/stash, no Android/camera files, don't touch original folder. No paid APIs (free tiers only); name any new env var/dependency BEFORE adding it.
- Allowed: index.html, static/settings.js, chat/search/brain backend files (name before pushing), tests, check_js.bat. Commit per item.
- Baseline: inline blocks = 30 (`origin/main`). `static/settings.js?v=17` (index.html:94).

## Issues (evidence)
1. Comparison table reply cut off mid-row ("Starting ≈ $").
2. "List 3 AI tools with their logos" / "What is the latest iPhone model" → "live search snippet only contains a definition of 'list' / 'What'": junk search, no sources, no images.
3. Apology "I'm having trouble processing your request" intermittent (2 failures then success same question); app only responds after minimise+reopen.
4. Three-dots message menu does not close on outside tap.
5. Suspect search API returns no image URLs so no images reach chat.

## PART 1 (read-only, file/line evidence) — REPORTED (pre-edit)
### a) Truncation
- `core/brain.py:390-442` `_call_groq_stream`: `max_completion_tokens: 512` (405), `stream: True` (407). Parses SSE `choices[0].delta.content` (431-437) but **ignores `finish_reason`/usage/length** — cannot detect truncation or auto-continue.
- `core/brain.py:501-534` `_call_openai_compat` (OpenRouter/NVIDIA/fallback non-stream): `max_tokens: 1024` (506), non-stream.
- `core/brain.py:445-488` `_call_llm_cluster_stream`: Groq streams first; if Groq fails **before** output, falls back to `_call_llm_cluster` non-stream and yields full result as one chunk (485-488). If Groq fails **after** emitting partial output, it returns (466-476 context) — cannot safely continue on another provider mid-stream without duplication.
- `core/brain.py:1851+` `stream_respond` drives streaming; `core/brain.py:1901-2110` research/response path.
- Client receives tokens via `app.py:2864-2909` `_dispatch_chat_stream` → `marcus.stream_respond` → SSE `{"token":...}` (2893-2899); on exception yields `{"error":...}` (2900-2901). `/chat/stream` route `app.py:3323-3370` dispatches by capabilities.
- **Why table stopped:** Groq hard cap 512 completion tokens + no `finish_reason=length` detection/continue. A markdown/ASCII table mid-row ("Starting ≈ $") is consistent with hitting the cap.

### b) Search
- Intent/classification: `core/external_apis.py:409+` `classify_research_intent` (LLM, max_tokens 10, returns intent/domain incl. `search`); `107+` `parse_directed_search`. Brain `core/brain.py:1902-1927` sets intent/research_domain, builds `search_message` (1923/1926 continuation expansion) and calls `si.research_with_fetch(search_message, directed_site, research_domain)` (1938-1945).
- Query planning: `core/source_intelligence.py:505-592` `plan_search` builds `plan.queries` from the **full message** (545 `queries=[text]`) plus 1-2 domain supplements (583, capped 3). No filler stripping ("list", "what is") observed in this path.
- Providers/chain: `core/external_apis.py:819-907` `_search_tinyfish` uses `TINYFISH_API_KEY` (740-741) → `GET https://api.search.tinyfish.ai` (822-825, params `query, num, fast`). **Captured fields per result:** `title` (r.title), `snippet` (r.snippet), `url` (r.url), `domain` (r.site_name/urlparse netloc), `published: ""` (889-903). **No `image`, `thumbnail`, `og:image`, `image_url` fields captured or passed through.** `_record_structured_results` stores those dicts; `_format_items` renders title/url/snippet text. `909-934` `_search_general_web` = TinyFish → DDG Lite → Wikipedia. DDG `937-978` returns title/snippet only. Wikipedia `981-1012` on direct lookup miss retries `wikipedia.summary(query.split()[0], ...)` (997) — **`"What is the latest iPhone model".split()[0]` = `What`, `"List 3 AI tools..."` → `List`** (root cause of junk "definition of list/What"). No og:image/thumbnail/page-metadata extraction route found (`core/external_apis.py` has only SSRF/private-blocking for TinyFish *fetch* 749/765; `app.py`/core search shows no og:image/twitter:image/proxy/metadata routes).
- **Real TinyFish response shape (code-derived, no live call made in this read-only step):** code reads `resp.json().results[]` objects and only uses `title,snippet,url,site_name`; does not read any image/thumbnail/media fields even if the API returned them. Unknown from code alone whether TinyFish returns images — must be confirmed empirically; regardless, current code drops them.

### c) Apology / intermittent + backgrounded hang
- Backend apology: `core/brain.py:2074-2084` in `stream_respond` — when `_call_llm_cluster_stream` raises (all providers failed), logs and yields `I'm having trouble processing your request right now. Please try again in a moment.` then returns. `2108-2110` catches unhandled `stream_respond` exception and returns silently (no user message). Mid-stream Groq failure after partial output returns (445-476) — partial answer may be left incomplete with no apology/continue.
- `/chat/stream` dispatch `app.py:2864-2909` (`_dispatch_chat_stream`, main text path): streams `marcus.stream_respond` tokens/dicts (2893-2899); on exception yields SSE `{"error": str(exc)}` (2900-2901) and still sends `done` (2903). Video-text stream `3260-3320` similar (3300-3310). Route wrapper `3323-3370`.
- Client stream: `index.html:11443-11921` uses raw `fetch('/chat/stream', {signal: _sendAbortController.signal})` (11529-11548). `_sendAbortController` global 6726, created 11450 with **no timeout timer**; abort only manual/UI paths? (11449 aborts previous). **No stream read timeout** — a stalled SSE/body read can hang indefinitely. On `streamError`/parse issues it falls back to non-stream `POST /chat` via `apiFetch` with `timeoutMs:120000` (11921-11933). `apiFetch` (4268-4294) adds AbortController timeout (default 30s) — not used for stream.
- **No `visibilitychange` handler aborts/retries chat send** (`index.html:9092-9101` only pauses mic/assistant waveform RAF). Backgrounding can stall/suspend the fetch/stream; on resume nothing detects a dead/hung stream or retries → matches "only responds after minimise and reopen" symptom pattern.
- Intermittency consistent with transient provider/key/rate/config failures hitting the all-providers-failed path (2080) or stream errors surfaced via SSE error + fallback behavior.

### d) Menu
- `index.html:4649-4671` `toggleMessageContextMenu(e,msgIndex)`: prevents default, toggles `.open` on `#messageContextMenu` (2718), positions, sets `menu._sourceWrap`/`dataset.activeMsgIndex`. `4673-4681` `closeMessageContextMenu()`: removes `.open`, clears inline pos/data attrs; **adds/removes no document listeners**. `4682-4705` `handleMessageContextAction` closes after action.
- Global handlers: Escape `4640-4647` closes only `#personaMenu`/`#plusMenu` — **not `messageContextMenu`**. Document click closes `userMessageMenu` (4812) and other menus (6328); **no outside click/touchstart/scroll handler closes `#messageContextMenu`**. Only `toggle`/action/Escape-on-wrong-menus — so outside tap leaves it open.

### e) Storage
- `core/memory.py:519-541` `add_message(chat_id, role, content, timestamp, image_data="", image_url="", video_url="")` stores `image_data`/`image_url`/`video_url` (534-539). No structured image entries (`{url,source,caption,credit,...}`) or per-source search-image arrays.
- `core/db.py:4` notes ephemeral FS/Mongo source of truth; `126` shows usage counters — message/chat schema not centrally enforced here (stored via memory/db collections; schemaless-friendly). `app.py:911-947` history routes return `marcus.memory.get_chat(chat_id)` messages as-is (`/chat/history`, `/api/chat/messages`).
- User-uploaded images: client sends `image` in chat payload (seen around 11484+ in recent context; 11926 fallback includes `image`), persisted via memory `image_data/image_url` paths depending on flow; reload returns stored message fields. Exact upload persistence path not fully traced in Part 1 beyond these fields.
- **No route found** for fetching/proxying images or page metadata/og:image extraction (`rg` in app/core for og:image/twitter:image/proxy metadata returned only SSRF notes in `core/external_apis.py:749,765`).

## PART 2 progress
1. [DONE, committing] Raise reply limits (Groq stream/non-stream 4096, openai-compat 4096); `_call_groq_stream` detects `finish_reason` and emits control event; `stream_respond` auto-continues up to 3 times on `finish_reason==length` (new LLM calls per continuation; no mid-stream provider splice) so tables/long answers not cut off; emits `stream_continue`/`truncated` events. Client handles `stream_continue` and shows "Continue response" button on `truncated`. On stream failure before any output, tries non-stream cluster once before apology; never replaces already-emitted partial output with apology.
2. [DONE, committed] Clean search query (strip filler like "list"/"what is") via `core/external_apis.py:clean_search_query()` used by TinyFish/DDG + `core/source_intelligence.plan_search`; require real results before claiming / `LIVE_DATA_UNAVAILABLE` treated as no live context; on search intent with no reliable results pass search-unavailable directive so model answers from general knowledge + brief note, never refuses (`core/brain.py` `_groq_messages` `search_note`, `stream_respond`). Fixed Wikipedia `query.split()[0]` junk fallback: cleaned query, title-search fallback, rejects disambiguation/definition hits unrelated to request (`core/external_apis.py:_search_wikipedia/_wikipedia_result_relevant`).
3. [TODO] Client ~45s stream timeout; on timeout/error/visibilitychange-visible+dead stream retry once before error; backend tries next provider before apology where safe.
4. [TODO] messageContextMenu closes on outside tap, touchstart, Escape, scroll, after item chosen; remove listeners on close; one menu at a time.
3. [TODO] Client ~45s stream timeout; on timeout/error/visibilitychange-visible+dead stream retry once before error; backend tries next provider before apology where safe.
4. [TODO] messageContextMenu closes on outside tap, touchstart, Escape, scroll, after item chosen; remove listeners on close; one menu at a time.

## PART 3 plan (after Part 2 tests pass)
- Search-sourced images priority (1)-(5) as specified; NEVER generate logos/products/people/places; no private-individual images. Server-side og fetch 3s, size+content-type limits, https only, block private/local. Per source `{title,url,site,date,snippet,image,image_credit}`. Render/citations/persist/fallback/sanitise as specified. Make one real, small request to TinyFish in a test script and print which fields it returns (images?).

## Env vars / deps (name BEFORE adding)
- Existing/used: `TINYFISH_API_KEY` (`core/external_apis.py:740-741`) - no change planned.
- Planned additions: none at this time. Part 3 og/image fetch can use existing `httpx`/`requests`/stdlib; if any new env var or dependency becomes necessary, I will name it BEFORE adding it.

## Backend files changed so far (before push)
- Part 2.2: `core/external_apis.py`, `core/source_intelligence.py`, `core/brain.py`. (More possible: `app.py`, `core/memory.py`.) Final exact list declared before pushing.
- Also allowed: `index.html`, `static/settings.js`, `tests/*`, `check_js.bat`, `NOTES.md`.
