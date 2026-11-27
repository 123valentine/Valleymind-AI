"""Hugging Face text-to-video provider with automatic in-provider model fallback."""

from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime
from typing import Any

import requests

from core.config import PROJECT_ROOT
from core.cost_control import provider_blocked, record_error, record_success
from core.failover import status_code_of
from core.video_providers.base import BaseVideoProvider, VideoTask, VideoTaskStatus

VIDEO_BASE = os.getenv(
    "HF_VIDEO_BASE",
    "https://router.huggingface.co/hf-inference/models",
).rstrip("/")
VIDEO_DIR = PROJECT_ROOT / "static" / "generated" / "videos"

DEFAULT_MODELS = (
    "Wan-AI/Wan2.1-T2V-1.3B",
    "Lightricks/LTX-Video",
    "Lightricks/LTX-2.5",
)


def _api_key() -> str:
    return (os.getenv("HF_TOKEN") or "").strip()


def _available() -> bool:
    return bool(_api_key()) and not provider_blocked("hf_video")


def _models() -> list[str]:
    configured = (os.getenv("HF_VIDEO_MODEL") or "").strip()
    models = [configured] if configured else list(DEFAULT_MODELS)
    for model in DEFAULT_MODELS:
        if model not in models:
            models.append(model)
    return models


def _save_video(data: bytes, ext: str = "mp4") -> str:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    if ext not in ("mp4", "webm", "mov", "avi"):
        ext = "mp4"
    filename = f"{uuid.uuid4().hex}_{datetime.now():%Y%m%d%H%M%S}.{ext}"
    (VIDEO_DIR / filename).write_bytes(data)
    return f"/static/generated/videos/{filename}"


def _extract_video(response: requests.Response) -> bytes:
    content_type = response.headers.get("content-type", "").lower()
    if "video/" in content_type and response.content:
        return response.content

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError("Video response could not be read") from exc

    candidates = []
    if isinstance(data, dict):
        for key in ("video", "video_url", "url", "output"):
            if data.get(key):
                candidates.append(data[key])
        for result in data.get("results") or []:
            if isinstance(result, dict):
                for key in ("video", "video_url", "url"):
                    if result.get(key):
                        candidates.append(result[key])

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            downloaded = requests.get(candidate, timeout=120)
            if downloaded.status_code == 200 and downloaded.content:
                return downloaded.content
        if isinstance(candidate, str) and len(candidate) > 128:
            try:
                return base64.b64decode(candidate)
            except Exception:
                pass
    raise RuntimeError("Video response contained no playable file")


def _call_router(prompt: str, model: str, timeout: int = 180) -> bytes:
    from urllib.parse import quote

    response = requests.post(
        f"{VIDEO_BASE}/{quote(model, safe='/')}",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json={
            "inputs": prompt,
            "parameters": {"num_frames": 81, "num_inference_steps": 20},
        },
        timeout=timeout,
    )
    error = getattr(response, "text", "")[:300]
    if response.status_code not in (200, 201):
        failure = RuntimeError(f"Video HTTP {response.status_code}: {error}")
        setattr(failure, "status_code", response.status_code)
        setattr(failure, "response", response)
        raise failure
    return _extract_video(response)


def _call_zero_gpu_space(prompt: str, timeout: int = 240) -> bytes | None:
    """Optional LTX ZeroGPU space fallback when serverless credits are gone."""
    space = (os.getenv("HF_VIDEO_ZERO_GPU_SPACE") or "").strip()
    if not space:
        return None
    try:
        from gradio_client import Client

        client = Client(space)
        result = client.predict(prompt, api_name="/generate_video")
        candidate = result[0] if isinstance(result, (list, tuple)) and result else result
        if isinstance(candidate, str) and os.path.exists(candidate):
            with open(candidate, "rb") as handle:
                return handle.read()
    except Exception as exc:
        print(f"[VIDEO] ZeroGPU fallback skipped: {exc}")
    return None


class HuggingFaceVideoProvider(BaseVideoProvider):
    name = "HuggingFaceVideo"
    priority = 5
    health = "healthy"

    def submit(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        if not _available():
            task.status = VideoTaskStatus.FAILED
            task.error = "Video generation unavailable"
            return task

        last_error: Exception | None = None
        for model in _models():
            try:
                data = _call_router(task.prompt, model, timeout=kwargs.get("timeout", 180))
                if not data:
                    raise RuntimeError("Video response was empty")
                url = _save_video(data)
                task.status = VideoTaskStatus.COMPLETED
                task.progress = 1.0
                task.video_url = url
                task.metadata["_provider"] = self.name
                task.metadata["model"] = model
                record_success("hf_video")
                print(f"[VIDEO] Completed with model={model}")
                return task
            except Exception as exc:
                last_error = exc
                response = getattr(exc, "response", None)
                record_error(
                    "hf_video",
                    status_code=status_code_of(exc),
                    body=str(getattr(response, "text", ""))[:300],
                    exc=exc,
                )
                print(f"[VIDEO] Model attempt failed: {exc}")

        zero_gpu = _call_zero_gpu_space(task.prompt)
        if zero_gpu:
            url = _save_video(zero_gpu)
            task.status = VideoTaskStatus.COMPLETED
            task.progress = 1.0
            task.video_url = url
            task.metadata["_provider"] = self.name
            task.metadata["model"] = "LTX-ZeroGPU"
            record_success("hf_video")
            return task

        task.status = VideoTaskStatus.FAILED
        task.error = str(last_error) if last_error else "Video generation failed"
        return task

    def poll(self, task: VideoTask, **kwargs: Any) -> VideoTask:
        return task


def discover() -> list[BaseVideoProvider]:
    if not _available():
        return []
    # Do not consult get_config() here; a missing config key must not disable
    # the token-based video path.
    return [HuggingFaceVideoProvider()]
