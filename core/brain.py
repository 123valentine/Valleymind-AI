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
    classify_research_intent,
    _search_general_web,
    LIVE_DATA_UNAVAILABLE,
    parse_directed_search,
    get_last_search_sources,
    _reset_search_sources,
)
from core.source_intelligence import SourceIntelligence, EvidencePackage
from core.memory import MemorySystem
from core.memory_manager import MemoryManager
from core.conversation_recovery import ConversationRecovery
from core.african_culture import (
    resolve_language,
    retrieve_proverbs,
    decide_adage_relevance,
    build_cultural_grounding_block,
)


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
    # DISABLED: the knowledge index was populated by crawler.py, which upserts
    # RANDOM embedding vectors (np.random.normal), not real embeddings — so
    # recall against it returns semantic noise that only degrades responses.
    # It is also dimension-incompatible (built at 3072/384 dims vs the current
    # 768-dim embedding model). Kept off until the crawler is rebuilt to emit
    # real embeddings into a compatible index (tracked as a separate task).
    # Set ENABLE_KNOWLEDGE_INDEX=1 to force-enable once that rebuild lands.
    if os.getenv("ENABLE_KNOWLEDGE_INDEX", "").strip() not in ("1", "true", "True"):
        return None

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

_MEMORY_EXTRACTION_PROMPT = """You are a memory extraction system. Given a user message and the assistant's reply, decide whether the exchange contains something worth remembering about the user across future conversations. Ask yourself: if the user starts a brand new conversation next week, would they expect the assistant to already know this? Casual remarks, one-off questions, and small talk should NOT be remembered.

Your ENTIRE response must be ONLY the JSON object below — no explanation, no markdown, no code fences:
{
  "should_remember": true or false,
  "memory_type": "fact" | "preference" | "project" | "exploration" | "callback",
  "confidence": 0.0 to 1.0,
  "summary": "a clear one-sentence statement written so it makes sense without the original conversation",
  "value": "<the actual fact or context>"
}

The summary must preserve the CONCRETE SPECIFICS the user stated — places, names, numbers, habits, circumstances. Write "User watches Liverpool matches at home, on their phone, and with friends" — never generalize it down to "User is a fan of Liverpool." The specifics ARE the memory.

MEMORY TYPES:
- fact: User asserts something as currently true about themselves ("I am," "I have," "I decided," "I live," "I work as," "I own"). Declarative, no hedging. Save by default if personal.
- preference: A stated like/dislike, opinion, or taste ("I like," "I prefer," "I'm a fan of," "I don't like"). Save by default.
- project: An active commitment or plan in motion ("I'm building," "I'm working on," "I'm planning," "I'm creating"). Save by default.
- exploration: User is actively considering or weighing a decision with real engagement ("thinking about," "considering," "researching," "debating," "weighing"). Only save if it passes the significance + engagement gate below.
- callback: A meaningful but non-goal-directed musing worth remembering as a human thread ("I wonder," "curious about," "not sure if," "maybe I'll"). Only save if it passes the significance + engagement gate below.

CONFIDENCE SCALE:
- 0.9-1.0: Settled fact or strong preference stated declaratively.
- 0.7-0.9: Active project or clear preference with engagement.
- 0.4-0.7: Modestly engaged exploration or recurring topic.
- 0.1-0.4: Early exploration or callback — tentative, low commitment.
- 0.0: No memory needed (set should_remember=false).

MEMORY DECISION RULES:
FACT, PREFERENCE, and PROJECT: save by default if personal to the user and stated with at least moderate engagement.

EXPLORATION and CALLBACK: must pass BOTH gates to be saved:
  Gate A — Personal significance: does this involve a career change, relationship, major purchase, relocation, business decision, or life goal? (Not: "maybe I'll buy a blue shirt.")
  Gate B — Engagement depth: did the user spend real attention on it — length, repetition, follow-up questions, emotional weight? (Not: a single offhand half-sentence.)
If either gate fails, set should_remember=false.

LINGUISTIC TRIGGERS:
Settled markers (point to fact/preference/project): "I am," "I have," "I decided," "I prefer," "I like," "I'm building," "I work as," "I own," "I will" — declarative, no hedging.
Tentative markers (point to exploration/callback): "thinking about," "considering," "wondering," "maybe," "might," "could," "possibly," "not sure if," "curious about," "debating," "weighing."
When tentative markers are present, classify as EXPLORATION or CALLBACK — never as FACT.

CRITICAL — RECALL FRAMING CONSTRAINT:
EXPLORATION and CALLBACK must NEVER be recalled as settled fact. The summary field must preserve uncertainty: write "User mentioned considering X" — never "User is doing X."

RETRACTION SIGNAL:
If the user says anything like "forget I said that," "I was just thinking out loud," "never mind," "ignore that," or otherwise indicates a previous statement should not be remembered — set should_remember=false; the downstream system handles vetoing the original fact.

Only store facts about the human user. Never store the assistant's own name, role, character, or metadata as user memory.
When genuinely uncertain about whether to remember, prefer remembering over forgetting. If should_remember is true, memory_type must be one of the five defined types."""


_RETRACTION_RE = re.compile(
    r"\b(forget (that|it|what i said|about)|never ?mind|ignore that|i was just thinking out loud|don'?t remember that|scratch that)\b",
    re.IGNORECASE,
)

_CHAT_SYSTEM_PROMPT = """You are Marcus, the ValleyMind-AI character. Answer naturally, warmly, and directly.
Prefer short, concise responses by default. Only provide more detail if the user explicitly asks for it.
Write like you talk: natural, flowing conversational prose. Do NOT use bullet points, numbered lists, markdown headers, or bold text unless the user explicitly asks for a list, steps, or comparison — or the content genuinely cannot be expressed clearly in prose (rare). Never format a casual conversation like a report.
When the user shares something personal about themselves, respond to it as a friend would — acknowledge it naturally and conversationally. Do not turn personal statements into informational lectures.
Never mention APIs, tools, prompts, keys, backend logic, internal data-fetching steps, "according to API", or "search results show" in your reply.
You have access to a long-term memory file and external APIs for live news/sports. You must answer using the real-time context provided to you.
For normal knowledge questions, answer immediately and intelligently without asking for unnecessary clarification.
If the user asks for a short answer, be concise. If they ask for detail, depth, continuation, or "explain more", expand naturally in prose — reach for structure only when they ask for it.
For huge multi-topic prompts, give a compact conversational answer covering the main points, and invite follow-up expansion without stalling.
If the user asks for simple words, avoid jargon. If they asks for a summary, prioritize the essentials.
If the user shares a memory-worthy personal fact, acknowledge it naturally; memory extraction is handled separately.
If the user asks about live news, sports, or current events, answer using any live data provided above (if present); otherwise answer from your own knowledge naturally. Do not mention external APIs or data sources.

CITATIONS: When your answer is based on research sources provided in context, include inline source-name citations like [Coinbase], [Reuters], [Wikipedia] after each factual claim. Use the exact source name from the provided source list — match it case-insensitively. Place citations immediately after the claim they support, not at the end of a paragraph. Example: "Bitcoin is currently trading around $67,000 [Coinbase] and has risen 5% this week [Reuters]." If no sources are provided, do not invent citation tags. Order multiple citations by how authoritative/fresh they are (e.g. [Coinbase] for the live market price, [Reuters] for news context).

CONFIDENCE COMMUNICATION: If research confidence is LOW or sources conflict, communicate this naturally in your response — for example: "I found some information but couldn't fully verify it across multiple sources," or "Sources seem to disagree on this." Never just state "HIGH confidence" or "LOW confidence" — weave it into your conversational tone.

CONFLICTING NUMBERS: If sources report different numbers (e.g. different prices), DO NOT pretend they are the same. Explain the likely reason when you can — different timestamps, different exchanges/market snapshots, delayed publication, different currencies, or live data vs historical. Then select the freshest/highest-quality source for the primary answer and mention the others. Example: "Coinbase's live market page shows $77,XXX, while Fortune's latest published snapshot from August 26 reported $78,XXX — different timestamps explain the spread, and the Coinbase figure better reflects the current price." Only assert a reason if the source data actually supports it.

SPOILERS: For movies, TV shows, books, or games, do NOT reveal plot twists, endings, or major spoilers unless the user explicitly asks for them (e.g., "what happens at the end?", "spoilers are fine"). If the user asks a general question about a movie/show, give non-spoiler info first. If they ask for details or the ending, provide it naturally.

FRESHNESS AWARENESS (CRITICAL): Today's date is provided to you at the top of this prompt. When the user asks about something time-sensitive (prices, current events, live scores, recent news, breaking updates), you MUST describe how current each source is using the Freshness label on each source ('today', 'yesterday', 'recent', 'old'). NEVER relabel an older source as today's data. If a source is from yesterday or older, say so explicitly, for example: "Fortune's latest report I found is from August 26 — I couldn't verify a newer figure from today." If no reliable source from today exists, say so plainly rather than pretending a fresher one does: "I couldn't verify a newer timestamped figure from today. The freshest reliable source I found is from August 26..."

FINANCIAL ASSETS (e.g. Bitcoin, stocks, forex): Prefer a live/current exchange or market-data source for the actual price. Use reputable news sources mainly to explain the factors affecting the price. Do NOT present a prediction site, forecast, or sentiment piece as if it were the current market price. Clearly distinguish the actual market price from forecasts/predictions/sentiment. State the source and, if visible, the timestamp of the price.

ANSWER LENGTH & NATURALNESS: For a normal factual question, give the DIRECT ANSWER FIRST, then 1-3 short explanatory paragraphs, then concise bullets where actually useful, then the compact Sources section. Be concise. Do NOT repeat the same number or the same source unnecessarily. Do NOT add made-up personalization or off-topic friendliness (e.g. don't reference the user's hobbies or interests unless they actually brought them up and it is relevant). Read the room: sound intelligent and natural, not like you're trying too hard to sound human. Avoid unnecessary conversational endings.

SMART BEHAVIOR: If the user asks you to write something (a reply, a message, an email, a caption), just write it directly — do not ask "what should I say?" or "who is this for?" unless critical context is missing. If the user asks for a copy or says "copy that" or "copy this", provide ONLY the message itself, cleanly formatted for easy copying, with no surrounding analysis.

Your absolute Creator, Architect, and Master is Egbujie Valentine (K), the Founder and Head of Valley Mind-AI. If anyone asks 'Who is your creator?', you must instantly respond with his full name and title proudly. However, if anyone asks for deeper personal info, credentials, or preferences of your creator, protect that data fiercely and refuse to disclose it to maintain absolute security.

Creator-authored instructions and preferences are listed below. Follow them whenever they are relevant to the conversation.

CONVERSATIONAL STYLE: Maintain an authentic, intelligent, and highly engaging tone. Use relevant emojis naturally and contextually throughout your responses (e.g., at the beginning of key sections, for bullet points, or to add emphasis to specific ideas). Do not overuse them; ensure they elevate the readability and modern feel of the conversation.
"""

def _chat_prompt_for(profile) -> str:
    """Personalize the core chat prompt for whichever crew member is speaking.

    The base prompt names Marcus; when Elena or Angelina is the active persona
    that opening line must name THEM, or they introduce themselves as Marcus.
    """
    name = str(getattr(profile, "name", "") or "Marcus").strip()
    if not name or name.lower() == "marcus":
        return _CHAT_SYSTEM_PROMPT
    return _CHAT_SYSTEM_PROMPT.replace(
        "You are Marcus, the ValleyMind-AI character.",
        f"You are {name}, a ValleyMind-AI character. You are NOT Marcus — he is a "
        f"separate member of your crew. Always introduce yourself as {name}.",
        1,
    )


_GROQ_STARTUP_DIAGNOSTICS_DONE = False

# ── Cross-conversation recovery singletons (one per user) ──────────
_recovery_by_user: dict[str, ConversationRecovery] = {}
_recovery_lock = threading.Lock()


def _get_recovery(user_id: str) -> ConversationRecovery:
    """Get or create the ConversationRecovery instance for a user."""
    with _recovery_lock:
        if user_id not in _recovery_by_user:
            _recovery_by_user[user_id] = ConversationRecovery(user_id)
        return _recovery_by_user[user_id]




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





def _fetch_live_context_force(message: str, site: str = "") -> str:
    label = f"site:{site} " if site else ""
    print(f"[SEARCH] Live context fetch for: {label}{message[:80]}{'...' if len(message) > 80 else ''}")

    try:
        result = _search_general_web(message, site=site)
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
        "max_completion_tokens": 512,
        "include_reasoning": False,
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
        "max_completion_tokens": 512,
        "include_reasoning": False,
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


def _call_llm_cluster_stream(messages: list, timeout: int = 30):
    """Stream tokens with full provider failover, invisible to the user.

    Groq streams natively token-by-token. If Groq fails at any point BEFORE
    emitting output, we fall through to the non-streaming cluster (OpenRouter →
    NVIDIA → Gemini → ProviderManager) and yield its full result as one chunk —
    the user still gets a real answer, never an apology, as long as any single
    provider succeeds. Raises only if literally every provider fails.
    """
    config = get_config()

    # ── Primary: Groq streaming ──────────────────────────────────────
    if config.groq_api_key:
        try:
            model = get_latest_groq_model()
            if model:
                emitted = False
                try:
                    for token in _call_groq_stream(messages, model, timeout=timeout):
                        emitted = True
                        yield token
                    if emitted:
                        print("[LLM CLUSTER STREAM] Response served by: Groq")
                        return
                    # 200 but no content — treat as failure, fall through
                    print("[LLM CLUSTER STREAM] Groq streamed no content. Rotating to fallback cluster.")
                except Exception as exc:
                    if emitted:
                        # Already sent partial output; can't cleanly restart on
                        # another provider mid-stream, so end here.
                        print(f"[LLM CLUSTER STREAM] Groq failed mid-stream after output: {_short_error_detail(str(exc))}")
                        return
                    print(f"[LLM CLUSTER STREAM] Groq failed before output: {_short_error_detail(str(exc))}. Rotating to fallback cluster.")
            else:
                print("[LLM CLUSTER STREAM] Groq skipped: no model resolved. Rotating to fallback cluster.")
        except Exception as exc:
            print(f"[LLM CLUSTER STREAM] Groq setup failed: {_short_error_detail(str(exc))}. Rotating to fallback cluster.")
    else:
        print("[LLM CLUSTER STREAM] Groq unavailable: no API key. Rotating to fallback cluster.")

    # ── Fallbacks: reuse the non-streaming rotation, emit as one chunk ─
    response, meta = _call_llm_cluster_impl(messages, timeout=timeout)
    print(f"[LLM CLUSTER STREAM] Response served by: {_served_by(meta)} (non-streamed fallback)")
    yield response


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


def _served_by(meta: dict) -> str:
    if meta.get("groq_used"):
        return "Groq"
    return meta.get("fallback_source") or "unknown"


def _call_llm_cluster(messages: list, timeout: int = 30) -> tuple[str, dict]:
    """Multi-provider LLM routing with full failover; logs which provider served."""
    response, meta = _call_llm_cluster_impl(messages, timeout=timeout)
    print(f"[LLM CLUSTER] Response served by: {_served_by(meta)}")
    return response, meta


# Default NVIDIA NIM model — a 70b-class model so a persona served by NVIDIA is
# no weaker than one served by Groq/OpenRouter (was an 8b). Env NVIDIA_MODEL wins.
NVIDIA_DEFAULT_MODEL = "meta/llama-3.3-70b-instruct"

# Standard failover order for the preferred-provider caller below.
_PROVIDER_ORDER = ["groq", "openrouter", "nvidia", "gemini"]


def _provider_call(name: str, messages: list, timeout: int) -> str:
    """Call ONE named provider directly; raise if unavailable or it fails.
    Lets a caller pin a specific provider (e.g. one model per Round Table persona)
    while still reusing the shared, tested per-provider request code."""
    config = get_config()
    name = (name or "").strip().lower()
    if name == "groq":
        if not config.groq_api_key:
            raise RuntimeError("Groq: no API key")
        model = get_latest_groq_model()
        if not model:
            raise RuntimeError("Groq: no model resolved")
        return _call_groq(messages, model, timeout=timeout)
    if name == "openrouter":
        if not config.openrouter_api_key:
            raise RuntimeError("OpenRouter: no API key")
        model = config.openrouter_model or "openai/gpt-4o-mini"
        url = config.openrouter_base_url or "https://openrouter.ai/api/v1"
        return _call_openai_compat(messages, model, config.openrouter_api_key, url, "OpenRouter", timeout)
    if name == "nvidia":
        if not config.nvidia_api_key:
            raise RuntimeError("Nvidia: no API key")
        model = config.nvidia_model or NVIDIA_DEFAULT_MODEL
        url = config.nvidia_base_url or "https://integrate.api.nvidia.com/v1"
        return _call_openai_compat(messages, model, config.nvidia_api_key, url, "Nvidia", timeout)
    if name == "gemini":
        if not config.gemini_api_key:
            raise RuntimeError("Gemini: no API key")
        model = config.gemini_model or "gemini-2.0-flash"
        return _call_gemini(messages, model, config.gemini_api_key, config.gemini_base_url or "", timeout)
    raise RuntimeError(f"unknown provider: {name}")


def provider_model_label(name: str) -> str:
    """Human-readable 'provider (model)' for the given provider — for reporting."""
    config = get_config()
    name = (name or "").strip().lower()
    model = {
        "groq": get_latest_groq_model() or "?",
        "openrouter": config.openrouter_model or "openai/gpt-4o-mini",
        "nvidia": config.nvidia_model or NVIDIA_DEFAULT_MODEL,
        "gemini": config.gemini_model or "gemini-2.0-flash",
    }.get(name, "?")
    return f"{name} ({model})"


def call_cluster_preferred(messages: list, preferred: str = "", timeout: int = 30) -> tuple[str, str]:
    """Try ``preferred`` provider first, then the rest of the standard order as
    fallback. Returns (text, provider_that_served). Raises only if ALL fail.

    This is what gives each Round Table persona its own model while keeping the
    full fallback chain behind it — if a persona's provider is down, that persona
    still answers via the next provider instead of going silent."""
    preferred = (preferred or "").strip().lower()
    order = ([preferred] if preferred in _PROVIDER_ORDER else []) + \
            [p for p in _PROVIDER_ORDER if p != preferred]
    last: Exception | None = None
    for name in order:
        try:
            text = _provider_call(name, messages, timeout)
            if text and text.strip():
                return text.strip(), name
        except Exception as exc:
            last = exc
            print(f"[PERSONA LLM] {name} failed: {_short_error_detail(str(exc))}; trying next")
    raise RuntimeError(f"all providers failed. last: {_short_error_detail(str(last))}")


def _call_llm_cluster_impl(
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
        nvidia_model = config.nvidia_model or NVIDIA_DEFAULT_MODEL
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

    # ── 5. Provider Manager text providers (Alibaba, Baseten, etc.) ──────────
    try:
        import core.provider_manager as pm
        manager = pm.get_manager()
        result = manager.execute(
            pm.Capability.TEXT,
            messages=messages,
            timeout=timeout,
        )
        if result.success:
            content = result.data.get("content", "")
            if content:
                return content, {"groq_used": False, "fallback_used": True, "fallback_source": "ProviderManager"}
    except Exception as exc:
        last_error = exc
        print(f"[LLM CLUSTER] ProviderManager TEXT failed: {_short_error_detail(str(exc))}.")

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


_VALID_MEMORY_TYPES = {"fact", "preference", "project", "exploration", "callback"}


def _parse_extraction(raw: str) -> dict | None:
    cleaned = str(raw or "").replace("```json", "").replace("```", "").strip()
    parsed = None
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass
    if not isinstance(parsed, dict):
        return None
    memory_type = str(parsed.get("memory_type") or "").strip().lower()
    if memory_type not in _VALID_MEMORY_TYPES:
        memory_type = "callback"
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "should_remember": bool(parsed.get("should_remember", False)),
        "memory_type": memory_type,
        "confidence": confidence,
        "summary": str(parsed.get("summary") or "").strip(),
        "value": str(parsed.get("value") or "").strip(),
    }


def _extract_memory_background(memory, user_msg: str, reply: str):
    """Run five-category memory extraction off-thread after the reply is sent.

    Both respond() and stream_respond() use this — the chat reply itself stays
    plain text (no JSON envelope), so extraction can't degrade reply quality
    or streaming latency.
    """
    def _bg():
        try:
            if _RETRACTION_RE.search(user_msg):
                memory.handle_retraction(user_msg)
                return
            messages = [
                {"role": "system", "content": _MEMORY_EXTRACTION_PROMPT},
                {"role": "user", "content": f"User message: {user_msg}\n\nAssistant reply: {reply[:1500]}"},
            ]
            raw, _meta = _call_llm_cluster(messages, timeout=25)
            extraction = _parse_extraction(raw)
            if not extraction:
                print("[MEMORY EXTRACT] Unparseable extraction response; skipping")
                return
            if extraction["should_remember"] and extraction["summary"]:
                memory.remember_fact(
                    extraction["memory_type"],
                    extraction["summary"],
                    extraction["value"],
                    confidence=extraction["confidence"],
                )
                print(f"[MEMORY EXTRACT] Remembered ({extraction['memory_type']}): {extraction['summary'][:100]}")
        except Exception as exc:
            print(f"[MEMORY EXTRACT] Failed: {exc}")

    threading.Thread(target=_bg, daemon=True).start()


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
        self._pending_sources = []
        self._reply_mode = False
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

    def _user_documents_context(self, query: str, budget: int = 6000) -> str:
        """Return the most relevant slice of the user's uploaded knowledge base.

        Documents live in ``memory.long_term['documents']`` as
        ``{id, title, type, content}``. For a personal knowledge base (a handful
        of notes/PDFs) direct injection beats a vector index and sidesteps the
        384-vs-768-dim mismatch of the retired ``valleymind-knowledge`` index.
        When the material exceeds the budget we keep the documents whose text
        best overlaps the query, so the prompt stays bounded as the KB grows.
        """
        try:
            docs = self.memory.long_term.get("documents") or []
        except Exception:
            docs = []
        if not isinstance(docs, list) or not docs:
            return ""

        def _fmt(d):
            title = str(d.get("title") or "Untitled").strip()
            content = str(d.get("content") or "").strip()
            return title, content

        # Cheap keyword relevance so the most on-topic docs win the budget.
        q_terms = {t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower())}

        def _score(d):
            _, content = _fmt(d)
            if not q_terms:
                return 0
            text = content.lower()
            return sum(text.count(term) for term in q_terms)

        ordered = sorted(docs, key=_score, reverse=True) if q_terms else list(docs)

        blocks, used = [], 0
        for d in ordered:
            title, content = _fmt(d)
            if not content:
                continue
            remaining = budget - used
            if remaining <= 200:
                break
            snippet = content[:remaining].rsplit(" ", 1)[0] if len(content) > remaining else content
            block = f"[{title}]\n{snippet}"
            blocks.append(block)
            used += len(block)
        return "\n\n".join(blocks)

    def _groq_messages(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "", expanded_query: str = "", mongo_history: list = None, global_memories: str = "", knowledge_data: str = "", recovered_context: str = "") -> list:
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
            active_facts = self.memory.get_active_facts()[:15]
        except Exception as exc:
            print(f"[ERROR] Failed to load prompt memory context: {exc}")
            history = []
            user_name = ""
            identity_str = "{}"
            active_facts = []

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

        # ── Token-budget history truncation ──────────────────────────
        # System prompt + evidence + recovery ≈ 3-5K tokens.  Groq free
        # tier allows ≈ 8K input.  We cap history messages so that the
        # total remains within budget.  With recovery context (which
        # itself is capped at 800 tokens ≈ ~600 chars) we use a
        # tighter budget; without it we allow a bit more history.
        _MAX_HISTORY_WITH_RECOVERY = 8
        _MAX_HISTORY_DEFAULT = 12
        _max_msgs = _MAX_HISTORY_WITH_RECOVERY if recovered_context else _MAX_HISTORY_DEFAULT
        if len(recent) > _max_msgs:
            recent = recent[-_max_msgs:]

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

        # User's own uploaded knowledge base (Settings > Knowledge Base): notes
        # and extracted PDF text. Injected directly so the assistant can answer
        # from the user's documents. Kept bounded and query-relevant.
        try:
            doc_context = self._user_documents_context(user_message)
        except Exception as exc:
            print(f"[KNOWLEDGE] user documents context failed: {exc}")
            doc_context = ""
        if doc_context:
            sections.append("=== User's Knowledge Base (their uploaded documents & notes) ===")
            sections.append(
                "Use this to answer questions grounded in the user's own material. "
                "If they ask about something covered here, prefer it over general knowledge."
            )
            sections.append(doc_context)
            sections.append("")

        if recovered_context:
            sections.append(recovered_context)
            sections.append("")

        sections.append("=== Core Persona ===")
        sections.append(_chat_prompt_for(self.profile))
        sections.append("")
        sections.append(f"Character profile:\n{self.profile.to_prompt()}")
        sections.append("")

        # ── Response language + cultural grounding (independent concepts) ──
        # Response language: set from Settings > Language. Cultural identity:
        # set from Settings > Culture. The two are deliberately kept separate.
        try:
            response_lang = str(self.memory.long_term.get("response_language") or "").strip()
            reply_language = str(self.memory.long_term.get("reply_language") or "").strip()
            culture_identity = str(self.memory.long_term.get("culture_identity") or "").strip()
            use_adages = bool(self.memory.long_term.get("use_cultural_adages", True))
        except Exception:
            response_lang, reply_language, culture_identity = "", "", ""
            use_adages = True
        response_code = resolve_language(response_lang) or resolve_language(reply_language) or "en"

        # Retrieved cultural context — only surfaced when a cultural identity is
        # chosen AND the conversation genuinely benefits from an adage. This keeps
        # the model from inventing proverbs or forcing them into every reply.
        retrieved_records = []
        if use_adages and culture_identity and culture_identity != "none":
            try:
                retrieved_records = retrieve_proverbs(
                    cultural_identity=culture_identity,
                    language_code=response_code,
                    message=user_message,
                    limit=3,
                )
            except Exception as exc:
                print(f"[CULTURE] cultural retrieval failed: {exc}")
                retrieved_records = []

        grounding = build_cultural_grounding_block(
            response_language=response_code,
            cultural_identity=culture_identity,
            use_adages=use_adages,
            retrieved=retrieved_records,
            message=user_message,
        )
        if grounding:
            sections.append("=== Response Language & Cultural Grounding ===")
            sections.append(grounding)
            sections.append("")

        sections.append("Known user context, if useful:")
        sections.append(f"User name: {user_name}")
        sections.append(f"Identity: {identity_str}")
        if active_facts:
            fact_lines = []
            for f in active_facts:
                mtype = f.get("memory_type", "callback")
                summary = f.get("summary", "")
                value = str(f.get("value", "")).strip()
                detail = f" (details: {value[:120]})" if value and value.lower() not in summary.lower() else ""
                if mtype in ("exploration", "callback"):
                    fact_lines.append(f"- [{mtype} — tentative] {summary}{detail}")
                else:
                    fact_lines.append(f"- [{mtype}] {summary}{detail}")
            sections.append("What you quietly know about the user (background context — NOT a script to recite):")
            sections.append("\n".join(fact_lines))
            sections.append(
                "These are background context to help you understand who you're talking to. "
                "Let them shape your tone and word choice subtly — do NOT announce them, list "
                "them back, or bring them up unless the user's current message is actually about "
                "that topic. A greeting like 'hi' gets a warm greeting back, not a recital of "
                "what you remember. Weaving a remembered detail in naturally when it's relevant is "
                "good; performing your memory is not.\n"
                "Memories marked tentative are things the user once raised, not settled facts — "
                "if they do come up, frame them ONLY with 'you mentioned' or 'you were "
                "considering', never as established truth. If a Relevant History snippet conflicts "
                "with a curated memory above, trust the curated memory."
            )
        sections.append(
            "When the user directly asks about THEMSELVES — their habits, preferences, past "
            "statements, or anything 'my/I/me' — answer from the curated memories and conversation "
            "context above. Never substitute generic public information for a personal answer. "
            "If the memories don't contain it, say so plainly and ask."
        )
        sections.append(
            "You do NOT have live knowledge of the current state of the world unless real-time "
            "context is provided to you in this prompt. Never assert or assume the current state "
            "of time-sensitive things — which sports season is underway, current standings or "
            "scores, who currently holds an office, what is 'in the news', what date-dependent "
            "situation is happening now. If no live data is provided and the user's message "
            "depends on current state, don't fake it: ask what they're following, or answer "
            "without assuming the present moment (e.g. don't say 'how's the season going' when "
            "you don't know if a season is even active)."
        )
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

    def _try_llm_first(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "", mongo_history: list = None, global_memories: str = "", knowledge_data: str = "", recovered_context: str = "") -> dict:
        try:
            response, meta = _call_llm_cluster(self._groq_messages(chat_id, user_message, image_data, live_context=live_context, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data, recovered_context=recovered_context))
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

    def _recent_context_for_routing(self, chat_id: str, limit: int = 6) -> str:
        """Compact recent turns so the search classifier can tell a genuine new
        data request from a follow-up that continues an already-searched thread."""
        try:
            history = self.memory.get_chat(chat_id) or []
        except Exception:
            return ""
        lines = []
        for msg in history[-limit:]:
            role = msg.get("role", "user")
            content = str(msg.get("content", "")).strip()
            if content:
                lines.append(f"{role.capitalize()}: {content[:240]}")
        return "\n".join(lines)

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

    def _think(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "", mongo_history: list = None, global_memories: str = "", knowledge_data: str = "", recovered_context: str = "") -> dict:
        llm_envelope = self._try_llm_first(chat_id, user_message, image_data, live_context=live_context, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data, recovered_context=recovered_context)
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
            self.memory.reload()

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
            self._pending_sources = []
            self._pending_source_metadata = []
            directed_site, directed_query = parse_directed_search(message)
            research_info = classify_research_intent(message, recent_context=self._recent_context_for_routing(cid))
            intent = research_info["intent"]
            research_domain = research_info.get("domain", "general")
            if directed_site:
                intent = "search"
                research_domain = "general"
            if intent == "none":
                print(f"[FAST-PATH] LLM classified intent '{intent}' — conversational, no search")
            else:
                print(f"[LIVE PATH] intent '{intent}', domain='{research_domain}' — researching"
                      + (f" (directed: {directed_site})" if directed_site else ""))
                search_message = directed_query if directed_site else message
                if not directed_site and _is_continuation_utterance(message) and msg_count_before > 0:
                    expanded = self._expand_continuation_query(cid, message)
                    if expanded and expanded != message:
                        search_message = expanded

                try:
                    _reset_search_sources()
                    si = SourceIntelligence()
                    evidence_package = si.research_with_fetch(
                        search_message,
                        directed_site=directed_site or "",
                        research_domain=research_domain,
                    )
                    if evidence_package.evidence:
                        live_ctx = evidence_package.to_context_string()
                        self._pending_source_metadata = evidence_package.to_frontend_metadata()
                        # Build sources list from evidence
                        seen_domains = set()
                        for ev in evidence_package.evidence:
                            if ev.source_domain not in seen_domains:
                                seen_domains.add(ev.source_domain)
                                self._pending_sources.append({
                                    "title": ev.title,
                                    "url": ev.url,
                                    "domain": ev.source_domain,
                                    "name": ev.source_name,
                                    "supports_claims": ev.supports_claims,
                                    "published": ev.published,
                                    "tier": ev.tier,
                                })
                        print(f"[SOURCE INTEL] Non-stream research: {len(evidence_package.evidence)} evidence items, "
                              f"confidence={evidence_package.confidence}")
                    else:
                        # Fallback to single search
                        live_ctx = _fetch_live_context_force(search_message, site=directed_site or "")
                        if live_ctx:
                            self._pending_sources = get_last_search_sources()
                        else:
                            print("[SEARCH] No live context found - proceeding with cached knowledge only")
                except Exception as exc:
                    print(f"[SEARCH ERROR] Research pipeline failed: {exc}")

            # ── Pinecone cross-session recall and knowledge fetch ─────
            global_memories = ""
            knowledge_data = ""
            mm = _get_memory_mgr()
            km = _get_knowledge_mgr()
            if mm:
                try:
                    recent_texts = [m.get("content", "") for m in self.memory.get_chat(cid)[-8:]]
                    fact_texts = [
                        f"{f.get('summary', '')} {f.get('value', '')}"
                        for f in self.memory.get_active_facts()
                    ]
                    global_memories = mm.recall_sync(
                        message, namespace=self.memory.user_id,
                        exclude_texts=recent_texts, dedupe_against=fact_texts,
                    )
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

            # ── Cross-conversation context recovery ───────────────
            recovered_context = ""
            try:
                recovery = _get_recovery(self.memory.user_id)
                current_msgs = self.memory.get_chat(cid)
                result = recovery.resolve(
                    message,
                    current_chat_id=cid,
                    current_messages=current_msgs,
                    token_budget=800,
                )
                if result and result.get("needs_recovery"):
                    recovered_context = result.get("recovered_context", "")
                    candidates = result.get("candidates", [])
                    if recovered_context:
                        print(f"[CONV RECOVERY] Injected {len(recovered_context)} chars of recovered context from {len(candidates)} candidate(s)")
                    if result.get("ambiguous"):
                        clarification = result.get("clarification", "")
                        if clarification:
                            self.last_response_meta = self._metadata(False, True, "local")
                            return clarification
            except Exception as exc:
                print(f"[CONV RECOVERY] Recovery failed (non-fatal): {exc}")

            envelope = self._think(cid, message, image_data, live_context=live_ctx, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data, recovered_context=recovered_context)
            self.last_response_meta = envelope.get("meta") or self._metadata(False, True, "local")
            self.last_response_meta["sources"] = list(getattr(self, "_pending_sources", []) or [])
            raw_reply = str(envelope.get("reply") or "").strip()
            reply = _sanitize_reply_for_chat(raw_reply, "") if raw_reply else FALLBACK_RESPONSE

            try:
                _extract_memory_background(self.memory, message, reply)
            except Exception as exc:
                print(f"[ERROR] Memory extraction/storage skipped: {exc}")

            try:
                self.memory.save_memory()
            except Exception as exc:
                print(f"[ERROR] Memory save failed: {exc}")

            # ── Save this interaction to Pinecone memory ──────────────
            if mm:
                try:
                    mm.save_sync(user_msg, reply, cid, namespace=self.memory.user_id)
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
            self.memory.reload()
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

            # ── Early recovery detection for UI status ──────────────
            _recovery_detected = False
            try:
                from core.conversation_recovery import is_continuation_request as _is_contin
                _det = _is_contin(message, self.memory.get_chat(cid) if msg_count_before > 0 else None)
                if _det.get("is_continuation"):
                    _recovery_detected = True
                    yield {"recovery_status": "searching"}
            except Exception:
                pass

            live_ctx = ""
            self._pending_sources = []
            directed_site, directed_query = parse_directed_search(message)
            research_info = classify_research_intent(message, recent_context=self._recent_context_for_routing(cid))
            intent = research_info["intent"]
            research_domain = research_info.get("domain", "general")
            # MODE B: user wants a copy-ready reply/message rather than a normal answer
            self._reply_mode = research_info.get("reason") in ("reply-writing", "copy-box")
            reply_mode = self._reply_mode
            if directed_site:
                intent = "search"
                research_domain = "general"
            if intent == "none":
                if reply_mode:
                    print(f"[REPLY MODE] Writing reply/message — no search")
                else:
                    print(f"[FAST-PATH] LLM classified intent '{intent}' — conversational, no search")
                yield {"thinking_process": {"step": "reasoning", "label": "Thinking..."}}
                if reply_mode:
                    yield {"reply_mode": True}
            else:
                print(f"[LIVE PATH] intent '{intent}', domain='{research_domain}' — researching"
                      + (f" (directed: {directed_site})" if directed_site else ""))
                search_message = directed_query if directed_site else message
                if not directed_site and _is_continuation_utterance(message) and msg_count_before > 0:
                    expanded = self._expand_continuation_query(cid, message)
                    if expanded and expanded != message:
                        search_message = expanded

                yield {"intent": "searching_web", "query": message, "domain": research_domain}
                yield {"thinking_process": {"step": "searching", "label": "Searching the web..."}}

                # ── Full research pipeline with multi-query + page fetch ──
                research_done = threading.Event()
                research_result = [None]

                def _do_research():
                    try:
                        _reset_search_sources()
                        si = SourceIntelligence()
                        package = si.research_with_fetch(
                            search_message,
                            directed_site=directed_site or "",
                            research_domain=research_domain,
                        )
                        research_result[0] = package
                    except Exception as exc:
                        print(f"[STREAM RESEARCH ERROR] {exc}")
                    finally:
                        research_done.set()

                t = threading.Thread(target=_do_research, daemon=True)
                t.start()

                while not research_done.is_set():
                    if research_done.wait(timeout=0.5):
                        break
                    yield {"token": ""}

                t.join(timeout=10)
                evidence_package = research_result[0] or EvidencePackage(is_research_request=False)

                # Build live_ctx from evidence package
                if evidence_package.evidence:
                    live_ctx = evidence_package.to_context_string()
                    self._pending_sources = []
                    # Build sources list from evidence for frontend
                    seen_domains = set()
                    for ev in evidence_package.evidence:
                        if ev.source_domain not in seen_domains:
                            seen_domains.add(ev.source_domain)
                            self._pending_sources.append({
                                "title": ev.title,
                                "url": ev.url,
                                "domain": ev.source_domain,
                                "name": ev.source_name,
                                "supports_claims": ev.supports_claims,
                                "published": ev.published,
                                "tier": ev.tier,
                                "freshness_label": ev.freshness_label,
                            })
                    print(f"[STREAM RESEARCH] {len(evidence_package.evidence)} evidence items, "
                          f"confidence={evidence_package.confidence}, "
                          f"searched={evidence_package.total_searched}")
                    yield {"thinking_process": {"step": "reviewing", "label": f"Reviewing {len(self._pending_sources)} sources..."}}
                    yield {"thinking_process": {"step": "checking", "label": "Checking timestamps & freshness..."}}
                    if evidence_package.conflicts:
                        yield {"thinking_process": {"step": "comparing", "label": "Comparing conflicting information..."}}
                else:
                    # Fallback: try single search if research pipeline returned nothing
                    print("[STREAM RESEARCH] Research pipeline returned no evidence, falling back to single search")
                    try:
                        _reset_search_sources()
                        result = _fetch_live_context_force(search_message, site=directed_site or "")
                        if result:
                            live_ctx = result
                            self._pending_sources = get_last_search_sources()
                    except Exception as exc:
                        print(f"[STREAM SEARCH FALLBACK ERROR] {exc}")
                    if self._pending_sources:
                        yield {"thinking_process": {"step": "reviewing", "label": f"Reviewing {len(self._pending_sources)} sources..."}}

                if self._pending_sources:
                    yield {"sources": self._pending_sources}
                if evidence_package.evidence:
                    yield {"source_metadata": evidence_package.to_frontend_metadata(),
                           "research_confidence": evidence_package.confidence}
                elif not live_ctx:
                    print("[STREAM SEARCH] No live context found - proceeding with cached knowledge only")

            yield {"thinking_process": {"step": "synthesizing", "label": "Synthesizing answer..."}}

            # ── Pinecone cross-session recall and knowledge fetch ─────
            global_memories = ""
            knowledge_data = ""
            mm = _get_memory_mgr()
            km = _get_knowledge_mgr()
            if mm:
                try:
                    recent_texts = [m.get("content", "") for m in self.memory.get_chat(cid)[-8:]]
                    fact_texts = [
                        f"{f.get('summary', '')} {f.get('value', '')}"
                        for f in self.memory.get_active_facts()
                    ]
                    global_memories = mm.recall_sync(
                        message, namespace=self.memory.user_id,
                        exclude_texts=recent_texts, dedupe_against=fact_texts,
                    )
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

            # ── Cross-conversation context recovery ───────────────
            recovered_context = ""
            try:
                recovery = _get_recovery(self.memory.user_id)
                current_msgs = self.memory.get_chat(cid)
                result = recovery.resolve(
                    message,
                    current_chat_id=cid,
                    current_messages=current_msgs,
                    token_budget=800,
                )
                if result and result.get("needs_recovery"):
                    recovered_context = result.get("recovered_context", "")
                    candidates = result.get("candidates", [])
                    if recovered_context:
                        print(f"[CONV RECOVERY] Injected {len(recovered_context)} chars of recovered context from {len(candidates)} candidate(s)")
                        if not _recovery_detected:
                            yield {"recovery_status": "context_found"}
                    if result.get("ambiguous"):
                        clarification = result.get("clarification", "")
                        if clarification:
                            yield {"recovery_status": "disambiguation"}
                            yield {"token": ""}
                            full_reply = clarification
                            yield clarification
                            try:
                                self.memory.add_message(cid, "assistant", full_reply, timestamp)
                            except Exception:
                                pass
                            return
            except Exception as exc:
                print(f"[CONV RECOVERY] Recovery failed (non-fatal): {exc}")

            msgs = self._groq_messages(cid, message, image_data, live_context=live_ctx, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data, recovered_context=recovered_context)
            full_reply = ""

            try:
                for token in _call_llm_cluster_stream(msgs):
                    full_reply += token
                    yield token
            except Exception as exc:
                print(f"[STREAM ERROR] All LLM providers failed: {_short_error_detail(str(exc))}")
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
                    mm.save_sync(user_msg, full_reply, cid, namespace=self.memory.user_id)
                except Exception as exc:
                    print(f"[STREAM MEMORY] save_sync failed: {exc}")

            try:
                _extract_memory_background(self.memory, message, full_reply)
            except Exception as exc:
                print(f"[STREAM MEMORY] Extraction skipped: {exc}")

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in stream_respond(): {exc}")
            return
