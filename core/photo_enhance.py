"""Local, zero-API-limit photo finishing used by Studio's Auto Enhance and Retouch.

This module intentionally uses only Pillow and numpy so the Studio tools keep
working when every remote inference provider is unavailable or out of credit.
"""

from __future__ import annotations

import io
import os
from collections import deque
from typing import Tuple

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

REMOVE_BG_MODES = frozenset({
    "remove-bg", "remove_bg", "remove background", "background-remove",
    "background_remove", "cutout", "transparent",
})
MAX_SEGMENT_SIDE = 512
RESAMPLE = getattr(Image, "Resampling", Image)

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


def _rembg_cutout(image: Image.Image) -> Image.Image | None:
    """Use rembg when it is installed; otherwise fall back to local matting.

    The model is optional and never downloaded during tests. Failures are
    internal only and hand off to the deterministic Pillow/numpy matte.
    """
    if os.getenv("VM_DISABLE_REMBG", "").strip().lower() in ("1", "true", "yes", "on"):
        return None
    try:
        from rembg import remove  # type: ignore[import-not-found]

        result = remove(image, alpha_matting=True)
        if isinstance(result, bytes):
            result = Image.open(io.BytesIO(result))
        return result.convert("RGBA")
    except Exception as exc:
        print(f"[STUDIO PHOTO] Optional segmentation unavailable, using local matte: {exc}")
        return None


def _border_connected(seed: np.ndarray) -> np.ndarray:
    """Flood-fill the seed mask from the image border (GrabCut background prior)."""
    height, width = seed.shape
    visited = np.zeros_like(seed, dtype=bool)
    queue = deque()
    for x in range(width):
        for y in (0, height - 1):
            if seed[y, x] and not visited[y, x]:
                visited[y, x] = True
                queue.append((y, x))
    for y in range(height):
        for x in (0, width - 1):
            if seed[y, x] and not visited[y, x]:
                visited[y, x] = True
                queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width and seed[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = True
                queue.append((ny, nx))
    return visited


def _sobel_magnitude(grey: np.ndarray) -> np.ndarray:
    """Numpy Sobel gradient magnitude for a normalised grey image."""
    if min(grey.shape) < 3:
        return np.zeros_like(grey)
    magnitude = np.zeros_like(grey)
    magnitude[1:-1, 1:-1] = np.hypot(
        (
            -grey[:-2, :-2] - 2.0 * grey[1:-1, :-2] - grey[2:, :-2]
            + grey[:-2, 2:] + 2.0 * grey[1:-1, 2:] + grey[2:, 2:]
        ) / 4.0,
        (
            -grey[:-2, :-2] - 2.0 * grey[:-2, 1:-1] - grey[:-2, 2:]
            + grey[2:, :-2] + 2.0 * grey[2:, 1:-1] + grey[2:, 2:]
        ) / 4.0,
    )
    return magnitude


def _local_alpha_matte(image: Image.Image) -> Image.Image:
    """Multi-pass foreground cutout using Pillow and numpy only.

    Passes: border colour sampling, flood-filled background prior, GrabCut-style
    hole repair, Sobel edge refinement, and a soft alpha feather. It handles
    solid and studio-style backgrounds without downloading any model weights.
    """
    rgba = image.convert("RGBA")
    scale = min(1.0, MAX_SEGMENT_SIDE / max(rgba.size))
    work_size = (max(1, int(rgba.width * scale)), max(1, int(rgba.height * scale)))
    work = rgba.resize(work_size, RESAMPLE.LANCZOS)
    pixels = np.asarray(work.convert("RGB"), dtype=np.float32)
    height, width = pixels.shape[:2]

    # 1. Border sampling sets the background colour and its noise tolerance.
    border = max(3, int(min(height, width) * 0.06))
    ring = np.concatenate((
        pixels[:border, :, :].reshape(-1, 3),
        pixels[-border:, :, :].reshape(-1, 3),
        pixels[:, :border, :].reshape(-1, 3),
        pixels[:, -border:, :].reshape(-1, 3),
    ))
    background = np.median(ring, axis=0)
    ring_distance = np.sqrt(np.sum((ring - background) ** 2, axis=1))
    tolerance = max(22.0, float(np.percentile(ring_distance, 90)) * 1.4)
    distance = np.sqrt(np.sum((pixels - background) ** 2, axis=2))
    alpha = np.clip((distance - tolerance) / max(20.0, tolerance * 0.9), 0.0, 1.0)

    # 2. Flood-fill only background-coloured pixels reachable from the border.
    background_seed = distance < tolerance * 1.25
    border_background = _border_connected(background_seed)
    alpha[border_background] = 0.0

    # 3. Repair holes inside the subject (eyes, shadows, dark clothing).
    hole_seed = (distance < tolerance * 1.05) & ~border_background
    enclosed = hole_seed & ~_border_connected(hole_seed)
    alpha[enclosed] = 1.0

    # 4. Sobel refinement: snap ambiguous pixels on strong edges to the nearer
    # of the sampled background and learned foreground colours.
    foreground_seed = (alpha > 0.85) & ~border_background
    if foreground_seed.any():
        foreground = np.median(pixels[foreground_seed], axis=0)
        distance_foreground = np.sqrt(np.sum((pixels - foreground) ** 2, axis=2))
        grey = np.asarray(ImageOps.grayscale(work), dtype=np.float32) / 255.0
        edges = _sobel_magnitude(grey)
        edge_cut = float(np.percentile(edges[alpha > 0.05], 70)) if np.any(alpha > 0.05) else 0.2
        ambiguous = (alpha > 0.02) & (alpha < 0.98)
        strong_edge = ambiguous & (edges >= max(0.12, edge_cut))
        alpha[strong_edge] = np.where(
            distance_foreground[strong_edge] < distance[strong_edge], 1.0, 0.0
        )

    # 5. Soft feather only the boundary ramp; flat background stays fully
    # transparent and the subject interior stays fully opaque.
    soft = Image.fromarray((alpha * 255).astype(np.uint8), "L")
    feathered = np.asarray(
        soft.filter(ImageFilter.GaussianBlur(0.7)), dtype=np.float32
    ) / 255.0
    edge_zone = (alpha > 0.02) & (alpha < 0.98)
    alpha[edge_zone] = np.maximum(alpha[edge_zone], feathered[edge_zone] * 0.85)
    alpha[border_background & (distance < tolerance)] = 0.0
    alpha = np.clip(alpha, 0.0, 1.0)

    alpha_full = Image.fromarray((alpha * 255).astype(np.uint8), "L")
    if alpha_full.size != rgba.size:
        alpha_full = alpha_full.resize(rgba.size, RESAMPLE.BILINEAR)
    original_alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    combined = np.minimum(np.asarray(alpha_full, dtype=np.uint8), original_alpha)
    result = rgba.copy()
    result.putalpha(Image.fromarray(combined, "L"))
    return result


def remove_background_bytes(image_bytes: bytes) -> Tuple[bytes, str]:
    """Return a real transparent PNG cutout for Studio's Background Remover."""
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    cutout = _rembg_cutout(image) or _local_alpha_matte(image)
    cutout = cutout.convert("RGBA")
    out = io.BytesIO()
    cutout.save(out, format="PNG", optimize=True)
    return out.getvalue(), "image/png"


def make_preview_data_uri(image_bytes: bytes, mime_type: str) -> str:
    """Build a data URI suitable for the Studio before/after modal."""
    import base64

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
