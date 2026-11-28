import io
import os
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from core.photo_enhance import (
    REMOVE_BG_MODES,
    enhance_photo_bytes,
    make_preview_data_uri,
    remove_background_bytes,
)


def _sample_image(dark: bool = True) -> bytes:
    image = Image.new("RGB", (48, 48), (30, 45, 70) if dark else (200, 205, 210))
    out = io.BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


class PhotoEnhanceTests(unittest.TestCase):
    def test_auto_enhance_returns_jpeg_and_changes_pixels(self):
        source = _sample_image()
        enhanced, mime = enhance_photo_bytes(source, "auto")
        self.assertEqual(mime, "image/jpeg")
        self.assertNotEqual(source, enhanced)
        with Image.open(io.BytesIO(enhanced)) as result:
            self.assertEqual(result.size, (48, 48))

    def test_retouch_profile_is_different_from_auto(self):
        source = _sample_image(False)
        auto, _ = enhance_photo_bytes(source, "auto")
        retouch, _ = enhance_photo_bytes(source, "retouch")
        self.assertNotEqual(auto, retouch)

    def test_unknown_profile_falls_back_to_auto(self):
        source = _sample_image()
        auto, _ = enhance_photo_bytes(source, "auto")
        fallback, _ = enhance_photo_bytes(source, "unknown-mode")
        self.assertEqual(auto, fallback)

    def test_preview_data_uri(self):
        uri = make_preview_data_uri(b"abc", "image/jpeg")
        self.assertTrue(uri.startswith("data:image/jpeg;base64,"))
        self.assertTrue(uri.endswith("YWJj"))

    def test_remove_background_returns_real_transparent_png(self):
        source = Image.new("RGB", (128, 128), (240, 240, 240))
        ImageDraw.Draw(source).ellipse((30, 30, 98, 98), fill=(200, 30, 30))
        raw = io.BytesIO()
        source.save(raw, format="PNG")

        # Never download the optional segmentation model during tests.
        with mock.patch.dict(os.environ, {"VM_DISABLE_REMBG": "1"}):
            cutout, mime = remove_background_bytes(raw.getvalue())

        self.assertEqual(mime, "image/png")
        self.assertTrue(cutout.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(cutout)) as result:
            self.assertEqual(result.mode, "RGBA")
            self.assertEqual(result.size, (128, 128))
            self.assertEqual(result.getpixel((5, 5))[3], 0)      # background erased
            self.assertEqual(result.getpixel((64, 64))[3], 255)  # subject kept

    def test_remove_bg_mode_aliases_are_recognised(self):
        self.assertIn("remove-bg", REMOVE_BG_MODES)
        self.assertIn("remove background", REMOVE_BG_MODES)


if __name__ == "__main__":
    unittest.main()
