"""Qwen3-VL video understanding — how the crew actually watches its own work.

Marcus reviews each generated clip against the scene he asked for, and Elena
watches the finished cut start to finish. Both run through the same DashScope
multimodal endpoint on the existing Alibaba key.

Verified against dashscope-intl (see the probe results in the Part 7 commit):
  * model ``qwen3-vl-plus`` exists on the international endpoint;
    ``qwen3-vl-max`` does not.
  * Alibaba could NOT fetch an arbitrary external URL ("Failed to download
    multimodal content"), but an inline base64 ``data:video/mp4;base64,...``
    works reliably — so clips are always inlined.
  * Video tokens scale with resolution x frames (~594 tokens per second of
    720p24). Every clip is therefore transcoded to a small analysis proxy
    first, which cuts both payload size and token cost by roughly an order of
    magnitude without changing what the model can see.
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile

import requests

from core.config import get_config

_BASE = os.getenv("DASHSCOPE_BASE", "https://dashscope-intl.aliyuncs.com").rstrip("/")
VL_URL = f"{_BASE}/api/v1/services/aigc/multimodal-generation/generation"
VL_MODEL = os.getenv("QWEN_VL_MODEL", "qwen3-vl-plus").strip()

# Analysis proxy settings — small enough to keep payload and tokens low.
PROXY_HEIGHT = int(os.getenv("VL_PROXY_HEIGHT", "360"))
PROXY_FPS = int(os.getenv("VL_PROXY_FPS", "4"))
MAX_ANALYSIS_SECONDS = int(os.getenv("VL_MAX_SECONDS", "120"))
MAX_PAYLOAD_MB = float(os.getenv("VL_MAX_PAYLOAD_MB", "8"))


def available() -> bool:
    return bool(get_config().alibaba_api_key) and vision_enabled()


def vision_enabled() -> bool:
    return os.getenv("STUDIO_VISION_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_config().alibaba_api_key}",
            "Content-Type": "application/json"}


def make_proxy(src_path: str, seconds: int | None = None) -> str:
    """Downscale/short-sample a clip for analysis. Returns a temp path, or the
    original if ffmpeg is unavailable."""
    try:
        from core.video_assembly import ffmpeg_exe
        exe = ffmpeg_exe()
        if not exe:
            return src_path
        out = os.path.join(tempfile.gettempdir(), f"vlproxy_{os.path.basename(src_path)}")
        cmd = [exe, "-y", "-i", src_path,
               "-t", str(seconds or MAX_ANALYSIS_SECONDS),
               "-vf", f"scale=-2:{PROXY_HEIGHT},fps={PROXY_FPS}",
               "-an", "-c:v", "libx264", "-crf", "32", "-pix_fmt", "yuv420p", out]
        p = subprocess.run(cmd, capture_output=True, timeout=300)
        return out if (p.returncode == 0 and os.path.exists(out)) else src_path
    except Exception as exc:
        print(f"[VL] proxy failed, using original: {exc}")
        return src_path


def _to_data_uri(path: str) -> tuple[str, int]:
    data = open(path, "rb").read()
    return "data:video/mp4;base64," + base64.b64encode(data).decode(), len(data)


def analyze_video(video_path: str, prompt: str, *, system: str = "",
                  seconds: int | None = None, timeout: int = 300) -> dict:
    """Watch a video and answer. Returns {"text", "tokens"} or {"error"}.

    Never raises — a failed review must not take down a render.
    """
    if not available():
        return {"error": "video analysis is not configured"}
    if not os.path.exists(video_path):
        return {"error": "video not found"}

    proxy = make_proxy(video_path, seconds)
    try:
        uri, raw_bytes = _to_data_uri(proxy)
        if len(uri) / 1024 / 1024 > MAX_PAYLOAD_MB:
            return {"error": f"clip too large for analysis ({raw_bytes/1024/1024:.1f}MB)"}
        messages = []
        if system:
            messages.append({"role": "system", "content": [{"text": system}]})
        messages.append({"role": "user", "content": [{"video": uri}, {"text": prompt}]})
        resp = requests.post(VL_URL, headers=_headers(),
                             json={"model": VL_MODEL, "input": {"messages": messages}},
                             timeout=timeout)
        if resp.status_code != 200:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return {"error": f"VL HTTP {resp.status_code}: {str(body.get('message', ''))[:180]}"}
        data = resp.json()
        content = ((data.get("output") or {}).get("choices") or [{}])[0].get("message", {}).get("content")
        text = ""
        if isinstance(content, list):
            text = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict)).strip()
        elif isinstance(content, str):
            text = content.strip()
        usage = data.get("usage") or {}
        return {"text": text, "tokens": int(usage.get("total_tokens", 0) or 0)}
    except Exception as exc:
        return {"error": f"VL call failed: {exc}"}
    finally:
        if proxy != video_path:
            try:
                os.remove(proxy)
            except OSError:
                pass


# ── The crew's review prompts, in their own voices ──────────────────────────

def marcus_clip_review(video_path: str, scene: dict) -> dict:
    """Marcus checks a rendered clip against the shot he asked for."""
    asked = "; ".join(str(scene.get(k, "")) for k in ("action", "description", "camera") if scene.get(k))
    system = (
        "You are Marcus, the director. You are reviewing a clip your team just "
        "rendered against the shot you asked for. You are direct, specific and "
        "brief — a working director at a monitor, not a critic. Two or three "
        "sentences maximum. Speak in first person."
    )
    prompt = (
        f"The shot I asked for was: {asked}\n\n"
        "Watch the clip. Tell me (1) what is ACTUALLY on screen, and (2) whether it "
        "matches what I asked for.\n"
        "End your reply with a final line exactly of the form:\n"
        "MATCH: yes    (or)    MATCH: no\n"
        "Say no if the subject, the action, or the setting is clearly wrong."
    )
    out = analyze_video(video_path, prompt, system=system, seconds=20)
    if out.get("error"):
        return out
    text = out.get("text", "")
    verdict = "unknown"
    for line in reversed(text.splitlines()):
        low = line.strip().lower()
        if low.startswith("match:"):
            verdict = "yes" if "yes" in low else ("no" if "no" in low else "unknown")
            text = text.replace(line, "").strip()
            break
    return {"text": text, "match": verdict, "tokens": out.get("tokens", 0)}


def elena_cut_review(video_path: str, logline: str = "", beats: list | None = None) -> dict:
    """Elena watches the finished cut start to finish."""
    beat_list = ""
    if beats:
        beat_list = "\n".join(f"{b.get('number')}. {b.get('card') or b.get('title')}" for b in beats)
    system = (
        "You are Elena, the editor. You have just watched the finished cut all the "
        "way through. You talk like someone in an edit review: warm but honest, "
        "concrete about timing and what you actually saw. No bullet lists, no "
        "headings — just how it plays. Under 150 words."
    )
    prompt = (
        (f"The piece is meant to be: {logline}\n" if logline else "")
        + (f"The intended beats were:\n{beat_list}\n\n" if beat_list else "")
        + "Watch the whole cut and tell me: does it read as a story start to finish? "
        "Where does it drag or repeat? What's missing or unclear? Be specific about "
        "which part you mean."
    )
    return analyze_video(video_path, prompt, system=system, seconds=MAX_ANALYSIS_SECONDS)
