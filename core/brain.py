import json
import os
import re
import ast
import operator
from datetime import datetime

import requests

from core.auto_model import get_latest_groq_model
from core.character import load_character_profile
from core.config import PROJECT_ROOT, get_config
from core.external_apis import (
    LIVE_DATA_UNAVAILABLE,
    _search_duckduckgo,
    _search_wikipedia,
    classify_live_request,
    graceful_live_failure,
    strict_live_context,
)
from core.intent_classifier import I0_CONVERSATION, I1_FACT, I6_NEWS, I7_SPORTS, classify
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

_CHAT_SYSTEM_PROMPT = """You are Marcus, the ValleyMind-AI character. Answer naturally, warmly, and directly.
Prefer short, concise responses by default. Only provide more detail if the user explicitly asks for it.
Never mention APIs, tools, prompts, keys, backend logic, internal data-fetching steps, "according to API", or "search results show" in your reply.
For normal knowledge questions, answer immediately and intelligently without asking for unnecessary clarification.
If the user asks for a short answer, be concise. If they ask for detail, depth, continuation, or "explain more", expand in clear sections.
For huge multi-topic prompts, start with a compact organized answer, cover the main points, and invite follow-up expansion without stalling.
If the user asks for simple words, avoid jargon. If they asks for a summary, prioritize the essentials.
If the user shares a memory-worthy personal fact, acknowledge it naturally; memory extraction is handled separately."""

_GROQ_STARTUP_DIAGNOSTICS_DONE = False


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
            response = requests.post(
                f"{config.groq_base_url}/chat/completions",
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

    payload: dict[str, Any] = {"contents": contents}
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
) -> str:
    config = get_config()
    last_error: Optional[Exception] = None

    groq_key = config.groq_api_key
    if groq_key:
        try:
            groq_model = get_latest_groq_model()
            if groq_model:
                return _call_groq(messages, groq_model, timeout=timeout)
            print("[LLM CLUSTER] Groq skipped: no model resolved")
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] Groq failed: {_short_error_detail(str(exc))}. Rotating.")
    else:
        print("[LLM CLUSTER] Groq unavailable: no API key")

    openrouter_key = config.openrouter_api_key
    if openrouter_key:
        openrouter_model = config.openrouter_model or "openai/gpt-4o-mini"
        openrouter_url = config.openrouter_base_url or "https://openrouter.ai/api/v1"
        try:
            return _call_openai_compat(
                messages, openrouter_model, openrouter_key,
                openrouter_url, "OpenRouter", timeout,
            )
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] OpenRouter failed: {_short_error_detail(str(exc))}. Rotating.")
    else:
        print("[LLM CLUSTER] OpenRouter unavailable: no API key")

    nvidia_key = config.nvidia_api_key
    if nvidia_key:
        nvidia_model = config.nvidia_model or "nvidia/llama-3.1-nv-8b-instruct"
        nvidia_url = config.nvidia_base_url or "https://integrate.api.nvidia.com/v1"
        try:
            return _call_openai_compat(
                messages, nvidia_model, nvidia_key,
                nvidia_url, "Nvidia", timeout,
            )
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] Nvidia failed: {_short_error_detail(str(exc))}. Rotating.")
    else:
        print("[LLM CLUSTER] Nvidia unavailable: no API key")

    gemini_key = config.gemini_api_key
    if gemini_key:
        gemini_model = config.gemini_model or "gemini-2.0-flash"
        gemini_url = config.gemini_base_url or ""
        try:
            return _call_gemini(
                messages, gemini_model, gemini_key,
                gemini_url, timeout,
            )
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
            f"{config.groq_base_url}/models",
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
        print("[WARNING] Envelope parse failed; using sanitized raw Groq text")
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


def _extract_memory_fact(message: str) -> dict:
    text = str(message or "").strip()
    if not text:
        return {}

    patterns = [
        ("identity", "name", r"\bmy name is\s+([A-Za-z][A-Za-z .'-]{1,60})\b"),
        ("identity", "name", r"\bcall me\s+([A-Za-z][A-Za-z .'-]{1,60})\b"),
    ]

    for intent, entity, pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = re.split(r"[,.!?;]", match.group(1).strip())[0].strip()
            if value:
                return {"intent": intent, "entity": entity, "value": value}

    favorite = re.search(
        r"\bmy favorite\s+([A-Za-z][A-Za-z _-]{1,40})\s+is\s+([^,.!?;]{1,80})",
        text,
        re.IGNORECASE,
    )
    if favorite:
        entity = "favorite_" + re.sub(r"\W+", "_", favorite.group(1).strip().lower()).strip("_")
        value = favorite.group(2).strip()
        if entity and value:
            return {"intent": "preference", "entity": entity, "value": value}

    preference = re.search(
        r"\bi\s+(like|love|enjoy|prefer|hate|dislike)\s+([^,.!?;]{1,80})",
        text,
        re.IGNORECASE,
    )
    if preference:
        verb = preference.group(1).lower()
        value = preference.group(2).strip()
        if value:
            return {"intent": "preference", "entity": verb, "value": value}

    return {}


def _is_assistant_identity(entity: str, value: str) -> bool:
    combined = f"{entity} {value}".strip().lower()
    blocked_terms = {
        "marcus",
        "valleymind",
        "valley mind",
        "assistant",
        "ai",
        "bot",
        "character",
        "system",
    }
    return any(re.search(rf"\b{re.escape(term)}\b", combined) for term in blocked_terms)


def _sanitize_memory_fact(envelope: dict, source_message: str) -> dict:
    intent = str(envelope.get("intent") or "").strip()
    entity = str(envelope.get("entity") or "").strip()
    value = str(envelope.get("value") or "").strip()
    if intent not in {"identity", "preference"}:
        return envelope

    source = str(source_message or "").lower()
    explicit_user_fact = any(
        marker in source
        for marker in (
            "my name is",
            "call me",
            "my favorite",
            "i like",
            "i love",
            "i enjoy",
            "i prefer",
            "i hate",
            "i dislike",
        )
    )
    if not explicit_user_fact or _is_assistant_identity(entity, value):
        cleaned = dict(envelope)
        cleaned["intent"] = "general"
        cleaned["entity"] = ""
        cleaned["value"] = ""
        return cleaned
    return envelope


def _filtered_user_identity(identity: dict) -> dict:
    clean = {}
    for key, value in (identity or {}).items():
        if _is_assistant_identity(str(key), str(value)):
            continue
        clean[key] = value
    return clean


def _is_continue_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    return bool(re.search(
        r"\b(continue|resume|carry on|pick up)\b.*\b(last|previous|earlier|conversation|chat)\b",
        text,
    ))


def _is_conversation_recall_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    return bool(re.search(
        r"\b(what did i ask|what were we talking about|what was our last conversation|before that what were we talking about|what were we discussing before that)\b",
        text,
    ))


def _is_before_that_recall_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    return bool(re.search(r"\b(before that|prior to that|earlier than that)\b.*\b(talking about|discussing|conversation|chat)\b", text))


def _is_short_followup(message: str) -> bool:
    text = str(message or "").strip().lower()
    return bool(re.fullmatch(r"(yes|yeah|yep|sure|ok|okay|go on|continue|do it|please do|that one|both|no|nope)", text))


def _is_conversation_control_message(message: str) -> bool:
    return _is_continue_request(message) or _is_conversation_recall_request(message) or _is_short_followup(message)


def _is_conversation_control_reply(message: str) -> bool:
    text = str(message or "").strip().lower()
    return text.startswith("we were talking about") or text.startswith("before that, we were talking about")


def _normalized_message(message: str) -> str:
    return re.sub(r"\s+", " ", str(message or "").strip().lower())


_ARITHMETIC_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_arithmetic_node(node):
    if isinstance(node, ast.Expression):
        return _eval_arithmetic_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC_OPERATORS:
        left = _eval_arithmetic_node(node.left)
        right = _eval_arithmetic_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("Exponent too large")
        return _ARITHMETIC_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ARITHMETIC_OPERATORS:
        return _ARITHMETIC_OPERATORS[type(node.op)](_eval_arithmetic_node(node.operand))
    raise ValueError("Unsupported arithmetic expression")


def _local_arithmetic_reply(message: str) -> str:
    text = str(message or "").strip().lower()
    text = re.sub(r"\bwhat\s+is\b|\bcalculate\b|\bsolve\b", "", text)
    text = text.replace("x", "*").replace("÷", "/")
    text = re.sub(r"\bplus\b", "+", text)
    text = re.sub(r"\bminus\b", "-", text)
    text = re.sub(r"\btimes\b|\bmultiplied by\b", "*", text)
    text = re.sub(r"\bdivided by\b|\bover\b", "/", text)
    text = text.strip(" ?=.")
    if not text or not re.fullmatch(r"[0-9+\-*/%.() \t]+", text):
        return ""
    if not re.search(r"[+\-*/%]", text):
        return ""
    try:
        parsed = ast.parse(text, mode="eval")
        value = _eval_arithmetic_node(parsed)
    except Exception:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def _is_live_freshness_request(message: str) -> bool:
    text = _normalized_message(message)
    return bool(re.search(
        r"\b(today|now|latest|current|currently|right now|recent|breaking|live|updates?|news|headlines?|scores?|fixtures?|standings|table|injur(?:y|ies)|transfer|results?|what happened|what's happening|what's going on|current status|any new info|tell me about|give me the latest|recents? events?|happenings?|developments?|up-to-date|real-time|scores for|results for|who won|when was|what about|did .* happen|is .* happening|who is winning|update me|current affairs|happening now|this morning|last night|this afternoon|yesterday|tonight|scheduled)\b",
        text,
    ))


def _is_search_request(message: str) -> bool:
    text = _normalized_message(message)
    return bool(re.search(
        r"\b(search|find|look up|check|who is|what is|where is|how to|latest|current|recent|updates?|tell me about|who won|how did|what's the score|what was the result|how many|when did|what's happening with|give me info|any news|details on|status of|profile of|who are|what are|where are|google|find out|show me|give me|who was|what was|where was|meaning of|define|explain|why did|when was|where was|who are they|what are they|how does .* work|tell me everything)\b",
        text,
    ))


def _is_historical_sports_request(message: str) -> bool:
    text = _normalized_message(message)
    return bool(re.search(
        r"\b(history|historical|old|past|previous|all[- ]time|career|legend|won in|final in|season \d{4}|19\d{2}|20[0-2]\d)\b",
        text,
    )) and not _is_live_freshness_request(message)


def _needs_reference_context(message: str) -> bool:
    text = _normalized_message(message)
    return _is_historical_sports_request(message) or bool(re.search(
        r"\b(history of|historical|old match|past match|who won|final|career history|player history)\b",
        text,
    ))


def _reference_context_query(message: str) -> str:
    text = _normalized_message(message)
    if "liverpool" in text and "champions league" in text and "2005" in text:
        return "2005 UEFA Champions League final Liverpool AC Milan"
    return message


def _is_greeting(message: str) -> bool:
    text = _normalized_message(message)
    return bool(re.fullmatch(r"(hi|hii+|hello|hey|heyy+|yo|sup|good morning|good afternoon|good evening)[!. ]*", text))


def _is_casual_conversation_request(message: str) -> bool:
    text = _normalized_message(message)
    if _is_greeting(text):
        return True
    return bool(re.search(
        r"\b(how are you|how are things|what'?s up|wassup|how'?s it going|tell me a joke|joke|make me laugh|"
        r"casual chat|let'?s chat|talk to me|relationship|my girlfriend|my boyfriend|my wife|my husband|"
        r"i feel|i am feeling|i'm feeling|sad|lonely|angry|tired|depressed|frustrated)\b",
        text,
    ))


def _detected_route(message: str) -> dict:
    route = classify(message)
    intent = route.get("intent") or I0_CONVERSATION
    live_type = classify_live_request(message)

    if _is_casual_conversation_request(message):
        return {
            **route,
            "detected_route": "conversation",
            "live_type": "",
            "live_routing_used": False,
        }

    is_live_target = (
        live_type in {"news", "sports", "live"}
        and (_is_live_freshness_request(message) or _is_search_request(message))
        and not (live_type == "sports" and _is_historical_sports_request(message))
    )

    if is_live_target:
        return {
            **route,
            "intent": I7_SPORTS if live_type == "sports" else I6_NEWS,
            "detected_route": f"live_{live_type}" if live_type != "live" else "live_search",
            "live_type": live_type,
            "live_routing_used": True,
            "confidence": max(route.get("confidence", 0), 0.95),
        }

    if intent == I1_FACT or re.search(r"\b(what is|define|explain|meaning of|how does|why does)\b", _normalized_message(message)):
        return {
            **route,
            "detected_route": "reasoning",
            "live_type": "",
            "live_routing_used": False,
        }

    return {
        **route,
        "detected_route": "conversation",
        "live_type": "",
        "live_routing_used": False,
    }


def _is_identity_question(message: str) -> bool:
    text = _normalized_message(message)
    return bool(re.search(
        r"\b(who are you|what are you|what is your name|what's your name|your name|are you marcus|who created you)\b",
        text,
    ))


def _is_capability_question(message: str) -> bool:
    text = _normalized_message(message)
    return bool(re.search(
        r"\b(what can you do|what do you do|how can you help|what are your capabilities|help me with|what can i ask you)\b",
        text,
    ))


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


def _is_stale_fallback_text(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return lowered in {
        "even when i don't have the perfect words... i'm still here. listening. feeling.",
        "even when i donâ€™t have the perfect wordsâ€¦ iâ€™m still here. listening. feeling.",
        "ask better questions - i don't do boring.",
        "ask better questions â€” i donâ€™t do boring.",
        "i can't retrieve live data at the moment. please try again shortly.",
        "i'm having trouble responding right now. please try again.",
        "i don't have your name saved yet.",
    }


def _external_context_fallback_reply(external_context: str) -> str:
    lines = []
    for line in str(external_context or "").splitlines():
        cleaned = line.strip()
        if cleaned.startswith("- "):
            lines.append(cleaned)
        if len(lines) >= 3:
            break
    if not lines:
        return ""
    return "I found live external results, but the language model call failed. Here are the top results I could verify:\n" + "\n".join(lines)


def _clean_live_context_fallback(external_context: str) -> str:
    lines = []
    for line in str(external_context or "").splitlines():
        cleaned = line.strip()
        if cleaned.startswith("- "):
            cleaned = re.sub(r"\s+Source:\s*\S+", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"https?://\S+", "", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            lines.append(cleaned)
        if len(lines) >= 5:
            break
    if not lines:
        return "I can't retrieve enough relevant live data at the moment. Please try again shortly."
    return "Here are the relevant updates I found:\n" + "\n".join(lines)


def _is_uncertain_reply(text: str) -> bool:
    lowered = str(text or "").lower()
    return bool(re.search(
        r"\b(don't have|do not have|no access to|unable to access|cannot access|don't know|do not know|not sure|unsure|haven't been informed|no information|current information|real-time|latest information|knowledge cutoff|as an ai)\b",
        lowered,
    ))


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
        self._fallback_index: int = 0
        self._last_local_reply: str = ""
        self._diagnostics_done = False
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
        print("[BRAIN] architecture: 4-LLM Cooperative Cluster (Groq → OpenRouter → Nvidia → Gemini)")
        print(f"[BRAIN] active Groq model: {model or '<missing>'}")
        print(f"[BRAIN] endpoint: {config.groq_base_url}")
        print(f"[BRAIN] GROQ_API_KEY exists: {bool(config.groq_api_key)}")
        print(f"[BRAIN] RENDER env: {render_flag}")
        print(f"[BRAIN] OpenRouter configured: {bool(config.openrouter_api_key)}")
        print(f"[BRAIN] Nvidia configured: {bool(config.nvidia_api_key)}")
        print(f"[BRAIN] Gemini configured: {bool(config.gemini_api_key)}")
        print(f"[BRAIN] Sports pipeline: SPORTS_API_KEY → DuckDuckGo → Wikipedia")
        print(f"[BRAIN] News pipeline: NEWS_API_1 → NEWS_API_2 → DuckDuckGo → Wikipedia")
        print(f"[BRAIN] SPORTS_API_KEY configured: {bool(config.sports_api_key or config.api_sports_key)}")
        print(f"[BRAIN] NEWS_API_1 configured: {bool(config.news_api_1 or config.news_api_key)}")
        print(f"[BRAIN] NEWS_API_2 configured: {bool(config.news_api_2 or config.newscatcher_api_key or config.currents_api_key)}")
        if not config.groq_api_key:
            print("[BRAIN] Groq health: skipped because GROQ_API_KEY is missing")
            print("[BRAIN] Cluster operating in degraded mode (other providers may serve).")
            return
        healthy = _groq_health_check(model)
        print(f"[BRAIN] Groq health: {'ok' if healthy else 'failed'}")
        print("[BRAIN] All systems ready. 4-LLM cluster online.")

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

    def _handle_memory_question(self) -> str:
        try:
            self.memory.reload()
            mem = self.memory.get_full_memory() or {}
            identity = _filtered_user_identity(mem.get("identity", {}))
            prefs = mem.get("preferences", {})
            lines = []
            if identity:
                lines.append("Here's what I know about you:")
                for key, value in identity.items():
                    lines.append(f"  - {key}: {value}")
            if prefs:
                lines.append("Your preferences:")
                for key, value in prefs.items():
                    lines.append(f"  - {key}: {value}")
            if not lines:
                lines.append("I don't have anything saved about you yet.")
            return "\n".join(lines)
        except Exception as exc:
            print(f"[ERROR] _handle_memory_question failed: {exc}")
            return "I couldn't retrieve your memory right now."

    def _handle_name_question(self) -> str:
        try:
            self.memory.reload()
            name = self.memory.get_user_name() or "unknown_user"
            return f"Your name is {name}."
        except Exception as exc:
            print(f"[ERROR] _handle_name_question failed: {exc}")
            return "I couldn't retrieve your name right now."

    def _choose_variant(self, variants: list[str]) -> str:
        cleaned = [str(item).strip() for item in variants if str(item).strip()]
        if not cleaned:
            return FALLBACK_RESPONSE
        for _ in range(len(cleaned)):
            reply = cleaned[self._fallback_index % len(cleaned)]
            self._fallback_index += 1
            if reply != self._last_local_reply:
                self._last_local_reply = reply
                return reply
        self._last_local_reply = cleaned[0]
        return cleaned[0]

    def _handle_greeting(self) -> str:
        return self._choose_variant([
            "Hey. I'm here with you. What's on your mind?",
            "Hi. Good to see you. What are we working through today?",
            "Hey there. Talk to me.",
            "Hello. What are we working on?",
        ])

    def _handle_identity_question(self, message: str) -> str:
        lowered = _normalized_message(message)
        if "created" in lowered:
            return "I was created by EGBUJIE Valentine (K) for ValleyMind-AI."
        return self._choose_variant([
            "I'm Marcus, the ValleyMind-AI guide for conversation, memory continuity, reasoning, and practical support.",
            "My name is Marcus. I help you think, remember useful context, talk things through, and work on ideas.",
            "I'm Marcus inside ValleyMind-AI: a calm AI companion built for clear reasoning, memory, and conversation.",
        ])

    def _handle_capability_question(self) -> str:
        return self._choose_variant([
            "I can chat naturally, remember details you choose to share, answer questions, help solve problems, draft ideas, and think through plans with you.",
            "You can ask me to explain things, brainstorm, write, debug ideas, recall saved memory, or help you make a decision.",
            "I help with conversation, memory questions, practical reasoning, creative drafts, troubleshooting, and planning.",
        ])

    def _local_knowledge_reply(self, message: str) -> str:
        text = _normalized_message(message).strip(" ?.!")
        if text in {"physics", "what is physics", "define physics", "explain physics"}:
            return (
                "Physics is the science of matter, energy, motion, forces, space, and time. "
                "It studies how the universe behaves, from everyday motion and electricity to atoms, light, gravity, and galaxies."
            )
        if text in {"biology", "is biology", "what is biology", "define biology", "explain biology"}:
            return (
                "Biology is the science of life. It studies living things, including cells, plants, animals, humans, "
                "genes, evolution, ecosystems, and how organisms grow, survive, reproduce, and interact with their environment."
            )
        if text in {"quantum physics", "what is quantum physics", "define quantum physics", "explain quantum physics"}:
            return (
                "Quantum physics is the branch of physics that explains how matter and energy behave at very small scales, "
                "such as atoms and subatomic particles. It includes ideas like quantized energy, wave-particle behavior, uncertainty, and superposition."
            )
        if text in {"relativity", "what is relativity", "define relativity", "explain relativity", "what about relativity"}:
            return (
                "Relativity is Einstein's framework for understanding space, time, motion, and gravity. "
                "Special relativity explains how time and distance change at very high speeds, while general relativity describes gravity as the curvature of spacetime."
            )
        return ""

    def _local_intent_reply(self, message: str) -> str:
        if _is_greeting(message):
            return self._handle_greeting()
        if _is_identity_question(message):
            return self._handle_identity_question(message)
        if _is_capability_question(message):
            return self._handle_capability_question()
        return ""

    def _local_error_reply(self, message: str, route: dict) -> str:
        arithmetic = _local_arithmetic_reply(message)
        if arithmetic:
            return arithmetic
        local_reply = self._local_intent_reply(message)
        if local_reply:
            return local_reply
        knowledge = self._local_knowledge_reply(message)
        if knowledge:
            return knowledge
        return FALLBACK_RESPONSE

    def _handle_continue_conversation(self, chat_id: str) -> str:
        return self._handle_conversation_recall(chat_id, depth=1)

    def _conversation_pairs(self, chat_id: str) -> list[dict]:
        try:
            history = self.memory.get_chat(chat_id) or []
        except Exception as exc:
            print(f"[ERROR] Failed to load conversation continuation history: {exc}")
            history = []

        previous = [
            item for item in history
            if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
        ]
        if previous and previous[-1].get("role") == "user":
            previous = previous[:-1]
        previous = [
            item for item in previous
            if not (item.get("role") == "assistant" and _is_stale_fallback_text(item.get("content", "")))
        ]

        pairs = []
        pending_user = None
        for item in previous:
            role = item.get("role")
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                if _is_conversation_control_message(content):
                    continue
                if pending_user:
                    pairs.append({"user": pending_user, "assistant": ""})
                pending_user = content
            elif role == "assistant" and pending_user:
                if _is_conversation_control_reply(content):
                    continue
                pairs.append({"user": pending_user, "assistant": content})
                pending_user = None
        if pending_user:
            pairs.append({"user": pending_user, "assistant": ""})
        return pairs

    def _handle_conversation_recall(self, chat_id: str, depth: int = 1) -> str:
        pairs = self._conversation_pairs(chat_id)
        if not pairs:
            return "I don't see an earlier conversation to continue yet."
        index = max(0, len(pairs) - max(1, depth))
        pair = pairs[index]
        user_text = pair.get("user", "")
        assistant_text = pair.get("assistant", "")
        label = "Before that, we were talking about" if depth > 1 else "We were talking about"
        if user_text and assistant_text:
            return (
                label
                + ": "
                + user_text
                + "\n\nMy last reply was: "
                + assistant_text
            )
        if user_text:
            return label + ": " + user_text
        return "I found the earlier chat, but there was no clear topic attached to it."

    def _format_with_groq(self, user_message: str, source_context: str, route_type: str) -> dict:
        messages = [
            {
                "role": "system",
                "content": (
                    _CHAT_SYSTEM_PROMPT
                    + "\n\nUse the supplied current context to answer naturally, as if you already know the information. "
                    + "Do not expose raw API output, JSON, provider names, HTML, CSS, buttons, icons, menus, microphone labels, attachment labels, or frontend code. "
                    + "Do not use phrases such as backend context, API response, raw results, middleware, provider, JSON, NewsAPI, Newscatcher, Currents, or API-SPORTS in the reply. "
                    + "Do not say 'according to the API' or describe the data-gathering process. "
                    + "Use only the provided context for live data. If the context does not support a claim, say it is not confirmed."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Route: {route_type}\n"
                    f"Question: {user_message}\n\n"
                    f"Current context:\n{source_context}\n\n"
                    "Return only the final user-facing answer."
                ),
            },
        ]
        try:
            raw = _call_llm_cluster(messages)
            envelope = _parse_envelope(raw)
        except Exception:
            return {
                "reply": graceful_live_failure(route_type),
                "intent": "general",
                "entity": "",
                "value": "",
            }
        envelope["reply"] = _sanitize_reply_for_chat(
            envelope.get("reply", ""),
            graceful_live_failure(route_type),
        )
        return envelope

    def _format_live_with_groq(
        self,
        user_message: str,
        source_context: str,
        route_type: str,
        initial_groq_reply: str = "",
    ) -> dict:
        messages = [
            {
                "role": "system",
                "content": (
                    _CHAT_SYSTEM_PROMPT
                    + "\n\nYou are improving an answer using current context. "
                    + "Blend the context naturally with your reasoning. "
                    + "If the initial draft conflicts with the supplied context, correct the draft and trust the context. "
                    + "Do not reveal provider names, raw snippets, JSON, middleware labels, or backend wording. "
                    + "For sports history or older facts, you may use general knowledge; for latest/current claims, rely on the supplied context. "
                    + "If the current context is thin, be honest about what is and is not confirmed while still answering usefully."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User question: {user_message}\n\n"
                    f"Initial reasoning draft:\n{initial_groq_reply}\n\n"
                    f"Current context:\n{source_context}\n\n"
                    "Write one final natural answer."
                ),
            },
        ]
        try:
            raw = _call_llm_cluster(messages)
            envelope = _parse_envelope(raw)
        except Exception:
            return {}
        envelope["reply"] = _sanitize_reply_for_chat(envelope.get("reply", ""), "")
        return envelope

    def _groq_generate_search_query(self, user_message: str) -> str:
        config = get_config()
        model = self._get_model()
        if not config.groq_api_key or not model:
            return user_message

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a search query generator. "
                    "Given a user message, output a concise, effective search query for a search engine. "
                    "Output ONLY the raw search query string. No quotes, no markdown, no explanation."
                )
            },
            {"role": "user", "content": user_message},
        ]
        try:
            query = _call_groq(messages, model, timeout=10).strip().strip('"')
            if query and len(query.split()) >= 1:
                print(f"[API] Groq optimized search query: {query}")
                return query
        except Exception as exc:
            print(f"[WARNING] Groq query generation failed: {exc}")
        return user_message

    def _route_request(self, message: str) -> dict:
        return _detected_route(message)

    def _metadata(
        self,
        groq_used: bool,
        fallback_used: bool,
        fallback_source: str,
        detected_route: str = "",
        live_routing_used: bool = False,
    ) -> dict:
        return {
            "groq_used": bool(groq_used),
            "fallback_used": bool(fallback_used),
            "fallback_source": str(fallback_source or ""),
            "detected_route": str(detected_route or ""),
            "live_routing_used": bool(live_routing_used),
        }

    def _envelope(self, reply: str, meta: dict, intent: str = "general", entity: str = "", value: str = "") -> dict:
        return {
            "reply": _sanitize_reply_for_chat(reply, ""),
            "intent": intent,
            "entity": entity,
            "value": value,
            "meta": meta,
        }

    def _groq_messages(self, chat_id: str, user_message: str) -> list:
        try:
            self.memory.reload()
            long_term = self.memory.get_full_memory() or {}
            history = self.memory.get_chat(chat_id) or []
            user_name = self.memory.get_user_name() or ""
            identity_str = json.dumps(_filtered_user_identity(long_term.get("identity", {})))
            prefs_str = json.dumps(long_term.get("preferences", {}))
        except Exception as exc:
            print(f"[ERROR] Failed to load prompt memory context: {exc}")
            history = []
            user_name = ""
            identity_str = "{}"
            prefs_str = "{}"

        system_with_context = (
            _CHAT_SYSTEM_PROMPT
            + "\n\nCharacter profile:\n"
            + self.profile.to_prompt()
            + "\n\nKnown user context, if useful:\n"
            + f"User name: {user_name}\n"
            + f"Identity: {identity_str}\n"
            + f"Preferences: {prefs_str}\n"
            + "Answer the user's current message first. Do not route away from Groq."
        )

        messages = [{"role": "system", "content": system_with_context}]
        recent = history[-20:]
        if recent and recent[-1].get("role") == "user":
            recent = recent[:-1]
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content and isinstance(content, str):
                if role == "assistant" and _is_stale_fallback_text(content):
                    continue
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        return messages

    def _try_llm_first(self, chat_id: str, user_message: str) -> dict:
        config = get_config()
        route_hint = self._route_request(user_message)
        detected_route = str(route_hint.get("detected_route") or "conversation")
        live_type = str(route_hint.get("live_type") or "")
        live_routing_used = bool(route_hint.get("live_routing_used"))
        try:
            raw = _call_llm_cluster(self._groq_messages(chat_id, user_message))
            envelope = _parse_envelope(raw)
            reply = str(envelope.get("reply") or "").strip()
            if not reply:
                raise RuntimeError("Groq returned no usable reply after parsing.")

            # Fallback to search if Groq doesn't know or lacks current info
            if _is_uncertain_reply(reply) and not live_routing_used:
                print(f"[API] Groq uncertainty detected in reply. Triggering search fallback.")
                try:
                    search_query = self._groq_generate_search_query(user_message)
                    route = strict_live_context(search_query)
                    context = route.get("context", "")
                    if context and context != LIVE_DATA_UNAVAILABLE:
                        live_envelope = self._format_live_with_groq(
                            user_message,
                            context,
                            route.get("intent") or "live",
                            reply,
                        )
                        if str(live_envelope.get("reply") or "").strip():
                            live_envelope["meta"] = self._metadata(True, False, "groq_fallback_search", detected_route, True)
                            return live_envelope
                except Exception as exc:
                    print(f"[ERROR] Groq uncertainty fallback failed: {exc}")

            if live_routing_used and live_type in {"news", "sports", "live"}:
                try:
                    search_query = self._groq_generate_search_query(user_message)
                    route = strict_live_context(search_query)
                    context = route.get("context", "")
                    if context and context != LIVE_DATA_UNAVAILABLE:
                        live_envelope = self._format_live_with_groq(
                            user_message,
                            context,
                            route.get("intent") or live_type,
                            reply,
                        )
                        live_reply = str(live_envelope.get("reply") or "").strip()
                        if live_reply:
                            live_envelope["meta"] = self._metadata(True, False, "groq", detected_route, True)
                            return live_envelope
                    envelope["reply"] = graceful_live_failure(route.get("intent") or live_type)
                    envelope["meta"] = self._metadata(True, True, "live_unavailable", detected_route, True)
                    return envelope
                except Exception as exc:
                    print(f"[ERROR] Live context Groq refinement skipped: {exc}")
                    envelope["reply"] = graceful_live_failure(live_type)
                    envelope["meta"] = self._metadata(True, True, "live_error", detected_route, True)
                    return envelope
            if _needs_reference_context(user_message):
                try:
                    context = _search_wikipedia(_reference_context_query(user_message))
                    if context and context != LIVE_DATA_UNAVAILABLE:
                        reference_envelope = self._format_live_with_groq(
                            user_message,
                            context,
                            "reference",
                            "",
                        )
                        reference_reply = str(reference_envelope.get("reply") or "").strip()
                        if reference_reply:
                            reference_envelope["meta"] = self._metadata(True, False, "groq", "reference", False)
                            return reference_envelope
                except Exception as exc:
                    print(f"[ERROR] Reference context Groq refinement skipped: {exc}")
            envelope["meta"] = self._metadata(True, False, "groq", detected_route, False)
            return envelope
        except requests.exceptions.Timeout:
            _log_groq_failure("timeout", detail="LLM cluster chat completions timed out after retry")
        except requests.exceptions.ConnectionError as exc:
            _log_groq_failure("endpoint failure", detail=f"LLM cluster network connection failed after retry: {exc}")
        except Exception as exc:
            _log_groq_failure("LLM cluster call failed after retry", detail=str(exc))
        return {}

    def _memory_fallback_reply(self, chat_id: str, message: str) -> str:
        lowered = message.lower()
        memory_triggers = (
            "what do you remember",
            "what do you know about me",
            "what have i told you",
            "do you remember",
            "recall",
            "my info",
            "my data",
        )
        name_triggers = (
            "what is my name",
            "what's my name",
            "do you know my name",
            "remember my name",
        )
        if any(trigger in lowered for trigger in name_triggers):
            return self._handle_name_question()
        if _is_continue_request(message) or _is_conversation_recall_request(message):
            return self._handle_conversation_recall(
                chat_id,
                depth=2 if _is_before_that_recall_request(message) else 1,
            )
        if any(trigger in lowered for trigger in memory_triggers):
            return self._handle_memory_question()
        return ""

    def _web_lookup_fallback_reply(self, message: str) -> str:
        context = ""
        for provider in (_search_wikipedia, _search_duckduckgo):
            try:
                context = provider(message)
                if context and context != LIVE_DATA_UNAVAILABLE:
                    break
            except Exception as exc:
                _log_groq_failure("fallback lookup failed", detail=str(exc))
        if not context or context == LIVE_DATA_UNAVAILABLE:
            return ""
        try:
            envelope = self._format_with_groq(message, context, "reference")
            reply = str(envelope.get("reply") or "").strip()
            if reply:
                return reply
        except Exception as exc:
            _log_groq_failure("LLM synthesis of web lookup failed", detail=str(exc))
        return _clean_live_context_fallback(context)

    def _local_last_resort_reply(self, message: str) -> str:
        arithmetic = _local_arithmetic_reply(message)
        if arithmetic:
            return arithmetic
        knowledge = self._local_knowledge_reply(message)
        if knowledge:
            return knowledge
        local_reply = self._local_intent_reply(message)
        if local_reply:
            return local_reply
        scripted = self.profile.scripted_response(message)
        if scripted:
            return scripted
        return FALLBACK_RESPONSE

    def _fallback_pipeline(self, chat_id: str, user_message: str) -> dict:
        route_hint = self._route_request(user_message)
        detected_route = str(route_hint.get("detected_route") or "conversation")
        live_type = str(route_hint.get("live_type") or "")
        live_routing_used = bool(route_hint.get("live_routing_used"))

        # --- 1. Local memory / reference (no external data) ---
        memory_reply = self._memory_fallback_reply(chat_id, user_message)
        if memory_reply:
            return self._envelope(memory_reply, self._metadata(False, True, "memory", detected_route, False))

        # --- 2. Live data — always gather THEN synthesize via LLM cluster ---
        external_context = ""
        route_type = ""

        if live_routing_used and live_type in {"news", "sports", "live"}:
            try:
                search_query = self._groq_generate_search_query(user_message)
                route = strict_live_context(search_query)
                external_context = route.get("context", "")
                route_type = route.get("intent") or live_type
            except Exception as exc:
                print(f"[ERROR] Live context gather failed in fallback: {exc}")

        if external_context and external_context != LIVE_DATA_UNAVAILABLE:
            try:
                live_envelope = self._format_with_groq(user_message, external_context, route_type)
                live_reply = str(live_envelope.get("reply") or "").strip()
                if live_reply:
                    live_envelope["meta"] = self._metadata(False, True, f"live_{route_type}", detected_route, True)
                    return live_envelope
            except Exception as exc:
                print(f"[ERROR] LLM cluster synthesis of live data failed in fallback: {exc}")

        # --- 3. Reference / web lookup — always synthesize via LLM cluster ---
        if detected_route == "reference" or live_routing_used:
            try:
                lookup_context = ""
                for provider in (_search_wikipedia, _search_duckduckgo):
                    try:
                        lookup_context = provider(user_message)
                        if lookup_context and lookup_context != LIVE_DATA_UNAVAILABLE:
                            break
                    except Exception:
                        continue
                if lookup_context and lookup_context != LIVE_DATA_UNAVAILABLE:
                    reference_envelope = self._format_with_groq(
                        user_message,
                        lookup_context,
                        "reference",
                    )
                    reference_reply = str(reference_envelope.get("reply") or "").strip()
                    if reference_reply:
                        reference_envelope["meta"] = self._metadata(False, True, "wiki_synthesized", detected_route, False)
                        return reference_envelope
            except Exception as exc:
                print(f"[ERROR] LLM cluster synthesis of web lookup failed: {exc}")

        # --- 4. Last resort — local JSON backup (no network / no keys ever worked) ---
        return self._envelope(
            self._local_last_resort_reply(user_message),
            self._metadata(False, True, "local", detected_route, live_routing_used),
        )

    def _think(self, chat_id: str, user_message: str) -> dict:
        llm_envelope = self._try_llm_first(chat_id, user_message)
        if llm_envelope:
            return llm_envelope
        return self._fallback_pipeline(chat_id, user_message)

    def respond(self, message: str) -> str:
        try:
            message = (message or "").strip()
            if not message:
                return "I didn't catch that - could you say something?"

            chat_id = f"{self.profile.key}_main_chat"
            timestamp = datetime.now().isoformat()
            self.memory.reload()

            try:
                self.memory.add_message(chat_id, "user", message, timestamp)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (user) failed: {exc}")

            envelope = self._think(chat_id, message)
            self.last_response_meta = envelope.get("meta") or self._metadata(False, True, "local")
            raw_reply = str(envelope.get("reply") or "").strip()
            if raw_reply:
                reply = _sanitize_reply_for_chat(raw_reply, "")
            else:
                reply = self._local_error_reply(message, self._route_request(message))
                self.last_response_meta = self._metadata(False, True, "local")

            try:
                memory_envelope = dict(envelope)
                local_memory = _extract_memory_fact(message)
                if local_memory:
                    memory_envelope.update(local_memory)
                memory_envelope = _sanitize_memory_fact(memory_envelope, message)
                intent = str(memory_envelope.get("intent") or "general").strip()
                entity = str(memory_envelope.get("entity") or "").strip()
                value = str(memory_envelope.get("value") or "").strip()

                if intent == "identity" and entity and value:
                    self.memory.remember_identity(entity, value)
                elif intent == "preference" and entity and value:
                    self.memory.remember_preference(entity, value)
                elif intent == "memory_question":
                    reply = self._handle_memory_question()
            except Exception as exc:
                print(f"[ERROR] Memory extraction/storage skipped: {exc}")

            try:
                self.memory.add_message(chat_id, "assistant", reply, timestamp)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (assistant) failed: {exc}")

            return reply

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in respond(): {exc}")
            self.last_response_meta = self._metadata(False, True, "local")
            return FALLBACK_RESPONSE
