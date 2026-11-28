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
# Image-to-video fallbacks expose their live image endpoint through view_api().
HF_VIDEO_SPACES = (
    SPACE,
    "Lightricks/ltx-video-distilled",
    "DeepRat/LTX-Video-ZeroGPU-Optimized",
)
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
) -> tuple[bytes, str]:
    """Generate a clip, preferring the image-to-video endpoint when a photo is attached.

    Every Space is inspected with view_api() by ``space_media``; a Space without
    an image/file parameter is skipped for image-to-video rather than silently
    producing an unrelated text-to-video storyboard.
    """
    if not _api_key():
        raise RuntimeError("Video generation token unavailable")
    image_bytes = _reference_bytes(reference_image)
    configured = str(os.getenv("HF_VIDEO_I2V_SPACE", "")).strip()
    spaces = (configured,) + HF_VIDEO_SPACES if configured else HF_VIDEO_SPACES
    last_error: Exception | None = None
    for space in dict.fromkeys(spaces):
        try:
            data = hf_connectors.space_media(
                space,
                prompt=prompt,
                image_bytes=image_bytes,
                size=(576, 320),
                timeout=timeout,
            )
            if data:
                print(f"[VIDEO] Space completed (image_to_video={bool(image_bytes)})")
                return data, space
        except Exception as exc:
            last_error = exc
            print(f"[VIDEO] Space attempt failed: {exc}")
            if hf_connectors.is_zero_gpu_quota_error(exc):
                break  # one shared quota; remaining Spaces cannot succeed
    raise last_error or RuntimeError("Video generation unavailable")


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
            data, space = _call_ltx_space(
                task.prompt,
                reference_image=(
                    kwargs.get("reference_image")
                    or kwargs.get("image_data")
                    or kwargs.get("image_ref")
                    or kwargs.get("image")
                ),
                timeout=int(kwargs.get("timeout") or os.getenv("HF_VIDEO_SPACE_TIMEOUT", "240")),
            )
            if not data:
                raise RuntimeError("Video response was empty")
            task.video_url = _save_video(data)
            task.status = VideoTaskStatus.COMPLETED
            task.progress = 1.0
            task.metadata["_provider"] = self.name
            task.metadata["model"] = space
            record_success("hf_video")
            print(f"[VIDEO] Completed with {space}")
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
