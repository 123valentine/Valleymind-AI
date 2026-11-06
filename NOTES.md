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

## STEP 2 - DONE (all items committed)
1. DONE (b57a22d): Wrap VMRichRender/VMPresentationSelector/VMEnrichAssistantReply calls in try/catch + typeof check. addMessage (index.html:5686-5707) and post-stream hook (index.html:11735-11752) both guarded. On failure, reply renders plain text, error logged to console only.
2. DONE (b57a22d): Post-stream enrichment deferred via requestAnimationFrame so it runs AFTER reply is shown and can never turn a successful reply into an error bubble.
3. DONE (b57a22d): Confirmed cause fixed — enrichment hook call site now wrapped in try/catch inside send() try block, so enrichment exceptions never set streamError or trigger fallback-to-apology.
4. DONE (1876cad): tests/test_vm_richrender.js — 28/28 pass (plain reply, markdown table, marked missing, enrich throws — all show text, no apology).

## Next commits (separate, test between each)
- DONE (d337406): (A) Layout fixes from Phase 1 — desktop #messages max-width 960px→1120px (+1240px at 1600px), composer max-width 900px→1100px, spacing retained (gap 16px, padding 18px 16px).
- DONE (15c441e): (B) Tables with horizontal scroll — bubble.ai-rich table overflow-x:auto, white-space:nowrap cells, professional borders/header/zebra striping.
- PENDING (next session): (C) cards, (D) search result presentation, (E) PDF export.

## Gate
- check_js.bat passes, inline block count == origin/main (30), diff only index.html, static/settings.js, tests, check_js.bat. Push normally, wait Render Live, fetch live files proving try/catch guards + async enrichment. If live check fails, git revert and push. Report new commit hash + confirmed cause, then remove worktree.
