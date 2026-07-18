"""Video Generation Dispatcher — orchestrates the full video generation lifecycle.

Responsibilities:
    1. Manage a registry of video providers (priority-ordered fallback)
    2. Submit generation requests to the best available provider
    3. Poll for progress and status updates
    4. Track all active and completed tasks
    5. Yield lifecycle events for SSE streaming to the frontend

The dispatcher is completely independent from the router.
It owns the full generation lifecycle from submit → complete/fail.

Usage in app.py:
    from core.video_dispatcher import get_video_dispatcher

    dispatcher = get_video_dispatcher()
    for event in dispatcher.generate_stream(prompt):
        yield f"data: {json.dumps(event)}\n\n"
"""

from __future__ import annotations

import importlib
import time
from typing import Any, Iterator

from core.config import get_config
from core.video_providers.base import (
    BaseVideoProvider,
    VideoTask,
    VideoTaskStatus,
)


# ── Provider Registry ──────────────────────────────────────────────

def _discover_providers() -> list[BaseVideoProvider]:
    """Auto-discover video providers from known modules."""
    providers: list[BaseVideoProvider] = []
    module_paths = [
        "core.video_providers.alibaba",
        "core.video_providers.fal",
    ]

    for module_path in module_paths:
        try:
            mod = importlib.import_module(module_path)
            if hasattr(mod, "discover"):
                discovered = mod.discover()
                for p in discovered:
                    if isinstance(p, BaseVideoProvider):
                        providers.append(p)
        except Exception as exc:
            print(f"[VIDEO] Provider discovery from {module_path} skipped: {exc}")

    return providers


# ── Dispatcher ─────────────────────────────────────────────────────

class VideoDispatcher:
    """Manages video generation across multiple providers with automatic fallback.

    Thread-safety: All state is per-request (one dispatcher instance per
    generation cycle).  The module singleton is safe for single-request
    use; for concurrent requests, create fresh instances.
    """

    def __init__(self) -> None:
        self._providers = sorted(
            _discover_providers(),
            key=lambda p: p.priority,
        )
        self._tasks: dict[str, VideoTask] = {}

        if not self._providers:
            print("[VIDEO] WARNING: No video providers registered")

    @property
    def provider_count(self) -> int:
        return len(self._providers)

    def get_task(self, task_id: str) -> VideoTask | None:
        return self._tasks.get(task_id)

    # ── Synchronous generation (for /chat non-streaming) ────────────

    def generate(self, prompt: str, **kwargs: Any) -> VideoTask:
        """Generate a video synchronously — blocks until complete or failed.

        Returns the final VideoTask with status COMPLETED or FAILED.
        """
        task = VideoTask(prompt=prompt)

        task = self._submit(task, **kwargs)
        if task.status == VideoTaskStatus.FAILED:
            return task

        task = self._poll_until_done(task, **kwargs)
        self._tasks[task.task_id] = task
        return task

    # ── Streaming generation (for /chat/stream SSE) ─────────────────

    def generate_stream(self, prompt: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        """Generate a video and yield lifecycle events for SSE streaming.

        Yields dicts ready for json.dumps → SSE:
            {"intent": "generating_video", "query": "...", "status": "preparing", ...}
            {"intent": "video_progress", "status": "processing", "progress": 0.3, ...}
            {"video_url": "...", "thumbnail_url": "..."}
            {"error": "..."}
        """
        task = VideoTask(prompt=prompt)

        # ── Submit ───────────────────────────────────────────────────
        yield {
            "intent": "generating_video",
            "query": prompt,
            "status": task.status.value,
            "status_message": task.status_message,
            "task_id": task.task_id,
        }

        task = self._submit(task, **kwargs)
        self._tasks[task.task_id] = task

        if task.status == VideoTaskStatus.FAILED:
            yield {"error": task.error}
            return

        yield {
            "intent": "video_progress",
            "status": task.status.value,
            "status_message": task.status_message,
            "task_id": task.task_id,
        }

        # ── Poll until terminal ──────────────────────────────────────
        last_status = task.status
        deadline = time.time() + kwargs.get("max_poll_seconds", 300)

        while not task.is_terminal and time.time() < deadline:
            time.sleep(kwargs.get("poll_interval", 5.0))

            task = self._poll(task, **kwargs)
            self._tasks[task.task_id] = task

            # Only yield progress events when status changes
            if task.status != last_status:
                last_status = task.status
                yield {
                    "intent": "video_progress",
                    "status": task.status.value,
                    "status_message": task.status_message,
                    "progress": task.progress,
                    "task_id": task.task_id,
                }

        # ── Terminal state ───────────────────────────────────────────
        if task.status == VideoTaskStatus.COMPLETED:
            yield {
                "video_url": task.video_url,
                "thumbnail_url": task.thumbnail_url,
                "duration_seconds": task.duration_seconds,
                "task_id": task.task_id,
            }
        elif task.status == VideoTaskStatus.FAILED:
            yield {"error": task.error or "Video generation timed out"}
        else:
            yield {"error": "Video generation timed out — please try again"}

    # ── Internal methods ────────────────────────────────────────────

    def _submit(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        """Submit to the best available provider with fallback."""
        for provider in self._providers:
            if provider.health == "down":
                print(f"[VIDEO] Skipping down provider: {provider.name}")
                continue

            print(f"[VIDEO] Submitting to {provider.name}...")
            task = provider.submit(task, **kwargs)

            if task.status != VideoTaskStatus.FAILED:
                return task

            print(f"[VIDEO] Provider {provider.name} failed, trying next...")

        # All providers failed
        if task.status != VideoTaskStatus.FAILED:
            task.status = VideoTaskStatus.FAILED
            task.error = "All video providers failed"
        return task

    def _poll(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        """Poll the provider that submitted this task."""
        # Find the provider that matches this task's provider_task_id
        for provider in self._providers:
            if provider.health != "down":
                try:
                    return provider.poll(task, **kwargs)
                except Exception as exc:
                    task.error = str(exc)
                    return task

        task.status = VideoTaskStatus.FAILED
        task.error = "No available provider to poll"
        return task

    def _poll_until_done(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        """Poll until terminal state or timeout."""
        max_seconds = kwargs.get("max_poll_seconds", 300)
        interval = kwargs.get("poll_interval", 5.0)
        deadline = time.time() + max_seconds

        while not task.is_terminal and time.time() < deadline:
            time.sleep(interval)
            task = self._poll(task, **kwargs)
            self._tasks[task.task_id] = task

            if not task.is_terminal:
                print(f"[VIDEO] Task {task.task_id}: {task.status.value} (progress={task.progress:.0%})")

        if not task.is_terminal:
            task.status = VideoTaskStatus.FAILED
            task.error = "Video generation timed out"

        return task


# ── Module singleton ───────────────────────────────────────────────

_dispatcher: VideoDispatcher | None = None


def get_video_dispatcher() -> VideoDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = VideoDispatcher()
    return _dispatcher
