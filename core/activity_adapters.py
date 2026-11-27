"""
ValleyMind-AI Backend Activity Integration Adapters
===================================================
Lightweight wrappers connecting actual execution boundaries (search, scraping,
memory, vision, video, documents, LLM providers) to `core.activity_v2`.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional, TypeVar

from core.activity_v2 import (
    RequestActivitySession,
    get_current_session,
    track_activity,
)

F = TypeVar("F", bound=Callable[..., Any])


def activity_step(
    operation: str,
    *,
    started_label: Optional[str] = None,
    completed_label: Optional[str] = None,
    failed_label: Optional[str] = None,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with track_activity(operation, label=started_label) as handle:
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    if failed_label:
                        handle.fail(label=failed_label, detail=str(exc)[:160], error=exc)
                    raise
                if completed_label:
                    handle.complete(label=completed_label)
                return result

        return wrapper  # type: ignore[return-value]

    return decorator


class WebSearchAdapter:
    """Adapter for internet search and webpage reading operations."""

    @staticmethod
    def track_search(query: str) -> Any:
        preview = (query[:72] + "...") if len(query) > 75 else query
        return track_activity(
            "web_search",
            label="Searching the web...",
            detail=preview if preview else None,
            meta={"query_length": len(query)},
        )

    @staticmethod
    def track_page_read(
        url: str,
        index: Optional[int] = None,
        total: Optional[int] = None,
    ) -> Any:
        label = (
            f"Reading source {index} of {total}..."
            if index is not None and total is not None and total > 1
            else "Reading website source..."
        )
        return track_activity(
            "web_read",
            label=label,
            detail=url[:120],
            meta={"url": url},
        )


class MemoryAdapter:
    """Adapter for truthful memory retrieval and persistence."""

    @staticmethod
    def track_recall() -> Any:
        return track_activity(
            "memory_retrieval",
            label="Recalling relevant memories...",
        )

    @staticmethod
    def finalize_recall(handle: Any, memories_found: int) -> None:
        if memories_found > 0:
            noun = "memory" if memories_found == 1 else "memories"
            handle.complete(
                label=f"Recalled {memories_found} relevant {noun}",
                meta={"count": memories_found},
            )
        else:
            handle.complete(
                label="Checked memory (no prior notes matched)",
                meta={"count": 0},
            )

    @staticmethod
    def track_update() -> Any:
        return track_activity(
            "memory_update",
            label="Updating memory...",
        )

    @staticmethod
    def finalize_update(
        handle: Any,
        saved: bool,
        reason: Optional[str] = None,
    ) -> None:
        if saved:
            handle.complete(
                label="Saved new context to memory",
                detail=reason,
                meta={"saved": True},
            )
        else:
            handle.skip(
                label="Nothing new to save to memory",
                detail=reason or "No durable facts detected",
                meta={"saved": False},
            )


class MediaAnalysisAdapter:
    """Adapter for image, video, and document analysis pipelines."""

    @staticmethod
    def track_images(count: int) -> Any:
        label = (
            f"Analyzing {count} uploaded images..."
            if count > 1
            else "Analyzing uploaded image..."
        )
        return track_activity(
            "image_analysis",
            label=label,
            meta={"image_count": count},
        )

    @staticmethod
    def track_videos(count: int) -> Any:
        label = (
            f"Analyzing {count} uploaded videos..."
            if count > 1
            else "Analyzing uploaded video..."
        )
        return track_activity(
            "video_analysis",
            label=label,
            meta={"video_count": count},
        )

    @staticmethod
    def track_document(filename: Optional[str] = None) -> Any:
        label = (
            f"Reading {filename}..."
            if filename
            else "Reading uploaded document..."
        )
        return track_activity(
            "document_processing",
            label=label,
            detail=filename,
            meta={"filename": filename} if filename else {},
        )


class ProviderAdapter:
    """Adapter for LLM provider attempts, fallbacks, and response generation."""

    @staticmethod
    def track_response_generation(persona: Optional[str] = None) -> Any:
        return track_activity(
            "response_preparation",
            label="Preparing your answer...",
            meta={"persona": persona} if persona else {},
        )

    @staticmethod
    def notify_fallback(
        failed_provider: str,
        next_provider: str,
        reason: Optional[str] = None,
    ) -> None:
        # Provider identities stay internal; only generic status reaches the UI.
        with track_activity(
            "provider_fallback",
            label="Switching to backup AI provider...",
            detail="Temporary capacity issue",
            meta={"from": "primary", "to": "backup"},
        ) as handle:
            handle.complete(
                label="Connected to backup AI provider",
                meta={"from": "primary", "to": "backup"},
            )


def drain_sse_activities(session: Optional[RequestActivitySession] = None) -> str:
    active = session or get_current_session()
    if active is None:
        return ""
    frames = [ev.to_sse() for ev in active.drain_available()]
    return "".join(frames)
