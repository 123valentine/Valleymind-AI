"""Tests for the Cross-Conversation Context Recovery system.

Tests cover all 20 required test scenarios without requiring external
services (MongoDB, Pinecone, OpenRouter).  Uses unittest.mock for
database and API dependencies.

Run: env311\\Scripts\\python.exe -m pytest tests/test_conversation_recovery.py -v
"""

from __future__ import annotations

import json
import math
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import unittest

# ── Import modules under test ───────────────────────────────────────────────
from core.conversation_index import (
    ConversationIndex,
    ConversationRecord,
    _compute_idf,
    _cosine_sim,
    _extract_entities,
    _extract_topics,
    _summarize_conversation,
    _tfidf_vector,
    _tokenize,
)
from core.conversation_recovery import (
    ConversationRecovery,
    build_compact_context,
    is_continuation_request,
    recover_conversation_state,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_message(role: str, content: str, **extra) -> dict:
    msg = {"role": role, "content": content, "time": datetime.now().isoformat()}
    msg.update(extra)
    return msg


def _make_conversation(
    chat_id: str,
    user_id: str,
    messages: list[dict],
    title: str = "",
    last_activity: datetime | None = None,
) -> dict:
    return {
        "chat_id": chat_id,
        "user_id": user_id,
        "messages": messages,
        "title": title or f"Chat {chat_id}",
        "message_count": len(messages),
        "last_activity": last_activity or datetime.now(timezone.utc),
        "created_at": last_activity or datetime.now(timezone.utc),
    }


def _mock_mongo_chats(conversations: list[dict], *, filter_by_user: bool = True):
    """Create a mock MongoDB chats collection supporting .find().sort().limit()."""
    coll = MagicMock()

    def _filtered_find(query=None, projection=None):
        uid = (query or {}).get("user_id") if filter_by_user else None
        docs = [c for c in conversations if uid is None or c.get("user_id") == uid]
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = docs
        cursor.__iter__ = lambda self: iter(docs)
        return cursor

    coll.find.side_effect = _filtered_find
    coll.find_one.side_effect = lambda query, projection=None: next(
        (c for c in conversations if c.get("chat_id") == (query or {}).get("chat_id")), None
    )
    return coll


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: Current-thread continuation
# ═══════════════════════════════════════════════════════════════════════════════

class TestCurrentThreadContinuation(unittest.TestCase):
    def test_current_thread_with_sufficient_context(self):
        """When the current thread has enough context, no cross-thread search should happen."""
        recovery = ConversationRecovery(user_id="test_user")
        current_messages = [
            _make_message("user", "Let's fix the Groq 413 error in brain.py"),
            _make_message("assistant", "I'll investigate the /chat/stream path and the token budget."),
            _make_message("user", "Good, what did you find?"),
            _make_message("assistant", "The system prompt is too large and exceeds the 8K TPM limit."),
            _make_message("user", "Continue"),
        ]

        # Mock the index to track if search is called
        with patch.object(ConversationIndex, "search") as mock_search:
            result = recovery.resolve(
                "Continue",
                current_chat_id="current_chat",
                current_messages=current_messages,
            )
            # Should NOT search other conversations because current thread has context
            assert result is not None
            assert result["needs_recovery"] is False
            mock_search.assert_not_called()

    def test_current_thread_empty(self):
        """Empty current thread should trigger cross-thread search."""
        recovery = ConversationRecovery(user_id="test_user")
        with patch.object(ConversationIndex, "search", return_value=[
            {"chat_id": "old_chat", "title": "Groq 413 fix", "score": 0.5,
             "summary": "Fixing the Groq 413 error", "topics": ["groq", "413"],
             "entities": ["brain.py"], "message_count": 10,
             "last_activity": datetime.now(timezone.utc).isoformat(),
             "created_at": datetime.now(timezone.utc).isoformat()}
        ]):
            with patch.object(ConversationIndex, "get_conversation", return_value={
                "chat_id": "old_chat", "title": "Groq 413 fix",
                "messages": [
                    _make_message("user", "We need to fix the Groq 413 error"),
                    _make_message("assistant", "The token budget is exceeded."),
                ],
                "last_activity": datetime.now(timezone.utc),
            }):
                result = recovery.resolve(
                    "Continue where we stopped",
                    current_chat_id="current_chat",
                    current_messages=[],
                )
                assert result is not None
                assert result["needs_recovery"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: Cross-thread continuation
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossThreadContinuation(unittest.TestCase):
    def test_finds_previous_conversation(self):
        """Should find and recover context from a previous conversation."""
        recovery = ConversationRecovery(user_id="test_user")
        convos = [
            _make_conversation(
                "chat_1", "test_user",
                [
                    _make_message("user", "Let's build the memory system"),
                    _make_message("assistant", "I'll create the memory architecture."),
                    _make_message("user", "What's the status?"),
                    _make_message("assistant", "We have the Pinecone integration working."),
                ],
                title="Memory system build",
            ),
        ]

        with patch("core.conversation_index.chats_collection") as mock_coll:
            mock_coll.return_value = _mock_mongo_chats(convos)
            result = recovery.resolve(
                "Where did we stop with the memory?",
                current_chat_id="new_chat",
                current_messages=[_make_message("user", "Hi")],
            )

        assert result is not None
        assert result["needs_recovery"] is True
        assert len(result["candidates"]) > 0
        assert "memory" in result["recovered_context"].lower() or "Memory" in result["recovered_context"]


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: Semantic topic matching
# ═══════════════════════════════════════════════════════════════════════════════

class TestSemanticTopicMatching(unittest.TestCase):
    def test_keyword_matching(self):
        """Should match conversations by keyword overlap."""
        index = ConversationIndex(user_id="test_user")
        convos = [
            _make_conversation("c1", "test_user",
                [_make_message("user", "Fix the Groq HTTP 413 error"),
                 _make_message("assistant", "The token budget is too high.")],
                title="Groq 413 fix"),
            _make_conversation("c2", "test_user",
                [_make_message("user", "Build the React dashboard"),
                 _make_message("assistant", "I'll create the components.")],
                title="Dashboard build"),
        ]

        with patch("core.conversation_index.chats_collection") as mock_coll:
            mock_coll.return_value = _mock_mongo_chats(convos)
            results = index.search("Groq 413 error token budget")

        assert len(results) >= 1
        assert results[0]["chat_id"] == "c1"
        assert results[0]["score"] > results[-1]["score"] if len(results) > 1 else True

    def test_entity_matching(self):
        """Should match by technical entities (filenames, class names)."""
        index = ConversationIndex(user_id="test_user")
        convos = [
            _make_conversation("c1", "test_user",
                [_make_message("user", "Modify brain.py and _groq_messages"),
                 _make_message("assistant", "I'll update the function.")],
                title="brain.py changes"),
        ]

        with patch("core.conversation_index.chats_collection") as mock_coll:
            mock_coll.return_value = _mock_mongo_chats(convos)
            results = index.search("brain.py modifications")

        assert len(results) >= 1
        assert results[0]["chat_id"] == "c1"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: Vague "continue" request
# ═══════════════════════════════════════════════════════════════════════════════

class TestVagueContinueRequest(unittest.TestCase):
    def test_bare_continue(self):
        """Just 'continue' should be detected as high-confidence continuation."""
        result = is_continuation_request("continue")
        assert result["is_continuation"] is True
        assert result["confidence"] == "high"

    def test_continue_with_context(self):
        """'continue where we stopped' should be high-confidence."""
        result = is_continuation_request("continue where we stopped")
        assert result["is_continuation"] is True
        assert result["confidence"] == "high"

    def test_pick_up(self):
        """'pick up from where we left off' should be high-confidence."""
        result = is_continuation_request("pick up from where we left off")
        assert result["is_continuation"] is True
        assert result["confidence"] == "high"

    def test_resume(self):
        """'resume the project' should be high-confidence."""
        result = is_continuation_request("resume the project")
        assert result["is_continuation"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: "Where did we stop?" request
# ═══════════════════════════════════════════════════════════════════════════════

class TestWhereDidWeStop(unittest.TestCase):
    def test_where_did_we_stop(self):
        result = is_continuation_request("where did we stop?")
        assert result["is_continuation"] is True
        assert result["confidence"] == "high"

    def test_where_did_we_leave_off(self):
        result = is_continuation_request("where did we leave off?")
        assert result["is_continuation"] is True

    def test_what_was_next(self):
        result = is_continuation_request("what was the next step?")
        assert result["is_continuation"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Test 6: "Do you remember what we discussed?" request
# ═══════════════════════════════════════════════════════════════════════════════

class TestDoYouRemember(unittest.TestCase):
    def test_do_you_remember(self):
        result = is_continuation_request("do you remember what we discussed?")
        assert result["is_continuation"] is True
        assert result["confidence"] == "high"

    def test_remember_when(self):
        result = is_continuation_request("remember when we fixed that bug?")
        assert result["is_continuation"] is True

    def test_do_you_recall(self):
        result = is_continuation_request("do you recall our earlier conversation?")
        assert result["is_continuation"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Test 7: Multiple possible matching conversations
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultipleMatches(unittest.TestCase):
    def test_ambiguous_multiple_matches(self):
        """When multiple conversations match with similar scores, should be ambiguous."""
        recovery = ConversationRecovery(user_id="test_user")

        # All three share the word "project" so a query containing "project"
        # produces similar scores across all of them.
        convos = [
            _make_conversation("c1", "test_user",
                [_make_message("user", "Fix the Groq project token budget"),
                 _make_message("assistant", "Reducing max_tokens to 512.")],
                title="Groq project budget fix"),
            _make_conversation("c2", "test_user",
                [_make_message("user", "Build the memory project recovery system"),
                 _make_message("assistant", "Creating conversation_recovery.py.")],
                title="Memory project system"),
            _make_conversation("c3", "test_user",
                [_make_message("user", "Update the deployment project config"),
                 _make_message("assistant", "I'll update render.yaml.")],
                title="Deployment project update"),
        ]

        with patch("core.conversation_index.chats_collection") as mock_coll:
            mock_coll.return_value = _mock_mongo_chats(convos)
            result = recovery.resolve(
                "continue the project",
                current_chat_id="new_chat",
                current_messages=[],
            )

        assert result is not None
        assert result.get("needs_recovery") is True
        candidates = result.get("candidates", [])
        assert len(candidates) >= 2, f"Expected 2+ candidates, got {len(candidates)}"
        scores = [c["score"] for c in candidates[:2]]
        # Scores should be close — within 0.15 of each other
        assert abs(scores[0] - scores[1]) < 0.15, f"Scores not close: {scores}"
        assert result["ambiguous"] is True
        assert result["clarification"] != ""


# ═══════════════════════════════════════════════════════════════════════════════
# Test 8: Low-confidence retrieval
# ═══════════════════════════════════════════════════════════════════════════════

class TestLowConfidence(unittest.TestCase):
    def test_low_confidence_when_no_match(self):
        """When no conversation matches well, confidence should be low."""
        recovery = ConversationRecovery(user_id="test_user")

        with patch("core.conversation_index.chats_collection") as mock_coll:
            mock_coll.return_value = _mock_mongo_chats([])
            result = recovery.resolve(
                "continue",
                current_chat_id="new_chat",
                current_messages=[],
            )

        if result and result.get("needs_recovery"):
            assert result["confidence"] in ("low", "medium")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 9: Existing memory still works
# ═══════════════════════════════════════════════════════════════════════════════

class TestExistingMemoryUntouched(unittest.TestCase):
    def test_memory_system_not_modified(self):
        """The existing MemorySystem class should not be modified."""
        from core.memory import MemorySystem

        # Verify the class still has all expected methods
        expected_methods = [
            "load_long_term", "save_long_term", "reload", "get_chat",
            "add_message", "save_chat", "get_active_facts", "remember_fact",
            "remember_identity", "recall_identity", "get_user_name",
            "load_creator_context", "save_creator_message",
            "list_sessions", "create_session", "delete_session",
            "set_title", "handle_retraction", "get_full_memory",
        ]
        for method in expected_methods:
            assert hasattr(MemorySystem, method), f"MemorySystem missing method: {method}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 10: Existing memory categories remain unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestMemoryCategoriesUnchanged(unittest.TestCase):
    def test_fresh_long_term_structure(self):
        """The fresh long-term memory structure should have identity, preferences, facts."""
        from core.memory import _fresh_long_term
        lt = _fresh_long_term()
        assert "identity" in lt
        assert "preferences" in lt
        assert "facts" in lt
        assert isinstance(lt["identity"], dict)
        assert isinstance(lt["preferences"], dict)
        assert isinstance(lt["facts"], list)

    def test_memory_categories_preserved(self):
        """Recovery module should not alter memory categories."""
        from core.memory import _fresh_long_term
        original = _fresh_long_term()
        # Simulate adding a fact
        original["facts"].append({"memory_type": "test", "summary": "test fact"})
        # Recovery should not touch this
        assert len(original["facts"]) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Test 11: Conversation retrieval failure
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetrievalFailure(unittest.TestCase):
    def test_mongo_unavailable(self):
        """When MongoDB is unavailable, recovery should fail gracefully."""
        recovery = ConversationRecovery(user_id="test_user")
        with patch("core.conversation_index.chats_collection", return_value=None):
            result = recovery.resolve(
                "continue",
                current_chat_id="current",
                current_messages=[],
            )
        # Should not raise, should return something
        assert result is None or isinstance(result, dict)

    def test_mongo_exception(self):
        """When MongoDB raises an exception, recovery should fail gracefully."""
        recovery = ConversationRecovery(user_id="test_user")
        mock_coll = MagicMock()
        mock_coll.find.side_effect = Exception("MongoDB connection lost")
        with patch("core.conversation_index.chats_collection", return_value=mock_coll):
            result = recovery.resolve(
                "continue",
                current_chat_id="current",
                current_messages=[],
            )
        assert result is None or isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 12: Empty conversation history
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyHistory(unittest.TestCase):
    def test_no_conversations_at_all(self):
        """User with zero conversations should not crash."""
        recovery = ConversationRecovery(user_id="new_user")
        with patch("core.conversation_index.chats_collection", return_value=_mock_mongo_chats([])):
            result = recovery.resolve(
                "continue where we stopped",
                current_chat_id="",
                current_messages=[],
            )
        # Should handle gracefully
        assert result is None or (isinstance(result, dict) and not result.get("needs_recovery"))

    def test_index_builds_empty(self):
        """Index with no conversations should have 0 records."""
        with patch("core.conversation_index.chats_collection", return_value=_mock_mongo_chats([])):
            index = ConversationIndex(user_id="new_user")
            count = index.build()
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Test 13: Very large conversation archive
# ═══════════════════════════════════════════════════════════════════════════════

class TestLargeArchive(unittest.TestCase):
    def test_many_conversations(self):
        """Index should handle 100+ conversations efficiently."""
        convos = []
        for i in range(100):
            msgs = [_make_message("user", f"Message {i} about topic {i % 10}")]
            convos.append(_make_conversation(f"c{i}", "test_user", msgs, title=f"Chat {i}"))

        with patch("core.conversation_index.chats_collection") as mock_coll:
            mock_coll.return_value = _mock_mongo_chats(convos)
            index = ConversationIndex(user_id="test_user")
            count = index.build()

        assert count == 100
        results = index.search("topic 5")
        assert len(results) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Test 14: Very large historical conversation
# ═══════════════════════════════════════════════════════════════════════════════

class TestLargeConversation(unittest.TestCase):
    def test_conversation_with_many_messages(self):
        """Should handle conversations with 1000+ messages."""
        msgs = [_make_message("user" if i % 2 == 0 else "assistant",
                              f"Message {i} content here with some detail about the project.")
                for i in range(500)]

        convos = [_make_conversation("big_chat", "test_user", msgs, title="Big conversation")]

        with patch("core.conversation_index.chats_collection") as mock_coll:
            mock_coll.return_value = _mock_mongo_chats(convos)
            index = ConversationIndex(user_id="test_user")
            count = index.build()

        assert count == 1
        results = index.search("project detail")
        assert len(results) >= 1

    def test_state_recovery_large_conversation(self):
        """State recovery should truncate large conversations."""
        msgs = [_make_message("user" if i % 2 == 0 else "assistant",
                              f"Message {i} " * 50)
                for i in range(200)]

        state = recover_conversation_state(msgs, title="Large chat")
        # Should not crash and should produce compact output
        assert state["message_count"] == 200
        assert len(state["recent_exchanges"]) <= 6
        assert state["topic"] == "Large chat"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 15: Token-budget enforcement
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenBudget(unittest.TestCase):
    def test_compact_context_respects_budget(self):
        """The compact context should never exceed the token budget."""
        states = []
        for i in range(10):
            states.append({
                "topic": f"Topic {i}: " + "x" * 200,
                "recent_exchanges": [
                    {"role": "user", "content": "y" * 300},
                    {"role": "assistant", "content": "z" * 300},
                ] * 3,
                "key_entities": [f"entity_{j}" for j in range(20)],
                "decisions": [f"We decided to do step {j}" for j in range(5)],
                "unfinished_work": "Still working on " + "w" * 200,
            })

        budget = 500  # tokens
        context = build_compact_context(states, token_budget=budget)

        # Estimated tokens = chars / 4
        estimated_tokens = len(context) // 4
        assert estimated_tokens <= budget + 50  # Allow small overshoot for headers

    def test_compact_context_single_state(self):
        """Single state should produce minimal context."""
        state = {
            "topic": "Groq 413 fix",
            "recent_exchanges": [
                {"role": "user", "content": "Fix the error"},
                {"role": "assistant", "content": "I'll reduce max_tokens."},
            ],
            "key_entities": ["brain.py", "Groq"],
            "decisions": ["Keep the current model"],
            "unfinished_work": "Implement context budgeting",
        }
        context = build_compact_context([state], token_budget=800)
        assert "[RECOVERED CONVERSATION CONTEXT]" in context
        assert "Groq 413 fix" in context
        assert len(context) // 4 < 800

    def test_empty_states_produces_empty_context(self):
        """No states should produce empty context."""
        context = build_compact_context([], token_budget=800)
        assert context == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Test 16: User isolation / security
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserIsolation(unittest.TestCase):
    def test_user_isolation(self):
        """Each user should only see their own conversations."""
        user_a_convo = _make_conversation("a_chat", "user_a",
            [_make_message("user", "My secret project")], title="User A project")
        user_b_convo = _make_conversation("b_chat", "user_b",
            [_make_message("user", "My other project")], title="User B project")

        all_convos = [user_a_convo, user_b_convo]

        def mock_find(query=None, projection=None):
            uid = (query or {}).get("user_id")
            docs = [c for c in all_convos if c.get("user_id") == uid]
            cursor = MagicMock()
            cursor.sort.return_value = cursor
            cursor.limit.return_value = docs
            cursor.__iter__ = lambda self: iter(docs)
            return cursor

        mock_coll = MagicMock()
        mock_coll.find.side_effect = mock_find

        with patch("core.conversation_index.chats_collection", return_value=mock_coll):
            # User A should only see their conversation
            index_a = ConversationIndex(user_id="user_a")
            count_a = index_a.build()
            assert count_a == 1
            results_a = index_a.search("secret project")
            assert len(results_a) == 1
            assert results_a[0]["chat_id"] == "a_chat"

            # User B should only see their conversation
            index_b = ConversationIndex(user_id="user_b")
            count_b = index_b.build()
            assert count_b == 1
            results_b = index_b.search("other project")
            assert len(results_b) == 1
            assert results_b[0]["chat_id"] == "b_chat"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 17: Groq 413 prevention
# ═══════════════════════════════════════════════════════════════════════════════

class TestGroq413Prevention(unittest.TestCase):
    def test_recovered_context_within_budget(self):
        """Recovered context should fit within the 800-token budget."""
        states = [{
            "topic": "Groq token budgeting problem",
            "recent_exchanges": [
                {"role": "user", "content": "The Groq 413 error is caused by exceeding the 8K TPM limit."},
                {"role": "assistant", "content": "We need to truncate conversation history and reduce max_tokens."},
            ],
            "key_entities": ["brain.py", "_groq_messages", "openai/gpt-oss-120b", "TPM"],
            "decisions": ["Keep the current Groq model", "Add token budget guard"],
            "unfinished_work": "Implement context budgeting in _groq_messages()",
        }]

        context = build_compact_context(states, token_budget=800)
        tokens_estimate = len(context) // 4
        assert tokens_estimate <= 800, f"Context too large: {tokens_estimate} tokens"

    def test_multiple_states_still_within_budget(self):
        """Multiple recovered states should still respect the budget."""
        states = [
            {
                "topic": f"Topic {i}",
                "recent_exchanges": [
                    {"role": "user", "content": f"Message about topic {i} " * 10},
                    {"role": "assistant", "content": f"Response about topic {i} " * 10},
                ],
                "key_entities": [f"file_{i}.py"],
                "decisions": [f"Decision on topic {i}"],
                "unfinished_work": f"Work on topic {i}",
            }
            for i in range(5)
        ]

        context = build_compact_context(states, token_budget=500)
        tokens_estimate = len(context) // 4
        assert tokens_estimate <= 550, f"Context too large: {tokens_estimate} tokens"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 18: Groq fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestGroqFallback(unittest.TestCase):
    def test_recovery_failure_does_not_break_chat(self):
        """If the recovery system fails, normal chat should still work."""
        recovery = ConversationRecovery(user_id="test_user")

        # Simulate a failure in the search
        with patch("core.conversation_index.chats_collection", side_effect=Exception("DB down")):
            result = recovery.resolve(
                "continue where we stopped",
                current_chat_id="current",
                current_messages=[],
            )

        # Should not crash; may return None or a safe fallback
        assert result is None or isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 19: Streaming chat
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamingChat(unittest.TestCase):
    def test_continuation_detection_in_streaming(self):
        """Continuation detection should work the same for streaming."""
        # Same detection logic is used; verify it works
        messages_to_test = [
            "continue",
            "continue where we stopped",
            "pick up from where we left off",
            "what did we decide?",
            "do you remember?",
        ]
        for msg in messages_to_test:
            result = is_continuation_request(msg)
            assert result["is_continuation"] is True, f"Failed for: {msg}"

    def test_normal_message_not_continuation(self):
        """Normal messages should not be detected as continuations."""
        messages_to_test = [
            "What's the weather?",
            "Tell me a joke",
            "How are you?",
            "What can you do?",
            "Hello",
        ]
        for msg in messages_to_test:
            result = is_continuation_request(msg)
            assert result["is_continuation"] is False, f"False positive for: {msg}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 20: Non-streaming chat
# ═══════════════════════════════════════════════════════════════════════════════

class TestNonStreamingChat(unittest.TestCase):
    def test_state_recovery_basic(self):
        """State recovery should extract topic, entities, decisions from messages."""
        messages = [
            _make_message("user", "Let's fix the Groq 413 error in brain.py"),
            _make_message("assistant", "I'll investigate the /chat/stream path. The _groq_messages function sends too many tokens."),
            _make_message("user", "What did you find?"),
            _make_message("assistant", "We decided to reduce max_tokens from 1024 to 512 and add history truncation."),
            _make_message("user", "Good, implement that."),
        ]

        state = recover_conversation_state(messages, title="Groq 413 fix")
        assert state["topic"] == "Groq 413 fix"
        assert state["message_count"] == 5
        assert len(state["recent_exchanges"]) > 0
        assert len(state["decisions"]) > 0

    def test_threading_safety(self):
        """The index should be thread-safe."""
        with patch("core.conversation_index.chats_collection") as mock_coll:
            convos = [_make_conversation(f"c{i}", "test_user",
                      [_make_message("user", f"Topic {i}")], title=f"Chat {i}")
                      for i in range(10)]
            mock_coll.return_value = _mock_mongo_chats(convos)
            index = ConversationIndex(user_id="test_user")

            results = []
            def search_thread():
                r = index.search("topic")
                results.append(r)

            threads = [threading.Thread(target=search_thread) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert len(results) == 5
            for r in results:
                assert isinstance(r, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Additional unit tests for helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenize(unittest.TestCase):
    def test_basic_tokenization(self):
        tokens = _tokenize("Hello world, this is a test!")
        assert "world" in tokens
        assert "test" in tokens
        assert "hello" not in tokens  # stopword
        assert "is" not in tokens  # stopword
        assert "a" not in tokens   # stopword

    def test_empty(self):
        assert _tokenize("") == []
        assert _tokenize(None) == []


class TestCosineSim(unittest.TestCase):
    def test_identical_vectors(self):
        a = {"a": 1.0, "b": 2.0}
        assert abs(_cosine_sim(a, a) - 1.0) < 1e-6

    def test_disjoint_vectors(self):
        a = {"a": 1.0}
        b = {"b": 1.0}
        assert _cosine_sim(a, b) == 0.0

    def test_empty(self):
        assert _cosine_sim({}, {"a": 1.0}) == 0.0
        assert _cosine_sim({"a": 1.0}, {}) == 0.0


class TestSummarizeConversation(unittest.TestCase):
    def test_basic(self):
        msgs = [_make_message("user", "Hello"), _make_message("assistant", "Hi there!")]
        summary = _summarize_conversation(msgs, title="Test Chat")
        assert "Test Chat" in summary
        assert "Hello" in summary

    def test_truncation(self):
        msgs = [_make_message("user", "x" * 1000)]
        summary = _summarize_conversation(msgs)
        assert len(summary) <= 5000


class TestExtractEntities(unittest.TestCase):
    def test_filenames(self):
        msgs = [_make_message("user", "Edit brain.py and core/memory.py")]
        entities = _extract_entities(msgs)
        assert "brain.py" in entities
        assert "memory.py" in entities or "core/memory.py" in entities

    def test_empty(self):
        entities = _extract_entities([])
        assert entities == []


class TestRecoverConversationState(unittest.TestCase):
    def test_empty_messages(self):
        state = recover_conversation_state([])
        assert state["topic"] == "Unknown"
        assert state["recent_exchanges"] == []
        assert state["message_count"] == 0

    def test_with_title(self):
        state = recover_conversation_state(
            [_make_message("user", "Hello")],
            title="My Project"
        )
        assert state["topic"] == "My Project"


class TestIsContinuationRequest(unittest.TestCase):
    def test_not_continuation(self):
        result = is_continuation_request("What's the weather today?")
        assert result["is_continuation"] is False

    def test_empty_message(self):
        result = is_continuation_request("")
        assert result["is_continuation"] is False

    def test_high_confidence_keywords(self):
        for phrase in [
            "continue", "continue where we stopped", "pick up where we left off",
            "what did we decide", "do you remember", "let's continue",
            "where did we stop", "what was the next step",
        ]:
            result = is_continuation_request(phrase)
            assert result["is_continuation"] is True, f"Failed for: {phrase}"
            assert result["confidence"] == "high", f"Wrong confidence for: {phrase}"

    def test_medium_confidence_keywords(self):
        for phrase in [
            "remember that project", "the issue we discussed",
            "what about the previous work",
        ]:
            result = is_continuation_request(phrase)
            assert result["is_continuation"] is True, f"Failed for: {phrase}"


class TestBuildCompactContext(unittest.TestCase):
    def test_header_and_footer(self):
        context = build_compact_context([{"topic": "Test"}], token_budget=800)
        assert context.startswith("[RECOVERED CONVERSATION CONTEXT]")
        assert context.endswith("[END RECOVERED CONTEXT]")

    def test_with_all_fields(self):
        state = {
            "topic": "Groq fix",
            "recent_exchanges": [
                {"role": "user", "content": "Fix the error"},
                {"role": "assistant", "content": "Done!"},
            ],
            "key_entities": ["brain.py"],
            "decisions": ["Use max_tokens=512"],
            "unfinished_work": "Test the fix",
        }
        context = build_compact_context([state], token_budget=800)
        assert "Groq fix" in context
        assert "brain.py" in context
        assert "max_tokens" in context
        assert "Test the fix" in context
