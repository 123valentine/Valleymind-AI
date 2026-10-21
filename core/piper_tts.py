"""Local Piper TTS — opt-in, per-persona offline voices.

This is the ONLY module that runs Piper. It is deliberately small and additive:
the default persona TTS (Qwen) and the free edge-tts fallback in core/tts.py are
untouched. A persona only reaches Piper when the authenticated user has
explicitly saved a Piper voice for it (see the ``persona_voices`` map in the
cloud settings section).

Safety properties this module guarantees:

* Explicit allowlist — only the two supported voice ids map to model files. A
  client can never name a path, a URL, or an arbitrary model.
* Lazy loading — no model is loaded at import or at app startup. A model loads
  only when its voice is first synthesised.
* One model in memory at a time — a single-slot cache. Loading a different voice
  evicts the previous one, but eviction is refused while the current model is in
  use (active synthesis), so audio can never be produced with a half-evicted
  model.
* Concurrency-safe — a re-entrant load lock prevents duplicate loads; each
  synthesis holds a read lock so a concurrent eviction cannot pull the model out
  from under it.
* Local disk cache — output is cached under the shared TTS folder and served via
  the existing ``/tts/<file>`` route, exactly like synthesize_free, so no new
  serving path is introduced.

Missing model files and synthesis failures are reported honestly — never
faked, and never routed to a paid provider.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

from core.config import PROJECT_ROOT
from core.tts import TTS_FOLDER, _clean_text, _serve_url


# ── Allowlist ──────────────────────────────────────────────────────────────
# Voice id (what settings store) -> model basename on disk. Only these two are
# supported for now; both are already-downloaded female models. Marcus is never
# auto-assigned a female voice — the caller must explicitly select one, and no
# male model is bundled.
PIPER_VOICES: dict[str, str] = {
    "kristin_piper": "en_US-kristin-medium",
    "ljspeech_piper": "en_US-ljspeech-medium",
}

# Where the .onnx + .onnx.json live. Overridable so tests can point at any
# directory; production default is inside the app's own data folder.
PIPER_MODEL_DIR = Path(os.getenv("PIPER_MODEL_DIR") or (PROJECT_ROOT / "memory_data" / "piper"))

PIPER_ENGINE = "piper_local"
PIPER_MAX_CHARS = int(os.getenv("PIPER_MAX_CHARS", "1200"))
# Piper medium models run comfortably on CPU; synthesis is near-realtime locally
# (measured ~0.4-0.5 RTF on Windows). Cap the wait so a pathological input can
# never wedge a request thread.
PIPER_SYNTH_TIMEOUT = int(os.getenv("PIPER_SYNTH_TIMEOUT", "60"))


def voice_available() -> bool:
    """True when the ``piper`` package can run in this process. Never raises."""
    try:
        import piper  # noqa: F401
        return True
    except Exception:
        return False


def is_piper_voice(voice_id: str) -> bool:
    """Whether *voice_id* is one of the supported local Piper voices."""
    return str(voice_id or "").strip().lower() in PIPER_VOICES


def _model_paths(voice_id: str) -> tuple[Path, Path] | None:
    base = PIPER_VOICES.get(str(voice_id or "").strip().lower())
    if not base:
        return None
    model = PIPER_MODEL_DIR / f"{base}.onnx"
    config = PIPER_MODEL_DIR / f"{base}.onnx.json"
    return model, config


def model_files_present(voice_id: str) -> bool:
    """Whether both the .onnx and its .onnx.json exist on disk (no download)."""
    paths = _model_paths(voice_id)
    if not paths:
        return False
    model, config = paths
    return model.exists() and model.stat().st_size > 0 and config.exists()


# ── Single-slot, lazily-loaded model cache ─────────────────────────────────
class _Slot:
    """Holds at most one loaded PiperVoice and serialises load/synth/evict.

    Only one model is ever resident. A synthesis registers an in-flight use so
    the model cannot be evicted mid-synthesis; a request for a *different* voice
    waits (briefly, bounded) for the active synthesis to finish before swapping,
    rather than failing outright.
    """

    def __init__(self):
        self._voice_id: str | None = None
        self._voice = None
        self._lock = threading.RLock()
        # Signalled whenever _active drops to 0 (a synth finished / model freed).
        self._idle = threading.Condition(self._lock)
        self._active = 0  # number of in-flight syntheses on the current model
        # onnxruntime inference on one PiperVoice is serialised: concurrent
        # synthesize() calls on the SAME instance thrash the session and balloon
        # memory. One synthesis at a time per loaded model — matching how the
        # Round Table actually plays (sequentially) — keeps memory flat.
        self._synth_lock = threading.Lock()

    def _load_locked(self, voice_id: str):
        """Load (or return cached) the model. Caller holds _lock."""
        if self._voice_id == voice_id and self._voice is not None:
            return self._voice
        import piper
        paths = _model_paths(voice_id)
        if not paths:
            raise ValueError(f"unsupported piper voice: {voice_id}")
        model, config = paths
        if not (model.exists() and model.stat().st_size > 0 and config.exists()):
            raise FileNotFoundError(f"piper model missing for {voice_id}")
        voice = piper.PiperVoice.load(str(model), str(config))
        self._voice = voice
        self._voice_id = voice_id
        return voice

    def acquire(self, voice_id: str, wait: float | None = None):
        """Return a loaded voice and register an in-flight use of it.

        If a *different* voice is currently loaded and in use, wait up to *wait*
        seconds for it to go idle, then swap. Raises on unsupported/missing
        model, or TimeoutError if the current model never frees in time.
        """
        deadline = None if wait is None else (time.monotonic() + wait)
        with self._lock:
            while True:
                if self._voice_id == voice_id or self._voice is None or self._active == 0:
                    voice = self._load_locked(voice_id)
                    self._active += 1
                    return voice
                # A different model is resident AND in use — wait for idle.
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise TimeoutError("piper model busy")
                timeout = None if deadline is None else max(0.0, deadline - now)
                self._idle.wait(timeout=timeout)

    def release(self):
        with self._lock:
            if self._active > 0:
                self._active -= 1
            if self._active == 0:
                self._idle.notify_all()

    def loaded_voice_id(self) -> str | None:
        with self._lock:
            return self._voice_id

    def unload(self):
        """Drop the cached model (used by tests / explicit teardown)."""
        with self._lock:
            self._voice = None
            self._voice_id = None
            self._active = 0
            self._idle.notify_all()


_SLOT = _Slot()


def _cache_name(voice_id: str, text: str) -> str:
    h = hashlib.sha256(f"{PIPER_ENGINE}|{voice_id}|{text}".encode("utf-8")).hexdigest()
    return f"{h}.wav"


def _synth_to_bytes(voice_id: str, text: str) -> bytes | None:
    """Synthesise *text* with the allowlisted Piper model, returning WAV bytes.

    Returns None on any failure (missing model, load error, synth error) — never
    raises, so the caller can fall back cleanly.
    """
    if not is_piper_voice(voice_id):
        return None
    if not voice_available():
        return None
    if not model_files_present(voice_id):
        return None
    try:
        import io
        import wave
        # Wait (bounded) for a different resident model to go idle, then swap.
        voice = _SLOT.acquire(voice_id, wait=float(PIPER_SYNTH_TIMEOUT))
    except Exception as exc:
        print(f"[PIPER] load failed for {voice_id}: {exc}")
        return None
    try:
        import piper
        buf = io.BytesIO()
        chunks = []
        sample_rate = None
        # Serialise inference on the loaded model (see _Slot._synth_lock).
        with _SLOT._synth_lock:
            for chunk in voice.synthesize(text, piper.SynthesisConfig()):
                if sample_rate is None:
                    sample_rate = chunk.sample_rate
                chunks.append(chunk.audio_int16_bytes)
        if sample_rate is None or not chunks:
            return None
        pcm = b"".join(chunks)
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()
    except Exception as exc:
        print(f"[PIPER] synthesis failed for {voice_id}: {exc}")
        return None
    finally:
        _SLOT.release()


def synthesize_piper(text: str, voice_id: str) -> dict:
    """Speak *text* with the local Piper *voice_id*.

    Returns a dict the frontend plays, matching the shape used elsewhere:
      success -> {enabled, engine:'piper_local', url, voice, cached, characters}
      failure -> {enabled:False, engine:'none', reason, voice}

    Never raises and never touches a paid provider.
    """
    vid = str(voice_id or "").strip().lower()
    if not is_piper_voice(vid):
        return {"enabled": False, "engine": "none", "reason": "unsupported voice", "voice": vid}
    text = _clean_text(text)
    if not text:
        return {"enabled": False, "engine": "none", "reason": "empty text", "voice": vid}
    if not voice_available():
        return {"enabled": False, "engine": "none", "reason": "piper not installed", "voice": vid}
    if not model_files_present(vid):
        return {"enabled": False, "engine": "none", "reason": "model not available", "voice": vid}

    text = text[:PIPER_MAX_CHARS]
    name = _cache_name(vid, text)
    local_path = TTS_FOLDER / name
    if local_path.exists() and local_path.stat().st_size > 0:
        return {"enabled": True, "engine": PIPER_ENGINE, "url": _serve_url(name),
                "voice": vid, "cached": True, "characters": 0}

    audio = _synth_to_bytes(vid, text)
    if not audio:
        return {"enabled": False, "engine": "none", "reason": "piper synthesis failed", "voice": vid}
    try:
        TTS_FOLDER.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(audio)
    except Exception as exc:
        print(f"[PIPER] cache write failed: {exc}")
        return {"enabled": False, "engine": "none", "reason": "cache write failed", "voice": vid}
    return {"enabled": True, "engine": PIPER_ENGINE, "url": _serve_url(name),
            "voice": vid, "cached": False, "characters": len(text)}
