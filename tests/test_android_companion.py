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
ANDROID_BRIDGE_JS_RUNNER = ROOT / "tests" / "_android_bridge_js_test.js"


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
    """The overlay is character-first: no panel, no text boxes, no body halo.
    Its only chrome is a compact dark-glass micro-pill beneath the character's
    feet with Talk/Mute Mic, Camera Eyes and Close icon controls."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_overlay_page_verifier_markers_survive(self):
        # OverlayPageVerifier.PAGE_MARKER_JS checks for exactly these ids.
        html = self._read("static/companion_overlay.html")
        self.assertIn('id="vmOverlayRoot"', html)
        self.assertIn('id="stage"', html)
        self.assertIn('id="btnHide"', html)

    def test_overlay_has_no_top_badge_strip(self):
        # The old "OVER APP" / CAM strip sat over the character's head. Only the
        # full-body character and the bottom micro-pill may render.
        html = self._read("static/companion_overlay.html")
        for gone in ("vmOverlayBadges", "vmBadgeOverlay", "vmBadgeCamera", "OVER APP"):
            self.assertNotIn(gone, html, f"overlay still carries {gone}")
        css = self._read("static/companion_overlay.css")
        for gone in (".badges", ".badge-overlay", ".badge-camera"):
            self.assertNotIn(gone, css, f"overlay css still styles {gone}")
        js = self._read("static/companion_overlay.js")
        self.assertNotIn("vmBadgeOverlay", js)
        self.assertNotIn("badgeOverlay", js)

    def test_overlay_has_no_control_panel_or_reply_controls(self):
        html = self._read("static/companion_overlay.html")
        for gone in ("btnChat", "btnSend", "id=\"panel\"", "id=\"input\"", "id=\"hud\"", "id=\"log\""):
            self.assertNotIn(gone, html, f"overlay still carries {gone}")
        css = self._read("static/companion_overlay.css")
        for gone in ("#panel", "#hud", ".pill", ".bubble", "#input"):
            self.assertNotIn(gone, css, f"overlay css still styles {gone}")

    def test_overlay_has_no_standalone_tap_to_talk_mic_button(self):
        # The pill's Talk/Mute Mic icon replaces the old CALL/MUTE text boxes;
        # there is no separate mic-only tap-to-talk control.
        html = self._read("static/companion_overlay.html")
        self.assertNotIn('id="btnMic"', html)
        self.assertIn('id="btnTalk"', html)
        css = self._read("static/companion_overlay.css")
        self.assertNotIn(".mic", css)
        js = self._read("static/companion_overlay.js")
        self.assertNotIn("btnMic", js)
        self.assertNotIn("micBtn", js)

    def test_overlay_lights_a_centred_pill_glow_instead_of_a_body_halo(self):
        # No ring behind the legs: live state lights a soft glow centred inside
        # the Talk/Mute Mic button at the bottom of the compact window.
        html = self._read("static/companion_overlay.html")
        self.assertIn('id="vmMicroPill"', html)
        self.assertIn('id="btnTalk"', html)
        css = self._read("static/companion_overlay.css")
        self.assertIn("#vmMicroPill", css)
        self.assertIn(".vm-glow", css)
        for state in ("listening", "processing", "speaking", "error", "waking"):
            self.assertIn(f'[data-state="{state}"]', css)
        js = self._read("static/companion_overlay.js")
        # The old chest-projected halo anchor is gone.
        self.assertIn("engine.setAuto(false)", js)
        self.assertNotIn("projectWorld", js)
        self.assertNotIn("getRawBoneNode", js)

    def test_overlay_micro_pill_owns_the_visible_conversation_controls(self):
        html = self._read("static/companion_overlay.html")
        for marker in ('id="vmMicroPill"', 'id="btnTalk"', 'id="btnCamera"', 'id="btnHide"'):
            self.assertIn(marker, html)
        self.assertNotIn(">CALL<", html)
        self.assertNotIn(">MUTE<", html)
        css = self._read("static/companion_overlay.css")
        self.assertIn("height: calc(100% - 46px)", css)
        js = self._read("static/companion_overlay.js")
        self.assertIn("setRegion(176, 316)", js)
        self.assertIn('type: "STOP"', js)

    def test_overlay_loads_the_turn_state_machine(self):
        html = self._read("static/companion_overlay.html")
        self.assertIn("/static/companion_turn_sm.js", html)
        js = self._read("static/companion_overlay.js")
        self.assertIn("window.VMTurnSM", js)

    def test_autonomous_idle_stays_off_through_boot(self):
        js = self._read("static/companion_overlay.js")
        self.assertGreaterEqual(js.count("engine.setAuto(false);"), 3)
        boot_at = js.index("await engine.boot();")
        self.assertLess(js.index("engine.setAuto(false);"), boot_at)
        self.assertGreater(js.index("engine.setAuto(false);", boot_at), boot_at)

    def test_overlay_offers_the_four_free_voice_profiles(self):
        js = self._read("static/companion_overlay.js")
        for pid in ("marcus", "angelina", "elena", "companion"):
            self.assertIn(pid, js, f"missing voice profile {pid}")
        self.assertIn("vmVoiceProfile", js)

    def test_overlay_prefers_native_tts_v2_with_bounded_fallback(self):
        # Android WebView has no speechSynthesis. V2 is first because it reports
        # real utterance events; server TTS and the legacy native callback are
        # bounded fallbacks, not silent drops.
        js = self._read("static/companion_overlay.js")
        self.assertIn("ttsSpeakV2", js)
        self.assertIn("ttsSpeak", js)
        self.assertIn("speakNative", js)
        self.assertIn("startNativeV2Speech", js)
        self.assertIn("startServerSpeech", js)
        self.assertIn("startLegacyNativeSpeech", js)
        self.assertIn("state.ttsFallbackUsed", js)
        self.assertLess(js.index("startNativeV2Speech(text, seq);"),
                        js.index("startServerSpeech(text, seq);"))

    def test_tts_start_and_end_are_real_gates_for_the_mouth(self):
        js = self._read("static/companion_overlay.js")
        self.assertIn("function setFaceSpeaking(on)", js)
        self.assertIn("function closeMouth()", js)
        self.assertIn("function finishSpeaking(success, seq)", js)
        self.assertIn("closeMouth();", js)
        # Native V2 opens the mouth only on the engine's real onStart and closes
        # it in finishSpeaking on both success and failure.
        self.assertIn('onStart: () => { if (speechCurrent(seq, "nativeV2")) setFaceSpeaking(true); }', js)
        self.assertIn("onLevel: (level) =>", js)
        self.assertIn("onEnd: (ok) =>", js)
        self.assertIn("finishSpeaking(true, seq)", js)
        self.assertIn("finishSpeaking(false, seq)", js)

    def test_tts_end_restarts_listening_after_the_echo_guard(self):
        js = self._read("static/companion_overlay.js")
        self.assertIn("const ECHO_GUARD_MS = 250;", js)
        self.assertIn("state.restartDelayMs = ECHO_GUARD_MS;", js)
        self.assertIn("function startListeningAfterEchoGuard()", js)
        self.assertIn('case "start-listen": startListeningAfterEchoGuard();', js)


class HandsFreeConversationTests(unittest.TestCase):
    """The floating companion keeps the conversation going on its own: one tap
    starts it, the mic reopens after every reply, and it is bounded so a broken
    microphone can never spin forever."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_page_drives_the_loop_through_the_state_machine(self):
        js = self._read("static/companion_overlay.js")
        # The page only executes the effects the state machine returns.
        self.assertIn("sm.send(event)", js)
        self.assertIn('case "start-listen"', js)
        self.assertIn('case "session-end"', js)
        self.assertIn('case "wake"', js)

    def test_page_prefers_the_platform_recogniser_then_falls_back(self):
        js = self._read("static/companion_overlay.js")
        self.assertIn("hasNativeAsr", js)
        self.assertIn("asrStart", js)
        self.assertIn("onAsrEvent", js)
        # The existing fallbacks are kept, not replaced.
        self.assertIn("voice.startListening", js)
        self.assertIn("startVadRecording", js)

    def test_spoken_stop_phrases_end_the_hands_free_loop(self):
        js = self._read("static/companion_overlay.js")
        self.assertIn("stop listening", js)
        self.assertIn("goodbye", js)
        self.assertIn("function isCallStopPhrase(text)", js)
        self.assertIn("function handleFinalTranscript(text)", js)
        self.assertIn("dispatch({ type: \"STOP\" })", js)

    def test_native_bridge_exposes_the_recogniser(self):
        bridge = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionBridge.kt"
        )
        for method in ("asrSupported", "asrStart", "asrStop", "asrCancel"):
            self.assertIn(f"fun {method}", bridge)
        js = self._read(
            "android/app/src/main/java/ai/valleymind/companion/AndroidBridgeJs.kt"
        )
        self.assertIn("window.__vmAsrEvent", js)
        self.assertIn("onAsrEvent", js)

    def test_native_recogniser_never_uploads_audio(self):
        # The platform recogniser turns speech into text on the device.
        asr = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionAsr.kt"
        )
        self.assertIn("SpeechRecognizer", asr)
        self.assertIn("EXTRA_LANGUAGE_MODEL", asr)
        for leaked in ("/api/editing/transcribe", "HttpURLConnection", "OkHttp"):
            self.assertNotIn(leaked, asr)

    def test_native_recogniser_is_tuned_for_hands_free_speech(self):
        asr = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionAsr.kt"
        )
        self.assertIn("EXTRA_PARTIAL_RESULTS, true", asr)
        self.assertIn(
            "EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1800L", asr
        )
        self.assertIn(
            "EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1400L", asr
        )
        self.assertIn("MediaRecorder.AudioSource.VOICE_RECOGNITION", asr)
        for effect in ("AutomaticGainControl", "NoiseSuppressor", "AcousticEchoCanceler"):
            self.assertIn(effect + ".isAvailable()", asr)
            self.assertIn(effect + ".create(session)", asr)
        self.assertEqual(asr.count("effect.enabled = true"), 3)


class OverlayDragSurfaceTests(unittest.TestCase):
    """Dragging the companion must feel right: the character itself is the drag
    surface, a drag never also wakes the companion, and it can never be dragged
    out of reach."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_drag_surface_files_exist(self):
        for rel in (
            "android/app/src/main/java/ai/valleymind/companion/OverlayDragLayout.kt",
            "android/app/src/main/java/ai/valleymind/companion/OverlayDragTracker.kt",
        ):
            self.assertTrue((ROOT / rel).is_file(), f"missing {rel}")

    def test_service_uses_the_drag_surface_and_drops_the_handle_strip(self):
        svc = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt"
        )
        self.assertIn("OverlayDragLayout", svc)
        # The old visible handle bar and its ad-hoc drag listener are gone.
        self.assertNotIn("wireDrag", svc)
        self.assertNotIn("setBackgroundColor(0x22000000)", svc)

    def test_drag_position_is_clamped_to_the_screen(self):
        svc = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt"
        )
        self.assertIn("overlayBounds", svc)
        self.assertIn("OverlayBounds", svc)
        # The clamp uses the real window size, so the companion stays reachable.
        self.assertIn("displayMetrics", svc)

    def test_tap_and_drag_are_separated_by_the_touch_slop(self):
        layout = self._read(
            "android/app/src/main/java/ai/valleymind/companion/OverlayDragLayout.kt"
        )
        self.assertIn("scaledTouchSlop", layout)
        self.assertIn("onInterceptTouchEvent", layout)
        # The child is cancelled when the gesture becomes a drag, so a drag can
        # never also fire the tap that wakes the companion.
        self.assertIn("ACTION_CANCEL", layout)

    def test_drag_logic_is_unit_tested_on_the_jvm(self):
        path = (
            ROOT / "android" / "app" / "src" / "test" / "java" / "ai"
            / "valleymind" / "companion" / "OverlayDragTrackerTest.kt"
        )
        self.assertTrue(path.is_file(), "missing OverlayDragTrackerTest.kt")


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


class NativeAsrBridgeTests(unittest.TestCase):
    """The platform recogniser reaches the overlay page through an injected shim."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_shim_exposes_the_asr_contract(self):
        kt = self._read(
            "android/app/src/main/java/ai/valleymind/companion/AndroidBridgeJs.kt"
        )
        for needle in (
            "__vmAsrEvent",
            "__vmAsrBus",
            "hasNativeAsr",
            "asrStart",
            "asrStop",
            "asrCancel",
            "onAsrEvent",
        ):
            self.assertIn(needle, kt, f"the injected shim is missing {needle}")

    def test_bridge_and_service_expose_the_asr_host(self):
        bridge = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionBridge.kt"
        )
        service = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt"
        )
        for needle in ("asrSupported", "asrStart", "asrStop", "asrCancel"):
            self.assertIn(needle, bridge, f"CompanionBridge is missing {needle}")
            self.assertIn(needle, service, f"CompanionOverlayService is missing {needle}")
        self.assertIn("CompanionAsr", service, "the service must own the recogniser")

    def test_recogniser_is_created_with_the_public_api(self):
        asr = self._read(
            "android/app/src/main/java/ai/valleymind/companion/CompanionAsr.kt"
        )
        self.assertIn("SpeechRecognizer.createSpeechRecognizer(appContext)", asr)
        self.assertIn("setRecognitionListener", asr)
        self.assertNotIn(
            "createSpeechRecognizer(appContext,",
            asr,
            "the two-argument overload is not public API for apps",
        )

    @unittest.skipIf(NODE is None, "node is not available")
    def test_android_bridge_js_runner_passes(self):
        proc = subprocess.run(
            [NODE, str(ANDROID_BRIDGE_JS_RUNNER)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        self.assertEqual(
            proc.returncode, 0,
            msg="injected bridge shim runner exited non-zero\nstdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr),
        )
        summary = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual(summary["failed"], [], summary)
        self.assertGreater(summary["passed"], 0, "injected bridge shim asserted nothing")


class WebViewSyncTests(unittest.TestCase):
    """The in-app WebView must reuse the hosted app's session and never replay
    a stale cached bundle after a server redeploy."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_webview_accepts_first_and_third_party_cookies(self):
        act = self._read(
            "android/app/src/main/java/ai/valleymind/companion/MainActivity.kt"
        )
        self.assertIn("import android.webkit.CookieManager", act)
        for needle in (
            "CookieManager.getInstance().apply",
            "setAcceptCookie(true)",
            "setAcceptThirdPartyCookies(webView, true)",
            "flush()",
            "domStorageEnabled = true",
        ):
            self.assertIn(needle, act, f"MainActivity is missing {needle}")

    def test_webview_never_serves_stale_cached_html_or_js(self):
        act = self._read(
            "android/app/src/main/java/ai/valleymind/companion/MainActivity.kt"
        )
        self.assertIn("cacheMode = WebSettings.LOAD_NO_CACHE", act)
        self.assertIn("webView.clearCache(true)", act)
        # The cache is dropped on every load, including Retry and a base-URL
        # change, not only on a cold start.
        load_app = act.split("private fun loadApp()", 1)[1].split("\n    }", 1)[0]
        self.assertIn("webView.clearCache(true)", load_app)
        self.assertIn("webView.loadUrl(baseUrl)", load_app)

    def test_cold_start_uses_the_hosted_production_url(self):
        act = self._read(
            "android/app/src/main/java/ai/valleymind/companion/MainActivity.kt"
        )
        self.assertIn("baseUrl = configStore.getBaseUrl(BuildConfig.VALLEY_BASE_URL)", act)
        self.assertIn("!BuildConfig.DEBUG && configStore.hasOverride()", act)
        self.assertIn("configStore.clearBaseUrlOverride()", act)


if __name__ == "__main__":
    unittest.main()
