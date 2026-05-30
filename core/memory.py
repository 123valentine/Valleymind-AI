import json
import os
import threading
from datetime import datetime

from core.config import PROJECT_ROOT
from core.db import get_db, get_db_manager


ASSISTANT_IDENTITY_NAMES = {"marcus"}


def _fresh_long_term() -> dict:
    return {
        "identity": {},
        "preferences": {},
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
        self._locks: dict = {}
        self._locks_lock = threading.Lock()
        self._long_term_lock = threading.Lock()
        self.db = get_db()
        self.db_manager = get_db_manager()

        if os.path.basename(self.base_folder) == "memory_data":
            self.user_id = "default"
        else:
            self.user_id = os.path.basename(os.path.dirname(self.base_folder))

        self.long_term = self.load_long_term()
        self.initialize_long_term_file()

    def initialize_long_term_file(self):
        if self.long_term == _fresh_long_term():
            self.save_long_term()

    def _get_lock(self, chat_id: str) -> threading.Lock:
        with self._locks_lock:
            if chat_id not in self._locks:
                self._locks[chat_id] = threading.Lock()
            return self._locks[chat_id]

    def load_long_term(self) -> dict:
        if self.db is not None:
            try:
                data = self.db.long_term.find_one({"_id": self.user_id})
                if data:
                    data.pop("_id", None)
                    identity = data.setdefault("identity", {})
                    removed = False
                    for key, value in list(identity.items()):
                        if _looks_like_assistant_identity(key, value):
                            identity.pop(key, None)
                            removed = True
                    if removed:
                        self.save_long_term()
                    return data
            except Exception as exc:
                print(f"[ERROR] Failed to load long-term memory from MongoDB: {exc}")
        return _fresh_long_term()

    def reload(self) -> dict:
        with self._long_term_lock:
            self.long_term = self.load_long_term()
            return self.long_term

    def save_long_term(self):
        if self.db is not None:
            try:
                data = self.long_term.copy()
                data["_id"] = self.user_id
                self.db.long_term.replace_one({"_id": self.user_id}, data, upsert=True)
            except Exception as exc:
                print(f"[ERROR] Failed to save long-term memory to MongoDB: {exc}")

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

    def load_memory(self, user_id: str) -> dict:
        self.user_id = user_id
        return self.reload()

    def save_memory(self):
        self.save_long_term()

    def get_full_memory(self) -> dict:
        return self.long_term

    def load_chat(self, chat_id: str) -> list:
        if self.db is not None:
            try:
                data = self.db.chats.find_one({"chat_id": chat_id})
                if data and data.get("messages"):
                    return data.get("messages", [])
            except Exception as exc:
                print(f"[ERROR] Failed to load chat '{chat_id}' from MongoDB: {exc}")
        local_path = os.path.join("memory_data", "chats", f"{chat_id}.json")
        try:
            if os.path.exists(local_path):
                with open(local_path, "r", encoding="utf-8") as f:
                    local_data = json.load(f)
                if isinstance(local_data, dict) and local_data.get("messages"):
                    return local_data["messages"]
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[ERROR] Failed to load chat '{chat_id}' from local file: {exc}")
        return []

    def save_chat(self, chat_id: str, messages: list, title: str = ""):
        self.db_manager.background_chat_write(chat_id, messages, user_id=self.user_id, title=title)

    def create_session(self, chat_id: str, title: str = "New Chat") -> dict:
        self._cache[chat_id] = []
        now = datetime.now().isoformat()
        title_to_use = title or "New Chat"
        self.db_manager.background_chat_write(
            chat_id, [], user_id=self.user_id, title=title_to_use,
        )
        return {
            "chat_id": chat_id,
            "title": title_to_use,
            "created_at": now,
            "last_activity": now,
            "message_count": 0,
        }

    def list_sessions(self) -> list:
        return self.db_manager.list_sessions(self.user_id)

    def delete_session(self, chat_id: str):
        with self._get_lock(chat_id):
            self._cache.pop(chat_id, None)
        self.db_manager.delete_session(chat_id, self.user_id)

    def set_title(self, chat_id: str, title: str):
        self.db_manager.update_session_title(chat_id, title)

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

    def add_message(self, chat_id: str, role: str, content: str, timestamp=None, image_data: str = ""):
        role = str(role or "user").strip()
        content = str(content or "").strip()
        if not content and not image_data:
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

            if self.db is not None:
                try:
                    self.db.chats.delete_one({"chat_id": chat_id})
                except Exception as exc:
                    print(f"[ERROR] Failed to clear chat '{chat_id}' from MongoDB: {exc}")
