import json
import os
import threading
from datetime import datetime


class MemorySystem:

    def __init__(self):
        self.base_folder = "memory_data"
        self.chat_folder = os.path.join(self.base_folder, "chats")
        self.long_term_file = os.path.join(self.base_folder, "long_term.json")

        # ---- In-memory chat cache (chat_id -> list[dict]) ----
        # Eliminates the load-from-disk cost on every add_message() call.
        # Cache is the source of truth at runtime; disk is the persistent
        # backing store, written on every mutation via atomic rename.
        self._cache: dict = {}

        # ---- Per-chat locks ----
        # Keyed by chat_id so unrelated chats never block each other.
        # A single meta-lock guards creation of per-chat locks.
        self._locks: dict = {}
        self._locks_lock = threading.Lock()

        # ---- Long-term memory lock ----
        self._long_term_lock = threading.Lock()

        try:
            os.makedirs(self.chat_folder, exist_ok=True)
        except OSError as e:
            print(f"[ERROR] Could not create memory directories: {e}")

        self.long_term = self.load_long_term()

    # ================= INTERNAL HELPERS =================

    def _get_lock(self, chat_id: str) -> threading.Lock:
        """Return (creating if needed) a per-chat threading.Lock."""
        with self._locks_lock:
            if chat_id not in self._locks:
                self._locks[chat_id] = threading.Lock()
            return self._locks[chat_id]

    # ================= LONG TERM =================

    def load_long_term(self) -> dict:
        try:
            if os.path.exists(self.long_term_file):
                with open(self.long_term_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("identity", {})
                    data.setdefault("preferences", {})
                    return data
                print("[WARNING] long_term.json had unexpected format; resetting.")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[ERROR] Failed to load long_term.json: {e}")
        return {"identity": {}, "preferences": {}}

    def save_long_term(self):
        # Atomic write: temp file + os.replace() so a crash mid-write never
        # leaves a corrupted long_term.json on disk.
        tmp = self.long_term_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.long_term, f, indent=2)
            os.replace(tmp, self.long_term_file)
        except OSError as e:
            print(f"[ERROR] Failed to save long_term.json: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass

    # -------- IDENTITY --------

    def remember_identity(self, key: str, value: str):
        if not key or not isinstance(key, str):
            print("[WARNING] remember_identity called with invalid key; skipping.")
            return
        with self._long_term_lock:
            self.long_term["identity"][key] = value
            self.save_long_term()

    def recall_identity(self, key: str):
        return self.long_term.get("identity", {}).get(key)

    # -------- PREFERENCES --------

    def remember_preference(self, key: str, value: str):
        if not key or not isinstance(key, str):
            print("[WARNING] remember_preference called with invalid key; skipping.")
            return
        with self._long_term_lock:
            self.long_term["preferences"][key] = value
            self.save_long_term()

    def recall_preference(self, key: str):
        return self.long_term.get("preferences", {}).get(key)

    # -------- FULL MEMORY --------

    def get_full_memory(self) -> dict:
        return self.long_term

    # ================= CHAT MEMORY =================

    def get_chat_file(self, chat_id: str) -> str:
        safe_id = "".join(c for c in chat_id if c.isalnum() or c in ("_", "-"))
        if not safe_id:
            safe_id = "default"
        return os.path.join(self.chat_folder, f"{safe_id}.json")

    def _load_chat_from_disk(self, chat_id: str) -> list:
        """
        Read chat history from disk. Returns [] on any error.
        Private -- callers should go through get_chat() / add_message().
        """
        file = self.get_chat_file(chat_id)
        try:
            if os.path.exists(file):
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
                print(f"[WARNING] Chat file for '{chat_id}' was not a list; resetting.")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[ERROR] Failed to load chat '{chat_id}' from disk: {e}")
        return []

    def _save_chat_to_disk(self, chat_id: str, messages: list):
        """
        Persist chat history via atomic rename so a crash mid-write never
        corrupts the existing file.
        """
        file = self.get_chat_file(chat_id)
        tmp = file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=2)
            os.replace(tmp, file)
        except OSError as e:
            print(f"[ERROR] Failed to save chat '{chat_id}': {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _get_cached(self, chat_id: str) -> list:
        """
        Return the live cached message list for chat_id, loading from disk
        on first access.  Always returns a list -- never None.
        MUST be called while the caller holds the per-chat lock.
        """
        if chat_id not in self._cache:
            self._cache[chat_id] = self._load_chat_from_disk(chat_id)
        return self._cache[chat_id]

    # -------- PUBLIC CHAT API --------

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

            # Keep history bounded at 50 messages; trim in-place so the
            # cached list reference stays valid across all callers.
            if len(messages) > 50:
                del messages[:-50]

            self._save_chat_to_disk(chat_id, messages)

    def get_chat(self, chat_id: str) -> list:
        """
        Return a snapshot of chat history.
        Reads from cache (no disk hit if the cache is warm).
        Returns a shallow copy so callers cannot mutate the cache directly.
        """
        lock = self._get_lock(chat_id)
        with lock:
            return list(self._get_cached(chat_id))

    def clear_chat(self, chat_id: str):
        lock = self._get_lock(chat_id)
        with lock:
            self._cache.pop(chat_id, None)
            file = self.get_chat_file(chat_id)
            try:
                if os.path.exists(file):
                    os.remove(file)
            except OSError as e:
                print(f"[ERROR] Failed to clear chat '{chat_id}': {e}")