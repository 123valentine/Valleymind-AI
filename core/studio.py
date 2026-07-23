"""ValleyMind Studio pipeline — Angelina (script) -> Marcus (scenes) -> storyboard.

One user idea drives all three stages. Each persona keeps its own personality
(loaded from character/<name>/behavior.json) and knows the rest of the crew, so
they reference each other by name naturally.

Continuity is the point: Angelina produces a character sheet alongside the
script, and that sheet is threaded into Marcus's scene breakdown AND into every
storyboard image prompt, so names, appearance and wardrobe never drift between
scenes.

Video generation is deliberately NOT part of this pipeline — the global
VIDEO_GENERATION_ENABLED kill switch stays authoritative and untouched.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator

from core.character import load_character_profile
from core.config import PROJECT_ROOT

MAX_SCENES = 6  # legacy default; the real ceiling is max_scenes()


def max_scenes() -> int:
    """Upper bound on scenes (= storyboards = clips) per run. Raised well above
    the old hard 6 so a 90s–5min trailer can be built; env-overridable."""
    try:
        return max(1, min(60, int(os.getenv("STUDIO_MAX_SCENES", "24"))))
    except (TypeError, ValueError):
        return 24


# ── The crew are the filmmakers, never the cast ─────────────────────────────
# Each stage injects the persona's own behaviour prompt ("you are Angelina, the
# writer... Elena is the editor"), which invited the model to treat the crew as
# available characters — real runs produced "INT. ELENA'S EDITING BAY" and
# "INT. MARCUS'S DIRECTOR'S CHAIR" as actual scenes. This guard is injected into
# every writing/directing stage to shut that down.
_CREW_GUARD = (
    "\n\nHARD RULE — YOU ARE THE FILMMAKER, NOT A CHARACTER IN THE FILM.\n"
    "Angelina, Marcus and Elena are the real production crew making this piece. They are "
    "NEVER characters, never on screen, and never referenced in the story. The film is "
    "about the USER'S subject and nothing else.\n"
    "- Never write a scene set in an editing bay, edit suite, director's chair, control room, "
    "monitor bank, film set, sound stage, production office, screening room or any other "
    "filmmaking space.\n"
    "- Never name a character Angelina, Marcus or Elena.\n"
    "- Never depict anyone filming, editing, reviewing footage, or watching this film. No "
    "behind-the-scenes framing, no 'meanwhile in the edit' cutaways, no screens showing the "
    "film itself.\n"
    "ONLY EXCEPTION: the user explicitly asked for a film ABOUT filmmaking. If they did not "
    "say so, treat every filmmaking setting as forbidden."
)

# Safety net for when the model ignores the guard anyway.
_PRODUCTION_PAT = re.compile(
    r"\b(editing bay|edit bay|edit suite|editing suite|director'?s chair|cutting room|"
    r"control room|monitor bank|sound stage|soundstage|film set|movie set|production office|"
    r"screening room|projection room|behind[- ]the[- ]scenes|video village|"
    r"(?:bank|wall|row)s? of (?:monitors|screens))\b",
    re.IGNORECASE,
)
_CREW_NAME_PAT = re.compile(r"\b(angelina|marcus|elena)\b", re.IGNORECASE)
# Only honour a filmmaking setting when the user actually asked for one.
_FILMMAKING_IDEA_PAT = re.compile(
    r"\b(filmmak\w*|film crew|movie about (?:a )?(?:film|movie)|documentary about (?:a )?film|"
    r"behind[- ]the[- ]scenes|editor|film school|director|cinematographer|on set|movie studio)\b",
    re.IGNORECASE,
)


def idea_is_about_filmmaking(idea: str) -> bool:
    """True when the user genuinely asked for a film about filmmaking, which is
    the one case where production settings are legitimate."""
    return bool(_FILMMAKING_IDEA_PAT.search(str(idea or "")))


def scene_breaks_crew_rule(scene: dict) -> bool:
    """Does this scene put the crew or a production setting on screen?"""
    blob = " ".join(str(scene.get(k, "")) for k in ("title", "description", "action", "setting"))
    return bool(_PRODUCTION_PAT.search(blob) or _CREW_NAME_PAT.search(blob))


def _persona_prompt(key: str) -> str:
    """Load a persona's own system prompt so personality survives the pipeline."""
    try:
        path = PROJECT_ROOT / "character" / key / "behavior.json"
        profile = load_character_profile(str(path), key)
        return profile.to_prompt()
    except Exception:
        return ""


def _parse_json_block(raw: str) -> Any:
    """Pull a JSON object/array out of an LLM reply, tolerating fences/prose."""
    cleaned = str(raw or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass
    match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _sheet_to_text(sheet: dict) -> str:
    """Flatten the character sheet for prompt reuse and for display."""
    if not isinstance(sheet, dict):
        return ""
    lines = []
    for ch in sheet.get("characters", []) or []:
        if not isinstance(ch, dict):
            continue
        name = str(ch.get("name", "")).strip()
        appearance = str(ch.get("appearance", "")).strip()
        wardrobe = str(ch.get("wardrobe", "")).strip()
        bits = [b for b in (appearance, wardrobe) if b]
        if name:
            lines.append(f"{name}: {'; '.join(bits)}" if bits else name)
    return "\n".join(lines)


# ── Phase 0: conversational intake (before anything is generated) ────────────

_GO_RE = re.compile(
    r"\b(go|start|begin|make it|just make|let'?s go|do it|roll it|action|"
    r"produce it|shoot it|that'?s enough|good to go|ready)\b",
    re.IGNORECASE,
)


def user_said_go(text: str) -> bool:
    return bool(_GO_RE.search(str(text or "")))


def _intake_system() -> str:
    return (
        "You run the intake desk for ValleyMind Studio, a three-person film crew:\n"
        "- Angelina, the writer — owns story, characters, their names, dialogue, tone.\n"
        "- Marcus, the director and senior of the three — owns the visual look, camera "
        "style, mood, and length.\n"
        "- Elena, the editor — pacing and assembly.\n\n"
        "You are talking to a user BEFORE any script or image is made. Nothing is "
        "generated during intake. Decide what to do with their latest message:\n\n"
        "1. If they are just greeting or chatting and have NOT given a film idea, reply "
        "warmly in a crew member's voice and invite them to share an idea. mode='greeting'. "
        "Do NOT start making anything.\n"
        "2. If they gave an idea but important things are still unclear, ask exactly ONE "
        "natural question, in the voice of the RIGHT crew member — Angelina for "
        "story/characters/names/tone, Marcus for look/camera-style/length. Offer 2-4 short "
        "tap-able answer options. mode='gathering'.\n"
        "3. If there is enough to begin (an idea plus at least a rough sense of the "
        "characters and the tone), OR the user clearly says to go, reply briefly in "
        "Marcus's voice that the crew is ready, and write a consolidated creative brief. "
        "mode='ready'.\n\n"
        "Speak in-character and human, never like a form. Never write the actual script or "
        "scene list here.\n\n"
        "Respond with ONLY this JSON:\n"
        '{"mode":"greeting|gathering|ready","persona":"Angelina|Marcus|Elena",'
        '"reply":"<what the crew member says>","quick_replies":["...","..."],'
        '"brief":"<the accumulated creative brief; required when mode=ready>"}'
    )


def intake_messages(history: list[dict], latest: str) -> list[dict]:
    lines = []
    for h in (history or []):
        who = h.get("persona") or ("User" if h.get("role") == "user" else "Crew")
        lines.append(f"{who}: {h.get('text','')}")
    convo = "\n".join(lines) if lines else "(no messages yet)"
    return [
        {"role": "system", "content": _intake_system()},
        {"role": "user", "content": f"Conversation so far:\n{convo}\n\nUser's latest message: {latest}\n\nReturn the JSON decision."},
    ]


_DEFAULT_QUESTION = {
    "Angelina": "Tell me about the story — who's in it, and what happens to them?",
    "Marcus": "What look are you after — the mood, the pace, and roughly how long?",
    "Elena": "What feeling should it leave the viewer with?",
}
_DEFAULT_CHIPS = ["Give it more detail", "Just surprise me", "Keep it short", "Go now"]


def parse_intake(raw: str, force_ready: bool = False) -> dict:
    parsed = _parse_json_block(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    mode = str(parsed.get("mode", "")).strip().lower()
    if mode not in ("greeting", "gathering", "ready"):
        mode = "gathering"
    persona = str(parsed.get("persona", "") or "Marcus").strip().title()
    if persona not in ("Angelina", "Marcus", "Elena"):
        persona = "Marcus"
    reply = str(parsed.get("reply", "") or "").strip()
    quick = [str(q).strip() for q in (parsed.get("quick_replies") or []) if str(q).strip()][:4]
    brief = str(parsed.get("brief", "") or "").strip()

    if force_ready:
        mode = "ready"
        persona = "Marcus"
        reply = reply or "Alright — we've got enough to start. Rolling now."
    # Never surface a blank turn to the user.
    if not reply:
        if mode == "greeting":
            reply = "Hey — welcome to the Studio. What film do you want to make?"
        elif mode == "ready":
            reply = "We've got enough to start. Rolling now."
        else:
            reply = _DEFAULT_QUESTION.get(persona, _DEFAULT_QUESTION["Marcus"])
    if mode == "gathering" and not quick:
        quick = _DEFAULT_CHIPS
    return {"mode": mode, "persona": persona, "reply": reply, "quick_replies": quick, "brief": brief}


# ── Stage 1: Angelina writes ────────────────────────────────────────────────

def script_messages(idea: str) -> list[dict]:
    return [
        {"role": "system", "content": (
            _persona_prompt("angelina")
            + "\n\nYou are writing for ValleyMind Studio. Write a short screenplay for the "
              "user's idea: a logline, then the scenes in order with action lines and dialogue. "
              "Keep it tight and shootable — this will be broken into a handful of scenes and "
              "storyboarded. Give every character a specific, memorable look you can keep "
              "consistent. Write in prose/screenplay form only — no JSON, no preamble, no "
              "meta-commentary about the task."
            + _CREW_GUARD
        )},
        {"role": "user", "content": idea},
    ]


def character_sheet_messages(idea: str, script: str) -> list[dict]:
    return [
        {"role": "system", "content": (
            "Extract a character sheet from this screenplay. Respond with ONLY a JSON object:\n"
            '{"characters":[{"name":"...","appearance":"age, build, hair, face — concrete visual details",'
            '"wardrobe":"what they wear, specific"}],"look":"one line on the overall visual style/palette"}\n'
            "Be concrete and visual — these descriptions are reused verbatim in every image prompt, "
            "so they must be specific enough that the same person is recognisable across scenes. "
            "No markdown, no explanation."
        )},
        {"role": "user", "content": f"IDEA: {idea}\n\nSCREENPLAY:\n{script[:6000]}"},
    ]


# ── Stage 2: Marcus directs ─────────────────────────────────────────────────

def _notes_block(notes: list[str] | None) -> str:
    """Late direction the user typed mid-run — applied on top of everything."""
    notes = [n for n in (notes or []) if str(n).strip()]
    if not notes:
        return ""
    joined = "\n".join(f"- {n}" for n in notes)
    return (
        "\n\nLATE DIRECTION FROM THE USER — these override earlier choices and "
        f"must be applied:\n{joined}"
    )


def scene_messages(idea: str, script: str, sheet_text: str, notes: list[str] | None = None,
                   target: int | None = None, duration: int | None = None) -> list[dict]:
    n = target or max_scenes()
    runtime = f" for a piece running about {duration} seconds" if duration else ""
    return [
        {"role": "system", "content": (
            _persona_prompt("marcus")
            + f"\n\nYou are directing this for ValleyMind Studio{runtime}. Choose EXACTLY {n} "
              f"scenes — never more than {max_scenes()}.\n"
              "This is a TRAILER, not full coverage. Do NOT try to retell the whole plot in "
              f"{n} shots. Pick the {n} STRONGEST moments — the images with the most tension, "
              "motion or emotion — and let the gaps between them do the work. A trailer implies; "
              "it does not summarise. Skip connective tissue, establishing filler and anything "
              "that exists only to explain.\n"
              "For each scene give the visual description, "
              "the camera angle, and the framing. Reuse the character sheet's descriptions exactly — "
              "the same person must look the same in every scene.\n\n"
              "Respond with ONLY a JSON array:\n"
              '[{"number":1,"title":"short scene title","description":"what we see, visually concrete",'
              '"camera":"lens/movement, e.g. 50mm slow push","framing":"e.g. medium close-up, low angle"}]\n'
              "No markdown, no commentary."
            + _CREW_GUARD
            + _notes_block(notes)
        )},
        {"role": "user", "content": (
            f"IDEA: {idea}\n\nCHARACTER SHEET:\n{sheet_text}\n\nANGELINA'S SCREENPLAY:\n{script[:6000]}"
        )},
    ]


# ── Clarifying question (asked back to the user when genuinely ambiguous) ────

def clarify_messages(idea: str, script: str) -> list[dict]:
    return [
        {"role": "system", "content": (
            _persona_prompt("marcus")
            + "\n\nYou are about to direct this. If something genuinely important is ambiguous "
              "and would change how you shoot it (setting, era, tone, who the lead is), ask the "
              "user ONE short question. If nothing important is unclear, say nothing.\n\n"
              'Respond with ONLY JSON: {"question":"<one short question, or empty string>"}'
        )},
        {"role": "user", "content": f"IDEA: {idea}\n\nSCREENPLAY:\n{script[:3000]}"},
    ]


def parse_question(raw: str) -> str:
    parsed = _parse_json_block(raw)
    if isinstance(parsed, dict):
        q = str(parsed.get("question", "") or "").strip()
        # Guard against the model returning filler instead of a real question
        return q if len(q) > 5 else ""
    return ""


# ── Stage 3: storyboard prompt per scene ────────────────────────────────────

def storyboard_prompt(scene: dict, sheet_text: str, look: str = "", notes: list[str] | None = None) -> str:
    """Build one image prompt from the scene + the shared character sheet."""
    parts = [
        "Cinematic film storyboard frame.",
        str(scene.get("description", "")).strip(),
    ]
    cam = str(scene.get("camera", "")).strip()
    fr = str(scene.get("framing", "")).strip()
    if fr:
        parts.append(f"Framing: {fr}.")
    if cam:
        parts.append(f"Camera: {cam}.")
    if sheet_text:
        parts.append(f"Characters (keep consistent): {sheet_text}")
    if look:
        parts.append(f"Visual style: {look}")
    for n in (notes or []):
        n = str(n).strip()
        if n:
            parts.append(n)
    return " ".join(p for p in parts if p)[:1200]


def max_clips() -> int:
    """How many scenes get animated into clips.

    Each image-to-video call runs for minutes, so the default is deliberately
    small to keep a Studio run inside the request window. Raise
    STUDIO_MAX_CLIPS once runs move to a background queue.
    """
    try:
        return max(0, int(os.getenv("STUDIO_MAX_CLIPS", "2")))
    except (TypeError, ValueError):
        return 2


def t2v_prompt(scene: dict, sheet_text: str = "", look: str = "",
               notes: list[str] | None = None) -> str:
    """Full prompt for TEXT-to-video (the default path).

    Unlike the i2v motion prompt, nothing upstream carries the look here, so the
    whole shot must be described: the ACTION first (what physically happens),
    then who is in it, then style and camera. Action leads because a camera-only
    prompt renders a static plate.
    """
    parts = []
    action = str(scene.get("action", "")).strip()
    desc = str(scene.get("description", "")).strip()
    if action:
        parts.append(action)
    if desc and desc != action:
        parts.append(desc)
    background = str(scene.get("background", "")).strip()
    if background:
        parts.append(background)
    if sheet_text:
        parts.append(f"Characters (keep consistent): {sheet_text}")
    if look:
        parts.append(f"Visual style: {look}")
    fr = str(scene.get("framing", "")).strip()
    if fr:
        parts.append(f"Framing: {fr}")
    cam = str(scene.get("camera", "")).strip()
    if cam:
        parts.append(f"Camera: {cam}")
    parts.append("live action, cinematic, natural motion")
    for n in (notes or []):
        n = str(n).strip()
        if n:
            parts.append(n)
    return ". ".join(p.rstrip(". ") for p in parts if p)[:1600]


def clip_prompt(scene: dict, notes: list[str] | None = None) -> str:
    """Motion direction for image-to-video. The still already carries the look,
    so this describes MOVEMENT only — restating the scene invites the model to
    redraw the characters and break continuity."""
    parts = []
    cam = str(scene.get("camera", "")).strip()
    if cam:
        parts.append(cam)
    parts.append("subtle natural motion, cinematic, keep the subject's appearance unchanged")
    for n in (notes or []):
        n = str(n).strip()
        if n:
            parts.append(n)
    return " ".join(parts)[:800]


def normalize_scenes(parsed: Any, target: int | None = None,
                     allow_filmmaking: bool = False) -> list[dict]:
    """Coerce the model's scene output into a clean, capped list.

    Scenes that put the crew or a production setting on screen are dropped
    unless the user actually asked for a film about filmmaking — the prompt
    guard is the primary defence, this is the safety net.
    """
    if isinstance(parsed, dict):
        parsed = parsed.get("scenes") or parsed.get("Scenes") or []
    if not isinstance(parsed, list):
        return []
    cap = target or max_scenes()
    if not allow_filmmaking:
        kept = []
        for raw in parsed:
            if isinstance(raw, dict) and scene_breaks_crew_rule(raw):
                print(f"[STUDIO] dropped crew/production scene: {str(raw.get('title',''))[:60]}")
                continue
            kept.append(raw)
        parsed = kept
    scenes: list[dict] = []
    for i, raw in enumerate(parsed[:cap], start=1):
        if not isinstance(raw, dict):
            continue
        scenes.append({
            "number": int(raw.get("number") or i),
            "title": str(raw.get("title", "") or f"Scene {i}").strip(),
            "description": str(raw.get("description", "") or "").strip(),
            "camera": str(raw.get("camera", "") or "").strip(),
            "framing": str(raw.get("framing", "") or "").strip(),
        })
    return scenes
