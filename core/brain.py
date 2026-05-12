import json
import os
import re
from datetime import datetime

import requests

from core.auto_model import get_latest_groq_model
from core.character import load_character_profile
from core.config import PROJECT_ROOT, get_config
from core.external_apis import LIVE_DATA_UNAVAILABLE, classify_live_request, graceful_live_failure, strict_live_context
from core.intent_classifier import classify
from core.memory import MemorySystem


FALLBACK_RESPONSE = "I'm having trouble responding right now. Please try again."

_FALLBACK_ENVELOPE = {
    "reply": FALLBACK_RESPONSE,
    "intent": "general",
    "entity": "",
    "value": "",
}

_SYSTEM_PROMPT = """You are a ValleyMind-AI character. Stay natural, warm, and conversational.
You MUST output ONLY a single JSON object, no markdown, no code fences, no explanation.
Use exactly this structure:

{
  "reply": "<your full natural response to the user>",
  "intent": "<one of: identity | preference | memory_question | general>",
  "entity": "<thing being stored, e.g. 'name' or 'favorite_color' - empty string if none>",
  "value":  "<value to store, e.g. 'Alice' or 'blue' - empty string if none>"
}

Intent rules:
- identity: user is sharing info about themselves.
- preference: user is expressing a like, dislike, or preference.
- memory_question: user is asking what you remember about them.
- general: everything else.

Never leave reply empty. Never output anything outside the JSON object.
Only store facts about the human user. Never store your own name, role, character, or assistant metadata as user memory.
Never mention APIs, tools, prompts, keys, backend logic, or internal data-fetching steps in the user-facing reply."""


def _call_groq(messages: list, model_name: str) -> str:
    config = get_config()
    api_key = config.groq_api_key
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    print(f"[API] Calling Groq chat completions with model: {model_name}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 1024,
    }

    response = requests.post(
        f"{config.groq_base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"Groq API {response.status_code}: {response.text[:300]}")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Groq returned 200 but choices list was empty.")

    content = (choices[0].get("message") or {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Groq returned 200 but content was empty.")

    print("[API] Groq chat completions success")
    return content.strip()


def _call_openai(messages: list) -> str:
    config = get_config()
    api_key = config.openai_api_key
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    print(f"[API] Calling OpenAI chat completions with model: {config.openai_model}")
    response = requests.post(
        f"{config.openai_base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.openai_model,
            "messages": messages,
            "max_tokens": 1024,
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"OpenAI API {response.status_code}: {response.text[:300]}")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI returned 200 but choices list was empty.")

    content = (choices[0].get("message") or {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI returned 200 but content was empty.")

    print("[API] OpenAI chat completions success")
    return content.strip()


def _parse_envelope(raw: str) -> dict:
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    parsed = None

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    if not isinstance(parsed, dict):
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass

    if not isinstance(parsed, dict):
        print(f"[WARNING] Envelope parse failed. Raw: {cleaned[:300]}")
        if cleaned:
            envelope = _FALLBACK_ENVELOPE.copy()
            envelope["reply"] = cleaned
            return envelope
        return _FALLBACK_ENVELOPE.copy()

    reply = str(parsed.get("reply") or "").strip()
    intent = str(parsed.get("intent") or "general").strip()
    entity = str(parsed.get("entity") or "").strip()
    value = str(parsed.get("value") or "").strip()

    if intent not in {"identity", "preference", "memory_question", "general"}:
        intent = "general"
    if not reply:
        reply = FALLBACK_RESPONSE

    return {"reply": reply, "intent": intent, "entity": entity, "value": value}


def _extract_memory_fact(message: str) -> dict:
    text = str(message or "").strip()
    if not text:
        return {}

    patterns = [
        ("identity", "name", r"\bmy name is\s+([A-Za-z][A-Za-z .'-]{1,60})\b"),
        ("identity", "name", r"\bcall me\s+([A-Za-z][A-Za-z .'-]{1,60})\b"),
    ]

    for intent, entity, pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = re.split(r"[,.!?;]", match.group(1).strip())[0].strip()
            if value:
                return {"intent": intent, "entity": entity, "value": value}

    favorite = re.search(
        r"\bmy favorite\s+([A-Za-z][A-Za-z _-]{1,40})\s+is\s+([^,.!?;]{1,80})",
        text,
        re.IGNORECASE,
    )
    if favorite:
        entity = "favorite_" + re.sub(r"\W+", "_", favorite.group(1).strip().lower()).strip("_")
        value = favorite.group(2).strip()
        if entity and value:
            return {"intent": "preference", "entity": entity, "value": value}

    preference = re.search(
        r"\bi\s+(like|love|enjoy|prefer|hate|dislike)\s+([^,.!?;]{1,80})",
        text,
        re.IGNORECASE,
    )
    if preference:
        verb = preference.group(1).lower()
        value = preference.group(2).strip()
        if value:
            return {"intent": "preference", "entity": verb, "value": value}

    return {}


def _is_assistant_identity(entity: str, value: str) -> bool:
    combined = f"{entity} {value}".strip().lower()
    blocked_terms = {
        "marcus",
        "valleymind",
        "valley mind",
        "assistant",
        "ai",
        "bot",
        "character",
        "system",
    }
    return any(re.search(rf"\b{re.escape(term)}\b", combined) for term in blocked_terms)


def _sanitize_memory_fact(envelope: dict, source_message: str) -> dict:
    intent = str(envelope.get("intent") or "").strip()
    entity = str(envelope.get("entity") or "").strip()
    value = str(envelope.get("value") or "").strip()
    if intent not in {"identity", "preference"}:
        return envelope

    source = str(source_message or "").lower()
    explicit_user_fact = any(
        marker in source
        for marker in (
            "my name is",
            "call me",
            "my favorite",
            "i like",
            "i love",
            "i enjoy",
            "i prefer",
            "i hate",
            "i dislike",
        )
    )
    if not explicit_user_fact or _is_assistant_identity(entity, value):
        cleaned = dict(envelope)
        cleaned["intent"] = "general"
        cleaned["entity"] = ""
        cleaned["value"] = ""
        return cleaned
    return envelope


def _filtered_user_identity(identity: dict) -> dict:
    clean = {}
    for key, value in (identity or {}).items():
        if _is_assistant_identity(str(key), str(value)):
            continue
        clean[key] = value
    return clean


def _is_continue_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    return bool(re.search(
        r"\b(continue|resume|carry on|pick up)\b.*\b(last|previous|earlier|conversation|chat)\b",
        text,
    ))


def _is_conversation_recall_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    return bool(re.search(r"\b(what did i ask|what were we talking about|what was our last conversation)\b", text))


def _is_stale_fallback_text(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return lowered in {
        "i can't retrieve live data at the moment. please try again shortly.",
        "i'm having trouble responding right now. please try again.",
        "i don't have your name saved yet.",
    }


def _external_context_fallback_reply(external_context: str) -> str:
    lines = []
    for line in str(external_context or "").splitlines():
        cleaned = line.strip()
        if cleaned.startswith("- "):
            lines.append(cleaned)
        if len(lines) >= 3:
            break
    if not lines:
        return ""
    return "I found live external results, but the language model call failed. Here are the top results I could verify:\n" + "\n".join(lines)


def _clean_live_context_fallback(external_context: str) -> str:
    lines = []
    for line in str(external_context or "").splitlines():
        cleaned = line.strip()
        if cleaned.startswith("- "):
            lines.append(cleaned)
        if len(lines) >= 5:
            break
    if not lines:
        return "I can't retrieve enough relevant live data at the moment. Please try again shortly."
    return "Here are the relevant updates I found:\n" + "\n".join(lines)


class MarcusBrain:
    def __init__(self, memory_file: str = "", behavior_file: str = ""):
        character_name = os.path.basename(os.path.dirname(os.path.abspath(behavior_file))) if behavior_file else "marcus"
        self.profile = load_character_profile(behavior_file, character_name)
        self.behavior_file = behavior_file
        self.behavior = self.profile.raw or {
            "name": self.profile.name,
            "role": self.profile.role,
            "mood": self.profile.mood,
        }
        self.memory = MemorySystem(memory_file=memory_file)
        self.knowledge: dict = {}
        self.knowledge_file = os.path.join(PROJECT_ROOT, "knowledge.json")
        self._model_name: str = ""

        config = get_config()
        if not config.groq_api_key:
            print("[WARNING] GROQ_API_KEY is not set. AI responses will use safe local fallbacks.")

        self.load_knowledge()

    def _get_model(self) -> str:
        if self._model_name:
            return self._model_name
        try:
            name = get_latest_groq_model()
            if name and isinstance(name, str):
                self._model_name = name.strip()
                print(f"[API] Selected Groq model: {self._model_name}")
        except Exception as exc:
            print(f"[ERROR] Failed to resolve model name: {exc}")
        return self._model_name

    def load_behavior(self):
        self.profile = load_character_profile(self.behavior_file, self.profile.key)
        self.behavior = self.profile.raw

    def load_knowledge(self):
        try:
            if os.path.exists(self.knowledge_file):
                with open(self.knowledge_file, "r", encoding="utf-8") as file:
                    self.knowledge = json.load(file)
            else:
                self.knowledge = {}
        except Exception as exc:
            print(f"[ERROR] Failed to load knowledge file: {exc}")
            self.knowledge = {}

    def save_knowledge(self):
        try:
            with open(self.knowledge_file, "w", encoding="utf-8") as file:
                json.dump(self.knowledge, file, indent=4)
        except Exception as exc:
            print(f"[ERROR] Failed to save knowledge file: {exc}")

    def _handle_memory_question(self) -> str:
        try:
            self.memory.reload()
            mem = self.memory.get_full_memory() or {}
            identity = _filtered_user_identity(mem.get("identity", {}))
            prefs = mem.get("preferences", {})
            lines = []
            if identity:
                lines.append("Here's what I know about you:")
                for key, value in identity.items():
                    lines.append(f"  - {key}: {value}")
            if prefs:
                lines.append("Your preferences:")
                for key, value in prefs.items():
                    lines.append(f"  - {key}: {value}")
            if not lines:
                lines.append("I don't have anything saved about you yet.")
            return "\n".join(lines)
        except Exception as exc:
            print(f"[ERROR] _handle_memory_question failed: {exc}")
            return "I couldn't retrieve your memory right now."

    def _handle_name_question(self) -> str:
        try:
            self.memory.reload()
            name = self.memory.get_user_name() or "unknown_user"
            return f"Your name is {name}."
        except Exception as exc:
            print(f"[ERROR] _handle_name_question failed: {exc}")
            return "I couldn't retrieve your name right now."

    def _local_fallback_reply(self, message: str) -> str:
        scripted = self.profile.scripted_response(message)
        if scripted:
            return scripted
        return FALLBACK_RESPONSE

    def _handle_continue_conversation(self, chat_id: str) -> str:
        try:
            history = self.memory.get_chat(chat_id) or []
        except Exception as exc:
            print(f"[ERROR] Failed to load conversation continuation history: {exc}")
            history = []

        previous = [
            item for item in history
            if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
        ]
        if previous and previous[-1].get("role") == "user":
            previous = previous[:-1]
        previous = [
            item for item in previous
            if not (item.get("role") == "assistant" and _is_stale_fallback_text(item.get("content", "")))
        ]
        if not previous:
            return "I don't see an earlier conversation to continue yet."

        recent = previous[-12:]
        last_assistant_index = next(
            (index for index in range(len(recent) - 1, -1, -1) if recent[index].get("role") == "assistant"),
            -1,
        )
        if last_assistant_index >= 0:
            last_assistant = recent[last_assistant_index]
            last_user = next(
                (item for item in reversed(recent[:last_assistant_index]) if item.get("role") == "user"),
                {},
            )
        else:
            last_assistant = {}
            last_user = next((item for item in reversed(recent) if item.get("role") == "user"), {})
        user_text = str(last_user.get("content") or "").strip()
        assistant_text = str(last_assistant.get("content") or "").strip()

        if user_text and assistant_text:
            return (
                "We were talking about this: "
                + user_text
                + "\n\nMy last reply was: "
                + assistant_text
                + "\n\nTell me what part you want to continue from."
            )
        if user_text:
            return "We were talking about this: " + user_text
        return "I found the previous chat. Tell me where you want me to continue."

    def _groq_sports_fallback(self, user_message: str) -> dict:
        config = get_config()
        model = self._get_model()
        if not config.groq_api_key or not model:
            return {
                "reply": "I couldn't confirm the live sports data right now, but I can still talk through the football context if you want.",
                "intent": "general",
                "entity": "",
                "value": "",
            }
        messages = [
            {
                "role": "system",
                "content": (
                    _SYSTEM_PROMPT
                    + "\n\nThe sports API did not return usable live data. "
                    + "Answer from general football knowledge only. Be clear when something is not live-confirmed. "
                    + "Keep it natural as Marcus and do not mention backend errors, APIs, tools, or raw data."
                ),
            },
            {
                "role": "user",
                "content": user_message,
            },
        ]
        try:
            return _parse_envelope(_call_groq(messages, model))
        except Exception as exc:
            print(f"[ERROR] Groq sports fallback failed: {exc}")
            return {
                "reply": "I couldn't confirm the live sports data right now, but I can still discuss the football context from general knowledge.",
                "intent": "general",
                "entity": "",
                "value": "",
            }

    def _think(self, chat_id: str, user_message: str) -> dict:
        try:
            live_history = self.memory.get_chat(chat_id) or []
            if live_history and live_history[-1].get("role") == "user":
                live_history = live_history[:-1]
            recent_live_context = "\n".join(
                str(item.get("content") or "")
                for item in live_history[-6:]
                if item.get("content")
            )
        except Exception:
            recent_live_context = ""

        live_context_query = (
            f"Recent conversation:\n{recent_live_context}\nCurrent question: {user_message}"
            if recent_live_context
            else user_message
        )
        live_type = classify_live_request(user_message)
        if live_type in {"news", "sports", "live"}:
            route = strict_live_context(live_context_query if live_type == "sports" else user_message)
            external_context = route.get("context", "")
            if external_context == LIVE_DATA_UNAVAILABLE or not external_context:
                if (route.get("intent") or live_type) == "sports":
                    return self._groq_sports_fallback(user_message)
                reply = graceful_live_failure(route.get("intent") or live_type)
                return {"reply": reply, "intent": "general", "entity": "", "value": ""}
            config = get_config()
            model = self._get_model()
            if not config.groq_api_key or not model:
                return {
                    "reply": graceful_live_failure(route.get("intent") or live_type),
                    "intent": "general",
                    "entity": "",
                    "value": "",
                }
            messages = [
                {
                    "role": "system",
                    "content": (
                        _SYSTEM_PROMPT
                        + "\n\nYou are answering a live-data question. Use only the provided live context and the user's question. "
                        + "Do not mention APIs, tools, keys, backend logic, or internal fetching. "
                        + "Do not include unrelated fixtures, teams, or articles. If the context does not support a claim, say it is not confirmed. "
                        + "Write a natural Marcus reply. Do not expose raw provider data or JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Question: {user_message}\n\n"
                        f"Relevant live context:\n{external_context}\n\n"
                        "Answer naturally and concisely using only this relevant context."
                    ),
                },
            ]
            try:
                return _parse_envelope(_call_groq(messages, model))
            except Exception as exc:
                print(f"[ERROR] Groq live summary failed. Fallback reason: {exc}")
                return {
                    "reply": graceful_live_failure(route.get("intent") or live_type),
                    "intent": "general",
                    "entity": "",
                    "value": "",
                }

        config = get_config()
        model = self._get_model()
        if not model:
            print("[API] Fallback triggered: no Groq model available")
            fallback = _FALLBACK_ENVELOPE.copy()
            fallback["reply"] = self._local_fallback_reply(user_message)
            return fallback
        if not config.groq_api_key:
            print("[API] Fallback triggered: GROQ_API_KEY is not configured")
            fallback = _FALLBACK_ENVELOPE.copy()
            fallback["reply"] = self._local_fallback_reply(user_message)
            return fallback

        try:
            self.memory.reload()
            long_term = self.memory.get_full_memory() or {}
            history = self.memory.get_chat(chat_id) or []
            prompt_context = {
                "user": self.memory.get_user_name() or "",
                "chat_history": history,
                "system": self.profile.name,
            }
            identity_str = json.dumps(_filtered_user_identity(long_term.get("identity", {})))
            prefs_str = json.dumps(long_term.get("preferences", {}))
        except Exception as exc:
            print(f"[ERROR] Failed to load prompt memory context: {exc}")
            prompt_context = {"user": "", "chat_history": [], "system": self.profile.name}
            history = []
            identity_str = "{}"
            prefs_str = "{}"

        route = classify(user_message)
        system_with_context = (
            _SYSTEM_PROMPT
            + "\n\nCharacter profile:\n"
            + self.profile.to_prompt()
            + "\n\nWhat you already know about this user:\n"
            + f"Prompt context: {json.dumps({'user': prompt_context['user'], 'system': prompt_context['system']})}\n"
            + f"Identity: {identity_str}\n"
            + f"Preferences: {prefs_str}\n"
            + f"Chat history messages loaded: {len(prompt_context['chat_history'])}\n"
            + f"Rule-based intent hint: {route.get('intent')} ({route.get('confidence')})"
        )

        messages = [{"role": "system", "content": system_with_context}]
        recent = history[-20:]
        if recent and recent[-1].get("role") == "user":
            recent = recent[:-1]

        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content and isinstance(content, str):
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})

        try:
            if config.groq_api_key and model:
                return _parse_envelope(_call_groq(messages, model))
            print("[API] Groq skipped: key or model unavailable")
        except requests.exceptions.Timeout:
            print("[ERROR] Groq request timed out. Fallback reason: timeout.")
        except requests.exceptions.ConnectionError:
            print("[ERROR] Groq network connection failed. Fallback reason: connection error.")
        except Exception as exc:
            print(f"[ERROR] Groq call failed. Fallback reason: {exc}")

        fallback = _FALLBACK_ENVELOPE.copy()
        fallback["reply"] = self._local_fallback_reply(user_message)
        print("[API] Local fallback triggered after Groq failure")
        return fallback

    def respond(self, message: str) -> str:
        try:
            message = (message or "").strip()
            if not message:
                return "I didn't catch that - could you say something?"

            chat_id = f"{self.profile.key}_main_chat"
            timestamp = datetime.now().isoformat()
            self.memory.reload()

            try:
                self.memory.add_message(chat_id, "user", message, timestamp)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (user) failed: {exc}")

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
            name_triggers = (
                "what is my name",
                "what's my name",
                "do you know my name",
                "remember my name",
            )
            if any(trigger in lowered for trigger in name_triggers):
                reply = self._handle_name_question()
                try:
                    self.memory.add_message(chat_id, "assistant", reply, timestamp)
                except Exception as exc:
                    print(f"[ERROR] Memory add_message (assistant) failed: {exc}")
                return reply

            if _is_continue_request(message) or _is_conversation_recall_request(message):
                reply = self._handle_continue_conversation(chat_id)
                try:
                    self.memory.add_message(chat_id, "assistant", reply, timestamp)
                except Exception as exc:
                    print(f"[ERROR] Memory add_message (assistant) failed: {exc}")
                return reply

            if any(trigger in lowered for trigger in memory_triggers):
                reply = self._handle_memory_question()
                try:
                    self.memory.add_message(chat_id, "assistant", reply, timestamp)
                except Exception as exc:
                    print(f"[ERROR] Memory add_message (assistant) failed: {exc}")
                return reply

            envelope = self._think(chat_id, message)
            local_memory = _extract_memory_fact(message)
            if local_memory:
                envelope = {**envelope, **local_memory}
            envelope = _sanitize_memory_fact(envelope, message)

            reply = str(envelope.get("reply") or "").strip() or FALLBACK_RESPONSE
            intent = str(envelope.get("intent") or "general").strip()
            entity = str(envelope.get("entity") or "").strip()
            value = str(envelope.get("value") or "").strip()

            if intent == "identity" and entity and value:
                self.memory.remember_identity(entity, value)
            elif intent == "preference" and entity and value:
                self.memory.remember_preference(entity, value)
            elif intent == "memory_question":
                reply = self._handle_memory_question()

            try:
                self.memory.add_message(chat_id, "assistant", reply, timestamp)
            except Exception as exc:
                print(f"[ERROR] Memory add_message (assistant) failed: {exc}")

            return reply

        except Exception as exc:
            print(f"[CRITICAL] Unhandled error in respond(): {exc}")
            return FALLBACK_RESPONSE
