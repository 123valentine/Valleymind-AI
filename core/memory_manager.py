import asyncio
import logging
import os
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

    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "baai/bge-base-en-v1.5").strip()
    EMBEDDING_DIMS = int(os.getenv("EMBEDDING_DIMS", "768").strip())
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
                    spec=ServerlessSpec(cloud="aws", region="us-east-1"),
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
                    json={"model": self.EMBEDDING_MODEL, "input": text},
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
                json={"model": self.EMBEDDING_MODEL, "input": text},
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

    # ── public async API ─────────────────────────────────────────────

    async def save_to_memory(self, user_input: str, ai_response: str, session_id: str):
        if not user_input.strip() or not ai_response.strip():
            logger.warning("save_to_memory skipped — empty input or response")
            return

        combined = f"User: {user_input}\nAssistant: {ai_response}"

        try:
            vector = await self._embed(combined)
        except Exception as exc:
            logger.error("Embedding failed in save_to_memory: %s", exc)
            return

        metadata = {
            "user_input": user_input,
            "ai_response": ai_response,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        vector_id = f"{session_id}#{datetime.now(timezone.utc).timestamp()}"

        try:
            await asyncio.to_thread(
                self._index.upsert,
                vectors=[(vector_id, vector, metadata)],
            )
            logger.info("Upserted memory %s for session %s", vector_id, session_id)
        except Exception as exc:
            logger.error("Pinecone upsert failed: %s", exc)

    async def recall_from_memory(self, user_input: str, top_k: int = 3) -> str:
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
            )
        except Exception as exc:
            logger.error("Pinecone query failed: %s", exc)
            return ""

        matches = list(result.matches) if hasattr(result, "matches") else result.get("matches", [])
        if not matches:
            return ""

        lines = []
        for i, match in enumerate(matches, 1):
            meta = match.metadata if hasattr(match, "metadata") else match.get("metadata", {})
            score = match.score if hasattr(match, "score") else match.get("score", 0)
            user_text = (meta or {}).get("user_input", "")
            ai_text = (meta or {}).get("ai_response", "")
            ts = (meta or {}).get("timestamp", "")
            lines.append(
                f"[{i}] (relevance: {score:.3f}, {ts})\n"
                f"    User: {user_text}\n"
                f"    Assistant: {ai_text}"
            )

        return "Relevant History:\n" + "\n".join(lines)

    # ── public sync API (for MarcusBrain respond / stream_respond) ───

    def recall_sync(self, user_input: str, top_k: int = 3) -> str:
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
            )
        except Exception as exc:
            logger.error("Pinecone query failed in recall_sync: %s", exc)
            return ""

        matches = list(result.matches) if hasattr(result, "matches") else result.get("matches", [])
        if not matches:
            return ""

        lines = []
        for i, match in enumerate(matches, 1):
            meta = match.metadata if hasattr(match, "metadata") else match.get("metadata", {})
            score = match.score if hasattr(match, "score") else match.get("score", 0)
            user_text = (meta or {}).get("user_input", "")
            ai_text = (meta or {}).get("ai_response", "")
            ts = (meta or {}).get("timestamp", "")
            lines.append(
                f"[{i}] (relevance: {score:.3f}, {ts})\n"
                f"    User: {user_text}\n"
                f"    Assistant: {ai_text}"
            )

        return "Relevant History:\n" + "\n".join(lines)

    def save_sync(self, user_input: str, ai_response: str, session_id: str):
        if not user_input.strip() or not ai_response.strip():
            logger.warning("save_sync skipped — empty input or response")
            return

        combined = f"User: {user_input}\nAssistant: {ai_response}"

        try:
            vector = self._embed_sync(combined)
        except Exception as exc:
            logger.error("Embedding failed in save_sync: %s", exc)
            return

        metadata = {
            "user_input": user_input,
            "ai_response": ai_response,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        vector_id = f"{session_id}#{datetime.now(timezone.utc).timestamp()}"

        try:
            self._index.upsert(vectors=[(vector_id, vector, metadata)])
            logger.info("Saved memory %s for session %s via sync", vector_id, session_id)
        except Exception as exc:
            logger.error("Pinecone upsert failed in save_sync: %s", exc)
