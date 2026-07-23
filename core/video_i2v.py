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


def _fake_mode() -> bool:
    """When on, no real Alibaba calls are made — clips are tiny locally-rendered
    mp4s. Lets the whole async pipeline (submit/poll/collect/assemble) be tested
    end to end for $0. Enable with STUDIO_FAKE_I2V=1."""
    return os.getenv("STUDIO_FAKE_I2V", "").strip().lower() in ("1", "true", "yes", "on")


def _fake_fail_scenes() -> set:
    """Comma-separated scene numbers to force-fail in fake mode, so partial
    failure handling can be exercised (e.g. STUDIO_FAKE_FAIL=3,7)."""
    out = set()
    for tok in (os.getenv("STUDIO_FAKE_FAIL", "") or "").replace(" ", "").split(","):
        if tok.isdigit():
            out.add(int(tok))
    return out


def _fake_clip_file(tag: str) -> str:
    """Render a ~1s solid-colour clip with the bundled ffmpeg so fake clips are
    real, playable, concat-able mp4s. Returns an absolute path or ""."""
    try:
        import subprocess, tempfile
        from core.video_assembly import ffmpeg_exe
        exe = ffmpeg_exe()
        if not exe:
            return ""
        colour = ["red", "green", "blue", "orange", "purple", "teal", "maroon", "navy"][hash(tag) % 8]
        out = os.path.join(tempfile.gettempdir(), f"fake_clip_{tag}.mp4")
        subprocess.run(
            [exe, "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=320x240:d=1",
             "-pix_fmt", "yuv420p", out],
            capture_output=True, timeout=60,
        )
        return out if os.path.exists(out) else ""
    except Exception as exc:
        print(f"[I2V:FAKE] could not render fake clip: {exc}")
        return ""


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


def _clip_parameters(duration=None) -> dict:
    """Build the ``parameters`` block. Wan i2v accepts an optional integer
    ``duration`` (seconds); shorter clips cost proportionally less. Set per call
    or globally via VIDEO_CLIP_DURATION. Omitted entirely when unset so the
    model uses its own default."""
    raw = duration if duration is not None else os.getenv("VIDEO_CLIP_DURATION", "").strip()
    try:
        if raw not in (None, ""):
            return {"duration": int(raw)}
    except (TypeError, ValueError):
        pass
    return {}


def submit_clip(prompt: str, image_ref: str, duration=None, tag: str = "", fake: bool = False) -> dict:
    """Submit ONE image-to-video job and return immediately — never blocks on
    generation. Returns {"task_id": str} on success, or
    {"error": str, "status_code": int} on failure (status_code 429 == rate
    limited, so callers can back off). ``fake=True`` (per-run test mode) forces a
    free local clip even when the global fake switch is off."""
    if fake or _fake_mode():
        return {"task_id": f"fake:{tag or abs(hash((prompt, image_ref))) % 100000}"}
    if not available():
        return {"error": "image-to-video is not configured", "status_code": 0}
    try:
        media_url = _to_media_url(image_ref)
    except Exception as exc:
        return {"error": str(exc), "status_code": 0}
    try:
        resp = requests.post(
            I2V_URL, headers=_headers(async_mode=True),
            json={
                "model": I2V_MODEL,
                "input": {
                    "prompt": (prompt or "gentle cinematic motion")[:800],
                    "media": [{"type": "first_frame", "url": media_url}],
                },
                "parameters": _clip_parameters(duration),
            },
            timeout=180,
        )
    except Exception as exc:
        return {"error": f"i2v submit failed: {exc}", "status_code": 0}
    if resp.status_code == 429:
        return {"error": "rate limited (429)", "status_code": 429}
    if resp.status_code != 200:
        return {"error": f"i2v submit HTTP {resp.status_code}: {resp.text[:200]}", "status_code": resp.status_code}
    task_id = (resp.json().get("output") or {}).get("task_id", "")
    if not task_id:
        return {"error": f"i2v returned no task_id: {str(resp.json())[:180]}", "status_code": 200}
    return {"task_id": task_id}


T2V_MODEL = os.getenv("ALIBABA_VIDEO_MODEL", "wan2.7-t2v-2026-06-12").strip()


def submit_clip_t2v(prompt: str, duration=None, tag: str = "", fake: bool = False) -> dict:
    """Submit a TEXT-to-video job — the Studio default. No storyboard image is
    needed, which removes one paid image call per clip and (in testing) follows
    the prompt better than the i2v chain. Same async task API as i2v, so
    poll_clip() handles both."""
    if fake or _fake_mode():
        return {"task_id": f"fake:{tag or abs(hash(prompt)) % 100000}"}
    if not available():
        return {"error": "video generation is not configured", "status_code": 0}
    try:
        resp = requests.post(
            I2V_URL, headers=_headers(async_mode=True),
            json={
                "model": T2V_MODEL,
                "input": {"prompt": (prompt or "cinematic shot")[:1600]},
                "parameters": _clip_parameters(duration),
            },
            timeout=180,
        )
    except Exception as exc:
        return {"error": f"t2v submit failed: {exc}", "status_code": 0}
    if resp.status_code == 429:
        return {"error": "rate limited (429)", "status_code": 429}
    if resp.status_code != 200:
        return {"error": f"t2v submit HTTP {resp.status_code}: {resp.text[:200]}", "status_code": resp.status_code}
    task_id = (resp.json().get("output") or {}).get("task_id", "")
    if not task_id:
        return {"error": f"t2v returned no task_id: {str(resp.json())[:180]}", "status_code": 200}
    return {"task_id": task_id}


def poll_clip(task_id: str) -> dict:
    """One non-blocking status check for a submitted task. Returns
    {"status": "RUNNING"|"SUCCEEDED"|"FAILED", "video_url"?: str, "error"?: str}.
    Transient network errors report RUNNING so the driver keeps waiting rather
    than discarding a clip that is still cooking."""
    tid = str(task_id or "")
    if tid.startswith("fake:"):
        tag = tid.split(":", 1)[1]
        if tag.isdigit() and int(tag) in _fake_fail_scenes():
            return {"status": "FAILED", "error": "forced fake failure"}
        path = _fake_clip_file(tag or "0")
        return {"status": "SUCCEEDED", "video_url": path} if path else {"status": "FAILED", "error": "fake render failed"}
    if not tid:
        return {"status": "FAILED", "error": "no task id"}
    try:
        out = requests.get(f"{TASK_URL}/{tid}", headers=_headers(), timeout=30).json().get("output") or {}
    except Exception as exc:
        print(f"[I2V] poll error (will retry): {exc}")
        return {"status": "RUNNING", "error": f"poll error: {exc}"}
    status = str(out.get("task_status", "")).upper()
    if status == "SUCCEEDED":
        url = out.get("video_url") or ""
        if not url:
            results = out.get("results") or []
            if results and isinstance(results[0], dict):
                url = results[0].get("url", "")
        return {"status": "SUCCEEDED", "video_url": url} if url else {"status": "FAILED", "error": "succeeded but no URL"}
    if status in ("FAILED", "CANCELED", "UNKNOWN"):
        return {"status": "FAILED", "error": str(out.get("message", ""))[:180]}
    return {"status": "RUNNING"}


def generate_clip(prompt: str, image_ref: str, timeout_seconds: float | None = None,
                  duration=None, tag: str = "") -> dict:
    """Blocking generate — submit then poll until done. Kept as the synchronous
    fallback path. Returns {"video_url": str} or {"error": str}. Never raises.
    """
    sub = submit_clip(prompt, image_ref, duration=duration, tag=tag)
    if sub.get("error"):
        return {"error": sub["error"]}
    task_id = sub["task_id"]
    deadline = time.time() + (timeout_seconds or MAX_POLL_SECONDS)
    while time.time() < deadline:
        r = poll_clip(task_id)
        if r["status"] == "SUCCEEDED":
            return {"video_url": r.get("video_url", "")}
        if r["status"] == "FAILED":
            return {"error": r.get("error", "i2v failed")}
        time.sleep(POLL_INTERVAL)
    return {"error": "i2v timed out"}
