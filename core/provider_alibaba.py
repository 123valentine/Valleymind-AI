"""Alibaba Model Studio (DashScope) providers — internal, never user-facing.

Officially supported endpoints (Alibaba Cloud DashScope API):
  - /compatible-mode/v1/chat/completions   (OpenAI-compatible text)
  - /api/v1/services/aigc/text2image/image-synthesis  (image generation)
  - /compatible-mode/v1/embeddings          (OpenAI-compatible embeddings)
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

import requests

from core.config import PROJECT_ROOT, get_config
from core.provider_manager import (
    BaseProvider,
    Capability,
    ProviderResult,
)


ALIBABA_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ALIBABA_IMAGE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
ALIBABA_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks"
GENERATED_DIR = PROJECT_ROOT / "static" / "generated"
POLL_INTERVAL = 2.0
MAX_POLL_SECONDS = 60.0


def _api_key() -> str:
    return get_config().alibaba_api_key


def _available() -> bool:
    return bool(_api_key())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def _ensure_dir():
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)


def _save_image(data: bytes, ext: str = "png") -> str:
    _ensure_dir()
    if ext not in ("png", "jpeg", "jpg", "webp", "gif"):
        ext = "png"
    filename = f"{uuid.uuid4().hex}_{datetime.now():%Y%m%d%H%M%S}.{ext}"
    filepath = GENERATED_DIR / filename
    filepath.write_bytes(data)
    return f"/static/generated/{filename}"


# ── TEXT ────────────────────────────────────────────────────────

class AlibabaStudioTextProvider(BaseProvider):
    name = "AliStudio"
    capability = Capability.TEXT
    priority = 25
    health = "unknown"

    def execute(self, **kwargs: Any) -> ProviderResult:
        start = time.perf_counter()
        try:
            key = _api_key()
            if not key:
                raise RuntimeError("ALIBABA_MODEL_STUDIO_API_KEY not configured")

            messages = kwargs.get("messages", [])
            if not messages:
                raise RuntimeError("No messages provided for text generation")

            model = kwargs.get("model", "qwen-plus")
            resp = requests.post(
                f"{ALIBABA_BASE}/chat/completions",
                headers=_headers(),
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": kwargs.get("max_tokens", 1024),
                },
                timeout=kwargs.get("timeout", 30),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Alibaba Studio HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("Alibaba Studio returned empty choices")

            content = (choices[0].get("message") or {}).get("content", "")
            elapsed = (time.perf_counter() - start) * 1000
            return ProviderResult(
                success=True,
                data={"content": content.strip()},
                provider_name=self.name,
                latency_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return ProviderResult(
                success=False,
                error=str(exc),
                provider_name=self.name,
                latency_ms=elapsed,
            )


# ── IMAGE ───────────────────────────────────────────────────────

class AlibabaStudioImageProvider(BaseProvider):
    name = "AliStudio"
    capability = Capability.IMAGE
    priority = 25
    health = "unknown"

    def execute(self, **kwargs: Any) -> ProviderResult:
        start = time.perf_counter()
        try:
            key = _api_key()
            if not key:
                raise RuntimeError("ALIBABA_MODEL_STUDIO_API_KEY not configured")

            prompt = kwargs.get("prompt", "")
            if not prompt:
                raise RuntimeError("No prompt provided for image generation")

            resp = requests.post(
                ALIBABA_IMAGE_URL,
                headers=_headers(),
                json={
                    "model": "wanx2.1-t2i-turbo",
                    "input": {"prompt": prompt},
                    "parameters": {"size": "1024*1024", "n": 1},
                },
                timeout=kwargs.get("timeout", 60),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Alibaba Image HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()
            output = data.get("output") or {}
            task_id = output.get("task_id")

            if task_id:
                deadline = time.time() + MAX_POLL_SECONDS
                while time.time() < deadline:
                    poll = requests.get(
                        f"{ALIBABA_TASK_URL}/{task_id}",
                        headers=_headers(),
                        timeout=30,
                    )
                    if poll.status_code != 200:
                        raise RuntimeError(f"Alibaba task poll HTTP {poll.status_code}: {poll.text[:300]}")
                    poll_data = poll.json()
                    poll_output = poll_data.get("output") or {}
                    task_status = poll_output.get("task_status", "")
                    if task_status == "SUCCEEDED":
                        output = poll_output
                        break
                    if task_status == "FAILED":
                        raise RuntimeError(f"Alibaba image task failed: {poll_output.get('message', 'unknown')}")
                    time.sleep(POLL_INTERVAL)
                else:
                    raise RuntimeError("Alibaba image task timed out")
            else:
                task_status = output.get("task_status", "")
                if task_status == "FAILED":
                    raise RuntimeError(f"Alibaba image task failed: {output.get('message', 'unknown')}")

            results = output.get("results") or []
            if not results:
                img_url = output.get("image_url", "")
                if not img_url:
                    raise RuntimeError("Alibaba returned no image results")
                results = [{"image_url": img_url}]

            img_response = requests.get(results[0]["image_url"], timeout=30)
            img_response.raise_for_status()

            ext = img_response.headers.get("content-type", "image/png").split("/")[-1]
            saved = _save_image(img_response.content, ext)

            elapsed = (time.perf_counter() - start) * 1000
            return ProviderResult(
                success=True,
                data={
                    "image_url": saved,
                    "revised_prompt": prompt,
                    "text": "",
                },
                provider_name=self.name,
                latency_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return ProviderResult(
                success=False,
                error=str(exc),
                provider_name=self.name,
                latency_ms=elapsed,
            )


# ── EMBEDDINGS ──────────────────────────────────────────────────

class AlibabaStudioEmbeddingsProvider(BaseProvider):
    name = "AliStudio"
    capability = Capability.EMBEDDINGS
    priority = 25
    health = "unknown"

    def execute(self, **kwargs: Any) -> ProviderResult:
        start = time.perf_counter()
        try:
            key = _api_key()
            if not key:
                raise RuntimeError("ALIBABA_MODEL_STUDIO_API_KEY not configured")

            texts = kwargs.get("texts", kwargs.get("input", []))
            if isinstance(texts, str):
                texts = [texts]
            if not texts:
                raise RuntimeError("No input texts for embeddings")

            model = kwargs.get("model", "text-embedding-v3")
            resp = requests.post(
                f"{ALIBABA_BASE}/embeddings",
                headers=_headers(),
                json={"model": model, "input": texts},
                timeout=kwargs.get("timeout", 30),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Alibaba Embeddings HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()
            embedding_data = data.get("data") or []
            vectors = [item["embedding"] for item in embedding_data]

            elapsed = (time.perf_counter() - start) * 1000
            return ProviderResult(
                success=True,
                data={"embeddings": vectors},
                provider_name=self.name,
                latency_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return ProviderResult(
                success=False,
                error=str(exc),
                provider_name=self.name,
                latency_ms=elapsed,
            )


# ── Auto-discovery ──────────────────────────────────────────────

def discover() -> list[BaseProvider]:
    if not _available():
        return []
    return [
        AlibabaStudioTextProvider(),
        AlibabaStudioImageProvider(),
        AlibabaStudioEmbeddingsProvider(),
    ]
