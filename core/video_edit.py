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
