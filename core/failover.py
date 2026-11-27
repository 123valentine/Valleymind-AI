"""Shared retry/failover helpers for generation providers.

Provider names and raw upstream errors stay internal; callers translate any
exhausted chain into a neutral, user-facing message.
"""

from __future__ import annotations

import re
from typing import Any

# 410 means a provider retired/moved a model; the next provider can still serve it.
RETRYABLE_STATUS_CODES = {401, 402, 403, 408, 409, 410, 425, 429, 500, 502, 503, 504}

_RETRYABLE_TEXT = re.compile(
    r"(quota|rate.?limit|throttl|too many requests|timeout|timed? ?out|"
    r"payment required|insufficient (credit|balance|quota)|out of credit|"
    r"unauthori[sz]ed|forbidden|server error|service unavailable|bad gateway|"
    r"overloaded|busy|temporarily unavailable|no credits|billing)",
    re.IGNORECASE,
)


def status_code_of(value: Any) -> int | None:
    """Extract an HTTP status from exceptions, responses, or result objects."""
    for attr in ("status_code", "code", "http_status"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, int):
            return candidate
    if isinstance(value, dict):
        for key in ("status_code", "code", "http_status", "status"):
            candidate = value.get(key)
            if isinstance(candidate, int):
                return candidate
    return None


def is_retryable_provider_error(error: Any) -> bool:
    """True when a provider failure should hand off to the next provider."""
    status = status_code_of(error)
    if status is not None and status in RETRYABLE_STATUS_CODES:
        return True
    if status is not None and 500 <= status <= 599:
        return True

    response = getattr(error, "response", None)
    response_status = status_code_of(response)
    if response_status is not None and response_status in RETRYABLE_STATUS_CODES:
        return True
    if response_status is not None and 500 <= response_status <= 599:
        return True

    text = str(error)
    if _RETRYABLE_TEXT.search(text):
        return True

    # requests/httpx timeouts are not guaranteed to carry a status code.
    class_name = type(error).__name__.lower()
    return any(word in class_name for word in ("timeout", "timeouterror", "connecterror"))


GENERIC_PROVIDER_ERROR = "Generation is temporarily unavailable. Please try again in a moment."
