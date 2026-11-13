"""Tests for the Contextual Activity Indicator system and duplicate-analysis fix.

Tests:
  * ActivityTracker start/update/complete/fail/cancel
  * No stale state leakage between requests
  * ActivityRegistry message templates
  * activity_event/activity_complete/activity_fail/activity_cancel helpers
  * describe_media_for_brain reuses pre-computed results (no duplicate NVIDIA calls)
  * describe_media_for_brain analyzes once when no pre-computed results given
"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.getcwd())

from core.activity import (
    ActivityTracker, ActivityRegistry, ActivityCategory,
    activity_manager, activity_event, activity_complete, activity_fail,
    activity_cancel,
)
from core.vision import VisionService, VisionResult

failures = 0
def ok(cond, label):
    global failures
    if cond:
        print(f"  PASS: {label}")
    else:
        print(f"  FAIL: {label}")
        failures += 1

import base64

def fake_image_uri(name="test.png", mime="image/png"):
    b64 = base64.b64encode(f"fake-image-{name}".encode()).decode()
    return f"data:{mime};base64,{b64}"

def fake_video_uri(name="test.mp4", mime="video/mp4"):
    b64 = base64.b64encode(f"fake-video-{name}".encode()).decode()
    return f"data:{mime};base64,{b64}"

# ═════════════════════════════════════════════════════════════════════
# Activity system tests
# ═════════════════════════════════════════════════════════════════════

print("\n=== Activity: start/update/complete lifecycle ===")
tracker = ActivityTracker("req-1")
act = tracker.start("general", "processing", label="Processing your request…", detail="Starting")
ok(act.status == "active", "started activity is active")
ok(act.label == "Processing your request…", "started activity has label")
cur = tracker.get_current()
ok(cur is not None and cur["label"] == "Processing your request…", "get_current returns snapshot")

# Update
updated = tracker.update("Formulating an answer…", detail="Drafting reply")
ok(updated is not None and updated.label == "Formulating an answer…", "update changes label")
cur = tracker.get_current()
ok(cur["label"] == "Formulating an answer…", "current reflects update")

# Complete
completed = tracker.complete()
ok(completed is not None and completed.status == "completed", "complete marks completed")
ok(completed.ended_at is not None, "complete sets ended_at")
cur = tracker.get_current()
ok(cur is None, "get_current returns None after complete")

# History
ok(len(tracker.history) == 1, "completed activity moved to history")
ok(tracker.history[0].status == "completed", "history entry is completed")

print("\n=== Activity: fail and cancel ===")
tracker2 = ActivityTracker("req-2")
tracker2.start("web_research", "searching_web", label="Searching the web…")
failed = tracker2.fail(detail="API timeout")
ok(failed is not None and failed.status == "failed", "fail marks failed")
ok(failed.detail == "API timeout", "fail records detail")
cur = tracker2.get_current()
ok(cur is None, "current cleared after fail")

tracker3 = ActivityTracker("req-3")
tracker3.start("coding", "running_code", label="Running code…")
cancelled = tracker3.cancel()
ok(cancelled is not None and cancelled.status == "cancelled", "cancel marks cancelled")
cur = tracker3.get_current()
ok(cur is None, "current cleared after cancel")

print("\n=== Activity: no stale state between requests ===")
# Request 1 completes
t1 = activity_manager.get_tracker("req-a")
t1.start("general", "thinking", label="Thinking…")
t1.complete()
# Request 2 starts fresh
t2 = activity_manager.get_tracker("req-b")
cur_b = t2.get_current()
ok(cur_b is None, "new request has no current activity (no stale state)")
t2.start("web_research", "searching_web", label="Searching the web…")
cur_b2 = t2.get_current()
ok(cur_b2 is not None and cur_b2["label"] == "Searching the web…", "new request has its own activity")
# Request 1 still completed
cur_a = t1.get_current()
ok(cur_a is None, "request 1 remains completed")

print("\n=== Activity: registry labels ===")
labels = ActivityRegistry.labels("web_research", "searching_web")
ok(len(labels) > 0, "registry returns labels")
ok("Searching the web…" in labels, "searching_web label present")
labels2 = ActivityRegistry.labels("unknown", "nonexistent")
ok(len(labels2) > 0, "unknown category falls back to default")

print("\n=== Activity: event helper functions ===")
# activity_event starts and returns SSE-shaped dict
evt = activity_event("req-evt", "image_video", "analyzing_video", label="Analyzing video…", detail="clip.mp4")
ok("activity" in evt, "event has activity key")
ok(evt["activity"]["label"] == "Analyzing video…", "event label correct")
ok(evt["activity"]["category"] == "image_video", "event category correct")
ok(evt["activity"]["status"] == "active", "event status active")

# activity_complete
evt_c = activity_complete("req-evt")
ok(evt_c is not None, "complete returns event")
ok("activity_complete" in evt_c, "complete event has activity_complete key")
ok(evt_c["activity_complete"]["status"] == "completed", "complete status correct")
# Current should now be None
cur_evt = activity_manager.get_tracker("req-evt").get_current()
ok(cur_evt is None, "current cleared after complete via helper")

# activity_fail
activity_event("req-fail", "web_research", "searching_web")
evt_f = activity_fail("req-fail", detail="Network error")
ok(evt_f is not None, "fail returns event")
ok("activity_failed" in evt_f, "fail event has activity_failed key")
ok(evt_f["activity_failed"]["detail"] == "Network error", "fail detail correct")

# activity_cancel
activity_event("req-cancel", "coding", "running_code")
evt_can = activity_cancel("req-cancel")
ok(evt_can is not None, "cancel returns event")
ok("activity_cancelled" in evt_can, "cancel event key present")

# ═════════════════════════════════════════════════════════════════════
# Duplicate-analysis fix: describe_media_for_brain reuses pre-computed results
# ═════════════════════════════════════════════════════════════════════

print("\n=== Duplicate fix: describe_media_for_brain reuses pre-computed image result ===")
svc = VisionService()
# Patch provider to NVIDIA
with patch.object(svc, "_get_vision_provider",
                  return_value=("nvidia", {"api_key": "key", "base_url": "https://integrate.api.nvidia.com/v1"})):
    call_count = {"n": 0}
    def fake_post(url, headers, json, timeout):
        call_count["n"] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "Analysis of images.", "reasoning": ""}}],
            "usage": {"total_tokens": 200},
        }
        return resp

    images = [fake_image_uri("a.png"), fake_image_uri("b.png")]
    # First analyze (endpoint path)
    with patch("core.vision.requests.post", side_effect=fake_post):
        img_result = svc.analyze_images(images, question="Compare them.")
        ok(img_result.success, "first analyze_images succeeds")
        calls_after_first = call_count["n"]
        ok(calls_after_first == 1, f"first analysis made exactly 1 API call (got {calls_after_first})")

        # Now describe_media_for_brain with pre-computed result — should NOT call API again
        combined = svc.describe_media_for_brain(
            image_data_list=images,
            image_result=img_result,   # pre-computed, reuse
            question="Compare them.",
        )
        ok(combined.success, "describe_media_for_brain with pre-computed result succeeds")
        ok("Analysis of images." in combined.description, "uses pre-computed description")
        ok(call_count["n"] == calls_after_first, "NO additional API call made (duplicate prevented)")
        calls_after_second = call_count["n"]

print("\n=== Duplicate fix: describe_media_for_brain reuses pre-computed video result ===")
svc2 = VisionService()
with patch.object(svc2, "_get_vision_provider",
                  return_value=("nvidia", {"api_key": "key", "base_url": "https://integrate.api.nvidia.com/v1"})):
    call_count2 = {"n": 0}
    def fake_post_videos(url, headers, json, timeout):
        call_count2["n"] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "Video analysis result.", "reasoning": ""}}],
            "usage": {"total_tokens": 400},
        }
        return resp

    videos = [fake_video_uri("v1.mp4")]
    # First analyze (endpoint path)
    with patch("core.vision.requests.post", side_effect=fake_post_videos):
        vid_result = svc2.analyze_videos(videos, question="What happens?")
        ok(vid_result.success, "first analyze_videos succeeds")
        calls_v_first = call_count2["n"]
        ok(calls_v_first == 1, f"first video analysis made exactly 1 API call (got {calls_v_first})")

        # Now describe_media_for_brain with pre-computed video result
        combined2 = svc2.describe_media_for_brain(
            video_data_uri_list=videos,
            video_result=vid_result,   # pre-computed, reuse
            question="What happens?",
        )
        ok(combined2.success, "describe_media_for_brain with pre-computed video succeeds")
        ok("Video analysis result." in combined2.description, "uses pre-computed video description")
        ok(call_count2["n"] == calls_v_first, "NO additional video API call (duplicate prevented)")

print("\n=== Duplicate fix: describe_media_for_brain analyzes once when no pre-computed result ===")
svc3 = VisionService()
with patch.object(svc3, "_get_vision_provider",
                  return_value=("nvidia", {"api_key": "key", "base_url": "https://integrate.api.nvidia.com/v1"})):
    call_count3 = {"n": 0}
    def fake_post3(url, headers, json, timeout):
        call_count3["n"] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "Fresh analysis.", "reasoning": ""}}],
            "usage": {"total_tokens": 200},
        }
        return resp

    images3 = [fake_image_uri("x.png")]
    # No pre-computed result -> analyze on demand (exactly once)
    with patch("core.vision.requests.post", side_effect=fake_post3):
        combined3 = svc3.describe_media_for_brain(
            image_data_list=images3,
            question="Describe.",
        )
        ok(combined3.success, "describe_media_for_brain without pre-computed result succeeds")
        ok("Fresh analysis." in combined3.description, "fresh analysis performed")
        ok(call_count3["n"] == 1, "exactly 1 API call when no pre-computed result")

# ═════════════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
if failures == 0:
    print("ALL ACTIVITY + DUPLICATE-FIX TESTS PASSED")
else:
    print(f"{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
