"""Regression tests: completed assistant replies must survive app termination.

Root cause this guards against:
  * The user message was persisted synchronously at the start of the stream.
  * The assistant message was persisted only AFTER the token loop finished.
  * On Android the WebView is killed when the app is removed from Recents, so
    a reply that looked finished could be lost while the user row survived.

The fix checkpoints the assistant reply into durable storage while it streams.
These tests do not need a browser, MongoDB, Pinecone or any LLM provider.

Run: python -m tests.test_stream_persistence
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.brain import MarcusBrain

BEHAVIOR_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "character",
    "marcus",
    "behavior.json",
)


class _DummyRecovery:
    """Stands in for ConversationRecovery; never asks a clarification."""

    def resolve(self, *args, **kwargs):
        return {"needs_recovery": False}


class StreamPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vm_stream_test_")
        self.memory_file = os.path.join(self.tmpdir, "long_term.json")

        self._patches = [
            patch("core.memory.user_memory_collection", lambda: None),
            patch("core.memory.chats_collection", lambda: None),
            patch("core.memory.sessions_collection", lambda: None,
                  create=True),
            patch("core.brain.MarcusBrain._startup_diagnostics", lambda self, config=None: None),
            patch("core.brain.classify_research_intent",
                  lambda message, recent_context=None: {
                      "intent": "none", "domain": "general", "reason": "",
                  }),
            patch("core.brain._get_memory_mgr", lambda: None),
            patch("core.brain._get_knowledge_mgr", lambda: None),
            patch("core.brain._get_recovery", lambda user_id: _DummyRecovery()),
            patch("core.brain._extract_memory_background",
                  lambda memory, user_msg, reply: None),
        ]
        for p in self._patches:
            p.start()

        self.brain = MarcusBrain(
            memory_file=self.memory_file,
            behavior_file=BEHAVIOR_FILE,
        )
        # Keep the suite offline: never build LLM prompts or auto-title calls.
        self.brain._groq_messages = lambda *a, **k: []
        self.brain._generate_title = lambda message: ""
        self.chat_id = "marcus_stream_test"

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def _chat_file(self):
        return os.path.join(self.tmpdir, "chats", self.chat_id + ".json")

    def _durable_messages(self):
        """Read the reply straight from disk, bypassing the in-memory cache."""
        with open(self._chat_file(), "r", encoding="utf-8") as f:
            return json.load(f)

    def _stream_with(self, tokens):
        with patch("core.brain._call_llm_cluster_stream", lambda msgs, **kw: iter(tokens)):
            return list(self.brain.stream_respond("What is the plan?", chat_id=self.chat_id))

    # -- tests ------------------------------------------------------------
    def test_completed_reply_is_persisted_exactly_once(self):
        reply = "The plan is to ship the restore fix."
        tokens = [reply[:12], reply[12:26], reply[26:]]
        out = self._stream_with(tokens)
        self.assertTrue(any(t == reply[:12] for t in out if isinstance(t, str)))

        rows = self._durable_messages()
        roles = [r.get("role") for r in rows]
        self.assertEqual(roles, ["user", "assistant"], "no duplicate assistant rows")
        self.assertEqual(rows[0]["content"], "What is the plan?")
        self.assertEqual(rows[1]["content"], reply)

    def test_partial_reply_is_durable_when_stream_aborts(self):
        """Simulates the WebView being killed before evt.done (client close)."""
        first_half = "Here is the beginning of the answer, "

        def tokens():
            yield first_half
            raise GeneratorExit  # what Flask does when the client disconnects

        with patch("core.brain._call_llm_cluster_stream", lambda msgs, **kw: tokens()):
            gen = self.brain.stream_respond("Explain the fix", chat_id=self.chat_id)
            seen_token = False
            try:
                for item in gen:
                    if isinstance(item, str) and item == first_half:
                        seen_token = True
                        break
            finally:
                gen.close()  # client goes away: no done event, no clean finish

        self.assertTrue(seen_token, "the first token must reach the client")
        rows = self._durable_messages()
        self.assertEqual(rows[0]["role"], "user")
        self.assertEqual(rows[1]["role"], "assistant")
        self.assertEqual(rows[1]["content"], first_half)

    def test_recovery_clarification_is_durable(self):
        class _AmbiguousRecovery:
            def resolve(self, *args, **kwargs):
                return {
                    "needs_recovery": True,
                    "recovered_context": "ctx",
                    "ambiguous": True,
                    "clarification": "Which project did you mean?",
                }

        with patch("core.brain._get_recovery", lambda user_id: _AmbiguousRecovery()):
            list(self.brain.stream_respond("continue", chat_id=self.chat_id))

        rows = self._durable_messages()
        self.assertEqual([r.get("role") for r in rows], ["user", "assistant"])
        self.assertEqual(rows[1]["content"], "Which project did you mean?")

    def test_history_read_returns_both_roles(self):
        self._stream_with(["All ", "done."])
        # New brain instance = process restart (simulates reopening the app).
        fresh = MarcusBrain(memory_file=self.memory_file,
                            behavior_file=BEHAVIOR_FILE)
        history = fresh.memory.get_chat(self.chat_id)
        self.assertEqual([m.get("role") for m in history], ["user", "assistant"])
        self.assertEqual(history[1]["content"], "All done.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
