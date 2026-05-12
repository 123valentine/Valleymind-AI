import json
import os
import threading
from datetime import datetime

from core.config import PROJECT_ROOT


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

        self.chat_folder = os.path.join(self.base_folder, "chats")
        self._cache: dict = {}
        self._locks: dict = {}
        self._locks_lock = threading.Lock()
        self._long_term_lock = threading.Lock()
        self._long_term_changed = False

        try:
            os.makedirs(self.chat_folder, exist_ok=True)
        except OSError as exc:
            print(f"[ERROR] Could not create memory directories: {exc}")

        self.long_term = self.load_long_term()
        self.initialize_long_term_file()

    def initialize_long_term_file(self):
        if os.path.exists(self.long_term_file) and not self._long_term_changed:
            return
        self.save_long_term()
        self._long_term_changed = False

    def _get_lock(self, chat_id: str) -> threading.Lock:
        with self._locks_lock:
            if chat_id not in self._locks:
                self._locks[chat_id] = threading.Lock()
            return self._locks[chat_id]

    def load_long_term(self) -> dict:
        try:
            if os.path.exists(self.long_term_file):
                with open(self.long_term_file, "r", encoding="utf-8") as file:
                    data = json.load(file)
                if isinstance(data, dict):
                    identity = data.setdefault("identity", {})
                    if not isinstance(identity, dict):
                        identity = {}
                        data["identity"] = identity
                    preferences = data.setdefault("preferences", {})
                    if not isinstance(preferences, dict):
                        data["preferences"] = {}
                    removed = []
                    for key, value in list(identity.items()):
                        if _looks_like_assistant_identity(key, value):
                            removed.append(key)
                            identity.pop(key, None)
                    if removed:
                        self._long_term_changed = True
                        print("[WARNING] Removed assistant identity from user memory.")
                    return data
                print("[WARNING] long-term memory had unexpected format; resetting.")
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[ERROR] Failed to load long-term memory: {exc}")
        return _fresh_long_term()

    def reload(self) -> dict:
        with self._long_term_lock:
            self.long_term = self.load_long_term()
            self.initialize_long_term_file()
            return self.long_term

    def save_long_term(self):
        tmp = self.long_term_file + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.long_term_file), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as file:
                json.dump(self.long_term, file, indent=2)
            os.replace(tmp, self.long_term_file)
        except OSError as exc:
            print(f"[ERROR] Failed to save long-term memory: {exc}")
            try:
                os.remove(tmp)
            except OSError:
                pass

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

    def get_full_memory(self) -> dict:
        return self.long_term

    def get_chat_file(self, chat_id: str) -> str:
        safe_id = "".join(char for char in str(chat_id) if char.isalnum() or char in ("_", "-"))
        if not safe_id:
            safe_id = "default"
        return os.path.join(self.chat_folder, f"{safe_id}.json")

    def _load_chat_from_disk(self, chat_id: str) -> list:
        file_path = self.get_chat_file(chat_id)
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                if isinstance(data, list):
                    return data
                print(f"[WARNING] Chat file for '{chat_id}' was not a list; resetting.")
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[ERROR] Failed to load chat '{chat_id}' from disk: {exc}")
        return []

    def _save_chat_to_disk(self, chat_id: str, messages: list):
        file_path = self.get_chat_file(chat_id)
        tmp = file_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as file:
                json.dump(messages, file, indent=2)
            os.replace(tmp, file_path)
        except OSError as exc:
            print(f"[ERROR] Failed to save chat '{chat_id}': {exc}")
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _get_cached(self, chat_id: str) -> list:
        if chat_id not in self._cache:
            self._cache[chat_id] = self._load_chat_from_disk(chat_id)
        return self._cache[chat_id]

    def add_message(self, chat_id: str, role: str, content: str, timestamp=None):
        role = str(role or "user").strip()
        content = str(content or "").strip()
        if not content:
            print(f"[WARNING] add_message: empty content for role '{role}'; skipping.")
            return

        lock = self._get_lock(chat_id)
        with lock:
            messages = self._get_cached(chat_id)
            messages.append({
                "role": role,
                "content": content,
                "time": timestamp or datetime.now().isoformat(),
            })
            if len(messages) > 50:
                del messages[:-50]
            self._save_chat_to_disk(chat_id, messages)

    def get_chat(self, chat_id: str) -> list:
        lock = self._get_lock(chat_id)
        with lock:
            return list(self._get_cached(chat_id))

    def clear_chat(self, chat_id: str):
        lock = self._get_lock(chat_id)
        with lock:
            self._cache.pop(chat_id, None)
            file_path = self.get_chat_file(chat_id)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError as exc:
                print(f"[ERROR] Failed to clear chat '{chat_id}': {exc}")
