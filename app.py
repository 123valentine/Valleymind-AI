import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from threading import Lock
from urllib.parse import quote

from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from core.brain import MarcusBrain
from core.config import PROJECT_ROOT
from core.tts import speak_marcus

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
_cors_origins = _allowed_origins or ([] if _is_production else _local_dev_origins)
if _cors_origins:
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
    SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "true" if _is_production else "false").lower() == "true",
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


def _derive_initial_user_name(email: str) -> str:
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

    brain = MarcusBrain(
        memory_file=str(memory_path),
        behavior_file=str(behavior_path),
    )
    with _marcus_lock:
        _marcus_by_user[user_id] = brain
    return brain


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


@app.route("/")
def home():
    return send_from_directory(PROJECT_ROOT, "index.html")


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
    return jsonify({
        "authenticated": True,
        "email": auth.get("email", ""),
        "user_id": user_id,
        "character": "marcus",
        "memory_loaded": bool(marcus),
    })


@app.route("/chat/history", methods=["GET"])
def chat_history():
    user_id, error = _require_login()
    if error:
        return error

    marcus = load_marcus(user_id)
    if not marcus:
        return jsonify({"status": "error", "message": "Marcus is not configured"}), 404
    _refresh_marcus_memory(marcus)

    chat_id = f"{marcus.profile.key}_main_chat"
    messages = marcus.memory.get_chat(chat_id)
    return jsonify({"status": "success", "messages": messages})


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
    with _users_lock:
        users = _load_users()
        user = users.get(email)

        if user:
            stored_hash = str(user.get("password_hash") or "")
            if not check_password_hash(stored_hash, password):
                return jsonify({"status": "error", "message": "Invalid email or password"}), 401
        else:
            users[email] = {
                "user_id": user_id,
                "password_hash": generate_password_hash(password),
            }
            _save_users(users)

    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["email"] = email
    session["user"] = {"id": user_id, "email": email}

    token = secrets.token_urlsafe(32)
    _auth_tokens[token] = {"user_id": user_id, "email": email}

    marcus = load_marcus(user_id)
    if marcus:
        _initialize_user_memory(marcus, email)
    return jsonify({
        "status": "success",
        "authenticated": True,
        "email": email,
        "character": "marcus",
        "session_token": token,
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
        if not message:
            return jsonify({"status": "error", "message": "message field is required"}), 400

        marcus = load_marcus(user_id)

        if not marcus:
            return jsonify({
                "status": "error",
                "message": "Marcus is not configured",
            }), 404
        auth = _current_auth()
        _initialize_user_memory(marcus, auth.get("email", ""))
        _debug_user_memory(user_id, marcus)

        reply = marcus.respond(message)
        voice = speak_marcus(reply)

        return jsonify({
            "status": "success",
            "character": "marcus",
            "reply": reply,
            "voice": voice,
        })

    except Exception as e:
        print(f"[CRITICAL] /chat crashed: {e}")
        return jsonify({
            "status": "error",
            "message": "Internal server error",
        }), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "").lower() == "true")
