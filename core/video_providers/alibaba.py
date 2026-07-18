"""Alibaba DashScope video generation provider.

Uses the Alibaba DashScope async video generation API:
  - Endpoint: POST /api/v1/services/aigc/video-generation/generation
  - Model:    wanx2.1-t2v-turbo
  - Auth:     Bearer token from ALIBABA_MODEL_STUDIO_API_KEY
  - Flow:     Submit → Poll task → Download video

The API is fully asynchronous:
    1. Submit returns a task_id immediately
    2. Poll the task status until SUCCEEDED or FAILED
    3. On success, download the video to local static storage
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from core.config import PROJECT_ROOT, get_config
from core.video_providers.base import BaseVideoProvider, VideoTask, VideoTaskStatus


# ── Endpoints ──────────────────────────────────────────────────────
# Singapore / International accounts must use dashscope-intl.aliyuncs.com for
# the DashScope video API (NOT maas.aliyuncs.com, which is only the
# OpenAI/Anthropic-compatible text gateway). Override with DASHSCOPE_BASE if the
# account is on the China mainland region (dashscope.aliyuncs.com).

_DASHSCOPE_BASE = os.getenv("DASHSCOPE_BASE", "https://dashscope-intl.aliyuncs.com").rstrip("/")
# wan2.7-era models use the .../video-synthesis path (the older wanx2.1 models
# used .../generation, which now returns "url error" for wan2.7).
ALIBABA_VIDEO_URL = f"{_DASHSCOPE_BASE}/api/v1/services/aigc/video-generation/video-synthesis"
ALIBABA_TASK_URL = f"{_DASHSCOPE_BASE}/api/v1/tasks"

# Current Singapore Wan text-to-video model (override with ALIBABA_VIDEO_MODEL).
_DEFAULT_VIDEO_MODEL = os.getenv("ALIBABA_VIDEO_MODEL", "wan2.7-t2v-2026-06-12").strip()

# Polling configuration
POLL_INTERVAL = 5.0        # seconds between polls
MAX_POLL_SECONDS = 300.0   # 5 minutes max wait
INITIAL_BACKOFF = 3.0      # first poll delay after submit

# Local storage
VIDEO_DIR = PROJECT_ROOT / "static" / "generated" / "videos"


def _api_key() -> str:
    return get_config().alibaba_api_key


def _available() -> bool:
    return bool(_api_key())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def _ensure_dir() -> None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def _save_video(data: bytes, ext: str = "mp4") -> str:
    _ensure_dir()
    if ext not in ("mp4", "webm", "mov", "avi"):
        ext = "mp4"
    filename = f"{uuid.uuid4().hex}_{datetime.now():%Y%m%d%H%M%S}.{ext}"
    filepath = VIDEO_DIR / filename
    filepath.write_bytes(data)
    return f"/static/generated/videos/{filename}"


def _map_task_status(provider_status: str) -> VideoTaskStatus:
    """Map Alibaba's task_status strings to our lifecycle states."""
    mapping = {
        "PENDING": VideoTaskStatus.SUBMITTED,
        "QUEUED": VideoTaskStatus.QUEUED,
        "RUNNING": VideoTaskStatus.PROCESSING,
        "SUCCEEDED": VideoTaskStatus.COMPLETED,
        "FAILED": VideoTaskStatus.FAILED,
        "CANCELED": VideoTaskStatus.FAILED,
        "UNKNOWN": VideoTaskStatus.QUEUED,
    }
    return mapping.get(provider_status.upper(), VideoTaskStatus.PROCESSING)


class AlibabaVideoProvider(BaseVideoProvider):
    """Alibaba DashScope text-to-video provider.

    Implements the full async lifecycle:
        submit → poll → download → save locally
    """

    name = "AlibabaVideo"
    priority = 10

    def submit(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        start = time.perf_counter()
        try:
            key = _api_key()
            if not key:
                raise RuntimeError("ALIBABA_MODEL_STUDIO_API_KEY not configured")

            prompt = task.prompt
            if not prompt:
                raise RuntimeError("No prompt provided for video generation")

            model = kwargs.get("model") or _DEFAULT_VIDEO_MODEL

            resp = requests.post(
                ALIBABA_VIDEO_URL,
                # The video-synthesis endpoint is async-only: without this header
                # DashScope rejects with "current user api does not support
                # synchronous calls".
                headers={**_headers(), "X-DashScope-Async": "enable"},
                json={
                    "model": model,
                    "input": {"prompt": prompt},
                    "parameters": {
                        "size": kwargs.get("size", "1280*720"),
                    },
                },
                timeout=60,
            )

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Alibaba video submit HTTP {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            output = data.get("output") or {}
            provider_task_id = output.get("task_id", "")

            if not provider_task_id:
                raise RuntimeError(f"Alibaba video returned no task_id: {data}")

            task.provider_task_id = provider_task_id
            task.status = VideoTaskStatus.SUBMITTED
            task.metadata["submit_latency_ms"] = (time.perf_counter() - start) * 1000
            task.metadata["provider_model"] = model
            self.record_success()

            print(f"[VIDEO] Alibaba submit OK — task_id={provider_task_id}")
            return task

        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            task.status = VideoTaskStatus.FAILED
            task.error = str(exc)
            task.metadata["submit_latency_ms"] = elapsed
            self.record_failure()
            print(f"[VIDEO] Alibaba submit FAILED: {exc}")
            return task

    def poll(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        if not task.provider_task_id:
            task.status = VideoTaskStatus.FAILED
            task.error = "No provider task ID to poll"
            return task

        try:
            resp = requests.get(
                f"{ALIBABA_TASK_URL}/{task.provider_task_id}",
                headers=_headers(),
                timeout=30,
            )

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Alibaba task poll HTTP {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            output = data.get("output") or {}
            provider_status = output.get("task_status", "UNKNOWN")

            task.status = _map_task_status(provider_status)

            # Extract progress if provider reports it
            if "progress" in output:
                try:
                    task.progress = float(output["progress"])
                except (ValueError, TypeError):
                    pass

            # On success, extract video URL and download
            if task.status == VideoTaskStatus.COMPLETED:
                video_url = (
                    output.get("video_url")
                    or output.get("url")
                    or (output.get("results", [{}])[0].get("url") if output.get("results") else "")
                    or ""
                )

                if not video_url:
                    task.status = VideoTaskStatus.FAILED
                    task.error = "Alibaba video succeeded but returned no video URL"
                    return task

                # Download video to local storage
                video_response = requests.get(video_url, timeout=120)
                video_response.raise_for_status()

                content_type = video_response.headers.get("content-type", "video/mp4")
                ext = content_type.split("/")[-1].split(";")[0] if "/" in content_type else "mp4"
                local_path = _save_video(video_response.content, ext)
                task.video_url = local_path
                task.metadata["original_video_url"] = video_url

                print(f"[VIDEO] Alibaba video downloaded → {local_path}")

            # On failure, capture error message
            if task.status == VideoTaskStatus.FAILED:
                task.error = output.get("message", "Video generation failed")
                print(f"[VIDEO] Alibaba video FAILED: {task.error}")

            return task

        except Exception as exc:
            task.status = VideoTaskStatus.FAILED
            task.error = str(exc)
            self.record_failure()
            print(f"[VIDEO] Alibaba poll FAILED: {exc}")
            return task


def discover() -> list[BaseVideoProvider]:
    """Auto-discovery entry point for ProviderManager integration."""
    if not _available():
        return []
    return [AlibabaVideoProvider()]
