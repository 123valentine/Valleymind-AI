"""Elena's stage — join storyboard clips into one trailer with ffmpeg.

Two strategies, in order of resource cost:

- hard_cut: concat demuxer with ``-c copy``. ffmpeg streams the packets through
  without decoding, so peak memory stays tiny regardless of clip count/size.
  This is the safe default for a 512MB instance. Requires the clips to share a
  codec/params, which they do — they all come from the same wan i2v model.

- crossfade: xfade + acrossfade filters. This DECODES and RE-ENCODES the whole
  timeline, which is where memory and CPU spike. Used only if explicitly asked
  for and the instance can afford it.

ffmpeg comes from imageio-ffmpeg (a bundled static binary), so no system
package is required on Render.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from pathlib import Path


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")


def available() -> bool:
    return ffmpeg_exe() is not None


def _run(cmd: list[str], timeout: int) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            return False, (p.stderr or "")[-600:]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out"
    except Exception as exc:
        return False, str(exc)


_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _find_font(size: int):
    """A bold sans face on Windows (dev) or Linux (Render); PIL's bitmap default
    as a last resort so a missing font never fails the render."""
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def probe_params(path: str) -> dict:
    """Read a clip's codec params by parsing `ffmpeg -i` (imageio-ffmpeg ships
    ffmpeg but not ffprobe). Title cards must match these exactly or the
    ``-c copy`` concat produces a broken file."""
    import re
    exe = ffmpeg_exe()
    out = {"width": 1280, "height": 720, "fps": 24.0, "has_audio": False}
    if not exe:
        return out
    try:
        p = subprocess.run([exe, "-i", path], capture_output=True, text=True, timeout=60)
        err = p.stderr or ""
    except Exception:
        return out
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", err)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
    if m:
        try:
            out["fps"] = float(m.group(1)) or 24.0
        except ValueError:
            pass
    out["has_audio"] = "Audio:" in err
    return out


def make_title_card(text: str, out_path: str, *, width: int = 1280, height: int = 720,
                    fps: float = 24.0, seconds: float = 1.5, has_audio: bool = False,
                    timeout: int = 120) -> tuple[bool, str]:
    """Render a trailer title card as its OWN short clip.

    Deliberately NOT burned over the footage: overlaying text would force a
    re-encode of the whole timeline (the crossfade path peaked at 737MB). A
    standalone card is encoded once, tiny and alone, then hard-cut into the
    sequence like any other shot — so the join stays a stream copy.
    """
    exe = ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    label = " ".join(str(text or "").split()).upper()
    if not label:
        return False, "empty card text"

    # 1. Draw the card with Pillow (full control, no filter-string escaping).
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (width, height), (8, 10, 14))
        draw = ImageDraw.Draw(img)
        size = max(18, int(height * 0.11))
        font = _find_font(size)
        # Shrink until it fits with margins, then wrap to two lines if needed.
        words, lines = label.split(), []
        max_w = int(width * 0.82)
        while size > 14:
            font = _find_font(size)
            lines, cur = [], ""
            for w in words:
                trial = (cur + " " + w).strip()
                if draw.textlength(trial, font=font) <= max_w or not cur:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
            if len(lines) <= 2 and all(draw.textlength(l, font=font) <= max_w for l in lines):
                break
            size = int(size * 0.85)
        line_h = int(size * 1.25)
        total_h = line_h * len(lines)
        y = (height - total_h) // 2
        for line in lines:
            x = (width - draw.textlength(line, font=font)) // 2
            draw.text((x, y), line, font=font, fill=(240, 240, 235))
            y += line_h
        png = out_path + ".png"
        img.save(png)
    except Exception as exc:
        return False, f"card render failed: {exc}"

    # 2. Encode to a clip matching the footage's params so -c copy concat works.
    cmd = [exe, "-y", "-loop", "1", "-i", png]
    if has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += [
        "-t", str(seconds),
        "-vf", f"scale={width}:{height},format=yuv420p",
        "-r", str(int(round(fps))),
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "90000",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest"]
    cmd += [out_path]
    ok, err = _run(cmd, timeout)
    try:
        os.remove(png)
    except OSError:
        pass
    return (ok and os.path.exists(out_path)), err


def hard_cut(clip_paths: list[str], out_path: str, timeout: int = 300) -> tuple[bool, str]:
    """Concatenate clips end to end with NO re-encoding (streaming copy)."""
    exe = ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    if not clip_paths:
        return False, "no clips to join"

    listfd, listpath = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(listfd, "w", encoding="utf-8") as f:
            for p in clip_paths:
                ap = str(Path(p).resolve()).replace("'", r"'\''")
                f.write(f"file '{ap}'\n")
        cmd = [exe, "-y", "-f", "concat", "-safe", "0", "-i", listpath,
               "-c", "copy", "-movflags", "+faststart", out_path]
        return _run(cmd, timeout)
    finally:
        try:
            os.remove(listpath)
        except OSError:
            pass


def crossfade(clip_paths: list[str], out_path: str, dur: float = 0.5, timeout: int = 600) -> tuple[bool, str]:
    """Join clips with crossfade transitions. RE-ENCODES — heavy on 512MB."""
    exe = ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    n = len(clip_paths)
    if n == 0:
        return False, "no clips to join"
    if n == 1:
        return hard_cut(clip_paths, out_path, timeout)

    # Per-clip durations so each xfade offset is placed correctly
    durs = []
    for p in clip_paths:
        ok, d = _probe_duration(exe, p)
        if not ok:
            return False, f"could not probe {p}: {d}"
        durs.append(d)

    inputs = []
    for p in clip_paths:
        inputs += ["-i", str(Path(p).resolve())]

    # Chain xfade (video) and acrossfade (audio) across all inputs
    vfilters, afilters = [], []
    last_v, last_a = "0:v", "0:a"
    offset = 0.0
    for i in range(1, n):
        offset += durs[i - 1] - dur
        vout = f"v{i}"
        aout = f"a{i}"
        vfilters.append(f"[{last_v}][{i}:v]xfade=transition=fade:duration={dur}:offset={offset:.3f}[{vout}]")
        afilters.append(f"[{last_a}][{i}:a]acrossfade=d={dur}[{aout}]")
        last_v, last_a = vout, aout

    filtergraph = ";".join(vfilters + afilters)
    cmd = [exe, "-y", *inputs, "-filter_complex", filtergraph,
           "-map", f"[{last_v}]", "-map", f"[{last_a}]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    return _run(cmd, timeout)


def _probe_duration(exe: str, path: str) -> tuple[bool, float]:
    """Read a clip's duration via ffmpeg stderr (avoids needing ffprobe)."""
    try:
        p = subprocess.run([exe, "-i", str(Path(path).resolve())],
                           capture_output=True, text=True, timeout=60)
        for line in p.stderr.splitlines():
            if "Duration:" in line:
                ts = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = ts.split(":")
                return True, int(h) * 3600 + int(m) * 60 + float(s)
    except Exception as exc:
        return False, 0.0  # noqa
    return False, 0.0


def assemble(clip_paths: list[str], out_path: str, mode: str = "hard_cut") -> tuple[bool, str]:
    """Join clips using the requested mode, falling back to hard_cut."""
    if mode == "crossfade":
        ok, err = crossfade(clip_paths, out_path)
        if ok:
            return ok, err
        print(f"[ASSEMBLY] crossfade failed ({err[:120]}); falling back to hard cut")
    return hard_cut(clip_paths, out_path)
