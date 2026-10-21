import asyncio
import hashlib
import os
import re
import time
from pathlib import Path

from core.config import PROJECT_ROOT
from core import r2_storage
from core.cost_control import provider_blocked, record_error, record_success


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
    """Qwen TTS needs the Alibaba key AND R2 (the audio cache lives there).

    Also gated by the central cost-control switch: with paid providers off we
    fall back to the browser's speech synthesis instead of billing DashScope.
    """
    from core.config import get_config
    if provider_blocked("alibaba"):
        return False
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
    if provider_blocked("alibaba"):
        return None
    headers = {"Authorization": f"Bearer {get_config().alibaba_api_key}",
               "Content-Type": "application/json"}
    body = {"model": QWEN_TTS_MODEL, "input": {"text": text, "voice": voice}}
    try:
        r = requests.post(QWEN_TTS_URL, headers=headers, json=body, timeout=60)
        j = r.json()
    except Exception as exc:
        record_error("alibaba", exc=exc)
        print(f"[TTS] qwen request failed: {exc}")
        return None
    if r.status_code != 200:
        record_error("alibaba", status_code=r.status_code, body=str(j.get("message", ""))[:200])
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
        record_success("alibaba")
        return ar.content
    except Exception as exc:
        print(f"[TTS] audio download failed: {exc}")
        return None


# ── Free persona voices via edge-tts ───────────────────────────────────────
# The four companion voice profiles are served by Microsoft Edge's free edge-tts
# engine — no API key, no per-character cost, no account. Audio is cached on
# local disk (sha256 of engine+voice+text) and served same-origin like the paid
# path, so the overlay gets a real MP3 whose amplitude the browser can analyse
# for genuine lip-sync.
#
# This is additive: the default /api/tts behaviour (Qwen) is unchanged. Callers
# opt in with engine:"free" (or persona:"companion").

FREE_ENGINE = "edge_tts_free"
FREE_VOICE_MAP = {
    "marcus":   "en-US-GuyNeural",
    "angelina": "en-US-JennyNeural",
    "elena":    "en-US-AriaNeural",
    "companion": "en-US-ChristopherNeural",
}
FREE_DEFAULT_KEY = "companion"
FREE_MAX_CHARS = int(os.getenv("TTS_FREE_MAX_CHARS", "1200"))
# Chunk long text so edge-tts never stalls on an oversized single utterance.
_FREE_CHUNK_CHARS = 600


def free_voice_for_profile(profile: str) -> str:
    key = (profile or "").strip().lower()
    if key not in FREE_VOICE_MAP:
        key = FREE_DEFAULT_KEY
    return FREE_VOICE_MAP[key]


def free_tts_available() -> bool:
    """True when the free edge-tts engine can run in this process."""
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:
        return False


def _free_cache_name(voice: str, text: str) -> str:
    h = hashlib.sha256(f"{FREE_ENGINE}|{voice}|{text}".encode("utf-8")).hexdigest()
    return f"{h}.mp3"


async def _edge_save(text: str, output_path: Path, voice: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice or FREE_VOICE_MAP[FREE_DEFAULT_KEY])
    await communicate.save(str(output_path))


def _edge_synth_to_bytes(text: str, voice: str) -> "bytes | None":
    """Synthesise *text* with edge-tts, returning MP3 bytes (or None)."""
    import asyncio
    import tempfile
    chunks = [text[i:i + _FREE_CHUNK_CHARS] for i in range(0, len(text), _FREE_CHUNK_CHARS)] or [text]
    parts = []
    for chunk in chunks:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "chunk.mp3"
            try:
                asyncio.run(asyncio.wait_for(_edge_save(chunk, out, voice), timeout=30))
            except Exception as exc:
                print(f"[TTS] free edge synthesis failed: {exc}")
                return None
            if not out.exists() or out.stat().st_size == 0:
                return None
            parts.append(out.read_bytes())
    return b"".join(parts) if parts else None


def synthesize_free(text: str, profile: str = FREE_DEFAULT_KEY) -> dict:
    """Speak *text* with the free edge-tts engine in the chosen profile.

    Returns a dict the frontend plays:
      success -> {engine:'edge_tts_free', url, voice, profile, cached, characters}
      failure -> {engine:'none', reason}

    No cost gating: edge-tts is free. Audio is cached on local disk by a hash of
    engine+voice+text, so repeated lines are never re-synthesised.
    """
    voice = free_voice_for_profile(profile)
    key = (profile or "").strip().lower() or FREE_DEFAULT_KEY
    text = _clean_text(text)
    if not text:
        return {"enabled": False, "engine": "none", "reason": "empty text", "voice": voice}
    if not free_tts_available():
        return {"enabled": False, "engine": "none", "reason": "edge-tts not installed", "voice": voice}

    text = text[:FREE_MAX_CHARS]
    name = _free_cache_name(voice, text)
    local_path = TTS_FOLDER / name

    if local_path.exists() and local_path.stat().st_size > 0:
        return {"enabled": True, "engine": FREE_ENGINE, "url": _serve_url(name),
                "voice": voice, "profile": key, "cached": True, "characters": 0}

    audio = _edge_synth_to_bytes(text, voice)
    if not audio:
        return {"enabled": False, "engine": "none", "reason": "free tts failed", "voice": voice}

    try:
        TTS_FOLDER.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(audio)
    except Exception as exc:
        print(f"[TTS] free cache write failed: {exc}")
        return {"enabled": False, "engine": "none", "reason": "cache write failed", "voice": voice}

    return {"enabled": True, "engine": FREE_ENGINE, "url": _serve_url(name),
            "voice": voice, "profile": key, "cached": False, "characters": len(text)}


# ── Local Piper personas (opt-in, per-persona) ─────────────────────────────
# A persona whose saved selection is a supported local Piper voice is spoken by
# the local Piper engine (core/piper_tts.py) instead of the paid Qwen path.
# This NEVER calls Qwen: if Piper is unavailable or fails, it falls back to the
# free edge-tts engine and reports which engine actually produced the audio.
# Callers that have not selected Piper are unaffected — they never reach here.

def synthesize_persona_local(text: str, persona: str, voice_id: str) -> dict:
    """Speak *text* for *persona* using the selected local Piper *voice_id*.

    Piper success -> {engine:'piper_local', ...}
    Piper failure -> free edge fallback {engine:'edge_tts_free', ...}, or
                     {engine:'none', reason} if edge is also unavailable.

    Qwen is never invoked on this path, including when Piper fails.
    """
    from core import piper_tts
    key = (persona or "").strip().lower() or FREE_DEFAULT_KEY
    if piper_tts.is_piper_voice(voice_id):
        result = piper_tts.synthesize_piper(text, voice_id)
        if result.get("enabled"):
            return result
        reason = result.get("reason", "piper unavailable")
        print(f"[TTS] piper unavailable for {key} ({reason}); free fallback")
    # Piper unavailable/failed — fall back to the FREE engine only.
    fallback = synthesize_free(text, key)
    if fallback.get("enabled"):
        fallback["fallback_from"] = "piper_local"
        return fallback
    return {"enabled": False, "engine": "none",
            "reason": "piper and free fallback both unavailable",
            "voice": voice_id}
