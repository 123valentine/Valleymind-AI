"""Independent Resend diagnostic for ValleyMind-AI.

Tests the exact Resend pipeline that core/email_service.py uses.
Reports PASS/FAIL for each stage. Never logs the API key.

Usage:
    python test_resend.py                        # config check only
    python test_resend.py --check                # + validate API key with Resend
    python test_resend.py --send you@email.com   # + send a real test message
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

RESULTS: list[tuple[str, str, str]] = []  # (stage, status, detail)


def result(stage: str, status: str, detail: str = ""):
    RESULTS.append((stage, status, detail))
    mark = "PASS" if status == "PASS" else "FAIL" if status == "FAIL" else "INFO"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{mark}] {stage}{suffix}")


def check_config():
    print("\n1. Configuration check")
    from core import email_service
    c = email_service._cfg()
    result("RESEND_API_KEY", "PASS" if c["api_key"] else "FAIL",
           "set" if c["api_key"] else "MISSING")
    result("EMAIL_FROM", "PASS" if c["sender"] else "FAIL", c["sender"] or "MISSING")
    result("EMAIL_REPLY_TO", "SKIP" if not c["reply_to"] else "PASS",
           c["reply_to"] or "(optional, unset)")
    result("available()", "PASS" if email_service.available() else "FAIL")
    return email_service.available()


def check_api_key() -> bool:
    print("\n2. Resend API key validation")
    try:
        import httpx
        from core import email_service
        c = email_service._cfg()
        resp = httpx.get("https://api.resend.com/domains",
                         headers={"Authorization": f"Bearer {c['api_key']}"},
                         timeout=email_service.RESEND_TOTAL_TIMEOUT)
        if resp.status_code == 200:
            domains = [d.get("name") for d in (resp.json().get("data") or [])]
            result("api_auth", "PASS", "key accepted by Resend")
            result("verified_domains", "INFO",
                   ", ".join(domains) if domains else "none — sandbox sender only delivers to YOUR account address")
            return True
        if resp.status_code == 401:
            result("api_auth", "FAIL", "unauthorized — RESEND_API_KEY is invalid/revoked")
        else:
            result("api_auth", "FAIL", f"status={resp.status_code}")
    except Exception as exc:
        result("api_auth", "FAIL", f"{type(exc).__name__}: {exc}")
    return False


def test_send(recipient: str):
    print(f"\n3. Send test message to {recipient.split('@')[0]}@***")
    from core import email_service
    html = ("<p>This is a delivery test from <strong>ValleyMind-AI</strong> via "
            "<strong>Resend</strong>.</p><p>If you received this, the email pipeline is working.</p>")
    res = email_service.send_email(
        recipient,
        f"{email_service.BRAND} — delivery test",
        html,
        "Delivery test from ValleyMind-AI via Resend.",
    )
    if res.get("success"):
        result("message_submission", "PASS", f"id={res.get('id')}")
        print("\n     Message accepted by Resend. Check inbox and spam/junk folder.")
        print("     NOTE: with onboarding@resend.dev it only delivers to YOUR Resend account address.")
    else:
        result("message_submission", "FAIL", f"error={res.get('error')}")


def main():
    print(f"{'='*60}")
    print("  ValleyMind AI — Resend Email Diagnostic")
    print(f"{'='*60}")

    ok = check_config()

    do_check = "--check" in sys.argv or "--send" in sys.argv
    send_to = None
    if "--send" in sys.argv:
        idx = sys.argv.index("--send")
        if idx + 1 < len(sys.argv):
            send_to = sys.argv[idx + 1]

    if ok and do_check:
        ok = check_api_key()

    if ok and send_to:
        test_send(send_to)
    elif not do_check:
        print("\n  Run with --check to validate the key against Resend.")
        print("  Run with --send email@example.com to send a real test message.")

    print(f"\n{'='*60}")
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    if fails:
        print(f"  RESULT: {len(fails)} FAILURE(S) detected")
        for stage, _, detail in fails:
            print(f"    - {stage}: {detail}")
    else:
        print("  RESULT: ALL CHECKS PASSED")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
