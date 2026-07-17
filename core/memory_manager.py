import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec

logger = logging.getLogger(__name__)
load_dotenv()


class MemoryManager:
    """Persistent long-term memory backed by Pinecone + OpenRouter embeddings.

    Provides both async (save_to_memory / recall_from_memory) and
    synchronous (save_sync / recall_sync) methods so it can be used
    from both async and sync code paths.

    Requirements (add to requirements.txt):
        pinecone>=3.0.0
    """

    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-large").strip()
    # Must match the Pinecone index dimension (existing indexes are 768).
    # The `dimensions` request parameter makes text-embedding-3-* return
    # vectors at exactly this size.
    EMBEDDING_DIMS = int(os.getenv("EMBEDDING_DIMS", "768").strip())
    # Calibrated empirically: correct matches score 0.35-0.45, wrong-topic and
    # unrelated matches score below 0.12. 0.25 sits mid-band with margin.
    RELEVANCE_THRESHOLD = float(os.getenv("MEMORY_RELEVANCE_THRESHOLD", "0.25").strip())
    OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"

    def __init__(
        self,
        pinecone_api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        openrouter_api_key: Optional[str] = None,
    ):
        self.pinecone_api_key = (
            pinecone_api_key
            or os.getenv("PINECONE_API_KEY_MEMORY", "").strip()
        )
        self.index_name = (
            index_name
            or os.getenv("PINECONE_INDEX_MEMORY", "valleymind-memory").strip()
        )
        self.openrouter_api_key = (
            openrouter_api_key
            or os.getenv("OPENROUTER_API_KEY", "").strip()
        )

        missing = []
        if not self.pinecone_api_key:
            missing.append("PINECONE_API_KEY_MEMORY")
        if not self.openrouter_api_key:
            missing.append("OPENROUTER_API_KEY")
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

        self._pc = Pinecone(api_key=self.pinecone_api_key)
        self._ensure_index()
        self._index = self._pc.Index(self.index_name)
        logger.info("MemoryManager initialised — index '%s'", self.index_name)

    # ── index lifecycle ──────────────────────────────────────────────

    def _ensure_index(self):
        try:
            existing = [i.name for i in self._pc.list_indexes()]
            if self.index_name not in existing:
                self._pc.create_index(
                    name=self.index_name,
                    dimension=self.EMBEDDING_DIMS,
                    metric="cosine",
                    spec=ServerlessSpec
                    (cloud="aws",
                 region=os.getenv("PINECONE_REGION", "us-east-1")),
                )
                logger.info("Created index '%s'", self.index_name)
            else:
                logger.info("Index '%s' already exists", self.index_name)
        except Exception as exc:
            logger.error("Failed to ensure index '%s': %s", self.index_name, exc)
            raise

    # ── embedding (async via httpx) ──────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self.OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.EMBEDDING_MODEL, "input": text, "dimensions": self.EMBEDDING_DIMS},
                )
                resp.raise_for_status()
                payload = resp.json()
                return payload["data"][0]["embedding"]
        except httpx.HTTPStatusError as exc:
            logger.error("OpenRouter HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
            raise
        except (KeyError, IndexError) as exc:
            logger.error("Unexpected OpenRouter response shape: %s", exc)
            raise
        except httpx.RequestError as exc:
            logger.error("OpenRouter request failed: %s", exc)
            raise

    # ── embedding (sync via requests) for existing sync code paths ───

    def _embed_sync(self, text: str) -> list[float]:
        import requests as _requests
        try:
            resp = _requests.post(
                self.OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.EMBEDDING_MODEL, "input": text, "dimensions": self.EMBEDDING_DIMS},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except _requests.RequestException as exc:
            logger.error("OpenRouter sync request failed: %s", exc)
            raise
        except (KeyError, IndexError) as exc:
            logger.error("Unexpected OpenRouter sync response: %s", exc)
            raise

    # ── shared helpers ───────────────────────────────────────────────

    @staticmethod
    def _build_metadata(user_input: str, ai_response: str, session_id: str) -> tuple[str, dict]:
        metadata = {
            "user_input": user_input,
            "ai_response": ai_response,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        vector_id = f"{session_id}#{datetime.now(timezone.utc).timestamp()}"
        return vector_id, metadata

    @staticmethod
    def _word_overlap(a: str, b: str) -> float:
        a_words = set(re.sub(r"[^\w\s]", "", str(a or "").lower()).split())
        b_words = set(re.sub(r"[^\w\s]", "", str(b or "").lower()).split())
        if not a_words or not b_words:
            return 0.0
        return len(a_words & b_words) / max(len(a_words), len(b_words))

    def _format_matches(
        self,
        result,
        min_score: float,
        exclude_texts: Optional[list[str]] = None,
        dedupe_against: Optional[list[str]] = None,
    ) -> str:
        """Filter matches by relevance and format the survivors.

        Returns "" when nothing clears the threshold — the caller injects
        nothing into context in that case. Snippets that substantially overlap
        an entry in ``dedupe_against`` (e.g. a distilled categorized fact) are
        dropped — the curated fact wins over the raw transcript.
        """
        matches = list(result.matches) if hasattr(result, "matches") else result.get("matches", [])
        excluded = {t.strip() for t in (exclude_texts or []) if t and t.strip()}
        dedupe = [d for d in (dedupe_against or []) if d and d.strip()]

        lines = []
        for match in matches:
            meta = match.metadata if hasattr(match, "metadata") else match.get("metadata", {})
            score = match.score if hasattr(match, "score") else match.get("score", 0)
            if score < min_score:
                continue
            user_text = (meta or {}).get("user_input", "")
            ai_text = (meta or {}).get("ai_response", "")
            # Skip snippets already present in the short-term history block
            if user_text.strip() in excluded:
                continue
            # Either side of the exchange overlapping a curated fact makes the
            # snippet redundant (e.g. a Q/A whose answer restates the fact).
            if any(
                self._word_overlap(user_text, d) > 0.5 or self._word_overlap(ai_text, d) > 0.5
                for d in dedupe
            ):
                continue
            ts = (meta or {}).get("timestamp", "")
            lines.append(
                f"[{len(lines) + 1}] (relevance: {score:.3f}, {ts})\n"
                f"    User: {user_text}\n"
                f"    Assistant: {ai_text}"
            )

        if not lines:
            return ""
        return "Relevant History:\n" + "\n".join(lines)

    # ── public async API ─────────────────────────────────────────────

    async def save_to_memory(self, user_input: str, ai_response: str, session_id: str, namespace: str = ""):
        if not user_input.strip() or not ai_response.strip():
            logger.warning("save_to_memory skipped — empty input or response")
            return

        try:
            vector = await self._embed(f"User: {user_input}\nAssistant: {ai_response}")
        except Exception as exc:
            logger.error("Embedding failed in save_to_memory: %s", exc)
            return

        vector_id, metadata = self._build_metadata(user_input, ai_response, session_id)
        try:
            await asyncio.to_thread(
                self._index.upsert,
                vectors=[(vector_id, vector, metadata)],
                namespace=namespace or None,
            )
            logger.info("Upserted memory %s for session %s", vector_id, session_id)
        except Exception as exc:
            logger.error("Pinecone upsert failed: %s", exc)

    async def recall_from_memory(
        self,
        user_input: str,
        top_k: int = 5,
        namespace: str = "",
        min_score: Optional[float] = None,
        exclude_texts: Optional[list[str]] = None,
    ) -> str:
        if not user_input.strip():
            return ""

        try:
            query_vector = await self._embed(user_input)
        except Exception as exc:
            logger.error("Embedding failed in recall_from_memory: %s", exc)
            return ""

        try:
            result = await asyncio.to_thread(
                self._index.query,
                vector=query_vector,
                top_k=top_k,
                include_metadata=True,
                namespace=namespace or None,
            )
        except Exception as exc:
            logger.error("Pinecone query failed: %s", exc)
            return ""

        threshold = self.RELEVANCE_THRESHOLD if min_score is None else min_score
        return self._format_matches(result, threshold, exclude_texts)

    # ── public sync API (for MarcusBrain respond / stream_respond) ───

    def recall_sync(
        self,
        user_input: str,
        top_k: int = 5,
        namespace: str = "",
        min_score: Optional[float] = None,
        exclude_texts: Optional[list[str]] = None,
        dedupe_against: Optional[list[str]] = None,
    ) -> str:
        if not user_input.strip():
            return ""

        try:
            query_vector = self._embed_sync(user_input)
        except Exception as exc:
            logger.error("Embedding failed in recall_sync: %s", exc)
            return ""

        try:
            result = self._index.query(
                vector=query_vector,
                top_k=top_k,
                include_metadata=True,
                namespace=namespace or None,
            )
        except Exception as exc:
            logger.error("Pinecone query failed in recall_sync: %s", exc)
            return ""

        threshold = self.RELEVANCE_THRESHOLD if min_score is None else min_score
        return self._format_matches(result, threshold, exclude_texts, dedupe_against)

    def save_sync(self, user_input: str, ai_response: str, session_id: str, namespace: str = ""):
        if not user_input.strip() or not ai_response.strip():
            logger.warning("save_sync skipped — empty input or response")
            return

        try:
            vector = self._embed_sync(f"User: {user_input}\nAssistant: {ai_response}")
        except Exception as exc:
            logger.error("Embedding failed in save_sync: %s", exc)
            return

        vector_id, metadata = self._build_metadata(user_input, ai_response, session_id)
        try:
            self._index.upsert(vectors=[(vector_id, vector, metadata)], namespace=namespace or None)
            logger.info("Saved memory %s for session %s via sync", vector_id, session_id)
        except Exception as exc:
            logger.error("Pinecone upsert failed in save_sync: %s", exc)
