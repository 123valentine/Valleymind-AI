"""Image-to-video (Wan i2v) — turns a storyboard still into a moving clip.

Verified against DashScope-intl. The request shape is fussy and was determined
empirically:

    POST /api/v1/services/aigc/video-generation/video-synthesis
    header X-DashScope-Async: enable          (endpoint is async-only)
    {"model": "wan2.7-i2v-2026-04-25",
     "input": {"prompt": "...",
               "media": [{"type": "first_frame", "url": "<url or data URI>"}]}}

``media`` must be a LIST of MediaItem objects and ``type`` must be one of
first_frame / last_frame / driving_audio / first_clip — "image" is rejected.

Both a public URL and a base64 data URI are accepted. Qwen storyboards come
back as an Alibaba OSS URL (handed straight through), while Pollinations
images are local files that Alibaba cannot fetch, so those are inlined as
base64 instead.

Using the storyboard frame as the FIRST FRAME is what keeps the character
looking like the same person from scene to scene.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import time
from pathlib import Path

import requests

from core.config import PROJECT_ROOT, get_config

_DASHSCOPE_BASE = os.getenv("DASHSCOPE_BASE", "https://dashscope-intl.aliyuncs.com").rstrip("/")
I2V_URL = f"{_DASHSCOPE_BASE}/api/v1/services/aigc/video-generation/video-synthesis"
TASK_URL = f"{_DASHSCOPE_BASE}/api/v1/tasks"
I2V_MODEL = os.getenv("ALIBABA_I2V_MODEL", "wan2.7-i2v-2026-04-25").strip()
MAX_POLL_SECONDS = float(os.getenv("I2V_MAX_POLL_SECONDS", "420"))
POLL_INTERVAL = 8.0


def _headers(async_mode: bool = False) -> dict:
    h = {"Authorization": f"Bearer {get_config().alibaba_api_key}", "Content-Type": "application/json"}
    if async_mode:
        h["X-DashScope-Async"] = "enable"
    return h


def available() -> bool:
    return bool(get_config().alibaba_api_key)


def _to_media_url(image_ref: str) -> str:
    """Return something DashScope can actually load.

    Remote http(s) URLs pass through. Anything local (/static/... or a path) is
    read off disk and inlined as a base64 data URI, since Alibaba cannot reach
    this app — which is also what makes local testing possible.
    """
    ref = str(image_ref or "").strip()
    if not ref:
        raise RuntimeError("no image reference for image-to-video")
    if ref.startswith("http://") or ref.startswith("https://"):
        return ref

    path = Path(ref) if os.path.isabs(ref) else (PROJECT_ROOT / ref.lstrip("/"))
    if not path.exists():
        raise RuntimeError(f"storyboard image not found on disk: {ref}")
    data = path.read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def generate_clip(prompt: str, image_ref: str, timeout_seconds: float | None = None) -> dict:
    """Generate one clip from a still. Returns {"video_url": str} or {"error": str}.

    Never raises — the Studio keeps going and shows the still if a clip fails.
    """
    if not available():
        return {"error": "image-to-video is not configured"}
    try:
        media_url = _to_media_url(image_ref)
    except Exception as exc:
        return {"error": str(exc)}

    try:
        resp = requests.post(
            I2V_URL,
            headers=_headers(async_mode=True),
            json={
                "model": I2V_MODEL,
                "input": {
                    "prompt": (prompt or "gentle cinematic motion")[:800],
                    "media": [{"type": "first_frame", "url": media_url}],
                },
                "parameters": {},
            },
            timeout=180,
        )
        if resp.status_code != 200:
            return {"error": f"i2v submit HTTP {resp.status_code}: {resp.text[:200]}"}
        task_id = (resp.json().get("output") or {}).get("task_id", "")
        if not task_id:
            return {"error": f"i2v returned no task_id: {str(resp.json())[:180]}"}
    except Exception as exc:
        return {"error": f"i2v submit failed: {exc}"}

    deadline = time.time() + (timeout_seconds or MAX_POLL_SECONDS)
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            out = requests.get(f"{TASK_URL}/{task_id}", headers=_headers(), timeout=30).json().get("output") or {}
        except Exception as exc:
            print(f"[I2V] poll error (retrying): {exc}")
            continue
        status = str(out.get("task_status", "")).upper()
        if status == "SUCCEEDED":
            url = out.get("video_url") or ""
            if not url:
                results = out.get("results") or []
                if results and isinstance(results[0], dict):
                    url = results[0].get("url", "")
            return {"video_url": url} if url else {"error": "i2v succeeded but returned no URL"}
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            return {"error": f"i2v {status}: {str(out.get('message',''))[:180]}"}

    return {"error": "i2v timed out"}
