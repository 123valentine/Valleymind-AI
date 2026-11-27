import io
import unittest

from PIL import Image

from core.photo_enhance import enhance_photo_bytes, make_preview_data_uri


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


if __name__ == "__main__":
    unittest.main()
