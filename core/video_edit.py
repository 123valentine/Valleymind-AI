"""Automated short-form edit logic — the "brain" of Massive Editing.

Given a word-level transcript, this module decides which parts of a video to KEEP
(dropping silences and filler words) and builds an animated word-by-word caption
track (ASS/libass). The ffmpeg render chains that consume these decisions are
added lower in this file; the decision + caption functions here are PURE and
unit-testable with no ffmpeg and no network.

Word input shape (from core.transcription): a list of
    {"word": str, "start": float, "end": float}   # seconds, original timeline
"""
from __future__ import annotations

import os
import re

# ── Config (env-overridable) ────────────────────────────────────────────────

# Single-token fillers and multi-word filler phrases cut from the edit.
_DEFAULT_FILLERS = (
    "um", "uh", "erm", "ah", "eh", "hmm", "mmm", "like", "basically",
    "actually", "honestly", "literally", "you know", "i mean", "sort of",
    "kind of", "kinda", "sorta",
)


def fillers() -> set[str]:
    raw = os.getenv("EDIT_FILLERS", "").strip()
    items = [w.strip().lower() for w in raw.split(",")] if raw else list(_DEFAULT_FILLERS)
    return {w for w in items if w}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def silence_gap() -> float:
    """A pause between kept words longer than this (seconds) is cut."""
    return _f("EDIT_SILENCE_GAP", 0.6)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _norm(w: str) -> str:
    """Lowercase, strip punctuation — for matching filler tokens."""
    return re.sub(r"[^a-z']", "", str(w or "").lower())


def _mark_drops(words: list, filler_set: set[str]) -> list:
    """Return a per-word boolean 'drop' list. Drops single-token fillers and
    multi-word filler phrases (e.g. 'you know', 'i mean')."""
    norms = [_norm(w.get("word", "")) for w in words]
    drop = [False] * len(words)
    singles = {f for f in filler_set if " " not in f}
    phrases = [f.split() for f in filler_set if " " in f]
    for i, n in enumerate(norms):
        if not n or n in singles:
            drop[i] = True
    # Multi-word phrases: mark the whole run.
    for ph in phrases:
        L = len(ph)
        for i in range(len(norms) - L + 1):
            if norms[i:i + L] == ph:
                for j in range(i, i + L):
                    drop[j] = True
    return drop


# ── Edit plan: keep-segments + output-time caption words ────────────────────

def build_edit_plan(words: list, *, gap: float | None = None,
                    drop_fillers: bool = True) -> dict:
    """Turn a word-level transcript into an edit plan.

    Returns:
      {
        "keep":        [(start, end), ...]      # ORIGINAL-time segments to retain
        "kept_words":  [{"text","out_start","out_end","seg"}...]  # OUTPUT timeline
        "total_out":   float                    # output duration (seconds)
        "removed":     int                      # words dropped
      }
    A segment breaks whenever a filler is removed or the gap between two kept
    words exceeds the silence threshold — that is the silence/filler trim.
    """
    gap_thr = silence_gap() if gap is None else float(gap)
    filler_set = fillers() if drop_fillers else set()
    drop = _mark_drops(words, filler_set) if words else []

    segments: list[dict] = []
    cur: dict | None = None
    removed = 0
    for i, w in enumerate(words or []):
        if drop[i]:
            removed += 1
            if cur:
                segments.append(cur)
                cur = None
            continue
        try:
            ws, we = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if we < ws:
            we = ws
        txt = str(w.get("word", "")).strip()
        if cur is None:
            cur = {"ss": ws, "se": we, "words": [(ws, we, txt)]}
        elif ws - cur["se"] > gap_thr:
            segments.append(cur)
            cur = {"ss": ws, "se": we, "words": [(ws, we, txt)]}
        else:
            cur["se"] = we
            cur["words"].append((ws, we, txt))
    if cur:
        segments.append(cur)

    keep: list = []
    kept_words: list = []
    cum = 0.0
    for si, s in enumerate(segments):
        keep.append((round(s["ss"], 3), round(s["se"], 3)))
        seg_dur = s["se"] - s["ss"]
        for (ws, we, txt) in s["words"]:
            if not txt:
                continue
            kept_words.append({
                "text": txt,
                "out_start": round(cum + (ws - s["ss"]), 3),
                "out_end": round(cum + (we - s["ss"]), 3),
                "seg": si,
            })
        cum += seg_dur
    return {"keep": keep, "kept_words": kept_words,
            "total_out": round(cum, 3), "removed": removed}


# ── Animated captions (ASS / libass) ────────────────────────────────────────

def _ass_time(t: float) -> str:
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return str(text or "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _chunks(kept_words: list, n: int):
    """Group kept words into caption lines of up to n words, never spanning a
    hard cut (segment boundary)."""
    line: list = []
    for w in kept_words:
        if line and (w["seg"] != line[-1]["seg"] or len(line) >= n):
            yield line
            line = []
        line.append(w)
    if line:
        yield line


def build_ass(kept_words: list, *, fontname: str = "Arial", play_w: int = 720,
              play_h: int = 1280, words_per_line: int = 3, uppercase: bool = True) -> str:
    """Build an ASS subtitle track with word-by-word karaoke highlight — the
    Hormozi/Gadzhi look: big, bold, centered in the lower-third; each word turns
    from base white to the highlight colour as it's spoken (``\\kf`` karaoke).

    Colours are ASS &HAABBGGRR. PrimaryColour = spoken/active (yellow),
    SecondaryColour = not-yet-spoken (white), OutlineColour = black.
    """
    fontsize = max(24, int(play_h * 0.075))
    outline = max(2, int(fontsize * 0.09))
    shadow = max(1, int(fontsize * 0.04))
    margin_v = int(play_h * 0.20)
    try:
        wpl = max(1, int(os.getenv("EDIT_CAPTION_WORDS", str(words_per_line))))
    except (TypeError, ValueError):
        wpl = words_per_line

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {int(play_w)}\n"
        f"PlayResY: {int(play_h)}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Pop,{fontname},{fontsize},&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,40,40,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines: list[str] = []
    for chunk in _chunks(kept_words, wpl):
        start = chunk[0]["out_start"]
        end = max(chunk[-1]["out_end"], start + 0.4)   # min on-screen time
        parts = []
        for w in chunk:
            dur_cs = max(1, int(round((w["out_end"] - w["out_start"]) * 100)))
            txt = w["text"].upper() if uppercase else w["text"]
            parts.append(f"{{\\kf{dur_cs}}}{_ass_escape(txt)}")
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Pop,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


# ── ffmpeg render (trim → vertical → B-roll overlay → burn captions) ─────────
#
# All stages RE-ENCODE (unlike the Studio -c copy concat). To stay inside the
# 512MB free instance the passes use -preset ultrafast, the input is capped
# upstream (<=60s, <=720p), and the caller pulses the job heartbeat between
# passes. Font/subtitle files are referenced by basename with cwd=workdir so no
# Windows/Linux filter-path escaping is needed.

import subprocess


_FAMILY = {
    "arialbd.ttf": "Arial", "arial.ttf": "Arial",
    "DejaVuSans-Bold.ttf": "DejaVu Sans", "DejaVuSans.ttf": "DejaVu Sans",
    "LiberationSans-Bold.ttf": "Liberation Sans", "LiberationSans-Regular.ttf": "Liberation Sans",
    "Helvetica.ttc": "Helvetica",
}


def output_size() -> tuple[int, int]:
    raw = os.getenv("EDIT_OUTPUT", "720x1280").lower()
    try:
        w, h = raw.split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 720, 1280


def font_file_and_family() -> tuple[str, str]:
    """Locate a concrete bold font file (libass has no fontconfig in this build)."""
    from core.video_assembly import _FONT_CANDIDATES
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p, _FAMILY.get(os.path.basename(p), "Arial")
    return "", "Arial"


def _select_expr(keep: list) -> str:
    return "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keep) or "1"


def map_to_output(t: float, keep: list) -> float:
    """Map an ORIGINAL-time instant to the trimmed OUTPUT timeline."""
    cum = 0.0
    for s, e in keep:
        if t < s:
            return round(cum, 3)
        if t <= e:
            return round(cum + (t - s), 3)
        cum += (e - s)
    return round(cum, 3)


def remap_brolls(brolls: list, keep: list, *, min_dur: float = 1.5) -> list:
    """Map B-roll cue windows (original time) onto the output timeline."""
    out = []
    for b in brolls or []:
        try:
            os_ = map_to_output(float(b["start"]), keep)
            oe_ = map_to_output(float(b["end"]), keep)
        except (KeyError, TypeError, ValueError):
            continue
        if oe_ - os_ < min_dur:
            oe_ = os_ + min_dur
        out.append({"image": b.get("image", ""), "out_start": os_, "out_end": round(oe_, 3)})
    return [b for b in out if b["image"]]


def _run_cwd(cmd: list, timeout: int, cwd: str | None = None) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        if p.returncode != 0:
            return False, (p.stderr or "")[-800:]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out"
    except Exception as exc:
        return False, str(exc)


def render_edit(src_path: str, plan: dict, ass_text: str, brolls: list, out_path: str,
                *, fps: int = 30, on_progress=None, timeout: int = 900) -> tuple[bool, str]:
    """Two-pass render:
      1. trim to keep-segments (select/aselect + compacting setpts) + center-crop
         and scale to vertical output size.
      2. overlay B-roll stills during their windows + burn the ASS captions.
    Returns (ok, error). Never raises.
    """
    import shutil
    import tempfile
    from core.video_assembly import ffmpeg_exe

    exe = ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    keep = plan.get("keep") or []
    if not keep:
        return False, "nothing to keep (empty edit plan)"

    def beat():
        if on_progress:
            try:
                on_progress()
            except Exception:
                pass

    w, h = output_size()
    ar = f"({w}/{h})"
    work = tempfile.mkdtemp(prefix="edit_")
    try:
        # ── Pass 1: trim + vertical ──────────────────────────────────────────
        sel = _select_expr(keep)
        vf = (f"select='{sel}',setpts=N/FRAME_RATE/TB,"
              f"crop='min(iw,ih*{ar})':'min(ih,iw/{ar})',scale={w}:{h},setsar=1")
        af = f"aselect='{sel}',asetpts=N/SR/TB"
        edited = os.path.join(work, "edited.mp4")
        cmd1 = [exe, "-y", "-i", src_path, "-vf", vf, "-af", af, "-r", str(fps),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-movflags", "+faststart", edited]
        ok, err = _run_cwd(cmd1, timeout)
        if not ok or not os.path.exists(edited):
            return False, f"trim/vertical pass failed: {err[:300]}"
        beat()

        # ── Pass 2: B-roll overlay + burn captions ───────────────────────────
        # Font + subtitles referenced by basename with cwd=work (no path escaping).
        font_path, family = font_file_and_family()
        if font_path:
            try:
                shutil.copy(font_path, os.path.join(work, os.path.basename(font_path)))
            except OSError:
                pass
        ass_text = ass_text.replace("Fontname,", "Fontname,")  # no-op guard
        with open(os.path.join(work, "subs.ass"), "w", encoding="utf-8") as f:
            f.write(ass_text)

        rb = remap_brolls(brolls or [], keep)
        inputs = ["-i", edited]
        for b in rb:
            inputs += ["-loop", "1", "-i", b["image"]]
        parts, prev = [], "0:v"
        for i, b in enumerate(rb, start=1):
            parts.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                         f"crop={w}:{h},setsar=1[b{i}]")
            parts.append(f"[{prev}][b{i}]overlay=0:0:enable='between(t,{b['out_start']},{b['out_end']})'[v{i}]")
            prev = f"v{i}"
        parts.append(f"[{prev}]ass=subs.ass:fontsdir=.[vout]")
        fc = ";".join(parts)
        cmd2 = [exe, "-y", *inputs, "-filter_complex", fc,
                "-map", "[vout]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", "-movflags", "+faststart", out_path]
        ok, err = _run_cwd(cmd2, timeout, cwd=work)
        if not ok or not os.path.exists(out_path):
            return False, f"overlay/caption pass failed: {err[:300]}"
        beat()
        return True, ""
    except Exception as exc:
        return False, f"render error: {exc}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── Full pipeline: source video → transcribe → trim → B-roll → captions → store

def max_seconds() -> int:
    return max(5, int(_f("EDIT_MAX_SECONDS", 60)))


def max_broll() -> int:
    return max(0, int(_f("EDIT_MAX_BROLL", 4)))


def _json(raw: str):
    m = re.search(r"\{.*\}", str(raw or ""), re.DOTALL)
    if not m:
        return {}
    try:
        import json
        return json.loads(m.group(0))
    except Exception:
        return {}


def _in_keep(t: float, keep: list) -> bool:
    return any(s <= t <= e for s, e in keep)


def _broll_cues(words: list, keep: list, *, limit: int) -> list:
    """One LLM pass → up to `limit` B-roll cues {start, end, image_prompt}, in
    ORIGINAL time, chosen from the kept transcript."""
    if limit <= 0:
        return []
    from core.brain import _call_llm_cluster
    lines, buf, buf_start = [], [], None
    for w in words:
        try:
            s = float(w["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if not _in_keep(s, keep):
            continue
        if buf_start is None:
            buf_start = s
        buf.append(str(w.get("word", "")).strip())
        if len(buf) >= 8:
            lines.append(f"[{buf_start:.1f}s] " + " ".join(buf))
            buf, buf_start = [], None
    if buf:
        lines.append(f"[{buf_start:.1f}s] " + " ".join(buf))
    if not lines:
        return []
    system = (
        "You pick B-roll moments for a short talking-head video. Given a timestamped "
        f"transcript, choose UP TO {limit} moments where a single AI-generated image would "
        "strengthen the point (spread them out, ~1 per 8-10s; fewer is fine). For each give the "
        "start second copied from the transcript and a concise, vivid image prompt — concrete, "
        "photographic, NO text/words in the image. Respond with ONLY JSON: "
        '{"cues":[{"at": 12.3, "prompt": "..."}]}'
    )
    try:
        raw, _ = _call_llm_cluster(
            [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}],
            timeout=30)
    except Exception as exc:
        print(f"[EDIT] b-roll cue LLM failed: {exc}")
        return []
    data = _json(raw)
    cues = []
    for c in (data.get("cues") if isinstance(data, dict) else []) or []:
        try:
            at = float(c["at"])
            pr = str(c["prompt"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if pr and _in_keep(at, keep):
            cues.append({"start": at, "end": at + 2.5, "image_prompt": pr})
    return cues[:limit]


def _download_to(url: str, dest: str) -> bool:
    try:
        if str(url).startswith("http"):
            import requests
            r = requests.get(url, timeout=60)
            if r.status_code != 200 or not r.content:
                return False
            data = r.content
        else:
            from core.media_manager import fetch_media_bytes
            data = fetch_media_bytes(url)
            if not data:
                return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def _gen_broll(cues: list, workdir: str) -> list:
    """Generate a B-roll image per cue (free provider by default), downloaded to
    a local file for ffmpeg overlay."""
    import core.provider_manager as pm
    provider = os.getenv("EDIT_BROLL_PROVIDER", "Pollinations")
    out = []
    for i, c in enumerate(cues):
        try:
            r = pm.get_manager().execute(pm.Capability.IMAGE, prompt=c["image_prompt"],
                                         prefer=provider, enhance=False)
            if not getattr(r, "success", False):
                continue
            url = (r.data or {}).get("image_url", "")
            dest = os.path.join(workdir, f"broll_{i}.png")
            if url and _download_to(url, dest):
                out.append({"image": dest, "start": c["start"], "end": c["end"]})
        except Exception as exc:
            print(f"[EDIT] b-roll image {i} failed: {exc}")
    return out


def overlay_sticker(video_path: str, sticker_path: str, out_path: str, *,
                    position: str = "br", scale: float = 0.28, start: float = 0.0,
                    end: float | None = None, timeout: int = 600) -> tuple[bool, str]:
    """Overlay a transparent sticker (PNG/alpha) onto a video at a corner/center
    for an optional time window. Alpha is preserved by the overlay filter."""
    from core.video_assembly import ffmpeg_exe, probe_params
    exe = ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    p = probe_params(video_path)
    w = int(p.get("width", 1280) or 1280)
    sw = max(48, int(w * max(0.05, min(0.9, scale))))
    m = max(8, int(w * 0.03))
    pos = {
        "br": (f"main_w-overlay_w-{m}", f"main_h-overlay_h-{m}"),
        "bl": (f"{m}", f"main_h-overlay_h-{m}"),
        "tr": (f"main_w-overlay_w-{m}", f"{m}"),
        "tl": (f"{m}", f"{m}"),
        "center": ("(main_w-overlay_w)/2", "(main_h-overlay_h)/2"),
    }.get(position, (f"main_w-overlay_w-{m}", f"main_h-overlay_h-{m}"))
    enable = ""
    if end is not None:
        enable = f":enable='between(t,{start},{end})'"
    elif start > 0:
        enable = f":enable='gte(t,{start})'"
    # The sticker is a single still image; overlay's default eof_action=repeat
    # holds it for the whole clip. (Avoid -loop 1 + -shortest — that makes the
    # image an infinite input and hangs the encode.) Output length is bounded by
    # the base video [0:v].
    fc = f"[1:v]scale={sw}:-1[st];[0:v][st]overlay={pos[0]}:{pos[1]}{enable}[v]"
    cmd = [exe, "-y", "-i", video_path, "-i", sticker_path,
           "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-movflags", "+faststart", out_path]
    return _run_cwd(cmd, timeout)


def run_sticker_apply(user_id: str, source: str, sticker_url: str, *,
                      position: str = "br", on_progress=None) -> dict:
    """Apply a sticker to a video and store the result. {video_url} or {error}."""
    import shutil
    import tempfile
    from core.media_manager import get_media_manager, fetch_media_bytes

    def beat():
        if on_progress:
            try:
                on_progress()
            except Exception:
                pass

    work = tempfile.mkdtemp(prefix="sticker_")
    try:
        srcfile = os.path.join(work, "src.mp4")
        if os.path.exists(source):
            shutil.copy(source, srcfile)
        else:
            data = fetch_media_bytes(source)
            if not data:
                return {"error": "could not fetch the video"}
            with open(srcfile, "wb") as f:
                f.write(data)
        stf = os.path.join(work, "sticker.png")
        if not _download_to(sticker_url, stf):
            return {"error": "could not fetch the sticker"}
        beat()
        out = os.path.join(work, "out.mp4")
        ok, err = overlay_sticker(srcfile, stf, out, position=position)
        if not ok or not os.path.exists(out):
            return {"error": err or "sticker overlay failed"}
        beat()
        rec = get_media_manager(user_id).save_media(
            out, media_type="video", prompt="Sticker overlay", provider="StickerApply",
            chat_id=f"edit_{user_id}")
        if not rec:
            return {"error": "overlaid the sticker but could not store the video"}
        return {"video_url": rec["local_path"]}
    except Exception as exc:
        return {"error": f"sticker apply error: {exc}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_autoedit(user_id: str, source: str, *, on_progress=None, test_mode: bool = False) -> dict:
    """Full Massive-Edit pipeline for one source video. Returns
    {"video_url", "stats"} or {"error"}. Never raises. B-roll is skipped in
    test_mode (keeps a dry run free + fast)."""
    import shutil
    import tempfile
    from core.media_manager import get_media_manager, fetch_media_bytes
    from core.video_assembly import ffmpeg_exe, _probe_duration
    import core.transcription as tr

    exe = ffmpeg_exe()
    if not exe:
        return {"error": "ffmpeg not available"}

    def beat():
        if on_progress:
            try:
                on_progress()
            except Exception:
                pass

    work = tempfile.mkdtemp(prefix="autoedit_")
    try:
        # 1. Resolve source to a local file.
        srcfile = os.path.join(work, "source.mp4")
        if os.path.exists(source):
            shutil.copy(source, srcfile)
        else:
            data = fetch_media_bytes(source)
            if not data:
                return {"error": "could not fetch source video"}
            with open(srcfile, "wb") as f:
                f.write(data)
        beat()

        # 2. Cap length: stream-copy trim to the first max_seconds (cheap, no re-encode).
        dur = _probe_duration(exe, srcfile)[1]
        work_src = srcfile
        if dur and dur > max_seconds() + 0.5:
            capped = os.path.join(work, "capped.mp4")
            ok, _ = _run_cwd([exe, "-y", "-i", srcfile, "-t", str(max_seconds()),
                              "-c", "copy", "-movflags", "+faststart", capped], 180)
            if ok and os.path.exists(capped):
                work_src = capped
        beat()

        # 3. Transcribe.
        res = tr.transcribe(work_src)
        if res.get("error"):
            return {"error": "transcription: " + res["error"]}
        words = res["words"]
        beat()

        # 4. Edit plan (trim silences/fillers).
        plan = build_edit_plan(words)
        if not plan["keep"] or plan["total_out"] < 1.0:
            return {"error": "nothing usable to keep after trimming"}

        # 5. B-roll (skipped in test mode).
        brolls = []
        if not test_mode and max_broll() > 0:
            brolls = _gen_broll(_broll_cues(words, plan["keep"], limit=max_broll()), work)
        beat()

        # 6. Captions + render.
        _, family = font_file_and_family()
        w, h = output_size()
        ass = build_ass(plan["kept_words"], fontname=family, play_w=w, play_h=h)
        out = os.path.join(work, "final.mp4")
        ok, err = render_edit(work_src, plan, ass, brolls, out, on_progress=on_progress)
        if not ok:
            return {"error": err}
        beat()

        # 7. Store (R2 via MediaManager).
        rec = get_media_manager(user_id).save_media(
            out, media_type="video", prompt="Massive Edit", provider="MassiveEdit",
            chat_id=f"edit_{user_id}")
        if not rec:
            return {"error": "rendered but could not store the video"}
        return {"video_url": rec["local_path"], "stats": {
            "source_seconds": round(dur or 0.0, 1),
            "output_seconds": plan["total_out"],
            "removed_words": plan["removed"],
            "brolls": len(brolls),
            "caption_words": len(plan["kept_words"]),
        }}
    except Exception as exc:
        return {"error": f"auto-edit error: {exc}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
