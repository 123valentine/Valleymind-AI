"""Music engine — the creative brain + facade over the active provider.

"Producer" functions here are REAL AI output via the shared LLM cluster.
Nothing in this module ever claims to have rendered audio: rendering is the
exclusive province of the provider layer, which is honest about when it can't.

Public face (used by routes):
    produce_package(...)        Advanced Create creative package
    make_production_plan(...)   Sing-with-AI / Vocal Lab production plan
    refine_production_plan(...) stepwise refinement of a plan
"""

from __future__ import annotations

import json

from core.brain import _call_llm_cluster
from core.music import african
from core.music import providers

_SYSTEM_PRODUCER = (
    "You are ValleyMind's senior music producer (Nigerian/African focus). You "
    "write real creative material — lyrics, structures, arrangements, plans. "
    "You NEVER claim to have rendered, mastered, mixed or generated audio "
    "files: audio rendering is a separate engine step. Return ONLY valid JSON "
    "with no markdown fences, no prose outside the JSON."
)

_TIMEOUT = 45


def _extract_json(raw: str) -> dict:
    """Best-effort JSON extraction from an LLM reply (fences/trailing prose)."""
    if not raw:
        return {}
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _llm_json(messages: list, timeout: int = _TIMEOUT) -> tuple[dict, str]:
    """Call the cluster and parse JSON. Returns (data, error)."""
    try:
        raw, _meta = _call_llm_cluster(messages, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {}, f"AI producer could not be reached: {exc}"
    data = _extract_json(raw)
    if not data:
        return {}, "AI produced no usable JSON (provider offline or malformed)."
    return data, ""


def _as_float(value, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _pkg(kind: str, data: dict, *, provider_note: str = "") -> dict:
    """Wrap a produced package with honest rendering info."""
    out = {"kind": kind, "status": "success", "data": data,
           "server_audio": False, "audio_available": False}
    if provider_note:
        out["note"] = provider_note
    return out


def _voice_hint(voice: str) -> str:
    v = (voice or "").strip().lower()
    if "clone" in v:
        return "\nVoice: use the user's recorded voice (authorized AI clone of their own voice)."
    if "elena" in v:
        return "\nVoice: use ValleyMind's approved AI singing voice, Elena."
    return "\nVoice: keep and gently enhance the user's own recorded vocal."


def _provider_note(_capability: str = "") -> str:
    try:
        return providers.get_provider().status().get("message", "")
    except Exception:  # noqa: BLE001
        return ""


def _apply_genre_defaults(data: dict, genre: str) -> None:
    """Fill gaps with genre-registry constants (real musical defaults, never
    invented numbers)."""
    if not data.get("bpm"):
        data["bpm"] = african.resolve_bpm(genre)
    if not data.get("beatPattern") and not data.get("pattern"):
        data["beatPattern"] = african.resolve_pattern(genre)
    if not data.get("instruments"):
        data["instruments"] = african.resolve_instruments(genre)
    if not data.get("bassRoot"):
        data["bassRoot"] = african.bass_root_for_key(data.get("key", "")) or "A"


_CREATE_SCHEMA = """Return JSON exactly:
{
  "title": "song title",
  "lyrics": "full singable lyrics with [Verse] [Chorus] [Bridge] section labels",
  "structure": "e.g. Intro-Verse-Chorus-Verse2-Chorus-Bridge-Outro",
  "bpm": 112,
  "key": "e.g. C minor",
  "instruments": ["Log drums", "Shakers", "..."],
  "beatPattern": "16-char pattern using K L k T t H h 0 -",
  "bassRoot": "bass note the track centers on (must match the key)",
  "sections": [{"id": "verse1", "section": "Verse", "length_bars": 8, "energy": 0.5, "notes": "..."}],
  "arrangement": "concise production notes (drops, fills, vocal-friendly moments)",
  "production_note": "what this producer produced now vs. what final audio rendering (a future step) will add"
}
Cover Intro/Verse/Chorus/etc. in the sections array (per-section bar counts that
cohere into a real song; energy 0..1)."""


def produce_package(
    brief: str = "",
    *,
    role: str = "singer",
    genre: str = "Afrobeats",
    mood: str = "",
    tempo: str = "medium",
    key: str = "",
    language: str = "English",
    voice: str = "",
    lyrics: str = "",
    subgenre: str = "",
    bpm: int | float | None = None,
    structure: str = "",
    energy: float | None = None,
    instrumental: bool = False,
    negative: str = "",
    duration: int | float | None = None,
    time_signature: str = "",
    lyrics_mode: str = "",
) -> dict:
    """Advanced-Create: full creative package (lyrics + arrangement + plan).

    Real LLM output; carries no audio bytes (``audio_available`` stays False
    because rendering is a provider step). Extra professional controls are
    folded into the producer prompt so Simple and Advanced mode share one
    generator."""
    genre_ctx = african.genre_context(genre)
    label = (african.GENRES_BY_ID.get(african.resolve_genre(genre), {}).get("label")
             or genre)
    controls = []
    if subgenre.strip():
        controls.append("Subgenre: " + subgenre.strip())
    if time_signature.strip():
        controls.append("Time signature: " + time_signature.strip())
    if bpm:
        controls.append(f"Target BPM: {int(float(bpm))} (exact)")
    if energy is not None:
        controls.append(f"Target energy 0..1: {_clamp01(_as_float(energy, 0.5)):.2f}")
    if duration:
        controls.append(f"Target duration: {int(float(duration))} seconds")
    if instrumental:
        controls.append("Instrumental only — NO lead vocals; write a full "
                        "instrumental arrangement with a melody line.")
    if negative.strip():
        controls.append("Exclude (negative prompt): " + negative.strip())
    if lyrics_mode.strip():
        controls.append("Lyrics mode: " + lyrics_mode.strip())
    controls_line = ("\n".join(controls) + "\n") if controls else ""

    if instrumental and not lyrics.strip():
        lyrics_block = ("\nInstrumental track — provide arrangement/section notes "
                        "instead of sung lyrics (you may include short cue "
                        "labels).\n")
    else:
        lyrics_block = ("\nUser already wrote lyrics — keep and refine structure "
                        "around them:\n" + lyrics
                        if lyrics.strip() else
                        "\nNo lyrics yet — write full singable lyrics.\n")

    prompt = (
        "Song brief: " + (brief.strip() or "An original Afrobeats-influenced song") + "\n"
        "Role: " + (role or "singer") + "\n"
        "Genre: " + label + "\n"
        "Mood: " + (mood or "infer a fitting mood") + "\n"
        "Tempo (desired): " + (tempo or "medium") + "\n"
        "Key: " + (key or "suggest one from the genre") + "\n"
        + controls_line
        + (("User structure: " + structure.strip() + "\n") if structure.strip() else "")
        + african.lyric_language_context(language) + "\n"
        + _voice_hint(voice) + "\n"
        + lyrics_block
        + "\n" + genre_ctx + "\n"
        + _CREATE_SCHEMA
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"kind": "create", "status": "error", "error": err, "data": {},
                "audio_available": False, "server_audio": False}
    _apply_genre_defaults(data, genre)
    return _pkg("create", data, provider_note=_provider_note("generate"))


_PLAN_SCHEMA = """Produce the production plan as JSON exactly like this:
{
  "title": "a fitting song title",
  "bpm": 112,
  "key": "C minor",
  "structure": "Intro-Verse-Chorus-Verse2-Chorus-Bridge-Outro (adapt to the vocal, not forced)",
  "instruments": ["Drums", "Bass", "Keys/Pads", "..."],
  "beatPattern": "16-char string: K (kick) L (soft kick) k T (snare/clap) t H (open hat) h (closed hat) 0 (hat tick) - (rest)",
  "bassRoot": "note letter+octave to center the bass (must match the key)",
  "energyMap": "short note on where energy builds (verses vs chorus) for the client to arrange",
  "arrangement": "concise production notes (feel, dynamics, drop/fills, vocal-friendly moments)",
  "note": "honest note: what is planned now vs. what a future server-side audio renderer would add"
}
If the analysis confidence is low, make sensible musical defaults and say so in the note."""


def make_production_plan(
    analysis: dict,
    *,
    genre: str = "Afrobeats",
    mood: str = "",
    style_text: str = "",
    structure: str = "",
    key_override: str = "",
) -> dict:
    """Vocal Lab / Sing-with-AI producer: rough-vocal analysis -> production
    plan the client renders into real audio (same contract as /api/music/sing)."""
    bpm = _as_float(analysis.get("bpm"))
    est_key = str(analysis.get("key") or "").strip()
    est_mood = str(analysis.get("mood") or "").strip()
    energy = _clamp01(_as_float(analysis.get("energy"), 0.5))
    dur = _as_float(analysis.get("duration"))
    conf = _clamp01(_as_float(analysis.get("confidence")))
    sections = analysis.get("sections")
    use_key = key_override.strip() or est_key
    use_mood = mood.strip() or est_mood

    prompt = (
        "Uploaded vocal analysis:\n"
        f"BPM (estimate): {int(round(bpm)) if bpm else 'not detected'}\n"
        f"Key (estimate/override): {use_key or 'not detected'}\n"
        f"Energy (0..1): {energy:.2f}\n"
        f"Duration (sec): {int(round(dur)) if dur else 'unknown'}\n"
        f"Confidence (0..1): {conf:.2f}\n"
        f"Sections (approx): {sections if sections else 'none detected'}\n"
        f"Song structure (user): {structure.strip() or 'adapt to the vocal'}\n\n"
        "User style:\n"
        f"Genre: {genre or 'Afrobeats'}\n"
        f"Mood: {use_mood or 'infer from vocal'}\n"
        f"Production style: {style_text.strip() or '(none)'}\n"
        + african.genre_context(genre) + "\n"
        + _PLAN_SCHEMA
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "error": err, "plan": {}}
    if not data.get("bpm") and bpm:
        data["bpm"] = int(round(bpm))
    data["sourceBpm"] = int(round(bpm)) if bpm else (data.get("bpm") or 100)
    data.setdefault("beatPattern", african.resolve_pattern(genre))
    data.setdefault("bassRoot", african.bass_root_for_key(data.get("key")) or "A")
    data.setdefault("instruments", african.resolve_instruments(genre))
    return {"status": "success", "plan": data}


def refine_production_plan(
    instruction: str,
    current_plan: dict | None,
    *,
    genre: str = "",
) -> dict:
    """Refine an existing production plan without restarting (Vocal Lab step)."""
    import json as _json

    plan_json = _json.dumps(current_plan or {}, ensure_ascii=False, default=str)
    prompt = (
        "Current genre: " + (genre or "not set") + "\n"
        "Current plan: " + plan_json + "\n"
        "User instruction: " + instruction + "\n\n"
        "Return JSON with ONLY the fields that changed:\n"
        '{"bpm":"...if changed","key":"...if changed","structure":"...if changed",'
        '"instruments":"[...] if changed","beatPattern":"...if changed",'
        '"bassRoot":"...if changed","energyMap":"...if changed",'
        '"arrangement":"...if changed","filename_hint":"...if changed",'
        '"summary":"what changed in one line"}'
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "error": err, "changes": {}}
    return {"status": "success", "changes": data,
            "summary": data.pop("summary", "")}


# --------------------------------------------------------------------------- #
# Single-track generation (AI Track Generation)
# --------------------------------------------------------------------------- #

_TRACK_SCHEMA = """Return JSON exactly:
{
  "track_type": "drums|bass|keys|guitar|synth|percussion|pads|fx|strings|brass",
  "name": "short human name for the part",
  "beatPattern": "16-char percussion pattern (K L k T t H h 0 -) — meaningful for percussive parts",
  "bassRoot": "bass note if the part is tonal (must match the key)",
  "notes": "how the part should be played (register, rhythm, articulation)",
  "midi_sketch": "optional short note/rhythm description a player or synth can follow",
  "bar_length": 8,
  "mix_note": "relative level/pan suggestion for this part",
  "note": "honest note: this is a part plan; rendering the audio is a provider step"
}"""


def produce_track(
    track_type: str,
    *,
    brief: str = "",
    genre: str = "Afrobeats",
    bpm: int | float | None = None,
    key: str = "",
    bars: int = 8,
    mood: str = "",
    detail: str = "",
) -> dict:
    """AI Track Generation: plan a single instrumental part the user can drop
    into the timeline. Real LLM output; audio rendering stays provider-gated."""
    part = (track_type or "drums").strip().lower()
    prompt = (
        "Generate ONE instrumental track part for an existing song.\n"
        f"Part type: {part}\n"
        f"Song genre: {genre} ({african.genre_context(genre)})\n"
        f"Song mood: {mood or 'unspecified'}\n"
        f"Target BPM: {int(float(bpm)) if bpm else african.resolve_bpm(genre)}\n"
        f"Key: {key or 'match the song'}\n"
        f"Length: {int(bars) if bars else 8} bars\n"
        + (f"Extra direction: {detail.strip()}\n" if detail.strip() else "")
        + (f"Song context: {brief.strip()}\n" if brief.strip() else "")
        + _TRACK_SCHEMA
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "error": err, "data": {}}
    data.setdefault("track_type", part)
    data.setdefault("bpm", int(float(bpm)) if bpm else african.resolve_bpm(genre))
    data.setdefault("beatPattern", african.resolve_pattern(genre))
    data.setdefault("bassRoot", african.bass_root_for_key(key or "") or "A")
    data.setdefault("bar_length", int(bars) if bars else 8)
    return _pkg("track", data, provider_note=_provider_note("generate"))


# --------------------------------------------------------------------------- #
# Lyrics Lab
# --------------------------------------------------------------------------- #

_LYRICS_ACTIONS = {
    "generate": "Write brand-new, complete, singable lyrics for the song.",
    "rewrite": "Rewrite the lyrics while keeping the story and section layout.",
    "continue": "Continue the lyrics naturally from where they stop.",
    "rhyme": "Change the rhyme scheme as instructed (e.g. AABB, ABAB).",
    "translate": "Translate the lyrics into the requested target language, "
                 "keeping them singable.",
    "emotional": "Rewrite to be more emotionally moving.",
    "poetic": "Rewrite to be more poetic and imagery-rich.",
    "simplify": "Simplify the language so it is easy to sing and remember.",
    "igbo": "Add natural Igbo lines/wording alongside the existing language.",
    "pidgin": "Add natural Nigerian Pidgin alongside the existing language.",
    "mix": "Blend English, Igbo and Nigerian Pidgin the way contemporary "
           "Nigerian songwriters do — natural, not forced.",
}


def lyrics_lab(
    text: str = "",
    *,
    action: str = "generate",
    instruction: str = "",
    language: str = "English",
    target_language: str = "",
    genre: str = "Afrobeats",
    mood: str = "",
    title: str = "",
) -> dict:
    """AI Lyrics Lab — generate / rewrite / continue / translate / flavour."""
    act = (action or "generate").strip().lower()
    if act not in _LYRICS_ACTIONS:
        act = "generate"
    prompt = (
        f"Song title: {title or 'untitled'}\n"
        f"Genre: {genre}\nMood: {mood or 'unspecified'}\n"
        f"Primary language: {language}\n"
        + (f"Target language: {target_language}\n" if target_language else "")
        + f"Task: {_LYRICS_ACTIONS[act]}\n"
        + (f"User instruction: {instruction}\n" if instruction.strip() else "")
        + (f"\nCurrent lyrics:\n{text}\n" if text.strip() else "\nNo lyrics yet.\n")
        + african.lyric_language_context(language) + "\n\n"
        "Return JSON exactly:\n"
        '{"title":"a fitting title","lyrics":"the full lyrics with [Verse] '
        '[Chorus] [Bridge] labels","summary":"one line on what you did"}'
    )
    data, err = _llm_json([
        {"role": "system", "content": _SYSTEM_PRODUCER},
        {"role": "user", "content": prompt},
    ])
    if err:
        return {"status": "error", "error": err, "data": {}}
    return {
        "status": "success",
        "action": act,
        "title": data.get("title") or title or "Untitled",
        "lyrics": data.get("lyrics", ""),
        "summary": data.get("summary", ""),
    }