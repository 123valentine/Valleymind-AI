"""Android companion tests.

Locks in the Android contract: a thin native shell reuses the EXISTING backend,
auth, VRM engine and voice module; the system-wide floating companion is a real
native overlay (SYSTEM_ALERT_WINDOW + a foreground service), driven through the
SAME single Settings toggle via the window.vmCompanionAndroid bridge that
static/settings.js already selects as its "android-overlay" adapter.

These are structural checks on the native sources plus a Node-executed test of
the real web control flow. They are NOT a substitute for an actual Android
build / device verification (see android/README.md).

Run with:
  env311\\Scripts\\python.exe -m pytest tests/test_android_companion.py -v
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
sys.path.insert(0, str(ROOT))

NODE = shutil.which("node")
ANDROID_BRIDGE_RUNNER = ROOT / "tests" / "_android_bridge_test.js"


class AndroidProjectTests(unittest.TestCase):
    """The native Android project exists with the required overlay machinery."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_project_files_exist(self):
        for rel in (
            "android/settings.gradle",
            "android/build.gradle",
            "android/app/build.gradle",
            "android/app/src/main/AndroidManifest.xml",
            "android/app/src/main/java/ai/valleymind/companion/MainActivity.kt",
            "android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt",
            "android/app/src/main/java/ai/valleymind/companion/CompanionBridge.kt",
            "android/app/src/main/java/ai/valleymind/companion/AndroidBridgeJs.kt",
        ):
            self.assertTrue((ROOT / rel).is_file(), f"missing {rel}")

    def test_manifest_requests_overlay_and_foreground_service(self):
        manifest = self._read("android/app/src/main/AndroidManifest.xml")
        self.assertIn("android.permission.SYSTEM_ALERT_WINDOW", manifest)
        self.assertIn("android.permission.FOREGROUND_SERVICE", manifest)
        self.assertIn("android.permission.FOREGROUND_SERVICE_SPECIAL_USE", manifest)
        self.assertIn("android.permission.INTERNET", manifest)
        self.assertIn("android:foregroundServiceType=\"specialUse\"", manifest)
        self.assertIn("CompanionOverlayService", manifest)

    def test_service_uses_a_system_overlay_window(self):
        svc = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt"
        )
        self.assertIn("TYPE_APPLICATION_OVERLAY", svc)
        self.assertIn("WindowManager.LayoutParams", svc)
        self.assertIn("FLAG_NOT_FOCUSABLE", svc)
        self.assertIn("FLAG_NOT_TOUCH_MODAL", svc)
        # It reuses the existing companion surface, not a new brain.
        self.assertIn("/static/companion_overlay.html", svc)
        self.assertIn("FOREGROUND_SERVICE_TYPE_SPECIAL_USE", svc)
        # Real state, not merely the request.
        self.assertIn("isVisible", svc)

    def test_activity_handles_the_overlay_permission_flow(self):
        act = self._read(
            "android/app/src/main/java/ai/valleymind/companion/MainActivity.kt"
        )
        self.assertIn("Settings.ACTION_MANAGE_OVERLAY_PERMISSION", act)
        self.assertIn("Settings.canDrawOverlays", act)
        self.assertIn("requestOverlayPermission", act)
        # Re-checks the REAL permission on return instead of trusting a result.
        self.assertIn("onOverlayPermissionReturned", act)
        self.assertIn("onResume", act)

    def test_bridge_exposes_the_web_contract_via_javascript_interface(self):
        bridge = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionBridge.kt"
        )
        self.assertIn("@JavascriptInterface", bridge)
        for method in ("setVisible", "hasPermission", "requestPermission", "getStateJson"):
            self.assertIn(f"fun {method}", bridge)

    def test_js_bootstrap_exposes_vmcompanionandroid(self):
        js = self._read(
            "android/app/src/main/java/ai/valleymind/companion/AndroidBridgeJs.kt"
        )
        self.assertIn("window.vmCompanionAndroid", js)
        self.assertIn("setVisible", js)
        self.assertIn("hasPermission", js)
        self.assertIn("requestPermission", js)
        self.assertIn("window.__vmAndroidState", js)
        # Values come from native state, never from the request.
        self.assertIn("getStateJson()", js)


class OverlaySurfaceTests(unittest.TestCase):
    """The overlay WebView page reuses the EXISTING engine + voice + backend."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_overlay_page_and_assets_exist(self):
        for rel in (
            "static/companion_overlay.html",
            "static/companion_overlay.js",
            "static/companion_overlay.css",
        ):
            self.assertTrue((ROOT / rel).is_file(), f"missing {rel}")

    def test_overlay_reuses_the_one_engine_and_voice(self):
        html = self._read("static/companion_overlay.html")
        # The importmap points the module graph at the existing vrm-preview engine.
        self.assertIn("importmap", html)
        self.assertIn("/vrm-preview/assets/node_modules", html)
        self.assertIn("/static/cloud_voice.js", html)
        js = self._read("static/companion_overlay.js")
        self.assertIn('from "/vrm-preview/assets/companion.js"', js)
        # Same brain/session as the site.
        self.assertIn("/api/cloud/chat", js)
        self.assertIn("credentials: \"include\"", js)

    def test_overlay_can_put_itself_away_through_the_same_bridge(self):
        js = self._read("static/companion_overlay.js")
        self.assertIn("window.vmCompanionAndroid", js)
        self.assertIn("setVisible(false)", js)


class OverlayChromeTests(unittest.TestCase):
    """The overlay is character-first: no panel, and the mic is the only control
    besides the required icon-only hide affordance."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_overlay_page_verifier_markers_survive(self):
        # OverlayPageVerifier.PAGE_MARKER_JS checks for exactly these ids.
        html = self._read("static/companion_overlay.html")
        self.assertIn('id="vmOverlayRoot"', html)
        self.assertIn('id="stage"', html)
        self.assertIn('id="btnHide"', html)

    def test_overlay_has_no_control_panel_or_reply_controls(self):
        html = self._read("static/companion_overlay.html")
        for gone in ("btnChat", "btnSend", "id=\"panel\"", "id=\"input\"", "id=\"hud\"", "id=\"log\""):
            self.assertNotIn(gone, html, f"overlay still carries {gone}")
        css = self._read("static/companion_overlay.css")
        for gone in ("#panel", "#hud", ".pill", ".bubble", "#input"):
            self.assertNotIn(gone, css, f"overlay css still styles {gone}")

    def test_overlay_keeps_a_mic_attached_to_the_body(self):
        js = self._read("static/companion_overlay.js")
        self.assertIn('id="btnMic"', self._read("static/companion_overlay.html"))
        # The mic follows the model's own world->screen projection.
        self.assertIn("projectWorld", js)
        self.assertIn("getRawBoneNode", js)

    def test_overlay_loads_the_turn_state_machine(self):
        html = self._read("static/companion_overlay.html")
        self.assertIn("/static/companion_turn_sm.js", html)
        js = self._read("static/companion_overlay.js")
        self.assertIn("window.VMTurnSM", js)

    def test_overlay_offers_the_four_free_voice_profiles(self):
        js = self._read("static/companion_overlay.js")
        for pid in ("marcus", "angelina", "elena", "companion"):
            self.assertIn(pid, js, f"missing voice profile {pid}")
        self.assertIn("vmVoiceProfile", js)

    def test_overlay_declares_a_native_tts_fallback(self):
        # Android WebView has no speechSynthesis; the page must fall back to the
        # native engine rather than silently dropping the reply.
        js = self._read("static/companion_overlay.js")
        self.assertIn("ttsSpeak", js)
        self.assertIn("speakNative", js)


class TurnStateMachineTests(unittest.TestCase):
    """The tap-to-talk turn machine runs as a pure-node test AND is wired in the
    overlay page."""

    def test_turn_sm_module_exists(self):
        self.assertTrue((ROOT / "static" / "companion_turn_sm.js").is_file())

    @unittest.skipIf(NODE is None, "node is not available")
    def test_turn_sm_node_runner_passes(self):
        runner = ROOT / "tests" / "_companion_turn_sm_test.js"
        proc = subprocess.run(
            [NODE, str(runner)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        self.assertEqual(
            proc.returncode, 0,
            msg="turn state machine runner exited non-zero\nstdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr),
        )
        summary = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual(summary["failed"], [], summary)
        self.assertGreater(summary["passed"], 0, "turn state machine asserted nothing")


class WebAdapterIntegrationTests(unittest.TestCase):
    """settings.js selects the Android adapter and gates on permission."""

    def test_settings_selects_android_overlay_adapter(self):
        settings = (ROOT / "static" / "settings.js").read_text(encoding="utf-8")
        self.assertIn("function vmCompanionControl()", settings)
        self.assertIn('"android-overlay"', settings)
        self.assertIn("window.vmCompanionAndroid", settings)
        self.assertIn("needsPermission", settings)
        self.assertIn("requestPermission", settings)

    @unittest.skipIf(NODE is None, "node is not available")
    def test_android_bridge_node_runner_passes(self):
        proc = subprocess.run(
            [NODE, str(ANDROID_BRIDGE_RUNNER)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        self.assertEqual(
            proc.returncode, 0,
            msg="android bridge runner exited non-zero\nstdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr),
        )
        summary = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual(summary["failed"], [], summary)
        self.assertGreater(summary["passed"], 0, "android bridge runner asserted nothing")


if __name__ == "__main__":
    unittest.main()
