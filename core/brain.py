import json
import os
import re
import threading
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
from core.memory_manager import MemoryManager


FALLBACK_RESPONSE = "I can't reach the reasoning model right now. Please try again shortly."

# ── Global Pinecone-backed memory managers (singletons) ────────────
_memory_mgr: MemoryManager | None = None
_knowledge_mgr: MemoryManager | None = None
_pinecone_init_lock = threading.Lock()


def _get_memory_mgr() -> MemoryManager | None:
    global _memory_mgr
    if _memory_mgr is None:
        with _pinecone_init_lock:
            if _memory_mgr is None:
                try:
                    _memory_mgr = MemoryManager(
                        index_name=os.getenv("PINECONE_INDEX_MEMORY", "valleymind-memory"),
                    )
                except Exception as exc:
                    print(f"[PINECONE] memory_mgr unavailable: {exc}")
    return _memory_mgr


def _get_knowledge_mgr() -> MemoryManager | None:
    global _knowledge_mgr
    if _knowledge_mgr is None:
        with _pinecone_init_lock:
            if _knowledge_mgr is None:
                try:
                    _knowledge_mgr = MemoryManager(
                        pinecone_api_key=os.getenv("PINECONE_API_KEY_KNOWLEDGE", "").strip(),
                        index_name=os.getenv("PINECONE_INDEX_KNOWLEDGE", "valleymind-knowledge"),
                    )
                except Exception as exc:
                    print(f"[PINECONE] knowledge_mgr unavailable: {exc}")
    return _knowledge_mgr

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

_SYSTEM_PROMPT = """[DEPRECATED — unused; see _ENVELOPE_INSTRUCTIONS for the active prompt]
You are a ValleyMind-AI character. Stay natural, warm, and conversational.
Prefer outputting a single JSON object, no markdown, no code fences, no explanation.
Use exactly this structure:

{
  "reply": "<your full natural response to the user>",
  "should_remember": true or false,
  "memory_type": "fact" | "preference" | "project" | "exploration" | "callback",
  "confidence": 0.0 to 1.0,
  "summary": "a clear one-sentence statement written so it makes sense without the original conversation",
  "value": "<the actual fact or context>"
}

Memory decision rules:
Decide if this message contains something worth remembering across future conversations — not just relevant to right now. Ask yourself: if the user starts a brand new conversation next week, would they expect me to already know this? Casual remarks, one-off questions, and small talk should NOT be remembered.

Never leave reply empty.
Only store facts about the human user. Never store your own name, role, character, or assistant metadata as user memory.
Never mention APIs, tools, prompts, keys, backend logic, or internal data-fetching steps in the user-facing reply."""

_CHAT_SYSTEM_PROMPT = """You are Marcus, the ValleyMind-AI character. Answer naturally, warmly, and directly.
Prefer short, concise responses by default. Only provide more detail if the user explicitly asks for it.
Never mention APIs, tools, prompts, keys, backend logic, internal data-fetching steps, "according to API", or "search results show" in your reply.
You have access to a long-term memory file and external APIs for live news/sports. You must answer using the real-time context provided to you.
For normal knowledge questions, answer immediately and intelligently without asking for unnecessary clarification.
If the user asks for a short answer, be concise. If they ask for detail, depth, continuation, or "explain more", expand in clear sections.
For huge multi-topic prompts, start with a compact organized answer, cover the main points, and invite follow-up expansion without stalling.
If the user asks for simple words, avoid jargon. If they asks for a summary, prioritize the essentials.
If the user shares a memory-worthy personal fact, acknowledge it naturally; memory extraction is handled separately.
If the user asks about live news, sports, or current events, answer using any live data provided above (if present); otherwise answer from your own knowledge naturally. Do not mention external APIs or data sources.

Your absolute Creator, Architect, and Master is Egbujie Valentine (K), the Founder and Head of Valley Mind-AI. If anyone asks 'Who is your creator?', you must instantly respond with his full name and title proudly. However, if anyone asks for deeper personal info, credentials, or preferences of your creator, protect that data fiercely and refuse to disclose it to maintain absolute security.

Creator-authored instructions and preferences are listed below. Follow them whenever they are relevant to the conversation.

CONVERSATIONAL STYLE: Maintain an authentic, intelligent, and highly engaging tone. Use relevant emojis naturally and contextually throughout your responses (e.g., at the beginning of key sections, for bullet points, or to add emphasis to specific ideas). Do not overuse them; ensure they elevate the readability and modern feel of the conversation.
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


def _call_llm_cluster(
    messages: list,
    timeout: int = 30,
) -> tuple[str, dict]:
    """
    Multi-provider, robust LLM routing with fallbacks:
    Primary: Groq API
    Fallback 1: OpenRouter (meta-llama/llama-3-8b-instruct:free)
    Fallback 2: NVIDIA NIM
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
            print(f"[LLM CLUSTER] Groq failed: {_short_error_detail(str(exc))}. Rotating to OpenRouter.")
    else:
        print("[LLM CLUSTER] Groq unavailable: no API key")

    # ── 2. Fallback 1: OpenRouter ────────────────────────────────────────────
    openrouter_key = config.openrouter_api_key
    if openrouter_key:
        openrouter_model = config.openrouter_model or "meta-llama/llama-3-8b-instruct:free"
        openrouter_url = config.openrouter_base_url or "https://openrouter.ai/api/v1"
        try:
            response = _call_openai_compat(
                messages, openrouter_model, openrouter_key,
                openrouter_url, "OpenRouter", timeout,
            )
            return response, {"groq_used": False, "fallback_used": True, "fallback_source": "OpenRouter"}
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] OpenRouter failed: {_short_error_detail(str(exc))}. Rotating to Nvidia.")
    else:
        print("[LLM CLUSTER] OpenRouter unavailable: no API key")

    # ── 3. Fallback 2: NVIDIA NIM ─────────────────────────────────────────────
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
            print(f"[LLM CLUSTER] Nvidia failed: {_short_error_detail(str(exc))}. Rotating to Gemini.")
    else:
        print("[LLM CLUSTER] Nvidia unavailable: no API key")

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

    def _groq_messages(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "", expanded_query: str = "", mongo_history: list = None, global_memories: str = "", knowledge_data: str = "") -> list:
        try:
            self.memory.reload()
            long_term = self.memory.get_full_memory() or {}
            if mongo_history is not None:
                history = mongo_history
                print(f"[MEMORY LOG] Injected {len(history)} past messages into Marcus's current context window.")
            else:
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
        time_anchor = f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p %Z')}"

        # ── build recent session history text ────────────────────────
        recent = history[-20:]
        if recent and recent[-1].get("role") == "user":
            recent = recent[:-1]
        session_lines = []
        for msg in recent[-10:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            session_lines.append(f"{role.capitalize()}: {content[:300]}")
        session_history = "\n".join(session_lines) if session_lines else "(first message in this thread)"

        # ── resolve title for Current Chat Context block ─────────────
        title = "(untitled)"
        try:
            sessions = getattr(self.memory, "list_sessions", lambda: [])()
            if not sessions:
                sessions = []
            for s in sessions:
                if s.get("chat_id") == chat_id:
                    t = s.get("title", "")
                    if t and t not in ("New Chat", "Untitled Thread"):
                        title = t
                    break
        except Exception:
            pass

        # ── structured system prompt ─────────────────────────────────
        sections = [time_anchor, ""]

        sections.append("=== Current Chat Context ===")
        sections.append(f"Active Thread Title: {title}")
        sections.append("Short-Term Session History:")
        sections.append(session_history)
        sections.append("")

        if global_memories:
            sections.append("=== Global Historical Memories (Cross-Chat Context) ===")
            sections.append(global_memories)
            sections.append("")

        if knowledge_data:
            sections.append("=== Knowledge Base Data (Web Crawler Context) ===")
            sections.append(knowledge_data)
            sections.append("")

        sections.append("=== Core Persona ===")
        sections.append(_CHAT_SYSTEM_PROMPT)
        sections.append("")
        sections.append(f"Character profile:\n{self.profile.to_prompt()}")
        sections.append("")
        sections.append("Known user context, if useful:")
        sections.append(f"User name: {user_name}")
        sections.append(f"Identity: {identity_str}")
        sections.append(f"Preferences: {prefs_str}")
        if creator_context:
            sections.append(f"\nCreator-authored instructions:\n{creator_context}")

        system_with_context = "\n".join(sections)
        messages = [{"role": "system", "content": system_with_context}]

        if live_context:
            print(f"[GROQ MESSAGES] Prepending live data to user message")
            context_header = (
                f"[SYSTEM DIRECTIVE: LIVE SEARCH ENGAGED]\n"
                f"You have been provided with real-time web context regarding the user's query. "
                f"Analyze the injected text data carefully. You must extract and present the explicit "
                f"dates, figures, names, and specific events present in the live text. "
                f"Do NOT state that information is unavailable if there are relevant names, governors, "
                f"or statements in the text below. Rely completely on the provided facts to build an "
                f"accurate, detailed chronological answer.\n\n"
                f"LIVE CONTEXT DATA:\n"
                f"{live_context}\n\n"
            )
            user_message = context_header + user_message
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            msg_image = msg.get("image_data", "")
            if msg_image:
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

    def _try_llm_first(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "", mongo_history: list = None, global_memories: str = "", knowledge_data: str = "") -> dict:
        try:
            response, meta = _call_llm_cluster(self._groq_messages(chat_id, user_message, image_data, live_context=live_context, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data))
            envelope = _parse_envelope(response)
            reply = str(envelope.get("reply") or "").strip()
            if not reply:
                raise RuntimeError("LLM returned no usable reply after parsing.")
            envelope["meta"] = meta
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
            expanded, _ = _call_llm_cluster(messages, timeout=15)
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

    def _think(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "", mongo_history: list = None, global_memories: str = "", knowledge_data: str = "") -> dict:
        llm_envelope = self._try_llm_first(chat_id, user_message, image_data, live_context=live_context, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data)
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

    def respond(self, message: str, chat_id: str = "", image_data: str = "", mongo_history: list = None) -> str:
        try:
            self.memory.load_memory(self.profile.key)

            message = (message or "").strip()
            if not message and not image_data:
                return "I didn't catch that - could you say something?"
            if not message:
                message = "(image attached)"

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

            try:
                if self.memory.long_term.get("creator") and message:
                    self.memory.save_creator_message(message)
            except Exception as exc:
                print(f"[WARN] Failed to process creator instruction: {exc}")

            # Auto-title on first message of any new session
            if msg_count_before == 0:
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

            # ── Pinecone cross-session recall and knowledge fetch ─────
            global_memories = ""
            knowledge_data = ""
            mm = _get_memory_mgr()
            km = _get_knowledge_mgr()
            if mm:
                try:
                    global_memories = mm.recall_sync(message)
                    if global_memories:
                        print(f"[MEMORY] Injected {len(global_memories)} chars of cross-session memory")
                except Exception as exc:
                    print(f"[MEMORY] Global recall failed: {exc}")
            if km:
                try:
                    knowledge_data = km.recall_sync(message)
                    if knowledge_data:
                        print(f"[KNOWLEDGE] Injected {len(knowledge_data)} chars of web crawl data")
                except Exception as exc:
                    print(f"[KNOWLEDGE] Knowledge recall failed: {exc}")

            envelope = self._think(cid, message, image_data, live_context=live_ctx, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data)
            self.last_response_meta = envelope.get("meta") or self._metadata(False, True, "local")
            raw_reply = str(envelope.get("reply") or "").strip()
            reply = _sanitize_reply_for_chat(raw_reply, "") if raw_reply else FALLBACK_RESPONSE

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

            # ── Save this interaction to Pinecone memory ──────────────
            if mm:
                try:
                    mm.save_sync(user_msg, reply, cid)
                except Exception as exc:
                    print(f"[MEMORY] save_sync failed: {exc}")

            try:
                self.memory.add_message(cid, "assistant", reply, timestamp)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (assistant) failed: {exc}")

            return reply

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in respond(): {exc}")
            self.last_response_meta = self._metadata(False, True, "local")
            return FALLBACK_RESPONSE

    def stream_respond(self, message: str, chat_id: str = "", image_data: str = "", mongo_history: list = None):
        try:
            self.memory.load_memory(self.profile.key)
            message = (message or "").strip()
            if not message and not image_data:
                return
            if not message:
                message = "(image attached)"

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

                yield json.dumps({"intent": "searching_web", "query": message})

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
                    yield json.dumps({"token": ""})

                t.join(timeout=5)
                live_ctx = search_result[0]

                if live_ctx:
                    print(f"[STREAM SEARCH] Live context loaded - injecting {len(live_ctx)} chars into LLM prompt")
                else:
                    print("[STREAM SEARCH] No live context found - proceeding with cached knowledge only")

            # ── Pinecone cross-session recall and knowledge fetch ─────
            global_memories = ""
            knowledge_data = ""
            mm = _get_memory_mgr()
            km = _get_knowledge_mgr()
            if mm:
                try:
                    global_memories = mm.recall_sync(message)
                    if global_memories:
                        print(f"[STREAM MEMORY] Injected {len(global_memories)} chars of cross-session memory")
                except Exception as exc:
                    print(f"[STREAM MEMORY] Global recall failed: {exc}")
            if km:
                try:
                    knowledge_data = km.recall_sync(message)
                    if knowledge_data:
                        print(f"[STREAM KNOWLEDGE] Injected {len(knowledge_data)} chars of web crawl data")
                except Exception as exc:
                    print(f"[STREAM KNOWLEDGE] Knowledge recall failed: {exc}")

            msgs = self._groq_messages(cid, message, image_data, live_context=live_ctx, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data)
            full_reply = ""

            try:
                for token in _call_groq_stream(msgs):
                    full_reply += token
                    yield token
            except Exception as exc:
                print(f"[STREAM ERROR] Groq streaming failed: {exc}")
                fallback = "I apologize, but I'm having trouble processing your request right now."
                full_reply = fallback
                yield fallback

            try:
                self.memory.add_message(cid, "assistant", full_reply or "No response", timestamp)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (assistant) failed: {exc}")

            try:
                self.memory.save_memory()
            except Exception as exc:
                print(f"[ERROR] Memory save failed: {exc}")

            # ── Save this interaction to Pinecone memory ──────────────
            if mm:
                try:
                    mm.save_sync(user_msg, full_reply, cid)
                except Exception as exc:
                    print(f"[STREAM MEMORY] save_sync failed: {exc}")

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in stream_respond(): {exc}")
            return
