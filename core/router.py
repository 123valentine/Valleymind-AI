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


# ── RouteDecision — the only output of the router ────────────────────────────

@dataclass(frozen=True)
class RouteDecision:
    """Immutable routing decision.  No Flask, no generation, no ProviderManager."""
    capabilities: tuple[Capability, ...]
    confidence: float
    reasoning: str


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
  image  — user wants to SEE a visual output: picture, illustration,
           artwork, design, logo, poster, flyer, banner, meme, diagram,
           visual concept, mockup, painting, drawing, render.
           Also: "show me", "visualize", "imagine this scene",
           "turn into art", "design a", "make a poster/flyer/logo".
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
        """Classify a user request into one or more routing capabilities.

        Parameters
        ----------
        message   : raw user text.
        has_image : whether the user attached an image to this message.
        source    : explicit UI hint ("image_modal", "video_tool", …).

        Returns
        -------
        RouteDecision — capabilities, confidence, reasoning.
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


# ── Module singleton ─────────────────────────────────────────────────────────

_router: Optional[CapabilityRouter] = None


def get_router() -> CapabilityRouter:
    global _router
    if _router is None:
        _router = CapabilityRouter()
    return _router
