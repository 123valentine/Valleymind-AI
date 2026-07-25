import asyncio
import hashlib
import os
import re
import time
from pathlib import Path

from core.config import PROJECT_ROOT
from core import r2_storage


TTS_FOLDER = PROJECT_ROOT / "memory_data" / "tts"


def _safe_filename() -> str:
    return f"marcus_{int(time.time() * 1000)}.mp3"


def _clean_text(text: str) -> str:
    text = re.sub(r"`{1,3}.*?`{1,3}", " ", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


DEFAULT_VOICE = "en-US-GuyNeural"


async def _edge_to_file(text: str, output_path: Path, voice: str = DEFAULT_VOICE):
    import edge_tts

    communicate = edge_tts.Communicate(text, voice or DEFAULT_VOICE)
    await communicate.save(str(output_path))


def speak_marcus(text: str, voice: str = DEFAULT_VOICE) -> dict:
    """
    Optional Marcus-only local TTS.

    Generate Marcus speech through the existing local TTS hook.

    Edge TTS is preferred because it produces a browser-playable audio file.
    If it is unavailable or fails, the frontend can fall back to Chrome/browser
    speech synthesis using the returned reason.
    """
    text = _clean_text(text)
    if not text:
        return {"enabled": False, "spoken": False, "reason": "empty text"}

    try:
        TTS_FOLDER.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename()
        output_path = TTS_FOLDER / filename
        asyncio.run(asyncio.wait_for(_edge_to_file(text[:1600], output_path, voice), timeout=15))
        if output_path.exists() and output_path.stat().st_size > 0:
            print(f"[TTS] Edge TTS generated: {filename}")
            return {
                "enabled": True,
                "spoken": False,
                "engine": "edge_tts",
                "url": f"/tts/{filename}",
            }
        raise RuntimeError("Edge TTS did not create an audio file")
    except Exception as exc:
        print(f"[WARNING] Edge TTS failed, browser speech fallback required: {exc}")

    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
        print("[TTS] pyttsx3 spoke locally")
        return {"enabled": True, "spoken": True, "engine": "pyttsx3"}
    except ImportError:
        return {
            "enabled": True,
            "spoken": False,
            "engine": "browser",
            "reason": "edge_tts failed and pyttsx3 is not installed",
        }
    except Exception as exc:
        print(f"[WARNING] Marcus local TTS failed, browser speech fallback required: {exc}")
        return {"enabled": True, "spoken": False, "engine": "browser", "reason": str(exc)}


# ── Persona TTS via Qwen (qwen3-tts-flash) ─────────────────────────────────
# Consistent, server-generated voices so a persona sounds identical on every
# device. Browser speechSynthesis picks the device's default voice, which is why
# Marcus sounded female on mobile and male on desktop. Audio is cached in R2 by a
# hash of model+voice+text, so the same line is never synthesised — or paid for —
# twice. Spend is both gated and recorded against the shared studio budget cap.

PERSONA_VOICES = {
    "marcus":   os.getenv("TTS_VOICE_MARCUS", "Elias"),
    "elena":    os.getenv("TTS_VOICE_ELENA", "Chelsie"),
    "angelina": os.getenv("TTS_VOICE_ANGELINA", "Katerina"),
}
QWEN_TTS_MODEL = os.getenv("QWEN_TTS_MODEL", "qwen3-tts-flash")
_DASHSCOPE_BASE = os.getenv("DASHSCOPE_BASE", "https://dashscope-intl.aliyuncs.com").rstrip("/")
QWEN_TTS_URL = f"{_DASHSCOPE_BASE}/api/v1/services/aigc/multimodal-generation/generation"
# qwen3-tts-flash returns 24kHz WAV (verified against the API: RIFF, audio/x-wav).
_TTS_EXT = "wav"
_TTS_CT = "audio/wav"
# Flash tier list price: $27.59 per 1,000,000 characters. Override via env if the
# account's billed rate differs.
TTS_PRICE_PER_CHAR = float(os.getenv("QWEN_TTS_PRICE_PER_CHAR", "0.00002759"))
TTS_MAX_CHARS = int(os.getenv("QWEN_TTS_MAX_CHARS", "1200"))
_R2_TTS_PREFIX = "assets/tts"


def voice_for_persona(persona: str) -> str:
    return PERSONA_VOICES.get((persona or "").strip().lower(), PERSONA_VOICES["marcus"])


def qwen_tts_available() -> bool:
    """Qwen TTS needs the Alibaba key AND R2 (the audio cache lives there)."""
    from core.config import get_config
    return bool(get_config().alibaba_api_key) and r2_storage.available()


def _cache_name(voice: str, text: str) -> str:
    h = hashlib.sha256(f"{QWEN_TTS_MODEL}|{voice}|{text}".encode("utf-8")).hexdigest()
    return f"{h}.{_TTS_EXT}"


def _serve_url(name: str) -> str:
    return f"/tts/{name}"


def _browser_fallback(reason: str, voice: str = "") -> dict:
    return {"enabled": True, "engine": "browser", "url": "", "reason": reason, "voice": voice}


def synthesize_persona(text: str, persona: str = "marcus") -> dict:
    """Speak *text* in *persona*'s fixed Qwen voice.

    Returns a dict the frontend plays:
      success  -> {engine:'qwen_tts', url, voice, cached, characters, cost}
      fallback -> {engine:'browser', reason}   (device speechSynthesis)

    Caches audio in R2 (repeat lines are free) and both gates and records spend
    against the shared studio budget cap.
    """
    voice = voice_for_persona(persona)
    text = _clean_text(text)
    if not text:
        return {"enabled": False, "engine": "none", "reason": "empty text", "voice": voice}
    if not qwen_tts_available():
        return _browser_fallback("qwen tts not configured", voice)

    text = text[:TTS_MAX_CHARS]
    name = _cache_name(voice, text)
    r2_key = f"{_R2_TTS_PREFIX}/{name}"
    local_path = TTS_FOLDER / name

    # 1. Cache hit — no API call, no cost.
    if local_path.exists() and local_path.stat().st_size > 0:
        return {"enabled": True, "engine": "qwen_tts", "url": _serve_url(name),
                "voice": voice, "cached": True, "characters": 0, "cost": 0.0}
    try:
        if r2_storage.object_exists(r2_key):
            return {"enabled": True, "engine": "qwen_tts", "url": _serve_url(name),
                    "voice": voice, "cached": True, "characters": 0, "cost": 0.0}
    except Exception:
        pass

    # 2. Budget gate — shared with video generation.
    chars = len(text)
    cost = round(chars * TTS_PRICE_PER_CHAR, 6)
    try:
        import core.studio_jobs as sj
        if sj.remaining_budget() < cost:
            return _browser_fallback("tts budget cap reached", voice)
    except Exception as exc:
        print(f"[TTS] budget check skipped: {exc}")

    # 3. Synthesise + fetch the audio bytes.
    audio = _qwen_synth(text, voice)
    if not audio:
        return _browser_fallback("qwen tts failed", voice)

    # 4. Cache: R2 primary; local disk only as a transient serve fallback.
    cached_ok = False
    try:
        r2_storage.upload_bytes(r2_key, audio, _TTS_CT)
        cached_ok = True
    except Exception as exc:
        print(f"[TTS] R2 cache upload failed: {exc}")
    if not cached_ok:
        try:
            TTS_FOLDER.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(audio)
        except Exception as exc:
            print(f"[TTS] local cache write failed: {exc}")

    # 5. Record spend against the shared cap.
    try:
        import core.studio_jobs as sj
        sj.add_spend(cost)
    except Exception as exc:
        print(f"[TTS] spend record skipped: {exc}")

    return {"enabled": True, "engine": "qwen_tts", "url": _serve_url(name),
            "voice": voice, "cached": False, "characters": chars, "cost": cost}


def _qwen_synth(text: str, voice: str):
    """Call qwen3-tts-flash and return the audio bytes (WAV), or None on failure."""
    import requests
    from core.config import get_config
    headers = {"Authorization": f"Bearer {get_config().alibaba_api_key}",
               "Content-Type": "application/json"}
    body = {"model": QWEN_TTS_MODEL, "input": {"text": text, "voice": voice}}
    try:
        r = requests.post(QWEN_TTS_URL, headers=headers, json=body, timeout=60)
        j = r.json()
    except Exception as exc:
        print(f"[TTS] qwen request failed: {exc}")
        return None
    if r.status_code != 200:
        print(f"[TTS] qwen HTTP {r.status_code}: {str(j.get('message', ''))[:160]}")
        return None
    audio_url = ((j.get("output") or {}).get("audio") or {}).get("url")
    if not audio_url:
        print("[TTS] qwen response had no audio url")
        return None
    try:
        ar = requests.get(audio_url, timeout=60)
        if ar.status_code != 200 or not ar.content:
            return None
        return ar.content
    except Exception as exc:
        print(f"[TTS] audio download failed: {exc}")
        return None
