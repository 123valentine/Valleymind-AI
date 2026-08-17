"""Cross-conversation context recovery.

Detects when a user message depends on previous conversation context,
searches the conversation archive, recovers the relevant state, and
builds a compact context block that can be injected into the LLM
prompt without exceeding token budgets.

This module is PURELY ADDITIVE — it never modifies the existing
MemorySystem, MemoryManager, or any stored data.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Optional

from core.conversation_index import ConversationIndex, _tokenize


# ── Continuation / context-dependent detection ──────────────────────────────
#
# Patterns are matched case-insensitively against the user message.
# We separate exact-phrase matches (high confidence) from keyword
# matches (medium confidence) and semantic fallbacks (low confidence).

_EXACT_CONTINUATION_PATTERNS = re.compile(
    r"\b("
    r"continue(?:\s+(?:where|from|that|the|with|doing|working))?"
    r"|pick\s+(?:up|it)\s+(?:where|from|it)"
    r"|go\s+back\s+(?:to|and)"
    r"|where\s+(?:did|do)\s+(?:we|i)\s+(?:leave|stop|end|pause|finish)"
    r"|what\s+(?:did|do)\s+(?:we|i)\s+(?:decide|decided|agree|agree|discuss)"
    r"|what\s+were\s+(?:we|i)\s+(?:doing|working|talking|discussing)"
    r"|what\s+(?:was|is)\s+(?:the\s+)?(?:next|status|state|progress)"
    r"|what\s+(?:was|is)\s+that\s+(?:thing|problem|issue|project|topic)"
    r"|what\s+did\s+we\s+last\s+(?:talk|discuss|work)"
    r"|let'?s\s+(?:continue|go\s+back|keep\s+going|resume|pick\s+up)"
    r"|do\s+you\s+remember(?:\s+(?:what|when|where|how|the))?"
    r"|do\s+you\s+recall"
    r"|remember\s+(?:when|what|where|the|that|how)"
    r"|bring\s+(?:me\s+)?(?:up\s+to\s+speed|back)"
    r"|catch\s+me\s+up"
    r"|refresh(?:\s+(?:my|your))?\s+(?:memory|recollection)"
    r"|fill\s+me\s+in"
    r"|recap(?:\s+(?:what|how|the))?"
    r"|summary\s+of\s+(?:our|the|what)"
    r"|what'?s\s+(?:the\s+)?(?:status|progress|state)\s+(?:of|on|with)"
    r"|where\s+did\s+we\s+(?:stop|end|leave)"
    r"|continue\s+(?:the\s+)?(?:development|work|project|fix|build|implementation|investigation|analysis|discussion|implementation|implementation)"
    r"|resume\s+(?:the|our|that|this)"
    r")\b",
    re.IGNORECASE,
)

# Weaker signals — need surrounding context to confirm
_KEYWORD_CONTINUATION_SIGNALS = re.compile(
    r"\b(continue|resume|remember|recall|decided|left\s+off|stopped|"
    r"where\s+were\s+we|previous|earlier|last\s+time|before|yesterday|"
    r"the\s+project|the\s+issue|the\s+problem|the\s+fix|the\s+feature|"
    r"the\s+upgrade|the\s+change|that\s+thing|that\s+discussion)\b",
    re.IGNORECASE,
)

# Phrases that are clearly NOT continuations (greetings, standalone questions)
_NOT_CONTINUATION = re.compile(
    r"^(hi|hello|hey|good\s+(?:morning|afternoon|evening)|yo|sup|what'?s\s+up|"
    r"how\s+(?:are|r)\s+you|how'?s\s+it\s+going|what'?s\s+new|"
    r"what'?s\s+the\s+weather|tell\s+me\s+a\s+joke|what\s+can\s+you\s+do|"
    r"help|help\s+me|who\s+(?:are|r)\s+you|what\s+(?:are|r)\s+you)\s*[!.?]*$",
    re.IGNORECASE,
)


def is_continuation_request(message: str, current_history: list[dict] | None = None) -> dict:
    """Determine if a user message is a continuation/context-dependent request.

    Returns a dict with:
        is_continuation: bool
        confidence: "high" | "medium" | "low"
        intent: str (brief description of what the user wants)
    """
    text = (message or "").strip()
    if not text:
        return {"is_continuation": False, "confidence": "low", "intent": ""}

    # Check for clear non-continuation patterns first
    if _NOT_CONTINUATION.match(text):
        return {"is_continuation": False, "confidence": "low", "intent": ""}

    # Exact phrase match — high confidence
    if _EXACT_CONTINUATION_PATTERNS.search(text):
        # Short messages like just "continue" are highest confidence
        if len(text.split()) <= 4:
            return {"is_continuation": True, "confidence": "high", "intent": "continue_previous"}
        return {"is_continuation": True, "confidence": "high", "intent": "continue_previous"}

    # Keyword signal match — medium confidence
    keywords_found = _KEYWORD_CONTINUATION_SIGNALS.findall(text.lower())
    if keywords_found and len(text.split()) <= 15:
        return {"is_continuation": True, "confidence": "medium", "intent": "context_dependent"}

    # If the message is very short (< 5 words) and contains a pronoun reference
    # that might refer to previous context ("what about it?", "and the other thing?")
    words = text.split()
    if len(words) <= 5:
        pronoun_refs = {"it", "that", "this", "them", "those", "there"}
        content_words = [w.strip(".,!?;:'\"") for w in words if w.strip(".,!?;:'\"").lower() not in _pronoun_stopwords()]
        if len(content_words) <= 2 and any(w.lower() in pronoun_refs for w in content_words):
            return {"is_continuation": True, "confidence": "medium", "intent": "reference_resolution"}

    return {"is_continuation": False, "confidence": "low", "intent": ""}


def _pronoun_stopwords() -> set:
    return {"is", "are", "was", "were", "the", "a", "an", "and", "or", "but",
            "what", "about", "do", "does", "did", "how", "why", "when", "where",
            "can", "could", "would", "should", "will", "shall", "may", "might"}


# ── Conversation state recovery ─────────────────────────────────────────────

def recover_conversation_state(messages: list[dict], title: str = "") -> dict:
    """Extract a compact state summary from a conversation's messages.

    Returns a dict with:
        topic: str
        recent_exchanges: list of recent user/assistant pairs
        key_entities: list of technical terms / entities mentioned
        decisions: list of apparent decisions made
        unfinished_work: str (inferred from last exchanges)
        message_count: int
    """
    if not messages:
        return {
            "topic": title or "Unknown",
            "recent_exchanges": [],
            "key_entities": [],
            "decisions": [],
            "unfinished_work": "",
            "message_count": 0,
        }

    # Topic from title or first few messages
    topic = title or _infer_topic(messages)

    # Recent exchanges (last 6 messages, alternating user/assistant)
    recent = []
    for msg in messages[-6:]:
        role = msg.get("role", "user")
        content = str(msg.get("content", "")).strip()
        if not content or content == "(media attached)":
            continue
        # Truncate long messages for compact context
        if len(content) > 400:
            content = content[:400] + "..."
        recent.append({"role": role, "content": content})

    # Key entities (technical terms, filenames, project names)
    key_entities = _extract_key_entities(messages)

    # Apparent decisions (sentences containing "decided", "agree", "will use", etc.)
    decisions = _extract_decisions(messages)

    # Unfinished work (inferred from last user message or trailing context)
    unfinished = _infer_unfinished_work(messages)

    return {
        "topic": topic,
        "recent_exchanges": recent,
        "key_entities": key_entities,
        "decisions": decisions,
        "unfinished_work": unfinished,
        "message_count": len(messages),
    }


def _infer_topic(messages: list[dict]) -> str:
    """Infer the topic from the first user message(s)."""
    for msg in messages[:5]:
        if msg.get("role") == "user":
            content = str(msg.get("content", "")).strip()
            if content and len(content) > 10:
                # Use the first sentence or first 80 chars
                first_sentence = re.split(r"[.!?\n]", content)[0].strip()
                if len(first_sentence) > 80:
                    first_sentence = first_sentence[:80].rsplit(" ", 1)[0]
                return first_sentence
    return "Unknown topic"


def _extract_key_entities(messages: list[dict]) -> list[str]:
    """Extract technical terms, filenames, and proper nouns from messages."""
    text = ""
    for msg in messages[-15:]:
        text += str(msg.get("content", ""))[:500] + " "

    entities = set()
    # Filenames
    for match in re.finditer(r"\b(\w+\.(?:py|js|ts|json|yaml|md|txt|css|html))\b", text):
        entities.add(match.group(1))
    # Class/function names (CamelCase or snake_case with 3+ parts)
    for match in re.finditer(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+){1,5})\b", text):
        word = match.group(1)
        if len(word) > 4 and word.lower() not in {"the", "this", "that", "what", "when", "where", "which", "there", "their", "about", "could", "would", "should", "before", "after", "between", "through"}:
            entities.add(word)
    # ALL-CAPS technical terms
    for match in re.finditer(r"\b([A-Z]{2,15})\b", text):
        word = match.group(1)
        if word not in {"HTTP", "JSON", "HTML", "CSS", "API", "URL", "URI", "GET", "POST", "PUT", "DELETE", "TCP", "UDP", "SSH", "SSL", "TLS", "AWS", "GCP", "DNS", "FTP", "SQL", "ORM", "IDE", "SDK", "CLI", "TTS", "STT", "LLM", "TPM", "RPM"}:
            entities.add(word)
    return sorted(entities)[:15]


def _extract_decisions(messages: list[dict]) -> list[str]:
    """Extract apparent decisions from assistant responses."""
    decision_patterns = re.compile(
        r"(?:we(?:'ll| will| should| decided| agreed| need to| must| are going to)|"
        r"the (?:plan|decision|approach|solution|fix) is|"
        r"I(?:'ll| will| would) (?:recommend|suggest|use|implement|create|add)|"
        r"let(?:'s| us) (?:use|implement|create|add|build|go with|stick with))\s+"
        r"(.{10,120}?)[.!]",
        re.IGNORECASE,
    )
    decisions = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content", ""))
        for match in decision_patterns.finditer(content):
            decision = match.group(0).strip()
            if decision and decision not in decisions:
                decisions.append(decision)
            if len(decisions) >= 5:
                break
    return decisions


def _infer_unfinished_work(messages: list[dict]) -> str:
    """Infer what work remains based on the last few messages."""
    if not messages:
        return ""

    # Look at the last few messages for cues
    last_msgs = messages[-3:]
    for msg in reversed(last_msgs):
        content = str(msg.get("content", "")).strip()
        role = msg.get("role", "")

        if role == "user" and content:
            # Last user message might indicate what they want
            if len(content) <= 100:
                return f"User's last request: {content}"
            return f"User's last message: {content[:100]}..."

        if role == "assistant" and content:
            # Check for trailing phrases suggesting unfinished work
            trailing = re.search(
                r"(?:next|todo|pending|remaining|still need to|follow.?up|"
                r"next step|will implement|coming up|to come|"
                r"I'?ll (?:now|next|then|continue|implement|add|fix))\s*[:\-]?\s*(.{10,200})",
                content, re.IGNORECASE,
            )
            if trailing:
                return trailing.group(0).strip()[:200]

    return ""


# ── Compact context builder ─────────────────────────────────────────────────

def build_compact_context(
    recovered_states: list[dict],
    current_topic: str = "",
    token_budget: int = 800,
) -> str:
    """Build a compact context block from recovered conversation states.

    Respects token_budget (approximated as chars / 4).
    Never exceeds the budget.
    """
    if not recovered_states:
        return ""

    char_budget = token_budget * 4

    lines = ["[RECOVERED CONVERSATION CONTEXT]"]
    used = len(lines[0]) + 1

    for i, state in enumerate(recovered_states):
        if used >= char_budget:
            break

        section_lines = []
        section_lines.append(f"\n--- Conversation {i + 1} ---")

        topic = state.get("topic", "Unknown")
        section_lines.append(f"Topic: {topic}")

        # Recent exchanges (compact)
        exchanges = state.get("recent_exchanges", [])
        if exchanges:
            section_lines.append("Recent exchanges:")
            for ex in exchanges[-4:]:
                role = "User" if ex.get("role") == "user" else "Assistant"
                content = ex.get("content", "")[:200]
                section_lines.append(f"  {role}: {content}")

        # Key entities
        entities = state.get("key_entities", [])
        if entities:
            section_lines.append(f"Key terms: {', '.join(entities[:10])}")

        # Decisions
        decisions = state.get("decisions", [])
        if decisions:
            section_lines.append("Decisions made:")
            for d in decisions[:3]:
                section_lines.append(f"  - {d[:150]}")

        # Unfinished work
        unfinished = state.get("unfinished_work", "")
        if unfinished:
            section_lines.append(f"Status: {unfinished[:200]}")

        section = "\n".join(section_lines)
        section_len = len(section) + 1

        if used + section_len > char_budget:
            # Try to fit a truncated version
            remaining = char_budget - used - 50
            if remaining > 100:
                section = section[:remaining] + "\n[truncated]"
                lines.append(section)
            break

        lines.append(section)
        used += section_len

    lines.append("\n[END RECOVERED CONTEXT]")
    result = "\n".join(lines)

    # Final safety check
    if len(result) > char_budget:
        result = result[:char_budget - 20] + "\n[context truncated]"

    return result


# ── Main recovery orchestrator ──────────────────────────────────────────────

class ConversationRecovery:
    """High-level interface for cross-conversation context recovery.

    Usage::

        recovery = ConversationRecovery(user_id="u123")
        result = recovery.resolve("Continue where we stopped", current_chat_id="chat_456")
        if result:
            # result.recovered_context is the compact context to inject
            # result.confidence tells you how sure we are
            # result.candidates lists what we found
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._index = ConversationIndex(user_id)
        self._lock = threading.Lock()

    def resolve(
        self,
        message: str,
        current_chat_id: str = "",
        current_messages: list[dict] | None = None,
        token_budget: int = 800,
    ) -> dict | None:
        """Check if a message needs cross-conversation recovery and perform it.

        Returns None if no recovery is needed (normal chat message).
        Returns a dict with:
            needs_recovery: bool
            confidence: "high" | "medium" | "low"
            recovered_context: str (compact context block to inject)
            candidates: list of matching conversations
            ambiguous: bool (if True, the system should ask the user)
            clarification: str (suggested clarification question if ambiguous)
        """
        # Step 1: Detect continuation request
        detection = is_continuation_request(message, current_messages)
        if not detection["is_continuation"]:
            return None

        # Step 2: Check current thread first
        current_context_sufficient = self._check_current_thread(
            current_messages or [], message
        )
        if current_context_sufficient:
            return {
                "needs_recovery": False,
                "confidence": "high",
                "recovered_context": "",
                "candidates": [],
                "ambiguous": False,
                "clarification": "",
            }

        # Step 3: Search previous conversations
        try:
            candidates = self._index.search(
                message,
                top_k=5,
                exclude_chat_id=current_chat_id,
            )
        except Exception as exc:
            print(f"[CONV RECOVERY] Search failed: {exc}")
            return {
                "needs_recovery": False,
                "confidence": "low",
                "recovered_context": "",
                "candidates": [],
                "ambiguous": False,
                "clarification": "",
            }

        if not candidates:
            return {
                "needs_recovery": False,
                "confidence": "low",
                "recovered_context": "",
                "candidates": [],
                "ambiguous": False,
                "clarification": "",
            }

        # Step 4: Rank and evaluate candidates
        top_score = candidates[0]["score"] if candidates else 0
        second_score = candidates[1]["score"] if len(candidates) > 1 else 0

        # Determine confidence
        if top_score > 0.3 and (top_score - second_score) > 0.1:
            confidence = "high"
            ambiguous = False
        elif top_score > 0.15 and (top_score - second_score) > 0.05:
            confidence = "medium"
            ambiguous = False
        elif top_score > 0.1:
            confidence = "medium"
            # Check if there are competing candidates
            close_candidates = [c for c in candidates if c["score"] > top_score * 0.7]
            ambiguous = len(close_candidates) > 1
        else:
            confidence = "low"
            ambiguous = False

        # Step 5: Recover state from top candidate(s)
        recovered_states = []
        for cand in candidates[:2 if ambiguous else 1]:
            conv = self._index.get_conversation(cand["chat_id"])
            if conv and conv.get("messages"):
                state = recover_conversation_state(conv["messages"], conv.get("title", ""))
                state["score"] = cand["score"]
                state["chat_id"] = cand["chat_id"]
                recovered_states.append(state)

        # Step 6: Build compact context
        context = build_compact_context(recovered_states, token_budget=token_budget)

        # Step 7: Build clarification if ambiguous
        clarification = ""
        if ambiguous and len(candidates) >= 2:
            titles = [c.get("title", "a conversation") for c in candidates[:2]]
            clarification = (
                f"I found multiple relevant threads: \"{titles[0]}\" and \"{titles[1]}\". "
                f"Which one should I continue?"
            )

        return {
            "needs_recovery": context != "",
            "confidence": confidence,
            "recovered_context": context,
            "candidates": candidates[:5],
            "ambiguous": ambiguous,
            "clarification": clarification,
        }

    def _check_current_thread(
        self, messages: list[dict], user_message: str
    ) -> bool:
        """Check if the current thread already has enough context to answer.

        Returns True if the current conversation is sufficient and no
        cross-conversation search is needed.
        """
        if not messages:
            return False

        # If there are at least 2 recent messages with substantive content,
        # the current thread likely has enough context
        substantive = [
            m for m in messages[-6:]
            if m.get("content") and len(str(m.get("content", "")).strip()) > 20
        ]
        if len(substantive) >= 3:
            # Check if the user's message keywords overlap with recent content
            msg_tokens = set(_tokenize(user_message))
            recent_text = " ".join(
                str(m.get("content", ""))[:300] for m in messages[-6:]
            )
            recent_tokens = set(_tokenize(recent_text))
            overlap = len(msg_tokens & recent_tokens) / max(len(msg_tokens), 1)
            if overlap > 0.3:
                return True

        return False
