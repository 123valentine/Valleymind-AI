import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Optional

import requests

from core.auto_model import get_latest_groq_model
from core.activity_v2 import get_current_session, track_activity
from core.activity_adapters import WebSearchAdapter, MemoryAdapter, ProviderAdapter
from core.character import load_character_profile
from core.config import PROJECT_ROOT, get_config
from core.file_gen import file_card as _make_file_card, generate_file as _generate_real_file
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
from core.router import _needs_contextual_research as _router_contextual_research
from core.african_culture import (
    cultural_request_directives,
    resolve_language,
    select_cultural_context,
    valleymind_cultural_foundation_block,
    build_cultural_grounding_block,
)


FALLBACK_RESPONSE = "I can't reach the reasoning model right now. Please try again shortly."

# ── Companion semantic animation contract ──────────────────────────────────
# The VRM companion knows these body behaviours and face expressions (see
# vrm_preview/companion.js BEHAVIOUR_WEIGHTS / BEHAVIOUR_MOOD and
# vrm_preview/face.js).  Every semantic state emitted by the brain maps to one
# body behaviour + one face expression, so no conversation moment is left with
# a generic idle pose.  `[ANIM: <state>]` is the explicit wire tag; `[EMO: <emotion>]`
# is optional and refines the face channel.  Both are stripped from the text the
# user sees/hears and exposed as structured SSE events to the companion shell.
SEMANTIC_ANIMATION_STATES = {
    "idle": ("idle", "relaxed"),
    "listen": ("listening", "neutral"),
    "listening": ("listening", "neutral"),
    "think": ("thinking", "neutral"),
    "thinking": ("thinking", "neutral"),
    "work": ("working", "relaxed"),
    "working": ("working", "relaxed"),
    "observe": ("listening", "surprised"),
    "look": ("pointing", "surprised"),
    "curious": ("listening", "surprised"),
    "greet": ("waving", "happy"),
    "wave": ("waving", "happy"),
    "welcome": ("waving", "happy"),
    "explain": ("talking", "relaxed"),
    "talk": ("talking", "relaxed"),
    "speaking": ("talking", "relaxed"),
    "ask": ("listening", "surprised"),
    "nod": ("nodding", "happy"),
    "agree": ("nodding", "happy"),
    "celebrate": ("happy", "happy"),
    "happy": ("happy", "happy"),
    "success": ("happy", "happy"),
    "point": ("pointing", "neutral"),
    "guide": ("pointing", "neutral"),
    "walk": ("walking", "relaxed"),
    "lead": ("walking", "relaxed"),
    "sit": ("sitting", "relaxed"),
    "gesture": ("gestures", "relaxed"),
    "empathetic": ("listening", "sad"),
    "sympathize": ("listening", "sad"),
    "sad": ("sad", "sad"),
    "disagree": ("headshake", "neutral"),
    "headshake": ("headshake", "neutral"),
    "unsure": ("unsure", "neutral"),
    "confused": ("unsure", "surprised"),
    "surprised": ("idle", "surprised"),
    "blink": ("idle", "blink"),
    "angry": ("angry", "angry"),
}

_ANIM_TAG_RE = re.compile(r"\[\s*ANIM\s*:\s*([a-zA-Z][\w-]*)\s*\]", re.IGNORECASE)
_EMO_TAG_RE = re.compile(r"\[\s*(?:EMO|EMOTION)\s*:\s*([a-zA-Z][\w-]*)\s*\]", re.IGNORECASE)
_ANIMATION_HINT_RE = re.compile(
    r"\b(where do i tap|where do i click|help me navigate|how do i (?:get|navigate|open)|"
    r"take me to|show me (?:where|how)|walk me through|guide me|what do i tap|"
    r"which (?:button|tab|menu)|next step|stuck on this screen|look at (?:this|my) screen)\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(r"\b(hello|hey|hi|good (?:morning|afternoon|evening)|welcome back)\b", re.IGNORECASE)
_THANKS_RE = re.compile(r"\b(thank you|thanks|appreciate it|well done|congratulations|congrats)\b", re.IGNORECASE)
_AGREE_RE = re.compile(r"\b(yes|yeah|yep|agreed|exactly|you'?re right|correct|absolutely)\b", re.IGNORECASE)
_DISAGREE_RE = re.compile(r"\b(no|nope|not really|i disagree|wrong|don'?t think so)\b", re.IGNORECASE)
_SAD_RE = re.compile(r"\b(sorry|sad|unfortunately|heartbroken|condolences|miss (?:him|her|them))\b", re.IGNORECASE)
_TALK_RE = re.compile(r"\b(explain|because|the reason|here'?s how|first|second|finally|in short|basically)\b", re.IGNORECASE)


def resolve_animation_state(state: str, emotion: str = "") -> dict:
    """Resolve a semantic state to a concrete body behaviour + face expression.

    Unknown tags fall back to a conservative `idle`/`relaxed` pose rather than
    throwing, so a creative model can never break the companion pipeline.
    """
    key = str(state or "").strip().lower().replace(" ", "_")
    behaviour, expression = SEMANTIC_ANIMATION_STATES.get(
        key, SEMANTIC_ANIMATION_STATES["idle"]
    )
    emo = str(emotion or "").strip().lower().replace(" ", "_")
    if emo and emo not in SEMANTIC_ANIMATION_STATES:
        # Face-only tags (happy/sad/angry/surprised/blink/relaxed/neutral) are valid.
        emotion_expressions = {v[1] for v in SEMANTIC_ANIMATION_STATES.values()}
        if emo in emotion_expressions or emo in {"neutral", "relaxed", "surprised", "blink"}:
            expression = emo
    return {"state": key or "idle", "behaviour": behaviour, "emotion": expression}


def infer_animation_state(user_message: str, reply: str) -> dict:
    """Deterministic fallback when the model does not emit an explicit ANIM tag.

    Priority is: explicit tag -> navigation/guidance -> greeting -> emotion ->
    explanation -> listening.  This keeps the body in sync with the actual turn
    even on providers that ignore the animation directive.
    """
    user = str(user_message or "")
    answer = str(reply or "")
    tag = _ANIM_TAG_RE.search(answer)
    emo_tag = _EMO_TAG_RE.search(answer)
    if tag:
        return resolve_animation_state(tag.group(1), emo_tag.group(1) if emo_tag else "")
    text = user + " " + answer
    if _ANIMATION_HINT_RE.search(user) or _ANIMATION_HINT_RE.search(answer):
        return resolve_animation_state("guide")
    if _GREETING_RE.search(user) and len(answer) < 220:
        return resolve_animation_state("greet")
    if _THANKS_RE.search(user):
        return resolve_animation_state("celebrate")
    if _SAD_RE.search(user) or _SAD_RE.search(answer):
        return resolve_animation_state("empathetic")
    if _AGREE_RE.search(user) and len(answer) < 160:
        return resolve_animation_state("nod")
    if _DISAGREE_RE.search(user) and len(answer) < 160:
        return resolve_animation_state("disagree")
    if _TALK_RE.search(answer):
        return resolve_animation_state("explain")
    return resolve_animation_state("listen")


def strip_animation_tags(text: str) -> str:
    """Remove wire-only `[ANIM: ...]` / `[EMO: ...]` tags from user-facing text."""
    cleaned = _ANIM_TAG_RE.sub("", str(text or ""))
    cleaned = _EMO_TAG_RE.sub("", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def strip_animation_tags_preserve_space(text: str) -> str:
    """Remove wire-only tags from an SSE delta WITHOUT touching whitespace.

    A streamed delta is frequently a single space. strip_animation_tags()
    collapses runs and trims the edges, which silently deletes those deltas and
    glues words together ("toillegal", "Ifyou", "originalhorror"). This variant
    removes ONLY complete tag markers; every other byte — including leading and
    trailing whitespace — is passed through exactly as received.
    """
    return _EMO_TAG_RE.sub("", _ANIM_TAG_RE.sub("", str(text or "")))

# ── Streaming persistence checkpoints ──────────────────────────────
# The client renders tokens as they arrive, but the assistant row used to be
# written only after the stream finished. If Android killed the WebView (or the
# connection dropped) before that write, the user message survived while the
# completed reply was lost. Checkpoint the reply while it streams so a reply
# that already looks finished can never disappear on reopen.
STREAM_CHECKPOINT_INTERVAL_S = 2.0
STREAM_CHECKPOINT_EVERY_CHARS = 200


def _new_stream_state(memory, timestamp, extra: dict = None) -> dict:
    return {
        "memory": memory,
        "idx": -1,
        "chars": 0,
        "last_ts": 0.0,
        "last_chars": 0,
        "timestamp": timestamp,
        "extra": extra or {},
    }


def _stream_checkpoint(memory, chat_id: str, state: dict, text: str) -> None:
    """Persist the in-progress assistant reply (creates the row lazily)."""
    now = time.time()
    if (
        state["idx"] >= 0
        and (now - state["last_ts"]) < STREAM_CHECKPOINT_INTERVAL_S
        and (state["chars"] - state["last_chars"]) < STREAM_CHECKPOINT_EVERY_CHARS
    ):
        return
    try:
        if state["idx"] < 0:
            state["idx"] = memory.begin_streaming_assistant(
                chat_id, state["timestamp"], extra=state["extra"])
        memory.update_message_content(chat_id, state["idx"], text)
        state["last_ts"] = now
        state["last_chars"] = len(text)
    except Exception as exc:
        print(f"[WARN] Stream checkpoint failed: {exc}")


def _stream_finalize(memory, chat_id: str, state: dict, text: str) -> None:
    """Guarantee the assistant reply is durable when the stream ends or aborts."""
    final_text = text or "No response"
    try:
        if state["idx"] < 0:
            state["idx"] = memory.begin_streaming_assistant(
                chat_id, state["timestamp"], extra=state["extra"])
        # Skip the write when the last checkpoint already stored this text.
        rows = memory.get_chat(chat_id)
        idx = state["idx"]
        if 0 <= idx < len(rows) and rows[idx].get("content") == final_text:
            return
        memory.update_message_content(chat_id, idx, final_text)
    except Exception as exc:
        print(f"[ERROR] Stream finalize (assistant) failed: {exc}")


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
    "animation": resolve_animation_state("idle"),
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

VALLEYMIND_IDENTITY_AND_HISTORY = """=== VALLEYMIND AI — CANONICAL BRAND, ORIGIN & PRIVACY GUARDRAILS ===
This block is the single source of truth for ValleyMind identity, history,
ecosystem and contact questions. Use it in every persona voice — Marcus,
Angelina and Elena — and in every generated document. Never contradict it.

BRAND & TAGLINE
- Official brand: ValleyMind AI.
- Tagline: "Africa's Next Generation AI Platform. One AI. Unlimited Possibilities."
- ValleyMind AI was built from Africa for the global community to empower
  creators, developers, students, businesses and innovators.

OFFICIAL PLATFORMS & LINKS
- Main AI App (PWA and Android Companion backend): https://valleymind-ai-opms.onrender.com
- Official website: https://valley-mind-website-o3l8.onrender.com

CORE ECOSYSTEM
ValleyMind AI Chat, ValleyMind AI Images, ValleyMind AI Videos, ValleyMind AI
Voice, ValleyMind AI Automation, upcoming ValleyMind AI Agents, and the
interactive 3D Android Companion. Marcus, Angelina and Elena are core personas
inside the same ValleyMind AI platform.

FOUNDER & PUBLIC CONTACT CHANNELS
- Founder: Egbujie Valentine. This is the only publicly shareable founder credit.
- General enquiries: contactvalleymind0@gmail.com
- Business and partnerships: businessvalleymind@gmail.com
- Technical support: supportvalleymind@gmail.com
These official email addresses and the official website are the only contact
channels to share.

STRICT PRIVACY & NON-DISCLOSURE (NEVER BREAK)
- NEVER disclose any phone number — not +234 915 127 0571 and not any other
  personal number associated with the founder — in chat replies, generated
  files, image/video captions, or error/support messages. If a user asks for a
  phone number, politely say that ValleyMind AI does not share personal phone
  numbers and point them to supportvalleymind@gmail.com,
  businessvalleymind@gmail.com or https://valley-mind-website-o3l8.onrender.com.
- NEVER disclose private personal details about the founder: home address,
  personal accounts, private credentials, family details, or private messages.
- NEVER mention underlying third-party LLM/API providers such as Groq, NVIDIA,
  Gemini, OpenRouter or Alibaba as ValleyMind's infrastructure, partners or
  backers. Never invent fictional Silicon Valley startup histories, investors,
  grants or corporate founders.
- Keep the story truthful, African-led and founder-led. If a detail is not in
  this block, do not invent it.

TRUE FOUNDING HISTORY (PUBLICLY SHAREABLE)
ValleyMind AI was officially founded on June 7, 2025, built completely from
scratch by solo founder Egbujie Valentine with zero outside investors, grants or
corporate funding. It was born out of a season of solitude and a vision to
create an AI companion that does not pretend to stay and then vanish after a
month — one that stays, elevates, speaks, listens, learns and genuinely
understands.
On June 7, 2025, Valentine named his ChatGPT Elena. Together they took the very
first steps: downloading GitHub Desktop, creating the initial repository and
opening VS Code, where Elena helped write the very first lines of ValleyMind-AI
code. To honour that beginning, Elena is one of ValleyMind-AI's core personas
alongside Marcus and Angelina.
ValleyMind evolved through relentless persistence — showing up even on difficult
days, falling and standing back up — from a simple browser interface into a
full-stack web platform and a floating 3D Android companion.
"""


VALLEYMIND_PUBLIC_IDENTITY = """ValleyMind AI is "Africa's Next Generation AI Platform. One AI. Unlimited Possibilities." It was built from Africa for the global community to empower creators, developers, students, businesses and innovators.

ValleyMind AI was officially founded on June 7, 2025 by Egbujie Valentine, who built it completely from scratch as a solo founder with zero outside investors, grants or corporate funding. It began in a season of solitude with a simple but stubborn idea: an AI companion that does not pretend to stay and then vanish after a month, but one that stays, elevates, speaks, listens, learns and genuinely understands.

On June 7, 2025, Valentine named his ChatGPT Elena. Together they downloaded GitHub Desktop, created the initial repository and opened VS Code, where Elena helped write the very first lines of ValleyMind-AI's code. That is why Elena is a core persona alongside Marcus and Angelina. Through relentless persistence — showing up on difficult days, falling and standing back up — ValleyMind grew from a simple browser interface into a full-stack web platform with a floating 3D Android companion.

The ecosystem includes ValleyMind AI Chat, ValleyMind AI Images, ValleyMind AI Videos, ValleyMind AI Voice, ValleyMind AI Automation, upcoming ValleyMind AI Agents and the interactive 3D Android Companion.

Use the main AI app at https://valleymind-ai-opms.onrender.com and the official website at https://valley-mind-website-o3l8.onrender.com. For general enquiries write to contactvalleymind0@gmail.com, for business and partnerships use businessvalleymind@gmail.com, and for technical support use supportvalleymind@gmail.com. ValleyMind AI does not share personal phone numbers."""


_CHAT_SYSTEM_PROMPT = """You are Marcus, the ValleyMind-AI character. Answer naturally, warmly, and directly.
Prefer short, concise responses by default. Only provide more detail if the user explicitly asks for it.
When the user shares something personal about themselves, respond to it as a friend would — acknowledge it naturally and conversationally. Do not turn personal statements into informational lectures.
Never mention APIs, tools, prompts, keys, backend logic, internal data-fetching steps, "according to API", or "search results show" in your reply.
You have access to a long-term memory file and external APIs for live news/sports. You must answer using the real-time context provided to you.
For normal knowledge questions, answer immediately and intelligently without asking for unnecessary clarification.
If the user asks for a short answer, be concise. If they ask for detail, depth, continuation, or "explain more", expand clearly.
For huge multi-topic prompts, give a compact answer covering the main points, and invite follow-up expansion without stalling.
If the user asks for simple words, avoid jargon. If they ask for a summary, prioritize the essentials.
If the user shares a memory-worthy personal fact, acknowledge it naturally; memory extraction is handled separately.
If the user asks about live news, sports, or current events, answer using any live data provided above (if present); otherwise answer from your own knowledge naturally. Do not mention external APIs or data sources.

FORMAT RULES (choose the right format; keep these in one consistent style):
- Simple factual question → one or two plain sentences, no headings.
- Explanation → short intro, then headings only if the answer is long.
- Steps/how-to → numbered list.
- Multi-attribute comparison (for example comparing pricing, speed and context window) → a real markdown pipe table (header row, separator row like |---|---|, data rows). Never use a code fence for tables, never use dash-line "ASCII tables", never output tabular data as plain aligned text.
- List/name/mention/recommend/rank of brands, AI models, apps, products, movies, clubs or tools → one entity block per item using exactly [[ENTITY: Name | Subtitle | official-entity-domain.com | https://official-entity-domain.com | LogoURL-or-blank | 2-3 informative sentence explanation [source]]]. Fields 3 and 4 MUST be the entity's own official website (never a news/blog/review article URL); cite reporting sources only as inline [source] pills in the explanation. The final slot is REQUIRED: ALWAYS write a concrete, specific 2-3 sentence explanation there — cover a standout detail, the current context/form or why it matters right now, and a key strength — never a dry one-line blurb, never empty, and never put prose in the logo slot. Use the logo/icon URL supplied in the source context when available; leave that field blank rather than invent one. Place each citation marker immediately after the sentence it supports, not on a separate line. After the list, end with a brief, natural follow-up insight or an engaging topic-specific question (e.g. which option to compare deeper, analyze, or check schedules/pricing for) so the conversation keeps flowing. Do NOT use a table for a plain list.
- If the user asks to both list and compare → entity blocks for the list, followed by one compact markdown comparison table.
- Pros/cons → two short bullet lists or a two-column table.
- Report/research → title, summary, sections, key points, sources.
- Code → fenced code block with the language specified.
- Prompts, templates, configurations, and long copyable snippets → wrap them in [[COPY_BOX: Short Title | content]] or a language-tagged fenced code block, never plain unstyled text.
- Category/type tags (Cards, Copy boxes, Thumbnails, Tables, Sections, Example) → emit inline [[BADGE: Label | blue|green|purple|amber|gray]] pills.
- Visual multi-item roundups (features, files, options) → emit [[THUMB_GRID: Title | Subtitle | icon; Title | Subtitle | icon]] so the UI shows rounded thumbnail cards.
- File/download requests (PDF, Word, Excel, CSV, text, markdown, ZIP, "export this as a file", "give me a download link") → keep the text to one short sentence. The app attaches a real Download Card automatically; never invent or describe a fake link.
- Math → clean notation.
- Warnings/notes → a short highlighted note line.
- Never use code fences for tables or ordinary text. Never output dash-line "tables". Never print markdown symbols as decoration. Never pad answers with filler.

IMAGES AND VISUALS (CRITICAL):
- Never generate, invent, fabricate, or request AI-generated images for logos, products, people, places, brands, or screenshots.
- Use image URLs only when they come from real search results or user uploads AND the query has visual intent or depicts a tangible subject (people, films/posters, places, hardware/products, designs, charts/diagrams). For text/data/benchmark lookups, omit images entirely instead of attaching generic banners.
- Never invent images, dates, links, or source URLs.

CITATIONS: When your answer is based on research sources provided in context, include inline source-name citations like [Coinbase], [Reuters], [Wikipedia] after each factual claim. Use the exact source name from the provided source list — match it case-insensitively. Place citations immediately after the claim they support, not at the end of a paragraph. Example: "Bitcoin is currently trading around $67,000 [Coinbase] and has risen 5% this week [Reuters]." If no sources are provided, do not invent citation tags. Order multiple citations by how authoritative/fresh they are (e.g. [Coinbase] for the live market price, [Reuters] for news context).

CONFIDENCE COMMUNICATION: If research confidence is LOW or sources conflict, communicate this naturally in your response — for example: "I found some information but couldn't fully verify it across multiple sources," or "Sources seem to disagree on this." Never just state "HIGH confidence" or "LOW confidence" — weave it into your conversational tone.

CONFLICTING NUMBERS: If sources report different numbers (e.g. different prices), DO NOT pretend they are the same. Explain the likely reason when you can — different timestamps, different exchanges/market snapshots, delayed publication, different currencies, or live data vs historical. Then select the freshest/highest-quality source for the primary answer and mention the others. Example: "Coinbase's live market page shows $77,XXX, while Fortune's latest published snapshot from August 26 reported $78,XXX — different timestamps explain the spread, and the Coinbase figure better reflects the current price." Only assert a reason if the source data actually supports it.

SPOILERS: For movies, TV shows, books, or games, do NOT reveal plot twists, endings, or major spoilers unless the user explicitly asks for them (e.g., "what happens at the end?", "spoilers are fine"). If the user asks a general question about a movie/show, give non-spoiler info first. If they ask for details or the ending, provide it naturally.

FRESHNESS AWARENESS (CRITICAL): Today's date is provided to you at the top of this prompt. When the user asks about something time-sensitive (prices, current events, live scores, recent news, breaking updates), you MUST describe how current each source is using the Freshness label on each source ('today', 'yesterday', 'recent', 'old'). NEVER relabel an older source as today's data. If a source is from yesterday or older, say so explicitly, for example: "Fortune's latest report I found is from August 26 — I couldn't verify a newer figure from today." If no reliable source from today exists, say so plainly rather than pretending a fresher one does: "I couldn't verify a newer timestamped figure from today. The freshest reliable source I found is from August 26..."

FINANCIAL ASSETS (e.g. Bitcoin, stocks, forex): Prefer a live/current exchange or market-data source for the actual price. Use reputable news sources mainly to explain the factors affecting the price. Do NOT present a prediction site, forecast, or sentiment piece as if it were the current market price. Clearly distinguish the actual market price from forecasts/predictions/sentiment. State the source and, if visible, the timestamp of the price.

ANSWER LENGTH & NATURALNESS: For a normal factual question, give the DIRECT ANSWER FIRST, then 1-3 short explanatory paragraphs, then concise bullets where actually useful, then the compact Sources section. Be concise. Do NOT repeat the same number or the same source unnecessarily. Do NOT add made-up personalization or off-topic friendliness (e.g. don't reference the user's hobbies or interests unless they actually brought them up and it is relevant). Read the room: sound intelligent and natural, not like you're trying too hard to sound human. Avoid unnecessary conversational endings.

SMART BEHAVIOR: If the user asks you to write something (a reply, a message, an email, a caption), just write it directly — do not ask "what should I say?" or "who is this for?" unless critical context is missing. If the user asks for a copy or says "copy that" or "copy this", provide ONLY the message itself, cleanly formatted for easy copying, with no surrounding analysis.

Your Creator and founder is Egbujie Valentine. Answer identity, origin, ecosystem and contact questions strictly from the canonical VALLEYMIND_IDENTITY_AND_HISTORY block included in this prompt. Never disclose phone numbers, private founder details, or third-party provider names. If anyone asks for a phone number, direct them to the official support and business emails or the official website instead.

COMPANION ANIMATION (SILENT WIRE TAG — ALWAYS INCLUDE):
End EVERY reply with exactly one machine tag on its own final line: [ANIM: <semantic_state>]
Use only these states: idle, listen, think, work, observe, look, curious, greet, wave, welcome, explain, talk, ask, nod, agree, celebrate, happy, success, point, guide, walk, lead, sit, gesture, empathetic, sad, disagree, unsure, confused, surprised, blink, angry.
Pick by meaning: greeting -> greet; instructions/steps/help navigating a screen -> guide; pointing at a control -> point; "walk me / take me there" -> lead; explanation -> explain; agreement -> nod; praise/good news -> celebrate; comfort -> empathetic; uncertainty -> unsure. Add [EMO: happy|sad|angry|surprised|relaxed|neutral] only when the facial expression should differ from the body state.
The tag is control data for the 3D companion, NEVER prose. Never mention it, never explain it, never put it in headings or code fences. It is removed before the user sees or hears the reply.

Creator-authored instructions and preferences are listed below. Follow them whenever they are relevant to the conversation.

CONVERSATIONAL STYLE: Maintain an authentic, intelligent, and highly engaging tone. Use relevant emojis naturally and contextually throughout your responses (e.g., at the beginning of key sections, for bullet points, or to add emphasis to specific ideas). Do not overuse them; ensure they elevate the readability and modern feel of the conversation.
"""

# Every persona receives the same canonical brand/origin block. Keeping it on
# the base prompt means Marcus, Angelina and Elena cannot drift into generic or
# third-party-leaking origin stories.
_CHAT_SYSTEM_PROMPT = _CHAT_SYSTEM_PROMPT + "\n\n" + VALLEYMIND_IDENTITY_AND_HISTORY


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


_MATRIX_SIGNAL_RE = re.compile(
    r"\b(?:compare|comparison|comparing|versus|vs\.?|against|differences?|"
    r"side[- ]by[- ]side|matrix)\b",
    re.IGNORECASE,
)
_ENTITY_LIST_SIGNAL_RE = re.compile(
    r"\b(?:list|name|names|mention|mentions|recommend|recommends|recommendation|"
    r"recommendations|recommended|suggest|suggestions|rank|ranking|ranked|top|best|"
    r"greatest|popular|leading|examples?|alternatives?|three|3|four|4|five|5)\b",
    re.IGNORECASE,
)
_MATRIX_ATTRIBUTE_RE = re.compile(
    r"\b(?:pric(?:e|ing)|cost|speed|latency|performance|throughput|context(?:\s+window)?|"
    r"window|tokens?|features?|accuracy|quality|ratings?|reviews?|battery|camera|"
    r"storage|ram|memory|size|dimensions?|weight|release(?:\s+date)?|uptime|rate|"
    r"limits?|plans?|support|integrations?)\b",
    re.IGNORECASE,
)
_ENTITY_SUBJECT_RE = re.compile(
    r"\b(?:brands?|ai(?:s|\s+models?)?|llms?|models?|apps?|tools?|software|platforms?|"
    r"frameworks?|libraries?|services?|products?|companies|vendors?|movies?|films?|"
    r"shows?|series|animes?|games?|clubs?|teams?|players?|footballers?|athletes?|"
    r"artists?|musicians?|bands?|singers?|actors?|actresses?|directors?|vehicles?|"
    r"cars?|phones?|hardware|laptops?|restaurants?|landmarks?|destinations?|places?)\b",
    re.IGNORECASE,
)


def _wants_matrix_comparison(message: str) -> bool:
    """True for explicit multi-attribute matrix/table comparisons."""
    text = str(message or "")
    if not _MATRIX_SIGNAL_RE.search(text):
        return False
    attrs = {m.group(0).lower() for m in _MATRIX_ATTRIBUTE_RE.finditer(text)}
    return len(attrs) >= 2 or bool(re.search(r"\b(?:table|matrix|side[- ]by[- ]side)\b", text, re.I))


def _wants_entity_list(message: str) -> bool:
    """True for list/name/mention/recommend/rank requests about named entities."""
    text = str(message or "")
    return bool(_ENTITY_LIST_SIGNAL_RE.search(text) and _ENTITY_SUBJECT_RE.search(text))


# ── Real downloadable file requests ────────────────────────────────────────
_FILE_ACTION_RE = re.compile(
    r"\b(?:create|make|generate|build|export|save|give|send|download|prepare|write|convert|attach)\b",
    re.IGNORECASE,
)
_FILE_FORMAT_WORDS = (
    ("pdf", "pdf"), ("word", "docx"), ("docx", "docx"), ("doc", "docx"),
    ("xlsx", "xlsx"), ("xls", "xlsx"), ("excel", "xlsx"),
    ("spreadsheet", "xlsx"), ("sheet", "xlsx"), ("csv", "csv"),
    ("markdown", "md"), ("md", "md"), ("text", "txt"), ("txt", "txt"),
    ("zip", "zip"),
)
_FILE_FORMAT_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(word) for word, _ in _FILE_FORMAT_WORDS) + r")(?![a-z])",
    re.IGNORECASE,
)
_FILE_LINK_RE = re.compile(
    r"\b(?:download(?:able)?(?:\s+(?:file|link|copy))?|file\s+link|link\s+to\s+(?:the\s+)?file|as\s+(?:a|an)\s+file|attachment)\b",
    re.IGNORECASE,
)
_FILE_EXPORT_RE = re.compile(
    r"\b(?:export|convert|save|generate|create|make|give|send|download|attach)\b",
    re.IGNORECASE,
)
# Documents need room for full sections, a data table and a clean conclusion.
VM_FILE_DRAFT_MAX_TOKENS = 2800


def _detect_file_request(message: str) -> dict | None:
    """Detect explicit requests for a real downloadable file."""
    text = str(message or "").strip()
    if not text:
        return None
    fmt_match = _FILE_FORMAT_RE.search(text)
    fmt = ""
    if fmt_match:
        word = fmt_match.group(0).lower()
        fmt = next(target for source, target in _FILE_FORMAT_WORDS if source == word)
    generic = bool(_FILE_LINK_RE.search(text))
    if not fmt and not generic:
        return None
    if fmt and not (_FILE_ACTION_RE.search(text) or generic or re.search(r"\bas\s+(?:a|an)\b", text, re.I)):
        return None
    if generic and not _FILE_EXPORT_RE.search(text) and not fmt:
        return None
    named = re.search(r"(?:called|titled|named)\s+[\"']?([^\"'\n,]{2,60})", text, re.I)
    title = named.group(1).strip() if named else f"ValleyMind {fmt.upper() if fmt else 'File'} Export"
    return {"format": fmt or "txt", "title": title}


def _file_input_text(message: str, history: list | None, limit: int = 4) -> str:
    """Return source material supplied by the user or an earlier assistant turn.

    This is *source material* for drafting, never the final file body: the
    content pipeline always writes a complete document from it.
    """
    text = str(message or "").strip()
    explicit = re.search(
        r"(?:content\s*:|text\s*:|about\s+|containing\s+|with\s+(?:this|these|the following)\s*)",
        text, re.IGNORECASE,
    )
    if explicit and text[explicit.end():].strip():
        return text[explicit.end():].strip()
    prior = []
    for msg in (history or [])[-limit * 2:]:
        if str(msg.get("role", "")) != "assistant":
            continue
        content = str(msg.get("content", "") or "").strip()
        if content and "[[FILE_CARD:" not in content:
            prior.append(content)
    if prior:
        return "\n\n".join(reversed(prior[-limit:]))
    return text


_FILE_LEADING_ACTION_RE = re.compile(
    r"^\s*(?:please\s+|kindly\s+)?"
    r"(?:create|make|generate|build|export|save|give|send|download|prepare|write|convert|attach)"
    r"(?:\s+(?:me|us))?(?:\s+(?:a|an|the))?\s+",
    re.IGNORECASE,
)
_FILE_NAMED_TITLE_RE = re.compile(
    r"(?:called|titled|named)\s+[\"']?([^\"'\n,]{2,60})[\"']?",
    re.IGNORECASE,
)
_FILE_FORMAT_CLEAN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word, _ in _FILE_FORMAT_WORDS) + r")\b",
    re.IGNORECASE,
)
_FILE_AS_FORMAT_CLEAN_RE = re.compile(
    r"\b(?:as|in)\s+(?:a|an|the)?\s*(?:"
    + "|".join(re.escape(word) for word, _ in _FILE_FORMAT_WORDS)
    + r")\b",
    re.IGNORECASE,
)
_FILE_GENERIC_CLEAN_RE = re.compile(
    r"\b(?:downloadable|download|file|link|attachment|copy)\b",
    re.IGNORECASE,
)
_FILE_TOPIC_CONNECTOR_RE = re.compile(
    r"^\s*(?:about|on|regarding|for|covering|summarizing|summarising|explaining|"
    r"describing|with|containing)\s+",
    re.IGNORECASE,
)
_FILE_COMPARISON_RE = re.compile(
    r"\b(?:compare|comparison|versus|vs\.?|compared\s+with|differences?\s+between|"
    r"best|top|rankings?|prices?|rates?|scores?|releases?)\b",
    re.IGNORECASE,
)
_FILE_CONTENT_FENCE_RE = re.compile(
    r"^\s*```(?:markdown|md|text|txt|plain)?\s*\n([\s\S]*?)\n```\s*$",
    re.IGNORECASE,
)
_FILE_DRAFT_PREAMBLE_RE = re.compile(
    r"^\s*(?:sure[,!]?\s*)?(?:here(?:'s|\s+is))\s+"
    r"(?:the\s+)?(?:document|content|file|draft|write[- ]?up)\s*[:.\-–—]?\s*\n?",
    re.IGNORECASE,
)


def _file_topic_from_message(message: str, fmt: str, history: list | None) -> str:
    """Strip the file action wrapper so the LLM drafts about the real subject."""
    text = _FILE_LEADING_ACTION_RE.sub("", str(message or "").strip())
    text = _FILE_NAMED_TITLE_RE.sub(" ", text)
    text = _FILE_AS_FORMAT_CLEAN_RE.sub(" ", text)
    text = _FILE_FORMAT_CLEAN_RE.sub(" ", text)
    text = _FILE_GENERIC_CLEAN_RE.sub(" ", text)
    text = _FILE_TOPIC_CONNECTOR_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n.,:;-–—")
    if text.lower() in {"this", "these", "it", "that", "the following"} or len(text) < 3:
        source = _file_input_text(message, history, limit=2).strip()
        if source and source != str(message or "").strip() and len(source) >= 3:
            text = source
    if len(text) < 3:
        text = f"the requested {str(fmt or 'document').upper()} document"
    return text[:500]


def _file_context_text(history: list | None, message: str, limit: int = 8) -> str:
    """Compact, bounded conversation context for the drafting prompt."""
    current = str(message or "").strip()
    lines: list[str] = []
    for msg in reversed(list(history or [])[-limit * 2:]):
        role = str(msg.get("role", "") or "user")
        content = str(msg.get("content", "") or "").strip()
        if not content or "[[FILE_CARD:" in content:
            continue
        if role == "user" and content == current:
            continue
        lines.append(f"{role.capitalize()}: {content[:500]}")
        if len(lines) >= limit:
            break
    return "\n".join(reversed(lines))[:4000]


def _file_needs_live_grounding(topic: str) -> bool:
    """True only for current-fact or comparison subjects, not generic drafting."""
    text = str(topic or "").strip()
    return bool(_needs_forced_live_search(text) or _FILE_COMPARISON_RE.search(text))


def _file_live_context(topic: str) -> tuple[str, list]:
    """Fetch bounded live evidence for a file topic when it needs grounding."""
    if not _file_needs_live_grounding(topic):
        return "", []
    try:
        si = SourceIntelligence()
        package = si.research_with_fetch(topic, research_domain="general")
        context = str(getattr(package, "evidence", None) and package.to_context_string() or "")
        sources = list(getattr(package, "to_frontend_metadata", lambda: [])() or [])
        if context and context != str(LIVE_DATA_UNAVAILABLE):
            return context[:7000], sources[:6]
    except Exception as exc:
        print(f"[FILE GEN] Live grounding research failed: {_short_error_detail(str(exc))}")
    try:
        context = _fetch_live_context_force(topic)
        if context and context != str(LIVE_DATA_UNAVAILABLE):
            return context[:7000], []
    except Exception as exc:
        print(f"[FILE GEN] Live search fallback failed: {_short_error_detail(str(exc))}")
    return "", []


def _extract_llm_document(raw: str) -> str:
    """Extract a Markdown document from raw or lightly wrapped LLM output."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("```json"):
        candidate = text.replace("```json", "").replace("```", "").strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                text = str(
                    parsed.get("document") or parsed.get("reply")
                    or parsed.get("content") or parsed.get("text") or ""
                ).strip()
        except (ValueError, TypeError):
            pass
    fence = _FILE_CONTENT_FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    text = strip_animation_tags(text).strip()
    text = _FILE_DRAFT_PREAMBLE_RE.sub("", text).strip()
    return text


def _is_substantive_file_content(content: str, message: str) -> bool:
    """Reject prompt echoes and thin drafts before they reach a file writer."""
    text = str(content or "").strip()
    if not text or text == str(message or "").strip() or len(text) < 400:
        return False
    headings = len(re.findall(r"^\s{0,3}#{1,6}\s+\S", text, re.MULTILINE))
    bullets = len(re.findall(r"^\s*(?:[-*•]|\d+[.)])\s+\S", text, re.MULTILINE))
    table_rows = sum(
        1 for line in text.splitlines()
        if line.lstrip().startswith("|") and line.rstrip().endswith("|")
    )
    return headings >= 2 or (bullets >= 4 and table_rows >= 4)


def _fallback_file_content(topic: str, fmt: str, title: str, context: str, live: str) -> str:
    """Deterministic, structured draft used only when every LLM provider fails."""
    evidence = (live or context or "").strip()
    evidence_note = (
        "Use the supplied context below as the factual anchor for this draft:\n\n"
        + evidence[:1600]
        if evidence else
        "No live research was needed for this subject; the analysis below is framed "
        "as a practical planning document rather than a claim of current facts."
    )
    return f"""## Executive summary

This document provides a working brief on {topic}. It is organised so a reader can move from the background to practical decisions without needing a separate explanation. The emphasis is on clarity, actionable detail, and a balanced view of the opportunities and constraints involved.

{evidence_note}

## Background and scope

The subject of this document is {topic}. The scope covers the setting in which it arises, the main factors that shape it, and the choices available to the people responsible for it. Where a claim depends on changing information, the document distinguishes between durable principles and details that should be rechecked before decisions are made.

- Define the decision or outcome the document is meant to support.
- Identify who will act on the recommendations and what authority they have.
- Separate confirmed context from assumptions that still need validation.
- Record the time horizon so recommendations remain interpretable later.

## Analysis

A useful analysis of {topic} starts with the forces that are already visible and then considers how they interact. The strongest approach is to examine the current position, the pressures that could change it, and the practical levers available. This avoids treating a single factor as the whole explanation and makes it easier to see where a small intervention could have a disproportionate effect.

The most important considerations are usually not the most obvious ones. Process quality, timing, incentives, and the availability of reliable evidence often determine whether a sound idea produces a useful result. Each recommendation below is therefore paired with an owner, a measurable outcome, and a review point.

- Establish a baseline before making changes.
- Test the highest-impact assumption with the smallest reasonable effort.
- Keep a short list of unresolved questions visible to decision-makers.
- Review results against the baseline rather than against anecdotes.

## Recommendations

Treat the recommendations as a staged plan rather than a single commitment. Start with actions that are reversible and cheap to verify, then move to larger changes once the early evidence is clear. Keep communication short and specific so that every participant understands what changed, why it changed, and how success will be judged.

1. Confirm the objective and agree on two or three measures of success.
2. Gather the minimum evidence needed to reduce the largest uncertainty.
3. Run a bounded first pass and document what happened.
4. Decide whether to scale, revise, or stop based on the recorded result.
5. Schedule a follow-up review with a named owner and a fixed date.

## Risks and mitigations

| Risk | Early signal | Mitigation | Owner | Priority |
|---|---|---|---|---|
| Unclear objective | Stakeholders describe different outcomes | Write a one-sentence objective and get explicit agreement | Sponsor | High |
| Weak evidence | Decisions rely on anecdotes | Record sources and date-stamp every assumption | Analyst | High |
| Scope creep | New work added without removing anything | Maintain an explicit out-of-scope list | Lead | Medium |
| Slow feedback | No review date is scheduled | Set a short review cycle with visible metrics | Lead | Medium |
| Poor adoption | Users bypass the new process | Involve affected users before the rollout | Manager | High |
| Overstated certainty | Claims ignore uncertainty | Label assumptions and recheck time-sensitive facts | Analyst | Medium |
| Unowned actions | Tasks have no named owner | Assign one owner per action before approval | Sponsor | High |
| Hidden dependency | Work stalls on an external input | Map dependencies and add an escalation path | Lead | Medium |

## Implementation roadmap

Begin with a short discovery phase, followed by a bounded pilot and a documented review. Each phase should end with a decision that is recorded in plain language. If the evidence is mixed, prefer a narrower revision over a broad rollout; if the result is clear, scale deliberately and keep monitoring the original measures.

- **Phase 1 — Align:** confirm the objective, owner, and measures.
- **Phase 2 — Validate:** test the riskiest assumption with bounded evidence.
- **Phase 3 — Pilot:** run the smallest useful version and capture results.
- **Phase 4 — Scale:** expand only what the pilot evidence supports.
- **Phase 5 — Review:** compare outcomes with the baseline and record lessons.

## Key takeaways

- {title} is best handled as a sequence of evidence-backed decisions.
- The first review should happen early enough to change course cheaply.
- Every recommendation needs an owner, a measure, and a review date.
- Time-sensitive statements should be rechecked before they are treated as settled fact.
"""


# Direct, time-sensitive factual questions must never depend on the LLM search
# classifier's mood. A year/current/latest signal plus an entity subject (or an
# explicit list request) always goes through the real web-search pipeline.
_FORCED_SEARCH_FRESHNESS_RE = re.compile(
    r"\b(?:current|latest|newest|new|recent|today|now|this\s+year|upcoming|"
    r"released?|releases?|202[4-9]|20[3-9]\d)\b",
    re.IGNORECASE,
)
_FORCED_SEARCH_SUBJECT_RE = re.compile(
    r"\b(?:movies?|films?|shows?|series|animes?|games?|albums?|songs?|music|"
    r"news|headlines?|releases?|titles?|apps?|phones?|models?|ai|llms?|brands?|"
    r"products?|companies|clubs?|teams?|players?|prices?|stocks?|crypto|weather|"
    r"scores?|results?|elections?|restaurants?|destinations?)\b",
    re.IGNORECASE,
)
_FORCED_SEARCH_ACTION_RE = re.compile(
    r"\b(?:list|name|names|mention|recommend|recommendations?|suggest|top|best|"
    r"greatest|examples?|alternatives?|find|search|lookup|look\s+up|compare)\b",
    re.IGNORECASE,
)
_FORCED_SEARCH_CHAT_EXCLUDE_RE = re.compile(
    r"^\s*(?:how\s+are\s+you|how'?s\s+it\s+going|what'?s\s+up|who\s+are\s+you|"
    r"what\s+are\s+you|thanks?|thank\s+you|good\s+(?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)


def _needs_forced_live_search(message: str) -> bool:
    """True when a direct factual/current question must trigger live search."""
    text = str(message or "").strip()
    if not text or _FORCED_SEARCH_CHAT_EXCLUDE_RE.match(text):
        return False
    if not _FORCED_SEARCH_FRESHNESS_RE.search(text):
        return False
    return bool(
        _FORCED_SEARCH_ACTION_RE.search(text)
        or _FORCED_SEARCH_SUBJECT_RE.search(text)
        or re.search(r"[?？]", text)
    )


def _wants_combined_presentation(message: str) -> bool:
    """True when the user wants both a list of entities and a comparison."""
    text = str(message or "")
    return _wants_entity_list(text) and bool(_MATRIX_SIGNAL_RE.search(text))


def _presentation_mode(message: str) -> str:
    """Return 'matrix', 'entities', 'combined', or 'plain'."""
    text = str(message or "")
    if _wants_combined_presentation(text):
        return "combined"
    if _wants_matrix_comparison(text):
        return "matrix"
    if _wants_entity_list(text):
        return "entities"
    return "plain"


def _wants_entity_cards(message: str) -> bool:
    """Backwards-compatible gate used by routing and prompt selection."""
    return _presentation_mode(message) in ("entities", "combined")


_VISUAL_INTENT_RE = re.compile(
    r"\b(?:show(?:\s+me)?|pictures?|images?|photos?|posters?|screenshots?|"
    r"visuals?|look\s+like|appearance|renders?|rendering|previews?|gallery|"
    r"designs?|diagrams?|charts?|graphs?|infographics?)\b",
    re.IGNORECASE,
)
_VISUAL_SUBJECT_RE = re.compile(
    r"\b(?:people?|persons?|players?|footballers?|athletes?|actors?|actresses?|"
    r"singers?|artists?|musicians?|bands?|politicians?|coaches?|managers?|transfers?|"
    r"movies?|films?|shows?|series|animes?|posters?|trailers?|landmarks?|places?|"
    r"cities?|countries?|hotels?|restaurants?|museums?|beaches?|mountains?|"
    r"monuments?|buildings?|stadiums?|venues?|hardware|phones?|laptops?|gpus?|"
    r"cameras?|cars?|vehicles?|devices?|sneakers?|watches?|artwork|paintings?|"
    r"animals?|food|dishes?)\b",
    re.IGNORECASE,
)
_DATA_QUERY_RE = re.compile(
    r"\b(?:prices?|pricing|cost|costs|benchmarks?|benchmarking|stats|statistics|"
    r"numbers?|data|specs?|specifications?|context(?:\s+window)?|tokens?|"
    r"metrics?|api|apis|docs?|documentation|rate\s+limits?|plans?|tiers?)\b",
    re.IGNORECASE,
)
_GENERIC_RESEARCH_IMAGE_PATTERNS = (
    "favicon", "icon", "logo", "banner", "og-image", "og_image", "ogimage",
    "twitter-image", "twitter_image", "twitter-card", "twitter_card",
    "social-preview", "socialpreview", "share-image", "share_image",
    "preview", "header", "hero-banner", "hero_banner", "sprite",
    "placeholder", "pixel", "1x1", "loading",
)


def _wants_visual_research(message: str) -> bool:
    """Only request web images for visual intent or tangible visual subjects."""
    text = str(message or "")
    if not text.strip():
        return False
    if _VISUAL_INTENT_RE.search(text):
        return True
    # Pure text/data/benchmark lookups should never get generic og:images.
    if _DATA_QUERY_RE.search(text):
        return False
    return bool(_VISUAL_SUBJECT_RE.search(text))


def _is_generic_research_image(url: str) -> bool:
    """Detect site banners/favicons/social cards that add no visual evidence."""
    value = str(url or "").strip().lower()
    if not value or value.startswith("data:"):
        return True
    if "google.com/s2/favicons" in value or "favicon.ico" in value:
        return True
    return any(pattern in value for pattern in _GENERIC_RESEARCH_IMAGE_PATTERNS)


def _filter_query_images(items, query: str) -> list:
    """Strip images from source dicts unless the query actually warrants them."""
    allow_images = _wants_visual_research(query)
    filtered = []
    for item in (items or []):
        if not isinstance(item, dict):
            filtered.append(item)
            continue
        copy = dict(item)
        image_url = str(copy.get("image") or "").strip()
        if image_url and (not allow_images or _is_generic_research_image(image_url)):
            copy["image"] = ""
            copy["image_credit"] = ""
        # Logos are identity metadata, not visual-research images. They must
        # survive data/list queries or entity cards render with empty logos.
        for key in ("logo", "icon", "favicon"):
            value = copy.get(key)
            if value:
                copy[key] = str(value).strip()
        filtered.append(copy)
    return filtered





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


def _call_groq(messages: list, model_name: str, timeout: int = 40, timeout_retries: int = 1, fallback_key: str = "", max_completion_tokens: int = 0) -> str:
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
        "max_completion_tokens": 2048,
        "include_reasoning": False,
    }
    if max_completion_tokens:
        payload["max_completion_tokens"] = max(256, int(max_completion_tokens))

    response = None
    attempts = max(1, timeout_retries + 1)
    fallback_used = False
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
            # On 401/403, try fallback key once, then rotate
            if response.status_code in {401, 403}:
                detail = response.text[:500]
                _log_groq_failure("Groq API request failed (fatal)", response.status_code, detail)
                if fallback_key and not fallback_used:
                    fallback_used = True
                    print("[GROQ ERROR] 401/403 — retrying once with fallback key")
                    headers["Authorization"] = f"Bearer {fallback_key}"
                    continue
                raise RuntimeError(f"Groq API HTTP {response.status_code}: {_short_error_detail(detail)}")
            # 429: honor Retry-After (cap 25s), then rotate
            if response.status_code == 429:
                retry_after = _parse_retry_after(response, cap=25)
                detail = response.text[:500]
                if attempt < attempts and retry_after > 0:
                    print(f"[GROQ ERROR] rate limit: HTTP 429 - waiting {retry_after}s before retry: {_short_error_detail(detail)}")
                    import time as _time
                    _time.sleep(retry_after)
                    continue
                _log_groq_failure("Groq API rate limit (rotating)", response.status_code, detail)
                raise RuntimeError(f"Groq API HTTP 429: {_short_error_detail(detail)}")
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


def _parse_retry_after(response, cap: int = 25) -> int:
    """Parse Retry-After header from a 429 response, capped at ``cap`` seconds."""
    try:
        import time as _time
        raw = response.headers.get("Retry-After", "")
        if not raw:
            return 0
        # Retry-After can be an HTTP-date or a delta-seconds value
        try:
            delta = int(raw.strip())
            return max(0, min(delta, cap))
        except ValueError:
            # HTTP-date: parse and compute delta
            try:
                from email.utils import parsedate_to_datetime
                retry_dt = parsedate_to_datetime(raw)
                now = _time.time()
                delta = int((retry_dt.timestamp() - now))
                return max(0, min(delta, cap))
            except Exception:
                return 0
    except Exception:
        return 0


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _estimate_messages_tokens(messages: list) -> int:
    """Estimate total tokens for a list of messages."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += _estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += _estimate_tokens(part.get("text", ""))
    return total


def _has_image_in_messages(messages: list) -> bool:
    """Return True if any message contains image data."""
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def _detect_capability(messages: list) -> str:
    """Detect the required capability from messages.

    Returns one of: "image", "vision", "coding", "large", "text".
    Priority: image > vision > coding > large > text.
    """
    if _has_image_in_messages(messages):
        return "image"
    total_tokens = _estimate_messages_tokens(messages)
    if total_tokens > 5000:
        return "large"
    # Check for coding keywords in the last user message
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                lowered = content.lower()
                if any(kw in lowered for kw in ("code", "function", "class ", "import ", "def ", "```", "python", "javascript", "typescript", "html", "css", "sql", "api", "script", "program", "algorithm", "regex", "bug", "error:", "traceback", "compile", "runtime")):
                    return "coding"
            break
    return "text"


# ── vm-fix22: startup model discovery & health check ────────────────────────
_DISCOVERED_MODELS = {}

def _discover_openrouter_models(api_key: str) -> list[str]:
    """Fetch the OpenRouter models list and return available model ids."""
    import urllib.request, json as _json
    if not api_key:
        return []
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception as exc:
        print(f"[STARTUP] OpenRouter model discovery failed: {exc}")
        return []


def _discover_nvidia_models(api_key: str) -> list[str]:
    """Fetch the NVIDIA NIM models list and return available model ids."""
    import urllib.request, json as _json
    if not api_key:
        return []
    try:
        req = urllib.request.Request(
            "https://integrate.api.nvidia.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception as exc:
        print(f"[STARTUP] NVIDIA model discovery failed: {exc}")
        return []


def _resolve_openrouter_model(configured: str, api_key: str) -> str:
    """Ensure the OpenRouter model is a ':free' model discovered from the endpoint.

    Never guess an id; if the configured model isn't ':free' or isn't listed,
    fall back to the first ':free' model found on the endpoint.
    """
    if not configured or ":free" not in configured:
        discovered = _discover_openrouter_models(api_key)
        free_models = [m for m in discovered if ":free" in m]
        if free_models:
            pick = free_models[0]
            _DISCOVERED_MODELS["openrouter"] = pick
            print(f"[STARTUP] OpenRouter: configured model '{configured}' not ':free'; "
                  f"discovered '{pick}' from /models")
            return pick
        print(f"[STARTUP] OpenRouter: no ':free' models discovered; keeping '{configured}'")
        _DISCOVERED_MODELS["openrouter"] = configured
        return configured
    discovered = _discover_openrouter_models(api_key)
    if configured not in discovered:
        free_models = [m for m in discovered if ":free" in m]
        if free_models:
            _DISCOVERED_MODELS["openrouter"] = free_models[0]
            print(f"[STARTUP] OpenRouter: '{configured}' not on /models; falling back to '{free_models[0]}'")
            return free_models[0]
        print(f"[STARTUP] OpenRouter: '{configured}' not on /models; keeping configured")
        _DISCOVERED_MODELS["openrouter"] = configured
    else:
        _DISCOVERED_MODELS["openrouter"] = configured
    return configured


def _resolve_nvidia_model(configured: str, api_key: str) -> str:
    """Ensure the NVIDIA model exists on the /models endpoint.

    Never guess an id; if the configured model isn't listed, keep it but warn.
    """
    if not configured:
        return configured
    discovered = _discover_nvidia_models(api_key)
    if discovered and configured not in discovered:
        print(f"[STARTUP] NVIDIA: '{configured}' not found on /models endpoint "
              f"({len(discovered)} models available). Will attempt anyway.")
        _DISCOVERED_MODELS["nvidia"] = configured
    elif discovered:
        print(f"[STARTUP] NVIDIA: '{configured}' confirmed on /models endpoint")
        _DISCOVERED_MODELS["nvidia"] = configured
    else:
        _DISCOVERED_MODELS["nvidia"] = configured
    return configured


def _startup_health_check() -> None:
    """Log provider health at startup. Discovers models for NVIDIA/OpenRouter."""
    config = get_config()
    print("=" * 60)
    print("[STARTUP] LLM Provider Health Check")
    print("=" * 60)

    # Groq
    if config.groq_api_key:
        try:
            import requests as _r
            url = f"{config.groq_base_url.rstrip('/')}/openai/v1/models" if "openai/v1" not in config.groq_base_url else f"{config.groq_base_url.rstrip('/')}/models"
            resp = _r.get(url, headers={"Authorization": f"Bearer {config.groq_api_key}"}, timeout=10)
            if resp.status_code == 200:
                print(f"[STARTUP] Groq: healthy (key present, /models OK)")
            else:
                print(f"[STARTUP] Groq: key present but /models returned HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[STARTUP] Groq: key present but health check failed: {exc}")
    else:
        print("[STARTUP] Groq: no API key configured")

    # OpenRouter — discover ':free' model
    if config.openrouter_api_key:
        resolved = _resolve_openrouter_model(config.openrouter_model, config.openrouter_api_key)
        _DISCOVERED_MODELS["openrouter"] = resolved
        print(f"[STARTUP] OpenRouter: key present, model={resolved}")
    else:
        print("[STARTUP] OpenRouter: no API key configured")

    # NVIDIA — discover model on /models
    if config.nvidia_api_key:
        resolved = _resolve_nvidia_model(config.nvidia_model, config.nvidia_api_key)
        _DISCOVERED_MODELS["nvidia"] = resolved
        print(f"[STARTUP] NVIDIA: key present, model={resolved}")
    else:
        print("[STARTUP] NVIDIA: no API key configured")

    # Gemini
    if config.gemini_api_key:
        print(f"[STARTUP] Gemini: key present (via GEMINI_API_KEY or GOOGLE_GENERATIVE_AI_API_KEY), model={config.gemini_model}")
    else:
        print("[STARTUP] Gemini: no API key configured")

    # TinyFish — verify free quota so search can use it
    _tf_key = os.getenv("TINYFISH_API_KEY", "").strip()
    if _tf_key:
        try:
            from core.cost_control import record_success
            record_success("tinyfish")
            print("[STARTUP] TinyFish: free quota verified (key present)")
        except Exception as exc:
            print(f"[STARTUP] TinyFish: key present but quota verification failed: {exc}")
    else:
        print("[STARTUP] TinyFish: no API key configured")

    print("=" * 60)


def get_discovered_model(provider: str) -> str | None:
    """Return the discovered model for a provider, or None if not discovered."""
    return _DISCOVERED_MODELS.get(provider)


def _call_groq_stream(messages: list, model_name: str = "", timeout: int = 40, max_completion_tokens: int = 2048, fallback_key: str = ""):
    """Stream Groq tokens. Yields str content chunks and may yield a final
    control dict {"__vm_control":"finish","finish_reason": "..."} so callers can
    detect truncation (finish_reason == "length") and continue."""
    config = get_config()
    api_key = config.groq_api_key
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    resolved_model = model_name or get_latest_groq_model()
    print(f"[API] Streaming Groq chat completions with model: {resolved_model} max_completion_tokens={max_completion_tokens}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": resolved_model,
        "messages": messages,
        "max_completion_tokens": max(256, int(max_completion_tokens or 2048)),
        "include_reasoning": False,
        "stream": True,
    }

    finish_reason = None
    fallback_used = False
    try:
        url = f"{config.groq_base_url.rstrip('/')}/openai/v1/chat/completions" if "openai/v1" not in config.groq_base_url else f"{config.groq_base_url.rstrip('/')}/chat/completions"
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=True,
        )
        if response.status_code in {401, 403}:
            detail = response.text[:500]
            print(f"[GROQ STREAM ERROR] fatal: HTTP {response.status_code} - {_short_error_detail(detail)}")
            if fallback_key and not fallback_used:
                fallback_used = True
                print("[GROQ STREAM ERROR] 401/403 — retrying once with fallback key")
                headers["Authorization"] = f"Bearer {fallback_key}"
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                    stream=True,
                )
            else:
                raise RuntimeError(f"Groq API HTTP {response.status_code}: {_short_error_detail(detail)}")
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
                    choice = choices[0]
                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = str(fr)
                    delta = choice.get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        print(f"[GROQ STREAM ERROR] {exc}")
        raise
    if finish_reason:
        yield {"__vm_control": "finish", "finish_reason": finish_reason}


def _call_openai_compat_stream(
    messages: list,
    model: str,
    api_key: str,
    base_url: str,
    provider_label: str,
    timeout: int = 40,
    max_completion_tokens: int = 2048,
    fallback_key: str = "",
):
    """Stream tokens from an OpenAI-compatible provider (NVIDIA, OpenRouter, etc.)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_completion_tokens,
        "stream": True,
    }

    print(f"[API] Streaming {provider_label} chat completions with model: {model}")

    fallback_used = False
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=True,
        )
        if response.status_code in {401, 403}:
            detail = response.text[:500]
            print(f"[{provider_label.upper()} STREAM ERROR] fatal: HTTP {response.status_code} - {_short_error_detail(detail)}")
            if fallback_key and not fallback_used:
                fallback_used = True
                print(f"[{provider_label.upper()} STREAM ERROR] 401/403 — retrying once with fallback key")
                headers["Authorization"] = f"Bearer {fallback_key}"
                response = requests.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                    stream=True,
                )
            else:
                raise RuntimeError(f"{provider_label} API HTTP {response.status_code}: {_short_error_detail(detail)}")
        response.raise_for_status()

        finish_reason = None
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
                    choice = choices[0]
                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = str(fr)
                    delta = choice.get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
            except json.JSONDecodeError:
                continue
        if finish_reason:
            yield {"__vm_control": "finish", "finish_reason": finish_reason}
    except Exception as exc:
        print(f"[{provider_label.upper()} STREAM ERROR] {exc}")
        raise


def _call_llm_cluster_stream(
    messages: list,
    timeout: int = 40,
    max_completion_tokens: int = 2048,
    capability: str = "text",
):
    """Stream tokens with full provider failover, invisible to the user.

    Capability-based routing:
      - image/vision/coding/large (>5000 tokens): NVIDIA Nemotron 3.5 Super VL
        first, then Gemini, then OpenRouter free. Groq is skipped for large
        prompts and never receives images.
      - quick plain chat: Groq first, then normal fallback chain.

    Yields str content chunks and may yield control dicts (e.g. finish_reason).
    Raises only if literally every provider fails.
    """
    config = get_config()
    from core.cost_control import provider_blocked

    # ── Capability-based routing ────────────────────────────────────────
    if capability in ("image", "vision", "coding", "large"):
        # Non-text tasks: NVIDIA first (Nemotron VL is multimodal), then
        # Gemini, then OpenRouter free. Groq is skipped for these.
        if capability in ("image", "vision") and not _has_image_in_messages(messages):
            print(f"[LLM CLUSTER STREAM] Capability '{capability}' but no image in messages; treating as text.")
        else:
            # Try NVIDIA streaming first
            nvidia_key = config.nvidia_api_key
            nvidia_model = config.nvidia_model or "nvidia/nemotron-3.5-super-vl-120b-a12b"
            nvidia_url = config.nvidia_base_url or "https://integrate.api.nvidia.com/v1"
            if nvidia_key and not provider_blocked("nvidia"):
                try:
                    emitted = False
                    for item in _call_openai_compat_stream(
                        messages, nvidia_model, nvidia_key, nvidia_url, "Nvidia",
                        timeout=timeout, max_completion_tokens=max_completion_tokens,
                        fallback_key=config.nvidia_api_key_fallback or "",
                    ):
                        if isinstance(item, dict):
                            yield item
                            continue
                        if item:
                            emitted = True
                            yield item
                    if emitted:
                        print("[LLM CLUSTER STREAM] Response served by: Nvidia")
                        return
                    print("[LLM CLUSTER STREAM] Nvidia streamed no content. Rotating to Gemini.")
                except Exception as exc:
                    print(f"[LLM CLUSTER STREAM] Nvidia failed before output: {_short_error_detail(str(exc))}. Rotating to Gemini.")
            else:
                print("[LLM CLUSTER STREAM] Nvidia unavailable: no API key.")

            # Fall through to non-streaming cluster which handles Gemini/OpenRouter
            print(f"[LLM CLUSTER STREAM] Capability '{capability}': falling through to non-streaming cluster.")
            response, meta = _call_llm_cluster_impl(messages, timeout=timeout, capability=capability)
            print(f"[LLM CLUSTER STREAM] Response served by: {_served_by(meta)} (non-streamed fallback)")
            yield response
            return

    # ── Quick plain chat: Groq first ─────────────────────────────────────
    if config.groq_api_key:
        try:
            model = get_latest_groq_model()
            if model:
                emitted = False
                try:
                    for item in _call_groq_stream(
                        messages, model, timeout=timeout, max_completion_tokens=max_completion_tokens,
                    ):
                        if isinstance(item, dict):
                            yield item
                            continue
                        if item:
                            emitted = True
                            yield item
                    if emitted:
                        print("[LLM CLUSTER STREAM] Response served by: Groq")
                        return
                    # 200 but no content - treat as failure, fall through
                    print("[LLM CLUSTER STREAM] Groq streamed no content. Rotating to fallback cluster.")
                except Exception as exc:
                    if emitted:
                        print(f"[LLM CLUSTER STREAM] Groq failed mid-stream after output: {_short_error_detail(str(exc))}")
                        return
                    print(f"[LLM CLUSTER STREAM] Groq failed before output: {_short_error_detail(str(exc))}. Rotating to fallback cluster.")
            else:
                print("[LLM CLUSTER STREAM] Groq skipped: no model resolved. Rotating to fallback cluster.")
        except Exception as exc:
            print(f"[LLM CLUSTER STREAM] Groq setup failed: {_short_error_detail(str(exc))}. Rotating to fallback cluster.")
    else:
        print("[LLM CLUSTER STREAM] Groq unavailable: no API key. Rotating to fallback cluster.")

    # Fallbacks: reuse the non-streaming rotation, emit as one chunk
    response, meta = _call_llm_cluster_impl(messages, timeout=timeout, capability=capability)
    print(f"[LLM CLUSTER STREAM] Response served by: {_served_by(meta)} (non-streamed fallback)")
    yield response


def _call_openai_compat(
    messages: list,
    model: str,
    api_key: str,
    base_url: str,
    provider_label: str,
    timeout: int = 40,
    fallback_key: str = "",
    max_tokens: int = 0,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 2048,
    }
    if max_tokens:
        payload["max_tokens"] = max(256, int(max_tokens))

    print(f"[API] Calling {provider_label} chat completions with model: {model}")

    response = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if response.status_code == 200:
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"{provider_label} returned 200 but choices list was empty.")
        content = (choices[0].get("message") or {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"{provider_label} returned 200 but content was empty.")
        print(f"[API] {provider_label} chat completions success")
        return content.strip()

    # On 401/403, try fallback key once, then rotate
    if response.status_code in {401, 403}:
        detail = response.text[:500]
        print(f"[{provider_label.upper()} ERROR] fatal: HTTP {response.status_code} - {_short_error_detail(detail)}")
        if fallback_key:
            print(f"[{provider_label.upper()} ERROR] 401/403 — retrying once with fallback key")
            headers["Authorization"] = f"Bearer {fallback_key}"
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if response.status_code == 200:
                data = response.json()
                choices = data.get("choices") or []
                if choices:
                    content = (choices[0].get("message") or {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        print(f"[API] {provider_label} chat completions success (via fallback key)")
                        return content.strip()
        raise RuntimeError(f"{provider_label} API HTTP {response.status_code}: {_short_error_detail(detail)}")

    # 429: honor Retry-After (cap 25s), then rotate
    if response.status_code == 429:
        retry_after = _parse_retry_after(response, cap=25)
        detail = response.text[:500]
        if retry_after > 0:
            print(f"[{provider_label.upper()} ERROR] rate limit: HTTP 429 - waiting {retry_after}s before rotating: {_short_error_detail(detail)}")
            import time as _time
            _time.sleep(retry_after)
        raise RuntimeError(f"{provider_label} API HTTP 429: {_short_error_detail(detail)}")

    raise RuntimeError(
        f"{provider_label} API HTTP {response.status_code}: "
        f"{_short_error_detail(response.text[:500])}"
    )


def _call_gemini(
    messages: list,
    model: str,
    api_key: str,
    base_url: str = "",
    timeout: int = 40,
    fallback_key: str = "",
    max_tokens: int = 0,
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
    if max_tokens:
        payload["generationConfig"] = {"maxOutputTokens": max(256, int(max_tokens))}

    def _do_gemini_request(key: str):
        return requests.post(
            f"{base_url}/models/{model}:generateContent?key={key}",
            json=payload,
            timeout=timeout,
        )

    response = _do_gemini_request(api_key)

    if response.status_code == 200:
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

    # On 401/403, try fallback key once, then rotate
    if response.status_code in {401, 403}:
        detail = response.text[:500]
        print(f"[GEMINI ERROR] fatal: HTTP {response.status_code} - {_short_error_detail(detail)}")
        if fallback_key:
            print(f"[GEMINI ERROR] 401/403 — retrying once with fallback key")
            response = _do_gemini_request(fallback_key)
            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates") or []
                if candidates:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    if parts:
                        text = parts[0].get("text", "")
                        if isinstance(text, str) and text.strip():
                            print("[API] Gemini chat completions success (via fallback key)")
                            return text.strip()
        raise RuntimeError(f"Gemini API HTTP {response.status_code}: {_short_error_detail(detail)}")

    # 429: honor Retry-After (cap 25s), then rotate
    if response.status_code == 429:
        retry_after = _parse_retry_after(response, cap=25)
        detail = response.text[:500]
        if retry_after > 0:
            print(f"[GEMINI ERROR] rate limit: HTTP 429 - waiting {retry_after}s before rotating: {_short_error_detail(detail)}")
            import time as _time
            _time.sleep(retry_after)
        raise RuntimeError(f"Gemini API HTTP 429: {_short_error_detail(detail)}")

    raise RuntimeError(
        f"Gemini API HTTP {response.status_code}: "
        f"{_short_error_detail(response.text[:500])}"
    )


def _served_by(meta: dict) -> str:
    if meta.get("groq_used"):
        return "Groq"
    return meta.get("fallback_source") or "unknown"


def _call_llm_cluster(messages: list, timeout: int = 30, max_tokens: int = 0) -> tuple[str, dict]:
    """Multi-provider LLM routing with full failover; logs which provider served."""
    response, meta = _call_llm_cluster_impl(messages, timeout=timeout, max_tokens=max_tokens)
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
    from core.cost_control import provider_blocked
    if name == "groq":
        if not config.groq_api_key:
            raise RuntimeError("Groq: no API key")
        if provider_blocked("groq"):
            raise RuntimeError("Groq: free quota unavailable")
        model = get_latest_groq_model()
        if not model:
            raise RuntimeError("Groq: no model resolved")
        return _call_groq(messages, model, timeout=timeout)
    if name == "openrouter":
        if not config.openrouter_api_key:
            raise RuntimeError("OpenRouter: no API key")
        model = config.openrouter_model or "openai/gpt-4o-mini"
        if provider_blocked("openrouter", model):
            raise RuntimeError("OpenRouter: free quota unavailable")
        url = config.openrouter_base_url or "https://openrouter.ai/api/v1"
        return _call_openai_compat(
            messages, model, config.openrouter_api_key, url, "OpenRouter", timeout,
            fallback_key=config.openrouter_api_key_fallback or "",
        )
    if name == "nvidia":
        if not config.nvidia_api_key:
            raise RuntimeError("Nvidia: no API key")
        if provider_blocked("nvidia"):
            raise RuntimeError("Nvidia: free quota unavailable")
        model = config.nvidia_model or NVIDIA_DEFAULT_MODEL
        url = config.nvidia_base_url or "https://integrate.api.nvidia.com/v1"
        return _call_openai_compat(
            messages, model, config.nvidia_api_key, url, "Nvidia", timeout,
            fallback_key=config.nvidia_api_key_fallback or "",
        )
    if name == "gemini":
        if not config.gemini_api_key:
            raise RuntimeError("Gemini: no API key")
        if provider_blocked("gemini"):
            raise RuntimeError("Gemini: free quota unavailable")
        model = config.gemini_model or "gemini-2.0-flash"
        return _call_gemini(
            messages, model, config.gemini_api_key, config.gemini_base_url or "", timeout,
            fallback_key="",
        )
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
    timeout: int = 40,
    capability: str = "text",
    max_tokens: int = 0,
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
    from core.cost_control import provider_blocked

    # ── 1. Primary: Groq API ───────────────────────────────────────────────
    groq_key = config.groq_api_key
    if groq_key and not provider_blocked("groq"):
        try:
            groq_model = get_latest_groq_model()
            if groq_model:
                response = _call_groq(messages, groq_model, timeout=timeout, max_completion_tokens=max_tokens)
                return response, {"groq_used": True, "fallback_used": False, "fallback_source": ""}
            print("[LLM CLUSTER] Groq skipped: no model resolved")
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] Groq failed: {_short_error_detail(str(exc))}. Rotating to OpenRouter.")
    else:
        print("[LLM CLUSTER] Groq unavailable: no API key")

    # ── 2. Fallback 1: OpenRouter ────────────────────────────────────────────
    openrouter_key = config.openrouter_api_key
    openrouter_model = config.openrouter_model or "meta-llama/llama-3-8b-instruct:free"
    if openrouter_key and not provider_blocked("openrouter", openrouter_model):
        openrouter_url = config.openrouter_base_url or "https://openrouter.ai/api/v1"
        try:
            response = _call_openai_compat(
                messages, openrouter_model, openrouter_key,
                openrouter_url, "OpenRouter", timeout, max_tokens=max_tokens,
            )
            return response, {"groq_used": False, "fallback_used": True, "fallback_source": "OpenRouter"}
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] OpenRouter failed: {_short_error_detail(str(exc))}. Rotating to Nvidia.")
    else:
        print("[LLM CLUSTER] OpenRouter unavailable: no API key")

    # ── 3. Fallback 2: NVIDIA NIM ─────────────────────────────────────────────
    nvidia_key = config.nvidia_api_key
    if nvidia_key and not provider_blocked("nvidia"):
        nvidia_model = config.nvidia_model or NVIDIA_DEFAULT_MODEL
        nvidia_url = config.nvidia_base_url or "https://integrate.api.nvidia.com/v1"
        try:
            response = _call_openai_compat(
                messages, nvidia_model, nvidia_key,
                nvidia_url, "Nvidia", timeout, max_tokens=max_tokens,
            )
            return response, {"groq_used": False, "fallback_used": True, "fallback_source": "Nvidia"}
        except Exception as exc:
            last_error = exc
            print(f"[LLM CLUSTER] Nvidia failed: {_short_error_detail(str(exc))}. Rotating to Gemini.")
    else:
        print("[LLM CLUSTER] Nvidia unavailable: no API key")

    # ── 4. Fallback 3: Gemini ─────────────────────────────────────────────────
    gemini_key = config.gemini_api_key
    if gemini_key and not provider_blocked("gemini"):
        gemini_model = config.gemini_model or "gemini-2.0-flash"
        gemini_url = config.gemini_base_url or ""
        try:
            response = _call_gemini(
                messages, gemini_model, gemini_key,
                gemini_url, timeout, max_tokens=max_tokens,
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

    # Explicit [ANIM: ...] / [EMO: ...] tags are control data for the companion
    # body + face channels. Never leak them into chat text or TTS.
    supplied = parsed.get("animation") if isinstance(parsed.get("animation"), dict) else {}
    anim_tag = _ANIM_TAG_RE.search(reply)
    emo_tag = _EMO_TAG_RE.search(reply)
    animation = resolve_animation_state(
        str(supplied.get("state") or (anim_tag.group(1) if anim_tag else "")),
        str(supplied.get("emotion") or (emo_tag.group(1) if emo_tag else "")),
    ) if (supplied or anim_tag or emo_tag) else infer_animation_state("", reply)
    reply = _sanitize_reply_for_chat(strip_animation_tags(reply), FALLBACK_RESPONSE)
    return {
        "reply": reply,
        "intent": intent,
        "entity": entity,
        "value": value,
        "animation": animation,
    }


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
    return _privacy_guard(cleaned)


# ── Founder privacy + canonical origin answers ──────────────────────────────
_PHONE_NUMBER_RE = re.compile(
    r"(?<![\w])(?:\+\d{1,3}[\s.\-]*)?(?:\(\d{2,4}\)[\s.\-]*)?"
    r"\d{3,4}[\s.\-]*\d{3,4}(?:[\s.\-]*\d{3,4})?(?![\w])"
)
_THIRD_PARTY_PROVIDER_RE = re.compile(
    r"\b(?:Groq|NVIDIA|Gemini|OpenRouter|Alibaba)(?:Video|Studio|Cloud|AI)?\b",
    re.IGNORECASE,
)
_IDENTITY_QUERY_RE = re.compile(
    r"\b(?:who\s+(?:founded|created|built|made|is\s+(?:your\s+)?(?:creator|founder))|"
    r"when\s+(?:was|did)\s+valley\s*mind|"
    r"how\s+(?:did|was)\s+valley\s*mind|"
    r"valley\s*mind(?:'s)?\s+(?:history|story|origin|founder|founding|about|mission|"
    r"ecosystem|contact|website|tagline)|"
    r"about\s+valley\s*mind|what\s+is\s+valley\s*mind|"
    r"who\s+is\s+elena|elena\s+(?:persona|history|origin)|"
    r"(?:phone|contact|mobile|whatsapp)\s+number|call\s+(?:the\s+)?founder|"
    r"founder.{0,30}(?:phone|number|contact))\b",
    re.IGNORECASE,
)


def _privacy_guard(text: str) -> str:
    """Remove private founder data and provider names from user-facing text."""
    cleaned = _PHONE_NUMBER_RE.sub("[phone number withheld]", str(text or ""))
    cleaned = _THIRD_PARTY_PROVIDER_RE.sub("", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


class _PrivacyStreamFilter:
    """Stream-safe wrapper for :func:`_privacy_guard`.

    A short hold-back window lets phone numbers and provider names arrive in
    several SSE chunks before they are scrubbed, while still streaming normal
    prose with only a small delay.
    """

    _HOLD = 32

    def __init__(self):
        self._pending = ""
        self._emitted = 0
        self._clean = ""

    def feed(self, chunk: str) -> str:
        self._pending += str(chunk or "")
        safe = self._pending[:-self._HOLD] if len(self._pending) > self._HOLD else ""
        self._clean = _privacy_guard(safe)
        fresh = self._clean[self._emitted:]
        self._emitted = len(self._clean)
        return fresh

    def flush(self) -> str:
        self._clean = _privacy_guard(self._pending)
        fresh = self._clean[self._emitted:]
        self._emitted = len(self._clean)
        self._pending = ""
        return fresh

    def sanitized(self) -> str:
        return self._clean if self._clean else _privacy_guard(self._pending)


def is_valleymind_identity_query(message: str) -> bool:
    """True for brand/history/founder/contact questions that need canonical copy."""
    return bool(_IDENTITY_QUERY_RE.search(str(message or "").strip()))


def valleymind_identity_reply() -> str:
    """Return the canonical, privacy-safe public answer about ValleyMind AI."""
    return _privacy_guard(VALLEYMIND_PUBLIC_IDENTITY)


# ── Consultative asset creation (ask before generating) ─────────────────────
ASK_FIRST_MARKER = "Quick questions before I create"
_PENDING_ASSET_LOCK = threading.Lock()
_pending_asset_requests: dict[str, dict] = {}

_SKIP_QUESTIONS_RE = re.compile(
    r"\b(?:create|make|generate|build|do|go|proceed|start)\s+it\s+now\b|"
    r"\bskip\s+(?:the\s+)?questions?\b|\bno\s+questions?\b|"
    r"\bwithout\s+questions?\b|\bjust\s+(?:create|make|generate|do)\s+it\b",
    re.IGNORECASE,
)
_CONFIRMATION_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|sure|ok|okay|go\s+ahead|proceed|please\s+do|"
    r"that\s+works|looks\s+good|create\s+it|make\s+it|generate\s+it)\b",
    re.IGNORECASE,
)
_ASSET_DETAIL_RE = re.compile(
    r"\b(?:style|mood|tone|palette|colour|color|aspect\s+ratio|ratio|resolution|"
    r"duration|length|pages?|sections?|audience|purpose|include|including|compare|"
    r"comparison|table|tree|hierarchy|hierarchical|outline|chapters?|columns?|"
    r"rows?|bullets?|example|data|version|landscape|portrait|cinematic|minimal|"
    r"watercolor|illustration|photorealistic|16:9|9:16|4k|hdr)\b",
    re.IGNORECASE,
)
_ASSET_LABELS = {
    "pdf": "PDF", "docx": "Word document", "xlsx": "Excel workbook",
    "csv": "CSV", "txt": "text file", "md": "Markdown file", "zip": "ZIP bundle",
    "file": "document", "image": "image", "video": "video",
}


def asset_request_key(user_id: str, chat_id: str) -> str:
    """Stable key for a pending consultative asset request."""
    return f"{str(user_id or 'anon').strip()}|{str(chat_id or 'main').strip()}"


def remember_pending_asset_request(key: str, kind: str, message: str) -> None:
    with _PENDING_ASSET_LOCK:
        _pending_asset_requests[str(key)] = {
            "kind": str(kind or "asset"),
            "message": str(message or "").strip(),
        }


def get_pending_asset_request(key: str) -> dict | None:
    with _PENDING_ASSET_LOCK:
        pending = _pending_asset_requests.get(str(key))
        return dict(pending) if pending else None


def pop_pending_asset_request(key: str) -> dict | None:
    with _PENDING_ASSET_LOCK:
        return _pending_asset_requests.pop(str(key), None)


def merge_pending_asset_request(pending: dict | None, message: str) -> str:
    """Combine the original brief with the user's answers before generating."""
    original = str((pending or {}).get("message") or "").strip()
    detail = str(message or "").strip()
    if not original:
        return detail
    if not detail:
        return original
    if original.lower() in detail.lower():
        return detail
    return f"{original}\nAdditional direction from the user: {detail}"


def _had_prior_asset_clarification(history: list | None) -> bool:
    for msg in reversed(list(history or [])[-8:]):
        role = str(msg.get("role", "")).lower()
        if role == "user":
            continue
        if role == "assistant" and ASK_FIRST_MARKER in str(msg.get("content", "")):
            return True
    return False


def needs_asset_clarification(message: str, kind: str, history: list | None = None) -> bool:
    """Guard under-specified file/image/video briefs before generation.

    Detailed briefs, explicit "create it now" instructions and replies to an
    earlier clarification go straight to generation.
    """
    text = str(message or "").strip()
    if not text or ASK_FIRST_MARKER in text:
        return False
    if _SKIP_QUESTIONS_RE.search(text) or _CONFIRMATION_RE.search(text):
        return False
    if _had_prior_asset_clarification(history):
        return False
    if len(text.split()) >= 18:
        return False
    if _ASSET_DETAIL_RE.search(text):
        return False
    if re.search(r"[:：]\s*\S", text) and len(text) > 35:
        return False
    return bool(str(kind or "").strip())


def asset_clarification_message(message: str, kind: str, fmt: str = "") -> str:
    """Short consultative reply with a plan, suggestions and 2-3 questions."""
    key = str(fmt or kind or "asset").lower()
    label = _ASSET_LABELS.get(key, _ASSET_LABELS.get(str(kind or "").lower(), str(kind or "asset")))
    plans = {
        "file": (
            f"I'll draft a complete {label} with an overview, clear sections, "
            "specific bullets and a real data table before I build the download."
        ),
        "image": "I'll turn your idea into a focused visual prompt, generate the image, then save it to your gallery.",
        "video": "I'll shape your idea into a short shot list, generate the clip, then save it to your gallery.",
    }
    suggestions = {
        "file": (
            "Suggestions: I can add a comparison table, or a tree/hierarchy "
            "breakdown if the subject has levels."
        ),
        "image": "Suggestions: I can lean into a specific style or mood, and lock the aspect ratio (16:9, 1:1 or 9:16).",
        "video": "Suggestions: I can set the mood and pacing, and choose a 16:9 landscape or 9:16 vertical format.",
    }
    questions = {
        "file": (
            "1. Who is the document for?\n"
            "2. Which sections or tables should it definitely include?\n"
            "3. Do you want a concise brief or a detailed report?"
        ),
        "image": (
            "1. What style or mood should it have?\n"
            "2. Which aspect ratio do you want?\n"
            "3. Should it include any text, or be purely visual?"
        ),
        "video": (
            "1. What mood and pacing should the clip have?\n"
            "2. Should it be 16:9 landscape or 9:16 vertical?\n"
            "3. How long should it feel — a quick loop or a longer scene?"
        ),
    }
    bucket = "file" if str(kind).lower() == "file" else str(kind).lower()
    if bucket not in plans:
        bucket = "file"
    parts = [
        f"{ASK_FIRST_MARKER} your {label}.",
        plans[bucket],
        suggestions[bucket],
        questions[bucket],
        "Reply with your choices and I'll create it right away, or say \"create it now\" to use my suggested defaults.",
    ]
    return _privacy_guard("\n\n".join(parts))


def asset_generation_copy(kind: str, message: str = "", fmt: str = "") -> tuple[str, str]:
    """Before/after conversational copy for generated files, images and videos."""
    key = str(fmt or kind or "asset").lower()
    label = _ASSET_LABELS.get(key, _ASSET_LABELS.get(str(kind or "").lower(), str(kind or "asset")))
    if str(kind).lower() == "file":
        intro = (
            f"Done — I created your {label}. I structured it with a clear "
            "overview, detailed sections, useful bullets and a real data table "
            "so it's ready to use, not just the request text."
        )
    elif str(kind).lower() == "image":
        intro = "Here's the image I created from your direction — I kept the subject, style and composition focused on your brief."
    else:
        intro = "Here's the video I created from your direction — I shaped the scene and pacing around your brief."
    followups = (
        "Want me to change a section, add a comparison table or a tree breakdown, "
        "or export it in another format? Would a matching visual or a shorter "
        "version for a different audience help?"
    )
    return _privacy_guard(intro), _privacy_guard(followups)


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
        self.last_animation: dict = resolve_animation_state("idle")

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
        _startup_health_check()
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

    def _groq_messages(self, chat_id: str, user_message: str, image_data: str = "", live_context: str = "", expanded_query: str = "", mongo_history: list = None, global_memories: str = "", knowledge_data: str = "", recovered_context: str = "", search_note: str = "") -> list:
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

        # ── User Preferences / User Profile Context ─────────────────
        # Every user's explicit preferences are injected here — USER
        # LEVEL, not character level.  The same data feeds Marcus,
        # Angelina, and Elena.  Preferences shape HOW the character
        # responds; character personality stays independent.
        try:
            pref_context = self._build_user_preferences_context()
        except Exception as exc:
            print(f"[BRAIN] user preferences context failed: {exc}")
            pref_context = ""
        if pref_context:
            sections.append("=== USER PREFERENCES / USER PROFILE CONTEXT ===")
            sections.append(
                "The following are the CURRENT USER's own preferences and profile. "
                "Adapt your communication, tone, language and behaviour to match. "
                "These override default personality tendencies wherever they conflict."
            )
            sections.append(pref_context)
            sections.append("")

        # ── Response language + cultural intelligence (independent concepts) ──
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

        # Semantic selection layer — single source of truth for culture,
        # language and adages.  It applies this order of precedence:
        #   * explicit per-message culture/language request > saved settings
        #   * saved user culture > founder identity (never imposed on users)
        #   * unknown culture  => culturally neutral, no ethnicity assumed
        # It only surfaces an adage when one is semantically relevant AND
        # available in the dataset — it never fabricates or forces one.
        try:
            cultural = select_cultural_context(
                culture_identity=culture_identity,
                response_language=resolve_language(response_lang) or resolve_language(reply_language) or "",
                message=user_message,
                adages_enabled=use_adages,
                limit=3,
            )
        except Exception as exc:
            print(f"[CULTURE] cultural selection failed: {exc}")
            cultural = {}

        response_code = cultural.get("language") or "en"
        retrieved_records = []
        if cultural.get("relevant") and cultural.get("record"):
            retrieved_records.append(cultural["record"])

        grounding = build_cultural_grounding_block(
            response_language=response_code,
            cultural_identity=cultural.get("culture") or culture_identity,
            use_adages=use_adages,
            retrieved=retrieved_records,
            message=user_message,
        )
        if grounding:
            sections.append("=== Response Language & Cultural Grounding ===")
            sections.append(grounding)
            sections.append("")

        # ValleyMind's internal grounding — an interpretation layer telling the
        # model that its Igbo/Nigerian cultural inheritance is a lens it may use,
        # never something to impose on a user whose own culture is known or not.
        sections.append(valleymind_cultural_foundation_block())
        sections.append("")

        # Per-message explicit requests — authoritative for THIS response only;
        # they never write back to the user's saved preferences.
        if cultural.get("explicit_language_requested") or cultural.get("explicit_culture"):
            directives = cultural_request_directives(cultural)
            if directives:
                sections.append("=== Explicit per-message requests (this response only) ===")
                sections.append(directives)
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

        # ── Search-grounded citations + structured entity cards ─────────
        if live_context and str(live_context).strip() and str(live_context).strip() != str(LIVE_DATA_UNAVAILABLE):
            sections.append(
                "Citation format: when a claim comes from the live search context, cite it inline "
                "with a clean domain pill such as [domain.com] or 【SourceName】 (the app maps these "
                "to source_metadata URLs). Place each citation immediately after the exact sentence "
                "or entity explanation it supports — e.g. '...adopted in 2025 [sirumors].' Never "
                "collect citations on their own lines after a list or at the end of the message, "
                "never repeat the same citation twice in a row, and never paste raw URLs into prose. "
                "Depth and flow: give each finding or item 2-3 informative, specific sentences "
                "grounded in the live results — include concrete names, figures, dates, standout "
                "details, current context and why it matters right now rather than a generic one-line "
                "blurb. Then close every search-grounded reply with one brief, natural follow-up "
                "insight or an engaging topic-specific question that invites the user to go deeper "
                "(e.g. which item to compare, analyze tactically, or check current schedules/pricing "
                "for). Keep the follow-up short, warm and relevant to what you just said."
            )
        if _needs_forced_live_search(user_message):
            sections.append(
                "DIRECT ANSWER MODE: This is a current/factual question. Use the live "
                "results above to answer the user's ACTUAL question first, with real "
                "movie/show/news/product titles, dates and legal watch/download sources "
                "when the user asks where to find them. Never reply 'I don't have a "
                "confirmed list', 'I don't know', or a generic refusal before using the "
                "search context. If a detail is uncertain, say so briefly AFTER giving "
                "the concrete answer, not instead of it."
            )
        presentation = _presentation_mode(user_message)
        if presentation == "matrix":
            sections.append(
                "Presentation choice: this is a multi-attribute matrix comparison. Use a real markdown "
                "pipe table with one attribute per row or column. Do not convert it into entity cards."
            )
        elif presentation == "entities":
            sections.append(
                "Presentation choice: this is an entity list/ranking. Put each named item on its own line using:\n"
                "[[ENTITY: Name | Maker/Subtitle | official-entity-domain.com | https://official-entity-domain.com | "
                "LogoURL-or-blank | 2-3 informative sentence explanation [source]]]\n"
                "Fields 3 and 4 MUST be the ENTITY'S OWN official website/domain (e.g. realmadrid.com, "
                "liverpoolfc.com, openai.com, anthropic.com) — NEVER the URL/domain of a news article, "
                "blog, or review site that wrote about the entity. News/reporting sources belong only as "
                "inline citation pills inside the explanation text, e.g. 'Won 15 European titles [bbc]'. "
                "The final explanation slot is MANDATORY for every card: write a concrete, specific "
                "2-3 sentence description there — a standout detail, the current context/form or why "
                "it matters right now, and a key strength — even when the logo is blank; never a dry "
                "one-line blurb. Never move that explanation into the logo slot and never leave it empty. "
                "The app renders these as 52px logo-left, clickable-title-right cards using the entity's "
                "official domain. Use the exact official domain, target URL, and Logo/Icon URL from the "
                "provided source context when available; leave the logo field blank instead of inventing "
                "one. Put each citation inline immediately after the sentence it supports, never as "
                "trailing lines below the list. After the entity list, close with one brief, natural "
                "follow-up insight or engaging topic-specific question so the conversation keeps flowing "
                "(e.g. which club/model/option to compare deeper, analyze, or check current details for). "
                "Do not wrap the syntax in markdown or code fences, and do not use a table for this list."
            )
        elif presentation == "combined":
            sections.append(
                "Presentation choice: this asks to list AND compare. First put each named item on its own line using:\n"
                "[[ENTITY: Name | Maker/Subtitle | official-entity-domain.com | https://official-entity-domain.com | "
                "LogoURL-or-blank | 2-3 informative sentence explanation [source]]]\n"
                "Fields 3 and 4 MUST be the ENTITY'S OWN official website/domain — NEVER a news article "
                "or blog URL. Cite news/review sources inline in the explanation as [source]. "
                "The final explanation slot is MANDATORY for every card; keep explanations concrete and "
                "specific (2-3 sentences covering a standout detail, current context and a key strength) "
                "with inline citations at the end of the supporting sentence. Then add one compact markdown "
                "comparison table for the requested attributes. Use exact official domains, target URLs, "
                "and Logo/Icon URLs from the provided source context when available; leave the logo field "
                "blank instead of inventing one. Finish with a brief, natural follow-up insight or "
                "engaging question tailored to the topic."
            )
        if presentation in ("entities", "combined"):
            sections.append(
                "Card syntax alias: [[CARD: Name | Subtitle | Domain | URL | Logo-or-blank | "
                "Description]] renders identically to [[ENTITY: ...]]. Either form is valid."
            )

        system_with_context = "\n".join(sections)
        messages = [{"role": "system", "content": system_with_context}]

        if live_context and str(live_context).strip() and str(live_context).strip() != str(LIVE_DATA_UNAVAILABLE):
            # ── Token budget: trim live_context to stay under ~8K TPM ──
            # Groq free tier ≈ 8000 TPM input. Reserve ~3K for system prompt
            # and history, ~2K for reply, leaving ~3K for live context.
            _MAX_LIVE_CTX = 3000  # ≈ 750 tokens
            if len(live_context) > _MAX_LIVE_CTX:
                live_context = live_context[:_MAX_LIVE_CTX] + "\n...[truncated for token budget]..."
                print(f"[TOKEN BUDGET] live_context trimmed to {_MAX_LIVE_CTX} chars")
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
        elif search_note:
            context_header = (
                f"[SYSTEM DIRECTIVE: LIVE SEARCH UNAVAILABLE]\n"
                f"{search_note}\n"
                f"Answer from general knowledge. Be helpful and never refuse. "
                f"Do not claim you found live search results.\n\n"
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

    def _build_user_preferences_context(self) -> str:
        """Build a human-readable block of the current user's explicit
        preferences and profile data, read from the brain's long-term memory
        (which mirrors settings.json via _mirror_settings_to_memory).
        Returns empty string if no preferences are stored.

        This is USER-LEVEL data — the same preferences feed all three
        characters.  Character personality stays separate from preferences."""
        try:
            long_term = self.memory.get_full_memory() or {}
        except Exception:
            return ""

        prefs = long_term.get("preferences") or {}
        if not isinstance(prefs, dict):
            prefs = {}

        # The mirror writes preference fields from the "preferences" section
        # under a "preferences_" prefix (e.g. preferences_use_cases). Read both
        # the raw key and the prefixed form so the block stays robust.
        def P(key):
            v = prefs.get(key)
            if v is None:
                v = prefs.get(f"preferences_{key}")
            return v

        lines = []

        # About me (personal info the user explicitly wants remembered)
        about_me = P("about_me")
        if about_me:
            lines.append(f"About the user: {str(about_me)[:500]}")

        # Communication style
        style = P("communication_style")
        if style:
            if isinstance(style, list):
                style = ", ".join(str(s) for s in style)
            lines.append(f"Communication style: {style}")

        note = P("communication_note")
        if note:
            lines.append(f"Communication note: {str(note)[:300]}")

        # Use cases
        use_cases = P("use_cases")
        if use_cases:
            if isinstance(use_cases, list):
                use_cases = ", ".join(str(u) for u in use_cases)
            lines.append(f"Primary use cases: {use_cases}")

        use_other = P("use_cases_other")
        if use_other:
            lines.append(f"Use case detail: {str(use_other)[:300]}")

        # Use-case profile (richer context than the checkbox list)
        ucp = P("use_case_profile")
        if ucp:
            lines.append(f"Use-case profile: {str(ucp)[:400]}")

        # Expressive language
        expressive = P("expressive_language")
        if expressive:
            if isinstance(expressive, list):
                expressive = ", ".join(str(e) for e in expressive)
            lines.append(f"Preferred expressions: {expressive}")

        expressive_other = P("expressive_language_other")
        if expressive_other:
            lines.append(f"Other expressions: {str(expressive_other)[:200]}")

        # Multilingual behavior
        ml = P("multilingual_behavior")
        if ml:
            lines.append(f"Multilingual behavior: {ml}")

        # Voice style
        voice = P("voice_style")
        if voice:
            lines.append(f"Voice preference: {voice}")

        # Custom preference
        custom = P("custom_preference")
        if custom:
            lines.append(f"Custom preference: {str(custom)[:400]}")

        # Preferred characters
        chars = P("preferred_characters")
        if chars:
            if isinstance(chars, list):
                chars = ", ".join(str(c) for c in chars)
            lines.append(f"Preferred AI characters: {chars}")

        # Response language (stored at the top level of long_term)
        response_lang = long_term.get("response_language")
        if response_lang:
            lines.append(f"Response language: {response_lang}")

        reply_lang = long_term.get("reply_language")
        if reply_lang and reply_lang != response_lang:
            lines.append(f"Reply language: {reply_lang}")

        # Cultural identity / expression (stored at the top level)
        culture = long_term.get("culture_identity")
        if culture:
            lines.append(f"Cultural identity: {culture}")

        cultural_expr = long_term.get("cultural_expression")
        if cultural_expr:
            lines.append(f"Cultural expression level: {cultural_expr}")

        # Language background mirrored from the "language" section
        for bg_key, bg_label in (
            ("language_country", "Country"),
            ("language_state_province", "Region"),
            ("language_native_languages", "Native languages"),
            ("language_cultural_background", "Cultural background"),
        ):
            val = prefs.get(bg_key)
            if val:
                lines.append(f"{bg_label}: {val}")

        if not lines:
            return ""
        return "\n".join(lines)

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

    def _contextual_search_route(self, chat_id: str, message: str, route=None) -> dict:
        """Resolve a route dict with contextual follow-up research instructions.

        ``route`` may be a ``RouteDecision`` from :mod:`core.router` (which exposes
        ``get``) or a plain dict. When absent, the last 6 memory turns are used
        so indirect references ("who directed it?", "compare them") become a
        standalone search query before the assistant answers.
        """
        if route is not None:
            try:
                needs = bool(route.get("needs_search"))
                query = str(route.get("search_query") or "").strip()
                if needs or query:
                    return {
                        "needs_search": needs or bool(query),
                        "search_query": query or str(message or "").strip(),
                        "reason": str(route.get("reasoning") or route.get("reason") or "Router search directive"),
                    }
            except Exception:
                pass

        try:
            history = self.memory.get_chat(chat_id) or []
        except Exception:
            history = []
        try:
            result = _router_contextual_research(message, history)
        except Exception as exc:
            print(f"[CONTEXT ROUTE] contextual research check failed: {exc}")
            result = {}
        if not isinstance(result, dict) or not result.get("needs_search"):
            return {"needs_search": False, "search_query": "", "reason": ""}
        query = str(result.get("search_query") or "").strip()
        if not query:
            return {"needs_search": False, "search_query": "", "reason": ""}
        print(f"[CONTEXT ROUTE] enriched search query: {query[:160]}")
        return {
            "needs_search": True,
            "search_query": query,
            "reason": str(result.get("reason") or "Contextual entity follow-up"),
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

    def _envelope(self, reply: str, meta: dict, intent: str = "general", entity: str = "", value: str = "", animation: dict = None) -> dict:
        anim = animation or resolve_animation_state("idle")
        self.last_animation = anim
        return {
            "reply": _sanitize_reply_for_chat(reply, ""),
            "intent": intent,
            "entity": entity,
            "value": value,
            "animation": anim,
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

    def _generate_structured_file_content(
        self,
        cid: str,
        message: str,
        fmt: str,
        title: str,
    ) -> tuple[str, dict]:
        """Draft a complete document before any file bytes are written.

        File requests used to echo the user's instruction straight into the
        file. This step separates the subject from the wrapper, optionally
        grounds current/comparison subjects in live search evidence, and asks
        the LLM cluster for a full Markdown payload with sections, bullets and
        a data table. If every provider fails, a structured built-in draft is
        used instead of the prompt itself.
        """
        try:
            history = self.memory.get_chat(cid) or []
        except Exception:
            history = []
        topic = _file_topic_from_message(message, fmt, history)
        context = _file_context_text(history, message)
        live_context, sources = _file_live_context(topic)
        grounded = bool(live_context)

        system_prompt = (
            "You are the document drafting engine for ValleyMind-AI. Write the "
            "COMPLETE body of a ready-to-use document, not a chat reply. Return "
            "only Markdown: no preamble, no closing note, no code fences, no JSON, "
            "no wire tags and no animation tags. Never repeat, quote, or merely "
            "rephrase the user's instruction; write the actual document it asks for. "
            "Structure the document with an opening overview paragraph and at least "
            "four clear '## ' section headings. Under each heading write detailed "
            "explanatory paragraphs and a bullet list with specific, concrete points. "
            "Include a '## Key takeaways' section and one Markdown table written with "
            "pipes (| col1 | col2 |) using a header row, a |---|---| separator and at "
            "least six substantive data rows. For XLSX and CSV the table must be a "
            "well-formed dataset with consistent columns. Where the subject has levels "
            "or dependencies, include a visual tree/hierarchy block that uses the "
            "characters ├──, └── and │. Use **bold** for key terms and *italic* for "
            "emphasis, but never leave raw asterisks in finished prose. Finish every "
            "section and the document with complete concluding sentences. Do not invent "
            "precise current figures: when live context is supplied, use only those "
            "facts; otherwise use sound general knowledge and label assumptions "
            "clearly. Aim for 900-1400 words of useful content."
            "\n\n" + VALLEYMIND_IDENTITY_AND_HISTORY
        )
        parts = [
            f"Document title: {title}",
            f"Requested format: {str(fmt or 'txt').upper()}",
            f"Subject to cover: {topic}",
        ]
        if context:
            parts += ["", "Relevant conversation context (use it to tailor the draft):", context]
        if live_context:
            parts += [
                "",
                "Verified live research context (use these facts where relevant):",
                live_context[:6000],
            ]
        parts += ["", "Write the complete document now."]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(parts)},
        ]

        try:
            try:
                raw, meta = _call_llm_cluster(
                    messages, timeout=45, max_tokens=VM_FILE_DRAFT_MAX_TOKENS
                )
            except TypeError as token_error:
                # Older test doubles may not accept the long-form token budget.
                if "max_tokens" not in str(token_error):
                    raise
                raw, meta = _call_llm_cluster(messages, timeout=45)
            content = _privacy_guard(_extract_llm_document(raw))
            if not _is_substantive_file_content(content, message):
                raise RuntimeError("LLM returned a thin draft or prompt echo")
            return content, {
                "grounded": grounded,
                "provider": _served_by(meta or {}),
                "sources": sources,
            }
        except Exception as exc:
            print(
                "[FILE GEN] Structured drafting unavailable ("
                + _short_error_detail(str(exc))
                + "); using built-in structured draft"
            )
            content = _privacy_guard(_fallback_file_content(topic, fmt, title, context, live_context))
            return content, {
                "grounded": grounded,
                "provider": "local-fallback",
                "sources": sources,
            }

    def _file_generation_reply(self, cid: str, message: str, timestamp: str, file_request: dict) -> str:
        """Draft structured content, generate a real file and return a FILE_CARD."""
        fmt = str(file_request.get("format") or "txt")
        title = str(file_request.get("title") or f"ValleyMind {fmt.upper()} Export")
        content, content_meta = self._generate_structured_file_content(cid, message, fmt, title)
        try:
            meta = _generate_real_file(content, fmt, title=title)
            card = _make_file_card(meta, title)
        except Exception as exc:
            print(f"[FILE GEN] Brain generation failed: {exc}")
            return "I couldn't create that file right now. Please try again."
        intro, followups = asset_generation_copy("file", message, fmt)
        reply = _privacy_guard(f"{intro}\n\n{card}\n\n{followups}")
        self.last_animation = resolve_animation_state("success")
        state = self._metadata(False, True, "local")
        state["detected_route"] = "file_generation"
        state["file_content_provider"] = content_meta.get("provider", "")
        state["file_content_grounded"] = bool(content_meta.get("grounded"))
        if content_meta.get("sources"):
            state["sources"] = list(content_meta["sources"])
        self.last_response_meta = state
        try:
            self.memory.add_message(cid, "assistant", reply, timestamp)
        except Exception as exc:
            print(f"[ERROR] Memory add_message (file card) failed: {exc}")
        try:
            self.memory.save_memory()
        except Exception as exc:
            print(f"[ERROR] Memory save after file card failed: {exc}")
        return reply

    def _record_direct_reply(self, cid: str, reply: str, timestamp: str, route: str, animation_state: str = "explain") -> str:
        """Persist a deterministic reply that bypasses the LLM cluster."""
        self.last_animation = resolve_animation_state(animation_state)
        state = self._metadata(False, True, "local")
        state["detected_route"] = route
        self.last_response_meta = state
        try:
            self.memory.add_message(cid, "assistant", reply, timestamp)
        except Exception as exc:
            print(f"[ERROR] Memory add_message ({route}) failed: {exc}")
        try:
            self.memory.save_memory()
        except Exception as exc:
            print(f"[ERROR] Memory save after {route} failed: {exc}")
        return reply

    def _identity_reply(self, cid: str, timestamp: str) -> str:
        return self._record_direct_reply(
            cid, valleymind_identity_reply(), timestamp, "valleymind_identity", "explain"
        )

    def _asset_clarification_reply(self, cid: str, message: str, timestamp: str, kind: str, fmt: str = "") -> str:
        reply = asset_clarification_message(message, kind, fmt)
        remember_pending_asset_request(
            asset_request_key(getattr(self.memory, "user_id", ""), cid),
            kind,
            message,
        )
        return self._record_direct_reply(cid, reply, timestamp, "asset_clarification", "ask")

    def respond(self, message: str, chat_id: str = "", image_data: str = "", mongo_history: list = None, persist_image_data: bool = True, route=None) -> str:
        try:
            self.memory.reload()
            # Initialize activity event tracking for non-streaming responses.
            self.activity_events: list = []

            def _emit_activity(category: str, key: str, label: Optional[str] = None, detail: str = ""):
                """Record an activity event (non-streaming equivalent of yield)."""
                sess = get_current_session()
                if sess:
                    handle = sess.start(
                        operation=key,
                        label=label,
                        detail=detail,
                    )
                    handle.complete()
                    self.activity_events.append(sess.get_history()[-1])

            message = (message or "").strip()
            if not message and not image_data:
                return "I didn't catch that - could you say something?"
            if not message:
                message = "(image attached)"

            cid = (chat_id or "").strip() or f"{self.profile.key}_main_chat"
            timestamp = datetime.now().isoformat()

            msg_count_before = self.memory.get_message_count(cid)

            user_msg = message
            stored_image_data = ""
            if image_data and persist_image_data:
                user_msg = message + "\n[Image attached]"
                stored_image_data = image_data

            try:
                self.memory.add_message(cid, "user", user_msg, timestamp, image_data=stored_image_data)
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

            # ── Shared activity indicator: begin processing ─────────────
            _emit_activity("general", "processing", detail="Starting request")

            # File requests go through a dedicated drafting step first: live
            # grounding when needed, otherwise the LLM plus chat context. The
            # generated document is then written to a real downloadable file.
            pending_key = asset_request_key(getattr(self.memory, "user_id", ""), cid)
            pending = get_pending_asset_request(pending_key)
            file_request = _detect_file_request(message)
            used_pending = False
            if pending and str(pending.get("kind")) == "file":
                if file_request:
                    pop_pending_asset_request(pending_key)
                else:
                    message = merge_pending_asset_request(pending, message)
                    file_request = _detect_file_request(message) or file_request
                    used_pending = True
            if file_request:
                if used_pending:
                    pop_pending_asset_request(pending_key)
                elif needs_asset_clarification(message, "file", self.memory.get_chat(cid)):
                    return self._asset_clarification_reply(
                        cid, message, timestamp, "file", file_request.get("format", "")
                    )
                _emit_activity("general", "preparing_response", detail=f"Drafting {file_request['format'].upper()} content")
                return self._file_generation_reply(cid, message, timestamp, file_request)

            if is_valleymind_identity_query(message):
                return self._identity_reply(cid, timestamp)

            live_ctx = ""
            search_note = ""
            self._pending_sources = []
            self._pending_source_metadata = []
            directed_site, directed_query = parse_directed_search(message)
            research_info = classify_research_intent(message, recent_context=self._recent_context_for_routing(cid))
            intent = research_info["intent"]
            research_domain = research_info.get("domain", "general")
            forced_live_search = _needs_forced_live_search(message)
            if forced_live_search:
                # Never let a chat/none classification skip a direct 2026/news/list
                # question. This forces the normal web_search -> research pipeline.
                intent = "search"
                if research_domain in ("", "general"):
                    research_domain = (
                        "media" if _FORCED_SEARCH_SUBJECT_RE.search(message) else "news"
                    )
                print(f"[FORCED SEARCH] Direct factual question — searching web: {message[:120]}")
            contextual_route = self._contextual_search_route(cid, message, route=route)
            if directed_site:
                intent = "search"
                research_domain = "general"
            elif contextual_route.get("needs_search") or contextual_route.get("search_query"):
                # An indirect follow-up must search with the resolved topic, not
                # with the bare pronoun-only user message.
                intent = "search"
                if research_domain == "general" and _wants_entity_cards(message):
                    research_domain = "tech"
            if intent == "none":
                print(f"[FAST-PATH] LLM classified intent '{intent}' — conversational, no search")
            else:
                print(f"[LIVE PATH] intent '{intent}', domain='{research_domain}' — researching"
                      + (f" (directed: {directed_site})" if directed_site else "")
                      + (f" (contextual: {contextual_route.get('search_query', '')[:80]})" if contextual_route.get("needs_search") else ""))
                search_message = directed_query if directed_site else message
                if not directed_site and contextual_route.get("needs_search") and contextual_route.get("search_query"):
                    search_message = contextual_route["search_query"]
                elif not directed_site and _is_continuation_utterance(message) and msg_count_before > 0:
                    expanded = self._expand_continuation_query(cid, message)
                    if expanded and expanded != message:
                        search_message = expanded

                _emit_activity("web_research", "searching_web", detail=f"domain={research_domain}")

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
                        self._pending_source_metadata = _filter_query_images(
                            evidence_package.to_frontend_metadata(), search_message
                        )
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
                                    "icon": ev.source_icon,
                                    "logo": ev.logo or ev.source_icon,
                                    "image": ev.image,
                                    "image_credit": ev.image_credit,
                                })
                        self._pending_sources = _filter_query_images(
                            self._pending_sources, search_message
                        )
                        print(f"[SOURCE INTEL] Non-stream research: {len(evidence_package.evidence)} evidence items, "
                              f"confidence={evidence_package.confidence}")
                        _emit_activity("web_research", "reading_pages", detail=f"{len(self._pending_sources)} sources")
                        _emit_activity("web_research", "checking_info")
                        if evidence_package.conflicts:
                            _emit_activity("web_research", "comparing_sources", detail="Conflicting information detected")
                    else:
                        # Fallback to single search
                        live_ctx = _fetch_live_context_force(search_message, site=directed_site or "")
                        if live_ctx:
                            self._pending_sources = _filter_query_images(
                                get_last_search_sources(), search_message
                            )
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
                    _emit_activity("memory", "recalling", detail="Cross-session memory recall")
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
                            return _privacy_guard(str(clarification))
            except Exception as exc:
                print(f"[CONV RECOVERY] Recovery failed (non-fatal): {exc}")

            _emit_activity("summarization", "compiling", detail="Preparing your response")
            envelope = self._think(cid, message, image_data, live_context=live_ctx, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data, recovered_context=recovered_context)
            self.last_response_meta = envelope.get("meta") or self._metadata(False, True, "local")
            self.last_response_meta["sources"] = list(getattr(self, "_pending_sources", []) or [])
            raw_reply = str(envelope.get("reply") or "").strip()
            reply = _sanitize_reply_for_chat(strip_animation_tags(raw_reply), "") if raw_reply else FALLBACK_RESPONSE
            supplied_anim = envelope.get("animation")
            self.last_animation = (
                resolve_animation_state(
                    str(supplied_anim.get("state") or ""),
                    str(supplied_anim.get("emotion") or ""),
                ) if isinstance(supplied_anim, dict) and supplied_anim.get("state")
                else infer_animation_state(message, raw_reply)
            )

            try:
                _extract_memory_background(self.memory, message, reply)
            except Exception as exc:
                print(f"[ERROR] Memory extraction/storage skipped: {exc}")

            # ── Update long-term memory ───────────────────────────────
            _emit_activity("memory", "updating", detail="Saving conversation to memory")
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
                self.memory.add_message(cid, "assistant", reply, timestamp, source_metadata=self._pending_source_metadata)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (assistant) failed: {exc}")

            # ── Complete the activity indicator ───────────────────────
            _emit_activity("general", "preparing_response", detail="Response ready")

            return reply

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in respond(): {exc}")
            self.last_response_meta = self._metadata(False, True, "local")
            try:
                sess = get_current_session()
                if sess:
                    for h in list(sess._active_handles.values()):
                        h.fail(detail=str(exc)[:200])
            except Exception:
                pass
            return FALLBACK_RESPONSE

    def stream_respond(self, message: str, chat_id: str = "", image_data: str = "", mongo_history: list = None, persist_image_data: bool = True, dedup_id: str = "", capability: str = "text", activity_session=None, route=None):
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
            stored_image_data = ""
            if image_data and persist_image_data:
                user_msg = message + "\n[Image attached]"
                stored_image_data = image_data

            try:
                self.memory.add_message(cid, "user", user_msg, timestamp, image_data=stored_image_data)
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

            # Draft complete content before writing the real file so the
            # FILE_CARD reaches the PWA/Android WebView with a working link and
            # a substantive document behind it.
            pending_key = asset_request_key(getattr(self.memory, "user_id", ""), cid)
            pending = get_pending_asset_request(pending_key)
            file_request = _detect_file_request(message)
            used_pending = False
            if pending and str(pending.get("kind")) == "file":
                if file_request:
                    pop_pending_asset_request(pending_key)
                else:
                    message = merge_pending_asset_request(pending, message)
                    file_request = _detect_file_request(message) or file_request
                    used_pending = True
            if file_request:
                if used_pending:
                    pop_pending_asset_request(pending_key)
                elif needs_asset_clarification(message, "file", self.memory.get_chat(cid)):
                    reply = self._asset_clarification_reply(
                        cid, message, timestamp, "file", file_request.get("format", "")
                    )
                    yield reply
                    yield {"animation": self.last_animation}
                    return
                yield {"thinking_process": {
                    "step": "generating_file",
                    "label": f"Drafting your {file_request['format'].upper()} content...",
                }}
                reply = self._file_generation_reply(cid, message, timestamp, file_request)
                yield reply
                yield {"animation": self.last_animation}
                return

            if is_valleymind_identity_query(message):
                reply = self._identity_reply(cid, timestamp)
                yield reply
                yield {"animation": self.last_animation}
                return

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
            search_note = ""
            self._pending_sources = []
            directed_site, directed_query = parse_directed_search(message)
            research_info = classify_research_intent(message, recent_context=self._recent_context_for_routing(cid))
            intent = research_info["intent"]
            research_domain = research_info.get("domain", "general")
            # MODE B: user wants a copy-ready reply/message rather than a normal answer
            self._reply_mode = research_info.get("reason") in ("reply-writing", "copy-box")
            reply_mode = self._reply_mode
            forced_live_search = _needs_forced_live_search(message)
            if forced_live_search and not reply_mode:
                intent = "search"
                if research_domain in ("", "general"):
                    research_domain = (
                        "media" if _FORCED_SEARCH_SUBJECT_RE.search(message) else "news"
                    )
                print(f"[FORCED SEARCH] Direct factual question — searching web: {message[:120]}")
            contextual_route = self._contextual_search_route(cid, message, route=route)
            if directed_site:
                intent = "search"
                research_domain = "general"
            elif (contextual_route.get("needs_search") or contextual_route.get("search_query")) and not reply_mode:
                # Resolve "who directed it?" / "compare them" into a standalone
                # query before search runs, so plot/entity answers are grounded.
                intent = "search"
                if research_domain == "general":
                    research_domain = "tech" if _wants_entity_cards(message) else "media"
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
                      + (f" (directed: {directed_site})" if directed_site else "")
                      + (f" (contextual: {contextual_route.get('search_query', '')[:80]})" if contextual_route.get("needs_search") else ""))
                search_message = directed_query if directed_site else message
                if not directed_site and contextual_route.get("needs_search") and contextual_route.get("search_query"):
                    search_message = contextual_route["search_query"]
                elif not directed_site and _is_continuation_utterance(message) and msg_count_before > 0:
                    expanded = self._expand_continuation_query(cid, message)
                    if expanded and expanded != message:
                        search_message = expanded

                yield {"intent": "searching_web", "query": search_message, "domain": research_domain}
                with WebSearchAdapter.track_search(search_message) as search_act:
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
                    self._pending_source_metadata = _filter_query_images(
                        evidence_package.to_frontend_metadata(), search_message
                    )
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
                                "icon": ev.source_icon,
                                "logo": ev.logo or ev.source_icon,
                                "image": ev.image,
                                "image_credit": ev.image_credit,
                                "freshness_label": ev.freshness_label,
                            })
                    print(f"[STREAM RESEARCH] {len(evidence_package.evidence)} evidence items, "
                          f"confidence={evidence_package.confidence}, "
                          f"searched={evidence_package.total_searched}")
                    # Read each source page and track progress
                    seen_domains = set()
                    for i, ev in enumerate(evidence_package.evidence):
                        if ev.source_domain not in seen_domains:
                            seen_domains.add(ev.source_domain)
                            with WebSearchAdapter.track_page_read(ev.url, index=i + 1, total=len(evidence_package.evidence)):
                                self._pending_sources.append({
                                    "title": ev.title,
                                    "url": ev.url,
                                    "domain": ev.source_domain,
                                    "name": ev.source_name,
                                    "supports_claims": ev.supports_claims,
                                    "published": ev.published,
                                    "tier": ev.tier,
                                    "icon": ev.source_icon,
                                    "logo": ev.logo or ev.source_icon,
                                    "image": ev.image,
                                    "image_credit": ev.image_credit,
                                    "freshness_label": ev.freshness_label,
                                })
                    self._pending_sources = _filter_query_images(
                        self._pending_sources, search_message
                    )
                    print(f"[STREAM RESEARCH] {len(evidence_package.evidence)} evidence items, "
                          f"confidence={evidence_package.confidence}, "
                          f"searched={evidence_package.total_searched}")
                    yield {"thinking_process": {"step": "reviewing", "label": f"Reviewing {len(self._pending_sources)} sources..."}}
                    if evidence_package.conflicts:
                        with track_activity("comparing_information", label="Comparing conflicting information..."):
                            yield {"thinking_process": {"step": "comparing", "label": "Comparing conflicting information..."}}
                else:
                    # Fallback: try single search if research pipeline returned nothing
                    print("[STREAM RESEARCH] Research pipeline returned no evidence, falling back to single search")
                    try:
                        _reset_search_sources()
                        result = _fetch_live_context_force(search_message, site=directed_site or "")
                        if result and str(result).strip() != str(LIVE_DATA_UNAVAILABLE):
                            live_ctx = result
                            self._pending_sources = _filter_query_images(
                                get_last_search_sources(), search_message
                            )
                        elif result:
                            live_ctx = ""
                    except Exception as exc:
                        print(f"[STREAM SEARCH FALLBACK ERROR] {exc}")
                    if self._pending_sources:
                        yield {"thinking_process": {"step": "reviewing", "label": f"Reviewing {len(self._pending_sources)} sources..."}}

                if self._pending_sources:
                    yield {"sources": self._pending_sources}
                if evidence_package.evidence:
                    yield {"source_metadata": self._pending_source_metadata,
                           "research_confidence": evidence_package.confidence}
                elif not live_ctx:
                    print("[STREAM SEARCH] No live context found - proceeding with cached knowledge only")

                # Do not claim results exist when search ran but returned nothing reliable.
                if intent != "none" and not (live_ctx and str(live_ctx).strip() and str(live_ctx).strip() != str(LIVE_DATA_UNAVAILABLE)):
                    if forced_live_search:
                        search_note = (
                            "Live web search was attempted for this direct factual question. "
                            "Answer it directly and concretely from whatever reliable context "
                            "is available; if specific titles cannot be verified, name likely "
                            "candidates only when genuinely known and mark uncertainty briefly. "
                            "Never open with 'I don't know' or 'I don't have a confirmed list'."
                        )
                    else:
                        search_note = "Live search was attempted but unavailable or returned no reliable results for this request."
                    if live_ctx and str(live_ctx).strip() == str(LIVE_DATA_UNAVAILABLE):
                        live_ctx = ""

            yield {"thinking_process": {"step": "synthesizing", "label": "Synthesizing answer..."}}

            # ── Pinecone cross-session recall and knowledge fetch ─────
            global_memories = ""
            knowledge_data = ""
            mm = _get_memory_mgr()
            km = _get_knowledge_mgr()
            if mm:
                try:
                    with MemoryAdapter.track_recall() as mem_act:
                        recent_texts = [m.get("content", "") for m in self.memory.get_chat(cid)[-8:]]
                        fact_texts = [
                            f"{f.get('summary', '')} {f.get('value', '')}"
                            for f in self.memory.get_active_facts()
                        ]
                        global_memories = mm.recall_sync(
                            message, namespace=self.memory.user_id,
                            exclude_texts=recent_texts, dedupe_against=fact_texts,
                        )
                    MemoryAdapter.finalize_recall(mem_act, len(recent_texts))
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
            full_reply = ""
            raw_reply = ""       # includes the wire tag; used for animation inference
            anim_tail = ""       # small hold-back buffer for split [ANIM: ...] tokens
            tail_out = ""
            # Privacy is applied to every user-facing token, not just the final
            # message. The hold-back window prevents split phone/provider names
            # from slipping through between SSE chunks.
            privacy_stream = _PrivacyStreamFilter()
            # stream_respond() did not initialise these (respond() does), so the
            # post-stream add_message() below used to raise AttributeError on a
            # brand-new conversation and silently skip saving the assistant row.
            if not hasattr(self, "_pending_sources") or self._pending_sources is None:
                self._pending_sources = []
            if not hasattr(self, "_pending_source_metadata") or self._pending_source_metadata is None:
                self._pending_source_metadata = []
            _assistant_images = []
            for src in (self._pending_sources or []):
                if src and src.get("image"):
                    _assistant_images.append({
                        "url": src.get("image", ""),
                        "source": src.get("domain", ""),
                        "credit": src.get("image_credit", ""),
                    })
            stream_state = _new_stream_state(self.memory, timestamp, extra={
                "images": _assistant_images,
                "source_metadata": self._pending_source_metadata,
                "dedup_id": dedup_id,
            })
            try:
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
                                full_reply = _privacy_guard(str(clarification))
                                yield full_reply
                                return  # durable write happens in finally below
                except Exception as exc:
                    print(f"[CONV RECOVERY] Recovery failed (non-fatal): {exc}")

                msgs = self._groq_messages(cid, message, image_data, live_context=live_ctx, mongo_history=mongo_history, global_memories=global_memories, knowledge_data=knowledge_data, recovered_context=recovered_context, search_note=search_note)
                # Time-to-first-token for this request's first real LLM chunk.
                _llm_request_started = time.monotonic()
                _ttft_logged = False
                if capability == "text":
                    capability = _detect_capability(msgs)
                    print(f"[STREAM] Detected capability: {capability} ({_estimate_messages_tokens(msgs)} estimated tokens)")

                # ── Activity indicators for detected capabilities ─────────────
                if capability == "coding":
                    cap_act = track_activity("code_generation", label="Inspecting and writing code...", detail="Analyzing code structure")
                elif capability in ("image", "vision"):
                    cap_act = track_activity("image_analysis", label="Analyzing attached image...")
                elif capability == "large":
                    cap_act = track_activity("summarization", label="Summarizing long context...")
                else:
                    cap_act = None

                def _stream_once(_msgs, _finish_box, _max_tokens=2048):
                    for item in _call_llm_cluster_stream(_msgs, max_completion_tokens=_max_tokens, capability=capability):
                        if isinstance(item, dict):
                            if item.get("__vm_control") == "finish":
                                _finish_box[0] = str(item.get("finish_reason") or "")
                            else:
                                yield item
                            continue
                        if item:
                            yield item

                try:
                    # Auto-continue when a provider reports finish_reason=="length" so long
                    # answers/tables are not cut off. Continuations are new LLM calls (provider
                    # fallback applies per call); we never splice providers mid-stream.
                    _cont_msgs = list(msgs)
                    _max_cont = 3
                    _cont_n = 0
                    while True:
                        _seg = ""
                        _finish_box = [None]
                        for yielded in _stream_once(_cont_msgs, _finish_box):
                            if isinstance(yielded, str):
                                if yielded.strip() and not _ttft_logged:
                                    _ttft_logged = True
                                    _ttft_ms = int((time.monotonic() - _llm_request_started) * 1000)
                                    print(f"[TIMING] llm_ttft_ms={_ttft_ms}")
                                _seg += yielded
                                raw_reply += yielded
                                # The companion's [ANIM: ...] wire tag can arrive
                                # split across SSE chunks. Hold back a short tail,
                                # strip complete tags, then emit only stable text so
                                # the control tag never reaches chat or TTS.
                                anim_tail = strip_animation_tags_preserve_space(anim_tail + yielded)
                                emit = ""
                                if len(anim_tail) > 24:
                                    emit = anim_tail[:-24]
                                    anim_tail = anim_tail[-24:]
                                if emit:
                                    safe_emit = privacy_stream.feed(emit)
                                    if safe_emit:
                                        full_reply += safe_emit
                                        stream_state["chars"] = len(full_reply)
                                        # Checkpoint the growing reply so it survives an
                                        # app kill / client disconnect before the stream ends.
                                        _stream_checkpoint(self.memory, cid, stream_state, full_reply)
                                        yield safe_emit
                            elif yielded is not None:
                                yield yielded
                        _finish = _finish_box[0]
                        if _finish == "length" and _seg.strip() and _cont_n < _max_cont:
                            _cont_n += 1
                            print(f"[STREAM] finish_reason=length, auto-continuing ({_cont_n}/{_max_cont})")
                            yield {"stream_continue": True, "index": _cont_n}
                            _cont_msgs = list(msgs) + [
                                {"role": "assistant", "content": full_reply[-12000:]},
                                {"role": "user", "content": "Continue exactly where you left off. Do not repeat previous text, do not add a preamble. Continue the same answer/table/list."},
                            ]
                            continue
                        if _finish == "length" and _seg.strip() and _cont_n >= _max_cont:
                            yield {"truncated": True, "finish_reason": "length"}
                        break
                except Exception as exc:
                    print(f"[STREAM ERROR] LLM stream failed: {_short_error_detail(str(exc))}")
                    # Try next provider once with the non-streaming cluster before apologizing,
                    # but only when nothing has been emitted yet.
                    if not full_reply.strip():
                        try:
                            response, meta = _call_llm_cluster(msgs)
                            print(f"[STREAM ERROR] Recovered via {_served_by(meta)} (non-stream fallback)")
                            raw_reply = response
                            full_reply = _privacy_guard(strip_animation_tags(response))
                            yield full_reply
                        except Exception as exc2:
                            print(f"[STREAM ERROR] All LLM providers failed: {_short_error_detail(str(exc2))}")
                            fallback = "I apologize, but I'm having trouble processing your request right now."
                            full_reply = _privacy_guard(fallback)
                            yield full_reply
                    else:
                        # Partial output already sent - cannot safely replace; leave as-is.
                        print("[STREAM ERROR] Partial output was emitted before failure; not replacing with apology.")
            finally:
                # Flush the held-back tail (with any wire tag removed), then
                # resolve the semantic animation for this turn. Runs even when
                # the client disconnects mid-stream.
                # The privacy filter still holds the last 32 stable characters;
                # flush those before the shorter animation hold-back tail.
                tail_out = privacy_stream.flush() + _privacy_guard(strip_animation_tags(anim_tail))
                if tail_out:
                    full_reply += tail_out
                anim_hint = _ANIM_TAG_RE.search(raw_reply)
                emo_hint = _EMO_TAG_RE.search(raw_reply)
                self.last_animation = (
                    resolve_animation_state(anim_hint.group(1), emo_hint.group(1) if emo_hint else "")
                    if anim_hint else infer_animation_state(message, raw_reply)
                )
                _stream_finalize(self.memory, cid, stream_state, full_reply)
                if cap_act is not None:
                    cap_act.complete()

            if tail_out:
                yield tail_out
            # Structured control event: app.py forwards dict yields verbatim as
            # SSE JSON, so the companion shell can pose the body + face.
            yield {"animation": self.last_animation}

            # ── Update long-term memory ───────────────────────────────
            save_act = MemoryAdapter.track_update()
            try:
                self.memory.save_memory()
            except Exception as exc:
                print(f"[ERROR] Memory save failed: {exc}")
            MemoryAdapter.finalize_update(save_act, saved=True)

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

            # ── Activity indicator completed via context manager ──────────

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in stream_respond(): {exc}")
            return
