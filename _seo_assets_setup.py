"""One-time generator for ValleyMind AI's SEO & PWA image assets.

Run from the project root:
    python _seo_assets_setup.py

Produces (all derived from static/valleymind-logo.png on the brand
background so search engines and phones see consistent artwork):

    static/og-image.png                  1200x630  Open Graph / Twitter preview
    static/icons/icon-192.png             192x192  PWA icon
    static/icons/icon-512.png             512x512  PWA icon
    static/icons/icon-maskable-512.png    512x512  maskable-safe PWA icon
    static/icons/apple-touch-icon.png     180x180  iOS home-screen icon
    static/favicon.ico                16/32/48    classic browser favicon

The generated PNGs are committed to the repo; this script only exists so the
assets can be regenerated if the logo changes.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "static" / "valleymind-logo.png"
ICONS_DIR = ROOT / "static" / "icons"

# Brand palette (matches index.html / base.html).
BG_TOP = (11, 20, 26)       # #0b141a
BG_BOTTOM = (14, 32, 44)    # slightly lighter at the bottom for depth


def vertical_gradient(width: int, height: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(width):
            pixels[x, y] = color
    return img


def place_logo(canvas: Image.Image, logo: Image.Image, width_fraction: float) -> None:
    """Paste the logo centred on the canvas at the given width fraction."""
    target_w = int(canvas.width * width_fraction)
    target_h = int(target_w * logo.height / logo.width)
    resized = logo.resize((target_w, target_h), Image.LANCZOS)
    x = (canvas.width - target_w) // 2
    y = (canvas.height - target_h) // 2
    canvas.paste(resized, (x, y))


def make_icon(size: int, logo: Image.Image, width_fraction: float = 0.72) -> Image.Image:
    canvas = vertical_gradient(size, size, BG_TOP, BG_BOTTOM)
    place_logo(canvas, logo, width_fraction)
    return canvas


def main() -> None:
    if not LOGO_PATH.exists():
        raise SystemExit(f"Logo not found at {LOGO_PATH}")
    logo = Image.open(LOGO_PATH).convert("RGB")
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    # 1) Open Graph preview (1200x630) — used for shared links everywhere.
    og = vertical_gradient(1200, 630, BG_TOP, BG_BOTTOM)
    place_logo(og, logo, 0.55)
    og.save(ROOT / "static" / "og-image.png")
    print("wrote static/og-image.png")

    # 2) PWA icons.
    make_icon(192, logo, 0.72).save(ICONS_DIR / "icon-192.png")
    make_icon(512, logo, 0.72).save(ICONS_DIR / "icon-512.png")
    # Maskable: content must sit inside the central 80% safe zone, so shrink it.
    make_icon(512, logo, 0.58).save(ICONS_DIR / "icon-maskable-512.png")
    print("wrote static/icons/icon-{192,512,maskable-512}.png")

    # 3) iOS home-screen icon (iOS ignores the maskable safe zone).
    make_icon(180, logo, 0.72).save(ICONS_DIR / "apple-touch-icon.png")
    print("wrote static/icons/apple-touch-icon.png")

    # 4) Classic multi-size favicon.ico.
    favicon_src = make_icon(64, logo, 0.72)
    favicon_src.save(
        ROOT / "static" / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )
    print("wrote static/favicon.ico")

    print("Done. All SEO/PWA assets regenerated.")


if __name__ == "__main__":
    main()
