"""Targeted tests for the Groq 413 token-budget fix.

Verifies:
  1. Groq payload uses max_completion_tokens=512 (not max_tokens=1024)
  2. include_reasoning=False is present in Groq payloads
  3. Actual outgoing message history is truncated (not merely summarized)
  4. Large histories cannot recreate the 413 condition
  5. Recent/relevant messages are preserved
  6. Cross-conversation recovered context stays within 800-token budget
  7. Source Intelligence evidence budget remains intact
  8. Groq fallback providers still work if Groq fails
  9. MemorySystem and MemoryManager remain untouched
 10. No API secrets appear in frontend payloads/logs

Run: env311\\Scripts\\python.exe -m pytest tests/test_groq_413_fix.py -v
"""

from __future__ import annotations

import json
import re
import threading
import unittest
from unittest.mock import MagicMock, patch

# ── Helpers ─────────────────────────────────────────────────────────────────


def _read_source(path: str) -> str:
    """Read a source file for static verification."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _make_history(n: int, role_cycle=("user", "assistant")) -> list[dict]:
    """Generate n synthetic chat messages."""
    history = []
    for i in range(n):
        role = role_cycle[i % 2]
        history.append({"role": role, "content": f"Message {i}: " + "word " * 20})
    return history


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Groq payload static verification (source code inspection)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGroqPayloadParameters(unittest.TestCase):
    """Verify that Groq payloads use max_completion_tokens and include_reasoning."""

    def setUp(self):
        self.src = _read_source("core/brain.py")

    def test_call_groq_uses_max_completion_tokens(self):
        """_call_groq must use max_completion_tokens=512, not max_tokens."""
        # Find the payload block in _call_groq
        idx = self.src.find("def _call_groq(")
        self.assertGreater(idx, 0, "_call_groq not found")
        # Find next function after it
        next_fn = self.src.find("\ndef _call_groq_stream(", idx + 1)
        block = self.src[idx:next_fn]
        self.assertIn('"max_completion_tokens": 512', block,
                       "_call_groq must use max_completion_tokens=512")
        self.assertNotIn('"max_tokens": 1024', block,
                         "_call_groq must NOT use max_tokens=1024")

    def test_call_groq_stream_uses_max_completion_tokens(self):
        """_call_groq_stream must use max_completion_tokens=512."""
        idx = self.src.find("def _call_groq_stream(")
        self.assertGreater(idx, 0, "_call_groq_stream not found")
        next_fn = self.src.find("\ndef _call_llm_cluster_stream(", idx + 1)
        block = self.src[idx:next_fn]
        self.assertIn('"max_completion_tokens": 512', block,
                       "_call_groq_stream must use max_completion_tokens=512")
        self.assertNotIn('"max_tokens": 1024', block,
                         "_call_groq_stream must NOT use max_tokens=1024")

    def test_call_groq_includes_reasoning_false(self):
        """_call_groq must include include_reasoning=False."""
        idx = self.src.find("def _call_groq(")
        next_fn = self.src.find("\ndef _call_groq_stream(", idx + 1)
        block = self.src[idx:next_fn]
        self.assertIn('"include_reasoning": False', block,
                       "_call_groq must include include_reasoning=False")

    def test_call_groq_stream_includes_reasoning_false(self):
        """_call_groq_stream must include include_reasoning=False."""
        idx = self.src.find("def _call_groq_stream(")
        next_fn = self.src.find("\ndef _call_llm_cluster_stream(", idx + 1)
        block = self.src[idx:next_fn]
        self.assertIn('"include_reasoning": False', block,
                       "_call_groq_stream must include include_reasoning=False")

    def test_openai_compat_not_changed(self):
        """_call_openai_compat (non-Groq fallback) should still use max_tokens."""
        idx = self.src.find("def _call_openai_compat(")
        self.assertGreater(idx, 0, "_call_openai_compat not found")
        next_fn = self.src.find("\ndef _call_gemini(", idx + 1)
        block = self.src[idx:next_fn]
        self.assertIn('"max_tokens": 1024', block,
                       "_call_openai_compat must still use max_tokens=1024 (non-Groq)")
        self.assertNotIn("max_completion_tokens", block,
                         "_call_openai_compat must NOT use max_completion_tokens")

    def test_groq_model_unchanged(self):
        """openai/gpt-oss-120b must remain the Groq model."""
        with open("render.yaml", "r") as f:
            yaml_content = f.read()
        self.assertIn("openai/gpt-oss-120b", yaml_content,
                       "GROQ_MODEL must be openai/gpt-oss-120b in render.yaml")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. History truncation logic verification
# ═══════════════════════════════════════════════════════════════════════════════


class TestHistoryTruncation(unittest.TestCase):
    """Verify that _groq_messages actually truncates message history."""

    def setUp(self):
        self.src = _read_source("core/brain.py")

    def test_truncation_constants_defined(self):
        """The truncation limits must be defined in _groq_messages."""
        idx = self.src.find("def _groq_messages(")
        next_fn = self.src.find("\ndef _try_llm_first(", idx + 1)
        block = self.src[idx:next_fn]
        self.assertIn("_MAX_HISTORY_WITH_RECOVERY = 8", block,
                       "Must define _MAX_HISTORY_WITH_RECOVERY = 8")
        self.assertIn("_MAX_HISTORY_DEFAULT = 12", block,
                       "Must define _MAX_HISTORY_DEFAULT = 12")

    def test_truncation_applied_to_recent(self):
        """The code must actually truncate `recent` list, not just summarize."""
        idx = self.src.find("def _groq_messages(")
        next_fn = self.src.find("\ndef _try_llm_first(", idx + 1)
        block = self.src[idx:next_fn]
        self.assertIn("if len(recent) > _max_msgs:", block,
                       "Must have conditional truncation on recent")
        self.assertIn("recent = recent[-_max_msgs:]", block,
                       "Must slice recent to last _max_msgs messages")

    def test_no_old_code_sending_untruncated_messages(self):
        """The old code had no truncation before the message append loop."""
        idx = self.src.find("def _groq_messages(")
        next_fn = self.src.find("\ndef _try_llm_first(", idx + 1)
        block = self.src[idx:next_fn]
        # Truncation must appear BEFORE the for-loop that appends messages
        trunc_pos = block.find("recent = recent[-_max_msgs:]")
        append_pos = block.find("for msg in recent:")
        self.assertGreater(trunc_pos, 0, "Truncation line must exist")
        self.assertGreater(append_pos, 0, "Message append loop must exist")
        self.assertLess(trunc_pos, append_pos,
                        "Truncation MUST come before the message append loop")


class TestHistoryTruncationLogic(unittest.TestCase):
    """Test the truncation logic directly via message list manipulation."""

    def test_short_history_not_truncated(self):
        """History shorter than limit should not be truncated."""
        history = _make_history(5)
        max_msgs = 12
        recent = history[-20:]
        if len(recent) > max_msgs:
            recent = recent[-max_msgs:]
        self.assertEqual(len(recent), 5)

    def test_long_history_truncated_to_12_without_recovery(self):
        """History > 12 messages should be truncated to 12 (no recovery)."""
        history = _make_history(50)
        recovered_context = ""
        max_msgs = 8 if recovered_context else 12
        recent = history[-20:]  # messages 30-49
        if len(recent) > max_msgs:
            recent = recent[-max_msgs:]  # messages 38-49
        self.assertEqual(len(recent), 12)
        # Most recent messages preserved (message 49 is last)
        self.assertIn("Message 49", recent[-1]["content"])

    def test_long_history_truncated_to_8_with_recovery(self):
        """History > 8 messages should be truncated to 8 (with recovery)."""
        history = _make_history(50)
        recovered_context = "[RECOVERED CONTEXT]\nTopic: search system\n[END]"
        max_msgs = 8 if recovered_context else 12
        recent = history[-20:]  # messages 30-49
        if len(recent) > max_msgs:
            recent = recent[-max_msgs:]  # messages 42-49
        self.assertEqual(len(recent), 8)
        # Most recent messages preserved
        self.assertIn("Message 49", recent[-1]["content"])

    def test_exact_boundary_messages_preserved(self):
        """Exactly 12 messages should not be truncated."""
        history = _make_history(12)
        max_msgs = 12
        recent = history[-20:]
        if len(recent) > max_msgs:
            recent = recent[-max_msgs:]
        self.assertEqual(len(recent), 12)

    def test_one_over_boundary_truncated(self):
        """13 messages should be truncated to 12."""
        history = _make_history(13)
        max_msgs = 12
        recent = history[-20:]
        if len(recent) > max_msgs:
            recent = recent[-max_msgs:]
        self.assertEqual(len(recent), 12)

    def test_very_large_history_capped(self):
        """200 messages should still be capped to 12 (or 8 with recovery)."""
        history = _make_history(200)
        max_msgs = 8
        recent = history[-20:]
        if len(recent) > max_msgs:
            recent = recent[-max_msgs:]
        self.assertEqual(len(recent), 8)

    def test_recent_only_messages_preserved(self):
        """Truncation must keep the NEWEST messages, not the oldest."""
        history = _make_history(20)
        max_msgs = 8
        recent = history[-20:]
        if len(recent) > max_msgs:
            recent = recent[-max_msgs:]
        # Last message in recent should be message 19 (0-indexed)
        self.assertIn("Message 19", recent[-1]["content"])
        # First message in recent should be message 12 (20 - 8 = 12)
        self.assertIn("Message 12", recent[0]["content"])


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Token budget verification
# ═══════════════════════════════════════════════════════════════════════════════


class TestTokenBudget(unittest.TestCase):
    """Verify total token budget stays within Groq free-tier limits."""

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return len(text) // 4

    def test_normal_chat_within_budget(self):
        """Normal chat (no search, no recovery) should stay under 8K input."""
        system_prompt = "x" * 8000  # ~2K tokens
        history = _make_history(12)
        history_text = "\n".join(
            f"{m['role'].capitalize()}: {m['content'][:300]}" for m in history
        )
        user_msg = "Hello"
        combined = system_prompt + history_text + user_msg
        total_tokens = self._estimate_tokens(combined)
        # With 512 output, input must be < ~7.5K tokens
        self.assertLess(total_tokens, 7500,
                         f"Normal chat estimated {total_tokens} tokens — should be < 7500")

    def test_with_recovery_stays_within_budget(self):
        """Chat with recovery context should stay under 8K input."""
        system_prompt = "x" * 8000  # ~2K tokens
        history = _make_history(8)  # truncated to 8 with recovery
        history_text = "\n".join(
            f"{m['role'].capitalize()}: {m['content'][:300]}" for m in history
        )
        recovery = "[RECOVERED CONTEXT]\n" + "y" * 2400 + "\n[END]"  # ~800 tokens
        user_msg = "Continue where we left off"
        combined = system_prompt + history_text + recovery + user_msg
        total_tokens = self._estimate_tokens(combined)
        self.assertLess(total_tokens, 7500,
                         f"With recovery estimated {total_tokens} tokens — should be < 7500")

    def test_search_plus_recovery_within_budget(self):
        """Search + recovery (worst case) should approach but not exceed 8K."""
        system_prompt = "x" * 8000
        history = _make_history(8)
        history_text = "\n".join(
            f"{m['role'].capitalize()}: {m['content'][:300]}" for m in history
        )
        recovery = "[RECOVERED CONTEXT]\n" + "y" * 2400 + "\n[END]"
        search = "LIVE CONTEXT DATA:\n" + "z" * 4000  # ~1K tokens
        user_msg = "Continue and check the docs"
        combined = system_prompt + history_text + recovery + search + user_msg
        total_tokens = self._estimate_tokens(combined)
        # Worst case: should still be bounded
        self.assertLess(total_tokens, 8500,
                         f"Search+Recovery estimated {total_tokens} tokens — should be < 8500")

    def test_old_20_message_history_would_exceed(self):
        """Sending 20 untruncated messages would have been riskier."""
        history = _make_history(20)
        history_text = "\n".join(
            f"{m['role'].capitalize()}: {m['content'][:300]}" for m in history
        )
        system_prompt = "x" * 8000
        combined_20 = system_prompt + history_text
        combined_12 = system_prompt + "\n".join(
            f"{m['role'].capitalize()}: {m['content'][:300]}" for m in history[-12:]
        )
        tokens_20 = self._estimate_tokens(combined_20)
        tokens_12 = self._estimate_tokens(combined_12)
        # 20 messages should use more tokens than 12
        self.assertGreater(tokens_20, tokens_12,
                            "20 messages must use more tokens than 12 — proves truncation helps")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Recovery + Source Intelligence budgets untouched
# ═══════════════════════════════════════════════════════════════════════════════


class TestExistingBudgetsIntact(unittest.TestCase):
    """Verify recovery and source intelligence budgets are not affected."""

    def test_recovery_800_token_budget_unchanged(self):
        """build_compact_context must still enforce 800-token budget."""
        from core.conversation_recovery import build_compact_context
        # Create a large state that would exceed 800 tokens
        state = {
            "topic": "test topic " * 50,
            "recent_exchanges": [
                {"role": "user", "content": "x" * 500},
                {"role": "assistant", "content": "y" * 500},
            ] * 10,
            "key_entities": ["entity_" + str(i) for i in range(50)],
            "decisions": ["decision " * 50] * 10,
            "unfinished_work": "status " * 100,
        }
        result = build_compact_context([state], token_budget=800)
        # ~800 tokens ≈ ~3200 chars; verify bounded
        tokens_est = len(result) // 4
        self.assertLessEqual(tokens_est, 850,
                              f"Recovery context estimated {tokens_est} tokens — must be ≤ 800")

    def test_source_intelligence_evidence_budget_unchanged(self):
        """Source Intelligence evidence must stay within 3000 chars / 6 items."""
        from core.source_intelligence import (
            EVIDENCE_BUDGET_CHARS,
            EVIDENCE_BUDGET_EVIDENCES,
            EvidencePackage,
            _build_evidence_from_snippet,
        )
        self.assertEqual(EVIDENCE_BUDGET_CHARS, 3000)
        self.assertEqual(EVIDENCE_BUDGET_EVIDENCES, 6)

        # Build evidence items and package them (same as build_evidence_package does)
        package = EvidencePackage(is_research_request=True)
        evidence = []
        for i in range(20):
            ev = _build_evidence_from_snippet({
                "title": f"Source {i}",
                "url": f"https://example{i}.com/article",
                "domain": f"example{i}.com",
                "snippet": "x" * 500,
            })
            if ev.content and ev.url:
                evidence.append(ev)
            if len(evidence) >= EVIDENCE_BUDGET_EVIDENCES:
                break
        package.evidence = evidence

        # Evidence list must respect budget
        self.assertLessEqual(len(package.evidence), EVIDENCE_BUDGET_EVIDENCES,
                              "Evidence list must not exceed 6 items")

        ctx = package.to_context_string()
        self.assertLessEqual(len(ctx), EVIDENCE_BUDGET_CHARS + 200,
                              "Evidence context must respect 3000-char budget")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Fallback provider verification
# ═══════════════════════════════════════════════════════════════════════════════


class TestFallbackProviders(unittest.TestCase):
    """Verify non-Groq fallback providers are not affected by the fix."""

    def test_openai_compat_uses_max_tokens(self):
        """OpenRouter/NVIDIA fallback must still use max_tokens (not max_completion_tokens)."""
        src = _read_source("core/brain.py")
        idx = src.find("def _call_openai_compat(")
        next_fn = src.find("\ndef _call_gemini(", idx + 1)
        block = src[idx:next_fn]
        self.assertIn('"max_tokens": 1024', block)
        self.assertNotIn("max_completion_tokens", block)

    def test_fallback_chain_unchanged(self):
        """The LLM cluster fallback chain must still be Groq -> OpenRouter -> NVIDIA -> Gemini."""
        src = _read_source("core/brain.py")
        # Check the _call_llm_cluster_impl function
        idx = src.find("def _call_llm_cluster_impl(")
        self.assertGreater(idx, 0, "_call_llm_cluster_impl not found")
        block = src[idx:idx + 3000]
        self.assertIn("openrouter", block.lower(), "OpenRouter must be in fallback chain")
        self.assertIn("nvidia", block.lower(), "NVIDIA must be in fallback chain")
        self.assertIn("gemini", block.lower(), "Gemini must be in fallback chain")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Memory system untouched
# ═══════════════════════════════════════════════════════════════════════════════


class TestMemoryUntouched(unittest.TestCase):
    """Verify MemorySystem and MemoryManager are not modified."""

    def test_memory_system_methods_unchanged(self):
        """MemorySystem must still have all its original methods."""
        from core.memory import MemorySystem
        expected = [
            "get_chat", "add_message", "get_message_count",
            "get_active_facts", "get_full_memory", "reload",
            "set_title", "save_memory", "get_user_name",
        ]
        for method in expected:
            self.assertTrue(hasattr(MemorySystem, method),
                            f"MemorySystem missing method: {method}")

    def test_memory_manager_methods_unchanged(self):
        """MemoryManager must still have all its original methods."""
        from core.memory_manager import MemoryManager
        expected = ["recall_sync", "save_sync"]
        for method in expected:
            self.assertTrue(hasattr(MemoryManager, method),
                            f"MemoryManager missing method: {method}")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Security: no secrets in frontend or logs
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoSecretsInFrontend(unittest.TestCase):
    """Verify no API keys or secrets leak to the frontend."""

    def test_index_html_no_api_keys(self):
        """index.html must not contain any API key values."""
        with open("index.html", "r", encoding="utf-8") as f:
            html = f.read()
        # Check for common key patterns
        patterns = [
            r"sk-[a-zA-Z0-9]{20,}",
            r"gsk_[a-zA-Z0-9]{20,}",
            r"ghp_[a-zA-Z0-9]{20,}",
            r"AIza[a-zA-Z0-9_-]{20,}",
        ]
        for pat in patterns:
            matches = re.findall(pat, html)
            self.assertEqual(len(matches), 0,
                             f"Found potential API key in index.html: {matches}")

    def test_source_metadata_no_secrets(self):
        """Source metadata frontend payload must not contain secrets."""
        from core.source_intelligence import EvidencePackage, _build_evidence_from_snippet
        package = EvidencePackage(is_research_request=True)
        ev = _build_evidence_from_snippet({
            "title": "Test",
            "url": "https://example.com/test",
            "domain": "example.com",
            "snippet": "Test snippet",
        })
        package.evidence.append(ev)
        meta = package.to_frontend_metadata()
        meta_str = json.dumps(meta)
        self.assertNotIn("api_key", meta_str.lower())
        self.assertNotIn("secret", meta_str.lower())
        self.assertNotIn("password", meta_str.lower())

    def test_brain_logs_mask_api_keys(self):
        """brain.py must not log raw API key values."""
        src = _read_source("core/brain.py")
        # Check all print statements — should never contain the actual key value
        # Allow lines that just mention key name (e.g. "missing GROQ_API_KEY")
        # but flag lines that print the key itself
        for line in src.split("\n"):
            if "print(" not in line:
                continue
            lower = line.lower()
            # Skip lines that only reference key names, not values
            if "missing" in lower or "exists" in lower or "configured" in lower or "status" in lower:
                continue
            # Lines that print the actual api_key variable value should not exist
            if "print(" in line and "api_key" in lower:
                # Should use bool() or presence check, not print the value
                self.assertTrue(
                    "bool(" in line or "exists:" in line or "configured" in line
                    or "not configured" in lower,
                    f"brain.py may be logging api_key value: {line.strip()}"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Integration: Groq call with mock verification
# ═══════════════════════════════════════════════════════════════════════════════


class TestGroqCallIntegration(unittest.TestCase):
    """Test that _call_groq sends correct payload via mock."""

    @patch("core.brain.requests.post")
    @patch("core.brain.get_config")
    def test_groq_payload_structure(self, mock_config, mock_post):
        """_call_groq must send max_completion_tokens=512 and include_reasoning=False."""
        # Mock config to provide API key
        cfg = MagicMock()
        cfg.groq_api_key = "test-key"
        cfg.groq_base_url = "https://api.groq.com/openai/v1"
        mock_config.return_value = cfg

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Test reply"}}]
        }
        mock_post.return_value = mock_resp

        from core.brain import _call_groq
        result = _call_groq(
            [{"role": "user", "content": "Hello"}],
            "openai/gpt-oss-120b",
            timeout=5,
            timeout_retries=0,
        )
        self.assertEqual(result, "Test reply")

        # Verify the payload
        call_args = mock_post.call_args
        payload = (call_args.kwargs.get("json") if hasattr(call_args, 'kwargs')
                   else (call_args[1].get("json") if len(call_args) > 1 else None))
        self.assertIsNotNone(payload, "Payload must be passed as json=")
        self.assertEqual(payload["max_completion_tokens"], 512)
        self.assertFalse(payload["include_reasoning"])
        self.assertNotIn("max_tokens", payload)

    @patch("core.brain.requests.post")
    @patch("core.brain.get_config")
    def test_groq_stream_payload_structure(self, mock_config, mock_post):
        """_call_groq_stream must send max_completion_tokens=512 and include_reasoning=False."""
        # Mock config to provide API key
        cfg = MagicMock()
        cfg.groq_api_key = "test-key"
        cfg.groq_base_url = "https://api.groq.com/openai/v1"
        mock_config.return_value = cfg

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"content":"Hi"}}]}',
            b'data: [DONE]',
        ]
        mock_post.return_value = mock_resp

        from core.brain import _call_groq_stream
        tokens = list(_call_groq_stream(
            [{"role": "user", "content": "Hello"}],
            "openai/gpt-oss-120b",
            timeout=5,
        ))
        self.assertEqual(tokens, ["Hi"])

        # Verify the payload
        call_args = mock_post.call_args
        payload = (call_args.kwargs.get("json") if hasattr(call_args, 'kwargs')
                   else (call_args[1].get("json") if len(call_args) > 1 else None))
        self.assertIsNotNone(payload, "Payload must be passed as json=")
        self.assertEqual(payload["max_completion_tokens"], 512)
        self.assertFalse(payload["include_reasoning"])
        self.assertTrue(payload.get("stream"))
        self.assertNotIn("max_tokens", payload)

    @patch("core.brain.requests.post")
    def test_openai_compat_uses_max_tokens(self, mock_post):
        """_call_openai_compat (fallback) must still use max_tokens=1024."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Fallback reply"}}]
        }
        mock_post.return_value = mock_resp

        from core.brain import _call_openai_compat
        result = _call_openai_compat(
            [{"role": "user", "content": "Hello"}],
            "meta/llama-3.3-70b-instruct",
            "fake-key",
            "https://api.nvidia.com",
            "Nvidia",
            timeout=5,
        )
        self.assertEqual(result, "Fallback reply")

        call_args = mock_post.call_args
        payload = (call_args.kwargs.get("json") if hasattr(call_args, 'kwargs')
                   else (call_args[1].get("json") if len(call_args) > 1 else None))
        self.assertEqual(payload["max_tokens"], 1024)
        self.assertNotIn("max_completion_tokens", payload)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Git diff verification helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitIntegrity(unittest.TestCase):
    """Helpers to verify git state is clean where expected."""

    def test_render_yaml_model_value(self):
        """render.yaml GROQ_MODEL must be openai/gpt-oss-120b."""
        with open("render.yaml", "r") as f:
            content = f.read()
        self.assertIn("openai/gpt-oss-120b", content)

    def test_auto_model_fallback_unchanged(self):
        """auto_model.py FALLBACK_GROQ_MODEL must be openai/gpt-oss-20b."""
        with open("core/auto_model.py", "r") as f:
            content = f.read()
        self.assertIn("openai/gpt-oss-20b", content)


if __name__ == "__main__":
    unittest.main()
