"""Semantic Capability Router — single entry point for user request classification.

Architecture (layered routing):
    Priority 1: Explicit UI hints   (source field from frontend — zero cost)
    Priority 2: Metadata analysis   (attachments, message shape — zero cost)
    Priority 3: Semantic LLM call   (only when genuinely ambiguous)

The router ONLY decides.  It never generates content.
app.py receives the RouteDecision and dispatches to the correct pipeline(s).

There is exactly one Capability enum — imported from provider_manager.
There is exactly one output type   — RouteDecision(capabilities, confidence, reasoning).

A single user request can trigger MULTIPLE capabilities.
Example: "Explain neural networks and create an infographic"
    → capabilities=["text", "image"]

Extending with new capabilities:
    1. Add a value to Capability in provider_manager.py.
    2. Describe it in _CLASSIFY_PROMPT below.
    3. Add a dispatch branch in app.py.
    No changes to the router class itself are needed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from core.provider_manager import Capability
from core.activity_v2 import track_activity


# ── RouteDecision — the only output of the router ────────────────────────────

@dataclass(frozen=True)
class RouteDecision:
    """Immutable routing decision.  No Flask, no generation, no ProviderManager.

    ``needs_search`` / ``search_query`` carry contextual follow-up research
    instructions from :meth:`CapabilityRouter._needs_contextual_research` to the
    response pipeline.  They default to empty/false so every existing 3-argument
    construction (and test) keeps working unchanged.
    """
    capabilities: tuple[Capability, ...]
    confidence: float
    reasoning: str
    needs_search: bool = False
    search_query: str = ""

    def get(self, key: str, default=None):
        """Dict-style access so callers can use ``route.get("needs_search")``."""
        if hasattr(self, key):
            return getattr(self, key)
        return default

    def to_dict(self) -> dict:
        return {
            "capabilities": tuple(c.value if hasattr(c, "value") else c for c in self.capabilities),
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "needs_search": self.needs_search,
            "search_query": self.search_query,
        }


# ── Classification prompt (used only at Priority 3) ─────────────────────────

_CLASSIFY_PROMPT = """\
You are an intent classifier for an AI assistant.  Given a user message,
determine WHICH capabilities they are requesting.  A single request can
require MULTIPLE capabilities served together.

Available capabilities (use the exact string values):
  text   — conversation, questions, explanations, opinions, advice,
           brainstorming, facts, emotional support, search queries,
           code questions asked alongside conversation, or any request
           that benefits from a written explanation or discussion.
  image  — user wants a NEW visual CREATED/GENERATED: illustration,
           artwork, original design, poster, flyer, banner, meme, diagram,
           visual concept, mockup, painting, drawing, render, or a newly
           designed logo/artwork (creative generation).
           Also: "visualize this concept", "imagine this scene",
           "turn into art", "design a poster/flyer", "draw/create/generate/make an image".
           IMPORTANT: Do NOT classify as image when the user wants to SEE or
           FIND a real-world logo, product photo, person, place, brand image,
           or screenshot (e.g. "show me the Nike logo", "photo of iPhone",
           "ChatGPT logo"). For those, use ["text"] — never generate fake
           images of real entities.
  video  — user wants a VIDEO or animation: movie, clip, footage, timelapse,
           cinematic scene, animated sequence, motion graphics,
           animated birthday invitation, turning an image into motion,
           "make a video", "create a video of", "animate this",
           "turn into a video", "show this in motion".
  audio  — user wants music, sound effects, or audio content.
  code   — user's PRIMARY intent is to have code written, debugged,
           explained, reviewed, or refactored.

Classification rules:
  1. Classify by intent, not by surface keywords.
  2. If the user attached an image ([Image attached] tag), classify
     based on the TEXT — sending an image ≠ asking to generate one.
  3. If ambiguous, default to ["text"] (safest).
  4. Code as a side question inside conversation → ["text"].
     Code as the primary request → ["code"].

MULTI-CAPABILITY examples:
  "Explain what a neural network is and create an infographic."
      → ["text", "image"]
  "Design a birthday flyer for John with instructions on how to customize it."
      → ["text", "image"]
  "Create a realistic lion."
      → ["image"]
  "Generate a 3D model of a futuristic city and explain the design choices."
      → ["text", "image"]
  "Explain quantum physics."
      → ["text"]
  "Show me a picture of a sunset over the ocean."
      → ["image"]
  "Write me a song and generate the audio."
      → ["text", "audio"]
  "Create a cinematic video of a lion walking through the jungle."
      → ["video"]
  "Generate an animated birthday invitation and explain what it shows."
      → ["text", "video"]
  "Turn this image into a moving video."
      → ["video"]
  "Create a realistic 10-second video of a futuristic city."
      → ["video"]
  "Make a video showing how to bake a cake, with step-by-step narration."
      → ["text", "video"]

Rules for combining:
  - "text" + any media = the text explains or contextualizes the media.
  - If the user only wants a visual/audio with no explanation, use
    the media capability ALONE.
  - If the user asks for BOTH explanation AND visual, include BOTH.
  - When in doubt about whether explanation is wanted, include "text".

Respond with ONLY a JSON object (no markdown fences, no explanation):
{"capabilities": ["<cap1>", "<cap2>", ...], "confidence": <0.0-1.0>, "reasoning": "<one sentence>"}"""


# ── Contextual entity / plot follow-up detection ────────────────────────────

_FOLLOWUP_CUE_RE = re.compile(
    r"\b(?:let'?s talk about|talk about|what happened|who directed|director|"
    r"season\s*2|episode|plot|character|boyfriend|girlfriend|compare|compared|"
    r"mention|list|recommend|top|best|versus|vs\.?|three\s+a\.?i\.?s?)\b",
    re.IGNORECASE,
)
_ENTITY_NOUN_RE = re.compile(
    r"\b(?:movies?|films?|tv|series|shows?|episodes?|books?|novels?|anime|manga|"
    r"games?|songs?|albums?|artists?|singers?|actors?|actresses?|directors?|"
    r"celebrit(?:y|ies)|club|team|football|products?|brands?|a\.?i\.?s?|llms?|"
    r"models?|apps?|tools?|software|companies)\b",
    re.IGNORECASE,
)
_CONTEXT_DEICTIC_RE = re.compile(
    r"\b(?:this|that|it|they|them|those|these|the|same|earlier|before)\b",
    re.IGNORECASE,
)
_CODE_ONLY_RE = re.compile(
    r"\b(?:code|function|python|javascript|typescript|debug|refactor|compile|"
    r"stack trace|error message|sql query|regex|unit test)\b",
    re.IGNORECASE,
)
_MEMORY_ONLY_RE = re.compile(
    r"(?:\b(?:my|mine|me|i)\b.*\b(?:name|birthday|job|wife|husband|address)\b)|"
    r"(?:\b(?:remember|forget|recall)\b.*\b(?:my|me|i)\b)",
    re.IGNORECASE,
)
_QUERY_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "do", "does", "did", "of",
    "to", "in", "on", "for", "and", "or", "it", "its", "this", "that", "these",
    "those", "them", "they", "about", "me", "my", "please", "tell", "what",
    "who", "when", "where", "why", "how", "can", "you", "i", "let", "lets",
    "s", "t", "just", "with", "from", "there", "happened", "talk",
})


def _history_text(history, limit: int = 6) -> str:
    """Flatten the last ``limit`` turns into a compact classifier string."""
    if not history:
        return ""
    if isinstance(history, str):
        return history[-4000:]
    turns = []
    for turn in history[-limit:]:
        if isinstance(turn, dict):
            role = str(turn.get("role") or "user").capitalize()
            content = str(turn.get("content") or turn.get("text") or "").strip()
        else:
            role, content = "User", str(turn).strip()
        if content:
            turns.append(f"{role}: {content[:400]}")
    return "\n".join(turns)


def _active_topic_from_history(history, limit: int = 6) -> str:
    """Best-effort extraction of the movie/show/entity currently being discussed."""
    if not history:
        return ""
    turns = list(history)[-limit:] if not isinstance(history, str) else [history]

    def _content(turn):
        if isinstance(turn, dict):
            return str(turn.get("content") or turn.get("text") or "")
        return str(turn or "")

    def _is_user(turn):
        if isinstance(turn, dict):
            return str(turn.get("role") or "user").lower() != "assistant"
        return True

    invalid = {
        "i", "you", "he", "she", "it", "they", "them", "we", "that", "this",
        "these", "those", "the", "yes", "yh", "yeah", "ok", "okay", "what",
        "who", "and", "but", "so", "because", "there", "here",
    }
    title_re = re.compile(
        r"[《“\"']([^》”\"']{2,80})[》”\"']|"
        r"\b((?:[A-Z][\w'&-]+\s+){1,3}[A-Z][\w'&-]*|[A-Z][\w'&-]{3,})\s*(?:\(\d{4}\))?"
    )
    entity_context_re = re.compile(
        r"\b(?:movie|film|show|series|anime|book|novel|game|song|club|team|brand|"
        r"product|a\.?i\.?|model)\b[\s:-]+(?:about\s+)?([^.,!?;\n]{2,70})",
        re.IGNORECASE,
    )
    # Prefer the user's own turns; assistant phrasing ("That sounds like…") is
    # a poor source for a search topic.
    ordered = [t for t in reversed(turns) if _is_user(t)] + [t for t in reversed(turns) if not _is_user(t)]
    for turn in ordered:
        content = re.sub(
            r"^(?:User|Assistant|Marcus|Angelina|Elena):\s*",
            "", _content(turn).strip(), flags=re.IGNORECASE,
        )
        m = entity_context_re.search(content)
        if m:
            candidate = m.group(1).strip()
            if candidate.lower() not in invalid:
                return candidate[:80]
        m = title_re.search(content)
        if m:
            candidate = (m.group(1) or m.group(2) or "").strip()
            if candidate and candidate.lower() not in invalid:
                return candidate[:80]
    return ""


# ── Router ───────────────────────────────────────────────────────────────────

class CapabilityRouter:
    """Layered semantic router.  Only decides, never generates.

    Every ``classify`` call is stateless and independent.
    """

    def classify(
        self,
        message: str,
        *,
        has_image: bool = False,
        source: Optional[str] = None,
        history: Optional[list] = None,
    ) -> RouteDecision:
        """Classify a user request into one or more routing capabilities.

        Parameters
        ----------
        message   : raw user text.
        has_image : whether the user attached an image to this message.
        source    : explicit UI hint ("image_modal", "video_tool", …).
        history   : recent conversation turns (dicts with role/content, or strings).
                    Used to resolve indirect entity/plot follow-ups into a
                    standalone, searchable query.

        Returns
        -------
        RouteDecision — capabilities, confidence, reasoning.
        """
        start = time.perf_counter()

        with track_activity("request_understanding"):
            # ── Priority 1: explicit UI hints (zero latency) ──────────────
            decision = self._p1_ui_hints(source)
            if decision:
                self._log(decision, start)
                return decision

            # ── Contextual entity/plot follow-ups (zero latency) ──────────
            # A follow-up such as "who directed it?" is meaningless on its own;
            # resolve it against recent turns and force a grounded web search.
            contextual = self._needs_contextual_research(message, history)
            if contextual.get("needs_search"):
                decision = RouteDecision(
                    capabilities=(Capability.TEXT,),
                    confidence=0.88,
                    reasoning=contextual.get("reason", "Contextual entity follow-up"),
                    needs_search=True,
                    search_query=contextual.get("search_query", ""),
                )
                self._log(decision, start)
                return decision

            # ── Priority 2: metadata / structural analysis (zero latency) ─
            decision = self._p2_metadata(message, has_image)
            if decision:
                self._log(decision, start)
                return decision

            # ── Priority 3: semantic LLM classification ───────────────────
            decision = self._p3_semantic(message, has_image, history)
            self._log(decision, start)
            return decision

    # ── Contextual follow-up research ─────────────────────────────────

    @staticmethod
    def _needs_contextual_research(message: str, history=None) -> dict:
        """Detect entity/plot follow-ups and build a standalone search query.

        Inspects the current turn plus the last 4–6 history turns. Returns a
        dict with ``needs_search``, ``search_query`` and ``reason``. Pure code
        generation and explicit memory-only questions are deliberately ignored.
        """
        text = str(message or "").strip()
        if not text:
            return {"needs_search": False, "search_query": "", "reason": "empty"}

        has_code_intent = bool(_CODE_ONLY_RE.search(text))
        has_entity = bool(_ENTITY_NOUN_RE.search(text))
        if has_code_intent and not has_entity:
            return {"needs_search": False, "search_query": "", "reason": "code request"}
        if _MEMORY_ONLY_RE.search(text):
            return {"needs_search": False, "search_query": "", "reason": "memory-only question"}

        context = _history_text(history, limit=6)
        cue = bool(_FOLLOWUP_CUE_RE.search(text))
        deictic = bool(_CONTEXT_DEICTIC_RE.search(text))
        list_request = bool(
            re.search(r"\b(?:mention|list|recommend|compare|top|best|three|3)\b", text, re.IGNORECASE)
            and has_entity
        )
        followup = bool(cue and (has_entity or deictic) and (context or has_entity))
        entity_question = bool(has_entity and re.search(r"[?？]\s*$", text))

        if not (list_request or followup or entity_question):
            return {"needs_search": False, "search_query": "", "reason": "no contextual entity clue"}

        topic = _active_topic_from_history(history, limit=6)
        if topic and not _ENTITY_NOUN_RE.search(topic):
            history_entity = _ENTITY_NOUN_RE.search(context)
            if history_entity:
                topic = f"{history_entity.group(0).lower()} {topic}"
        clues = [
            word for word in re.findall(r"[A-Za-z][A-Za-z0-9'&-]{1,}", text)
            if word.lower() not in _QUERY_STOPWORDS
        ]
        parts = []
        for part in [topic] + clues:
            part = part.strip()
            if part and part.lower() not in [p.lower() for p in parts]:
                parts.append(part)
        query = " ".join(parts) if parts else text
        reason = "Contextual entity follow-up" if (followup or list_request) else "Entity research question"
        return {
            "needs_search": True,
            "search_query": query,
            "reason": reason,
            "active_topic": topic,
        }

    # ── P1: explicit UI hints ─────────────────────────────────────────

    @staticmethod
    def _p1_ui_hints(source: Optional[str]) -> Optional[RouteDecision]:
        """Map explicit frontend tool-mode flags to capabilities."""
        if source == "image_modal":
            return RouteDecision((Capability.IMAGE,), 1.0, "Explicit image generation from UI")
        if source == "video_tool":
            return RouteDecision((Capability.VIDEO,), 1.0, "Explicit video generation from UI")
        if source == "audio_tool":
            return RouteDecision((Capability.AUDIO,), 1.0, "Explicit audio generation from UI")
        return None

    # ── P2: metadata / structural analysis ────────────────────────────

    @staticmethod
    def _p2_metadata(message: str, has_image: bool) -> Optional[RouteDecision]:
        """Resolve obvious cases from message shape alone — no LLM needed."""
        text = (message or "").strip()

        # Empty body with nothing attached → safe default to chat
        if not text and not has_image:
            return RouteDecision((Capability.TEXT,), 1.0, "Empty message defaults to chat")

        # Image attached but no text → user is sharing, not generating
        if not text and has_image:
            return RouteDecision((Capability.TEXT,), 0.9, "Image attachment without text intent — treating as chat")

        return None  # genuinely ambiguous → escalate to P3

    # ── P3: semantic LLM classification ───────────────────────────────

    def _p3_semantic(self, message: str, has_image: bool, history=None) -> RouteDecision:
        """Use the application's LLM provider cluster to classify intent."""
        classified_message = message.strip()
        context = _history_text(history, limit=6)
        if context:
            classified_message = (
                "[Recent conversation context (use it to resolve follow-ups)]\n"
                + context + "\n\n[Latest message]\n" + classified_message
            )
        if has_image:
            classified_message = (
                "[Note: The user attached an image to this message.  "
                "They are SENDING an image, not requesting generation.  "
                "Classify based on the TEXT intent only.]\n"
                + classified_message
            )

        messages = [
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": classified_message},
        ]

        try:
            from core.brain import _call_llm_cluster
            raw, _meta = _call_llm_cluster(messages, timeout=10)
            return self._parse_json(raw)
        except Exception as exc:
            print(f"[Router] LLM classification failed: {exc}")
            return RouteDecision((Capability.TEXT,), 0.0, f"Classification failed — defaulting to text: {exc}")

    # ── Response parsing ──────────────────────────────────────────────

    @staticmethod
    def _parse_json(raw: str) -> RouteDecision:
        """Extract a RouteDecision from the LLM's JSON response."""
        cleaned = raw.strip()
        # Strip markdown fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        data: dict = {}
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except (json.JSONDecodeError, ValueError):
                    pass

        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        reasoning = str(data.get("reasoning", "No reasoning provided")).strip()

        # Parse capabilities — handle both old single-value and new array format
        raw_caps = data.get("capabilities") or data.get("capability") or "text"
        if isinstance(raw_caps, str):
            raw_caps = [raw_caps]

        capabilities: list[Capability] = []
        for cap_str in raw_caps:
            cap_str = str(cap_str).strip().lower()
            try:
                capabilities.append(Capability(cap_str))
            except ValueError:
                print(f"[Router] Unknown capability '{cap_str}' — skipping")

        if not capabilities:
            capabilities = [Capability.TEXT]

        return RouteDecision(
            capabilities=tuple(capabilities),
            confidence=confidence,
            reasoning=reasoning,
        )

    # ── Structured logging ────────────────────────────────────────────

    @staticmethod
    def _log(decision: RouteDecision, start: float) -> None:
        elapsed_ms = (time.perf_counter() - start) * 1000
        caps_str = ", ".join(c.value for c in decision.capabilities)
        print(f"[Router] Classified in {elapsed_ms:.0f}ms")
        print(f"[Router]   Capabilities: [{caps_str}]")
        print(f"[Router]   Confidence:   {decision.confidence:.2f}")
        print(f"[Router]   Reasoning:    {decision.reasoning}")


# Module-level alias used by the response pipeline (core.brain).
def _needs_contextual_research(message: str, history=None) -> dict:
    return CapabilityRouter._needs_contextual_research(message, history)


# ── Module singleton ─────────────────────────────────────────────────────────

_router: Optional[CapabilityRouter] = None


def get_router() -> CapabilityRouter:
    global _router
    if _router is None:
        _router = CapabilityRouter()
    return _router
