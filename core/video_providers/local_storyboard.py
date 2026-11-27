"""Zero-network local storyboard fallback for video requests."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

from core.cost_control import provider_blocked, record_success
from core.video_assembly import available as ffmpeg_available, hard_cut, make_title_card
from core.video_providers.base import BaseVideoProvider, VideoTask, VideoTaskStatus

VIDEO_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "static", "generated", "videos")


def _output_dir() -> str:
    path = os.path.abspath(VIDEO_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def _build_local_video(prompt: str) -> str:
    """Render a short, playable storyboard card sequence with local ffmpeg."""
    folder = _output_dir()
    stem = f"vm-storyboard-{uuid.uuid4().hex}_{datetime.now():%Y%m%d%H%M%S}"
    cards = [
        os.path.join(folder, f"{stem}-1.mp4"),
        os.path.join(folder, f"{stem}-2.mp4"),
        os.path.join(folder, f"{stem}-3.mp4"),
    ]
    labels = [prompt or "A short visual story", "Scene one: the setup", "Scene two: the payoff"]
    made = []
    for label, path in zip(labels, cards):
        ok, _ = make_title_card(label, path, seconds=1.8, timeout=120)
        if ok:
            made.append(path)

    if not made:
        raise RuntimeError("Local video encoder unavailable")

    final = os.path.join(folder, f"{stem}.mp4")
    if len(made) > 1:
        ok, _ = hard_cut(made, final, timeout=180)
        if not ok and os.path.exists(made[0]):
            final = made[0]
    else:
        final = made[0]

    for path in made:
        if path != final:
            try:
                os.remove(path)
            except OSError:
                pass

    filename = os.path.basename(final)
    return f"/static/generated/videos/{filename}"


class LocalStoryboardVideoProvider(BaseVideoProvider):
    name = "LocalStoryboard"
    priority = 900
    health = "healthy"

    def submit(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        if provider_blocked("ffmpeg"):
            task.status = VideoTaskStatus.FAILED
            task.error = "Video generation unavailable"
            return task
        try:
            task.video_url = _build_local_video(task.prompt)
            task.status = VideoTaskStatus.COMPLETED
            task.progress = 1.0
            task.metadata["_provider"] = self.name
            task.metadata["model"] = "local-storyboard"
            record_success("ffmpeg")
        except Exception as exc:
            task.status = VideoTaskStatus.FAILED
            task.error = str(exc)
        return task

    def poll(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        return task


def discover() -> list[BaseVideoProvider]:
    if not ffmpeg_available() or provider_blocked("ffmpeg"):
        return []
    return [LocalStoryboardVideoProvider()]
