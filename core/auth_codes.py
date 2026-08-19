"""Secure single-use codes & tokens for email verification, OTP and resets.

Pure functions over a plain user-record dict — the caller (app.py) owns loading,
saving and locking. Design rules that the whole email/auth system relies on:

* Only the **sha256 hash** of a code/token is ever stored on the record.
* The plaintext value is returned exactly once (to be emailed) and must never be
  logged or returned through an API response.
* Every challenge has an expiry, an attempt counter, and is single-use
  (cleared on success). Comparisons are timing-safe.

Challenge state is namespaced by ``kind`` (e.g. "verify", "otp") so several
independent challenges can live on one record:

    <kind>_code_hash, <kind>_token_hash, <kind>_expires,
    <kind>_attempts, <kind>_purpose, <kind>_sent_at
"""
from __future__ import annotations

import hashlib
import secrets
import time

_SUFFIXES = ("code_hash", "token_hash", "expires", "attempts", "purpose", "sent_at")


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def new_numeric_code(digits: int = 6) -> str:
    """A cryptographically secure zero-padded numeric code."""
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def new_url_token() -> str:
    return secrets.token_urlsafe(32)


def clear(record: dict, kind: str) -> None:
    for suffix in _SUFFIXES:
        record.pop(f"{kind}_{suffix}", None)


def set_challenge(record: dict, kind: str, *, ttl_seconds: int,
                  with_token: bool = False, purpose: str | None = None,
                  digits: int = 6):
    """Issue a fresh challenge on ``record`` (invalidating any previous one of the
    same kind). Returns ``(code, token)`` in plaintext — send them, never store or
    log them. ``token`` is None unless ``with_token`` is set."""
    code = new_numeric_code(digits)
    record[f"{kind}_code_hash"] = _hash(code)
    record[f"{kind}_expires"] = time.time() + ttl_seconds
    record[f"{kind}_attempts"] = 0
    record[f"{kind}_sent_at"] = time.time()
    if purpose is not None:
        record[f"{kind}_purpose"] = purpose
    token = None
    if with_token:
        token = new_url_token()
        record[f"{kind}_token_hash"] = _hash(token)
    return code, token


def seconds_since_sent(record: dict, kind: str) -> float:
    return time.time() - float(record.get(f"{kind}_sent_at") or 0)


def cooldown_active(record: dict, kind: str, cooldown_seconds: int) -> bool:
    """True if a new challenge was sent within the cooldown window (resend guard)."""
    return bool(record.get(f"{kind}_sent_at")) and \
        seconds_since_sent(record, kind) < cooldown_seconds


def has_active(record: dict, kind: str) -> bool:
    return bool(record.get(f"{kind}_code_hash") or record.get(f"{kind}_token_hash"))


def check_code(record: dict, kind: str, code: str, *, max_attempts: int = 5,
               purpose: str | None = None):
    """Validate a numeric code. Returns ``(ok, reason)`` where reason is one of
    ok / expired / locked / invalid. Single-use: clears the challenge on success.
    Increments the attempt counter on a wrong-but-present code."""
    import logging as _log
    _ck = f"{kind}_code_hash"
    stored = record.get(_ck)
    if not stored:
        _log.warning("[OTP] check_code kind=%s sub=no_challenge", kind)
        return False, "invalid"
    if purpose is not None and record.get(f"{kind}_purpose") not in (None, purpose):
        _log.warning("[OTP] check_code kind=%s sub=purpose_mismatch expected=%s got=%s",
                     kind, purpose, record.get(f"{kind}_purpose"))
        return False, "invalid"
    if time.time() > float(record.get(f"{kind}_expires") or 0):
        clear(record, kind)
        _log.warning("[OTP] check_code kind=%s sub=expired", kind)
        return False, "expired"
    if int(record.get(f"{kind}_attempts") or 0) >= max_attempts:
        clear(record, kind)
        _log.warning("[OTP] check_code kind=%s sub=locked attempts=%s max=%s",
                     kind, record.get(f"{kind}_attempts"), max_attempts)
        return False, "locked"
    if not secrets.compare_digest(str(stored), _hash(str(code or "").strip())):
        record[f"{kind}_attempts"] = int(record.get(f"{kind}_attempts") or 0) + 1
        _log.warning("[OTP] check_code kind=%s sub=wrong_code attempts_now=%s",
                     kind, record.get(f"{kind}_attempts"))
        return False, "invalid"
    clear(record, kind)  # single use
    _log.info("[OTP] check_code kind=%s sub=ok", kind)
    return True, "ok"


def check_token(record: dict, kind: str, token: str):
    """Validate an opaque URL token (for magic links). Same contract as
    check_code but without an attempt counter (tokens are high-entropy)."""
    stored = record.get(f"{kind}_token_hash")
    if not stored:
        return False, "invalid"
    if time.time() > float(record.get(f"{kind}_expires") or 0):
        clear(record, kind)
        return False, "expired"
    if not secrets.compare_digest(str(stored), _hash(str(token or "").strip())):
        return False, "invalid"
    clear(record, kind)  # single use
    return True, "ok"
