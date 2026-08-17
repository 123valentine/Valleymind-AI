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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

BRAND = "ValleyMind AI"
SUPPORT_EMAIL = "supportvalleymind@gmail.com"
ACCENT = "#00c2b8"
BG = "#0d0d1a"


def _cfg() -> dict:
    user = os.getenv("SMTP_USER", "").strip()
    raw_port = str(os.getenv("SMTP_PORT", "587")).strip()
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
        "port": int(raw_port) if raw_port.isdigit() else 587,
        "user": user,
        "password": os.getenv("SMTP_PASS", "").strip(),
        "sender": os.getenv("MAIL_FROM", "").strip() or user,
        "reply_to": os.getenv("MAIL_REPLY_TO", "").strip(),
    }


def available() -> bool:
    """True when SMTP is configured enough to attempt a send."""
    c = _cfg()
    return bool(c["user"] and c["password"] and c["sender"])


def _send(to: str, subject: str, text_body: str, html_body: str) -> bool:
    """Low-level multipart send. Never raises; never logs secrets/codes/bodies."""
    c = _cfg()
    if not (c["user"] and c["password"] and c["sender"] and to):
        print("[MAIL] SMTP not configured; skipping send")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((BRAND, c["sender"]))
    msg["To"] = to
    if c["reply_to"]:
        msg["Reply-To"] = c["reply_to"]
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(c["host"], c["port"], timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(c["user"], c["password"])
            server.sendmail(c["sender"], [to], msg.as_string())
        return True
    except Exception as exc:
        # Log the failure CLASS and recipient only — never the credentials, body,
        # code, token or the raw provider message.
        print(f"[MAIL] send to {to} failed: {type(exc).__name__}")
        return False


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

def send_verification_email(to: str, code: str, link: str, minutes: int = 30) -> bool:
    body = (
        f"<p>Welcome to {BRAND}! Confirm this email address to finish setting up your account.</p>"
        f"<p style='margin:18px 0;text-align:center;'>{_button('Verify my email', link)}</p>"
        f"<p>Or enter this code in the app:</p>{_code_box(code)}"
        f"<p style='color:#8a93a6;font-size:13px;'>This code and link expire in {minutes} minutes. "
        f"If you didn't create a {BRAND} account, you can ignore this email.</p>"
    )
    text = (f"Welcome to {BRAND}!\n\nVerify your email: {link}\n\n"
            f"Or enter this code in the app: {code}\n\n"
            f"This expires in {minutes} minutes. If you didn't sign up, ignore this email.\n"
            f"Support: {SUPPORT_EMAIL}")
    return _send(to, f"Verify your {BRAND} email", text, _shell("Verify your email", body, "Confirm your email to finish signing up."))


def send_password_reset_email(to: str, link: str, minutes: int = 30) -> bool:
    body = (
        f"<p>We received a request to reset your {BRAND} password.</p>"
        f"<p style='margin:18px 0;text-align:center;'>{_button('Reset my password', link)}</p>"
        f"<p style='color:#8a93a6;font-size:13px;'>This link expires in {minutes} minutes and can be used once. "
        f"If you didn't request this, you can safely ignore this email — your password will not change.</p>"
    )
    text = (f"Reset your {BRAND} password:\n{link}\n\n"
            f"This link expires in {minutes} minutes and can be used once. "
            f"If you didn't request it, ignore this email.\nSupport: {SUPPORT_EMAIL}")
    return _send(to, f"Reset your {BRAND} password", text, _shell("Reset your password", body, "Reset your ValleyMind AI password."))


_OTP_PURPOSE_LABEL = {
    "email_verification": "verify your email",
    "login_verification": "sign in",
    "password_reset": "reset your password",
    "security_confirmation": "confirm a security-sensitive action",
}


def send_otp_email(to: str, code: str, purpose: str = "login_verification", minutes: int = 10) -> bool:
    what = _OTP_PURPOSE_LABEL.get(purpose, "continue")
    body = (
        f"<p>Use this one-time code to {what}:</p>{_code_box(code)}"
        f"<p style='color:#8a93a6;font-size:13px;'>This code expires in {minutes} minutes and can be used once. "
        f"Never share it with anyone — {BRAND} staff will never ask for it.</p>"
    )
    text = (f"Your {BRAND} one-time code to {what}: {code}\n\n"
            f"Expires in {minutes} minutes. Never share this code.\nSupport: {SUPPORT_EMAIL}")
    return _send(to, f"Your {BRAND} verification code", text, _shell("Your verification code", body, "Your one-time ValleyMind AI code."))


def send_security_email(to: str, event: str, detail: str = "") -> bool:
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
    return _send(to, f"{BRAND} security alert: {event}", text, _shell("Security alert", body, event))
