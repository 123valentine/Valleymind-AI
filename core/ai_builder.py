"""AI Builder — project planning and code generation powered by the OpenCode API.

The engine is the OpenCode Zen API (an OpenAI-compatible HTTP endpoint). Every
call is made from THIS backend process; the browser only ever talks to the
ValleyMind backend, so the API key never leaves the server.

Security contract:
  - The API key is read ONLY from the OPENCODE_API_KEY environment variable.
  - No endpoint ever returns the key to the client.
  - Generated projects live under memory_data/users/<user_id>/ai_builder/ so
    each user can only reach their own builds.
"""

import io
import json
import os
import re
import secrets
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.config import PROJECT_ROOT

ZEN_BASE_URL = "https://opencode.ai/zen/v1"

PROJECTS_ROOT_TEMPLATE = str(PROJECT_ROOT / "memory_data" / "users" / "{user_id}" / "ai_builder")
SAFE_PID_RE = re.compile(r"^[a-zA-Z0-9_-]{4,80}$")

_models_cache = {"at": 0.0, "models": []}
_MODELS_TTL_SECONDS = 600


# ── Configuration (env only) ────────────────────────────────────────────────

def api_key() -> str:
    return os.getenv("OPENCODE_API_KEY", "").strip()


def base_url() -> str:
    return os.getenv("OPENCODE_BASE_URL", ZEN_BASE_URL).strip().rstrip("/")


# Planning (clarify + plan) has no automatic failover, so it uses a fast,
# NON-reasoning free model. deepseek-v4-flash-free and hy3-free are chain-of-
# thought models that emit everything as reasoning_content and spend their whole
# token budget "thinking" — at the manifest step (small budget) they returned
# zero answer content and the build died. nemotron-3.5-lightning-free and
# laguna-s-2.1-free return clean JSON directly (finish_reason=stop), so planning
# stays well under the gunicorn timeout. Build generation uses the big-pickle
# flagship with automatic failover to these free models (see BUILDER_TIERS).
def plan_model() -> str:
    return os.getenv("OPENCODE_PLAN_MODEL", "nemotron-3.5-lightning-free").strip()


def build_model() -> str:
    return os.getenv("OPENCODE_BUILD_MODEL", "nemotron-3.5-lightning-free").strip()


# ── Branded builder tiers ────────────────────────────────────────────────────
# The UI shows ONLY the ValleyMind Builder branding. The real OpenCode model ids
# below never reach the browser unless Developer Mode is on (creator-gated). Add
# or re-rank tiers here — no UI change is required, the dropdown is server-driven.
# vmb4 (big-pickle) is the flagship default: it is the exact model used by the
# OpenCode coding agent that builds ValleyMind itself, so Builder output matches
# the session's quality. big-pickle shares the account's free-tier usage quota
# (HTTP 429 FreeUsageLimitError when it is exhausted), so the tiers below it are
# automatic fallbacks that keep every build running.
BUILDER_TIERS = [
    {"id": "vmb1", "label": "ValleyMind Builder 1.0", "model": "mimo-v2.5-free",        "note": "Quick drafts and small projects."},
    {"id": "vmb2", "label": "ValleyMind Builder 2.0", "model": "laguna-s-2.1-free",     "note": "Everyday building."},
    {"id": "vmb3", "label": "ValleyMind Builder 3.0", "model": "nemotron-3-ultra-free", "note": "Balanced quality and speed."},
    {"id": "vmb4", "label": "ValleyMind Builder 4.0", "model": "big-pickle",            "note": "Flagship — the model powering ValleyMind Studio."},
    {"id": "vmb5", "label": "ValleyMind Builder 5.0", "model": "nemotron-3.5-lightning-free", "note": "Fast, reliable fallback for high demand."},
]
DEFAULT_BUILDER_ID = os.getenv("OPENCODE_DEFAULT_BUILDER", "vmb4").strip() or "vmb4"


def _tier_by_id(bid):
    for t in BUILDER_TIERS:
        if t["id"] == bid:
            return t
    return None


def builder_options(dev: bool = False) -> list:
    """Branded builder options for the UI. The real `model` id is included ONLY
    when dev is True (Developer Mode). Tiers whose model isn't live are dropped,
    and Builders currently in a health cooldown are hidden automatically — they
    reappear on their own once healthy again (no code change needed)."""
    avail = set(available_models())
    tiers = [t for t in BUILDER_TIERS if (not avail or t["model"] in avail)] or list(BUILDER_TIERS)
    # Hide temporarily-unhealthy Builders; keep the list non-empty as a fallback.
    healthy = [t for t in tiers if is_available(t["model"])]
    tiers = healthy or tiers
    opts = []
    for t in tiers:
        o = {"id": t["id"], "label": t["label"], "note": t["note"],
             "default": t["id"] == DEFAULT_BUILDER_ID}
        if dev:
            o["model"] = t["model"]
        opts.append(o)
    if opts and not any(o["default"] for o in opts):
        opts[-1]["default"] = True   # keep a default even if the flagship is offline
    return opts


def resolve_builder_model(builder_id: str) -> str:
    """Map a branded builder id -> the real model id (default tier if unknown)."""
    t = _tier_by_id((builder_id or "").strip()) or _tier_by_id(DEFAULT_BUILDER_ID)
    return t["model"] if t else build_model()


def unmapped_free_models() -> list:
    """Live free models not yet mapped to a builder tier — surfaced (dev only) so
    new models can be benchmarked and promoted into the ranking over time."""
    mapped = {t["model"] for t in BUILDER_TIERS}
    return [m for m in available_models() if m.endswith("-free") and m not in mapped]


# ── Health monitoring + failover ordering ────────────────────────────────────
_HEALTH = {}                    # model id -> live stats
_HEALTH_LOCK = threading.Lock()
_COOLDOWN_SECONDS = 120         # how long a failing model is skipped for


def _health_row(model):
    row = _HEALTH.get(model)
    if row is None:
        row = {"calls": 0, "ok": 0, "fail": 0, "timeouts": 0, "builds": 0,
               "total_ms": 0.0, "cooldown_until": 0.0, "last_error": "", "last_ok": 0.0}
        _HEALTH[model] = row
    return row


def record_success(model, ms):
    with _HEALTH_LOCK:
        r = _health_row(model)
        r["calls"] += 1; r["ok"] += 1; r["total_ms"] += max(0.0, ms)
        r["last_ok"] = time.time(); r["cooldown_until"] = 0.0


def record_failure(model, exc):
    with _HEALTH_LOCK:
        r = _health_row(model)
        r["calls"] += 1; r["fail"] += 1
        if isinstance(exc, BuilderModelError) and exc.kind == "timeout":
            r["timeouts"] += 1
        r["last_error"] = str(exc)[:200]
        if _should_failover(exc):                       # unstable -> cool it down
            r["cooldown_until"] = time.time() + _COOLDOWN_SECONDS


def record_build_completed(model):
    with _HEALTH_LOCK:
        _health_row(model)["builds"] += 1


def is_available(model) -> bool:
    return time.time() >= _HEALTH.get(model, {}).get("cooldown_until", 0.0)


def health_snapshot() -> dict:
    """Per-tier health for the creator dashboard / notifications."""
    out = {}
    now = time.time()
    with _HEALTH_LOCK:
        for t in BUILDER_TIERS:
            m = t["model"]; r = _HEALTH.get(m, {})
            calls, ok = r.get("calls", 0), r.get("ok", 0)
            out[t["id"]] = {
                "label": t["label"], "model": m,
                "available": now >= r.get("cooldown_until", 0.0),
                "cooldown_s": max(0, int(r.get("cooldown_until", 0.0) - now)),
                "calls": calls, "success": ok, "failures": r.get("fail", 0),
                "timeouts": r.get("timeouts", 0),
                "success_rate": round(ok / calls, 3) if calls else None,
                "avg_ms": round(r.get("total_ms", 0.0) / ok) if ok else None,
                "builds_completed": r.get("builds", 0),
                "last_error": r.get("last_error", ""),
            }
    return out


def _rank(tier) -> int:
    try:
        return BUILDER_TIERS.index(tier)      # later in the list = higher tier
    except ValueError:
        return -1


def build_candidates(builder_id=None, model=None) -> list:
    """Ordered tiers to attempt for a build: the user's selection first, then the
    remaining tiers by rank (best first), with unavailable models pushed last.
    Only models the API currently lists are included."""
    avail_api = set(available_models())
    tiers = [t for t in BUILDER_TIERS if (not avail_api or t["model"] in avail_api)] or list(BUILDER_TIERS)
    sel = _tier_by_id(builder_id)
    if model and not sel:                     # creator raw-model override
        sel = {"id": "custom", "label": "Custom model", "model": model, "note": ""}
    if not sel:                               # default to the flagship builder
        sel = _tier_by_id(DEFAULT_BUILDER_ID)
    rest = sorted(
        [t for t in tiers if not sel or t["model"] != sel["model"]],
        key=lambda t: (0 if is_available(t["model"]) else 1, -_rank(t)),
    )
    ordered = ([sel] if sel else []) + rest
    seen, out = set(), []
    for t in ordered:
        if t["model"] in seen:
            continue
        seen.add(t["model"]); out.append(t)
    return out


def configured() -> bool:
    return bool(api_key())


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {api_key()}"}


def _user_facing_error(exc) -> str:
    """Map any Builder/model error to a friendly, provider-agnostic message.
    NEVER leaks raw provider output, status bodies, provider names or keys — the
    full technical error is kept only in the (creator-only) health metrics."""
    msg = str(exc) or repr(exc)
    low = msg.lower()
    status = getattr(exc, "status", None)
    kind = getattr(exc, "kind", None)
    if status == 429 or kind == "rate_limit" or "429" in msg:
        return "The AI Builders are busy right now — ValleyMind is automatically retrying."
    if status in (500, 502, 503, 504) or kind in ("timeout", "connection") \
            or any(s in low for s in ("503", "502", "500", "504", "timeout", "unavailable",
                                      "upstream request failed", "server_error", "connection")):
        return ("One of our AI Builders is temporarily unavailable. ValleyMind is automatically "
                "switching to another available Builder.")
    if status == 402 or "creditserror" in low or "no payment method" in low or "402" in msg:
        return "The AI Builder is temporarily over capacity. Please try again shortly."
    if status == 401 or "unauthorized" in low or "invalid_api_key" in low or "not configured" in low or "401" in msg:
        return "The AI Builder is having a temporary configuration issue on our side. Please try again shortly."
    if "could not design" in low:
        return "The Builder couldn't plan this project just now. Please try Regenerate."
    if any(s in low for s in ("no files", "no output", "empty")):
        return "The Builders were unavailable, so nothing could be generated. Please try again in a moment."
    return "The AI Builder ran into a temporary problem. Please try again."


# ── OpenCode Zen chat client ────────────────────────────────────────────────

def available_models() -> list:
    if not configured():
        return []
    now = time.time()
    if _models_cache["models"] and (now - _models_cache["at"]) < _MODELS_TTL_SECONDS:
        return _models_cache["models"]
    try:
        resp = httpx.get(f"{base_url()}/models", headers=_auth_headers(), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
            _models_cache.update(at=now, models=ids)
            return ids
    except Exception as exc:
        print(f"[AI BUILDER] models fetch failed: {exc}")
    return list(_models_cache["models"])


class BuilderModelError(RuntimeError):
    """A model call failed. ``retryable`` marks provider/availability failures
    (5xx, 429, timeout, connection) that should trigger automatic failover to
    the next Builder model — vs. hard errors (bad key/credits/request)."""

    def __init__(self, message, status=None, retryable=False, kind=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.kind = kind or ("rate_limit" if status == 429 else ("http" if status else "error"))
        self.retry_after = retry_after


# 5xx + rate-limit + transient statuses -> failover; 4xx (auth/credits/bad req) -> hard fail.
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _should_failover(exc) -> bool:
    """Whether an exception means 'try the next Builder model'. Covers provider
    errors AND a model that returns no usable output (junk/empty)."""
    if isinstance(exc, BuilderModelError):
        return exc.retryable
    msg = str(exc).lower()
    return any(s in msg for s in ("could not design", "no output", "empty file",
                                  "unreadable", "temporarily unavailable"))


def _is_provider_down(exc) -> bool:
    """A provider/availability failure (5xx, 429, timeout, connection) — as opposed
    to a per-file content problem (empty/junk). Used to decide pause-vs-skip."""
    return isinstance(exc, BuilderModelError) and exc.retryable and exc.kind != "empty"


def chat_stream(model: str, messages: list, max_tokens: int = 8192, temperature: float = 0.3):
    """Stream a chat completion from the OpenCode API.

    Yields dicts: {"content": str, "reasoning": str, "finish": str|None}.
    Raises BuilderModelError (with a retryable flag) on HTTP/transport errors.
    """
    key = api_key()
    if not key:
        raise BuilderModelError("OPENCODE_API_KEY is not configured", retryable=False)
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        with httpx.stream(
            "POST",
            f"{base_url()}/chat/completions",
            headers=_auth_headers(),
            json=payload,
            timeout=(30.0, 900.0),
        ) as resp:
            if resp.status_code != 200:
                body = ""
                try:
                    body = resp.read().decode("utf-8", "replace")[:500]
                except Exception:
                    pass
                raise BuilderModelError(
                    f"OpenCode API HTTP {resp.status_code}: {body}",
                    status=resp.status_code,
                    retryable=resp.status_code in _RETRYABLE_STATUS,
                    retry_after=resp.headers.get("retry-after"),
                )
            for raw_line in resp.iter_lines():
                line = str(raw_line or "").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                delta = choice.get("delta") or {}
                yield {
                    "content": delta.get("content") or "",
                    "reasoning": delta.get("reasoning_content") or "",
                    "finish": choice.get("finish_reason"),
                }
    except httpx.TimeoutException as exc:
        raise BuilderModelError(f"OpenCode API timeout: {exc}", retryable=True, kind="timeout")
    except httpx.RequestError as exc:
        raise BuilderModelError(f"OpenCode API connection error: {exc}", retryable=True, kind="connection")


def _extract_json(text: str):
    """Best-effort extraction of a JSON value (object or array) from a model reply."""
    text = (text or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for start_ch, end_ch in (("{", "}"), ("[", "]")):
        start = text.find(start_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == start_ch:
                depth += 1
            elif c == end_ch:
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
    return None


def _accumulate(model, messages, max_tokens, temperature, on_delta=None):
    """Stream a completion, returning (content, reasoning, finish_reason).

    Both channels are returned so callers can fall back to ``reasoning`` when a
    reasoning model emits its answer there (or when the content channel is
    truncated). ``on_delta`` receives each content chunk as it arrives.
    """
    content, reasoning, finish = [], [], None
    for ev in chat_stream(model, messages, max_tokens=max_tokens, temperature=temperature):
        chunk = ev.get("content") or ""
        if chunk:
            content.append(chunk)
            if on_delta:
                on_delta(chunk)
        thought = ev.get("reasoning") or ""
        if thought:
            reasoning.append(thought)
        if ev.get("finish"):
            finish = ev.get("finish")
    return "".join(content), "".join(reasoning), finish


def _stream_json(model, messages, max_tokens, temperature, on_delta=None):
    """Stream a JSON-returning completion, resilient to reasoning models.

    Tries the content channel, then the reasoning channel. If the model ran out
    of budget mid-thought (finish_reason == "length") with nothing parseable, it
    asks once more for the final JSON only, with extra head-room. Returns the
    parsed value (dict/list) or None.
    """
    content, reasoning, finish = _accumulate(model, messages, max_tokens, temperature, on_delta)
    parsed = _extract_json(content)
    if parsed is None:
        parsed = _extract_json(reasoning)
    if parsed is None and finish == "length":
        followup = list(messages) + [
            {"role": "assistant", "content": (content or reasoning)[-1500:]},
            {"role": "user", "content": "Output ONLY the final JSON now — no reasoning, no explanation, no markdown fences."},
        ]
        content2, reasoning2, _ = _accumulate(model, followup, max(max_tokens, 8000), temperature)
        parsed = _extract_json(content2)
        if parsed is None:
            parsed = _extract_json(reasoning2)
    return parsed


# ── Prompt templates ────────────────────────────────────────────────────────

CLARIFY_SYSTEM = (
    "You are ValleyMind AI Builder's planning assistant. A user described a software "
    "project they want to build. Ask up to 5 focused clarifying questions that will let "
    "you produce an excellent project specification. Only ask questions that genuinely "
    "matter — skip anything already obvious or answered in the request.\n\n"
    "Return ONLY a JSON object with this exact shape:\n"
    '{"questions": [{"id": "q1", "question": "...", "options": ["A", "B", "C"], "why": "..."}]}\n\n'
    "Rules:\n"
    "- 1 to 5 questions, short and specific.\n"
    "- Each question needs at least 2 concrete option strings.\n"
    '- If the request already gives enough detail that you would ask nothing, return {"questions": []}.\n'
    "- No markdown fences, no text before or after the JSON."
)

PLAN_SYSTEM = (
    "You are ValleyMind AI Builder. Using the user's request, the chosen project type, "
    "and their answers to the clarifying questions, produce a complete, professional "
    "project specification that a senior engineer can build from.\n\n"
    "Return ONLY a JSON object with EXACTLY this structure:\n"
    "{\n"
    '  "project_name": "Short descriptive name",\n'
    '  "project_type": "one of: website, mobile, desktop, api, admin, ai, saas",\n'
    '  "tagline": "One sentence summary",\n'
    '  "project_overview": "2-4 sentence overview.",\n'
    '  "features": ["Feature one", "Feature two"],\n'
    '  "stack": [{"category": "Frontend", "choice": "React + Vite", "reason": "..."}],\n'
    '  "ui_pages": [{"path": "/", "name": "Home", "description": "..."}],\n'
    '  "database_design": [{"entity": "users", "fields": [{"name": "email", "type": "string", "note": "unique"}]}],\n'
    '  "api_design": [{"method": "GET", "path": "/api/users", "purpose": "..."}],\n'
    '  "folder_structure": ["src/", "src/components/"],\n'
    '  "development_roadmap": [{"phase": 1, "title": "...", "tasks": ["..."]}],\n'
    '  "estimated_complexity": {"level": "Medium", "build_time": "1-2 weeks", "effort": "..."}\n'
    "}\n\n"
    "The stack array must recommend: Frontend, Backend, Database, Authentication and "
    "Deployment. Keep arrays rich but concise. No markdown fences, no commentary "
    "before or after the JSON."
)

MANIFEST_SYSTEM = (
    "You are a senior software architect. Given the project specification below, list the "
    "complete set of files needed to build a working, production-ready project. Include "
    "the frontend, backend, database schema/migrations, authentication, configuration, a "
    "README, and an installation guide.\n\n"
    'Return ONLY a JSON array of unique relative file paths with forward slashes, e.g.:\n'
    '["package.json", "README.md", "src/index.js", "src/components/App.jsx", "backend/server.py", "backend/.env.example"]\n\n'
    "Rules:\n"
    "- 8 to 45 files. Every path unique.\n"
    '- Never include "node_modules", ".git", build artifacts, or ".env" with secrets; include ".env.example" instead.\n'
    "- Only text-based source, config and documentation files. Do NOT include binary "
    "assets (favicon.ico, .png/.jpg/.gif images, fonts, audio, video, archives) — "
    "those are added manually, not generated.\n"
    "- Stick to the stack recommended in the spec.\n"
    "- No markdown fences, no extra text."
)

FILE_SYSTEM = (
    "You are a senior developer writing ONE file of a generated project. Produce the "
    "complete, production-ready contents of the file named in the user message.\n\n"
    "Rules:\n"
    "- Output ONLY the raw file contents. No markdown fences, no explanations, no surrounding text.\n"
    "- If the file is a config file (package.json, requirements.txt, etc.), output valid file content.\n"
    "- If the file is README.md, include an installation + run guide.\n"
    "- Never hardcode secrets; reference environment variables and keep a .env.example.\n"
    "- Keep every import/reference consistent with the other planned files listed.\n"
    "- Modern best practices, clean and complete code."
)


# ── Project storage ─────────────────────────────────────────────────────────

# ── Builder chat attachments (describe / clarify / plan) ────────────────────
# Attachments are transient: an image is passed to the model as a multimodal
# part (data URL), a text file is inlined (bounded). NOTHING is written to the
# database or project disk — no Mongo bloat, no leaked user files.
MAX_ATTACHMENTS = 4
ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 200 * 1024


def validate_attachments(attachments) -> tuple:
    """Validate a client-supplied attachments list.

    Returns (clean_list, error_message). Each clean item is
      {"name": str, "type": "image"|"text", ...}
    where image items carry ``data_url`` and text items carry ``content``.
    Any invalid attachment rejects the whole request (400).
    """
    if not attachments:
        return [], ""
    if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS:
        return [], "Attach at most " + str(MAX_ATTACHMENTS) + " files."
    clean = []
    for idx, att in enumerate(attachments):
        if not isinstance(att, dict):
            return [], "Invalid attachment entry."
        name = str(att.get("name") or "file_" + str(idx)).strip()[:120] or "file"
        kind = str(att.get("type") or "").strip().lower()
        mime = str(att.get("mime") or "").strip().lower()
        if kind == "image":
            if mime not in ALLOWED_IMAGE_MIME:
                return [], "Images must be PNG, JPEG, GIF or WebP."
            data_url = str(att.get("data_url") or "").strip()
            if not data_url.startswith("data:") or "," not in data_url:
                return [], "Image attachment is missing its data payload."
            raw_size = len(data_url.split(",", 1)[1]) * 3 // 4   # approx base64 -> bytes
            if raw_size > MAX_IMAGE_BYTES:
                return [], "Image is too large (max 5 MB)."
            clean.append({"name": name, "type": "image", "mime": mime, "data_url": data_url})
        elif kind == "text":
            content = str(att.get("content") or "")
            if len(content.encode("utf-8")) > MAX_TEXT_CHARS:
                return [], "Text file is too large (max 200 KB)."
            clean.append({"name": name, "type": "text", "mime": mime or "text/plain", "content": content})
        else:
            return [], "Only image and text files can be attached."
    return clean, ""


def _user_message_with_attachments(message: str, attachments) -> object:
    """Build the chat user message: a plain string when there are no attachments,
    otherwise an OpenAI-style multimodal content list (image_url parts for images,
    inline text parts for text files)."""
    if not attachments:
        return message
    parts = [{"type": "text", "text": message}]
    for att in attachments:
        if att["type"] == "image":
            parts.append({"type": "image_url", "image_url": {"url": att["data_url"]}})
        else:
            snippet = att["content"][:MAX_TEXT_CHARS]
            parts.append({"type": "text",
                          "text": f"[Attached file: {att['name']}]\n{snippet}"})
    return parts


def _strip_image_parts(exc, messages):
    """Some free models reject multimodal image parts (HTTP 400). When that
    happens and the request contained an image, rebuild the messages with the
    images dropped but text files preserved — so attachments never break a build.
    Returns the stripped message list, or None when there is nothing to retry."""
    if not isinstance(exc, BuilderModelError) or exc.status != 400:
        return None
    has_image = False
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            has_image = any(p.get("type") == "image_url" for p in content)
            if has_image:
                break
    if not has_image:
        return None
    stripped = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            kept = [p for p in content if p.get("type") != "image_url"]
            content = kept or [{"type": "text", "text": "(image attachment omitted)"}]
        stripped.append({"role": m.get("role"), "content": content})
    return stripped


def projects_root(user_id: str) -> Path:
    return Path(PROJECTS_ROOT_TEMPLATE.replace("{user_id}", user_id))


def new_project_id() -> str:
    return "aib_" + secrets.token_hex(6)


def project_dir(user_id: str, pid: str) -> Path:
    if not SAFE_PID_RE.match(pid or ""):
        raise ValueError("Invalid project id")
    return projects_root(user_id) / pid


def resolve_project(user_id: str, pid: str):
    """Return the project directory or None if it does not exist."""
    try:
        proj = project_dir(user_id, pid)
    except ValueError:
        return None
    if proj.is_dir():
        return proj
    return None


def _clean_file_path(path: str) -> str:
    parts = []
    for part in str(path or "").replace("\\", "/").strip().strip("/").split("/"):
        if part in ("", ".", ".."):
            continue
        parts.append(part)
    if not parts:
        raise ValueError("Empty file path")
    return "/".join(parts)


def _safe_target(proj: Path, rel: str) -> Path:
    target = (proj / _clean_file_path(rel)).resolve()
    root = proj.resolve()
    if not target.is_relative_to(root):
        raise ValueError("Invalid file path")
    return target


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:[a-zA-Z0-9_+\-]*)\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text).strip()
    return text


def save_project_file(proj: Path, rel: str, content: str) -> Path:
    target = _safe_target(proj, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_strip_fences(content) or "", encoding="utf-8")
    return target


def _render_spec(spec) -> str:
    if isinstance(spec, str):
        return spec
    if not isinstance(spec, dict):
        return json.dumps(spec, ensure_ascii=False)
    return json.dumps(spec, ensure_ascii=False, indent=2)


def _format_answers(answers) -> str:
    if not answers:
        return "(none provided — use your best judgment)"
    if isinstance(answers, dict):
        return "\n".join(f"- {k}: {v}" for k, v in answers.items() if v)
    return str(answers)


def build_tree(proj: Path) -> list:
    nodes = []
    root = proj.resolve()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.name == "project.json":
            continue
        nodes.append({
            "path": rel,
            "name": p.name,
            "type": "dir" if p.is_dir() else "file",
            "size": p.stat().st_size if p.is_file() else 0,
        })
    return nodes


def project_meta(proj: Path) -> dict:
    meta_path = proj / "project.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_project_meta(proj: Path, meta: dict):
    meta_path = proj / "project.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def zip_project_bytes(proj: Path) -> bytes:
    buf = io.BytesIO()
    root = proj.resolve()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name != "project.json":
                zf.write(p, arcname=p.relative_to(root).as_posix())
    return buf.getvalue()


def list_user_projects(user_id: str) -> list:
    root = projects_root(user_id)
    projects = []
    if not root.is_dir():
        return projects
    for proj in sorted(root.iterdir()):
        if not proj.is_dir():
            continue
        meta = project_meta(proj)
        if not meta:
            continue
        files = [n for n in build_tree(proj) if n.get("type") == "file"]
        projects.append({
            "id": proj.name,
            "name": meta.get("name") or meta.get("project_name") or proj.name,
            "created_at": meta.get("created_at", ""),
            "status": meta.get("status", ""),
            "file_count": len(files),
            "total_size": sum(n.get("size", 0) for n in files),
        })
    projects.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return projects


# ── Clarify + plan streaming generators ─────────────────────────────────────

DEFAULT_QUESTIONS = [
    {"id": "q_goal", "question": "What is the main goal of this project?", "options": ["Personal use / portfolio", "Client or business deliverable", "Startup / product prototype", "Learning / experiment"], "why": "Goals shape scope, features and complexity."},
    {"id": "q_audience", "question": "Who is the primary audience?", "options": ["The public", "A small team", "Just me / private", "Enterprise users"], "why": "Audience drives UI polish, auth and scalability choices."},
    {"id": "q_platform", "question": "Which platform should it target first?", "options": ["Web (responsive)", "Mobile-first", "Desktop", "API / backend only"], "why": "Platform determines the whole stack."},
    {"id": "q_auth", "question": "Do users need accounts / authentication?", "options": ["Yes, with login & signup", "No accounts needed", "Social login only", "Admin accounts only"], "why": "Auth is a major architectural decision."},
    {"id": "q_deploy", "question": "Where do you plan to deploy / run it?", "options": ["Free cloud (Render/Vercel/Netlify)", "Your own server", "Not sure yet", "App stores (mobile)"], "why": "Deployment target influences build steps and hosting config."},
]


def clarify_generator(model: str, message: str, project_type: str, attachments=None):
    yield {"type": "status", "message": "Analyzing your request and preparing clarifying questions..."}
    user_content = _user_message_with_attachments(
        f"Project type: {project_type}\n\nWhat the user wants to build:\n{message}",
        attachments or [])
    messages = [
        {"role": "system", "content": CLARIFY_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    buf = ""
    reasoning_buf = ""
    finish = None
    try:
        for ev in chat_stream(model, messages, max_tokens=3000, temperature=0.3):
            content = ev.get("content") or ""
            if content:
                buf += content
                yield {"type": "delta", "text": content}
            reasoning_buf += ev.get("reasoning") or ""
            if ev.get("finish"):
                finish = ev.get("finish")
    except Exception as exc:
        # Some free models reject image parts (400). Drop images, keep the text.
        stripped = _strip_image_parts(exc, messages)
        if stripped is not None:
            messages = stripped
            buf, reasoning_buf, finish = "", "", None
            try:
                for ev in chat_stream(model, messages, max_tokens=3000, temperature=0.3):
                    content = ev.get("content") or ""
                    if content:
                        buf += content
                        yield {"type": "delta", "text": content}
                    reasoning_buf += ev.get("reasoning") or ""
                    if ev.get("finish"):
                        finish = ev.get("finish")
            except Exception as exc2:
                yield {"type": "error", "message": _user_facing_error(exc2)}
                return
        else:
            yield {"type": "error", "message": _user_facing_error(exc)}
            return
    parsed = _extract_json(buf) or _extract_json(reasoning_buf)
    if parsed is None and finish == "length":
        try:
            parsed = _stream_json(model, messages, max_tokens=8000, temperature=0.3)
        except Exception:
            parsed = None
    questions = []
    if isinstance(parsed, dict):
        qs = parsed.get("questions")
        if isinstance(qs, list):
            questions = [q for q in qs if isinstance(q, dict) and q.get("question")]
    if not questions:
        questions = DEFAULT_QUESTIONS
        yield {"type": "result", "questions": questions, "fallback": True}
        return
    yield {"type": "result", "questions": questions, "fallback": False}


def plan_generator(model: str, message: str, project_type: str, answers=None, attachments=None):
    yield {"type": "status", "message": "Crafting your project specification (this can take up to a minute)..."}
    user_content = _user_message_with_attachments(
        (f"Project type: {project_type}\n\n"
         f"What the user wants to build:\n{message}\n\n"
         f"Answers to clarifying questions:\n{_format_answers(answers)}"),
        attachments or [])
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    buf = ""
    reasoning_buf = ""
    finish = None
    try:
        for ev in chat_stream(model, messages, max_tokens=12000, temperature=0.4):
            content = ev.get("content") or ""
            if content:
                buf += content
                yield {"type": "delta", "text": content}
            reasoning_buf += ev.get("reasoning") or ""
            if ev.get("finish"):
                finish = ev.get("finish")
    except Exception as exc:
        stripped = _strip_image_parts(exc, messages)
        if stripped is not None:
            messages = stripped
            buf, reasoning_buf, finish = "", "", None
            try:
                for ev in chat_stream(model, messages, max_tokens=12000, temperature=0.4):
                    content = ev.get("content") or ""
                    if content:
                        buf += content
                        yield {"type": "delta", "text": content}
                    reasoning_buf += ev.get("reasoning") or ""
                    if ev.get("finish"):
                        finish = ev.get("finish")
            except Exception as exc2:
                yield {"type": "error", "message": _user_facing_error(exc2)}
                return
        else:
            yield {"type": "error", "message": _user_facing_error(exc)}
            return
    spec = _extract_json(buf) or _extract_json(reasoning_buf)
    if not isinstance(spec, dict) and finish == "length":
        try:
            spec = _stream_json(model, messages, max_tokens=16000, temperature=0.4)
        except Exception:
            spec = None
    if not isinstance(spec, dict):
        yield {"type": "error", "message": "The model returned an unreadable plan. Please try again."}
        return
    spec.setdefault("project_type", project_type or "website")
    yield {"type": "result", "spec": spec}


# ── Build streaming generator ───────────────────────────────────────────────

def _thread_call(fn):
    result = {"done": False, "value": None, "error": None}

    def _run():
        try:
            result["value"] = fn()
        except Exception as exc:
            result["error"] = exc
        finally:
            result["done"] = True

    threading.Thread(target=_run, daemon=True).start()
    return result


def _gen_file_content(build_model: str, spec_text: str, path: str, all_paths: list, max_tokens: int = 24000):
    """Generator yielding content chunks for one file (handles output truncation)."""
    system = FILE_SYSTEM
    user = (
        f"<project spec>\n{spec_text}\n</project spec>\n\n"
        f"Planned project files:\n" + "\n".join(f"- {p}" for p in all_paths) +
        f"\n\nGenerate the file: {path}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    chunks = []
    for _attempt in range(4):
        finished = False
        for ev in chat_stream(build_model, messages, max_tokens=max_tokens, temperature=0.3):
            content = ev.get("content") or ""
            if content:
                chunks.append(content)
                yield content
            finish = ev.get("finish")
            if finish == "stop":
                finished = True
                break
            if finish == "length":
                break
        if finished:
            break
        continuation = "".join(chunks)
        if not continuation:
            raise RuntimeError("Model produced no output for " + path)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": continuation},
            {"role": "user", "content": "Continue the file exactly where the previous response stopped. Output ONLY the continuation with no markdown fences."},
        ]
    return "".join(chunks)


def _manifest_for(spec_text: str, model: str) -> list:
    messages = [
        {"role": "system", "content": MANIFEST_SYSTEM},
        {"role": "user", "content": f"Project specification:\n{spec_text}"},
    ]
    parsed = _stream_json(model, messages, max_tokens=6000, temperature=0.2)
    if not isinstance(parsed, list):
        raise RuntimeError("Could not design a file structure for this project")
    cleaned = []
    seen = set()
    for p in parsed:
        try:
            rel = _clean_file_path(p)
        except ValueError:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        cleaned.append(rel)
    if not cleaned:
        raise RuntimeError("Could not design a file structure for this project")
    return cleaned


# Aggregate build + live-session stats for the Creator Dashboard.
_ACTIVE_BUILDS = 0
_BUILD_STATS = {"completed": 0, "failed": 0, "paused": 0, "resumed": 0, "failovers": 0, "total_seconds": 0.0}


def _active_inc():
    global _ACTIVE_BUILDS
    with _HEALTH_LOCK:
        _ACTIVE_BUILDS += 1


def _active_dec():
    global _ACTIVE_BUILDS
    with _HEALTH_LOCK:
        _ACTIVE_BUILDS = max(0, _ACTIVE_BUILDS - 1)


def active_builds() -> int:
    return _ACTIVE_BUILDS


def _record_build_result(ok, seconds, failovers, paused=False, resumed=False):
    with _HEALTH_LOCK:
        _BUILD_STATS["failovers"] += int(failovers or 0)
        if paused:
            _BUILD_STATS["paused"] += 1          # paused != failed (it's resumable)
            return
        if ok:
            _BUILD_STATS["completed"] += 1
            _BUILD_STATS["total_seconds"] += max(0.0, seconds)
            if resumed:
                _BUILD_STATS["resumed"] += 1     # completed via Resume
        else:
            _BUILD_STATS["failed"] += 1


def build_stats_snapshot() -> dict:
    with _HEALTH_LOCK:
        s = dict(_BUILD_STATS)
    completed = s["completed"]
    s["avg_build_seconds"] = round(s["total_seconds"] / completed) if completed else None
    hs = health_snapshot()
    most = max(hs.values(), key=lambda r: r["builds_completed"], default=None)
    s["most_used_builder"] = most["label"] if (most and most["builds_completed"]) else None
    return s


# Binary assets a text LLM cannot produce. SVG is deliberately excluded — it is
# XML markup the model can write. .env is excluded too (spec uses .env.example).
_BINARY_ASSET_RE = re.compile(
    r"\.(ico|png|jpe?g|gif|webp|bmp|tiff?|woff2?|ttf|eot|otf|mp3|mp4|m4a|wav|ogg|pdf|zip|gz|tar|jar|exe|dll|so|dylib|wasm)$",
    re.IGNORECASE,
)


def _is_binary_asset(path: str) -> bool:
    return bool(_BINARY_ASSET_RE.search(path or ""))


# ── Rate-limit backoff, pacing + checkpointing ──────────────────────────────
_RATE_MAX_RETRIES = int(os.getenv("OPENCODE_RATE_MAX_RETRIES", "4") or 4)
_BACKOFF_CAP = 60
_PACE_SECONDS = float(os.getenv("OPENCODE_PACE_SECONDS", "0") or 0)


def _backoff_seconds(attempt, retry_after=None):
    if retry_after is not None:
        try:
            return min(_BACKOFF_CAP, max(1, int(float(retry_after))))
        except Exception:
            pass
    return min(_BACKOFF_CAP, 2 ** (attempt + 1))   # 2, 4, 8, 16, 32, 60


def _checkpoint(proj, updates):
    """Crash-safe: merge progress into project.json so nothing is lost on a
    rate-limit pause or interruption — this is what makes a build resumable."""
    try:
        meta = project_meta(proj) or {}
        meta.update(updates)
        save_project_meta(proj, meta)
        return meta
    except Exception:
        return {}


def _generate_files(proj, spec_text, manifest, candidates, ctx):
    """Stream-generate every manifest file not already on disk. Per file it does:
    failover across models, exponential backoff on rate limits (retrying the same
    model, honoring Retry-After), adaptive pacing, and periodic checkpointing.
    Mutates ``ctx`` and yields SSE events. If every model is rate-limited it sets
    ctx['paused'] and stops — the project is resumable from the last file."""
    total = len(manifest)
    for index, path in enumerate(manifest):
        # Resume: skip files already written on a previous attempt.
        try:
            done_path = _safe_target(proj, path)
        except Exception:
            done_path = None
        if done_path is not None and done_path.exists() and done_path.stat().st_size > 0:
            ctx["done_files"] += 1
            yield {"type": "file_done", "path": path, "index": index, "total": total,
                   "completed": ctx["done_files"], "resumed": True}
            continue

        yield {"type": "file_start", "path": path, "index": index, "total": total}
        if _is_binary_asset(path):
            ctx["skipped_files"].append(path)
            yield {"type": "file_skipped", "path": path, "index": index, "total": total,
                   "message": "Binary asset — add this file manually"}
            continue
        if ctx["pace"] > 0:
            time.sleep(ctx["pace"])               # proactive throttle to avoid rate limits

        outcome = None                             # "saved" | "skipped"
        while outcome is None:
            tier = candidates[ctx["active"]]
            attempt = 0
            last_exc = None
            while True:                            # try this model, backing off on provider errors
                collected = []
                t0 = time.time()
                try:
                    for chunk in _gen_file_content(tier["model"], spec_text, path, manifest):
                        collected.append(chunk)
                        yield {"type": "chunk", "text": chunk, "path": path, "index": index, "total": total}
                    content = "".join(collected)
                    if not content.strip():
                        raise BuilderModelError("Model produced an empty file for " + path,
                                                retryable=True, kind="empty")
                    save_project_file(proj, path, content)
                    record_success(tier["model"], (time.time() - t0) * 1000)
                    outcome = "saved"
                    break
                except GeneratorExit:
                    raise
                except Exception as exc:
                    record_failure(tier["model"], exc)
                    last_exc = exc
                    # Retry the SAME Builder with exponential backoff for provider
                    # errors (5xx / 429 / timeout / connection) before failing over.
                    if _is_provider_down(exc) and attempt < _RATE_MAX_RETRIES:
                        wait = _backoff_seconds(attempt, getattr(exc, "retry_after", None))
                        attempt += 1
                        ctx["pace"] = min(10.0, max(ctx["pace"], 1.0) * 1.5)   # adaptive slow-down
                        reason = "is busy" if getattr(exc, "kind", "") == "rate_limit" else "is temporarily unavailable"
                        yield {"type": "status", "message": tier["label"] + " " + reason
                               + " — retrying in " + str(wait) + "s (attempt "
                               + str(attempt) + "/" + str(_RATE_MAX_RETRIES) + ")..."}
                        yield {"type": "file_start", "path": path, "index": index, "total": total}
                        time.sleep(wait)
                        continue                    # retry the same model
                    break                            # give up on this model

            if outcome == "saved":
                break

            if _should_failover(last_exc) and ctx["active"] + 1 < len(candidates):
                ctx["active"] += 1
                ctx["failover_count"] += 1
                yield {"type": "failover", "message": tier["label"]
                       + " is temporarily unavailable. Switched automatically to "
                       + candidates[ctx["active"]]["label"] + "."}
                yield {"type": "file_start", "path": path, "index": index, "total": total}
                continue                             # retry file on the next model
            if _is_provider_down(last_exc):
                # Every Builder is unavailable (busy/down) — pause instead of failing.
                # Completed files are on disk and the plan is checkpointed, so it resumes.
                ctx["paused"] = True
                _checkpoint(proj, {"status": "paused", "manifest": manifest,
                                   "file_count": ctx["done_files"]})
                yield {"type": "paused", "completed": ctx["done_files"], "total": total,
                       "project_id": proj.name,
                       "message": "All AI Builders are currently busy. Your project has been safely paused ("
                       + str(ctx["done_files"]) + "/" + str(total) + " files done) and can be resumed once "
                       "capacity becomes available."}
                return
            ctx["skipped_files"].append(path)
            yield {"type": "file_error", "path": path, "index": index, "total": total,
                   "message": _user_facing_error(last_exc)}
            outcome = "skipped"

        if outcome == "saved":
            ctx["done_files"] += 1
            yield {"type": "file_done", "path": path, "index": index, "total": total, "completed": ctx["done_files"]}
            if ctx["done_files"] % 3 == 0:           # periodic checkpoint
                _checkpoint(proj, {"status": "building", "manifest": manifest, "file_count": ctx["done_files"]})


def build_generator(user_id: str, spec, builder_id=None, model=None):
    """Yield SSE event dicts that drive the full build lifecycle.

    Self-healing: if the active Builder model hits a provider error (5xx / 429 /
    timeout / connection) or returns no usable output, the build automatically
    fails over to the next-highest-ranked available Builder model and continues —
    the user never has to retry. ``builder_id`` is the branded selection; ``model``
    is an optional raw override (creator dev mode).
    """
    candidates = build_candidates(builder_id, model) or [
        {"id": "vmb", "label": "ValleyMind Builder", "model": build_model(), "note": ""}]
    active = 0
    failover_count = 0
    proj = None
    meta = {}
    _active_inc()
    _build_t0 = time.time()
    try:
        spec = spec or {}
        root = projects_root(user_id)
        root.mkdir(parents=True, exist_ok=True)
        pid = new_project_id()
        proj = root / pid
        proj.mkdir(parents=True, exist_ok=True)
        spec_text = _render_spec(spec)

        yield {"type": "status", "message": "Setting up the project workspace..."}

        # ── Architecture (file manifest) with automatic failover ──
        manifest = None
        while manifest is None:
            tier = candidates[active]
            yield {"type": "status", "message": "Designing the architecture with " + tier["label"] + "..."}
            t0 = time.time()
            res = _thread_call(lambda m=tier["model"]: _manifest_for(spec_text, m))
            while not res["done"]:
                yield {"type": "heartbeat", "message": "Designing the architecture..."}
                time.sleep(10)
            if res["error"]:
                exc = res["error"]
                record_failure(tier["model"], exc)
                if _should_failover(exc) and active + 1 < len(candidates):
                    active += 1
                    failover_count += 1
                    yield {"type": "failover", "message": tier["label"]
                           + " is temporarily unavailable. Switched automatically to "
                           + candidates[active]["label"] + "."}
                    continue
                raise RuntimeError(_user_facing_error(exc))
            record_success(tier["model"], (time.time() - t0) * 1000)
            manifest = res["value"]
        yield {"type": "manifest", "files": manifest}

        # Checkpoint the plan immediately so a rate-limit pause is resumable.
        _checkpoint(proj, {
            "id": pid,
            "name": (spec.get("project_name") if isinstance(spec, dict) else "") or pid,
            "spec": spec,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "manifest": manifest,
            "status": "building",
            "file_count": 0,
        })

        ctx = {"active": active, "failover_count": failover_count, "done_files": 0,
               "skipped_files": [], "paused": False, "pace": _PACE_SECONDS}
        for ev in _generate_files(proj, spec_text, manifest, candidates, ctx):
            yield ev
        failover_count = ctx["failover_count"]

        if ctx["paused"]:
            # Paused on a rate limit — already emitted + checkpointed; resumable.
            _record_build_result(False, time.time() - _build_t0, failover_count, paused=True)
            return
        if ctx["done_files"] == 0:
            raise RuntimeError("No files could be generated for this project")

        used = candidates[ctx["active"]]
        record_build_completed(used["model"])
        meta = {
            "id": pid,
            "name": (spec.get("project_name") if isinstance(spec, dict) else "") or pid,
            "spec": spec,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": used["model"],
            "build_model": used["model"],
            "builder_label": used["label"],
            "manifest": manifest,
            "status": "complete" if not ctx["skipped_files"] else "partial",
            "file_count": ctx["done_files"],
            "skipped_files": ctx["skipped_files"],
            "failovers": failover_count,
        }
        save_project_meta(proj, meta)
        tree = build_tree(proj)
        yield {"type": "project", "project": {"id": pid, "meta": meta, "tree": tree}}
        _record_build_result(True, time.time() - _build_t0, failover_count)
        yield {"type": "done"}
    except GeneratorExit:
        if proj is not None:
            try:
                meta.setdefault("status", "incomplete")
                save_project_meta(proj, meta)
            except Exception:
                pass
        raise
    except Exception as exc:
        if proj is not None:
            try:
                meta.setdefault("status", "incomplete")
                save_project_meta(proj, meta)
            except Exception:
                pass
        _record_build_result(False, time.time() - _build_t0, failover_count)
        yield {"type": "error", "message": _user_facing_error(exc)}
    finally:
        _active_dec()


def resume_generator(user_id: str, pid: str, builder_id=None, model=None):
    """Resume an interrupted or rate-limit-paused build: regenerate only the
    files still missing from disk, using the same failover + backoff engine.
    Idempotent — can be called repeatedly until the project is complete."""
    proj = resolve_project(user_id, pid)
    if proj is None:
        yield {"type": "error", "message": "Project not found."}
        return
    meta = project_meta(proj) or {}
    manifest = meta.get("manifest")
    if not isinstance(manifest, list) or not manifest:
        yield {"type": "error", "message": "This project has no saved plan to resume — please rebuild it."}
        return
    spec = meta.get("spec") or {}
    spec_text = _render_spec(spec)
    candidates = build_candidates(builder_id, model) or [
        {"id": "vmb", "label": "ValleyMind Builder", "model": build_model(), "note": ""}]
    ctx = {"active": 0, "failover_count": 0, "done_files": 0,
           "skipped_files": [], "paused": False, "pace": _PACE_SECONDS}
    _active_inc()
    _build_t0 = time.time()
    try:
        yield {"type": "status", "message": "Resuming your build..."}
        yield {"type": "manifest", "files": manifest}
        for ev in _generate_files(proj, spec_text, manifest, candidates, ctx):
            yield ev
        if ctx["paused"]:
            _record_build_result(False, time.time() - _build_t0, ctx["failover_count"], paused=True)
            return
        if ctx["done_files"] == 0:
            _record_build_result(False, time.time() - _build_t0, ctx["failover_count"])
            yield {"type": "error", "message": "No files could be generated."}
            return
        used = candidates[ctx["active"]]
        record_build_completed(used["model"])
        meta.update({
            "model": used["model"], "build_model": used["model"], "builder_label": used["label"],
            "status": "complete" if not ctx["skipped_files"] else "partial",
            "file_count": ctx["done_files"], "skipped_files": ctx["skipped_files"],
            "failovers": int(meta.get("failovers", 0) or 0) + ctx["failover_count"],
        })
        save_project_meta(proj, meta)
        tree = build_tree(proj)
        yield {"type": "project", "project": {"id": pid, "meta": meta, "tree": tree}}
        _record_build_result(True, time.time() - _build_t0, ctx["failover_count"], resumed=True)
        yield {"type": "done"}
    except GeneratorExit:
        raise
    except Exception as exc:
        _record_build_result(False, time.time() - _build_t0, ctx["failover_count"])
        yield {"type": "error", "message": _user_facing_error(exc)}
    finally:
        _active_dec()
