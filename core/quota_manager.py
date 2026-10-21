"""Free-quota-aware provider gating for ValleyMind.

This replaces the old blanket ``PAID_PROVIDERS_ENABLED`` kill switch with a
per-provider decision engine. The goal: keep using a provider **while it is
inside its verified free allowance**, block it the moment that allowance is
exhausted, and never silently fall back to a provider that could charge money.

Design rules (non-negotiable):

  * No quota limit or reset date is ever invented. Anything not verified in
    ``core/free_quota_limits.json`` / a ``FREE_QUOTA_*`` env var is treated as
    ``UNKNOWN``.
  * A provider that **can incur charges** (``can_charge``) and whose quota is
    ``UNKNOWN`` is *blocked* — fail closed, do not assume free.
  * Providers with no key and no billing surface (Pollinations, DuckDuckGo,
    Wikipedia, edge-tts, pyttsx3, ffmpeg, browser speech) are always allowed.
  * We never issue a request merely to probe quota.

State is persisted to ``memory_data/quota_state.json`` (override with
``QUOTA_STATE_PATH``) so a restart does not resurrect an exhausted provider.

Public API (see ``core.cost_control`` for the thin facade the rest of the app
imports)::

    check(provider, model=None, units=1) -> QuotaDecision
    is_provider_available(provider, model=None) -> bool
    block_message(provider) -> str
    record_success(provider, units=0)
    record_error(provider, status_code=None, body="", exc=None, kind=None)
    mark_exhausted(provider, reason="")
    override(provider, state, source=None, note="")
    reset(provider)
    snapshot() -> dict
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


_CORE_DIR = Path(__file__).resolve().parent
_DEFAULT_STATE_PATH = _CORE_DIR.parent / "memory_data" / "quota_state.json"
_LIMITS_PATH = _CORE_DIR / "free_quota_limits.json"

# Default rate-limit backoff when the provider did not send Retry-After.
_DEFAULT_BACKOFF_SECONDS = 60.0

# Provider error → how long to stop calling after a rate limit we can't time.
_RATE_LIMIT_BACKOFF_CAP = 3600.0


class QuotaSource(str, Enum):
    """How confident we are in a provider's current quota state."""

    PROVIDER_CONFIRMED = "provider_confirmed"  # operator verified from the dashboard
    LOCAL_ESTIMATE = "local_estimate"          # learned from observed calls
    UNKNOWN = "unknown"                        # nothing verified


class QuotaState(str, Enum):
    AVAILABLE = "available"
    EXHAUSTED = "exhausted"
    UNKNOWN = "unknown"
    INVALID_KEY = "invalid_key"
    RATE_LIMITED = "rate_limited"


class ErrorKind(str, Enum):
    """Normalised classification of a provider failure."""

    INVALID_KEY = "invalid_key"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    MISSING_MODEL = "missing_model"
    UNKNOWN = "unknown"


QUOTA_MESSAGE = (
    "This feature is temporarily unavailable because its free API quota "
    "has been exhausted."
)
UNVERIFIED_MESSAGE = (
    "This feature is temporarily unavailable because its free API quota "
    "could not be verified."
)
BLOCKED_MESSAGE = QUOTA_MESSAGE  # backward-compatible alias


@dataclass(frozen=True)
class FreeTierSpec:
    """Static facts about a provider's free-tier posture.

    ``free_allowance`` is deliberately descriptive — it must never contain a
    fabricated number. Real limits live in the operator config.
    """

    key: str
    label: str
    can_charge: bool
    default_state: QuotaState
    note: str = ""
    reset_hint: str = ""


# ── Provider registry ───────────────────────────────────────────────────────
#
# ``can_charge=True`` means misuse can produce a real bill. Those default to
# UNKNOWN (blocked) until the operator confirms a free allowance. Keyless /
# free-key providers that only rate-limit default to AVAILABLE.

_SPECS: dict[str, FreeTierSpec] = {
    # Alibaba DashScope — text / image / video t2v / i2v / TTS / VL. A single
    # account is billed across all of them, so they share one 'alibaba' key.
    "alibaba": FreeTierSpec(
        "alibaba", "Alibaba Model Studio (DashScope)", can_charge=True,
        default_state=QuotaState.UNKNOWN,
        note="Text/image/video/TTS/vision all bill the same account.",
    ),
    "fal": FreeTierSpec(
        "fal", "fal.ai video", can_charge=True,
        default_state=QuotaState.UNKNOWN,
        note="Pay-per-generation video queue.",
    ),
    "opencode": FreeTierSpec(
        "opencode", "OpenCode Zen (AI Builder)", can_charge=True,
        default_state=QuotaState.UNKNOWN,
    ),
    "resend": FreeTierSpec(
        "resend", "Resend email", can_charge=True,
        default_state=QuotaState.UNKNOWN,
    ),
    "openrouter": FreeTierSpec(
        "openrouter", "OpenRouter (text + embeddings)", can_charge=True,
        default_state=QuotaState.UNKNOWN,
        note="Free models exist, but the embedding model used here is paid.",
    ),
    "pinecone": FreeTierSpec(
        "pinecone", "Pinecone vector memory", can_charge=True,
        default_state=QuotaState.UNKNOWN,
    ),
    "tinyfish": FreeTierSpec(
        "tinyfish", "TinyFish search + fetch", can_charge=True,
        default_state=QuotaState.UNKNOWN,
    ),
    "currents": FreeTierSpec(
        "currents", "Currents news API", can_charge=True,
        default_state=QuotaState.UNKNOWN,
    ),
    "newscatcher": FreeTierSpec(
        "newscatcher", "NewsCatcher API", can_charge=True,
        default_state=QuotaState.UNKNOWN,
    ),
    "newsapi": FreeTierSpec(
        "newsapi", "NewsAPI (newsapi.org)", can_charge=True,
        default_state=QuotaState.UNKNOWN,
    ),
    "apisports": FreeTierSpec(
        "apisports", "API-Sports football", can_charge=True,
        default_state=QuotaState.UNKNOWN,
    ),
    # Free-key LLM providers: a key can rate-limit or 402 but cannot silently
    # run up a charge, so we let them run and only block on observed exhaustion.
    "groq": FreeTierSpec("groq", "Groq", can_charge=False, default_state=QuotaState.AVAILABLE),
    "nvidia": FreeTierSpec("nvidia", "NVIDIA NIM", can_charge=False, default_state=QuotaState.AVAILABLE),
    "gemini": FreeTierSpec("gemini", "Google Gemini", can_charge=False, default_state=QuotaState.AVAILABLE),
    # Keyless / free surfaces — always available.
    "pollinations": FreeTierSpec("pollinations", "Pollinations image", can_charge=False, default_state=QuotaState.AVAILABLE),
    "duckduckgo": FreeTierSpec("duckduckgo", "DuckDuckGo Lite", can_charge=False, default_state=QuotaState.AVAILABLE),
    "wikipedia": FreeTierSpec("wikipedia", "Wikipedia", can_charge=False, default_state=QuotaState.AVAILABLE),
    "edge_tts": FreeTierSpec("edge_tts", "Edge TTS", can_charge=False, default_state=QuotaState.AVAILABLE),
    "pyttsx3": FreeTierSpec("pyttsx3", "pyttsx3 local TTS", can_charge=False, default_state=QuotaState.AVAILABLE),
    "ffmpeg": FreeTierSpec("ffmpeg", "Local ffmpeg", can_charge=False, default_state=QuotaState.AVAILABLE),
    "browser_tts": FreeTierSpec("browser_tts", "Browser speech", can_charge=False, default_state=QuotaState.AVAILABLE),
}

# Aliases so call sites can use the natural integration name.
_ALIASES = {
    "dashscope": "alibaba",
    "alibaba_studio": "alibaba",
    "ali": "alibaba",
    "qwen": "alibaba",
    "fal.ai": "fal",
    "fal_ai": "fal",
    "opencode_zen": "opencode",
    "ai_builder": "opencode",
    "builder": "opencode",
    "email": "resend",
    "news": "newsapi",
    "sports": "apisports",
    "api_sports": "apisports",
    "ddg": "duckduckgo",
    "wiki": "wikipedia",
    "edge-tts": "edge_tts",
    "browser": "browser_tts",
}


def _canonical(provider: str) -> str:
    key = (provider or "").strip().lower()
    return _ALIASES.get(key, key)


def spec_for(provider: str) -> Optional[FreeTierSpec]:
    return _SPECS.get(_canonical(provider))


# ── Error classification ────────────────────────────────────────────────────

_BALANCE_MARKERS = (
    "insufficient", "insufficient_quota", "quota exceeded", "exceeded your current quota",
    "out of credits", "no credits", "credit limit", "arrears", "overdue",
    "balance", "not enough", "billing", "payment required", "no payment method",
    "account is in arrears", "recharge",
)
_RATE_MARKERS = (
    "rate limit", "rate_limit", "ratelimit", "too many requests", "throttl",
    "requests per", "tpm", "rpm", "slow down",
)
_INVALID_KEY_MARKERS = (
    "invalid api key", "invalid_api_key", "invalid key", "api key not valid",
    "unauthorized", "authentication", "invalid bearer", "incorrect api key",
    "no api key", "key not found", "permission denied", "forbidden",
)
_MODEL_MARKERS = (
    "model not found", "does not exist", "unknown model", "no such model",
    "model_not_found", "invalid model",
)


def classify_error(status_code: Optional[int] = None, body: str = "",
                   exc: Optional[BaseException] = None) -> ErrorKind:
    """Map an HTTP status / body / exception to an :class:`ErrorKind`.

    Order matters: an explicit balance/credit signal wins over a bare 402/429,
    because DashScope and friends report exhausted free quota as 400/429 with a
    "balance"/"quota" message rather than a clean 402.
    """
    text = (body or "") + " " + (str(exc) if exc is not None else "")
    low = text.lower()

    if any(m in low for m in _BALANCE_MARKERS):
        return ErrorKind.QUOTA_EXHAUSTED
    if status_code == 402:
        return ErrorKind.QUOTA_EXHAUSTED

    if status_code == 429 or any(m in low for m in _RATE_MARKERS):
        return ErrorKind.RATE_LIMITED

    if status_code in (401, 403) or any(m in low for m in _INVALID_KEY_MARKERS):
        return ErrorKind.INVALID_KEY

    if status_code == 404 or any(m in low for m in _MODEL_MARKERS):
        return ErrorKind.MISSING_MODEL

    if status_code in (408, 409, 425, 500, 502, 503, 504):
        return ErrorKind.NETWORK
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ErrorKind.NETWORK
    if exc is not None:
        name = type(exc).__name__.lower()
        if any(t in name for t in ("timeout", "connection", "socket", "ssl")):
            return ErrorKind.NETWORK
        low_exc = str(exc).lower()
        if any(t in low_exc for t in ("timed out", "timeout", "connection", "temporarily unavailable", "unreachable")):
            return ErrorKind.NETWORK

    return ErrorKind.UNKNOWN


# ── Runtime record + decision ───────────────────────────────────────────────

@dataclass
class QuotaRecord:
    state: str = ""
    source: str = ""
    units: float = 0.0
    last_success: float = 0.0
    last_error: str = ""
    error_kind: str = ""
    backoff_until: float = 0.0
    reset_at: float = 0.0
    note: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "source": self.source,
            "units": self.units,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "error_kind": self.error_kind,
            "backoff_until": self.backoff_until,
            "reset_at": self.reset_at,
            "note": self.note,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QuotaRecord":
        rec = cls()
        for k in ("state", "source", "last_error", "error_kind", "note"):
            setattr(rec, k, str(data.get(k, "") or ""))
        for k in ("units", "last_success", "backoff_until", "reset_at", "updated_at"):
            try:
                setattr(rec, k, float(data.get(k, 0) or 0))
            except (TypeError, ValueError):
                setattr(rec, k, 0.0)
        return rec


@dataclass
class QuotaDecision:
    allowed: bool
    provider: str
    state: QuotaState
    source: QuotaSource
    message: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def _now() -> float:
    return time.time()


def _parse_time(value: Any) -> float:
    """Accept epoch seconds or an ISO-8601 string. Returns 0.0 on anything else."""
    if value in (None, "", 0, "0"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return 0.0


def _state_path() -> Path:
    override = os.getenv("QUOTA_STATE_PATH", "").strip()
    return Path(override) if override else _DEFAULT_STATE_PATH


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def global_kill_switch() -> bool:
    """True only when ``PAID_PROVIDERS_ENABLED`` is explicitly set to a falsey
    token. Unset/empty no longer means "off" — that is now per-provider."""
    raw = os.getenv("PAID_PROVIDERS_ENABLED", None)
    if raw is None:
        return False
    return raw.strip().lower() in ("0", "false", "no", "off")


class QuotaManager:
    """Thread-safe, process-wide free-quota gate."""

    def __init__(self, state_path: Optional[Path] = None, limits_path: Optional[Path] = None):
        self._lock = threading.RLock()
        self._state_path = Path(state_path) if state_path else None
        self._limits_path = Path(limits_path) if limits_path else _LIMITS_PATH
        self._records: dict[str, QuotaRecord] = {}
        self._overrides: dict[str, dict] = {}
        self._loaded = False

    # ── persistence ──────────────────────────────────────────────────

    @property
    def state_path(self) -> Path:
        return self._state_path or _state_path()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._load_overrides()
        self._load_state()

    def _load_overrides(self) -> None:
        """Operator-verified config: free_quota_limits.json plus FREE_QUOTA_* env."""
        overrides: dict[str, dict] = {}
        try:
            if self._limits_path.exists():
                data = json.loads(self._limits_path.read_text(encoding="utf-8"))
                raw = data.get("providers", data) if isinstance(data, dict) else {}
                for name, entry in (raw or {}).items():
                    if name.startswith("_") or not isinstance(entry, dict):
                        continue
                    overrides[_canonical(name)] = entry
        except Exception as exc:  # never let bad config break the app
            print(f"[QUOTA] could not read {self._limits_path}: {exc}")

        for provider in _SPECS:
            env = os.getenv(f"FREE_QUOTA_{provider.upper()}", "").strip().lower()
            entry = dict(overrides.get(provider, {}))
            if env:
                if env in QuotaState._value2member_map_:
                    entry["state"] = env
                    entry["source"] = QuotaSource.PROVIDER_CONFIRMED.value
            reset_env = os.getenv(f"FREE_QUOTA_{provider.upper()}_RESET_AT", "").strip()
            if reset_env:
                entry["reset_at"] = reset_env
            if entry:
                overrides[provider] = entry
        self._overrides = overrides

    def _load_state(self) -> None:
        try:
            path = self.state_path
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                for name, entry in (data.get("providers") or {}).items():
                    self._records[_canonical(name)] = QuotaRecord.from_dict(entry)
        except Exception as exc:
            print(f"[QUOTA] could not read state {self.state_path}: {exc}")

    def _save_state(self) -> None:
        try:
            path = self.state_path
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "providers": {name: rec.to_dict() for name, rec in self._records.items()},
            }
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[QUOTA] could not persist state: {exc}")

    def reload(self) -> None:
        with self._lock:
            self._records.clear()
            self._overrides.clear()
            self._loaded = False
            self._ensure_loaded()

    # ── effective state ──────────────────────────────────────────────

    def _effective(self, key: str) -> tuple[QuotaState, QuotaSource, QuotaRecord]:
        rec = self._records.get(key, QuotaRecord())
        spec = _SPECS[key]

        state = spec.default_state
        source = QuotaSource.UNKNOWN

        if rec.state in QuotaState._value2member_map_:
            state = QuotaState(rec.state)
            source = QuotaSource(rec.source) if rec.source in QuotaSource._value2member_map_ else QuotaSource.LOCAL_ESTIMATE

        # Reset has priority over a persisted EXHAUSTED/RATE_LIMITED.
        if state in (QuotaState.EXHAUSTED, QuotaState.INVALID_KEY, QuotaState.RATE_LIMITED):
            if rec.reset_at and _now() >= rec.reset_at:
                state = spec.default_state
                source = QuotaSource.LOCAL_ESTIMATE

        override = self._overrides.get(key)
        if override:
            ov_state = str(override.get("state", "")).strip().lower()
            ov_reset = _parse_time(override.get("reset_at"))
            if ov_reset:
                rec.reset_at = ov_reset
                if state in (QuotaState.EXHAUSTED, QuotaState.INVALID_KEY, QuotaState.RATE_LIMITED) \
                        and _now() >= ov_reset:
                    state = spec.default_state
            if ov_state in QuotaState._value2member_map_:
                state = QuotaState(ov_state)
            if override.get("source") in QuotaSource._value2member_map_:
                source = QuotaSource(override["source"])
            elif ov_state in QuotaState._value2member_map_:
                source = QuotaSource.PROVIDER_CONFIRMED

        return state, source, rec

    # ── public API ───────────────────────────────────────────────────

    def check(self, provider: str, model: Optional[str] = None,
              units: float = 1.0) -> QuotaDecision:
        """Decide whether a call to *provider* may proceed right now."""
        key = _canonical(provider)
        if key not in _SPECS:
            return QuotaDecision(
                allowed=False, provider=key, state=QuotaState.UNKNOWN,
                source=QuotaSource.UNKNOWN, message=UNVERIFIED_MESSAGE,
                reason="unregistered provider",
            )

        # OpenRouter models suffixed ':free' are free by provider design — they
        # are the one documented case where an affordable zero-cost model exists
        # on a provider whose key can otherwise charge.
        if key == "openrouter" and model and str(model).strip().lower().endswith(":free"):
            return QuotaDecision(allowed=True, provider=key, state=QuotaState.AVAILABLE,
                                 source=QuotaSource.PROVIDER_CONFIRMED, reason="free model")

        with self._lock:
            self._ensure_loaded()

            if global_kill_switch():
                return QuotaDecision(
                    allowed=False, provider=key, state=QuotaState.UNKNOWN,
                    source=QuotaSource.PROVIDER_CONFIRMED, message=QUOTA_MESSAGE,
                    reason="global kill switch (PAID_PROVIDERS_ENABLED=false)",
                )

            state, source, rec = self._effective(key)
            spec = _SPECS[key]

            if state == QuotaState.RATE_LIMITED:
                if rec.backoff_until and _now() < rec.backoff_until:
                    return QuotaDecision(
                        allowed=False, provider=key, state=state, source=source,
                        message=QUOTA_MESSAGE,
                        reason=f"rate limited; retry after {int(rec.backoff_until - _now())}s",
                    )
                # Backoff elapsed — treat as available again.
                rec.state = QuotaState.AVAILABLE.value
                rec.backoff_until = 0.0
                rec.updated_at = _now()
                state = QuotaState.AVAILABLE
                self._save_state()

            if state == QuotaState.AVAILABLE:
                return QuotaDecision(allowed=True, provider=key, state=state, source=source)

            if state == QuotaState.EXHAUSTED:
                return QuotaDecision(allowed=False, provider=key, state=state, source=source,
                                     message=QUOTA_MESSAGE, reason="free quota exhausted")

            if state == QuotaState.INVALID_KEY:
                return QuotaDecision(allowed=False, provider=key, state=state, source=source,
                                     message=UNVERIFIED_MESSAGE, reason="credential rejected")

            # UNKNOWN
            return QuotaDecision(
                allowed=False, provider=key,
                state=QuotaState.UNKNOWN if spec.can_charge else QuotaState.AVAILABLE,
                source=source, message=UNVERIFIED_MESSAGE,
                reason="free allowance not verified",
            )

    def is_available(self, provider: str, model: Optional[str] = None) -> bool:
        return self.check(provider, model).allowed

    def message(self, provider: str) -> str:
        decision = self.check(provider)
        return decision.message or QUOTA_MESSAGE

    def record_success(self, provider: str, units: float = 0.0) -> None:
        key = _canonical(provider)
        if key not in _SPECS:
            return
        with self._lock:
            self._ensure_loaded()
            rec = self._records.setdefault(key, QuotaRecord())
            rec.state = QuotaState.AVAILABLE.value
            if not rec.source or rec.source == QuotaSource.UNKNOWN.value:
                rec.source = QuotaSource.LOCAL_ESTIMATE.value
            if units:
                rec.units = (rec.units or 0.0) + float(units)
            rec.last_success = _now()
            rec.last_error = ""
            rec.error_kind = ""
            rec.backoff_until = 0.0
            rec.updated_at = _now()
            self._save_state()

    def record_error(self, provider: str, status_code: Optional[int] = None,
                     body: str = "", exc: Optional[BaseException] = None,
                     kind: Optional[ErrorKind] = None,
                     retry_after: Optional[float] = None) -> ErrorKind:
        """Record a provider failure and degrade its state accordingly."""
        key = _canonical(provider)
        resolved = kind or classify_error(status_code, body, exc)
        if key not in _SPECS:
            return resolved
        with self._lock:
            self._ensure_loaded()
            rec = self._records.setdefault(key, QuotaRecord())
            rec.last_error = (body or (str(exc) if exc else ""))[:300]
            rec.error_kind = resolved.value
            rec.updated_at = _now()

            if resolved == ErrorKind.QUOTA_EXHAUSTED:
                rec.state = QuotaState.EXHAUSTED.value
                rec.source = QuotaSource.LOCAL_ESTIMATE.value
            elif resolved == ErrorKind.INVALID_KEY:
                rec.state = QuotaState.INVALID_KEY.value
                rec.source = QuotaSource.LOCAL_ESTIMATE.value
            elif resolved == ErrorKind.RATE_LIMITED:
                wait = retry_after if retry_after is not None else _DEFAULT_BACKOFF_SECONDS
                wait = max(1.0, min(float(wait), _RATE_LIMIT_BACKOFF_CAP))
                rec.state = QuotaState.RATE_LIMITED.value
                rec.source = QuotaSource.LOCAL_ESTIMATE.value
                rec.backoff_until = _now() + wait
            # NETWORK / MISSING_MODEL / UNKNOWN are transient — keep current
            # state so a flaky request cannot silently disable a provider.
            self._save_state()
        return resolved

    def mark_exhausted(self, provider: str, reason: str = "") -> None:
        key = _canonical(provider)
        with self._lock:
            self._ensure_loaded()
            rec = self._records.setdefault(key, QuotaRecord())
            rec.state = QuotaState.EXHAUSTED.value
            rec.source = QuotaSource.LOCAL_ESTIMATE.value
            rec.note = reason or rec.note
            rec.updated_at = _now()
            self._save_state()

    def override(self, provider: str, state: QuotaState,
                 source: QuotaSource = QuotaSource.PROVIDER_CONFIRMED,
                 note: str = "") -> None:
        key = _canonical(provider)
        with self._lock:
            self._ensure_loaded()
            rec = self._records.setdefault(key, QuotaRecord())
            rec.state = state.value
            rec.source = source.value
            rec.backoff_until = 0.0
            if note:
                rec.note = note
            rec.updated_at = _now()
            self._save_state()

    def reset(self, provider: str) -> None:
        key = _canonical(provider)
        with self._lock:
            self._ensure_loaded()
            self._records.pop(key, None)
            self._save_state()

    def snapshot(self) -> dict:
        with self._lock:
            self._ensure_loaded()
            out = {}
            for key, spec in _SPECS.items():
                state, source, rec = self._effective(key)
                out[key] = {
                    "label": spec.label,
                    "can_charge": spec.can_charge,
                    "state": state.value,
                    "source": source.value,
                    "units": rec.units,
                    "last_success": rec.last_success,
                    "last_error": rec.last_error,
                    "error_kind": rec.error_kind,
                    "backoff_until": rec.backoff_until,
                    "reset_at": rec.reset_at,
                    "note": rec.note,
                }
            return out


# ── Module-level singleton + thin functional API ────────────────────────────

_manager = QuotaManager()


def manager() -> QuotaManager:
    return _manager


def check(provider: str, model: Optional[str] = None, units: float = 1.0) -> QuotaDecision:
    return _manager.check(provider, model, units)


def is_provider_available(provider: str, model: Optional[str] = None) -> bool:
    return _manager.is_available(provider, model)


def block_message(provider: str = "") -> str:
    if provider:
        return _manager.message(provider)
    return QUOTA_MESSAGE


def global_disabled() -> bool:
    return global_kill_switch()


def record_success(provider: str, units: float = 0.0) -> None:
    _manager.record_success(provider, units)


def record_error(provider: str, status_code: Optional[int] = None, body: str = "",
                 exc: Optional[BaseException] = None, kind: Optional[ErrorKind] = None,
                 retry_after: Optional[float] = None) -> ErrorKind:
    return _manager.record_error(provider, status_code, body, exc, kind, retry_after)


def mark_exhausted(provider: str, reason: str = "") -> None:
    _manager.mark_exhausted(provider, reason)


def override(provider: str, state: QuotaState,
             source: QuotaSource = QuotaSource.PROVIDER_CONFIRMED, note: str = "") -> None:
    _manager.override(provider, state, source, note)


def reset(provider: str) -> None:
    _manager.reset(provider)


def snapshot() -> dict:
    return _manager.snapshot()
