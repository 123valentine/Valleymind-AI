"""Per-user, traversal-safe file storage for the Music workstation.

Every artefact the workstation produces — uploaded vocals/beats, reference
tracks, generated audio, separated stems, processed vocals, mastering renders,
artwork and project version snapshots — lives under::

    memory_data/music_workstation/<user_id>/<category>/<file>

Security rules (fail-closed):

  * ``user_id`` is validated against a strict allow-list; anything containing a
    path separator, ``..`` or an unexpected character raises before any path is
    built.
  * Every derived path is resolved and re-checked to live *inside* the user's
    own root, so no filename supplied by a client can ever escape the sandbox.
  * A user's directory is keyed solely by the authenticated ``user_id`` the
    backend derives from the session — never by a client-supplied id.

The module is deliberately dependency-free (stdlib only) so it can be imported
from anywhere in the backend without pulling in Flask or Mongo.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from pathlib import Path

from core.config import PROJECT_ROOT

# Root for all workstation artefacts. Tests (and deployments that want a
# different volume) override this with ``set_base``.
_BASE = PROJECT_ROOT / "memory_data" / "music_workstation"

# Categories keep artefacts tidy and make cleanup predictable.
CATEGORIES = (
    "uploads",      # raw user uploads (vocals, beats, songs)
    "reference",    # reference tracks the user asked us to analyse
    "generated",    # AI / provider rendered audio
    "stems",        # separated stem files
    "vocals",       # processed / cleaned vocal parts
    "master",       # mastered renders
    "versions",     # immutable project version snapshots
    "artwork",      # cover / artwork images
    "exports",      # user-triggered exports
    "tmp",          # transient processing files (auto-cleaned)
)

# Audio formats the workstation accepts. Kept broad but explicit.
AUDIO_EXTS = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga",
    ".opus", ".webm", ".aiff", ".aif", ".wma", ".mp4", ".mov",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Default per-file cap for music uploads (env-overridable). The Flask app has a
# global MAX_CONTENT_LENGTH ceiling too; this is the workstation's own tighter
# guard so a huge file never even reaches a provider.
try:
    MAX_UPLOAD_MB = float(os.getenv("MUSIC_MAX_UPLOAD_MB", "50"))
except (TypeError, ValueError):
    MAX_UPLOAD_MB = 50.0
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class StorageError(Exception):
    """Raised for any rejected storage operation with a machine-readable code."""

    def __init__(self, message: str, *, code: str = "storage_error"):
        super().__init__(message)
        self.code = code


def set_base(path) -> None:
    """Override the storage root (used by tests / alternate volumes)."""
    global _BASE
    _BASE = Path(path)


def base() -> Path:
    return _BASE


def validate_user_id(user_id: str) -> str:
    """Return the validated user id or raise ``StorageError``.

    Only the backend's own hashed user ids pass; a client cannot inject a path.
    """
    value = str(user_id or "").strip()
    if not value or not _USER_ID_RE.match(value) or ".." in value:
        raise StorageError("Invalid user scope.", code="invalid_user")
    return value


def _sanitize_name(name: str, default_ext: str = "") -> str:
    raw = os.path.basename(str(name or "").replace("\\", "/")).strip()
    cleaned = _SAFE_NAME_RE.sub("_", raw).lstrip(".") or "file"
    if len(cleaned) > 160:
        stem, ext = os.path.splitext(cleaned)
        cleaned = stem[:140] + ext
    if default_ext and not os.path.splitext(cleaned)[1]:
        cleaned += default_ext
    return cleaned


def user_root(user_id: str) -> Path:
    root = _BASE / validate_user_id(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def category_dir(user_id: str, category: str) -> Path:
    cat = str(category or "uploads").strip().lower()
    if cat not in CATEGORIES:
        cat = "uploads"
    path = user_root(user_id) / cat
    path.mkdir(parents=True, exist_ok=True)
    return path


def _unique(prefix: str, original: str) -> str:
    safe = _sanitize_name(original)
    stem, ext = os.path.splitext(safe)
    token = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    return f"{prefix}{stem}_{token}{ext}" if prefix else f"{stem}_{token}{ext}"


def user_file_path(user_id: str, rel_path: str, *, must_exist: bool = False) -> Path:
    """Resolve ``rel_path`` inside the user's sandbox, refusing any escape.

    Accepts a plain filename, a ``category/filename`` pair, or a relative path
    produced by a previous ``save_*`` call. Absolute paths and ``..`` are
    rejected outright.
    """
    if not rel_path:
        raise StorageError("Missing file path.", code="missing_path")
    rel = str(rel_path).replace("\\", "/").strip().lstrip("/")
    if rel.startswith("~") or ":" in rel.split("/", 1)[0]:
        raise StorageError("Absolute paths are not allowed.", code="bad_path")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise StorageError("Path traversal is not allowed.", code="bad_path")

    root = user_root(user_id).resolve()
    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise StorageError("Path escapes the user sandbox.", code="bad_path")
    if must_exist and not target.is_file():
        raise StorageError("File not found.", code="not_found")
    return target


def rel_for(user_id: str, path: Path) -> str:
    return str(Path(path).resolve().relative_to(user_root(user_id).resolve())).replace("\\", "/")


def _check_size(size: int) -> None:
    if size and size > MAX_UPLOAD_BYTES:
        raise StorageError(
            f"File is larger than the {int(MAX_UPLOAD_MB)} MB limit.",
            code="too_large",
        )


def save_bytes(user_id: str, category: str, data: bytes, filename: str) -> dict:
    """Persist raw bytes and return a public descriptor."""
    _check_size(len(data or b""))
    target_dir = category_dir(user_id, category)
    target = target_dir / _unique("", filename)
    target.write_bytes(data or b"")
    return {
        "name": _sanitize_name(filename),
        "stored_name": target.name,
        "category": category,
        "rel": rel_for(user_id, target),
        "url": f"/api/music/files/{rel_for(user_id, target)}",
        "size": target.stat().st_size,
        "created_at": int(time.time()),
    }


def save_stream(user_id: str, category: str, file_storage, *, filename: str = "") -> dict:
    """Persist an uploaded ``FileStorage`` (or any file-like) to disk."""
    name = filename or getattr(file_storage, "filename", "") or "upload"
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = file_storage.stream.tell()
        file_storage.stream.seek(0)
    except Exception:  # noqa: BLE001 — some streams are not seekable
        size = 0
    _check_size(size)

    target_dir = category_dir(user_id, category)
    target = target_dir / _unique("", name)
    file_storage.save(str(target))
    return {
        "name": _sanitize_name(name),
        "stored_name": target.name,
        "category": category,
        "rel": rel_for(user_id, target),
        "url": f"/api/music/files/{rel_for(user_id, target)}",
        "size": target.stat().st_size,
        "created_at": int(time.time()),
    }


def list_files(user_id: str, category: str = "", *, ext_filter=None) -> list:
    """List stored files (optionally filtered by category / extension)."""
    roots = [category_dir(user_id, category)] if category else [
        user_root(user_id) / c for c in CATEGORIES if (user_root(user_id) / c).is_dir()
    ]
    out = []
    for root in roots:
        for path in sorted(root.glob("*")):
            if not path.is_file():
                continue
            if ext_filter and path.suffix.lower() not in ext_filter:
                continue
            try:
                rel = rel_for(user_id, path)
            except ValueError:
                continue
            out.append({
                "name": path.name,
                "category": path.parent.name,
                "rel": rel,
                "url": f"/api/music/files/{rel}",
                "size": path.stat().st_size,
                "modified": int(path.stat().st_mtime),
            })
    return out


def delete_file(user_id: str, rel_path: str) -> bool:
    try:
        target = user_file_path(user_id, rel_path, must_exist=True)
    except StorageError:
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


def usage_bytes(user_id: str) -> int:
    total = 0
    root = user_root(user_id)
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                continue
    return total


def cleanup_tmp(max_age_seconds: int = 3600) -> int:
    """Remove transient ``tmp`` files older than ``max_age_seconds``.

    Sweeps every user's tmp directory. Returns the number of files removed.
    Called opportunistically by the job runner; never raises.
    """
    removed = 0
    cutoff = time.time() - max(60, int(max_age_seconds))
    if not _BASE.is_dir():
        return 0
    for user_dir in _BASE.iterdir():
        tmp = user_dir / "tmp"
        if not tmp.is_dir():
            continue
        for path in tmp.glob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue
    return removed
