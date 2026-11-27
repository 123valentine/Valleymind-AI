"""Zero-key Pollinations video fallback (sync response or async job)."""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from core.config import PROJECT_ROOT
from core.cost_control import provider_blocked, record_error, record_success
from core.failover import status_code_of
from core.video_providers.base import BaseVideoProvider, VideoTask, VideoTaskStatus

VIDEO_BASE = os.getenv(
    "POLLINATIONS_VIDEO_BASE", "https://gen.pollinations.ai/video"
).rstrip("/")
JOB_BASE = "https://gen.pollinations.ai/v1/jobs"
VIDEO_DIR = PROJECT_ROOT / "static" / "generated" / "videos"


def _api_key() -> str:
    return (os.getenv("POLLINATIONS_API_KEY") or "").strip()


def _available() -> bool:
    return not provider_blocked("pollinations_video")


def _models() -> list[str]:
    configured = (os.getenv("POLLINATIONS_VIDEO_MODELS") or "").strip()
    models = [part.strip() for part in configured.split(",") if part.strip()] if configured else []
    return models or ["wan", "seedance"]


def _headers() -> dict[str, str]:
    key = _api_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _save_video(data: bytes, ext: str = "mp4") -> str:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    if ext not in ("mp4", "webm", "mov", "avi"):
        ext = "mp4"
    filename = f"{uuid.uuid4().hex}_{datetime.now():%Y%m%d%H%M%S}.{ext}"
    (VIDEO_DIR / filename).write_bytes(data)
    return f"/static/generated/videos/{filename}"


def _await_job(job_id: str, headers: dict[str, str], wait_seconds: int) -> bytes:
    deadline = time.time() + max(5, wait_seconds)
    while time.time() < deadline:
        status_response = requests.get(f"{JOB_BASE}/{job_id}", headers=headers, timeout=30)
        if status_response.status_code not in (200, 202):
            raise RuntimeError(f"Video job status HTTP {status_response.status_code}")
        payload = status_response.json() if status_response.content else {}
        status = str(payload.get("status") or "").lower()
        if status in ("succeeded", "success", "completed"):
            result = requests.get(f"{JOB_BASE}/{job_id}/result", headers=headers, timeout=120)
            if result.status_code == 200 and result.content:
                return result.content
            raise RuntimeError(f"Video result HTTP {result.status_code}")
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError("Video job failed")
        time.sleep(2)
    raise TimeoutError("Video job timed out")


def _request_video(prompt: str, model: str, timeout: int = 180, job_wait: int = 180) -> bytes:
    params = {"model": model, "duration": 4, "nologo": "true"}
    key = _api_key()
    if key:
        params["key"] = key
    response = requests.get(
        f"{VIDEO_BASE}/{quote(prompt, safe='')}",
        params=params,
        headers=_headers(),
        timeout=timeout,
    )
    if response.status_code == 200 and response.content:
        return response.content
    if response.status_code in (200, 201, 202):
        payload = response.json() if response.content else {}
        job_id = payload.get("id") or payload.get("job_id")
        if job_id:
            return _await_job(str(job_id), _headers(), job_wait)
    error = str(getattr(response, "text", ""))[:300]
    failure = RuntimeError(f"Video HTTP {response.status_code}: {error}")
    setattr(failure, "status_code", response.status_code)
    setattr(failure, "response", response)
    raise failure


class PollinationsVideoProvider(BaseVideoProvider):
    name = "PollinationsVideo"
    priority = 15
    health = "healthy"

    def submit(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        if not _available():
            task.status = VideoTaskStatus.FAILED
            task.error = "Video generation unavailable"
            return task

        last_error: Exception | None = None
        for model in _models():
            try:
                data = _request_video(
                    task.prompt,
                    model,
                    timeout=kwargs.get("timeout", 180),
                    job_wait=kwargs.get("job_wait", 180),
                )
                if not data:
                    raise RuntimeError("Video response was empty")
                task.status = VideoTaskStatus.COMPLETED
                task.progress = 1.0
                task.video_url = _save_video(data)
                task.metadata["_provider"] = self.name
                task.metadata["model"] = model
                record_success("pollinations_video")
                return task
            except Exception as exc:
                last_error = exc
                response = getattr(exc, "response", None)
                record_error(
                    "pollinations_video",
                    status_code=status_code_of(exc),
                    body=str(getattr(response, "text", ""))[:300],
                    exc=exc,
                )
                print(f"[VIDEO] Backup model attempt failed: {exc}")

        task.status = VideoTaskStatus.FAILED
        task.error = str(last_error) if last_error else "Video generation failed"
        return task

    def poll(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        return task


def discover() -> list[BaseVideoProvider]:
    if not _available():
        return []
    return [PollinationsVideoProvider()]
