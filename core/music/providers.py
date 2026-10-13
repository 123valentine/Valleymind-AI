"""MusicProvider abstraction — the honest boundary between the Music
workstation and whatever actually renders audio bytes.

The workstation's creative layers (lyrics, arrangement, production plans,
reference analysis) are produced by the LLM pipeline and always work. Turning
those plans into *server-side audio* (a full mix, separated stems, a mastered
file, an AI vocal stem) requires an external renderer. Those capabilities live
behind a provider:

  * ``LocalProvider``   — the current honest engine. Everything creative works;
    audio is rendered by the browser's Web-Audio engine (as today) or reported
    as "render client-side" — never faked server-side.
  * ``UnavailableProvider`` — a provider id was requested via env but not
    implemented/configured. Every audio op raises ``ProviderUnavailable`` and
    the API tells the user exactly what is missing.

Providers are selected from environment, never hard-coded:
  MUSIC_PROVIDER (default "local")   — which engine to serve audio ops with
  MUSIC_API_KEY                       — credential for the configured provider

No secret is ever read here beyond env lookup; nothing is logged.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Optional


class MusicError(Exception):
    """Raised for any Music engine failure with a machine-readable code."""

    def __init__(self, message: str, *, code: str = "music_error"):
        super().__init__(message)
        self.code = code


class ProviderUnavailable(MusicError):
    """A requested audio capability is not configured on the active provider.

    This is a *normal, expected* state — the UI renders a clear "provider
    unavailable" card and still keeps every creative feature usable."""

    def __init__(self, message: str, *, capability: str = ""):
        super().__init__(message, code="provider_unavailable")
        self.capability = capability


def _env(*names: str) -> str:
    for n in names:
        val = (os.getenv(n) or "").strip()
        if val:
            return val
    return ""


class MusicProvider(ABC):
    """Base class for a music audio renderer.

    Subclasses implement only the ops they truly support; the base raises
    ``ProviderUnavailable`` for everything else so a half-configured provider
    can never silently pretend success."""

    provider_id = "base"
    provider_label = "Base provider"

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @abstractmethod
    def capabilities(self) -> dict:
        """Which audio operations this provider can really do.

        Returns::
            {
              "server_audio": bool,   # True when this provider renders audio
              "generate": bool,       # full-track generation
              "remix": bool,
              "extend": bool,
              "stems": bool,          # stem separation
              "master": bool,         # AI/mastering
              "vocal_ai": bool,       # AI vocal/singing into the mix
            }
        """
        raise NotImplementedError

    def status(self) -> dict:
        """Honest human-readable provider status for the UI / API."""
        caps = self.capabilities()
        cap_list = sorted(k for k, v in caps.items() if v and k != "server_audio")
        return {
            "provider_id": self.provider_id,
            "label": self.provider_label,
            "configured": True,
            "server_audio": bool(caps.get("server_audio")),
            "available_capabilities": cap_list,
            "message": (
                "Server-side audio rendering is unavailable in this build. "
                "Lyrics, arrangements, plans and reference analysis work now; "
                "audio is rendered by your browser or awaits a configured provider."
                if not caps.get("server_audio")
                else "Server-side audio rendering is available."
            ),
        }

    # ------------------------------------------------------------------ #
    # Audio operations — base raises ProviderUnavailable
    # ------------------------------------------------------------------ #

    def render_track(self, ctx: dict) -> dict:
        raise ProviderUnavailable(
            f"{self.provider_label} cannot render a full track here.",
            capability="generate",
        )

    def separate_stems(self, ctx: dict) -> dict:
        raise ProviderUnavailable(
            f"{self.provider_label} cannot separate stems here.",
            capability="stems",
        )

    def master_track(self, ctx: dict) -> dict:
        raise ProviderUnavailable(
            f"{self.provider_label} cannot master audio here.",
            capability="master",
        )

    def extend_track(self, ctx: dict) -> dict:
        raise ProviderUnavailable(
            f"{self.provider_label} cannot extend tracks here.",
            capability="extend",
        )

    def remix_track(self, ctx: dict) -> dict:
        raise ProviderUnavailable(
            f"{self.provider_label} cannot remix tracks here.",
            capability="remix",
        )

    def vocal_produce(self, ctx: dict) -> dict:
        raise ProviderUnavailable(
            f"{self.provider_label} cannot produce AI vocals here.",
            capability="vocal_ai",
        )


class LocalProvider(MusicProvider):
    """The current, honest engine.

    Creative work (LLM packages + plans) is served by the engine, and the
    browser renders audio from those plans with its own Web-Audio synthesis —
    the same mechanism the existing Sing-with-AI flow already uses. This
    provider does not allocate server audio bytes, and it says so explicitly
    rather than ever faking a WAV."""

    provider_id = "local"
    provider_label = "ValleyMind Local Producer"

    def capabilities(self) -> dict:
        return {
            "server_audio": False,
            "generate": False,
            "remix": False,
            "extend": False,
            "stems": False,
            "master": False,
            "vocal_ai": False,
        }

    def render_track(self, ctx: dict) -> dict:
        return {
            "rendered": False,
            "provider": "local",
            "detail": "client",
            "audio_url": "",
            "note": (
                "No server-side audio provider is configured. Render this plan "
                "in your browser (ValleyMind Studio Web Audio) or configure "
                "MUSIC_PROVIDER in the environment."
            ),
        }


class UnavailableProvider(MusicProvider):
    """A provider id was configured that this build does not implement."""

    provider_id = "unavailable"
    provider_label = "Unavailable provider"

    def __init__(self, requested: str = ""):
        self.requested = requested or "external"
        self.provider_label = f"Provider '{self.requested}'"
        super().__init__()

    def capabilities(self) -> dict:
        return {
            "server_audio": False,
            "generate": False,
            "remix": False,
            "extend": False,
            "stems": False,
            "master": False,
            "vocal_ai": False,
        }

    def _raise(self, capability: str) -> None:
        raise ProviderUnavailable(
            "MUSIC_PROVIDER is set to "
            f"'{self.requested}', but this build only implements the 'local' "
            "provider. Set MUSIC_PROVIDER=local (or unset it) to use the "
            "built-in producer.",
            capability=capability,
        )

    def render_track(self, ctx: dict) -> dict:
        self._raise("generate")

    def separate_stems(self, ctx: dict) -> dict:
        self._raise("stems")

    def master_track(self, ctx: dict) -> dict:
        self._raise("master")

    def extend_track(self, ctx: dict) -> dict:
        self._raise("extend")

    def remix_track(self, ctx: dict) -> dict:
        self._raise("remix")

    def vocal_produce(self, ctx: dict) -> dict:
        self._raise("vocal_ai")

    def status(self) -> dict:
        return {
            **super().status(),
            "configured": False,
            "message": (
                f"MUSIC_PROVIDER is set to '{self.requested}', which is not "
                "implemented in this build. Using the local producer instead."
            ),
        }


def configured_provider_id() -> str:
    """Normalised MUSIC_PROVIDER value ('' for 'local'/unset)."""
    return (os.getenv("MUSIC_PROVIDER") or "").strip().lower() or "local"


def music_api_key() -> str:
    return _env("MUSIC_API_KEY", "SUNO_API_KEY", "UDIO_API_KEY")


def get_provider() -> MusicProvider:
    """Active provider from environment — never raises, always returns one."""
    pid = configured_provider_id()
    if pid in ("local", "client", "studio"):
        return LocalProvider()
    return UnavailableProvider(pid)


def provider_status() -> dict:
    """Status payload for the /api/music/capabilities endpoint."""
    provider = get_provider()
    caps = provider.capabilities()
    return {
        "provider": {
            "id": provider.provider_id,
            "label": provider.provider_label,
            "status": provider.status(),
        },
        "creative": {
            "generate_package": True,      # LLM lyrics/arrangement producers
            "production_plan": True,       # Sing-with-AI plan producer
            "reference_analysis": True,
            "elena_copilot": True,
            "lyrics_lab": True,
            "ai_sing": True,               # existing /api/music/sing flow
        },
        "audio": {
            "client_render": True,         # browser Web-Audio synthesis
            "server_audio_api": bool(getattr(provider, "capabilities", lambda: {})()["server_audio"]),
            "stems": bool(caps.get("stems")),
            "master": bool(caps.get("master")),
            "vocal_ai": bool(caps.get("vocal_ai")),
        },
        "server_audio_configured": bool(caps.get("server_audio")),
        "notes": provider.status().get("message", ""),
    }


def capabilities_for(provider: MusicProvider) -> dict:
    return provider.capabilities()