"""Generate the Valleymind Template Library.

One-time data builder. Every template is a JSON "project definition" (NOT a
finished video): it describes a timeline of clips, placeholder slots, text
layers, stickers, filters, transitions, music timing and beat markers. The
frontend renders a form from each template's placeholders; the render engine
(core/template_render.py) swaps user media into the slots and renders a video.

Each template lives in static/templates/<id>/ with:
  template.json   - the full project definition
  thumbnail.webp  - generated card art (gradient + name)
  preview.mp4     - optional; absent templates fall back to a CSS animated
                    preview driven by `grad`/`icon`.

Run:  python _templates_gen.py
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "static" / "templates"
THUMB_W, THUMB_H = 720, 960
FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
FONT_REG = "C:/Windows/Fonts/arial.ttf"


# ── Placeholder + clip helpers ─────────────────────────────────────────────

def P(key: str, label: str, ptype: str, required: bool = True, **kw) -> dict:
    d = {"key": key, "label": label, "type": ptype, "required": bool(required)}
    d.update(kw)
    return d


def T(key, start, dur, y, size=88, anim="fade-up", color="#ffffff", align="center",
      box=True, uppercase=True, stroke="#000000", stroke_width=3, max_chars=0) -> dict:
    return {
        "key": key, "start": round(float(start), 3), "duration": round(float(dur), 3),
        "x": 0.5, "y": round(float(y), 3), "size": int(size), "align": align,
        "color": color, "stroke": stroke, "stroke_width": int(stroke_width),
        "box": bool(box), "font": "", "animation": anim, "uppercase": bool(uppercase),
        "max_chars": int(max_chars),
    }


def CL(media, duration, motion, *, fit="cover", bg=None, filters=None, effects=None,
       text=None, stickers=None, tr=None, caption=None) -> dict:
    clip = {
        "media": media,
        "start": 0.0,
        "duration": round(float(duration), 3),
        "fit": fit,
        "motion": motion,
        "filters": filters or [],
        "effects": effects or [],
        "text_layers": text or [],
        "stickers": stickers or [],
        "transition_out": tr or {"type": "none", "duration": 0.0},
    }
    if bg:
        clip["background"] = bg
    if caption:
        clip["caption"] = caption
    return clip


# ── Presets ────────────────────────────────────────────────────────────────

MOTION_STATIC = {"type": "static"}
MOTION_ZIN = {"type": "zoom", "from": 1.0, "to": 1.18, "ease": "ease-in-out"}
MOTION_ZOUT = {"type": "zoom", "from": 1.18, "to": 1.0, "ease": "ease-in-out"}
MOTION_ZSLOW = {"type": "zoom", "from": 1.0, "to": 1.10, "ease": "linear"}
MOTION_ZSHARP = {"type": "zoom", "from": 1.0, "to": 1.30, "ease": "ease-in-out"}
MOTION_KEN = {"type": "zoom", "from": 1.05, "to": 1.22, "ease": "ease-in-out"}
MOTION_PANUP = {"type": "pan", "dx": 0.0, "dy": -0.05, "zoom": 1.15}
MOTION_PANDN = {"type": "pan", "dx": 0.0, "dy": 0.05, "zoom": 1.15}
MOTION_PANL = {"type": "pan", "dx": -0.06, "dy": 0.0, "zoom": 1.15}
MOTION_PANR = {"type": "pan", "dx": 0.06, "dy": 0.0, "zoom": 1.15}

TR_NONE = {"type": "none", "duration": 0.0}
TR_FADE = {"type": "fade", "duration": 0.5}
TR_FADEFAST = {"type": "fade", "duration": 0.3}
TR_SLIDEL = {"type": "slideleft", "duration": 0.5}
TR_SLIDER = {"type": "slideright", "duration": 0.5}
TR_WIPEUP = {"type": "wipeup", "duration": 0.5}
TR_WIPELEFT = {"type": "wipeleft", "duration": 0.5}
TR_CIRCLE = {"type": "circleopen", "duration": 0.6}
TR_DISSOLVE = {"type": "dissolve", "duration": 0.6}

F_NONE = []
F_VIBRANT = [{"type": "eq", "contrast": 1.08, "saturation": 1.25, "brightness": 0.99}]
F_POP = [{"type": "eq", "contrast": 1.15, "saturation": 1.35, "brightness": 1.0}]
F_WARM = [{"type": "eq", "contrast": 1.05, "saturation": 1.12, "brightness": 1.01}]
F_CINEMA = [{"type": "eq", "contrast": 1.12, "saturation": 1.05, "brightness": 0.96}]
F_MUTED = [{"type": "eq", "contrast": 1.0, "saturation": 0.85, "brightness": 1.0}]
F_BW = [{"type": "eq", "contrast": 1.15, "saturation": 0.0, "brightness": 1.02}]

E_VIGNETTE = [{"type": "vignette", "amount": 0.35}]
E_VIGNETTE_SOFT = [{"type": "vignette", "amount": 0.22}]
E_NONE = []

BG_DARK = {"type": "gradient", "from": "#0b1020", "to": "#1a1140", "angle": 160}
BG_BLACK = {"type": "gradient", "from": "#050505", "to": "#181818", "angle": 150}
BG_GOLD = {"type": "gradient", "from": "#241a05", "to": "#3d2c08", "angle": 150}
BG_EMERALD = {"type": "gradient", "from": "#022c22", "to": "#064e3b", "angle": 160}
BG_ROSE = {"type": "gradient", "from": "#2a0a14", "to": "#4c1220", "angle": 160}
BG_NAVY = {"type": "gradient", "from": "#0a1128", "to": "#1e1b4b", "angle": 160}
BG_ORANGE = {"type": "gradient", "from": "#1c0f02", "to": "#3b1d05", "angle": 160}
BG_VIOLET = {"type": "gradient", "from": "#160b2e", "to": "#2e1065", "angle": 160}
BG_CYAN = {"type": "gradient", "from": "#06121e", "to": "#083344", "angle": 160}
BG_PINK = {"type": "gradient", "from": "#2a0f1f", "to": "#4a1233", "angle": 160}
BG_PAPER = {"type": "gradient", "from": "#f8f4e6", "to": "#ece4cd", "angle": 150}

STICKER_LOGO = {"src": "/static/assets/stickers/sticker_0153.png", "x": 0.5, "y": 0.12,
                "scale": 0.22, "rotation": 0, "start": 0.0, "duration": 0.0,
                "animation": "none", "blend": "normal"}

FADE_OUT_BAR = {"type": "gradientbar", "from": "rgba(0,0,0,0)", "to": "rgba(0,0,0,0.55)",
                "position": "bottom", "height": 0.22, "start": 0.0, "duration": 0.0}
FADE_IN_BAR = {"type": "gradientbar", "from": "rgba(0,0,0,0.55)", "to": "rgba(0,0,0,0)",
               "position": "top", "height": 0.16, "start": 0.0, "duration": 0.0}


# ── Categories ─────────────────────────────────────────────────────────────

CATEGORIES = [
    ("Trending", "flame", ["#f43f5e", "#fb923c"]),
    ("Viral", "zap", ["#a855f7", "#ec4899"]),
    ("Business", "briefcase", ["#f59e0b", "#ef4444"]),
    ("YouTube", "youtube", ["#ef4444", "#7f1d1d"]),
    ("TikTok", "music-2", ["#22d3ee", "#a78bfa"]),
    ("Reels", "smartphone", ["#ec4899", "#f43f5e"]),
    ("Facebook", "facebook", ["#3b82f6", "#1e3a8a"]),
    ("WhatsApp Status", "message-circle", ["#22c55e", "#065f46"]),
    ("Education", "graduation-cap", ["#6366f1", "#8b5cf6"]),
    ("Birthday", "cake", ["#38bdf8", "#a78bfa"]),
    ("Wedding", "heart", ["#fb7185", "#fda4af"]),
    ("Love", "heart-pulse", ["#f43f5e", "#fb7185"]),
    ("Travel", "plane", ["#0ea5e9", "#22d3ee"]),
    ("Food", "utensils", ["#f97316", "#eab308"]),
    ("Sports", "dumbbell", ["#22c55e", "#16a34a"]),
    ("Gaming", "gamepad-2", ["#a855f7", "#7c3aed"]),
    ("Music", "music", ["#10b981", "#3b82f6"]),
    ("Motivation", "trending-up", ["#f97316", "#eab308"]),
    ("Podcast", "mic", ["#14b8a6", "#0d9488"]),
    ("Slideshow", "presentation", ["#f59e0b", "#fb923c"]),
    ("Product Showcase", "package", ["#f59e0b", "#f97316"]),
    ("Fashion", "shirt", ["#f472b6", "#e879f9"]),
    ("Technology", "cpu", ["#06b6d4", "#2563eb"]),
    ("Real Estate", "building-2", ["#34d399", "#0f766e"]),
    ("News", "newspaper", ["#f87171", "#991b1b"]),
    ("Flyers", "megaphone", ["#fb923c", "#f43f5e"]),
    ("Posters", "image", ["#8b5cf6", "#d946ef"]),
    ("Certificates", "award", ["#f59e0b", "#92400e"]),
    ("Logos", "pen-tool", ["#22d3ee", "#0ea5e9"]),
    ("Invitations", "mail", ["#fbbf24", "#f59e0b"]),
    ("Business Cards", "id-card", ["#64748b", "#334155"]),
    ("Social Media Posts", "share-2", ["#8b5cf6", "#ec4899"]),
    ("Africa", "sun", ["#f59e0b", "#fb923c"]),
    ("Nigeria", "music", ["#10b981", "#3b82f6"]),
]
CAT_BY_NAME = {name: {"icon": icon, "grad": grad} for name, icon, grad in CATEGORIES}


# ── Template table ─────────────────────────────────────────────────────────

def _pl_title(label="Headline text", **kw):
    return P("TITLE", label, "text", True, max_chars=60, **kw)


def _pl_sub(label="Supporting text"):
    return P("SUBTITLE", label, "text", False, max_chars=90)


def _pl_music():
    return P("MUSIC", "Background music (optional)", "audio", False)


TEMPLATES = [
    # ── Trending ────────────────────────────────────────────────────────────
    dict(id="trend_beat_hook", name="Beat Hook", cat="Trending",
         desc="12s trending beat-synced hook with snap zooms.",
         icon="flame", grad=["#f43f5e", "#fb923c"], ratio="9:16", dur=12.0,
         pop=98, likes=48210, downloads=19340, edit=4, bpm=132,
         pl=[P("PHOTO_1", "Main photo", "image", True), P("PHOTO_2", "Second photo", "image", True),
             _pl_title("Hook line"), _pl_sub(), _pl_music()],
         clips=[
             CL("bg", 2.0, MOTION_ZSLOW, bg=BG_DARK, tr=TR_FADEFAST,
                text=[T("TITLE", 0.2, 1.6, 0.30, 110, "pop", "#ffffff", box=True),
                      T("SUBTITLE", 0.55, 1.3, 0.46, 52, "fade-up", "#fca5a5")]),
             CL("PHOTO_1", 3.0, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_2", 3.0, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADEFAST),
             CL("PHOTO_1", 2.5, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 1.5, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("SUBTITLE", 0.2, 1.2, 0.45, 46, "fade-up", "#ffffff", uppercase=False)]),
         ], overlays=[FADE_OUT_BAR, FADE_IN_BAR]),

    dict(id="trend_speedramp", name="Speed Ramp", cat="Trending",
         desc="Fast cuts, punch-ins and flash captions.",
         icon="gauge", grad=["#fb923c", "#ef4444"], ratio="9:16", dur=14.0,
         pop=95, likes=36120, downloads=12870, edit=6, bpm=140,
         pl=[P("VIDEO_1", "Main clip", "video", True), P("VIDEO_2", "Extra clip", "video", False),
             _pl_title("Bold caption"), _pl_sub()],
         clips=[
             CL("VIDEO_1", 3.0, MOTION_ZIN, filters=F_POP, effects=E_VIGNETTE, tr=TR_WIPELEFT,
                text=[T("TITLE", 0.2, 2.6, 0.18, 78, "slide-left", "#ffffff", stroke_width=4)]),
             CL("VIDEO_2", 3.0, MOTION_ZSHARP, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_1", 3.0, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADEFAST),
             CL("VIDEO_2", 2.5, MOTION_PANUP, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 2.5, MOTION_ZIN, bg=BG_BLACK, tr=TR_NONE,
                text=[T("SUBTITLE", 0.2, 2.1, 0.42, 64, "pop", "#fca5a5", uppercase=False)]),
         ], overlays=[FADE_OUT_BAR]),

    # ── Viral ───────────────────────────────────────────────────────────────
    dict(id="viral_hook_opener", name="Viral Hook Opener", cat="Viral",
         desc="Scroll-stopping hook with punchy beat cuts.",
         icon="zap", grad=["#a855f7", "#ec4899"], ratio="9:16", dur=12.0,
         pop=97, likes=45210, downloads=18240, edit=4, bpm=130,
         pl=[P("PHOTO_1", "Hook image", "image", True), P("PHOTO_2", "Reveal image", "image", True),
             _pl_title("The hook"), _pl_sub()],
         clips=[
             CL("bg", 1.8, MOTION_ZSLOW, bg=BG_VIOLET, tr=TR_FADEFAST,
                text=[T("TITLE", 0.2, 1.4, 0.32, 104, "pop")]),
             CL("PHOTO_1", 2.6, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_2", 2.6, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADEFAST),
             CL("PHOTO_1", 2.6, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 2.4, MOTION_ZIN, bg=BG_VIOLET, tr=TR_NONE,
                text=[T("SUBTITLE", 0.2, 2.0, 0.44, 52, "fade-up", "#d8b4fe")]),
         ], overlays=[FADE_OUT_BAR]),

    dict(id="viral_before_after", name="Before / After", cat="Viral",
         desc="Side-swipe transformation reveal.",
         icon="replace", grad=["#ec4899", "#8b5cf6"], ratio="9:16", dur=14.0,
         pop=93, likes=38900, downloads=15120, edit=5, bpm=128,
         pl=[P("PHOTO_1", "Before photo", "image", True), P("PHOTO_2", "After photo", "image", True),
             P("VIDEO_1", "Bonus clip (optional)", "video", False),
             _pl_title("Transformation"), _pl_sub("Bigger text")],
         clips=[
             CL("PHOTO_1", 3.5, MOTION_ZIN, filters=F_MUTED, tr=TR_SLIDEL,
                text=[T("SUBTITLE", 0.2, 3.1, 0.84, 54, "fade-up", "#94a3b8", uppercase=False)]),
             CL("PHOTO_2", 3.5, MOTION_ZOUT, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADEFAST,
                text=[T("TITLE", 0.2, 3.1, 0.84, 64, "pop")]),
             CL("VIDEO_1", 3.5, MOTION_KEN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 2.9, 0.42, 58, "fade-up", "#e9d5ff", uppercase=False)]),
         ]),

    # ── Business ────────────────────────────────────────────────────────────
    dict(id="biz_clean_launch", name="Clean Launch", cat="Business",
         desc="Polished product launch with premium grade.",
         icon="rocket", grad=["#f59e0b", "#ef4444"], ratio="16:9", dur=18.0,
         pop=94, likes=22140, downloads=9760, edit=7, bpm=118,
         pl=[P("LOGO", "Logo", "image", False), P("PHOTO_1", "Product shot 1", "image", True),
             P("PHOTO_2", "Product shot 2", "image", True),
             P("PRODUCT_NAME", "Product name", "text", True, max_chars=40),
             P("SUBTITLE", "Tagline", "text", False, max_chars=80)],
         clips=[
             CL("bg", 3.0, MOTION_ZSLOW, bg=BG_DARK, tr=TR_FADE,
                stickers=[STICKER_LOGO],
                text=[T("PRODUCT_NAME", 0.4, 2.3, 0.34, 92, "fade-up"),
                      T("SUBTITLE", 1.0, 1.8, 0.50, 44, "fade-up", "#fbbf24", uppercase=False)]),
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_CINEMA, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_2", 4.0, MOTION_KEN, filters=F_CINEMA, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_1", 4.0, MOTION_PANL, filters=F_CINEMA, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 3.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                stickers=[STICKER_LOGO],
                text=[T("SUBTITLE", 0.4, 2.4, 0.44, 50, "fade-up", "#ffffff", uppercase=False)]),
         ], overlays=[FADE_IN_BAR]),

    dict(id="biz_quote_card", name="Quote Card", cat="Business",
         desc="Shareable stat / quote motion card.",
         icon="quote", grad=["#f97316", "#facc15"], ratio="1:1", dur=10.0,
         pop=88, likes=15870, downloads=7230, edit=3, bpm=110,
         pl=[_pl_title("Headline stat"), _pl_sub("Supporting line"), P("NAME", "Attribution", "text", False)],
         clips=[
             CL("bg", 3.5, MOTION_ZSLOW, bg=BG_ORANGE, tr=TR_FADE,
                text=[T("TITLE", 0.3, 2.9, 0.38, 120, "pop"),
                      T("SUBTITLE", 0.7, 2.5, 0.52, 46, "fade-up", "#fde68a", uppercase=False)]),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_BLACK, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 2.9, 0.36, 56, "fade-up", "#fca5a5", uppercase=False),
                      T("NAME", 1.0, 2.2, 0.52, 40, "fade-up", "#94a3b8")]),
             CL("bg", 3.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 2.4, 0.42, 44, "fade-up", "#fbbf24", uppercase=False)]),
         ]),

    # ── YouTube ─────────────────────────────────────────────────────────────
    dict(id="yt_intro", name="YouTube Intro", cat="YouTube",
         desc="Branded 8s channel intro.",
         icon="play", grad=["#ef4444", "#7f1d1d"], ratio="16:9", dur=8.0,
         pop=92, likes=18740, downloads=8150, edit=3, bpm=124,
         pl=[P("LOGO", "Channel logo", "image", False), P("CHANNEL_NAME", "Channel name", "text", True, max_chars=30),
             P("SUBTITLE", "Slogan", "text", False, max_chars=60)],
         clips=[
             CL("bg", 4.0, MOTION_ZIN, bg=BG_DARK, tr=TR_CIRCLE,
                stickers=[STICKER_LOGO],
                text=[T("CHANNEL_NAME", 0.5, 3.1, 0.34, 84, "pop"),
                      T("SUBTITLE", 1.2, 2.4, 0.48, 40, "fade-up", "#fca5a5", uppercase=False)]),
             CL("bg", 4.0, MOTION_ZOUT, bg=BG_BLACK, tr=TR_NONE,
                stickers=[STICKER_LOGO],
                text=[T("SUBTITLE", 0.4, 3.2, 0.46, 44, "fade-up", "#ffffff", uppercase=False)]),
         ]),

    dict(id="yt_outro", name="YouTube Outro", cat="YouTube",
         desc="End-screen card with subscribe nudge.",
         icon="hand", grad=["#dc2626", "#450a0a"], ratio="16:9", dur=10.0,
         pop=90, likes=14320, downloads=6900, edit=3, bpm=110,
         pl=[P("LOGO", "Channel logo", "image", False), P("CHANNEL_NAME", "Channel name", "text", True, max_chars=30),
             P("BUTTON_TEXT", "Button text", "text", False, max_chars=24, default_value="Subscribe")],
         clips=[
             CL("bg", 5.0, MOTION_ZSLOW, bg=BG_BLACK, tr=TR_FADE,
                stickers=[STICKER_LOGO],
                text=[T("CHANNEL_NAME", 0.4, 4.2, 0.36, 80, "fade-up"),
                      T("BUTTON_TEXT", 1.2, 3.4, 0.52, 56, "pop", "#ffffff", box=True, stroke="#16a34a")]),
             CL("bg", 5.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("CHANNEL_NAME", 0.4, 4.2, 0.44, 60, "fade-up", "#fca5a5")]),
         ]),

    dict(id="yt_highlights", name="YouTube Highlights", cat="YouTube",
         desc="Fast recap edit for longer content.",
         icon="zap", grad=["#ef4444", "#0f172a"], ratio="16:9", dur=20.0,
         pop=91, likes=23610, downloads=9640, edit=8, bpm=126,
         pl=[P("VIDEO_1", "Main clip", "video", True), P("VIDEO_2", "Clip 2", "video", False),
             P("VIDEO_3", "Clip 3", "video", False), _pl_title("Title card")],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_ZIN, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_2", 4.0, MOTION_KEN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_3", 4.0, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("VIDEO_1", 4.0, MOTION_PANUP, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADEFAST),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_BLACK, tr=TR_NONE,
                text=[T("TITLE", 0.3, 3.4, 0.44, 76, "pop")]),
         ]),

    # ── TikTok ──────────────────────────────────────────────────────────────
    dict(id="tiktok_ducttape", name="Duct-Tape Cuts", cat="TikTok",
         desc="POV-style fast cuts with snap transitions.",
         icon="clapperboard", grad=["#22d3ee", "#a78bfa"], ratio="9:16", dur=13.0,
         pop=96, likes=41230, downloads=17650, edit=5, bpm=134,
         pl=[P("VIDEO_1", "Main clip", "video", True), P("VIDEO_2", "Cut clip", "video", False),
             _pl_title("Caption"), _pl_sub()],
         clips=[
             CL("VIDEO_1", 3.2, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_2", 3.2, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_1", 3.2, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADEFAST),
             CL("bg", 3.4, MOTION_ZIN, bg=BG_CYAN, tr=TR_NONE,
                text=[T("TITLE", 0.2, 2.8, 0.40, 80, "pop"),
                      T("SUBTITLE", 0.7, 2.3, 0.54, 42, "fade-up", "#a5f3fc", uppercase=False)]),
         ]),

    dict(id="tiktok_pov", name="POV Story", cat="TikTok",
         desc="First-person storytelling with beat captions.",
         icon="user", grad=["#a78bfa", "#f472b6"], ratio="9:16", dur=15.0,
         pop=94, likes=33780, downloads=14120, edit=6, bpm=120,
         pl=[P("VIDEO_1", "POV clip", "video", True), P("PHOTO_1", "Story photo", "image", False),
             _pl_title("Story text"), _pl_sub("Caption line")],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_ZIN, filters=F_WARM, effects=E_VIGNETTE, tr=TR_FADE),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE, tr=TR_FADE),
             CL("VIDEO_1", 4.0, MOTION_ZSLOW, filters=F_WARM, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.0, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("TITLE", 0.2, 2.6, 0.40, 72, "pop"),
                      T("SUBTITLE", 0.6, 2.2, 0.54, 40, "fade-up", "#f9a8d4", uppercase=False)]),
         ]),

    # ── Reels ───────────────────────────────────────────────────────────────
    dict(id="reels_glitch", name="Glitch Reveal", cat="Reels",
         desc="Neon glitch intro + beat reveal.",
         icon="smartphone", grad=["#ec4899", "#f43f5e"], ratio="9:16", dur=12.0,
         pop=95, likes=36150, downloads=15980, edit=5, bpm=136,
         pl=[P("PHOTO_1", "Reveal photo", "image", True), P("VIDEO_1", "Clip (optional)", "video", False),
             _pl_title("Reveal text"), _pl_sub()],
         clips=[
             CL("bg", 2.2, MOTION_ZSHARP, bg=BG_PINK, tr=TR_SLIDEL,
                text=[T("TITLE", 0.2, 1.8, 0.36, 96, "pop")]),
             CL("PHOTO_1", 3.0, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_WIPELEFT),
             CL("VIDEO_1", 3.4, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADEFAST),
             CL("PHOTO_1", 3.4, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 2.8, 0.80, 48, "fade-up", "#f9a8d4", uppercase=False)]),
         ]),

    dict(id="reels_duet", name="Duet Format", cat="Reels",
         desc="Reaction-style split with punch captions.",
         icon="users", grad=["#fb7185", "#f472b6"], ratio="9:16", dur=13.0,
         pop=92, likes=27890, downloads=11240, edit=5, bpm=124,
         pl=[P("VIDEO_1", "Main clip", "video", True), P("PHOTO_1", "Reaction photo", "image", False),
             _pl_title("Caption"), _pl_sub("Small caption")],
         clips=[
             CL("VIDEO_1", 3.5, MOTION_ZIN, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE,
                text=[T("TITLE", 0.2, 3.1, 0.16, 64, "slide-left")]),
             CL("PHOTO_1", 3.5, MOTION_ZSHARP, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 2.9, 0.84, 44, "fade-up", "#ffffff", uppercase=False)]),
             CL("VIDEO_1", 3.5, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 2.5, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("SUBTITLE", 0.2, 2.1, 0.42, 40, "fade-up", "#fecdd3", uppercase=False)]),
         ]),

    # ── Facebook ────────────────────────────────────────────────────────────
    dict(id="fb_newsflash", name="Newsflash", cat="Facebook",
         desc="Bold caption news-style card.",
         icon="megaphone", grad=["#3b82f6", "#1e3a8a"], ratio="1:1", dur=12.0,
         pop=86, likes=12940, downloads=5480, edit=3, bpm=112,
         pl=[P("PHOTO_1", "Featured photo", "image", True), _pl_title("Headline"), _pl_sub("Details")],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADE,
                text=[T("TITLE", 0.2, 3.6, 0.18, 60, "fade-up")]),
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_NAVY, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 3.3, 0.40, 56, "fade-up", "#bfdbfe", uppercase=False)]),
             CL("PHOTO_1", 4.0, MOTION_ZOUT, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_NONE),
         ]),

    dict(id="fb_community", name="Community Post", cat="Facebook",
         desc="Warm community announcement card.",
         icon="users", grad=["#60a5fa", "#0f172a"], ratio="4:5", dur=12.0,
         pop=84, likes=11230, downloads=4910, edit=3, bpm=110,
         pl=[P("PHOTO_1", "Community photo", "image", True), P("NAME", "Group / author", "text", False),
             _pl_title("Announcement"), _pl_sub("Details")],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_NAVY, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.36, 72, "fade-up"),
                      T("NAME", 1.0, 2.6, 0.50, 40, "fade-up", "#93c5fd")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 3.4, 0.44, 44, "fade-up", "#e2e8f0", uppercase=False)]),
         ]),

    # ── WhatsApp Status ─────────────────────────────────────────────────────
    dict(id="wa_quote", name="Status Quote", cat="WhatsApp Status",
         desc="Clean quote status with soft pop.",
         icon="message-circle", grad=["#22c55e", "#065f46"], ratio="9:16", dur=10.0,
         pop=90, likes=19840, downloads=8760, edit=2, bpm=108,
         pl=[_pl_title("Quote / update"), P("NAME", "Your name", "text", False, max_chars=30)],
         clips=[
             CL("bg", 5.0, MOTION_ZSLOW, bg=BG_EMERALD, tr=TR_FADE,
                text=[T("TITLE", 0.3, 4.3, 0.38, 64, "fade-up", "#ffffff", uppercase=False),
                      T("NAME", 1.4, 3.2, 0.54, 36, "fade-up", "#a7f3d0")]),
             CL("bg", 5.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("TITLE", 0.3, 4.3, 0.40, 56, "fade-up", "#6ee7b7", uppercase=False)]),
         ]),

    dict(id="wa_holiday", name="Holiday Wish", cat="WhatsApp Status",
         desc="Festive greeting status.",
         icon="sparkles", grad=["#34d399", "#059669"], ratio="9:16", dur=10.0,
         pop=88, likes=16470, downloads=7320, edit=2, bpm=112,
         pl=[_pl_title("Greeting"), P("SUBTITLE", "Extra line", "text", False), P("NAME", "From", "text", False)],
         clips=[
             CL("bg", 5.0, MOTION_ZIN, bg=BG_EMERALD, tr=TR_FADE,
                text=[T("TITLE", 0.3, 4.3, 0.36, 72, "pop"),
                      T("SUBTITLE", 0.9, 3.7, 0.50, 42, "fade-up", "#a7f3d0", uppercase=False)]),
             CL("bg", 5.0, MOTION_ZSLOW, bg=BG_DARK, tr=TR_NONE,
                text=[T("NAME", 0.3, 4.3, 0.46, 44, "fade-up", "#6ee7b7")]),
         ]),

    # ── Education ───────────────────────────────────────────────────────────
    dict(id="edu_explainer", name="Explainer", cat="Education",
         desc="Clean classroom explainer with bullet captions.",
         icon="graduation-cap", grad=["#6366f1", "#8b5cf6"], ratio="16:9", dur=16.0,
         pop=89, likes=15420, downloads=6820, edit=6, bpm=112,
         pl=[P("PHOTO_1", "Concept image", "image", True), P("PHOTO_2", "Diagram / image 2", "image", False),
             _pl_title("Lesson title"), _pl_sub("Key point")],
         clips=[
             CL("bg", 3.0, MOTION_ZSLOW, bg=BG_NAVY, tr=TR_FADE,
                text=[T("TITLE", 0.3, 2.4, 0.38, 76, "fade-up")]),
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_MUTED, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 3.4, 0.84, 46, "fade-up", "#c7d2fe", uppercase=False)]),
             CL("PHOTO_2", 4.0, MOTION_KEN, filters=F_MUTED, tr=TR_FADE),
             CL("PHOTO_1", 4.0, MOTION_ZOUT, filters=F_MUTED, tr=TR_NONE),
         ]),

    dict(id="edu_quiz", name="Quiz Pop", cat="Education",
         desc="Question-and-reveal quiz card.",
         icon="help-circle", grad=["#8b5cf6", "#6366f1"], ratio="9:16", dur=12.0,
         pop=87, likes=13890, downloads=6010, edit=4, bpm=120,
         pl=[_pl_title("Question"), _pl_sub("Answer reveal")],
         clips=[
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_VIOLET, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.38, 72, "pop")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_NAVY, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 3.3, 0.40, 56, "fade-up", "#c4b5fd", uppercase=False)]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 3.4, 0.42, 48, "pop", "#a78bfa", uppercase=False)]),
         ]),

    # ── Birthday ────────────────────────────────────────────────────────────
    dict(id="bday_blast", name="Birthday Blast", cat="Birthday",
         desc="Confetti-energy birthday highlights.",
         icon="cake", grad=["#38bdf8", "#a78bfa"], ratio="9:16", dur=15.0,
         pop=94, likes=28430, downloads=12980, edit=5, bpm=128,
         pl=[P("PHOTO_1", "Party photo 1", "image", True), P("PHOTO_2", "Party photo 2", "image", True),
             P("NAME", "Birthday name", "text", True, max_chars=30),
             P("DATE", "Date / age", "text", False, max_chars=20)],
         clips=[
             CL("bg", 2.2, MOTION_ZSLOW, bg=BG_PINK, tr=TR_FADEFAST,
                text=[T("NAME", 0.3, 1.7, 0.34, 96, "pop"),
                      T("DATE", 0.8, 1.2, 0.48, 40, "fade-up", "#fbcfe8")]),
             CL("PHOTO_1", 3.0, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_2", 3.0, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_1", 3.0, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.8, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("DATE", 0.3, 3.2, 0.42, 64, "pop", "#fecdd3", uppercase=False)]),
         ], overlays=[FADE_OUT_BAR]),

    dict(id="bday_candles", name="Candle Countdown", cat="Birthday",
         desc="Countdown cards to the candle moment.",
         icon="hourglass", grad=["#a78bfa", "#38bdf8"], ratio="9:16", dur=12.0,
         pop=90, likes=20140, downloads=9180, edit=4, bpm=120,
         pl=[P("PHOTO_1", "Photo of the birthday star", "image", True),
             P("NAME", "Their name", "text", True, max_chars=30), _pl_sub("Wish text")],
         clips=[
             CL("PHOTO_1", 3.0, MOTION_KEN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.0, MOTION_ZSLOW, bg=BG_PINK, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 2.4, 0.40, 64, "pop", "#ffffff", uppercase=False)]),
             CL("PHOTO_1", 3.0, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.0, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("NAME", 0.3, 2.4, 0.42, 72, "pop")]),
         ]),

    # ── Wedding ─────────────────────────────────────────────────────────────
    dict(id="wed_save_date", name="Save The Date", cat="Wedding",
         desc="Romantic save-the-date reveal.",
         icon="heart", grad=["#fb7185", "#fda4af"], ratio="9:16", dur=14.0,
         pop=95, likes=31240, downloads=14760, edit=5, bpm=100,
         pl=[P("PHOTO_1", "Couple photo", "image", True), P("NAME", "Couple names", "text", True, max_chars=40),
             P("DATE", "Wedding date", "text", True, max_chars=30), P("LOCATION", "Venue", "text", False, max_chars=40)],
         clips=[
             CL("bg", 2.5, MOTION_ZSLOW, bg=BG_ROSE, tr=TR_FADE,
                text=[T("NAME", 0.3, 1.9, 0.36, 84, "pop")]),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_DARK, tr=TR_FADE,
                text=[T("DATE", 0.3, 3.4, 0.38, 64, "fade-up", "#fda4af"),
                      T("LOCATION", 1.0, 2.7, 0.52, 38, "fade-up", "#fbcfe8", uppercase=False)]),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("NAME", 0.3, 2.9, 0.42, 60, "fade-up")]),
         ], overlays=[FADE_IN_BAR]),

    dict(id="wed_slideshow", name="Wedding Slideshow", cat="Wedding",
         desc="Golden-hour photo slideshow.",
         icon="camera", grad=["#fda4af", "#f472b6"], ratio="16:9", dur=20.0,
         pop=94, likes=27890, downloads=13210, edit=7, bpm=96,
         pl=[P("PHOTO_1", "Photo 1", "image", True), P("PHOTO_2", "Photo 2", "image", True),
             P("PHOTO_3", "Photo 3", "image", True), P("NAME", "Names", "text", True, max_chars=40)],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_2", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_3", 4.0, MOTION_ZOUT, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_1", 4.0, MOTION_PANR, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("NAME", 0.3, 3.4, 0.42, 72, "fade-up")]),
         ], overlays=[FADE_OUT_BAR]),

    # ── Love ────────────────────────────────────────────────────────────────
    dict(id="love_valentine", name="Valentine Card", cat="Love",
         desc="Soft romantic card with pulse accents.",
         icon="heart-pulse", grad=["#f43f5e", "#fb7185"], ratio="9:16", dur=11.0,
         pop=91, likes=22940, downloads=10410, edit=3, bpm=104,
         pl=[_pl_title("Love note"), P("NAME", "To", "text", False, max_chars=30),
             P("SUBTITLE", "Extra line", "text", False)],
         clips=[
             CL("bg", 3.5, MOTION_ZSLOW, bg=BG_ROSE, tr=TR_FADE,
                text=[T("TITLE", 0.3, 2.9, 0.38, 72, "pop"),
                      T("NAME", 0.9, 2.3, 0.52, 42, "fade-up", "#fecdd3")]),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_DARK, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 2.9, 0.40, 56, "fade-up", "#fda4af", uppercase=False)]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 3.4, 0.42, 48, "fade-up", "#fff1f2", uppercase=False)]),
         ]),

    dict(id="love_anniversary", name="Anniversary Film", cat="Love",
         desc="Milestone recap montage.",
         icon="sparkles", grad=["#fb7185", "#f43f5e"], ratio="9:16", dur=16.0,
         pop=90, likes=19820, downloads=8760, edit=6, bpm=108,
         pl=[P("PHOTO_1", "Photo 1", "image", True), P("PHOTO_2", "Photo 2", "image", True),
             P("NAME", "Your names", "text", True, max_chars=40), P("DATE", "Year", "text", False, max_chars=20)],
         clips=[
             CL("PHOTO_1", 3.5, MOTION_ZIN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("PHOTO_2", 3.5, MOTION_ZOUT, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("PHOTO_1", 3.5, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 5.5, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("NAME", 0.3, 4.8, 0.36, 64, "fade-up"),
                      T("DATE", 1.2, 3.9, 0.50, 44, "pop", "#fecdd3")]),
         ]),

    # ── Travel ──────────────────────────────────────────────────────────────
    dict(id="travel_adventure", name="Adventure Reel", cat="Travel",
         desc="Wanderlust montage with cinematic grade.",
         icon="mountain", grad=["#0ea5e9", "#22d3ee"], ratio="9:16", dur=16.0,
         pop=95, likes=34210, downloads=15640, edit=6, bpm=122,
         pl=[P("VIDEO_1", "Main travel clip", "video", True), P("PHOTO_1", "Landscape photo", "image", True),
             P("LOCATION", "Destination", "text", True, max_chars=40), _pl_sub("Caption")],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_ZIN, filters=F_CINEMA, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_CINEMA, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("VIDEO_1", 4.0, MOTION_PANR, filters=F_CINEMA, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_CYAN, tr=TR_NONE,
                text=[T("LOCATION", 0.3, 3.4, 0.36, 80, "fade-up"),
                      T("SUBTITLE", 1.0, 2.7, 0.50, 40, "fade-up", "#a5f3fc", uppercase=False)]),
         ]),

    dict(id="travel_polaroid", name="Polaroid Trip", cat="Travel",
         desc="Instant-photo flashback cards.",
         icon="camera", grad=["#22d3ee", "#0ea5e9"], ratio="1:1", dur=14.0,
         pop=91, likes=23780, downloads=10490, edit=5, bpm=116,
         pl=[P("PHOTO_1", "Photo 1", "image", True), P("PHOTO_2", "Photo 2", "image", True),
             P("LOCATION", "Place", "text", True, max_chars=40)],
         clips=[
             CL("PHOTO_1", 3.5, MOTION_ZIN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_SLIDEL),
             CL("PHOTO_2", 3.5, MOTION_ZOUT, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_SLIDEL),
             CL("PHOTO_1", 3.5, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_CYAN, tr=TR_NONE,
                text=[T("LOCATION", 0.3, 2.9, 0.42, 64, "pop")]),
         ]),

    # ── Food ────────────────────────────────────────────────────────────────
    dict(id="food_delicious", name="Delicious Bite", cat="Food",
         desc="Appetizing recipe teaser.",
         icon="utensils", grad=["#f97316", "#eab308"], ratio="9:16", dur=13.0,
         pop=93, likes=25980, downloads=11870, edit=4, bpm=124,
         pl=[P("PHOTO_1", "Dish photo 1", "image", True), P("PHOTO_2", "Dish photo 2", "image", True),
             P("PRODUCT_NAME", "Dish name", "text", True, max_chars=40), _pl_sub("Detail line")],
         clips=[
             CL("PHOTO_1", 3.0, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_2", 3.0, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_1", 3.0, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_ORANGE, tr=TR_NONE,
                text=[T("PRODUCT_NAME", 0.3, 3.4, 0.36, 72, "pop"),
                      T("SUBTITLE", 1.0, 2.7, 0.50, 40, "fade-up", "#fde68a", uppercase=False)]),
         ]),

    dict(id="food_recipe", name="Recipe Steps", cat="Food",
         desc="Numbered recipe steps card.",
         icon="list-checks", grad=["#fbbf24", "#f97316"], ratio="9:16", dur=14.0,
         pop=90, likes=21450, downloads=9820, edit=5, bpm=118,
         pl=[P("PHOTO_1", "Final dish photo", "image", True),
             _pl_title("Recipe title"), _pl_sub("Short steps")],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_POP, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZSLOW, bg=BG_ORANGE, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 2.9, 0.40, 56, "fade-up", "#fef3c7", uppercase=False)]),
             CL("PHOTO_1", 3.5, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 3.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("TITLE", 0.3, 2.4, 0.42, 72, "pop")]),
         ]),

    # ── Sports ──────────────────────────────────────────────────────────────
    dict(id="sport_matchday", name="Matchday Hype", cat="Sports",
         desc="High-energy match highlights intro.",
         icon="trophy", grad=["#22c55e", "#16a34a"], ratio="9:16", dur=15.0,
         pop=96, likes=40210, downloads=18340, edit=5, bpm=138,
         pl=[P("VIDEO_1", "Highlight clip", "video", True), P("PHOTO_1", "Celebration photo", "image", True),
             P("NAME", "Team / player", "text", True, max_chars=40), _pl_sub()],
         clips=[
             CL("VIDEO_1", 3.5, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_1", 3.5, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_1", 3.5, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 4.5, MOTION_ZIN, bg=BG_EMERALD, tr=TR_NONE,
                text=[T("NAME", 0.3, 3.9, 0.36, 84, "pop"),
                      T("SUBTITLE", 1.2, 3.0, 0.50, 42, "fade-up", "#bbf7d0", uppercase=False)]),
         ]),

    dict(id="sport_stats", name="Stats Flash", cat="Sports",
         desc="Scoreboard-style stat reveal.",
         icon="activity", grad=["#4ade80", "#14532d"], ratio="9:16", dur=12.0,
         pop=92, likes=26180, downloads=11940, edit=4, bpm=130,
         pl=[_pl_title("Headline stat"), P("SUBTITLE", "Detail line", "text", False),
             P("NAME", "Team / context", "text", False)],
         clips=[
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_EMERALD, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.36, 108, "pop")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_DARK, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 3.3, 0.40, 54, "fade-up", "#bbf7d0", uppercase=False)]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_BLACK, tr=TR_NONE,
                text=[T("NAME", 0.3, 3.4, 0.44, 40, "fade-up", "#86efac")]),
         ]),

    # ── Gaming ──────────────────────────────────────────────────────────────
    dict(id="game_clip", name="Game Clip", cat="Gaming",
         desc="Epic gameplay clip with punchy captions.",
         icon="gamepad-2", grad=["#a855f7", "#7c3aed"], ratio="16:9", dur=14.0,
         pop=95, likes=35280, downloads=16210, edit=5, bpm=126,
         pl=[P("VIDEO_1", "Gameplay clip", "video", True), P("PHOTO_1", "Screenshot", "image", False),
             _pl_title("Caption"), P("NAME", "Gamer tag", "text", False, max_chars=30)],
         clips=[
             CL("VIDEO_1", 3.5, MOTION_ZIN, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL,
                text=[T("TITLE", 0.2, 3.1, 0.16, 60, "slide-left")]),
             CL("PHOTO_1", 3.5, MOTION_KEN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_1", 3.5, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_VIOLET, tr=TR_NONE,
                text=[T("NAME", 0.3, 2.9, 0.42, 56, "fade-up", "#d8b4fe")]),
         ]),

    dict(id="game_boss", name="Boss Battle", cat="Gaming",
         desc="Epic boss-fight trailer energy.",
         icon="swords", grad=["#7c3aed", "#312e81"], ratio="16:9", dur=18.0,
         pop=94, likes=30120, downloads=14280, edit=7, bpm=132,
         pl=[P("VIDEO_1", "Battle clip", "video", True), P("VIDEO_2", "Cutscene clip", "video", False),
             P("NAME", "Game / boss name", "text", True, max_chars=40)],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_WIPELEFT),
             CL("VIDEO_2", 4.0, MOTION_KEN, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_1", 4.0, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("VIDEO_2", 3.0, MOTION_PANUP, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_FADEFAST),
             CL("bg", 3.0, MOTION_ZIN, bg=BG_VIOLET, tr=TR_NONE,
                text=[T("NAME", 0.3, 2.4, 0.42, 84, "pop")]),
         ]),

    # ── Music ───────────────────────────────────────────────────────────────
    dict(id="music_lyric", name="Lyric Motion", cat="Music",
         desc="Beat-synced lyric card.",
         icon="music", grad=["#10b981", "#3b82f6"], ratio="9:16", dur=12.0,
         pop=93, likes=27830, downloads=12840, edit=4, bpm=128,
         pl=[_pl_title("Lyric line"), P("SUBTITLE", "Next line", "text", False),
             P("NAME", "Artist / track", "text", False, max_chars=40)],
         clips=[
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_EMERALD, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.36, 84, "pop")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_DARK, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 3.3, 0.40, 56, "fade-up", "#a7f3d0", uppercase=False)]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_NAVY, tr=TR_NONE,
                text=[T("NAME", 0.3, 3.4, 0.44, 40, "fade-up", "#93c5fd")]),
         ]),

    dict(id="music_audio_viz", name="Audio Viz", cat="Music",
         desc="Album cover + beat pulse visualizer.",
         icon="bar-chart-3", grad=["#0ea5e9", "#10b981"], ratio="1:1", dur=12.0,
         pop=90, likes=21560, downloads=9560, edit=3, bpm=120,
         pl=[P("PHOTO_1", "Cover art", "image", True), P("NAME", "Artist / title", "text", True, max_chars=40)],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_VIBRANT, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_CYAN, tr=TR_FADE,
                text=[T("NAME", 0.3, 3.4, 0.40, 64, "fade-up")]),
             CL("PHOTO_1", 4.0, MOTION_ZOUT, filters=F_VIBRANT, effects=E_VIGNETTE_SOFT, tr=TR_NONE),
         ]),

    # ── Motivation ──────────────────────────────────────────────────────────
    dict(id="motiv_dawn", name="Rising Up", cat="Motivation",
         desc="Training montage to a triumphant close.",
         icon="flame", grad=["#f97316", "#eab308"], ratio="9:16", dur=18.0,
         pop=95, likes=34120, downloads=15760, edit=6, bpm=120,
         pl=[P("VIDEO_1", "Training clip", "video", True), P("PHOTO_1", "Sunrise photo", "image", False),
             P("QUOTE", "Motivational quote", "text", True, max_chars=120)],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_ZIN, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_FADE),
             CL("VIDEO_1", 4.0, MOTION_PANUP, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_FADE),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 6.0, MOTION_ZIN, bg=BG_ORANGE, tr=TR_NONE,
                text=[T("QUOTE", 0.3, 5.4, 0.42, 56, "fade-up", "#ffffff", uppercase=False, box=False)]),
         ]),

    dict(id="motiv_discipline", name="Discipline", cat="Motivation",
         desc="Spartan stat-driven motivation card.",
         icon="target", grad=["#f59e0b", "#f43f5e"], ratio="9:16", dur=13.0,
         pop=92, likes=26840, downloads=12370, edit=4, bpm=124,
         pl=[_pl_title("The rule"), P("QUOTE", "Motivation line", "text", True, max_chars=100)],
         clips=[
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_BLACK, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.36, 96, "pop")]),
             CL("bg", 4.5, MOTION_ZIN, bg=BG_ORANGE, tr=TR_FADE,
                text=[T("QUOTE", 0.3, 3.9, 0.42, 52, "fade-up", "#fef3c7", uppercase=False, box=False)]),
             CL("bg", 4.5, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("QUOTE", 0.3, 3.9, 0.42, 56, "fade-up", "#fbbf24", uppercase=False, box=False)]),
         ]),

    # ── Podcast ─────────────────────────────────────────────────────────────
    dict(id="podcast_clip", name="Podcast Clip", cat="Podcast",
         desc="Hook clip with highlight captions.",
         icon="mic", grad=["#14b8a6", "#0d9488"], ratio="9:16", dur=15.0,
         pop=91, likes=22450, downloads=10820, edit=5, bpm=118,
         pl=[P("VIDEO_1", "Podcast clip", "video", True), P("PHOTO_1", "Episode art", "image", False),
             _pl_title("Hot take"), _pl_sub("Episode name")],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_ZIN, filters=F_MUTED, effects=E_VIGNETTE, tr=TR_FADE,
                text=[T("TITLE", 0.2, 3.6, 0.16, 58, "slide-left")]),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_MUTED, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("VIDEO_1", 3.5, MOTION_ZOUT, filters=F_MUTED, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_EMERALD, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 2.9, 0.42, 44, "fade-up", "#99f6e4", uppercase=False)]),
         ]),

    dict(id="podcast_intro", name="Podcast Intro", cat="Podcast",
         desc="Show-branded intro card.",
         icon="radio", grad=["#2dd4bf", "#115e59"], ratio="16:9", dur=10.0,
         pop=88, likes=15430, downloads=6890, edit=3, bpm=112,
         pl=[P("LOGO", "Show logo", "image", False), P("PRODUCT_NAME", "Show name", "text", True, max_chars=40),
             _pl_sub("Tagline")],
         clips=[
             CL("bg", 5.0, MOTION_ZSLOW, bg=BG_EMERALD, tr=TR_FADE,
                stickers=[STICKER_LOGO],
                text=[T("PRODUCT_NAME", 0.5, 4.1, 0.34, 88, "fade-up"),
                      T("SUBTITLE", 1.2, 3.4, 0.48, 40, "fade-up", "#99f6e4", uppercase=False)]),
             CL("bg", 5.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("PRODUCT_NAME", 0.4, 4.2, 0.44, 64, "fade-up")]),
         ]),

    # ── Slideshow ───────────────────────────────────────────────────────────
    dict(id="slide_classic", name="Classic Slideshow", cat="Slideshow",
         desc="Clean photo slideshow with soft crossfades.",
         icon="presentation", grad=["#f59e0b", "#fb923c"], ratio="16:9", dur=20.0,
         pop=90, likes=18940, downloads=9120, edit=6, bpm=104,
         pl=[P("PHOTO_1", "Photo 1", "image", True), P("PHOTO_2", "Photo 2", "image", True),
             P("PHOTO_3", "Photo 3", "image", True), _pl_title("Title card")],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_2", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_3", 4.0, MOTION_ZOUT, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_1", 4.0, MOTION_PANR, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_ORANGE, tr=TR_NONE,
                text=[T("TITLE", 0.3, 3.4, 0.44, 76, "fade-up")]),
         ]),

    dict(id="slide_memory", name="Memory Recap", cat="Slideshow",
         desc="Nostalgic throwback slideshow.",
         icon="images", grad=["#fb923c", "#f43f5e"], ratio="1:1", dur=18.0,
         pop=89, likes=16720, downloads=7340, edit=6, bpm=108,
         pl=[P("PHOTO_1", "Photo 1", "image", True), P("PHOTO_2", "Photo 2", "image", True),
             P("PHOTO_3", "Photo 3", "image", True), P("DATE", "Year / event", "text", False, max_chars=30)],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_2", 4.0, MOTION_ZOUT, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("PHOTO_3", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_DISSOLVE),
             CL("bg", 6.0, MOTION_ZIN, bg=BG_ORANGE, tr=TR_NONE,
                text=[T("DATE", 0.3, 5.4, 0.42, 68, "pop")]),
         ]),

    # ── Product Showcase ────────────────────────────────────────────────────
    dict(id="prod_reveal", name="Product Reveal", cat="Product Showcase",
         desc="Sleek product reveal with premium motion.",
         icon="package", grad=["#f59e0b", "#f97316"], ratio="9:16", dur=15.0,
         pop=94, likes=28760, downloads=13240, edit=5, bpm=122,
         pl=[P("PRODUCT_IMG", "Product image", "image", True), P("LOGO", "Brand logo", "image", False),
             P("PRODUCT_NAME", "Product name", "text", True, max_chars=40),
             P("PRICE", "Price", "text", False, max_chars=20)],
         clips=[
             CL("bg", 2.5, MOTION_ZSLOW, bg=BG_BLACK, tr=TR_FADE,
                stickers=[STICKER_LOGO],
                text=[T("PRODUCT_NAME", 0.4, 1.9, 0.32, 88, "pop")]),
             CL("PRODUCT_IMG", 3.5, MOTION_ZIN, filters=F_POP, effects=E_VIGNETTE, tr=TR_CIRCLE),
             CL("PRODUCT_IMG", 3.5, MOTION_ZOUT, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 5.5, MOTION_ZIN, bg=BG_ORANGE, tr=TR_NONE,
                text=[T("PRODUCT_NAME", 0.3, 4.8, 0.34, 64, "fade-up"),
                      T("PRICE", 1.2, 3.9, 0.48, 72, "pop", "#fde68a")]),
         ]),

    dict(id="prod_360", name="360 Spin", cat="Product Showcase",
         desc="Cinematic product spin loop.",
         icon="rotate-cw", grad=["#f97316", "#f59e0b"], ratio="1:1", dur=12.0,
         pop=91, likes=21430, downloads=9870, edit=4, bpm=114,
         pl=[P("VIDEO_1", "Product video", "video", True), P("PHOTO_1", "Detail shot", "image", False),
             P("PRODUCT_NAME", "Product name", "text", True, max_chars=40)],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_ZIN, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_FADE),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_CINEMA, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("VIDEO_1", 4.0, MOTION_ZOUT, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_NONE,
                text=[T("PRODUCT_NAME", 0.3, 3.4, 0.16, 56, "fade-up")]),
         ]),

    # ── Fashion ─────────────────────────────────────────────────────────────
    dict(id="fashion_lookbook", name="Lookbook", cat="Fashion",
         desc="Trendy outfit lookbook pan.",
         icon="shirt", grad=["#f472b6", "#e879f9"], ratio="9:16", dur=15.0,
         pop=93, likes=26980, downloads=12140, edit=5, bpm=126,
         pl=[P("VIDEO_1", "Fashion clip", "video", True), P("PHOTO_1", "Look photo", "image", True),
             P("NAME", "Brand / drop", "text", True, max_chars=40), _pl_sub()],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_PANUP, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_FADE),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_VIBRANT, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("VIDEO_1", 3.5, MOTION_ZIN, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_PINK, tr=TR_NONE,
                text=[T("NAME", 0.3, 2.9, 0.36, 72, "fade-up"),
                      T("SUBTITLE", 1.0, 2.2, 0.50, 38, "fade-up", "#fbcfe8", uppercase=False)]),
         ]),

    dict(id="fashion_flatlay", name="Flatlay Pop", cat="Fashion",
         desc="Stylish flatlay card with label captions.",
         icon="layers", grad=["#e879f9", "#f472b6"], ratio="1:1", dur=13.0,
         pop=89, likes=18540, downloads=8360, edit=4, bpm=118,
         pl=[P("PHOTO_1", "Flatlay photo", "image", True), P("PRODUCT_NAME", "Collection", "text", True, max_chars=40),
             P("PRICE", "Price / note", "text", False, max_chars=20)],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_PINK, tr=TR_FADE,
                text=[T("PRODUCT_NAME", 0.3, 3.3, 0.38, 76, "fade-up"),
                      T("PRICE", 1.0, 2.6, 0.52, 44, "pop", "#fce7f3")]),
             CL("PHOTO_1", 5.0, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_NONE),
         ]),

    # ── Technology ──────────────────────────────────────────────────────────
    dict(id="tech_feature", name="Tech Feature", cat="Technology",
         desc="Clean product feature explainer.",
         icon="cpu", grad=["#06b6d4", "#2563eb"], ratio="16:9", dur=15.0,
         pop=92, likes=23180, downloads=10740, edit=5, bpm=116,
         pl=[P("PHOTO_1", "Product photo", "image", True), P("PRODUCT_NAME", "Product name", "text", True, max_chars=40),
             P("SUBTITLE", "Feature line", "text", False, max_chars=80)],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZSLOW, bg=BG_CYAN, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 2.9, 0.40, 54, "fade-up", "#a5f3fc", uppercase=False)]),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_NAVY, tr=TR_NONE,
                text=[T("PRODUCT_NAME", 0.3, 2.9, 0.42, 72, "pop")]),
         ]),

    dict(id="tech_update", name="Update Drop", cat="Technology",
         desc="App-update announcement card.",
         icon="refresh-cw", grad=["#2563eb", "#06b6d4"], ratio="9:16", dur=12.0,
         pop=90, likes=19830, downloads=9240, edit=4, bpm=120,
         pl=[_pl_title("Update name"), P("SUBTITLE", "What's new", "text", False, max_chars=90),
             P("BUTTON_TEXT", "Button label", "text", False, max_chars=24, default_value="Update now")],
         clips=[
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_NAVY, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.36, 84, "pop")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_CYAN, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 3.3, 0.38, 52, "fade-up", "#cffafe", uppercase=False),
                      T("BUTTON_TEXT", 1.6, 2.0, 0.56, 44, "pop", "#ffffff", box=True, stroke="#2563eb")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("TITLE", 0.3, 3.4, 0.44, 60, "fade-up")]),
         ]),

    # ── Real Estate ─────────────────────────────────────────────────────────
    dict(id="re_listing", name="Listing Tour", cat="Real Estate",
         desc="Property walkthrough with price card.",
         icon="building-2", grad=["#34d399", "#0f766e"], ratio="9:16", dur=16.0,
         pop=93, likes=24780, downloads=11830, edit=6, bpm=112,
         pl=[P("VIDEO_1", "Property video", "video", True), P("PHOTO_1", "Hero photo", "image", True),
             P("PRODUCT_NAME", "Property name", "text", True, max_chars=60),
             P("PRICE", "Price", "text", True, max_chars=24)],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_ZIN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("VIDEO_1", 4.0, MOTION_PANR, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_EMERALD, tr=TR_NONE,
                text=[T("PRODUCT_NAME", 0.3, 3.4, 0.34, 60, "fade-up", "#ffffff", uppercase=False),
                      T("PRICE", 1.2, 2.5, 0.48, 76, "pop", "#a7f3d0")]),
         ]),

    dict(id="re_open_house", name="Open House", cat="Real Estate",
         desc="Bright open-house invite card.",
         icon="key-round", grad=["#059669", "#34d399"], ratio="16:9", dur=12.0,
         pop=90, likes=17430, downloads=8120, edit=4, bpm=112,
         pl=[P("PHOTO_1", "Property photo", "image", True), P("DATE", "Date & time", "text", True, max_chars=40),
             P("LOCATION", "Address", "text", True, max_chars=60)],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_EMERALD, tr=TR_FADE,
                text=[T("DATE", 0.3, 3.3, 0.36, 64, "pop", "#a7f3d0"),
                      T("LOCATION", 1.0, 2.6, 0.50, 40, "fade-up", "#ffffff", uppercase=False)]),
             CL("PHOTO_1", 4.0, MOTION_ZOUT, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_NONE),
         ]),

    # ── News ────────────────────────────────────────────────────────────────
    dict(id="news_breaking", name="Breaking News", cat="News",
         desc="Urgent breaking-news flash.",
         icon="alert-triangle", grad=["#f87171", "#991b1b"], ratio="9:16", dur=12.0,
         pop=89, likes=16980, downloads=7450, edit=4, bpm=120,
         pl=[P("PHOTO_1", "News photo", "image", True), _pl_title("Headline"),
             P("SUBTITLE", "Details", "text", False, max_chars=90)],
         clips=[
             CL("bg", 2.0, MOTION_ZSLOW, bg=BG_BLACK, tr=TR_FADEFAST,
                text=[T("TITLE", 0.2, 1.6, 0.36, 84, "pop", "#fca5a5")]),
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_CINEMA, effects=E_VIGNETTE, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 3.4, 0.84, 46, "fade-up", "#ffffff", uppercase=False)]),
             CL("bg", 6.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 5.4, 0.40, 52, "fade-up", "#fecaca", uppercase=False)]),
         ], overlays=[FADE_OUT_BAR]),

    # ── Flyers ──────────────────────────────────────────────────────────────
    dict(id="flyer_event", name="Event Flyer", cat="Flyers",
         desc="Bold event flyer motion card.",
         icon="megaphone", grad=["#fb923c", "#f43f5e"], ratio="9:16", dur=12.0,
         pop=90, likes=18730, downloads=8620, edit=4, bpm=122,
         pl=[P("PHOTO_1", "Event photo", "image", True), P("NAME", "Event name", "text", True, max_chars=50),
             P("DATE", "Date", "text", True, max_chars=30), P("LOCATION", "Venue", "text", False, max_chars=50)],
         clips=[
             CL("bg", 2.5, MOTION_ZSLOW, bg=BG_ORANGE, tr=TR_FADEFAST,
                text=[T("NAME", 0.3, 1.9, 0.32, 84, "pop")]),
             CL("PHOTO_1", 4.0, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE,
                text=[T("DATE", 0.3, 3.4, 0.80, 56, "pop", "#ffffff"),
                      T("LOCATION", 1.2, 2.5, 0.90, 38, "fade-up", "#ffffff", uppercase=False)]),
             CL("bg", 5.5, MOTION_ZIN, bg=BG_ORANGE, tr=TR_NONE,
                text=[T("NAME", 0.3, 4.9, 0.32, 72, "fade-up"),
                      T("DATE", 1.2, 4.0, 0.46, 64, "pop", "#fde68a")]),
         ]),

    # ── Posters ─────────────────────────────────────────────────────────────
    dict(id="poster_quote", name="Poster Quote", cat="Posters",
         desc="Artistic quote poster.",
         icon="image", grad=["#8b5cf6", "#d946ef"], ratio="4:5", dur=11.0,
         pop=88, likes=15240, downloads=7180, edit=3, bpm=110,
         pl=[P("PHOTO_1", "Art photo", "image", False), P("QUOTE", "Quote", "text", True, max_chars=120),
             P("NAME", "Author", "text", False, max_chars=40)],
         clips=[
             CL("bg", 3.5, MOTION_ZSLOW, bg=BG_VIOLET, tr=TR_FADE,
                text=[T("QUOTE", 0.3, 2.9, 0.40, 56, "fade-up", "#ffffff", uppercase=False, box=False)]),
             CL("PHOTO_1", 4.0, MOTION_KEN, filters=F_MUTED, effects=E_VIGNETTE_SOFT, tr=TR_FADE,
                text=[T("NAME", 0.3, 3.4, 0.84, 40, "fade-up", "#ffffff")]),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("QUOTE", 0.3, 2.9, 0.40, 52, "fade-up", "#d8b4fe", uppercase=False, box=False)]),
         ]),

    # ── Certificates ────────────────────────────────────────────────────────
    dict(id="cert_award", name="Award Certificate", cat="Certificates",
         desc="Elegant award certificate motion.",
         icon="award", grad=["#f59e0b", "#92400e"], ratio="16:9", dur=14.0,
         pop=89, likes=16840, downloads=7940, edit=4, bpm=104,
         pl=[P("NAME", "Recipient name", "text", True, max_chars=50),
             P("PRODUCT_NAME", "Award / course title", "text", True, max_chars=60),
             P("DATE", "Date", "text", False, max_chars=30)],
         clips=[
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_GOLD, tr=TR_FADE,
                text=[T("PRODUCT_NAME", 0.3, 3.3, 0.30, 64, "fade-up", "#fef3c7"),
                      T("NAME", 1.2, 2.4, 0.44, 96, "pop", "#fde68a")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_PAPER, tr=TR_FADE,
                text=[T("PRODUCT_NAME", 0.3, 3.3, 0.36, 72, "fade-up", "#78350f"),
                      T("DATE", 1.4, 2.2, 0.52, 44, "fade-up", "#92400e")]),
             CL("bg", 6.0, MOTION_ZIN, bg=BG_GOLD, tr=TR_NONE,
                text=[T("NAME", 0.3, 5.4, 0.36, 88, "pop", "#fde68a")]),
         ], overlays=[FADE_IN_BAR]),

    # ── Logos ───────────────────────────────────────────────────────────────
    dict(id="logo_reveal", name="Logo Reveal", cat="Logos",
         desc="Minimal logo reveal with glow pop.",
         icon="pen-tool", grad=["#22d3ee", "#0ea5e9"], ratio="1:1", dur=10.0,
         pop=90, likes=19340, downloads=9150, edit=3, bpm=110,
         pl=[P("LOGO", "Logo", "image", True), P("NAME", "Brand name", "text", False, max_chars=40)],
         clips=[
             CL("bg", 5.0, MOTION_ZSLOW, bg=BG_CYAN, tr=TR_FADE,
                stickers=[STICKER_LOGO],
                text=[T("NAME", 0.8, 3.8, 0.36, 88, "fade-up")]),
             CL("bg", 5.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                stickers=[STICKER_LOGO],
                text=[T("NAME", 0.6, 4.0, 0.44, 56, "pop", "#a5f3fc")]),
         ]),

    # ── Invitations ─────────────────────────────────────────────────────────
    dict(id="invite_wedding", name="Wedding Invite", cat="Invitations",
         desc="Romantic invitation motion card.",
         icon="mail", grad=["#fbbf24", "#f59e0b"], ratio="9:16", dur=13.0,
         pop=93, likes=22890, downloads=10940, edit=4, bpm=106,
         pl=[P("NAME", "Couple names", "text", True, max_chars=40), P("DATE", "Date & time", "text", True, max_chars=40),
             P("LOCATION", "Venue", "text", True, max_chars=50)],
         clips=[
             CL("bg", 2.5, MOTION_ZSLOW, bg=BG_GOLD, tr=TR_FADE,
                text=[T("NAME", 0.3, 1.9, 0.36, 76, "pop", "#fef3c7")]),
             CL("bg", 4.5, MOTION_ZIN, bg=BG_DARK, tr=TR_FADE,
                text=[T("DATE", 0.3, 3.9, 0.38, 56, "fade-up", "#fde68a"),
                      T("LOCATION", 1.2, 3.0, 0.52, 38, "fade-up", "#ffffff", uppercase=False)]),
             CL("bg", 6.0, MOTION_ZIN, bg=BG_GOLD, tr=TR_NONE,
                text=[T("NAME", 0.3, 5.4, 0.40, 56, "fade-up", "#fef3c7")]),
         ], overlays=[FADE_OUT_BAR]),

    dict(id="invite_party", name="Party Invite", cat="Invitations",
         desc="Bright party invitation card.",
         icon="party-popper", grad=["#f472b6", "#a78bfa"], ratio="9:16", dur=12.0,
         pop=91, likes=20410, downloads=9650, edit=3, bpm=124,
         pl=[P("NAME", "Party name", "text", True, max_chars=50), P("DATE", "Date & time", "text", True, max_chars=40),
             P("LOCATION", "Venue", "text", False, max_chars=50)],
         clips=[
             CL("bg", 2.5, MOTION_ZSLOW, bg=BG_PINK, tr=TR_FADEFAST,
                text=[T("NAME", 0.3, 1.9, 0.36, 84, "pop")]),
             CL("bg", 4.5, MOTION_ZIN, bg=BG_VIOLET, tr=TR_FADE,
                text=[T("DATE", 0.3, 3.9, 0.38, 60, "fade-up", "#e9d5ff"),
                      T("LOCATION", 1.2, 3.0, 0.52, 40, "fade-up", "#ffffff", uppercase=False)]),
             CL("bg", 5.0, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("NAME", 0.3, 4.4, 0.40, 64, "fade-up")]),
         ]),

    # ── Business Cards ──────────────────────────────────────────────────────
    dict(id="bcard_contact", name="Contact Card", cat="Business Cards",
         desc="Animated digital business card.",
         icon="id-card", grad=["#64748b", "#334155"], ratio="9:16", dur=12.0,
         pop=87, likes=13240, downloads=6110, edit=3, bpm=110,
         pl=[P("LOGO", "Logo", "image", False), P("NAME", "Full name", "text", True, max_chars=40),
             P("PRODUCT_NAME", "Job title / company", "text", True, max_chars=50),
             P("LOCATION", "Contact detail", "text", False, max_chars=60)],
         clips=[
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_DARK, tr=TR_FADE,
                stickers=[STICKER_LOGO],
                text=[T("NAME", 0.4, 3.2, 0.34, 76, "fade-up"),
                      T("PRODUCT_NAME", 1.2, 2.4, 0.48, 40, "fade-up", "#94a3b8")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_NAVY, tr=TR_FADE,
                text=[T("PRODUCT_NAME", 0.3, 3.3, 0.38, 56, "fade-up", "#93c5fd")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_BLACK, tr=TR_NONE,
                text=[T("LOCATION", 0.3, 3.4, 0.44, 40, "fade-up", "#cbd5e1", uppercase=False)]),
         ]),

    # ── Social Media Posts ──────────────────────────────────────────────────
    dict(id="socm_announce", name="Announcement", cat="Social Media Posts",
         desc="Scroll-stopping announcement card.",
         icon="megaphone", grad=["#8b5cf6", "#ec4899"], ratio="9:16", dur=12.0,
         pop=92, likes=21940, downloads=10380, edit=3, bpm=122,
         pl=[_pl_title("Announcement"), P("SUBTITLE", "Details", "text", False, max_chars=90),
             P("BUTTON_TEXT", "Button text", "text", False, max_chars=24, default_value="Learn more")],
         clips=[
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_VIOLET, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.34, 92, "pop")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_DARK, tr=TR_FADE,
                text=[T("SUBTITLE", 0.3, 3.3, 0.40, 52, "fade-up", "#c4b5fd", uppercase=False),
                      T("BUTTON_TEXT", 1.8, 1.8, 0.56, 44, "pop", "#ffffff", box=True, stroke="#8b5cf6")]),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_ROSE, tr=TR_NONE,
                text=[T("TITLE", 0.3, 3.4, 0.44, 64, "fade-up")]),
         ]),

    dict(id="socm_promo", name="Sale Promo", cat="Social Media Posts",
         desc="Discount promo with bold price pop.",
         icon="tag", grad=["#ec4899", "#f43f5e"], ratio="4:5", dur=11.0,
         pop=91, likes=20270, downloads=9740, edit=3, bpm=122,
         pl=[P("PHOTO_1", "Product photo", "image", False), P("TITLE", "Offer headline", "text", True, max_chars=50),
             P("PRICE", "Discounted price", "text", False, max_chars=20),
             P("BUTTON_TEXT", "Button text", "text", False, max_chars=24, default_value="Shop now")],
         clips=[
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE,
                text=[T("PRICE", 0.3, 3.4, 0.78, 88, "pop", "#fde047")]),
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_ROSE, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.38, 72, "pop"),
                      T("BUTTON_TEXT", 1.6, 2.0, 0.56, 44, "pop", "#ffffff", box=True, stroke="#f43f5e")]),
             CL("PHOTO_1", 3.0, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_NONE),
         ]),

    # ── Africa ──────────────────────────────────────────────────────────────
    dict(id="africa_safari", name="Safari Film", cat="Africa",
         desc="Golden-hour safari montage.",
         icon="sun", grad=["#f59e0b", "#fb923c"], ratio="9:16", dur=16.0,
         pop=94, likes=27840, downloads=13120, edit=6, bpm=110,
         pl=[P("VIDEO_1", "Safari clip", "video", True), P("PHOTO_1", "Landscape photo", "image", True),
             P("LOCATION", "Destination", "text", True, max_chars=40), _pl_sub()],
         clips=[
             CL("VIDEO_1", 4.0, MOTION_KEN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("PHOTO_1", 4.0, MOTION_ZIN, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("VIDEO_1", 4.0, MOTION_PANR, filters=F_WARM, effects=E_VIGNETTE_SOFT, tr=TR_FADE),
             CL("bg", 4.0, MOTION_ZIN, bg=BG_ORANGE, tr=TR_NONE,
                text=[T("LOCATION", 0.3, 3.4, 0.36, 80, "fade-up"),
                      T("SUBTITLE", 1.0, 2.7, 0.50, 40, "fade-up", "#fde68a", uppercase=False)]),
         ]),

    dict(id="africa_drums", name="Drums & Dance", cat="Africa",
         desc="Kinetic cultural celebration.",
         icon="music-2", grad=["#f97316", "#10b981"], ratio="9:16", dur=14.0,
         pop=93, likes=24310, downloads=11540, edit=5, bpm=128,
         pl=[P("VIDEO_1", "Dance clip", "video", True), P("PHOTO_1", "Festival photo", "image", False),
             _pl_title("Celebration line"), P("NAME", "Event name", "text", False, max_chars=50)],
         clips=[
             CL("VIDEO_1", 3.5, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_1", 3.5, MOTION_KEN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_1", 3.5, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_ORANGE, tr=TR_NONE,
                text=[T("TITLE", 0.3, 2.9, 0.34, 72, "pop"),
                      T("NAME", 1.2, 2.0, 0.48, 40, "fade-up", "#fde68a")]),
         ]),

    # ── Nigeria ─────────────────────────────────────────────────────────────
    dict(id="ng_lagos", name="Lagos Vibe", cat="Nigeria",
         desc="Kinetic Lagos street-energy reel.",
         icon="music", grad=["#10b981", "#3b82f6"], ratio="9:16", dur=14.0,
         pop=94, likes=27650, downloads=12980, edit=5, bpm=130,
         pl=[P("VIDEO_1", "Street clip", "video", True), P("PHOTO_1", "Street photo", "image", True),
             _pl_title("Vibe caption"), _pl_sub()],
         clips=[
             CL("VIDEO_1", 3.5, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("PHOTO_1", 3.5, MOTION_KEN, filters=F_VIBRANT, effects=E_VIGNETTE, tr=TR_SLIDEL),
             CL("VIDEO_1", 3.5, MOTION_ZOUT, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADE),
             CL("bg", 3.5, MOTION_ZIN, bg=BG_EMERALD, tr=TR_NONE,
                text=[T("TITLE", 0.3, 2.9, 0.34, 76, "pop"),
                      T("SUBTITLE", 1.2, 2.0, 0.48, 40, "fade-up", "#a7f3d0", uppercase=False)]),
         ]),

    dict(id="ng_green", name="Green Sweat", cat="Nigeria",
         desc="Naija energy hype card.",
         icon="flame", grad=["#22c55e", "#16a34a"], ratio="9:16", dur=12.0,
         pop=92, likes=21540, downloads=9820, edit=4, bpm=128,
         pl=[P("PHOTO_1", "Action photo", "image", True), _pl_title("Hype line"),
             P("SUBTITLE", "Details", "text", False, max_chars=80)],
         clips=[
             CL("PHOTO_1", 3.0, MOTION_ZSHARP, filters=F_POP, effects=E_VIGNETTE, tr=TR_FADEFAST),
             CL("bg", 4.0, MOTION_ZSLOW, bg=BG_EMERALD, tr=TR_FADE,
                text=[T("TITLE", 0.3, 3.3, 0.36, 96, "pop")]),
             CL("bg", 5.0, MOTION_ZIN, bg=BG_DARK, tr=TR_NONE,
                text=[T("SUBTITLE", 0.3, 4.4, 0.42, 48, "fade-up", "#bbf7d0", uppercase=False)]),
         ]),
]

MUSIC_FREE_SOURCES = [
    "", "", "", "", "",
]


# ── Builders ───────────────────────────────────────────────────────────────

def _resolve_template(spec: dict) -> dict:
    cat = spec["cat"]
    meta = CAT_BY_NAME[cat]
    spec.setdefault("icon", meta["icon"])
    spec.setdefault("grad", meta["grad"])

    fps = 30
    width, height = _ratio_dims(spec["ratio"])

    clips = []
    for cl in spec["clips"]:
        c = dict(cl)
        clips.append(c)

    total = 0.0
    overlaps = 0.0
    for i, cl in enumerate(clips):
        total += float(cl["duration"])
        tr = cl.get("transition_out") or TR_NONE
        if i < len(clips) - 1:
            overlaps += float(tr.get("duration", 0.0))
    duration = max(0.0, total - overlaps)

    pls = []
    for p in spec["pl"]:
        d = dict(p)
        d.setdefault("hint", "")
        pls.append(d)

    required_media = sum(1 for p in pls if p["type"] in ("image", "video") and p["required"])

    bpm = spec.get("bpm", 120)
    beat = 60.0 / max(bpm, 1)
    markers = []
    t = 0.0
    while t < duration:
        markers.append(round(t, 3))
        t += beat
    markers.append(round(duration, 3))

    return {
        "schema_version": 1,
        "id": spec["id"],
        "name": spec["name"],
        "category": cat,
        "tags": [cat.lower().replace(" ", "-"), "template"],
        "description": spec["desc"],
        "icon": spec["icon"],
        "grad": spec["grad"],
        "thumbnail": f"/static/templates/{spec['id']}/thumbnail.webp",
        "preview": f"/static/templates/{spec['id']}/preview.mp4",
        "duration": round(duration, 2),
        "aspect_ratio": spec["ratio"],
        "width": width,
        "height": height,
        "fps": fps,
        "popularity": spec.get("pop", 90),
        "likes": spec.get("likes", 10000),
        "downloads": spec.get("downloads", 5000),
        "edit_time_min": spec.get("edit", 4),
        "media_required": required_media,
        "placeholders": pls,
        "music": {
            "bpm": bpm,
            "tracks": [
                {"src": "", "placeholder": "MUSIC", "start": 0.0,
                 "volume": 0.85, "fade_in": 0.2, "fade_out": 0.5}
            ],
        },
        "beat_markers": markers,
        "timeline": clips,
        "overlays": spec.get("overlays", []),
    }


def _ratio_dims(ratio: str):
    a, b = ratio.split(":")
    a, b = float(a), float(b)
    if b >= a:
        return 1080, int(round(1080 * b / a / 16) * 16)
    return 1080, int(round(1080 * a / b / 16) * 16)


def _validate(t: dict, seen: set) -> None:
    if t["id"] in seen:
        raise SystemExit(f"duplicate template id: {t['id']}")
    seen.add(t["id"])
    keys = {p["key"] for p in t["placeholders"]}
    for cl in t["timeline"]:
        for tl in cl.get("text_layers", []):
            if tl["key"] not in keys:
                raise SystemExit(f"{t['id']}: text key {tl['key']!r} missing from placeholders")
        if cl.get("media") not in ("bg", "PHOTO_1", "PHOTO_2", "PHOTO_3", "PHOTO_4", "PHOTO_5",
                                   "VIDEO_1", "VIDEO_2", "VIDEO_3", "PRODUCT_IMG"):
            raise SystemExit(f"{t['id']}: unknown media {cl.get('media')!r}")


# ── Thumbnail art ──────────────────────────────────────────────────────────

def _hex(c: str) -> tuple:
    c = c.strip().lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _make_thumb(t: dict, dest: Path) -> None:
    W, H = THUMB_W, THUMB_H
    g0, g1 = (_hex(t["grad"][0]), _hex(t["grad"][1]))
    small = Image.new("RGB", (8, 8))
    sp = small.load()
    for y in range(8):
        for x in range(8):
            f = (x + y) / 14.0
            sp[x, y] = tuple(int(g0[i] * (1 - f) + g1[i] * f) for i in range(3))
    base = small.resize((W, H), Image.LANCZOS).convert("RGBA")

    dark = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dark)
    for i in range(H):
        f = i / H
        a = int(40 + 150 * f)
        dd.line([(0, i), (W, i)], fill=(5, 8, 20, a))
    base.alpha_composite(dark)

    d = ImageDraw.Draw(base)
    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    cx, cy, r = W * 0.5, H * 0.30, W * 0.34
    rd.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, 40), width=3)
    rd.ellipse((cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62),
               fill=(255, 255, 255, 16))
    base.alpha_composite(ring)

    def _font(path, size):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()

    name = t["name"].upper()
    big = _font(FONT_BOLD, 58)
    smallf = _font(FONT_REG, 34)

    name_img = Image.new("RGBA", (W, 160), (0, 0, 0, 0))
    nd = ImageDraw.Draw(name_img)
    bbox = nd.textbbox((0, 0), name, font=big)
    tw = bbox[2] - bbox[0]
    nd.text(((W - tw) / 2 - bbox[0], 30 - bbox[1]), name, font=big, fill=(255, 255, 255, 245))
    base.alpha_composite(name_img, (0, int(H * 0.62)))

    cat = t["category"].upper()
    cimg = Image.new("RGBA", (W, 80), (0, 0, 0, 0))
    cd = ImageDraw.Draw(cimg)
    bbox = cd.textbbox((0, 0), cat, font=smallf)
    cw = bbox[2] - bbox[0]
    cd.rounded_rectangle((W / 2 - cw / 2 - 26, 8, W / 2 + cw / 2 + 26, 58), fill=(255, 255, 255, 28), radius=24)
    cd.text((W / 2 - cw / 2 - bbox[0], 18 - bbox[1]), cat, font=smallf, fill=(255, 255, 255, 210))
    base.alpha_composite(cimg, (0, int(H * 0.78)))

    base.convert("RGB").save(dest, format="WEBP", quality=86)


def _color_dots(t: dict, dest: Path) -> None:
    pass  # (kept for future art variants)


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    out = OUT.resolve()
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    seen = set()
    catalog = []
    for spec in TEMPLATES:
        t = _resolve_template(spec)
        _validate(t, seen)
        folder = out / t["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "template.json").write_text(
            json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        thumb = folder / "thumbnail.webp"
        _make_thumb(t, thumb)
        catalog.append({
            "id": t["id"], "name": t["name"], "category": t["category"],
            "icon": t["icon"], "grad": t["grad"], "duration": t["duration"],
            "aspect_ratio": t["aspect_ratio"], "popularity": t["popularity"],
            "likes": t["likes"], "downloads": t["downloads"],
            "edit_time_min": t["edit_time_min"], "media_required": t["media_required"],
        })

    (out / "catalog.json").write_text(
        json.dumps({"count": len(catalog), "templates": catalog}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(f"Generated {len(catalog)} templates -> {out}")
    for t in catalog:
        print(f"  {t['id']:24s} {t['category']:20s} {t['duration']:>5.1f}s {t['aspect_ratio']}")


if __name__ == "__main__":
    main()
