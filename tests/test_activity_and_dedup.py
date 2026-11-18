"""Tests for the Contextual Activity Indicator system (v2).

Tests:
  * ActivityTracker start/update/complete/fail/cancel
  * No stale state leakage between requests
  * ActivityRegistry message templates
  * track_activity / context manager helpers
  * describe_media_for_brain reuses pre-computed results
  * describe_media_for_brain analyzes once when no pre-computed result
"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.getcwd())

from core.activity_v2 import (
    create_activity_session, track_activity, clear_current_session,
    activity_registry, ActivityCategory,
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

# ══════════════════════════════════════════════════════════════════════
# Activity tracker tests
# ═════════════════════════════════════════════════════════════════════

print("\n=== Activity: start/update/complete lifecycle ===")
session = create_activity_session(request_id="req-1")
with track_activity("response_preparation", label="Processing your request…", detail="Starting") as act:
    ok(act is not None, "track_activity returns handle")
    # Activity starts as started
    history = session.get_history()
    ok(len(history) == 1, "started activity recorded")
    ok(history[0]["status"] == "started", "started activity has started status")
    ok(history[0]["label"] == "Processing your request…", "started activity has label")

# Update
with track_activity("web_research", label="Searched the web…") as act2:
    act2.complete(label="Searched the web")
history = session.get_history()
# Find the web_search event
web_events = [e for e in history if e["operation"] == "web_research"]
ok(len(web_events) == 2, "web_search has start + complete events")
ok(web_events[1]["status"] == "completed", "completed activity has completed status")

# History - start+complete for first + start+complete for second = 4 events
ok(len(history) == 4, "two events per activity, 4 total")
ok(history[0]["status"] == "started", "first event started")
ok(history[1]["status"] == "completed", "first event completed")
ok(history[2]["status"] == "started", "second event started")
ok(history[3]["status"] == "completed", "second event completed")

print("\n=== Activity: fail and cancel ===")
session2 = create_activity_session(request_id="req-2")
with track_activity("web_research", label="Searching the web…") as fail_act:
    fail_act.fail(detail="API timeout")
history2 = session2.get_history()
ok(len(history2) == 2, "two events recorded for video")
ok(history2[1]["status"] == "failed", "failed activity has failed status")
ok(history2[1]["detail"] == "API timeout", "fail records detail")

session3 = create_activity_session(request_id="req-3")
with track_activity("coding", label="Running code…") as cancel_act:
    cancel_act.fail(detail="Cancelled by user", error=Exception("cancelled"))
history3 = session3.get_history()
ok(len(history3) == 2, "two events recorded for cancel")
ok(history3[1]["status"] == "failed", "cancelled activity has failed status")

print("\n=== Activity: no stale state between requests ===")
# Request 1 completes
s1 = create_activity_session(request_id="req-a")
with track_activity("response_preparation", label="Thinking…") as t1_act:
    t1_act.complete()
history1 = s1.get_history()
ok(len(history1) == 2, "request 1 has start+complete events")
ok(history1[0]["status"] == "started", "request 1 event started")
ok(history1[1]["status"] == "completed", "request 1 event completed")

# Request 2 starts fresh
s2 = create_activity_session(request_id="req-b")
history2 = s2.get_history()
ok(len(history2) == 0, "new request has no activity (no stale state)")
with track_activity("web_research", label="Searching the web…") as t2_act:
    pass
history2b = s2.get_history()
ok(len(history2b) == 2, "new request has its own activity")
ok(history2b[0]["label"] == "Searching the web…", "new request has its own activity")
# Request 1 still completed
history1b = s1.get_history()
ok(len(history1b) == 2, "request 1 still has start+complete events")
ok(history1b[1]["status"] == "completed", "request 1 remains completed")

print("\n=== Activity: registry labels ===")
labels = activity_registry.list_operations()
ok(len(labels) > 0, "registry returns labels")
ok("web_search" in labels, "web_search registered")
# Registry get with unknown key returns default spec
spec = activity_registry.get("nonexistent_op")
ok(spec.operation == "nonexistent_op", "registry returns spec for unknown op")

print("\n=== Activity: event emission ===")
session4 = create_activity_session(request_id="req-evt")
evt_act = track_activity("video_analysis", label="Analyzing video…", detail="clip.mp4")
evt_act.complete()
history4 = session4.get_history()
ok(len(history4) == 2, "two events recorded")
ok(history4[0]["operation"] == "video_analysis", "event operation correct")
ok(history4[0]["label"] == "Analyzing video…", "event label correct")
ok(history4[0]["category"] == "vision", "event category correct")
ok(history4[1]["status"] == "completed", "event status completed")

# activity_complete via track_activity
session5 = create_activity_session(request_id="req-evt2")
with track_activity("web_research", label="Searched the web") as comp_act:
    comp_act.complete()
history5 = session5.get_history()
ok(len(history5) == 2, "two events recorded")
ok(history5[1]["status"] == "completed", "complete status correct")
cur = s1.get_history()
ok(len(cur) == 2, "other session unaffected")

# activity_fail via track_activity
session6 = create_activity_session(request_id="req-fail")
with track_activity("web_research", label="Searching the web…") as fail_act:
    fail_act.fail(detail="Network error")
history6 = session6.get_history()
ok(len(history6) == 2, "two events recorded")
ok(history6[1]["status"] == "failed", "failed status")
ok(history6[1]["detail"] == "Network error", "fail detail correct")

# activity_cancel via track_activity
session7 = create_activity_session(request_id="req-cancel")
with track_activity("coding", label="Running code…") as cancel_act:
    cancel_act.fail(detail="Cancelled by user", error=Exception("cancelled"))
history7 = session7.get_history()
ok(len(history7) == 2, "two events recorded")
ok(history7[1]["status"] == "failed", "cancelled activity has failed status")

# ══════════════════════════════════════════════════════════════════════
# Duplicate-analysis fix: describe_media_for_brain reuses pre-computed results
# ══════════════════════════════════════════════════════════════════════

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

        # Now describe_media_for_brain with pre-computed result
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

# ══════════════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
if failures == 0:
    print("ALL ACTIVITY + DUPLICATE-FIX TESTS PASSED")
else:
    print(f"{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)