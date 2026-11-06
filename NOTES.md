# vm-fix18 NOTES (read first on resume)

## Setup
- Worktree `..\vm-fix18` from `origin/main` @ `c6d6713` (detached HEAD). `origin/main` includes the chat formatting upgrade (c6d6713).
- Constraints: no force, no --force-with-lease, no reset/clean/stash, no Android/camera files, don't touch original folder or create files there. Touch only index.html, static/settings.js, tests, check_js.bat. Commit after each item. Keep NOTES.md untracked in worktree.
- Baseline inline block count = 30 (matches origin/main).
- DO NOT revert the chat formatting upgrade; FIX it.

## PROBLEM: replies show backend apology "I apologize, but I'm having trouble processing your request right now." since the formatting upgrade

## STEP 1 - DONE (read-only, confirmed)
### (a) origin/main
- `git rev-parse origin/main` = c6d6713a1a2132343b406838fc849b3f71e3deb2 (short c6d6713)
- `git log --oneline -6 origin/main`: c6d6713 (chat formatting), 8a0833e (vm-fix16 test), 8e84026, 3e56b45, 8549b2 (vm-b tests), 6d8af06 (vm-b STEP2)

### (b) Apology source = BACKEND, not client
- `core/brain.py:2065`: `fallback = "I apologize, but I'm having trouble processing your request right now."` — returned when `_call_llm_cluster_stream` raises (all LLM providers fail).
- `core/brain.py:309-311`: `api_key = config.groq_api_key; if not api_key: raise RuntimeError("GROQ_API_KEY is not configured.")` — Groq unconfigured.
- `.env` does NOT exist in worktree → Groq key missing → providers fail → apology.
- **Confirmed cause (backend)**: apology is server-generated; client cannot produce this text. Unrelated to client formatting change.

### (c) Client-side amplification path (confirmed in index.html)
- Enrichment hook at index.html:11733-11740 is INSIDE `send()`'s try block (line 11430) WITHOUT its own try/catch.
- `send()` catch at line 11816 catches ANY exception from try block, sets `streamError`, triggers fallback to `/chat` (line 11818+) which returns backend apology.
- So IF enrichment threw, it would be converted to apology via fallback path.
- VMEnrichAssistantReply (settings.js) HAS internal try/catch, but the CALL SITE in index.html does NOT.
- marked loads at index.html:78 (CDN), settings.js loads at :97 (`?v=16`) → marked available before settings.js. Enrichment runs at stream time, so marked should be present.

### (d) Backend/provider separate cause (unrelated to client change)
- GROQ_API_KEY not configured (.env missing) → `_call_groq` raises RuntimeError (brain.py:310-311)
- `_call_llm_cluster_stream` (brain.py:428) rotates to fallbacks (OpenRouter/NVIDIA/Gemini/ProviderManager); raises only if ALL fail
- When all fail → brain.py:2065 apology
- **Unrelated to client change, but is the primary reason user sees the apology.**

## STEP 2 - FIX plan
1. Wrap every call to VMRichRender, VMPresentationSelector, VMEnrichAssistantReply in try/catch + typeof check. On failure, reply renders exactly as before (plain text), error only logged to console. Enrichment must never run inside the code path that decides request success.
2. Run enrichment asynchronously (requestAnimationFrame) AFTER reply is fully shown, so it can never turn a successful reply into an error bubble.
3. Fix confirmed cause: index.html enrichment hook call site lacks try/catch inside send() try block → wrap it.
4. Tests: plain reply, markdown+table reply, marked missing, VMEnrichAssistantReply throws — all show text, no apology.

## Next commits (separate, test between each)
- (A) Layout fixes from Phase 1 (width, spacing, avatar layer)
- (B) Tables with horizontal scroll
- Stop after A and B this session; write (C) cards, (D) search results, (E) PDF export into NOTES.md.

## Gate
- check_js.bat passes, inline block count == origin/main (30), diff only index.html, static/settings.js, tests, check_js.bat. Push normally, wait Render Live, fetch live files proving try/catch guards + async enrichment. If live check fails, git revert and push. Report new commit hash + confirmed cause, then remove worktree.
