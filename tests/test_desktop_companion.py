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
import shutil
import subprocess
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

NODE = shutil.which("node")
PRESENTATION_RUNNER = ROOT / "tests" / "_companion_presentation_test.js"


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
        self.assertIn('ipcMain.handle("desktop:set-visible"', main)
        self.assertIn('ipcMain.handle("desktop:get-visible"', main)
        self.assertIn("broadcastVisibility", main)

    def test_companion_starts_hidden_and_ready_never_shows(self):
        # Starting the integration (or the companion reporting ready) must never
        # reveal the floating window: it defaults hidden and desktop:ready only
        # reports the current state.
        main = self._read("desktop/main.js")
        self.assertIn("let companionVisible = false;", main)
        self.assertNotIn("let companionVisible = true;", main)
        self.assertIn('ipcMain.on("desktop:ready", () => { broadcastVisibility(); reportActualVisibility(); })', main)
        # No remaining code path force-shows the companion.
        self.assertNotIn("setCompanionVisible(true)", main)

    def test_login_and_relaunch_do_not_force_show(self):
        # Signing in or relaunching the app must not turn the floating window on;
        # the Settings toggle is the only control for its appearance.
        main = self._read("desktop/main.js")
        self.assertIn("if (companionVisible) showCompanion();", main)

    def test_set_visible_reports_true_visibility(self):
        main = self._read("desktop/main.js")
        self.assertIn("function companionIsVisible()", main)
        self.assertIn("return companionIsVisible();", main)

    def test_shell_reports_actual_visibility_to_controller(self):
        # main.js must report the window's REAL visibility (not just the desired
        # state) so the controller's /state can never lie: "reported" is what
        # happens, "requested" is only intent.
        main = self._read("desktop/main.js")
        self.assertIn("function reportActualVisibility()", main)
        self.assertIn('postControl("/sync", { visible: actual })', main)
        self.assertIn('postControl("/visibility", { visible: !!visible })', main)
        # A local change publishes intent AND the resulting real state.
        self.assertIn("reportActualVisibility();             // and the resulting REAL window state", main)

    def test_settings_toggle_confirms_real_visibility(self):
        # The switch must reflect CONFIRMED native visibility, not an optimistic
        # request: it polls controller /state and reverts on failure.
        settings = self._read("static/settings.js")
        self.assertIn("function vmCompanionPollDesktopUntilVisible(token, desired)", settings)
        self.assertIn("j.visible === desired", settings)
        # And the visual switch is CSS-driven (a real checkbox), not inline JS.
        self.assertIn("vm-switch", settings)

    def test_companion_visibility_uses_one_platform_adapter_interface(self):
        # ONE shared control interface selects a platform adapter (Windows app
        # IPC, Android overlay, or the loopback controller) so both platforms
        # share the same flow -- no second control, no duplicate companion.
        settings = self._read("static/settings.js")
        self.assertIn("function vmCompanionControl()", settings)
        self.assertIn('"windows-app"', settings)
        self.assertIn('"android-overlay"', settings)
        self.assertIn('"controller"', settings)
        self.assertIn("window.vmCompanionAndroid", settings)

    def test_unsupported_environment_reports_accurate_limitation(self):
        # A browser/PWA (especially on mobile) can never create a system overlay:
        # it must NOT pretend to activate one, and must not show Windows-only
        # wording on a phone. The limitation is explicit and platform-aware.
        settings = self._read("static/settings.js")
        self.assertIn("function vmCompanionLimitationMessage()", settings)
        self.assertIn("ValleyMind Android app", settings)
        self.assertIn("ValleyMind iOS app", settings)
        self.assertIn('kind: "unsupported"', settings)

    def test_backend_readiness_is_robust_and_truthful(self):
        # Readiness is a real /auth/status probe, not a fixed sleep. The wait
        # must fail fast when the child exits and report the child's own output.
        # The timeout is only a safety cap; the slow-start cause is fixed at the
        # source (see the two tests below).
        main = self._read("desktop/main.js")
        self.assertIn("/auth/status", main)
        self.assertIn("function serverLogTail", main)
        self.assertIn("before it was ready", main)
        self.assertIn("Backend output:", main)
        self.assertIn("if (serverExited)", main)

    def test_heavy_groq_import_is_lazy(self):
        # groq pulls in a large pydantic tree (~7s to import). Importing it at
        # module load made the backend miss the readiness window, so it must be
        # imported on first use inside a helper instead.
        ext = self._read("core/external_apis.py")
        self.assertIn("def _groq_client", ext)
        self.assertNotIn("\nfrom groq import Groq", ext)  # not a top-level import
        self.assertIn("    from groq import Groq", ext)   # inside the helper only
        self.assertNotIn("client = Groq(", ext)           # call sites use the helper

    def test_auth_migration_does_not_block_startup(self):
        # The Mongo auth migration used to run at import, before the port bound,
        # so a slow/offline DB pushed startup past the readiness window. It must
        # now run in a background daemon thread.
        app_src = self._read("app.py")
        self.assertIn("def _run_auth_migration", app_src)
        self.assertIn("target=_run_auth_migration", app_src)
        self.assertIn("daemon=True", app_src)

    def test_unified_start_launches_the_single_app(self):
        pkg = json.loads(self._read("desktop/package.json"))
        self.assertIn("--app", pkg["scripts"]["start"])
        # The companion-only (browser-relay) mode stays available, but is not the
        # default entry point any more.
        self.assertIn("start:companion", pkg["scripts"])

    def test_closing_main_window_quits_the_unified_app(self):
        # Requirement: closing ValleyMind fully shuts down the companion and the
        # backend. Closing the main window (not minimizing) must quit the app.
        main = self._read("desktop/main.js")
        self.assertIn("if (APP_MODE && !quitting) app.quit();", main)

    def test_minimize_never_hides_the_companion(self):
        main = self._read("desktop/main.js")
        self.assertNotIn('on("minimize"', main)
        self.assertNotIn("'minimize'", main)

    def test_settings_reports_actual_visibility_over_ipc(self):
        settings = self._read("static/settings.js")
        self.assertIn('typeof actual === "boolean"', settings)
        preload = self._read("desktop/preload.js")
        self.assertIn('ipcRenderer.invoke("desktop:set-visible"', preload)

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


class CompanionPresentationTests(unittest.TestCase):
    """Embedded<->external presentation-mode switching.

    The ONE Settings toggle moves the SAME companion between the embedded
    presentation (inside ValleyMind) and the external floating window. The rules
    that make the switch safe live in a small, pure coordinator
    (static/companion_presentation.js) plus the confirmation-gated wiring in
    static/settings.js. These are structural + Node-executed checks; they are
    NOT a substitute for genuine Windows/Electron verification.
    """

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_presentation_coordinator_module_exists(self):
        js = self._read("static/companion_presentation.js")
        self.assertIn("createCoordinator", js)
        self.assertIn("externalConfirmedVisible", js)
        self.assertIn("externalHidden", js)
        self.assertIn("reconcile", js)
        # Pure module: it must not touch the DOM, the network or the bridge.
        for bad in ("document.", "fetch(", "window.vmDesktop", "vmVrmCompanion"):
            self.assertNotIn(bad, js, f"coordinator must stay pure: found {bad}")

    def test_index_loads_coordinator_before_settings(self):
        html = self._read("index.html")
        module = html.find("/static/companion_presentation.js")
        settings = html.find('<script src="/static/settings.js')
        self.assertNotEqual(module, -1, "companion_presentation.js must be loaded")
        self.assertNotEqual(settings, -1, "settings.js must be loaded")
        self.assertLess(module, settings, "the coordinator must load before settings.js")
        # Cache-busted, matching the versioned-URL convention for /static.
        self.assertRegex(html, r'<script src="/static/companion_presentation\.js\?v=\d+"')

    def test_settings_uses_the_presentation_coordinator(self):
        settings = self._read("static/settings.js")
        self.assertIn("VmCompanionPresentation", settings)
        self.assertIn("createCoordinator", settings)
        self.assertIn("function _vmCompanionApplyExternal(", settings)
        self.assertIn("getEmbedded", settings)

    def test_hide_is_confirmation_gated_not_optimistic(self):
        # ON must NOT hide the embedded companion until the external window is
        # CONFIRMED visible. The native adapter resolves to the actual state; the
        # controller path confirms via polling.
        settings = self._read("static/settings.js")
        self.assertIn("_vmCompanionApplyExternal(vis);", settings)     # native confirm
        self.assertIn("_vmCompanionApplyExternal(desired);", settings)  # controller confirm
        self.assertIn("j.visible === desired", settings)

    def test_failure_restores_the_embedded_companion(self):
        # Every failure/timeout path must bring the embedded companion back, so a
        # failed transition never leaves the companion invisible.
        settings = self._read("static/settings.js")
        self.assertIn("_vmCompanionApplyExternal(false);", settings)
        # Going back to embedded restores it at once (never both hidden).
        self.assertIn("if (!on) _vmCompanionApplyExternal(false);", settings)

    def test_unsupported_environment_never_hides_embedded(self):
        settings = self._read("static/settings.js")
        # The unsupported branch of the state refresh returns before it can touch
        # the embedded companion; the limitation message stays truthful.
        self.assertIn('if (ctrl.kind === "unsupported") {', settings)
        self.assertIn('vmCompanionDesktopPref(ctrl.unsupported, "#f59e0b");', settings)

    def test_toggle_revert_is_diagnosable(self):
        # A reverted switch must never be a silent mystery: the wiring records the
        # environment (adapter, platform, bridge presence) and the exact reason at
        # every outcome, and keeps a bounded in-page trail for inspection. It must
        # never record credentials or conversation content.
        settings = self._read("static/settings.js")
        self.assertIn("function _vmCompanionDiag(", settings)
        self.assertIn("window.__vmCompanionDiag", settings)
        # Environment snapshot fields.
        for field in ("adapter:", "platform:", "electronApp:", "desktopBridge:",
                      "androidBridge:", "controlUrl:", "origin:"):
            self.assertIn(field, settings)
        # A diag call at each decisive outcome.
        for stage in ('"toggle-request"', '"native-result"', '"native-error"',
                      '"controller-unreachable"', '"controller-refused"',
                      '"confirm-ok"', '"confirm-timeout"', '"confirm-lost-helper"',
                      '"controller-state-error"', '"native-get"', '"native-broadcast"'):
            self.assertIn(stage, settings, f"missing diagnostic stage {stage}")
        # Every status update is recorded (so the visible reason is captured too).
        self.assertIn('_vmCompanionDiag("status"', settings)
        # The trail is bounded.
        self.assertRegex(settings, r"__vmCompanionDiag\.length > \d+")

    def test_embedded_hide_is_non_persisting(self):
        # The coordinator hides/shows the embedded companion through hide()/show(),
        # which never persist a hidden state: reloads keep the established default
        # (visible) and only setVisible() (the explicit "Show companion" toggle)
        # writes prefs.visible.
        js = self._read("static/vrm_companion.js")
        hide_body = js.split("function hide() {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("prefs.visible", hide_body, "hide() must not persist a hidden state")
        self.assertIn("if (prefs.visible === false) { hide(); return; }", js)
        self.assertIn("prefs.visible = !!on;", js)  # persisted only by setVisible()

    @unittest.skipIf(NODE is None, "node is not available")
    def test_presentation_coordinator_node_runner_passes(self):
        proc = subprocess.run(
            [NODE, str(PRESENTATION_RUNNER)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        self.assertEqual(
            proc.returncode, 0,
            msg="coordinator runner exited non-zero\nstdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr),
        )
        summary = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual(summary["failed"], [], summary)
        self.assertGreater(summary["passed"], 0, "coordinator runner asserted nothing")


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

    def test_sync_reports_actual_visibility_to_state(self):
        # The companion reports its REAL window visibility via /sync; /state then
        # reflects that — not merely the requested value — so the UI can never
        # show a false ON.
        self._req("/desired")  # establish liveness (heartbeat) without a child
        self._req("/sync", "POST", {"visible": True})
        status, body, _ = self._req("/state")
        self.assertEqual(status, 200)
        self.assertTrue(body["running"])
        self.assertTrue(body["visible"])
        self._req("/sync", "POST", {"visible": False})
        _, body, _ = self._req("/state")
        self.assertFalse(body["visible"])

    def test_state_reports_actual_not_merely_requested(self):
        # Ask for ON, but the companion reports the window is STILL hidden (e.g.
        # it is starting, or failed to show): /state must say visible=false while
        # exposing the requested value separately as `desired`.
        self._req("/desired")
        self._req("/visibility", "POST", {"visible": True})
        _, body, _ = self._req("/state")
        self.assertTrue(body["desired"], "the request is tracked as the desired state")
        self.assertFalse(body["visible"], "actual visibility is reported, not the request")
        # Only once the companion confirms does /state flip to visible=true.
        self._req("/sync", "POST", {"visible": True})
        _, body, _ = self._req("/state")
        self.assertTrue(body["visible"])

    def test_state_exposes_desired_field(self):
        _, body, _ = self._req("/state")
        self.assertIn("desired", body)
        self.assertIn("visible", body)

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
