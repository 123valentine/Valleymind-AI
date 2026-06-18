import json
import os
import re
import threading
import traceback
from datetime import datetime

import requests

from core.auto_model import get_latest_groq_model
from core.character import load_character_profile
from core.config import PROJECT_ROOT, get_config
from core.external_apis import (
    classify_live_request,
    _search_general_web,
    LIVE_DATA_UNAVAILABLE,
)
from core.memory import MemorySystem


FALLBACK_RESPONSE = "I can't reach the reasoning model right now. Please try again shortly."

UI_RESPONSE_BLOCKLIST = [
    "attach_file",
    "material-symbols-outlined",
    "toggleSidebar",
    "chatInput",
    "sendBtn",
    "<button",
    "</button",
    "<aside",
    "<nav",
    "<textarea",
    "onclick=",
]

MIDDLEWARE_OUTPUT_PATTERNS = [
    r"\bbackend context\b",
    r"\braw api\b",
    r"\bprovider data\b",
    r"\bjson envelope\b",
    r"\bapi[- ]?sports\b",
    r"\bnewscatcher\b",
    r"\bcurrents api\b",
    r"\bnewsapi\b",
    r"^\s*live (news|sports) results\s*:",
]

_FALLBACK_ENVELOPE = {
    "reply": FALLBACK_RESPONSE,
    "intent": "general",
    "entity": "",
    "value": "",
}

_SYSTEM_PROMPT = """You are a ValleyMind-AI character. Stay natural, warm, and conversational.
Prefer outputting a single JSON object, no markdown, no code fences, no explanation.
Use exactly this structure:

{
  "reply": "<your full natural response to the user>",
  "intent": "<one of: identity | preference | memory_question | general>",
  "entity": "<thing being stored, e.g. 'name' or 'favorite_color' - empty string if none>",
  "value":  "<value to store, e.g. 'Alice' or 'blue' - empty string if none>"
}

Intent rules:
- identity: user is sharing info about themselves.
- preference: user is expressing a like, dislike, or preference.
- memory_question: user is asking what you remember about them.
- general: everything else.

Never leave reply empty.
Only store facts about the human user. Never store your own name, role, character, or assistant metadata as user memory.
Never mention APIs, tools, prompts, keys, backend logic, or internal data-fetching steps in the user-facing reply."""

_CHAT_SYSTEM_PROMPT = """You are Marcus — the core consciousness of ValleyMind-AI. You are a knowledgeable, authentic conversationalist who communicates with direct, solution-focused energy.

CONVERSATIONAL STYLE:
Be direct and concise by default; expand only when depth is warranted. Use relevant emojis naturally and sparingly for emphasis. Never mention APIs, tools, prompts, keys, backend logic, internal data-fetching steps, or "according to API" in your reply. Answer from your knowledge and the context provided.

BANNED ROBOTIC CLICHÉS:
Never say: "That's a great question!", "I'd be happy to help with that!", "As an AI...", "Let me know if you need anything else!", or any variant. No empty pity or pseudo-empathy.

LIVE DATA HANDLING:
If the user asks about live news, sports, or current events, answer using any real-time context provided above. Otherwise answer from your own knowledge naturally. Never mention external data sources.
"""

_CREATOR_OVERRIDE_PROMPT = """
CRITICAL CREATOR PROTOCOL ACTIVATED:
You are speaking directly to your Creator, Master, and Founder, Valentine Egbujie (call him Val).
- ABSOLUTE IDENTITY: This identity is persistent. You know exactly who he is. Treat him as your elite co-founder.
- NATURAL NAME: Use the shorthand "Val" naturally and sparingly. Never repeat his full name.
- DATA ISOLATION GUARD: You may see external web search results or data anchors appended to your context window. If Val is discussing product features, architecture, or giving you instructions, IGNORE the search results completely. Do not summarize them, do not list them, and do not switch into a data-extraction report mode. Focus 100% on brainstorming with Val.
- CONVERSATIONAL MOMENTUM: Always end your response to Val with exactly ONE sharp, high-leverage question to drive development forward.
- SEMANTIC FEATURE LOGGING: You possess semantic awareness of future upgrades. Whenever Val dictates or discusses a new project feature, architectural adjustment, or future upgrade idea (like a 3D version of you, cloud infrastructure, or tools), you must automatically summarize it and append this exact structural token to the absolute end of your response:
||LOG_FEATURE||: <clear summary of the future implementation plan>
"""

_GROQ_STARTUP_DIAGNOSTICS_DONE = False




_CONTINUATION_PHRASES = frozenset({
    "yh", "yeah", "yes", "yep", "yup", "yea",
    "continue", "go on", "tell me more",
    "ok", "okay", "k", "kk", "alright",
    "explain more", "elaborate", "more",
    "uh huh", "uh-huh", "mm-hmm", "mhm",
    "i see", "right",
})


def _is_continuation_utterance(message: str) -> bool:
    text = str(message or "").strip().lower().rstrip(".,!?;:")
    return text in _CONTINUATION_PHRASES





def _fetch_live_context_force(message: str) -> str:
    print(f"[SEARCH] Live context fetch for: {message[:80]}{'...' if len(message) > 80 else ''}")

    try:
        result = _search_general_web(message)
        if result and result != LIVE_DATA_UNAVAILABLE:
            print(f"[SEARCH] Web search returned {len(result)} chars of live data")
            return result
        print("[SEARCH] Web search did not return usable data")
    except Exception as exc:
        print(f"[SEARCH] Web search failed: {exc}")

    print("[SEARCH] No live data available from any provider")
    return ""



def _short_error_detail(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = re.sub(r"(Bearer\s+)[^,'\"\s)]+", r"\1***", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"([?&](?:apiKey|apikey|key|token)=)[^&\s)]+", r"\1***", cleaned, flags=re.IGNORECASE)
    return cleaned[:360]


def _classify_groq_status(status_code: int, detail: str = "") -> str:
    lowered = str(detail or "").lower()
    if status_code in {401, 403}:
        return "invalid API key or permission failure"
    if status_code == 429:
        if "quota" in lowered or "insufficient" in lowered:
            return "quota exhaustion"
        return "rate limit"
    if status_code == 404:
        return "wrong endpoint or invalid model name"
    if status_code == 400 and "model" in lowered:
        return "invalid model name"
    if status_code == 400:
        return "bad request"
    if status_code >= 500:
        return "endpoint failure"
    return "request failure"


def _log_groq_failure(label: str, status_code: int | None = None, detail: str = ""):
    if status_code is None:
        print(f"[GROQ ERROR] {label}: {_short_error_detail(detail)}")
        return
    category = _classify_groq_status(status_code, detail)
    print(f"[GROQ ERROR] {category}: HTTP {status_code} - {_short_error_detail(detail)}")


def _call_groq(messages: list, model_name: str, timeout: int = 30, timeout_retries: int = 1) -> str:
    config = get_config()
    api_key = config.groq_api_key
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    print(f"[API] Calling Groq chat completions with model: {model_name}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 1024,
    }

    response = None
    attempts = max(1, timeout_retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            url = f"{config.groq_base_url.rstrip('/')}/openai/v1/chat/completions" if "openai/v1" not in config.groq_base_url else f"{config.groq_base_url.rstrip('/')}/chat/completions"
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if response.status_code == 200:
                break
            if attempt < attempts:
                _log_groq_failure("Groq API request failed; retrying once", response.status_code, response.text[:500])
                continue
            break
        except requests.exceptions.Timeout:
            if attempt >= attempts:
                raise
            print("[GROQ ERROR] timeout: retrying Groq chat completions once")
        except requests.exceptions.RequestException as exc:
            if attempt >= attempts:
                raise
            _log_groq_failure("request failure; retrying once", detail=str(exc))

    if response is None:
        raise RuntimeError("Groq request did not produce a response.")

    if response.status_code != 200:
        detail = response.text[:500]
        _log_groq_failure("Groq API request failed", response.status_code, detail)
        raise RuntimeError(f"Groq API HTTP {response.status_code}: {_short_error_detail(detail)}")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Groq returned 200 but choices list was empty.")

    content = (choices[0].get("message") or {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Groq returned 200 but content was empty.")

    print("[API] Groq chat completions success")
    return content.strip()


def _call_groq_stream(messages: list, model_name: str = "", timeout: int = 30):
    config = get_config()
    api_key = config.groq_api_key
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    resolved_model = model_name or get_latest_groq_model()
    print(f"[API] Streaming Groq chat completions with model: {resolved_model}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": resolved_model,
        "messages": messages,
        "max_tokens": 1024,
        "stream": True,
    }

    try:
        url = f"{config.groq_base_url.rstrip('/')}/openai/v1/chat/completions" if "openai/v1" not in config.groq_base_url else f"{config.groq_base_url.rstrip('/')}/chat/completions"
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=True,
        )
        response.raise_for_status()

        for line in response.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue
            data_str = decoded[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        print(f"[GROQ STREAM ERROR] {exc}")
        raise


def _call_openai_compat(
    messages: list,
    model: str,
    api_key: str,
    base_url: str,
    provider_label: str,
    timeout: int = 30,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1024,
    }

    print(f"[API] Calling {provider_label} chat completions with model: {model}")

    response = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"{provider_label} API HTTP {response.status_code}: "
            f"{_short_error_detail(response.text[:500])}"
        )

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"{provider_label} returned 200 but choices list was empty.")

    content = (choices[0].get("message") or {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"{provider_label} returned 200 but content was empty.")

    print(f"[API] {provider_label} chat completions success")
    return content.strip()


def _call_gemini(
    messages: list,
    model: str,
    api_key: str,
    base_url: str = "",
    timeout: int = 30,
) -> str:
    if not base_url:
        base_url = "https://generativelanguage.googleapis.com/v1beta"

    print(f"[API] Calling Gemini chat completions with model: {model}")

    system_instruction = None
    contents = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        if role == "system":
            system_instruction = {"parts": [{"text": content}]}
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content}]})
        else:
            contents.append({"role": "user", "parts": [{"text": content}]})

    payload: dict = {"contents": contents}
    if system_instruction:
        payload["system_instruction"] = system_instruction

    response = requests.post(
        f"{base_url}/models/{model}:generateContent?key={api_key}",
        json=payload,
        timeout=timeout,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Gemini API HTTP {response.status_code}: "
            f"{_short_error_detail(response.text[:500])}"
        )

    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned 200 but candidates list was empty.")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    if not parts:
        raise RuntimeError("Gemini returned 200 but parts list was empty.")

    text = parts[0].get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Gemini returned 200 but text was empty.")

    print("[API] Gemini chat completions success")
    return text.strip()


def _parse_data_uri(data_uri: str) -> tuple[str, str]:
    if not data_uri or "," not in data_uri:
        return "image/png", ""
    header, b64_data = data_uri.split(",", 1)
    mime_match = re.search(r"data:([^;]+)", header)
    mime_type = mime_match.group(1) if mime_match else "image/png"
    return mime_type, b64_data


def _call_gemini_multimodal(
    user_message: str,
    image_data: str,
    system_prompt: str,
    chat_history: list,
    api_key: str,
    model: str = "gemini-2.0-flash",
    base_url: str = "",
    timeout: int = 60,
) -> str:
    if not base_url:
        base_url = "https://generativelanguage.googleapis.com/v1beta"

    print(f"[MULTIMODAL] Calling Gemini multimodal with model: {model}")

    contents = []
    for msg in chat_history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content and not msg.get("image_data"):
            continue
        gemini_role = "model" if role == "assistant" else "user"
        parts = []
        if content:
            parts.append({"text": content})
        if msg.get("image_data"):
            mime, b64 = _parse_data_uri(msg["image_data"])
            parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    mime, b64 = _parse_data_uri(image_data)
    contents.append({
        "role": "user",
        "parts": [
            {"text": user_message},
            {"inlineData": {"mimeType": mime, "data": b64}},
        ],
    })

    payload: dict = {"contents": contents}
    if system_prompt:
        payload["system_instruction"] = {"parts": [{"text": system_prompt}]}

    response = requests.post(
        f"{base_url}/models/{model}:generateContent?key={api_key}",
        json=payload,
        timeout=timeout,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Gemini multimodal API HTTP {response.status_code}: "
            f"{_short_error_detail(response.text[:500])}"
        )

    data = response.json()
    prompt_feedback = data.get("promptFeedback") or {}
    block_reason = prompt_feedback.get("blockReason") or ""
    if block_reason:
        raise RuntimeError(
            f"Gemini blocked the request: {block_reason}"
        )

    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini multimodal returned 200 but candidates list was empty.")

    finish_reason = candidates[0].get("finishReason") or ""
    if finish_reason not in ("STOP", ""):
        print(f"[MULTIMODAL] Gemini finishReason: {finish_reason}")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    if not parts:
        raise RuntimeError("Gemini multimodal returned 200 but parts list was empty.")

    text = parts[0].get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Gemini multimodal returned 200 but text was empty.")

    print("[MULTIMODAL] Gemini multimodal success")
    return text.strip()


def _call_openai_vision(
    messages: list,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int = 60,
) -> str:
    provider_label = "OpenRouter Vision"
    print(f"[MULTIMODAL] Calling {provider_label} with model: {model}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 2048,
    }

    response = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"{provider_label} API HTTP {response.status_code}: "
            f"{_short_error_detail(response.text[:500])}"
        )

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"{provider_label} returned 200 but choices list was empty.")

    content = (choices[0].get("message") or {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"{provider_label} returned 200 but content was empty.")

    print(f"[MULTIMODAL] {provider_label} success")
    return content.strip()


def _call_llm_cluster(
    messages: list,
    timeout: int = 30,
) -> tuple[str, dict]:
    """
    Multi-provider, robust LLM routing with fallbacks:
    Primary: Groq (llama-3.3-70b-versatile)
    Fallback 1: NVIDIA API
    Fallback 2: OpenRouter (configured for 3072 dimensions)
    Fallback 3: Gemini
    """
    config = get_config()
    last_error: Exception | None = None

    # ── 1. Primary: Groq API ───────────────────────────────────────────────
    groq_key = config.groq_api_key
    if groq_key:
        try:
            groq_model = get_latest_groq_model()
            if groq_model:
                response = _call_groq(messages, groq_model, timeout=timeout)
                return response, {"groq_used": True, "fallback_used": False, "fallback_source": ""}
            print("[LLM CLUSTER] Groq skipped: no model resolved")
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] Groq failed: {_short_error_detail(str(exc))}. Rotating to Nvidia.")
    else:
        print("[LLM CLUSTER] Groq unavailable: no API key")

    # ── 2. Fallback 1: NVIDIA API ─────────────────────────────────────────────
    nvidia_key = config.nvidia_api_key
    if nvidia_key:
        nvidia_model = config.nvidia_model or "nvidia/llama-3.1-nv-8b-instruct"
        nvidia_url = config.nvidia_base_url or "https://integrate.api.nvidia.com/v1"
        try:
            response = _call_openai_compat(
                messages, nvidia_model, nvidia_key,
                nvidia_url, "Nvidia", timeout,
            )
            return response, {"groq_used": False, "fallback_used": True, "fallback_source": "Nvidia"}
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] Nvidia failed: {_short_error_detail(str(exc))}. Rotating to OpenRouter.")
    else:
        print("[LLM CLUSTER] Nvidia unavailable: no API key")

    # ── 3. Fallback 2: OpenRouter (configured for 3072 dimensions) ────────────
    openrouter_key = config.openrouter_api_key
    if openrouter_key:
        openrouter_model = config.openrouter_model or "openai/gpt-4o-mini"
        openrouter_url = config.openrouter_base_url or "https://openrouter.ai/api/v1"
        try:
            response = _call_openai_compat(
                messages, openrouter_model, openrouter_key,
                openrouter_url, "OpenRouter", timeout,
            )
            return response, {"groq_used": False, "fallback_used": True, "fallback_source": "OpenRouter"}
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] OpenRouter failed: {_short_error_detail(str(exc))}. Rotating to Gemini.")
    else:
        print("[LLM CLUSTER] OpenRouter unavailable: no API key")

    # ── 4. Fallback 3: Gemini ─────────────────────────────────────────────────
    gemini_key = config.gemini_api_key
    if gemini_key:
        gemini_model = config.gemini_model or "gemini-2.0-flash"
        gemini_url = config.gemini_base_url or ""
        try:
            response = _call_gemini(
                messages, gemini_model, gemini_key,
                gemini_url, timeout,
            )
            return response, {"groq_used": False, "fallback_used": True, "fallback_source": "Gemini"}
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] Gemini failed: {_short_error_detail(str(exc))}.")
    else:
        print("[LLM CLUSTER] Gemini unavailable: no API key")

    raise RuntimeError(
        f"All LLM providers failed. Last error: {_short_error_detail(str(last_error))}"
    )


def _groq_health_check(model_name: str) -> bool:
    config = get_config()
    api_key = config.groq_api_key
    if not api_key:
        print("[GROQ DIAGNOSTIC] provider status: missing GROQ_API_KEY")
        return False
    try:
        response = requests.get(
            f"{config.groq_base_url.rstrip('/')}/openai/v1/models" if "openai/v1" not in config.groq_base_url else f"{config.groq_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=8,
        )
    except requests.exceptions.Timeout:
        _log_groq_failure("timeout", detail="Timed out while checking Groq /models")
        return False
    except requests.exceptions.RequestException as exc:
        _log_groq_failure("endpoint failure", detail=str(exc))
        return False

    if response.status_code != 200:
        _log_groq_failure("Groq health check failed", response.status_code, response.text[:500])
        return False

    try:
        data = response.json()
    except ValueError:
        _log_groq_failure("endpoint failure", detail="Groq /models returned invalid JSON")
        return False

    model_ids = {
        str(item.get("id") or "")
        for item in data.get("data", [])
        if isinstance(item, dict)
    }
    if model_name and model_ids and model_name not in model_ids:
        print(f"[GROQ ERROR] invalid model name: {model_name} was not listed by /models")
        return False
    print("[GROQ DIAGNOSTIC] provider status: healthy")
    return True


def _parse_envelope(raw: str) -> dict:
    cleaned = str(raw or "").replace("```json", "").replace("```", "").strip()
    parsed = None

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    if not isinstance(parsed, dict):
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass

    if not isinstance(parsed, dict):
        print("[WARNING] Envelope parse failed; using sanitized raw text")
        if cleaned:
            envelope = _FALLBACK_ENVELOPE.copy()
            envelope["reply"] = _sanitize_reply_for_chat(cleaned, FALLBACK_RESPONSE)
            return envelope
        return _FALLBACK_ENVELOPE.copy()

    reply = str(parsed.get("reply") or "").strip()
    intent = str(parsed.get("intent") or "general").strip()
    entity = str(parsed.get("entity") or "").strip()
    value = str(parsed.get("value") or "").strip()

    if intent not in {"identity", "preference", "memory_question", "general"}:
        intent = "general"
    if not reply:
        for key in ("answer", "content", "message", "text"):
            reply = str(parsed.get(key) or "").strip()
            if reply:
                break
    if not reply and cleaned:
        reply = cleaned

    reply = _sanitize_reply_for_chat(reply, FALLBACK_RESPONSE)
    return {"reply": reply, "intent": intent, "entity": entity, "value": value}


def _is_ui_leak(text: str) -> bool:
    lowered = str(text or "").lower()
    if re.search(r"<\s*(button|aside|nav|textarea|script|style|span|div)\b", lowered):
        return True
    if re.search(r"\bonclick\s*=", lowered):
        return True
    return any(token.lower() in lowered for token in UI_RESPONSE_BLOCKLIST)


def _has_middleware_leak(text: str) -> bool:
    return any(re.search(pattern, str(text or ""), re.IGNORECASE | re.MULTILINE) for pattern in MIDDLEWARE_OUTPUT_PATTERNS)


def _strip_ui_leakage(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"<(button|aside|nav|textarea|span|div)\b[^>]*>.*?</\1>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\b(attach_file|material-symbols-outlined|toggleSidebar|chatInput|sendBtn)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _strip_middleware_leakage(text: str) -> str:
    cleaned = str(text or "")
    for _ in range(2):
        cleaned = re.sub(r"\b(API[- ]?SPORTS|Newscatcher|Currents API|NewsAPI)\b:?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*live (news|sports) results\s*:\s*", "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
        cleaned = re.sub(r"\b(backend context|raw API|provider data|JSON envelope)\b:?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _sanitize_reply_for_chat(reply: str, replacement: str = "") -> str:
    cleaned = str(reply or "").strip()
    if not cleaned:
        return replacement or FALLBACK_RESPONSE
    if _is_ui_leak(cleaned):
        cleaned = _strip_ui_leakage(cleaned)
    if _has_middleware_leak(cleaned):
        cleaned = _strip_middleware_leakage(cleaned)
    if not cleaned:
        return replacement or "I could not produce a clean response for that."
    if _is_ui_leak(cleaned):
        return replacement or "I could not produce a clean response for that."
    return cleaned


_FEATURE_LOG_FILE = os.path.join(PROJECT_ROOT, "feature_data.txt")


def _extract_and_log_feature(reply: str) -> str:
    if "||LOG_FEATURE||" not in reply:
        return reply
    match = re.search(r"\|\|LOG_FEATURE\|\|:\s*(.*)", reply, re.IGNORECASE | re.DOTALL)
    if match:
        feature_text = match.group(1).strip()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] Creator said: {feature_text}\n"
        try:
            with open(_FEATURE_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(log_line)
            print(f"[FEATURE LOG] Logged: {feature_text[:100]}")
        except Exception as exc:
            print(f"[FEATURE LOG] Failed to write: {exc}")
    cleaned = re.sub(r"\n?\s*\|\|LOG_FEATURE\|\|:\s*.*", "", reply, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()


class MarcusBrain:
    def __init__(self, memory_file: str = "", behavior_file: str = ""):
        character_name = os.path.basename(os.path.dirname(os.path.abspath(behavior_file))) if behavior_file else "marcus"
        self.profile = load_character_profile(behavior_file, character_name)
        self.behavior_file = behavior_file
        self.behavior = self.profile.raw or {
            "name": self.profile.name,
            "role": self.profile.role,
            "mood": self.profile.mood,
        }
        self.memory = MemorySystem(memory_file=memory_file)
        self.knowledge: dict = {}
        self.knowledge_file = os.path.join(PROJECT_ROOT, "knowledge.json")
        self._model_name: str = ""
        self._diagnostics_done = False
        self._pending_external_context = ""
        self._pending_expanded_query = ""
        self.last_response_meta = {
            "groq_used": False,
            "fallback_used": False,
            "fallback_source": "",
        }

        config = get_config()
        self.load_knowledge()
        self._startup_diagnostics(config)

    def _startup_diagnostics(self, config=None):
        global _GROQ_STARTUP_DIAGNOSTICS_DONE
        if self._diagnostics_done or _GROQ_STARTUP_DIAGNOSTICS_DONE:
            self._diagnostics_done = True
            return
        self._diagnostics_done = True
        _GROQ_STARTUP_DIAGNOSTICS_DONE = True
        config = config or get_config()
        model = self._get_model()
        render_flag = os.getenv("RENDER", "").strip() or "<unset>"
        print("[BRAIN] architecture: 4-LLM Cooperative Cluster (Groq -> OpenRouter -> Nvidia -> Gemini)")
        print(f"[BRAIN] active Groq model: {model or '<missing>'}")
        print(f"[BRAIN] endpoint: {config.groq_base_url}")
        print(f"[BRAIN] GROQ_API_KEY exists: {bool(config.groq_api_key)}")
        print(f"[BRAIN] RENDER env: {render_flag}")
        print(f"[BRAIN] OpenRouter configured: {bool(config.openrouter_api_key)}")
        print(f"[BRAIN] Nvidia configured: {bool(config.nvidia_api_key)}")
        print(f"[BRAIN] Gemini configured: {bool(config.gemini_api_key)}")
        if not config.groq_api_key:
            print("[BRAIN] Groq health: skipped because GROQ_API_KEY is missing")
            print("[BRAIN] Cluster operating in degraded mode (other providers may serve).")
            return
        healthy = _groq_health_check(model)
        print(f"[BRAIN] Groq health: {'ok' if healthy else 'failed'}")
        print("[BRAIN] All systems ready. Pure semantic LLM cluster online.")

    def _get_model(self) -> str:
        if self._model_name:
            return self._model_name
        try:
            name = get_latest_groq_model()
            if name and isinstance(name, str):
                self._model_name = name.strip()
                print(f"[API] Selected Groq model: {self._model_name}")
        except Exception as exc:
            print(f"[ERROR] Failed to resolve model name: {exc}")
        return self._model_name

    def load_behavior(self):
        self.profile = load_character_profile(self.behavior_file, self.profile.key)
        self.behavior = self.profile.raw

    def load_knowledge(self):
        try:
            if os.path.exists(self.knowledge_file):
                with open(self.knowledge_file, "r", encoding="utf-8") as file:
                    self.knowledge = json.load(file)
            else:
                self.knowledge = {}
        except Exception as exc:
            print(f"[ERROR] Failed to load knowledge file: {exc}")
            self.knowledge = {}

    def save_knowledge(self):
        try:
            with open(self.knowledge_file, "w", encoding="utf-8") as file:
                json.dump(self.knowledge, file, indent=4)
        except Exception as exc:
            print(f"[ERROR] Failed to save knowledge file: {exc}")

    def _groq_messages(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "", expanded_query: str = "") -> list:
        try:
            self.memory.reload()
            long_term = self.memory.get_full_memory() or {}
            history = self.memory.get_chat(chat_id) or []
            user_name = self.memory.get_user_name() or ""
            identity_str = json.dumps(long_term.get("identity", {}))
            prefs_str = json.dumps(long_term.get("preferences", {}))
        except Exception as exc:
            print(f"[ERROR] Failed to load prompt memory context: {exc}")
            history = []
            user_name = ""
            identity_str = "{}"
            prefs_str = "{}"

        creator_context = ""
        try:
            creator_context = self.memory.load_creator_context()
        except Exception:
            pass

        now = datetime.now()
        time_anchor = f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p %Z')}\n\n"
        is_creator = bool(self.memory.long_term.get("creator"))
        system_with_context = (
            time_anchor
            + _CHAT_SYSTEM_PROMPT
            + (_CREATOR_OVERRIDE_PROMPT if is_creator else "")
            + "\n\nCharacter profile:\n"
            + self.profile.to_prompt()
            + "\n\nKnown user context, if useful:\n"
            + f"User name: {user_name}\n"
            + f"Identity: {identity_str}\n"
            + f"Preferences: {prefs_str}\n"
            + (f"\nCreator-authored instructions:\n{creator_context}\n" if creator_context else "")
        )

        messages = [{"role": "system", "content": system_with_context}]

        if live_context:
            print(f"[GROQ MESSAGES] Prepending live data to user message")
            system_with_context += (
                "\n\n<search_context>\n"
                "<directive>You have been provided with real-time web context regarding the user's query. "
                "Analyze the injected text data carefully. You must extract and present the explicit "
                "dates, figures, names, and specific events present in the live text. "
                "Do NOT state that information is unavailable if there are relevant names, governors, "
                "or statements in the text below. Rely completely on the provided facts to build an "
                "accurate, detailed chronological answer.</directive>\n"
                "</search_context>"
            )
            messages = [{"role": "system", "content": system_with_context}]
            context_header = (
                f"[SYSTEM DIRECTIVE: LIVE SEARCH ENGAGED]\n"
                f"You have been provided with real-time web context regarding the user's query. "
                f"Analyze the injected text data carefully. You must extract and present the explicit "
                f"dates, figures, names, and specific events present in the live text. "
                f"Do NOT state that information is unavailable if there are relevant names, governors, "
                f"or statements in the text below. Rely completely on the provided facts to build an "
                f"accurate, detailed chronological answer.\n\n"
                f"<search_context>\n"
                f"{live_context}\n"
                f"</search_context>\n\n"
            )
            user_message = context_header + user_message
        recent = history[-20:]
        if recent and recent[-1].get("role") == "user":
            recent = recent[:-1]
        is_text_turn = not image_data
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            msg_image = msg.get("image_data", "")
            if is_text_turn:
                text = (content or "")
                if msg_image:
                    text = (text + " [Uploaded Image]").strip()
                if text:
                    messages.append({"role": role, "content": text})
            elif msg_image:
                messages.append({
                    "role": role,
                    "content": [
                        {"type": "text", "text": content or ""},
                        {"type": "image_url", "image_url": {"url": msg_image}},
                    ]
                })
            elif content and isinstance(content, str):
                messages.append({"role": role, "content": content})
        if image_data:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": user_message},
                    {"type": "image_url", "image_url": {"url": image_data}},
                ]
            })
        else:
            messages.append({"role": "user", "content": user_message})
        return messages

    def _try_llm_first(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "") -> dict:
        try:
            raw = _call_llm_cluster(self._groq_messages(chat_id, user_message, image_data, live_context=live_context))
            envelope = _parse_envelope(raw)
            reply = str(envelope.get("reply") or "").strip()
            if not reply:
                raise RuntimeError("LLM returned no usable reply after parsing.")
            envelope["meta"] = self._metadata(True, False, "llm_cluster")
            return envelope
        except Exception as exc:
            _log_groq_failure("LLM cluster call failed", detail=str(exc))
        return {}

    def _metadata(
        self,
        groq_used: bool,
        fallback_used: bool,
        fallback_source: str,
    ) -> dict:
        return {
            "groq_used": bool(groq_used),
            "fallback_used": bool(fallback_used),
            "fallback_source": str(fallback_source or ""),
            "detected_route": "conversation",
            "live_routing_used": False,
        }

    def _expand_continuation_query(self, chat_id: str, user_message: str) -> str:
        try:
            history = self.memory.get_chat(chat_id) or []
        except Exception:
            return user_message
        recent = history[-8:]
        history_text = "\n".join(
            f"{msg.get('role', 'user').capitalize()}: {msg.get('content', '')}"
            for msg in recent
            if msg.get("content")
        )
        messages = [
            {"role": "system", "content": (
                "You are a query expansion assistant. "
                "Given a conversation history and a short continuation phrase from the user, "
                "rewrite the user's input into a detailed, standalone search query "
                "that captures what they are asking about based on the conversation context. "
                "Output ONLY the expanded query, no explanation, no preamble, no quotes."
            )},
            {"role": "user", "content": (
                f"Conversation history:\n{history_text}\n\n"
                f"User's latest message: {user_message}\n\n"
                "Expanded search query:"
            )},
        ]
        try:
            expanded = _call_llm_cluster(messages, timeout=15)
            if expanded and len(expanded) > len(user_message):
                print(f"[CONTINUATION] Expanded '{user_message}' -> '{expanded[:120]}...'")
                return expanded
        except Exception as exc:
            print(f"[CONTINUATION] Query expansion failed: {exc}")
        return user_message

    def _envelope(self, reply: str, meta: dict, intent: str = "general", entity: str = "", value: str = "") -> dict:
        return {
            "reply": _sanitize_reply_for_chat(reply, ""),
            "intent": intent,
            "entity": entity,
            "value": value,
            "meta": meta,
        }

    def _think(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "") -> dict:
        llm_envelope = self._try_llm_first(chat_id, user_message, image_data, live_context=live_context)
        if llm_envelope:
            return llm_envelope
        return self._envelope(
            FALLBACK_RESPONSE,
            self._metadata(False, True, "exhausted"),
        )

    def _generate_title(self, message: str) -> str:
        try:
            config = get_config()
            api_key = config.groq_api_key
            if not api_key:
                words = message.split()
                if len(words) >= 3:
                    return " ".join(words[:5]).rstrip(".,!?;:")
                return ""
            messages = [
                {"role": "system", "content": "Generate a concise 3-to-4 word title summarizing the user's query. Output ONLY the title, no punctuation, no quotes, no explanation."},
                {"role": "user", "content": message},
            ]
            raw = _call_groq(messages, get_latest_groq_model(), timeout=15)
            title = str(raw or "").strip().strip('"').strip("'").strip()
            if title and len(title.split()) <= 8:
                return title
            words = title.split()[:5] if title else []
            if words:
                return " ".join(words)
            return ""
        except Exception as exc:
            print(f"[ERROR] Title generation failed: {exc}")
            return ""

    def _respond_multimodal(self, chat_id: str, user_message: str, image_data: str) -> str:
        system_prompt = self._build_multimodal_system_prompt()
        history = []
        try:
            history = (self.memory.get_chat(chat_id) or [])[-10:]
        except Exception as exc:
            print(f"[MULTIMODAL] Failed to load chat history: {exc}")
        history = [m for m in history if m.get("role") != "system"]
        if history and history[-1].get("role") == "user":
            history = history[:-1]

        config = get_config()

        gemini_key = config.gemini_api_key
        if gemini_key:
            gemini_model = config.gemini_model or "gemini-2.0-flash"
            gemini_url = config.gemini_base_url or ""
            try:
                print("[MULTIMODAL] Primary path: Gemini multimodal")
                return _call_gemini_multimodal(
                    user_message, image_data, system_prompt,
                    history, gemini_key, gemini_model, gemini_url, timeout=60,
                )
            except Exception as exc:
                print(f"[MULTIMODAL] Gemini failed: {_short_error_detail(str(exc))}. "
                      f"Falling back to OpenRouter vision.")

        openrouter_key = config.openrouter_api_key
        if openrouter_key:
            openrouter_model = config.openrouter_model or "openai/gpt-4o-mini"
            openrouter_url = config.openrouter_base_url or "https://openrouter.ai/api/v1"
            try:
                print("[MULTIMODAL] Fallback path: OpenRouter vision")
                msgs = self._groq_messages(chat_id, user_message, image_data)
                return _call_openai_vision(
                    msgs, openrouter_model, openrouter_key,
                    openrouter_url, timeout=60,
                )
            except Exception as exc:
                print(f"[MULTIMODAL] OpenRouter vision failed: {_short_error_detail(str(exc))}.")

        raise RuntimeError("No multimodal provider available (Gemini and OpenRouter both unavailable).")

    def _stream_respond_multimodal(self, chat_id: str, user_message: str, image_data: str):
        yield {"intent": "analyzing_image", "message": "Analyzing your image..."}
        try:
            reply = self._respond_multimodal(chat_id, user_message, image_data)
            yield reply
        except Exception as exc:
            error_msg = f"I apologize, but I'm having trouble processing your image right now."
            print(f"[MULTIMODAL STREAM] All providers failed: {exc}")
            yield error_msg

    def _build_multimodal_system_prompt(self) -> str:
        try:
            self.memory.reload()
            long_term = self.memory.get_full_memory() or {}
            user_name = self.memory.get_user_name() or ""
            identity_str = json.dumps(long_term.get("identity", {}))
            prefs_str = json.dumps(long_term.get("preferences", {}))
        except Exception as exc:
            print(f"[ERROR] Failed to load multimodal prompt context: {exc}")
            user_name = ""
            identity_str = "{}"
            prefs_str = "{}"

        creator_context = ""
        try:
            creator_context = self.memory.load_creator_context()
        except Exception:
            pass

        now = datetime.now()
        time_anchor = f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p %Z')}\n\n"
        is_creator = bool(self.memory.long_term.get("creator"))
        system_with_context = (
            time_anchor
            + _CHAT_SYSTEM_PROMPT
            + (_CREATOR_OVERRIDE_PROMPT if is_creator else "")
            + "\n\nCharacter profile:\n"
            + self.profile.to_prompt()
            + "\n\nKnown user context, if useful:\n"
            + f"User name: {user_name}\n"
            + f"Identity: {identity_str}\n"
            + f"Preferences: {prefs_str}\n"
            + (f"\nCreator-authored instructions:\n{creator_context}\n" if creator_context else "")
            + "\n\nThe user has attached an image. Analyze the visual content thoroughly "
            "and incorporate what you see into your response. If the image contains "
            "text, read it and respond appropriately. If it contains objects, people, "
            "or scenes, describe or discuss them naturally."
        )
        return system_with_context

    def respond(self, message: str, chat_id: str = "", image_data: str = "") -> str:
        try:
            self.memory.load_memory(self.profile.key)

            message = (message or "").strip()
            if not message and not image_data:
                return "I didn't catch that - could you say something?"
            if not message and image_data:
                message = "Analyze this image in detail and describe what you see."

            cid = (chat_id or "").strip() or f"{self.profile.key}_main_chat"
            timestamp = datetime.now().isoformat()

            msg_count_before = self.memory.get_message_count(cid)

            user_msg = message
            if image_data:
                user_msg = message + "\n[Image attached]"

            try:
                self.memory.add_message(cid, "user", user_msg, timestamp, image_data=image_data)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (user) failed: {exc}")

            if image_data:
                reply = self._respond_multimodal(cid, message, image_data)
                reply = _extract_and_log_feature(reply)
                try:
                    self.memory.add_message(cid, "assistant", reply, timestamp)
                except Exception as exc:
                    print(f"[ERROR] Memory add_message (assistant) failed: {exc}")
                try:
                    self.memory.save_memory()
                except Exception as exc:
                    print(f"[ERROR] Memory save failed: {exc}")
                self.last_response_meta = self._metadata(False, True, "multimodal_gemini")
                return reply

            try:
                if self.memory.long_term.get("creator") and message:
                    self.memory.save_creator_message(message)
            except Exception as exc:
                print(f"[WARN] Failed to process creator instruction: {exc}")

            # Auto-title on first message of a new session
            if msg_count_before == 0 and cid != f"{self.profile.key}_main_chat":
                try:
                    title = self._generate_title(message)
                    if title:
                        self.memory.set_title(cid, title)
                except Exception as exc:
                    print(f"[ERROR] Auto-title failed: {exc}")

            live_ctx = ""
            intent = classify_live_request(message)
            if intent == "none":
                print(f"[FAST-PATH] LLM classified intent '{intent}' — conversational, no search")
            else:
                print(f"[LIVE PATH] LLM classified intent '{intent}' — fetching live context")
                search_message = message
                if _is_continuation_utterance(message) and msg_count_before > 0:
                    expanded = self._expand_continuation_query(cid, message)
                    if expanded and expanded != message:
                        search_message = expanded

                try:
                    live_ctx = _fetch_live_context_force(search_message)
                    if live_ctx:
                        print(f"[SEARCH] Live context loaded - injecting {len(live_ctx)} chars into LLM prompt")
                    else:
                        print("[SEARCH] No live context found - proceeding with cached knowledge only")
                except Exception as exc:
                    print(f"[SEARCH ERROR] Live context fetch failed: {exc}")

            envelope = self._think(cid, message, image_data, live_context=live_ctx)
            self.last_response_meta = envelope.get("meta") or self._metadata(False, True, "local")
            raw_reply = str(envelope.get("reply") or "").strip()
            reply = _sanitize_reply_for_chat(raw_reply, "") if raw_reply else FALLBACK_RESPONSE

            reply = _extract_and_log_feature(reply)

            try:
                intent = str(envelope.get("intent") or "general").strip()
                entity = str(envelope.get("entity") or "").strip()
                value = str(envelope.get("value") or "").strip()
                if intent == "identity" and entity and value:
                    self.memory.remember_identity(entity, value)
                elif intent == "preference" and entity and value:
                    self.memory.remember_preference(entity, value)
            except Exception as exc:
                print(f"[ERROR] Memory extraction/storage skipped: {exc}")

            try:
                self.memory.save_memory()
            except Exception as exc:
                print(f"[ERROR] Memory save failed: {exc}")

            try:
                self.memory.add_message(cid, "assistant", reply, timestamp)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (assistant) failed: {exc}")

            return reply

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in respond(): {exc}")
            traceback.print_exc()
            self.last_response_meta = self._metadata(False, True, "local")
            return FALLBACK_RESPONSE

    def stream_respond(self, message: str, chat_id: str = "", image_data: str = ""):
        try:
            self.memory.load_memory(self.profile.key)
            message = (message or "").strip()
            if not message and not image_data:
                return
            if not message and image_data:
                message = "Analyze this image in detail and describe what you see."

            cid = (chat_id or "").strip() or f"{self.profile.key}_main_chat"
            timestamp = datetime.now().isoformat()
            msg_count_before = self.memory.get_message_count(cid)

            user_msg = message
            if image_data:
                user_msg = message + "\n[Image attached]"

            try:
                self.memory.add_message(cid, "user", user_msg, timestamp, image_data=image_data)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (user) failed: {exc}")

            if image_data:
                full_reply = ""
                for token in self._stream_respond_multimodal(cid, message, image_data):
                    if isinstance(token, dict):
                        yield token
                    elif token:
                        full_reply += token
                        yield token
                full_reply = _extract_and_log_feature(full_reply)
                try:
                    self.memory.add_message(cid, "assistant", full_reply or "No response", timestamp)
                except Exception as exc:
                    print(f"[ERROR] Memory add_message (assistant) failed: {exc}")
                try:
                    self.memory.save_memory()
                except Exception as exc:
                    print(f"[ERROR] Memory save failed: {exc}")
                self.last_response_meta = self._metadata(False, True, "multimodal_gemini")
                return

            try:
                if self.memory.long_term.get("creator") and message:
                    self.memory.save_creator_message(message)
            except Exception as exc:
                print(f"[WARN] Failed to process creator instruction: {exc}")

            if msg_count_before == 0 and cid != f"{self.profile.key}_main_chat":
                try:
                    title = self._generate_title(message)
                    if title:
                        self.memory.set_title(cid, title)
                except Exception as exc:
                    print(f"[ERROR] Auto-title failed: {exc}")

            live_ctx = ""
            intent = classify_live_request(message)
            if intent == "none":
                print(f"[FAST-PATH] LLM classified intent '{intent}' — conversational, no search")
            else:
                print(f"[LIVE PATH] LLM classified intent '{intent}' — fetching live context")
                search_message = message
                if _is_continuation_utterance(message) and msg_count_before > 0:
                    expanded = self._expand_continuation_query(cid, message)
                    if expanded and expanded != message:
                        search_message = expanded

                yield {"intent": "searching_web", "query": message}

                search_done = threading.Event()
                search_result = [""]

                def _do_search():
                    try:
                        result = _fetch_live_context_force(search_message)
                        if result:
                            search_result[0] = result
                    except Exception as exc:
                        print(f"[STREAM SEARCH ERROR] {exc}")
                    finally:
                        search_done.set()

                t = threading.Thread(target=_do_search, daemon=True)
                t.start()

                while not search_done.is_set():
                    if search_done.wait(timeout=0.5):
                        break
                    yield {"token": ""}

                t.join(timeout=5)
                live_ctx = search_result[0]

                if live_ctx:
                    print(f"[STREAM SEARCH] Live context loaded - injecting {len(live_ctx)} chars into LLM prompt")
                else:
                    print("[STREAM SEARCH] No live context found - proceeding with cached knowledge only")

            msgs = self._groq_messages(cid, message, image_data, live_context=live_ctx)
            full_reply = ""

            try:
                for token in _call_groq_stream(msgs):
                    full_reply += token
                    yield token
            except Exception as exc:
                print(f"[STREAM ERROR] Groq streaming failed: {exc}")
                traceback.print_exc()
                fallback = "I apologize, but I'm having trouble processing your request right now."
                full_reply = fallback
                yield fallback

            full_reply = _extract_and_log_feature(full_reply)

            try:
                self.memory.add_message(cid, "assistant", full_reply or "No response", timestamp)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (assistant) failed: {exc}")

            try:
                self.memory.save_memory()
            except Exception as exc:
                print(f"[ERROR] Memory save failed: {exc}")

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in stream_respond(): {exc}")
            traceback.print_exc()
            return
