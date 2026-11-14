"""Regression tests for the video-analysis routing fix (Issue 3).

These tests are fully mocked: no live NVIDIA / Alibaba keys are used and no
network request leaves the process.

Covered:
  * NVIDIA credentials are resolved explicitly, even when OpenRouter/Gemini
    keys come first in the image-provider preference order.
  * analyze_video / analyze_videos / _call_nvidia_nemotron hit the NVIDIA
    endpoint with the NVIDIA key (never another provider's key or URL).
  * prepare_video_data_uri builds a valid data:video/mp4 URI.
  * POST /api/video/analyze is no longer gated on the Alibaba key: an
    NVIDIA-only deploy returns a real analysis instead of
    "Video analysis isn't available right now."
  * The unchanged Alibaba fallback still answers when NVIDIA is unavailable.
"""
import io
import os
import sys
import base64
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.getcwd())

from core.vision import VisionService, VisionResult

failures = 0


def ok(cond, label):
    global failures
    if cond:
        print(f"  PASS: {label}")
    else:
        print(f"  FAIL: {label}")
        failures += 1


def fake_video_uri(name="clip.mp4", mime="video/mp4"):
    return f"data:{mime};base64," + base64.b64encode(f"bytes-{name}".encode()).decode()


def nvidia_resp(text="A car drives past a red barn."):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "OK"
    resp.json.return_value = {
        "choices": [{"message": {"content": text, "reasoning": ""}}],
        "usage": {"total_tokens": 321},
    }
    return resp


# ── Test 1: explicit NVIDIA resolution beats provider order ────────────
print("\n=== Test 1: NVIDIA config wins over OpenRouter/Gemini order ===")
from dataclasses import replace  # noqa: E402

svc = VisionService()
svc.config = replace(
    svc.config,
    nvidia_api_key="nv-key",
    openrouter_api_key="or-key",
    gemini_api_key="gem-key",
)
resolved = svc._get_nvidia_config()
ok(resolved is not None, "NVIDIA config resolved with a key present")
ok(resolved and resolved.get("api_key") == "nv-key", "NVIDIA key used, not OpenRouter/Gemini")
ok(resolved and "integrate.api.nvidia.com" in resolved.get("base_url", ""),
   "NVIDIA base URL used")

captured = {}


def fake_post(url, headers, json, timeout):
    captured["url"] = url
    captured["auth"] = headers.get("Authorization")
    captured["model"] = json.get("model")
    return nvidia_resp()


with patch("core.vision.requests.post", side_effect=fake_post):
    result = svc.analyze_video(fake_video_uri(), question="What happens?")
ok(result.success, "video analysis succeeds")
ok(captured["url"].startswith("https://integrate.api.nvidia.com"),
   "request goes to the NVIDIA endpoint")
ok(captured["auth"] == "Bearer nv-key", "request carries the NVIDIA key")
ok(str(captured["model"]).startswith("nvidia/"), "Nemotron model requested")
ok("red barn" in result.description, "description returned")

# Multi-video must use the same explicit resolution.
captured.clear()


def fake_post_multi(url, headers, json, timeout):
    captured.setdefault("urls", []).append(url)
    captured.setdefault("auths", []).append(headers.get("Authorization"))
    return nvidia_resp("Two clips compared.")


with patch("core.vision.requests.post", side_effect=fake_post_multi):
    multi = svc.analyze_videos([fake_video_uri("a.mp4"), fake_video_uri("b.mp4")],
                               question="Compare.")
ok(multi.success, "multi-video analysis succeeds")
ok(all("integrate.api.nvidia.com" in u for u in captured["urls"]),
   "every multi-video call uses NVIDIA")
ok(all(a == "Bearer nv-key" for a in captured["auths"]),
   "every multi-video call uses the NVIDIA key")
ok("Two clips compared" in multi.description, "multi-video findings returned")

# ── Test 2: without an NVIDIA key the failure is explicit ──────────────
print("\n=== Test 2: no NVIDIA key -> clear, non-network failure ===")
svc2 = VisionService()
svc2.config = replace(
    svc2.config,
    nvidia_api_key="",
    openrouter_api_key="or-key",
)
res = svc2.analyze_video(fake_video_uri(), question="x")
ok(not res.success, "video analysis fails without NVIDIA")
ok("NVIDIA" in res.error or "configured" in res.error.lower(),
   "error names the missing NVIDIA configuration")

# ── Test 3: prepare_video_data_uri ─────────────────────────────────────
print("\n=== Test 3: video file -> data URI ===")
tmp = os.path.join(os.getcwd(), "_routing_test_clip.mp4")
try:
    with open(tmp, "wb") as fh:
        fh.write(b"\x00\x00\x00\x18ftypmp42" + b"payload" * 32)
    svc3 = VisionService()
    uri, err = svc3.prepare_video_data_uri(tmp, "clip.mp4", "application/octet-stream")
    ok(not err, "mp4 prepared without error")
    ok(bool(uri) and uri.startswith("data:video/mp4;base64,"), "returns a video/mp4 data URI")
    ok(uri and base64.b64decode(uri.split(",", 1)[1]) == open(tmp, "rb").read(),
       "encoded bytes match the file")
    missing_uri, missing_err = svc3.prepare_video_data_uri(
        os.path.join(os.getcwd(), "_does_not_exist.mp4"))
    ok(missing_uri is None and bool(missing_err), "missing file rejected")
finally:
    try:
        os.remove(tmp)
    except OSError:
        pass

# ── Test 4: /api/video/analyze with NVIDIA only ────────────────────────
print("\n=== Test 4: NVIDIA-only deploy answers instead of 503 ===")
import app as appmod  # noqa: E402


class FakeVision:
    def _get_nvidia_config(self):
        return {"api_key": "nv-key", "base_url": "https://integrate.api.nvidia.com/v1"}

    def prepare_video_data_uri(self, path, filename="", mimetype=""):
        return "data:video/mp4;base64," + base64.b64encode(b"clip").decode(), ""

    def analyze_video(self, data_uri, question="", context=""):
        return VisionResult(
            success=True,
            description="A drone shot over a coastline.",
            provider="nvidia/test-model",
            raw={"usage": {"total_tokens": 0}},
        )


def _fake_login():
    return "test-user", None


def _post_video():
    client = appmod.app.test_client()
    data = {
        "video": (io.BytesIO(b"\x00\x00\x00\x18ftypmp42fake-data"), "clip.mp4"),
        "question": "What happens?",
        "context": "chat",
        "save": "0",  # do not touch the media library in a unit test
    }
    return client.post("/api/video/analyze", data=data, content_type="multipart/form-data")


try:
    with patch.object(appmod, "_require_login", _fake_login), \
         patch.object(appmod, "_get_user_tier", lambda uid: "free"), \
         patch.object(appmod, "_get_analysis_count", lambda uid: 0), \
         patch("core.studio_jobs.remaining_budget", lambda: 100.0), \
         patch("core.video_vision.available", lambda: False), \
         patch.object(appmod, "get_vision_service", lambda: FakeVision()):
        resp = _post_video()
        body = resp.get_json() or {}
        ok(resp.status_code == 200, "HTTP 200 with only an NVIDIA key")
        ok(body.get("status") == "success", "analysis succeeds")
        ok(body.get("provider") == "nvidia", "response reports the NVIDIA provider")
        ok(body.get("description") == "A drone shot over a coastline.",
           "NVIDIA description returned")
        ok("video_url" in body and "tokens" in body,
           "existing response contract preserved")

    # Alibaba fallback: no NVIDIA credentials at all, but Qwen is configured.
    class NoNvidiaVision:
        def _get_nvidia_config(self):
            return None

    alibaba_result = {"text": "A cat naps on a windowsill.", "tokens": 12}

    def _fake_alibaba(path, prompt, system=""):
        return dict(alibaba_result)

    with patch.object(appmod, "_require_login", _fake_login), \
         patch.object(appmod, "_get_user_tier", lambda uid: "free"), \
         patch.object(appmod, "_get_analysis_count", lambda uid: 0), \
         patch("core.studio_jobs.remaining_budget", lambda: 100.0), \
         patch("core.studio_jobs.add_spend", lambda amount: None), \
         patch.object(appmod, "usage_collection", lambda: None), \
         patch("core.video_vision.available", lambda: True), \
         patch("core.video_vision.analyze_video", _fake_alibaba), \
         patch.object(appmod, "get_vision_service", lambda: NoNvidiaVision()):
        resp3 = _post_video()
        body3 = resp3.get_json() or {}
        ok(resp3.status_code == 200, "Alibaba answers when NVIDIA is unconfigured")
        ok(body3.get("provider") == "alibaba", "response reports the Alibaba provider")
        ok(body3.get("description") == "A cat naps on a windowsill.",
           "Alibaba description returned")
        ok(body3.get("tokens") == 12, "Alibaba token count preserved")

    # Alibaba fallback: NVIDIA is configured but its call fails (e.g. 401).
    class BrokenNvidiaVision(FakeVision):
        def analyze_video(self, data_uri, question="", context=""):
            return VisionResult(success=False, error="NVIDIA HTTP 401: unauthorized")

    with patch.object(appmod, "_require_login", _fake_login), \
         patch.object(appmod, "_get_user_tier", lambda uid: "free"), \
         patch.object(appmod, "_get_analysis_count", lambda uid: 0), \
         patch("core.studio_jobs.remaining_budget", lambda: 100.0), \
         patch("core.studio_jobs.add_spend", lambda amount: None), \
         patch.object(appmod, "usage_collection", lambda: None), \
         patch("core.video_vision.available", lambda: True), \
         patch("core.video_vision.analyze_video", _fake_alibaba), \
         patch.object(appmod, "get_vision_service", lambda: BrokenNvidiaVision()):
        resp4 = _post_video()
        body4 = resp4.get_json() or {}
        ok(resp4.status_code == 200, "Alibaba answers when the NVIDIA call fails")
        ok(body4.get("provider") == "alibaba", "fallback reports Alibaba, not NVIDIA")

    # Both configured but both fail: surface the NVIDIA failure, HTTP 502.
    with patch.object(appmod, "_require_login", _fake_login), \
         patch.object(appmod, "_get_user_tier", lambda uid: "free"), \
         patch.object(appmod, "_get_analysis_count", lambda uid: 0), \
         patch("core.studio_jobs.remaining_budget", lambda: 100.0), \
         patch("core.video_vision.available", lambda: True), \
         patch("core.video_vision.analyze_video",
               lambda path, prompt, system="": {"error": "Qwen quota exhausted"}), \
         patch.object(appmod, "get_vision_service", lambda: BrokenNvidiaVision()):
        resp5 = _post_video()
        body5 = resp5.get_json() or {}
        ok(resp5.status_code == 502, "both-provider failure is a 502")
        ok("Qwen quota exhausted" in (body5.get("message") or ""),
           "trailing Alibaba error is surfaced to the caller")

    # Both providers unavailable: same message the user reported.
    with patch.object(appmod, "_require_login", _fake_login), \
         patch.object(appmod, "_get_user_tier", lambda uid: "free"), \
         patch.object(appmod, "_get_analysis_count", lambda uid: 0), \
         patch("core.studio_jobs.remaining_budget", lambda: 100.0), \
         patch("core.video_vision.available", lambda: False), \
         patch.object(appmod, "get_vision_service", lambda: type(
             "NoVision", (), {"_get_nvidia_config": lambda self: None})()):
        resp2 = _post_video()
        body2 = resp2.get_json() or {}
        ok(resp2.status_code == 503, "503 only when no provider exists at all")
        ok(body2.get("message") == "Video analysis isn't available right now.",
           "original user-facing message preserved for a truly unconfigured server")
except Exception as exc:  # pragma: no cover - diagnostic only
    ok(False, f"route test crashed: {exc}")

print(f"\n{'=' * 50}")
if failures == 0:
    print("ALL TESTS PASSED")
else:
    print(f"{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
