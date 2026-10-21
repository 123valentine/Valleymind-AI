"""Cost-control facade for ValleyMind — now free-quota-aware.

This module used to be a single blanket kill switch (``PAID_PROVIDERS_ENABLED``,
default off). It is now a thin facade over :mod:`core.quota_manager`, which
decides per provider whether a call is inside its verified free allowance.

Semantics
---------
* ``PAID_PROVIDERS_ENABLED`` is still honoured, but only as an **emergency
  global kill switch**: setting it explicitly to a falsey value (``0/false/no/off``)
  blocks every chargeable provider at once. Leaving it unset no longer disables
  the product — that is now decided per provider by the quota manager.
* A provider that can incur charges and whose free allowance is *unverified*
  (``UNKNOWN``) is blocked: fail closed, never assume free.
* A provider whose free allowance is exhausted is blocked until its documented
  reset / manual re-verification.
* Keyless / free-key providers (Groq, NVIDIA, Gemini, Pollinations, DuckDuckGo,
  Wikipedia, edge-tts, pyttsx3, ffmpeg, browser speech) are unaffected.

Call sites should prefer the provider-aware helpers::

    from core.cost_control import provider_blocked, BLOCKED_MESSAGE
    if provider_blocked("alibaba"):
        return {"error": BLOCKED_MESSAGE}

and record the outcome so the manager can block on exhaustion::

    from core.cost_control import record_success, record_error
    record_success("alibaba")
    record_error("alibaba", status_code=resp.status_code, body=resp.text)
"""

from __future__ import annotations

from core.quota_manager import (  # re-exported public surface
    BLOCKED_MESSAGE,
    QUOTA_MESSAGE,
    UNVERIFIED_MESSAGE,
    ErrorKind,
    QuotaDecision,
    QuotaSource,
    QuotaState,
    block_message as _qm_block_message,
    check,
    global_disabled as _global_disabled,
    is_provider_available,
    mark_exhausted,
    override,
    record_error,
    record_success,
    reset,
    snapshot,
)


class PaidProvidersDisabled(RuntimeError):
    """Raised when a provider is blocked by cost control.

    ``provider`` names the blocked integration for logs only — it is never
    shown to end users (the user sees :data:`BLOCKED_MESSAGE`).
    """

    def __init__(self, provider: str = "", message: str = BLOCKED_MESSAGE):
        super().__init__(message)
        self.provider = provider
        self.user_message = message


def paid_providers_enabled() -> bool:
    """True unless the emergency global kill switch is engaged.

    Note: this no longer means "every provider may spend" — per-provider quota
    still applies. It only reports whether the blanket switch is *not* off.
    """
    return not _global_disabled()


def paid_providers_disabled(provider: str = "") -> bool:
    """Backward-compatible gate.

    * ``provider`` given  → True when that provider is currently blocked.
    * ``provider`` omitted → True only when the emergency global kill switch is on.
    """
    if provider:
        return not is_provider_available(provider)
    return _global_disabled()


def provider_blocked(provider: str, model: str | None = None) -> bool:
    """True when *provider* must not be called right now (quota/kill switch)."""
    return not is_provider_available(provider, model)


def require_paid(provider: str = "") -> None:
    """Raise :class:`PaidProvidersDisabled` when *provider* is blocked.

    With no provider, raises only when the global kill switch is engaged.
    """
    if paid_providers_disabled(provider):
        raise PaidProvidersDisabled(provider)


def require_provider(provider: str, model: str | None = None) -> None:
    """Raise :class:`PaidProvidersDisabled` when *provider* is blocked."""
    if provider_blocked(provider, model):
        raise PaidProvidersDisabled(provider, _qm_block_message(provider))


def block_message(provider: str = "") -> str:
    """User-facing message for a blocked provider (never leaks internals)."""
    return _qm_block_message(provider)
