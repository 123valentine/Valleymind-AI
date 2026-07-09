"""Internal Provider Manager — routes generation tasks to the best available provider.

This module is for internal infrastructure only.  Provider names, API vendors,
failover logic, and quota information must NEVER appear in the user-facing UI.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Capability(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    EMBEDDINGS = "embeddings"
    AUDIO = "audio"
    SPEECH = "speech"
    CODE = "code"


@dataclass
class ProviderResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    provider_name: str = ""
    latency_ms: float = 0.0


@dataclass
class RoutingLogEntry:
    capability: str
    provider_name: str
    success: bool
    latency_ms: float
    error: str | None = None
    timestamp: float = 0.0


class BaseProvider(ABC):
    """Abstract base for every generation provider."""

    name: str = ""
    capability: Capability | None = None
    health: str = "unknown"  # "healthy" | "degraded" | "down"
    priority: int = 100       # lower = tried first
    quota_remaining: int | None = None

    def __init__(self) -> None:
        self._consecutive_failures = 0
        self._total_requests = 0
        self._total_latency_ms = 0.0

    @abstractmethod
    def execute(self, **kwargs: Any) -> ProviderResult:
        ...

    @property
    def avg_latency_ms(self) -> float:
        if self._total_requests == 0:
            return 0.0
        return self._total_latency_ms / self._total_requests

    def record(self, result: ProviderResult) -> None:
        self._total_requests += 1
        self._total_latency_ms += result.latency_ms
        if result.success:
            self._consecutive_failures = 0
            self.health = "healthy"
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self.health = "degraded"
            if self._consecutive_failures >= 10:
                self.health = "down"


# ──────────────────────────────────────────────────────────────
# Image providers
# ──────────────────────────────────────────────────────────────

class PollinationsImageProvider(BaseProvider):
    name = "Pollinations"
    capability = Capability.IMAGE
    priority = 10
    health = "healthy"

    def execute(self, **kwargs: Any) -> ProviderResult:
        from core.image_gen import generate_image as pollinations_gen

        start = time.perf_counter()
        try:
            result = pollinations_gen(
                prompt=kwargs.get("prompt", ""),
                api_key=kwargs.get("api_key"),
                enhance=kwargs.get("enhance", True),
                reference_image=kwargs.get("reference_image"),
            )
            elapsed = (time.perf_counter() - start) * 1000
            return ProviderResult(
                success=True,
                data=result,
                provider_name=self.name,
                latency_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return ProviderResult(
                success=False,
                error=str(exc),
                provider_name=self.name,
                latency_ms=elapsed,
            )


class GeminiImageProvider(BaseProvider):
    """Stub — ready for future Gemini image-gen integration."""
    name = "Gemini"
    capability = Capability.IMAGE
    priority = 20
    health = "healthy"

    def execute(self, **kwargs: Any) -> ProviderResult:
        return ProviderResult(
            success=False,
            error="Gemini image provider not yet implemented",
            provider_name=self.name,
        )


# ──────────────────────────────────────────────────────────────
# Video providers (stubs for future use)
# ──────────────────────────────────────────────────────────────

class PlaceholderVideoProvider(BaseProvider):
    name = "PlaceholderVideo"
    capability = Capability.VIDEO
    priority = 10
    health = "healthy"

    def execute(self, **kwargs: Any) -> ProviderResult:
        return ProviderResult(
            success=False,
            error="Video generation not yet available",
            provider_name=self.name,
        )


# ──────────────────────────────────────────────────────────────
# Provider Manager
# ──────────────────────────────────────────────────────────────

class ProviderManager:
    """Routes generation requests to the best available provider with automatic
    fallback on failure.  Internal-only; never expose provider details to users."""

    def __init__(self) -> None:
        self._providers: dict[Capability, list[BaseProvider]] = {
            Capability.TEXT: [],
            Capability.IMAGE: [
                PollinationsImageProvider(),
                GeminiImageProvider(),
            ],
            Capability.VIDEO: [
                PlaceholderVideoProvider(),
            ],
            Capability.EMBEDDINGS: [],
            Capability.AUDIO: [],
            Capability.SPEECH: [],
            Capability.CODE: [],
        }

        # Auto-discover and register third-party providers
        self._register_discovered("core.provider_alibaba")

        self.routing_log: list[RoutingLogEntry] = []
        self._max_log_entries = 500

    # ── Internal auto-discovery ────────────────────────────────

    def _register_discovered(self, module_path: str) -> None:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            if not hasattr(mod, "discover"):
                return
            providers = mod.discover()
            for p in providers:
                if p.capability in self._providers:
                    self._providers[p.capability].append(p)
        except Exception as exc:
            print(f"[PROVIDER] Discovery from {module_path} skipped: {exc}")

    # ── public API used by app.py ─────────────────────────────

    def execute(self, capability: Capability, **kwargs: Any) -> ProviderResult:
        """Try every registered provider for *capability* in priority order until
        one succeeds.  Returns the first successful result; if all fail, returns
        the last failure."""
        providers = sorted(
            self._providers.get(capability, []),
            key=lambda p: (p.priority, p.avg_latency_ms),
        )

        if not providers:
            return ProviderResult(
                success=False,
                error=f"No providers registered for {capability.value}",
            )

        last_result: ProviderResult | None = None
        for provider in providers:
            if provider.health == "down":
                self._log(provider, False, 0.0, "provider marked down")
                continue

            result = provider.execute(**kwargs)
            provider.record(result)
            self._log(provider, result.success, result.latency_ms, result.error)

            if result.success:
                return result

            last_result = result

        return ProviderResult(
            success=False,
            error=(last_result.error if last_result else "All providers failed"),
        )

    # ── developer-mode queries (never user-facing) ────────────

    def provider_status(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for capability, providers in self._providers.items():
            for p in providers:
                entries.append({
                    "name": p.name,
                    "capability": capability.value,
                    "health": p.health,
                    "priority": p.priority,
                    "quota_remaining": p.quota_remaining,
                    "avg_latency_ms": round(p.avg_latency_ms, 1),
                    "total_requests": p._total_requests,
                })
        return entries

    def recent_routing_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {
                "capability": e.capability,
                "provider": e.provider_name,
                "success": e.success,
                "latency_ms": round(e.latency_ms, 1),
                "error": e.error,
                "timestamp": e.timestamp,
            }
            for e in self.routing_log[-limit:]
        ]

    def metrics(self) -> dict[str, Any]:
        total = sum(
            p._total_requests
            for providers in self._providers.values()
            for p in providers
        )
        failed = sum(
            p._consecutive_failures
            for providers in self._providers.values()
            for p in providers
        )
        return {
            "total_requests": total,
            "consecutive_failures": failed,
            "providers_healthy": sum(
                1
                for providers in self._providers.values()
                for p in providers
                if p.health == "healthy"
            ),
            "providers_degraded": sum(
                1
                for providers in self._providers.values()
                for p in providers
                if p.health == "degraded"
            ),
            "providers_down": sum(
                1
                for providers in self._providers.values()
                for p in providers
                if p.health == "down"
            ),
        }

    # ── internals ─────────────────────────────────────────────

    def _log(self, provider: BaseProvider, success: bool, latency_ms: float,
             error: str | None = None) -> None:
        entry = RoutingLogEntry(
            capability=provider.capability.value if provider.capability else "",
            provider_name=provider.name,
            success=success,
            latency_ms=latency_ms,
            error=error,
            timestamp=time.time(),
        )
        self.routing_log.append(entry)
        if len(self.routing_log) > self._max_log_entries:
            self.routing_log = self.routing_log[-self._max_log_entries:]


# Module-level singleton
_manager: ProviderManager | None = None


def get_manager() -> ProviderManager:
    global _manager
    if _manager is None:
        _manager = ProviderManager()
    return _manager
