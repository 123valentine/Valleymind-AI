"""Speech-to-text with word-level timestamps, via Groq whisper-large-v3.

Massive Editing needs to know exactly WHEN each word is spoken so it can trim
silences/fillers and time the animated captions. This module extracts a small
mono audio track from the uploaded video and sends it to Groq's OpenAI-compatible
``/audio/transcriptions`` endpoint (word timestamps), reusing the same Groq
credentials the chat cluster already uses and the bundled ffmpeg.
"""
from __future__ import annotations

import os

from core.config import get_config
from core.video_assembly import ffmpeg_exe, _run

WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
# Groq's transcription file cap is generous; mono 16kHz mp3 @64k is ~0.5MB/min,
# so a capped (<=60s) clip is tiny. Longer inputs should be chunked (later phase).
_MAX_AUDIO_MB = float(os.getenv("EDIT_MAX_AUDIO_MB", "24"))


def available() -> bool:
    return bool(get_config().groq_api_key)


def extract_audio(src_path: str, out_path: str, timeout: int = 180) -> tuple[bool, str]:
    """Pull a small mono 16kHz mp3 from a video (or re-encode an audio file).
    Small + mono is all Whisper needs and keeps the upload well under the cap."""
    exe = ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    cmd = [exe, "-y", "-i", src_path, "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "libmp3lame", "-b:a", "64k", out_path]
    ok, err = _run(cmd, timeout)
    return (ok and os.path.exists(out_path) and os.path.getsize(out_path) > 0), err


def _to_words(data: dict) -> list:
    """Normalize a Groq verbose_json response to [{word,start,end}].

    Prefers real word timestamps; if a model/format returns only segments, split
    each segment's text evenly across its span so downstream logic still works.
    """
    words = data.get("words")
    out: list = []
    if isinstance(words, list) and words:
        for w in words:
            try:
                out.append({"word": str(w.get("word", "")).strip(),
                            "start": float(w.get("start")), "end": float(w.get("end"))})
            except (TypeError, ValueError):
                continue
        if out:
            return out
    # Fallback: derive approximate word times from segments.
    for seg in (data.get("segments") or []):
        try:
            ss, se = float(seg.get("start")), float(seg.get("end"))
        except (TypeError, ValueError):
            continue
        toks = str(seg.get("text", "")).split()
        if not toks:
            continue
        step = (se - ss) / len(toks)
        for i, tok in enumerate(toks):
            out.append({"word": tok, "start": round(ss + i * step, 3),
                        "end": round(ss + (i + 1) * step, 3)})
    return out


def transcribe(src_path: str, *, language: str | None = None,
               already_audio: bool = False, timeout: int = 180) -> dict:
    """Transcribe a video/audio file to word-level timestamps.

    Returns {"words":[{word,start,end}], "text": str, "duration": float}
    or {"error": str}. Never raises.
    """
    import tempfile
    import requests

    cfg = get_config()
    if not cfg.groq_api_key:
        return {"error": "transcription not configured (no GROQ_API_KEY)"}

    audio_path, tmp_made = src_path, False
    if not already_audio:
        fd, audio_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        tmp_made = True
        ok, err = extract_audio(src_path, audio_path)
        if not ok:
            _rm(audio_path if tmp_made else None)
            return {"error": f"audio extraction failed: {err[:160]}"}

    try:
        size_mb = os.path.getsize(audio_path) / 1024 / 1024
        if size_mb > _MAX_AUDIO_MB:
            return {"error": f"audio is {size_mb:.0f}MB (cap {_MAX_AUDIO_MB:.0f}MB) — clip is too long"}
        base = cfg.groq_base_url.rstrip("/")
        url = f"{base}/openai/v1/audio/transcriptions"
        data = {"model": WHISPER_MODEL, "response_format": "verbose_json",
                "timestamp_granularities[]": "word"}
        if language:
            data["language"] = language
        with open(audio_path, "rb") as fh:
            files = {"file": (os.path.basename(audio_path), fh, "audio/mpeg")}
            resp = requests.post(url, headers={"Authorization": f"Bearer {cfg.groq_api_key}"},
                                 data=data, files=files, timeout=timeout)
        if resp.status_code != 200:
            return {"error": f"Groq transcription HTTP {resp.status_code}: {resp.text[:200]}"}
        j = resp.json()
        words = _to_words(j)
        if not words:
            return {"error": "transcription returned no words"}
        return {"words": words, "text": str(j.get("text", "")).strip(),
                "duration": float(j.get("duration") or (words[-1]["end"] if words else 0.0))}
    except Exception as exc:
        return {"error": f"transcription failed: {exc}"}
    finally:
        if tmp_made:
            _rm(audio_path)


def _rm(path):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass
