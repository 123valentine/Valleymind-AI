"""Enhanced Flask app with Pinecone-backed session handling.

This file combines the clean, modern structure from valleymind-backend/app.py
with the advanced session handling functions from the previous version,
adapted for Pinecone-backed architecture.
"""

import gzip
import hashlib
import io
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import quote

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session, stream_with_context
from werkzeug.security import check_password_hash, generate_password_hash

from core.brain import MarcusBrain, _call_llm_cluster, _CHAT_SYSTEM_PROMPT
from core.config import PROJECT_ROOT, get_config
from core.db import auth_tokens_collection, app_config_collection, chats_collection, get_db, studio_runs_collection, usage_collection, users_collection
from core.media_manager import get_media_manager
from core.router import RouteDecision, get_router
from core.seo import PUBLIC_PAGES as SEO_PAGE_REGISTRY
from core.seo import URL_ALIASES as SEO_URL_ALIASES
from core.seo import robots_txt as seo_robots_body
from core.seo import sitemap_xml as seo_sitemap_body
from core.seo import render_page as seo_render_page
from core.tts import speak_marcus
from core.video_dispatcher import get_video_dispatcher
from core import ai_builder
from core import template_library as tpllib
import core.template_render as tr
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
# Hard ceiling on request bodies. Video uploads are the only large payload and a
# phone can easily produce 200MB+, which a 512MB instance cannot absorb.
try:
    _max_upload_mb = float(os.getenv("VIDEO_UPLOAD_MAX_MB", "100"))
except (TypeError, ValueError):
    _max_upload_mb = 100.0
app.config["MAX_CONTENT_LENGTH"] = int(_max_upload_mb * 1024 * 1024) + (2 * 1024 * 1024)

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
_auth_attempt_times = {}   # {(ip, endpoint): [datetime]} — auth brute-force guard
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


# ── Auth migration on startup (dry-run first, then apply) ──────────────────
def _run_auth_migration():
    """Run the unified auth migration at startup.  First performs a dry-run
    to report what WOULD change, then applies the changes.  Records in
    DELETE_IDS are removed.  All output goes to stdout for operator
    visibility.  Failures are caught and logged -- the app must never
    refuse to start because of a migration issue."""
    from core.auth_migration import DELETE_IDS, normalize_user_records
    coll = users_collection()
    if coll is None:
        print("[AUTH_MIGRATE] MongoDB unavailable -- skipping startup migration")
        return
    try:
        # Phase 1: dry-run
        dry = normalize_user_records(coll, dry_run=True)
        print(f"[AUTH_MIGRATE] dry-run: scanned={dry['scanned']} would_modify={dry['would_modify']}"
              f" would_delete={dry['would_delete']}"
              f" verified_kept={dry['verified_kept']}"
              f" unverified_normalized={dry['unverified_normalized']}"
              f" legacy_fields_removed={dry['legacy_fields_removed']}"
              f" email_mirrored={dry['email_mirrored']}"
              f" errors={dry['errors']}")
        if dry.get("would_delete"):
            print(f"[AUTH_MIGRATE] delete_ids: {sorted(DELETE_IDS)}")
        # Per-record detail for operator review
        for rec in dry.get("records", []):
            if rec.get("changes"):
                tag = " [DELETE]" if rec.get("deleted") else ""
                print(f"[AUTH_MIGRATE]   {_id_summary(rec['_id'])} changes={len(rec['changes'])}{tag}")
                for ch in rec["changes"]:
                    print(f"[AUTH_MIGRATE]     {ch['field']}: {ch['old']} -> {ch['new']} ({ch['reason']})")

        # Phase 2: apply (only if there are changes or deletions)
        if dry["would_modify"] > 0 or dry["would_delete"] > 0:
            applied = normalize_user_records(coll, dry_run=False)
            print(f"[AUTH_MIGRATE] applied: modified={applied['actually_modified']}"
                  f" deleted={applied['actually_deleted']}"
                  f" verified_kept={applied['verified_kept']}"
                  f" unverified_normalized={applied['unverified_normalized']}"
                  f" legacy_fields_removed={applied['legacy_fields_removed']}"
                  f" email_mirrored={applied['email_mirrored']}"
                  f" errors={applied['errors']}")
        else:
            print("[AUTH_MIGRATE] all records already canonical -- no changes needed")
    except Exception as exc:
        print(f"[AUTH_MIGRATE] startup migration failed (non-fatal): {exc}")


def _id_summary(doc_id) -> str:
    """Compact repr of a user _id (email) for migration logs.  No secrets."""
    s = str(doc_id or "")
    at = s.find("@")
    if at > 0:
        return s[:at + 1] + "***"
    return s[:12] + "***" if len(s) > 12 else s


# Run migration at module load (once, before any request is served).
_run_auth_migration()


# Jinja helper used by the marketing templates' footer copyright year.
@app.template_filter("utcnow_year")
def _utcnow_year_filter(_value=None):
    return datetime.now(timezone.utc).year


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


def _require_login_only():
    """Require an authenticated session but do NOT check email verification.
    Use this only for auth-related endpoints (verify, resend, OTP, etc.)
    where the user must be able to interact before their email is verified."""
    user_id = _current_user_id()
    if not user_id:
        return "", (jsonify({"status": "error", "message": "Login required"}), 401)
    return user_id, None


def _require_login():
    """THE single server-side gate for normal application access.

    Requires an authenticated session AND ValleyMind email verification.
    Verification must be literally True on the user record — missing, NULL,
    or malformed state is UNVERIFIED (see core/auth_migration.py). A session
    without an email address is also gated: credentials alone never grant
    access. Unverified users receive a 403 with needs_verification=true so
    the frontend routes them to the verification screen. Auth-related
    endpoints (verify / resend / OTP) use _require_login_only instead so
    an unverified user can complete verification.

    Hardened guards (fail-closed):
      1. user_id must be non-empty (session/token present).
      2. email must be non-empty after strip (sessions without email cannot
         pass — credentials alone never grant access).
      3. User record must exist AND be a dict (deleted/phantom records).
      4. is_verified_record must return literally True (not truthy, not
         string "true", not missing — see auth_migration.is_verified_record).
    Every guard defaults to DENIED. No bypass path exists."""
    from core.auth_migration import is_verified_record

    # ── Guard 1: session must identify a user ───────────────────
    user_id = _current_user_id()
    if not user_id:
        return "", (jsonify({"status": "error", "message": "Login required"}), 401)

    # ── Guard 2: session must carry a non-empty email ───────────
    #    A session with a user_id but no email is a stale/incomplete
    #    session.  Credentials alone must never grant application access.
    auth = _current_auth()
    email = str(auth.get("email") or "").strip().lower()
    if not email:
        return "", (jsonify({
            "status": "error",
            "message": "Email verification required",
            "needs_verification": True,
            "email": "",
        }), 403)

    # ── Guard 3: user record must exist and be a dict ───────────
    #    If the users collection is down or the record was deleted,
    #    _load_users() returns {} — .get() yields None — which is
    #    unverified.  A non-dict record (corrupt storage) is also
    #    unverified.
    user_rec = _load_users().get(email)

    # ── Guard 4: is_verified_record must be literally True ──────
    #    Missing field, None, False, int, string "true", empty dict,
    #    or any other non-True value → denied.  is_verified_record()
    #    is the single predicate; never substitute a truthiness check.
    #    Bypassed when EMAIL_VERIFICATION_ENABLED is False (temporarily
    #    disabled until a verified sending domain is configured).
    if EMAIL_VERIFICATION_ENABLED and not is_verified_record(user_rec):
        return "", (jsonify({
            "status": "error",
            "message": "Email verification required",
            "needs_verification": True,
            "email": email,
        }), 403)

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

# ── Password-reset tokens, email, and auth rate limiting ─────────────────────
# Reset is email-based and token-only: a fresh, single-use, time-limited token
# emailed to the account owner. There is deliberately NO security-question or
# any other reset path — the old shared-answer scheme was an account-takeover
# hole (every account had the same answer) and has been removed entirely.

MIN_PASSWORD_LEN = 8
RESET_TOKEN_TTL = timedelta(minutes=30)
# Transactional email challenges (verification + OTP). Only hashes are stored;
# see core/auth_codes.py and core/email_service.py.
VERIFY_TTL = timedelta(minutes=30)
OTP_TTL = timedelta(minutes=10)
EMAIL_RESEND_COOLDOWN = 50   # seconds between resends per account (anti-spam)
MAX_CODE_ATTEMPTS = 5

# ── Email verification gate ────────────────────────────────────────────────
# When False (the default) the OTP / email-verification requirement is
# bypassed: new sign-ups and Google log-ins are immediately usable, and
# existing unverified accounts are not blocked.  All OTP / Resend code is
# preserved and can be re-enabled by setting this to "true".
EMAIL_VERIFICATION_ENABLED = os.getenv(
    "EMAIL_VERIFICATION_ENABLED", "false"
).strip().lower() in ("true", "1", "yes")

# All transactional + promotional mail runs through core/email_service.py
# (Resend HTTP API). Config lives there: RESEND_API_KEY, EMAIL_FROM, etc.
APP_BASE_URL = (
    os.getenv("APP_BASE_URL", "").strip()
    or os.getenv("SITE_URL", "").strip()
    or "https://valleymind-ai-opms.onrender.com"
).rstrip("/")


def _hash_token(token: str) -> str:
    """sha256 hex of a reset token — only the hash is ever stored."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _auth_rate_limited(endpoint: str) -> bool:
    """Max 5 requests per IP per 15 min for a given auth endpoint. In-memory
    (single worker); True means refuse the caller with HTTP 429."""
    now = datetime.now()
    window_start = now - timedelta(minutes=15)
    key = (_client_ip(), endpoint)
    recent = [t for t in _auth_attempt_times.get(key, []) if t > window_start]
    if len(recent) >= 5:
        _auth_attempt_times[key] = recent
        return True
    recent.append(now)
    _auth_attempt_times[key] = recent
    return False


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
    _user_rec = _load_users().get(email_auth) if email_auth else None
    from core.auth_migration import is_verified_record
    return jsonify({
        "authenticated": True,
        "email": email_auth,
        "user_id": user_id,
        "character": "marcus",
        "memory_loaded": bool(marcus),
        "is_creator": _is_creator(email_auth),
        "email_verified": is_verified_record(_user_rec),
        "email_verification_enabled": EMAIL_VERIFICATION_ENABLED,
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
    local = _tts_folder / filename
    if local.exists():
        return send_from_directory(_tts_folder, filename)
    # Persona TTS audio is cached in R2 under assets/tts/<filename>; hand back a
    # short-lived presigned link so the bytes stream straight from Cloudflare.
    try:
        from core import r2_storage
        key = f"assets/tts/{filename}"
        if r2_storage.available() and r2_storage.object_exists(key):
            return redirect(r2_storage.presigned_url(key, expires=3600), code=302)
    except Exception as exc:
        print(f"[TTS] R2 serve miss for {filename}: {exc}")
    return ("not found", 404)


@app.route("/api/tts", methods=["POST"])
def api_tts():
    """Synthesise a persona's line server-side so it sounds identical on every
    device. Cached in R2; metered against the studio budget cap."""
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    persona = normalize_persona(str(data.get("persona") or "marcus"))
    if not text:
        return jsonify({"status": "error", "message": "no text"}), 400
    from core.tts import synthesize_persona
    result = synthesize_persona(text, persona)
    return jsonify({"status": "success", **result})


@app.route("/api/roundtable", methods=["POST"])
def api_roundtable():
    """The Round Table: one 'director' LLM call plans which crew members respond
    to a message, in what order. TTS per turn is done client-side via /api/tts."""
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    history = data.get("history")
    if not message:
        return jsonify({"status": "error", "message": "no message"}), 400
    from core.roundtable import orchestrate, provider_report
    turns = orchestrate(message, history if isinstance(history, list) else [])
    # models: which provider/model each persona ended up on (distinct per persona)
    return jsonify({"status": "success", "turns": turns, "models": provider_report()})


@app.route("/api/music", methods=["POST"])
def api_music():
    """Music Studio — 'Let ValleyMind produce it' stage.

    Turns a brief (which may reference a user-sung/hummed melody recorded in the
    browser) plus production settings into the creative material for a track:
    lyrics and an arrangement/production spec. This is the honest, working AI
    stage of the pipeline: it produces real creative output (lyrics + structure).
    Rendering the finished audio (beat synthesis, AI vocals, mixing) is a
    declared future step and is reported as such rather than faked."""
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    brief = str(data.get("brief") or "").strip()
    role = str(data.get("role") or "singer").strip()
    genre = str(data.get("genre") or "Afrobeats").strip()
    mood = str(data.get("mood") or "romantic").strip()
    tempo = str(data.get("tempo") or "medium").strip()
    key = str(data.get("key") or "").strip()
    language = str(data.get("language") or "English").strip()
    voice = str(data.get("voice") or "").strip()
    user_lyrics = str(data.get("lyrics") or "").strip()

    if not brief and not user_lyrics:
        return jsonify({"status": "error", "message": "Describe your song or add lyrics first"}), 400

    voice_note = ""
    if voice == "clone":
        voice_note = "\nVoice: use the user's recorded voice (they authorized an AI clone of their own voice)."
    elif voice == "elena":
        voice_note = "\nVoice: use ValleyMind's approved AI singing voice, Elena."
    else:
        voice_note = "\nVoice: keep and gently enhance the user's own recorded vocal."

    lyrics_hint = ("\nUser already wrote lyrics (keep them, refine only where asked):\n"
                   + user_lyrics) if user_lyrics else (
                   "\nNo lyrics provided — write full, singable lyrics." )

    system = (
        "You are ValleyMind's music producer. The user sang or hummed a melody "
        "and/or described an idea. You create the working creative package for "
        "the track: complete singable lyrics and a production/arrangement spec. "
        "Do NOT claim to have rendered audio; the mix is produced later. Be "
        "specific and musical."
    )
    prompt = (
        "Brief: " + brief + "\n"
        "Role: " + role + "\n"
        "Genre: " + genre + "\n"
        "Mood: " + mood + "\n"
        "Tempo: " + tempo + "\n"
        "Key: " + (key or "suggest one") + "\n"
        "Language: " + language + lyrics_hint + voice_note + "\n\n"
        "Return JSON exactly like this (no markdown fences):\n"
        "{\"title\":\"...\",\"lyrics\":\"full song lyrics with hooks and sections\","
        "\"structure\":\"e.g. Intro-Verse-Chorus-Verse2-Chorus-Bridge-Outro\","
        "\"arrangement\":\"instruments, beat pattern, tempo BPM, key signature, production notes\","
        "\"note\":\"what the AI produced now vs. what final audio rendering (future) will add\"}"
    )
    try:
        raw, _ = _call_llm_cluster([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ], timeout=45)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": f"Music generation failed: {exc}"}), 502

    out = {}
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            out = json.loads(raw[start:end + 1])
    except Exception:  # noqa: BLE001
        out = {}

    if not isinstance(out, dict):
        out = {}
    return jsonify({
        "status": "success",
        "title": out.get("title", ""),
        "lyrics": out.get("lyrics", ""),
        "structure": out.get("structure", ""),
        "arrangement": out.get("arrangement", ""),
        "note": out.get("note", ""),
        "generated": bool(out),
    })


# ── Music Projects Cloud Sync ─────────────────────────────────────────
# Per-user storage for saved song projects (metadata only — audio blobs
# stay browser-local).  Follows the same file pattern as settings.

_MUSIC_PROJECTS_DIR = PROJECT_ROOT / "memory_data" / "music_projects"


def _music_projects_path(user_id: str) -> Path:
    p = _MUSIC_PROJECTS_DIR / _safe_user_id(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p / "projects.json"


def _load_music_projects(user_id: str) -> list:
    fpath = _music_projects_path(user_id)
    try:
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_music_projects(user_id: str, projects: list):
    fpath = _music_projects_path(user_id)
    try:
        fpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(fpath) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(projects, f, indent=2)
        os.replace(tmp, fpath)
    except OSError as exc:
        print(f"[ERROR] Failed to save music projects: {exc}")


@app.route("/api/music/projects", methods=["GET", "POST"])
def api_music_projects():
    """Cloud-synced music project storage.

    GET  → returns the user's saved projects array.
    POST → full-replace: accepts {"projects": [...]} and overwrites the
           user's server-side store.  Called by the client after every
           save/delete so the server is always a mirror of the client
           state.
    """
    user_id, error = _require_login()
    if error:
        return error
    if request.method == "GET":
        return jsonify({"status": "success", "projects": _load_music_projects(user_id)})
    body = request.get_json(silent=True) or {}
    projects = body.get("projects")
    if not isinstance(projects, list):
        return jsonify({"status": "error", "message": "projects must be a list"}), 400
    # Cap at 200 projects to prevent abuse.
    projects = projects[:200]
    _save_music_projects(user_id, projects)
    return jsonify({"status": "success", "count": len(projects)})


@app.route("/api/music/projects/<project_id>", methods=["DELETE"])
def api_music_project_delete(project_id: str):
    """Delete a single project by id."""
    user_id, error = _require_login()
    if error:
        return error
    projects = _load_music_projects(user_id)
    projects = [p for p in projects if p.get("id") != project_id]
    _save_music_projects(user_id, projects)
    return jsonify({"status": "success"})


@app.route("/api/music/ai-edit", methods=["POST"])
def api_music_ai_edit():
    """Music Studio AI Edit — incremental refinement.

    Accepts an instruction plus the current project state (lyrics, arrangement,
    settings) and returns targeted changes. Supports:
      - Lyrics editing (rewrite, extend, shorten, translate)
      - Arrangement/instrumentation suggestions
      - Mood/tempo/key changes
      - General production direction

    Returns a JSON object with changed fields the client can apply selectively.
    """
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    instruction = str(data.get("instruction") or "").strip()
    if not instruction:
        return jsonify({"status": "error", "message": "Instruction is required"}), 400

    current_lyrics = str(data.get("lyrics") or "").strip()
    current_arrangement = str(data.get("arrangement") or "").strip()
    current_genre = str(data.get("genre") or "").strip()
    current_mood = str(data.get("mood") or "").strip()
    current_tempo = str(data.get("tempo") or "").strip()
    current_key = str(data.get("key") or "").strip()
    current_name = str(data.get("name") or "").strip()

    system = (
        "You are ValleyMind's music producer assistant. The user has an "
        "existing song project and wants to make specific changes. Analyse "
        "the instruction and return ONLY the fields that changed. Do NOT "
        "rewrite unchanged content. Be precise and musical."
    )
    context = (
        "Current project:\n"
        f"Name: {current_name}\n"
        f"Genre: {current_genre}\n"
        f"Mood: {current_mood}\n"
        f"Tempo: {current_tempo}\n"
        f"Key: {current_key or 'not set'}\n"
        f"Arrangement: {current_arrangement or 'not set'}\n"
        f"Lyrics:\n{current_lyrics or '(none yet)'}\n\n"
        f"User instruction: {instruction}\n\n"
        "Return JSON with ONLY the changed fields (no markdown fences):\n"
        '{"title":"...if changed","lyrics":"...if changed",'
        '"arrangement":"...if changed","genre":"...if changed",'
        '"mood":"...if changed","tempo":"...if changed","key":"...if changed",'
        '"changes_summary":"brief description of what changed"}'
    )
    try:
        raw, _ = _call_llm_cluster([
            {"role": "system", "content": system},
            {"role": "user", "content": context},
        ], timeout=45)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": f"AI edit failed: {exc}"}), 502

    out = {}
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            out = json.loads(raw[start:end + 1])
    except Exception:  # noqa: BLE001
        out = {}

    if not isinstance(out, dict):
        out = {}
    return jsonify({
        "status": "success",
        "changes": out,
        "summary": out.pop("changes_summary", ""),
    })


@app.route("/login", methods=["POST"])
@app.route("/auth/login", methods=["POST"])
def login():
    if _auth_rate_limited("login"):
        return jsonify({"status": "error", "message": "Too many attempts. Please try again later."}), 429
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"status": "error", "message": "Valid email is required"}), 400
    if not password:
        return jsonify({"status": "error", "message": "Password is required"}), 400

    user_id = _safe_user_id(email)
    is_creator = _is_creator(email)
    email_verified = False
    with _users_lock:
        users = _load_users()
        user = users.get(email)

        if not user:
            # Unified auth: login NEVER creates accounts. Unknown credentials
            # are rejected with the same generic message as a wrong password
            # (no account enumeration, no unverified skeleton records).
            # Registration (/auth/register) is the only account-creation path.
            return jsonify({"status": "error", "message": "Invalid email or password"}), 401

        stored_hash = str(user.get("password_hash") or "")
        if not check_password_hash(stored_hash, password):
            return jsonify({"status": "error", "message": "Invalid email or password"}), 401

        from core.auth_migration import is_verified_record
        email_verified = is_verified_record(user)
        if is_creator:
            user["identity_name"] = CREATOR_NAME
            user["title"] = CREATOR_TITLE
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
        "email_verified": email_verified,
        "needs_verification": not email_verified,
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
    from core.auth_migration import is_verified_record
    from core import auth_codes

    # Challenge plaintext (emailed once, never stored) when a send is needed.
    _verify_code = _verify_token = None
    challenge_issued = False

    with _users_lock:
        users = _load_users()
        user = users.get(email)
        is_new_user = user is None
        if user:
            # Existing account: attach/refresh the Google identity but keep
            # THIS account's own verification state. A grandfathered verified
            # record stays verified; an unverified one must still complete
            # ValleyMind OTP regardless of what Google asserted.
            user["google_id"] = google_id
            if name:
                user["name"] = name
            if picture:
                user["picture"] = picture
            email_verified = is_verified_record(user)
        else:
            # Unified auth: even Google sign-ups start UNVERIFIED in
            # ValleyMind. Google proves mailbox ownership; ValleyMind OTP
            # proves this person wants access HERE.
            # When EMAIL_VERIFICATION_ENABLED is False, new accounts start
            # verified so the user can enter the app immediately.
            user = {
                "user_id": user_id,
                "google_id": google_id,
                "name": name,
                "picture": picture,
                "email": email,
                "auth_method": "google",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "email_verified": False if EMAIL_VERIFICATION_ENABLED else True,
            }
            if not EMAIL_VERIFICATION_ENABLED:
                user["email_verified_at"] = datetime.now(timezone.utc).isoformat()
            users[email] = user
            email_verified = user["email_verified"]
        if is_creator:
            users[email]["identity_name"] = CREATOR_NAME
            users[email]["title"] = CREATOR_TITLE

        if EMAIL_VERIFICATION_ENABLED and not email_verified:
            # Issue (or rotate) the single-use verification challenge now so
            # the user can complete it right after this response. The resend
            # endpoint remains available for retries/delivery failures.
            _verify_code, _verify_token = auth_codes.set_challenge(
                user, "verify", ttl_seconds=int(VERIFY_TTL.total_seconds()),
                with_token=True, purpose="email_verification")
            challenge_issued = True
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
                print(f"[WARN] Failed to set creator identity for {email}: {exc}")

    # Verification mail goes out synchronously with an honest result, exactly
    # like /auth/register. If the provider rejects it, email_sent=False tells
    # the frontend to offer the resend button; the challenge stays valid.
    email_sent = False
    if challenge_issued and _verify_code:
        try:
            from core import email_service
            _rid = uuid.uuid4().hex[:12]
            print(f"[EMAIL][rid={_rid}] google_verify_fire email={email.split('@')[0]}@*** configured={email_service.available()}")
            _vlink = f"{APP_BASE_URL}/verify-email?token={_verify_token}"
            email_sent = _send_email_now(email_service.send_verification_email,
                                         email, _verify_code, _vlink,
                                         minutes=int(VERIFY_TTL.total_seconds() // 60),
                                         request_id=_rid)
        except Exception as exc:
            print(f"[EMAIL] google_verify_fire_exception {type(exc).__name__}")

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
        "email_verified": email_verified,
        "needs_verification": not email_verified,
        "is_new_user": is_new_user,
        "email_verification_enabled": EMAIL_VERIFICATION_ENABLED,
        "email_sent": bool(email_sent) if challenge_issued else None,
    })


# ── Registration ───────────────────────────────────────────────

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,24}$")


def _normalize_username(value: str) -> str:
    """Return a canonical username (trimmed, lowercased) or empty string."""
    return str(value or "").strip().lower()


def _username_taken(users: dict, username: str, exclude_email: str = "") -> bool:
    """Check whether a username is already in use by another account."""
    uname = _normalize_username(username)
    if not uname:
        return False
    for email, record in users.items():
        if exclude_email and str(email or "").lower() == str(exclude_email or "").lower():
            continue
        if _normalize_username(record.get("username")) == uname:
            return True
        # Legacy accounts named via the old 'name' field are not handles, so
        # only treat exact matches on the explicit username column as taken.
    return False


@app.route("/auth/register", methods=["POST"])
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    full_name = str(data.get("full_name") or data.get("name") or "").strip()
    username = _normalize_username(data.get("username"))
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    confirm_password = str(data.get("confirm_password") or data.get("confirmPassword") or "")
    picture = str(data.get("picture") or "").strip()
    agree_terms = bool(data.get("agree_terms"))
    read_privacy = bool(data.get("read_privacy"))

    # ── Validation ──────────────────────────────────────────────
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"status": "error", "message": "A valid email address is required"}), 400
    if not full_name:
        return jsonify({"status": "error", "message": "Full name is required"}), 400
    if len(full_name) > 120:
        return jsonify({"status": "error", "message": "Full name is too long"}), 400
    if not username or not _USERNAME_RE.match(username):
        return jsonify({"status": "error", "message": "Username must be 3-24 characters using letters, numbers, dots, dashes or underscores"}), 400
    if not password or len(password) < MIN_PASSWORD_LEN:
        return jsonify({"status": "error", "message": f"Password must be at least {MIN_PASSWORD_LEN} characters"}), 400
    if password != confirm_password:
        return jsonify({"status": "error", "message": "Passwords do not match"}), 400
    if not agree_terms:
        return jsonify({"status": "error", "message": "You must agree to the Terms of Service to continue"}), 400
    if not read_privacy:
        return jsonify({"status": "error", "message": "You must confirm you have read the Privacy Policy to continue"}), 400
    if picture and not picture.startswith("data:image/"):
        return jsonify({"status": "error", "message": "Profile picture must be a valid image"}), 400
    if picture and len(picture) > 2_000_000:
        return jsonify({"status": "error", "message": "Profile picture is too large"}), 400

    user_id = _safe_user_id(email)
    is_creator = _is_creator(email)

    with _users_lock:
        users = _load_users()
        if email in users:
            return jsonify({"status": "error", "message": "An account with that email already exists"}), 409
        if _username_taken(users, username):
            return jsonify({"status": "error", "message": "That username is already taken — try another"}), 409

        record = {
            "user_id": user_id,
            "name": full_name,
            "username": username,
            "email": email,
            "picture": picture or "",
            "password_hash": generate_password_hash(password),
            "auth_method": "email",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "email_verified": False,
        }
        if is_creator:
            record["identity_name"] = CREATOR_NAME
            record["title"] = CREATOR_TITLE

        # When email verification is disabled, new accounts start verified
        # so the user can enter the app immediately.  OTP/Resend code is
        # preserved and re-enabled by setting EMAIL_VERIFICATION_ENABLED=true.
        _verify_code = _verify_token = None
        if EMAIL_VERIFICATION_ENABLED:
            # Issue a single-use verification challenge (magic-link token +
            # 6-digit code).  Only the hashes are stored; the plaintext is
            # returned once, to email.
            from core import auth_codes
            _verify_code, _verify_token = auth_codes.set_challenge(
                record, "verify", ttl_seconds=int(VERIFY_TTL.total_seconds()),
                with_token=True, purpose="email_verification")
        else:
            record["email_verified"] = True
            record["email_verified_at"] = datetime.now(timezone.utc).isoformat()
        users[email] = record
        _save_users(users)

    # ── Auto sign-in (mirrors /auth/login) ──────────────────────
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
        try:
            marcus.memory.initialize_user_name(full_name)
            marcus.memory.remember_preference("username", username)
            marcus.memory.remember_fact(
                "fact",
                f"User's full name is {full_name}",
                full_name,
                confidence=0.95,
            )
            marcus.memory.reload()
        except Exception as exc:
            print(f"[WARN] Failed to initialize memory for new user {email}: {exc}")
        if is_creator:
            try:
                marcus.memory.set_creator_identity(CREATOR_NAME, CREATOR_TITLE)
            except Exception as exc:
                print(f"[WARN] Failed to set creator identity in memory: {exc}")

    # Send the verification email SYNCHRONOUSLY (capped at RESEND_TIMEOUT)
    # so the response can reflect whether the provider actually accepted it.
    # The OTP is already persisted on the user record above; if the send fails
    # the user can retry from the verify modal's resend button (which is also
    # synchronous + honest now).  Skipped entirely when verification is disabled.
    email_sent = False
    if EMAIL_VERIFICATION_ENABLED and _verify_code:
        try:
            from core import email_service
            _rid = uuid.uuid4().hex[:12]
            print(f"[EMAIL][rid={_rid}] register_fire email={email.split('@')[0]}@*** configured={email_service.available()}")
            _vlink = f"{APP_BASE_URL}/verify-email?token={_verify_token}"
            email_sent = _send_email_now(email_service.send_verification_email,
                                         email, _verify_code, _vlink,
                                         minutes=int(VERIFY_TTL.total_seconds() // 60),
                                         request_id=_rid)
        except Exception as exc:
            print(f"[EMAIL] register_fire_exception {type(exc).__name__}")

    _reg_email_verified = record.get("email_verified", False)
    return jsonify({
        "status": "success",
        "authenticated": True,
        "email": email,
        "username": username,
        "name": full_name,
        "picture": picture,
        "user_id": user_id,
        "character": "marcus",
        "session_token": token,
        "is_creator": is_creator,
        "email_verified": _reg_email_verified,
        "needs_verification": not _reg_email_verified,
        "email_verification_enabled": EMAIL_VERIFICATION_ENABLED,
        "first_time": True,
        # Honest delivery signal: true only when Resend accepted the message.
        # The account is created either way; the verify modal's resend button
        # covers the failure case. Frontend treats this as advisory only.
        "email_sent": email_sent,
    }), 201


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
    """Step 1 of email-based reset. ALWAYS returns the same generic response so
    it never reveals whether an account exists. If the account does exist, a
    fresh single-use token (only its hash is stored, 30-min expiry) is emailed.
    No account => nothing is sent; the response is identical either way."""
    if _auth_rate_limited("forgot-password"):
        return jsonify({"status": "error", "message": "Too many requests. Please try again later."}), 429

    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    generic = jsonify({
        "status": "success",
        "message": "If an account exists for that email, a reset link has been sent.",
    })

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return generic  # never reveal validity/existence

    raw_token = secrets.token_urlsafe(32)
    with _users_lock:
        users = _load_users()
        user = users.get(email)
        if user:
            user["reset_token_hash"] = _hash_token(raw_token)
            user["reset_token_expires"] = time.time() + RESET_TOKEN_TTL.total_seconds()
            users[email] = user
            _save_users(users)

    # Send outside the lock. Only email when the account exists; either way the
    # caller sees `generic`. The raw token appears only in the link, never logged.
    if user:
        link = f"{APP_BASE_URL}/reset-password?token={raw_token}"
        try:
            from core import email_service
            _rid = uuid.uuid4().hex[:12]
            print(f"[EMAIL][rid={_rid}] password_reset_fire email={email.split('@')[0]}@*** configured={email_service.available()}")
            _fire_email(email_service.send_password_reset_email,
                        email, link,
                        minutes=int(RESET_TOKEN_TTL.total_seconds() // 60),
                        request_id=_rid)
        except Exception as exc:
            print(f"[EMAIL] password_reset_fire_exception {type(exc).__name__}")

    return generic


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    """Step 2 of email-based reset. Accepts only { token, new_password }: the
    token is sha256'd and matched against the stored hash, must be unexpired,
    and is consumed on use. No security-question path exists anymore."""
    if _auth_rate_limited("reset-password"):
        return jsonify({"status": "error", "message": "Too many requests. Please try again later."}), 429

    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "").strip()
    new_password = str(data.get("new_password") or "")

    if not token:
        return jsonify({"status": "error", "message": "Reset token is required."}), 400
    if not new_password or len(new_password) < MIN_PASSWORD_LEN:
        return jsonify({"status": "error", "message": f"Password must be at least {MIN_PASSWORD_LEN} characters."}), 400

    token_hash = _hash_token(token)
    with _users_lock:
        users = _load_users()
        match_email, match_user = None, None
        for em, rec in users.items():
            if rec.get("reset_token_hash") == token_hash:
                match_email, match_user = em, rec
                break

        # Same generic error for "not found" and "expired" — reveal nothing.
        if not match_user:
            return jsonify({"status": "error", "message": "This reset link is invalid or has expired."}), 400

        expires = float(match_user.get("reset_token_expires") or 0)
        if time.time() > expires:
            match_user.pop("reset_token_hash", None)
            match_user.pop("reset_token_expires", None)
            users[match_email] = match_user
            _save_users(users)
            return jsonify({"status": "error", "message": "This reset link is invalid or has expired."}), 400

        # Reuse the existing werkzeug hashing; consume the token (single use).
        match_user["password_hash"] = generate_password_hash(new_password)
        match_user.pop("reset_token_hash", None)
        match_user.pop("reset_token_expires", None)
        users[match_email] = match_user
        _save_users(users)

    # Transactional security notice (best-effort; never blocks the reset).
    try:
        from core import email_service
        _rid = uuid.uuid4().hex[:12]
        _fire_email(email_service.send_security_email,
                    match_email, "Your password was reset",
                    "Your ValleyMind AI password was just reset using a password-reset link. "
                    "If this wasn't you, secure your account immediately.",
                    request_id=_rid)
    except Exception as exc:
        print(f"[EMAIL] security_email_exception {type(exc).__name__}")

    return jsonify({"status": "success", "message": "Your password has been reset. You can now sign in."})


@app.route("/auth/change-password", methods=["POST"])
def change_password():
    user_id, error = _require_login_only()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    current_password = str(data.get("current_password") or "")
    new_password = str(data.get("new_password") or "")

    if not current_password:
        return jsonify({"status": "error", "message": "Current password required."}), 400
    if not new_password or len(new_password) < MIN_PASSWORD_LEN:
        return jsonify({"status": "error", "message": f"New password must be at least {MIN_PASSWORD_LEN} characters."}), 400

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

    try:
        from core import email_service
        _rid = uuid.uuid4().hex[:12]
        _fire_email(email_service.send_security_email,
                    email, "Your password was changed",
                    "Your ValleyMind AI password was just changed from your account settings. "
                    "If this wasn't you, reset your password and contact support.",
                    request_id=_rid)
    except Exception as exc:
        print(f"[EMAIL] security_email_exception {type(exc).__name__}")

    return jsonify({"status": "success", "message": "Password changed successfully."})


# ── Email verification & one-time codes (transactional) ─────────────────────

_EMAIL_UNAVAILABLE = "We couldn't send the email right now. Please try again shortly."
_OTP_PURPOSES = {"email_verification", "login_verification", "password_reset", "security_confirmation"}


def _fire_email(send_fn, *args, **kwargs):
    """Send email in a background thread so the HTTP response is never blocked
    by SMTP.  Failures are logged safely (no secrets) and silently swallowed —
    the caller already returned a generic success/error to the client.

    Only for non-critical mail where the response must stay generic/fast
    (password reset anti-enumeration, security notices). Verification-critical
    sends must use _send_email_now so the API never claims "sent" unconfirmed.
    """
    rid = kwargs.pop("request_id", "") or uuid.uuid4().hex[:12]
    def _bg():
        try:
            send_fn(*args, request_id=rid, **kwargs)
        except Exception as exc:
            print(f"[EMAIL][rid={rid}] bg_fail {type(exc).__name__}: {exc}")
    Thread(target=_bg, daemon=True).start()


def _send_email_now(send_fn, *args, **kwargs) -> bool:
    """Send email SYNCHRONOUSLY and return whether the provider accepted it.

    Used for verification-critical mail (signup verification, resend, OTP) so
    the API response reflects reality: email_service caps the whole Resend
    round-trip at RESEND_TIMEOUT (8s), well inside the frontend's 30s
    fetch timeout. Never logs secrets or codes."""
    from core import email_service
    rid = kwargs.pop("request_id", "") or uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    if not email_service.available():
        print(f"[EMAIL][rid={rid}] sync_skip reason=not_configured elapsed={(time.monotonic() - t0) * 1000:.0f}ms")
        return False
    try:
        ok = bool(send_fn(*args, request_id=rid, **kwargs))
    except Exception as exc:
        print(f"[EMAIL][rid={rid}] sync_exception {type(exc).__name__} elapsed={(time.monotonic() - t0) * 1000:.0f}ms")
        return False
    print(f"[EMAIL][rid={rid}] sync_result ok={ok} total={(time.monotonic() - t0) * 1000:.0f}ms")
    return ok


@app.route("/verify-email", methods=["GET"])
def verify_email_link():
    """Magic-link handler clicked from the verification email. Validates the
    single-use token, marks the account verified, and redirects into the app with
    a status the SPA can display. No login required — the token is the credential."""
    from core import auth_codes
    token = str(request.args.get("token") or "").strip()
    status = "invalid"
    if token:
        token_hash = _hash_token(token)
        with _users_lock:
            users = _load_users()
            match_email, match_rec = None, None
            for em, rec in users.items():
                if rec.get("verify_token_hash") == token_hash:
                    match_email, match_rec = em, rec
                    break
            if match_rec:
                from core.auth_migration import is_verified_record
                if is_verified_record(match_rec):
                    status = "success"
                else:
                    ok, reason = auth_codes.check_token(match_rec, "verify", token)
                    if ok:
                        match_rec["email_verified"] = True
                        match_rec["email_verified_at"] = datetime.now(timezone.utc).isoformat()
                        status = "success"
                    else:
                        status = reason  # expired / invalid
                    users[match_email] = match_rec
                    _save_users(users)
    return redirect(f"{APP_BASE_URL}/?verify={status}", code=302)


@app.route("/api/auth/verify-email", methods=["POST"])
def verify_email_code():
    """Verify via the 6-digit code entered in the app by the signed-in user."""
    user_id, error = _require_login_only()
    if error:
        return error
    from core import auth_codes
    email = str(_current_auth().get("email") or "").strip().lower()
    code = str((request.get_json(silent=True) or {}).get("code") or "").strip()
    if not code:
        return jsonify({"status": "error", "message": "Verification code is required."}), 400
    with _users_lock:
        users = _load_users()
        user = users.get(email)
        if not user:
            print(f"[OTP] verify_email email={email.split('@')[0]}@*** result=not_found")
            return jsonify({"status": "error", "message": "Account not found."}), 404
        from core.auth_migration import is_verified_record
        if is_verified_record(user):
            print(f"[OTP] verify_email email={email.split('@')[0]}@*** result=already_verified")
            return jsonify({"status": "success", "email_verified": True, "message": "Your email is already verified."})
        has_challenge = bool(user.get("verify_code_hash"))
        attempts_before = int(user.get("verify_attempts") or 0)
        ok, reason = auth_codes.check_code(user, "verify", code, max_attempts=MAX_CODE_ATTEMPTS, purpose="email_verification")
        if ok:
            user["email_verified"] = True
            user["email_verified_at"] = datetime.now(timezone.utc).isoformat()
        users[email] = user
        _save_users(users)
    print(f"[OTP] verify_email email={email.split('@')[0]}@*** result={reason} has_challenge={has_challenge} attempts_before={attempts_before}")
    if ok:
        return jsonify({"status": "success", "email_verified": True, "message": "Your email is verified."})
    msg = {"expired": "That code has expired — request a new one.",
           "locked": "Too many attempts — request a new code."}.get(reason, "That code is incorrect.")
    return jsonify({"status": "error", "message": msg}), 400


@app.route("/api/auth/resend-verification", methods=["POST"])
def resend_verification():
    user_id, error = _require_login_only()
    if error:
        return error
    if _auth_rate_limited("resend-verification"):
        return jsonify({"status": "error", "message": "Too many requests. Please try again later."}), 429
    from core import auth_codes, email_service
    email = str(_current_auth().get("email") or "").strip().lower()
    with _users_lock:
        users = _load_users()
        user = users.get(email)
        if not user:
            return jsonify({"status": "error", "message": "Account not found."}), 404
        from core.auth_migration import is_verified_record
        if is_verified_record(user):
            return jsonify({"status": "success", "message": "Your email is already verified."})
        if auth_codes.cooldown_active(user, "verify", EMAIL_RESEND_COOLDOWN):
            return jsonify({"status": "error", "message": "Please wait a moment before requesting another email."}), 429
        code, tok = auth_codes.set_challenge(user, "verify", ttl_seconds=int(VERIFY_TTL.total_seconds()),
                                             with_token=True, purpose="email_verification")
        users[email] = user
        _save_users(users)
    link = f"{APP_BASE_URL}/verify-email?token={tok}"
    _rid = uuid.uuid4().hex[:12]
    print(f"[EMAIL][rid={_rid}] resend_fire email={email.split('@')[0]}@*** configured={email_service.available()}")
    # Synchronous send: only report "sent" when SMTP actually accepted the
    # message. A failure returns 503 so the frontend shows an error instead of
    # a false success.
    sent = _send_email_now(email_service.send_verification_email,
                           email, code, link,
                           minutes=int(VERIFY_TTL.total_seconds() // 60),
                           request_id=_rid)
    if not sent:
        return jsonify({"status": "error", "message": _EMAIL_UNAVAILABLE}), 503
    return jsonify({"status": "success", "message": "Verification email sent — check your inbox."})


@app.route("/api/auth/otp/request", methods=["POST"])
def otp_request():
    """Issue a reusable one-time code for a given purpose (signed-in user)."""
    user_id, error = _require_login_only()
    if error:
        return error
    if _auth_rate_limited("otp-request"):
        return jsonify({"status": "error", "message": "Too many requests. Please try again later."}), 429
    from core import auth_codes, email_service
    email = str(_current_auth().get("email") or "").strip().lower()
    purpose = str((request.get_json(silent=True) or {}).get("purpose") or "security_confirmation").strip()
    if purpose not in _OTP_PURPOSES:
        purpose = "security_confirmation"
    with _users_lock:
        users = _load_users()
        user = users.get(email)
        if not user:
            return jsonify({"status": "error", "message": "Account not found."}), 404
        if auth_codes.cooldown_active(user, "otp", EMAIL_RESEND_COOLDOWN):
            return jsonify({"status": "error", "message": "Please wait a moment before requesting another code."}), 429
        code, _ = auth_codes.set_challenge(user, "otp", ttl_seconds=int(OTP_TTL.total_seconds()), purpose=purpose)
        users[email] = user
        _save_users(users)
    _rid = uuid.uuid4().hex[:12]
    print(f"[EMAIL][rid={_rid}] otp_request_fire email={email.split('@')[0]}@*** purpose={purpose} configured={email_service.available()}")
    # Synchronous send with an honest result — same contract as resend.
    sent = _send_email_now(email_service.send_otp_email,
                           email, code, purpose=purpose,
                           minutes=int(OTP_TTL.total_seconds() // 60),
                           request_id=_rid)
    if not sent:
        return jsonify({"status": "error", "message": _EMAIL_UNAVAILABLE}), 503
    return jsonify({"status": "success", "message": "A one-time code has been sent to your email."})


@app.route("/api/auth/otp/verify", methods=["POST"])
def otp_verify():
    user_id, error = _require_login_only()
    if error:
        return error
    if _auth_rate_limited("otp-verify"):
        return jsonify({"status": "error", "message": "Too many requests. Please try again later."}), 429
    from core import auth_codes
    email = str(_current_auth().get("email") or "").strip().lower()
    data = request.get_json(silent=True) or {}
    code = str(data.get("code") or "").strip()
    purpose = str(data.get("purpose") or "security_confirmation").strip()
    if not code:
        return jsonify({"status": "error", "message": "Code is required."}), 400
    with _users_lock:
        users = _load_users()
        user = users.get(email)
        if not user:
            return jsonify({"status": "error", "message": "Account not found."}), 404
        ok, reason = auth_codes.check_code(user, "otp", code, max_attempts=MAX_CODE_ATTEMPTS, purpose=purpose)
        users[email] = user
        _save_users(users)
    if ok:
        return jsonify({"status": "success", "verified": True, "purpose": purpose})
    msg = {"expired": "That code has expired.",
           "locked": "Too many attempts — request a new code."}.get(reason, "That code is incorrect.")
    return jsonify({"status": "error", "message": msg}), 400


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
    prompt = _build_image_user_context(user_id, message)
    result = pm.get_manager().execute(
        pm.Capability.IMAGE,
        prompt=prompt,
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
            prompt=_build_image_user_context(user_id, message),
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

        yield f"data: {json.dumps({'done': True, 'chat_id': resolved_chat_id, 'updated_title': updated_title, 'reply_mode': bool(getattr(marcus, '_reply_mode', False))})}\n\n"

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
        prompt=_build_image_user_context(user_id, message),
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
            prompt=_build_image_user_context(user_id, message),
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

        yield f"data: {json.dumps({'done': True, 'chat_id': resolved_chat_id, 'updated_title': updated_title, 'reply_mode': bool(getattr(marcus, '_reply_mode', False))})}\n\n"

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
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )


def _build_image_user_context(user_id: str, prompt: str) -> str:
    """Prepend only image-relevant user preferences to the prompt.
    For images we inject: language (for text-in-image), cultural context,
    creative style — NOT all preferences blindly.  The user's explicit
    request is always passed through unchanged AFTER the context block and
    always takes precedence over any preference-derived hint."""
    try:
        settings = _load_settings(user_id)
        prefs = settings.get("preferences") or {}
        lang_section = settings.get("language") or {}
        culture_section = settings.get("culture") or {}
    except Exception:
        return prompt

    context_parts = []

    # Language context — affects text rendered inside images
    response_lang = lang_section.get("response_language") or ""
    if response_lang and response_lang != "en":
        context_parts.append(f"User's preferred language: {response_lang}")

    # Cultural expression — affects visual motifs and style
    cultural_expr = culture_section.get("cultural_expression") or ""
    if cultural_expr and cultural_expr != "off":
        context_parts.append(f"Cultural expression preference: {cultural_expr}")

    # Creative context — relevant for image generation
    creative_style = prefs.get("voice_style") or ""
    if creative_style:
        context_parts.append(f"Visual tone: {creative_style}")

    # Use-case context — helps tailor the image purpose
    use_cases = prefs.get("use_cases")
    if isinstance(use_cases, list) and use_cases:
        context_parts.append(f"User's creative context: {', '.join(use_cases[:3])}")

    if not context_parts:
        return prompt

    ctx = " | ".join(context_parts)
    # The explicit request below always takes precedence over this reference
    # context — preferences only add relevant colour, never override intent.
    return (
        f"[User context (reference only — the explicit request below always "
        f"takes precedence): {ctx}]\n{prompt}"
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
            prompt=_build_image_user_context(user_id, prompt),
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
        "privacy", "language", "culture", "integrations", "extensions",
        "interests", "goals", "accessibility", "security",
        "connected", "tutorials", "help",
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
    # Personalization must actually change the brain, so every saved section is
    # mirrored into long-term memory as preferences the assistant can read.
    _mirror_settings_to_memory(user_id, section, body)
    return jsonify({"status": "success", "section": section, "message": "Saved"})


# ── Preferences Setup Status API ─────────────────────────────────────
# Server-side per-user flag that replaces the fragile sessionStorage
# "vm_pref_setup_pending" flag.  Stored inside the existing preferences
# section of settings.json — no second database, single source of truth.

@app.route("/api/settings/setup-status", methods=["GET", "POST"])
def api_settings_setup_status():
    user_id, error = _require_login()
    if error:
        return error
    prefs = _get_section_settings(user_id, "preferences")
    if request.method == "GET":
        status = prefs.get("preferences_setup_status", "not_started")
        # Authoritative derivation: if the user has saved any meaningful
        # preference data but the flag is still absent (legacy accounts where
        # the setup_status POST was clobbered by a concurrent preferences PUT,
        # or users who configured preferences manually via Settings without
        # going through the wizard), treat the setup as completed.  Only
        # applies when the flag is "not_started" (absent) — an explicit
        # "skipped" or "completed" is always preserved as-is.
        if status == "not_started":
            has_content = any(
                prefs.get(k)
                for k in ("communication_style", "use_cases", "voice_style",
                          "custom_preference", "expressive_language",
                          "preferred_characters", "multilingual_behavior")
            )
            if has_content:
                status = "completed"
        return jsonify({"status": "success", "setup_status": status})
    body = request.get_json(silent=True) or {}
    new_status = str(body.get("setup_status") or "").strip().lower()
    if new_status not in ("completed", "skipped", "not_started"):
        return jsonify({"status": "error", "message": "Invalid setup_status value"}), 400
    prefs["preferences_setup_status"] = new_status
    _put_section_settings(user_id, "preferences", prefs)
    return jsonify({"status": "success", "setup_status": new_status})


def _mirror_preference_to_memory(marcus, key: str, text: str, label: str):
    """Write one user preference into long-term memory as BOTH the legacy
    preference dict entry AND a first-class, AI-readable fact (the preference
    dict is only migrated into facts once, so newly saved keys must be added as
    facts explicitly to stay visible to the brain)."""
    text = str(text or "").strip()
    if not text or text.lower() in ("none", "n/a", "na"):
        return
    marcus.memory.remember_preference(key, text[:2000])
    marcus.memory.remember_fact(
        "preference",
        f"User prefers {label}: {text}"[:400],
        text[:2000],
        confidence=0.9,
    )


def _mirror_settings_to_memory(user_id: str, section: str, body: dict):
    """Persist relevant settings into the user's long-term memory so the brain
    can personalise recommendations and replies. This is the single choke point
    through which every Settings save reaches Marcus memory."""
    try:
        marcus = load_marcus(user_id)
        if not marcus:
            return
        if section == "language":
            lang = str(body.get("language") or "").strip()
            if lang:
                marcus.memory.long_term["reply_language"] = lang
            # NEW: persistent response language (canonical code, e.g. "ig", "pcm", "en")
            response_lang = str(body.get("response_language") or "").strip()
            if response_lang:
                marcus.memory.long_term["response_language"] = response_lang
            # Voluntarily-provided language & cultural background (NEVER inferred —
            # only what the user typed/selected explicitly). Kept as preferences
            # the brain may draw on, not as identity assertions.
            for key, label in (
                ("country", "country"),
                ("state_province", "region/state/province"),
                ("native_languages", "native language(s)"),
                ("cultural_background", "cultural background"),
            ):
                val = body.get(key)
                if isinstance(val, list):
                    val = ", ".join(str(i).strip() for i in val if str(i).strip())
                _mirror_preference_to_memory(marcus, f"language_{key}", val, label)
            prefer_no = body.get("prefer_not_to_say")
            if prefer_no is True or prefer_no == "true":
                marcus.memory.remember_preference("language_prefer_not_to_say", "true")
            elif prefer_no is False or prefer_no == "false":
                marcus.memory.remember_preference("language_prefer_not_to_say", "false")
            marcus.memory.save_memory()
        elif section == "culture":
            # CULTURAL IDENTITY is INDEPENDENT from response language (by design).
            cid = str(body.get("cultural_identity") or "").strip().lower()
            if cid:
                marcus.memory.long_term["culture_identity"] = cid
            use = body.get("use_cultural_adages")
            if use is not None:
                marcus.memory.long_term["use_cultural_adages"] = bool(use)
            # Cultural expression level (off/natural/deep) — stored as a stated
            # preference only; retrieval/rendering arrives in a later phase.
            expr = str(body.get("cultural_expression") or "").strip().lower()
            if expr:
                marcus.memory.long_term["cultural_expression"] = expr
                _mirror_preference_to_memory(marcus, "cultural_expression", expr, "the level of cultural expression in replies")
            marcus.memory.save_memory()
        elif section == "interests":
            tags = body.get("tags", body.get("interests"))
            if isinstance(tags, list):
                tags_text = ", ".join(str(t).strip() for t in tags if str(t).strip())
                if tags_text:
                    marcus.memory.remember_preference("interests", tags_text)
            elif isinstance(tags, str) and tags.strip():
                marcus.memory.remember_preference("interests", tags.strip())
            goals = body.get("goals")
            if isinstance(goals, list):
                goals_text = ", ".join(str(g).strip() for g in goals if str(g).strip())
                if goals_text:
                    marcus.memory.remember_preference("goals", goals_text)
        elif section == "goals":
            for key, val in body.items():
                if isinstance(val, str) and val.strip():
                    marcus.memory.remember_preference(key, val.strip())
        elif section == "preferences":
            _DISPLAY_LABELS = {
                "communication_style": "communication style",
                "communication_note": "extra communication guidance",
                "use_cases": "primary uses of ValleyMind",
                "use_cases_other": "what they use ValleyMind for (other)",
                "use_case_profile": "use-case profile and goals",
                "expressive_language": "expressive language features",
                "about_me": "personal information the user wants remembered",
                "custom_preference": "custom working preference",
                "voice_style": "voice style",
                "language": "response language",
            }
            for key, val in body.items():
                if val is None or val == "" or (isinstance(val, list) and not val):
                    continue
                text = str(val)
                if isinstance(val, list):
                    text = ", ".join(str(i) for i in val)
                text = text[:2000]
                marcus.memory.remember_preference(f"preferences_{key}", text)
                label = _DISPLAY_LABELS.get(key, key.replace("_", " "))
                marcus.memory.remember_fact(
                    "preference",
                    f"User prefers {label}: {text}"[:400],
                    text,
                    confidence=0.9,
                )
            # about_me is personal info the user explicitly wants remembered.
            # Store as a high-confidence fact so the brain treats it as
            # settled truth, not a tentative preference.
            about_me = str(body.get("about_me") or "").strip()
            if about_me:
                marcus.memory.remember_fact(
                    "identity",
                    f"User-provided personal context: {about_me}"[:400],
                    about_me[:2000],
                    confidence=1.0,
                )
            # use_case_profile gives richer context than the checkbox list.
            ucp = str(body.get("use_case_profile") or "").strip()
            if ucp:
                marcus.memory.remember_fact(
                    "preference",
                    f"User use-case profile: {ucp}"[:400],
                    ucp[:2000],
                    confidence=0.95,
                )
        elif section in ("appearance", "notifications", "accessibility", "creator"):
            for key, val in body.items():
                if val is None or val == "":
                    continue
                text = str(val)
                if isinstance(val, list):
                    text = ", ".join(str(i) for i in val)
                marcus.memory.remember_preference(f"{section}_{key}", text[:2000])
    except Exception as exc:
        print(f"[SETTINGS] could not mirror {section} into memory: {exc}")


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
        display_name = user.get("name") or user.get("username") or (email.split("@")[0] if email else "")
        return jsonify({
            "status": "success",
            "profile": {
                "full_name": user.get("name", ""),
                "username": user.get("username", display_name),
                "name": display_name,
                "email": email,
                "avatar": user.get("picture", ""),
                "auth_method": user.get("auth_method", ""),
                "is_creator": user.get("is_creator", False),
                "created_at": user.get("created_at", ""),
            }
        })
    body = request.get_json(silent=True) or {}
    full_name = str(body.get("full_name") or "").strip()
    username = _normalize_username(body.get("username"))
    with _users_lock:
        users = _load_users()
        if email in users:
            if full_name:
                users[email]["name"] = full_name[:120]
            if username:
                if not _USERNAME_RE.match(username):
                    return jsonify({"status": "error", "message": "Username must be 3-24 characters using letters, numbers, dots, dashes or underscores"}), 400
                if _username_taken(users, username, exclude_email=email):
                    return jsonify({"status": "error", "message": "That username is already taken"}), 409
                users[email]["username"] = username
            if "picture" in body:
                users[email]["picture"] = str(body["picture"]).strip()
            _save_users(users)
        # Keep Marcus memory in sync so the brain refers to the user correctly.
        marcus = load_marcus(user_id)
        if marcus:
            try:
                if full_name:
                    marcus.memory.initialize_user_name(full_name)
                if username:
                    marcus.memory.remember_preference("username", username)
            except Exception as exc:
                print(f"[WARN] Failed to sync profile into memory: {exc}")
    return jsonify({"status": "success", "message": "Profile updated"})


# ── Account deletion (GDPR-style full wipe) ────────────────────

@app.route("/api/settings/account", methods=["DELETE"])
def api_settings_delete_account():
    user_id, error = _require_login()
    if error:
        return error
    auth = _current_auth()
    email = str(auth.get("email") or "").strip()

    # Remove the auth record from the users store.
    with _users_lock:
        users = _load_users()
        if email in users:
            users.pop(email, None)
            _save_users(users)

    # Drop the user's settings, sessions and long-term memory directories.
    try:
        import shutil
        for target in (
            _SETTINGS_DIR / _safe_user_id(user_id),
            PROJECT_ROOT / "memory_data" / "users" / str(user_id),
        ):
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
    except Exception as exc:
        print(f"[ERROR] Failed to remove user data on delete: {exc}")

    # Remove stored bearer tokens + chat/session records for this user.
    coll = auth_tokens_collection()
    if coll is not None:
        try:
            coll.delete_many({"user_id": user_id})
        except Exception as exc:
            print(f"[ERROR] Failed to purge auth tokens: {exc}")
    chats = chats_collection()
    if chats is not None:
        try:
            chats.delete_many({"user_id": user_id})
        except Exception as exc:
            print(f"[ERROR] Failed to purge chat records: {exc}")

    session.clear()
    return jsonify({"status": "success", "message": "Your account has been deleted"})


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
    """Per-tier caps (env-overridable). Paid video count is the creator-set ACTIVE
    limit (see _video_active_limit), which is always clamped to the authoritative
    VIDEO_GENERATION_LIMIT env maximum. Display + gate share this one source."""
    def _i(name, default):
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default
    return {
        "free": {"images": _i("FREE_IMAGE_LIMIT", 30), "videos": _i("FREE_VIDEO_LIMIT", 5)},
        "paid": {"images": _i("PAID_IMAGE_LIMIT", 1000), "videos": _video_active_limit()},
    }


def _video_env_max() -> int:
    """Authoritative maximum for a paid user's video generations. The Render env
    var VIDEO_GENERATION_LIMIT is the ceiling — the creator can only lower the
    active limit, never raise it above this. Defaults to 200."""
    try:
        return max(1, int(os.getenv("VIDEO_GENERATION_LIMIT", "200") or 200))
    except (TypeError, ValueError):
        return 200


def _video_active_limit() -> int:
    """The creator-set ACTIVE video limit (50/100/200). Persisted in the app_config
    doc so it takes effect immediately without a deploy, and clamped to the env max.
    Falls back to the env maximum when unset or the DB is unreachable."""
    ceiling = _video_env_max()
    try:
        override = int(os.getenv("_VIDEO_ACTIVE_LIMIT_OVERRIDE", "") or 0)
        if override > 0:
            return max(1, min(override, ceiling))
    except (TypeError, ValueError):
        pass
    try:
        coll = app_config_collection()
        if coll is not None:
            doc = coll.find_one({"_id": "video_config"})
            if doc and doc.get("active_limit") is not None:
                return max(1, min(int(doc["active_limit"]), ceiling))
    except Exception as exc:
        print(f"[VIDEO] active limit read failed: {exc}")
    return ceiling


def _video_limit_for(user_id: str) -> int:
    """Per-user video limit. Normal paid users get the creator-set ACTIVE limit;
    the creator's own testing always uses the full env maximum so testing quota is
    never shared with (or capped by the settings meant for) normal users."""
    email = _current_auth().get("email", "")
    if _is_creator(email):
        return _video_env_max()
    return _video_active_limit()


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
    # Developer Mode is creator-only, enforced server-side: routing internals
    # (provider names, health, metrics) must never reach normal users.
    if not _is_creator(_current_auth().get("email", "")):
        return jsonify({"status": "error", "message": "Not authorized"}), 403
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
    # Developer Mode is creator-only, enforced server-side (see provider-status).
    if not _is_creator(_current_auth().get("email", "")):
        return jsonify({"status": "error", "message": "Not authorized"}), 403
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


def _studio_assemble(user_id: str, clips: list, media=None) -> dict:
    """Join studio clips into one trailer. Thin wrapper over the ONE assembly
    path in ``core.studio_jobs`` so the synchronous fallback, the async job
    finalize, and uploaded-footage all go through the exact same join (R2 fetch →
    hard-cut concat → store), with the same guarantees and no divergent copy."""
    import core.studio_jobs as sj
    return sj.assemble_sources(user_id, clips, cards=None)


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


def _studio_async_enabled() -> bool:
    """Async video job path (default on). Turn off to use the synchronous
    fallback: STUDIO_ASYNC_VIDEO=0."""
    return os.getenv("STUDIO_ASYNC_VIDEO", "1").strip().lower() not in ("0", "false", "no", "off")


def _studio_clips_sync(user_id, clip_scenes, frame_sources, scenes, saved, media, studio, fold_notes):
    """Original blocking clip path, kept as a fallback. Yields SSE strings."""
    import core.video_i2v as i2v
    yield f"data: {json.dumps({'stage': 'clips', 'status': 'working', 'total': len(clip_scenes)})}\n\n"
    for scene in clip_scenes:
        n = scene["number"]
        motion = studio.clip_prompt(scene, notes=fold_notes())
        out = i2v.generate_clip(motion, frame_sources[n], tag=str(n))
        if out.get("error"):
            print(f"[STUDIO] clip for scene {n} failed: {out['error']}")
            yield f"data: {json.dumps({'stage': 'clips', 'clip_failed': n})}\n\n"
            continue
        rec = media.save_video(out["video_url"], prompt=f"Scene {n}: {scene['title']}",
                               provider="AlibabaI2V", chat_id=f"studio_{user_id}")
        stored_clip = rec["local_path"] if rec else ""
        if not stored_clip:
            yield f"data: {json.dumps({'stage': 'clips', 'clip_failed': n})}\n\n"
            continue
        clip_evt = {"number": n, "title": scene["title"], "video_url": stored_clip}
        saved["clips"].append(clip_evt)
        _studio_save_run(user_id, saved)
        yield f"data: {json.dumps({'stage': 'clips', 'clip': clip_evt})}\n\n"
    yield f"data: {json.dumps({'stage': 'clips', 'status': 'done'})}\n\n"
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


def _analysis_limit(tier: str) -> int:
    """Per-user video-analysis allowance. Analysis is far cheaper than
    generation, so free users get a taste rather than a hard block — but it is
    never unlimited, and the global budget cap still applies on top."""
    try:
        return int(os.getenv("PAID_ANALYSIS_LIMIT", "200") if tier == "paid"
                   else os.getenv("FREE_ANALYSIS_LIMIT", "5"))
    except (TypeError, ValueError):
        return 200 if tier == "paid" else 5


def _get_analysis_count(user_id: str) -> int:
    coll = usage_collection()
    if coll is None:
        return 0
    try:
        return int((coll.find_one({"_id": user_id}) or {}).get("analyses", 0))
    except Exception:
        return 0


@app.route("/api/video/analyze", methods=["POST"])
def api_video_analyze():
    """Watch an uploaded video with Qwen3-VL and describe it.

    Used by both the chat composer and the Studio (where the description seeds
    a production). Respects the same global video budget cap as generation.
    """
    user_id, error = _require_login()
    if error:
        return error

    import core.studio_jobs as sj
    import core.video_vision as vv

    if not vv.available():
        return jsonify({"status": "error",
                        "message": "Video analysis isn't available right now."}), 503

    upload = request.files.get("video")
    if not upload or not upload.filename:
        return jsonify({"status": "error", "message": "No video uploaded."}), 400

    # Per-user allowance + the shared hard budget cap.
    tier = _get_user_tier(user_id)
    used = _get_analysis_count(user_id)
    limit = _analysis_limit(tier)
    if used >= limit:
        return jsonify({"status": "error",
                        "message": f"You've used all {limit} video analyses on your plan."}), 403
    if sj.remaining_budget() <= 0:
        return jsonify({"status": "error",
                        "message": "The video budget is exhausted."}), 402

    question = (request.form.get("question") or "").strip()
    context = (request.form.get("context") or "chat").strip()

    import tempfile
    suffix = os.path.splitext(upload.filename)[1][:8] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    try:
        upload.save(tmp_path)
        size_mb = os.path.getsize(tmp_path) / 1024 / 1024
        if size_mb > _max_upload_mb:
            return jsonify({"status": "error",
                            "message": f"That video is {size_mb:.0f}MB — the limit is {_max_upload_mb:.0f}MB."}), 413

        if context == "studio":
            prompt = (question or
                      "Describe this footage for a film crew about to build a piece from it: "
                      "who or what is in it, what happens, the setting, and the visual style. "
                      "Be concrete and specific.")
            system = ("You are a film crew's assistant describing source footage the director "
                      "will work from. Be concrete and visual. Under 150 words.")
        else:
            prompt = question or "Watch this video and tell me what you see."
            system = ("You describe videos clearly and specifically for the person who uploaded "
                      "them. Say what actually happens. Under 180 words.")

        result = vv.analyze_video(tmp_path, prompt, system=system)
        if result.get("error"):
            return jsonify({"status": "error", "message": result["error"]}), 502

        # Charge the VL tokens against the same cap that governs generation.
        tokens = int(result.get("tokens", 0) or 0)
        if tokens:
            sj.add_spend(round((tokens / 1000.0) * sj.vl_cost_per_1k(), 6))
        coll = usage_collection()
        if coll is not None:
            try:
                coll.update_one({"_id": user_id}, {"$inc": {"analyses": 1}}, upsert=True)
            except Exception as exc:
                print(f"[ANALYZE] usage bump failed: {exc}")

        # Keep the upload in the user's library so it can be replayed/reused.
        stored = ""
        if str(request.form.get("save", "1")).lower() not in ("0", "false", "no"):
            try:
                rec = get_media_manager(user_id).save_video(
                    tmp_path, prompt=(question or upload.filename)[:200],
                    provider="Upload", chat_id=f"upload_{user_id}")
                stored = rec["local_path"] if rec else ""
            except Exception as exc:
                print(f"[ANALYZE] could not store upload: {exc}")

        return jsonify({
            "status": "success",
            "description": result.get("text", ""),
            "video_url": stored,
            "tokens": tokens,
            "analyses_used": used + 1,
            "analyses_limit": limit,
            "remaining_budget_usd": sj.remaining_budget(),
        })
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@app.route("/api/studio/job/<job_id>", methods=["GET"])
def api_studio_job(job_id):
    """Poll a background video job. Also nudges a stalled job back to life so a
    run survives the browser (and even the server process) going away."""
    user_id, error = _require_login()
    if error:
        return error
    import core.studio_jobs as sj
    job = sj.get_job(job_id)
    if not job or job.get("user_id") != user_id:
        return jsonify({"status": "error", "message": "job not found"}), 404
    sj.maybe_resume(job_id)
    return jsonify({"status": "success", "job": sj.public_view(job)})


@app.route("/api/studio/job/<job_id>/regenerate/<int:scene_number>", methods=["POST"])
def api_studio_regenerate_clip(job_id, scene_number):
    """Re-shoot ONE scene — used when Marcus flags a clip as not matching."""
    user_id, error = _require_login()
    if error:
        return error
    import core.studio_jobs as sj
    job = sj.get_job(job_id)
    if not job or job.get("user_id") != user_id:
        return jsonify({"status": "error", "message": "job not found"}), 404

    if not job.get("test_mode"):
        tier = _get_user_tier(user_id)
        _, videos_used = _get_usage_counts(user_id)
        ok, reason = sj.video_access(tier, videos_used, _video_limit_for(user_id))
        if not ok:
            return jsonify({"status": "error", "message": reason}), 403
        afford, est, remaining = sj.can_afford(1)
        if not afford:
            return jsonify({"status": "error",
                            "message": f"Not enough budget (${remaining:.2f} left)."}), 402

    target = next((c for c in job["clips"] if c["number"] == scene_number), None)
    if not target:
        return jsonify({"status": "error", "message": "scene not in this job"}), 404

    # Reset just this clip and let the driver pick it up again.
    target.update({"status": sj.PENDING, "task_id": "", "video_url": "",
                   "error": "", "review": None})
    job["status"] = "running"
    job["final_video"] = ""      # the cut must be rebuilt around the new shot
    job["cut_review"] = None
    job["submission_capped"] = False
    sj.save_job(job)
    sj.launch(job_id)
    return jsonify({"status": "success", "job": sj.public_view(sj.get_job(job_id))})


@app.route("/api/studio/job/<job_id>/assemble", methods=["POST"])
def api_studio_retry_assembly(job_id):
    """Re-run just the join. Clips already exist and cost nothing to reuse, so
    this is free — it exists because assembly is the one step that can die
    after all the expensive work succeeded."""
    user_id, error = _require_login()
    if error:
        return error
    import core.studio_jobs as sj
    job = sj.get_job(job_id)
    if not job or job.get("user_id") != user_id:
        return jsonify({"status": "error", "message": "job not found"}), 404
    done = [c for c in job.get("clips", []) if c.get("status") == sj.DONE and c.get("video_url")]
    if len(done) < 2:
        return jsonify({"status": "error",
                        "message": "Not enough finished clips to join."}), 400
    # Re-open the job so the driver finalises it again.
    job["status"] = "running"
    job["error"] = ""
    job["assembling_at"] = None                      # drop any stale finalize claim
    job["assembling_owner"] = None
    job["heartbeat"] = "1970-01-01T00:00:00+00:00"   # force resume
    sj.save_job(job)
    sj.launch(job_id)
    return jsonify({"status": "success", "job": sj.public_view(sj.get_job(job_id))})


@app.route("/api/studio/assemble-uploads", methods=["POST"])
def api_studio_assemble_uploads():
    """Join footage the USER uploaded into one finished video.

    Same engine as a generated trailer: each upload is stored to R2, then a job
    whose clips are already finished runs the ONE shared assembly path — so an
    upload run gets the same resume/notify guarantees and lands in the Studio and
    the Video Gallery. Assembly is free (nothing is generated)."""
    user_id, error = _require_login()
    if error:
        return error
    import core.studio_jobs as sj

    files = [f for f in (request.files.getlist("clips")
                         or request.files.getlist("videos"))
             if f and f.filename]
    if len(files) < 2:
        return jsonify({"status": "error",
                        "message": "Upload at least two video clips to join."}), 400
    if len(files) > sj.max_clips_cap():
        return jsonify({"status": "error",
                        "message": f"Too many clips (max {sj.max_clips_cap()})."}), 400

    import tempfile
    media = get_media_manager(user_id)
    stored = []
    for idx, upload in enumerate(files, start=1):
        suffix = os.path.splitext(upload.filename)[1][:8] or ".mp4"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        try:
            upload.save(tmp_path)
            size_mb = os.path.getsize(tmp_path) / 1024 / 1024
            if size_mb > _max_upload_mb:
                return jsonify({"status": "error",
                                "message": f"{upload.filename} is {size_mb:.0f}MB — "
                                           f"the limit is {_max_upload_mb:.0f}MB."}), 413
            rec = media.save_video(tmp_path, prompt=(upload.filename or f"Clip {idx}")[:200],
                                   provider="Upload", chat_id=f"studio_upload_{user_id}")
            if not rec:
                return jsonify({"status": "error",
                                "message": f"Couldn't store {upload.filename}."}), 502
            stored.append({"video_url": rec["local_path"],
                           "title": os.path.splitext(upload.filename)[0][:60]})
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if len(stored) < 2:
        return jsonify({"status": "error",
                        "message": "Need at least two valid clips to join."}), 400

    job = sj.new_upload_job(user_id, stored)
    sj.launch(job["_id"])
    return jsonify({"status": "success", "job": sj.public_view(sj.get_job(job["_id"]))})


def _edit_enabled() -> bool:
    return os.getenv("EDIT_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


@app.route("/api/editing/transcribe", methods=["POST"])
def api_editing_transcribe():
    """Transcribe a raw voice-note (or any audio/video upload) into text for a
    Massive Editing instruction. Reuses the same Groq Whisper pipeline the edit
    itself uses, so voice instructions carry the SAME weight as typed ones."""
    user_id, error = _require_login()
    if error:
        return error
    if not _edit_enabled():
        return jsonify({"status": "error", "message": "Editing is temporarily unavailable."}), 503
    import core.transcription as tr
    upload = request.files.get("audio")
    if not upload or not upload.filename:
        return jsonify({"status": "error", "message": "Attach a voice note to transcribe."}), 400
    import tempfile
    suffix = os.path.splitext(upload.filename)[1][:8] or ".m4a"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    try:
        upload.save(tmp_path)
        res = tr.transcribe(tmp_path, already_audio=True, timeout=120)
        if res.get("error"):
            return jsonify({"status": "error", "message": "Transcription failed: " + res["error"]}), 502
        return jsonify({"status": "success",
                        "text": res.get("text", "").strip(),
                        "words": res.get("words", [])})
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Transcription error: {exc}"}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _edit_collect_uploads(user_id: str) -> tuple[list, dict, str]:
    """Store every file attached to /api/editing/run (``media`` + legacy
    ``video`` + ``voice_note``) via the media manager. Returns:
      (media_assets, primary_video_rec, error_str)
    ``media_assets`` = one entry per stored non-primary asset
      {"type": "video"|"image"|"audio", "url": local_path, "name": filename}
    ``primary_video_rec`` = the media record chosen as the edit source.
    """
    mm = get_media_manager(user_id)
    files = list(request.files.getlist("media"))
    legacy = request.files.get("video")
    if legacy and legacy.filename:
        files.append(legacy)
    if not files:
        return [], {}, "Attach at least one video to edit."

    media_assets: list = []
    primary = None
    for f in files:
        if not f or not f.filename:
            continue
        kind = _asset_kind(f.mimetype or "", f.filename)
        import tempfile
        suffix = os.path.splitext(f.filename)[1][:8] or ".bin"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        try:
            f.save(tmp_path)
            size_mb = os.path.getsize(tmp_path) / 1024 / 1024
            if size_mb > _max_upload_mb:
                return [], {}, f"{f.filename} is {size_mb:.0f}MB — the limit is {_max_upload_mb:.0f}MB."
            name = (f.filename or "asset")[:200]
            if not kind:
                return [], {}, f"{f.filename} isn't a supported video/image/audio file."
            if kind == "video":
                rec = mm.save_video(tmp_path, prompt=name, provider="EditUpload",
                                    chat_id=f"edit_{user_id}")
                if primary is None:
                    primary = rec
                elif rec:
                    media_assets.append({"type": "video", "url": rec["local_path"], "name": name})
            elif kind == "image":
                rec = mm.save_image(tmp_path, prompt=name, provider="EditUpload",
                                    chat_id=f"edit_{user_id}")
                if rec:
                    media_assets.append({"type": "image", "url": rec["local_path"], "name": name})
            elif kind == "audio":
                rec = mm.save_media(tmp_path, media_type="audio", prompt=name,
                                    provider="EditUpload", chat_id=f"edit_{user_id}",
                                    library="edit", category="music")
                if rec:
                    media_assets.append({"type": "audio", "url": rec["local_path"], "name": name})
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return media_assets, primary or {}, ""


@app.route("/api/editing/run", methods=["POST"])
def api_editing_run():
    """Massive Editing (instruction-driven): accept raw footage + the user's
    typed/voice instruction + optional media assets and a selected sticker, store
    them, and launch a background job that interprets the instruction into an
    editing plan and renders the edited video. Returns the job to poll via
    /api/studio/job/<id>.

    Form fields:
      instruction       — the primary text instruction ("what do you want me to do")
      voice_transcript  — text transcription of a recorded voice instruction
      voice_note        — the recorded voice-note audio itself (kept on the job)
      media[]           — one or more video/image/audio uploads
      video             — legacy single-video field (still supported)
      sticker_url       — a /static/... sticker asset to actually overlay
      sticker_name      — human label for that sticker
      test_mode         — "1" for a free dry run (no B-roll spend)

    No paid video-generation spend (Groq Whisper + a free B-roll provider +
    local ffmpeg). Nothing auto-starts on upload — the job launches here, on
    this explicit request."""
    user_id, error = _require_login()
    if error:
        return error
    if not _edit_enabled():
        return jsonify({"status": "error", "message": "Editing is temporarily unavailable."}), 503

    import core.studio_jobs as sj

    instruction = str(request.form.get("instruction", "")).strip()
    voice_transcript = str(request.form.get("voice_transcript", "")).strip()
    sticker_url = str(request.form.get("sticker_url", "")).strip()
    sticker_name = str(request.form.get("sticker_name", "")).strip()
    sticker_pos = str(request.form.get("sticker_pos", "")).strip().lower()
    if sticker_pos not in ("br", "bl", "tr", "tl", "center"):
        sticker_pos = "br"
    if sticker_url and not sticker_url.startswith("/static/"):
        sticker_url = ""

    media_assets, primary, msg = _edit_collect_uploads(user_id)
    if not primary:
        if not media_assets:
            return jsonify({"status": "error", "message": msg or "Upload a video to edit."}), 400
        return jsonify({"status": "error",
                        "message": "Attach a video clip to edit (images/audio alone need a video to be edited on)."}), 400

    # The instruction is the PRIMARY brief — require either typed or spoken text
    # unless nothing was attached yet, so "Start AI Edit" always sends intent.
    if not (instruction or voice_transcript or request.files.get("voice_note")):
        return jsonify({"status": "error",
                        "message": "Tell the AI what you want before editing — type or record an instruction."}), 400

    voice_note = ""
    vn = request.files.get("voice_note")
    if vn and vn.filename:
        import tempfile
        suffix = os.path.splitext(vn.filename)[1][:8] or ".webm"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        try:
            vn.save(tmp_path)
            vrec = get_media_manager(user_id).save_media(
                tmp_path, media_type="audio", prompt="Voice instruction for an edit",
                provider="EditVoice", chat_id=f"edit_{user_id}", library="edit",
                category="reaction")
            if vrec:
                voice_note = vrec["local_path"]
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    test_mode = str(request.form.get("test_mode", "")).strip().lower() in ("1", "true", "yes", "on")
    job = sj.new_autoedit_job(
        user_id, primary["local_path"], test_mode=test_mode,
        instruction=instruction, voice_transcript=voice_transcript,
        voice_note=voice_note, media_assets=media_assets,
        sticker_source=sticker_url, sticker_name=sticker_name,
        sticker_pos=sticker_pos)
    sj.launch(job["_id"])
    return jsonify({"status": "success", "job": sj.public_view(sj.get_job(job["_id"]))})


@app.route("/api/editing/refine", methods=["POST"])
def api_editing_refine():
    """Refine a previous Massive Edit with manual sidebar overrides.

    Accepts the previous job_id plus a JSON ``manual`` payload (trim, speed,
    rotate, crop, canvas, captions, effects, music, sticker, etc.) and launches
    a new autoedit job that replays the AI's edit plan with the manual
    overrides applied on top.  The user can also send a new instruction to
    modify the AI plan itself.

    JSON body:
      job_id     — the previous autoedit job to refine
      manual     — dict of manual overrides (see _normalize_manual in video_edit)
      instruction — optional new instruction (changes the AI plan)
      keep_plan  — if true (default), replay the previous AI plan unchanged
    """
    user_id, error = _require_login()
    if error:
        return error
    if not _edit_enabled():
        return jsonify({"status": "error", "message": "Editing is temporarily unavailable."}), 503

    import core.studio_jobs as sj
    data = request.get_json(silent=True) or {}
    prev_job_id = str(data.get("job_id", "")).strip()
    if not prev_job_id:
        return jsonify({"status": "error", "message": "No previous job to refine."}), 400

    prev_job = sj.get_job(prev_job_id)
    if not prev_job or prev_job.get("user_id") != user_id:
        return jsonify({"status": "error", "message": "Previous edit not found."}), 404
    if not prev_job.get("final_video"):
        return jsonify({"status": "error", "message": "Previous edit hasn't finished yet."}), 400

    manual = data.get("manual") or {}
    instruction = str(data.get("instruction", "")).strip()
    keep_plan = bool(data.get("keep_plan", True))
    prev_plan_steps = prev_job.get("edit_plan") or []
    prev_plan = {
        "steps": prev_plan_steps,
        "captions": True,
        "music": bool(prev_job.get("stats", {}).get("music")),
        "broll": {"use_uploads": []},
        "slow_motion": {"start": None, "end": None, "factor": 2.0},
        "sticker_windows": [],
    }

    new_job = sj.new_autoedit_job(
        user_id, prev_job["source_video"],
        test_mode=bool(prev_job.get("test_mode")),
        instruction=instruction or prev_job.get("instruction", ""),
        voice_transcript=prev_job.get("voice_transcript", ""),
        media_assets=prev_job.get("media_assets") or [],
        sticker_source=prev_job.get("sticker_source", ""),
        sticker_name=prev_job.get("sticker_name", ""),
        sticker_pos=prev_job.get("sticker_pos", "br"),
        manual=manual,
        keep_plan=keep_plan,
        prev_plan=prev_plan)
    sj.launch(new_job["_id"])
    return jsonify({"status": "success", "job": sj.public_view(sj.get_job(new_job["_id"]))})


# ── Editing asset library (user's own funny sounds / music / reactions) ──────
_ASSET_CATEGORIES = {"sfx", "music", "reaction", "sticker", "gif"}


@app.route("/api/editing/stickers", methods=["GET"])
def api_editing_stickers():
    """List the BUILT-IN sticker / GIF library bundled in static/assets/. These
    are global (available to everyone) and served as ordinary static files."""
    user_id, error = _require_login()
    if error:
        return error
    kind = str(request.args.get("type", "stickers")).strip().lower()
    if kind not in ("stickers", "gifs"):
        kind = "stickers"
    from core.config import PROJECT_ROOT as _ROOT
    base = _ROOT / "static" / "assets" / kind
    label = "sticker" if kind == "stickers" else "gif"
    items = []
    try:
        manifest = base / "manifest.json"
        if manifest.exists():
            for e in json.loads(manifest.read_text(encoding="utf-8-sig")):  # tolerate BOM
                f = e.get("file")
                if f and (base / f).exists():
                    items.append({"name": e.get("name") or f, "kind": label,
                                  "url": f"/static/assets/{kind}/{f}"})
        elif base.exists():
            for p in sorted(base.iterdir()):
                if p.suffix.lower() in (".png", ".webp", ".gif"):
                    items.append({"name": p.stem, "kind": label,
                                  "url": f"/static/assets/{kind}/{p.name}"})
    except Exception as exc:
        print(f"[STICKERS] list failed: {exc}")
    return jsonify({"status": "success", "stickers": items})


def _asset_kind(mimetype: str, filename: str) -> str:
    mt = (mimetype or "").lower()
    if mt.startswith("audio/"):
        return "audio"
    if mt.startswith("video/"):
        return "video"
    if mt.startswith("image/"):
        return "image"
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    if ext in ("mp3", "wav", "ogg", "m4a", "aac", "weba"):
        return "audio"
    if ext in ("mp4", "webm", "mov", "m4v"):
        return "video"
    if ext in ("png", "jpg", "jpeg", "gif", "webp"):
        return "image"
    return ""


@app.route("/api/editing/assets", methods=["GET"])
def api_editing_assets_list():
    """List the user's editing-library assets (their own SFX / music / reaction
    clips), optionally filtered by category."""
    user_id, error = _require_login()
    if error:
        return error
    category = str(request.args.get("category", "")).strip().lower()
    if category not in _ASSET_CATEGORIES:
        category = ""
    items = get_media_manager(user_id).list_assets(category=category)
    return jsonify({"status": "success", "assets": items})


@app.route("/api/editing/assets", methods=["POST"])
def api_editing_assets_upload():
    """Upload one or more editing assets (funny sounds, music, or reaction
    photos/clips) into the user's own library — used later to auto-insert at
    fitting moments. Their own files, so no music-licensing cost."""
    user_id, error = _require_login()
    if error:
        return error
    if not _edit_enabled():
        return jsonify({"status": "error", "message": "Editing is temporarily unavailable."}), 503
    category = str(request.form.get("category", "sfx")).strip().lower()
    if category not in _ASSET_CATEGORIES:
        category = "sfx"
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"status": "error", "message": "No files uploaded."}), 400

    import tempfile
    media = get_media_manager(user_id)
    saved, errors = [], []
    for up in files:
        kind = _asset_kind(getattr(up, "mimetype", ""), up.filename)
        if not kind:
            errors.append(f"{up.filename}: unsupported file type")
            continue
        suffix = os.path.splitext(up.filename)[1][:8] or ""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        try:
            up.save(tmp_path)
            size_mb = os.path.getsize(tmp_path) / 1024 / 1024
            if size_mb > _max_upload_mb:
                errors.append(f"{up.filename}: {size_mb:.0f}MB over the {_max_upload_mb:.0f}MB limit")
                continue
            rec = media.save_asset(tmp_path, kind=kind, category=category,
                                   name=os.path.splitext(up.filename)[0][:80])
            if rec:
                saved.append(rec)
            else:
                errors.append(f"{up.filename}: could not store")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return jsonify({"status": "success", "saved": len(saved), "assets": saved, "errors": errors})


@app.route("/api/editing/assets/<media_id>/delete", methods=["POST"])
def api_editing_assets_delete(media_id):
    user_id, error = _require_login()
    if error:
        return error
    ok = get_media_manager(user_id).delete_media(media_id)
    return jsonify({"status": "success" if ok else "error"})


@app.route("/api/editing/apply-sticker", methods=["POST"])
def api_editing_apply_sticker():
    """Apply a sticker/GIF to a video: store the uploaded clip, then run a
    background overlay job. Poll the result via /api/studio/job/<id>."""
    user_id, error = _require_login()
    if error:
        return error
    if not _edit_enabled():
        return jsonify({"status": "error", "message": "Editing is temporarily unavailable."}), 503
    import core.studio_jobs as sj

    sticker_url = str(request.form.get("sticker_url", "")).strip()
    position = str(request.form.get("position", "br")).strip().lower()
    if position not in ("br", "bl", "tr", "tl", "center"):
        position = "br"
    # SSRF guard: only in-app static assets (built-in stickers or the user's own
    # stored media) may be overlaid — never an arbitrary remote URL.
    if not sticker_url.startswith("/static/"):
        return jsonify({"status": "error", "message": "Invalid sticker."}), 400
    upload = request.files.get("video")
    if not upload or not upload.filename:
        return jsonify({"status": "error", "message": "Upload a video to sticker."}), 400

    import tempfile
    suffix = os.path.splitext(upload.filename)[1][:8] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    try:
        upload.save(tmp_path)
        size_mb = os.path.getsize(tmp_path) / 1024 / 1024
        if size_mb > _max_upload_mb:
            return jsonify({"status": "error",
                            "message": f"{upload.filename} is {size_mb:.0f}MB — the limit is "
                                       f"{_max_upload_mb:.0f}MB."}), 413
        rec = get_media_manager(user_id).save_video(
            tmp_path, prompt=(upload.filename or "clip")[:200],
            provider="StickerSource", chat_id=f"edit_{user_id}")
        if not rec:
            return jsonify({"status": "error", "message": "Couldn't store the upload."}), 502
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    job = sj.new_sticker_job(user_id, rec["local_path"], sticker_url, position=position)
    sj.launch(job["_id"])
    return jsonify({"status": "success", "job": sj.public_view(sj.get_job(job["_id"]))})


@app.route("/api/studio/estimate", methods=["GET"])
def api_studio_estimate():
    """Cost meter data for the Studio: estimated cost, remaining budget, and
    whether this user is even allowed to generate video."""
    user_id, error = _require_login()
    if error:
        return error
    import core.studio_jobs as sj
    # Either an explicit clip count, or a target runtime that derives one.
    try:
        duration = int(request.args.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        duration = 0
    try:
        clips = int(request.args.get("clips") or 0)
    except (TypeError, ValueError):
        clips = 0
    if not clips:
        clips = sj.scenes_for_duration(duration or sj.default_duration())
    clips = max(1, min(sj.max_clips_cap(), clips))
    tier = _get_user_tier(user_id)
    _, videos_used = _get_usage_counts(user_id)
    video_limit = _video_limit_for(user_id)
    access_ok, access_reason = sj.video_access(tier, videos_used, video_limit)
    est = sj.estimate_cost(clips)
    paths = sj.path_costs()
    return jsonify({
        "status": "success",
        "clips": clips,
        "video_mode_default": "t2v",
        "path_costs": paths,
        # What this run would cost on each path, so the saving is visible.
        "est_cost_t2v_usd": round(clips * paths["t2v_per_clip_usd"], 2),
        "est_cost_i2v_usd": round(clips * paths["i2v_per_clip_usd"], 2),
        "duration_seconds": duration or sj.default_duration(),
        "default_duration": sj.default_duration(),
        "seconds_per_scene": sj.seconds_per_scene(),
        "default_clips": sj.default_clips(),
        "test_clips": sj.test_clips(),
        "max_clips": sj.max_clips_cap(),
        "cost_per_clip_usd": sj.cost_per_clip(),
        "est_cost_usd": est,
        "remaining_budget_usd": sj.remaining_budget(),
        "budget_usd": sj.budget_usd(),
        "video_enabled": _video_generation_enabled(),
        "tier": tier,
        "video_access": access_ok,
        "access_reason": access_reason,
        "videos_used": videos_used,
        "video_limit": video_limit,
    })


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
    scenes -> text-to-video clips -> assembled trailer. The default path is
    text-to-video, which renders straight from each scene with NO storyboard
    image; storyboards are generated only for image-to-video (reference mode).
    Video generation stays gated by the global kill switch."""
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
    import core.studio_jobs as sj

    # Per-run length + test mode. Default is a short ~90s trailer, not 5 minutes.
    test_mode = bool(data.get("test_mode"))
    # Video path: text-to-video is the default (better prompt following, and no
    # paid storyboard image per clip). i2v is opt-in via reference mode.
    video_mode = str(data.get("video_mode") or "t2v").strip().lower()
    if video_mode not in ("t2v", "i2v"):
        video_mode = "t2v"
    reference_image = str(data.get("reference_image") or "").strip()
    if reference_image:
        video_mode = "i2v"  # an explicit reference always means animate that image
    # Generate-from-scratch makes ONE video (text-to-video from the scenes, or
    # image-to-video from a single user-supplied reference). Neither uses per-scene
    # storyboard images, so none are generated unless the caller explicitly asks
    # for a storyboard preview with storyboards=true. No env default turns it on.
    want_storyboards = bool(data.get("storyboards"))
    try:
        requested = int(data.get("clips") or 0)
    except (TypeError, ValueError):
        requested = 0
    # Scene count derives from target runtime (~1 scene per 5s of finished
    # video), so a 30s piece gets ~6 scenes rather than a slideshow.
    try:
        target_duration = int(data.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        target_duration = 0
    if not target_duration:
        target_duration = sj.default_duration()
    # wan2.7-t2v renders at most ~15s per request, so a longer piece is split into
    # several <=15s clips that the assembly engine joins into one continuous video.
    # plan_clips decides how many and how long each is: 10s -> one 10s clip (no
    # assembly); 40s -> four 10s clips joined.
    n_clips, per_clip_seconds = sj.plan_clips(target_duration)
    if requested > 0:
        n_clips = max(1, min(sj.max_clips_cap(), requested))
    target_clips = n_clips

    if run_id:
        with _studio_runs_lock:
            _studio_runs.setdefault(run_id, {"notes": [], "answer": None})

    def generate():
        notes_applied = []
        # Mirrors what the Studio shows, persisted so a reload restores it
        saved = {"idea": idea, "script": "", "sheet_text": "", "scenes": [], "frames": [],
                 "clips": [], "beats": [], "logline": ""}

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
                for token in _call_llm_cluster_stream(
                        studio.script_messages(idea, target=target_clips, duration=target_duration)):
                    if not token:
                        continue
                    script += token
                    yield f"data: {json.dumps({'stage': 'writing', 'token': token})}\n\n"
            except Exception as exc:
                print(f"[STUDIO] script generation failed: {exc}")
                yield f"data: {json.dumps({'error': 'Angelina could not finish the beat sheet. Please try again.'})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

            # Parse Angelina's beats — these drive the title cards later.
            beat_data = studio.parse_beats(script)
            saved["logline"] = beat_data.get("logline", "")
            saved["beats"] = beat_data.get("beats", [])
            yield f"data: {json.dumps({'stage': 'writing', 'beats': saved['beats'], 'logline': saved['logline']})}\n\n"

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
                    studio.scene_messages(idea, script, sheet_text, notes=fold_notes(),
                                          target=target_clips, duration=target_duration), timeout=90,
                )
                scenes = studio.normalize_scenes(
                    studio._parse_json_block(raw), target=target_clips,
                    allow_filmmaking=studio.idea_is_about_filmmaking(idea),
                )
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

            # ── Stage 3: storyboard frames (visual preview only) ────────
            # These are NOT the video source any more — text-to-video renders
            # straight from the scene. They stay as a preview, and become the
            # animation source only in i2v/reference mode.
            config = get_config()
            media = get_media_manager(user_id)
            # Keep the provider's own URL per scene: image-to-video needs a
            # source Alibaba can load, and its OSS URL is ideal.
            frame_sources = {}
            if not want_storyboards:
                yield f"data: {json.dumps({'stage': 'storyboard', 'status': 'skipped'})}\n\n"
            else:
                yield f"data: {json.dumps({'stage': 'storyboard', 'status': 'working', 'total': len(scenes)})}\n\n"
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

            # ── Stage 4: generate ONE video (generate-from-scratch) ─────
            # Generate-from-scratch asks Alibaba for a SINGLE video of the whole
            # piece and displays it as-is when it lands — NO chunking into clips,
            # NO title cards, and it never touches the assembly engine (that is
            # the separate upload-and-join feature). It runs as a background job
            # (budget-capped, resumable) so this request returns fast and the run
            # survives the browser closing. Gated by the global kill switch, then
            # server-side tier + budget checks.
            import core.video_i2v as i2v
            gen_mode = "i2v" if reference_image else "t2v"   # i2v only with a user image
            if not _video_generation_enabled():
                yield f"data: {json.dumps({'stage': 'clips', 'status': 'disabled', 'message': VIDEO_DISABLED_MESSAGE})}\n\n"
            elif not i2v.available():
                yield f"data: {json.dumps({'stage': 'clips', 'status': 'skipped'})}\n\n"
            else:
                # Tier gate (server-side): free tier gets no video; paid tier is
                # capped per period. Test mode spends nothing so it's exempt.
                gate_ok, gate_reason = True, ""
                if not test_mode:
                    tier = _get_user_tier(user_id)
                    _, videos_used = _get_usage_counts(user_id)
                    video_limit = _video_limit_for(user_id)
                    gate_ok, gate_reason = sj.video_access(tier, videos_used, video_limit)
                if not gate_ok:
                    yield f"data: {json.dumps({'stage': 'clips', 'status': 'denied', 'message': gate_reason})}\n\n"
                else:
                    # Budget is checked per clip (each clip is one paid generation).
                    afford, est, remaining = (True, 0.0, sj.remaining_budget()) if test_mode else sj.can_afford(n_clips)
                    if not afford:
                        msg = (f"This needs about ${est:.2f} but only ${remaining:.2f} of the "
                               f"video budget is left. Try Test Mode or a shorter video.")
                        yield f"data: {json.dumps({'stage': 'clips', 'status': 'budget', 'message': msg, 'est_cost_usd': est, 'remaining_budget_usd': remaining})}\n\n"
                    elif n_clips <= 1:
                        # Fits in one clip (<= the model's 15s cap): ONE real
                        # Alibaba generation, shown as-is — no assembly, no cards.
                        one_prompt = studio.single_video_prompt(
                            idea, script, scenes, sheet_text=sheet_text, look=look,
                            notes=fold_notes(), duration=per_clip_seconds)
                        job = sj.new_single_video_job(
                            user_id, prompt=one_prompt, duration=per_clip_seconds,
                            mode=gen_mode, image_ref=reference_image, test_mode=test_mode,
                            logline=saved.get("logline", ""),
                            title=(saved.get("logline") or "Your video"),
                            charge_usd=(0.0 if test_mode else sj.cost_per_clip()))
                        sj.launch(job["_id"])
                        cost = sj.public_view(job).get("cost", {})
                        yield f"data: {json.dumps({'stage': 'clips', 'status': 'queued', 'job_id': job['_id'], 'total': 1, 'single_video': True, 'test_mode': test_mode, 'video_mode': gen_mode, 'duration': per_clip_seconds, 'cost': cost})}\n\n"
                    else:
                        # Longer than one clip: generate N clips of per_clip_seconds
                        # each and join them with the assembly engine into ONE
                        # continuous video — NO title cards between shots (cards={}).
                        frame_sources = ({s["number"]: reference_image for s in scenes}
                                         if (reference_image and gen_mode == "i2v") else {})
                        job = sj.new_job(
                            user_id, scenes, frame_sources, target_clips=n_clips,
                            per_clip=per_clip_seconds, test_mode=test_mode,
                            notes=fold_notes(), mode=gen_mode, sheet_text=sheet_text,
                            look=look, cards={},  # clean continuous cut, no cards
                            logline=saved.get("logline", ""), beats=[])
                        sj.launch(job["_id"])
                        cost = sj.public_view(job).get("cost", {})
                        yield f"data: {json.dumps({'stage': 'clips', 'status': 'queued', 'job_id': job['_id'], 'total': len(job['clips']), 'single_video': False, 'clip_seconds': per_clip_seconds, 'test_mode': test_mode, 'video_mode': gen_mode, 'duration': target_duration, 'cost': cost})}\n\n"

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
    """Serve user media.

    Primary path: 302-redirect the browser to a short-lived R2 presigned URL, so
    the bytes stream straight from Cloudflare and Flask never loads the file into
    memory (loading whole GridFS videos into RAM here is what crashed Render).
    The bucket stays private; only the signed, expiring link is handed out.

    GridFS remains a fallback for any file not yet migrated to R2 (emptied once
    the batch migration completes); local disk is the last resort for dev.
    """
    from core import r2_storage

    filename = subpath.rsplit("/", 1)[-1]

    if r2_storage.available():
        key = r2_storage.key_for_subpath(user_id, subpath)
        try:
            if r2_storage.object_exists(key):
                return redirect(r2_storage.presigned_url(key, expires=3600), code=302)
        except Exception as exc:
            print(f"[MEDIA] R2 presign failed for {key}, falling back: {exc}")

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
    allowed_files = ["manifest.json", "sw.js", "phone-studio.html", "jpj.txt", "favicon.ico"]

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

    # Security scanners and crawlers probe for forged paths like index.php,
    # .git, .env or .sql. Returning the SPA shell for those creates soft 404s
    # that hurt SEO and can confuse crawlers, so reject them outright.
    _lower = path.lower()
    if any(_lower.endswith(ext) for ext in (".php", ".aspx", ".asp", ".jsp", ".cgi", ".pl", ".env", ".git", ".bak", ".sql", ".pem", ".sh")):
        return Response("Not Found", status=404)

    print(f"[TRACE STATIC] SPA fallback for: /{path}")
    return send_from_directory(str(PROJECT_ROOT), "index.html")


# ── SEO: robots.txt, sitemap.xml, server-rendered marketing pages ─────────
# Every public page is registered from the same core.seo.PUBLIC_PAGES registry
# that generates sitemap.xml, so routes and the sitemap can never drift out of
# sync. Adding a page = add one entry to PUBLIC_PAGES + one template.

@app.route("/robots.txt", methods=["GET"])
def seo_robots_txt():
    return Response(
        seo_robots_body(),
        mimetype="text/plain",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.route("/sitemap.xml", methods=["GET"])
def seo_sitemap_xml():
    return Response(
        seo_sitemap_body(),
        mimetype="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _make_page_view(page_key: str):
    def _view():
        return seo_render_page(page_key)
    _view.__name__ = f"page_{page_key}"
    return _view


for _seo_page in SEO_PAGE_REGISTRY:
    if _seo_page.get("template"):
        app.add_url_rule(
            _seo_page["path"],
            endpoint=f"seo_page_{_seo_page['key']}",
            view_func=_make_page_view(_seo_page["key"]),
        )

# Legacy/alternate URLs 301 to their canonical page (no duplicate content).
for _alias_path, _target_path in SEO_URL_ALIASES.items():
    app.add_url_rule(
        _alias_path,
        endpoint=f"seo_alias_{_target_path.strip('/')}",
        view_func=lambda _t=_target_path: redirect(_t, code=301),
    )


# ── AI Builder ──────────────────────────────────────────────────────────────
# Engine: the OpenCode API (OpenCode Zen). Every call is made server-side; the
# key is read from OPENCODE_API_KEY only and never reaches the browser. The
# frontend talks exclusively to these /api/ai-builder/* routes.

def _ai_builder_sse(events):
    """Wrap an event-dict generator as a Server-Sent Events response."""
    def _gen():
        for ev in events:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(
        stream_with_context(_gen()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _ai_builder_project(user_id: str, pid: str):
    proj = ai_builder.resolve_project(user_id, pid)
    if proj is None:
        return None
    return proj


@app.route("/api/ai-builder/status", methods=["GET"])
def ai_builder_status():
    user_id, error = _require_login()
    if error:
        return error
    # Developer Mode reveals the real provider model ids. It is gated to the
    # creator account so normal users can NEVER see provider names — the UI only
    # shows ValleyMind Builder branding.
    is_creator = _is_creator(_current_auth().get("email", ""))
    dev = is_creator and str(request.args.get("dev", "")).strip().lower() in ("1", "true", "yes")
    resp = {
        "status": "success",
        "configured": ai_builder.configured(),
        "is_creator": is_creator,
        "builders": ai_builder.builder_options(dev=dev) if ai_builder.configured() else [],
        "default_builder": ai_builder.DEFAULT_BUILDER_ID,
        "dev": dev,
    }
    if dev:
        resp["models"] = ai_builder.available_models()
        resp["unmapped_free"] = ai_builder.unmapped_free_models()
    return jsonify(resp)


@app.route("/api/ai-builder/clarify", methods=["POST"])
def ai_builder_clarify():
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify({"status": "error", "message": "A project description is required"}), 400
    project_type = str(data.get("project_type") or "website").strip()[:40]
    attachments, attach_err = ai_builder.validate_attachments(data.get("attachments"))
    if attach_err:
        return jsonify({"status": "error", "message": attach_err}), 400
    model = ai_builder.plan_model()
    if not ai_builder.configured():
        return jsonify({"status": "error", "message": "AI Builder is not configured on the server"}), 503
    return _ai_builder_sse(ai_builder.clarify_generator(model, message, project_type, attachments=attachments))


@app.route("/api/ai-builder/plan", methods=["POST"])
def ai_builder_plan():
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify({"status": "error", "message": "A project description is required"}), 400
    project_type = str(data.get("project_type") or "website").strip()[:40]
    answers = data.get("answers")
    attachments, attach_err = ai_builder.validate_attachments(data.get("attachments"))
    if attach_err:
        return jsonify({"status": "error", "message": attach_err}), 400
    model = ai_builder.plan_model()
    if not ai_builder.configured():
        return jsonify({"status": "error", "message": "AI Builder is not configured on the server"}), 503
    return _ai_builder_sse(ai_builder.plan_generator(model, message, project_type, answers, attachments=attachments))


@app.route("/api/ai-builder/build", methods=["POST"])
def ai_builder_build():
    user_id, error = _require_login()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return jsonify({"status": "error", "message": "An approved project spec is required"}), 400
    if not ai_builder.configured():
        return jsonify({"status": "error", "message": "AI Builder is not configured on the server"}), 503
    # The UI sends a branded builder id (e.g. "vmb5"); the build engine resolves
    # it and, on provider failure, fails over across the ranked tiers on its own.
    # A raw `model` override is honored only for the creator (Developer Mode).
    builder_id = str(data.get("builder") or "").strip()
    raw = str(data.get("model") or "").strip()
    is_creator = _is_creator(_current_auth().get("email", ""))
    override = raw if (is_creator and raw in set(ai_builder.available_models())) else None
    return _ai_builder_sse(
        ai_builder.build_generator(user_id, spec, builder_id=builder_id, model=override))


@app.route("/api/ai-builder/projects/<pid>/resume", methods=["POST"])
def ai_builder_resume(pid):
    """Resume a paused/interrupted build — regenerates only the missing files."""
    user_id, error = _require_login()
    if error:
        return error
    if not ai_builder.configured():
        return jsonify({"status": "error", "message": "AI Builder is not configured on the server"}), 503
    if ai_builder.resolve_project(user_id, pid) is None:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    data = request.get_json(silent=True) or {}
    builder_id = str(data.get("builder") or "").strip()
    return _ai_builder_sse(ai_builder.resume_generator(user_id, pid, builder_id=builder_id))


@app.route("/api/ai-builder/health", methods=["GET"])
def ai_builder_health():
    """Live Builder-model health (availability, success rate, latency, failovers).
    Creator-only — foundation for the Creator Dashboard + notifications."""
    user_id, error = _require_login()
    if error:
        return error
    if not _is_creator(_current_auth().get("email", "")):
        return jsonify({"status": "error", "message": "Not authorized"}), 403
    return jsonify({
        "status": "success",
        "health": ai_builder.health_snapshot(),
        "unmapped_free": ai_builder.unmapped_free_models(),
    })


@app.route("/api/creator/dashboard", methods=["GET"])
def creator_dashboard():
    """Creator-only analytics: live users, daily stats, AI usage, builder stats
    and system health. Every section degrades gracefully to null if unavailable."""
    user_id, error = _require_login()
    if error:
        return error
    if not _is_creator(_current_auth().get("email", "")):
        return jsonify({"status": "error", "message": "Not authorized"}), 403

    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    online_cut = now - timedelta(minutes=10)
    day_cut = now - timedelta(hours=24)

    def _count(coll, q):
        try:
            return coll.count_documents(q) if coll is not None else None
        except Exception:
            return None

    users, chats = users_collection(), chats_collection()

    # Live + user counts
    online_users = None
    active_24h = None
    try:
        if chats is not None:
            online_users = len(chats.distinct("user_id", {"last_activity": {"$gte": online_cut}}))
            active_24h = len(chats.distinct("user_id", {"last_activity": {"$gte": day_cut}}))
    except Exception:
        pass
    users_block = {
        "total": _count(users, {}),
        "new_today": _count(users, {"created_at": {"$gte": today}}),
        "active_24h": active_24h,
    }
    chats_block = {
        "total": _count(chats, {}),
        "today": _count(chats, {"created_at": {"$gte": today}}),
        "active_now": _count(chats, {"last_activity": {"$gte": online_cut}}),
    }

    # AI usage — summed across per-user usage docs
    usage_block = {"images": None, "videos": None, "analyses": None}
    try:
        uc = usage_collection()
        if uc is not None:
            agg = list(uc.aggregate([{"$group": {"_id": None,
                "images": {"$sum": "$images"}, "videos": {"$sum": "$videos"},
                "analyses": {"$sum": "$analyses"}}}]))
            if agg:
                usage_block = {k: int(agg[0].get(k, 0) or 0) for k in ("images", "videos", "analyses")}
    except Exception:
        pass

    # Builder stats + per-tier health
    builder_stats = ai_builder.build_stats_snapshot()
    builder_stats["active_sessions"] = ai_builder.active_builds()

    # System health (psutil optional; DB/R2/jobs best-effort)
    system = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        system.update({
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "memory_percent": vm.percent,
            "memory_used_mb": round(vm.used / 1e6),
            "memory_total_mb": round(vm.total / 1e6),
            "disk_percent": psutil.disk_usage(os.getcwd()).percent,
        })
    except Exception as exc:
        system["metrics"] = "unavailable (" + str(exc)[:50] + ")"
    try:
        db = get_db()
        if db is not None:
            db.command("ping")
            system["database"] = "up"
            try:
                st = db.command("dbStats")
                system["db_storage_mb"] = round((st.get("dataSize", 0) + st.get("indexSize", 0)) / 1e6, 1)
            except Exception:
                pass
        else:
            system["database"] = "ephemeral fallback (no MONGODB_URI)"
    except Exception:
        system["database"] = "down"
    try:
        from core import r2_storage
        system["storage_r2"] = "up" if r2_storage.available() else "unavailable"
    except Exception:
        system["storage_r2"] = "unknown"
    try:
        from core.db import studio_jobs_collection
        jc = studio_jobs_collection()
        if jc is not None:
            system["active_jobs"] = jc.count_documents(
                {"status": {"$in": ["running", "processing", "queued", "pending", "in_progress"]}})
    except Exception:
        pass

    return jsonify({
        "status": "success",
        "generated_at": now.isoformat(),
        "live": {
            "online_users": online_users,
            "active_builder_sessions": ai_builder.active_builds(),
            "active_chats": chats_block["active_now"],
        },
        "users": users_block,
        "chats": chats_block,
        "usage": usage_block,
        "builders": {
            "stats": builder_stats,
            "health": ai_builder.health_snapshot(),
            "unmapped_free": ai_builder.unmapped_free_models(),
        },
        "system": system,
    })


@app.route("/api/video/quota", methods=["GET"])
def video_quota_get():
    """Creator-only read of the ACTIVE video limit (what normal paid users get)
    plus the authoritative env maximum. Normal users get 403 — the quota control
    is a creator tool, and even the values are not exposed to them."""
    user_id, error = _require_login()
    if error:
        return error
    if not _is_creator(_current_auth().get("email", "")):
        return jsonify({"status": "error", "message": "Not authorized"}), 403
    return jsonify({
        "status": "success",
        "active_limit": _video_active_limit(),
        "env_max": _video_env_max(),
    })


@app.route("/api/video/quota", methods=["POST"])
def video_quota_set():
    """Creator-only: change the ACTIVE video limit (50/100/200). Persisted in the
    app_config doc and clamped to the VIDEO_GENERATION_LIMIT env max, so it takes
    effect immediately and never exceeds the Render-declared ceiling."""
    user_id, error = _require_login()
    if error:
        return error
    if not _is_creator(_current_auth().get("email", "")):
        return jsonify({"status": "error", "message": "Not authorized"}), 403
    data = request.get_json(silent=True) or {}
    try:
        new_limit = int(data.get("limit") or 0)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "limit must be a number"}), 400
    env_max = _video_env_max()
    if new_limit not in (50, 100, 200):
        return jsonify({"status": "error", "message": "limit must be 50, 100 or 200"}), 400
    if new_limit > env_max:
        return jsonify({
            "status": "error",
            "message": f"limit cannot exceed the environment maximum of {env_max}",
        }), 400
    try:
        coll = app_config_collection()
        if coll is not None:
            coll.update_one(
                {"_id": "video_config"},
                {"$set": {"active_limit": new_limit, "updated_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
        else:
            # No Mongo (ephemeral fallback): reflect the value in-process so it
            # still takes effect now; it resets on restart, exactly like the rest
            # of the ephemeral fallback tier.
            os.environ["_VIDEO_ACTIVE_LIMIT_OVERRIDE"] = str(new_limit)
    except Exception as exc:
        print(f"[VIDEO] active limit write failed: {exc}")
    return jsonify({
        "status": "success",
        "active_limit": _video_active_limit(),
        "env_max": env_max,
    })


@app.route("/api/ai-builder/projects", methods=["GET"])
def ai_builder_projects():
    user_id, error = _require_login()
    if error:
        return error
    return jsonify({"status": "success", "projects": ai_builder.list_user_projects(user_id)})


@app.route("/api/ai-builder/projects/<pid>", methods=["GET"])
def ai_builder_project_detail(pid):
    user_id, error = _require_login()
    if error:
        return error
    proj = _ai_builder_project(user_id, pid)
    if proj is None:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    return jsonify({
        "status": "success",
        "project": {
            "id": proj.name,
            "meta": ai_builder.project_meta(proj),
            "tree": ai_builder.build_tree(proj),
        },
    })


@app.route("/api/ai-builder/projects/<pid>/file", methods=["GET"])
def ai_builder_project_file(pid):
    user_id, error = _require_login()
    if error:
        return error
    proj = _ai_builder_project(user_id, pid)
    if proj is None:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    rel = request.args.get("path", "")
    try:
        target = ai_builder._safe_target(proj, rel)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid file path"}), 400
    if not target.is_file():
        return jsonify({"status": "error", "message": "File not found"}), 404
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Could not read file: {exc}"}), 500
    return jsonify({
        "status": "success",
        "path": target.relative_to(proj).as_posix(),
        "size": target.stat().st_size,
        "content": content,
    })


@app.route("/api/ai-builder/projects/<pid>/download", methods=["GET"])
def ai_builder_project_download(pid):
    user_id, error = _require_login()
    if error:
        return error
    proj = _ai_builder_project(user_id, pid)
    if proj is None:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    meta = ai_builder.project_meta(proj)
    name = re.sub(r"[^A-Za-z0-9_\-]+", "-", str(meta.get("name") or proj.name)).strip("-") or "project"
    try:
        payload = ai_builder.zip_project_bytes(proj)
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Could not zip project: {exc}"}), 500
    return Response(
        payload,
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}.zip"',
            "Content-Length": str(len(payload)),
        },
    )


@app.route("/api/ai-builder/projects/<pid>/preview/")
@app.route("/api/ai-builder/projects/<pid>/preview/<path:subpath>")
def ai_builder_project_preview(pid, subpath=""):
    """Serve generated static files so the project can be previewed in an iframe."""
    user_id, error = _require_login()
    if error:
        return error
    proj = _ai_builder_project(user_id, pid)
    if proj is None:
        return ("Project not found", 404)
    root = proj.resolve()
    target = (root / subpath).resolve() if subpath else root
    if not target.is_relative_to(root):
        return ("Not found", 404)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return ("Not found", 404)
    rel = target.relative_to(root).as_posix()
    return send_from_directory(str(root), rel)


@app.route("/api/ai-builder/projects/<pid>", methods=["DELETE"])
def ai_builder_project_delete(pid):
    user_id, error = _require_login()
    if error:
        return error
    proj = _ai_builder_project(user_id, pid)
    if proj is None:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    import shutil as _shutil
    try:
        _shutil.rmtree(str(proj), ignore_errors=True)
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Could not delete project: {exc}"}), 500
    return jsonify({"status": "success"})



# ── Template Library API ───────────────────────────────────────────────────

_template_render_locks: dict = {}
_template_render_locks_guard = Lock()


def _template_render_lock(pid: str) -> Lock:
    with _template_render_locks_guard:
        return _template_render_locks.setdefault(pid, Lock())


def _template_launch_render(user_id: str, pid: str) -> None:
    lock = _template_render_lock(pid)
    if not lock.acquire(blocking=False):
        return

    def _run():
        try:
            tr.render_project(user_id, pid)
        except Exception as exc:
            proj = tpllib.get_project(user_id, pid)
            if proj:
                proj["status"] = "failed"
                proj["error"] = str(exc)[:400]
                tpllib.save_project(user_id, pid, proj)
        finally:
            try:
                lock.release()
            except Exception:
                pass

    Thread(target=_run, daemon=True, name=f"tmpl-{pid[:8]}").start()


@app.route("/api/templates/categories", methods=["GET"])
def api_template_categories():
    user_id, error = _require_login()
    if error:
        return error
    return jsonify({"status": "success", "categories": tpllib.categories()})


@app.route("/api/templates", methods=["GET"])
def api_template_list():
    user_id, error = _require_login()
    if error:
        return error
    category = (request.args.get("category") or "").strip()
    search = (request.args.get("search") or "").strip().lower()
    sort = (request.args.get("sort") or "popular").strip()
    cards = tpllib.list_cards()
    if category and category != "All":
        cards = [c for c in cards if c.get("category") == category]
    if search:
        cards = [c for c in cards if search in str(c.get("name", "")).lower()
                 or search in str(c.get("description", "")).lower()
                 or search in str(c.get("category", "")).lower()]
    if sort == "newest":
        cards.sort(key=lambda c: c.get("id", ""), reverse=True)
    elif sort == "duration":
        cards.sort(key=lambda c: c.get("duration", 0))
    else:
        cards.sort(key=lambda c: c.get("popularity", 0), reverse=True)
    return jsonify({
        "status": "success",
        "count": len(cards),
        "categories": tpllib.categories(),
        "templates": cards,
    })


@app.route("/api/templates/projects", methods=["GET"])
def api_template_projects():
    user_id, error = _require_login()
    if error:
        return error
    return jsonify({"status": "success", "projects": tpllib.list_user_projects(user_id)})


@app.route("/api/templates/<tid>", methods=["GET"])
def api_template_detail(tid):
    user_id, error = _require_login()
    if error:
        return error
    t = tpllib.get_template(tid)
    if t is None:
        return jsonify({"status": "error", "message": "Template not found"}), 404
    view = tpllib.card_view(t)
    view["placeholders"] = t.get("placeholders") or []
    view["timeline"] = t.get("timeline") or []
    view["beat_markers"] = t.get("beat_markers") or []
    view["music"] = t.get("music") or {}
    view["liked"] = user_id in tpllib._stat_for(tid).get("liked_by", [])
    return jsonify({"status": "success", "template": view})


@app.route("/api/templates/<tid>/like", methods=["POST"])
def api_template_like(tid):
    user_id, error = _require_login()
    if error:
        return error
    result = tpllib.toggle_like(tid, user_id)
    if result["count"] == 0:
        return jsonify({"status": "error", "message": "Template not found"}), 404
    return jsonify({"status": "success", **result})


@app.route("/api/templates/<tid>/download", methods=["POST"])
def api_template_download_count(tid):
    user_id, error = _require_login()
    if error:
        return error
    count = tpllib.add_download(tid)
    return jsonify({"status": "success", "count": count})


@app.route("/api/templates/<tid>/use", methods=["POST"])
def api_template_use(tid):
    """Create a per-user project from a template; returns the placeholder form."""
    user_id, error = _require_login()
    if error:
        return error
    project, err = tpllib.create_project(user_id, tid)
    if err:
        return jsonify({"status": "error", "message": err}), 404
    t = tpllib.get_template(tid)
    form = []
    for p in (t.get("placeholders") or []):
        form.append({
            "key": p.get("key", ""),
            "label": p.get("label", p.get("key", "")),
            "type": p.get("type", "text"),
            "required": bool(p.get("required", False)),
            "max_chars": p.get("max_chars", 0),
            "default": p.get("default_value", ""),
            "hint": p.get("hint", ""),
        })
    return jsonify({
        "status": "success",
        "project_id": project["_pid"],
        "name": project.get("name"),
        "aspect_ratio": project.get("aspect_ratio"),
        "duration": t.get("duration"),
        "media_required": t.get("media_required"),
        "form": form,
    })


@app.route("/api/templates/projects/<pid>", methods=["GET"])
def api_template_project(pid):
    user_id, error = _require_login()
    if error:
        return error
    proj = tpllib.get_project(user_id, pid)
    if proj is None:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    return jsonify({"status": "success", "project": tpllib.project_public(user_id, proj)})


@app.route("/api/templates/projects/<pid>/render", methods=["POST"])
def api_template_render(pid):
    """Save placeholder text + uploaded media, then kick off a background render."""
    user_id, error = _require_login()
    if error:
        return error
    proj = tpllib.get_project(user_id, pid)
    if proj is None:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    if proj.get("status") == "rendering":
        return jsonify({"status": "success", "project": tpllib.project_public(user_id, proj)})

    template = tpllib.get_template(proj.get("template_id", ""))
    if template is None:
        return jsonify({"status": "error", "message": "Template not found"}), 404

    placeholders = proj.get("placeholders") or {}
    missing = []
    for p in template.get("placeholders") or []:
        key = p.get("key", "")
        ptype = p.get("type", "text")
        required = bool(p.get("required", False))
        entry = placeholders.get(key) or {}
        if ptype == "text":
            value = (request.form.get(key) or "").strip()
            if required and not value:
                missing.append(p.get("label") or key)
            entry["value"] = value
        else:
            upload = request.files.get(key)
            if upload and upload.filename:
                fname = tpllib.save_project_media(user_id, pid, key, upload)
                entry["file"] = fname
                entry["value"] = upload.filename[:120]
            elif required and not entry.get("file"):
                missing.append(p.get("label") or key)
        placeholders[key] = entry

    if missing:
        return jsonify({"status": "error",
                        "message": f"Missing required placeholders: {', '.join(missing)}"}), 400

    proj["placeholders"] = placeholders
    proj["status"] = "queued"
    proj["progress"] = 0.0
    proj["error"] = ""
    proj["log"] = ["Render queued"]
    tpllib.save_project(user_id, pid, proj)
    _template_launch_render(user_id, pid)
    return jsonify({"status": "success", "project": tpllib.project_public(user_id, proj)})


@app.route("/api/templates/projects/<pid>/media/<path:rel>", methods=["GET"])
def api_template_project_media(pid, rel):
    """Serve a project's uploaded media (safe path resolution)."""
    user_id, error = _require_login()
    if error:
        return error
    proj = tpllib.get_project(user_id, pid)
    if proj is None:
        return ("Project not found", 404)
    try:
        target = tpllib.media_path(user_id, pid, rel)
    except ValueError:
        return ("Not found", 404)
    if not target.is_file():
        return ("Not found", 404)
    return send_from_directory(str(target.parent), target.name)


@app.route("/api/templates/projects/<pid>", methods=["DELETE"])
def api_template_project_delete(pid):
    user_id, error = _require_login()
    if error:
        return error
    if not tpllib.delete_project(user_id, pid):
        return jsonify({"status": "error", "message": "Project not found"}), 404
    return jsonify({"status": "success"})



# ── Response middleware: security headers, browser caching, gzip ───────────
# All three run via Flask after_request hooks so every route benefits without
# touching per-route code.

@app.after_request
def _add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


_SEO_PAGE_PATHS = {p["path"] for p in SEO_PAGE_REGISTRY if p.get("template")}


@app.after_request
def _add_cache_headers(resp):
    # Flask 3 ships with SEND_FILE_MAX_AGE_DEFAULT=None, which makes file
    # responses fall back to Werkzeug's default `Cache-Control: no-cache`.
    # We override per URL class so the browser can cache aggressively where
    # it is safe (static assets / marketing pages) and never cache dynamic
    # JSON/SSE/API responses.
    path = request.path
    if path.startswith("/static/"):
        # Brand images, icons and static assets — safe for a day (Last-Modified
        # + ETag conditional requests still let browsers revalidate after).
        resp.headers["Cache-Control"] = "public, max-age=86400"
    elif path in ("/", "/robots.txt", "/sitemap.xml"):
        # App shell + SEO control files — short public cache.
        resp.headers["Cache-Control"] = "public, max-age=3600"
    elif path in _SEO_PAGE_PATHS:
        # Server-rendered marketing pages — short cache, still always fresh.
        resp.headers["Cache-Control"] = "public, max-age=600"
    else:
        # Every dynamic/API/SSE response must never be cached by proxies.
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# Compressible MIME families (kept conservative — never media/images).
_GZIP_MIME_PREFIXES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/ld+json",
    "image/svg+xml",
)
_GZIP_MIN_BYTES = 512

# Whitespace spanning a newline *between* HTML tags. Collapsing it is safe:
# text inside <script>/<style> elements is never between a `>` and `<`, so
# their content is untouched, and inline spacing between words is preserved
# (only newline-separated tag boundaries are joined).
_HTML_WHITESPACE = re.compile(r">\s*[\r\n]\s*<")


@app.after_request
def _compress_response(resp):
    # Minify + gzip in ONE handler. Flask 3 runs after_request hooks in
    # REVERSE registration order, so separate hooks cannot rely on ordering;
    # doing both steps here guarantees minify runs before compression.
    # Streaming responses (SSE chat, media) are passed through untouched.
    # The direct_passthrough check covers stream_with_context() responses;
    # the explicit event-stream check covers the same routes even though they
    # are built with a plain generator (get_data() would raise on those, but
    # we never want to risk buffering a live stream).
    if resp.direct_passthrough or resp.status_code not in (200, 201, 204):
        return resp
    if resp.headers.get("Content-Encoding"):
        return resp  # already compressed downstream — do not touch
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if content_type == "text/event-stream":
        return resp

    # Step 1 — minify server-rendered marketing HTML.
    if content_type.startswith("text/html") and request.path in _SEO_PAGE_PATHS:
        try:
            html = resp.get_data(as_text=True)
        except (RuntimeError, TypeError):
            return resp
        minified = _HTML_WHITESPACE.sub("><", html)
        if len(minified) < len(html):
            resp.set_data(minified.encode("utf-8"))

    # Step 2 — gzip compressible MIME families (never media/images).
    if not content_type.startswith(_GZIP_MIME_PREFIXES):
        return resp
    if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
        return resp
    try:
        body = resp.get_data()
    except (RuntimeError, TypeError):
        return resp
    if len(body) < _GZIP_MIN_BYTES:
        return resp
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
        gz.write(body)
    encoded = buf.getvalue()
    if len(encoded) >= len(body):
        return resp  # no savings — leave it uncompressed
    resp.set_data(encoded)
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(encoded))
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers.pop("ETag", None)
    return resp


# ── Background workers ──────────────────────────────────────────────────────
# Studio assembly watchdog: relaunches any video job whose driver died mid-join
# (a deploy, an OOM, the free instance sleeping) so the run reaches a terminal
# state and the trailer gets saved even if the user closed the browser. Runs
# once per process; safe under gunicorn (idempotent, duplicate launches no-op).
try:
    import core.studio_jobs as _sj_boot
    _sj_boot.start_watchdog()
except Exception as _exc:  # never let a watchdog hiccup stop the app from serving
    print(f"[BOOT] could not start studio assembly watchdog: {_exc}")


# ── Startup auth migration (runs once per process, idempotent) ──────────────
# Normalizes every account to the unified verification schema: missing/NULL
# email_verified becomes explicit False (never "verified by default"), legacy
# security-question fields are stripped, and the email field mirrors the
# record id. Verified accounts stay verified. Disable locally with
# AUTH_MIGRATION_ENABLED=false.
if os.getenv("AUTH_MIGRATION_ENABLED", "true").strip().lower() != "false":
    try:
        from core.auth_migration import normalize_user_records
        _mig = normalize_user_records(users_collection())
        if not _mig.get("skipped"):
            print(f"[AUTH-MIGRATE] startup result: {_mig}")
        else:
            print(f"[AUTH-MIGRATE] startup skipped: {_mig.get('skipped')}")
    except Exception as _exc:  # never block serving on a migration hiccup
        print(f"[AUTH-MIGRATE] startup failed (non-fatal): {_exc}")


# ── Startup email diagnostic (runs once per process) ────────────────────────
# Reports whether the Resend transactional/promotional pipeline is configured.
# Offline on purpose (no network call at boot) and never logs secrets — only
# presence/absence flags and the configured sender identity.
def _email_startup_check():
    try:
        from core import email_service
        c = email_service._cfg()
        ok = email_service.available()
        print(f"[MAIL DEBUG] startup check: provider=resend available={ok}")
        print(f"[MAIL DEBUG] startup check: api_key={'set' if c['api_key'] else 'MISSING'} "
              f"sender={c['sender'] or 'MISSING'} reply_to={'set' if c['reply_to'] else 'unset'}")
        if not ok:
            print("[MAIL DEBUG] startup check: EMAIL NOT CONFIGURED — set RESEND_API_KEY and EMAIL_FROM")
    except Exception as exc:
        print(f"[MAIL DEBUG] startup check failed: {type(exc).__name__}: {exc}")

_email_startup_check()
del _email_startup_check


# ── Email diagnostic endpoint (developer-only, protected by internal key) ────
# Usage: GET /internal/email-test?key=<EMAIL_DIAG_KEY>&to=email@example.com
# Checks Resend configuration and optionally sends a real test message.
# Returns JSON with step-by-step results. Never exposes the API key.
_EMAIL_DIAG_KEY = os.getenv("EMAIL_DIAG_KEY", "").strip() or os.getenv("SMTP_DIAG_KEY", "").strip()


@app.route("/internal/email-test", methods=["GET"])
@app.route("/internal/smtp-test", methods=["GET"])  # legacy alias
def email_test_endpoint():
    if not _EMAIL_DIAG_KEY or request.args.get("key") != _EMAIL_DIAG_KEY:
        return jsonify({"status": "error", "message": "Not found"}), 404

    import httpx as _httpx
    from core import email_service
    c = email_service._cfg()
    results = []

    def add(stage, status, detail=""):
        results.append({"stage": stage, "status": status, "detail": detail})

    add("config_available", "PASS" if email_service.available() else "FAIL")
    add("config_api_key", "PASS" if c["api_key"] else "FAIL", "set" if c["api_key"] else "missing")
    add("config_sender", "PASS" if c["sender"] else "FAIL", c["sender"] or "missing")
    add("config_reply_to", "SKIP" if not c["reply_to"] else "PASS", c["reply_to"] or "unset")

    if not email_service.available():
        return jsonify({"status": "error", "message": "Resend not configured — set RESEND_API_KEY and EMAIL_FROM",
                        "results": results}), 200

    # API-key validation against Resend (list verified domains; no mail sent).
    try:
        resp = _httpx.get("https://api.resend.com/domains",
                          headers={"Authorization": f"Bearer {c['api_key']}"},
                          timeout=email_service.RESEND_TOTAL_TIMEOUT)
        if resp.status_code == 200:
            domains = [d.get("name") for d in (resp.json().get("data") or [])]
            add("api_auth", "PASS", f"verified domains: {', '.join(domains) if domains else 'none (sandbox only)'}")
        elif resp.status_code == 401:
            add("api_auth", "FAIL", "unauthorized — check RESEND_API_KEY")
            return jsonify({"status": "error", "message": "Resend rejected the API key", "results": results}), 200
        else:
            add("api_auth", "WARN", f"status={resp.status_code}")
    except Exception as exc:
        add("api_auth", "FAIL", f"{type(exc).__name__}: {exc}")

    # Optional live send
    to_addr = str(request.args.get("to") or "").strip()
    if to_addr:
        html = ("<p>This is a delivery test from ValleyMind-AI via <strong>Resend</strong>.</p>"
                "<p>If you received this, the email pipeline is working.</p>")
        result = email_service.send_email(to_addr,
                                          f"ValleyMind AI — {email_service.BRAND} delivery test",
                                          html, "Delivery test from ValleyMind-AI via Resend.")
        if result.get("success"):
            add("send", "PASS", f"accepted for {to_addr.split('@')[0]}@*** id={result.get('id')}")
        else:
            add("send", "FAIL", f"error={result.get('error')}")
    else:
        add("send", "SKIP", "no recipient — add &to=email to test delivery")

    ok = all(r["status"] in ("PASS", "SKIP") for r in results)
    return jsonify({"status": "success" if ok else "error", "results": results}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "").lower() == "true")