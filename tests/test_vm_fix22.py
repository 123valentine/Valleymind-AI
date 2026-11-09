"""vm-fix22 mocked tests for provider rotation, token budget, dedup, startup discovery."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
import json

# ── 1. Config defaults ──────────────────────────────────────────────
from core.config import get_config

cfg = get_config()
assert cfg.nvidia_model == "nvidia/nemotron-3.5-super-vl-120b-a12b", f"nvidia_model: {cfg.nvidia_model}"
assert cfg.openrouter_model == "nvidia/nemotron-3.5-super-vl:free", f"openrouter_model: {cfg.openrouter_model}"
assert cfg.gemini_model == "gemini-3.8-flash", f"gemini_model: {cfg.gemini_model}"
assert cfg.gemini_api_key, "gemini_api_key should read GOOGLE_GENERATIVE_AI_API_KEY fallback"
print("CONFIG_DEFAULTS_OK")

# ── 2. Startup model discovery (mocked) ─────────────────────────────
from core.brain import (
    _resolve_openrouter_model,
    _resolve_nvidia_model,
    _startup_health_check,
    get_discovered_model,
)

# OpenRouter: configured model without ':free' → discover free model
fake_openrouter_models = {
    "data": [
        {"id": "nvidia/nemotron-3.5-super-vl:free"},
        {"id": "meta-llama/llama-3.1-8b-instruct:free"},
        {"id": "google/gemma-4-31b-it:free"},
    ]
}
with patch("urllib.request.urlopen") as m:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_openrouter_models).encode()
    m.return_value.__enter__.return_value = mock_resp
    resolved = _resolve_openrouter_model("openai/gpt-4o-mini", "fake-key")
    assert resolved == "nvidia/nemotron-3.5-super-vl:free", f"Expected nemotron:free, got {resolved}"
    assert get_discovered_model("openrouter") == "nvidia/nemotron-3.5-super-vl:free"
print("STARTUP_DISCOVERY_OPENROUTER_OK")

# OpenRouter: configured model IS ':free' and listed → keep it
with patch("urllib.request.urlopen") as m:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_openrouter_models).encode()
    m.return_value.__enter__.return_value = mock_resp
    resolved = _resolve_openrouter_model("nvidia/nemotron-3.5-super-vl:free", "fake-key")
    assert resolved == "nvidia/nemotron-3.5-super-vl:free"
print("STARTUP_DISCOVERY_OPENROUTER_KEEP_OK")

# NVIDIA: configured model not on /models → warn but keep
fake_nvidia_models = {
    "data": [
        {"id": "nvidia/nemotron-3.5-super-vl-120b-a12b"},
        {"id": "nvidia/llama-3.1-nemotron-70b-instruct"},
    ]
}
with patch("urllib.request.urlopen") as m:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_nvidia_models).encode()
    m.return_value.__enter__.return_value = mock_resp
    resolved = _resolve_nvidia_model("meta/llama-3.1-8b-instruct", "fake-key")
    assert resolved == "meta/llama-3.1-8b-instruct"  # keeps configured but warns
print("STARTUP_DISCOVERY_NVIDIA_WARN_OK")

# NVIDIA: configured model on /models → confirmed
with patch("urllib.request.urlopen") as m:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_nvidia_models).encode()
    m.return_value.__enter__.return_value = mock_resp
    resolved = _resolve_nvidia_model("nvidia/nemotron-3.5-super-vl-120b-a12b", "fake-key")
    assert resolved == "nvidia/nemotron-3.5-super-vl-120b-a12b"
print("STARTUP_DISCOVERY_NVIDIA_CONFIRM_OK")

# ── 3. 429 retry-after wait (cap 25s) ───────────────────────────────
from core.brain import _parse_retry_after

class FakeResp:
    def __init__(self, headers):
        self.headers = headers

# Delta-seconds value
assert _parse_retry_after(FakeResp({"Retry-After": "10"}), cap=25) == 10
# Value above cap → capped at 25
assert _parse_retry_after(FakeResp({"Retry-After": "100"}), cap=25) == 25
# No header → 0
assert _parse_retry_after(FakeResp({}), cap=25) == 0
# Invalid value → 0
assert _parse_retry_after(FakeResp({"Retry-After": "abc"}), cap=25) == 0
print("RETRY_AFTER_CAP_OK")

# ── 4. Immediate rotation on 401/403/404/410 ────────────────────────
from core.brain import _call_openai_compat, _call_groq

for status in [401, 403, 404, 410]:
    fake_resp = MagicMock()
    fake_resp.status_code = status
    fake_resp.text = '{"error": {"message": "test error"}}'
    with patch("requests.post", return_value=fake_resp):
        try:
            _call_openai_compat(
                [{"role": "user", "content": "hi"}],
                "test-model",
                "fake-key",
                "https://test.api/v1",
                "TestProvider",
                timeout=5,
            )
            assert False, f"Should have raised for HTTP {status}"
        except RuntimeError as exc:
            assert str(status) in str(exc), f"Expected {status} in error: {exc}"
print("IMMEDIATE_ROTATION_401_403_404_410_OK")

# ── 5. Token budget: max_completion_tokens = 2048 ───────────────────
from core.brain import _call_groq_stream
import inspect
sig = inspect.signature(_call_groq_stream)
assert sig.parameters["max_completion_tokens"].default == 2048, \
    f"Expected 2048, got {sig.parameters['max_completion_tokens'].default}"
print("TOKEN_BUDGET_2048_OK")

# ── 6. 40s per-provider timeout ─────────────────────────────────────
sig_groq = inspect.signature(_call_groq)
assert sig_groq.parameters["timeout"].default == 40, \
    f"Expected 40, got {sig_groq.parameters['timeout'].default}"
sig_or = inspect.signature(_call_openai_compat)
assert sig_or.parameters["timeout"].default == 40, \
    f"Expected 40, got {sig_or.parameters['timeout'].default}"
sig_cluster = inspect.signature(__import__('core.brain', fromlist=['_call_llm_cluster_impl'])._call_llm_cluster_impl)
assert sig_cluster.parameters["timeout"].default == 40, \
    f"Expected 40, got {sig_cluster.parameters['timeout'].default}"
print("TIMEOUT_40S_OK")

# ── 7. Dedup: _vm_dedup_id + chat_id matching ───────────────────────
from core.memory import MemorySystem
mem = MemorySystem(memory_file="")
mem.add_message("test_chat", "user", "hello", dedup_id="abc-123")
msgs = mem.get_chat("test_chat")
assert msgs[-1].get("dedup_id") == "abc-123", f"dedup_id not persisted: {msgs[-1]}"
print("DEDUP_PERSIST_OK")

# ── 8. Health check log ─────────────────────────────────────────────
from io import StringIO
import contextlib
buf = StringIO()
with contextlib.redirect_stdout(buf):
    _startup_health_check()
output = buf.getvalue()
assert "[STARTUP] LLM Provider Health Check" in output
assert "Groq:" in output
assert "OpenRouter:" in output
assert "NVIDIA:" in output
assert "Gemini:" in output
print("HEALTH_CHECK_LOG_OK")

print()
print("ALL_VM_FIX22_MOCKED_TESTS_OK")
