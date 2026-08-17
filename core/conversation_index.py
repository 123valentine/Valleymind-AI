"""Cross-conversation keyword search index.

Provides fast, embedding-free retrieval of relevant previous conversations
using TF-IDF-style keyword matching.  Designed to work when OpenRouter
embeddings are unavailable (402 / no credits) and to fall back gracefully
when they are.

This module is PURELY ADDITIVE — it reads from the existing MongoDB
``chats`` collection and never writes to it or modifies any existing
memory structures.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from core.db import chats_collection


# ── Shared stopwords ────────────────────────────────────────────────────────
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "because", "but", "and", "or", "if", "while", "about", "it", "its",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "they",
    "them", "his", "her", "their", "mine", "yours", "ours", "theirs",
    "am", "s", "t", "re", "ve", "ll", "d", "m", "don", "doesn",
    "didn", "won", "wouldn", "couldn", "shouldn", "isn", "aren", "wasn",
    "weren", "hasn", "haven", "hadn", "let", "also", "like", "get",
    "got", "make", "made", "go", "going", "went", "come", "came",
    "take", "took", "give", "gave", "say", "said", "tell", "told",
    "know", "knew", "think", "thought", "see", "saw", "want", "put",
    "use", "try", "tried", "keep", "kept", "set", "ask", "asked",
    "yeah", "yes", "ok", "okay", "right", "well", "sure", "now",
    "new", "one", "two", "first", "still", "even", "back", "way",
    "much", "many", "thing", "things", "something", "anything",
    "please", "thanks", "thank", "hello", "hi", "hey",
})

# ── Lightweight term tokenizer ──────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9]{2,}")


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokenization with stopword removal."""
    return [w for w in _WORD_RE.findall(str(text or "").lower()) if w not in _STOPWORDS]


# ── Document-level TF-IDF helpers ──────────────────────────────────────────

def _compute_idf(doc_freq: dict[str, int], n_docs: int) -> dict[str, float]:
    """Standard IDF: log(N / df) with smoothing."""
    idf = {}
    for term, df in doc_freq.items():
        idf[term] = math.log((n_docs + 1) / (df + 1)) + 1.0
    return idf


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Term-frequency * IDF vector for a single document."""
    tf = Counter(tokens)
    total = len(tokens) or 1
    return {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}


def _cosine_sim(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors."""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Conversation summary extraction ─────────────────────────────────────────

def _summarize_conversation(messages: list[dict], title: str = "", max_chars: int = 4000) -> str:
    """Extract a compact text representation of a conversation.

    Prefers recent messages (most relevant to current state) and the title.
    Truncates to ``max_chars`` to keep indexing fast.
    """
    parts = []
    if title:
        parts.append(f"Title: {title}")
    # Take the last N messages to capture current state
    recent = messages[-30:] if len(messages) > 30 else messages
    for msg in recent:
        role = msg.get("role", "user")
        content = str(msg.get("content", "")).strip()
        if not content or content == "(media attached)":
            continue
        # Truncate very long individual messages
        if len(content) > 500:
            content = content[:500] + "..."
        parts.append(f"{role}: {content}")
    text = "\n".join(parts)
    return text[:max_chars]


def _extract_topics(messages: list[dict], title: str = "") -> list[str]:
    """Extract likely topic keywords from conversation title + recent messages."""
    text = (title or "").lower()
    for msg in messages[-10:]:
        content = str(msg.get("content", "")).strip()
        if content:
            text += " " + content[:300].lower()
    tokens = _tokenize(text)
    # Return the most frequent meaningful terms
    freq = Counter(tokens)
    return [term for term, _ in freq.most_common(20)]


def _extract_entities(messages: list[dict], title: str = "") -> list[str]:
    """Extract capitalized terms / technical identifiers as entities."""
    text = (title or "") + " "
    for msg in messages[-15:]:
        text += str(msg.get("content", ""))[:400] + " "
    # Capitalized words (likely proper nouns / project names)
    entities = set()
    for match in re.finditer(r"\b([A-Z][a-zA-Z0-9_.-]{1,30})\b", text):
        word = match.group(1)
        if word.lower() not in _STOPWORDS and len(word) > 2:
            entities.add(word)
    # Technical identifiers (file.py, Class.method, etc.)
    for match in re.finditer(r"\b(\w+\.(?:py|js|ts|json|yaml|md|txt))\b", text):
        entities.add(match.group(1))
    return sorted(entities)[:30]


# ── Indexed conversation record ─────────────────────────────────────────────

class ConversationRecord:
    """A single indexed conversation with pre-computed search metadata."""

    __slots__ = (
        "chat_id", "user_id", "title", "message_count",
        "last_activity", "created_at", "summary", "topics",
        "entities", "tokens", "_tfidf",
    )

    def __init__(
        self,
        chat_id: str,
        user_id: str,
        title: str = "",
        message_count: int = 0,
        last_activity: Optional[datetime] = None,
        created_at: Optional[datetime] = None,
        summary: str = "",
        topics: list[str] | None = None,
        entities: list[str] | None = None,
        tokens: list[str] | None = None,
    ):
        self.chat_id = chat_id
        self.user_id = user_id
        self.title = title
        self.message_count = message_count
        self.last_activity = last_activity
        self.created_at = created_at
        self.summary = summary
        self.topics = topics or []
        self.entities = entities or []
        self.tokens = tokens or []
        self._tfidf: dict[str, float] = {}


# ── Main search index ───────────────────────────────────────────────────────

class ConversationIndex:
    """In-memory keyword search index over a user's conversation history.

    Usage::

        index = ConversationIndex(user_id="u123")
        index.build()                     # loads from MongoDB
        results = index.search("Groq 413 error", top_k=5)

    Thread-safe.  The index is lightweight and can be rebuilt cheaply.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._records: list[ConversationRecord] = []
        self._idf: dict[str, float] = {}
        self._lock = threading.Lock()
        self._built = False

    @property
    def record_count(self) -> int:
        return len(self._records)

    def build(self, force: bool = False) -> int:
        """Load all conversations for this user from MongoDB and index them.

        Returns the number of indexed conversations.
        """
        with self._lock:
            if self._built and not force:
                return len(self._records)

        coll = chats_collection()
        if coll is None:
            return 0

        try:
            cursor = coll.find(
                {"user_id": self.user_id},
                {"chat_id": 1, "title": 1, "messages": 1, "message_count": 1,
                 "last_activity": 1, "created_at": 1},
            ).sort("last_activity", -1).limit(500)
        except Exception as exc:
            print(f"[CONV INDEX] MongoDB query failed: {exc}")
            return 0

        records = []
        for doc in cursor:
            chat_id = doc.get("chat_id", "")
            if not chat_id:
                continue
            messages = doc.get("messages") or []
            if not isinstance(messages, list) or not messages:
                continue

            title = doc.get("title", "")
            summary = _summarize_conversation(messages, title)
            topics = _extract_topics(messages, title)
            entities = _extract_entities(messages, title)
            all_tokens = _tokenize(title + " " + summary)

            last_act = doc.get("last_activity")
            created = doc.get("created_at")

            records.append(ConversationRecord(
                chat_id=chat_id,
                user_id=self.user_id,
                title=title or "",
                message_count=doc.get("message_count") or len(messages),
                last_activity=last_act if isinstance(last_act, datetime) else None,
                created_at=created if isinstance(created, datetime) else None,
                summary=summary,
                topics=topics,
                entities=entities,
                tokens=all_tokens,
            ))

        # Compute IDF across all records
        doc_freq: dict[str, int] = Counter()
        for rec in records:
            unique_terms = set(rec.tokens)
            for term in unique_terms:
                doc_freq[term] += 1
        idf = _compute_idf(doc_freq, len(records) or 1)

        # Compute TF-IDF vectors
        for rec in records:
            rec._tfidf = _tfidf_vector(rec.tokens, idf)

        with self._lock:
            self._records = records
            self._idf = idf
            self._built = True

        print(f"[CONV INDEX] Indexed {len(records)} conversations for user {self.user_id[:8]}...")
        return len(records)

    def search(
        self,
        query: str,
        top_k: int = 5,
        exclude_chat_id: str = "",
    ) -> list[dict]:
        """Search indexed conversations by keyword similarity.

        Returns a list of dicts sorted by relevance descending::

            [{"chat_id": ..., "title": ..., "score": ..., "summary": ...,
              "topics": [...], "entities": [...], "last_activity": ...}]
        """
        if not self._built:
            self.build()

        if not self._records:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            # Fallback: return most recent conversations
            return self._recent_fallback(top_k, exclude_chat_id)

        query_tfidf = _tfidf_vector(query_tokens, self._idf)

        results = []
        for rec in self._records:
            if rec.chat_id == exclude_chat_id:
                continue

            # TF-IDF cosine similarity
            sim = _cosine_sim(query_tfidf, rec._tfidf)

            # Boost for title matches
            title_tokens = set(_tokenize(rec.title))
            query_set = set(query_tokens)
            title_overlap = len(query_set & title_tokens) / max(len(query_set), 1)
            sim += title_overlap * 0.3

            # Boost for entity matches
            entity_set = {e.lower() for e in rec.entities}
            entity_overlap = len(query_set & entity_set) / max(len(query_set), 1)
            sim += entity_overlap * 0.2

            # Recency boost (log decay over days)
            if rec.last_activity:
                days_ago = max(0, (datetime.now(timezone.utc) - rec.last_activity.replace(tzinfo=timezone.utc)).days)
                recency = 1.0 / (1.0 + math.log1p(days_ago))
                sim += recency * 0.1

            if sim > 0.01:
                results.append({
                    "chat_id": rec.chat_id,
                    "title": rec.title,
                    "score": round(sim, 4),
                    "summary": rec.summary[:1500],
                    "topics": rec.topics[:10],
                    "entities": rec.entities[:15],
                    "message_count": rec.message_count,
                    "last_activity": rec.last_activity.isoformat() if rec.last_activity else "",
                    "created_at": rec.created_at.isoformat() if rec.created_at else "",
                })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def get_conversation(self, chat_id: str) -> dict | None:
        """Retrieve a single conversation's full message history."""
        coll = chats_collection()
        if coll is None:
            return None
        try:
            doc = coll.find_one(
                {"chat_id": chat_id, "user_id": self.user_id},
                {"messages": 1, "title": 1, "last_activity": 1},
            )
            if doc:
                return {
                    "chat_id": chat_id,
                    "title": doc.get("title", ""),
                    "messages": doc.get("messages") or [],
                    "last_activity": doc.get("last_activity"),
                }
        except Exception as exc:
            print(f"[CONV INDEX] Failed to retrieve conversation {chat_id}: {exc}")
        return None

    def _recent_fallback(self, top_k: int, exclude_chat_id: str = "") -> list[dict]:
        """Return the most recent conversations when keyword search yields nothing."""
        results = []
        for rec in self._records[:top_k * 2]:
            if rec.chat_id == exclude_chat_id:
                continue
            results.append({
                "chat_id": rec.chat_id,
                "title": rec.title,
                "score": 0.05,
                "summary": rec.summary[:1500],
                "topics": rec.topics[:10],
                "entities": rec.entities[:15],
                "message_count": rec.message_count,
                "last_activity": rec.last_activity.isoformat() if rec.last_activity else "",
                "created_at": rec.created_at.isoformat() if rec.created_at else "",
            })
            if len(results) >= top_k:
                break
        return results
