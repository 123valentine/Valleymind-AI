"""Music workstation operations — reference analysis, remix, extend, mastering
guidance and the Elena Music Co-Pilot.

Like the engine, every producer here returns real LLM creative output and never
claims rendered audio. Ops that need server-side audio bytes (an actual remix
file, separated stems, a mastered file, an AI vocal) are gated through the
provider in core.music.jobs; these functions produce the *plan* that such a job
(and the browser renderer, today) consumes.
"""

from __future__ import annotations

import json

from core.brain import _call_llm_cluster
from core.music import african, providers
from core.music.engine import _SYSTEM_PRODUCER, _as_float, _clamp01, _extract_json

_TIMEOUT = 45


def _llm_json(messages: list, timeout: int = _TIMEOUT) -> tuple[dict, str]:
    try:
        raw, _meta = _call_llm_cluster(messages, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {}, f"AI producer could not be reached: {exc}"
    data = _extract_json(raw)
    if not data:
        return {}, "AI produced no usable JSON (provider offline or malformed)."
    return data, ""


def _provider_caps() -> dict:
    try:
        return providers.get_provider().capabilities()
    except Exception:  # noqa: BLE001
        return {}


def _provider_note(_capability: str = "") -> str:
    try:
        return providers.get_provider().status().get("message", "")
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# Reference-track analysis
# --------------------------------------------------------------------------- #

_REFERENCE_SCHEMA = """Return JSON exactly:
{
  "analysis": {
    "genre": "likely genre",
    "bpm_range": [100, 120],
    "key": "likely key",
    "feel": "one-line feel description",
    "hallmarks": ["signature drum sound", "melodic device", "..."],
    "vocal_style": "how the topline sits",
    "production_notes": "arrangement/energy shape of the reference"
  },
  "blueprint": {
    "structure": "recommended structure for the new song",
    "bpm": 112,
    "key": "recommended key",
    "instruments": ["..."],
    "beatPattern": "16-char percussion string (K L k T t H h 0 -)",
    "bassRoot": "bass note (must match key)",
    "arrangement": "new production notes inspired by (not copying) the reference",
    "note": "explain how this draws from the reference without copying it, and what rendering still needs to happen"
  }
}"""


def analyze_reference(
    reference: str = "",
    *,
    genre_hint: str = "",
    analysis: dict | None = None,
    user_notes: str = "",
) -> dict:
    """Analyse a stated reference track (name/artist/genre) plus any
    browser-computed spectral facts, then produce a blueprint for an original
    song in that lane. Honest: audio-level transcription needs a provider."""
    analysis = analysis or {}
    client_facts = ""
    if analysis:
        parts = []
        if analysis.get("bpm"):
            parts.append(f"Detected BPM: {analysis.get('bpm')}")
        if analysis.get("key"):
            parts.append(f"Detected key: {analysis.get('key')}")
        if analysis.get("energy") is not None:
            parts.append(f"Detected energy: {float(analysis.get('energy', 0)):.2f}")
        if parts:
            client_facts = "Browser-computed audio facts:\n" + "\n".join(parts) + "\n"
    prompt = (
        "Reference track: " + (reference or "(unnamed)") + "\n"
        "User genre guess: " + (genre_hint or "not given") + "\n"
        + client_facts
        + ("Additional user notes: " + user_notes + "\n" if user_notes.strip() else "")
        + african.genre_context(genre_hint or (reference or "Afrobeats")) + "\n"
        + _REFERENCE_SCHEMA
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "status_verb": "error", "error": err,
                "analysis": {}, "blueprint": {}}
    return {
        "status": "success",
        "analysis": data.get("analysis", {}) if isinstance(data.get("analysis"), dict) else {},
        "blueprint": data.get("blueprint", {}),
        "provider_note": _provider_note("reference"),
    }


# --------------------------------------------------------------------------- #
# Remix
# --------------------------------------------------------------------------- #

_REMIX_SCHEMA = """Return JSON exactly:
{
  "title": "original title (if the original had one)",
  "remix_style": "the new lane — genre/mood/energy shift",
  "structure": "new structure",
  "bpm": 112,
  "key": "key",
  "instruments": ["..."],
  "beatPattern": "16-char percussion string (K L k T t H h 0 -)",
  "bassRoot": "bass note (must match key)",
  "arrangement": "how the remix reworks the original's sections",
  "sections": [{"id": "drop1", "section": "Drop", "length_bars": 8, "energy": 0.9, "notes": "..."}],
  "note": "honest note: plan vs. rendering; whether the remix needs the original stems/track"
}"""


def make_remix_plan(
    source_package: dict | None,
    *,
    instruction: str = "",
    genre: str = "",
    mood: str = "",
    tempo: str = "",
) -> dict:
    """Plan a remix of an existing track (its package/plan fields) into a new
    arrangement. Rendering the remix into audio is a provider step."""
    src = source_package or {}
    src_desc = {
        "title": src.get("title", ""),
        "genre": src.get("genre", ""),
        "mood": src.get("mood", ""),
        "tempo": src.get("tempo", ""),
        "bpm": src.get("bpm"),
        "key": src.get("key", ""),
        "structure": src.get("structure", ""),
        "arrangement": src.get("arrangement", ""),
        "instruments": src.get("instruments", []),
        "beatPattern": src.get("beatPattern", ""),
    }
    prompt = (
        "Source track package: " + json.dumps(src_desc, ensure_ascii=False) + "\n"
        "Remix instruction: " + (instruction or "Give it a fresh, club-ready energy") + "\n"
        "Requested genre: " + (genre or "keep the source's lane") + "\n"
        "Requested mood: " + (mood or "keep source mood") + "\n"
        "Requested tempo: " + (tempo or "keep source tempo") + "\n"
        + african.genre_context(genre or "Afrobeats") + "\n"
        + _REMIX_SCHEMA
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "error": err, "data": {}}
    if not data.get("bpm") and src_desc.get("bpm"):
        data["bpm"] = src_desc["bpm"]
    data.setdefault("beatPattern", african.resolve_pattern(genre or "afrobeats"))
    data.setdefault("bassRoot", african.bass_root_for_key(data.get("key")) or "A")
    return {"status": "success", "data": data,
            "provider_note": _provider_note("remix")}


# --------------------------------------------------------------------------- #
# Extend
# --------------------------------------------------------------------------- #

_EXTEND_SCHEMA = """Return JSON exactly:
{
  "extension": [{"id": "x1", "insert_after": "chorus2", "section": "Chorus", "length_bars": 8, "energy": 0.85, "notes": "..."}],
  "new_structure": "full updated structure string",
  "bpm": 112,
  "key": "key",
  "total_bars_estimate": 72,
  "arrangement_note": "how the extension slots into the song",
  "note": "honest note: this plans the longer form; extending audio requires rendering/providers"
}"""


def make_extend_plan(
    source_package: dict | None,
    *,
    target_seconds: int = 0,
    target_seconds_ui: int = 0,
    instruction: str = "",
) -> dict:
    """Plan an extension (longer arrangement) of an existing track/plan.
    Returns new sections to insert + an updated structure. Audio remains a
    provider step."""
    src = source_package or {}
    raw_secs = int(target_seconds or target_seconds_ui or 0)
    target = f"target finished length: {raw_secs} seconds" if raw_secs > 0 else "target length: user will say"
    src_desc = {
        "title": src.get("title", ""),
        "bpm": src.get("bpm"),
        "key": src.get("key", ""),
        "structure": src.get("structure", ""),
        "arrangement": src.get("arrangement", ""),
        "sections": src.get("sections", []),
        "lyrics_sections": src.get("lyrics") and "[has lyrics]",
    }
    prompt = (
        "Source track plan: " + json.dumps(src_desc, ensure_ascii=False) + "\n"
        + target + "\n"
        "Extension instruction: " + (instruction or "add a true intro, an extra verse/hook cycle and a big outro") + "\n"
        + _EXTEND_SCHEMA
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "error": err, "data": {}}
    if not data.get("bpm") and src_desc.get("bpm"):
        data["bpm"] = src_desc["bpm"]
    return {"status": "success", "data": data,
            "provider_note": _provider_note("extend")}


# --------------------------------------------------------------------------- #
# Mastering guidance
# --------------------------------------------------------------------------- #

_MASTER_SCHEMA = """Return JSON exactly:
{
  "grade": "one-letter grade (S/A/B/C) + short justification",
  "issues": [{"area": "loudness", "problem": "description", "fix": "what to change", "priority": "high|med|low"}],
  "settings": {
    "target_lufs": -14,
    "ceiling_db": -1.0,
    "eq_hint": "suggested tone shaping",
    "compression_hint": "suggested dynamics handling",
    "stereo_hint": "width guidance"
  },
  "note": "honest: these are AI recommendations for your mix, not a mastered audio file"
}"""


def master_notes(
    *,
    mix: dict | None = None,
    instruction: str = "",
) -> dict:
    """Review a mix's settings (client sends its Web-Audio mixer state) and
    return real mastering RECOMMENDATIONS. Actual mastered audio is a provider
    step (reported honestly as unavailable unless configured)."""
    mix = mix or {}
    src = {
        "master_volume": mix.get("master"),
        "volume": mix.get("vol"),
        "eq": mix.get("eq"),
        "fx": mix.get("fx"),
        "tracks": [
            {"kind": k, "vol": v.get("vol"), "pan": v.get("pan")}
            for k, v in (mix.get("tracks") or {}).items()
        ] if isinstance(mix.get("tracks"), dict) else [],
        "user_instruction": (instruction or "Give it radio-level loudness without squashing it"),
    }
    prompt = (
        "Mix settings from the client:\n" + json.dumps(src, ensure_ascii=False) + "\n"
        + _MASTER_SCHEMA
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "error": err}
    return {"status": "success", "mastering": data,
            "provider_note": _provider_note("master")}


# --------------------------------------------------------------------------- #
# Elena Music Co-Pilot
# --------------------------------------------------------------------------- #

_COPILOT_INTENTS = [
    "create", "reference", "remix", "extend", "vocal_lab", "sing",
    "stems", "master", "lyrics", "edit", "play", "save", "export", "help", "other",
]

_COPILOT_SCHEMA = """Return JSON exactly:
{
  "intent": "one of: create reference remix extend vocal_lab sing stems master lyrics edit play save export help other",
  "params": {"brief": "...", "genre": "...", "mood": "...", "tempo": "...", "key": "...",
             "language": "...", "instruction": "...", "reference": "...", "lyrics": "...", "target_seconds": 0},
  "reply": "what you will do now — one or two sentences, plain, honest",
  "limitations": ["...only if you cannot do something the user asked, e.g. stems need a provider"]
}"""


def elena_copilot(
    message: str,
    *,
    context: dict | None = None,
    history: list | None = None,
) -> dict:
    """Elena Music Co-Pilot: translate natural language into a structured music
    operation the workstation can execute. Honest about limits via the current
    provider's capability report."""
    context = context or {}
    caps = _provider_caps()
    cap_line = (
        "Capabilities active right now:\n"
        "- Creative (works): create, reference analysis, remix/extend plans, "
        "lyrics, production plans, mastering recommendations.\n"
        "- Server-side audio rendering: "
        + ("AVAILABLE" if caps.get("server_audio") else "UNAVAILABLE")
        + ". Stems, mastered files and server-rendered mixes require it; "
        "today the browser renders what the plans describe.\n"
    )
    ctx_line = ""
    if context:
        ctx_line = (
            "User is in a project:\n"
            + json.dumps({k: v for k, v in context.items()
                          if k in ("title", "genre", "mood", "bpm", "key", "structure")},
                         ensure_ascii=False, default=str)
            + "\n"
        )
    history_line = ""
    if isinstance(history, list) and history:
        history_line = "Recent conversation:\n" + "\n".join(
            f"user: {t.get('role','')}: {t.get('content','')}" for t in history[-4:]
        ) + "\n"
    prompt = (
        "The user is speaking to the Music Co-Pilot inside ValleyMind Studio.\n"
        + cap_line + ctx_line + history_line
        + "User message: " + message + "\n"
        + _COPILOT_SCHEMA
    )
    data, err = _llm_json([
        {"role": "system", "content":
            "You are Elena, ValleyMind's Music Co-Pilot. You listen to what the "
            "user wants musically and translate it into a structured operation "
            "for the workstation. You are warm, brief and never overstate what "
            "the tool can do. Return ONLY the JSON described, no fences."},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "error": err}
    intent = str(data.get("intent") or "other").lower()
    if intent not in _COPILOT_INTENTS:
        intent = "other"
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    return {
        "status": "success",
        "intent": intent,
        "params": params,
        "reply": str(data.get("reply") or "Got it — tell me more and I'll set it up."),
        "limitations": data.get("limitations") or [],
        "suggested_action": _suggested_action(intent, params),
    }


def _suggested_action(intent: str, params: dict) -> str:
    """Best client-side route for the intent (used by the studio UI to actually
    DO something instead of just chatting)."""
    mapping = {
        "create": "/api/music/generate",
        "reference": "/api/music/reference",
        "remix": "/api/music/remix",
        "extend": "/api/music/extend",
        "vocal_lab": "/api/music/sing",
        "sing": "/api/music/sing",
        "stems": "/api/music/stems",
        "master": "/api/music/master",
        "lyrics": "/api/music/generate",
        "edit": "/api/music/ai-edit",
    }
    return mapping.get(intent, "")