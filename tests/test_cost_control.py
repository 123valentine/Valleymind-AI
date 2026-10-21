"""Free-quota-aware cost-control tests (no real provider calls).

Every test runs against a temp quota-state file and asserts that:
  1. a provider is blocked without touching the network when its free quota
     is unverified/exhausted, and
  2. the quota-manager state machine (available / exhausted / rate-limited /
     invalid-key / reset / persistence) behaves as documented.

Run directly:
    python -m pytest tests/test_cost_control.py -q
"""

import os

import pytest


# ── network guards ──────────────────────────────────────────────────────────

def _no_network(monkeypatch):
    """Fail loudly if any test accidentally hits the network."""
    import requests
    import httpx

    def boom(*a, **k):
        raise AssertionError("network call attempted while provider was expected to be blocked")

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.setattr(httpx, "get", boom)


@pytest.fixture(autouse=True)
def _isolate_quota(monkeypatch, tmp_path):
    """Fresh temp quota state + no global kill switch for every test."""
    monkeypatch.delenv("PAID_PROVIDERS_ENABLED", raising=False)
    for key in list(os.environ):
        if key.startswith("FREE_QUOTA_"):
            monkeypatch.delenv(key, raising=False)
    state = tmp_path / "quota_state.json"
    monkeypatch.setenv("QUOTA_STATE_PATH", str(state))

    from core import quota_manager as q
    q._manager._state_path = state
    q._manager.reload()
    yield
    q._manager._state_path = None
    q._manager.reload()


# ── default posture ─────────────────────────────────────────────────────────

def test_free_key_providers_default_available():
    from core import quota_manager as q
    for provider in ("groq", "nvidia", "gemini", "pollinations", "duckduckgo",
                     "wikipedia", "edge_tts", "pyttsx3", "ffmpeg", "browser_tts"):
        assert q.check(provider).allowed is True, provider


def test_chargeable_providers_default_blocked_fail_closed():
    from core import quota_manager as q
    for provider in ("alibaba", "fal", "opencode", "resend", "openrouter",
                     "pinecone", "tinyfish", "currents", "newscatcher",
                     "newsapi", "apisports"):
        decision = q.check(provider)
        assert decision.allowed is False, provider
        assert decision.reason, provider


def test_unregistered_provider_fails_closed():
    from core import quota_manager as q
    assert q.check("mystery-llm").allowed is False


def test_alias_resolution():
    from core import quota_manager as q
    assert q.check("dashscope").provider == "alibaba"
    assert q.check("ddg").provider == "duckduckgo"


# ── global kill switch (still honoured as emergency off) ────────────────────

def test_global_kill_switch_blocks_everything(monkeypatch):
    from core import quota_manager as q, cost_control
    monkeypatch.setenv("PAID_PROVIDERS_ENABLED", "false")
    assert q.check("groq").allowed is False
    assert q.check("pollinations").allowed is False
    assert cost_control.paid_providers_enabled() is False
    assert cost_control.paid_providers_disabled() is True


def test_unset_switch_is_not_a_global_disable():
    from core import cost_control
    assert cost_control.paid_providers_enabled() is True
    assert cost_control.paid_providers_disabled() is False


# ── override / operator verification ────────────────────────────────────────

def test_override_marks_provider_available():
    from core import quota_manager as q, cost_control
    assert cost_control.provider_blocked("alibaba") is True
    q.override("alibaba", q.QuotaState.AVAILABLE, note="verified free tokens")
    assert cost_control.provider_blocked("alibaba") is False
    assert q.check("alibaba").allowed is True


def test_env_override_available(monkeypatch):
    from core import quota_manager as q
    monkeypatch.setenv("FREE_QUOTA_RESEND", "available")
    q._manager.reload()
    assert q.check("resend").allowed is True


def test_openrouter_free_model_allowed_but_paid_model_blocked():
    from core import quota_manager as q
    assert q.check("openrouter", "meta-llama/llama-3-8b-instruct:free").allowed is True
    assert q.check("openrouter", "openai/gpt-4o-mini").allowed is False


# ── state machine ───────────────────────────────────────────────────────────

def test_record_success_flips_unknown_to_available():
    from core import quota_manager as q
    q.record_success("resend")
    assert q.check("resend").allowed is True


def test_quota_exhausted_blocks_until_reset(monkeypatch):
    from core import quota_manager as q
    # groq is free-key and defaults available; exhaust it.
    assert q.check("groq").allowed is True
    q.record_error("groq", status_code=429, body="quota exceeded: balance too low")
    assert q.check("groq").allowed is False
    assert q.check("groq").state is q.QuotaState.EXHAUSTED
    # A reset in the past clears it back to the provider default.
    import time
    q._manager._records["groq"].reset_at = time.time() - 5
    assert q.check("groq").allowed is True


def test_rate_limit_backoff_then_recovery():
    from core import quota_manager as q
    q.override("alibaba", q.QuotaState.AVAILABLE)
    q.record_error("alibaba", status_code=429, retry_after=120)
    blocked = q.check("alibaba")
    assert blocked.allowed is False
    assert blocked.state is q.QuotaState.RATE_LIMITED
    # Expire the backoff and it recovers.
    q._manager._records["alibaba"].backoff_until = 0.0
    assert q.check("alibaba").allowed is True


def test_invalid_key_blocks_and_does_not_retry():
    from core import quota_manager as q
    q.override("alibaba", q.QuotaState.AVAILABLE)
    q.record_error("alibaba", status_code=401, body="invalid api key")
    assert q.check("alibaba").state is q.QuotaState.INVALID_KEY
    assert q.check("alibaba").allowed is False


def test_network_error_is_transient_keeps_state():
    from core import quota_manager as q
    q.override("alibaba", q.QuotaState.AVAILABLE)
    q.record_error("alibaba", exc=TimeoutError("timed out"))
    assert q.check("alibaba").allowed is True  # transient — not disabled


# ── error classification ────────────────────────────────────────────────────

@pytest.mark.parametrize("status,body,expected", [
    (402, "", "quota_exhausted"),
    (400, "Insufficient balance", "quota_exhausted"),
    (429, "", "rate_limited"),
    (401, "", "invalid_key"),
    (403, "forbidden", "invalid_key"),
    (404, "model not found", "missing_model"),
    (503, "upstream", "network"),
    (400, "weird", "unknown"),
])
def test_classify_error(status, body, expected):
    from core import quota_manager as q
    assert q.classify_error(status, body).value == expected


def test_classify_network_exception():
    from core import quota_manager as q
    assert q.classify_error(exc=ConnectionError("connection refused")).value == "network"


# ── persistence across restart ──────────────────────────────────────────────

def test_state_persists_across_reload():
    from core import quota_manager as q
    q.override("alibaba", q.QuotaState.EXHAUSTED, note="drained")
    q._manager.reload()  # simulates a process restart
    decision = q.check("alibaba")
    assert decision.allowed is False
    assert decision.state is q.QuotaState.EXHAUSTED


# ── the required user-facing message ────────────────────────────────────────

def test_blocked_message_text():
    from core import quota_manager as q, cost_control
    assert cost_control.BLOCKED_MESSAGE == (
        "This feature is temporarily unavailable because its free API quota has been exhausted.")
    q.record_error("groq", status_code=402, body="balance")
    assert q.block_message("groq") == cost_control.BLOCKED_MESSAGE


# ── provider integrations refuse without network when blocked ───────────────

def test_alibaba_provider_manager_blocked_and_hidden(monkeypatch):
    _no_network(monkeypatch)
    from core import provider_alibaba as pa
    assert pa._available() is False
    assert pa.discover() == []
    for cls in (pa.AlibabaStudioTextProvider, pa.AlibabaStudioImageProvider,
                pa.AlibabaStudioEmbeddingsProvider, pa.QwenImageProvider):
        result = cls().execute(prompt="x", messages=[{"role": "user", "content": "hi"}],
                               texts=["x"])
        assert result.success is False
        assert "free API quota" in (result.error or "")


def test_alibaba_provider_visible_when_free_quota_verified():
    from core import quota_manager as q, provider_alibaba as pa
    q.override("alibaba", q.QuotaState.AVAILABLE)
    assert pa._available() is True
    assert len(pa.discover()) >= 1


def test_video_providers_blocked(monkeypatch):
    _no_network(monkeypatch)
    from core.video_providers.alibaba import AlibabaVideoProvider, discover as alibaba_discover
    from core.video_providers.fal import FalVideoProvider, discover as fal_discover
    from core.video_providers.base import VideoTask

    assert alibaba_discover() == []
    assert fal_discover() == []
    for provider in (AlibabaVideoProvider(), FalVideoProvider()):
        task = provider.submit(VideoTask(prompt="a lion"))
        assert task.status.value == "failed"
        assert "free API quota" in (task.error or "")
        assert task.provider_task_id == ""


def test_video_i2v_blocked(monkeypatch):
    _no_network(monkeypatch)
    from core import video_i2v as i2v
    assert i2v.available() is False
    assert "free API quota" in i2v.submit_clip("motion", "http://x/y.png")["error"]
    assert "free API quota" in i2v.submit_clip_t2v("motion")["error"]
    out = i2v.poll_clip("real-task-id")
    assert out["status"] == "FAILED"
    assert "free API quota" in out["error"]


def test_video_i2v_fake_mode_still_free():
    from core import video_i2v as i2v
    sub = i2v.submit_clip("motion", "http://x/y.png", fake=True)
    assert "task_id" in sub and sub["task_id"].startswith("fake:")


def test_qwen_tts_gated_falls_back_to_browser(monkeypatch):
    _no_network(monkeypatch)
    from core import tts
    assert tts.qwen_tts_available() is False
    out = tts.synthesize_persona("hello there", persona="marcus")
    assert out.get("engine") == "browser"


def test_video_vision_gated(monkeypatch):
    _no_network(monkeypatch)
    from core import video_vision
    assert video_vision.available() is False


def test_ai_builder_blocked(monkeypatch):
    _no_network(monkeypatch)
    from core import ai_builder
    assert ai_builder.configured() is False
    with pytest.raises(ai_builder.BuilderModelError) as exc:
        list(ai_builder.chat_stream("some-model", [{"role": "user", "content": "hi"}]))
    assert "free API quota" in str(exc.value)
    assert ai_builder._user_facing_error(exc.value) == ai_builder.BLOCKED_MESSAGE


def test_ai_builder_configured_when_free_quota_verified():
    from core import quota_manager as q, ai_builder
    q.override("opencode", q.QuotaState.AVAILABLE)
    assert ai_builder.configured() is True


def test_email_blocked(monkeypatch):
    _no_network(monkeypatch)
    from core import email_service
    result = email_service._deliver({"to": ["x@example.com"], "subject": "s", "html": "<p>x</p>"})
    assert result["success"] is False and result["error"] == "provider_blocked"


def test_external_apis_blocked(monkeypatch):
    _no_network(monkeypatch)
    from core import external_apis as ea
    assert ea._search_tinyfish("anything") == ""
    assert ea._fetch_url_content("https://example.com") == ""
    assert ea._search_newsapi("news") == ""
    assert ea._search_currents("news") == ""
    assert ea._search_newscatcher("news") == ""
    assert ea._search_api_sports("liverpool live score") == ea.LIVE_DATA_UNAVAILABLE
    with pytest.raises(RuntimeError):
        ea._api_sports_get("fixtures", {})


def test_memory_manager_disabled_is_noop(monkeypatch):
    _no_network(monkeypatch)
    from core.memory_manager import MemoryManager
    mm = MemoryManager()
    assert mm._index is None
    assert mm.recall_sync("what did we discuss?") == ""
    mm.save_sync("hi", "hello", "sess")


# ── stability fixes ─────────────────────────────────────────────────────────

def test_video_dispatcher_polls_submitter(monkeypatch):
    from core.video_dispatcher import VideoDispatcher
    from core.video_providers.base import BaseVideoProvider, VideoTask, VideoTaskStatus

    calls = {"first_submit": 0, "first_poll": 0, "second_poll": 0}

    class First(BaseVideoProvider):
        name = "First"
        priority = 1

        def submit(self, task, **kwargs):
            calls["first_submit"] += 1
            task.status = VideoTaskStatus.FAILED
            task.error = "boom"
            return task

        def poll(self, task, **kwargs):
            calls["first_poll"] += 1
            return task

    class Second(BaseVideoProvider):
        name = "Second"
        priority = 2

        def submit(self, task, **kwargs):
            task.provider_task_id = "second-123"
            task.status = VideoTaskStatus.SUBMITTED
            return task

        def poll(self, task, **kwargs):
            calls["second_poll"] += 1
            return task

    d = VideoDispatcher()
    d._providers = [First(), Second()]
    task = d._submit(VideoTask(prompt="x"))
    assert task.metadata.get("_provider") == "Second"
    d._poll(task)
    assert calls["second_poll"] == 1
    assert calls["first_poll"] == 0  # the bug would have polled First


def test_stream_video_dispatch_uses_state_generator():
    """Regression for the app.py NameError: _dispatch_video_stream must tail the
    spawned state via _stream_video_state, not an undefined generate()."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "app.py"), "r", encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("def _dispatch_video_stream(")
    end = src.index("def _build_image_user_context(", start)
    block = src[start:end]
    assert "stream_with_context(_stream_video_state(state, resolved_chat_id))" in block
    assert "generate()" not in block
