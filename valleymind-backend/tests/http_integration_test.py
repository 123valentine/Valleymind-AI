import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

import requests


DEFAULT_TIMEOUT = 90
DEFAULT_EMAIL = f"test_user_http_{int(time.time())}@example.com"
DEFAULT_PASSWORD = "Valleymind-Integration-Check-123!"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


class IntegrationFailure(AssertionError):
    pass


def fail(message: str):
    raise IntegrationFailure(message)


def request_json(session: requests.Session, method: str, url: str, **kwargs):
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    response = session.request(method, url, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        fail(f"{method} {url} returned non-JSON body: HTTP {response.status_code} {response.text[:240]!r}")
    return response, payload


def assert_natural_text(label: str, text: str):
    if not isinstance(text, str) or not text.strip():
        fail(f"{label} reply was empty or not text.")

    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        fail(f"{label} exposed raw JSON-like output: {stripped[:160]}")

    forbidden_fragments = (
        '"response"',
        '"fixture"',
        '"teams"',
        '"articles"',
        "SPORTS_API_STATUS",
        "SPORTS_API_DATA",
        "x-apisports-key",
        "apiKey",
        "Authorization",
        "Live news results:",
        "Live sports results:",
        "Live external context gathered",
        "__LIVE_DATA_UNAVAILABLE__",
        "Traceback",
    )
    lowered = stripped.lower()
    for fragment in forbidden_fragments:
        if fragment.lower() in lowered:
            fail(f"{label} exposed internal/raw data fragment {fragment!r}: {stripped[:240]}")

    try:
        parsed = json.loads(stripped)
    except ValueError:
        parsed = None
    if isinstance(parsed, (dict, list)):
        fail(f"{label} reply was a JSON dump, not natural text.")


def check_status_before_login(session: requests.Session, base_url: str) -> CheckResult:
    response, payload = request_json(session, "GET", f"{base_url}/auth/status")
    if response.status_code != 200:
        fail(f"/auth/status before login returned HTTP {response.status_code}")
    if payload.get("authenticated") is not False:
        fail(f"/auth/status before login should be unauthenticated, got: {payload}")
    return CheckResult("auth status before login", True, "unauthenticated")


def check_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    response, payload = request_json(
        session,
        "POST",
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
    )
    if response.status_code != 200:
        fail(f"/auth/login returned HTTP {response.status_code}: {payload}")
    if payload.get("status") != "success" or payload.get("authenticated") is not True:
        fail(f"/auth/login did not authenticate: {payload}")
    token = str(payload.get("session_token") or "").strip()
    if not token:
        fail("/auth/login did not return a session_token")
    session.headers.update({
        "X-Session-Token": token,
        "Authorization": f"Bearer {token}",
    })
    return token


def check_status_after_login(session: requests.Session, base_url: str, email: str) -> CheckResult:
    response, payload = request_json(session, "GET", f"{base_url}/auth/status")
    if response.status_code != 200:
        fail(f"/auth/status after login returned HTTP {response.status_code}")
    if payload.get("authenticated") is not True:
        fail(f"/auth/status after login was not authenticated: {payload}")
    if str(payload.get("email") or "").lower() != email.lower():
        fail(f"/auth/status email mismatch: {payload}")
    if payload.get("memory_loaded") is not True:
        fail(f"/auth/status did not report memory_loaded=true: {payload}")
    return CheckResult("auth status after login", True, "authenticated + memory loaded")


def check_history(session: requests.Session, base_url: str) -> CheckResult:
    response, payload = request_json(session, "GET", f"{base_url}/chat/history")
    if response.status_code != 200:
        fail(f"/chat/history returned HTTP {response.status_code}: {payload}")
    if payload.get("status") != "success":
        fail(f"/chat/history did not return success: {payload}")
    if not isinstance(payload.get("messages"), list):
        fail(f"/chat/history messages was not a list: {payload}")
    return CheckResult("chat history", True, f"{len(payload.get('messages', []))} messages")


def chat(session: requests.Session, base_url: str, message: str) -> str:
    response, payload = request_json(
        session,
        "POST",
        f"{base_url}/chat",
        json={"message": message},
    )
    if response.status_code != 200:
        fail(f"/chat for {message!r} returned HTTP {response.status_code}: {payload}")
    if payload.get("status") != "success":
        fail(f"/chat for {message!r} did not return success: {payload}")
    reply = payload.get("reply")
    assert_natural_text(f"/chat {message!r}", reply)
    return reply


def run(base_url: str, email: str, password: str):
    base_url = base_url.rstrip("/")
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    results = []
    results.append(check_status_before_login(session, base_url))

    check_login(session, base_url, email, password)
    results.append(CheckResult("login", True, email))
    results.append(check_status_after_login(session, base_url, email))
    results.append(check_history(session, base_url))

    memory_reply = chat(session, base_url, "What is my name?")
    results.append(CheckResult("memory chat", True, memory_reply[:120]))

    normal_reply = chat(session, base_url, "Who are you?")
    results.append(CheckResult("general chat", True, normal_reply[:120]))

    news_reply = chat(session, base_url, "latest AI news today")
    results.append(CheckResult("news routing via /chat", True, news_reply[:120]))

    sports_reply = chat(session, base_url, "Liverpool next match and EPL table")
    results.append(CheckResult("sports routing via /chat", True, sports_reply[:120]))

    results.append(check_history(session, base_url))
    return results


def main():
    parser = argparse.ArgumentParser(
        description="HTTP-level Valleymind-AI integration test. Uses only deployed/running API routes."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("VALLEYMIND_BASE_URL", "http://127.0.0.1:8000"),
        help="Running Valleymind base URL, e.g. https://your-app.onrender.com",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("VALLEYMIND_TEST_EMAIL", DEFAULT_EMAIL),
        help="Test login email. If it does not exist, the app's login flow may create it.",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("VALLEYMIND_TEST_PASSWORD", DEFAULT_PASSWORD),
        help="Test login password.",
    )
    args = parser.parse_args()

    try:
        results = run(args.base_url, args.email, args.password)
    except requests.RequestException as exc:
        print(f"HTTP integration test failed: request error: {exc}", file=sys.stderr)
        return 1
    except IntegrationFailure as exc:
        print(f"HTTP integration test failed: {exc}", file=sys.stderr)
        return 1

    print(f"HTTP integration test passed against {args.base_url.rstrip('/')}")
    for result in results:
        print(f"- {result.name}: ok {result.detail}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
