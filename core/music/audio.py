"""ValleyMind Music — real audio engine on ffmpeg.

The workstation roadmap requires actual audio operations (trim, fade, EQ,
compression, normalization, noise reduction, vocal cleanup, format conversion,
loudness measurement and a quick DSP stem split). ValleyMind already ships a
bundled static ffmpeg (``imageio-ffmpeg``) used by the video pipeline, so the
Music engine can perform those operations *for real* on the server — no fake
"87% processed" buttons.

Honesty rules (mirrored from the rest of the music engine):

  * Every operation writes into the per-user sandbox via ``core.music.storage``.
  * If ffmpeg is unavailable the module reports ``available() == False`` and the
    routes surface a clear, honest "audio processor unavailable" state.
  * Operation parameters are strictly validated + clamped; user text never
    reaches a filter string unescaped (all numbers, no shell).
  * Machine-learning stem separation (vocals/drums/bass/melody) stays a
    provider-gated capability. ``quick_stems`` here is the *real* DSP
    mid/side split, labelled honestly as such.

Everything below is deliberately dependency-lite (stdlib + the bundled ffmpeg).
"""

from __future__ import annotations

import re
import subprocess
import threading
import time

from core.music.storage import user_file_path, StorageError

# --------------------------------------------------------------------------- #
# ffmpeg discovery
# --------------------------------------------------------------------------- #

_FFMPEG_CACHE: dict = {"path": None, "checked": False}
_FFMPEG_LOCK = threading.RLock()  # reentrant: _filters() calls ffmpeg_path() while held
_FILTERS_CACHE: dict = {"filters": None, "checked": False}


def ffmpeg_path() -> str | None:
    """Return the active ffmpeg binary (bundled imageio-ffmpeg or system)."""
    with _FFMPEG_LOCK:
        if _FFMPEG_CACHE["checked"]:
            return _FFMPEG_CACHE["path"]
        try:
            from core.video_assembly import ffmpeg_exe
            _FFMPEG_CACHE["path"] = ffmpeg_exe()
        except Exception:  # noqa: BLE001
            _FFMPEG_CACHE["path"] = None
        _FFMPEG_CACHE["checked"] = True
        return _FFMPEG_CACHE["path"]


def available() -> bool:
    return ffmpeg_path() is not None


def _run(args: list, timeout: int = 240) -> tuple[bool, str]:
    """Run ffmpeg, capture combined output. Returns (ok, message)."""
    exe = ffmpeg_path()
    if not exe:
        return False, "ffmpeg is not available on this server."
    try:
        cmd = [exe, "-y", "-hide_banner", "-nostats"] + args
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0:
            tail = (out or "ffmpeg failed").strip()[-700:]
            return False, tail or f"ffmpeg exit code {p.returncode}"
        return True, out
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out processing this file."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not start ffmpeg: {exc}"


def _filters() -> set[str]:
    """Set of filter names the active ffmpeg build supports (cached)."""
    with _FFMPEG_LOCK:
        if _FILTERS_CACHE["checked"]:
            return _FILTERS_CACHE["filters"]
        names: set[str] = set()
        exe = ffmpeg_path()
        if exe:
            try:
                p = subprocess.run([exe, "-hide_banner", "-filters"],
                                   capture_output=True, text=True, timeout=30,
                                   stdin=subprocess.DEVNULL)
                for line in (p.stdout or "").splitlines():
                    m = re.match(r"\s*[T.SC]{3}\s+(\S+)", line)
                    if m:
                        names.add(m.group(1))
            except Exception:  # noqa: BLE001
                pass
        _FILTERS_CACHE["filters"] = names
        _FILTERS_CACHE["checked"] = True
        return names


def has_filter(name: str) -> bool:
    return name in _filters()


# --------------------------------------------------------------------------- #
# Safe numeric parameter helpers
# --------------------------------------------------------------------------- #

def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _fmt(v: float) -> str:
    return ("%.6g" % v)


def _db2lin(db: float) -> float:
    """Linear amplitude factor for a gain expressed in decibels."""
    return 10.0 ** (float(db) / 20.0)


# --------------------------------------------------------------------------- #
# Tool registry (surfaced to the UI so controls are real, not decorative)
# --------------------------------------------------------------------------- #

AUDIO_TOOLS: list[dict] = [
    {"id": "trim", "label": "Trim",
     "hint": "Keep only a section of the audio.",
     "params": [
         {"key": "start", "label": "Start (s)", "min": 0, "max": 36000, "step": 0.5, "default": 0},
         {"key": "end", "label": "End (s)", "min": 0, "max": 36000, "step": 0.5, "default": 0},
     ]},
    {"id": "fade", "label": "Fade in / out",
     "hint": "Smooth fades at the edges.",
     "params": [
         {"key": "fadeIn", "label": "Fade in (s)", "min": 0, "max": 30, "step": 0.25, "default": 1},
         {"key": "fadeOut", "label": "Fade out (s)", "min": 0, "max": 30, "step": 0.25, "default": 3},
     ]},
    {"id": "silence", "label": "Remove silence",
     "hint": "Cuts leading/trailing silence below a threshold.",
     "params": [
         {"key": "threshold", "label": "Threshold (dB, e.g. -35)", "min": -80, "max": -20, "step": 1, "default": -35},
         {"key": "minDur", "label": "Min silence (s)", "min": 0.1, "max": 5, "step": 0.1, "default": 0.25},
     ]},
    {"id": "normalize", "label": "Normalize",
     "hint": "Match loudness to a target (loudnorm).",
     "params": [
         {"key": "level", "label": "Target (LUFS)", "min": -23, "max": -8, "step": 0.5, "default": -14},
     ]},
    {"id": "noise", "label": "Noise reduction",
     "hint": "Suppress background hiss (fft denoising).",
     "params": [
         {"key": "amount", "label": "Reduction (dB)", "min": 0, "max": 30, "step": 1, "default": 12},
         {"key": "floor", "label": "Noise floor (dB)", "min": -80, "max": -10, "step": 1, "default": -30},
     ]},
    {"id": "highpass", "label": "High-pass filter",
     "hint": "Rolls off lows (rumble removal).",
     "params": [
         {"key": "freq", "label": "Frequency (Hz)", "min": 20, "max": 500, "step": 1, "default": 80},
     ]},
    {"id": "lowpass", "label": "Low-pass filter",
     "hint": "Rolls off highs.",
     "params": [
         {"key": "freq", "label": "Frequency (Hz)", "min": 3000, "max": 20000, "step": 100, "default": 16000},
     ]},
    {"id": "eq", "label": "EQ (Bass / Mids / Treble)",
     "hint": "Three-band tone shaping.",
     "params": [
         {"key": "bass", "label": "Bass (dB)", "min": -12, "max": 12, "step": 0.5, "default": 0},
         {"key": "mids", "label": "Mids (dB)", "min": -12, "max": 12, "step": 0.5, "default": 0},
         {"key": "treble", "label": "Treble (dB)", "min": -12, "max": 12, "step": 0.5, "default": 0},
     ]},
    {"id": "compressor", "label": "Compressor",
     "hint": "Controls dynamics.",
     "params": [
         {"key": "threshold", "label": "Threshold (dB)", "min": -60, "max": 0, "step": 1, "default": -20},
         {"key": "ratio", "label": "Ratio", "min": 1, "max": 20, "step": 0.5, "default": 3},
         {"key": "makeup", "label": "Make-up (dB)", "min": 0, "max": 24, "step": 0.5, "default": 2},
     ]},
    {"id": "limiter", "label": "Limiter",
     "hint": "Ceiling for the loudest peaks.",
     "params": [
         {"key": "limit", "label": "Limit (dBTP)", "min": -12, "max": 0, "step": 0.1, "default": -1},
     ]},
    {"id": "reverb", "label": "Reverb (room)",
     "hint": "Adds space and depth.",
     "params": [
         {"key": "mix", "label": "Wet mix (%)", "min": 0, "max": 100, "step": 1, "default": 25},
         {"key": "roomsize", "label": "Room size", "min": 0.1, "max": 1, "step": 0.05, "default": 0.5},
     ]},
    {"id": "delay", "label": "Delay / echo",
     "hint": "Echo repeats.",
     "params": [
         {"key": "time", "label": "Delay (ms)", "min": 50, "max": 1500, "step": 10, "default": 250},
         {"key": "feedback", "label": "Feedback (%)", "min": 0, "max": 90, "step": 1, "default": 35},
     ]},
    {"id": "pitch", "label": "Pitch shift",
     "hint": "Shift up or down in semitones (keeps tempo).",
     "params": [
         {"key": "semitones", "label": "Semitones", "min": -12, "max": 12, "step": 0.5, "default": 0},
     ]},
    {"id": "tempo", "label": "Tempo",
     "hint": "Change speed (keeps pitch).",
     "params": [
         {"key": "rate", "label": "Rate (0.5–2.5×)", "min": 0.5, "max": 2.5, "step": 0.05, "default": 1},
     ]},
    {"id": "gain", "label": "Gain",
     "hint": "Add or remove overall level.",
     "params": [
         {"key": "db", "label": "Gain (dB)", "min": -30, "max": 30, "step": 0.5, "default": 0},
     ]},
    {"id": "stereo", "label": "Stereo width",
     "hint": "0 = mono, 1 = unchanged, 2 = wide.",
     "params": [
         {"key": "width", "label": "Width", "min": 0, "max": 2, "step": 0.05, "default": 1},
     ]},
    {"id": "reverse", "label": "Reverse",
     "hint": "Play the audio backwards.",
     "params": []},
]

TOOLS_BY_ID = {t["id"]: t for t in AUDIO_TOOLS}


def tool_specs() -> list[dict]:
    return AUDIO_TOOLS


def _has_chain(filters: list[str]) -> tuple[bool, str]:
    """Every required filter must exist in this ffmpeg build (except 'pan')."""
    missing = [f for f in filters if f not in ("pan",) and f not in _filters()]
    if missing:
        return False, ", ".join(missing)
    return True, ""


# --------------------------------------------------------------------------- #
# Filter-chain builder — every tool maps to a safe numeric chain
# --------------------------------------------------------------------------- #

def build_filter_chain(tools: list, mr: int = 16) -> tuple[str, str]:
    """Build the ``-af`` chain string from a validated list of {id, params}.

    Returns ``(chain, error)``. All values are machine-clamped numbers.
    """
    chain: list[str] = []
    for tool in tools or []:
        tid = str((tool or {}).get("id") or "").strip()
        spec = TOOLS_BY_ID.get(tid)
        if not spec:
            continue
        p = (tool or {}).get("params") or {}
        if tid == "trim":
            start = _clamp(_num(p.get("start"), 0), 0, 36000)
            end = _clamp(_num(p.get("end"), 0), 0, 36000)
            if start > 0 or end > 0:
                node = "atrim=" + (("start=" + _fmt(start)) if start > 0 else "") + (
                    ("," + "end=" + _fmt(end)) if end > 0 else "")
                chain.append(node + ",asetpts=PTS-STARTPTS")
        elif tid == "fade":
            fi = _clamp(_num(p.get("fadeIn"), 0), 0, 30)
            fo = _clamp(_num(p.get("fadeOut"), 0), 0, 30)
            if fi > 0:
                chain.append("afade=t=in:st=0:d=" + _fmt(fi))
            if fo > 0:
                chain.append("afade=t=out:st=" + _fmt(mr - fo) + ":d=" + _fmt(fo))
        elif tid == "silence":
            thr = _clamp(_num(p.get("threshold"), -35), -80, -20)
            dur = _clamp(_num(p.get("minDur"), 0.25), 0.1, 5)
            chain.append(
                "silenceremove=start_periods=1:start_duration=" + _fmt(dur) +
                ":start_threshold=" + _fmt(thr) + "dB:stop_periods=1:stop_duration=" +
                _fmt(dur) + ":stop_threshold=" + _fmt(thr) + "dB")
        elif tid == "normalize":
            lvl = _clamp(_num(p.get("level"), -14), -23, -8)
            chain.append("loudnorm=I=" + _fmt(lvl) + ":TP=-1.5:LRA=11,aresample=44100")
        elif tid == "noise":
            amt = _clamp(_num(p.get("amount"), 12), 0, 30)
            floor = _clamp(_num(p.get("floor"), -30), -80, -10)
            chain.append("afftdn=nr=" + _fmt(amt) + ":nf=" + _fmt(floor))
        elif tid == "highpass":
            f_ = _clamp(_num(p.get("freq"), 80), 20, 500)
            chain.append("highpass=f=" + _fmt(f_))
        elif tid == "lowpass":
            f_ = _clamp(_num(p.get("freq"), 16000), 3000, 20000)
            chain.append("lowpass=f=" + _fmt(f_))
        elif tid == "eq":
            bass = _clamp(_num(p.get("bass"), 0), -12, 12)
            mids = _clamp(_num(p.get("mids"), 0), -12, 12)
            treble = _clamp(_num(p.get("treble"), 0), -12, 12)
            if abs(bass) > 0.001:
                chain.append("equalizer=f=120:width_type=o:width=1.1:g=" + _fmt(bass))
            if abs(mids) > 0.001:
                chain.append("equalizer=f=1000:width_type=o:width=1.0:g=" + _fmt(mids))
            if abs(treble) > 0.001:
                chain.append("equalizer=f=6200:width_type=o:width=1.1:g=" + _fmt(treble))
        elif tid == "compressor":
            thr_lin = _db2lin(_clamp(_num(p.get("threshold"), -20), -60, 0))
            ratio = _clamp(_num(p.get("ratio"), 3), 1, 20)
            makeup_lin = _db2lin(_clamp(_num(p.get("makeup"), 2), 0, 24))
            chain.append("acompressor=threshold=" + _fmt(thr_lin) + ":ratio=" + _fmt(ratio) +
                         ":makeup=" + _fmt(makeup_lin) + ":attack=10:release=180")
        elif tid == "limiter":
            limit_lin = _db2lin(_clamp(_num(p.get("limit"), -1), -12, 0))
            chain.append("alimiter=limit=" + _fmt(limit_lin) + ":level=disabled")
        elif tid == "reverb":
            mix = _clamp(_num(p.get("mix"), 25), 0, 100)
            size = _clamp(_num(p.get("roomsize"), 0.5), 0.1, 1)
            if has_filter("areverb"):
                chain.append("areverb=wet=" + _fmt(mix / 100.0) + ":dry=" +
                             _fmt(1 - mix / 100.0) + ":roomsize=" + _fmt(size) +
                             ":damping=0.5:stereo=true")
            else:
                chain.append("aecho=0.8:0.88:" + _fmt(int(60 + size * 140)) + ":0.3")
        elif tid == "delay":
            t_ms = int(_clamp(_num(p.get("time"), 250), 50, 1500))
            fb = _clamp(_num(p.get("feedback"), 35), 0, 90)
            chain.append("aecho=0.8:0.88:" + str(t_ms) + ":" + _fmt(1 - fb / 90.0))
        elif tid == "pitch":
            semi = _clamp(_num(p.get("semitones"), 0), -12, 12)
            if abs(semi) > 0.001:
                ratio = 2.0 ** (semi / 12.0)
                chain.append("asetrate=r=" + _fmt(44100 * ratio) +
                             ",aresample=44100,atempo=" + _fmt(1.0 / ratio))
        elif tid == "tempo":
            rate = _clamp(_num(p.get("rate"), 1), 0.5, 2.5)
            if abs(rate - 1) > 0.001:
                temps = []
                r = rate
                while r > 1.9999:
                    temps.append("2.0")
                    r /= 2.0
                temps.append(_fmt(r))
                chain.append("atempo=" + ",atempo=".join(temps))
        elif tid == "gain":
            db = _clamp(_num(p.get("db"), 0), -30, 30)
            if abs(db) > 0.001:
                chain.append("volume=" + _fmt(db) + "dB")
        elif tid == "stereo":
            w = _clamp(_num(p.get("width"), 1), 0, 2)
            a = 0.5 + w / 2.0
            b = 0.5 - w / 2.0
            chain.append("pan=stereo|c0=" + _fmt(a) + "*c0+" + _fmt(b) +
                         "*c1|c1=" + _fmt(b) + "*c0+" + _fmt(a) + "*c1")
        elif tid == "reverse":
            chain.append("areverse")
    return (",".join(chain), "") if chain else ("", "")


# --------------------------------------------------------------------------- #
# probe / loudness
# --------------------------------------------------------------------------- #

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SR_RE = re.compile(r"(\d+) Hz")
_CH_RE = re.compile(r"(mono|stereo)\b")
_CODEC_RE = re.compile(r"Audio:\s*([^,\s]+)")
_I_RE = re.compile(r"I:\s*([-+0-9.]+)\s*LUFS")
_LRA_RE = re.compile(r"LRA:\s*([-+0-9.]+)\s*LU")
_TP_RE = re.compile(r"TP:\s*([-+0-9.]+)\s*dBFS")
_MEAN_V_RE = re.compile(r"mean_volume:\s*([-+0-9.]+)\s*dB")
_MAX_V_RE = re.compile(r"max_volume:\s*([-+0-9.]+)\s*dB")


def probe(path: str) -> dict:
    """Parse duration / sample-rate / channels / codec from ``ffmpeg -i``."""
    exe = ffmpeg_path()
    out = ""
    if exe:
        try:
            p = subprocess.run([exe, "-hide_banner", "-i", path],
                               capture_output=True, text=True, timeout=120,
                               stdin=subprocess.DEVNULL)
            out = (p.stdout or "") + (p.stderr or "")
        except Exception:  # noqa: BLE001
            pass
    info: dict = {}
    m = _DUR_RE.search(out)
    if m:
        info["duration"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 +
                                 float(m.group(3)), 3)
    m = _SR_RE.search(out)
    if m:
        info["sample_rate"] = int(m.group(1))
    m = _CH_RE.search(out)
    if m:
        info["channels"] = m.group(1)
    m = _CODEC_RE.search(out)
    if m:
        info["codec"] = m.group(1)
    return info


def _measure(file_path: str) -> dict:
    """True loudness measurement: ebur128 (integrated/LRA/TP) + volumedetect."""
    res: dict = {}
    # Pass 1: ebur128 (integrated loudness). Degrades to nothing if unsupported.
    ok, out = _run(["-i", file_path, "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    if ok:
        i = _I_RE.findall(out)
        lra = _LRA_RE.findall(out)
        tp = _TP_RE.findall(out)
        if i:
            res["integrated_lufs"] = round(float(i[-1]), 1)
        if lra:
            res["lra"] = round(float(lra[-1]), 1)
        if tp:
            res["true_peak_db"] = round(float(tp[-1]), 1)
    # Pass 2: volumedetect (safe everywhere).
    ok, out = _run(["-i", file_path, "-af", "volumedetect", "-f", "null", "-"])
    if ok:
        m = _MEAN_V_RE.search(out)
        if m:
            res["mean_db"] = round(float(m.group(1)), 1)
        m = _MAX_V_RE.search(out)
        if m:
            res["max_db"] = round(float(m.group(1)), 1)
    return res


def loudness(user_id: str, rel: str) -> dict:
    """Public loudness report for a stored file. Raises StorageError on bad rel."""
    path = user_file_path(user_id, rel, must_exist=True)
    info = probe(str(path))
    meas = _measure(str(path))
    return {**info, **meas, "rel": rel}


# --------------------------------------------------------------------------- #
# Ops: apply tools / vocal clean / quick stems / convert
# --------------------------------------------------------------------------- #

def apply_tools(user_id: str, rel: str, tools: list) -> dict:
    """Apply a validated tool chain to a stored file, saving to ``generated``.

    Returns a descriptor with the new file (or a structured error).
    """
    if not available():
        return {"status": "error", "error": "Audio processor (ffmpeg) is not available on this server.",
                "error_code": "audio_unavailable"}
    path = user_file_path(user_id, rel, must_exist=True)
    info = probe(str(path))
    total = _num(info.get("duration", 0), 0)
    chain, err = build_filter_chain(tools, mr=int(total or 16))
    if err:
        return {"status": "error",
                "error": "This ffmpeg build is missing filter(s): " + err,
                "error_code": "filter_missing"}
    if not chain:
        return {"status": "error", "error": "No processing requested.", "error_code": "no_tools"}

    from core.music.storage import save_bytes  # local import to avoid cycles
    out_basename = "processed_" + str(time.time()).replace(".", "") + ".wav"
    src = user_file_path(user_id, "tmp/" + out_basename)  # tmp scratch then move
    src.parent.mkdir(parents=True, exist_ok=True)
    ok, msg = _run(["-i", str(path), "-af", chain, "-c:a", "pcm_s16le",
                    "-vn", "-y", str(src)], timeout=600)
    if not ok:
        try:
            src.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "error": "Processing failed: " + (msg or "ffmpeg error"),
                "error_code": "ffmpeg_failed", "detail": msg}
    data = src.read_bytes()
    try:
        src.unlink()
    except Exception:  # noqa: BLE001
        pass
    desc = save_bytes(user_id, "generated", data, out_basename)
    desc["processed_from"] = rel
    desc["tools"] = [{"id": t.get("id"), "params": t.get("params") or {}} for t in tools]
    desc["analysis"] = probe(str(user_file_path(user_id, desc["rel"])))
    return {"status": "success", "file": desc}


def process_vocal(user_id: str, rel: str, opts: dict) -> dict:
    """Vocal cleanup chain — real noise reduction, EQ, compression, normalize."""
    if not available():
        return {"status": "error", "error": "Audio processor (ffmpeg) is not available on this server.",
                "error_code": "audio_unavailable"}
    path = user_file_path(user_id, rel, must_exist=True)
    info = probe(str(path))
    preset = str((opts or {}).get("preset") or "shine").strip().lower()
    nr = _clamp(_num((opts or {}).get("noise_db"), -1), -1, 30)
    hp = _clamp(_num((opts or {}).get("highpass_hz"), -1), -1, 500)
    lp = _clamp(_num((opts or {}).get("lowpass_hz"), -1), -1, 20000)
    presence = _clamp(_num((opts or {}).get("presence_db"), 0), -6, 6)
    gain = _clamp(_num((opts or {}).get("gain_db"), 0), -12, 12)
    compress = bool((opts or {}).get("compress", True))
    normalize = bool((opts or {}).get("normalize", True))

    filters: list[str] = []
    noise_val = nr if nr >= 0 else (15 if preset == "radio" else (12 if preset == "shine" else 8))
    filters.append("afftdn=nr=" + _fmt(noise_val) + ":nf=" + _fmt(-(30 + noise_val)))
    hp_val = hp if hp >= 0 else (85 if preset == "radio" else (80 if preset == "shine" else 75))
    filters.append("highpass=f=" + _fmt(hp_val))
    lp_val = lp if lp >= 0 else (17000 if preset == "radio" else (16000 if preset == "shine" else 15000))
    filters.append("lowpass=f=" + _fmt(lp_val))
    if abs(presence) > 0.001:
        filters.append("equalizer=f=3200:width_type=o:width=0.8:g=" + _fmt(presence))
    if compress:
        filters.append("acompressor=threshold=" + _fmt(_db2lin(-24)) +
                       ":ratio=2.5:makeup=" + _fmt(_db2lin(3)) +
                       ":attack=8:release=160")
    filters.append("alimiter=limit=" + _fmt(_db2lin(-1.3)) + ":level=disabled")
    if normalize:
        filters.append("loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100")
    if abs(gain) > 0.001:
        filters.append("volume=" + _fmt(gain) + "dB")
    chain = ",".join(filters)
    missing = _has_chain([f.split("=")[0] for f in filters])
    if missing[0] is False:
        return {"status": "error",
                "error": "This ffmpeg build is missing filter(s): " + missing[1],
                "error_code": "filter_missing"}

    from core.music.storage import save_bytes  # local import to avoid cycles
    out_basename = "vocal_clean_" + str(time.time()).replace(".", "") + ".wav"
    src = user_file_path(user_id, "tmp/" + out_basename)
    src.parent.mkdir(parents=True, exist_ok=True)
    ok, msg = _run(["-i", str(path), "-af", chain, "-c:a", "pcm_s16le", "-vn", "-y", str(src)],
                   timeout=600)
    if not ok:
        try:
            src.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "error": "Vocal processing failed: " + (msg or "ffmpeg error"),
                "error_code": "ffmpeg_failed"}
    data = src.read_bytes()
    try:
        src.unlink()
    except Exception:  # noqa: BLE001
        pass
    desc = save_bytes(user_id, "vocals", data, out_basename)
    desc["processed_from"] = rel
    desc["preset"] = preset
    desc["clean"] = True
    desc["analysis"] = probe(str(user_file_path(user_id, desc["rel"])))
    return {"status": "success", "vocal": desc}


def quick_stems(user_id: str, rel: str) -> dict:
    """Real DSP mid/side split — NOT a machine-learning stem model.

    Produces two honest tracks:
      * "Center — vocals & bass & kick"  (L+R)
      * "Sides — panned instruments"     (L−R)
    """
    if not available():
        return {"status": "error", "error": "Audio processor (ffmpeg) is not available on this server.",
                "error_code": "audio_unavailable"}
    path = user_file_path(user_id, rel, must_exist=True)
    info = probe(str(path))

    from core.music.storage import save_bytes  # local import to avoid cycles
    stems = []
    for key, name, chain in (
        ("center", "Center (vocals/bass/kick)",
         "pan=mono|c0=0.5*c0+0.5*c1,pan=stereo|c0=c0|c1=c0"),
        ("sides", "Sides (panned instruments)",
         "pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c1-0.5*c0"),
    ):
        out_base = f"stem_{key}_{int(time.time())}_{uuid_suffix()}.wav"
        src = user_file_path(user_id, "tmp/" + out_base)
        src.parent.mkdir(parents=True, exist_ok=True)
        ok, msg = _run(["-i", str(path), "-af", chain, "-c:a", "pcm_s16le", "-vn", "-y", str(src)],
                       timeout=600)
        if not ok:
            try:
                src.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            return {"status": "error",
                    "error": "Stem split failed: " + (msg or "ffmpeg error"),
                    "error_code": "ffmpeg_failed"}
        data = src.read_bytes()
        try:
            src.unlink()
        except Exception:  # noqa: BLE001
            pass
        desc = save_bytes(user_id, "stems", data, out_base)
        desc["name"] = name
        desc["part"] = key
        desc["method"] = "dsp_mid_side"
        stems.append(desc)
    return {"status": "success", "stems": stems,
            "info": info,
            "note": "DSP mid/side split, not a machine-learning stem model. "
                    "Center ≈ vocals, bass & kick; Sides ≈ panned instruments."}


def convert(user_id: str, rel: str, fmt: str) -> dict:
    """Convert a stored audio file to wav or mp3 into the ``exports`` category.

    Returns the new file descriptor. Raises StorageError on bad rel.
    """
    if not available():
        return {"status": "error", "error": "Audio processor (ffmpeg) is not available on this server.",
                "error_code": "audio_unavailable"}
    fmt = (fmt or "wav").strip().lower()
    if fmt not in ("wav", "mp3"):
        return {"status": "error", "error": "Only wav / mp3 conversion is supported.",
                "error_code": "bad_format"}
    path = user_file_path(user_id, rel, must_exist=True)
    info = probe(str(path))
    if fmt == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k"]
    else:
        codec_args = ["-c:a", "pcm_s16le"]

    from core.music.storage import save_bytes  # local import to avoid cycles
    out_base = f"export_{int(time.time())}_{uuid_suffix()}.{fmt}"
    src = user_file_path(user_id, "tmp/" + out_base)
    src.parent.mkdir(parents=True, exist_ok=True)
    ok, msg = _run(["-i", str(path)] + codec_args + ["-vn", "-y", str(src)], timeout=600)
    if not ok:
        try:
            src.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "error": "Conversion failed: " + (msg or "ffmpeg error"),
                "error_code": "ffmpeg_failed"}
    data = src.read_bytes()
    try:
        src.unlink()
    except Exception:  # noqa: BLE001
        pass
    desc = save_bytes(user_id, "exports", data, out_base)
    desc["converted_from"] = rel
    desc["format"] = fmt
    desc["analysis"] = probe(str(user_file_path(user_id, desc["rel"])))
    return {"status": "success", "file": desc}


def _uuid_suffix() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def uuid_suffix() -> str:
    return _uuid_suffix()


# --------------------------------------------------------------------------- #
# Continuous preview-friendly info for mastering
# --------------------------------------------------------------------------- #

def analyze_for_master(user_id: str, rel: str) -> dict:
    """Loudness + probe used by the Mastering view for real before-numbers."""
    try:
        path = user_file_path(user_id, rel, must_exist=True)
    except StorageError:
        return {"status": "error", "error": "File not found.", "error_code": "not_found"}
    info = probe(str(path))
    meas = _measure(str(path))
    return {"status": "success", "rel": rel, "analysis": {**info, **meas}}