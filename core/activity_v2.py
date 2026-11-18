"""
ValleyMind-AI Unified Contextual Activity Tracking System (v2)
==============================================================
Thread-safe, async-compatible, provider-agnostic activity tracking engine.
Emits single-line JSON `data: {"type": "activity", ...}` SSE frames compatible
with `index.html`'s Layer-1 SSE pre-filter.
"""

from __future__ import annotations

import contextvars
import json
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional


class ActivityStatus(str, Enum):
    STARTED = "started"
    PROGRESSING = "progressing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ActivityCategory(str, Enum):
    REASONING = "reasoning"
    RESEARCH = "research"
    MEMORY = "memory"
    VISION = "vision"
    DOCUMENT = "document"
    DATA = "data"
    CODE = "code"
    MEDIA = "media"
    GENERATION = "generation"
    SYSTEM = "system"


@dataclass(frozen=True)
class OperationSpec:
    """Registered definition for an operation type."""
    operation: str
    category: ActivityCategory
    started_label: str
    completed_label: str
    failed_label: str
    skipped_label: str


@dataclass
class ActivityProgress:
    """Measurable discrete progress (never fabricated percentages)."""
    current: int
    total: int
    unit: str = "items"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current": self.current,
            "total": self.total,
            "unit": self.unit,
        }


@dataclass
class ActivityEvent:
    """Canonical activity event transmitted to SSE streams and JSON responses."""
    event_id: str
    request_id: str
    seq: int
    operation: str
    category: str
    status: str
    label: str
    detail: Optional[str] = None
    parent_id: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    duration_ms: Optional[int] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "2.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_sse(self) -> str:
        envelope = {
            "type": "activity",
            "activity": self.to_dict(),
        }
        payload = json.dumps(envelope, ensure_ascii=False)
        return f"data: {payload}\n\n"


class ActivityRegistry:
    """Central extensible catalog of ValleyMind operations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._specs: Dict[str, OperationSpec] = {}
        self._register_defaults()

    def register(self, spec: OperationSpec) -> None:
        with self._lock:
            self._specs[spec.operation] = spec

    def get(self, operation: str) -> OperationSpec:
        with self._lock:
            if operation in self._specs:
                return self._specs[operation]
            readable = operation.replace("_", " ").capitalize()
            return OperationSpec(
                operation=operation,
                category=ActivityCategory.SYSTEM,
                started_label=f"{readable}...",
                completed_label=f"Completed {readable.lower()}",
                failed_label=f"{readable} failed",
                skipped_label=f"Skipped {readable.lower()}",
            )

    def list_operations(self) -> List[str]:
        with self._lock:
            return sorted(self._specs.keys())

    def _register_defaults(self) -> None:
        defaults = [
            OperationSpec(
                operation="request_understanding",
                category=ActivityCategory.REASONING,
                started_label="Understanding your request...",
                completed_label="Understood request",
                failed_label="Failed to classify request",
                skipped_label="Skipped request classification",
            ),
            OperationSpec(
                operation="web_search",
                category=ActivityCategory.RESEARCH,
                started_label="Searching the web...",
                completed_label="Searched the web",
                failed_label="Web search failed",
                skipped_label="Skipped web search",
            ),
            OperationSpec(
                operation="web_read",
                category=ActivityCategory.RESEARCH,
                started_label="Reading webpages...",
                completed_label="Read website sources",
                failed_label="Could not read source",
                skipped_label="Skipped reading source",
            ),
            OperationSpec(
                operation="info_gathering",
                category=ActivityCategory.RESEARCH,
                started_label="Gathering relevant information...",
                completed_label="Gathered relevant information",
                failed_label="Information gathering failed",
                skipped_label="Skipped information gathering",
            ),
            OperationSpec(
                operation="memory_retrieval",
                category=ActivityCategory.MEMORY,
                started_label="Recalling relevant memories...",
                completed_label="Recalled relevant memories",
                failed_label="Memory lookup unavailable",
                skipped_label="No prior memory lookup needed",
            ),
            OperationSpec(
                operation="memory_update",
                category=ActivityCategory.MEMORY,
                started_label="Updating memory...",
                completed_label="Memory updated",
                failed_label="Memory update failed",
                skipped_label="Nothing new to save to memory",
            ),
            OperationSpec(
                operation="image_analysis",
                category=ActivityCategory.VISION,
                started_label="Analyzing image...",
                completed_label="Analyzed image",
                failed_label="Image analysis failed",
                skipped_label="Skipped image analysis",
            ),
            OperationSpec(
                operation="video_analysis",
                category=ActivityCategory.VISION,
                started_label="Analyzing video...",
                completed_label="Analyzed video",
                failed_label="Video analysis failed",
                skipped_label="Skipped video analysis",
            ),
            OperationSpec(
                operation="document_processing",
                category=ActivityCategory.DOCUMENT,
                started_label="Reading uploaded document...",
                completed_label="Extracted document contents",
                failed_label="Document reading failed",
                skipped_label="Skipped document processing",
            ),
            OperationSpec(
                operation="data_analysis",
                category=ActivityCategory.DATA,
                started_label="Processing and analyzing data...",
                completed_label="Data analysis complete",
                failed_label="Data analysis failed",
                skipped_label="Skipped data analysis",
            ),
            OperationSpec(
                operation="comparing_information",
                category=ActivityCategory.REASONING,
                started_label="Comparing information...",
                completed_label="Compared information",
                failed_label="Comparison failed",
                skipped_label="Skipped comparison",
            ),
            OperationSpec(
                operation="summarization",
                category=ActivityCategory.REASONING,
                started_label="Summarizing findings...",
                completed_label="Summarized findings",
                failed_label="Summarization failed",
                skipped_label="Skipped summarization",
            ),
            OperationSpec(
                operation="code_generation",
                category=ActivityCategory.CODE,
                started_label="Inspecting and writing code...",
                completed_label="Code ready",
                failed_label="Code generation failed",
                skipped_label="Skipped code generation",
            ),
            OperationSpec(
                operation="compiling_results",
                category=ActivityCategory.REASONING,
                started_label="Compiling results...",
                completed_label="Compiled results",
                failed_label="Failed to compile results",
                skipped_label="Skipped compiling results",
            ),
            OperationSpec(
                operation="image_generation",
                category=ActivityCategory.MEDIA,
                started_label="Generating image...",
                completed_label="Generated image",
                failed_label="Image generation failed",
                skipped_label="Skipped image generation",
            ),
            OperationSpec(
                operation="video_generation",
                category=ActivityCategory.MEDIA,
                started_label="Generating video...",
                completed_label="Generated video",
                failed_label="Video generation failed",
                skipped_label="Skipped video generation",
            ),
            OperationSpec(
                operation="voice_synthesis",
                category=ActivityCategory.MEDIA,
                started_label="Generating voice audio...",
                completed_label="Voice audio ready",
                failed_label="Voice generation failed",
                skipped_label="Skipped voice generation",
            ),
            OperationSpec(
                operation="provider_fallback",
                category=ActivityCategory.SYSTEM,
                started_label="Switching to backup AI provider...",
                completed_label="Connected to backup AI provider",
                failed_label="Backup AI provider unavailable",
                skipped_label="Primary provider succeeded",
            ),
            OperationSpec(
                operation="response_preparation",
                category=ActivityCategory.GENERATION,
                started_label="Preparing your answer...",
                completed_label="Response ready",
                failed_label="Failed to generate response",
                skipped_label="Skipped response generation",
            ),
        ]
        for spec in defaults:
            self.register(spec)


activity_registry = ActivityRegistry()


class RequestActivitySession:
    """Isolated activity state for a single user request."""

    def __init__(
        self,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        persona: Optional[str] = None,
        registry: Optional[ActivityRegistry] = None,
        listener: Optional[Callable[[ActivityEvent], None]] = None,
    ) -> None:
        self.request_id = request_id or f"req_{uuid.uuid4().hex[:12]}"
        self.user_id = user_id
        self.persona = persona
        self.registry = registry or activity_registry
        self._listener = listener

        self._lock = threading.RLock()
        self._seq = 0
        self._events: List[ActivityEvent] = []
        self._queue: queue.Queue[Optional[ActivityEvent]] = queue.Queue()
        self._active_handles: Dict[str, "ActivityHandle"] = {}
        self._closed = False
        self._cancelled = False
        self._last_dedup_key: Optional[str] = None

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self, reason: str = "Request cancelled") -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            active = list(self._active_handles.values())
        for handle in active:
            handle.fail(detail=reason, meta={"cancelled": True})
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)

    def start(
        self,
        operation: str,
        *,
        label: Optional[str] = None,
        detail: Optional[str] = None,
        parent_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "ActivityHandle":
        event_id = f"act_{uuid.uuid4().hex[:10]}"
        spec = self.registry.get(operation)
        handle = ActivityHandle(
            session=self,
            event_id=event_id,
            spec=spec,
            parent_id=parent_id,
        )
        with self._lock:
            self._active_handles[event_id] = handle

        self._emit(
            event_id=event_id,
            spec=spec,
            status=ActivityStatus.STARTED,
            label=label or spec.started_label,
            detail=detail,
            parent_id=parent_id,
            meta=meta,
        )
        return handle

    def record_skipped(
        self,
        operation: str,
        *,
        label: Optional[str] = None,
        detail: Optional[str] = None,
        parent_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> ActivityEvent:
        spec = self.registry.get(operation)
        event_id = f"act_{uuid.uuid4().hex[:10]}"
        return self._emit(
            event_id=event_id,
            spec=spec,
            status=ActivityStatus.SKIPPED,
            label=label or spec.skipped_label,
            detail=detail,
            parent_id=parent_id,
            duration_ms=0,
            meta=meta,
        )

    def _emit(
        self,
        *,
        event_id: str,
        spec: OperationSpec,
        status: ActivityStatus,
        label: str,
        detail: Optional[str] = None,
        parent_id: Optional[str] = None,
        progress: Optional[ActivityProgress] = None,
        duration_ms: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> ActivityEvent:
        with self._lock:
            if self._closed:
                return ActivityEvent(
                    event_id=event_id,
                    request_id=self.request_id,
                    seq=self._seq,
                    operation=spec.operation,
                    category=spec.category.value,
                    status=status.value,
                    label=label,
                    detail=detail,
                    parent_id=parent_id,
                    progress=progress.to_dict() if progress else None,
                    duration_ms=duration_ms,
                    meta=dict(meta or {}),
                )

            dedup_key = (
                f"{event_id}:{status.value}:{label}:{detail}:"
                f"{progress.to_dict() if progress else ''}"
            )
            if dedup_key == self._last_dedup_key and self._events:
                return self._events[-1]
            self._last_dedup_key = dedup_key

            self._seq += 1
            event = ActivityEvent(
                event_id=event_id,
                request_id=self.request_id,
                seq=self._seq,
                operation=spec.operation,
                category=spec.category.value,
                status=status.value,
                label=label,
                detail=detail,
                parent_id=parent_id,
                progress=progress.to_dict() if progress else None,
                duration_ms=duration_ms,
                meta=dict(meta or {}),
            )
            self._events.append(event)

            if status in (
                ActivityStatus.COMPLETED,
                ActivityStatus.FAILED,
                ActivityStatus.SKIPPED,
            ):
                self._active_handles.pop(event_id, None)

        self._queue.put(event)
        if self._listener is not None:
            try:
                self._listener(event)
            except Exception:
                pass
        return event

    def drain_available(self) -> List[ActivityEvent]:
        drained: List[ActivityEvent] = []
        while True:
            try:
                item = self._queue.get_nowait()
                if item is None:
                    break
                drained.append(item)
            except queue.Empty:
                break
        return drained

    def iter_sse(self, poll_timeout: float = 0.15) -> Generator[str, None, None]:
        while True:
            try:
                item = self._queue.get(timeout=poll_timeout)
                if item is None:
                    break
                yield item.to_sse()
            except queue.Empty:
                if self.is_closed:
                    break

    def get_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [ev.to_dict() for ev in self._events]

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "request_id": self.request_id,
                "total_events": len(self._events),
                "active_count": len(self._active_handles),
                "cancelled": self._cancelled,
                "events": [ev.to_dict() for ev in self._events],
            }


class ActivityHandle:
    """Context manager binding an activity lifecycle to real execution."""

    def __init__(
        self,
        session: RequestActivitySession,
        event_id: str,
        spec: OperationSpec,
        parent_id: Optional[str] = None,
    ) -> None:
        self.session = session
        self.event_id = event_id
        self.spec = spec
        self.parent_id = parent_id
        self._started_at = time.monotonic()
        self._finalized = False
        self._lock = threading.Lock()

    @property
    def is_finalized(self) -> bool:
        with self._lock:
            return self._finalized

    def _elapsed_ms(self) -> int:
        return max(0, int((time.monotonic() - self._started_at) * 1000))

    def progress(
        self,
        *,
        label: Optional[str] = None,
        detail: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        unit: str = "items",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[ActivityEvent]:
        with self._lock:
            if self._finalized:
                return None
        prog = None
        if current is not None and total is not None and total > 0:
            prog = ActivityProgress(current=current, total=total, unit=unit)
        return self.session._emit(
            event_id=self.event_id,
            spec=self.spec,
            status=ActivityStatus.PROGRESSING,
            label=label or self.spec.started_label,
            detail=detail,
            parent_id=self.parent_id,
            progress=prog,
            meta=meta,
        )

    def complete(
        self,
        *,
        label: Optional[str] = None,
        detail: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[ActivityEvent]:
        with self._lock:
            if self._finalized:
                return None
            self._finalized = True
        return self.session._emit(
            event_id=self.event_id,
            spec=self.spec,
            status=ActivityStatus.COMPLETED,
            label=label or self.spec.completed_label,
            detail=detail,
            parent_id=self.parent_id,
            duration_ms=self._elapsed_ms(),
            meta=meta,
        )

    def fail(
        self,
        *,
        label: Optional[str] = None,
        detail: Optional[str] = None,
        error: Optional[BaseException] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[ActivityEvent]:
        with self._lock:
            if self._finalized:
                return None
            self._finalized = True
        merged_meta = dict(meta or {})
        if error is not None:
            merged_meta.setdefault("error_type", type(error).__name__)
        return self.session._emit(
            event_id=self.event_id,
            spec=self.spec,
            status=ActivityStatus.FAILED,
            label=label or self.spec.failed_label,
            detail=detail,
            parent_id=self.parent_id,
            duration_ms=self._elapsed_ms(),
            meta=merged_meta,
        )

    def skip(
        self,
        *,
        label: Optional[str] = None,
        detail: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[ActivityEvent]:
        with self._lock:
            if self._finalized:
                return None
            self._finalized = True
        return self.session._emit(
            event_id=self.event_id,
            spec=self.spec,
            status=ActivityStatus.SKIPPED,
            label=label or self.spec.skipped_label,
            detail=detail,
            parent_id=self.parent_id,
            duration_ms=self._elapsed_ms(),
            meta=meta,
        )

    def __enter__(self) -> "ActivityHandle":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if not self.is_finalized:
            if exc_type is not None:
                self.fail(
                    detail=str(exc_val)[:160] if exc_val else exc_type.__name__,
                    error=exc_val,
                )
            else:
                self.complete()
        return False


class _NullActivityHandle:
    event_id = "act_noop"
    parent_id = None
    is_finalized = False

    def progress(self, **kwargs: Any) -> None:
        return None

    def complete(self, **kwargs: Any) -> None:
        return None

    def fail(self, **kwargs: Any) -> None:
        return None

    def skip(self, **kwargs: Any) -> None:
        return None

    def __enter__(self) -> "_NullActivityHandle":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        return False


_current_session: contextvars.ContextVar[Optional[RequestActivitySession]] = (
    contextvars.ContextVar("vm_current_activity_session", default=None)
)


def create_activity_session(
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    persona: Optional[str] = None,
    listener: Optional[Callable[[ActivityEvent], None]] = None,
) -> RequestActivitySession:
    session = RequestActivitySession(
        request_id=request_id,
        user_id=user_id,
        persona=persona,
        listener=listener,
    )
    _current_session.set(session)
    return session


def get_current_session() -> Optional[RequestActivitySession]:
    return _current_session.get()


def clear_current_session() -> None:
    session = _current_session.get()
    if session is not None:
        session.close()
    _current_session.set(None)


def track_activity(
    operation: str,
    *,
    label: Optional[str] = None,
    detail: Optional[str] = None,
    parent_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Any:
    session = get_current_session()
    if session is None:
        return _NullActivityHandle()
    return session.start(
        operation,
        label=label,
        detail=detail,
        parent_id=parent_id,
        meta=meta,
    )


def record_skipped_activity(
    operation: str,
    *,
    label: Optional[str] = None,
    detail: Optional[str] = None,
    parent_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[ActivityEvent]:
    session = get_current_session()
    if session is None:
        return None
    return session.record_skipped(
        operation,
        label=label,
        detail=detail,
        parent_id=parent_id,
        meta=meta,
    )
