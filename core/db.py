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
            tlsCAFile=certifi.where(),
            tlsAllowInvalidCertificates=True,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000,
            socketTimeoutMS=10000,
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

    def _local_json_chat_write(self, chat_id: str, messages: list):
        dir_path = os.path.join("memory_data", "chats")
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, f"{chat_id}.json")
        data = {
            "chat_id": chat_id,
            "messages": messages,
            "last_activity": datetime.now(timezone.utc).isoformat(),
        }
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

    def background_chat_write(self, chat_id: str, messages: list):
        if USE_LOCAL_JSON:
            self._local_json_chat_write(chat_id, messages)
            return
        db = self.get_db()
        if db is None:
            return
        try:
            sanitized = sanitize_document(messages)
            db.chats.replace_one(
                {"chat_id": chat_id},
                {
                    "chat_id": chat_id,
                    "messages": sanitized,
                    "last_activity": datetime.now(timezone.utc),
                },
                upsert=True,
            )
        except Exception as exc:
            print(f"[DB] Background chat write failed for '{chat_id}': {exc}")

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
