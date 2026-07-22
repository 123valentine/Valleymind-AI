"""MongoDB persistence layer -- durable storage for chats, users, and auth tokens.

Local JSON files under memory_data/ are not durable in production (the
container filesystem is ephemeral), so this module is the source of truth
whenever MONGODB_URI is configured and reachable. Every public function
returns None/False on any failure instead of raising, so callers can fall
back to the existing local-JSON behavior without special-casing exceptions.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from core.config import get_config

_client_lock = threading.Lock()
_client = None
_db = None
_indexes_ready = False


def get_db():
    """Returns the Mongo database, or None if unavailable. Never raises."""
    global _client, _db, _indexes_ready

    if _db is not None:
        return _db

    uri = get_config().mongodb_uri
    if not uri:
        return None

    with _client_lock:
        if _db is not None:
            return _db
        try:
            from pymongo import MongoClient

            # mongodb+srv:// already implies TLS with standard certificate
            # verification -- no need to weaken it with
            # tlsAllowInvalidCertificates.
            client = MongoClient(
                uri,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
                # Generous socket timeout so multi-MB GridFS video uploads don't
                # abort mid-write on slower links (videos can be ~10MB+).
                socketTimeoutMS=120000,
                maxPoolSize=10,
                minPoolSize=1,
                retryWrites=True,
                retryReads=True,
                w="majority",
                journal=True,
            )
            client.admin.command("ping")
            # The configured URI has no /<db-name> path segment, so fall
            # back to an explicit default instead of letting pymongo raise.
            database = client.get_default_database(default="valleymind_db")
            _client = client
            _db = database
        except Exception as exc:
            print(f"[DB] Mongo unavailable, using local JSON fallback: {exc}")
            return None

    if not _indexes_ready:
        _ensure_indexes(_db)
        _indexes_ready = True

    return _db


def _ensure_indexes(db) -> None:
    try:
        db.chats.create_index("chat_id", name="chat_id_unique", unique=True, background=True)
        db.chats.create_index("user_id", name="user_id_idx", background=True)
    except Exception as exc:
        print(f"[DB] chats index setup warning: {exc}")

    try:
        db.users.create_index("user_id", name="user_id_unique", unique=True, background=True)
    except Exception as exc:
        print(f"[DB] users index setup warning: {exc}")

    try:
        db.auth_tokens.create_index(
            "created_at", name="ttl_auth_token", expireAfterSeconds=2592000, background=True
        )
    except Exception as exc:
        print(f"[DB] auth_tokens index setup warning: {exc}")


def chats_collection():
    db = get_db()
    return db.chats if db is not None else None


def users_collection():
    db = get_db()
    return db.users if db is not None else None


def auth_tokens_collection():
    db = get_db()
    return db.auth_tokens if db is not None else None


def app_config_collection():
    db = get_db()
    return db.app_config if db is not None else None


def user_memory_collection():
    db = get_db()
    return db.user_memory if db is not None else None


def studio_runs_collection():
    """Last Studio run per user, so the surface survives a page reload."""
    db = get_db()
    return db.studio_runs if db is not None else None


def usage_collection():
    """Per-user generation counters (images, videos)."""
    db = get_db()
    return db.usage if db is not None else None


def studio_jobs_collection():
    """Async video jobs — per-clip state so a trailer survives a browser close
    and can be resumed by the poll endpoint."""
    db = get_db()
    return db.studio_jobs if db is not None else None


def video_spend_collection():
    """Cumulative video spend for the hard budget cap (single 'global' doc)."""
    db = get_db()
    return db.video_spend if db is not None else None
