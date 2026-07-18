"""fal.ai text-to-video provider (fallback after Alibaba).

Uses the fal.ai queue API:
  - Submit: POST https://queue.fal.run/{model}
  - Poll:   GET  https://queue.fal.run/{model}/requests/{id}/status
  - Result: GET  https://queue.fal.run/{model}/requests/{id}
  - Auth:   Authorization: Key {FAL_KEY}
  - Model:  default fal-ai/ltx-video (fast t2v), override with FAL_VIDEO_MODEL

The API is fully asynchronous: submit returns a request_id, poll until
COMPLETED, then fetch the result payload and read the video URL. Download
of the MP4 happens in app.py's dispatch via the shared MediaManager, same
as Alibaba, so this provider only needs to surface the final video_url.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from core.video_providers.base import BaseVideoProvider, VideoTask, VideoTaskStatus


FAL_QUEUE_BASE = "https://queue.fal.run"


def _api_key() -> str:
    # The env var was originally stored under the awkward name "fal.ai";
    # prefer conventional names but fall back to it for compatibility.
    return (
        os.getenv("FAL_KEY", "").strip()
        or os.getenv("FAL_API_KEY", "").strip()
        or os.getenv("fal.ai", "").strip()
    )


def _model() -> str:
    return os.getenv("FAL_VIDEO_MODEL", "fal-ai/ltx-video").strip()


def _available() -> bool:
    return bool(_api_key())


def _headers() -> dict[str, str]:
    return {"Authorization": f"Key {_api_key()}", "Content-Type": "application/json"}


def _map_status(fal_status: str) -> VideoTaskStatus:
    mapping = {
        "IN_QUEUE": VideoTaskStatus.QUEUED,
        "IN_PROGRESS": VideoTaskStatus.PROCESSING,
        "COMPLETED": VideoTaskStatus.COMPLETED,
    }
    return mapping.get((fal_status or "").upper(), VideoTaskStatus.PROCESSING)


def _extract_video_url(payload: dict) -> str:
    """fal video models return the URL under slightly different shapes."""
    if not isinstance(payload, dict):
        return ""
    video = payload.get("video")
    if isinstance(video, dict) and video.get("url"):
        return video["url"]
    if isinstance(video, str):
        return video
    videos = payload.get("videos")
    if isinstance(videos, list) and videos:
        first = videos[0]
        if isinstance(first, dict) and first.get("url"):
            return first["url"]
        if isinstance(first, str):
            return first
    if payload.get("url"):
        return payload["url"]
    return ""


class FalVideoProvider(BaseVideoProvider):
    """fal.ai text-to-video provider — fallback after Alibaba (higher priority number)."""

    name = "FalVideo"
    priority = 20  # tried after Alibaba (priority 10)

    def submit(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        start = time.perf_counter()
        try:
            key = _api_key()
            if not key:
                raise RuntimeError("FAL_KEY not configured")
            if not task.prompt:
                raise RuntimeError("No prompt provided for video generation")

            model = kwargs.get("fal_model") or _model()
            resp = requests.post(
                f"{FAL_QUEUE_BASE}/{model}",
                headers=_headers(),
                json={"prompt": task.prompt},
                timeout=60,
            )
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"fal submit HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()
            request_id = data.get("request_id") or data.get("requestId") or ""
            if not request_id:
                raise RuntimeError(f"fal returned no request_id: {str(data)[:200]}")

            task.provider_task_id = request_id
            task.status = VideoTaskStatus.SUBMITTED
            task.metadata["fal_model"] = model
            task.metadata["submit_latency_ms"] = (time.perf_counter() - start) * 1000
            self.record_success()
            print(f"[VIDEO] fal submit OK — request_id={request_id} model={model}")
            return task

        except Exception as exc:
            task.status = VideoTaskStatus.FAILED
            task.error = str(exc)
            self.record_failure()
            print(f"[VIDEO] fal submit FAILED: {exc}")
            return task

    def poll(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        if not task.provider_task_id:
            task.status = VideoTaskStatus.FAILED
            task.error = "No provider task ID to poll"
            return task

        model = task.metadata.get("fal_model") or _model()
        try:
            sr = requests.get(
                f"{FAL_QUEUE_BASE}/{model}/requests/{task.provider_task_id}/status",
                headers=_headers(),
                timeout=30,
            )
            if sr.status_code != 200:
                raise RuntimeError(f"fal status HTTP {sr.status_code}: {sr.text[:200]}")
            fal_status = sr.json().get("status", "")
            task.status = _map_status(fal_status)

            if task.status == VideoTaskStatus.COMPLETED:
                rr = requests.get(
                    f"{FAL_QUEUE_BASE}/{model}/requests/{task.provider_task_id}",
                    headers=_headers(),
                    timeout=30,
                )
                if rr.status_code != 200:
                    raise RuntimeError(f"fal result HTTP {rr.status_code}: {rr.text[:200]}")
                video_url = _extract_video_url(rr.json())
                if not video_url:
                    task.status = VideoTaskStatus.FAILED
                    task.error = "fal completed but returned no video URL"
                    return task
                task.video_url = video_url
                print(f"[VIDEO] fal video ready → {video_url[:120]}")

            return task

        except Exception as exc:
            task.status = VideoTaskStatus.FAILED
            task.error = str(exc)
            self.record_failure()
            print(f"[VIDEO] fal poll FAILED: {exc}")
            return task


def discover() -> list[BaseVideoProvider]:
    if not _available():
        return []
    return [FalVideoProvider()]
