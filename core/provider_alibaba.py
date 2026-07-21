"""Alibaba Model Studio (DashScope) providers — internal, never user-facing.

Officially supported endpoints (Alibaba Cloud DashScope API):
  - /compatible-mode/v1/chat/completions   (OpenAI-compatible text)
  - /api/v1/services/aigc/text2image/image-synthesis  (image generation)
  - /compatible-mode/v1/embeddings          (OpenAI-compatible embeddings)
"""

from __future__ import annotations

import os
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


# Singapore / International account — same base as the video providers.
_DASHSCOPE_BASE = os.getenv("DASHSCOPE_BASE", "https://dashscope-intl.aliyuncs.com").rstrip("/")
ALIBABA_BASE = f"{_DASHSCOPE_BASE}/compatible-mode/v1"
ALIBABA_IMAGE_URL = f"{_DASHSCOPE_BASE}/api/v1/services/aigc/text2image/image-synthesis"
ALIBABA_TASK_URL = f"{_DASHSCOPE_BASE}/api/v1/tasks"
# Verified working models on this account: qwen-image, qwen-image-plus.
# ("qwen-image-2.0" is rejected by the image-synthesis endpoint.)
QWEN_IMAGE_MODEL = os.getenv("QWEN_IMAGE_MODEL", "qwen-image").strip()
GENERATED_DIR = PROJECT_ROOT / "static" / "generated"
POLL_INTERVAL = 2.0
MAX_POLL_SECONDS = 60.0
QWEN_MAX_POLL_SECONDS = float(os.getenv("QWEN_IMAGE_MAX_POLL", "180"))


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

class QwenImageProvider(BaseProvider):
    """Higher-quality image generation via Qwen-Image on DashScope.

    Async submit -> poll -> returns the provider's OSS image URL. That URL is
    also what the Studio hands to image-to-video as the first frame, so the
    same still becomes the opening frame of its clip.

    Pollinations remains registered as the free-tier provider; which tier gets
    which is controlled by IMAGE_PROVIDER_FREE / IMAGE_PROVIDER_PAID.
    """

    name = "QwenImage"
    capability = Capability.IMAGE
    priority = 15          # between Pollinations (10) and Gemini stub (20)
    health = "healthy"

    def execute(self, **kwargs: Any) -> ProviderResult:
        start = time.perf_counter()
        try:
            key = _api_key()
            if not key:
                raise RuntimeError("ALIBABA_MODEL_STUDIO_API_KEY not configured")
            prompt = str(kwargs.get("prompt", "")).strip()
            if not prompt:
                raise RuntimeError("No prompt provided for image generation")

            model = kwargs.get("model") or QWEN_IMAGE_MODEL
            size = kwargs.get("size") or "1024*1024"

            resp = requests.post(
                ALIBABA_IMAGE_URL,
                headers={**_headers(), "X-DashScope-Async": "enable"},
                json={
                    "model": model,
                    "input": {"prompt": prompt[:1200]},
                    "parameters": {"size": size, "n": 1},
                },
                timeout=60,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Qwen image submit HTTP {resp.status_code}: {resp.text[:220]}")
            task_id = (resp.json().get("output") or {}).get("task_id", "")
            if not task_id:
                raise RuntimeError(f"Qwen image returned no task_id: {str(resp.json())[:200]}")

            deadline = time.time() + QWEN_MAX_POLL_SECONDS
            image_url = ""
            while time.time() < deadline:
                time.sleep(3.0)
                pr = requests.get(f"{ALIBABA_TASK_URL}/{task_id}", headers=_headers(), timeout=30)
                out = pr.json().get("output") or {}
                status = str(out.get("task_status", "")).upper()
                if status == "SUCCEEDED":
                    results = out.get("results") or []
                    if results and isinstance(results[0], dict):
                        image_url = results[0].get("url", "")
                    break
                if status in ("FAILED", "CANCELED", "UNKNOWN"):
                    raise RuntimeError(f"Qwen image task {status}: {str(out.get('message',''))[:180]}")

            if not image_url:
                raise RuntimeError("Qwen image timed out before returning a URL")

            latency = (time.perf_counter() - start) * 1000
            print(f"[IMAGE] Qwen image ready in {latency:.0f}ms ({model})")
            return ProviderResult(
                success=True,
                data={"image_url": image_url, "revised_prompt": prompt, "text": "",
                      "source_url": image_url},
                provider_name=self.name,
                latency_ms=latency,
            )
        except Exception as exc:
            latency = (time.perf_counter() - start) * 1000
            print(f"[IMAGE] Qwen image failed: {exc}")
            return ProviderResult(success=False, error=str(exc), provider_name=self.name, latency_ms=latency)


def discover() -> list[BaseProvider]:
    if not _available():
        return []
    return [
        AlibabaStudioTextProvider(),
        AlibabaStudioImageProvider(),
        AlibabaStudioEmbeddingsProvider(),
        QwenImageProvider(),
    ]
