import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

from core.config import PROJECT_ROOT
from core.db import chats_collection, user_memory_collection


ASSISTANT_IDENTITY_NAMES = {"marcus"}


def _fresh_long_term() -> dict:
    return {
        "identity": {},
        "preferences": {},
        "facts": [],
    }


def _looks_like_assistant_identity(key: str, value: str) -> bool:
    key = str(key or "").strip().lower()
    value = str(value or "").strip().lower()
    return key == "name" and value in ASSISTANT_IDENTITY_NAMES


class MemorySystem:
    def __init__(self, memory_file: str = "", base_folder: str = ""):
        if memory_file:
            self.long_term_file = os.path.abspath(memory_file)
            self.base_folder = os.path.dirname(self.long_term_file)
        else:
            self.base_folder = os.path.abspath(base_folder or os.path.join(PROJECT_ROOT, "memory_data"))
            self.long_term_file = os.path.join(self.base_folder, "long_term.json")

        self._cache: dict = {}
        self._sessions: dict = {}
        self._locks: dict = {}
        self._locks_lock = threading.Lock()
        self._long_term_lock = threading.Lock()

        if os.path.basename(self.base_folder) == "memory_data":
            self.user_id = "default"
        else:
            self.user_id = os.path.basename(os.path.dirname(self.base_folder))

        self.long_term = self.load_long_term()
        self.initialize_long_term_file()
        self._migrate_dicts_to_facts()

    def initialize_long_term_file(self):
        if self.long_term == _fresh_long_term():
            self.save_long_term()

    def _get_lock(self, chat_id: str) -> threading.Lock:
        with self._locks_lock:
            if chat_id not in self._locks:
                self._locks[chat_id] = threading.Lock()
            return self._locks[chat_id]

    def load_long_term(self) -> dict:
        coll = user_memory_collection()
        if coll is not None:
            try:
                doc = coll.find_one({"_id": self.user_id})
                if doc and isinstance(doc.get("data"), dict):
                    return doc["data"]
                if doc is not None:
                    return _fresh_long_term()
            except Exception as exc:
                print(f"[MEMORY] Mongo load_long_term failed, falling back to local file: {exc}")

        try:
            if os.path.exists(self.long_term_file):
                with open(self.long_term_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[MEMORY] Failed to load long-term memory: {exc}")
        return _fresh_long_term()

    def reload(self) -> dict:
        with self._long_term_lock:
            self.long_term = self.load_long_term()
            return self.long_term

    def save_long_term(self):
        coll = user_memory_collection()
        if coll is not None:
            try:
                coll.replace_one(
                    {"_id": self.user_id},
                    {"_id": self.user_id, "data": self.long_term, "updated_at": datetime.now(timezone.utc)},
                    upsert=True,
                )
            except Exception as exc:
                print(f"[MEMORY] Mongo save_long_term failed, local file only: {exc}")

        try:
            os.makedirs(os.path.dirname(self.long_term_file), exist_ok=True)
            tmp = self.long_term_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.long_term, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.long_term_file)
        except OSError as exc:
            print(f"[MEMORY] Failed to save long-term memory: {exc}")

    def _creator_prefs_file(self) -> str:
        return os.path.join(self.base_folder, "creator_preferences.json")

    def set_creator_identity(self, name: str, title: str):
        with self._long_term_lock:
            prefs = {"identity_name": name, "title": title, "updated_at": datetime.now().isoformat()}
            self.long_term.setdefault("creator", {}).update(prefs)
            self.save_long_term()

    def save_creator_message(self, content: str):
        fpath = self._creator_prefs_file()
        prefs = []
        try:
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, list):
                    prefs = existing
        except (json.JSONDecodeError, OSError):
            pass
        prefs.append({
            "timestamp": datetime.now().isoformat(),
            "instruction": content[:2000],
        })
        try:
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(prefs, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            print(f"[ERROR] Failed to save creator preference: {exc}")

    def load_creator_context(self) -> str:
        fpath = self._creator_prefs_file()
        try:
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
                if isinstance(prefs, list) and prefs:
                    return "\n".join(
                        f"- {p.get('instruction', '')}"
                        for p in prefs[-10:]
                    )
        except (json.JSONDecodeError, OSError):
            pass
        return ""

    def remember_identity(self, key: str, value: str):
        if not key or not isinstance(key, str):
            print("[WARNING] remember_identity called with invalid key; skipping.")
            return
        if _looks_like_assistant_identity(key, value):
            print("[WARNING] Refused to store assistant identity as user identity.")
            return
        with self._long_term_lock:
            self.long_term["identity"][key] = value
            self.save_long_term()

    def recall_identity(self, key: str):
        return self.long_term.get("identity", {}).get(key)

    def get_user_name(self) -> str:
        name = str((self.long_term.get("identity") or {}).get("name") or "").strip()
        if _looks_like_assistant_identity("name", name):
            return ""
        return name

    def initialize_user_name(self, name: str):
        name = str(name or "").strip()
        if not name or _looks_like_assistant_identity("name", name):
            return
        with self._long_term_lock:
            if self.get_user_name():
                return
            self.long_term.setdefault("identity", {})["name"] = name
            self.save_long_term()

    def remember_preference(self, key: str, value: str):
        if not key or not isinstance(key, str):
            print("[WARNING] remember_preference called with invalid key; skipping.")
            return
        with self._long_term_lock:
            self.long_term["preferences"][key] = value
            self.save_long_term()

    def recall_preference(self, key: str):
        return self.long_term.get("preferences", {}).get(key)

    # ── Five-category fact memory (fact/preference/project/exploration/callback) ──

    def remember_fact(self, memory_type: str, summary: str, value: str, confidence: float = 0.0):
        if not summary or not isinstance(summary, str):
            print("[WARNING] remember_fact called without a summary; skipping.")
            return
        confidence = max(0.0, min(1.0, float(confidence)))
        with self._long_term_lock:
            facts = self.long_term.setdefault("facts", [])
            existing_idx = self._find_fact_by_summary(summary)
            if existing_idx is not None:
                existing = facts[existing_idx]
                old_type = existing.get("memory_type", "callback")
                existing["confidence"] = max(existing.get("confidence", 0.0), confidence)
                existing["mention_count"] = existing.get("mention_count", 1) + 1
                existing["last_updated"] = datetime.now().isoformat()
                existing["memory_type"] = self._promote_type(old_type, existing["confidence"])
                if existing["memory_type"] != old_type:
                    print(f"[MEMORY] Promoted fact '{summary[:60]}' from {old_type} to {existing['memory_type']}")
                if existing.get("expires_at") is not None:
                    existing["expires_at"] = self._compute_expiry(existing["memory_type"])
            else:
                facts.append({
                    "memory_type": memory_type or "callback",
                    "summary": summary.strip(),
                    "value": (value or "").strip(),
                    "confidence": confidence,
                    "timestamp": datetime.now().isoformat(),
                    "last_updated": datetime.now().isoformat(),
                    "mention_count": 1,
                    "expires_at": self._compute_expiry(memory_type),
                    "vetoed": False,
                })
            self._expire_stale_facts()
            self.save_long_term()

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[^\w\s]", "", str(text or "").lower()).strip()

    def _find_fact_by_summary(self, summary: str) -> int | None:
        words = set(self._normalize_text(summary).split())
        if not words:
            return None
        facts = self.long_term.get("facts", [])
        best_idx = None
        best_score = 0.0
        for i, f in enumerate(facts):
            if f.get("vetoed"):
                continue
            f_words = set(self._normalize_text(f.get("summary", "")).split())
            if not f_words:
                continue
            overlap = len(words & f_words) / max(len(words), len(f_words))
            if overlap > 0.3 and overlap > best_score:
                best_score = overlap
                best_idx = i
        return best_idx

    @staticmethod
    def _promote_type(current_type: str, confidence: float) -> str:
        upgrades = {
            "callback": (0.6, "exploration"),
            "exploration": (0.75, "project"),
            "project": (0.95, "fact"),
        }
        if current_type in upgrades:
            threshold, promoted = upgrades[current_type]
            if confidence >= threshold:
                return promoted
        return current_type

    @staticmethod
    def _compute_expiry(memory_type: str) -> str | None:
        ttl_map = {
            "exploration": 60,
            "callback": 30,
        }
        days = ttl_map.get(memory_type)
        if days is not None:
            return (datetime.now() + timedelta(days=days)).isoformat()
        return None

    def _expire_stale_facts(self):
        now = datetime.now()
        facts = self.long_term.get("facts", [])
        before = len(facts)
        facts[:] = [f for f in facts if not f.get("vetoed") and (
            f.get("expires_at") is None or datetime.fromisoformat(f["expires_at"]) > now
        )]
        removed = before - len(facts)
        if removed:
            print(f"[MEMORY] Expired {removed} stale fact(s)")

    _RETRACTION_STOPWORDS = frozenset({
        "i", "me", "my", "you", "your", "we", "the", "a", "an", "it", "that",
        "this", "what", "said", "say", "told", "mentioned", "forget", "about",
        "ignore", "never", "mind", "actually", "please", "was", "is", "are",
        "be", "to", "of", "in", "on", "for", "and", "or", "just", "thinking",
        "out", "loud", "dont", "do", "not", "remember", "scratch", "earlier",
        "before", "thing", "stuff", "user",
    })

    def handle_retraction(self, text: str) -> int:
        # Overlap is normalized by the retraction's own content words: a short
        # "forget about X" should veto any fact about X, no matter how long the
        # fact summary is. (The archived max-length formula let filler words
        # dilute the score below threshold.)
        words = set(self._normalize_text(text).split()) - self._RETRACTION_STOPWORDS
        if not words:
            return 0
        count = 0
        with self._long_term_lock:
            for f in self.long_term.get("facts", []):
                if f.get("vetoed"):
                    continue
                f_words = set(self._normalize_text(
                    f.get("summary", "") + " " + f.get("value", "")
                ).split())
                if not f_words:
                    continue
                coverage = len(words & f_words) / len(words)
                if coverage >= 0.5:
                    f["vetoed"] = True
                    f["confidence"] = 0.0
                    count += 1
            if count:
                self.save_long_term()
                print(f"[MEMORY] Vetoed {count} fact(s) by retraction")
        return count

    def get_active_facts(self) -> list[dict]:
        """Non-vetoed, non-expired facts, strongest first (for prompt injection)."""
        now = datetime.now()
        facts = []
        for f in self.long_term.get("facts", []):
            if f.get("vetoed"):
                continue
            expires = f.get("expires_at")
            if expires is not None:
                try:
                    if datetime.fromisoformat(expires) <= now:
                        continue
                except (ValueError, TypeError):
                    pass
            facts.append(dict(f))
        facts.sort(key=lambda f: (f.get("confidence", 0.0), f.get("mention_count", 1)), reverse=True)
        return facts

    def _migrate_dicts_to_facts(self):
        """One-time migration of legacy identity/preferences dicts into facts.

        The dicts themselves are kept — get_user_name(), login initialization,
        and the settings endpoints still read/write them — but their contents
        become first-class facts so the categorized system sees them.
        """
        if self.long_term.get("facts_migrated_v1"):
            return
        with self._long_term_lock:
            if self.long_term.get("facts_migrated_v1"):
                return
            facts = self.long_term.setdefault("facts", [])
            existing_summaries = {self._normalize_text(f.get("summary", "")) for f in facts}
            now = datetime.now().isoformat()

            def _add(memory_type: str, summary: str, value: str, confidence: float):
                if self._normalize_text(summary) in existing_summaries:
                    return
                facts.append({
                    "memory_type": memory_type,
                    "summary": summary,
                    "value": value,
                    "confidence": confidence,
                    "timestamp": now,
                    "last_updated": now,
                    "mention_count": 1,
                    "expires_at": None,
                    "vetoed": False,
                })

            for key, val in (self.long_term.get("identity") or {}).items():
                val = str(val or "").strip()
                if val and not _looks_like_assistant_identity(key, val):
                    _add("fact", f"User's {key} is {val}", val, 0.95)
            for key, val in (self.long_term.get("preferences") or {}).items():
                val = str(val or "").strip()
                if val:
                    _add("preference", f"User prefers {key}: {val}", val, 0.9)

            self.long_term["facts_migrated_v1"] = True
            self.save_long_term()
            migrated = len(facts)
            if migrated:
                print(f"[MEMORY] Migrated legacy identity/preferences into {migrated} fact(s)")

    def load_memory(self, user_id: str = "") -> dict:
        # self.user_id is the storage owner (derived from the memory path at
        # construction) and scopes every Mongo chat query. It must never be
        # replaced with a character key, so the argument is ignored.
        return self.reload()

    def save_memory(self):
        self.save_long_term()

    def get_full_memory(self) -> dict:
        return self.long_term

    def load_chat(self, chat_id: str) -> list:
        coll = chats_collection()
        if coll is not None:
            try:
                doc = coll.find_one({"chat_id": chat_id, "user_id": self.user_id})
                if doc and isinstance(doc.get("messages"), list):
                    return doc["messages"]
                if doc is not None:
                    return []
            except Exception as exc:
                print(f"[MEMORY] Mongo load_chat failed for {chat_id}, falling back to local file: {exc}")

        chat_file = os.path.join(self.base_folder, "chats", f"{chat_id}.json")
        try:
            if os.path.exists(chat_file):
                with open(chat_file, "r", encoding="utf-8") as f:
                    messages = json.load(f)
                if isinstance(messages, list):
                    return messages
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[MEMORY] Failed to load chat {chat_id}: {exc}")
        return []

    def save_chat(self, chat_id: str, messages: list, title: str = ""):
        coll = chats_collection()
        if coll is not None:
            try:
                now = datetime.now(timezone.utc)
                existing = coll.find_one({"chat_id": chat_id})
                doc = {
                    "chat_id": chat_id,
                    "user_id": self.user_id,
                    "messages": messages,
                    "message_count": len(messages),
                    "last_activity": now,
                    "created_at": (existing or {}).get("created_at", now),
                }
                resolved_title = title or (existing or {}).get("title", "")
                if resolved_title:
                    doc["title"] = resolved_title
                coll.replace_one({"chat_id": chat_id}, doc, upsert=True)
                return
            except Exception as exc:
                print(f"[MEMORY] Mongo save_chat failed for {chat_id}, falling back to local file: {exc}")

        chat_dir = os.path.join(self.base_folder, "chats")
        chat_file = os.path.join(chat_dir, f"{chat_id}.json")
        try:
            os.makedirs(chat_dir, exist_ok=True)
            tmp = chat_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=1, ensure_ascii=False)
            os.replace(tmp, chat_file)
        except OSError as exc:
            print(f"[MEMORY] Failed to save chat {chat_id}: {exc}")

    def create_session(self, chat_id: str, title: str = "New Chat") -> dict:
        self._cache[chat_id] = []
        now = datetime.now().isoformat()
        title_to_use = title or "New Chat"
        session = {
            "chat_id": chat_id,
            "title": title_to_use,
            "created_at": now,
            "last_activity": now,
            "message_count": 0,
        }
        self._sessions[chat_id] = session
        return session

    def list_sessions(self) -> list:
        return list(self._sessions.values())

    def delete_session(self, chat_id: str):
        self._sessions.pop(chat_id, None)
        with self._get_lock(chat_id):
            self._cache.pop(chat_id, None)

    def set_title(self, chat_id: str, title: str):
        if chat_id not in self._sessions:
            now = datetime.now().isoformat()
            self._sessions[chat_id] = {
                "chat_id": chat_id,
                "title": "New Chat",
                "created_at": now,
                "last_activity": now,
                "message_count": 0,
            }
        self._sessions[chat_id]["title"] = title

    def update_reaction(self, chat_id: str, message_index: int, reaction: str) -> bool:
        lock = self._get_lock(chat_id)
        with lock:
            messages = self._get_cached(chat_id)
            if 0 <= message_index < len(messages):
                msg = dict(messages[message_index])
                if reaction in ("up", "down", ""):
                    msg["reaction"] = reaction if reaction else None
                else:
                    msg["reaction"] = None
                messages[message_index] = msg
                self.save_chat(chat_id, messages)
                return True
        return False

    def get_message_count(self, chat_id: str) -> int:
        lock = self._get_lock(chat_id)
        with lock:
            return len(self._get_cached(chat_id))

    def _get_cached(self, chat_id: str) -> list:
        if chat_id not in self._cache:
            self._cache[chat_id] = self.load_chat(chat_id)
        return self._cache[chat_id]

    def add_message(self, chat_id: str, role: str, content: str, timestamp=None, image_data: str = "", image_url: str = ""):
        role = str(role or "user").strip()
        content = str(content or "").strip()
        if not content and not image_data and not image_url:
            print(f"[WARNING] add_message: empty content and no image for role '{role}'; skipping.")
            return

        lock = self._get_lock(chat_id)
        with lock:
            messages = self._get_cached(chat_id)
            msg = {
                "role": role,
                "content": content or "(image attached)",
                "time": timestamp or datetime.now().isoformat(),
            }
            if image_data:
                msg["image_data"] = image_data
            if image_url:
                msg["image_url"] = image_url
            messages.append(msg)
            self.save_chat(chat_id, messages)

    def get_chat(self, chat_id: str) -> list:
        lock = self._get_lock(chat_id)
        with lock:
            return list(self._get_cached(chat_id))

    def clear_chat(self, chat_id: str):
        lock = self._get_lock(chat_id)
        with lock:
            self._cache.pop(chat_id, None)

            coll = chats_collection()
            if coll is not None:
                try:
                    coll.delete_one({"chat_id": chat_id, "user_id": self.user_id})
                except Exception as exc:
                    print(f"[MEMORY] Mongo clear_chat failed for {chat_id}, falling back to local file: {exc}")

            chat_file = os.path.join(self.base_folder, "chats", f"{chat_id}.json")
            try:
                if os.path.exists(chat_file):
                    os.remove(chat_file)
            except OSError as exc:
                print(f"[MEMORY] Failed to clear chat {chat_id}: {exc}")
