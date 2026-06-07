import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import quote

import certifi
from flask import Flask, Response, jsonify, request, send_from_directory, session, stream_with_context
from flask_cors import CORS
from pymongo import MongoClient
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

# ── MongoDB Atlas ────────────────────────────────────────────────────────
_mongo_uri = os.getenv("MONGODB_URI", "").strip()
if not _mongo_uri:
    _mongo_uri = "mongodb+srv://egbujievalentine_db_user:oaXtrA7Et5Mjva6W@valleymind-ai.cx8cbqf.mongodb.net/?appName=ValleyMind-AI"
_mongo_client: MongoClient | None = None
_mongo_db = None
_mongo_chat_sessions = None
_mongo_messages = None

try:
    _mongo_client = MongoClient(_mongo_uri, tlsCAFile=certifi.where())
    _mongo_db = _mongo_client["valleymind_db"]
    _mongo_chat_sessions = _mongo_db["chat_sessions"]
    _mongo_messages = _mongo_db["messages"]
    _mongo_client.admin.command("ping")
    print("[SUCCESS] Pinged your deployment. You successfully connected to MongoDB Atlas!")
    # Ensure indexes for sidebar session listing and message retrieval
    _mongo_chat_sessions.create_index(
        [("user_id", 1), ("last_updated", -1)],
        name="user_sessions_idx",
        background=True,
    )
    _mongo_chat_sessions.create_index(
        [("session_id", 1), ("user_id", 1)],
        name="session_user_unique",
        unique=True,
        background=True,
    )
    _mongo_messages.create_index(
        [("session_id", 1), ("timestamp", 1)],
        name="session_messages_idx",
        background=True,
    )
    _mongo_messages.create_index(
        [("user_id", 1)],
        name="messages_user_idx",
        background=True,
    )
except Exception as e:
    print(f"[ERROR] MongoDB Atlas connection failed: {e}")
    _mongo_client = None
    _mongo_db = None
    _mongo_chat_sessions = None
    _mongo_messages = None

app = Flask(__name__)
app.permanent_session_lifetime = timedelta(days=30)
_is_production = os.getenv("RENDER", "").lower() == "true" or os.getenv("FLASK_ENV", "").lower() == "production"
_allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
_local_dev_origins = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:5501",
    "http://localhost:5501",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "null",
]
_cors_origins = _allowed_origins or (
    _local_dev_origins
    if not _is_production
    else r"https?://([\w-]+\.)*(vercel\.app|onrender\.com)"
)
CORS(app, supports_credentials=True, origins=_cors_origins)

# Cache Marcus per authenticated user so memory never leaks across accounts.
_marcus_by_user = {}
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
    SESSION_COOKIE_SAMESITE="None",
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
            session["is_creator"] = auth.get("is_creator", False)
            session["user"] = {
                "id": auth.get("user_id", ""),
                "email": auth.get("email", ""),
                "is_creator": auth.get("is_creator", False),
            }
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


def load_marcus(user_id: str):
    user_id = str(user_id or "").strip()
    if not user_id:
        return None

    with _marcus_lock:
        cached = _marcus_by_user.get(user_id)
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
            _marcus_by_user[user_id] = brain
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


def _fetch_mongo_history(session_id: str, user_id: str) -> list:
    """Fetch and format message history from the messages collection."""
    try:
        if _mongo_messages is None:
            return []
        cursor = _mongo_messages.find(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0},
        ).sort("timestamp", 1)
        raw = list(cursor)
        history = []
        for doc in raw:
            user_msg = (doc.get("user_message") or "").strip()
            ai_resp = (doc.get("ai_response") or "").strip()
            if user_msg:
                history.append({"role": "user", "content": user_msg})
            if ai_resp:
                history.append({"role": "assistant", "content": ai_resp})
        return history
    except Exception as exc:
        print(f"[ERROR] _fetch_mongo_history failed: {exc}")
        return []


@app.route("/auth/status", methods=["GET"])
def auth_status():
    auth = _current_auth()
    user_id = str(auth.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"authenticated": False})

    # Touch Marcus here so memory auto-restores when the browser auto-logs in.
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


@app.route("/chat/sessions", methods=["GET"])
def chat_sessions():
    user_id, error = _require_login()
    if error:
        return error
    if not user_id:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        if not _mongo_chat_sessions:
            print("[ERROR] /chat/sessions: _mongo_chat_sessions is None — DB not connected")
            return jsonify({"status": "success", "sessions": []})
        cursor = _mongo_chat_sessions.find(
            {"user_id": user_id},
            {"_id": 0},
        ).sort("last_updated", -1)
        return jsonify({"status": "success", "sessions": list(cursor)})
    except Exception as exc:
        print(f"[ERROR] /chat/sessions failed: {exc}")
        return jsonify({"status": "success", "sessions": []})


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
        return jsonify({"status": "success", "session": {
            "chat_id": session["chat_id"],
            "title": session["title"],
            "created_at": session["created_at"],
            "last_activity": session["last_activity"],
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
        # Also clean up from chat_sessions and messages collections
        if _mongo_chat_sessions is not None:
            try:
                _mongo_chat_sessions.delete_one({"session_id": chat_id, "user_id": user_id})
            except Exception:
                pass
        if _mongo_messages is not None:
            try:
                _mongo_messages.delete_many({"session_id": chat_id, "user_id": user_id})
            except Exception:
                pass
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


@app.route("/api/chat/history", methods=["GET"])
def api_chat_history():
    """Return all chat sessions for the authenticated user, sorted by last_updated descending."""
    try:
        print(f"[DEBUG-AUTH] Loading history for user_id: {session.get('user_id')} and email: {session.get('email')}")
        user_id, error = _require_login()
        if error:
            return error
        if not _mongo_chat_sessions:
            print("[ERROR] /api/chat/history: _mongo_chat_sessions is None — DB not connected")
            return jsonify([]), 200
        if not user_id:
            print("[ERROR] /api/chat/history: user_id is empty after auth check")
            return jsonify({"error": "Unauthorized"}), 401
        cursor = _mongo_chat_sessions.find(
            {"user_id": user_id},
            {"_id": 0},
        ).sort("last_updated", -1)
        return jsonify({"sessions": list(cursor)})
    except Exception as exc:
        print(f"[ERROR] /api/chat/history failed: {exc}")
        return jsonify([]), 200


@app.route("/api/chat/messages", methods=["GET"])
def api_chat_messages():
    """Return all message pairs for a given session_id, ordered by timestamp ascending."""
    user_id, error = _require_login()
    if error:
        return error
    session_id = request.args.get("session_id", "").strip()
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    try:
        if not _mongo_messages:
            return jsonify({"messages": []})
        cursor = _mongo_messages.find(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0},
        ).sort("timestamp", 1)
        raw = list(cursor)
        messages = []
        for doc in raw:
            messages.append({"role": "user", "content": doc.get("user_message", "")})
            messages.append({"role": "assistant", "content": doc.get("ai_response", "")})
        return jsonify({"messages": messages})
    except Exception as exc:
        print(f"[ERROR] /api/chat/messages failed: {exc}")
        return jsonify({"error": "Failed to fetch messages"}), 500


@app.route("/api/chat/message", methods=["POST"])
def api_chat_message():
    """
    Chat endpoint backed by the 'messages' collection for persistent session memory.

    Request body:
      Required: { "session_id": "...", "message": "..." }
      Optional: { "response": "..." } — if pre-provided, skip LLM call and persist directly.

    When 'response' is NOT provided, Marcus fetches all past messages from the
    'messages' collection for this session_id, injects them into the LLM context
    (after System Prompt, before the new user message), generates a reply, persists
    both the user message and the AI response, and returns the reply.
    """
    if not _mongo_chat_sessions or not _mongo_messages:
        return jsonify({"error": "Database not initialized"}), 500

    user_id, error = _require_login()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    user_message = (data.get("message") or "").strip()
    preprovided_response = (data.get("response") or "").strip()

    if not session_id or not user_message:
        return jsonify({"error": "session_id and message are required"}), 400

    try:
        # ── Upsert session metadata ───────────────────────────────────────
        existing = _mongo_chat_sessions.find_one({
            "chat_id": session_id,
            "user_id": user_id,
        })
        title = (existing or {}).get("title", "")
        if not title:
            title = user_message[:25].rstrip()

        _mongo_chat_sessions.update_one(
            {"chat_id": session_id, "user_id": user_id},
            {"$set": {"title": title, "last_updated": datetime.utcnow(), "session_id": session_id},
             "$inc": {"message_count": 1}},
            upsert=True,
        )

        # ── Save user message immediately (before LLM call) ───────────────
        _msg_result = _mongo_messages.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "user_message": user_message,
            "ai_response": "",
            "timestamp": datetime.utcnow(),
        })

        # ── Resolve response (call LLM or use pre-provided) ───────────────
        if preprovided_response:
            ai_response = preprovided_response
        else:
            _system_prompt = (
                _CHAT_SYSTEM_PROMPT
                if isinstance(_CHAT_SYSTEM_PROMPT, str) and _CHAT_SYSTEM_PROMPT
                else "You are Marcus, an authentic AI assistant."
            )
            try:
                history = _fetch_mongo_history(session_id, user_id)
                messages = [{"role": "system", "content": _system_prompt}]
                messages.extend(history)
                messages.append({"role": "user", "content": user_message})
                ai_response = _call_llm_cluster(messages)
            except Exception:
                ai_response = "Marcus is currently optimizing his connection. Please try sending your message again."

        # ── Update saved doc with AI response ─────────────────────────────
        _mongo_messages.update_one(
            {"_id": _msg_result.inserted_id},
            {"$set": {"ai_response": ai_response}},
        )

        return jsonify({
            "status": "success",
            "reply": ai_response,
            "character": "marcus",
            "session_id": session_id,
        })

    except Exception as exc:
        print(f"[ERROR] /api/chat/message failed: {exc}")
        return jsonify({"error": "Failed to process message"}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Chat endpoint backed by MongoDB for short-term conversation memory.

    Accepts JSON: { "user_id": "...", "message": "..." }
    - Reads all prior messages for that user from MongoDB as context.
    - Saves the incoming user message immediately.
    - Generates a reply via Marcus brain.
    - Appends the assistant reply to the same MongoDB document.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "error", "message": "No JSON body received"}), 400

        user_id = str(data.get("user_id") or "").strip()
        message = str(data.get("message") or "").strip()

        if not user_id:
            return jsonify({"status": "error", "message": "user_id is required"}), 400
        if not message:
            return jsonify({"status": "error", "message": "message is required"}), 400

        if not _mongo_chat_collection:
            return jsonify({"status": "error", "message": "MongoDB not available"}), 503

        # ── 1. Fetch previous messages from MongoDB ──────────────────────
        doc = _mongo_chat_collection.find_one({"user_id": user_id})
        previous_messages = doc["messages"] if doc and isinstance(doc.get("messages"), list) else []

        # ── 2. Build context string from history ─────────────────────────
        context_lines = []
        for prev in previous_messages[-8:]:   # last 8 exchanges for context
            role = prev.get("role", "user")
            content = prev.get("content", "")
            context_lines.append(f"{role}: {content}")
        context_str = "\n".join(context_lines)
        full_prompt = f"{context_str}\nuser: {message}" if context_str else message

        # ── 3. Save user message to MongoDB immediately ──────────────────
        now_iso = datetime.now(timezone.utc).isoformat()
        user_entry = {"role": "user", "content": message, "timestamp": now_iso}
        _mongo_chat_collection.update_one(
            {"user_id": user_id},
            {"$push": {"messages": user_entry}},
            upsert=True,
        )

        # ── 4. Get AI response via Marcus ────────────────────────────────
        marcus = load_marcus(user_id)
        if not marcus:
            reply = "I'm sorry, but I'm not fully configured yet. Please try again later."
        else:
            auth = _current_auth()
            if auth.get("email"):
                _initialize_user_memory(marcus, auth["email"])
            reply = marcus.respond(full_prompt)

        # ── 5. Append assistant reply to MongoDB ─────────────────────────
        assistant_entry = {"role": "assistant", "content": reply, "timestamp": datetime.now(timezone.utc).isoformat()}
        _mongo_chat_collection.update_one(
            {"user_id": user_id},
            {"$push": {"messages": assistant_entry}},
        )

        return jsonify({
            "status": "success",
            "reply": reply,
            "character": "marcus",
            "user_id": user_id,
        })

    except Exception as exc:
        print(f"[CRITICAL] /api/chat crashed: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


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

        resolved_chat_id = chat_id or f"{marcus.profile.key}_main_chat"
        mongo_history = _fetch_mongo_history(resolved_chat_id, user_id)
        reply = marcus.respond(message, chat_id=resolved_chat_id, image_data=image_data, mongo_history=mongo_history)
        response_meta = getattr(marcus, "last_response_meta", {}) or {}
        voice = (
            {"enabled": True, "spoken": False, "engine": "browser", "reason": "reply too long for blocking server TTS"}
            if len(reply) > 900
            else speak_marcus(reply)
        )

        updated_title = None
        if message and resolved_chat_id and resolved_chat_id != f"{marcus.profile.key}_main_chat":
            try:
                session_info = marcus.memory.db_manager.get_session(resolved_chat_id)
                if session_info:
                    current_title = str(session_info.get("title") or "")
                    if current_title in ("", "New Chat", "Untitled Thread"):
                        words = message.split()
                        if len(words) >= 3:
                            title = " ".join(words[:5]).rstrip(".,!?;:")
                            if len(title) > 40:
                                title = title[:40].rsplit(" ", 1)[0] if " " in title[:40] else title[:40]
                            marcus.memory.set_title(resolved_chat_id, title)
                            updated_title = title
            except Exception as exc:
                print(f"[WARN] Auto-title fallback failed: {exc}")

        return jsonify({
            "status": "success",
            "chat_id": resolved_chat_id,
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
        mongo_history = _fetch_mongo_history(resolved_chat_id, user_id)

        def generate():
            updated_title = None
            if message and resolved_chat_id != f"{marcus.profile.key}_main_chat":
                try:
                    session_info = marcus.memory.db_manager.get_session(resolved_chat_id)
                    if session_info:
                        current_title = str(session_info.get("title") or "")
                        if current_title in ("", "New Chat", "Untitled Thread"):
                            words = message.split()
                            if len(words) >= 3:
                                title = " ".join(words[:5]).rstrip(".,!?;:")
                                if len(title) > 40:
                                    title = title[:40].rsplit(" ", 1)[0] if " " in title[:40] else title[:40]
                                marcus.memory.set_title(resolved_chat_id, title)
                                updated_title = title
                except Exception as exc:
                    print(f"[WARN] Auto-title fallback failed: {exc}")

            try:
                for token in marcus.stream_respond(message, chat_id=resolved_chat_id, image_data=image_data, mongo_history=mongo_history):
                    if token:
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


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "").lower() == "true")
