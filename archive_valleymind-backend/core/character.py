import importlib
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.config import PROJECT_ROOT


@dataclass
class CharacterProfile:
    key: str
    name: str
    role: str = "AI Assistant"
    mood: str = "calm"
    created_by: str = ""
    description: str = ""
    system_prompt: str = ""
    response_module: str = ""
    response_function: str = ""
    voice: str = ""
    raw: dict = field(default_factory=dict)
    _scripted: Optional[Callable[[str], str]] = None

    def scripted_response(self, message: str) -> str:
        if not self._scripted:
            return ""
        try:
            return str(self._scripted(message) or "").strip()
        except Exception as exc:
            print(f"[ERROR] Scripted response failed for {self.key}: {exc}")
            return ""

    def to_prompt(self) -> str:
        parts = [
            f"Character name: {self.name}",
            f"Created by: {self.created_by}" if self.created_by else "",
            f"Description: {self.description}" if self.description else "",
            f"Role: {self.role}",
            f"Mood/style: {self.mood}",
        ]
        if self.system_prompt:
            parts.append(self.system_prompt)
        return "\n".join(part for part in parts if part)


def load_character_profile(behavior_file: str = "", character_name: str = "marcus") -> CharacterProfile:
    key = (character_name or "marcus").lower().strip()
    behavior = {}

    if behavior_file and os.path.exists(behavior_file):
        try:
            with open(behavior_file, "r", encoding="utf-8") as file:
                loaded = json.load(file)
            if isinstance(loaded, dict):
                behavior = loaded
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[ERROR] Failed to load behavior file '{behavior_file}': {exc}")

    name = str(behavior.get("name") or key.title())
    profile = CharacterProfile(
        key=key,
        name=name,
        role=str(behavior.get("role") or "AI Assistant"),
        mood=str(behavior.get("mood") or "calm"),
        created_by=str(behavior.get("created_by") or ""),
        description=str(behavior.get("description") or ""),
        system_prompt=str(behavior.get("system_prompt") or ""),
        response_module=str(behavior.get("response_module") or ""),
        response_function=str(behavior.get("response_function") or ""),
        voice=str(behavior.get("voice") or ""),
        raw=behavior,
    )

    if profile.response_module and profile.response_function:
        try:
            module_name = profile.response_module
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            module = importlib.import_module(module_name)
            profile._scripted = getattr(module, profile.response_function)
        except Exception as exc:
            print(f"[WARNING] Could not connect scripted responses for {key}: {exc}")

    return profile
