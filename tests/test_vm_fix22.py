"""vm-fix22 mocked tests: fallback keys, capability routing, dedup."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

# ── 1. Fallback key: 401/403 → retry once with fallback key ───────────
from core.brain import _call_openai_compat, _call_gemini, _call_groq

class FakeResp:
    def __init__(self, status_code, text='{"error": {"message": "test"}}'):
        self.status_code = status_code
        self.text = text
        self._json = json.loads(text) if text else {}

    def json(self):
        return self._json

# OpenRouter 401 → fallback key works
fake_401_then_200 = [
    FakeResp(401),
    FakeResp(200, json.dumps({"choices": [{"message": {"content": "fallback worked"}}]})),
]
with patch("requests.post", side_effect=fake_401_then_200):
    result = _call_openai_compat(
        [{"role": "user", "content": "hi"}], "test-model", "dead-key",
        "https://test.api/v1", "OpenRouter", timeout=5,
        fallback_key="live-key",
    )
    assert result == "fallback worked", f"Expected 'fallback worked', got {result!r}"
print("FALLBACK_OPENROUTER_401_OK")

# OpenRouter 403 → no fallback → raise
fake_403 = [FakeResp(403)]
with patch("requests.post", side_effect=fake_403):
    try:
        _call_openai_compat(
            [{"role": "user", "content": "hi"}], "test-model", "dead-key",
            "https://test.api/v1", "OpenRouter", timeout=5,
            fallback_key="",
        )
        assert False, "Should have raised for HTTP 403"
    except RuntimeError as exc:
        assert "403" in str(exc)
print("FALLBACK_OPENROUTER_403_NO_FALLBACK_OK")

# Gemini 401 → fallback key works
fake_gemini_401_then_200 = [
    FakeResp(401),
    FakeResp(200, json.dumps({"candidates": [{"content": {"parts": [{"text": "gemini fallback"}]}}]})),
]
with patch("requests.post", side_effect=fake_gemini_401_then_200):
    result = _call_gemini(
        [{"role": "user", "content": "hi"}], "gemini-3.8-flash", "dead-key",
        timeout=5, fallback_key="live-key",
    )
    assert result == "gemini fallback", f"Expected 'gemini fallback', got {result!r}"
print("FALLBACK_GEMINI_401_OK")

# ── 2. Capability detection ───────────────────────────────────────────
from core.brain import _detect_capability, _has_image_in_messages, _estimate_messages_tokens

# Image in messages → "image"
msgs_with_image = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}}]}]
assert _detect_capability(msgs_with_image) == "image"
assert _has_image_in_messages(msgs_with_image) == True
print("CAPABILITY_IMAGE_OK")

# Large prompt (>5000 tokens ≈ 20000 chars) → "large"
large_msg = [{"role": "user", "content": "x" * 25000}]
assert _detect_capability(large_msg) == "large"
print("CAPABILITY_LARGE_OK")

# Coding keywords → "coding"
coding_msg = [{"role": "user", "content": "Write a Python function to check if a number is prime"}]
assert _detect_capability(coding_msg) == "coding"
print("CAPABILITY_CODING_OK")

# Plain text → "text"
text_msg = [{"role": "user", "content": "Hello, how are you?"}]
assert _detect_capability(text_msg) == "text"
print("CAPABILITY_TEXT_OK")

# ── 3. Dedup: _vm_dedup_id + chat_id matching ─────────────────────────
from core.memory import MemorySystem
mem = MemorySystem(memory_file="")
mem.add_message("test_chat", "user", "hello", dedup_id="abc-123")
msgs = mem.get_chat("test_chat")
assert msgs[-1].get("dedup_id") == "abc-123", f"dedup_id not persisted: {msgs[-1]}"
print("DEDUP_PERSIST_OK")

print()
print("ALL_VM_FIX22_MOCKED_TESTS_OK")
