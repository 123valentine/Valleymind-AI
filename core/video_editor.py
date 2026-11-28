"""Storage routing for Massive Editing uploads.

The rendering engine lives in :mod:`core.video_edit`; this module is the thin
upload gateway used by the Massive Editing routes. It deliberately stores no
bytes itself: uploads are staged in a short-lived temporary file and handed to
the existing media manager, which writes to object storage when credentials are
configured and falls back to temporary local storage only when they are unset.

Keeping this boundary in one place makes the storage decision testable and
keeps Render's disk/memory free of bundled stickers, reaction clips and other
heavy media assets.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core import r2_storage

_KINDS = ("image", "video", "audio")

# Chat-side sticker placement vocabulary. The picker uses human labels while the
# render engine uses compact corner codes; normalising in one place keeps both
# surfaces in agreement.
_STICKER_POSITION_ALIASES = {
    "tl": "tl", "top left": "tl", "upper left": "tl", "top-left": "tl",
    "tr": "tr", "top right": "tr", "upper right": "tr", "top-right": "tr",
    "bl": "bl", "bottom left": "bl", "lower left": "bl", "bottom-left": "bl",
    "br": "br", "bottom right": "br", "lower right": "br", "bottom-right": "br",
    "center": "center", "centre": "center", "middle": "center",
}
_POSITION_EXPRESSIONS = {
    "tl": lambda m: (str(m), str(m)),
    "tr": lambda m: (f"main_w-overlay_w-{m}", str(m)),
    "bl": lambda m: (str(m), f"main_h-overlay_h-{m}"),
    "br": lambda m: (f"main_w-overlay_w-{m}", f"main_h-overlay_h-{m}"),
    "center": lambda m: ("(main_w-overlay_w)/2", "(main_h-overlay_h)/2"),
}


def normalize_sticker_position(value: Any, default: str = "br") -> str:
    """Return the canonical ``tl|tr|bl|br|center`` code for a placement label.

    Accepts compact codes plus the chat picker labels (``Top Left``,
    ``Upper Right``, ``Lower Left``, ``Lower Right``, ``Center``), with or without
    hyphens. Unknown values fall back to ``default`` (also normalised).
    """
    fallback = str(default or "br").strip().lower()
    fallback = _STICKER_POSITION_ALIASES.get(fallback, "br")
    raw = re.sub(r"[\s_\-]+", " ", str(value or "").strip().lower())
    return _STICKER_POSITION_ALIASES.get(raw, fallback)


def sticker_overlay_expressions(value: Any, margin: int = 24) -> tuple[str, str]:
    """FFmpeg ``overlay=x:y`` expressions for a canonical placement label."""
    try:
        m = max(0, int(margin))
    except (TypeError, ValueError):
        m = 24
    return _POSITION_EXPRESSIONS[normalize_sticker_position(value)](m)


def _manual_num(value: Any, default: float, low: float, high: float) -> float:
    """Clamp an optional override number; malformed values use ``default``."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:  # NaN
        return default
    return max(low, min(high, n))


def parse_manual_overrides(raw: Any) -> dict:
    """Decode optional JSON ``manual`` overrides from /api/editing/run.

    The workspace always sends a manual payload, but a default payload is a
    no-op.  Return {} for missing, malformed or default-only input so the normal
    prompt-to-video job stays fully automated.  Non-default presentation values
    are mapped onto the engine's manual vocabulary where one exists.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}

    out: dict = {}
    speed = _manual_num(data.get("speed"), 1.0, 0.5, 2.0)
    if abs(speed - 1.0) > 0.001:
        out["speed"] = round(speed, 2)

    curve = str(data.get("speed_curve") or "").strip()
    if curve and curve.lower() != "linear":
        out["speed_curve"] = curve[:40]

    for field in ("ramp_in", "ramp_out"):
        value = _manual_num(data.get(field), 0.0, 0.0, 10.0)
        if value > 0:
            out[field] = round(value, 2)

    trim = data.get("trim")
    if not isinstance(trim, dict):
        trim = {"start": data.get("trim_start"), "end": data.get("trim_end")}
    start = _manual_num(trim.get("start"), 0.0, 0.0, 1e6)
    end_raw = trim.get("end")
    end = None if end_raw in (None, "", 0, "0") else _manual_num(end_raw, 0.0, 0.0, 1e6)
    if start > 0 or (end is not None and end > 0):
        out["trim"] = {"start": round(start, 3),
                       "end": None if end is None else round(max(start, end), 3)}

    if data.get("beat_sync") is True:
        out["beat_sync"] = True

    effects = data.get("effects")
    if isinstance(effects, list):
        names = [str(e).strip() for e in effects if str(e or "").strip()]
        if names:
            out["effects"] = names[:20]

    canvas = data.get("canvas")
    if isinstance(canvas, dict):
        aspect = str(canvas.get("aspect") or "").strip()
        if aspect in ("9:16", "16:9", "1:1"):
            out["canvas"] = {"aspect": aspect}

    for field in ("overlay", "transition", "sticker_text", "sticker_anim"):
        value = str(data.get(field) or "").strip()
        if value:
            out[field] = value[:120]

    return out


def storage_backend() -> str:
    """Return the active backend name: ``"r2"`` or ``"temporary"``."""
    return "r2" if r2_storage.available() else "temporary"


def object_storage_ready() -> bool:
    """True when durable object storage is configured."""
    return storage_backend() == "r2"


def store_upload(
    manager: Any,
    path: str,
    kind: str,
    *,
    prompt: str = "",
    category: str = "",
    chat_id: str = "",
) -> dict | None:
    """Route one staged upload through the supplied media manager.

    ``manager`` is injected so routes can keep using their cached manager and
    tests can substitute a fake. Raises ``ValueError`` for unsupported kinds;
    the media manager owns the actual storage fallback policy.
    """
    kind = str(kind or "").strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"Unsupported media kind: {kind}")
    label = str(prompt or "Massive Editing upload")[:200]
    scope = str(chat_id or "edit")

    if kind == "image":
        return manager.save_image(path, prompt=label, provider="MassiveEditing",
                                  chat_id=scope)
    if kind == "video":
        return manager.save_video(path, prompt=label, provider="MassiveEditing",
                                  chat_id=scope)
    return manager.save_media(path, media_type="audio", prompt=label,
                              provider="MassiveEditing", chat_id=scope,
                              library="edit", category=category or "music")


def store_library_asset(
    manager: Any,
    path: str,
    *,
    kind: str,
    category: str,
    name: str = "",
) -> dict | None:
    """Store a user's sticker/reaction/audio asset in the per-user library."""
    kind = str(kind or "").strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"Unsupported media kind: {kind}")
    category = str(category or "sfx").strip().lower() or "sfx"
    if hasattr(manager, "save_asset"):
        return manager.save_asset(path, kind=kind, category=category,
                                  name=str(name or "")[:80])
    return manager.save_media(path, media_type=kind, prompt=str(name or "")[:200],
                              provider="MassiveEditing", library="edit",
                              category=category,
                              chat_id=f"edit_assets_{getattr(manager, 'user_id', 'unknown')}")
