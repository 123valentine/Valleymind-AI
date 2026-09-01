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


def _cue_lines(words: list, keep: list, width: int = 8) -> list:
    """Group kept transcript words into timestamped lines for an LLM to cue on.
    Each line: ``[12.3s] some words...`` — timestamps are ORIGINAL time."""
    lines, buf, buf_start = [], [], None
    for w in words or []:
        try:
            s = float(w["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if not _in_keep(s, keep):
            continue
        if buf_start is None:
            buf_start = s
        buf.append(str(w.get("word", "")).strip())
        if len(buf) >= width:
            lines.append(f"[{buf_start:.1f}s] " + " ".join(buf))
            buf, buf_start = [], None
    if buf:
        lines.append(f"[{buf_start:.1f}s] " + " ".join(buf))
    return lines


def _broll_cues(words: list, keep: list, *, limit: int) -> list:
    """One LLM pass → up to `limit` B-roll cues {start, end, image_prompt}, in
    ORIGINAL time, chosen from the kept transcript."""
    if limit <= 0:
        return []
    from core.brain import _call_llm_cluster
    lines = _cue_lines(words, keep)
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
                    end: float | None = None, fade_in: float = 0.0,
                    timeout: int = 600) -> tuple[bool, str]:
    """Overlay a transparent sticker (PNG/alpha) onto a video at a corner/center
    for an optional time window. Alpha is preserved by the overlay filter. When
    ``fade_in > 0`` the sticker "pops in" — its alpha ramps 0→1 over that many
    seconds from the window start (an entrance animation, e.g. for celebrations).

    Implementation note: the single-image input is a one-frame stream, so a fade
    cannot manipulate it directly (no intermediate frames). We therefore loop it
    once into a real frame stream (\\.-loop 1\\.) before fading when an entrance
    animation is requested; the overlay still ends when the base video ends, so
    nothing hangs."""
    from core.video_assembly import ffmpeg_exe, probe_params, _probe_duration
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
    fade_in = max(0.0, float(fade_in))
    if fade_in > 0:
        dur = 0.0
        try:
            _, dur = _probe_duration(exe, video_path)
        except Exception:
            dur = 0.0
        cmd = [exe, "-y", "-i", video_path]
        if dur and dur > 0:
            cmd += ["-loop", "1", "-t", str(dur + 0.5), "-i", sticker_path]
        else:
            cmd += ["-loop", "1", "-i", sticker_path]
        fc = (f"[1:v]scale={sw}:-1,fade=t=in:st={start}:d={fade_in}:alpha=1[st];"
              f"[0:v][st]overlay={pos[0]}:{pos[1]}{enable}[v]")
    else:
        cmd = [exe, "-y", "-i", video_path, "-i", sticker_path]
        fc = f"[1:v]scale={sw}:-1[st];[0:v][st]overlay={pos[0]}:{pos[1]}{enable}[v]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
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
        # Normalize the sticker to a flat RGBA PNG first frame. WhatsApp .webp
        # stickers are often ANIMATED, which ffmpeg's overlay filter cannot decode
        # ("Invalid data found when processing input"). Flattening the first frame
        # keeps alpha and lets the proven PNG overlay path handle any sticker
        # format (webp/gif/png). Best-effort: if it's already a usable PNG this is
        # a harmless re-save; if PIL can't read it, the overlay errors cleanly.
        try:
            from PIL import Image
            with Image.open(stf) as _sticker_img:
                try:
                    _sticker_img.seek(0)
                except (EOFError, ValueError):
                    pass
                _sticker_img.convert("RGBA").save(stf, "PNG")
        except Exception as _norm_exc:
            print(f"[STICKER] normalize skipped for {sticker_url}: {_norm_exc}")
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


# ── Instruction-driven edit planning ──────────────────────────────────────
# The user's typed / voice instructions are treated as the PRIMARY editing
# brief. An LLM pass turns them into a concrete editing plan (trim / slow-mo /
# sticker windows / captions / music), then the render chain executes it below.

_DEFAULT_PLAN_STEPS = [
    "Transcribe the clip",
    "Trim silences and filler words",
    "Add animated captions",
    "Add B-roll shots",
    "Render the vertical short",
]


def _plan_steps(status: str = "planned") -> list:
    return [{"step": s, "status": status} for s in _DEFAULT_PLAN_STEPS]


def build_instruction_plan(*, instruction: str = "", voice_transcript: str = "",
                           words: list, keep: list, media_assets: list | None = None,
                           sticker: dict | None = None) -> dict:
    """Translate the user's instruction into a concrete editing plan.

    Returns a dict the render chain understands::
      {
        "steps":      [{"step": str, "status": str}, ...],     # for the UI
        "captions":   bool,
        "music":      bool,                                      # use uploaded audio
        "broll":      {"use_uploads": [index...], "windows": [{"at": sec}]},
        "slow_motion":{"start": float|None, "end": float|None, "factor": float},
        "sticker_windows": [{"at": sec, "duration": float, "position": str, "fade": float}],
      }
    All timestamps are in ORIGINAL timeline seconds (they are mapped onto the
    trimmed output timeline downstream). Never raises — falls back to the
    default plan on any failure so an instruction can never break an edit.
    """
    from core.brain import _call_llm_cluster

    lines = _cue_lines(words, keep)
    brief = "\n".join(p for p in (instruction, voice_transcript) if str(p).strip())

    media_note = ""
    images_note = "none"
    audio_note = "none"
    for i, a in enumerate(media_assets or []):
        kind = str(a.get("type", "")).strip()
        name = str(a.get("name", "") or "file").strip()
        if kind == "image":
            images_note = f"{name}#{i}"
        elif kind == "audio":
            audio_note = f"{name}#{i}"
    if (media_assets or []) and images_note == "none" and audio_note == "none":
        media_note = f"{len(media_assets)} additional asset(s) attached."

    sticker_note = "none"
    if sticker and sticker.get("url"):
        sn = str(sticker.get("name") or "sticker").strip()
        sticker_note = f"{sn} (kind: {sticker.get('kind', 'sticker')})"

    system = (
        "You are the editing brain of 'Massive Editing'. You read a user's editing "
        "instruction plus a timestamped transcript of the source footage, and you return "
        "the concrete edit plan as JSON ONLY. Do not describe — return machine-readable JSON.\n"
        "\nRules:\n"
        "- Timestamps in 'at', 'start', 'end' MUST be copied (as a number) from the transcript "
        "lines given. Never invent seconds that are not on a line.\n"
        "- If the user names a sticker (e.g. 'the fire sticker', 'the crown sticker'), or a "
        "sticker is selected, set sticker_use=true and put a sticker window at the moment it "
        "should appear (goal celebration, beginning for ~3s, etc.). Choose the position the "
        "user implied; default 'br'. Set 'fade' to 0.5 when the user says pop in / entrance / "
        "animate. If no natural moment exists, place it at the first transcript time for ~3s.\n"
        "- If the user wants slow motion (e.g. 'slow motion to the goal'), set slow_motion with "
        "a start/end copy from nearby transcript lines.\n"
        "- If the user uploaded images and says to use them (as B-roll/insets), list their "
        "indices under broll.use_uploads and give each an 'at' from the transcript.\n"
        "- If the user wants music ('energetic music', 'add music') and audio was uploaded, set "
        "music=true.\n"
        "- captions default true; false only when the user says no captions/text.\n"
        "- steps: 3-8 short, human-readable operations that become the visible edit plan.\n"
        "\nRespond with ONLY this shape:\n"
        '{"steps":["..."],"captions":true,"music":false,'
        '"broll":{"use_uploads":[0],"windows":[{"at":12.3}]},'
        '"slow_motion":{"start":null,"end":null,"factor":2.0},'
        '"sticker_use":true,"sticker_windows":[{"at":12.3,"duration":3.0,"position":"tr","fade":0.0}],'
        '"note":"optional"}\n'
        'Use null start/end for slow_motion when not applying it, and an empty list for '
        'sticker_windows / broll.windows / broll.use_uploads when not used.'
    )
    user = (
        ("USER INSTRUCTION:\n" + brief + "\n\n") if brief else ""
        + (f"TRANSCRIPT (original-time lines):\n" + "\n".join(lines[:60]) + "\n\n" if lines
           else "TRANSCRIPT: (empty)\n\n")
        + f"UPLOADED MEDIA: {media_note or 'none'}\n"
        + f"  images: {images_note}\n  audio: {audio_note}\n"
        + f"SELECTED STICKER: {sticker_note}"
    )
    try:
        raw, _ = _call_llm_cluster(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            timeout=40)
        data = _json(raw)
    except Exception as exc:
        print(f"[EDIT] instruction plan LLM failed: {exc}")
        data = {}

    def _fnum(v, default=None):
        try:
            if v is None or str(v).strip() == "":
                return default
            return round(float(v), 3) if isinstance(v, (int, float)) else round(float(str(v)), 3)
        except (TypeError, ValueError):
            return default

    steps = data.get("steps") if isinstance(data, dict) else []
    if not isinstance(steps, list) or not steps or not all(str(s).strip() for s in steps):
        steps = list(_DEFAULT_PLAN_STEPS)

    sw = []
    max_t = float(keep[-1][1]) if isinstance(keep, list) and keep else 0.0
    for w in (data.get("sticker_windows") if isinstance(data, dict) else []) or []:
        at = _fnum(w.get("at"))
        if at is None:
            continue
        at = max(0.0, min(at, max_t))
        sw.append({
            "at": at,
            "duration": max(1.0, float(_fnum(w.get("duration"), 3.0) or 3.0)),
            "position": str(w.get("position") or "br") if str(w.get("position") or "br").lower()
                        in ("br", "bl", "tr", "tl", "center") else "br",
            "fade": max(0.0, float(_fnum(w.get("fade"), 0.0) or 0.0)),
        })
    # Selection as an authoritative signal: a selected sticker is used even when
    # the LLM omits windows (fallback: first transcript moment, ~3s).
    if sticker and sticker.get("url") and not sw:
        first_at = _fnum((words[0].get("start") if words else None), 0.0) if words else 0.0
        sw = [{"at": max(0.0, min(first_at, max_t)), "duration": 3.0,
               "position": str(sticker.get("position") or "br"), "fade": 0.5}]

    sm = data.get("slow_motion") if isinstance(data, dict) else {}
    slow = {
        "start": _fnum(sm.get("start")) if isinstance(sm, dict) else None,
        "end": _fnum(sm.get("end")) if isinstance(sm, dict) else None,
        "factor": max(1.2, min(4.0, float(_fnum(sm.get("factor"), 2.0) or 2.0)))
        if isinstance(sm, dict) else 2.0,
    }
    if slow["start"] is not None:
        slow["start"] = max(0.0, min(slow["start"], max_t))
    if slow["end"] is not None:
        slow["end"] = max(slow["start"] or 0.0, min(slow["end"], max_t))
    if slow["start"] is None or slow["end"] is None or slow["end"] - (slow["start"] or 0.0) < 0.4:
        slow = {"start": None, "end": None, "factor": 2.0}

    bw = data.get("broll") if isinstance(data, dict) else {}
    use_uploads = []
    if isinstance(bw, dict):
        use_uploads = [int(i) for i in (bw.get("use_uploads") or [])
                       if str(i).isdigit()]
        if isinstance(bw.get("windows"), list):
            pass  # windows are original-time; keep line times via cues below

    plan = {
        "steps": [{"step": str(s).strip(), "status": "planned"} for s in steps],
        "captions": bool(data.get("captions", True)),
        "music": bool(data.get("music", False)),
        "broll": {"use_uploads": use_uploads},
        "slow_motion": {
            "start": slow["start"], "end": slow["end"], "factor": slow["factor"],
        },
        "sticker_windows": sw,
    }
    return plan


def map_plan_to_output(plan: dict, keep: list) -> dict:
    """Map original-time window markers in a plan onto the trimmed OUTPUT
    timeline using the edit plan's keep segments."""
    out = dict(plan)
    sm = dict(plan.get("slow_motion") or {})
    if sm.get("start") is not None and sm.get("end") is not None:
        s = map_to_output(float(sm["start"]), keep)
        e = map_to_output(float(sm["end"]), keep)
        if e - s < 0.4:
            e = min(s + 2.0, plan.get("total_out") or s + 2.0)
        sm["start"] = round(s, 3)
        sm["end"] = round(e, 3)
    out["slow_motion"] = sm
    if plan.get("total_out") is not None:
        out["total_out"] = plan["total_out"] or 0.0
    return out


def apply_slowmo(src_path: str, out_path: str, *, start: float, end: float,
                 factor: float = 2.0, timeout: int = 900) -> tuple[bool, str]:
    """Apply slow-motion to the [start, end) window only. The window is slowed
    by ``factor`` (video + audio), keeping pitch via atempo; the rest of the
    clip is untouched. Output duration grows by (end-start)*(factor-1)."""
    from core.video_assembly import ffmpeg_exe
    exe = ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    S = max(0.0, float(start))
    E = max(S + 0.1, float(end))
    F = max(1.1, min(4.0, float(factor)))
    ext = (E - S) * (F - 1.0)
    new_end = E + ext
    vf = (
        f"[0:v]split=3[v0][v1][v2];"
        f"[v0]trim=start=0:end={S},setpts=PTS[p0];"
        f"[v1]trim=start={S}:end={E},setpts=PTS-START/TB,setpts=PTS*{F},setpts=PTS+{S}/TB[p1];"
        f"[v2]trim=start={E},setpts=PTS-START/TB,setpts=PTS+{new_end}/TB[p2];"
        f"[p0][p1][p2]concat=n=3:v=1:a=0[vout]"
    )
    af = (
        f"[0:a]asplit=3[a0][a1][a2];"
        f"[a0]atrim=start=0:end={S},asetpts=PTS[a0o];"
        f"[a1]atrim=start={S}:end={E},asetpts=PTS-START/TB,atempo={1.0 / F},asetpts=PTS+{S}/TB[a1o];"
        f"[a2]atrim=start={E},asetpts=PTS-START/TB,asetpts=PTS+{new_end}/TB[a2o];"
        f"[a0o][a1o][a2o]concat=n=3:v=0:a=1,aresample=44100[aout]"
    )
    cmd = [exe, "-y", "-i", src_path, "-filter_complex", vf + ";" + af,
           "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "44100", "-movflags", "+faststart", out_path]
    return _run_cwd(cmd, timeout)


def apply_music(video_path: str, music_path: str, out_path: str, *,
                volume: float = 0.3, timeout: int = 900) -> tuple[bool, str]:
    """Mix an uploaded music/SFX track under the video's own audio, trimmed to
    the video length. Real audio mix (amix), no placeholder."""
    from core.video_assembly import ffmpeg_exe
    exe = ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    vol = max(0.0, min(1.0, float(volume)))
    fc = (
        f"[1:a]volume={vol},aresample=44100[m];"
        f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
        f"alimiter=limit=0.97[aout]"
    )
    cmd = [exe, "-y", "-i", video_path, "-i", music_path,
           "-filter_complex", fc,
           "-map", "0:v", "-map", "[aout]",
           "-c:v", "copy", "-c:a", "aac", "-ar", "44100",
           "-movflags", "+faststart", out_path]
    return _run_cwd(cmd, timeout)


def run_autoedit(user_id: str, source: str, *, on_progress=None, test_mode: bool = False,
                 instruction: str = "", voice_transcript: str = "",
                 media_assets: list | None = None, sticker: dict | None = None,
                 on_plan=None) -> dict:
    """Full Massive-Edit pipeline for one source video. The user's typed/voice
    instruction (when provided) is parsed into a concrete editing plan that this
    pipeline then executes — trimming, captions, B-roll from uploaded images,
    slow-motion windows, selected-sticker overlays and an uploaded-music mix.
    Returns {"video_url", "stats"} or {"error"}. Never raises. B-roll is
    skipped in test_mode (keeps a dry run free + fast)."""
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

        # 5. Translate the user's instruction into an editing brief (LLM). The
        # completed plan is surfaced through on_plan so the job can publish it
        # BEFORE any heavy rendering — the visible "AI Edit Plan" checklist.
        have_brief = bool(str(instruction or "").strip() or str(voice_transcript or "").strip())
        user_plan = None
        if have_brief:
            user_plan = build_instruction_plan(
                instruction=instruction, voice_transcript=voice_transcript,
                words=words, keep=plan["keep"], media_assets=media_assets,
                sticker=sticker)
        else:
            user_plan = {
                "steps": [{"step": s, "status": "planned"} for s in _DEFAULT_PLAN_STEPS],
                "captions": True, "music": bool(
                    any(str(a.get("type", "")).lower() == "audio" for a in (media_assets or []))),
                "broll": {"use_uploads": []},
                "slow_motion": {"start": None, "end": None, "factor": 2.0},
                "sticker_windows": [],
            }
        user_plan["total_out"] = plan["total_out"] or 0.0
        if on_plan:
            try:
                on_plan({"plan": user_plan["steps"], "stage": "planned"})
            except Exception:
                pass

        # 6. B-roll: prefer the user's OWN uploaded images when the instruction
        # names them (mapped to the trimmed timeline); otherwise the classic AI
        # B-roll cues. Skipped in test mode.
        brolls = []
        upload_images = [a for a in (media_assets or [])
                         if str(a.get("type", "")).lower() == "image" and a.get("url")]
        if not test_mode and max_broll() > 0:
            if user_plan.get("broll", {}).get("use_uploads") and upload_images:
                for idx in user_plan["broll"].get("use_uploads", []):
                    if idx >= len(upload_images):
                        continue
                    img = upload_images[idx]
                    dest = os.path.join(work, f"upimg_{len(brolls)}.png")
                    if not _download_to(str(img.get("url", "")), dest):
                        continue
                    if os.path.exists(dest):
                        # One ~2.5s window per named upload, timed off the kept
                        # transcript (first kept moment, staggered by index).
                        ats = [s for s, _e in plan["keep"]]
                        at = ats[min(idx, len(ats) - 1)] if ats else 0.0
                        out_s = map_to_output(at, plan["keep"])
                        brolls.append({"image": dest, "start": at,
                                       "end": min(at + 2.5, plan["keep"][-1][1])})
                        if on_plan:
                            try:
                                on_plan({"plan": user_plan["steps"],
                                         "stage": "broll",
                                         "message": f"Using your uploaded image #{idx + 1} as B-roll."})
                            except Exception:
                                pass
            else:
                brolls = _gen_broll(_broll_cues(words, plan["keep"], limit=max_broll()), work)
        beat()

        # 7. Captions + render.
        _, family = font_file_and_family()
        w, h = output_size()
        ass = build_ass(
            plan["kept_words"] if user_plan.get("captions", True) else [],
            fontname=family, play_w=w, play_h=h)
        out = os.path.join(work, "final.mp4")
        ok, err = render_edit(work_src, plan, ass, brolls, out, on_progress=on_progress)
        if not ok:
            return {"error": err}
        beat()

        # 8. Sticker overlay: a selected/uploaded sticker is a REAL editing asset.
        # Windows from the plan are mapped to the trimmed output timeline and
        # applied as overlays (position + optional pop-in fade).
        sticker_applied = 0
        if sticker and sticker.get("url") and user_plan.get("sticker_windows"):
            stf = os.path.join(work, "plan_sticker.png")
            if _download_to(sticker["url"], stf):
                try:
                    from PIL import Image
                    with Image.open(stf) as _si:
                        try:
                            _si.seek(0)
                        except (EOFError, ValueError):
                            pass
                        _si.convert("RGBA").save(stf, "PNG")
                except Exception as _ne:
                    print(f"[EDIT] sticker normalize skipped: {_ne}")
                # Multiple windows: sequential passes, each on the previous output.
                cur = out
                for i, sw_ in enumerate(user_plan.get("sticker_windows", [])):
                    out_s = map_to_output(float(sw_["at"]), plan["keep"])
                    out_e = round(out_s + float(sw_.get("duration", 3.0)), 3)
                    tmp = os.path.join(work, f"stickered_{i}.mp4")
                    ok2, err2 = overlay_sticker(
                        cur, stf, tmp,
                        position=str(sw_.get("position") or "br"),
                        start=out_s, end=out_e,
                        fade_in=float(sw_.get("fade", 0.0) or 0.0))
                    if ok2 and os.path.exists(tmp):
                        cur = tmp
                        sticker_applied += 1
                    if on_plan:
                        try:
                            on_plan({"plan": user_plan["steps"],
                                     "stage": "sticker",
                                     "message": f"Placed the {sticker.get('name') or 'sticker'} "
                                                f"sticker at {out_s:.1f}s ({sw_.get('position')})."})
                        except Exception:
                            pass
                if cur != out and os.path.abspath(cur) != os.path.abspath(out):
                    os.replace(cur, out)
        if sticker_applied == 0 and sticker and sticker.get("url") and user_plan.get("sticker_windows"):
            pass  # sticker selected but couldn't be fetched/normalized — non-fatal
        beat()

        # 9. Slow-motion window (real per-window ffmpeg slow-mo), if planned.
        sm = user_plan.get("slow_motion") or {}
        slow_applied = False
        if sm.get("start") is not None and sm.get("end") is not None:
            os_ = float(sm["start"]); oe_ = float(sm["end"])
            sm_out = os.path.join(work, "slowmo.mp4")
            ok3, err3 = apply_slowmo(out, sm_out, start=os_, end=oe_,
                                     factor=float(sm.get("factor", 2.0)))
            if ok3 and os.path.exists(sm_out):
                out = sm_out
                slow_applied = True
            if on_plan:
                try:
                    on_plan({"plan": user_plan["steps"], "stage": "slow-motion",
                             "message": f"Applied {(sm.get('factor') or 2.0)}x slow-motion "
                                        f"to {os_:.1f}s–{oe_:.1f}s."
                                        if slow_applied else
                                        "Slow-motion requested but the target window could not be applied."})
                except Exception:
                    pass
        beat()

        # 10. Music mix: upload an audio track + ask for music → it's really mixed.
        music_applied = False
        if user_plan.get("music"):
            upload_audio = next((a for a in (media_assets or [])
                                 if str(a.get("type", "")).lower() == "audio" and a.get("url")), None)
            if upload_audio:
                mf = os.path.join(work, "plan_music_mixed.mp4")
                mpath = os.path.join(work, "plan_music.bin")
                got = False
                if os.path.exists(upload_audio["url"]):
                    shutil.copy(upload_audio["url"], mpath)
                    got = True
                else:
                    data = fetch_media_bytes(upload_audio["url"])
                    if data:
                        with open(mpath, "wb") as f:
                            f.write(data)
                        got = True
                if got:
                    ok4, err4 = apply_music(out, mpath, mf)
                    if ok4 and os.path.exists(mf):
                        out = mf
                        music_applied = True
                if on_plan:
                    try:
                        on_plan({"plan": user_plan["steps"], "stage": "music",
                                 "message": f"Mixed {upload_audio.get('name') or 'music'} "
                                            "under the edit." if music_applied else
                                            "Music requested but the audio track could not be mixed."})
                    except Exception:
                        pass
        beat()

        # 11. Store (R2 via MediaManager).
        rec = get_media_manager(user_id).save_media(
            out, media_type="video", prompt="Massive Edit", provider="MassiveEdit",
            chat_id=f"edit_{user_id}")
        if not rec:
            return {"error": "rendered but could not store the video"}
        if on_plan:
            try:
                on_plan({"plan": user_plan["steps"], "stage": "done"})
            except Exception:
                pass
        return {"video_url": rec["local_path"], "stats": {
            "source_seconds": round(dur or 0.0, 1),
            "output_seconds": plan["total_out"],
            "removed_words": plan["removed"],
            "brolls": len(brolls),
            "caption_words": len(plan["kept_words"]),
            "sticker_applied": sticker_applied,
            "slow_motion": slow_applied,
            "music": music_applied,
            "planned": bool(have_brief),
        }}
    except Exception as exc:
        return {"error": f"auto-edit error: {exc}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
