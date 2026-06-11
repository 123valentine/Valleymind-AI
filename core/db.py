import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

import certifi
from pymongo import MongoClient, collection, errors

from core.config import get_config

_SENTINEL = object()

USE_LOCAL_JSON = False


def _sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return value
    if isinstance(value, dict):
        return {_sanitize_key(k): _sanitize_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        if re.match(r"^\$", value):
            return "\uFF04" + value[1:]
        return value
    return value


def _sanitize_key(key: str) -> str:
    if not isinstance(key, str):
        return key
    key = key.replace(".", "_")
    if re.match(r"^\$", key):
        key = "\uFF04" + key[1:]
    return key


def sanitize_document(doc: Any) -> Any:
    return _sanitize_value(doc)


class SanitizedCollection:
    def __init__(self, collection: collection.Collection):
        self._coll = collection

    def _sanitize(self, doc: Any) -> Any:
        return _sanitize_value(doc)

    def find_one(self, filter: Any = None, *args: Any, **kwargs: Any) -> Any:
        return self._coll.find_one(filter, *args, **kwargs)

    def find(self, filter: Any = None, *args: Any, **kwargs: Any) -> Any:
        return self._coll.find(filter, *args, **kwargs)

    def insert_one(self, document: Any, *args: Any, **kwargs: Any) -> Any:
        return self._coll.insert_one(self._sanitize(document), *args, **kwargs)

    def insert_many(self, documents: list, *args: Any, **kwargs: Any) -> Any:
        return self._coll.insert_many([self._sanitize(d) for d in documents], *args, **kwargs)

    def replace_one(
        self, filter: Any, replacement: Any, *args: Any, **kwargs: Any
    ) -> Any:
        return self._coll.replace_one(filter, self._sanitize(replacement), *args, **kwargs)

    def update_one(self, filter: Any, update: Any, *args: Any, **kwargs: Any) -> Any:
        return self._coll.update_one(self._sanitize(filter), self._sanitize(update), *args, **kwargs)

    def update_many(self, filter: Any, update: Any, *args: Any, **kwargs: Any) -> Any:
        return self._coll.update_many(self._sanitize(filter), self._sanitize(update), *args, **kwargs)

    def delete_one(self, filter: Any, *args: Any, **kwargs: Any) -> Any:
        return self._coll.delete_one(filter, *args, **kwargs)

    def delete_many(self, filter: Any, *args: Any, **kwargs: Any) -> Any:
        return self._coll.delete_many(filter, *args, **kwargs)

    def count_documents(self, filter: Any, *args: Any, **kwargs: Any) -> int:
        return self._coll.count_documents(filter, *args, **kwargs)

    def create_index(self, keys: Any, *args: Any, **kwargs: Any) -> str:
        return self._coll.create_index(keys, *args, **kwargs)

    def create_indexes(self, indexes: list, *args: Any, **kwargs: Any) -> list:
        return self._coll.create_indexes(indexes, *args, **kwargs)

    def list_indexes(self) -> list:
        return self._coll.list_indexes()

    def drop(self) -> None:
        self._coll.drop()

    @property
    def name(self) -> str:
        return self._coll.name

    def __getattr__(self, name: str) -> Any:
        return getattr(self._coll, name)


class SanitizedDatabase:
    def __init__(self, database):
        self._db = database
        self.long_term = SanitizedCollection(database.long_term)
        self.chats = SanitizedCollection(database.chats)

    @property
    def client(self):
        return self._db.client

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return SanitizedCollection(self._db[name])


class DatabaseManager:
    _instance: Optional["DatabaseManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._client: Optional[MongoClient] = None
        self._db: Optional[SanitizedDatabase] = None
        self._connect_lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._background_lock = threading.Lock()
        self._shutdown_event = threading.Event()

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _connect(self) -> MongoClient:
        uri = get_config().mongodb_uri
        if not uri:
            raise RuntimeError("MONGODB_URI is not configured")

        client = MongoClient(
            uri,
            tls=True,
            tlsAllowInvalidCertificates=True,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=30000,
            maxPoolSize=10,
            minPoolSize=1,
            retryWrites=True,
            retryReads=True,
            w="majority",
            journal=True,
        )
        client.admin.command("ping")
        return client

    def _ensure_indexes(self):
        try:
            existing = set()
            try:
                for idx in self._db.chats.list_indexes():
                    existing.add(idx.get("name", ""))
            except Exception:
                pass

            if "ttl_chat_expiry" not in existing:
                try:
                    self._db.chats.create_index(
                        [("last_activity", 1)],
                        name="ttl_chat_expiry",
                        expireAfterSeconds=86400 * 30,
                        background=True,
                    )
                except errors.OperationFailure:
                    pass

            if "chat_id_idx" not in existing:
                try:
                    self._db.chats.create_index(
                        [("chat_id", 1)],
                        name="chat_id_idx",
                        unique=True,
                        background=True,
                    )
                except errors.OperationFailure:
                    pass

            existing_lt = set()
            try:
                for idx in self._db.long_term.list_indexes():
                    existing_lt.add(idx.get("name", ""))
            except Exception:
                pass

            if "_id_" not in existing_lt:
                try:
                    self._db.long_term.create_index(
                        [("updated_at", 1)],
                        name="lt_updated_idx",
                        background=True,
                    )
                except errors.OperationFailure:
                    pass

        except Exception as exc:
            print(f"[DB] Index setup warning: {exc}")

    @property
    def executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            with self._background_lock:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=2,
                        thread_name_prefix="mongo_bg",
                    )
        return self._executor

    def get_db(self) -> Optional[SanitizedDatabase]:
        global USE_LOCAL_JSON

        if self._db is not None:
            return self._db

        uri = get_config().mongodb_uri
        if not uri:
            return None

        with self._connect_lock:
            if self._db is not None:
                return self._db
            try:
                client = self._connect()
                database = client.get_default_database()
                self._client = client
                self._db = SanitizedDatabase(database)
                self._ensure_indexes()
                return self._db
            except errors.ConnectionFailure as exc:
                print(f"[DB] Connection failure: {exc}")
            except errors.ConfigurationError as exc:
                print(f"[DB] Configuration error: {exc}")
            except Exception as exc:
                print(f"[DB] Failed to connect: {exc}")
        print("[DB] Falling back to local JSON storage")
        USE_LOCAL_JSON = True
        return None

    def health(self) -> dict:
        status = {
            "connected": False,
            "latency_ms": None,
            "error": None,
        }
        if self._client is None:
            status["error"] = "not_connected"
            return status
        try:
            start = time.monotonic()
            self._client.admin.command("ping")
            elapsed = (time.monotonic() - start) * 1000
            status["connected"] = True
            status["latency_ms"] = round(elapsed, 1)
        except Exception as exc:
            status["error"] = str(exc)
        return status

    def submit_background_write(self, fn, *args, **kwargs):
        future = self.executor.submit(fn, *args, **kwargs)
        return future

    def _local_json_chat_write(self, chat_id: str, messages: list, user_id: str = "", title: str = ""):
        dir_path = os.path.join("memory_data", "chats")
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, f"{chat_id}.json")
        existing = None
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "chat_id": chat_id,
            "messages": messages,
            "last_activity": now,
        }
        if user_id:
            data["user_id"] = user_id
        if title:
            data["title"] = title
        elif existing and existing.get("title"):
            data["title"] = existing["title"]
        if existing and "created_at" in existing:
            data["created_at"] = existing["created_at"]
        else:
            data["created_at"] = now
        # Update sessions index
        if user_id:
            idx = self._load_sessions_index(user_id)
            found = False
            for s in idx:
                if s.get("chat_id") == chat_id:
                    s["last_activity"] = now
                    s["title"] = title or s.get("title", "")
                    s["message_count"] = len(messages)
                    found = True
                    break
            if not found:
                idx.append({
                    "chat_id": chat_id,
                    "title": title or "New Chat",
                    "user_id": user_id,
                    "created_at": now,
                    "last_activity": now,
                    "message_count": len(messages),
                })
            idx.sort(key=lambda x: x.get("last_activity", ""), reverse=True)
            self._save_sessions_index(user_id, idx)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _local_json_long_term_write(self, data: dict):
        dir_path = "memory_data"
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, "long_term.json")
        data["_id"] = "main"
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _chats_dir(self):
        return os.path.join("memory_data", "chats")

    def _sessions_index_file(self, user_id: str):
        os.makedirs(os.path.join("memory_data", "users", user_id), exist_ok=True)
        return os.path.join("memory_data", "users", user_id, "sessions_index.json")

    def _load_sessions_index(self, user_id: str) -> list:
        fpath = self._sessions_index_file(user_id)
        try:
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[DB] Failed to load sessions index: {exc}")
        return []

    def _save_sessions_index(self, user_id: str, sessions: list):
        try:
            fpath = self._sessions_index_file(user_id)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            tmp = fpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sessions, f, indent=2, ensure_ascii=False)
            os.replace(tmp, fpath)
        except OSError as exc:
            print(f"[DB] Failed to save sessions index: {exc}")

    def list_sessions(self, user_id: str) -> list:
        if not user_id:
            return []
        if USE_LOCAL_JSON:
            return self._load_sessions_index(user_id)
        db = self.get_db()
        if db is None:
            return self._load_sessions_index(user_id)
        try:
            cursor = db.chats.find(
                {"user_id": user_id},
                {"messages": 0},
            ).sort("last_activity", -1)
            return list(cursor)
        except Exception as exc:
            print(f"[DB] Failed to list sessions for '{user_id}': {exc}")
            return self._load_sessions_index(user_id)

    def get_session(self, chat_id: str) -> dict:
        if not USE_LOCAL_JSON:
            db = self.get_db()
            if db is not None:
                try:
                    doc = db.chats.find_one({"chat_id": chat_id})
                    if doc:
                        return doc
                except Exception as exc:
                    print(f"[DB] Failed to get session '{chat_id}': {exc}")
        fpath = os.path.join(self._chats_dir(), f"{chat_id}.json")
        try:
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[DB] Failed to load session '{chat_id}': {exc}")
        return {}

    def delete_session(self, chat_id: str, user_id: str = ""):
        if USE_LOCAL_JSON:
            fpath = os.path.join(self._chats_dir(), f"{chat_id}.json")
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except OSError as exc:
                print(f"[DB] Failed to delete session file '{chat_id}': {exc}")
            if user_id:
                idx = self._load_sessions_index(user_id)
                idx = [s for s in idx if s.get("chat_id") != chat_id]
                self._save_sessions_index(user_id, idx)
            return
        db = self.get_db()
        if db is None:
            return
        try:
            db.chats.delete_one({"chat_id": chat_id})
        except Exception as exc:
            print(f"[DB] Failed to delete session '{chat_id}': {exc}")

    def background_chat_write(self, chat_id: str, messages: list, user_id: str = "", title: str = ""):
        if USE_LOCAL_JSON:
            self._local_json_chat_write(chat_id, messages, user_id, title)
            return
        db = self.get_db()
        if db is None:
            return
        try:
            sanitized = sanitize_document(messages)
            existing = db.chats.find_one({"chat_id": chat_id})
            now = datetime.now(timezone.utc)
            doc = {
                "chat_id": chat_id,
                "messages": sanitized,
                "last_activity": now,
            }
            if user_id:
                doc["user_id"] = user_id
            if title:
                doc["title"] = title
            elif existing and existing.get("title"):
                doc["title"] = existing["title"]
            if not existing:
                doc["created_at"] = now
            elif "created_at" in existing:
                doc["created_at"] = existing["created_at"]
            doc["message_count"] = len(messages)
            db.chats.replace_one(
                {"chat_id": chat_id},
                doc,
                upsert=True,
            )
        except Exception as exc:
            print(f"[DB] Background chat write failed for '{chat_id}': {exc}")

    def update_session_title(self, chat_id: str, title: str):
        if USE_LOCAL_JSON:
            fpath = os.path.join(self._chats_dir(), f"{chat_id}.json")
            user_id = ""
            try:
                if os.path.exists(fpath):
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["title"] = title
                    user_id = str(data.get("user_id") or "")
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[DB] Failed to update session title '{chat_id}': {exc}")
            if user_id:
                idx = self._load_sessions_index(user_id)
                for s in idx:
                    if s.get("chat_id") == chat_id:
                        s["title"] = title
                        break
                self._save_sessions_index(user_id, idx)
            return
        db = self.get_db()
        if db is None:
            return
        try:
            db.chats.update_one(
                {"chat_id": chat_id},
                {"$set": {"title": title, "last_activity": datetime.now(timezone.utc)}},
            )
        except Exception as exc:
            print(f"[DB] Failed to update session title '{chat_id}': {exc}")

    def background_long_term_write(self, data: dict):
        if USE_LOCAL_JSON:
            self._local_json_long_term_write(data)
            return
        db = self.get_db()
        if db is None:
            return
        try:
            sanitized = sanitize_document(data)
            sanitized["_id"] = "main"
            sanitized["updated_at"] = datetime.now(timezone.utc)
            db.long_term.replace_one({"_id": "main"}, sanitized, upsert=True)
        except Exception as exc:
            print(f"[DB] Background long-term write failed: {exc}")

    def shutdown(self, wait: bool = True):
        self._shutdown_event.set()
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass


_db_manager = DatabaseManager.get_instance()


def get_db() -> Optional[SanitizedDatabase]:
    return _db_manager.get_db()


def get_db_manager() -> DatabaseManager:
    return _db_manager
