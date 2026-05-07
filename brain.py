import json
import os
import re
import requests
from datetime import datetime
from dotenv import load_dotenv

from core.auto_model import get_latest_groq_model
from core.memory import MemorySystem

# ================= CONFIG =================
# load_dotenv() is also called in app.py before this module is imported,
# so the key will already be in the environment. This call is a safety net
# for cases where brain.py is run or tested directly.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

API_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not API_KEY:
    print("[WARNING] GROQ_API_KEY is not set. AI responses will be unavailable.")


# ================= FALLBACKS =================
FALLBACK_RESPONSE = "I'm having trouble responding right now. Please try again."

_FALLBACK_ENVELOPE = {
    "reply": FALLBACK_RESPONSE,
    "intent": "general",
    "entity": "",
    "value": "",
}


# ================= SYSTEM PROMPT =================
# One LLM call does two jobs:
#   1. Generates a natural reply (reply field)
#   2. Classifies the message for memory storage (intent / entity / value)
_SYSTEM_PROMPT = """You are Marcus AI — intelligent, warm, and conversational.
You respond naturally to every message. You never sound like a bot running keyword checks.

You MUST output ONLY a single JSON object — no markdown, no code fences, no explanation.
Use exactly this structure:

{
  "reply": "<your full natural response to the user>",
  "intent": "<one of: identity | preference | memory_question | general>",
  "entity": "<thing being stored, e.g. 'name' or 'favorite_color' — empty string if none>",
  "value":  "<value to store, e.g. 'Alice' or 'blue' — empty string if none>"
}

Intent rules (default to 'general' when unsure):
- identity        : user is sharing info about themselves (name, age, job, location, etc.)
- preference      : user is expressing a like, dislike, or preference
- memory_question : user is asking what you remember about them
- general         : everything else including greetings, questions, tasks, chat

For greetings respond warmly and naturally. Use intent='general'.
Never leave reply empty. Never output anything outside the JSON object."""


# ================= LOW-LEVEL API CALL =================
def _call_groq(messages: list, model_name: str) -> str:
    """
    POST to Groq and return the raw content string.
    Raises on failure — caller handles exceptions.
    Never returns None or empty string on success.
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 1024,
    }

    res = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if res.status_code != 200:
        raise RuntimeError(f"Groq API {res.status_code}: {res.text[:300]}")

    data = res.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Groq returned 200 but choices list was empty.")

    content = (choices[0].get("message") or {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Groq returned 200 but content was empty.")

    return content.strip()


# ================= ENVELOPE PARSER =================
def _parse_envelope(raw: str) -> dict:
    """
    Parse the JSON envelope from the LLM's raw output.
    Two-attempt strategy:
      1. Parse the whole string.
      2. Extract the first {...} block if attempt 1 fails.
    Always returns a fully populated safe dict — never raises.
    """
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    parsed = None

    # Attempt 1: full string parse
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 2: find first {...} block
    if not isinstance(parsed, dict):
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass

    if not isinstance(parsed, dict):
        print(f"[WARNING] Envelope parse failed. Raw: {cleaned[:300]}")
        return _FALLBACK_ENVELOPE.copy()

    # Normalise every field
    reply  = str(parsed.get("reply")  or "").strip()
    intent = str(parsed.get("intent") or "general").strip()
    entity = str(parsed.get("entity") or "").strip()
    value  = str(parsed.get("value")  or "").strip()

    if intent not in {"identity", "preference", "memory_question", "general"}:
        intent = "general"

    if not reply:
        reply = FALLBACK_RESPONSE

    return {"reply": reply, "intent": intent, "entity": entity, "value": value}


# ================= BRAIN =================
class MarcusBrain:

    def __init__(self, memory_file: str = "", behavior_file: str = ""):
        # memory_file is accepted for app.py compatibility but MemorySystem
        # manages its own storage paths internally.
        self.memory = MemorySystem()
        self.behavior_file = behavior_file
        self.behavior: dict = {}
        self.knowledge: dict = {}

        self.core_dir = os.path.dirname(os.path.abspath(__file__))
        self.knowledge_file = os.path.join(self.core_dir, "knowledge.json")

        # Model name cached after first resolution to avoid repeated API calls
        self._model_name: str = ""

        self.load_behavior()
        self.load_knowledge()

    # ================= MODEL =================

    def _get_model(self) -> str:
        if self._model_name:
            return self._model_name
        try:
            name = get_latest_groq_model(API_KEY)
            if name and isinstance(name, str):
                self._model_name = name.strip()
            else:
                print("[ERROR] get_latest_groq_model returned an invalid name.")
        except Exception as e:
            print(f"[ERROR] Failed to resolve model name: {e}")
        return self._model_name

    # ================= BEHAVIOR =================

    def load_behavior(self):
        try:
            if os.path.exists(self.behavior_file):
                with open(self.behavior_file, "r", encoding="utf-8") as f:
                    self.behavior = json.load(f)
            else:
                self.behavior = {"name": "Marcus", "role": "AI Assistant", "mood": "calm"}
        except Exception as e:
            print(f"[ERROR] Failed to load behavior file: {e}")
            self.behavior = {"name": "Marcus", "role": "AI Assistant", "mood": "calm"}

    # ================= KNOWLEDGE =================

    def load_knowledge(self):
        try:
            if os.path.exists(self.knowledge_file):
                with open(self.knowledge_file, "r", encoding="utf-8") as f:
                    self.knowledge = json.load(f)
            else:
                self.knowledge = {}
        except Exception as e:
            print(f"[ERROR] Failed to load knowledge file: {e}")
            self.knowledge = {}

    def save_knowledge(self):
        try:
            with open(self.knowledge_file, "w", encoding="utf-8") as f:
                json.dump(self.knowledge, f, indent=4)
        except Exception as e:
            print(f"[ERROR] Failed to save knowledge file: {e}")

    # ================= MEMORY RECALL (no LLM) =================

    def _handle_memory_question(self) -> str:
        try:
            mem = self.memory.get_full_memory() or {}
            identity = mem.get("identity", {})
            prefs    = mem.get("preferences", {})

            lines = []
            if identity:
                lines.append("Here's what I know about you:")
                for k, v in identity.items():
                    lines.append(f"  • {k}: {v}")
            if prefs:
                lines.append("Your preferences:")
                for k, v in prefs.items():
                    lines.append(f"  • {k}: {v}")
            if not lines:
                lines.append("I don't have anything saved about you yet.")

            return "\n".join(lines)
        except Exception as e:
            print(f"[ERROR] _handle_memory_question failed: {e}")
            return "I couldn't retrieve your memory right now."

    # ================= SINGLE LLM CALL =================

    def _think(self, chat_id: str, user_message: str) -> dict:
        """
        ONE Groq call that generates a reply AND classifies intent.
        Returns a safe envelope dict. Never raises.
        """
        model = self._get_model()
        if not model:
            return _FALLBACK_ENVELOPE.copy()

        # Build long-term memory context to inject into system prompt
        try:
            lt = self.memory.get_full_memory() or {}
            identity_str = json.dumps(lt.get("identity", {}))
            prefs_str    = json.dumps(lt.get("preferences", {}))
        except Exception:
            identity_str = "{}"
            prefs_str    = "{}"

        system_with_memory = (
            _SYSTEM_PROMPT
            + f"\n\nWhat you already know about this user:\n"
            f"Identity: {identity_str}\n"
            f"Preferences: {prefs_str}"
        )

        # Build message list from history
        try:
            history = self.memory.get_chat(chat_id) or []
        except Exception as e:
            print(f"[ERROR] Failed to retrieve chat history: {e}")
            history = []

        messages = [{"role": "system", "content": system_with_memory}]

        # Last 20 history entries, drop the last if it's the current user message
        # (already stored before _think is called) to avoid duplication
        recent = history[-20:]
        if recent and recent[-1].get("role") == "user":
            recent = recent[:-1]

        for msg in recent:
            role    = msg.get("role", "user")
            content = msg.get("content", "")
            if content and isinstance(content, str):
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})

        try:
            raw = _call_groq(messages, model)
            return _parse_envelope(raw)
        except requests.exceptions.Timeout:
            print("[ERROR] Groq request timed out.")
        except requests.exceptions.ConnectionError:
            print("[ERROR] Network connection failed.")
        except Exception as e:
            print(f"[ERROR] _think() failed: {e}")

        return _FALLBACK_ENVELOPE.copy()

    # ================= MAIN ENTRY POINT =================

    def respond(self, message: str) -> str:
        """
        Process a user message. Always returns a non-empty string.
        Never raises. Never returns None.

        Flow:
          1. Sanitise and store user message.
          2. Check for memory question — handle without LLM if matched.
          3. ONE LLM call via _think() → reply + intent metadata.
          4. Update long-term memory from metadata.
          5. Store and return the reply.
        """
        try:
            message = (message or "").strip()
            if not message:
                return "I didn't catch that — could you say something?"

            chat_id   = "main_chat"
            timestamp = datetime.now().isoformat()

            # Step 1: store user message
            try:
                self.memory.add_message(chat_id, "user", message, timestamp)
            except Exception as e:
                print(f"[ERROR] Memory add_message (user) failed: {e}")

            # Step 2: memory question short-circuit (zero API cost)
            lowered = message.lower()
            memory_triggers = (
                "what do you remember",
                "what do you know about me",
                "what have i told you",
                "do you remember",
                "recall",
                "my info",
                "my data",
            )
            if any(t in lowered for t in memory_triggers):
                reply = self._handle_memory_question()
                try:
                    self.memory.add_message(chat_id, "assistant", reply, timestamp)
                except Exception as e:
                    print(f"[ERROR] Memory add_message (assistant) failed: {e}")
                return reply

            # Step 3: single LLM call
            envelope = self._think(chat_id, message)

            reply  = str(envelope.get("reply")  or "").strip()
            intent = str(envelope.get("intent") or "general").strip()
            entity = str(envelope.get("entity") or "").strip()
            value  = str(envelope.get("value")  or "").strip()

            if not reply:
                reply = FALLBACK_RESPONSE

            # Step 4: update long-term memory
            if intent == "identity" and entity and value:
                try:
                    self.memory.remember_identity(entity, value)
                except Exception as e:
                    print(f"[ERROR] remember_identity failed: {e}")

            elif intent == "preference" and entity and value:
                try:
                    self.memory.remember_preference(entity, value)
                except Exception as e:
                    print(f"[ERROR] remember_preference failed: {e}")

            elif intent == "memory_question":
                # LLM flagged it but trigger list didn't catch it — handle now
                reply = self._handle_memory_question()

            # Step 5: store and return reply
            try:
                self.memory.add_message(chat_id, "assistant", reply, timestamp)
            except Exception as e:
                print(f"[ERROR] Memory add_message (assistant) failed: {e}")

            return reply

        except Exception as e:
            print(f"[CRITICAL] Unhandled error in respond(): {e}")
            return FALLBACK_RESPONSE