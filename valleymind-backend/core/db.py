import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pinecone
from pinecone import Pinecone, ServerlessSpec

from core.config import PROJECT_ROOT, get_config


_SENTINEL = object()

_EMBEDDING_DIM = 384


def _sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return value
    if isinstance(value, dict):
        return {_sanitize_key(k): _sanitize_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        if value.startswith("$"):
            return "\uFF04" + value[1:]
        return value
    return value


def _sanitize_key(key: str) -> str:
    if not isinstance(key, str):
        return key
    key = key.replace(".", "_")
    if key.startswith("$"):
        key = "\uFF04" + key[1:]
    return key


def sanitize_document(doc: Any) -> Any:
    return _sanitize_value(doc)


class PineconeManager:
    _instance: Optional["PineconeManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._pc: Optional[Pinecone] = None
        self._index = None
        self._connect_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "PineconeManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_index(self):
        if self._index is not None:
            return self._index
        with self._connect_lock:
            if self._index is not None:
                return self._index
            config = get_config()
            api_key = config.pinecone_api_key
            if not api_key:
                print("[PINECONE] No API key configured — Pinecone unavailable")
                return None
            try:
                self._pc = Pinecone(api_key=api_key)
                env = config.pinecone_environment
                index_name = config.pinecone_index_name
                existing = [idx["name"] for idx in self._pc.list_indexes()]
                if index_name not in existing:
                    self._pc.create_index(
                        name=index_name,
                        dimension=_EMBEDDING_DIM,
                        metric="cosine",
                        spec=ServerlessSpec(cloud="aws", region=env),
                    )
                    print(f"[PINECONE] Created index '{index_name}'")
                self._index = self._pc.Index(index_name)
                print(f"[PINECONE] Connected to index '{index_name}'")
                return self._index
            except Exception as exc:
                print(f"[PINECONE] Connection failed: {exc}")
                return None

    # ── Vector operations ──────────────────────────────────────────────────

    def upsert_vectors(self, vectors: list, namespace: str = ""):
        idx = self.get_index()
        if idx is None:
            print("[PINECONE] Index unavailable; skipping upsert")
            return
        try:
            idx.upsert(vectors=vectors, namespace=namespace)
        except Exception as exc:
            print(f"[PINECONE] Upsert failed: {exc}")

    def query_vectors(
        self,
        vector: list,
        top_k: int = 10,
        filter: Optional[dict] = None,
        namespace: str = "",
        include_metadata: bool = True,
    ) -> list:
        idx = self.get_index()
        if idx is None:
            print("[PINECONE] Index unavailable; returning empty results")
            return []
        try:
            result = idx.query(
                vector=vector,
                top_k=top_k,
                filter=filter,
                namespace=namespace,
                include_metadata=include_metadata,
            )
            return result.get("matches", [])
        except Exception as exc:
            print(f"[PINECONE] Query failed: {exc}")
            return []

    def delete_vectors(
        self,
        ids: Optional[list] = None,
        filter: Optional[dict] = None,
        namespace: str = "",
    ):
        idx = self.get_index()
        if idx is None:
            return
        try:
            idx.delete(ids=ids, filter=filter, namespace=namespace)
        except Exception as exc:
            print(f"[PINECONE] Delete failed: {exc}")

    # ── Session metadata (local JSON only) ────────────────────────────────

    @staticmethod
    def _sessions_dir(user_id: str) -> Path:
        d = PROJECT_ROOT / "memory_data" / "users" / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _sessions_file(user_id: str) -> Path:
        return PROJECT_ROOT / "memory_data" / "users" / user_id / "sessions_index.json"

    def _load_sessions_index(self, user_id: str) -> list:
        fpath = self._sessions_file(user_id)
        try:
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[SESSION] Failed to load sessions index: {exc}")
        return []

    def _save_sessions_index(self, user_id: str, sessions: list):
        try:
            fpath = self._sessions_file(user_id)
            fpath.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(fpath) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sessions, f, indent=2, ensure_ascii=False)
            os.replace(tmp, fpath)
        except OSError as exc:
            print(f"[SESSION] Failed to save sessions index: {exc}")

    def list_sessions(self, user_id: str) -> list:
        if not user_id:
            return []
        return self._load_sessions_index(user_id)

    def get_session(self, chat_id: str, projection: Optional[dict] = None) -> dict:
        fpath = PROJECT_ROOT / "memory_data" / "chats" / f"{chat_id}.json"
        try:
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if projection:
                        data = {k: v for k, v in data.items() if k in projection}
                    return data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[SESSION] Failed to load session '{chat_id}': {exc}")
        return {}

    def upsert_session_meta(
        self,
        user_id: str,
        chat_id: str,
        title: str = "",
        message_count: int = 0,
        last_activity: str = "",
    ):
        now = last_activity or datetime.now(timezone.utc).isoformat()
        sessions = self._load_sessions_index(user_id)
        found = False
        for s in sessions:
            if s.get("chat_id") == chat_id:
                if title:
                    s["title"] = title
                s["last_activity"] = now
                s["message_count"] = max(s.get("message_count", 0), message_count)
                found = True
                break
        if not found:
            sessions.append({
                "chat_id": chat_id,
                "title": title or "New Chat",
                "user_id": user_id,
                "created_at": now,
                "last_activity": now,
                "message_count": message_count,
            })
        sessions.sort(key=lambda x: x.get("last_activity", ""), reverse=True)
        self._save_sessions_index(user_id, sessions)

    def delete_session(self, chat_id: str, user_id: str = ""):
        if user_id:
            idx = self._load_sessions_index(user_id)
            idx = [s for s in idx if s.get("chat_id") != chat_id]
            self._save_sessions_index(user_id, idx)

    def update_session_title(self, chat_id: str, title: str):
        fpath = PROJECT_ROOT / "memory_data" / "chats" / f"{chat_id}.json"
        user_id = ""
        try:
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["title"] = title
                user_id = str(data.get("user_id") or data.get("user_id") or "")
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[SESSION] Failed to update session title '{chat_id}': {exc}")
        if user_id:
            self.upsert_session_meta(user_id, chat_id, title=title)

    # ── Health ─────────────────────────────────────────────────────────────

    def health(self) -> dict:
        status = {
            "pinecone_connected": False,
            "latency_ms": None,
            "error": None,
        }
        idx = self.get_index()
        if idx is None:
            status["error"] = "not_connected"
            return status
        try:
            import time
            start = time.monotonic()
            idx.describe_index_stats()
            elapsed = (time.monotonic() - start) * 1000
            status["pinecone_connected"] = True
            status["latency_ms"] = round(elapsed, 1)
        except Exception as exc:
            status["error"] = str(exc)
        return status


_pinecone_manager = PineconeManager.get_instance()


def get_db():
    return _pinecone_manager


def get_db_manager():
    return _pinecone_manager
