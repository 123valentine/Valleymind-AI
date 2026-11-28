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

from typing import Any

from core import r2_storage

_KINDS = ("image", "video", "audio")


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
