"""Template render engine — turn a template.json project into a video.

Pipeline (ffmpeg, via the bundled imageio-ffmpeg static binary):
  per clip:  input (user photo / user video / generated gradient)
             -> cover scale+crop -> zoompan Ken-Burns -> eq grade
             -> vignette -> composited text/sticker overlay -> yuv420p
  then:      xfade transitions between clips
  then:      global overlay bars + music track -> libx264 + aac.

Placeholders are replaced at render time: images/videos/audio are the user's
uploads, text is the user's value. Missing optional media falls back to the
clip's background gradient. Nothing in template.json is written back out — the
template stays a reusable project definition.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from core.config import PROJECT_ROOT
from core.template_library import STATIC_GENERATED, get_template, project_dir, save_project

FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)

XFADE_MAP = {
    "fade": "fade", "slideleft": "slideleft", "slideright": "slideright",
    "wipeup": "wipeup", "wipeleft": "wipeleft", "circleopen": "circleopen",
    "dissolve": "dissolve", "none": "fade",
}

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac")
PAD = 0.06


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def available() -> bool:
    return ffmpeg_exe() is not None


def _fnum(v) -> str:
    s = f"{float(v):.6f}"
    s = s.rstrip("0").rstrip(".")
    return s or "0"


def _font_path(bold: bool) -> str:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return ""


# ── Small helpers ──────────────────────────────────────────────────────────

def _resolve_media(root: Path, rel: str) -> Path | None:
    if not rel:
        return None
    try:
        target = (root / rel).resolve()
        if target.is_relative_to(root.resolve()) and target.is_file():
            return target
    except Exception:
        return None
    return None


def _template_dir(template: dict) -> Path:
    return PROJECT_ROOT / "static" / "templates" / str(template.get("id", ""))


def _hex_to_rgb(c: str) -> tuple:
    c = (c or "").strip().lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (0, 0, 0)


def _parse_rgba(c: str) -> tuple:
    c = (c or "").strip()
    m = re.match(r"rgba?\(([\d.,\s]+)\)", c)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        try:
            r, g, b = (int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
            a = float(parts[3]) * 255 if len(parts) > 3 else 255
            return (r, g, b, int(a))
        except Exception:
            return (0, 0, 0, 0)
    r, g, b = _hex_to_rgb(c)
    return (r, g, b, 255)


# ── PIL canvas helpers ─────────────────────────────────────────────────────

def _gradient_png(bg: dict, w: int, h: int, dest: Path) -> Path:
    from PIL import Image
    if bg.get("type") == "color":
        img = Image.new("RGB", (w, h), _hex_to_rgb(str(bg.get("value", "#000000"))))
    else:
        c0 = _hex_to_rgb(str(bg.get("from", "#0b1020")))
        c1 = _hex_to_rgb(str(bg.get("to", "#1a1140")))
        ang = math.radians(float(bg.get("angle", 160)))
        dx, dy = math.cos(ang), math.sin(ang)
        small = Image.new("RGB", (16, 16))
        px = small.load()
        for y in range(16):
            for x in range(16):
                t = ((x / 15.0) * dx + (y / 15.0) * dy + 1.0) / 2.0
                t = max(0.0, min(1.0, t))
                px[x, y] = tuple(int(c0[i] * (1 - t) + c1[i] * t) for i in range(3))
        img = small.resize((w, h), Image.LANCZOS)
    img.convert("RGB").save(str(dest), format="PNG")
    return dest


def _gradient_bar_png(bar: dict, w: int, h: int, dest: Path) -> Path:
    from PIL import Image
    top = bar.get("position", "bottom") == "top"
    c0 = _parse_rgba(str(bar.get("from", "rgba(0,0,0,0)")))
    c1 = _parse_rgba(str(bar.get("to", "rgba(0,0,0,0.55)")))
    bh = max(4, int(float(bar.get("height", 0.22)) * h))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    y0, y1 = (0, bh) if top else (h - bh, h)
    span = max(1.0, float(y1 - y0))
    for y in range(max(0, y0), min(h, y1)):
        f = (y - y0) / span
        if top:
            f = 1 - f
        row = tuple(int(c0[i] * (1 - f) + c1[i] * f) for i in range(4))
        for x in range(w):
            px[x, y] = row
    img.save(str(dest), format="PNG")
    return dest


def _load_font(bold: bool, size: int):
    from PIL import ImageFont
    path = _font_path(bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap(text: str, font, maxw: int) -> str:
    words = str(text).split()
    lines, cur = [], ""
    for wd in words:
        trial = f"{cur} {wd}".strip()
        try:
            width = font.getlength(trial)
        except Exception:
            width = len(trial) * 9
        if cur and width > maxw:
            lines.append(cur)
            cur = wd
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def _fit_size(text: str, maxw: int, maxh: int, base: int) -> int:
    size = int(base)
    while size > 16:
        font = _load_font(True, size)
        lines = _wrap(text, font, maxw).split("\n")
        width = max((font.getlength(l) for l in lines), default=0)
        height = len(lines) * int(size * 1.25)
        if width <= maxw and height <= maxh:
            return size
        size -= 4
    return size


def _text_canvas(text: str, size: int, color: str, stroke: str, sw: int,
                 w: int, h: int, x: float, y: float, box: bool, align: str):
    from PIL import Image, ImageDraw
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _load_font(True, size)
    maxw = int(w * 0.82)
    lines = _wrap(text, font, maxw).split("\n")
    lh = int(size * 1.22)
    total_h = len(lines) * lh
    y_start = int(y * h) - total_h // 2
    for i, line in enumerate(lines):
        tw = font.getlength(line)
        cx = int(x * w)
        tx = int(cx - tw / 2)
        if align == "left":
            tx = int(w * 0.08)
        elif align == "right":
            tx = int(w * 0.92 - tw)
        ty = y_start + i * lh
        if box:
            bbox = draw.textbbox((tx, ty - int(size * 0.12)), line, font=font)
            pad = int(size * 0.3)
            draw.rounded_rectangle(
                (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad),
                radius=int(size * 0.24), fill=(0, 0, 0, 155))
        draw.text((tx, ty), line, font=font, fill=color,
                  stroke_width=sw, stroke_fill=stroke)
    return canvas


def _sticker_paste(canvas, src_path: str, w: int, h: int, x: float, y: float,
                   scale: float, rotation: float) -> None:
    from PIL import Image
    try:
        st = Image.open(src_path).convert("RGBA")
    except Exception:
        return
    tw = max(10, int(w * float(scale)))
    th = max(10, int(tw * st.height / max(st.width, 1)))
    st = st.resize((tw, th), Image.LANCZOS)
    if rotation:
        st = st.rotate(float(rotation), expand=True, resample=Image.BICUBIC)
    canvas.alpha_composite(st, (int(x * w - st.width / 2), int(y * h - st.height / 2)))


def _compose_overlay(clip: dict, project: dict, w: int, h: int,
                     tdir: Path, dest: Path) -> Path | None:
    from PIL import Image
    texts = clip.get("text_layers") or []
    stickers = clip.get("stickers") or []
    if not texts and not stickers:
        return None
    cw, ch = int(w * (1 + PAD * 2)), int(h * (1 + PAD * 2))
    mx, my = int(w * PAD), int(h * PAD)
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    placeholders = project.get("placeholders") or {}
    for tl in texts:
        key = tl.get("key", "")
        value = str((placeholders.get(key) or {}).get("value", "")).strip()
        if not value:
            continue
        if tl.get("uppercase"):
            value = value.upper()
        maxw = int(w * 0.8)
        size = _fit_size(value, maxw, int(h * 0.24), int(tl.get("size", 72)))
        txt = _text_canvas(
            value, size, tl.get("color", "#ffffff"), tl.get("stroke", "#000000"),
            max(0, int(tl.get("stroke_width", 3))), w, h,
            float(tl.get("x", 0.5)) + mx / w, float(tl.get("y", 0.5)) + my / h,
            bool(tl.get("box", False)), tl.get("align", "center"))
        canvas.alpha_composite(txt, (0, 0))
    for st in stickers:
        src = st.get("src", "")
        if not src:
            continue
        src_path = str(PROJECT_ROOT / src.lstrip("/")) if src.startswith("/static/") else str(tdir / src)
        if not os.path.exists(src_path):
            continue
        _sticker_paste(canvas, src_path, w, h,
                       mx / w + float(st.get("x", 0.5)), my / h + float(st.get("y", 0.5)),
                       float(st.get("scale", 0.2)), float(st.get("rotation", 0)))
    canvas.save(str(dest), format="PNG")
    return dest


# ── Motion / fade / overlay animation expressions ─────────────────────────

def _motion_filter(clip: dict, w: int, h: int, fps: float, dur: float) -> str:
    motion = clip.get("motion") or {"type": "static"}
    mtype = motion.get("type", "static")
    if mtype == "static":
        return ""
    nf = max(1, int(dur * fps))
    if mtype == "pan":
        zoom = float(motion.get("zoom", 1.15))
        dx = float(motion.get("dx", 0.0))
        dy = float(motion.get("dy", 0.0))
        cx = f"(iw-iw/{_fnum(zoom)})/2"
        cy = f"(ih-ih/{_fnum(zoom)})/2"
        x = f"{cx}+{_fnum(dx)}*iw*min(on/{nf},1)"
        y = f"{cy}+{_fnum(dy)}*ih*min(on/{nf},1)"
        z = _fnum(zoom)
    else:
        z0 = float(motion.get("from", 1.0))
        z1 = float(motion.get("to", 1.18))
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2"
        z = f"min({_fnum(z1)},{_fnum(z0)}+({_fnum(z1)}-{_fnum(z0)})*min(on/{nf},1))"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps}"


def _overlay_fade(clip: dict, dur: float) -> str:
    texts = clip.get("text_layers") or []
    stickers = clip.get("stickers") or []
    starts = [float(t.get("start", 0)) for t in texts]
    starts += [float(s.get("start", 0)) for s in stickers]
    st = min(starts) if starts else 0.0
    st = max(0.0, min(st, max(0.0, dur - 0.6)))
    d = max(0.15, min(0.35, dur * 0.15))
    out = max(st + d + 0.1, dur - 0.4)
    return f"fade=t=in:st={_fnum(st)}:d={_fnum(d)}:alpha=1,fade=t=out:st={_fnum(out)}:d=0.3:alpha=1"


# ── Clip chain builder ─────────────────────────────────────────────────────

def _clip_chain(idx: int, clip: dict, project: dict, template: dict,
                root: Path, tmpdir: Path, w: int, h: int, fps: float):
    """Return (inputs, filter_graph_segment, tmp_files)."""
    inputs: list[list] = []
    tmp_files: list[Path] = []
    dur = float(clip.get("duration", 3.0))
    media = clip.get("media", "bg")

    # Resolve the clip's source media from project placeholders.
    src_path = None
    if media != "bg":
        ph = (project.get("placeholders") or {}).get(media)
        rel = (ph or {}).get("file", "")
        src_path = _resolve_media(root, rel)

    if src_path is None:
        bg = clip.get("background") or {"type": "gradient", "from": "#0b1020", "to": "#1a1140"}
        grad_file = tmpdir / f"bg_{idx}.png"
        _gradient_png(bg, w, h, grad_file)
        tmp_files.append(grad_file)
        src_path = grad_file
        is_image = True
    else:
        is_image = str(src_path).lower().endswith(IMG_EXTS)

    if is_image:
        inputs.append(["-loop", "1", "-framerate", str(fps), "-t", _fnum(dur),
                       "-i", str(src_path)])
    else:
        inputs.append(["-stream_loop", "-1", "-i", str(src_path)])

    big_w, big_h = int(w * 1.4) // 2 * 2, int(h * 1.4) // 2 * 2
    chain = [f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase",
             f"crop={big_w}:{big_h}"]
    motion = _motion_filter(clip, w, h, fps, dur)
    if motion:
        chain.append(motion)
    else:
        chain.append(f"scale={w}:{h}:force_original_aspect_ratio=increase")
        chain.append(f"crop={w}:{h}")

    for flt in clip.get("filters") or []:
        if flt.get("type") != "eq":
            continue
        parts = []
        for key in ("contrast", "saturation", "brightness"):
            if flt.get(key) is not None:
                parts.append(f"{key}={_fnum(flt[key])}")
        if parts:
            chain.append("eq=" + ":".join(parts))

    if not motion and not is_image:
        chain.append(f"fps={fps}")

    for ef in clip.get("effects") or []:
        if ef.get("type") == "vignette":
            chain.append("vignette=angle=PI/4" if float(ef.get("amount", 0.3)) >= 0.3 else "vignette")

    chain.append("format=yuv420p")

    overlay = _compose_overlay(clip, project, w, h, _template_dir(template),
                               tmpdir / f"ov_{idx}.png")
    if not overlay:
        graph = ",".join(chain) + f"[c{idx}]"
        return inputs, graph, tmp_files

    tmp_files.append(overlay)
    ov_idx = len(inputs)
    inputs.append(["-loop", "1", "-framerate", str(fps), "-t", _fnum(dur), "-i", str(overlay)])

    bx, by = -(w * PAD), -(h * PAD)
    texts = clip.get("text_layers") or []
    anim = texts[0].get("animation", "fade-up") if texts else "fade-up"
    if anim in ("slide-left", "slideleft"):
        x_expr = f"'{_fnum(bx)}+{_fnum(bx * -0.4)}*min(t/0.5,1)'"
        y_expr = f"'{_fnum(by)}'"
    elif anim in ("slide-right", "slideright"):
        x_expr = f"'{_fnum(bx)}+{_fnum(bx * 0.4)}*min(t/0.5,1)'"
        y_expr = f"'{_fnum(by)}'"
    elif anim == "pop":
        x_expr = f"'{_fnum(bx)}'"
        y_expr = f"'{_fnum(by)}+{_fnum(by * 0.5)}*min(t/0.4,1)'"
    else:  # fade-up
        x_expr = f"'{_fnum(bx)}'"
        y_expr = f"'{_fnum(by)}+{_fnum(abs(by) * 0.8)}*min(t/0.5,1)'"

    fade = _overlay_fade(clip, dur)
    graph = (",".join(chain) + f"[b{idx}];"
             f"[{ov_idx}:v]{fade},format=yuva420p[o{idx}];"
             f"[b{idx}][o{idx}]overlay=x={x_expr}:y={y_expr}[v{idx}];"
             f"[v{idx}]format=yuv420p[c{idx}]")
    return inputs, graph, tmp_files


# ── Command builder ────────────────────────────────────────────────────────

def _build_command(project: dict, template: dict, root: Path,
                   tmpdir: Path, out_path: Path) -> tuple[list, list]:
    w = int(template.get("width", 1080))
    h = int(template.get("height", 1920))
    fps = float(template.get("fps", 30))
    clips = template.get("timeline") or []

    inputs: list[list] = []
    graphs: list[str] = []
    tmp_files: list[Path] = []
    for i, clip in enumerate(clips):
        ins, g, fs = _clip_chain(i, clip, project, template, root, tmpdir, w, h, fps)
        inputs.extend(ins)
        graphs.append(g)
        tmp_files.extend(fs)

    # Transition list + offsets.
    trs = []
    for i, clip in enumerate(clips):
        if i == len(clips) - 1:
            break
        tr = clip.get("transition_out") or {"type": "none", "duration": 0.0}
        ttype = XFADE_MAP.get(tr.get("type", "none"), "fade")
        td = float(tr.get("duration", 0.0))
        if td <= 0.05:
            ttype, td = "fade", 0.1
        trs.append((ttype, td))

    offsets = []
    acc = float(clips[0].get("duration", 3.0)) if clips else 3.0
    for i, (_, td) in enumerate(trs):
        offsets.append(acc - td)
        if i + 1 < len(clips):
            acc += float(clips[i + 1].get("duration", 3.0)) - td
    total = acc

    prev = "[c0]" if clips else ""
    for i, (ttype, td) in enumerate(trs):
        nxt = f"[c{i + 1}]"
        outl = f"[x{i}]" if i < len(trs) - 1 else "[vout]"
        graphs.append(
            f"{prev}{nxt}xfade=transition={ttype}:duration={_fnum(td)}:offset={_fnum(offsets[i])}{outl}")
        prev = outl
    if not trs:
        prev = "[c0]" if clips else ""
        graphs.append(prev)

    label = "[vout]" if trs else "[c0]"

    for i, bar in enumerate(template.get("overlays") or []):
        if bar.get("type") != "gradientbar":
            continue
        bar_png = _gradient_bar_png(bar, w, h, tmpdir / f"bar_{i}.png")
        tmp_files.append(bar_png)
        b_idx = len(inputs)
        inputs.append(["-loop", "1", "-framerate", str(fps), "-t", _fnum(total),
                       "-i", str(bar_png)])
        outl = f"[g{i}]"
        graphs.append(f"[{b_idx}:v]format=yuva420p[gb{i}];"
                      f"{label}[gb{i}]overlay=0:0{outl}")
        label = outl

    # Music from the optional MUSIC placeholder.
    audio_src = None
    music_ph = (project.get("placeholders") or {}).get("MUSIC")
    rel = (music_ph or {}).get("file", "")
    cand = _resolve_media(root, rel) if rel else None
    if cand:
        audio_src = cand
    if audio_src:
        m_idx = len(inputs)
        inputs.append(["-i", str(audio_src)])
        fi = _fnum(min(0.5, total * 0.12))
        graphs.append(
            f"[{m_idx}:a]aresample=48000,volume=0.85,"
            f"afade=t=in:st=0:d={fi},afade=t=out:st={_fnum(max(0.0, total - 0.6))}:d=0.6,"
            f"apad,atrim=0:{_fnum(total)}[aout]")

    graph = ";".join(g for g in graphs if g)

    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-nostats"]
    for ins in inputs:
        cmd.extend(ins)
    cmd += ["-filter_complex", graph, "-map", label]
    if audio_src:
        cmd += ["-map", "[aout]"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", _fnum(fps), "-threads", "2",
            "-t", _fnum(total), "-movflags", "+faststart"]
    if audio_src:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += ["-progress", "pipe:1"]
    cmd.append(str(out_path))
    return cmd, tmp_files


# ── Render driver ──────────────────────────────────────────────────────────

def render_project(user_id: str, pid: str) -> dict:
    root = project_dir(user_id, pid)
    meta = root / "project.json"
    if not meta.is_file():
        return {"status": "failed", "error": "Project not found"}
    try:
        project = json.loads(meta.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "failed", "error": f"Could not load project: {exc}"}
    project["_pid"] = pid

    template = get_template(project.get("template_id", ""))
    if template is None:
        return {"status": "failed", "error": "Template not found"}

    STATIC_GENERATED.mkdir(parents=True, exist_ok=True)
    out_path = STATIC_GENERATED / f"{pid}.mp4"
    tmpdir = Path(tempfile.mkdtemp(prefix=f"tmpl_{pid[:8]}_"))
    try:
        cmd, tmp_files = _build_command(project, template, root, tmpdir, out_path)
    except Exception as exc:
        import traceback
        _cleanup(tmpdir, [])
        return {"status": "failed", "error": f"Pipeline error: {exc} {traceback.format_exc()[:500]}"}

    project["status"] = "rendering"
    project["log"] = ["Starting render…"]
    save_project(user_id, pid, project)

    try:
        ok, err = _run(cmd, project, user_id, pid)
    finally:
        _cleanup(tmpdir, tmp_files)

    if not ok:
        project["status"] = "failed"
        project["error"] = err or "render failed"
        save_project(user_id, pid, project)
        return {"status": "failed", "error": err or "render failed"}

    url = f"/static/generated/templates/{pid}.mp4"
    project["status"] = "done"
    project["progress"] = 1.0
    project["error"] = ""
    project["final_video"] = url
    project["log"] = (project.get("log") or [])[-15:] + ["Render complete"]
    save_project(user_id, pid, project)
    return {"status": "done", "final_video": url}


def _cleanup(tmpdir: Path, files: list) -> None:
    for f in files:
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        shutil.rmtree(str(tmpdir), ignore_errors=True)
    except Exception:
        pass


def _run(cmd: list, project: dict, user_id: str, pid: str) -> tuple[bool, str]:
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
    except Exception as exc:
        return False, f"ffmpeg failed to start: {exc}"

    total_us = float(project.get("duration", 15.0)) * 1_000_000.0
    err_lines: list[str] = []
    last_save = 0.0

    def _err_reader():
        for line in p.stderr:
            err_lines.append(line)

    threading.Thread(target=_err_reader, daemon=True).start()

    while True:
        try:
            line = p.stdout.readline()
        except Exception:
            break
        if not line:
            break
        m = re.search(r"out_time_ms=(\d+)", line)
        if m:
            prog = min(0.99, int(m.group(1)) / max(total_us, 1.0))
            now = time.time()
            if now - last_save > 1.2:
                last_save = now
                project["status"] = "rendering"
                project["progress"] = round(prog, 3)
                save_project(user_id, pid, project)
    p.wait()

    if p.returncode != 0:
        return False, "".join(err_lines)[-800:] or f"ffmpeg exit {p.returncode}"
    return True, ""
