"""Media Manager -- permanent storage for generated images and videos.

Storage: MongoDB GridFS (binary data) + a `media` collection (metadata) when
MONGODB_URI is configured and reachable -- this survives Render's ephemeral
container filesystem. Falls back to local disk when Mongo is unavailable,
exactly like the chat/session persistence layer in core/memory.py and app.py.

Local disk layout (fallback only):
    memory_data/users/{user_id}/media/
        images/
            {media_id}.png
        videos/
            {media_id}.mp4
        media_index.json
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from core.config import PROJECT_ROOT
from core.db import get_db

_SUBDIR = {"image": "images", "video": "videos"}


class MediaManager:
    """Thread-safe media manager for a single user (images + videos)."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.base_dir = PROJECT_ROOT / "memory_data" / "users" / user_id / "media"
        self._index_file = self.base_dir / "media_index.json"
        self._lock = threading.Lock()
        self._index: list[dict[str, Any]] = []

        for sub in _SUBDIR.values():
            (self.base_dir / sub).mkdir(parents=True, exist_ok=True)
        self._load_index()

    # -- Mongo/GridFS helpers -------------------------------------------------

    @staticmethod
    def _media_collection():
        db = get_db()
        return db.media if db is not None else None

    @staticmethod
    def _gridfs_bucket():
        db = get_db()
        if db is None:
            return None
        try:
            import gridfs

            return gridfs.GridFSBucket(db)
        except Exception as exc:
            print(f"[MEDIA] GridFS unavailable: {exc}")
            return None

    # -- Index persistence (local fallback only) ------------------------------

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

    # -- Fetch & Store ---------------------------------------------------------

    def save_media(
        self,
        source: str,
        *,
        media_type: str,
        prompt: str = "",
        revised_prompt: str = "",
        provider: str = "",
        chat_id: str = "",
    ) -> dict[str, Any] | None:
        """Fetch *source* (an http(s) URL or a local /static/... path) and store
        it permanently. Returns the media record dict, or ``None`` on failure.
        """
        media_id = uuid.uuid4().hex
        try:
            if source.startswith("http://") or source.startswith("https://"):
                resp = requests.get(source, timeout=120)
                resp.raise_for_status()
                data = resp.content
                content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            else:
                # Absolute path (e.g. an ffmpeg temp file) used as-is; a
                # "/static/..." app path is resolved under PROJECT_ROOT.
                local_source = Path(source) if os.path.isabs(source) else (PROJECT_ROOT / source.lstrip("/"))
                data = local_source.read_bytes()
                content_type = mimetypes.guess_type(str(local_source))[0] or ""

            ext = self._ext_from_content_type(content_type, media_type)
            if not content_type:
                content_type = self._content_type_from_ext(ext, media_type)
            filename = f"{media_id}.{ext}"
            local_path = f"/static/media/users/{self.user_id}/{_SUBDIR[media_type]}/{filename}"
            file_size = len(data)

            record = {
                "media_id": media_id,
                "type": media_type,
                "prompt": prompt,
                "revised_prompt": revised_prompt,
                "provider": provider,
                "local_path": local_path,
                "chat_id": chat_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "file_size": file_size,
            }

            if self._save_to_mongo(record, filename, data, content_type):
                print(f"[MEDIA] {media_type} saved to Mongo GridFS: {media_id} ({file_size} bytes)")
                return record

            self._save_to_disk(record, filename, data, media_type)
            print(f"[MEDIA] {media_type} saved to local disk (Mongo unavailable): {media_id} ({file_size} bytes)")
            return record

        except Exception as exc:
            print(f"[MEDIA] Failed to save {media_type}: {exc}")
            return None

    def save_image(self, url: str, **kwargs: Any) -> dict[str, Any] | None:
        return self.save_media(url, media_type="image", **kwargs)

    def save_video(self, url: str, **kwargs: Any) -> dict[str, Any] | None:
        return self.save_media(url, media_type="video", **kwargs)

    def _save_to_mongo(self, record: dict, filename: str, data: bytes, content_type: str) -> bool:
        coll = self._media_collection()
        bucket = self._gridfs_bucket()
        if coll is None or bucket is None:
            return False
        try:
            gridfs_id = bucket.upload_from_stream(
                filename, data, metadata={"content_type": content_type, "user_id": self.user_id}
            )
            doc = dict(record)
            doc["_id"] = record["media_id"]
            doc["user_id"] = self.user_id
            doc["gridfs_id"] = gridfs_id
            doc["gridfs_filename"] = filename
            coll.insert_one(doc)
            return True
        except Exception as exc:
            print(f"[MEDIA] Mongo/GridFS save failed, falling back to local disk: {exc}")
            return False

    def _save_to_disk(self, record: dict, filename: str, data: bytes, media_type: str) -> None:
        local_path = self.base_dir / _SUBDIR[media_type] / filename
        local_path.write_bytes(data)
        with self._lock:
            self._index.append(record)
            self._save_index()

    # -- Retrieval -----------------------------------------------------------

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        coll = self._media_collection()
        if coll is not None:
            try:
                doc = coll.find_one({"_id": media_id, "user_id": self.user_id})
                if doc is not None:
                    return self._strip_mongo_fields(doc)
            except Exception as exc:
                print(f"[MEDIA] Mongo get_media failed, falling back to local index: {exc}")

        with self._lock:
            for record in self._index:
                if record.get("media_id") == media_id:
                    return dict(record)
        return None

    def list_images(self, **kwargs: Any) -> list[dict]:
        return self._list("image", **kwargs)

    def list_videos(self, **kwargs: Any) -> list[dict]:
        return self._list("video", **kwargs)

    def _list(
        self, media_type: str, *, chat_id: str = "", search: str = "", limit: int = 50, offset: int = 0
    ) -> list[dict]:
        coll = self._media_collection()
        if coll is not None:
            try:
                query: dict[str, Any] = {"user_id": self.user_id, "type": media_type}
                if chat_id:
                    query["chat_id"] = chat_id
                items = [self._strip_mongo_fields(d) for d in coll.find(query)]
                if search:
                    items = self._semantic_search(items, search)
                items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
                return items[offset : offset + limit]
            except Exception as exc:
                print(f"[MEDIA] Mongo list failed, falling back to local index: {exc}")

        with self._lock:
            items = [r for r in self._index if r.get("type") == media_type]
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

    def count_images(self, **kwargs: Any) -> int:
        return self._count("image", **kwargs)

    def count_videos(self, **kwargs: Any) -> int:
        return self._count("video", **kwargs)

    def _count(self, media_type: str, *, chat_id: str = "") -> int:
        coll = self._media_collection()
        if coll is not None:
            try:
                query: dict[str, Any] = {"user_id": self.user_id, "type": media_type}
                if chat_id:
                    query["chat_id"] = chat_id
                return coll.count_documents(query)
            except Exception as exc:
                print(f"[MEDIA] Mongo count failed, falling back to local index: {exc}")

        with self._lock:
            items = [r for r in self._index if r.get("type") == media_type]
        if chat_id:
            items = [r for r in items if r.get("chat_id") == chat_id]
        return len(items)

    # -- Deletion ------------------------------------------------------------

    def delete_media(self, media_id: str) -> bool:
        coll = self._media_collection()
        bucket = self._gridfs_bucket()
        if coll is not None and bucket is not None:
            try:
                doc = coll.find_one({"_id": media_id, "user_id": self.user_id})
                if doc is not None:
                    gridfs_id = doc.get("gridfs_id")
                    if gridfs_id is not None:
                        try:
                            bucket.delete(gridfs_id)
                        except Exception as exc:
                            print(f"[MEDIA] GridFS delete warning for {media_id}: {exc}")
                    coll.delete_one({"_id": media_id, "user_id": self.user_id})
                    print(f"[MEDIA] Deleted {media_id} from Mongo/GridFS")
                    return True
            except Exception as exc:
                print(f"[MEDIA] Mongo delete failed, falling back to local index: {exc}")

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

        print(f"[MEDIA] Deleted {record.get('type', 'media')} {media_id} from local disk")
        return True

    # -- Utilities -----------------------------------------------------------

    @staticmethod
    def _strip_mongo_fields(doc: dict) -> dict:
        doc = dict(doc)
        doc.pop("_id", None)
        doc.pop("user_id", None)
        doc.pop("gridfs_id", None)
        doc.pop("gridfs_filename", None)
        return doc

    @staticmethod
    def _ext_from_content_type(content_type: str, media_type: str) -> str:
        ct = (content_type or "").split(";")[0].strip().lower()
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
        if ct in mapping:
            return mapping[ct]
        if "/" in ct:
            return ct.split("/")[-1]
        return "png" if media_type == "image" else "mp4"

    @staticmethod
    def _content_type_from_ext(ext: str, media_type: str) -> str:
        guessed = mimetypes.guess_type(f"file.{ext}")[0]
        if guessed:
            return guessed
        return "image/png" if media_type == "image" else "video/mp4"


# -- Module-level cache ------------------------------------------------------

_managers: dict[str, MediaManager] = {}
_managers_lock = threading.Lock()


def get_media_manager(user_id: str) -> MediaManager:
    with _managers_lock:
        if user_id not in _managers:
            _managers[user_id] = MediaManager(user_id)
        return _managers[user_id]
