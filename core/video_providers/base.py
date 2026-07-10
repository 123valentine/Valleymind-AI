"""Abstract video provider interface and lifecycle state machine.

Every video provider must subclass BaseVideoProvider and implement:
    - submit(prompt, **kwargs) -> VideoTask
    - poll(task) -> VideoTask

The rest of the application interacts only with this interface.
Provider-specific details (API endpoints, auth, polling intervals) are
fully encapsulated in the concrete implementation.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class VideoTaskStatus(str, Enum):
    """Lifecycle states for video generation.

    Flow:
        PENDING → SUBMITTED → QUEUED → PROCESSING → RENDERING → COMPLETED
                                                                    ↘ FAILED
    Each state maps to a user-facing status message.
    """
    PENDING = "pending"
    SUBMITTED = "submitted"
    QUEUED = "queued"
    PROCESSING = "processing"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"


# Maps task status to user-facing progress messages
STATUS_MESSAGES: dict[VideoTaskStatus, str] = {
    VideoTaskStatus.PENDING: "Preparing video request...",
    VideoTaskStatus.SUBMITTED: "Uploading request to video engine...",
    VideoTaskStatus.QUEUED: "Video queued for generation...",
    VideoTaskStatus.PROCESSING: "Generating video frames...",
    VideoTaskStatus.RENDERING: "Rendering final video...",
    VideoTaskStatus.COMPLETED: "Video ready!",
    VideoTaskStatus.FAILED: "Video generation failed.",
}


@dataclass
class VideoTask:
    """Represents a single video generation job through its full lifecycle."""
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    provider_task_id: str = ""
    status: VideoTaskStatus = VideoTaskStatus.PENDING
    prompt: str = ""
    video_url: str = ""
    thumbnail_url: str = ""
    duration_seconds: float = 0.0
    error: str = ""
    progress: float = 0.0        # 0.0 — 1.0 if provider reports it
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in (VideoTaskStatus.COMPLETED, VideoTaskStatus.FAILED)

    @property
    def status_message(self) -> str:
        return STATUS_MESSAGES.get(self.status, "Unknown status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "status_message": self.status_message,
            "video_url": self.video_url,
            "thumbnail_url": self.thumbnail_url,
            "duration_seconds": self.duration_seconds,
            "progress": self.progress,
            "error": self.error,
        }


class BaseVideoProvider(ABC):
    """Abstract base for video generation providers.

    Subclasses must implement:
        - submit: Send the generation request, return an updated task
        - poll:   Check status of an in-progress task, return updated task

    The provider owns all API-specific logic:
        - Authentication
        - Request formatting
        - Status mapping
        - File saving
        - Error handling
    """

    name: str = ""
    priority: int = 100       # lower = tried first
    health: str = "unknown"   # "healthy" | "degraded" | "down"

    def __init__(self) -> None:
        self._consecutive_failures = 0

    @abstractmethod
    def submit(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        """Submit a video generation request.

        Parameters
        ----------
        task : VideoTask with prompt set, status = PENDING.
        kwargs : provider-specific options (api_key, model, duration, etc.)

        Returns
        -------
        Updated VideoTask with provider_task_id set and status advanced.
        """
        ...

    @abstractmethod
    def poll(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        """Check the status of an in-progress generation.

        Parameters
        ----------
        task : VideoTask with provider_task_id set.
        kwargs : provider-specific options.

        Returns
        -------
        Updated VideoTask with status, progress, video_url, or error.
        """
        ...

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self.health = "healthy"

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= 3:
            self.health = "degraded"
        if self._consecutive_failures >= 10:
            self.health = "down"
