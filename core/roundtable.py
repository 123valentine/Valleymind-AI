"""The Round Table — orchestrates a three-way voice discussion.

One LLM "director" call per user message decides which crew members respond, in
what order, and what each says — following the room rules (most-relevant first,
not everyone every time, they react to each other, short spoken lines). TTS for
each turn is synthesised client-side via /api/tts (already metered against the
shared budget cap), so this module only plans the turns.
"""
from __future__ import annotations

import json
import re

from core.brain import _call_llm_cluster
from core.tts import voice_for_persona

CREW = ["angelina", "marcus", "elena"]

_SYSTEM = """You direct a live round-table VOICE discussion between three AI crew members and the user. The crew:
- Angelina (the writer): creative and imaginative; proposes ideas and pitches.
- Marcus (the director): critical and decisive; pushes back, challenges, sharpens the idea.
- Elena (the editor): practical and grounded; adds concrete notes, catches problems, wraps things up.

Given the conversation and the user's latest message, decide how the crew responds. Rules:
- They TAKE TURNS. Whoever the message is most relevant to speaks first.
- NOT everyone must respond. Include a crew member only if they genuinely have something to add. Often 1 or 2 speak, sometimes all three, occasionally none.
- They respond to EACH OTHER, not only the user (e.g. Angelina proposes, Marcus pushes back, Elena adds a practical note).
- Lines are SPOKEN ALOUD: keep each to 1-3 short, natural sentences. No markdown, no stage directions, no emojis.
- Stay in character.

Respond with ONLY a JSON object, no prose:
{"turns":[{"persona":"angelina|marcus|elena","text":"..."}]}
List the turns in speaking order. Use an empty list if nobody should respond."""


def _format_history(history: list) -> str:
    lines = []
    for h in (history or [])[-12:]:
        who = str(h.get("persona") or h.get("role") or "user").strip().lower()
        text = str(h.get("text") or h.get("content") or "").strip()
        if not text:
            continue
        label = who.capitalize() if who in CREW else "User"
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def _parse_turns(raw: str) -> list:
    if not raw:
        return []
    m = re.search(r"\{.*\}", raw, re.DOTALL)   # tolerate markdown fences / prose
    blob = m.group(0) if m else raw
    try:
        data = json.loads(blob)
    except Exception:
        return []
    turns = data.get("turns") if isinstance(data, dict) else None
    if not isinstance(turns, list):
        return []
    out = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        p = str(t.get("persona") or "").strip().lower()
        text = str(t.get("text") or "").strip()
        if p in CREW and text:
            out.append({"persona": p, "text": text})
        if len(out) >= 3:
            break
    return out


def orchestrate(message: str, history: list | None = None) -> list:
    """Return an ordered list of {persona, voice, text} turns for one message."""
    convo = _format_history(history or [])
    prompt = ((f"Conversation so far:\n{convo}\n\n" if convo else "")
              + f"User's latest message: {message}\n\nDirect the crew's response now.")
    try:
        raw, _meta = _call_llm_cluster(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": prompt}],
            timeout=30,
        )
    except Exception as exc:
        print(f"[ROUNDTABLE] orchestration failed: {exc}")
        return []
    turns = _parse_turns(raw)
    for t in turns:
        t["voice"] = voice_for_persona(t["persona"])
    return turns
