"""Centralized transactional email for ValleyMind.

ALL SMTP logic lives here (nothing scattered through app.py). Every public
function returns a plain ``bool`` — callers never see a provider error, and the
user-facing layer shows a single generic message on failure. Nothing sensitive
(SMTP password, verification code, token, or raw provider response) is ever
logged.

This module is TRANSACTIONAL only (verification, reset, OTP, security alerts).
It deliberately has no list-management / promotional capability — marketing mail
must live in a separate system with its own consent + unsubscribe handling.

Configuration (env vars — the same names app.py already used):
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM
    MAIL_REPLY_TO   (optional — only added as a Reply-To header when set)
"""
from __future__ import annotations

import os
import smtplib
import ssl
import socket
import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

BRAND = "ValleyMind AI"
SUPPORT_EMAIL = "supportvalleymind@gmail.com"
ACCENT = "#00c2b8"
BG = "#0d0d1a"

# Hard ceiling for the entire SMTP operation (connection + STARTTLS + auth + send).
SMTP_TOTAL_TIMEOUT = 10


def _cfg() -> dict:
    user = os.getenv("SMTP_USER", "").strip()
    raw_port = str(os.getenv("SMTP_PORT", "587")).strip()
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
        "port": int(raw_port) if raw_port.isdigit() else 587,
        "user": user,
        "password": os.getenv("SMTP_PASS", "").replace(" ", ""),
        "sender": os.getenv("MAIL_FROM", "").strip() or user,
        "reply_to": os.getenv("MAIL_REPLY_TO", "").strip(),
    }


def available() -> bool:
    """True when SMTP is configured enough to attempt a send."""
    c = _cfg()
    return bool(c["user"] and c["password"] and c["sender"])


def _send(to: str, subject: str, text_body: str, html_body: str,
          *, request_id: str = "") -> bool:
    """Low-level multipart send. Never raises; never logs secrets/codes/bodies.

    Every SMTP stage has an explicit socket timeout so the call can never block
    indefinitely.  A ``request_id`` (auto-generated if omitted) is threaded
    through structured timing logs so slow sends can be diagnosed without
    exposing credentials or message content.
    """
    rid = request_id or uuid.uuid4().hex[:12]
    t0 = time.monotonic()

    def _elapsed() -> str:
        return f"{(time.monotonic() - t0) * 1000:.0f}ms"

    c = _cfg()
    if not (c["user"] and c["password"] and c["sender"] and to):
        missing = [k for k in ("user", "password", "sender") if not c[k]]
        if not to:
            missing.append("to")
        print(f"[EMAIL][rid={rid}] config_incomplete missing={missing} elapsed={_elapsed()}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((BRAND, c["sender"]))
    msg["To"] = to
    if c["reply_to"]:
        msg["Reply-To"] = c["reply_to"]
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    host = c["host"]
    port = c["port"]

    # ── Connection ─────────────────────────────────────────────
    print(f"[EMAIL][rid={rid}] connect_start host={host}:{port}")
    try:
        server = smtplib.SMTP(host, port, timeout=SMTP_TOTAL_TIMEOUT)
        server.timeout = SMTP_TOTAL_TIMEOUT  # enforce on all subsequent ops
    except Exception as exc:
        print(f"[EMAIL][rid={rid}] connect_fail {type(exc).__name__} elapsed={_elapsed()}")
        return False
    print(f"[EMAIL][rid={rid}] connect_ok elapsed={_elapsed()}")

    # ── STARTTLS ───────────────────────────────────────────────
    try:
        server.starttls(context=ssl.create_default_context())
        # Re-enforce timeout after TLS negotiation (some impls reset it)
        server.timeout = SMTP_TOTAL_TIMEOUT
    except Exception as exc:
        print(f"[EMAIL][rid={rid}] starttls_fail {type(exc).__name__} elapsed={_elapsed()}")
        _safe_quit(server)
        return False
    print(f"[EMAIL][rid={rid}] starttls_ok elapsed={_elapsed()}")

    # ── Authentication ─────────────────────────────────────────
    try:
        server.login(c["user"], c["password"])
    except smtplib.SMTPAuthenticationError as exc:
        print(f"[EMAIL][rid={rid}] auth_fail smtp_code={exc.smtp_code} elapsed={_elapsed()}")
        _safe_quit(server)
        return False
    except Exception as exc:
        print(f"[EMAIL][rid={rid}] auth_fail {type(exc).__name__} elapsed={_elapsed()}")
        _safe_quit(server)
        return False
    print(f"[EMAIL][rid={rid}] auth_ok elapsed={_elapsed()}")

    # ── Send ───────────────────────────────────────────────────
    try:
        server.sendmail(c["sender"], [to], msg.as_string())
    except smtplib.SMTPRecipientsRefused as exc:
        print(f"[EMAIL][rid={rid}] send_fail recipient_refused elapsed={_elapsed()}")
        _safe_quit(server)
        return False
    except smtplib.SMTPSenderRefused as exc:
        print(f"[EMAIL][rid={rid}] send_fail sender_refused code={exc.smtp_code} elapsed={_elapsed()}")
        _safe_quit(server)
        return False
    except smtplib.SMTPDataError as exc:
        print(f"[EMAIL][rid={rid}] send_fail data_error code={exc.smtp_code} elapsed={_elapsed()}")
        _safe_quit(server)
        return False
    except Exception as exc:
        print(f"[EMAIL][rid={rid}] send_fail {type(exc).__name__} elapsed={_elapsed()}")
        _safe_quit(server)
        return False
    print(f"[EMAIL][rid={rid}] send_ok elapsed={_elapsed()}")

    _safe_quit(server)
    print(f"[EMAIL][rid={rid}] done total={_elapsed()}")
    return True


def _safe_quit(server: smtplib.SMTP) -> None:
    """Close the SMTP connection, ignoring errors."""
    try:
        server.quit()
    except Exception:
        try:
            server.close()
        except Exception:
            pass


# ── Branded template ────────────────────────────────────────────────────────

def _shell(heading: str, body_html: str, preheader: str = "") -> str:
    """A simple, mobile-friendly, inline-styled shell shared by all templates."""
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
        For your security, {BRAND} will never ask for your password or verification codes.
      </p>
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


# ── Public transactional senders ────────────────────────────────────────────

def send_verification_email(to: str, code: str, link: str, minutes: int = 30,
                            *, request_id: str = "") -> bool:
    body = (
        f"<p>Welcome to {BRAND}!</p>"
        f"<p>Your {BRAND} verification code is:</p>{_code_box(code)}"
        f"<p style='color:#8a93a6;font-size:13px;'>Enter this 6-digit code in the app to verify your email. "
        f"This code expires in {minutes} minutes.</p>"
        f"<p style='color:#8a93a6;font-size:13px;'>If you didn't create a {BRAND} account, you can ignore this email.</p>"
    )
    text = (f"Welcome to {BRAND}!\n\n"
            f"Your verification code is: {code}\n\n"
            f"Enter this 6-digit code in the app to verify your email.\n"
            f"This code expires in {minutes} minutes.\n"
            f"If you didn't sign up, ignore this email.\n"
            f"Support: {SUPPORT_EMAIL}")
    return _send(to, f"Your {BRAND} verification code", text,
                 _shell("Your verification code", body,
                        "Your ValleyMind AI verification code."),
                 request_id=request_id)


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
    return _send(to, f"Reset your {BRAND} password", text,
                 _shell("Reset your password", body, "Reset your ValleyMind AI password."),
                 request_id=request_id)


_OTP_PURPOSE_LABEL = {
    "email_verification": "verify your email",
    "login_verification": "sign in",
    "password_reset": "reset your password",
    "security_confirmation": "confirm a security-sensitive action",
}


def send_otp_email(to: str, code: str, purpose: str = "login_verification",
                    minutes: int = 10, *, request_id: str = "") -> bool:
    what = _OTP_PURPOSE_LABEL.get(purpose, "continue")
    body = (
        f"<p>Your {BRAND} verification code to {what} is:</p>{_code_box(code)}"
        f"<p style='color:#8a93a6;font-size:13px;'>This code expires in {minutes} minutes and can be used once. "
        f"Never share it with anyone — {BRAND} staff will never ask for it.</p>"
    )
    text = (f"Your {BRAND} verification code to {what}: {code}\n\n"
            f"Expires in {minutes} minutes. Never share this code.\nSupport: {SUPPORT_EMAIL}")
    return _send(to, f"Your {BRAND} verification code", text,
                 _shell("Your verification code", body, "Your one-time ValleyMind AI code."),
                 request_id=request_id)


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
    return _send(to, f"{BRAND} security alert: {event}", text,
                 _shell("Security alert", body, event),
                 request_id=request_id)
