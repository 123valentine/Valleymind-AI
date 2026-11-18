"""
Automated verification tests for ValleyMind-AI Activity Tracking System (v2).
Verifies lifecycle truthfulness, ordering, deduplication, error/skip states,
concurrent context isolation, and single-line JSON SSE serialization.
"""

import json
import threading
import pytest

from core.activity_adapters import (
    MediaAnalysisAdapter,
    MemoryAdapter,
    ProviderAdapter,
    WebSearchAdapter,
    drain_sse_activities,
)
from core.activity_v2 import (
    ActivityCategory,
    ActivityStatus,
    OperationSpec,
    activity_registry,
    clear_current_session,
    create_activity_session,
    track_activity,
)


@pytest.fixture(autouse=True)
def cleanup_session():
    yield
    clear_current_session()


def test_sequential_operations_order_and_truthful_completion():
    session = create_activity_session(request_id="req_seq_1", persona="Marcus")

    with track_activity("request_understanding"):
        pass

    with WebSearchAdapter.track_search("ValleyMind architecture") as search_act:
        search_act.complete(label="Searched the web", meta={"count": 3})

    with MediaAnalysisAdapter.track_images(2) as img_act:
        img_act.progress(current=1, total=2, unit="images")
        img_act.progress(current=2, total=2, unit="images")

    history = session.get_history()
    assert [ev["seq"] for ev in history] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert history[0]["operation"] == "request_understanding"
    assert history[0]["status"] == ActivityStatus.STARTED.value
    assert history[1]["status"] == ActivityStatus.COMPLETED.value
    assert history[2]["operation"] == "web_search"
    assert history[2]["status"] == ActivityStatus.STARTED.value
    assert history[3]["label"] == "Searched the web"
    assert history[3]["status"] == ActivityStatus.COMPLETED.value
    assert history[4]["operation"] == "image_analysis"
    assert history[4]["status"] == ActivityStatus.STARTED.value
    assert history[5]["progress"] == {"current": 1, "total": 2, "unit": "images"}
    assert history[6]["progress"] == {"current": 2, "total": 2, "unit": "images"}
    assert history[7]["status"] == ActivityStatus.COMPLETED.value


def test_failed_operation_never_reports_success():
    session = create_activity_session(request_id="req_fail_1")

    with pytest.raises(RuntimeError):
        with WebSearchAdapter.track_page_read("https://example.com/broken"):
            raise RuntimeError("Connection timed out")

    history = session.get_history()
    assert len(history) == 2
    assert history[0]["status"] == ActivityStatus.STARTED.value
    assert history[1]["status"] == ActivityStatus.FAILED.value
    assert history[1]["meta"]["error_type"] == "RuntimeError"
    assert "Connection timed out" in (history[1]["detail"] or "")


def test_memory_update_distinguishes_saved_vs_skipped():
    session = create_activity_session(request_id="req_mem_1")

    mem_noop = MemoryAdapter.track_update()
    MemoryAdapter.finalize_update(mem_noop, saved=False, reason="Casual greeting")

    mem_saved = MemoryAdapter.track_update()
    MemoryAdapter.finalize_update(mem_saved, saved=True, reason="Stored user preference")

    history = session.get_history()
    assert history[1]["status"] == ActivityStatus.SKIPPED.value
    assert history[1]["meta"]["saved"] is False
    assert history[3]["status"] == ActivityStatus.COMPLETED.value
    assert history[3]["meta"]["saved"] is True


def test_duplicate_consecutive_emissions_suppressed():
    session = create_activity_session(request_id="req_dedup_1")

    with track_activity("data_analysis") as act:
        act.progress(label="Processing rows...", current=5, total=10)
        act.progress(label="Processing rows...", current=5, total=10)

    history = session.get_history()
    assert len(history) == 3


def test_cancellation_marks_active_operations_failed():
    session = create_activity_session(request_id="req_cancel_1")
    handle = track_activity("video_analysis")
    assert not handle.is_finalized

    session.cancel("User stopped generation")
    assert handle.is_finalized
    assert session.is_closed

    history = session.get_history()
    assert history[-1]["status"] == ActivityStatus.FAILED.value
    assert history[-1]["meta"]["cancelled"] is True


def test_sse_drain_produces_layer1_compatible_data_frames():
    session = create_activity_session(request_id="req_sse_1")
    ProviderAdapter.notify_fallback("groq", "openrouter", reason="429 rate limit")

    sse_payload = drain_sse_activities(session)
    blocks = [b for b in sse_payload.strip().split("\n\n") if b]
    assert len(blocks) == 2
    assert blocks[0].startswith("data: ")

    parsed = json.loads(blocks[0].split("data: ", 1)[1])
    assert parsed["type"] == "activity"
    assert parsed["activity"]["operation"] == "provider_fallback"
    assert parsed["activity"]["status"] == "started"


def test_extensible_custom_operation_registration():
    activity_registry.register(
        OperationSpec(
            operation="custom_audio_mastering",
            category=ActivityCategory.MEDIA,
            started_label="Mastering audio track...",
            completed_label="Audio mastering complete",
            failed_label="Audio mastering failed",
            skipped_label="Skipped audio mastering",
        )
    )
    session = create_activity_session(request_id="req_ext_1")
    with track_activity("custom_audio_mastering"):
        pass

    history = session.get_history()
    assert history[0]["label"] == "Mastering audio track..."
    assert history[1]["label"] == "Audio mastering complete"


def test_thread_isolation_across_concurrent_requests():
    results = {}

    def worker(req_id: str, op: str):
        sess = create_activity_session(request_id=req_id)
        with track_activity(op):
            pass
        results[req_id] = [e["operation"] for e in sess.get_history()]
        clear_current_session()

    t1 = threading.Thread(target=worker, args=("req_A", "web_search"))
    t2 = threading.Thread(target=worker, args=("req_B", "code_generation"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["req_A"] == ["web_search", "web_search"]
    assert results["req_B"] == ["code_generation", "code_generation"]
