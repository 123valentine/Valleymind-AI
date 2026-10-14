"""Windows desktop companion tests.

Locks in the desktop MVP contract: an isolated Electron shell reuses the
EXISTING backend, VRM engine and voice module; the window is transparent,
frameless and always-on-top; no secrets reach the frontend; there is no
auto-start; and the new /desktop surface is served (before sign-in) without
ever exposing Electron's Node files.

Run with:
  env311\\Scripts\\python.exe -m pytest tests/test_desktop_companion.py -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module


class DesktopStaticTests(unittest.TestCase):
    """Structural guarantees on the desktop/ folder."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_desktop_files_exist(self):
        for rel in (
            "desktop/package.json",
            "desktop/main.js",
            "desktop/preload.js",
            "desktop/tray.png",
            "desktop/web/companion.html",
            "desktop/web/companion.css",
            "desktop/web/companion.js",
        ):
            self.assertTrue((ROOT / rel).is_file(), f"missing {rel}")

    def test_shims_defined_before_voice_module(self):
        html = self._read("desktop/web/companion.html")
        self.assertLess(
            html.index("window.apiFetch"),
            html.index("/static/cloud_voice.js"),
            "apiFetch/authHeaders shims must be defined before cloud_voice.js runs",
        )

    def test_reuses_existing_engine_voice_and_brain(self):
        js = self._read("desktop/web/companion.js")
        self.assertIn("/vrm-preview/assets/companion.js", js)  # the ONE body engine
        self.assertIn("/api/cloud/chat", js)                   # the real AI brain
        self.assertIn("VMCloudVoice", js)                      # the shared voice module
        self.assertIn("/api/editing/transcribe", js)           # server STT fallback

    def test_window_is_transparent_frameless_always_on_top(self):
        main = self._read("desktop/main.js")
        for marker in (
            "transparent: true",
            "frame: false",
            'backgroundColor: "#00000000"',
            "alwaysOnTop",
            "setAlwaysOnTop",
        ):
            self.assertIn(marker, main, f"main.js missing {marker}")

    def test_css_root_is_transparent_and_draggable(self):
        css = self._read("desktop/web/companion.css")
        self.assertIn("background: transparent", css)
        self.assertIn("-webkit-app-region", css)

    def test_no_secrets_in_frontend_or_shell(self):
        for rel in ("desktop/web/companion.js", "desktop/web/companion.html", "desktop/main.js"):
            text = self._read(rel)
            for bad in ("GROQ_API_KEY", "OPENAI_API_KEY", "PINECONE", "sk-", "API_KEY"):
                self.assertNotIn(bad, text, f"{bad} must not appear in {rel}")

    def test_no_autostart_registration(self):
        main = self._read("desktop/main.js")
        for bad in ("setLoginItemSettings", "app.setLoginItemSettings"):
            self.assertNotIn(bad, main)

    def test_quit_stops_the_backend(self):
        main = self._read("desktop/main.js")
        self.assertIn("before-quit", main)
        self.assertIn("stopServer", main)


class DesktopRouteTests(unittest.TestCase):
    """The real Flask test client serving the /desktop surface."""

    def setUp(self):
        self.client = app_module.app.test_client()

    def test_desktop_page_served_without_auth(self):
        resp = self.client.get("/desktop")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="stage"', html)
        self.assertIn("/desktop/assets/companion.js", html)
        self.assertIn("/desktop/assets/companion.css", html)
        # import map points at the shared three-vrm modules
        self.assertIn("/vrm-preview/assets/node_modules/three/build/three.module.js", html)

    def test_desktop_css_route(self):
        resp = self.client.get("/desktop/assets/companion.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("transparent", resp.get_data(as_text=True))

    def test_desktop_js_route(self):
        resp = self.client.get("/desktop/assets/companion.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/api/cloud/chat", resp.get_data(as_text=True))

    def test_missing_asset_is_404(self):
        resp = self.client.get("/desktop/assets/does-not-exist.js")
        self.assertEqual(resp.status_code, 404)

    def test_electron_node_files_are_not_served(self):
        marker = 'require("electron")'
        for path in ("/desktop/main.js", "/desktop/assets/main.js", "/desktop/assets/../main.js"):
            resp = self.client.get(path)
            self.assertNotIn(marker, resp.get_data(as_text=True), f"{path} leaked Node source")


if __name__ == "__main__":
    unittest.main()
