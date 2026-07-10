"""Centralized Media Manager — single source of truth for all user media.

Responsibilities:
    - Download provider media to local permanent storage
    - Generate thumbnails for images
    - Maintain a per-user media index (JSON)
    - CRUD operations: create, retrieve, list, delete
    - Search by prompt, chat_id, type
    - File validation and integrity checks
    - Backward-compatible with existing /static/generated/ paths

Storage layout:
    memory_data/users/{user_id}/
        media/
            images/
                {image_id}.png
            videos/
                {video_id}.mp4
            thumbnails/
                {image_id}_thumb.jpg
            media_index.json
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from core.config import PROJECT_ROOT


class MediaManager:
    """Thread-safe media manager for a single user."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.base_dir = PROJECT_ROOT / "memory_data" / "users" / user_id / "media"
        self.images_dir = self.base_dir / "images"
        self.videos_dir = self.base_dir / "videos"
        self.thumbnails_dir = self.base_dir / "thumbnails"
        self._index_file = self.base_dir / "media_index.json"
        self._lock = threading.Lock()
        self._index: list[dict[str, Any]] = []

        self._ensure_dirs()
        self._load_index()

    def _ensure_dirs(self) -> None:
        for d in (self.images_dir, self.videos_dir, self.thumbnails_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── Index persistence ──────────────────────────────────────

    def _load_index(self) -> None:
        try:
            if self._index_file.exists():
                with open(self._index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._index = data
                    return
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[MEDIA] Failed to load index for {self.user_id}: {exc}")
        self._index = []

    def _save_index(self) -> None:
        try:
            self._index_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self._index_file) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._index, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self._index_file)
        except OSError as exc:
            print(f"[MEDIA] Failed to save index for {self.user_id}: {exc}")

    # ── Download & Store ───────────────────────────────────────

    def save_image(
        self,
        url_or_bytes: str | bytes,
        *,
        prompt: str = "",
        revised_prompt: str = "",
        provider: str = "",
        chat_id: str = "",
        content_type: str = "image/png",
        media_id: str = "",
    ) -> dict[str, Any] | None:
        """Download an image from a URL (or accept raw bytes) and store permanently.

        Returns the media record dict, or None on failure.
        """
        media_id = media_id or uuid.uuid4().hex
        ext = self._ext_from_content_type(content_type)
        filename = f"{media_id}.{ext}"
        local_path = self.images_dir / filename

        try:
            if isinstance(url_or_bytes, bytes):
                data = url_or_bytes
            else:
                resp = requests.get(url_or_bytes, timeout=120)
                resp.raise_for_status()
                data = resp.content
                ct = resp.headers.get("content-type", content_type)
                if ct:
                    ext = self._ext_from_content_type(ct)
                    filename = f"{media_id}.{ext}"
                    local_path = self.images_dir / filename

            local_path.write_bytes(data)
            file_size = len(data)

            thumb_path = self._generate_thumbnail(local_path, media_id)

            record = {
                "media_id": media_id,
                "type": "image",
                "prompt": prompt,
                "revised_prompt": revised_prompt,
                "provider": provider,
                "local_path": f"/static/media/users/{self.user_id}/images/{filename}",
                "thumbnail_path": f"/static/media/users/{self.user_id}/thumbnails/{os.path.basename(thumb_path)}" if thumb_path else "",
                "chat_id": chat_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "file_size": file_size,
            }

            with self._lock:
                self._index.append(record)
                self._save_index()

            print(f"[MEDIA] Image saved: {media_id} ({file_size} bytes)")
            return record

        except Exception as exc:
            print(f"[MEDIA] Failed to save image: {exc}")
            return None

    def save_video(
        self,
        url_or_bytes: str | bytes,
        *,
        prompt: str = "",
        provider: str = "",
        chat_id: str = "",
        content_type: str = "video/mp4",
        duration_seconds: float = 0,
        media_id: str = "",
    ) -> dict[str, Any] | None:
        """Download a video from a URL (or accept raw bytes) and store permanently."""
        media_id = media_id or uuid.uuid4().hex
        ext = self._ext_from_content_type(content_type)
        filename = f"{media_id}.{ext}"
        local_path = self.videos_dir / filename

        try:
            if isinstance(url_or_bytes, bytes):
                data = url_or_bytes
            else:
                resp = requests.get(url_or_bytes, timeout=300)
                resp.raise_for_status()
                data = resp.content
                ct = resp.headers.get("content-type", content_type)
                if ct:
                    ext = self._ext_from_content_type(ct)
                    filename = f"{media_id}.{ext}"
                    local_path = self.videos_dir / filename

            local_path.write_bytes(data)
            file_size = len(data)

            record = {
                "media_id": media_id,
                "type": "video",
                "prompt": prompt,
                "provider": provider,
                "local_path": f"/static/media/users/{self.user_id}/videos/{filename}",
                "thumbnail_path": "",
                "chat_id": chat_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "file_size": file_size,
                "duration_seconds": duration_seconds,
            }

            with self._lock:
                self._index.append(record)
                self._save_index()

            print(f"[MEDIA] Video saved: {media_id} ({file_size} bytes)")
            return record

        except Exception as exc:
            print(f"[MEDIA] Failed to save video: {exc}")
            return None

    # ── Retrieval ──────────────────────────────────────────────

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        with self._lock:
            for record in self._index:
                if record.get("media_id") == media_id:
                    return dict(record)
        return None

    def list_images(self, chat_id: str = "", search: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        """List images, optionally filtered by chat_id or search term."""
        with self._lock:
            items = [r for r in self._index if r.get("type") == "image"]

        if chat_id:
            items = [r for r in items if r.get("chat_id") == chat_id]

        if search:
            q = search.lower()
            items = [
                r for r in items
                if q in (r.get("prompt") or "").lower()
                or q in (r.get("revised_prompt") or "").lower()
            ]

        items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return items[offset:offset + limit]

    def list_videos(self, chat_id: str = "", search: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        """List videos, optionally filtered by chat_id or search term."""
        with self._lock:
            items = [r for r in self._index if r.get("type") == "video"]

        if chat_id:
            items = [r for r in items if r.get("chat_id") == chat_id]

        if search:
            q = search.lower()
            items = [
                r for r in items
                if q in (r.get("prompt") or "").lower()
            ]

        items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return items[offset:offset + limit]

    def count_images(self, chat_id: str = "") -> int:
        with self._lock:
            if chat_id:
                return sum(1 for r in self._index if r.get("type") == "image" and r.get("chat_id") == chat_id)
            return sum(1 for r in self._index if r.get("type") == "image")

    def count_videos(self, chat_id: str = "") -> int:
        with self._lock:
            if chat_id:
                return sum(1 for r in self._index if r.get("type") == "video" and r.get("chat_id") == chat_id)
            return sum(1 for r in self._index if r.get("type") == "video")

    # ── Deletion ───────────────────────────────────────────────

    def delete_media(self, media_id: str) -> bool:
        """Delete a media item and its file."""
        record = None
        with self._lock:
            for i, r in enumerate(self._index):
                if r.get("media_id") == media_id:
                    record = self._index.pop(i)
                    self._save_index()
                    break

        if not record:
            return False

        media_type = record.get("type", "")
        local_path = record.get("local_path", "")
        thumb_path = record.get("thumbnail_path", "")

        self._delete_file(local_path)
        if thumb_path:
            self._delete_file(thumb_path)

        print(f"[MEDIA] Deleted {media_type} {media_id}")
        return True

    def delete_chat_media(self, chat_id: str) -> int:
        """Delete all media for a specific chat. Returns count deleted."""
        to_delete = []
        with self._lock:
            remaining = []
            for r in self._index:
                if r.get("chat_id") == chat_id:
                    to_delete.append(r)
                else:
                    remaining.append(r)
            self._index = remaining
            self._save_index()

        for record in to_delete:
            self._delete_file(record.get("local_path", ""))
            tp = record.get("thumbnail_path", "")
            if tp:
                self._delete_file(tp)

        return len(to_delete)

    def _delete_file(self, url_path: str) -> None:
        """Convert a URL path like /static/media/... to a real file path and delete."""
        if not url_path:
            return
        try:
            rel = url_path.lstrip("/")
            real_path = PROJECT_ROOT / rel
            if real_path.exists():
                real_path.unlink()
        except OSError as exc:
            print(f"[MEDIA] Failed to delete file {url_path}: {exc}")

    # ── Thumbnails ─────────────────────────────────────────────

    def _generate_thumbnail(self, image_path: Path, media_id: str) -> Path | None:
        """Generate a small thumbnail for an image. Returns the thumbnail path or None."""
        try:
            from PIL import Image
            thumb_size = (200, 200)
            thumb_path = self.thumbnails_dir / f"{media_id}_thumb.jpg"
            with Image.open(image_path) as img:
                img.thumbnail(thumb_size, Image.LANCZOS)
                img.convert("RGB").save(thumb_path, "JPEG", quality=85)
            return thumb_path
        except ImportError:
            print("[MEDIA] Pillow not available — skipping thumbnail generation")
            return None
        except Exception as exc:
            print(f"[MEDIA] Thumbnail generation failed: {exc}")
            return None

    # ── Utilities ──────────────────────────────────────────────

    @staticmethod
    def _ext_from_content_type(content_type: str) -> str:
        ct = content_type.split(";")[0].strip().lower()
        mapping = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "video/mp4": "mp4",
            "video/webm": "webm",
            "video/quicktime": "mov",
        }
        return mapping.get(ct, ct.split("/")[-1] if "/" in ct else "bin")

    def migrate_existing_static(self) -> int:
        """One-time migration: move existing /static/generated/ files into the media index.

        Returns the number of items migrated.
        """
        migrated = 0
        static_images = PROJECT_ROOT / "static" / "generated"
        static_videos = static_images / "videos"

        if static_images.is_dir():
            for f in static_images.iterdir():
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    existing = any(
                        r.get("local_path", "").endswith(f.name)
                        for r in self._index if r.get("type") == "image"
                    )
                    if not existing:
                        media_id = f.stem.split("_")[0] if "_" in f.stem else f.stem
                        record = {
                            "media_id": media_id,
                            "type": "image",
                            "prompt": "",
                            "revised_prompt": "",
                            "provider": "migrated",
                            "local_path": f"/static/generated/{f.name}",
                            "thumbnail_path": "",
                            "chat_id": "",
                            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                            "file_size": f.stat().st_size,
                        }
                        with self._lock:
                            self._index.append(record)
                            migrated += 1
            if migrated:
                self._save_index()

        if static_videos.is_dir():
            for f in static_videos.iterdir():
                if f.is_file() and f.suffix.lower() in (".mp4", ".webm", ".mov"):
                    existing = any(
                        r.get("local_path", "").endswith(f.name)
                        for r in self._index if r.get("type") == "video"
                    )
                    if not existing:
                        media_id = f.stem.split("_")[0] if "_" in f.stem else f.stem
                        record = {
                            "media_id": media_id,
                            "type": "video",
                            "prompt": "",
                            "provider": "migrated",
                            "local_path": f"/static/generated/videos/{f.name}",
                            "thumbnail_path": "",
                            "chat_id": "",
                            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                            "file_size": f.stat().st_size,
                            "duration_seconds": 0,
                        }
                        with self._lock:
                            self._index.append(record)
                            migrated += 1
            if migrated:
                self._save_index()

        return migrated


# ── Module-level cache of MediaManager instances ──────────────

_managers: dict[str, MediaManager] = {}
_managers_lock = threading.Lock()


def get_media_manager(user_id: str) -> MediaManager:
    with _managers_lock:
        if user_id not in _managers:
            _managers[user_id] = MediaManager(user_id)
        return _managers[user_id]
