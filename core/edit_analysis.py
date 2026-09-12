"""Content understanding for Massive Editing — the "understand first" stage.

analysis → plan → timeline → effect → render

This module produces the picture of WHAT is happening in the footage and WHEN
it is most energetic, so the planner can make every cut/sticker/zoom land on a
real reason instead of a blanket effect. Two signal tiers:

  * Local ffmpeg heuristics (always run, never raise): scene-change times,
    audio RMS energy curve, quiet spans, held/frozen frames, scene density.
  * Qwen3-VL vision pass (core.video_vision) when a key is configured — scene
    description, subject position and candidate "moments". Gracefully skipped
    when unavailable.

Every function degrades: no ffmpeg, no audio track, no vision key → an empty
or partial dict. Nothing here can break an edit.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

_RMS_WINDOW = 0.25
_AUDIO_RATE = 8000


def _from_video_assembly():
    from core.video_assembly import ffmpeg_exe, _probe_duration
    return ffmpeg_exe(), _probe_duration


def _parse_float(v, default=None):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return default


def _rms_curve(exe: str, src_path: str, workdir: str) -> list:
    """Per-window loudness of the original audio track (0..1 normalized RMS)."""
    raw = os.path.join(workdir, "analysis_rms.raw")
    cmd = [exe, "-y", "-i", src_path, "-map", "0:a?",
           "-ac", "1", "-ar", str(_AUDIO_RATE), "-f", "s16le", raw]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=300)
        if p.returncode != 0 or not os.path.exists(raw):
            return []
        data = open(raw, "rb").read()
        if len(data) < 2:
            return []
    except Exception as exc:
        print(f"[ANALYSIS] rms decode skipped: {exc}")
        return []
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass

    win = int(_AUDIO_RATE * _RMS_WINDOW)
    import struct
    curve: list = []
    peak = 0.0
    samples = struct.unpack("<%dh" % (len(data) // 2), data[: len(data) // 2 * 2])
    nwindows = len(samples) // win
    for w in range(nwindows):
        chunk = samples[w * win:(w + 1) * win]
        rms = (sum(s * s for s in chunk) / len(chunk)) ** 0.5
        peak = max(peak, rms)
        curve.append((round(w * _RMS_WINDOW, 3), rms))
    if peak <= 0:
        return []
    return [(round(t, 3), round(v / peak, 4)) for t, v in curve]


def _scene_times(exe: str, src_path: str) -> list:
    """Cut times where the frame visibly changes (scene/scene-score heuristic)."""
    cmd = [exe, "-y", "-i", src_path,
           "-vf", "select='gt(scene,0.32)',showinfo",
           "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        print(f"[ANALYSIS] scene scan skipped: {exc}")
        return []
    out = []
    for m in re.finditer(r"pts_time:(\d+\.?\d*)", p.stderr or ""):
        t = _parse_float(m.group(1))
        if t is not None:
            out.append(t)
    return out


def _freeze_windows(exe: str, src_path: str, workdir: str) -> list:
    """Windows where the frame is held nearly still (motion-freeze) > 0.4s."""
    raw = os.path.join(workdir, "analysis_freezes.raw")
    cmd = [exe, "-y", "-i", src_path, "-an",
           "-vf", "fps=10,scale=48:48,format=gray", "-pix_fmt", "gray", raw]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=300)
        if p.returncode != 0 or not os.path.exists(raw):
            return []
        data = open(raw, "rb").read()
    except Exception as exc:
        print(f"[ANALYSIS] freeze scan skipped: {exc}")
        return []
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    fr = 48 * 48
    if len(data) < fr * 2:
        return []
    prev = data[:fr]
    frozen = 0
    ws, we = None, None
    out: list = []
    frame = 0
    for i in range(0, len(data) - (len(data) % fr), fr):
        cur = data[i:i + fr]
        frame += 1
        diff = sum(abs(a - b) for a, b in zip(prev, cur))
        prev = cur
        if diff < fr * 2.5:  # essentially unchanged frame
            t = round(frame * 0.1, 3)
            if ws is None:
                ws = t
            we = t
        else:
            if ws is not None and we - ws >= 0.4:
                out.append({"start": ws, "end": we})
            ws, we = None, None
    if ws is not None and we - ws >= 0.4:
        out.append({"start": ws, "end": we})
    return out


def _moments_from(curve: list, scenes: list, duration: float) -> list:
    """Candidate highlight instants: loudness peaks above 60% of the max and
    dense scene change regions — the "something happened here" beats."""
    peaks: list = []
    if curve:
        loud = [cv for _t, cv in curve]
        hi = max(loud) or 1.0
        thr = hi * 0.6
        for i in range(1, len(curve) - 1):
            t, v = curve[i]
            if v >= thr and v >= curve[i - 1][1] and v >= curve[i + 1][1]:
                if not peaks or t - peaks[-1] > 1.2:
                    peaks.append(round(t, 2))
    moments = [{"at": t, "kind": "peak", "energy": "high"} for t in peaks]
    if scenes and duration >= 4.0:
        # 1s windows with 2+ cuts → busy moment worth emphasising.
        one = [s for s in scenes if s > 0.5 and s < (duration - 0.5)]
        for i in range(1, len(one)):
            if one[i] - one[i - 1] <= 1.0:
                t = round(one[i], 2)
                if not moments or t - moments[-1]["at"] > 1.2:
                    moments.append({"at": t, "kind": "peak", "energy": "medium"})
    moments.sort(key=lambda m: m["at"])
    dedup = []
    for m in moments:
        if not dedup or m["at"] - dedup[-1]["at"] > 0.9:
            dedup.append(m)
    return dedup[:12]


def _quiet_windows(curve: list) -> list:
    out: list = []
    active = None
    for t, v in curve:
        q = v < 0.035
        if q and active is None:
            active = t
        elif not q and active is not None:
            if t - active >= 0.8:
                out.append({"start": round(active, 2), "end": round(t, 2)})
            active = None
    if active is not None:
        dur = curve[-1][0] - active if curve else 0.0
        if dur >= 0.8:
            out.append({"start": round(active, 2),
                        "end": round(curve[-1][0], 2)})
    return out


def vision_summary(src_path: str) -> dict | None:
    """Qwen3-VL pass: what happens, subject position, candidate moments.
    Returns a dict or None when vision isn't configured / fails."""
    from core import video_vision as vv
    if not vv.available():
        return None
    system = (
        "You are the content-analysis engine of an automatic video editor. "
        "Watch the clip once and return STRICT JSON ONLY — no prose around it."
    )
    prompt = (
        'Return JSON with exactly: '
        '{"context": "1-2 sentence description of what happens and the vibe", '
        '"subject_focus": horizontal center of the main subject as a number 0..1 '
        '(0 = far left, 0.5 = centered, 1 = far right; your best estimate), '
        '"moments": up to 6 entries of {"at": whole seconds into the clip, '
        '"kind": one of joke|reaction|goal|reveal|peak|silence|intro|ending, '
        '"reason": short phrase}}. '
        'Estimate timestamps to the nearest second. If there is no clear subject, '
        'subject_focus is 0.5.'
    )
    res = vv.analyze_video(src_path, prompt, system=system, seconds=120)
    text = res.get("text") if isinstance(res, dict) else ""
    if not text or not isinstance(res, dict):
        return None
    try:
        import re as _re
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as exc:
        print(f"[ANALYSIS] vision JSON parse failed: {exc}")
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("moments"), list):
        data["moments"] = []
    data["moments"] = [
        {"at": _parse_float(m.get("at"), 0.0) or 0.0,
         "kind": str(m.get("kind", "peak"))[:30],
         "reason": str(m.get("reason", ""))[:120]}
        for m in data["moments"] if isinstance(m, dict)
    ][:8]
    f = _parse_float(data.get("subject_focus"), 0.5)
    data["subject_focus"] = 0.0 if f is None else max(0.0, min(1.0, f))
    return data


def analyze_clip(src_path: str, workdir: str | None = None, *,
                 use_vision: bool = True, on_progress=None) -> dict:
    """Full content-understanding pass over one clip. Never raises.

    Returns:
      {
        "duration": float,
        "scenes": [float...],          # scene-change times
        "energy": [{"t", "v"}...],     # per-window loudness 0..1
        "quiet": [{"start","end"}...],
        "freezes": [{"start","end"}...],
        "moments": [{"at","kind","energy"|"reason"}...],
        "subject_focus": float 0..1,
        "vision": dict | None,
      }
    """
    own = workdir is None
    wd = workdir or tempfile.mkdtemp(prefix="edit_analysis_")
    try:
        exe, probe = _from_video_assembly()
        base = {}
        if exe:
            dur = None
            try:
                dur = probe(exe, src_path)[1]
            except Exception:
                pass
            if callable(on_progress):
                on_progress()
            base = {
                "duration": round(dur or 0.0, 3),
                "scenes": _scene_times(exe, src_path),
                "energy": _rms_curve(exe, src_path, wd),
                "freezes": _freeze_windows(exe, src_path, wd),
            }
        curve = base.get("energy") or []
        base["quiet"] = _quiet_windows(curve)
        base["moments"] = _moments_from(curve, base.get("scenes") or [], base.get("duration") or 0.0)
        base["subject_focus"] = 0.5
        base["vision"] = None
        if use_vision and exe:
            if callable(on_progress):
                on_progress()
            vis = vision_summary(src_path)
            if isinstance(vis, dict):
                base["vision"] = vis
                base["subject_focus"] = vis.get("subject_focus", 0.5)
                for m in vis.get("moments", []):
                    if not base.get("moments") or m["at"] - base["moments"][-1]["at"] > 0.9:
                        base["moments"].append(m)
                base["moments"].sort(key=lambda m: m["at"])
        return base
    except Exception as exc:
        print(f"[ANALYSIS] pass degraded: {exc}")
        return {"duration": 0.0, "scenes": [], "energy": [], "quiet": [],
                "freezes": [], "moments": [], "subject_focus": 0.5, "vision": None}
    finally:
        if own:
            import shutil
            shutil.rmtree(wd, ignore_errors=True)


def analysis_brief(analysis: dict, words: list | None = None) -> str:
    """Compact, planner-ready text summary of the analysis (for the LLM prompt)."""
    if not analysis:
        return "analysis: none"
    bits = []
    vis = analysis.get("vision") or {}
    ctx = str(vis.get("context", "")).strip()
    if ctx:
        bits.append(f"context: {ctx}")
    dur = analysis.get("duration") or 0.0
    if dur:
        bits.append(f"duration: {round(dur, 1)}s")
    moments = analysis.get("moments") or []
    if moments:
        pm = "; ".join(f"~{m.get('at', 0):.0f}s {m.get('kind', 'peak')}"
                       for m in moments[:9])
        bits.append(f"high-energy moments: {pm}")
    freezes = analysis.get("freezes") or []
    if freezes:
        bits.append("held frames: " + "; ".join(
            f"{f['start']:.0f}-{f['end']:.0f}s" for f in freezes[:4]))
    quiet = analysis.get("quiet") or []
    if quiet and not words:
        bits.append("quiet spans: " + "; ".join(
            f"{q['start']:.0f}-{q['end']:.0f}s" for q in quiet[:4]))
    return "; ".join(bits) or "analysis: none"