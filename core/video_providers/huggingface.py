"""Official Lightricks/LTX-2.5 ZeroGPU Space video provider.

Text-to-video and image-to-video both go through ``gradio_client.Client``.
The Space's current Gradio schema is inspected at call time so parameter
labels stay in sync as the Space evolves. Failures hand off to the next
provider in the dispatcher chain (Pollinations, then local storyboard).
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime
from typing import Any

import requests

from core import hf_connectors
from core.config import PROJECT_ROOT
from core.cost_control import provider_blocked, record_error, record_success
from core.failover import status_code_of
from core.video_providers.base import BaseVideoProvider, VideoTask, VideoTaskStatus

SPACE = os.getenv("HF_VIDEO_SPACE", "Lightricks/LTX-2.5")
VIDEO_DIR = PROJECT_ROOT / "static" / "generated" / "videos"


def _api_key() -> str:
    return hf_connectors.hf_token()


def _available() -> bool:
    return bool(_api_key()) and not provider_blocked("hf_video")


def _save_video(data: bytes, ext: str = "mp4") -> str:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    if ext not in ("mp4", "webm", "mov", "avi"):
        ext = "mp4"
    filename = f"{uuid.uuid4().hex}_{datetime.now():%Y%m%d%H%M%S}.{ext}"
    (VIDEO_DIR / filename).write_bytes(data)
    return f"/static/generated/videos/{filename}"


def _reference_bytes(reference_image: Any) -> bytes | None:
    """Accept a raw data URI, URL, base64 string, dict or bytes."""
    if not reference_image:
        return None
    if isinstance(reference_image, (bytes, bytearray)):
        return bytes(reference_image)
    if isinstance(reference_image, dict):
        for key in ("data", "url", "image", "image_data", "mimeType"):
            found = _reference_bytes(reference_image.get(key))
            if found:
                return found
        return None
    if isinstance(reference_image, str):
        raw = reference_image.strip()
        if not raw:
            return None
        if raw.startswith("data:"):
            return base64.b64decode(raw.partition(",")[2].strip())
        if raw.startswith(("http://", "https://")):
            response = requests.get(raw, timeout=30)
            if response.status_code == 200 and response.content:
                return response.content
            return None
        try:
            return base64.b64decode(raw, validate=True)
        except Exception:
            return None
    return None


def _call_ltx_space(
    prompt: str,
    reference_image: Any = None,
    timeout: int = 240,
) -> bytes:
    """Generate a clip on the official LTX-2.5 ZeroGPU Space."""
    if not _api_key():
        raise RuntimeError("Video generation token unavailable")
    image_bytes = _reference_bytes(reference_image)
    data = hf_connectors.space_media(
        SPACE,
        prompt=prompt,
        image_bytes=image_bytes,
        size=(576, 320),
        timeout=timeout,
    )
    if not data:
        raise RuntimeError("Video response contained no playable file")
    return data


class HuggingFaceVideoProvider(BaseVideoProvider):
    name = "HuggingFaceVideo"
    priority = 5
    health = "healthy"

    def submit(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        if not _available():
            task.status = VideoTaskStatus.FAILED
            task.error = "Video generation unavailable"
            return task

        try:
            data = _call_ltx_space(
                task.prompt,
                reference_image=kwargs.get("reference_image") or kwargs.get("image_data"),
                timeout=int(kwargs.get("timeout") or os.getenv("HF_VIDEO_SPACE_TIMEOUT", "240")),
            )
            if not data:
                raise RuntimeError("Video response was empty")
            task.video_url = _save_video(data)
            task.status = VideoTaskStatus.COMPLETED
            task.progress = 1.0
            task.metadata["_provider"] = self.name
            task.metadata["model"] = SPACE
            record_success("hf_video")
            print(f"[VIDEO] Completed with {SPACE}")
        except Exception as exc:
            record_error(
                "hf_video",
                status_code=status_code_of(exc),
                body=str(exc)[:300],
                exc=exc,
            )
            print(f"[VIDEO] LTX Space attempt failed: {exc}")
            task.status = VideoTaskStatus.FAILED
            task.error = str(exc)
        return task

    def poll(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        return task


def discover() -> list[BaseVideoProvider]:
    if not _available():
        return []
    # Do not consult get_config() here; a missing config key must not disable
    # the token-based video path.
    return [HuggingFaceVideoProvider()]
