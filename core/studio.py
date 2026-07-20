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
import re
from typing import Any, Iterator

from core.character import load_character_profile
from core.config import PROJECT_ROOT

MAX_SCENES = 6


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

def scene_messages(idea: str, script: str, sheet_text: str) -> list[dict]:
    return [
        {"role": "system", "content": (
            _persona_prompt("marcus")
            + "\n\nYou are directing this for ValleyMind Studio. Break Angelina's screenplay into "
              f"numbered scenes (at most {MAX_SCENES}). For each scene give the visual description, "
              "the camera angle, and the framing. Reuse the character sheet's descriptions exactly — "
              "the same person must look the same in every scene.\n\n"
              "Respond with ONLY a JSON array:\n"
              '[{"number":1,"title":"short scene title","description":"what we see, visually concrete",'
              '"camera":"lens/movement, e.g. 50mm slow push","framing":"e.g. medium close-up, low angle"}]\n'
              "No markdown, no commentary."
        )},
        {"role": "user", "content": (
            f"IDEA: {idea}\n\nCHARACTER SHEET:\n{sheet_text}\n\nANGELINA'S SCREENPLAY:\n{script[:6000]}"
        )},
    ]


# ── Stage 3: storyboard prompt per scene ────────────────────────────────────

def storyboard_prompt(scene: dict, sheet_text: str, look: str = "") -> str:
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
    return " ".join(p for p in parts if p)[:1200]


def normalize_scenes(parsed: Any) -> list[dict]:
    """Coerce the model's scene output into a clean, capped list."""
    if isinstance(parsed, dict):
        parsed = parsed.get("scenes") or parsed.get("Scenes") or []
    if not isinstance(parsed, list):
        return []
    scenes: list[dict] = []
    for i, raw in enumerate(parsed[:MAX_SCENES], start=1):
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
