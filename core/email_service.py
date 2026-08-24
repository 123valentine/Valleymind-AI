"""Centralized email for ValleyMind — powered by the Resend HTTP API.

ALL email logic lives here (nothing scattered through app.py). Two families of
senders share one transport:

  TRANSACTIONAL (return ``bool`` so auth routes can report honest delivery):
      send_verification_email, send_otp_email, send_password_reset_email,
      send_security_email

  UNIVERSAL / PROMOTIONAL (return ``{"success": bool, "id" | "error": ...}``):
      send_email            — generic sender for any route
      send_promotional_email — branded newsletters / product updates / campaigns
      send_promotional_batch — one call, up to 100 recipients via Resend batch

Nothing sensitive (API key, verification code, token, or raw provider
response) is ever logged.

Testing vs production:
    With no verified domain, Resend's sandbox sender ``onboarding@resend.dev``
    can only deliver to your OWN Resend account address — perfect for dev.
    For production, verify your domain at https://resend.com/domains and set:
        EMAIL_FROM="ValleyMind <noreply@yourdomain.com>"

Configuration (env vars):
    RESEND_API_KEY          required — create at https://resend.com/api-keys
    EMAIL_FROM              e.g. 'ValleyMind <onboarding@resend.dev>'
    EMAIL_FROM_NAME         display name used if EMAIL_FROM lacks one (default: ValleyMind AI)
    EMAIL_REPLY_TO          optional Reply-To address
    EMAIL_UNSUBSCRIBE_URL   optional — adds List-Unsubscribe header + footer link to promotional mail
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

import httpx

BRAND = "ValleyMind AI"
SUPPORT_EMAIL = "supportvalleymind@gmail.com"
ACCENT = "#00c2b8"
BG = "#0d0d1a"

RESEND_ENDPOINT = "https://api.resend.com/emails"
RESEND_BATCH_ENDPOINT = "https://api.resend.com/emails/batch"
# Hard ceiling for the whole API round-trip. OTP/verification sends are made
# synchronously inside HTTP requests, so this must stay well below the
# frontend's 30s fetch timeout.
RESEND_TOTAL_TIMEOUT = 8.0


def _cfg() -> dict:
    """Snapshot of current configuration. Values are never logged by callers."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_name = os.getenv("EMAIL_FROM_NAME", "").strip() or BRAND
    raw_from = os.getenv("EMAIL_FROM", "").strip()
    if not raw_from:
        # Sandbox-friendly default; swap in a verified-domain address for prod.
        raw_from = f"{from_name} <onboarding@resend.dev>"
    elif "<" not in raw_from and "@" in raw_from:
        # Bare address given — attach the display name.
        raw_from = f"{from_name} <{raw_from}>"
    return {
        "api_key": api_key,
        "from_name": from_name,
        "sender": raw_from,
        "reply_to": os.getenv("EMAIL_REPLY_TO", "").strip(),
        "unsubscribe_url": os.getenv("EMAIL_UNSUBSCRIBE_URL", "").strip(),
    }


def available() -> bool:
    """True when Resend is configured enough to attempt a send."""
    c = _cfg()
    return bool(c["api_key"] and c["sender"])


# ── Transport ───────────────────────────────────────────────────────────────

def _deliver(payload: Dict[str, Any], *, request_id: str = "") -> Dict[str, Any]:
    """POST one message to the Resend API. Never raises; never logs secrets.

    Returns ``{"success": True, "id": ...}`` on acceptance or
    ``{"success": False, "error": ...}`` otherwise.
    """
    rid = request_id or uuid.uuid4().hex[:12]
    t0 = time.monotonic()

    def _elapsed() -> str:
        return f"{(time.monotonic() - t0) * 1000:.0f}ms"

    c = _cfg()
    if not available():
        missing = [k for k in ("api_key", "sender") if not c[k]]
        print(f"[EMAIL][rid={rid}] config_incomplete missing={missing} elapsed={_elapsed()}")
        return {"success": False, "error": "not_configured"}

    headers = {
        "Authorization": f"Bearer {c['api_key']}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(RESEND_ENDPOINT, json=payload, headers=headers,
                          timeout=RESEND_TOTAL_TIMEOUT)
    except Exception as exc:
        print(f"[EMAIL][rid={rid}] network_fail {type(exc).__name__} elapsed={_elapsed()}")
        return {"success": False, "error": type(exc).__name__}

    if resp.status_code in (200, 201):
        try:
            data = resp.json()
        except Exception:
            data = {}
        print(f"[EMAIL][rid={rid}] send_ok elapsed={_elapsed()}")
        return {"success": True, "id": data.get("id")}

    # Provider rejected the message. Log only status + short reason — never
    # the API key or full payload.
    try:
        err_name = str(resp.json().get("name") or "")
    except Exception:
        err_name = ""
    print(f"[EMAIL][rid={rid}] send_fail status={resp.status_code} name={err_name} elapsed={_elapsed()}")
    return {"success": False, "error": f"resend_status_{resp.status_code}"}


def _payload(to: str, subject: str, html_body: str, text_body: str = "",
             *, tags: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build a Resend send payload with the shared From/Reply-To config."""
    c = _cfg()
    params: Dict[str, Any] = {
        "from": c["sender"],
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    if text_body:
        params["text"] = text_body
    if c["reply_to"]:
        params["reply_to"] = c["reply_to"]
    if tags:
        params["headers"] = {f"X-ValleyMind-{k}": v for k, v in tags.items()}
    return params


# ── Branded template ────────────────────────────────────────────────────────

def _shell(heading: str, body_html: str, preheader: str = "",
           *, footer_note: str = "", unsubscribe_url: str = "") -> str:
    """A simple, mobile-friendly, inline-styled shell shared by all templates."""
    unsub_html = ""
    if unsubscribe_url:
        unsub_html = (f'<p style="margin:12px 0 0;font-family:Arial,Helvetica,sans-serif;'
                      f'font-size:11px;color:#aab2c0;">'
                      f'<a href="{unsubscribe_url}" style="color:#aab2c0;">Unsubscribe</a>'
                      f'&nbsp;from marketing emails.</p>')
    extra_note = f"<br>{footer_note}" if footer_note else ""
    return f"""\
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f8;">
<span style="display:none!important;opacity:0;color:#f4f6f8;">{preheader}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e6e9ef;">
    <tr><td style="background:{BG};padding:22px 28px;">
      <span style="color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:20px;font-weight:800;letter-spacing:-.02em;">Valley<span style="color:{ACCENT};">Mind</span> AI</span>
    </td></tr>
    <tr><td style="padding:30px 28px 8px;">
      <h1 style="margin:0 0 14px;font-family:Arial,Helvetica,sans-serif;font-size:20px;color:#0d0d1a;">{heading}</h1>
      <div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#3a4151;">{body_html}</div>
    </td></tr>
    <tr><td style="padding:22px 28px 28px;">
      <hr style="border:none;border-top:1px solid #e6e9ef;margin:0 0 16px;">
      <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.6;color:#8a93a6;">
        This is an automated message from {BRAND}. Need help? Contact
        <a href="mailto:{SUPPORT_EMAIL}" style="color:{ACCENT};text-decoration:none;">{SUPPORT_EMAIL}</a>.<br>
        For your security, {BRAND} will never ask for your password or verification codes.{extra_note}
      </p>{unsub_html}
    </td></tr>
  </table>
  <p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#aab2c0;margin:16px 0 0;">&copy; {BRAND}</p>
</td></tr></table></body></html>"""


def _button(label: str, href: str) -> str:
    return (f'<a href="{href}" style="display:inline-block;background:{ACCENT};color:#00201e;'
            f'font-weight:700;text-decoration:none;padding:12px 22px;border-radius:10px;'
            f'font-family:Arial,Helvetica,sans-serif;font-size:15px;">{label}</a>')


def _code_box(code: str) -> str:
    return (f'<div style="font-family:\'Courier New\',monospace;font-size:30px;font-weight:700;'
            f'letter-spacing:8px;color:#0d0d1a;background:#f4f6f8;border:1px solid #e6e9ef;'
            f'border-radius:10px;padding:16px;text-align:center;margin:6px 0;">{code}</div>')


# ── Universal / promotional senders (dict results) ──────────────────────────

def send_email(to_email: str, subject: str, html_content: str,
               text_content: str = None) -> Dict[str, Any]:
    """Universal email sender (OTP, promotional, notifications, etc.).

    Returns ``{"success": True, "id": "<resend-id>"}`` or
    ``{"success": False, "error": "..."}``. Never raises.
    """
    payload = _payload(to_email, subject, html_content, text_content or "")
    return _deliver(payload)


def send_promotional_email(to_email: str, title: str, body_html: str,
                           *, preheader: str = "",
                           unsubscribe_url: str = "") -> Dict[str, Any]:
    """Newsletters, product updates, promotions, notifications etc.

    ``body_html`` is wrapped in the ValleyMind branded shell; pass
    ``unsubscribe_url`` (or configure EMAIL_UNSUBSCRIBE_URL) for marketing
    sends so recipients get a List-Unsubscribe header + footer link.
    """
    c = _cfg()
    unsub = unsubscribe_url or c["unsubscribe_url"]
    html = _shell(title, body_html, preheader or title, footer_note="You are receiving this because you have a ValleyMind AI account.", unsubscribe_url=unsub)
    payload = _payload(to_email, title, html, "")
    if unsub:
        payload.setdefault("headers", {})["List-Unsubscribe"] = f"<{unsub}>"
    payload.setdefault("headers", {})["X-ValleyMind-Type"] = "promotional"
    return _deliver(payload)


def send_promotional_batch(recipients: Iterable[str], title: str, body_html: str,
                           *, preheader: str = "",
                           unsubscribe_url: str = "") -> Dict[str, Any]:
    """Send one promotional message to up to 100 recipients per call.

    Uses Resend's batch endpoint (one API call). Returns a summary dict;
    per-recipient ids land under ``ids`` when everything was accepted.
    """
    c = _cfg()
    unsub = unsubscribe_url or c["unsubscribe_url"]
    html = _shell(title, body_html, preheader or title, footer_note="You are receiving this because you have a ValleyMind AI account.", unsubscribe_url=unsub)
    batch: List[Dict[str, Any]] = []
    for addr in recipients:
        addr = str(addr or "").strip()
        if not addr:
            continue
        item = {"from": c["sender"], "to": [addr], "subject": title, "html": html}
        if unsub:
            item.setdefault("headers", {})["List-Unsubscribe"] = f"<{unsub}>"
        item.setdefault("headers", {})["X-ValleyMind-Type"] = "promotional"
        batch.append(item)

    rid = uuid.uuid4().hex[:12]
    if not batch:
        return {"success": False, "error": "no_recipients"}
    if len(batch) > 100:
        return {"success": False, "error": "batch_too_large"}

    headers = {"Authorization": f"Bearer {c['api_key']}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(RESEND_BATCH_ENDPOINT, json=batch, headers=headers,
                          timeout=RESEND_TOTAL_TIMEOUT)
    except Exception as exc:
        print(f"[EMAIL][rid={rid}] batch_network_fail {type(exc).__name__}")
        return {"success": False, "error": type(exc).__name__}
    if resp.status_code in (200, 201):
        try:
            data = resp.json().get("data") or []
        except Exception:
            data = []
        return {"success": True, "sent": len(batch), "ids": [d.get("id") for d in data]}
    return {"success": False, "error": f"resend_status_{resp.status_code}"}


# ── Public transactional senders (bool results — legacy contract) ───────────

def send_verification_email(to: str, code: str, link: str, minutes: int = 30,
                            *, request_id: str = "") -> bool:
    body = (
        f"<p>Welcome to {BRAND}!</p>"
        f"<p>Your {BRAND} verification code is:</p>{_code_box(code)}"
        f"<p style='margin:18px 0;text-align:center;'>{_button('Verify my email', link)}</p>"
        f"<p style='color:#8a93a6;font-size:13px;'>Or paste this code in the app. "
        f"This code expires in {minutes} minutes.</p>"
        f"<p style='color:#8a93a6;font-size:13px;'>If you didn't create a {BRAND} account, you can ignore this email.</p>"
    )
    text = (f"Welcome to {BRAND}!\n\n"
            f"Your verification code is: {code}\n"
            f"Magic link: {link}\n\n"
            f"This code expires in {minutes} minutes.\n"
            f"If you didn't sign up, ignore this email.\n"
            f"Support: {SUPPORT_EMAIL}")
    result = _deliver(_payload(to, f"Your {BRAND} verification code",
                               _shell("Your verification code", body,
                                      "Your ValleyMind AI verification code."),
                               text,
                               tags={"Type": "transactional"}),
                      request_id=request_id)
    return bool(result.get("success"))


def send_password_reset_email(to: str, link: str, minutes: int = 30,
                              *, request_id: str = "") -> bool:
    body = (
        f"<p>We received a request to reset your {BRAND} password.</p>"
        f"<p style='margin:18px 0;text-align:center;'>{_button('Reset my password', link)}</p>"
        f"<p style='color:#8a93a6;font-size:13px;'>This link expires in {minutes} minutes and can be used once. "
        f"If you didn't request this, you can safely ignore this email — your password will not change.</p>"
    )
    text = (f"Reset your {BRAND} password:\n{link}\n\n"
            f"This link expires in {minutes} minutes and can be used once. "
            f"If you didn't request it, ignore this email.\nSupport: {SUPPORT_EMAIL}")
    result = _deliver(_payload(to, f"Reset your {BRAND} password",
                               _shell("Reset your password", body, "Reset your ValleyMind AI password."),
                               text,
                               tags={"Type": "transactional"}),
                      request_id=request_id)
    return bool(result.get("success"))


_OTP_PURPOSE_LABEL = {
    "email_verification": "verify your email",
    "login_verification": "sign in",
    "password_reset": "reset your password",
    "security_confirmation": "confirm a security-sensitive action",
}


def send_otp_email(to: str, code: str, purpose: str = "login_verification",
                   minutes: int = 10, *, request_id: str = "") -> bool:
    """One-time passcode mail. Called by /api/auth/otp/request and usable
    directly as ``send_otp_email(addr, "123456")``."""
    what = _OTP_PURPOSE_LABEL.get(purpose, "continue")
    body = (
        f"<p>Your {BRAND} verification code to {what} is:</p>{_code_box(code)}"
        f"<p style='color:#8a93a6;font-size:13px;'>This code expires in {minutes} minutes and can be used once. "
        f"Never share it with anyone — {BRAND} staff will never ask for it.</p>"
    )
    text = (f"Your {BRAND} verification code to {what}: {code}\n\n"
            f"Expires in {minutes} minutes. Never share this code.\nSupport: {SUPPORT_EMAIL}")
    result = _deliver(_payload(to, f"Your {BRAND} verification code",
                               _shell("Your verification code", body, "Your one-time ValleyMind AI code."),
                               text,
                               tags={"Type": "transactional"}),
                      request_id=request_id)
    return bool(result.get("success"))


def send_security_email(to: str, event: str, detail: str = "",
                        *, request_id: str = "") -> bool:
    detail_html = f"<p style='color:#8a93a6;font-size:13px;'>{detail}</p>" if detail else ""
    body = (
        f"<p>The following change was just made to your {BRAND} account:</p>"
        f"<p style='font-weight:700;color:#0d0d1a;'>{event}</p>{detail_html}"
        f"<p style='color:#8a93a6;font-size:13px;'>If this was you, no action is needed. "
        f"If you don't recognise this, contact <a href='mailto:{SUPPORT_EMAIL}' style='color:{ACCENT};'>{SUPPORT_EMAIL}</a> "
        f"right away and reset your password.</p>"
    )
    text = (f"{BRAND} security notice: {event}\n{detail}\n\n"
            f"If this wasn't you, contact {SUPPORT_EMAIL} and reset your password.")
    result = _deliver(_payload(to, f"{BRAND} security alert: {event}",
                               _shell("Security alert", body, event),
                               text,
                               tags={"Type": "transactional"}),
                      request_id=request_id)
    return bool(result.get("success"))
