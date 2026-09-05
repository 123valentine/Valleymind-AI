"""ValleyMind Cloud — companion body / presentation foundation.

The Cloud is the body, interface and personality PRESENTATION around
ValleyMind's EXISTING intelligence. It is deliberately NOT an AI: this module
contains no LLM, no memory store and no conversation engine. It only models
the state and preference schema that describe the Cloud companion, plus a thin
adapter that injects the user's chosen personality/presentation into a message
BEFORE it is handed to the existing ValleyMind brain (core.brain.MarcusBrain).

Nothing here renders the character. The future 3D renderer reads the Cloud
state model (mirrored in static/cloud.js -> window.VMCloud.renderConfig).
"""

EMOTIONS = (
    "neutral",
    "happy",
    "excited",
    "thinking",
    "curious",
    "concerned",
    "sad",
    "frustrated",
    "angry",
    "surprised",
    "confused",
    "focused",
    "listening",
    "speaking",
)

INTERACTION_STATES = (
    "idle",
    "listening",
    "thinking",
    "speaking",
    "helping",
    "learning",
    "observing",
    "guiding",
)

MODES = ("companion",)

PRESENTATIONS = ("feminine", "masculine", "neutral")

PERSONALITY_STYLES = ("calm", "friendly", "playful", "professional", "energetic", "gentle")

FUTURE_CONTEXT_KEYS = (
    "screen_context",
    "browser_context",
    "application_context",
    "task_context",
    "selected_content",
    "attention_target",
)

STATE_KEYS = (
    "emotion",
    "mode",
    "status",
    "presentation",
    "accent",
    "intensity",
) + FUTURE_CONTEXT_KEYS

SERVICE_KEYS = ("voice_preference", "appearance")

DEFAULT_CLOUD_NAME = "Cloud"
CLOUD_NAME_MAX = 32

PREFERENCE_KEYS = (
    "presentation",
    "personality_style",
    "voice_preference",
    "appearance",
    "accent",
    "animation_intensity",
    "cloud_name",
)

SWITCH_COLOR = "#00E5FF"

STATE_ERRORS = {
    "needs_persona": "Cloud needs a persona to speak.",
}

PERSONALITY_GUIDES = {
    "calm": "calm, unhurried and reassuring",
    "friendly": "warm, casual and inviting",
    "playful": "playful, lighthearted and fun",
    "professional": "professional, precise and efficient",
    "energetic": "energetic, upbeat and motivating",
    "gentle": "gentle, soft and considerate",
}

_PRESENTATION_NOUN = {
    "feminine": "female-styled",
    "masculine": "male-styled",
    "neutral": "neutral-styled",
}


def _clamp01(value, default=0.5):
    """Return a float clamped to 0..1, falling back to `default` on bad input."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if num != num:  # NaN
        return default
    return max(0.0, min(1.0, num))


def normalize_emotion(value, default="neutral"):
    if value in EMOTIONS:
        return value
    return default


def normalize_status(value, default="idle"):
    if value in INTERACTION_STATES:
        return value
    return default


def normalize_mode(value, default="companion"):
    if value in MODES:
        return value
    return default


def normalize_presentation(value, default="neutral"):
    if value in PRESENTATIONS:
        return value
    return default


def normalize_personality(value, default="calm"):
    if value in PERSONALITY_STYLES:
        return value
    return default


def cloud_default_state(prefs=None):
    """Build the canonical default Cloud state from cloud preferences."""
    prefs = prefs if isinstance(prefs, dict) else {}
    state = {
        "emotion": normalize_emotion(prefs.get("emotion")),
        "mode": normalize_mode(prefs.get("mode")),
        "status": normalize_status(prefs.get("status")),
        "presentation": normalize_presentation(prefs.get("presentation")),
        "accent": str(prefs.get("accent") or "").strip()[:64],
        "intensity": _clamp01(prefs.get("animation_intensity")),
    }
    for key in FUTURE_CONTEXT_KEYS:
        state[key] = prefs.get(key) if key in prefs else None
    return state


def normalize_state_patch(patch, base=None):
    """Validate and clamp a partial state patch into a full state dict."""
    merged = dict(base) if isinstance(base, dict) else cloud_default_state()
    if not isinstance(patch, dict):
        return merged
    for key in STATE_KEYS:
        if key not in patch:
            continue
        value = patch[key]
        if key == "emotion":
            merged[key] = normalize_emotion(value, merged.get("emotion", "neutral"))
        elif key == "status":
            merged[key] = normalize_status(value, merged.get("status", "idle"))
        elif key == "mode":
            merged[key] = normalize_mode(value, merged.get("mode", "companion"))
        elif key == "presentation":
            merged[key] = normalize_presentation(value, merged.get("presentation", "neutral"))
        elif key == "intensity":
            merged[key] = _clamp01(value, merged.get("intensity", 0.5))
        elif key == "accent":
            merged[key] = str(value or "").strip()[:64]
        elif key in FUTURE_CONTEXT_KEYS:
            merged[key] = value
    return merged


def normalize_cloud_name(value, default=DEFAULT_CLOUD_NAME):
    """Safely normalize a user-chosen Cloud name.

    Rules: trim whitespace, drop control/format characters, reject empty
    values (fall back to the default) and enforce a reasonable maximum
    length. Kept deliberately conservative so the value can be rendered
    anywhere (via CSS/text and escaped HTML) without injection risk.
    """
    text = str(value or "").strip()
    cleaned = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    return (cleaned[:CLOUD_NAME_MAX] or str(default or DEFAULT_CLOUD_NAME)).strip()


def build_cloud_instructions(prefs=None):
    """Turn Cloud preferences into extra context riding into the existing brain."""
    prefs = prefs if isinstance(prefs, dict) else {}
    personality = normalize_personality(prefs.get("personality_style"))
    presentation = normalize_presentation(prefs.get("presentation"))
    noun = _PRESENTATION_NOUN[presentation]
    guide = PERSONALITY_GUIDES[personality]
    name = normalize_cloud_name(prefs.get("cloud_name"))
    lines = [
        f"You are ValleyMind Cloud, your {noun} cloud companion.",
        f"Manner: {guide}.",
        "You speak directly to the user; keep replies warm, concise and conversational.",
        "You run on the same ValleyMind brain and memory the user already uses.",
    ]
    if name and name != DEFAULT_CLOUD_NAME:
        lines.append(f"The user calls you {name}; answer to that name naturally.")
    return " ".join(lines)


def augment_cloud_message(message, prefs=None):
    """Prepend the Cloud personality frame to a user message before it reaches the brain."""
    message = str(message or "").strip()
    if not message:
        return message
    instructions = build_cloud_instructions(prefs)
    return f"{instructions}\n\n{message}"


def collect_cloud_preferences(section_data):
    """Filter a raw settings section down to known Cloud preference keys."""
    if not isinstance(section_data, dict):
        return {}
    prefs = {}
    for key in PREFERENCE_KEYS:
        if key in section_data:
            prefs[key] = section_data[key]
    return prefs