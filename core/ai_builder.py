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


# Default to a fast, NON-reasoning free model. deepseek-v4-flash-free is a
# chain-of-thought model that emits everything as reasoning_content and spends
# its whole token budget "thinking" — at the manifest step (small budget) it
# returned zero answer content and the build died. ling-3.0-flash-free returns
# clean JSON directly (finish_reason=stop) in ~16s, so both planning and the
# multi-file build stay well under the gunicorn timeout.
def plan_model() -> str:
    return os.getenv("OPENCODE_PLAN_MODEL", "ling-3.0-flash-free").strip()


def build_model() -> str:
    return os.getenv("OPENCODE_BUILD_MODEL", "ling-3.0-flash-free").strip()


# ── Branded builder tiers ────────────────────────────────────────────────────
# The UI shows ONLY the ValleyMind Builder branding. The real OpenCode model ids
# below never reach the browser unless Developer Mode is on (creator-gated). Add
# or re-rank tiers here — no UI change is required, the dropdown is server-driven.
# Ranked by builder performance (reliable completion first, then output quality),
# measured against the manifest+file build task. vmb5 is the flagship default.
BUILDER_TIERS = [
    {"id": "vmb1", "label": "ValleyMind Builder 1.0", "model": "mimo-v2.5-free",        "note": "Quick drafts and small projects."},
    {"id": "vmb2", "label": "ValleyMind Builder 2.0", "model": "longcat-2.0-free",      "note": "Everyday building."},
    {"id": "vmb3", "label": "ValleyMind Builder 3.0", "model": "nemotron-3-ultra-free", "note": "Balanced quality and speed."},
    {"id": "vmb4", "label": "ValleyMind Builder 4.0", "model": "big-pickle",            "note": "Richest output — slower; best for smaller projects."},
    {"id": "vmb5", "label": "ValleyMind Builder 5.0", "model": "ling-3.0-flash-free",   "note": "Fastest and most reliable. Recommended."},
]
DEFAULT_BUILDER_ID = os.getenv("OPENCODE_DEFAULT_BUILDER", "vmb5").strip() or "vmb5"


def _tier_by_id(bid):
    for t in BUILDER_TIERS:
        if t["id"] == bid:
            return t
    return None


def builder_options(dev: bool = False) -> list:
    """Branded builder options for the UI. The real `model` id is included ONLY
    when dev is True (Developer Mode). Tiers whose model isn't live are dropped."""
    avail = set(available_models())
    tiers = [t for t in BUILDER_TIERS if (not avail or t["model"] in avail)] or list(BUILDER_TIERS)
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


def configured() -> bool:
    return bool(api_key())


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {api_key()}"}


def _user_facing_error(exc) -> str:
    msg = str(exc) or repr(exc)
    msg = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-***", msg)
    if "not configured" in msg:
        return "The OpenCode API key is not configured on the server (OPENCODE_API_KEY)."
    if "401" in msg or "Unauthorized" in msg or "invalid_api_key" in msg:
        return "The OpenCode API rejected the server key (401). Check OPENCODE_API_KEY."
    if "402" in msg or "CreditsError" in msg or "No payment method" in msg:
        return "The OpenCode account has no credits/payment method for that model. Configure OPENCODE_PLAN_MODEL/OPENCODE_BUILD_MODEL to a free or funded model."
    if "429" in msg:
        return "The OpenCode API is rate-limited right now. Wait a moment and try again."
    return f"OpenCode API error: {msg[:300]}"


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


def chat_stream(model: str, messages: list, max_tokens: int = 8192, temperature: float = 0.3):
    """Stream a chat completion from the OpenCode API.

    Yields dicts: {"content": str, "reasoning": str, "finish": str|None}.
    Raises RuntimeError on HTTP errors or missing key.
    """
    key = api_key()
    if not key:
        raise RuntimeError("OPENCODE_API_KEY is not configured")
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
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
            raise RuntimeError(f"OpenCode API HTTP {resp.status_code}: {body}")
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


def clarify_generator(model: str, message: str, project_type: str):
    yield {"type": "status", "message": "Analyzing your request and preparing clarifying questions..."}
    messages = [
        {"role": "system", "content": CLARIFY_SYSTEM},
        {"role": "user", "content": f"Project type: {project_type}\n\nWhat the user wants to build:\n{message}"},
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


def plan_generator(model: str, message: str, project_type: str, answers=None):
    yield {"type": "status", "message": "Crafting your project specification (this can take up to a minute)..."}
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": (
            f"Project type: {project_type}\n\n"
            f"What the user wants to build:\n{message}\n\n"
            f"Answers to clarifying questions:\n{_format_answers(answers)}"
        )},
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


# Binary assets a text LLM cannot produce. SVG is deliberately excluded — it is
# XML markup the model can write. .env is excluded too (spec uses .env.example).
_BINARY_ASSET_RE = re.compile(
    r"\.(ico|png|jpe?g|gif|webp|bmp|tiff?|woff2?|ttf|eot|otf|mp3|mp4|m4a|wav|ogg|pdf|zip|gz|tar|jar|exe|dll|so|dylib|wasm)$",
    re.IGNORECASE,
)


def _is_binary_asset(path: str) -> bool:
    return bool(_BINARY_ASSET_RE.search(path or ""))


def build_generator(user_id: str, spec, model=None):
    """Yield SSE event dicts that drive the full build lifecycle.

    ``model`` optionally overrides the build model (chosen in the UI dropdown);
    it must already be validated by the caller. Falls back to build_model().
    """
    use_model = (model or "").strip() or build_model()
    proj = None
    meta = {}
    try:
        spec = spec or {}
        root = projects_root(user_id)
        root.mkdir(parents=True, exist_ok=True)
        pid = new_project_id()
        proj = root / pid
        proj.mkdir(parents=True, exist_ok=True)
        spec_text = _render_spec(spec)

        yield {"type": "status", "message": "Setting up the project workspace..."}

        yield {"type": "status", "message": "Designing the project architecture..."}
        manifest_res = _thread_call(lambda: _manifest_for(spec_text, use_model))
        while not manifest_res["done"]:
            yield {"type": "heartbeat", "message": "Designing the project architecture..."}
            time.sleep(10)
        if manifest_res["error"]:
            raise RuntimeError(_user_facing_error(manifest_res["error"]))
        manifest = manifest_res["value"]
        yield {"type": "manifest", "files": manifest}

        total = len(manifest)
        done_files = 0
        skipped_files = []
        for index, path in enumerate(manifest):
            yield {"type": "file_start", "path": path, "index": index, "total": total}
            # A text model can't emit binary assets (favicon.ico, images, fonts).
            # Skip them so one binary entry can't sink the whole build.
            if _is_binary_asset(path):
                skipped_files.append(path)
                yield {"type": "file_skipped", "path": path, "index": index, "total": total,
                       "message": "Binary asset — add this file manually"}
                continue
            collected = []
            try:
                for chunk in _gen_file_content(use_model, spec_text, path, manifest):
                    collected.append(chunk)
                    yield {"type": "chunk", "text": chunk, "path": path, "index": index, "total": total}
                content = "".join(collected)
                if not content.strip():
                    raise RuntimeError("Model produced an empty file for " + path)
                save_project_file(proj, path, content)
            except GeneratorExit:
                raise
            except Exception as exc:
                # One bad file must not discard the whole project — log it and
                # keep going. Only a build with ZERO usable files is a failure.
                skipped_files.append(path)
                yield {"type": "file_error", "path": path, "index": index, "total": total, "message": _user_facing_error(exc)}
                continue
            done_files += 1
            yield {"type": "file_done", "path": path, "index": index, "total": total, "completed": done_files}

        if done_files == 0:
            raise RuntimeError("No files could be generated for this project")

        meta = {
            "id": pid,
            "name": (spec.get("project_name") if isinstance(spec, dict) else "") or pid,
            "spec": spec,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": use_model,
            "build_model": use_model,
            "status": "complete" if not skipped_files else "partial",
            "file_count": done_files,
            "skipped_files": skipped_files,
        }
        save_project_meta(proj, meta)
        tree = build_tree(proj)
        yield {"type": "project", "project": {"id": pid, "meta": meta, "tree": tree}}
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
        yield {"type": "error", "message": _user_facing_error(exc)}
