"""Enhanced Flask app with Pinecone-backed session handling.

This file combines the clean, modern structure from valleymind-backend/app.py
with the advanced session handling functions from the previous version,
adapted for Pinecone-backed architecture.
"""

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_from_directory, session, stream_with_context
from werkzeug.security import check_password_hash, generate_password_hash

from core.brain import MarcusBrain, _call_llm_cluster, _CHAT_SYSTEM_PROMPT
from core.config import PROJECT_ROOT, get_config
from core.db import auth_tokens_collection, app_config_collection, chats_collection, get_db, studio_runs_collection, usage_collection, users_collection
from core.media_manager import get_media_manager
from core.router import RouteDecision, get_router
from core.tts import speak_marcus
from core.video_dispatcher import get_video_dispatcher
import core.provider_manager as pm

# ── Load .env for local dev ──────────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip().strip("\"'")
            if _key and not os.environ.get(_key):
                os.environ[_key] = _val



app = Flask(__name__, static_folder=str(PROJECT_ROOT / "static"), static_url_path='/static')
print(f"[TRACE BOOT] app.root_path = {app.root_path}")
print(f"[TRACE BOOT] app.static_folder = {app.static_folder}")
print(f"[TRACE BOOT] PROJECT_ROOT = {PROJECT_ROOT}")
app.permanent_session_lifetime = timedelta(days=30)

# ── CORS (commented out — frontend is now served from the same origin) ──────
# from flask_cors import CORS
# allowed_origins = [
#     "http://127.0.0.1:3000",
#     "http://localhost:3000",
#     "https://valleymind-ai.vercel.app",
# ]
# CORS(app, supports_credentials=True, origins=allowed_origins)

# Cache Marcus per authenticated user so memory never leaks across accounts.
_cache_marcus_by_user = {}
_auth_tokens = {}
_suggestion_times = {}
_marcus_lock = Lock()
_users_lock = Lock()
_users_file = PROJECT_ROOT / "memory_data" / "auth_users.json"
_session_secret_file = PROJECT_ROOT / "memory_data" / "session_secret.key"
_tts_folder = PROJECT_ROOT / "memory_data" / "tts"
_suggestions_file = PROJECT_ROOT / "memory_data" / "suggestions.json"
_admin_whatsapp_number = "234915170571"


def _get_auth_token(token: str) -> dict:
    """Resolve a bearer token to its auth record. Mongo first, in-memory cache as fallback."""
    if not token:
        return {}
    coll = auth_tokens_collection()
    if coll is not None:
        try:
            doc = coll.find_one({"_id": token})
            if doc:
                auth = {
                    "user_id": doc.get("user_id", ""),
                    "email": doc.get("email", ""),
                    "is_creator": doc.get("is_creator", False),
                }
                _auth_tokens[token] = auth
                return auth
        except Exception as exc:
            print(f"[ERROR] Mongo _get_auth_token failed, using local cache: {exc}")
    return _auth_tokens.get(token, {})


def _set_auth_token(token: str, data: dict):
    """Persist a bearer token so it survives process restarts, not just in-memory."""
    _auth_tokens[token] = data
    coll = auth_tokens_collection()
    if coll is not None:
        try:
            doc = dict(data)
            doc["_id"] = token
            doc["created_at"] = datetime.now(timezone.utc)
            coll.replace_one({"_id": token}, doc, upsert=True)
        except Exception as exc:
            print(f"[ERROR] Mongo _set_auth_token failed, token cached locally only: {exc}")


def _delete_auth_token(token: str):
    _auth_tokens.pop(token, None)
    coll = auth_tokens_collection()
    if coll is not None:
        try:
            coll.delete_one({"_id": token})
        except Exception as exc:
            print(f"[ERROR] Mongo _delete_auth_token failed: {exc}")


def _load_session_secret() -> str:
    configured = os.getenv("SECRET_KEY", "").strip() or os.getenv("FLASK_SECRET_KEY", "").strip()
    if configured:
        return configured

    coll = app_config_collection()
    if coll is not None:
        try:
            doc = coll.find_one({"_id": "session_secret"})
            if not doc or not doc.get("value"):
                # $setOnInsert so concurrent workers racing here converge on
                # one winner's value instead of each generating their own.
                coll.update_one(
                    {"_id": "session_secret"},
                    {"$setOnInsert": {"value": secrets.token_hex(32)}},
                    upsert=True,
                )
                doc = coll.find_one({"_id": "session_secret"})
            if doc and doc.get("value"):
                return str(doc["value"])
        except Exception as exc:
            print(f"[WARNING] Mongo session secret unavailable, falling back to local file: {exc}")

    try:
        _session_secret_file.parent.mkdir(parents=True, exist_ok=True)
        if _session_secret_file.exists():
            return _session_secret_file.read_text(encoding="utf-8").strip()
        generated = secrets.token_hex(32)
        _session_secret_file.write_text(generated, encoding="utf-8")
        return generated
    except OSError as exc:
        print(f"[WARNING] Failed to persist Flask session secret: {exc}")
        return secrets.token_hex(32)


app.secret_key = _load_session_secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "None"),
    SESSION_COOKIE_SECURE=True,
)


def _safe_user_id(email: str) -> str:
    normalized = (email or "").strip().lower()
    local = normalized.split("@", 1)[0]
    if local.startswith("test_user_"):
        suffix = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return f"test_user_{suffix}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _load_users() -> dict:
    coll = users_collection()
    if coll is not None:
        try:
            users = {}
            for doc in coll.find({}):
                email = doc.pop("_id", None)
                if email:
                    users[email] = doc
            return users
        except Exception as exc:
            print(f"[ERROR] Mongo _load_users failed, falling back to local file: {exc}")

    try:
        if _users_file.exists():
            with open(_users_file, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[ERROR] Failed to load auth users: {exc}")
    return {}


def _save_users(users: dict):
    coll = users_collection()
    if coll is not None:
        try:
            for email, record in users.items():
                doc = dict(record)
                doc["_id"] = email
                coll.replace_one({"_id": email}, doc, upsert=True)
            return
        except Exception as exc:
            print(f"[ERROR] Mongo _save_users failed, falling back to local file: {exc}")

    try:
        _users_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(_users_file) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(users, file, indent=2)
        os.replace(tmp, _users_file)
    except OSError as exc:
        print(f"[ERROR] Failed to save auth users: {exc}")


def _current_auth() -> dict:
    print(f"[DEBUG] _current_auth: Session: {dict(session)}")
    user_id = str(session.get("user_id") or "").strip()
    email = str(session.get("email") or "").strip()
    if user_id:
        return {"user_id": user_id, "email": email}

    # Fallback 1: headers (X-Session-Token / Authorization: Bearer)
    token = str(
        request.headers.get("X-Session-Token")
        or request.headers.get("Authorization", "").replace("Bearer ", "", 1)
        or ""
    ).strip()
    if token:
        auth = _get_auth_token(token)
        if auth and auth.get("user_id"):
            session.permanent = True
            session["user_id"] = auth.get("user_id", "")
            session["email"] = auth.get("email", "")
            session["user"] = {"id": auth.get("user_id", ""), "email": auth.get("email", "")}
            return auth

    # Fallback 2: POST JSON body (for clients that cannot send cookies on POST)
    if request.method == "POST" and request.content_type and "json" in request.content_type:
        try:
            body_token = str((request.get_json(silent=True) or {}).get("session_token") or "").strip()
            if body_token:
                auth = _get_auth_token(body_token)
                if auth and auth.get("user_id"):
                    session.permanent = True
                    session["user_id"] = auth.get("user_id", "")
                    session["email"] = auth.get("email", "")
                    session["user"] = {"id": auth.get("user_id", ""), "email": auth.get("email", "")}
                    return auth
        except Exception:
            pass

    return {}


def _current_user_id() -> str:
    return str(_current_auth().get("user_id") or "").strip()


def _require_login():
    user_id = _current_user_id()
    if not user_id:
        return "", (jsonify({"status": "error", "message": "Login required"}), 401)
    return user_id, None


def _append_suggestion(entry: dict):
    try:
        _suggestions_file.parent.mkdir(parents=True, exist_ok=True)
        suggestions = []
        if _suggestions_file.exists():
            with open(_suggestions_file, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                suggestions = data
        suggestions.append(entry)
        tmp = str(_suggestions_file) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(suggestions[-500:], file, indent=2)
        os.replace(tmp, _suggestions_file)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[ERROR] Failed to save suggestion: {exc}")


def _sanitize_suggestion(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def _suggestion_rate_limited(user_id: str) -> bool:
    now = datetime.now()
    window_start = now - timedelta(minutes=1)
    recent = [
        item for item in _suggestion_times.get(user_id, [])
        if item > window_start
    ]
    _suggestion_times[user_id] = recent
    if len(recent) >= 5:
        return True
    recent.append(now)
    return False


def _whatsapp_url(email: str, text: str) -> str:
    message = (
        "Valleymind-AI suggestion\n"
        f"From: {email or 'unknown user'}\n"
        f"Message: {text}"
    )
    return f"https://wa.me/{_admin_whatsapp_number}?text={quote(message)}"


CREATOR_EMAIL = "egbujievalentine@gmail.com"
CREATOR_NAME = "Egbujie Valentine (K)"
CREATOR_TITLE = "Founder and Head of Valley Mind-AI"

DEFAULT_SECURITY_QUESTION = "What is your creator code project?"
DEFAULT_SECURITY_ANSWER = "valley mind-ai"


def _is_creator(email: str) -> bool:
    return str(email or "").strip().lower() == CREATOR_EMAIL


def _derive_initial_user_name(email: str) -> str:
    if _is_creator(email):
        return CREATOR_NAME
    local = str(email or "").split("@", 1)[0].strip().lower()
    if not local:
        return ""
    if "valentine" in local:
        return "Valentine"
    cleaned = re.sub(r"[^a-z]+", " ", local).strip()
    if not cleaned:
        return ""
    return cleaned.split()[-1].capitalize()


# ── SESSION HANDLING FUNCTIONS (adapted for Pinecone-backed architecture) ───────────────────────────────────────────────

def _stringify_timestamp(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _normalize_session_doc(doc: dict) -> dict:
    """Normalize session records from chat_sessions or chats collections."""
    if not isinstance(doc, dict):
        return {}
    chat_id = str(doc.get("chat_id") or doc.get("session_id") or "").strip()
    if not chat_id:
        return {}
    title = str(doc.get("title") or "Untitled Thread").strip() or "Untitled Thread"
    last_updated = _stringify_timestamp(
        doc.get("last_updated") or doc.get("last_activity") or doc.get("created_at")
    )
    created_at = _stringify_timestamp(doc.get("created_at")) or last_updated
    try:
        message_count = int(doc.get("message_count") or 0)
    except (TypeError, ValueError):
        message_count = 0
    return {
        "chat_id": chat_id,
        "session_id": chat_id,
        "title": title,
        "message_count": message_count,
        "last_updated": last_updated,
        "created_at": created_at,
    }


def _session_sort_key(session: dict):
    raw = session.get("last_updated") or session.get("created_at") or ""
    if hasattr(raw, "isoformat"):
        return raw.isoformat()
    return str(raw)


def _merge_session_records(existing: dict, incoming: dict) -> dict:
    """Merge two normalized session records, preferring richer metadata."""
    merged = dict(existing)
    generic_titles = {"", "New Chat", "Untitled Thread"}
    if incoming.get("title") and incoming["title"] not in generic_titles:
        if merged.get("title") in generic_titles or not merged.get("title"):
            merged["title"] = incoming["title"]
    merged["message_count"] = max(
        int(merged.get("message_count") or 0),
        int(incoming.get("message_count") or 0),
    )
    if _session_sort_key(incoming) > _session_sort_key(merged):
        merged["last_updated"] = incoming.get("last_updated") or merged.get("last_updated")
    return merged


# ── SESSION INDEX HANDLING FOR PINECONE BACKED ARCHITECTURE ──────────────────────────────────────────────────────────────

_sessions_index_template = str(PROJECT_ROOT / "memory_data" / "users" / "{user_id}" / "sessions_index.json")


def _sessions_index_path(user_id: str):
    return Path(_sessions_index_template.replace("{user_id}", user_id))


def _load_sessions_index(user_id: str) -> list:
    fpath = _sessions_index_path(user_id)
    try:
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[ERROR] Failed to load sessions index: {exc}")
    return []


def _save_sessions_index(user_id: str, sessions: list):
    try:
        fpath = _sessions_index_path(user_id)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(fpath) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)
        os.replace(tmp, fpath)
    except OSError as exc:
        print(f"[ERROR] Failed to save sessions index: {exc}")


def _list_user_sessions(user_id: str) -> list:
    """List all sessions for a user, sorted by last_updated descending."""
    if not user_id:
        return []

    coll = chats_collection()
    if coll is not None:
        try:
            cursor = coll.find({"user_id": user_id}, {"messages": 0}).sort("last_activity", -1)
            normalized = [_normalize_session_doc(doc) for doc in cursor]
            return [doc for doc in normalized if doc]
        except Exception as exc:
            print(f"[ERROR] Mongo _list_user_sessions failed, falling back to local index: {exc}")

    try:
        sessions = _load_sessions_index(user_id)
        normalized_sessions = []
        for doc in sessions:
            normalized = _normalize_session_doc(doc)
            if normalized:
                normalized_sessions.append(normalized)
        merged_sessions = []
        seen_ids = set()
        for session_doc in normalized_sessions:
            chat_id = session_doc.get("chat_id")
            if chat_id and chat_id not in seen_ids:
                seen_ids.add(chat_id)
                merged_sessions.append(session_doc)
            elif chat_id and chat_id in seen_ids:
                for existing in merged_sessions:
                    if existing.get("chat_id") == chat_id:
                        merged = _merge_session_records(existing, session_doc)
                        existing.clear()
                        existing.update(merged)
        merged_sessions.sort(key=_session_sort_key, reverse=True)
        return merged_sessions
    except Exception as exc:
        print(f"[ERROR] Failed to list user sessions: {exc}")
        return []


def _upsert_chat_session_meta(user_id: str, chat_id: str, title: str = "", message_count: int = 0):
    """Update or create session metadata in sessions index."""
    coll = chats_collection()
    if coll is not None:
        try:
            now = datetime.now(timezone.utc)
            set_fields = {"user_id": user_id, "last_activity": now}
            if title:
                set_fields["title"] = title
            update = {
                "$set": set_fields,
                "$max": {"message_count": message_count},
                "$setOnInsert": {"chat_id": chat_id, "created_at": now},
            }
            if not title:
                update["$setOnInsert"]["title"] = "New Chat"
            coll.update_one({"chat_id": chat_id}, update, upsert=True)
            return
        except Exception as exc:
            print(f"[ERROR] Mongo _upsert_chat_session_meta failed, falling back to local index: {exc}")

    try:
        now = datetime.now(timezone.utc).isoformat()
        sessions = _load_sessions_index(user_id)
        found = False
        for session_doc in sessions:
            if session_doc.get("chat_id") == chat_id:
                if title:
                    session_doc["title"] = title
                session_doc["last_activity"] = now
                session_doc["message_count"] = max(session_doc.get("message_count", 0), message_count)
                found = True
                break
        if not found:
            session_doc = {
                "chat_id": chat_id,
                "title": title or "New Chat",
                "user_id": user_id,
                "created_at": now,
                "last_activity": now,
                "message_count": message_count,
            }
            sessions.append(session_doc)
        sessions.sort(key=lambda x: x.get("last_activity", ""), reverse=True)
        _save_sessions_index(user_id, sessions)
    except Exception as exc:
        print(f"[ERROR] Failed to upsert session meta: {exc}")


def _delete_chat_session_meta(user_id: str, chat_id: str):
    """Delete session metadata from sessions index."""
    coll = chats_collection()
    if coll is not None:
        try:
            coll.delete_one({"chat_id": chat_id, "user_id": user_id})
            return
        except Exception as exc:
            print(f"[ERROR] Mongo _delete_chat_session_meta failed, falling back to local index: {exc}")

    try:
        sessions = _load_sessions_index(user_id)
        sessions = [s for s in sessions if s.get("chat_id") != chat_id]
        _save_sessions_index(user_id, sessions)
    except Exception as exc:
        print(f"[ERROR] Failed to delete session meta: {exc}")


# ── REST OF THE MODERN APP (adapted from valleymind-backend/app.py) ────────────────────────────────────────────────

def load_marcus(user_id: str):
    user_id = str(user_id or "").strip()
    if not user_id:
        return None

    return load_persona_brain(user_id, "marcus")


VALID_PERSONAS = ("marcus", "elena", "angelina")


def normalize_persona(value: str) -> str:
    """Only the three known crew members; anything else falls back to Marcus."""
    p = str(value or "").strip().lower()
    return p if p in VALID_PERSONAS else "marcus"


def load_persona_brain(user_id: str, persona: str = "marcus"):
    """Brain for a specific persona. Personality comes from that character's
    behavior.json, but long-term memory stays SHARED across all three — who is
    speaking changes the voice, not what the assistant knows about the user."""
    user_id = str(user_id or "").strip()
    if not user_id:
        return None
    persona = normalize_persona(persona)
    cache_key = f"{user_id}:{persona}"

    with _marcus_lock:
        cached = _cache_marcus_by_user.get(cache_key)
    if cached is not None:
        return cached

    behavior_path = PROJECT_ROOT / "character" / persona / "behavior.json"
    # Shared memory path for every persona (deliberately the marcus folder) so
    # user facts never fragment per-voice.
    memory_path = PROJECT_ROOT / "memory_data" / "users" / user_id / "marcus" / "long_term.json"

    if not behavior_path.exists():
        print(f"[ERROR] behavior.json not found for persona '{persona}' at {behavior_path}")
        return None

    try:
        brain = MarcusBrain(
            memory_file=str(memory_path),
            behavior_file=str(behavior_path),
        )
        with _marcus_lock:
            _cache_marcus_by_user[cache_key] = brain
        return brain
    except Exception as exc:
        print(f"[ERROR] Failed to instantiate '{persona}' brain: {exc}")
        return None


def _refresh_marcus_memory(marcus):
    try:
        marcus.memory.reload()
    except Exception as exc:
        print(f"[ERROR] Failed to refresh Marcus memory: {exc}")


def _initialize_user_memory(marcus, email: str):
    _refresh_marcus_memory(marcus)
    try:
        if not marcus.memory.get_user_name():
            marcus.memory.initialize_user_name(_derive_initial_user_name(email))
            marcus.memory.reload()
    except Exception as exc:
        print(f"[ERROR] Failed to initialize user memory: {exc}")


def _debug_user_memory(user_id: str, marcus):
    try:
        print("USER_ID:", user_id)
        print("USER_NAME:", marcus.memory.get_user_name())
    except Exception as exc:
        print(f"[ERROR] Failed to print memory debug logs: {exc}")


@app.route("/auth/status", methods=["GET"])
def auth_status():
    auth = _current_auth()
    user_id = str(auth.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"authenticated": False})

    marcus = load_marcus(user_id)
    if marcus:
        _initialize_user_memory(marcus, auth.get("email", ""))
    email_auth = auth.get("email", "")
    return jsonify({
        "authenticated": True,
        "email": email_auth,
        "user_id": user_id,
        "character": "marcus",
        "memory_loaded": bool(marcus),
        "is_creator": _is_creator(email_auth),
        "video_generation_enabled": _video_generation_enabled(),
    })


def _new_chat_id() -> str:
    return f"marcus_{secrets.token_hex(8)}"


@app.route("/chat/history", methods=["GET"])
def chat_history():
    user_id, error = _require_login()
    print(f"[DEBUG] ChatHistory: user_id: {user_id}, error: {error}")
    if error:
        return error

    marcus = load_marcus(user_id)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus is not configured"}), 404
    _refresh_marcus_memory(marcus)

    chat_id = str(request.args.get("chat_id") or "").strip() or f"{marcus.profile.key}_main_chat"
    messages = marcus.memory.get_chat(chat_id)
    return jsonify({"status": "success", "messages": messages})


@app.route("/api/chat/messages", methods=["GET"])
def api_chat_messages_alias():
    user_id, error = _require_login()
    if error:
        return error

    marcus = load_marcus(user_id)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus is not configured"}), 404

    _refresh_marcus_memory(marcus)

    chat_id = str(
        request.args.get("session_id")
        or request.args.get("chat_id")
        or ""
    ).strip() or f"{marcus.profile.key}_main_chat"

    messages = marcus.memory.get_chat(chat_id)
    return jsonify({"status": "success", "messages": messages})


@app.route("/chat/sessions", methods=["GET"])
def chat_sessions():
    user_id, error = _require_login()
    if error:
        return error
    if not user_id:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        sessions = _list_user_sessions(user_id)
        return jsonify({"status": "success", "sessions": sessions})
    except Exception as exc:
        print(f"[ERROR] /chat/sessions failed: {exc}")
        return jsonify({
            "status": "error",
            "message": "Failed to load sessions",
            "sessions": [],
        }), 500


@app.route("/chat/sessions", methods=["POST"])
def chat_create_session():
    user_id, error = _require_login()
    if error:
        return error
    marcus = load_marcus(user_id)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus not configured"}), 404

    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "New Chat").strip()
    chat_id = str(data.get("chat_id") or "").strip() or _new_chat_id()

    try:
        session = marcus.memory.create_session(chat_id, title)
        _upsert_chat_session_meta(
            user_id,
            session["chat_id"],
            title=session["title"],
            message_count=0,
        )
        return jsonify({"status": "success", "session": {
            "chat_id": session["chat_id"],
            "session_id": session["chat_id"],
            "title": session["title"],
            "created_at": session["created_at"],
            "last_activity": session["last_activity"],
            "last_updated": session["last_activity"],
            "message_count": 0,
        }})
    except Exception as exc:
        print(f"[ERROR] Failed to create session: {exc}")
        return jsonify({"status": "error", "message": "Failed to create session"}), 500


@app.route("/chat/session/rename", methods=["POST"])
def rename_chat_session():
    user_id, error = _require_login()
    if error:
        return error
    marcus = load_marcus(user_id)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus not configured"}), 404

    data = request.get_json(silent=True) or {}
    chat_id = str(data.get("chat_id") or "").strip()
    new_title = str(data.get("title") or "").strip()

    if not chat_id or not new_title:
        return jsonify({"status": "error", "message": "chat_id and title are required"}), 400

    try:
        if hasattr(marcus.memory, "set_title"):
            marcus.memory.set_title(chat_id, new_title)
        elif hasattr(marcus.memory, "db_manager"):
            marcus.memory.db_manager.update_session_title(chat_id, new_title)
        _upsert_chat_session_meta(user_id, chat_id, title=new_title)
        return jsonify({"status": "success", "message": "Session renamed"})
    except Exception as exc:
        print(f"[ERROR] Failed to rename session '{chat_id}': {exc}")
        return jsonify({"status": "error", "message": "Failed to rename session"}), 500


@app.route("/chat/sessions/<chat_id>", methods=["DELETE"])
def chat_delete_session(chat_id):
    user_id, error = _require_login()
    if error:
        return error
    marcus = load_marcus(user_id)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus not configured"}), 404
    try:
        marcus.memory.delete_session(chat_id)
        _delete_chat_session_meta(user_id, chat_id)
        return jsonify({"status": "success"})
    except Exception as exc:
        print(f"[ERROR] Failed to delete session '{chat_id}': {exc}")
        return jsonify({"status": "error", "message": "Failed to delete session"}), 500


@app.route("/chat/sessions/<chat_id>/reaction", methods=["POST"])
def chat_session_reaction(chat_id):
    user_id, error = _require_login()
    if error:
        return error
    marcus = load_marcus(user_id)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus not configured"}), 404

    data = request.get_json(silent=True) or {}
    message_index = data.get("message_index")
    if message_index is None or not isinstance(message_index, int):
        return jsonify({"status": "error", "message": "message_index (int) is required"}), 400
    reaction = str(data.get("reaction") or "").strip() or ""
    if reaction not in ("up", "down", ""):
        return jsonify({"status": "error", "message": "reaction must be 'up', 'down', or empty"}), 400

    try:
        ok = marcus.memory.update_reaction(chat_id, message_index, reaction)
        if not ok:
            return jsonify({"status": "error", "message": "Invalid message_index"}), 404
        return jsonify({"status": "success"})
    except Exception as exc:
        print(f"[ERROR] Failed to update reaction: {exc}")
        return jsonify({"status": "error", "message": "Failed to update reaction"}), 500


@app.route("/suggestions", methods=["POST"])
def suggestions():
    user_id, error = _require_login()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    text = _sanitize_suggestion(data.get("text") or "")
    if not text:
        return jsonify({"status": "error", "message": "Suggestion is required"}), 400
    if _suggestion_rate_limited(user_id):
        return jsonify({"status": "error", "message": "Please wait before sending another suggestion"}), 429

    auth = _current_auth()
    _append_suggestion({
        "user_id": user_id,
        "email": auth.get("email", ""),
        "text": text,
        "time": datetime.now().isoformat(),
    })
    return jsonify({
        "status": "success",
        "whatsapp_url": _whatsapp_url(auth.get("email", ""), text),
    })


@app.route("/tts/<path:filename>", methods=["GET"])
def tts_file(filename):
    return send_from_directory(_tts_folder, filename)


@app.route("/login", methods=["POST"])
@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"status": "error", "message": "Valid email is required"}), 400
    if not password:
        return jsonify({"status": "error", "message": "Password is required"}), 400

    user_id = _safe_user_id(email)
    is_creator = _is_creator(email)
    with _users_lock:
        users = _load_users()
        user = users.get(email)

        if user:
            stored_hash = str(user.get("password_hash") or "")
            if not check_password_hash(stored_hash, password):
                return jsonify({"status": "error", "message": "Invalid email or password"}), 401
            if is_creator:
                user["identity_name"] = CREATOR_NAME
                user["title"] = CREATOR_TITLE
                _save_users(users)
        else:
            users[email] = {
                "user_id": user_id,
                "password_hash": generate_password_hash(password),
                "security_question": DEFAULT_SECURITY_QUESTION,
                "security_answer_hash": generate_password_hash(DEFAULT_SECURITY_ANSWER),
            }
            if is_creator:
                users[email]["identity_name"] = CREATOR_NAME
                users[email]["title"] = CREATOR_TITLE
            _save_users(users)

    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["email"] = email
    session["is_creator"] = is_creator
    session["user"] = {"id": user_id, "email": email, "is_creator": is_creator}
    if is_creator:
        session["user"]["identity_name"] = CREATOR_NAME
        session["user"]["title"] = CREATOR_TITLE

    token = secrets.token_urlsafe(32)
    _set_auth_token(token, {"user_id": user_id, "email": email, "is_creator": is_creator})

    marcus = load_marcus(user_id)
    if marcus:
        _initialize_user_memory(marcus, email)
        if is_creator:
            try:
                marcus.memory.set_creator_identity(CREATOR_NAME, CREATOR_TITLE)
            except Exception as exc:
                print(f"[WARN] Failed to set creator identity in memory: {exc}")
    return jsonify({
        "status": "success",
        "authenticated": True,
        "email": email,
        "character": "marcus",
        "session_token": token,
        "is_creator": is_creator,
    })


@app.route("/api/auth/google", methods=["POST"])
def google_auth():
    data = request.get_json(silent=True) or {}
    credential = str(data.get("credential") or "").strip()

    if not credential:
        return jsonify({"status": "error", "message": "Credential token is required"}), 400

    google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not google_client_id:
        return jsonify({"status": "error", "message": "Google auth is not configured"}), 500

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        idinfo = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            google_client_id,
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": f"Invalid or expired token: {exc}"}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Token verification failed: {exc}"}), 400

    google_id = str(idinfo.get("sub") or "")
    email = str(idinfo.get("email") or "").strip().lower()
    name = str(idinfo.get("name") or email.split("@")[0] if email else "User")
    picture = str(idinfo.get("picture") or "")

    if not email:
        return jsonify({"status": "error", "message": "Email not provided by Google"}), 400
    if not idinfo.get("email_verified"):
        return jsonify({"status": "error", "message": "Google email is not verified"}), 400

    user_id = _safe_user_id(email)
    is_creator = _is_creator(email)

    with _users_lock:
        users = _load_users()
        user = users.get(email)
        if user:
            user["google_id"] = google_id
            if name:
                user["name"] = name
            if picture:
                user["picture"] = picture
        else:
            users[email] = {
                "user_id": user_id,
                "google_id": google_id,
                "name": name,
                "picture": picture,
                "email_verified": True,
                "auth_method": "google",
            }
        if is_creator:
            users[email]["identity_name"] = CREATOR_NAME
            users[email]["title"] = CREATOR_TITLE
        _save_users(users)

    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["email"] = email
    session["is_creator"] = is_creator
    session["user"] = {"id": user_id, "email": email, "is_creator": is_creator}
    if is_creator:
        session["user"]["identity_name"] = CREATOR_NAME
        session["user"]["title"] = CREATOR_TITLE

    token = secrets.token_urlsafe(32)
    _set_auth_token(token, {"user_id": user_id, "email": email, "is_creator": is_creator})

    marcus = load_marcus(user_id)
    if marcus:
        _initialize_user_memory(marcus, email)
        if is_creator:
            try:
                marcus.memory.set_creator_identity(CREATOR_NAME, CREATOR_TITLE)
            except Exception as exc:
                print(f"[WARN] Failed to set creator identity in memory: {exc}")

    return jsonify({
        "status": "success",
        "authenticated": True,
        "email": email,
        "name": name,
        "picture": picture,
        "google_id": google_id,
        "character": "marcus",
        "session_token": token,
        "is_creator": is_creator,
    })


@app.route("/logout", methods=["POST"])
@app.route("/auth/logout", methods=["POST"])
def logout():
    token = str(
        request.headers.get("X-Session-Token")
        or request.headers.get("Authorization", "").replace("Bearer ", "", 1)
        or ""
    ).strip()
    if token:
        _delete_auth_token(token)
    session.clear()
    return jsonify({"status": "success", "authenticated": False})


@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"status": "error", "message": "Valid email is required"}), 400

    with _users_lock:
        users = _load_users()
        user = users.get(email)

        if not user:
            return jsonify({"status": "error", "message": "No account found with that email."}), 404

        question = user.get("security_question", DEFAULT_SECURITY_QUESTION)

    return jsonify({"status": "success", "question": question})


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    answer = str(data.get("answer") or "").strip().lower()
    new_password = str(data.get("new_password") or "")

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"status": "error", "message": "Valid email is required"}), 400
    if not answer:
        return jsonify({"status": "error", "message": "Security answer is required."}), 400
    if not new_password or len(new_password) < 4:
        return jsonify({"status": "error", "message": "Password must be at least 4 characters."}), 400

    with _users_lock:
        users = _load_users()
        user = users.get(email)

        if not user:
            return jsonify({"status": "error", "message": "No account found with that email."}), 404

        stored_hash = user.get("security_answer_hash")
        if not stored_hash:
            return jsonify({"status": "error", "message": "Security question not set for this account."}), 400

        if not check_password_hash(stored_hash, answer):
            return jsonify({"status": "error", "message": "Incorrect answer."}), 401

        user["password_hash"] = generate_password_hash(new_password)
        _save_users(users)

    return jsonify({"status": "success", "message": "Password reset successfully."})


@app.route("/auth/change-password", methods=["POST"])
def change_password():
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    current_password = str(data.get("current_password") or "")
    new_password = str(data.get("new_password") or "")

    if not current_password:
        return jsonify({"status": "error", "message": "Current password required."}), 400
    if not new_password or len(new_password) < 4:
        return jsonify({"status": "error", "message": "New password must be at least 4 characters."}), 400

    auth = _current_auth()
    email = str(auth.get("email") or "").strip().lower()

    with _users_lock:
        users = _load_users()
        user = users.get(email)
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        stored_hash = str(user.get("password_hash") or "")
        if not check_password_hash(stored_hash, current_password):
            return jsonify({"status": "error", "message": "Current password is incorrect."}), 401
        user["password_hash"] = generate_password_hash(new_password)
        _save_users(users)

    return jsonify({"status": "success", "message": "Password changed successfully."})


@app.route("/chat", methods=["POST"])
def chat():
    try:
        user_id, error = _require_login()
        if error:
            return error

        data = request.get_json(silent=True)

        if not data:
            return jsonify({"status": "error", "message": "No JSON body received"}), 400

        message = (data.get("message") or "").strip()
        if not message and not data.get("image"):
            return jsonify({"status": "error", "message": "message or image is required"}), 400

        chat_id = str(data.get("chat_id") or "").strip()
        image_data = str(data.get("image") or "").strip()
        source = str(data.get("source") or "").strip() or None
        persona = normalize_persona(data.get("persona"))

        # ── Route ─────────────────────────────────────────────────────
        router = get_router()
        decision = router.classify(message, has_image=bool(image_data), source=source)

        caps_str = ", ".join(c.value for c in decision.capabilities)
        print(f"[Router] Dispatching /chat → [{caps_str}]")

        # ── Dispatch ──────────────────────────────────────────────────
        has_text = pm.Capability.TEXT in decision.capabilities
        has_image = pm.Capability.IMAGE in decision.capabilities
        has_video = pm.Capability.VIDEO in decision.capabilities

        if has_video and has_text:
            return _dispatch_video_text_json(user_id, message, chat_id, image_data)
        if has_video:
            return _dispatch_video_json(user_id, message, chat_id, image_data)
        if has_text and has_image:
            return _dispatch_multi_json(user_id, message, chat_id, image_data)
        if has_image:
            return _dispatch_image_json(user_id, message, chat_id, image_data)
        return _dispatch_chat_json(user_id, message, chat_id, image_data, persona=persona)

    except Exception as e:
        print(f"[CRITICAL] /chat crashed: {e}")
        return jsonify({
            "status": "error",
            "message": "Internal server error",
        }), 500


# ── Dispatch functions ────────────────────────────────────────────────────────
# Router decides.  These functions execute.  Each one calls an existing pipeline
# directly — no duplication of business logic.


def _persist_chat_message(user_id: str, chat_id: str, role: str, content: str, image_url: str = "", video_url: str = ""):
    """Persist a single message to Marcus memory (best-effort)."""
    marcus = load_marcus(user_id)
    if not marcus:
        return
    resolved = chat_id or f"{marcus.profile.key}_main_chat"
    try:
        marcus.memory.add_message(resolved, role, content, image_url=image_url, video_url=video_url)
    except Exception as exc:
        print(f"[Dispatch] Failed to persist {role} message: {exc}")


def _embed_media_exchange(user_id: str, prompt: str, kind: str, chat_id: str):
    """Embed a media-generation exchange into semantic memory, off-thread.

    Text chats are embedded inside MarcusBrain; media generations bypass the
    brain, so without this a user's "remember that image you made me?" would
    find nothing.
    """
    def _bg():
        try:
            from core.brain import _get_memory_mgr
            mm = _get_memory_mgr()
            if mm:
                mm.save_sync(prompt, f"[generated a {kind} for this request]", chat_id, namespace=user_id)
        except Exception as exc:
            print(f"[MEMORY] media exchange embed failed: {exc}")

    Thread(target=_bg, daemon=True).start()


def _safe_persist_url(media_record: dict | None, source_url: str) -> str:
    """Resolve the URL to persist for a media message. Prefer our permanent
    GridFS path. If the GridFS save failed, only fall back to a URL that is our
    OWN (``/static/...``) — never an external provider URL, which may carry an
    expiry and rot. Returns "" if there's nothing safe to persist."""
    if media_record and media_record.get("local_path"):
        return media_record["local_path"]
    if source_url.startswith("/static/"):
        return source_url
    return ""


def _spawn_video_generation(user_id: str, chat_id: str, message: str) -> dict:
    """Run the full video lifecycle (generate → download → GridFS save → persist)
    in a background daemon thread, so it completes even if the browser
    disconnects mid-generation. Returns a mutable ``state`` dict the SSE stream
    can observe for progress and the final permanent URL.

    The chat message is persisted with our OWN permanent GridFS URL — never the
    provider's temporary signed URL (which carries an Expires param and would
    rot within hours).
    """
    state = {"done": False, "status": "submitted", "stored_url": "", "error": ""}

    def _run():
        try:
            dispatcher = get_video_dispatcher()
            task = dispatcher.generate(message)
            if task.status.value == "failed" or not task.video_url:
                state.update(done=True, status="failed", error=task.error or "Video generation failed")
                print(f"[VIDEO] Background generation failed: {task.error}")
                return

            media = get_media_manager(user_id)
            media_record = media.save_video(
                task.video_url, prompt=message, provider="AlibabaVideo", chat_id=chat_id,
            )
            stored_url = media_record["local_path"] if media_record else ""
            if not stored_url:
                state.update(done=True, status="failed", error="Video generated but could not be saved")
                print("[VIDEO] Background save to gallery failed")
                return

            # Persist with our permanent URL so the video shows in the chat on
            # every future reload, independent of the browser session.
            _persist_chat_message(user_id, chat_id, "assistant", f"[Video: {stored_url}]", video_url=stored_url)
            _embed_media_exchange(user_id, message, "video", chat_id)
            state.update(done=True, status="completed", stored_url=stored_url)
            print(f"[VIDEO] Background generation complete, persisted {stored_url}")
        except Exception as exc:
            state.update(done=True, status="failed", error=str(exc))
            print(f"[VIDEO] Background generation crashed: {exc}")

    Thread(target=_run, daemon=True).start()
    return state


def _stream_video_state(state: dict, resolved_chat_id: str, updated_title=None):
    """SSE generator that tails a background video ``state`` and emits progress,
    the final permanent URL, then done. Safe to abandon: if the client
    disconnects, the background thread still finishes the save + persist."""
    import time as _time
    yield f"data: {json.dumps({'intent': 'generating_video', 'query': '', 'status': 'preparing', 'status_message': 'Preparing video generation...'})}\n\n"
    while not state["done"]:
        _time.sleep(2)
        yield f"data: {json.dumps({'intent': 'video_progress', 'status': state['status'], 'status_message': 'Generating video, this can take a few minutes...'})}\n\n"
    if state["error"]:
        yield f"data: {json.dumps({'error': state['error']})}\n\n"
    else:
        yield f"data: {json.dumps({'video_url': state['stored_url']})}\n\n"
    done_evt = {'done': True, 'chat_id': resolved_chat_id}
    if updated_title:
        done_evt['updated_title'] = updated_title
    yield f"data: {json.dumps(done_evt)}\n\n"


# ── Video generation kill switch ─────────────────────────────────────────────

VIDEO_DISABLED_MESSAGE = (
    "Video generation is currently unavailable — it's turned off right now. "
    "Everything else still works, and any videos you've already made remain "
    "playable in your Video Gallery."
)


def _video_generation_enabled() -> bool:
    """Global kill switch for video generation. FAILS CLOSED: only an explicit
    truthy VIDEO_GENERATION_ENABLED turns it on; a missing or misconfigured var
    leaves video OFF. Applies to ALL users including the creator — no bypass.

    This is a permanent outer gate. Any future paywall / entitlement check must
    sit INSIDE this flag (only consulted when this returns True), never replace
    it: `if _video_generation_enabled() and user_has_video_access(...)`.
    """
    return os.getenv("VIDEO_GENERATION_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _video_disabled_json(user_id, message, chat_id):
    """JSON response for a blocked video request. No provider is ever called."""
    resolved = chat_id or f"marcus_main_chat"
    _persist_chat_message(user_id, resolved, "user", message)
    _persist_chat_message(user_id, resolved, "assistant", VIDEO_DISABLED_MESSAGE)
    return jsonify({
        "status": "success",
        "chat_id": resolved,
        "character": "marcus",
        "reply": VIDEO_DISABLED_MESSAGE,
        "video_disabled": True,
    })


def _video_disabled_stream(user_id, message, chat_id):
    """SSE response for a blocked video request. No provider is ever called."""
    marcus = load_marcus(user_id)
    resolved = chat_id or (f"{marcus.profile.key}_main_chat" if marcus else chat_id)
    _persist_chat_message(user_id, resolved, "user", message)
    _persist_chat_message(user_id, resolved, "assistant", VIDEO_DISABLED_MESSAGE)

    def generate():
        yield f"data: {json.dumps({'token': VIDEO_DISABLED_MESSAGE})}\n\n"
        yield f"data: {json.dumps({'done': True, 'chat_id': resolved, 'video_disabled': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )


def _dispatch_image_json(user_id, message, chat_id, image_data):
    """IMAGE → non-streaming JSON.  Reuses the existing ProviderManager image pipeline."""
    print(f"[Router]   Dispatch: IMAGE (json) — prompt={message[:120]!r}")

    _persist_chat_message(user_id, chat_id, "user", message)

    config = get_config()
    result = pm.get_manager().execute(
        pm.Capability.IMAGE,
        prompt=message,
        api_key=config.gemini_api_key or None,
        enhance=True,
    )

    if not result.success:
        print(f"[Router]   IMAGE failed: {result.error}")
        return jsonify({"status": "error", "message": "Image generation failed. Please try again."}), 500

    image_url = result.data.get("image_url", "")
    revised = result.data.get("revised_prompt", "")
    print(f"[Router]   IMAGE success — provider={result.provider_name} latency={result.latency_ms:.0f}ms")

    media = get_media_manager(user_id)
    media_record = media.save_image(
        image_url, prompt=message, revised_prompt=revised,
        provider=result.provider_name, chat_id=chat_id,
    )
    stored_url = _safe_persist_url(media_record, image_url)
    if not stored_url:
        return jsonify({"status": "error", "message": "Image generated but could not be saved. Please try again."}), 500
    _embed_media_exchange(user_id, message, "image", chat_id)

    _persist_chat_message(user_id, chat_id, "assistant", f"[Image: {stored_url}]", image_url=stored_url)

    return jsonify({
        "status": "success",
        "image_url": stored_url,
        "revised_prompt": revised,
        "text": result.data.get("text", ""),
    })


def _dispatch_image_stream(user_id, message, chat_id, image_data):
    """IMAGE → SSE stream.  Reuses the existing ProviderManager image pipeline."""
    print(f"[Router]   Dispatch: IMAGE (stream) — prompt={message[:120]!r}")

    marcus = load_marcus(user_id)
    resolved_chat_id = chat_id or (f"{marcus.profile.key}_main_chat" if marcus else chat_id)

    if marcus:
        try:
            marcus.memory.add_message(resolved_chat_id, "user", message)
        except Exception:
            pass

    config = get_config()

    def generate():
        updated_title = None
        if message and resolved_chat_id:
            try:
                sessions = _list_user_sessions(user_id)
                current_title = next((s.get("title", "") for s in sessions if s.get("chat_id") == resolved_chat_id), None)
                if current_title in (None, "", "New Chat", "Untitled Thread"):
                    words = message.split()
                    if len(words) >= 3:
                        title = " ".join(words[:8]).rstrip(".,!?;:")
                        if len(title) > 60:
                            title = title[:60].rsplit(" ", 1)[0] if " " in title[:60] else title[:60]
                        if marcus:
                            marcus.memory.set_title(resolved_chat_id, title)
                        _upsert_chat_session_meta(user_id, resolved_chat_id, title=title, message_count=2)
                        updated_title = title
                    else:
                        _upsert_chat_session_meta(user_id, resolved_chat_id, message_count=2)
            except Exception as exc:
                print(f"[WARN] Auto-title fallback failed: {exc}")

        yield f"data: {json.dumps({'intent': 'generating_image', 'query': message})}\n\n"

        result = pm.get_manager().execute(
            pm.Capability.IMAGE,
            prompt=message,
            api_key=config.gemini_api_key or None,
            enhance=True,
        )

        if not result.success:
            print(f"[Router]   IMAGE failed: {result.error}")
            yield f"data: {json.dumps({'error': 'Image generation failed. Please try again.'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'chat_id': resolved_chat_id, 'updated_title': updated_title})}\n\n"
            return

        image_url = result.data.get("image_url", "")
        revised = result.data.get("revised_prompt", "")
        print(f"[Router]   IMAGE success — provider={result.provider_name} latency={result.latency_ms:.0f}ms")

        media = get_media_manager(user_id)
        media_record = media.save_image(
            image_url, prompt=message, revised_prompt=revised,
            provider=result.provider_name, chat_id=resolved_chat_id,
        )
        stored_url = _safe_persist_url(media_record, image_url)
        if not stored_url:
            yield f"data: {json.dumps({'error': 'Image generated but could not be saved. Please try again.'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'chat_id': resolved_chat_id, 'updated_title': updated_title})}\n\n"
            return
        _embed_media_exchange(user_id, message, "image", resolved_chat_id)

        yield f"data: {json.dumps({'image_url': stored_url, 'revised_prompt': revised})}\n\n"

        if marcus:
            try:
                marcus.memory.add_message(resolved_chat_id, "assistant", f"[Image: {stored_url}]", image_url=stored_url)
            except Exception:
                pass

        yield f"data: {json.dumps({'done': True, 'chat_id': resolved_chat_id, 'updated_title': updated_title})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )


def _dispatch_chat_json(user_id, message, chat_id, image_data, persona="marcus"):
    """TEXT → non-streaming JSON, in the selected persona's voice."""
    marcus = load_persona_brain(user_id, persona)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus is not configured"}), 404

    auth = _current_auth()
    _initialize_user_memory(marcus, auth.get("email", ""))

    reply = marcus.respond(message, chat_id=chat_id, image_data=image_data)
    meta = getattr(marcus, "last_response_meta", {}) or {}
    voice = (
        {"enabled": True, "spoken": False, "engine": "browser", "reason": "reply too long for blocking server TTS"}
        if len(reply) > 900
        # Each crew member speaks in their own configured voice
        else speak_marcus(reply, voice=getattr(marcus.profile, "voice", "") or "en-US-GuyNeural")
    )

    updated_title = None
    if message and chat_id:
        try:
            sessions = _list_user_sessions(user_id)
            current_title = next((s.get("title", "") for s in sessions if s.get("chat_id") == chat_id), None)
            if current_title in (None, "", "New Chat", "Untitled Thread"):
                words = message.split()
                if len(words) >= 3:
                    title = " ".join(words[:8]).rstrip(".,!?;:")
                    if len(title) > 60:
                        title = title[:60].rsplit(" ", 1)[0] if " " in title[:60] else title[:60]
                    marcus.memory.set_title(chat_id, title)
                    _upsert_chat_session_meta(user_id, chat_id, title=title)
                    updated_title = title
        except Exception as exc:
            print(f"[WARN] Auto-title fallback failed: {exc}")

    return jsonify({
        "status": "success",
        "chat_id": chat_id or f"{marcus.profile.key}_main_chat",
        "character": normalize_persona(persona),
        "reply": reply,
        "voice": voice,
        "updated_title": updated_title,
        "sources": meta.get("sources") or [],
        "detected_route": str(meta.get("detected_route") or ""),
        "groq_used": bool(meta.get("groq_used")),
        "live_routing_used": bool(meta.get("live_routing_used")),
        "fallback_used": bool(meta.get("fallback_used")),
        "fallback_source": str(meta.get("fallback_source") or ""),
    })


def _dispatch_chat_stream(user_id, message, chat_id, image_data, persona="marcus"):
    """TEXT → SSE stream, in the selected persona's voice."""
    marcus = load_persona_brain(user_id, persona)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus not configured"}), 404

    auth = _current_auth()
    _initialize_user_memory(marcus, auth.get("email", ""))
    resolved_chat_id = chat_id or f"{marcus.profile.key}_main_chat"

    def generate():
        updated_title = None
        if message and resolved_chat_id:
            try:
                sessions = _list_user_sessions(user_id)
                current_title = next((s.get("title", "") for s in sessions if s.get("chat_id") == resolved_chat_id), None)
                if current_title in (None, "", "New Chat", "Untitled Thread"):
                    words = message.split()
                    if len(words) >= 3:
                        title = " ".join(words[:8]).rstrip(".,!?;:")
                        if len(title) > 60:
                            title = title[:60].rsplit(" ", 1)[0] if " " in title[:60] else title[:60]
                        marcus.memory.set_title(resolved_chat_id, title)
                        _upsert_chat_session_meta(user_id, resolved_chat_id, title=title)
                        updated_title = title
            except Exception as exc:
                print(f"[WARN] Auto-title fallback failed: {exc}")

        try:
            for token in marcus.stream_respond(message, chat_id=resolved_chat_id, image_data=image_data):
                if token is None:
                    continue
                if isinstance(token, dict):
                    yield f"data: {json.dumps(token)}\n\n"
                elif token:
                    yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        yield f"data: {json.dumps({'done': True, 'chat_id': resolved_chat_id, 'updated_title': updated_title})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )


# ── Multi-capability dispatch (TEXT + IMAGE together) ────────────────────────
# The router returned multiple capabilities.  We execute each pipeline in order
# and stream/return them together.  Reuses all existing single-capability
# dispatch logic internally — zero duplication of ProviderManager calls.


def _dispatch_multi_json(user_id, message, chat_id, image_data):
    """TEXT + IMAGE → non-streaming JSON.  Returns both text reply and image URL."""
    print(f"[Router]   Dispatch: TEXT+IMAGE (json) — prompt={message[:120]!r}")

    _persist_chat_message(user_id, chat_id, "user", message)

    # ── 1. Generate text via Marcus Brain ─────────────────────────────
    marcus = load_marcus(user_id)
    text_reply = ""
    if marcus:
        auth = _current_auth()
        _initialize_user_memory(marcus, auth.get("email", ""))
        text_reply = marcus.respond(message, chat_id=chat_id, image_data=image_data)
    meta = getattr(marcus, "last_response_meta", {}) or {} if marcus else {}

    # ── 2. Generate image via ProviderManager ─────────────────────────
    config = get_config()
    image_result = pm.get_manager().execute(
        pm.Capability.IMAGE,
        prompt=message,
        api_key=config.gemini_api_key or None,
        enhance=True,
    )

    image_url = ""
    revised = ""
    if image_result.success:
        image_url = image_result.data.get("image_url", "")
        revised = image_result.data.get("revised_prompt", "")
        print(f"[Router]   IMAGE success — provider={image_result.provider_name} latency={image_result.latency_ms:.0f}ms")

        media = get_media_manager(user_id)
        media_record = media.save_image(
            image_url, prompt=message, revised_prompt=revised,
            provider=image_result.provider_name, chat_id=chat_id,
        )
        image_url = media_record["local_path"] if media_record else image_url
    else:
        print(f"[Router]   IMAGE failed: {image_result.error}")

    # ── 3. Persist ────────────────────────────────────────────────────
    assistant_content = text_reply
    if image_url:
        assistant_content += f"\n\n[Image: {image_url}]"
    _persist_chat_message(user_id, chat_id, "assistant", assistant_content, image_url=image_url)

    voice = (
        {"enabled": True, "spoken": False, "engine": "browser", "reason": "reply too long for blocking server TTS"}
        if len(text_reply) > 900
        else speak_marcus(text_reply)
    )

    return jsonify({
        "status": "success",
        "chat_id": chat_id or (f"{marcus.profile.key}_main_chat" if marcus else chat_id),
        "character": "marcus",
        "reply": text_reply,
        "image_url": image_url,
        "revised_prompt": revised,
        "voice": voice,
        "detected_route": str(meta.get("detected_route") or ""),
        "groq_used": bool(meta.get("groq_used")),
        "live_routing_used": bool(meta.get("live_routing_used")),
        "fallback_used": bool(meta.get("fallback_used")),
        "fallback_source": str(meta.get("fallback_source") or ""),
    })


def _dispatch_multi_stream(user_id, message, chat_id, image_data):
    """TEXT + IMAGE → SSE stream.  Streams text tokens first, then sends image URL.

    The frontend already handles both token events and image_url events in the
    same stream — text accumulates into a bubble, image renders below it.
    """
    print(f"[Router]   Dispatch: TEXT+IMAGE (stream) — prompt={message[:120]!r}")

    marcus = load_marcus(user_id)
    auth = _current_auth()
    resolved_chat_id = chat_id or (f"{marcus.profile.key}_main_chat" if marcus else chat_id)

    if marcus:
        _initialize_user_memory(marcus, auth.get("email", ""))
        try:
            marcus.memory.add_message(resolved_chat_id, "user", message)
        except Exception:
            pass

    config = get_config()

    def generate():
        updated_title = None

        # ── 1. Stream text tokens via Marcus Brain ────────────────────
        if marcus:
            try:
                sessions = _list_user_sessions(user_id)
                current_title = next((s.get("title", "") for s in sessions if s.get("chat_id") == resolved_chat_id), None)
                if current_title in (None, "", "New Chat", "Untitled Thread"):
                    words = message.split()
                    if len(words) >= 3:
                        title = " ".join(words[:8]).rstrip(".,!?;:")
                        if len(title) > 60:
                            title = title[:60].rsplit(" ", 1)[0] if " " in title[:60] else title[:60]
                        marcus.memory.set_title(resolved_chat_id, title)
                        _upsert_chat_session_meta(user_id, resolved_chat_id, title=title)
                        updated_title = title
            except Exception as exc:
                print(f"[WARN] Auto-title fallback failed: {exc}")

            try:
                for token in marcus.stream_respond(message, chat_id=resolved_chat_id, image_data=image_data):
                    if token is None:
                        continue
                    if isinstance(token, dict):
                        yield f"data: {json.dumps(token)}\n\n"
                    elif token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        # ── 2. Generate image via ProviderManager ─────────────────────
        yield f"data: {json.dumps({'intent': 'generating_image', 'query': message})}\n\n"

        image_result = pm.get_manager().execute(
            pm.Capability.IMAGE,
            prompt=message,
            api_key=config.gemini_api_key or None,
            enhance=True,
        )

        if image_result.success:
            image_url = image_result.data.get("image_url", "")
            revised = image_result.data.get("revised_prompt", "")
            print(f"[Router]   IMAGE success — provider={image_result.provider_name} latency={image_result.latency_ms:.0f}ms")

            media = get_media_manager(user_id)
            media_record = media.save_image(
                image_url, prompt=message, revised_prompt=revised,
                provider=image_result.provider_name, chat_id=resolved_chat_id,
            )
            stored_url = media_record["local_path"] if media_record else image_url

            yield f"data: {json.dumps({'image_url': stored_url, 'revised_prompt': revised})}\n\n"

            if marcus:
                try:
                    marcus.memory.add_message(resolved_chat_id, "assistant", f"[Image: {stored_url}]", image_url=stored_url)
                except Exception:
                    pass
        else:
            print(f"[Router]   IMAGE failed: {image_result.error}")
            yield f"data: {json.dumps({'error': 'Image generation failed. The text response above is still valid.'})}\n\n"

        yield f"data: {json.dumps({'done': True, 'chat_id': resolved_chat_id, 'updated_title': updated_title})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )


# ── Video generation dispatch ────────────────────────────────────────────────
# Video generation is fully asynchronous — the dispatcher manages the lifecycle
# (submit → poll → download) and yields progress events for SSE streaming.
# All ProviderManager interaction is encapsulated in the video providers.


def _dispatch_video_json(user_id, message, chat_id, image_data):
    """VIDEO → non-streaming JSON.  Blocks until video is ready or failed."""
    if not _video_generation_enabled():
        print("[Router]   VIDEO blocked — generation disabled (kill switch)")
        return _video_disabled_json(user_id, message, chat_id)
    print(f"[Router]   Dispatch: VIDEO (json) — prompt={message[:120]!r}")

    _persist_chat_message(user_id, chat_id, "user", message)

    dispatcher = get_video_dispatcher()
    task = dispatcher.generate(message)

    if task.status.value == "failed":
        print(f"[Router]   VIDEO failed: {task.error}")
        return jsonify({"status": "error", "message": task.error or "Video generation failed"}), 500

    print(f"[Router]   VIDEO success — video_url={task.video_url}")

    media = get_media_manager(user_id)
    media_record = media.save_video(task.video_url, prompt=message, provider="AlibabaVideo", chat_id=chat_id)
    if not media_record:
        # Never fall back to task.video_url — it's a temporary signed URL that
        # would rot. If GridFS save failed, report failure rather than persist
        # a link that dies within hours.
        return jsonify({"status": "error", "message": "Video generated but could not be saved. Please try again."}), 500
    stored_url = media_record["local_path"]
    _embed_media_exchange(user_id, message, "video", chat_id)

    _persist_chat_message(user_id, chat_id, "assistant", f"[Video: {stored_url}]", video_url=stored_url)

    return jsonify({
        "status": "success",
        "video_url": stored_url,
        "thumbnail_url": task.thumbnail_url,
        "task_id": task.task_id,
    })


def _dispatch_video_stream(user_id, message, chat_id, image_data):
    """VIDEO → SSE stream. Generation runs in a background thread (survives
    client disconnect); the stream just tails its progress."""
    if not _video_generation_enabled():
        print("[Router]   VIDEO blocked — generation disabled (kill switch)")
        return _video_disabled_stream(user_id, message, chat_id)
    print(f"[Router]   Dispatch: VIDEO (stream) — prompt={message[:120]!r}")

    marcus = load_marcus(user_id)
    resolved_chat_id = chat_id or (f"{marcus.profile.key}_main_chat" if marcus else chat_id)

    if marcus:
        try:
            marcus.memory.add_message(resolved_chat_id, "user", message)
        except Exception:
            pass

    state = _spawn_video_generation(user_id, resolved_chat_id, message)

    return Response(
        stream_with_context(_stream_video_state(state, resolved_chat_id)),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )


def _dispatch_video_text_json(user_id, message, chat_id, image_data):
    """TEXT + VIDEO → non-streaming JSON.  Returns both text reply and video URL."""
    if not _video_generation_enabled():
        print("[Router]   TEXT+VIDEO blocked — generation disabled (kill switch)")
        return _video_disabled_json(user_id, message, chat_id)
    print(f"[Router]   Dispatch: TEXT+VIDEO (json) — prompt={message[:120]!r}")

    _persist_chat_message(user_id, chat_id, "user", message)

    # ── 1. Generate text via Marcus Brain ─────────────────────────────
    marcus = load_marcus(user_id)
    text_reply = ""
    if marcus:
        auth = _current_auth()
        _initialize_user_memory(marcus, auth.get("email", ""))
        text_reply = marcus.respond(message, chat_id=chat_id, image_data=image_data)
    meta = getattr(marcus, "last_response_meta", {}) or {} if marcus else {}

    # ── 2. Generate video via VideoDispatcher ─────────────────────────
    dispatcher = get_video_dispatcher()
    task = dispatcher.generate(message)

    video_url = ""
    if task.status.value != "failed" and task.video_url:
        print(f"[Router]   VIDEO success — provider url received")
        media = get_media_manager(user_id)
        media_record = media.save_video(task.video_url, prompt=message, provider="AlibabaVideo", chat_id=chat_id)
        if media_record:
            video_url = media_record["local_path"]  # permanent GridFS URL only
        else:
            print("[Router]   VIDEO save-to-gallery failed; not persisting a temporary URL")
    else:
        print(f"[Router]   VIDEO failed: {task.error}")

    # ── 3. Persist ────────────────────────────────────────────────────
    assistant_content = text_reply
    if video_url:
        assistant_content += f"\n\n[Video: {video_url}]"
    _persist_chat_message(user_id, chat_id, "assistant", assistant_content, video_url=video_url)

    voice = (
        {"enabled": True, "spoken": False, "engine": "browser", "reason": "reply too long for blocking server TTS"}
        if len(text_reply) > 900
        else speak_marcus(text_reply)
    )

    return jsonify({
        "status": "success",
        "chat_id": chat_id or (f"{marcus.profile.key}_main_chat" if marcus else chat_id),
        "character": "marcus",
        "reply": text_reply,
        "video_url": video_url,
        "task_id": task.task_id,
        "voice": voice,
        "detected_route": str(meta.get("detected_route") or ""),
        "groq_used": bool(meta.get("groq_used")),
        "live_routing_used": bool(meta.get("live_routing_used")),
        "fallback_used": bool(meta.get("fallback_used")),
        "fallback_source": str(meta.get("fallback_source") or ""),
    })


def _dispatch_video_text_stream(user_id, message, chat_id, image_data):
    """TEXT + VIDEO → SSE stream.  Streams text first, then video progress + URL."""
    if not _video_generation_enabled():
        print("[Router]   TEXT+VIDEO blocked — generation disabled (kill switch)")
        return _video_disabled_stream(user_id, message, chat_id)
    print(f"[Router]   Dispatch: TEXT+VIDEO (stream) — prompt={message[:120]!r}")

    marcus = load_marcus(user_id)
    auth = _current_auth()
    resolved_chat_id = chat_id or (f"{marcus.profile.key}_main_chat" if marcus else chat_id)

    if marcus:
        _initialize_user_memory(marcus, auth.get("email", ""))
        try:
            marcus.memory.add_message(resolved_chat_id, "user", message)
        except Exception:
            pass

    dispatcher = get_video_dispatcher()

    def generate():
        updated_title = None

        # ── 1. Stream text tokens via Marcus Brain ────────────────────
        if marcus:
            try:
                sessions = _list_user_sessions(user_id)
                current_title = next((s.get("title", "") for s in sessions if s.get("chat_id") == resolved_chat_id), None)
                if current_title in (None, "", "New Chat", "Untitled Thread"):
                    words = message.split()
                    if len(words) >= 3:
                        title = " ".join(words[:8]).rstrip(".,!?;:")
                        if len(title) > 60:
                            title = title[:60].rsplit(" ", 1)[0] if " " in title[:60] else title[:60]
                        marcus.memory.set_title(resolved_chat_id, title)
                        _upsert_chat_session_meta(user_id, resolved_chat_id, title=title)
                        updated_title = title
            except Exception as exc:
                print(f"[WARN] Auto-title fallback failed: {exc}")

            try:
                for token in marcus.stream_respond(message, chat_id=resolved_chat_id, image_data=image_data):
                    if token is None:
                        continue
                    if isinstance(token, dict):
                        yield f"data: {json.dumps(token)}\n\n"
                    elif token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        # ── 2. Generate video in background (survives disconnect), tail it ─
        state = _spawn_video_generation(user_id, resolved_chat_id, message)
        for chunk in _stream_video_state(state, resolved_chat_id, updated_title=updated_title):
            yield chunk

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    try:
        user_id, error = _require_login()
        if error:
            return error

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "error", "message": "No JSON body received"}), 400

        message = (data.get("message") or "").strip()
        if not message and not data.get("image"):
            return jsonify({"status": "error", "message": "message or image is required"}), 400

        chat_id = str(data.get("chat_id") or "").strip()
        image_data = str(data.get("image") or "").strip()
        source = str(data.get("source") or "").strip() or None
        persona = normalize_persona(data.get("persona"))

        # ── Route ─────────────────────────────────────────────────────
        router = get_router()
        decision = router.classify(message, has_image=bool(image_data), source=source)

        caps_str = ", ".join(c.value for c in decision.capabilities)
        print(f"[Router] Dispatching /chat/stream → [{caps_str}]")

        # ── Dispatch ──────────────────────────────────────────────────
        has_text = pm.Capability.TEXT in decision.capabilities
        has_image = pm.Capability.IMAGE in decision.capabilities
        has_video = pm.Capability.VIDEO in decision.capabilities

        if has_video and has_text:
            return _dispatch_video_text_stream(user_id, message, chat_id, image_data)
        if has_video:
            return _dispatch_video_stream(user_id, message, chat_id, image_data)
        if has_text and has_image:
            return _dispatch_multi_stream(user_id, message, chat_id, image_data)
        if has_image:
            return _dispatch_image_stream(user_id, message, chat_id, image_data)
        return _dispatch_chat_stream(user_id, message, chat_id, image_data, persona=persona)

    except Exception as e:
        print(f"[CRITICAL] /chat/stream crashed: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route("/api/chat/message", methods=["POST"])
def api_chat_message():
    try:
        user_id, error = _require_login()
        if error:
            return error

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "error", "message": "No JSON body received"}), 400

        chat_id = str(data.get("session_id") or "").strip()
        user_message = str(data.get("message") or "").strip()
        ai_response = str(data.get("response") or "").strip()

        if not chat_id:
            return jsonify({"status": "error", "message": "session_id is required"}), 400
        if not user_message and not ai_response:
            return jsonify({"status": "error", "message": "message or response is required"}), 400

        marcus = load_marcus(user_id)
        if not marcus:
            return jsonify({"status": "error", "message": "Marcus not configured"}), 404

        if user_message:
            marcus.memory.add_message(chat_id, "user", user_message)
        if ai_response:
            marcus.memory.add_message(chat_id, "assistant", ai_response)

        return jsonify({"status": "success"})

    except Exception as e:
        print(f"[CRITICAL] /api/chat/message crashed: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route("/api/generate-image", methods=["POST"])
def api_generate_image():
    try:
        user_id, error = _require_login()
        if error:
            return error

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "error", "message": "Please describe the image you want to create."}), 400

        prompt = str(data.get("prompt") or "").strip()
        reference_image_raw = data.get("reference_image")
        reference_image = None
        if isinstance(reference_image_raw, dict):
            ref_data = str(reference_image_raw.get("data") or "").strip()
            ref_mime = str(reference_image_raw.get("mimeType") or "image/jpeg").strip()
            if ref_data:
                reference_image = {"data": ref_data, "mimeType": ref_mime}

        if not prompt:
            return jsonify({"status": "error", "message": "Please describe the image you want to create."}), 400

        config = get_config()
        api_key = config.gemini_api_key

        manager = pm.get_manager()
        result = manager.execute(
            pm.Capability.IMAGE,
            prompt=prompt,
            api_key=api_key or None,
            enhance=True,
            reference_image=reference_image,
        )

        if not result.success:
            print(f"[IMAGE] Provider execution failed: {result.error}")
            return jsonify({
                "status": "error",
                "message": "Image generation failed. Please try again.",
            }), 500

        image_url = result.data["image_url"]
        print(f"[IMAGE] Success — url={image_url} provider={result.provider_name} latency={result.latency_ms:.0f}ms")

        return jsonify({
            "status": "success",
            "image_url": image_url,
            "revised_prompt": result.data.get("revised_prompt", ""),
            "text": result.data.get("text", ""),
        })

    except Exception as e:
        import traceback
        print(f"[IMAGE] EXCEPTION in api_generate_image: {e}")
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": "Image generation failed. Please try again.",
        }), 500


# ── Settings API ─────────────────────────────────────────────────

_SETTINGS_DIR = PROJECT_ROOT / "memory_data" / "settings"


def _settings_path(user_id: str) -> Path:
    p = _SETTINGS_DIR / _safe_user_id(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p / "settings.json"


def _load_settings(user_id: str) -> dict:
    fpath = _settings_path(user_id)
    try:
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_settings(user_id: str, data: dict):
    fpath = _settings_path(user_id)
    try:
        fpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(fpath) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, fpath)
    except OSError as exc:
        print(f"[ERROR] Failed to save settings: {exc}")


def _get_section_settings(user_id: str, section: str) -> dict:
    settings = _load_settings(user_id)
    return settings.get(section, {})


def _put_section_settings(user_id: str, section: str, data: dict):
    settings = _load_settings(user_id)
    settings[section] = data
    _save_settings(user_id, settings)


@app.route("/api/settings/<section>", methods=["GET", "POST", "PUT"])
def api_settings(section):
    user_id, error = _require_login()
    if error:
        return error
    allowed = {
        "account", "memory", "projects", "creator", "preferences",
        "appearance", "notifications", "knowledge", "billing",
        "privacy", "language", "integrations", "extensions",
    }
    if section not in allowed:
        return jsonify({"status": "error", "message": "Unknown section"}), 400
    if request.method == "GET":
        data = _get_section_settings(user_id, section)
        return jsonify({"status": "success", "section": section, "data": data})
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"status": "error", "message": "Body must be a JSON object"}), 400
    _put_section_settings(user_id, section, body)
    # Language must actually change replies, so persist it where the brain reads.
    if section == "language":
        lang = str(body.get("language") or "").strip()
        marcus = load_marcus(user_id)
        if marcus:
            try:
                marcus.memory.long_term["reply_language"] = lang
                marcus.memory.save_memory()
            except Exception as exc:
                print(f"[SETTINGS] could not persist reply language: {exc}")
    return jsonify({"status": "success", "section": section, "message": "Saved"})


# ── User profile endpoint ──────────────────────────────────────

@app.route("/api/settings/profile", methods=["GET", "PUT"])
def api_settings_profile():
    user_id, error = _require_login()
    if error:
        return error
    auth = _current_auth()
    email = str(auth.get("email") or "").strip()
    with _users_lock:
        users = _load_users()
        user = users.get(email, {})
    if request.method == "GET":
        return jsonify({
            "status": "success",
            "profile": {
                "username": user.get("name", email.split("@")[0] if email else ""),
                "email": email,
                "avatar": user.get("picture", ""),
                "is_creator": user.get("is_creator", False),
                "created_at": user.get("created_at", ""),
            }
        })
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    with _users_lock:
        users = _load_users()
        if email in users:
            if username:
                users[email]["name"] = username
            if "picture" in body:
                users[email]["picture"] = str(body["picture"]).strip()
            _save_users(users)
    return jsonify({"status": "success", "message": "Profile updated"})


# ── Memory fields API (long-term memory) ───────────────────────

_MEMORY_FIELDS = [
    "about_me", "my_goals", "current_projects", "long_term_vision",
    "skills", "interests", "preferred_communication_style",
    "always_remember", "never_remember",
]


@app.route("/api/settings/memory-fields", methods=["GET", "PUT"])
def api_settings_memory_fields():
    user_id, error = _require_login()
    if error:
        return error
    if request.method == "GET":
        data = _get_section_settings(user_id, "memory")
        return jsonify({"status": "success", "fields": data})
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"status": "error", "message": "Body must be a JSON object"}), 400
    # Merge with existing
    existing = _get_section_settings(user_id, "memory")
    existing.update(body)
    _put_section_settings(user_id, "memory", existing)
    # Feed it into the memory the brain actually reads: store as a high-
    # confidence FACT so it appears in active_facts and shapes replies (a bare
    # preference dict is no longer injected once facts migration has run).
    marcus = load_marcus(user_id)
    if marcus:
        for key, val in body.items():
            val = str(val or "").strip()
            if not val:
                continue
            marcus.memory.remember_preference(key, val[:2000])
            label = key.replace("_", " ").strip()
            summary = val if key.lower() in ("about", "note", "remember", "bio") else f"User's {label}: {val}"
            marcus.memory.remember_fact("fact", summary[:400], val[:2000], confidence=0.95)
    return jsonify({"status": "success", "message": "Memory updated"})


# ── Memory timeline / review ───────────────────────────────────

@app.route("/api/settings/memory-timeline", methods=["GET"])
def api_settings_memory_timeline():
    user_id, error = _require_login()
    if error:
        return error
    marcus = load_marcus(user_id)
    if not marcus:
        return jsonify({"status": "success", "entries": []})
    try:
        mem = marcus.memory.get_full_memory()
        prefs = mem.get("preferences", {})
        identity = mem.get("identity", {})
        entries = []
        for k, v in prefs.items():
            entries.append({"key": k, "value": str(v)[:200], "type": "preference", "time": ""})
        for k, v in identity.items():
            entries.append({"key": k, "value": str(v)[:200], "type": "identity", "time": ""})
        return jsonify({"status": "success", "entries": entries})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ── Projects CRUD ──────────────────────────────────────────────

@app.route("/api/settings/projects", methods=["GET", "POST"])
@app.route("/api/settings/projects/<project_id>", methods=["PUT", "DELETE"])
def api_settings_projects(project_id=None):
    user_id, error = _require_login()
    if error:
        return error
    settings = _load_settings(user_id)
    projects = settings.get("projects_list", [])
    if not isinstance(projects, list):
        projects = []

    if request.method == "GET":
        return jsonify({"status": "success", "projects": projects})

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        pid = f"proj_{secrets.token_hex(6)}"
        project = {
            "id": pid,
            "name": str(body.get("name", "Untitled Project"))[:100],
            "description": str(body.get("description", ""))[:2000],
            "goal": str(body.get("goal", ""))[:500],
            "deadline": str(body.get("deadline", ""))[:100],
            "status": str(body.get("status", "active"))[:20],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        projects.append(project)
        settings["projects_list"] = projects
        _save_settings(user_id, settings)
        # Update Marcus memory
        marcus = load_marcus(user_id)
        if marcus:
            marcus.memory.remember_preference("current_project", project["name"])
        return jsonify({"status": "success", "project": project})

    if not project_id:
        return jsonify({"status": "error", "message": "project_id required"}), 400

    if request.method == "PUT":
        body = request.get_json(silent=True) or {}
        for p in projects:
            if p.get("id") == project_id:
                for key in ("name", "description", "goal", "deadline", "status"):
                    if key in body:
                        p[key] = str(body[key])[:2000]
                p["updated_at"] = datetime.now().isoformat()
                break
        settings["projects_list"] = projects
        _save_settings(user_id, settings)
        return jsonify({"status": "success", "message": "Project updated"})

    if request.method == "DELETE":
        projects = [p for p in projects if p.get("id") != project_id]
        settings["projects_list"] = projects
        _save_settings(user_id, settings)
        return jsonify({"status": "success", "message": "Project deleted"})

    return jsonify({"status": "error", "message": "Method not allowed"}), 405


# ── Storage usage (real data) ──────────────────────────────────

def _get_storage_usage(user_id: str) -> dict:
    usage = {"images_mb": 0, "videos_mb": 0, "documents_mb": 0, "knowledge_mb": 0, "memory_mb": 0, "cache_mb": 0}
    try:
        # Count generated images
        gen_dir = PROJECT_ROOT / "static" / "generated"
        if gen_dir.exists():
            total_bytes = sum(f.stat().st_size for f in gen_dir.glob("**/*") if f.is_file())
            usage["images_mb"] = round(total_bytes / (1024 * 1024), 1)

        # Memory data
        mem_dir = PROJECT_ROOT / "memory_data" / "users" / _safe_user_id(user_id)
        if mem_dir.exists():
            total_bytes = sum(f.stat().st_size for f in mem_dir.glob("**/*") if f.is_file())
            usage["memory_mb"] = round(total_bytes / (1024 * 1024), 1)

        # Settings/knowledge
        settings_dir = PROJECT_ROOT / "memory_data" / "settings"
        if settings_dir.exists():
            total_bytes = sum(f.stat().st_size for f in settings_dir.glob("**/*") if f.is_file())
            usage["documents_mb"] = round(total_bytes / (1024 * 1024), 1)

        # Cache estimate
        usage["cache_mb"] = round(usage["memory_mb"] * 0.15, 1)

        total = sum(usage.values())
        usage["total_mb"] = round(total, 1)
        usage["available_mb"] = round(max(500 - total, 0), 1)
        usage["used_pct"] = round(min((total / 500) * 100, 100), 1)
    except Exception:
        pass
    return usage


@app.route("/api/settings/storage", methods=["GET"])
def api_settings_storage():
    user_id, error = _require_login()
    if error:
        return error
    return jsonify({"status": "success", "usage": _get_storage_usage(user_id)})


# ── Usage analytics ────────────────────────────────────────────

@app.route("/api/settings/usage", methods=["GET"])
def api_settings_usage():
    user_id, error = _require_login()
    if error:
        return error
    marcus = load_marcus(user_id)
    sessions = _load_sessions_index(user_id)
    total_messages = sum(int(s.get("message_count", 0)) for s in sessions)

    images_done, videos_done = _get_usage_counts(user_id)
    tier = _get_user_tier(user_id)
    limits = _tier_limits()

    usage = {
        "chat_sessions": len(sessions),
        "chat_messages": total_messages,
        "tier": tier,
        "images_generated": images_done,
        "videos_generated": videos_done,
        "images_limit": limits[tier]["images"],
        "videos_limit": limits[tier]["videos"],
        "limits": limits,
        "memory_entries": len(marcus.memory.get_full_memory().get("preferences", {})) + len(marcus.memory.get_full_memory().get("identity", {})) if marcus else 0,
        "knowledge_items": len(_load_settings(user_id).get("knowledge_items", []) or []),
        "storage_mb": _get_storage_usage(user_id).get("total_mb", 0),
        "sessions": sessions[:50],
    }
    return jsonify({"status": "success", "usage": usage})


def _tier_limits() -> dict:
    """Per-tier caps (env-overridable). Display-only for now — not enforced."""
    def _i(name, default):
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default
    return {
        "free": {"images": _i("FREE_IMAGE_LIMIT", 30), "videos": _i("FREE_VIDEO_LIMIT", 5)},
        "paid": {"images": _i("PAID_IMAGE_LIMIT", 1000), "videos": _i("PAID_VIDEO_LIMIT", 200)},
    }


def _get_user_tier(user_id: str) -> str:
    users = _load_users()
    for u in users.values():
        if _safe_user_id(u.get("email", "")) == user_id:
            t = str(u.get("tier", "free")).strip().lower()
            return t if t in ("free", "paid") else "free"
    return "free"


def _get_usage_counts(user_id: str) -> tuple:
    coll = usage_collection()
    if coll is not None:
        try:
            doc = coll.find_one({"_id": user_id}) or {}
            return int(doc.get("images", 0)), int(doc.get("videos", 0))
        except Exception as exc:
            print(f"[USAGE] read failed: {exc}")
    return 0, 0


# ── Knowledge items ────────────────────────────────────────────

def _sync_knowledge_to_memory(user_id: str, items: list) -> None:
    """Mirror the user's knowledge items into their brain memory so the chat
    engine can ground answers in them (see MarcusBrain._user_documents_context).
    Best-effort — never blocks the settings write on failure."""
    try:
        marcus = load_marcus(user_id)
        if not marcus:
            return
        docs = []
        for it in items or []:
            content = str(it.get("content") or "").strip()
            if not content:
                continue
            docs.append({
                "id": it.get("id"),
                "title": it.get("title") or "Untitled",
                "type": it.get("type") or "note",
                "content": content,
            })
        marcus.memory.long_term["documents"] = docs
        marcus.memory.save_memory()
    except Exception as exc:
        print(f"[KNOWLEDGE] Failed to sync knowledge to memory: {exc}")


@app.route("/api/settings/knowledge", methods=["GET", "POST", "DELETE"])
def api_settings_knowledge():
    user_id, error = _require_login()
    if error:
        return error
    settings = _load_settings(user_id)
    items = settings.get("knowledge_items", [])
    if not isinstance(items, list):
        items = []

    if request.method == "GET":
        return jsonify({"status": "success", "items": items})

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        # PDFs carry extracted text (client-side, via pdf.js) and can be long;
        # notes stay short. Cap generously so real documents survive.
        is_doc = str(body.get("type", "note")) in ("pdf", "doc", "txt")
        max_len = 40000 if is_doc else 5000
        item = {
            "id": f"know_{secrets.token_hex(6)}",
            "type": str(body.get("type", "note"))[:50],
            "title": str(body.get("title", "Untitled"))[:200],
            "content": str(body.get("content", ""))[:max_len],
            "created_at": datetime.now().isoformat(),
        }
        items.append(item)
        settings["knowledge_items"] = items
        _save_settings(user_id, settings)
        # Mirror into the user's brain memory so it's retrievable in chat.
        _sync_knowledge_to_memory(user_id, items)
        return jsonify({"status": "success", "item": item})

    if request.method == "DELETE":
        body = request.get_json(silent=True) or {}
        item_id = str(body.get("id") or "").strip()
        items = [i for i in items if i.get("id") != item_id]
        settings["knowledge_items"] = items
        _save_settings(user_id, settings)
        _sync_knowledge_to_memory(user_id, items)
        return jsonify({"status": "success", "message": "Deleted"})

    return jsonify({"status": "error", "message": "Method not allowed"}), 405


# ── Media library ──────────────────────────────────────────────

@app.route("/api/settings/media", methods=["GET", "DELETE"])
def api_settings_media():
    user_id, error = _require_login()
    if error:
        return error
    if request.method == "DELETE":
        body = request.get_json(silent=True) or {}
        url = str(body.get("url") or "").strip()
        if url:
            fname = url.rsplit("/", 1)[-1]
            fpath = PROJECT_ROOT / "static" / "generated" / fname
            if fpath.exists() and fpath.is_file():
                fpath.unlink()
                return jsonify({"status": "success", "message": "Deleted"})
        return jsonify({"status": "error", "message": "File not found"}), 404
    images = []
    gen_dir = PROJECT_ROOT / "static" / "generated"
    if gen_dir.exists():
        for f in sorted(gen_dir.glob("*.*"), key=lambda x: x.stat().st_mtime, reverse=True):
            ext = f.suffix.lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                images.append({
                    "url": f"/static/generated/{f.name}",
                    "name": f.name,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "type": "image",
                    "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
    return jsonify({"status": "success", "images": images, "videos": []})


# ── Billing/plans info ─────────────────────────────────────────

@app.route("/api/settings/billing", methods=["GET"])
def api_settings_billing():
    user_id, error = _require_login()
    if error:
        return error
    return jsonify({
        "status": "success",
        "plan": "free",
        "subscription_status": "active",
        "billing_history": [],
        "payment_methods": [],
        "invoices": [],
        "usage": {
            "monthly_chats": 0,
            "monthly_images": 0,
            "credits_remaining": 100,
            "monthly_limit": 100,
        },
        "plans": {
            "free": {
                "name": "Free",
                "price": 0,
                "currency": "NGN",
                "features": ["100 chats/month", "50 images/month", "Basic memory", "Standard support"],
            },
            "pro": {
                "name": "Pro",
                "price": 15000,
                "currency": "NGN",
                "features": ["Unlimited chats", "500 images/month", "Advanced memory", "Priority support", "Knowledge base", "Creator profile"],
            },
            "enterprise": {
                "name": "Enterprise",
                "price": 50000,
                "currency": "NGN",
                "features": ["Everything in Pro", "Unlimited images", "Team access", "Custom integrations", "Dedicated support", "API access"],
            },
        },
    })


# ── Login history / devices / active sessions ──────────────────

@app.route("/api/settings/security", methods=["GET"])
def api_settings_security():
    user_id, error = _require_login()
    if error:
        return error
    auth = _current_auth()
    return jsonify({
        "status": "success",
        "two_factor_enabled": False,
        "connected_accounts": [
            {"provider": "google", "connected": bool(auth.get("email"))},
        ],
        "login_history": [
            {
                "time": datetime.now().isoformat(),
                "ip": request.remote_addr or "127.0.0.1",
                "device": request.headers.get("User-Agent", "Unknown")[:100],
            }
        ],
        "devices": [
            {
                "name": request.headers.get("User-Agent", "Current Device")[:50],
                "current": True,
                "last_active": datetime.now().isoformat(),
            }
        ],
        "active_sessions": 1,
    })


# ── Developer Mode API (internal only; never exposed in normal UI) ──────


@app.route("/api/dev/provider-status", methods=["GET"])
def dev_provider_status():
    user_id, error = _require_login()
    if error:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    manager = pm.get_manager()
    return jsonify({
        "status": "success",
        "providers": manager.provider_status(),
        "metrics": manager.metrics(),
    })


@app.route("/api/dev/routing-log", methods=["GET"])
def dev_routing_log():
    user_id, error = _require_login()
    if error:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    manager = pm.get_manager()
    limit = request.args.get("limit", 100, type=int)
    return jsonify({
        "status": "success",
        "log": manager.recent_routing_log(limit=limit),
    })


# ── Media Library API ────────────────────────────────────────────────────────


# Live Studio runs: run_id -> {"notes": [...], "answer": str|None}. Lets the
# user redirect a run while it is in flight ("rename the lead", "scene 3 at
# night") and lets the crew ask a clarifying question back mid-run.
_studio_runs = {}
_studio_runs_lock = Lock()


def _studio_take_notes(run_id: str) -> list:
    """Drain any notes the user has added since the last stage boundary."""
    if not run_id:
        return []
    with _studio_runs_lock:
        run = _studio_runs.get(run_id)
        if not run or not run["notes"]:
            return []
        notes, run["notes"] = run["notes"], []
        return notes


def _studio_assemble(user_id: str, clips: list, media) -> dict:
    """Join the studio clips into one trailer with ffmpeg (Elena's stage).

    Default is a hard-cut concat (streaming copy, ~16MB peak) which is safe on a
    512MB instance. Crossfade transitions re-encode the whole timeline and peak
    well above 512MB, so they are opt-in via STUDIO_ASSEMBLY_MODE=crossfade.
    Pulls each clip out of GridFS to a temp file, assembles, stores the result.
    """
    import tempfile, shutil
    import core.video_assembly as va

    if not va.available():
        return {"error": "ffmpeg not available"}

    mode = os.getenv("STUDIO_ASSEMBLY_MODE", "hard_cut").strip().lower()
    workdir = tempfile.mkdtemp(prefix="studio_assembly_")
    local_paths = []
    try:
        db = get_db()
        import gridfs
        bucket = gridfs.GridFSBucket(db) if db is not None else None
        for i, clip in enumerate(sorted(clips, key=lambda c: c.get("number", 0))):
            fname = clip["video_url"].rsplit("/", 1)[-1]
            dest = os.path.join(workdir, f"clip_{i:02d}.mp4")
            data = None
            if bucket is not None:
                try:
                    data = bucket.open_download_stream_by_name(fname).read()
                except Exception:
                    data = None
            if data is None:  # local-disk fallback copy
                disk = PROJECT_ROOT / clip["video_url"].lstrip("/")
                if disk.exists():
                    data = disk.read_bytes()
            if not data:
                continue
            with open(dest, "wb") as f:
                f.write(data)
            local_paths.append(dest)

        if len(local_paths) < 2:
            return {"error": "not enough clips available to assemble"}

        out_path = os.path.join(workdir, "trailer.mp4")
        ok, err = va.assemble(local_paths, out_path, mode=mode)
        if not ok or not os.path.exists(out_path):
            return {"error": err or "assembly failed"}

        # Persist the trailer to GridFS + gallery via the same media manager
        rec = media.save_media(out_path, media_type="video", prompt="Studio trailer",
                               provider="StudioAssembly", chat_id=f"studio_{user_id}")
        if not rec:
            return {"error": "could not store trailer"}
        return {"video_url": rec["local_path"], "mode": mode}
    except Exception as exc:
        print(f"[STUDIO] assembly crashed: {exc}")
        return {"error": str(exc)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _studio_save_run(user_id: str, run: dict):
    """Persist the latest Studio run so it survives a page reload."""
    coll = studio_runs_collection()
    if coll is None:
        return
    try:
        coll.replace_one(
            {"_id": user_id},
            {"_id": user_id, "updated_at": datetime.now(timezone.utc), **run},
            upsert=True,
        )
    except Exception as exc:
        print(f"[STUDIO] could not persist run: {exc}")


@app.route("/api/studio/intake", methods=["POST"])
def api_studio_intake():
    """Conversational intake — greetings, clarifying questions, and a readiness
    signal. NOTHING is generated here; the crew just talks until there's enough
    to work with (or the user says go)."""
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    latest = str(data.get("message") or "").strip()
    history = data.get("history") or []
    if not latest:
        return jsonify({"status": "error", "message": "message is required"}), 400

    import core.studio as studio
    from core.brain import _call_llm_cluster

    force = studio.user_said_go(latest) and _studio_has_idea(history, latest)
    try:
        raw, _ = _call_llm_cluster(studio.intake_messages(history, latest), timeout=30)
        decision = studio.parse_intake(raw, force_ready=force)
        # The model occasionally returns an empty turn — one retry for a real
        # contextual reply before falling back to a generic prompt.
        if not force and not str((studio._parse_json_block(raw) or {}).get("reply", "")).strip():
            raw2, _ = _call_llm_cluster(studio.intake_messages(history, latest), timeout=30)
            retry = studio.parse_intake(raw2, force_ready=force)
            if str((studio._parse_json_block(raw2) or {}).get("reply", "")).strip():
                decision = retry
    except Exception as exc:
        print(f"[STUDIO] intake failed: {exc}")
        decision = {
            "mode": "gathering", "persona": "Marcus",
            "reply": "Tell me a bit about what you're picturing — the story, the mood, who's in it.",
            "quick_replies": [], "brief": "",
        }
    return jsonify({"status": "success", **decision})


def _studio_has_idea(history: list, latest: str) -> bool:
    """A bare 'go' with no prior idea shouldn't force a start."""
    total = sum(len(str(h.get("text", ""))) for h in (history or []) if h.get("role") == "user")
    return (total + len(latest)) > 12


@app.route("/api/studio/last", methods=["GET"])
def api_studio_last():
    """The user's most recent Studio run, for restoring the surface on reload."""
    user_id, error = _require_login()
    if error:
        return error
    coll = studio_runs_collection()
    if coll is None:
        return jsonify({"status": "success", "run": None})
    try:
        doc = coll.find_one({"_id": user_id}) or None
        if doc:
            doc.pop("_id", None)
            doc.pop("updated_at", None)
        return jsonify({"status": "success", "run": doc})
    except Exception as exc:
        print(f"[STUDIO] could not load last run: {exc}")
        return jsonify({"status": "success", "run": None})


@app.route("/api/studio/note", methods=["POST"])
def api_studio_note():
    """Add a mid-run note, or answer a question the crew asked."""
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or "").strip()
    text = str(data.get("text") or "").strip()
    if not run_id or not text:
        return jsonify({"status": "error", "message": "run_id and text are required"}), 400
    with _studio_runs_lock:
        run = _studio_runs.setdefault(run_id, {"notes": [], "answer": None})
        run["notes"].append(text)
        run["answer"] = text
    return jsonify({"status": "success"})


@app.route("/api/studio/run", methods=["POST"])
def api_studio_run():
    """ValleyMind Studio pipeline (SSE): Angelina writes -> Marcus breaks it into
    scenes -> one storyboard image per scene. Text + image only; video generation
    is not part of this pipeline and its kill switch is untouched."""
    user_id, error = _require_login()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    idea = str(data.get("idea") or "").strip()
    run_id = str(data.get("run_id") or "").strip()
    if not idea:
        return jsonify({"status": "error", "message": "An idea is required"}), 400

    from core.brain import _call_llm_cluster, _call_llm_cluster_stream
    import core.studio as studio

    if run_id:
        with _studio_runs_lock:
            _studio_runs.setdefault(run_id, {"notes": [], "answer": None})

    def generate():
        notes_applied = []
        # Mirrors what the Studio shows, persisted so a reload restores it
        saved = {"idea": idea, "script": "", "sheet_text": "", "scenes": [], "frames": [], "clips": []}

        def fold_notes():
            """Pick up any late direction the user typed while this run is live."""
            fresh = _studio_take_notes(run_id)
            if fresh:
                notes_applied.extend(fresh)
            return notes_applied

        try:
            # ── Stage 1: Angelina writes (streamed token by token) ──────
            yield f"data: {json.dumps({'stage': 'writing', 'status': 'working'})}\n\n"
            script = ""
            try:
                for token in _call_llm_cluster_stream(studio.script_messages(idea)):
                    if not token:
                        continue
                    script += token
                    yield f"data: {json.dumps({'stage': 'writing', 'token': token})}\n\n"
            except Exception as exc:
                print(f"[STUDIO] script generation failed: {exc}")
                yield f"data: {json.dumps({'error': 'Angelina could not finish the script. Please try again.'})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

            # Character sheet — threaded through every later stage for continuity
            sheet, sheet_text, look = {}, "", ""
            try:
                raw, _ = _call_llm_cluster(studio.character_sheet_messages(idea, script), timeout=40)
                parsed = studio._parse_json_block(raw)
                if isinstance(parsed, dict):
                    sheet = parsed
                    sheet_text = studio._sheet_to_text(parsed)
                    look = str(parsed.get("look", "") or "").strip()
            except Exception as exc:
                print(f"[STUDIO] character sheet failed (continuing without): {exc}")

            saved["script"] = script
            saved["sheet_text"] = sheet_text
            yield f"data: {json.dumps({'stage': 'writing', 'status': 'done', 'character_sheet': sheet, 'sheet_text': sheet_text})}\n\n"

            # ── Ambiguity check: the crew may ask one question back ─────
            if run_id:
                try:
                    q_raw, _ = _call_llm_cluster(studio.clarify_messages(idea, script), timeout=25)
                    question = studio.parse_question(q_raw)
                    if question:
                        yield f"data: {json.dumps({'question': question, 'persona': 'Marcus'})}\n\n"
                        # Give the user a short window to answer; whatever they
                        # type lands in the run's notes and is folded in below.
                        waited = 0.0
                        while waited < 20.0:
                            time.sleep(1.0)
                            waited += 1.0
                            with _studio_runs_lock:
                                run = _studio_runs.get(run_id) or {}
                                if run.get("answer"):
                                    break
                        answered = fold_notes()
                        yield f"data: {json.dumps({'question_resolved': True, 'notes': answered})}\n\n"
                except Exception as exc:
                    print(f"[STUDIO] clarify step skipped: {exc}")

            # ── Stage 2: Marcus breaks it into numbered scenes ──────────
            yield f"data: {json.dumps({'stage': 'directing', 'status': 'working'})}\n\n"
            scenes = []
            try:
                raw, _ = _call_llm_cluster(
                    studio.scene_messages(idea, script, sheet_text, notes=fold_notes()), timeout=60,
                )
                scenes = studio.normalize_scenes(studio._parse_json_block(raw))
            except Exception as exc:
                print(f"[STUDIO] scene breakdown failed: {exc}")

            if not scenes:
                yield f"data: {json.dumps({'error': 'Marcus could not break the script into scenes. Please try again.'})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

            saved["scenes"] = scenes
            for scene in scenes:
                yield f"data: {json.dumps({'stage': 'directing', 'scene': scene})}\n\n"
            yield f"data: {json.dumps({'stage': 'directing', 'status': 'done', 'scene_count': len(scenes)})}\n\n"

            # ── Stage 3: one storyboard frame per scene ─────────────────
            yield f"data: {json.dumps({'stage': 'storyboard', 'status': 'working', 'total': len(scenes)})}\n\n"
            config = get_config()
            media = get_media_manager(user_id)
            # Keep the provider's own URL per scene: image-to-video needs a
            # source Alibaba can load, and its OSS URL is ideal.
            frame_sources = {}
            for scene in scenes:
                # Late direction can land between frames — pick it up per frame
                prompt = studio.storyboard_prompt(scene, sheet_text, look, notes=fold_notes())
                try:
                    result = pm.get_manager().execute(
                        pm.Capability.IMAGE, prompt=prompt,
                        prefer=pm.studio_image_provider(),
                        api_key=config.gemini_api_key or None, enhance=False,
                    )
                    if not result.success:
                        raise RuntimeError(result.error or "image provider failed")
                    source_url = result.data.get("image_url", "")
                    record = media.save_image(
                        source_url, prompt=prompt,
                        provider=result.provider_name, chat_id=f"studio_{user_id}",
                    )
                    stored = _safe_persist_url(record, source_url)
                    if not stored:
                        raise RuntimeError("could not store storyboard frame")
                    # Prefer the provider's remote URL for i2v; fall back to our
                    # stored copy (inlined as base64) for local-only providers.
                    frame_sources[scene["number"]] = source_url if source_url.startswith("http") else stored
                    frame_evt = {"number": scene["number"], "title": scene["title"], "image_url": stored}
                    saved["frames"].append(frame_evt)
                    _studio_save_run(user_id, saved)
                    yield f"data: {json.dumps({'stage': 'storyboard', 'frame': frame_evt})}\n\n"
                except Exception as exc:
                    print(f"[STUDIO] storyboard frame {scene.get('number')} failed: {exc}")
                    yield f"data: {json.dumps({'stage': 'storyboard', 'frame_failed': scene.get('number')})}\n\n"

            yield f"data: {json.dumps({'stage': 'storyboard', 'status': 'done'})}\n\n"

            # ── Stage 4: animate each still into a clip (image-to-video) ─
            # Gated by the same global kill switch as all video generation.
            import core.video_i2v as i2v
            clip_scenes = [s for s in scenes if s["number"] in frame_sources][:studio.max_clips()]
            if not _video_generation_enabled():
                yield f"data: {json.dumps({'stage': 'clips', 'status': 'disabled', 'message': VIDEO_DISABLED_MESSAGE})}\n\n"
            elif not i2v.available() or not clip_scenes:
                yield f"data: {json.dumps({'stage': 'clips', 'status': 'skipped'})}\n\n"
            else:
                yield f"data: {json.dumps({'stage': 'clips', 'status': 'working', 'total': len(clip_scenes)})}\n\n"
                for scene in clip_scenes:
                    n = scene["number"]
                    motion = studio.clip_prompt(scene, notes=fold_notes())
                    out = i2v.generate_clip(motion, frame_sources[n])
                    if out.get("error"):
                        print(f"[STUDIO] clip for scene {n} failed: {out['error']}")
                        yield f"data: {json.dumps({'stage': 'clips', 'clip_failed': n})}\n\n"
                        continue
                    rec = media.save_video(
                        out["video_url"], prompt=f"Scene {n}: {scene['title']}",
                        provider="AlibabaI2V", chat_id=f"studio_{user_id}",
                    )
                    stored_clip = rec["local_path"] if rec else ""
                    if not stored_clip:
                        yield f"data: {json.dumps({'stage': 'clips', 'clip_failed': n})}\n\n"
                        continue
                    # Permanent GridFS URL only — never the provider's expiring link
                    clip_evt = {"number": n, "title": scene["title"], "video_url": stored_clip}
                    saved["clips"].append(clip_evt)
                    _studio_save_run(user_id, saved)
                    yield f"data: {json.dumps({'stage': 'clips', 'clip': clip_evt})}\n\n"
                yield f"data: {json.dumps({'stage': 'clips', 'status': 'done'})}\n\n"

                # ── Elena assembles the clips into one trailer ──────────
                if len(saved["clips"]) >= 2:
                    yield f"data: {json.dumps({'stage': 'assembly', 'status': 'working'})}\n\n"
                    final = _studio_assemble(user_id, saved["clips"], media)
                    if final.get("video_url"):
                        saved["final_video"] = final["video_url"]
                        saved["assembly_mode"] = final.get("mode", "hard_cut")
                        _studio_save_run(user_id, saved)
                        yield f"data: {json.dumps({'stage': 'assembly', 'status': 'done', 'final_video': final['video_url'], 'mode': final.get('mode')})}\n\n"
                    else:
                        yield f"data: {json.dumps({'stage': 'assembly', 'status': 'failed', 'message': final.get('error', 'assembly failed')})}\n\n"

            yield f"data: {json.dumps({'done': True})}\n\n"

        except Exception as exc:
            print(f"[STUDIO] pipeline crashed: {exc}")
            yield f"data: {json.dumps({'error': 'The Studio run failed unexpectedly.'})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.route("/api/media/images", methods=["GET"])
def api_media_images():
    """List user's images with optional search and pagination."""
    user_id, error = _require_login()
    if error:
        return error

    search = str(request.args.get("search") or "").strip()
    chat_id = str(request.args.get("chat_id") or "").strip()
    limit = min(int(request.args.get("limit") or 50), 200)
    offset = max(int(request.args.get("offset") or 0), 0)

    mgr = get_media_manager(user_id)
    images = mgr.list_images(chat_id=chat_id, search=search, limit=limit, offset=offset)
    total = mgr.count_images(chat_id=chat_id)
    return jsonify({"status": "success", "images": images, "total": total})


@app.route("/api/media/videos", methods=["GET"])
def api_media_videos():
    """List user's videos with optional search and pagination."""
    user_id, error = _require_login()
    if error:
        return error

    search = str(request.args.get("search") or "").strip()
    chat_id = str(request.args.get("chat_id") or "").strip()
    limit = min(int(request.args.get("limit") or 50), 200)
    offset = max(int(request.args.get("offset") or 0), 0)

    mgr = get_media_manager(user_id)
    videos = mgr.list_videos(chat_id=chat_id, search=search, limit=limit, offset=offset)
    total = mgr.count_videos(chat_id=chat_id)
    return jsonify({"status": "success", "videos": videos, "total": total})


@app.route("/api/media/<media_id>", methods=["GET"])
def api_media_detail(media_id):
    """Get a single media record."""
    user_id, error = _require_login()
    if error:
        return error

    mgr = get_media_manager(user_id)
    record = mgr.get_media(media_id)
    if not record:
        return jsonify({"status": "error", "message": "Media not found"}), 404
    return jsonify({"status": "success", "media": record})


@app.route("/api/media/<media_id>", methods=["DELETE"])
def api_media_delete(media_id):
    """Delete a media item."""
    user_id, error = _require_login()
    if error:
        return error

    mgr = get_media_manager(user_id)
    deleted = mgr.delete_media(media_id)
    if not deleted:
        return jsonify({"status": "error", "message": "Media not found"}), 404
    return jsonify({"status": "success", "message": "Media deleted"})


@app.route("/static/media/users/<user_id>/<path:subpath>")
def serve_user_media(user_id, subpath):
    """Serve user media files from Mongo/GridFS, falling back to local disk."""
    filename = subpath.rsplit("/", 1)[-1]
    db = get_db()
    if db is not None:
        try:
            import gridfs

            bucket = gridfs.GridFSBucket(db)
            grid_out = bucket.open_download_stream_by_name(filename)
            data = grid_out.read()
            content_type = (grid_out.metadata or {}).get("content_type") or "application/octet-stream"
            return Response(data, mimetype=content_type)
        except Exception as exc:
            print(f"[MEDIA] GridFS serve miss for {filename}, falling back to local disk: {exc}")

    media_dir = PROJECT_ROOT / "memory_data" / "users" / user_id / "media"
    return send_from_directory(str(media_dir), subpath)


# ── Static frontend serving (same-origin, eliminates CORS) ──────────────


@app.route("/")
def serve_index():
    return send_from_directory(str(PROJECT_ROOT), "index.html")


@app.route("/<path:path>")
def serve_frontend_assets(path):
    allowed_files = ["manifest.json", "sw.js", "phone-studio.html", "jpj.txt"]

    if path in allowed_files or path.startswith("static/"):
        import os as _os
        resolved = _os.path.normpath(_os.path.join(str(PROJECT_ROOT), path))
        print(f"[TRACE STATIC] Requested: /{path}")
        print(f"[TRACE STATIC] send_from_directory('{PROJECT_ROOT}', '{path}')")
        print(f"[TRACE STATIC] resolved absolute: {_os.path.abspath(resolved)}")
        print(f"[TRACE STATIC] exists: {_os.path.exists(resolved)}")
        print(f"[TRACE STATIC] app.root_path: {app.root_path}")
        print(f"[TRACE STATIC] CWD: {_os.getcwd()}")
        print(f"[TRACE STATIC] app.static_folder: {app.static_folder}")
        print(f"[TRACE STATIC] CAUGHT-BY-CATCHALL: {path}")
        if not _os.path.exists(resolved):
            print(f"[TRACE STATIC] FILE DOES NOT EXIST at resolved path: {resolved}")
        resp = send_from_directory(str(PROJECT_ROOT), path)
        print(f"[TRACE STATIC] Response type: {type(resp).__name__}")
        try:
            print(f"[TRACE STATIC] Response status: {resp.status_code}")
        except Exception:
            pass
        return resp

    print(f"[TRACE STATIC] SPA fallback for: /{path}")
    return send_from_directory(str(PROJECT_ROOT), "index.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "").lower() == "true")