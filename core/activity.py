"""Unified Contextual Activity Indicator system.

Centralized activity-status mechanism for ValleyMind's shared AI backend.
Describes the operation being performed (not the agent identity), observed
from real application events. All personas and agents inherit this through
the shared backend — there is exactly one activity manager, not one per
persona.

Design goals:
  * One centralized registry of activity categories/messages.
  * Per-request/session activity tracker (no stale state leakage).
  * Backend yields activity events via SSE; frontend renders them.
  * Activities reflect real operations, never invented progress.
  * One meaningful activity visible at a time; no rapid cycling.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ═════════════════════════════════════════════════════════════════════════════
# Activity categories
# ═════════════════════════════════════════════════════════════════════════════

class ActivityCategory(str, Enum):
    """High-level activity categories."""
    GENERAL = "general"
    IMAGE_VIDEO = "image_video"
    WEB_RESEARCH = "web_research"
    MEMORY = "memory"
    SUMMARIZATION = "summarization"
    FILES_DOCUMENTS = "files_documents"
    CODING = "coding"
    OTHER = "other"


@dataclass
class Activity:
    """A single activity event."""
    category: str
    label: str
    started_at: float
    ended_at: Optional[float] = None
    status: str = "active"   # active | completed | failed | cancelled
    detail: str = ""


# ═════════════════════════════════════════════════════════════════════════════
# Central registry of activity categories and message templates
# ═════════════════════════════════════════════════════════════════════════════

class ActivityRegistry:
    """Central registry. Each category maps activity keys to message templates.

    The first template in each list is the default label. Backends should
    reference activities by (category, key) so the wording stays centralized.
    """

    ACTIVITIES: Dict[str, Dict[str, List[str]]] = {
        ActivityCategory.GENERAL: {
            "understanding": ["Understanding your request…"],
            "processing": ["Processing your request…"],
            "thinking": ["Thinking…"],
            "formulating": ["Formulating an answer…"],
            "preparing_response": ["Preparing your response…"],
        },
        ActivityCategory.IMAGE_VIDEO: {
            "examining_image": ["Examining image…"],
            "processing_images": ["Processing multiple images…"],
            "analyzing_video": ["Analyzing video…"],
            "comparing_videos": ["Comparing videos…"],
            "extracting_visual": ["Extracting visual information…"],
            "reviewing_media": ["Reviewing media…"],
            "combining_findings": ["Combining visual findings…"],
        },
        ActivityCategory.WEB_RESEARCH: {
            "searching_web": ["Searching the web…"],
            "finding_sources": ["Finding relevant sources…"],
            "reading_pages": ["Reading webpages…"],
            "researching": ["Researching the topic…"],
            "checking_info": ["Checking information…"],
            "comparing_sources": ["Comparing sources…"],
            "verifying": ["Verifying details…"],
        },
        ActivityCategory.MEMORY: {
            "recalling": ["Recalling relevant memories…"],
            "searching_memory": ["Searching long-term memory…"],
            "retrieving_context": ["Retrieving previous context…"],
            "updating_memory": ["Updating long-term memory…"],
            "saving_memory": ["Saving information to memory…"],
            "organizing_memory": ["Organizing remembered information…"],
        },
        ActivityCategory.SUMMARIZATION: {
            "summarizing": ["Summarizing information…"],
            "summarizing_conversation": ["Summarizing the conversation…"],
            "condensing": ["Condensing key points…"],
            "compiling": ["Compiling results…"],
            "combining_info": ["Combining information…"],
            "organizing_findings": ["Organizing findings…"],
            "preparing_summary": ["Preparing a summary…"],
            "compiling_report": ["Compiling your report…"],
        },
        ActivityCategory.FILES_DOCUMENTS: {
            "reading_files": ["Reading your files…"],
            "inspecting_doc": ["Inspecting document…"],
            "extracting_info": ["Extracting information…"],
            "processing_doc": ["Processing document…"],
            "analyzing_file": ["Analyzing file contents…"],
            "organizing_docs": ["Organizing document information…"],
        },
        ActivityCategory.CODING: {
            "inspecting_code": ["Inspecting source code…"],
            "analyzing_code": ["Analyzing code…"],
            "running_code": ["Running code…"],
            "executing_tests": ["Executing tests…"],
            "debugging": ["Debugging the issue…"],
            "applying_changes": ["Applying changes…"],
            "checking_errors": ["Checking for errors…"],
            "verifying": ["Verifying implementation…"],
            "building": ["Building your project…"],
        },
        ActivityCategory.OTHER: {
            "generating_image": ["Generating an image…"],
            "creating_doc": ["Creating your document…"],
            "processing": ["Processing your request…"],
            "connecting": ["Connecting information…"],
            "finalizing": ["Finalizing results…"],
        },
    }

    @classmethod
    def labels(cls, category: str, key: str) -> List[str]:
        return cls.ACTIVITIES.get(category, {}).get(key, ["Processing…"])


# ═════════════════════════════════════════════════════════════════════════════
# Per-request activity tracker
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ActivityTracker:
    """Tracks the current activity for a single request/session."""

    request_id: str
    current: Optional[Activity] = None
    history: List[Activity] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self, category: str, key: str,
              label: Optional[str] = None, detail: str = "") -> Activity:
        """Begin a new activity. Completes any previously-active activity."""
        with self.lock:
            if self.current and self.current.status == "active":
                self.current.ended_at = time.time()
                self.current.status = "completed"
                self.history.append(self.current)

            labels = ActivityRegistry.labels(category, key)
            final_label = label or labels[0]
            activity = Activity(
                category=category,
                label=final_label,
                started_at=time.time(),
                status="active",
                detail=detail,
            )
            self.current = activity
            return activity

    def update(self, label: str, detail: str = "") -> Optional[Activity]:
        """Update the label/detail of the currently-active activity."""
        with self.lock:
            if self.current and self.current.status == "active":
                self.current.label = label
                if detail:
                    self.current.detail = detail
                return self.current
            return None

    def complete(self, status: str = "completed") -> Optional[Activity]:
        """Mark the current activity as completed and clear it."""
        with self.lock:
            if self.current and self.current.status == "active":
                self.current.ended_at = time.time()
                self.current.status = status
                activity = self.current
                self.current = None
                self.history.append(activity)
                return activity
            return None

    def fail(self, detail: str = "") -> Optional[Activity]:
        """Mark the current activity as failed and clear it."""
        with self.lock:
            if self.current and self.current.status == "active":
                self.current.ended_at = time.time()
                self.current.status = "failed"
                if detail:
                    self.current.detail = detail
                activity = self.current
                self.current = None
                self.history.append(activity)
                return activity
            return None

    def cancel(self) -> Optional[Activity]:
        """Cancel the current activity (e.g. request cancelled)."""
        with self.lock:
            if self.current and self.current.status == "active":
                self.current.ended_at = time.time()
                self.current.status = "cancelled"
                activity = self.current
                self.current = None
                self.history.append(activity)
                return activity
            return None

    def get_current(self) -> Optional[dict]:
        """Return a serializable snapshot of the current activity, or None."""
        with self.lock:
            if self.current:
                return {
                    "category": self.current.category,
                    "label": self.current.label,
                    "detail": self.current.detail,
                    "status": self.current.status,
                    "started_at": self.current.started_at,
                }
            return None


# ═════════════════════════════════════════════════════════════════════════════
# Manager — one shared instance, request-scoped trackers
# ═════════════════════════════════════════════════════════════════════════════

class ActivityManager:
    """Singleton. Maintains one ActivityTracker per request_id.

    Trackers are keyed by request/session id so status from one request never
    leaks into another.
    """

    _instance: Optional["ActivityManager"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls):  # type: ignore[override]
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._trackers: Dict[str, ActivityTracker] = {}
            cls._instance._registry_lock = threading.Lock()
        return cls._instance

    def get_tracker(self, request_id: str) -> ActivityTracker:
        with self._registry_lock:
            tracker = self._trackers.get(request_id)
            if tracker is None:
                tracker = ActivityTracker(request_id)
                self._trackers[request_id] = tracker
            return tracker

    def remove_tracker(self, request_id: str) -> None:
        with self._registry_lock:
            self._trackers.pop(request_id, None)

    def get_current(self, request_id: str) -> Optional[dict]:
        tracker = self.get_tracker(request_id)
        return tracker.get_current()


# Module-level singleton — the single activity manager for the whole app.
activity_manager = ActivityManager()


def activity_event(request_id: str, category: str, key: str,
                   label: Optional[str] = None, detail: str = "") -> dict:
    """Start an activity and return a serializable SSE event dict.

    The returned dict is shaped so the backend can ``yield`` it directly:
        yield activity_event(request_id, "web_research", "searching_web")
    """
    tracker = activity_manager.get_tracker(request_id)
    act = tracker.start(category, key, label=label, detail=detail)
    return {
        "activity": {
            "category": act.category,
            "label": act.label,
            "detail": act.detail,
            "status": act.status,
            "started_at": act.started_at,
        }
    }


def activity_complete(request_id: str) -> Optional[dict]:
    """Complete the current activity and return a completion event."""
    tracker = activity_manager.get_tracker(request_id)
    act = tracker.complete()
    if act is None:
        return None
    return {
        "activity_complete": {
            "category": act.category,
            "label": act.label,
            "status": act.status,
        }
    }


def activity_fail(request_id: str, detail: str = "") -> Optional[dict]:
    """Mark the current activity as failed."""
    tracker = activity_manager.get_tracker(request_id)
    act = tracker.fail(detail)
    if act is None:
        return None
    return {
        "activity_failed": {
            "category": act.category,
            "label": act.label,
            "detail": act.detail,
            "status": act.status,
        }
    }


def activity_cancel(request_id: str) -> Optional[dict]:
    """Cancel the current activity."""
    tracker = activity_manager.get_tracker(request_id)
    act = tracker.cancel()
    if act is None:
        return None
    return {
        "activity_cancelled": {
            "category": act.category,
            "label": act.label,
            "status": act.status,
        }
    }
