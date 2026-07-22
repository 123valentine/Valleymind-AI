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
