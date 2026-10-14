"""Windows desktop companion tests.

Locks in the desktop contract: an isolated Electron shell reuses the EXISTING
backend, VRM engine and voice module; the window is transparent, frameless and
always-on-top; no secrets reach the frontend; the new /desktop surface is served
(before sign-in) without ever exposing Electron's Node files.

Automatic start is provided by a small background controller
(desktop/controller.py) registered at sign-in by install-companion.ps1 -- NOT by
the Electron shell self-registering. A web page cannot launch an .exe, so the
controller owns the loopback endpoint the website toggle talks to and launches
the companion on demand.

Run with:
  env311\\Scripts\\python.exe -m pytest tests/test_desktop_companion.py -v
"""
import importlib.util
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
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

    def test_shell_does_not_self_register_autostart(self):
        # The Electron shell must never register itself to run at login. Auto
        # start is a separate, user-run concern (controller.py + install script).
        main = self._read("desktop/main.js")
        for bad in ("setLoginItemSettings", "app.setLoginItemSettings"):
            self.assertNotIn(bad, main)

    def test_quit_stops_the_backend(self):
        main = self._read("desktop/main.js")
        self.assertIn("before-quit", main)
        self.assertIn("stopServer", main)

    def test_shell_is_a_controller_client_not_a_server(self):
        # main.js must NOT own the loopback control port any more; it talks to
        # the always-on controller instead (so the toggle works before launch).
        main = self._read("desktop/main.js")
        self.assertIn("CONTROL_URL", main)
        self.assertIn("/desired", main)
        self.assertIn("/sync", main)
        self.assertIn("syncDesiredState", main)
        self.assertIn("startDesiredPolling", main)
        self.assertNotIn("CONTROL_PORT", main)
        self.assertNotIn("http.createServer", main)

    def test_background_controller_and_startup_scripts_exist(self):
        for rel in (
            "desktop/controller.py",
            "desktop/install-companion.ps1",
            "desktop/uninstall-companion.ps1",
        ):
            self.assertTrue((ROOT / rel).is_file(), f"missing {rel}")

    def test_desktop_shortcut_dependency_removed(self):
        # The shortcut-based flow is gone; the fallback launcher stays.
        self.assertFalse(
            (DESKTOP / "create-shortcut.ps1").exists(),
            "Desktop shortcut creation logic must be removed",
        )
        self.assertTrue((DESKTOP / "launch-companion.cmd").is_file(), "keep the fallback launcher")

    def test_controller_is_loopback_only_and_guarded(self):
        ctrl = self._read("desktop/controller.py")
        self.assertIn('HOST = "127.0.0.1"', ctrl)
        self.assertIn("/visibility", ctrl)
        self.assertIn("/desired", ctrl)
        self.assertIn("origin_allowed", ctrl)
        self.assertIn("Access-Control-Allow-Private-Network", ctrl)

    def test_settings_toggle_targets_controller_and_has_setup_hint(self):
        settings = self._read("static/settings.js")
        self.assertIn("http://127.0.0.1:8737", settings)
        self.assertIn("/visibility", settings)
        self.assertIn("/state", settings)
        self.assertIn("install-companion.ps1", settings)
        # The old shortcut-dependent guidance must be gone.
        self.assertNotIn("Desktop shortcut", settings)

    def test_in_shell_ipc_control_bridge_in_preload(self):
        # The single-app control path: the page can drive the floating window
        # directly over IPC, with no controller process and no loopback port.
        preload = self._read("desktop/preload.js")
        self.assertIn("setCompanionVisible", preload)
        self.assertIn("getCompanionVisible", preload)
        self.assertIn("onCompanionVisibility", preload)

    def test_shell_has_single_app_mode_and_ipc_visibility(self):
        main = self._read("desktop/main.js")
        self.assertIn('process.argv.includes("--app")', main)
        self.assertIn("createAppWindow", main)
        self.assertIn('ipcMain.on("desktop:set-visible"', main)
        self.assertIn('ipcMain.handle("desktop:get-visible"', main)
        self.assertIn("broadcastVisibility", main)

    def test_login_handshake_gated_to_login_window_only(self):
        # A window at "/" performs the sign-in handshake ONLY with ?login=1, so
        # the single-app main window never force-shows the floating companion.
        preload = self._read("desktop/preload.js")
        self.assertIn("login=1", preload)
        main = self._read("desktop/main.js")
        self.assertIn("/?login=1", main)

    def test_settings_prefers_ipc_bridge_then_controller(self):
        settings = self._read("static/settings.js")
        self.assertIn("_vmDesktopBridge", settings)
        self.assertIn("setCompanionVisible", settings)
        # The loopback controller fallback for the browser/hosted case remains.
        self.assertIn("http://127.0.0.1:8737", settings)


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


def _load_controller():
    spec = importlib.util.spec_from_file_location("vm_desktop_controller", DESKTOP / "controller.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeChild:
    """Stands in for the spawned Electron process (no real launch in tests)."""

    pid = 4242

    def poll(self):
        return None

    def terminate(self):
        return None


class ControllerServerTests(unittest.TestCase):
    """The real controller HTTP surface, on an ephemeral loopback port.

    launch is stubbed so tests never start Electron; everything else (routing,
    CORS, desired-state relay, duplicate prevention, heartbeat liveness) is the
    real code path.
    """

    def setUp(self):
        self.controller = _load_controller()
        st = self.controller.STATE
        st.desired_visible = False
        st.last_beat = 0.0
        st.child = None
        self.spawn_calls = []

        def fake_spawn():
            self.spawn_calls.append(1)
            st.child = _FakeChild()
            return True

        st.spawn = fake_spawn
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.controller.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        st = self.controller.STATE
        st.desired_visible = False
        st.last_beat = 0.0
        st.child = None

    def _req(self, path, method="GET", body=None, origin=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}"), dict(exc.headers)

    def test_state_starts_stopped(self):
        status, body, _ = self._req("/state")
        self.assertEqual(status, 200)
        self.assertFalse(body["running"])
        self.assertFalse(body["visible"])

    def test_visibility_on_launches_and_reports_running(self):
        status, body, _ = self._req("/visibility", "POST", {"visible": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["visible"])
        self.assertTrue(body["starting"])
        self.assertTrue(body["running"])
        self.assertEqual(len(self.spawn_calls), 1)

    def test_no_duplicate_launch_when_already_running(self):
        self._req("/visibility", "POST", {"visible": True})
        status, body, _ = self._req("/visibility", "POST", {"visible": True})
        self.assertEqual(status, 200)
        self.assertFalse(body["starting"], "must not spawn a second companion")
        self.assertEqual(len(self.spawn_calls), 1)

    def test_visibility_off_hides_but_keeps_running(self):
        self._req("/visibility", "POST", {"visible": True})
        status, body, _ = self._req("/visibility", "POST", {"visible": False})
        self.assertEqual(status, 200)
        self.assertFalse(body["visible"])
        self.assertTrue(body["running"], "companion stays alive so re-enabling is instant")
        status, body, _ = self._req("/state")
        self.assertTrue(body["running"])
        self.assertFalse(body["visible"])

    def test_desired_poll_is_the_heartbeat(self):
        # No child, no heartbeat yet -> not running.
        _, body, _ = self._req("/state")
        self.assertFalse(body["running"])
        # A companion poll registers liveness and returns the desired state.
        status, body, _ = self._req("/desired")
        self.assertEqual(status, 200)
        self.assertIn("visible", body)
        _, body, _ = self._req("/state")
        self.assertTrue(body["running"], "a recent /desired poll counts as liveness")

    def test_sync_updates_desired_state(self):
        self._req("/sync", "POST", {"visible": True})
        _, body, _ = self._req("/desired")
        self.assertTrue(body["visible"])
        self._req("/sync", "POST", {"visible": False})
        _, body, _ = self._req("/desired")
        self.assertFalse(body["visible"])

    def test_cors_allows_loopback_and_known_hosts(self):
        for origin in ("http://127.0.0.1:8000", "http://localhost:5000", "https://valleymind-ai.vercel.app"):
            status, _, headers = self._req("/state", "GET", origin=origin)
            self.assertEqual(status, 200, origin)
            self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
        status, _, headers = self._req("/visibility", "OPTIONS", origin="http://127.0.0.1:8000")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Private-Network"), "true")

    def test_cors_rejects_unknown_origin(self):
        status, body, headers = self._req("/state", "GET", origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertFalse(body["ok"])

    def test_unknown_path_is_404(self):
        status, body, _ = self._req("/nope")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])

    def test_origin_allowed_helper(self):
        allow = self.controller.origin_allowed
        self.assertTrue(allow(None))            # non-browser client
        self.assertTrue(allow("http://127.0.0.1:8737"))
        self.assertTrue(allow("https://valleymind.ai"))
        self.assertFalse(allow("https://evil.example"))


if __name__ == "__main__":
    unittest.main()
