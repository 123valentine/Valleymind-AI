"""Local, zero-API-limit photo finishing used by Studio's Auto Enhance and Retouch.

This module intentionally uses only Pillow so the Studio tools keep working
when every remote inference provider is unavailable or out of credit.
"""

from __future__ import annotations

import io
from typing import Tuple

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

PROFILES = {
    "auto": {
        "brightness": 1.10,
        "contrast": 1.10,
        "color": 1.08,
        "sharpness": 1.06,
        "unsharp": (1.2, 105, 3),
        "autocontrast_cutoff": 1,
    },
    "retouch": {
        "brightness": 1.06,
        "contrast": 1.05,
        "color": 1.04,
        "sharpness": 1.03,
        "unsharp": (1.0, 70, 4),
        "autocontrast_cutoff": 2,
    },
}


def _adaptive_brightness_factor(image: Image.Image) -> float:
    """Nudge exposure toward a neutral mid-tone without blowing highlights."""
    grey = ImageOps.grayscale(image)
    mean = ImageStat.Stat(grey).mean[0]
    if mean < 95:
        return 1.16
    if mean < 125:
        return 1.09
    if mean > 185:
        return 0.92
    if mean > 160:
        return 0.96
    return 1.0


def enhance_photo_bytes(image_bytes: bytes, mode: str = "auto") -> Tuple[bytes, str]:
    """Return enhanced image bytes and MIME type for the requested profile.

    ``mode`` accepts ``auto`` and ``retouch``; unknown values fall back to
    ``auto`` so a stale client can never break the endpoint.
    """
    profile = PROFILES.get(mode, PROFILES["auto"])
    with Image.open(io.BytesIO(image_bytes)) as source:
        source = ImageOps.exif_transpose(source)
        has_alpha = source.mode in ("RGBA", "LA") or (
            source.mode == "P" and "transparency" in source.info
        )
        image = source.convert("RGBA" if has_alpha else "RGB")

        # Mild tone normalization first, then adaptive exposure correction.
        image = ImageOps.autocontrast(image, cutoff=profile["autocontrast_cutoff"])
        brightness = profile["brightness"] * _adaptive_brightness_factor(image)
        image = ImageEnhance.Brightness(image).enhance(brightness)
        image = ImageEnhance.Contrast(image).enhance(profile["contrast"])
        image = ImageEnhance.Color(image).enhance(profile["color"])
        image = ImageEnhance.Sharpness(image).enhance(profile["sharpness"])

        if has_alpha:
            red, green, blue, alpha = image.split()
            rgb = Image.merge("RGB", (red, green, blue))
            rgb = rgb.filter(ImageFilter.UnsharpMask(*profile["unsharp"]))
            rr, gg, bb = rgb.split()
            result = Image.merge("RGBA", (rr, gg, bb, alpha))
            out = io.BytesIO()
            result.save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png"

        image = image.filter(ImageFilter.UnsharpMask(*profile["unsharp"]))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=93, optimize=True, subsampling=0)
        return out.getvalue(), "image/jpeg"


def make_preview_data_uri(image_bytes: bytes, mime_type: str) -> str:
    """Build a data URI suitable for the Studio before/after modal."""
    import base64

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
