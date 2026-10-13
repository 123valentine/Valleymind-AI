"""ValleyMind VRM desktop-companion integration tests.

These lock in the contract that the VRM companion is the SAME body the
/vrm-preview page runs, wired into the existing app shell as a mutually
exclusive alternative to the legacy Cloud character, and that it REUSES --
never duplicates -- the existing AI brain, voice pipeline, screen-vision module
and settings storage.

Two layers:

  1. Static guarantees on index.html and static/vrm_companion.js (overlay
     markup, import map, module load, mutual-exclusion CSS, app-shell
     lifecycle hooks, brain/voice/vision endpoints, compact default size,
     persistence keys, and the regression that the voice hooks are actually
     installed rather than merely declared).
  2. The real Flask test client serving the isolated /vrm-preview surface:
     the page, its own ES modules, the shared read-only valleymind.vrm, and
     the shared read-only Hanami .vrma library.

Run with:
  C:\\Users\\EGBUJIE VALENTINE\\Desktop\\Valleymind-AI\\env311\\Scripts\\python.exe -m pytest tests/test_vrm_companion.py -v
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module


class VrmCompanionStaticTests(unittest.TestCase):
    """Structural checks on index.html and static/vrm_companion.js."""

    def _html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _companion(self):
        return (ROOT / "static" / "vrm_companion.js").read_text(encoding="utf-8")

    def _settings(self):
        return (ROOT / "static" / "settings.js").read_text(encoding="utf-8")

    def _preview_html(self):
        return (ROOT / "vrm_preview" / "index.html").read_text(encoding="utf-8")

    # ── overlay exists in the app shell ────────────────────────────────────
    def test_overlay_markup_present(self):
        html = self._html()
        # Clean floating avatar: a bare canvas plus the optional chat panel.
        # No status chip, no clip/debug label, no task dashboard.
        for marker in (
            'id="vmVrmCompanion"',
            'id="vmVrmCompanionCanvas"',
            'id="vmVrmCompanionChat"',
            'id="vmVrmCompanionInput"',
        ):
            self.assertIn(marker, html, f"missing overlay element {marker}")

    def test_enclosing_chrome_is_removed(self):
        html = self._html()
        # The drag header, control bars, attached settings popover, the
        # in-overlay permission checklist, the idle status chip, the clip/debug
        # label and the whole on-screen task dashboard are gone; the character
        # renders directly over the app with no enclosing box.
        for gone in (
            'id="vmVrmCompanionHandle"',
            'id="vmVrmCompanionHomeBtn"',
            'id="vmVrmCompanionHideBtn"',
            'id="vmVrmCompanionChatBtn"',
            'id="vmVrmCompanionSettingsBtn"',
            'id="vmVrmCompanionSettings"',
            'id="vmVrmCompanionSize"',
            'id="vmVrmCompanionDock"',
            'id="vmVrmCompanionTaskBtn"',
            'id="vmVrmCompanionTasksOpen"',
            'id="vmVrmCompanionStatus"',
            'id="vmVrmCompanionLogLine"',
            'id="vmVrmCompanionChatStatus"',
            'id="vmVrmCompanionTask"',
            'id="vmVrmCompanionTaskStatus"',
            'id="vmVrmCompanionTaskInput"',
        ):
            self.assertNotIn(gone, html, f"obsolete overlay control still present: {gone}")

    def test_import_map_and_module_loaded(self):
        html = self._html()
        self.assertIn('type="importmap"', html)
        self.assertIn('/static/vrm_companion.js', html)
        # The module borrows the preview's Three.js / three-vrm install rather
        # than vendoring a second copy.
        self.assertIn('"/vrm-preview/assets/node_modules/three/build/three.module.js"', html)
        self.assertIn('@pixiv/three-vrm', html)
        self.assertIn('@pixiv/three-vrm-animation', html)

    def test_mutual_exclusion_with_cloud(self):
        html = self._html()
        # Exactly one companion is visible: the non-active one is hidden by CSS.
        self.assertIn('body[data-vm-companion="vrm"] #vmCloudCharacter', html)
        self.assertIn('body[data-vm-companion="cloud"] #vmVrmCompanion', html)
        # The persisted mode is applied to <body> before lifecycle code runs.
        self.assertIn("data-vm-companion", html)
        self.assertIn("valleymind.vrm_companion", html)

    def test_exclusivity_beats_clouds_inline_important(self):
        # The app shell force-shows Cloud with an INLINE display:block !important,
        # which outranks the stylesheet mutual-exclusion rule. The VRM module must
        # pin the inactive Cloud elements with an inline display:none !important,
        # and the app shell must refuse to re-assert Cloud while VRM is active.
        js = self._companion()
        self.assertIn("function applyCharacterVisibility()", js)
        self.assertIn('"vmCloudCharacter"', js)
        self.assertIn('setProperty("display", "none", "important")', js)
        html = self._html()
        self.assertIn(
            'document.body.getAttribute("data-vm-companion") === "vrm"', html)

    def test_app_shell_dispatches_companion_lifecycle(self):
        html = self._html()
        self.assertIn("window.vmVrmCompanionDispatch()", html)
        self.assertIn("window.vmVrmCompanionCleanup()", html)
        # Cloud is only shown when the VRM companion is NOT the active mode.
        self.assertIn('window.vmVrmCompanion.getMode() === "vrm"', html)

    def test_cloud_rollback_controls_remain(self):
        # The legacy Cloud character stays available as the rollback option,
        # chosen from the main Settings → Companion section.
        settings = self._settings()
        self.assertIn('data-vm-mode="cloud"', settings)
        self.assertIn('data-vm-mode="vrm"', settings)
        self.assertIn("vmCompanionSetMode", settings)

    # ── reuse (never duplicate) the existing systems ───────────────────────
    def test_reuses_preview_body_engine(self):
        js = self._companion()
        self.assertIn('from "/vrm-preview/assets/companion.js"', js)
        self.assertIn("SKIN_PRESETS", js)
        # No second body engine / face channel is defined here.
        self.assertNotIn("new THREE.", js)
        self.assertNotIn("class Companion", js)

    def test_reuses_existing_brain_not_a_new_one(self):
        js = self._companion()
        self.assertIn('"/api/cloud/chat"', js)
        self.assertIn('"/chat/sessions"', js)
        # No alternative/second chat backend is introduced.
        self.assertNotIn("/api/vrm", js)
        self.assertNotIn("/api/companion", js)

    def test_reuses_existing_voice_and_screen(self):
        js = self._companion()
        self.assertIn("window.VMCloudVoice", js)
        self.assertIn("window.VMCloudVision", js)
        # Voice input goes through the shared Web Speech wrapper.
        self.assertIn("window.VMCloudVoice.start()", js)
        self.assertIn("setLevelListener", js)

    def test_voice_hooks_are_actually_installed(self):
        # Regression: wireVoice() was previously declared but never invoked, so
        # listening/speaking status and amplitude mouth-sync were dead.
        js = self._companion()
        self.assertIn("function wireVoice()", js)
        self.assertIn("wireVoice();", js)
        self.assertIn("setHooks", js)

    def test_voice_callbacks_noop_when_cloud_is_active(self):
        js = self._companion()
        # voiceActive() fences the shared singleton's hooks to the VRM mode.
        self.assertIn("function voiceActive()", js)
        self.assertIn("getMode() === \"vrm\"", js)

    # ── companion geometry + persistence ───────────────────────────────────
    def test_default_size_is_compact_full_body(self):
        js = self._companion()
        self.assertIn("sizePct: 16", js)
        # The size control lives in the main Settings → Companion section and
        # stays in the requested compact band's vicinity.
        settings = self._settings()
        self.assertIn('id="vmSetSize" type="range" min="10" max="24"', settings)
        self.assertIn("vmCompanionSetSize", settings)

    def test_home_and_show_hide_controls(self):
        js = self._companion()
        self.assertIn("function home()", js)
        self.assertIn("function hide()", js)
        self.assertIn("function setVisible(", js)
        self.assertIn("prefs.sizePct = DEFAULTS.sizePct", js)
        # Visibility + reset are recoverable from the main Settings section.
        settings = self._settings()
        self.assertIn("Show companion", settings)
        self.assertIn("vmCompanionResetHome", settings)
        self.assertIn("vmCompanionSetVisible", settings)

    def test_persists_position_size_visibility_skin(self):
        js = self._companion()
        for key in ("sizePct", "dock", "skin", "visible", "mode", "x", "y"):
            self.assertIn(key, js, f"pref {key} not persisted")
        self.assertIn("localStorage.setItem", js)

    def test_no_duplicate_backend_or_window(self):
        js = self._companion()
        self.assertNotIn("app.run", js)
        self.assertNotIn("new WebSocket", js)
        self.assertNotIn("window.open(", js)

    def test_preview_page_runs_the_same_engine(self):
        preview = self._preview_html()
        self.assertIn("/vrm-preview/assets/page.js", preview)
        self.assertIn("/vrm-preview/assets/node_modules/three/build/three.module.js", preview)

    # ── voice-first companion behavior ──────────────────────────────────────
    def test_first_use_access_prompt_present(self):
        html = self._html()
        self.assertIn('id="vmCompanionAccess"', html)
        self.assertIn('id="vmCompanionAccessAllow"', html)
        self.assertIn('id="vmCompanionAccessDeny"', html)
        self.assertIn("Allow ValleyMind Companion access?", html)
        js = self._companion()
        self.assertIn('ACCESS_KEY = "valleymind.companion_access"', js)
        self.assertIn("function accessDecision()", js)
        self.assertIn("function maybePromptAccess()", js)
        # Distinct from consequential-action task permissions.
        self.assertIn("vmCompanionAccessAllow", js)
        self.assertIn("setAccessDecision(\"allowed\")", js)

    def test_access_gates_automatic_listening(self):
        js = self._companion()
        # Auto-listening requires explicit first-use consent + unmuted + visible.
        self.assertIn("!accessAllowed()", js)
        self.assertIn("prefs.muted", js)
        self.assertIn("function resumeListening()", js)
        self.assertIn("function startVoiceSession()", js)
        # The prompt is asked only once and only while signed in.
        self.assertIn("vmIsAuthenticated", js)
        self.assertIn("accessDecision() !== null", js)

    def test_voice_button_and_mute_control(self):
        html = self._html()
        # The idle status/mute chip was removed from the avatar; the mic still
        # has its dedicated button, and mute lives in Settings → Companion.
        self.assertNotIn('id="vmVrmCompanionStatus"', html)
        self.assertNotIn(".vmvrm-pill", html)
        self.assertIn('id="vmVrmCompanionMicBtn"', html)
        js = self._companion()
        self.assertIn("function toggleMute()", js)
        self.assertIn("function setMuted(", js)
        # Muting also stops an in-flight side effect (speech + schedules).
        self.assertIn("window.VMCloudVoice.stop()", js)
        self.assertIn("clearResumeTimer()", js)
        settings = self._settings()
        self.assertIn("vmCompanionSetMuted", settings)
        self.assertIn("Hands-free voice", settings)

    def test_voice_first_loop_resumes_after_reply(self):
        js = self._companion()
        self.assertIn("resumeListening()", js)
        self.assertIn("onSpeakEnd: function", js)
        # Overlap / duplicate guard: ignore a final result while a turn is busy.
        self.assertIn("if (busy) return;", js)
        self.assertIn("function clearResumeTimer()", js)
        self.assertIn("resumeTimer = setTimeout", js)

    def test_graceful_timeout_recovery_is_spoken(self):
        js = self._companion()
        self.assertIn("function chatWithTimeout(", js)
        self.assertIn("vmTimedOut", js)
        self.assertIn("that request took too long", js)
        self.assertIn("function guardSpeakFallback(", js)
        # The companion never stays stuck in "thinking" behind a hung request.
        self.assertIn("busy = false;", js)

    def test_reply_budget_matches_transport_and_surfaces_errors(self):
        # The blocking brain adapter walks a provider fallback chain, so a
        # wall-clock cap SHORTER than the transport timeout reported healthy but
        # slow replies as "took too long". The cap must match the transport, and
        # a non-2xx / non-JSON failure must surface the real status or message
        # instead of being collapsed to a generic "unreachable".
        js = self._companion()
        self.assertIn("const CHAT_BUDGET_MS = 120000;", js)
        self.assertIn("timeoutMs: CHAT_BUDGET_MS", js)
        self.assertIn("}), CHAT_BUDGET_MS);", js)
        self.assertNotIn("}), 45000);", js)
        self.assertIn("resp.ok", js)
        self.assertIn("resp.status", js)

    def test_reply_emotion_is_grounded_not_stale_state(self):
        # The post-reply emotion must come from THIS exchange (message + reply),
        # not the backend's d.state.emotion -- that field is the Cloud section's
        # default state, so a stored "angry" replayed after every unrelated turn
        # (e.g. a plain greeting).
        js = self._companion()
        self.assertIn("function emotionForReply(", js)
        self.assertIn("const emo = emotionForReply(text, reply)", js)
        self.assertNotIn("function endEmotion(", js)
        self.assertNotIn("const emo = endEmotion(d)", js)
        self.assertIn("EMOTION_CUES", js)
        self.assertIn("angry:", js)
        # Default is a neutral idle, never an unjustified dramatic reaction.
        self.assertIn('return "idle";', js)

    def test_mouth_is_closed_when_speech_ends(self):
        # The face channel keeps a target level + speaking flag, and simulates a
        # wobble whenever speaking is on with no level. Ending speech (or stop /
        # mute / hide) must explicitly close BOTH, or the mouth stays open or
        # keeps moving after the voice has finished.
        js = self._companion()
        self.assertIn("function setFaceTalking(", js)
        self.assertIn("engine.face.setSpeaking(true)", js)
        self.assertIn("engine.face.setSpeaking(false)", js)
        self.assertIn("engine.face.setTalkLevel(0)", js)
        # Closed on speak end, on every non-speaking status, on stop and on hide.
        self.assertGreaterEqual(js.count("setFaceTalking(false)"), 4)
        self.assertIn('if (s !== "speaking") setFaceTalking(false);', js)

    def test_purposeful_walking_reuses_existing_gait(self):
        js = self._companion()
        self.assertIn("function walkTo(", js)
        self.assertIn('engine.trigger("walking")', js)
        self.assertIn("function clampToViewport(", js)
        self.assertIn("function cancelWalk()", js)
        self.assertIn("function guide(", js)
        # The real walking clips exist in the shared manifest.
        manifest = json.loads((ROOT / "vrm_preview" / "clips.json").read_text(encoding="utf-8"))
        self.assertIn("world-walk", manifest["behaviours"]["walking"])

    def test_task_panel_hidden_from_normal_controls(self):
        html = self._html()
        # The on-screen dashboard is gone from the main view entirely...
        self.assertNotIn('id="vmVrmCompanionTaskBtn"', html)
        self.assertNotIn('id="vmVrmCompanionTasksOpen"', html)
        self.assertNotIn('id="vmVrmCompanionTask"', html)
        js = self._companion()
        self.assertIn("function openTaskPanel()", js)
        self.assertIn("function wireTaskControls()", js)
        self.assertIn("awaiting_confirmation", js)
        # ...but the plan + controls are preserved inline in Settings → Companion.
        settings = self._settings()
        self.assertIn('id="vmVrmCompanionTask"', settings)
        self.assertIn('id="vmVrmCompanionTaskInput"', settings)
        self.assertIn('id="vmVrmCompanionTaskApprove"', settings)
        self.assertIn("wireTaskControls", settings)
        self.assertIn("vmCompanionOpenTasks", settings)
        self.assertIn("vmCompanionTogglePerm", settings)

    # ── standalone character: transparent overlay + settings section ───────
    def test_companion_section_registered_in_settings(self):
        settings = self._settings()
        self.assertIn('{ id: "companion",     label: "Companion" }', settings)
        self.assertIn('case "companion":', settings)
        self.assertIn("function renderCompanionSection(", settings)

    def test_settings_script_is_cache_busted(self):
        # /static/* is served with Cache-Control: max-age=86400, so a changed
        # settings.js only reaches browsers when the shell requests a versioned
        # URL. Without the query, a stale settings.js is served and the
        # Companion section never appears even though it is registered.
        html = self._html()
        self.assertRegex(html, r'<script src="/static/settings\.js\?v=\d+"')

    def test_transparent_overlay_is_opt_in(self):
        # The shared preview engine gains a transparent mode that defaults OFF
        # (so /vrm-preview is unchanged); the app companion opts in.
        engine = (ROOT / "vrm_preview" / "companion.js").read_text(encoding="utf-8")
        self.assertIn("this.transparent = !!opts.transparent", engine)
        self.assertIn("alpha: this.transparent", engine)
        js = self._companion()
        self.assertIn("transparent: true", js)

    def test_overlay_is_non_obstructive(self):
        html = self._html()
        # The overlay itself must not swallow clicks on the app beneath it.
        self.assertIn('id="vmVrmCompanion"', html)
        self.assertIn("pointer-events:none", html.replace(" ", ""))
        # No residual boxed chrome (card / chip / debug label) on the avatar.
        self.assertNotIn(".vmvrm-pill", html)
        self.assertNotIn(".vmvrm-logl", html)

    # ── event-driven animation + voice-first routing ───────────────────────
    def test_animation_is_event_driven_not_random(self):
        js = self._companion()
        # The autonomous random state machine stays OFF for the companion, so
        # idle never cycles through unrelated clips; every clip is chosen by
        # setStatus() from a real app event.
        self.assertIn("engine.setAuto(false)", js)
        self.assertNotIn("setAuto(true)", js)
        self.assertIn('engine.trigger("idle")', js)
        self.assertIn("function setStatus(", js)
        # Momentary reactions settle back to idle on their own.
        self.assertIn("accentTimer", js)
        # One engine, one mixer, one central controller: no second instance.
        self.assertEqual(js.count("new Companion("), 1)
        self.assertIn("engine.trigger(", js)

    def test_voice_first_task_routing(self):
        js = self._companion()
        # Spoken/typed instructions route between control words, the existing
        # task layer, and ordinary conversation on the same brain.
        self.assertIn("function looksLikeTask(", js)
        self.assertIn("function resolveTaskVoiceCommand(", js)
        self.assertIn("function runTask(", js)
        self.assertIn("async function chatTurn(", js)
        # Already-running tasks are acknowledged, never silently duplicated.
        self.assertIn("I'm still on your last request", js)
        # Accurate reporting: only controller-confirmed states are spoken.
        self.assertIn("function speakTaskNotice(", js)
        self.assertIn("lastTaskState", js)
        # The controller emits (kind, snapshot): the handler MUST read the
        # second arg or it mistakes the event kind for the task state.
        self.assertIn("onEvent: function (kind, snap)", js)

    def test_task_dashboard_text_removed_from_main_view(self):
        html = self._html()
        for gone in ("idle · no task", "No plan yet",
                     "vmVrmCompanionTaskStatus", "vmVrmCompanionLogLine"):
            self.assertNotIn(gone, html, f"internal planning/debug text still on main view: {gone}")


class VrmPreviewRouteTests(unittest.TestCase):
    """/vrm-preview must serve the page, modules, model and clip library."""

    @classmethod
    def setUpClass(cls):
        cls.client = app_module.app.test_client()

    def test_page_is_served(self):
        resp = self.client.get("/vrm-preview")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"VRM Companion Preview", resp.data)

    def test_engine_module_is_served(self):
        resp = self.client.get("/vrm-preview/assets/companion.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"class Companion", resp.data)

    def test_face_module_is_served(self):
        resp = self.client.get("/vrm-preview/assets/face.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"class FaceChannel", resp.data)

    def test_manifest_is_served(self):
        resp = self.client.get("/vrm-preview/assets/clips.json")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["counts"]["total"], 166)
        self.assertEqual(data["counts"]["active"], 151)

    def test_model_is_served_unmodified(self):
        resp = self.client.get("/vrm-preview/model", buffered=False)
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(int(resp.headers.get("Content-Length", "0")), 1000)

    def test_clip_library_is_served(self):
        manifest = json.loads(
            (ROOT / "vrm_preview" / "clips.json").read_text(encoding="utf-8"))
        active = [c for c in manifest["clips"] if c.get("library") == "active"]
        self.assertTrue(active)
        resp = self.client.get("/vrm-preview/clips/" + active[0]["file"])
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.data), 100)

    def test_spare_clip_subfolder_is_served(self):
        manifest = json.loads(
            (ROOT / "vrm_preview" / "clips.json").read_text(encoding="utf-8"))
        spare = [c for c in manifest["clips"] if c.get("library") != "active"]
        if not spare:
            self.skipTest("no spare clips in catalogue")
        resp = self.client.get("/vrm-preview/clips/" + spare[0]["file"])
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.data), 100)


class VrmCompanionPhase1Tests(unittest.TestCase):
    """Phase-1 'living companion' contracts.

    Lock in: varied natural idle, restrained roaming, drag/free positioning
    (user-preferred), persistence of position/visibility across views, phone
    sizing/re-framing, a reduced-motion / hidden-tab pause, and the sleep/wake
    voice-state PREPARATION (which never claims a working wake word).
    """

    def _html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _companion(self):
        return (ROOT / "static" / "vrm_companion.js").read_text(encoding="utf-8")

    def _settings(self):
        return (ROOT / "static" / "settings.js").read_text(encoding="utf-8")

    # ── varied natural idle ────────────────────────────────────────────────
    def test_varied_idle_rotates_at_random_intervals(self):
        js = self._companion()
        self.assertIn("function scheduleIdleVariation(", js)
        self.assertIn("const IDLE_MIN_MS = 9000;", js)
        self.assertIn("const IDLE_MAX_MS = 21000;", js)
        self.assertIn("function clearIdleTimer(", js)
        # Rotation reuses the ONE authoritative entry point and never turns the
        # broad autonomous loop on.
        self.assertIn('engine.trigger("idle")', js)
        self.assertNotIn("setAuto(true)", js)
        self.assertIn("scheduleIdleVariation();", js)
        # Rotates ONLY while resting: any driven state clears the idle timer.
        self.assertIn('if (s !== "idle") { clearIdleTimer(); clearRoamTimer(); }', js)

    # ── restrained roaming + free positioning ──────────────────────────────
    def test_restrained_roaming_reuses_walking_and_clamping(self):
        js = self._companion()
        self.assertIn("function scheduleRoam(", js)
        self.assertIn("function roamStep(", js)
        self.assertIn("const ROAM_MIN_MS = 32000;", js)
        # Roaming moves through the SAME gait + viewport clamp as guidance.
        self.assertIn("walkTo(tx, ty", js)
        self.assertIn("function clampToViewport(", js)
        # Roam pauses once the user has placed the avatar by hand.
        self.assertIn("prefs.userPlaced !== true", js)

    def test_drag_repositions_and_marks_user_preferred(self):
        js = self._companion()
        self.assertIn("function wireDrag(", js)
        self.assertIn('"pointerdown"', js)
        self.assertIn('"pointermove"', js)
        self.assertIn("prefs.userPlaced = true", js)
        # Position is persisted (x / y already exist) and roaming is paused.
        self.assertIn("clearRoamTimer();", js)
        # Home / dock / reset clear the manual-placement lock so roaming can resume.
        self.assertGreaterEqual(js.count("prefs.userPlaced = false"), 3)
        # A press inside the chat panel is never a drag/tap.
        self.assertIn("chat.contains(e.target)", js)

    def test_tap_opens_existing_chat_not_a_new_control(self):
        js = self._companion()
        # The discreet touch control surface reuses the EXISTING chat panel.
        self.assertIn("toggleChat();", js)
        self.assertIn("function toggleChat(", js)

    # ── persistence across views (no duplication / no teardown) ─────────────
    def test_visible_over_internal_fullscreen_views(self):
        html = self._html()
        # Raised above Help & Learn / Settings (99999) so the companion remains
        # present while navigating; the old value is gone.
        self.assertIn("z-index: 100000;", html)
        self.assertNotIn("z-index: 99998", html)
        # Still a single engine + a single module (no duplicate renderer/mixer).
        js = self._companion()
        self.assertEqual(js.count("new Companion("), 1)
        self.assertIn("visibilitychange", js)
        # Hidden tab pauses the GPU instead of destroying the engine.
        self.assertIn("engine.setRendering(false)", js)

    # ── phone sizing / re-framing ──────────────────────────────────────────
    def test_resize_and_orientation_reframe_and_clamp(self):
        js = self._companion()
        self.assertIn("function refit(", js)
        self.assertIn("engine.frameFullBody", js)
        self.assertIn("function clampPosition(", js)
        self.assertIn('window.addEventListener("resize", onViewportChange)', js)
        self.assertIn('window.addEventListener("orientationchange", onViewportChange)', js)
        # Phone height is applied in px so it is never rounded to a cramped box.
        self.assertIn('el.style.height = px + "px";', js)

    # ── reduced motion ─────────────────────────────────────────────────────
    def test_reduced_motion_disables_autonomous_movement(self):
        js = self._companion()
        self.assertIn("function reducedMotion(", js)
        self.assertIn("prefers-reduced-motion: reduce", js)
        self.assertIn("function motionOk()", js)
        # Both the idle rotation and roaming bail out under reduced-motion.
        self.assertIn("if (!motionOk()) return;", js)
        self.assertIn("motionOk()", js)

    # ── voice-interaction state preparation (no faked wake word) ────────────
    def test_sleep_wake_state_is_preparation_not_a_claimed_wake_word(self):
        js = self._companion()
        self.assertIn("function sleep()", js)
        self.assertIn("function wake()", js)
        self.assertIn("function isAwake()", js)
        self.assertIn("awake: true,", js)
        # Awake gates BOTH the immediate and the post-reply listen paths.
        self.assertGreaterEqual(js.count("if (!isAwake()) return;"), 3)
        # No always-on recognition loop is introduced; the wake-word engine is a
        # documented native-Android follow-up, not something faked here.
        self.assertNotIn("continuous = true", js)

    def test_new_controls_surface_in_settings(self):
        settings = self._settings()
        self.assertIn("function vmCompanionSetRoam(", settings)
        self.assertIn("function vmCompanionSetAwake(", settings)
        self.assertIn("Wander around", settings)
        self.assertIn("Awake & ready", settings)
        self.assertIn('"vmSetRoam"', settings)
        self.assertIn('"vmSetAwake"', settings)

    def test_module_is_cache_busted(self):
        html = self._html()
        self.assertRegex(html, r'<script type="module" src="/static/vrm_companion\.js\?vm=\d+"')


if __name__ == "__main__":
    unittest.main()
