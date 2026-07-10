"""Semantic Capability Router — single entry point for user request classification.

Architecture (layered routing):
    Priority 1: Explicit UI hints   (source field from frontend — zero cost)
    Priority 2: Metadata analysis   (attachments, message shape — zero cost)
    Priority 3: Semantic LLM call   (only when genuinely ambiguous)

The router ONLY decides.  It never generates content.
app.py receives the RouteDecision and dispatches to the correct pipeline.

There is exactly one Capability enum — imported from provider_manager.
There is exactly one output type   — RouteDecision(capability, confidence, reasoning).

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
from dataclasses import dataclass
from typing import Optional

from core.provider_manager import Capability


# ── RouteDecision — the only output of the router ────────────────────────────

@dataclass(frozen=True)
class RouteDecision:
    """Immutable routing decision.  No Flask, no generation, no ProviderManager."""
    capability: Capability
    confidence: float
    reasoning: str


# ── Classification prompt (used only at Priority 3) ─────────────────────────

_CLASSIFY_PROMPT = """\
You are an intent classifier for an AI assistant.  Given a user message,
determine which capability they are requesting.

Available capabilities (use the exact value):
  text   — conversation, questions, explanations, opinions, advice,
           brainstorming, facts, emotional support, search queries,
           code questions asked alongside conversation.
  image  — user wants to SEE a visual output: picture, illustration,
           artwork, design, logo, poster, flyer, banner, meme, diagram,
           visual concept, mockup, painting, drawing, render.
           Also: "show me", "visualize", "imagine this scene",
           "turn into art", "design a", "make a poster/flyer/logo".
  video  — user wants a video or animation.
  audio  — user wants music, sound effects, or audio content.
  code   — user's PRIMARY intent is to have code written, debugged,
           explained, reviewed, or refactored.

Rules:
  1. Classify by intent, not by surface keywords.
  2. If the user attached an image ([Image attached] tag), classify
     based on the TEXT — sending an image ≠ asking to generate one.
  3. If ambiguous, default to "text" (safest).
  4. Code as a side question inside conversation → "text".
     Code as the primary request → "code".

Respond with ONLY a JSON object (no markdown, no explanation):
{"capability": "<value>", "confidence": <0.0-1.0>, "reasoning": "<one sentence>"}"""


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
    ) -> RouteDecision:
        """Classify a user request into a routing capability.

        Parameters
        ----------
        message   : raw user text.
        has_image : whether the user attached an image to this message.
        source    : explicit UI hint ("image_modal", "video_tool", …).

        Returns
        -------
        RouteDecision — capability, confidence, reasoning.
        """
        start = time.perf_counter()

        # ── Priority 1: explicit UI hints (zero latency) ──────────────
        decision = self._p1_ui_hints(source)
        if decision:
            self._log(decision, start)
            return decision

        # ── Priority 2: metadata / structural analysis (zero latency) ─
        decision = self._p2_metadata(message, has_image)
        if decision:
            self._log(decision, start)
            return decision

        # ── Priority 3: semantic LLM classification ───────────────────
        decision = self._p3_semantic(message, has_image)
        self._log(decision, start)
        return decision

    # ── P1: explicit UI hints ─────────────────────────────────────────

    @staticmethod
    def _p1_ui_hints(source: Optional[str]) -> Optional[RouteDecision]:
        """Map explicit frontend tool-mode flags to capabilities."""
        if source == "image_modal":
            return RouteDecision(Capability.IMAGE, 1.0, "Explicit image generation from UI")
        if source == "video_tool":
            return RouteDecision(Capability.VIDEO, 1.0, "Explicit video generation from UI")
        if source == "audio_tool":
            return RouteDecision(Capability.AUDIO, 1.0, "Explicit audio generation from UI")
        return None

    # ── P2: metadata / structural analysis ────────────────────────────

    @staticmethod
    def _p2_metadata(message: str, has_image: bool) -> Optional[RouteDecision]:
        """Resolve obvious cases from message shape alone — no LLM needed."""
        text = (message or "").strip()

        # Empty body with nothing attached → safe default to chat
        if not text and not has_image:
            return RouteDecision(Capability.TEXT, 1.0, "Empty message defaults to chat")

        # Image attached but no text → user is sharing, not generating
        if not text and has_image:
            return RouteDecision(Capability.TEXT, 0.9, "Image attachment without text intent — treating as chat")

        return None  # genuinely ambiguous → escalate to P3

    # ── P3: semantic LLM classification ───────────────────────────────

    def _p3_semantic(self, message: str, has_image: bool) -> RouteDecision:
        """Use the application's LLM provider cluster to classify intent."""
        classified_message = message.strip()
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
            return RouteDecision(Capability.TEXT, 0.0, f"Classification failed — defaulting to text: {exc}")

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

        cap_str = str(data.get("capability", "text")).strip().lower()
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        reasoning = str(data.get("reasoning", "No reasoning provided")).strip()

        try:
            capability = Capability(cap_str)
        except ValueError:
            print(f"[Router] Unknown capability '{cap_str}' — defaulting to text")
            capability = Capability.TEXT

        return RouteDecision(capability=capability, confidence=confidence, reasoning=reasoning)

    # ── Structured logging ────────────────────────────────────────────

    @staticmethod
    def _log(decision: RouteDecision, start: float) -> None:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[Router] Classified in {elapsed_ms:.0f}ms")
        print(f"[Router]   Capability: {decision.capability.value}")
        print(f"[Router]   Confidence: {decision.confidence:.2f}")
        print(f"[Router]   Reasoning:  {decision.reasoning}")


# ── Module singleton ─────────────────────────────────────────────────────────

_router: Optional[CapabilityRouter] = None


def get_router() -> CapabilityRouter:
    global _router
    if _router is None:
        _router = CapabilityRouter()
    return _router
