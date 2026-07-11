"""Media Manager -- permanent local storage for generated images.

Storage layout:
    memory_data/users/{user_id}/media/
        images/
            {media_id}.png
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
    """Thread-safe media manager for a single user (images only)."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.base_dir = PROJECT_ROOT / "memory_data" / "users" / user_id / "media"
        self.images_dir = self.base_dir / "images"
        self._index_file = self.base_dir / "media_index.json"
        self._lock = threading.Lock()
        self._index: list[dict[str, Any]] = []

        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    # -- Index persistence ---------------------------------------------------

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

    # -- Download & Store ----------------------------------------------------

    def save_image(
        self,
        url: str,
        *,
        prompt: str = "",
        revised_prompt: str = "",
        provider: str = "",
        chat_id: str = "",
    ) -> dict[str, Any] | None:
        """Download an image from *url* and store permanently.

        Returns the media record dict, or ``None`` on failure.
        """
        media_id = uuid.uuid4().hex
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            data = resp.content

            ct = resp.headers.get("content-type", "image/png")
            ext = self._ext_from_content_type(ct)
            filename = f"{media_id}.{ext}"
            local_path = self.images_dir / filename
            local_path.write_bytes(data)
            file_size = len(data)

            record = {
                "media_id": media_id,
                "type": "image",
                "prompt": prompt,
                "revised_prompt": revised_prompt,
                "provider": provider,
                "local_path": f"/static/media/users/{self.user_id}/images/{filename}",
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

    # -- Retrieval -----------------------------------------------------------

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        with self._lock:
            for record in self._index:
                if record.get("media_id") == media_id:
                    return dict(record)
        return None

    def list_images(
        self, *, chat_id: str = "", search: str = "", limit: int = 50, offset: int = 0
    ) -> list[dict]:
        with self._lock:
            items = [r for r in self._index if r.get("type") == "image"]

        if chat_id:
            items = [r for r in items if r.get("chat_id") == chat_id]

        if search:
            items = self._semantic_search(items, search)

        items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return items[offset : offset + limit]

    def _semantic_search(self, items: list[dict], query: str) -> list[dict]:
        """Fuzzy semantic search across prompt, revised_prompt, and provider fields."""
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 1]
        scored: list[tuple[float, dict]] = []

        for r in items:
            score = 0.0
            prompt = (r.get("prompt") or "").lower()
            revised = (r.get("revised_prompt") or "").lower()
            combined = prompt + " " + revised

            if query_lower in prompt:
                score += 10.0
            if query_lower in revised:
                score += 8.0

            for word in query_words:
                if word in prompt:
                    score += 3.0
                if word in revised:
                    score += 2.5
                for token in combined.split():
                    if token.startswith(word) or word.startswith(token):
                        score += 1.5

            if score > 0:
                scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    def count_images(self, *, chat_id: str = "") -> int:
        with self._lock:
            if chat_id:
                return sum(
                    1
                    for r in self._index
                    if r.get("type") == "image" and r.get("chat_id") == chat_id
                )
            return sum(1 for r in self._index if r.get("type") == "image")

    # -- Deletion ------------------------------------------------------------

    def delete_media(self, media_id: str) -> bool:
        record = None
        with self._lock:
            for i, r in enumerate(self._index):
                if r.get("media_id") == media_id:
                    record = self._index.pop(i)
                    self._save_index()
                    break

        if not record:
            return False

        local_path = record.get("local_path", "")
        if local_path:
            try:
                real = PROJECT_ROOT / local_path.lstrip("/")
                if real.exists():
                    real.unlink()
            except OSError as exc:
                print(f"[MEDIA] Failed to delete file {local_path}: {exc}")

        print(f"[MEDIA] Deleted image {media_id}")
        return True

    # -- Utilities -----------------------------------------------------------

    @staticmethod
    def _ext_from_content_type(content_type: str) -> str:
        ct = content_type.split(";")[0].strip().lower()
        mapping = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
        }
        return mapping.get(ct, ct.split("/")[-1] if "/" in ct else "png")


# -- Module-level cache ------------------------------------------------------

_managers: dict[str, MediaManager] = {}
_managers_lock = threading.Lock()


def get_media_manager(user_id: str) -> MediaManager:
    with _managers_lock:
        if user_id not in _managers:
            _managers[user_id] = MediaManager(user_id)
        return _managers[user_id]
