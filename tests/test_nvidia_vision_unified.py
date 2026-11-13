"""Comprehensive tests for the unified NVIDIA visual-analysis pipeline.

Tests multi-image, single/multi-video, mixed media, error handling, and
conversational integration using mocked NVIDIA API responses (no live keys).
"""
import sys
import os
import base64
from unittest.mock import patch, MagicMock

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

# ── Helpers ─────────────────────────────────────────────────────────────
def fake_image_uri(name="test.png", mime="image/png"):
    b64 = base64.b64encode(f"fake-image-bytes-{name}".encode()).decode()
    return f"data:{mime};base64,{b64}"

def fake_video_uri(name="test.mp4", mime="video/mp4"):
    b64 = base64.b64encode(f"fake-video-bytes-{name}".encode()).decode()
    return f"data:{mime};base64,{b64}"

def make_nvidia_svc():
    svc = VisionService()
    patcher = patch.object(
        svc, "_get_vision_provider",
        return_value=("nvidia", {"api_key": "test-key", "base_url": "https://integrate.api.nvidia.com/v1"}),
    )
    patcher.start()
    return svc, patcher

def make_mock_resp(content_text, status_code=200, text="OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = {
        "choices": [{"message": {"content": content_text, "reasoning": ""}}],
        "usage": {"total_tokens": 200},
    }
    return resp

# ── Test 1: Single image analysis ──────────────────────────────────────
print("\n=== Test 1: Single image analysis ===")
svc, p = make_nvidia_svc()
try:
    with patch("core.vision.requests.post", return_value=make_mock_resp("A red apple on a table.")):
        result = svc.analyze_image(fake_image_uri(), question="What do you see?")
        ok(result.success, "single image succeeds")
        ok("red apple" in result.description.lower(), "single image returns description")
        ok(result.provider.startswith("nvidia/"), "single image uses NVIDIA provider")
finally:
    p.stop()

# ── Test 2: Multiple images in order (single request) ──────────────────
print("\n=== Test 2: Multiple images in order ===")
svc, p = make_nvidia_svc()
try:
    captured = {}
    def fake_post(url, headers, json, timeout):
        content = json["messages"][0]["content"]
        image_parts = [p2 for p2 in content if p2.get("type") == "image_url"]
        captured["image_count"] = len(image_parts)
        captured["urls"] = [p2["image_url"]["url"] for p2 in image_parts]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "Three images analyzed in order.", "reasoning": ""}}],
            "usage": {"total_tokens": 300},
        }
        return resp

    images = [fake_image_uri("a.png"), fake_image_uri("b.png"), fake_image_uri("c.png")]
    with patch("core.vision.requests.post", side_effect=fake_post):
        result = svc.analyze_images(images, question="Describe each.")
        ok(result.success, "multi-image analysis succeeds")
        ok(captured.get("image_count") == 3, "all 3 images sent in ONE request")
        ok(captured.get("urls") == images, "image order preserved")
        ok("Three images analyzed" in result.description, "combined description returned")
        ok(result.images_analyzed == 3, "images_analyzed count is 3")
finally:
    p.stop()

# ── Test 3: Maximum image count validation ─────────────────────────────
print("\n=== Test 3: Maximum image count validation ===")
svc, p = make_nvidia_svc()
try:
    too_many = [fake_image_uri(f"img{i}.png") for i in range(20)]
    result = svc.analyze_images(too_many, question="x")
    ok(not result.success, "too many images rejected")
    ok("Too many images" in result.error or "Maximum" in result.error, "clear error message")
finally:
    p.stop()

# ── Test 4: Single video analysis ──────────────────────────────────────
print("\n=== Test 4: Single video analysis ===")
svc, p = make_nvidia_svc()
try:
    with patch("core.vision.requests.post", return_value=make_mock_resp("A cat jumps on a skateboard.")):
        result = svc.analyze_video(fake_video_uri(), question="What happens?")
        ok(result.success, "single video succeeds")
        ok("cat jumps" in result.description.lower(), "single video returns description")
finally:
    p.stop()

# ── Test 5: Multiple videos sequential processing ──────────────────────
print("\n=== Test 5: Multiple videos sequential processing ===")
svc, p = make_nvidia_svc()
try:
    video_responses = [
        "A blue car drives down the street.",
        "A red car stops at a light.",
        "A green car turns at the intersection.",
    ]
    call_order = []

    def fake_post_videos(url, headers, json, timeout):
        content = json["messages"][0]["content"]
        video_parts = [p2 for p2 in content if p2.get("type") == "video_url"]
        assert len(video_parts) == 1, f"Expected 1 video per request, got {len(video_parts)}"
        call_order.append(len(call_order))
        idx = len(call_order) - 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": video_responses[idx], "reasoning": ""}}],
            "usage": {"total_tokens": 400},
        }
        return resp

    videos = [fake_video_uri("v1.mp4"), fake_video_uri("v2.mp4"), fake_video_uri("v3.mp4")]
    with patch("core.vision.requests.post", side_effect=fake_post_videos):
        result = svc.analyze_videos(videos, question="Compare the cars.")
        ok(result.success, "multi-video analysis succeeds")
        ok("blue car" in result.description.lower(), "video 1 description included")
        ok("red car" in result.description.lower(), "video 2 description included")
        ok("green car" in result.description.lower(), "video 3 description included")
        ok(result.raw is not None and "per_video_results" in result.raw, "per_video_results in raw")
        ok(len(result.raw["per_video_results"]) == 3, "all 3 videos tracked")
        ok(result.raw["successful_count"] == 3, "3 successful videos")
        ok(len(call_order) == 3, "3 sequential API calls (one per video)")
finally:
    p.stop()

# ── Test 6: Mixed images + videos ──────────────────────────────────────
print("\n=== Test 6: Mixed images + videos ===")
svc, p = make_nvidia_svc()
try:
    def fake_post_mixed(url, headers, json, timeout):
        content = json["messages"][0]["content"]
        has_image = any(p2.get("type") == "image_url" for p2 in content)
        has_video = any(p2.get("type") == "video_url" for p2 in content)
        resp = MagicMock()
        resp.status_code = 200
        if has_image:
            resp.json.return_value = {
                "choices": [{"message": {"content": "Image analysis complete.", "reasoning": ""}}],
                "usage": {"total_tokens": 200},
            }
        elif has_video:
            resp.json.return_value = {
                "choices": [{"message": {"content": "Video analysis complete.", "reasoning": ""}}],
                "usage": {"total_tokens": 400},
            }
        return resp

    with patch("core.vision.requests.post", side_effect=fake_post_mixed):
        result = svc.describe_media_for_brain(
            image_data_list=[fake_image_uri()],
            video_data_uri_list=[fake_video_uri()],
            question="What do you see?",
        )
        ok(result.success, "mixed media analysis succeeds")
        ok("Image analysis complete" in result.description, "image findings included")
        ok("Video analysis complete" in result.description, "video findings included")
        ok("What do you see?" in result.description, "question preserved")
finally:
    p.stop()

# ── Test 7: Partial video failure ──────────────────────────────────────
print("\n=== Test 7: Partial video failure ===")
svc, p = make_nvidia_svc()
try:
    call_order = []

    def fake_post_partial(url, headers, json, timeout):
        content = json["messages"][0]["content"]
        video_parts = [p2 for p2 in content if p2.get("type") == "video_url"]
        call_order.append(len(call_order))
        idx = len(call_order) - 1
        resp = MagicMock()
        if idx == 0:
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"content": "Good video analyzed.", "reasoning": ""}}],
                "usage": {"total_tokens": 300},
            }
        else:
            resp.status_code = 400
            resp.text = "Bad video"
        return resp

    videos_fail = [fake_video_uri("good.mp4"), fake_video_uri("bad.mp4")]
    with patch("core.vision.requests.post", side_effect=fake_post_partial):
        result = svc.analyze_videos(videos_fail, question="Compare.")
        ok(result.success, "partial failure still returns success (some analyzed)")
        ok("Good video analyzed" in result.description, "successful video included")
        ok(result.raw is not None and result.raw["failed_count"] == 1, "1 failed video tracked")
        ok(result.raw is not None and result.raw["successful_count"] == 1, "1 successful video tracked")
finally:
    p.stop()

# ── Test 8: Missing NVIDIA key ─────────────────────────────────────────
print("\n=== Test 8: Missing NVIDIA key ===")
svc_no_key = VisionService()
result = svc_no_key.analyze_videos([fake_video_uri()], question="x")
ok(not result.success, "missing key returns failure")
ok("configured" in result.error.lower() or "NVIDIA" in result.error, "clear key error")

result2 = svc_no_key.analyze_images([fake_image_uri()], question="x")
ok(not result2.success, "missing key rejects multi-image too")

# ── Test 9: Invalid file formats ───────────────────────────────────────
print("\n=== Test 9: Invalid file formats ===")
bad_img = "data:image/exe;base64,ZmlsZQ=="
valid, err, _ = svc_no_key.validate_images([bad_img])
ok(not valid, "bad image MIME rejected by validate_images")
ok("unsupported" in err.lower() or "format" in err.lower(), "clear format error")

bad_vid = "data:video/avi;base64,ZmlsZQ=="
text = bad_vid.strip()
header, b64 = text.split(",", 1)
mime = header.split(";")[0].replace("data:", "").lower()
ok(mime not in svc_no_key.NVIDIA_ALLOWED_VIDEO_MIME_TYPES, "bad video MIME not allowed")

# ── Test 10: Oversized image ───────────────────────────────────────────
print("\n=== Test 10: Oversized image ===")
big_data = base64.b64encode(b"x" * (11 * 1024 * 1024)).decode()
big_img = f"data:image/png;base64,{big_data}"
valid, err = svc_no_key.validate_image(big_img)
ok(not valid, "oversized image rejected")
ok("too large" in err.lower() or "maximum" in err.lower(), "clear size error")

# ── Test 11: Empty/malformed uploads ───────────────────────────────────
print("\n=== Test 11: Empty/malformed uploads ===")
ok(not svc_no_key.validate_image("")[0], "empty string rejected")
ok(not svc_no_key.validate_image("not-a-data-uri")[0], "non-data-uri rejected")
ok(not svc_no_key.validate_image("data:image/png;notbase64,xyz")[0], "missing base64 rejected")
ok(not svc_no_key.analyze_videos([], question="x").success, "empty video list rejected")
ok(not svc_no_key.analyze_images([], question="x").success, "empty image list rejected")

# ── Test 12: Conversational integration ────────────────────────────────
print("\n=== Test 12: Conversational integration ===")
svc, p = make_nvidia_svc()
try:
    with patch("core.vision.requests.post", side_effect=fake_post_mixed):
        result = svc.describe_media_for_brain(
            image_data_list=[fake_image_uri("img1.png")],
            video_data_uri_list=[fake_video_uri("vid1.mp4")],
            question="What changed between them?",
            context="The user uploaded media for comparison.",
        )
        ok(result.success, "describe_media_for_brain succeeds")
        ok("Image Analysis" in result.description, "image section present")
        ok("Video Analysis" in result.description, "video section present")
        ok("What changed between them?" in result.description, "question preserved")
        ok("The user uploaded media" in result.description, "context preserved")
finally:
    p.stop()

# ── Test 13: Invalid API key (simulated 401) ───────────────────────────
print("\n=== Test 13: Invalid API key (401) ===")
svc, p = make_nvidia_svc()
try:
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.text = "Invalid API key"
    with patch("core.vision.requests.post", return_value=mock_401):
        result = svc.analyze_image(fake_image_uri(), question="x")
        ok(not result.success, "401 returns failure")
        ok("401" in result.error, "HTTP 401 in error")
finally:
    p.stop()

# ── Summary ────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
if failures == 0:
    print("ALL TESTS PASSED")
else:
    print(f"{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
