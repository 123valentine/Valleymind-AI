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
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_from_directory, session, stream_with_context
from werkzeug.security import check_password_hash, generate_password_hash

from core.brain import MarcusBrain, _call_llm_cluster, _CHAT_SYSTEM_PROMPT
from core.config import PROJECT_ROOT
from core.tts import speak_marcus

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



app = Flask(__name__, static_folder='../static', static_url_path='/static')
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


def _load_session_secret() -> str:
    configured = os.getenv("SECRET_KEY", "").strip() or os.getenv("FLASK_SECRET_KEY", "").strip()
    if configured:
        return configured
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

    token = str(
        request.headers.get("X-Session-Token")
        or request.headers.get("Authorization", "").replace("Bearer ", "", 1)
        or ""
    ).strip()
    if token:
        auth = _auth_tokens.get(token)
        if auth and auth.get("user_id"):
            session.permanent = True
            session["user_id"] = auth.get("user_id", "")
            session["email"] = auth.get("email", "")
            session["user"] = {"id": auth.get("user_id", ""), "email": auth.get("email", "")}
            return auth

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

def _normalize_session_doc(doc: dict) -> dict:
    """Normalize session records from chat_sessions or chats collections."""
    if not isinstance(doc, dict):
        return {}
    chat_id = str(doc.get("chat_id") or doc.get("session_id") or "").strip()
    if not chat_id:
        return {}
    title = str(doc.get("title") or "Untitled Thread").strip() or "Untitled Thread"
    last_updated = (
        doc.get("last_updated")
        or doc.get("last_activity")
        or doc.get("created_at")
        or ""
    )
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
        "created_at": doc.get("created_at") or last_updated,
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

    with _marcus_lock:
        cached = _cache_marcus_by_user.get(user_id)
    if cached is not None:
        return cached

    char_folder = PROJECT_ROOT / "character" / "marcus"
    behavior_path = char_folder / "behavior.json"
    memory_path = PROJECT_ROOT / "memory_data" / "users" / user_id / "marcus" / "long_term.json"

    if not behavior_path.exists():
        print(f"[ERROR] Marcus behavior.json not found at {behavior_path}")
        return None

    try:
        brain = MarcusBrain(
            memory_file=str(memory_path),
            behavior_file=str(behavior_path),
        )
        with _marcus_lock:
            _cache_marcus_by_user[user_id] = brain
        return brain
    except Exception as exc:
        print(f"[ERROR] Failed to instantiate Marcus brain: {exc}")
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
    _auth_tokens[token] = {"user_id": user_id, "email": email, "is_creator": is_creator}

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


@app.route("/logout", methods=["POST"])
@app.route("/auth/logout", methods=["POST"])
def logout():
    token = str(
        request.headers.get("X-Session-Token")
        or request.headers.get("Authorization", "").replace("Bearer ", "", 1)
        or ""
    ).strip()
    if token:
        _auth_tokens.pop(token, None)
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

        marcus = load_marcus(user_id)

        if not marcus:
            return jsonify({
                "status": "error",
                "message": "Marcus is not configured",
            }), 404
        auth = _current_auth()
        _initialize_user_memory(marcus, auth.get("email", ""))
        _debug_user_memory(user_id, marcus)

        reply = marcus.respond(message, chat_id=chat_id, image_data=image_data)
        response_meta = getattr(marcus, "last_response_meta", {}) or {}
        voice = (
            {"enabled": True, "spoken": False, "engine": "browser", "reason": "reply too long for blocking server TTS"}
            if len(reply) > 900
            else speak_marcus(reply)
        )

        updated_title = None
        if message and chat_id:
            try:
                sessions = _list_user_sessions(user_id)
                current_title = None
                for s in sessions:
                    if s.get("chat_id") == chat_id:
                        current_title = s.get("title", "")
                        break
                if current_title in (None, "", "New Chat", "Untitled Thread"):
                    words = message.split()
                    if len(words) >= 3:
                        title = " ".join(words[:8]).rstrip(".", ",", "!", "?", ";", ":")
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
            "character": "marcus",
            "reply": reply,
            "voice": voice,
            "updated_title": updated_title,
            "detected_route": str(response_meta.get("detected_route") or ""),
            "groq_used": bool(response_meta.get("groq_used")),
            "live_routing_used": bool(response_meta.get("live_routing_used")),
            "fallback_used": bool(response_meta.get("fallback_used")),
            "fallback_source": str(response_meta.get("fallback_source") or ""),
        })

    except Exception as e:
        print(f"[CRITICAL] /chat crashed: {e}")
        return jsonify({
            "status": "error",
            "message": "Internal server error",
        }), 500


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

        marcus = load_marcus(user_id)
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
                    current_title = None
                    for s in sessions:
                        if s.get("chat_id") == resolved_chat_id:
                            current_title = s.get("title", "")
                            break
                    if current_title in (None, "", "New Chat", "Untitled Thread"):
                        words = message.split()
                        if len(words) >= 3:
                            title = " ".join(words[:8]).rstrip(".", ",", "!", "?", ";", ":")
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
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            }
        )

    except Exception as e:
        print(f"[CRITICAL] /chat/stream crashed: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


# ── Static frontend serving (same-origin, eliminates CORS) ──────────────


@app.route("/")
def serve_index():
    return send_from_directory("../", "index.html")


@app.route("/<path:path>")
def serve_frontend_assets(path):
    allowed_files = ["manifest.json", "sw.js", "phone-studio.html", "jpj.txt"]

    if path in allowed_files or path.startswith("static/"):
        return send_from_directory("../", path)

    return send_from_directory("../", "index.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "").lower() == "true")