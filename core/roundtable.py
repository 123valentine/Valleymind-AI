"""The Round Table — a three-way discussion between Angelina, Marcus, Elena and
the user, in voice and/or text.

Why this is robust (the old version replied with nothing whenever the single
strict-JSON "director" call landed on a weak/degraded model or timed out):

1. A lightweight DIRECTOR decides only WHO speaks and in what ORDER. Its output
   is parsed defensively, and if it yields nothing a heuristic guarantees the
   room is never silent when the user actually addressed it.
2. Each chosen persona then speaks with its OWN assigned provider/model, in
   PLAIN TEXT — there is no JSON to mis-parse, so a persona line can't "fail to
   parse" into silence. Distinct models give genuinely distinct voices/thinking,
   and each keeps the full provider fallback chain behind it.

Provider-per-persona is env-configurable:
  ROUNDTABLE_PROVIDER_MARCUS / _ELENA / _ANGELINA = groq | openrouter | nvidia | gemini
"""
from __future__ import annotations

import json
import os
import re

from core.brain import _call_llm_cluster, call_cluster_preferred, provider_model_label
from core.tts import voice_for_persona

CREW = ["angelina", "marcus", "elena"]

# One provider per persona (env-overridable). Default: Marcus→Groq, Elena→
# OpenRouter, Angelina→NVIDIA (a 70b model, see brain.NVIDIA_DEFAULT_MODEL).
_DEFAULT_PROVIDER = {"marcus": "groq", "elena": "openrouter", "angelina": "nvidia"}

_ESSENCE = {
    "angelina": ("Angelina, the writer — imaginative, warm and quick with ideas. You pitch fresh "
                 "angles, characters and emotional hooks."),
    "marcus": ("Marcus, the director and the senior of the crew — sharp, decisive, a little blunt. "
               "You push back, challenge weak ideas and make the call."),
    "elena": ("Elena, the editor — practical and grounded. You add concrete notes, catch problems "
              "early and keep things moving toward something finished."),
}


def provider_for(persona: str) -> str:
    """Which LLM provider a persona speaks with (env-overridable)."""
    env = os.getenv(f"ROUNDTABLE_PROVIDER_{persona.upper()}", "").strip().lower()
    return env or _DEFAULT_PROVIDER.get(persona, "groq")


def provider_report() -> dict:
    """{persona: 'provider (model)'} — which model each persona ends up on."""
    return {p: provider_model_label(provider_for(p)) for p in CREW}


def _format_history(history: list | None) -> str:
    lines = []
    for h in (history or [])[-12:]:
        who = str(h.get("persona") or h.get("role") or "user").strip().lower()
        text = str(h.get("text") or h.get("content") or "").strip()
        if not text:
            continue
        label = who.capitalize() if who in CREW else "User"
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


# ── Step 1: who speaks, in what order ───────────────────────────────────────

_DIRECTOR_SYSTEM = """You direct a live round-table discussion between three crew members and the user.
- Angelina (writer): creative, proposes ideas.
- Marcus (director, senior): critical, pushes back, decides.
- Elena (editor): practical, catches problems, wraps up.

Decide WHO should speak next and in what order, reading the room like real people:
- Whoever the message actually fits speaks; NOT everyone every time. Usually 1 or 2. All three only when the topic truly pulls everyone in.
- Order by relevance — most-relevant first, never a fixed rotation. If the user names someone, that person leads.
- If the user asked the room something real, at LEAST one person must answer.

Respond with ONLY JSON, no prose: {"speakers":["marcus","angelina"]}
Persona keys in speaking order, 0 to 3 of them."""


def _parse_speakers(raw: str) -> list:
    """Pull an ordered list of persona keys out of the director reply — tolerant
    of fences/prose. Falls back to scanning for names in order of appearance."""
    if not raw:
        return []
    out: list = []
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            for s in (data.get("speakers") if isinstance(data, dict) else []) or []:
                s = str(s).strip().lower()
                if s in CREW and s not in out:
                    out.append(s)
        except Exception:
            pass
    if not out:                                  # name-scan fallback (order preserved)
        low = raw.lower()
        found = [(low.find(p), p) for p in CREW if p in low]
        out = [p for i, p in sorted(found) if i >= 0]
    return out[:3]


def _heuristic_speakers(message: str) -> list:
    """Never leave the room silent: if the user named crew, use them; otherwise
    the writer pitches and the director reacts."""
    low = (message or "").lower()
    named = [p for p in CREW if re.search(rf"\b{p}\b", low)]
    if named:
        return named[:3]
    return ["angelina", "marcus"]


def _plan_speakers(message: str, history: list) -> list:
    convo = _format_history(history)
    prompt = ((f"Conversation so far:\n{convo}\n\n" if convo else "")
              + f"User's latest message: {message}\n\nWho speaks, and in what order?")
    try:
        raw, _ = _call_llm_cluster(
            [{"role": "system", "content": _DIRECTOR_SYSTEM},
             {"role": "user", "content": prompt}],
            timeout=20,
        )
        speakers = _parse_speakers(raw)
    except Exception as exc:
        print(f"[ROUNDTABLE] director failed ({exc}); using heuristic")
        speakers = []
    return speakers or _heuristic_speakers(message)


# ── Step 2: each persona speaks (plain text, own model) ──────────────────────

_LABEL_RE = re.compile(r"^\s*(angelina|marcus|elena)\s*[:\-–]\s*", re.IGNORECASE)


def _clean_line(text: str) -> str:
    """Strip a leading 'Marcus:' label, markdown and surrounding quotes, and cap
    length so a line stays a short spoken beat."""
    t = str(text or "").strip()
    t = _LABEL_RE.sub("", t)
    t = t.replace("```", "").replace("**", "").replace("*", "").strip()
    if len(t) >= 2 and t[0] in "\"'“”" and t[-1] in "\"'“”":
        t = t[1:-1].strip()
    return t[:600].strip()


def _speak(persona: str, message: str, history: list, said_this_turn: list) -> tuple[str, str]:
    system = (
        f"You are {_ESSENCE[persona]}\n\n"
        "You are in a live round-table with the user and the other two crew members. "
        "Reply as yourself in 1-3 short, natural sentences that will be spoken ALOUD. "
        "No markdown, no stage directions, no emojis, no name label. React to the user AND "
        "to what your crewmates just said — add something new, don't repeat them. Stay fully "
        "in character."
    )
    convo = _format_history(history)
    ctx = f"Conversation so far:\n{convo}\n\n" if convo else ""
    if said_this_turn:
        ctx += ("Just said in this round (react to it):\n"
                + "\n".join(f"{p.capitalize()}: {t}" for p, t in said_this_turn) + "\n\n")
    ctx += f"User's latest message: {message}\n\nYour reply as {persona.capitalize()}:"
    text, provider = call_cluster_preferred(
        [{"role": "system", "content": system}, {"role": "user", "content": ctx}],
        preferred=provider_for(persona), timeout=30,
    )
    return _clean_line(text), provider


def orchestrate(message: str, history: list | None = None) -> list:
    """Return an ordered list of {persona, voice, text, provider} turns for one
    user message. Never raises; degrades to fewer turns rather than none."""
    history = history or []
    speakers = _plan_speakers(message, history)
    turns: list = []
    said: list = []
    for persona in speakers:
        try:
            text, provider = _speak(persona, message, history, said)
        except Exception as exc:
            print(f"[ROUNDTABLE] {persona} could not speak: {exc}")
            continue
        if not text:
            continue
        said.append((persona, text))
        turns.append({
            "persona": persona,
            "text": text,
            "voice": voice_for_persona(persona),
            "provider": provider,
        })
    return turns
