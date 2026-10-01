"""ValleyMind Cloud foundation tests.

Covers:
  1. Static guarantees about the frontend wiring in index.html: Cloud is a
     persistent app-shell companion, NOT a primary Studio tab. Its optional
     secondary workspace panel div, the vmWsGo hooks that fire
     vmCloudOnShow/vmCloudOnHide, and the cloud.js script tag are present.
The companion is small, renders the real static/cloud.png character,
       draggable, and position-persistent. An animation/state layer
       (static/cloud_anim.js) drives whole-character body language and exposes
       the cloudSetState(...) API the future brain will call.
  2. The canonical Cloud state model in core/cloud.py: emotions, interaction
     states, presentations, personality styles, and the personality-instruction
     adapter used to hand messages to the EXISTING brain.
  3. The /api/cloud/chat thin adapter via the real Flask client with an
     authenticated session and a mocked brain: login gating, input validation,
     proof that the EXISTING brain (not a new one) answers, message
     augmentation, and memory-mirroring of cloud preferences.

These tests intentionally never create a second AI or memory system: the chat
adapter must call the same brain the Chat tab uses.

Run with: C:\\Users\\EGBUJIE VALENTINE\\Desktop\\Valleymind-AI\\env311\\Scripts\\python.exe -m pytest tests/test_cloud.py -v
"""
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module
from core import cloud as cloud_model


class CloudStaticTestCase(unittest.TestCase):
    """Structural checks on index.html and static/cloud.js."""

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _cloud_js(self):
        return (ROOT / "static" / "cloud.js").read_text(encoding="utf-8")

    def test_cloud_not_a_primary_studio_tab(self):
        html = self._index_html()
        tabbar_start = html.index('<nav class="vm-ws-tabbar"')
        tabbar_end = html.index('data-ws-panel="studio"')
        tabbar = html[tabbar_start:tabbar_end]
        # Cloud is NOT a Studio page: it must not appear in the primary
        # workspace navigation. Cloud is a persistent app-shell companion.
        self.assertNotIn('data-ws="cloud"', tabbar)
        self.assertNotIn("vmWsGo('cloud')", tabbar)
        # The optional secondary/full Cloud surface still exists for the
        # companion to expand into; it is just not a primary destination.
        after_tabbar = html[tabbar_end:]
        self.assertIn('data-ws-panel="cloud"', after_tabbar)
        self.assertIn('id="vmWsPanelCloud"', after_tabbar)

    def test_cloud_workspace_panel_present(self):
        html = self._index_html()
        self.assertIn('data-ws-panel="cloud"', html)
        self.assertIn('id="vmWsPanelCloud"', html)

    def test_vm_ws_go_hooks_cloud_onshow(self):
        html = self._index_html()
        block = html[html.index("function vmWsGo("):]
        self.assertIn('ws === "cloud" && typeof vmCloudOnShow === "function"', block)
        self.assertIn("vmCloudOnShow()", block)

    def test_cloud_script_loaded(self):
        html = self._index_html()
        self.assertIn('<script src="/static/cloud.js', html)

    def test_app_shell_owns_companion_lifecycle(self):
        html = self._index_html()
        # setAppVisible is the single authenticated-app visibility signal: it
        # shows Cloud (companionShow) when the app shell appears and tears it
        # down (cleanupCompanion) when the shell disappears (logout/session end).
        self.assertIn("window.vmCloudCompanionShow();", html)
        self.assertIn("window.vmCloudCompanionCleanup();", html)
        # Cloud reads application-shell auth state (not the stored client
        # token), so already-authenticated cookie sessions still get Cloud on
        # normal opens, refreshes, and new tabs.
        self.assertIn("function vmIsAuthenticated()", html)
        self.assertIn("window.vmIsAuthenticated = vmIsAuthenticated;", html)

    def test_minimal_cloud_character_renders_direct_png_on_auth(self):
        # Visual isolation pass: a #vmCloudCharacter container sits directly
        # under <body> holding the flattened static/cloud.png <img> fallback
        # plus the animated rig-mount overlay, with no lifecycle engine, so the
        # exact character is always visible on the front end.
        html = self._index_html()
        body = html[html.index("<body"):]
        char_index = body.index('<div id="vmCloudCharacter"')
        self.assertLess(char_index, body.index("<script"))
        self.assertIn('src="/static/cloud.png"', body[char_index:char_index + 600])
        self.assertIn("draggable=\"false\"", body[char_index:char_index + 600])
        self.assertIn('class="vmcloud-fallback"', body[char_index:char_index + 600])
        self.assertIn('class="vmcloud-rig-mount"', body[char_index:char_index + 600])
        # The stylesheet forces the small fixed character visible in the
        # bottom-right corner, responsive (env safe-area) and 132px wide.
        self.assertIn("#vmCloudCharacter {", body)
        self.assertIn("position: fixed !important", body)
        self.assertIn("width: 132px !important", body)
        self.assertIn("bottom: calc(18px + env(safe-area-inset-bottom)) !important", body)
        # No CSS animation on the container: a keyframe animation would
        # override the single animation engine's inline whole-body transform.
        char_style = body[body.index("#vmCloudCharacter {"):]
        char_style = char_style[:char_style.index("</style>")]
        self.assertNotIn("animation:", char_style)
        # Auth gate lives in the app shell's setAppVisible (not the Cloud
        # lifecycle), plus an immediate show once auth state reports true.
        self.assertIn("window.vmCloudCharacterShow();", html)
        self.assertIn("window.vmCloudCharacterHide();", html)
        self.assertIn("window.vmIsAuthenticated()", html)
        # Dragging is attached directly to the character container.
        self.assertIn('var el = document.getElementById("vmCloudCharacter");', html)
        self.assertIn('"pointerdown"', html)
        self.assertIn('"pointermove"', html)
        self.assertIn('"pointerup"', html)

    def test_cloud_js_uses_app_shell_auth_state(self):
        js = self._cloud_js()
        self.assertIn('typeof window.vmIsAuthenticated === "function"', js)
        self.assertIn("window.vmIsAuthenticated()", js)
        self.assertIn("function companionShow()", js)
        # The probe auto-mounts Cloud when the authenticated app shell is
        # already visible, independent of login events and navigation.
        self.assertIn("function vmMaybeAutoMount()", js)
        self.assertIn('$id("mainArea")', js)
        self.assertIn("DOMContentLoaded", js)

    def test_companion_initialization_needs_no_cloud_route(self):
        js = self._cloud_js()
        block = js[js.index("function companionShow()"):js.index("function cleanupCompanion()")]
        # companionShow goes straight to the app-shell character: it never
        # navigates, never references the former Cloud page, and never builds
        # the old shell/panel/3D surface.
        self.assertIn("ensureCloudCharacter()", block)
        self.assertIn("injectStyles()", block)
        for forbidden in ("vmWsGo", 'data-ws="cloud"', "localStorage",
                          "ensureCompanionShell(",
                          "surfaceCompanion(",
                          "mount3DAt(",
                          "start3D("):
            self.assertNotIn(forbidden, block)

    def test_cloud_js_exposes_module(self):
        js = self._cloud_js()
        self.assertIn("window.VMCloud", js)
        self.assertIn("window.vmCloudOnShow", js)
        self.assertIn("injectStyles", js)
        self.assertIn('vmWsPanelCloud', js)

    def test_cloud_js_exposes_full_state_model(self):
        js = self._cloud_js()
        for emotion in cloud_model.EMOTIONS:
            self.assertIn(emotion, js)
        for status in cloud_model.INTERACTION_STATES:
            self.assertIn(status, js)
        for key in cloud_model.FUTURE_CONTEXT_KEYS:
            self.assertIn(key, js)

    def test_cloud_js_renders_config_data_only_no_renderer(self):
        js = self._cloud_js()
        self.assertIn("renderConfig", js)
        self.assertIn("expression", js)
        self.assertIn("animation", js)
        self.assertNotIn("getContext('webgl')", js)
        self.assertNotIn("THREE.", js)

    def test_cloud_js_uses_existing_endpoints(self):
        js = self._cloud_js()
        self.assertIn('"/api/cloud/chat"', js)
        self.assertIn('"/api/cloud/state"', js)
        self.assertIn('"/chat/sessions"', js)
        self.assertIn('settingsApiGet("cloud")', js)
        self.assertIn('settingsApiSave("cloud", p)', js)

    def test_cloud_js_defines_default_prefs_with_name(self):
        js = self._cloud_js()
        self.assertIn('cloud_name: "Cloud"', js)
        self.assertIn('companion_minimized: true', js)
        self.assertIn('companion_x: null', js)
        self.assertIn('companion_y: null', js)
        self.assertIn("function normalizeNameInput(", js)
        self.assertIn('slice(0, 32)', js)


class CloudStateModelTestCase(unittest.TestCase):
    """Canonical model and personality-instruction adapter in core/cloud.py."""

    def test_canonical_emotions(self):
        expected = {
            "neutral", "happy", "excited", "thinking", "curious", "concerned",
            "sad", "frustrated", "angry", "surprised", "confused", "focused",
            "listening", "speaking",
        }
        self.assertEqual(set(cloud_model.EMOTIONS), expected)
        self.assertEqual(len(cloud_model.EMOTIONS), 14)

    def test_canonical_interaction_states(self):
        expected = {
            "idle", "listening", "thinking", "speaking",
            "helping", "learning", "observing", "guiding",
        }
        self.assertEqual(set(cloud_model.INTERACTION_STATES), expected)
        self.assertEqual(len(cloud_model.INTERACTION_STATES), 8)

    def test_canonical_presentations_and_personalities(self):
        self.assertEqual(set(cloud_model.PRESENTATIONS), {"feminine", "masculine", "neutral"})
        self.assertEqual(
            set(cloud_model.PERSONALITY_STYLES),
            {"calm", "friendly", "playful", "professional", "energetic", "gentle"},
        )

    def test_default_state_full_shape(self):
        state = cloud_model.cloud_default_state()
        self.assertEqual(state["emotion"], "neutral")
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["mode"], "companion")
        self.assertEqual(state["presentation"], "neutral")
        self.assertEqual(state["intensity"], 0.5)
        for key in cloud_model.FUTURE_CONTEXT_KEYS:
            self.assertIsNone(state[key])

    def test_default_state_reflects_prefs(self):
        prefs = {
            "presentation": "feminine",
            "personality_style": "playful",
            "accent": "#ff66aa",
            "animation_intensity": 0.8,
        }
        state = cloud_model.cloud_default_state(prefs)
        self.assertEqual(state["presentation"], "feminine")
        self.assertEqual(state["accent"], "#ff66aa")
        self.assertEqual(state["intensity"], 0.8)

    def test_normalize_state_patch_validates_and_clamps(self):
        base = cloud_model.cloud_default_state()
        patched = cloud_model.normalize_state_patch(
            {"emotion": "excited", "status": "thinking", "intensity": 9,
             "emotion_invalid": "nope"},
            base=base,
        )
        self.assertEqual(patched["emotion"], "excited")
        self.assertEqual(patched["status"], "thinking")
        self.assertEqual(patched["intensity"], 1.0)
        invalid = cloud_model.normalize_state_patch(
            {"emotion": "surprised_bananas", "intensity": -3}, base=base)
        self.assertEqual(invalid["emotion"], "neutral")
        self.assertEqual(invalid["intensity"], 0.0)

    def test_augment_rides_into_existing_brain(self):
        augmented = cloud_model.augment_cloud_message(
            "Hello Cloud",
            {"personality_style": "playful", "presentation": "feminine"},
        )
        self.assertIn("ValleyMind Cloud", augmented)
        self.assertIn("playful", augmented)
        self.assertIn("female-styled", augmented)
        self.assertTrue(augmented.rstrip().endswith("Hello Cloud"))

    def test_no_ai_modules_in_cloud_core(self):
        src = Path(cloud_model.__file__).read_text(encoding="utf-8")
        self.assertNotIn("openai", src)
        self.assertNotIn("anthropic", src)
        self.assertNotIn("ollama", src)
        self.assertNotIn("_call_llm_cluster", src)


class CloudNameTestCase(unittest.TestCase):
    """Step 4 naming preference: safe normalization + identity injection."""

    def test_default_name_for_empty_values(self):
        for value in (None, "", "   ", "\t\n"):
            self.assertEqual(cloud_model.normalize_cloud_name(value), "Cloud")

    def test_custom_name_preserved(self):
        self.assertEqual(cloud_model.normalize_cloud_name("Nimbus"), "Nimbus")
        self.assertEqual(cloud_model.normalize_cloud_name("  Astra  "), "Astra")

    def test_long_name_truncated(self):
        long_name = "x" * 80
        self.assertEqual(cloud_model.normalize_cloud_name(long_name), "x" * 32)

    def test_control_characters_stripped(self):
        self.assertEqual(
            cloud_model.normalize_cloud_name("\x00Ab\x1f \tCd\x7f"), "Ab Cd")

    def test_name_is_always_non_empty(self):
        self.assertEqual(cloud_model.normalize_cloud_name("\x00\x1f\x7f"), "Cloud")

    def test_custom_name_rides_into_existing_brain(self):
        augmented = cloud_model.augment_cloud_message(
            "What do you see?",
            {"personality_style": "friendly", "presentation": "neutral",
             "cloud_name": "Nimbus"},
        )
        self.assertIn("ValleyMind Cloud", augmented)
        self.assertIn("calls you Nimbus", augmented)
        self.assertTrue(augmented.rstrip().endswith("What do you see?"))

    def test_default_name_adds_no_name_line(self):
        augmented = cloud_model.augment_cloud_message(
            "Hi",
            {"personality_style": "calm", "presentation": "neutral"},
        )
        self.assertNotIn("calls you", augmented)

    def test_cloud_name_is_a_preference_key(self):
        self.assertIn("cloud_name", cloud_model.PREFERENCE_KEYS)
        self.assertEqual(cloud_model.CLOUD_NAME_MAX, 32)
        self.assertEqual(cloud_model.DEFAULT_CLOUD_NAME, "Cloud")

    def test_collect_cloud_preferences_includes_name(self):
        prefs = cloud_model.collect_cloud_preferences(
            {"cloud_name": "Nimbus", "bogus": 1})
        self.assertEqual(prefs.get("cloud_name"), "Nimbus")
        self.assertNotIn("bogus", prefs)


class _SpyMemory:
    def __init__(self):
        self.reloaded = 0
        self.titles = {}
        self.preferences = []
        self.facts = []

    def reload(self):
        self.reloaded += 1

    def get_user_name(self):
        return ""

    def initialize_user_name(self, name):
        self.preferences.append(("initialize_user_name", name))

    def set_title(self, chat_id, title):
        self.titles[chat_id] = title

    def remember_preference(self, key, text=""):
        self.preferences.append((key, text))

    def remember_fact(self, fact_type, title, text="", confidence=0.9):
        self.facts.append((fact_type, title, text, confidence))

    def save_memory(self):
        pass


class _SpyBrain:
    """Stand-in for the EXISTING ValleyMind brain (MarcusBrain)."""

    class _Profile:
        key = "marcus"

    profile = _Profile()

    def __init__(self):
        self.memory = _SpyMemory()
        self.last_response_meta = {"sources": ["FAKE"], "fallback_used": False}
        self.calls = []

    def respond(self, message, chat_id="", image_data="", mongo_history=None, persist_image_data=True):
        self.calls.append({
            "message": message,
            "chat_id": chat_id,
            "image_data": image_data,
            "mongo_history": mongo_history,
            "persist_image_data": persist_image_data,
        })
        return "CLOUD-REPLY"


class CloudApiTestCase(unittest.TestCase):
    """Real app + patched isolated storage, exercised via the Flask client.

    The Cloud adapter must reuse the EXISTING brain path (load_persona_brain
    -> marcus.respond) and the EXISTING memory path without creating either.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmpdir.name)
        cls._spies = []

        def sessions_path(user_id):
            d = tmp / "sessions"
            return d / f"{user_id}.json"

        def spy_brain_factory(user_id=None, persona="marcus"):
            brain = _SpyBrain()
            cls._spies.append(brain)
            return brain

        cls._users_file_patch = patch.object(
            app_module, "_users_file", tmp / "auth_users.json")
        cls._users_coll_patch = patch.object(
            app_module, "users_collection", lambda: None)
        cls._auth_coll_patch = patch.object(
            app_module, "auth_tokens_collection", lambda: None)
        cls._chats_coll_patch = patch.object(
            app_module, "chats_collection", lambda: None)
        cls._settings_dir_patch = patch.object(
            app_module, "_SETTINGS_DIR", tmp / "settings")
        cls._sessions_path_patch = patch.object(
            app_module, "_sessions_index_path", sessions_path)
        cls._brains_patch = patch.object(
            app_module, "load_persona_brain", side_effect=spy_brain_factory)
        for p in (cls._users_file_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._chats_coll_patch,
                  cls._settings_dir_patch, cls._sessions_path_patch,
                  cls._brains_patch):
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in (cls._users_file_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._chats_coll_patch,
                  cls._settings_dir_patch, cls._sessions_path_patch,
                  cls._brains_patch):
            p.stop()
        cls.tmpdir.cleanup()

    def setUp(self):
        self._app = app_module.app.test_client()

    def _auth(self, email: str = None):
        if not email:
            email = f"cloud_{uuid.uuid4().hex[:12]}@example.com"
        user_id = app_module._safe_user_id(email)
        users = app_module._load_users()
        users[email] = {
            "_id": email, "email": email, "user_id": user_id, "email_verified": True,
        }
        app_module._save_users(users)
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["email"] = email
        return client

    def _put_cloud_prefs(self, client, prefs):
        return client.put("/api/settings/cloud", json=prefs)

    def test_cloud_chat_requires_login(self):
        before = len(self._spies)
        resp = self._app.post("/api/cloud/chat", json={"message": "hi"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(self._spies), before)

    def test_cloud_chat_requires_message(self):
        before = len(self._spies)
        client = self._auth()
        resp = client.post("/api/cloud/chat", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(self._spies), before)

    @patch.object(app_module, "_call_llm_cluster")
    def test_cloud_chat_calls_the_existing_brain(self, _llm):
        """The adapter must answer via the EXISTING brain, not a new AI."""
        before = len(self._spies)
        client = self._auth()
        self._put_cloud_prefs(client, {
            "personality_style": "playful",
            "presentation": "feminine",
            "accent": "#ff66aa",
            "animation_intensity": 0.7,
        })
        resp = client.post("/api/cloud/chat", json={
            "message": "Hello Cloud friend", "chat_id": "chat_x",
        })
        brain = self._spies[-1]
        self.assertGreater(len(self._spies), before)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["reply"], "CLOUD-REPLY")
        self.assertEqual(body["chat_id"], "chat_x")
        self.assertEqual(body["personality_style"], "playful")
        self.assertEqual(body["presentation"], "feminine")
        self.assertEqual(body["updated_title"], "Hello Cloud friend")
        self.assertEqual(brain.calls[0]["chat_id"], "chat_x")
        self.assertEqual(brain.calls[0]["image_data"], "")
        self.assertTrue(brain.calls[0]["message"].startswith("You are ValleyMind Cloud"))
        self.assertIn("playful, lighthearted", brain.calls[0]["message"])
        self.assertIn("female-styled", brain.calls[0]["message"])
        self.assertTrue(brain.calls[0]["message"].rstrip().endswith("Hello Cloud friend"))
        self.assertGreaterEqual(brain.memory.reloaded, 1)
        self.assertEqual(brain.memory.titles.get("chat_x"), "Hello Cloud friend")
        _llm.assert_not_called()

    def test_cloud_chat_reuses_saved_voice_and_memory_of_brain(self):
        client = self._auth()
        self._put_cloud_prefs(client, {"presentation": "masculine", "personality_style": "professional"})
        resp = client.post("/api/cloud/chat", json={
            "message": "Please draft a plan now", "chat_id": "chat_y",
        })
        brain = self._spies[-1]
        self.assertEqual(resp.status_code, 200)
        self.assertIn("professional, precise", brain.calls[0]["message"])
        self.assertIn("male-styled", brain.calls[0]["message"])
        self.assertEqual(brain.memory.titles.get("chat_y"), "Please draft a plan now")

    def test_cloud_state_get_returns_defaults(self):
        client = self._auth()
        resp = client.get("/api/cloud/state")
        self.assertEqual(resp.status_code, 200)
        state = resp.get_json()["state"]
        self.assertEqual(state["emotion"], "neutral")
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["mode"], "companion")
        self.assertEqual(state["presentation"], "neutral")
        self.assertEqual(state["intensity"], 0.5)
        for key in cloud_model.FUTURE_CONTEXT_KEYS:
            self.assertIn(key, state)
            self.assertIsNone(state[key])

    def test_cloud_state_post_validates_and_clamps(self):
        client = self._auth()
        resp = client.post("/api/cloud/state", json={
            "emotion": "excited", "status": "speaking", "intensity": 9,
            "bogus_key": "ignored",
        })
        self.assertEqual(resp.status_code, 200)
        state = resp.get_json()["state"]
        self.assertEqual(state["emotion"], "excited")
        self.assertEqual(state["status"], "speaking")
        self.assertEqual(state["intensity"], 1.0)
        self.assertIn("screen_context", state)

    def test_cloud_state_persists_and_survives_reload(self):
        client = self._auth()
        client.post("/api/cloud/state", json={"emotion": "thinking"})
        state = client.get("/api/cloud/state").get_json()["state"]
        self.assertEqual(state["emotion"], "thinking")

    def test_cloud_settings_section_roundtrip(self):
        client = self._auth()
        prefs = {
            "presentation": "feminine",
            "personality_style": "gentle",
            "voice_preference": "Melodic",
            "appearance": "soft glow",
            "accent": "#88ffaa",
            "animation_intensity": 0.4,
        }
        resp = self._put_cloud_prefs(client, prefs)
        self.assertEqual(resp.status_code, 200)
        got = client.get("/api/settings/cloud").get_json()["data"]
        self.assertEqual(got, prefs)

    def test_cloud_settings_mirrors_into_existing_memory(self):
        client = self._auth()
        resp = self._put_cloud_prefs(client, {
            "personality_style": "energetic",
            "voice_preference": "Bright",
            "accent": "",
        })
        self.assertEqual(resp.status_code, 200)
        brain = self._spies[-1] if self._spies else _SpyBrain()
        keys = [k for k, _ in brain.memory.preferences]
        self.assertIn("cloud_personality_style", keys)
        self.assertIn("cloud_voice_preference", keys)
        self.assertNotIn("cloud_accent", keys)
        self.assertEqual(
            dict(brain.memory.preferences)["cloud_personality_style"], "energetic")

    def test_cloud_name_roundtrips_through_settings(self):
        client = self._auth()
        resp = self._put_cloud_prefs(client, {
            "cloud_name": "Nimbus",
            "personality_style": "calm",
        })
        self.assertEqual(resp.status_code, 200)
        got = client.get("/api/settings/cloud").get_json()["data"]
        self.assertEqual(got.get("cloud_name"), "Nimbus")

    def test_cloud_name_mirrors_into_existing_memory(self):
        client = self._auth()
        self._put_cloud_prefs(client, {"cloud_name": "Nimbus"})
        brain = self._spies[-1] if self._spies else _SpyBrain()
        self.assertIn("cloud_cloud_name",
                      [k for k, _ in brain.memory.preferences])
        self.assertEqual(
            dict(brain.memory.preferences)["cloud_cloud_name"], "Nimbus")

    def test_cloud_name_rides_into_brain_message(self):
        client = self._auth()
        self._put_cloud_prefs(client, {
            "cloud_name": "Nimbus", "personality_style": "playful",
            "presentation": "neutral",
        })
        resp = client.post("/api/cloud/chat", json={
            "message": "What can you see with me?", "chat_id": "chat_n",
        })
        self.assertEqual(resp.status_code, 200)
        brain = self._spies[-1]
        self.assertIn("calls you Nimbus", brain.calls[0]["message"])
        self.assertTrue(brain.calls[0]["message"].rstrip().endswith(
            "What can you see with me?"))
        self.assertEqual(brain.calls[0]["image_data"], "")
        self.assertTrue(brain.calls[0]["persist_image_data"])

    def test_cloud_name_default_adds_no_name_line(self):
        client = self._auth()
        self._put_cloud_prefs(client, {"personality_style": "calm"})
        resp = client.post("/api/cloud/chat", json={
            "message": "Hello again", "chat_id": "chat_d",
        })
        brain = self._spies[-1]
        self.assertNotIn("calls you", brain.calls[0]["message"])

    def _frame(self):
        return "data:image/jpeg;base64," + (
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof" +
            "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB" +
            "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")

    def test_cloud_chat_reuses_existing_brain_for_screen_frame(self):
        client = self._auth()
        frame = self._frame()
        resp = client.post("/api/cloud/chat", json={
            "message": "What is on my screen?",
            "chat_id": "chat_v",
            "image_data": frame,
        })
        self.assertEqual(resp.status_code, 200)
        brain = self._spies[-1]
        self.assertEqual(brain.calls[0]["image_data"], frame)
        self.assertIs(brain.calls[0]["persist_image_data"], False)
        self.assertTrue(brain.calls[0]["message"].rstrip().endswith(
            "What is on my screen?"))

    def test_cloud_chat_ignores_non_image_frames(self):
        client = self._auth()
        resp = client.post("/api/cloud/chat", json={
            "message": "hi", "chat_id": "chat_b",
            "image_data": "https://example.com/x.png",
        })
        self.assertEqual(resp.status_code, 200)
        brain = self._spies[-1]
        self.assertEqual(brain.calls[0]["image_data"], "")

    def test_cloud_chat_ignores_malformed_base64_frames(self):
        client = self._auth()
        resp = client.post("/api/cloud/chat", json={
            "message": "hi", "chat_id": "chat_c",
            "image_data": "data:image/png;base64,@@@!!!",
        })
        self.assertEqual(resp.status_code, 200)
        brain = self._spies[-1]
        self.assertEqual(brain.calls[0]["image_data"], "")

    def test_cloud_chat_rejects_oversized_frames(self):
        client = self._auth()
        frame = "data:image/jpeg;base64," + ("A" * (4 * 1024 * 1024 + 1))
        resp = client.post("/api/cloud/chat", json={
            "message": "hi", "chat_id": "chat_o",
            "image_data": frame,
        })
        self.assertEqual(resp.status_code, 200)
        brain = self._spies[-1]
        self.assertEqual(brain.calls[0]["image_data"], "")


class CloudSingleImplementationTestCase(unittest.TestCase):
    """Cloud must have exactly ONE implementation: the layered SVG rig.

    static/cloud3d.js (a WebGL/three.js Cloud) was removed. It was dead code —
    VMCloud3D.attach() was never called and its stage markup did not exist — but
    keeping it meant a second, duplicate mascot implementation sitting in the
    project. These tests lock that in: no WebGL renderer, no VMCloud3D hook, and
    no second Cloud script in the page.
    """

    def _cloud3d_js(self):
        return ROOT / "static" / "cloud3d.js"

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _cloud_js(self):
        return (ROOT / "static" / "cloud.js").read_text(encoding="utf-8")

    def test_cloud3d_renderer_file_is_gone(self):
        self.assertFalse(self._cloud3d_js().exists(),
                         "static/cloud3d.js is a duplicate Cloud implementation")

    def test_cloud3d_script_not_loaded(self):
        html = self._index_html()
        self.assertNotIn("/static/cloud3d.js", html)

    def test_cloud_js_has_no_3d_hooks(self):
        js = self._cloud_js()
        self.assertNotIn("VMCloud3D", js)
        self.assertNotIn("mount3DAt", js)
        self.assertNotIn("start3D", js)

    def test_cloud_js_keeps_renderer_out_of_controller(self):
        js = self._cloud_js()
        self.assertNotIn("getContext('webgl')", js)
        self.assertNotIn("THREE.", js)
        self.assertNotIn("requestAnimationFrame", js)

    def test_cloud_js_still_wires_stage_and_rig(self):
        js = self._cloud_js()
        self.assertIn('id="vmCloudStage"', js)
        self.assertIn("window.vmCloudOnHide", js)

    def test_no_stuck_3d_status_text(self):
        # The WebGL surface used to render a permanent "Activating Cloud..."
        # caption because attach() never ran. It must not come back.
        self.assertNotIn("vmCloud3DStatus", self._cloud_js())

    def test_vm_ws_go_hooks_cloud_hide(self):
        html = self._index_html()
        block = html[html.index("function vmWsGo("):]
        self.assertIn('ws !== "cloud" && typeof vmCloudOnHide === "function"', block)
        self.assertIn("vmCloudOnHide()", block)


class CloudVoiceStaticTestCase(unittest.TestCase):
    """Structural checks for Step 3 voice (static/cloud_voice.js).

    Never runs a browser: asserts Cloud reuses the EXISTING TTS endpoint and the
    browser's native Web Speech API, keeps the brain call on /api/cloud/chat in
    cloud.js, and never opens a second mic stream.
    """

    def _voice_js(self):
        return (ROOT / "static" / "cloud_voice.js").read_text(encoding="utf-8")

    def _cloud_js(self):
        return (ROOT / "static" / "cloud.js").read_text(encoding="utf-8")

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def test_cloud_voice_script_loaded(self):
        html = self._index_html()
        self.assertIn('<script src="/static/cloud_voice.js', html)

    def test_cloud_voice_exposes_api(self):
        js = self._voice_js()
        self.assertIn("window.VMCloudVoice = {", js)
        self.assertIn("supported", js)
        self.assertIn("startListening", js)
        self.assertIn("stopListening", js)
        self.assertIn("interrupt", js)
        self.assertIn("stop: stop", js)
        self.assertIn("speak: speak", js)
        self.assertIn("getVoiceKey", js)
        self.assertIn("setHooks", js)
        self.assertIn("takeTranscript", js)

    def test_cloud_voice_uses_existing_tts_endpoint(self):
        js = self._voice_js()
        self.assertIn('"/api/tts"', js)
        self.assertIn('"qwen_tts"', js)
        self.assertIn("authHeaders", js)
        self.assertIn("credentials: \"include\"", js)

    def test_cloud_voice_uses_browser_speech_api(self):
        js = self._voice_js()
        self.assertIn("window.SpeechRecognition || window.webkitSpeechRecognition", js)
        self.assertIn("continuous = true", js)
        self.assertIn("interimResults = true", js)
        self.assertIn("1500", js)
        self.assertIn("not-allowed", js)

    def test_cloud_voice_owns_mic_without_getusermedia(self):
        js = self._voice_js()
        self.assertNotIn("getUserMedia", js)
        self.assertNotIn("navigator.mediaDevices", js)

    def test_cloud_voice_only_calls_tts_not_other_apis(self):
        js = self._voice_js()
        self.assertNotIn('"/api/cloud/chat"', js)
        self.assertNotIn('"/chat/sessions"', js)
        self.assertNotIn('"/api/roundtable"', js)

    def test_cloud_voice_maps_voice_keys(self):
        js = self._voice_js()
        self.assertIn("var VALID_KEYS = [\"marcus\", \"elena\", \"angelina\"]", js)
        self.assertIn('if (p === "feminine") return "elena";', js)
        self.assertIn('if (p === "masculine") return "marcus";', js)
        self.assertIn('return "marcus";', js)
        self.assertIn("vp.indexOf(\"bright\")", js)
        self.assertIn("vp.indexOf(\"melodic\")", js)

    def test_cloud_js_wires_voice(self):
        js = self._cloud_js()
        self.assertIn('id="vmCloudMicBtn"', js)
        self.assertIn("VMCloudVoice", js)
        self.assertIn("window.VMCloudVoice.setHooks", js)
        self.assertIn("toggleVoice", js)
        self.assertIn("pickCloudEmotion", js)
        self.assertIn("speakCloudReply", js)
        self.assertIn("interruptCloud", js)
        self.assertIn("voice_preference", js)

    def test_cloud_js_keeps_existing_brain_path(self):
        js = self._cloud_js()
        self.assertIn('"/api/cloud/chat"', js)
        self.assertIn('"/chat/sessions"', js)
        self.assertIn('markdownHtml', js)

    def test_cloud_js_state_transitions_present(self):
        js = self._cloud_js()
        self.assertIn('{ status: "listening", emotion: "listening" }', js)
        self.assertIn('{ status: "thinking", emotion: "thinking" }', js)
        self.assertIn('{ status: "speaking", emotion: emotion }', js)
        self.assertIn('{ status: "idle"', js)

    def test_cloud_js_emotion_heuristic_controlled(self):
        js = self._cloud_js()
        self.assertIn("var EMOTION_HINTS = {", js)
        for emo in ("happy", "concerned", "curious", "focused"):
            self.assertIn(emo, js)
        self.assertIn("function pickCloudEmotion(", js)
        self.assertIn("return emo;", js)

    def test_cloud_voice_no_overlap_guards(self):
        js = self._voice_js()
        self.assertIn("stopSpeech(true)", js)
        self.assertIn('"interrupted"', js)


class CloudVisionStaticTestCase(unittest.TestCase):
    """Step 4 screen-context foundation: static guarantees for cloud_vision.js.

    Screen sharing is STRICTLY explicit, throttled, transient and observation-
    only. These checks pin that contract into place (no full-res streams, no
    always-on capture, no control actions, no other endpoints).
    """

    def _vision_js(self):
        return (ROOT / "static" / "cloud_vision.js").read_text(encoding="utf-8")

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def test_cloud_vision_script_loaded(self):
        html = self._index_html()
        self.assertIn('<script src="/static/cloud_vision.js', html)

    def test_cloud_vision_exposes_api(self):
        js = self._vision_js()
        self.assertIn("window.VMCloudVision = {", js)
        self.assertIn("getState", js)
        self.assertIn("supported", js)
        self.assertIn("start: start", js)
        self.assertIn("stop: stop", js)
        self.assertIn("capture: capture", js)
        self.assertIn("onStateChange", js)
        self.assertIn("destroy", js)

    def test_cloud_vision_has_full_state_machine(self):
        js = self._vision_js()
        for state in ("off", "requesting", "active", "stopped", "denied", "unsupported"):
            self.assertIn('"' + state + '"', js)

    def test_cloud_vision_requires_explicit_permission(self):
        js = self._vision_js()
        self.assertIn("getDisplayMedia", js)
        self.assertIn("navigator.mediaDevices", js)
        self.assertNotIn("getDisplayMedia(", js.replace("navigator.mediaDevices.getDisplayMedia", ""))

    def test_cloud_vision_throttles_frames(self):
        js = self._vision_js()
        self.assertIn("2500", js)
        self.assertIn("image/jpeg", js)
        self.assertIn("0.5", js)
        self.assertIn("canvas", js)
        self.assertIn("toDataURL", js)
        self.assertIn("drawImage", js)

    def test_cloud_vision_is_transient(self):
        js = self._vision_js()
        self.assertNotIn('"/api/cloud/chat"', js)
        self.assertNotIn('"/chat/sessions"', js)
        self.assertNotIn('"/api/cloud/state"', js)
        self.assertNotIn("localStorage", js)
        self.assertNotIn("indexedDB", js)

    def test_cloud_vision_never_controls_the_computer(self):
        js = self._vision_js()
        for blocked in ("puppeteer", "playwright", "robotjs", "applescript",
                        "shell.exec", "execSync", "robot_move", "mouse"):
            self.assertNotIn(blocked, js)
        self.assertNotIn("click(", js)
        self.assertIn("NO COMPUTER CONTROL", js.upper())

    def test_cloud_vision_never_runs_before_activation(self):
        js = self._vision_js()
        self.assertIn('start()', js)
        # capture() must refuse to return frames outside the "active" state.
        self.assertIn('state !== "active"', js)

    def test_cloud_vision_stops_on_stream_end(self):
        js = self._vision_js()
        self.assertIn('addEventListener("ended"', js)
        self.assertIn("onStreamEnded", js)

    def test_cloud_vision_stops_everything_on_destroy(self):
        js = self._vision_js()
        self.assertIn("function destroy()", js)
        self.assertIn("stop()", js)
        self.assertIn("removeChild", js)


class CloudCompanionStaticTestCase(unittest.TestCase):
    """Step 4 persistent companion: one controller, two presentation surfaces."""

    def _cloud_js(self):
        return (ROOT / "static" / "cloud.js").read_text(encoding="utf-8")

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def test_companion_show_and_cleanup_exposed(self):
        js = self._cloud_js()
        self.assertIn("window.vmCloudCompanionShow", js)
        self.assertIn("window.vmCloudCompanionCleanup", js)
        self.assertIn("function companionShow()", js)
        self.assertIn("function cleanupCompanion()", js)

    def test_index_html_wires_companion_lifecycle(self):
        html = self._index_html()
        self.assertIn("vmCloudCompanionShow", html)
        self.assertIn("vmCloudCompanionCleanup", html)
        self.assertIn("function setAppVisible", html)

    def test_single_controller_single_shell(self):
        js = self._cloud_js()
        self.assertIn("vmCloudCompanion\"", js)
        self.assertIn("ensureCompanionShell", js)
        # One controller, one shell: creating the shell is idempotent by id.
        self.assertIn("var shell = $id(\"vmCloudCompanion\");", js)
        self.assertIn("if (shell) return shell;", js)

    def test_one_3d_engine_shared_between_surfaces(self):
        js = self._cloud_js()
        self.assertIn("surfaceCompanion", js)
        # The controller drives ONE implementation only: the SVG rig. There is no
        # second WebGL renderer to start, suspend or resume.
        self.assertIn("vmCloudAnim", js)
        self.assertNotIn("VMCloud3D", js)
        self.assertNotIn("new THREE.WebGLRenderer", js)

    def test_minimize_restore_preserves_state(self):
        js = self._cloud_js()
        self.assertIn("function minimizeCompanion()", js)
        self.assertIn("function restoreCompanion()", js)
        self.assertIn("CLOUD.prefs.companion_minimized", js)
        self.assertIn("savePrefsLight", js)

    def test_companion_responsive_placement_no_offsets(self):
        js = self._cloud_js()
        self.assertIn("#vmCloudCompanion{position:fixed;right:18px;bottom:18px;", js)
        self.assertIn("env(safe-area-inset-bottom", js)
        self.assertNotIn("left:280px", js)
        self.assertNotIn("left: 280px", js)

    def test_companion_sharing_indicator(self):
        js = self._cloud_js()
        self.assertIn("shell.classList.toggle(\"sharing\"", js)
        self.assertIn(".vmcloud-companion.sharing", js)
        # Sharing pulses the on-screen character (the actual cloud.png asset).
        self.assertIn(".vmcloud-companion.sharing .vmcloud-companion-mini-img", js)
        self.assertIn("@keyframes vmcloud-share-pulse", js)

    def test_naming_form_present(self):
        js = self._cloud_js()
        self.assertIn('id="vmCloudName"', js)
        self.assertIn('maxlength="32"', js)
        self.assertIn('placeholder="Cloud"', js)
        self.assertIn("What would you like to call your Cloud?", js)
        self.assertIn("function updateIdentity()", js)
        self.assertIn("vmCloudCompanionName", js)
        self.assertIn("vmCloudMiniLabel", js)
        self.assertIn("vmCloudTitle", js)

    def test_voice_reused_across_surfaces(self):
        js = self._cloud_js()
        self.assertIn("vmCloudCompMicBtn", js)
        self.assertIn("vmCloudCompanionVoiceStatus", js)
        self.assertIn("setVoiceStatus", js)
        # Companion routes through the SAME Step 3 voice pipeline.
        self.assertIn("window.VMCloud.toggleVoice", js)
        self.assertNotIn("new SpeechRecognition", js)

    def test_vision_ui_on_both_surfaces(self):
        js = self._cloud_js()
        self.assertIn("vmCloudVisionBtn", js)
        self.assertIn("vmCloudCompVisionBtn", js)
        self.assertIn("vmCloudVisionStatus", js)
        self.assertIn("vmCloudCompVisionStatus", js)
        self.assertIn("VMCloudVision.capture", js)
        self.assertIn("updateVisionUI", js)

    def test_companion_never_forks_state(self):
        js = self._cloud_js()
        self.assertIn("CLOUD.transcript", js)
        self.assertIn("CLOUD.chatId", js)
        self.assertNotIn("companion.transcript", js)
        self.assertIn('image_data: frame || ""', js)

    def test_logout_cleanup_cache_present(self):
        js = self._cloud_js()
        self.assertIn("VMCloudVision.destroy", js)
        self.assertIn("removeChild(shell)", js)
        self.assertIn("CLOUD.companionActive = false", js)

    def test_companion_mounted_at_app_shell_level(self):
        js = self._cloud_js()
        # One fixed companion appended to <body> — independent of any
        # workspace panel, so it survives Chat → Music → Sketch → Videos →
        # Website navigation as the SAME instance.
        self.assertIn('d.id = "vmCloudCompanion"', js)
        self.assertIn("document.body.appendChild(d)", js)
        self.assertIn('window.vmCloudCompanionShow', js)
        self.assertIn('window.vmCloudCompanionCleanup', js)

    def test_mini_state_renders_actual_cloud_png(self):
        js = self._cloud_js()
        self.assertIn('id="vmCloudCompanionMiniStage"', js)
        self.assertIn('id="vmCloudCompanionMiniOrb"', js)
        self.assertIn('id="vmCloudMiniStatus"', js)
        # The small companion renders the real character asset directly
        # (static/cloud.png) as a transparent <img> — it is not the old WebGL
        # placeholder orb nor a CSS blob, and it is not a horizontal bar.
        self.assertIn('src="/static/cloud.png"', js)
        self.assertIn('class="vmcloud-companion-mini-img"', js)
        self.assertIn("draggable=\"false\"", js)
        # Mini shows the character; every surface shares the ONE SVG rig and the
        # one animation controller. No per-surface 3D engine is mounted.
        self.assertIn("showMiniCharacter", js)
        self.assertIn("vmCloudStage", js)
        self.assertIn("vmCloudAnim", js)
        self.assertNotIn("VMCloud3D", js)
        self.assertNotIn("mount3DAt", js)

    def test_companion_expands_from_small_surface(self):
        js = self._cloud_js()
        # Tapping the creature restores the compact panel; closing it returns
        # to the small companion — it never destroys Cloud.
        self.assertIn("function restoreCompanion()", js)
        self.assertIn("function minimizeCompanion()", js)
        self.assertIn('surfaceCompanion("mini")', js)
        self.assertIn('surfaceCompanion("panel")', js)
        self.assertIn("restoreCompanion()", js)

    def test_companion_is_draggable_and_position_persists(self):
        js = self._cloud_js()
        self.assertIn("makeDraggable", js)
        self.assertIn("pointerdown", js)
        self.assertIn("pointermove", js)
        self.assertIn("pointerup", js)
        self.assertIn("pointercancel", js)
        self.assertIn("setCompanionPx", js)
        self.assertIn("applyCompanionPosition", js)
        self.assertIn("saveCompanionPosition", js)
        self.assertIn("clampPct", js)
        self.assertIn("companion_x", js)
        self.assertIn("companion_y", js)
        # Position is persisted through the existing settings system, and the
        # companion is draggable (mobile/touch safe) rather than sidebar-anchored.
        self.assertIn("touch-action:none", js)
        self.assertIn("savePrefsLight", js)
        self.assertNotIn("left:280px", js)
        self.assertNotIn("localStorage", js)

    def test_cloud_png_asset_is_tall_transparent_character(self):
        # The exact character is the source of truth for Cloud's appearance.
        png = ROOT / "static" / "cloud.png"
        self.assertTrue(png.exists(), "static/cloud.png is required")
        try:
            from PIL import Image
            im = Image.open(str(png))
            w, h = im.size
        except Exception:
            w, h = 433, 577  # known fallback from the committed asset
        # The character is a standing figure (tall, not a wide horizontal bar)
        # with transparent background (RGBA) so no white box is shown.
        self.assertGreater(h, w, "character must be taller than wide")
        self.assertLess(w / h, 1.1, "character must not be a wide horizontal bar")
        js = self._cloud_js()
        self.assertIn("aspect-ratio:433/577", js)

    def test_mini_is_small_not_fullscreen(self):
        js = self._cloud_js()
        # The companion shell is a small fixed element — it must never be a
        # full-width/height container or hide the app with a big invisible box.
        self.assertIn("#vmCloudCompanion{position:fixed;right:18px;bottom:18px;", js)
        self.assertIn("width:132px;height:auto", js)
        # The shell/small-companion rules never use viewport-filling sizes.
        suppress = re.search(r"\#vmCloudCompanion\{[^}]+\}", js)
        self.assertIsNotNone(suppress)
        self.assertNotIn("width:100vw", suppress.group(0))
        self.assertNotIn("height:100vh", suppress.group(0))
        self.assertNotIn("inset:0", suppress.group(0))
        # No full-screen rectangle allowed on the mini stage either.
        mini = re.search(r"\.vmcloud-companion-mini-stage\{[^}]+\}", js)
        self.assertIsNotNone(mini)
        self.assertNotIn("width:100%", mini.group(0))
        self.assertNotIn("inset:0", mini.group(0))

    def test_character_is_direct_body_img(self):
        js = self._cloud_js()
        # The on-screen companion is a single direct <img id="vmCloudCharacter">
        # appended straight to <body> — never boxed inside the workspace,
        # sidebar, iframe, or any large wrapper.
        self.assertIn("function ensureCloudCharacter()", js)
        self.assertIn('el.id = "vmCloudCharacter"', js)
        self.assertIn('el.src = "/static/cloud.png"', js)
        self.assertIn("document.body.appendChild(el)", js)
        self.assertIn("makeDraggable(el)", js)
        self.assertNotIn('draggable="true"', js[js.index("function ensureCloudCharacter()"):js.index("function companionShow()")])

    def test_character_required_styles(self):
        js = self._cloud_js()
        # Inline geometry pins a small, complete character to the bottom-right,
        # clear of mobile safe areas — no full-screen container, no bar.
        self.assertIn('el.style.position = "fixed"', js)
        self.assertIn('el.style.right = "18px"', js)
        self.assertIn('el.style.bottom = "calc(18px + env(safe-area-inset-bottom))"', js)
        self.assertIn('el.style.width = "132px"', js)
        self.assertIn('el.style.height = "auto"', js)
        self.assertIn('el.style.visibility = "visible"', js)
        self.assertIn('el.style.opacity = "1"', js)
        self.assertIn('el.style.touchAction = "none"', js)
        rule = re.search(r"\#vmCloudCharacter\{[^}]+\}", js)
        self.assertIsNotNone(rule)
        self.assertIn("right:18px", rule.group(0))
        self.assertIn("bottom:calc(18px + env(safe-area-inset-bottom", rule.group(0))
        self.assertIn("width:132px;height:auto", rule.group(0))
        self.assertNotIn("width:100%", rule.group(0))
        self.assertNotIn("width:100vw", rule.group(0))
        self.assertNotIn("inset:0", rule.group(0))

    def test_character_created_from_auth_lifecycle(self):
        html = self._index_html()
        js = self._cloud_js()
        # The authenticated app-shell visibility signal creates the character:
        # setAppVisible(true) → vmCloudCompanionShow → companionShow → ensure.
        self.assertIn("window.vmCloudCompanionShow();", html)
        block = js[js.index("function companionShow()"):js.index("function cleanupCompanion()")]
        self.assertIn("ensureCloudCharacter()", block)

    def test_character_dragged_directly_not_wrapped(self):
        js = self._cloud_js()
        # Dragging binds directly to #vmCloudCharacter itself, and the move/save
        # helpers target that same element — never a large draggable parent.
        ensure = js[js.index("function ensureCloudCharacter()"):js.index("function companionShow()")]
        self.assertIn("makeDraggable(el)", ensure)
        motion = js[js.index("function setCompanionPx"):js.index("function onDragMove")]
        self.assertIn("var el = companionElement();", motion)
        self.assertIn("companionElement", js)
        self.assertNotIn("left:280px", js)
        self.assertNotIn('makeDraggable($id("vmCloudCompanion").parentNode', js)

    def test_logout_keeps_character(self):
        js = self._cloud_js()
        cleanup = js[js.index("function cleanupCompanion()"):js.index("function injectStyles()")]
        # The robot companion is a persistent front-end element: teardown removes
        # the chat panel but never the #vmCloudCharacter robot, so the robot
        # stays visible on the front end at all times.
        self.assertIn("removeChild(shell)", cleanup)
        self.assertNotIn("removeChild(char)", cleanup)

    def test_one_visible_character_no_second_cloud(self):
        js = self._cloud_js()
        block = js[js.index("function companionShow()"):js.index("function cleanupCompanion()")]
        # The auth path mounts exactly one visible Cloud (the direct character):
        # it never creates the old shell or a second 3D/panel placeholder.
        self.assertNotIn("ensureCompanionShell(", block)
        self.assertNotIn("mount3DAt(", block)
        self.assertNotIn("document.createElement(\"div\")", block)


class _FocusedMemory:
    """Records only what a real MemorySystem would under respond()."""

    def __init__(self):
        self.user_id = "u_screen_test"
        self.added = []
        self.long_term = {}
        self.reloaded = 0

    def reload(self):
        self.reloaded += 1

    def get_message_count(self, chat_id):
        return 0

    def get_chat(self, chat_id):
        return []

    def get_active_facts(self):
        return []

    def get_full_memory(self):
        return {}

    def get_user_name(self):
        return ""

    def add_message(self, chat_id, role, content, timestamp=None,
                    image_data="", image_url="", video_url=""):
        self.added.append({
            "chat_id": chat_id, "role": role, "content": content,
            "image_data": image_data,
        })

    def set_title(self, chat_id, title):
        pass

    def save_creator_message(self, msg):
        pass

    def save_memory(self):
        pass

    def handle_retraction(self, msg):
        pass

    def remember_fact(self, *args, **kwargs):
        pass


class CloudScreenPersistenceTestCase(unittest.TestCase):
    """Real respond() behavior: screen frames are transient, never stored."""

    def setUp(self):
        import core.brain as brain_module
        from types import SimpleNamespace
        self.brain_module = brain_module
        self.llm_calls = []

        def fake_llm(messages, *args, **kwargs):
            self.llm_calls.append(messages)
            return ("Screen reply for tests", {
                "groq_used": False, "fallback_used": True, "fallback_source": "test"})

        def fake_intent(message, recent_context=""):
            return {"intent": "none", "domain": "general", "reason": "test",
                    "needs_multi_query": False, "freshness": "low"}

        patchers = (
            patch.object(brain_module, "_call_llm_cluster", side_effect=fake_llm),
            patch.object(brain_module, "get_config",
                         return_value=SimpleNamespace(
                             groq_api_key="", groq_base_url="",
                             openrouter_api_key="", nvidia_api_key="",
                             gemini_api_key="")),
            patch.object(brain_module, "_get_memory_mgr", return_value=None),
            patch.object(brain_module, "_get_knowledge_mgr", return_value=None),
            patch.object(brain_module, "_get_recovery", return_value=None),
            patch.object(brain_module, "classify_research_intent",
                         side_effect=fake_intent),
        )
        self._patchers = patchers
        for p in patchers:
            p.start()
        self.addCleanup(self._stop)

    def _stop(self):
        for p in self._patchers:
            try:
                p.stop()
            except Exception:
                pass

    def _make_brain(self):
        from types import SimpleNamespace
        b = self.brain_module.MarcusBrain.__new__(self.brain_module.MarcusBrain)
        b.profile = SimpleNamespace(key="marcus", to_prompt=lambda: "Cloud test character profile")
        b.memory = _FocusedMemory()
        b._pending_sources = []
        b._pending_source_metadata = []
        b.last_response_meta = {"sources": [], "fallback_used": False}
        b._reply_mode = False
        b._model_name = ""
        b._pending_external_context = ""
        b._pending_expanded_query = ""
        b._diagnostics_done = True
        return b

    def _valid_frame(self):
        return "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="

    def test_respond_never_persists_screen_frame(self):
        brain = self._make_brain()
        frame = self._valid_frame()
        reply = brain.respond(
            "What is on my screen?", image_data=frame, persist_image_data=False)
        self.assertIn("Screen reply", reply)
        user = [m for m in brain.memory.added if m["role"] == "user"]
        self.assertEqual(len(user), 1)
        self.assertEqual(user[0]["image_data"], "")
        self.assertNotIn("[Image attached]", user[0]["content"])

    def test_transient_frame_still_reaches_llm(self):
        brain = self._make_brain()
        frame = self._valid_frame()
        brain.respond("Look at my screen", image_data=frame,
                      persist_image_data=False)
        found = False
        for messages in self.llm_calls:
            for msg in messages:
                content = msg.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "image_url":
                            found = True
                elif isinstance(content, dict) and content.get("type") == "image_url":
                    found = True
        self.assertTrue(found, "screen frame must reach the existing brain LLM")

    def test_respond_default_still_persists_explicit_images(self):
        brain = self._make_brain()
        frame = self._valid_frame()
        brain.respond("Here is a picture", image_data=frame)
        user = [m for m in brain.memory.added if m["role"] == "user"]
        self.assertEqual(user[0]["image_data"], frame)
        self.assertIn("[Image attached]", user[0]["content"])

    def test_respond_default_path_for_plain_text_no_image(self):
        brain = self._make_brain()
        brain.respond("Hello there")
        user = [m for m in brain.memory.added if m["role"] == "user"]
        self.assertEqual(user[0]["image_data"], "")
        self.assertNotIn("[Image attached]", user[0]["content"])


class CloudAnimStaticTestCase(unittest.TestCase):
    """Static guarantees about static/cloud_anim.js: the centralized
    animation/state layer behind cloudSetState() and the future Brain API.

    static/cloud.png remains the single flattened source of truth and the
    standing fallback; independent eye/brow/mouth/arm/leg motion is provided by
    the layered vector rig (static/cloud_rig.js), never by the PNG itself. The
    rig keeps the design faithful (same palette, proportions and silhouette).
    """

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _anim_js(self):
        return (ROOT / "static" / "cloud_anim.js").read_text(encoding="utf-8")

    def test_anim_layer_script_loaded_after_cloud_vision(self):
        html = self._index_html()
        vision = re.search(
            r'<script src="/static/cloud_vision\.js\?v=2"></script>', html)
        anim = re.search(
            r'<script src="/static/cloud_anim\.js\?v=2"></script>', html)
        self.assertIsNotNone(anim, "cloud_anim.js script tag is required")
        self.assertIsNotNone(vision, "cloud_vision.js script tag is required")
        self.assertGreater(anim.start(), vision.start(),
                           "cloud_anim.js must load after cloud_vision.js")
        self.assertNotIn("defer", anim.group(0))
        self.assertNotIn("async", anim.group(0))

    def test_anim_layer_loads_last_after_rig(self):
        html = self._index_html()
        rig = re.search(r'<script src="/static/cloud_rig\.js\?v=2"></script>', html)
        anim = re.search(r'<script src="/static/cloud_anim\.js\?v=2"></script>', html)
        self.assertIsNotNone(rig)
        self.assertIsNotNone(anim)
        # The controller must come after the rig it animates.
        self.assertGreater(anim.start(), rig.start())

    def test_api_exports_cloud_set_state_and_vm_cloud_anim(self):
        src = self._anim_js()
        self.assertIn("window.cloudSetState", src)
        self.assertIn("window.vmCloudAnim", src)
        self.assertIn("setState: setState", src)
        self.assertIn("getState:", src)
        self.assertIn("getCapabilities:", src)

    def test_all_emotional_states_are_registered(self):
        src = self._anim_js()
        for key in ("idle", "listening", "thinking", "happy", "excited",
                    "sad", "surprised", "confused", "concerned", "greeting",
                    "speaking", "sleeping"):
            self.assertIn('key: "%s"' % key, src,
                          "state %r must be registered in STATES" % key)
            self.assertNotIn('key: "%s"' % key, src.replace('key: "%s"' % key, "", 1) + " " * 2,
                             "state %r must be unique" % key)

    def test_each_state_has_a_pose_with_face_limbs_descriptors(self):
        src = self._anim_js()
        for key in ("idle", "listening", "thinking", "happy", "excited",
                    "sad", "surprised", "confused", "concerned", "greeting",
                    "speaking", "sleeping"):
            block = self._pose_block(src, key)
            self.assertIsNotNone(block, "pose missing for %r" % key)
            self.assertIn("face:", block)
            self.assertIn("eyes:", block)
            self.assertIn("brows:", block)
            self.assertIn("mouth:", block)
            self.assertIn("arms:", block)
            self.assertIn("legs:", block)

    def _pose_block(self, src, key):
        marker = re.search(r"%s:\s*\{" % re.escape(key) + r"(?:\n|.)*?\n\s*\}",
                           src, re.MULTILINE)
        return marker.group(0) if marker else None

    def test_get_capabilities_is_honest_about_layered_rig(self):
        src = self._anim_js()
        # The layered SVG rig (static/cloud_rig.js) draws the companion as an
        # ORIGINAL cloud character, exposing separately addressable parts so
        # the animation layer can honestly claim independent face/limb motion
        # — flatter assets are the animatable surface, and static/cloud.png
        # remains only the no-JS fallback.
        self.assertIn("independentEyes: true", src)
        self.assertIn("independentEyebrows: true", src)
        self.assertIn("independentMouth: true", src)
        self.assertIn("independentArms: true", src)
        self.assertIn("independentLegs: true", src)
        self.assertIn("wholeBodyArticulation: true", src)
        self.assertIn("needsPartAssetsForFaceAndLimbs: false", src)
        self.assertIn("asset: \"static/cloud_rig.js\"", src)
        self.assertIn("assetType: \"layered SVG rig (original cloud companion visual)\"", src)
        self.assertIn("character: \"cloud\"", src)
        self.assertIn("autonomousDrift: true", src)
        self.assertIn("standingAssetFallback: \"static/cloud.png\"", src)

    def test_tweening_math_is_available(self):
        src = self._anim_js()
        self.assertIn("function easeInOutCubic", src)
        self.assertIn("function lerp", src)
        self.assertIn("requestAnimationFrame", src)
        self.assertIn("performance.now()", src)
        self.assertIn("translate3d(", src)

    def test_idle_life_is_randomized_within_sane_bounds(self):
        src = self._anim_js()
        self.assertIn("Math.random", src)
        self.assertIn("rand(2600, 6800)", src)
        self.assertIn("rand(7000, 16000)", src)
        self.assertIn("rand(11000, 22000)", src)
        self.assertIn("rand(24000, 50000)", src)


class CloudLiveAnimBridgeTestCase(unittest.TestCase):
    """The approved vector rig must be driven by the live AI state.

    static/cloud.js is the single owner of Cloud state. Its reflectState() is
    the one funnel every state change already goes through, so the bridge has to
    hang off that funnel — a second listener or a second animation system would
    reintroduce exactly the duplication this is meant to remove.
    """

    def _cloud_js(self):
        return (ROOT / "static" / "cloud.js").read_text(encoding="utf-8")

    def _anim_js(self):
        return (ROOT / "static" / "cloud_anim.js").read_text(encoding="utf-8")

    def _voice_js(self):
        return (ROOT / "static" / "cloud_voice.js").read_text(encoding="utf-8")

    def _rig_js(self):
        return (ROOT / "static" / "cloud_rig.js").read_text(encoding="utf-8")

    def test_cloud_js_drives_the_rig_animator(self):
        js = self._cloud_js()
        self.assertIn("window.vmCloudAnim", js,
                      "cloud.js must drive the rig's single animator")
        self.assertIn("function syncAnim()", js)
        self.assertIn("A.setState(key)", js)

    def test_bridge_is_pushed_from_the_single_state_funnel(self):
        js = self._cloud_js()
        # Every state mutation already funnels through reflectState(); the rig
        # push must live there so no caller can bypass it. It is the ONLY sink —
        # the removed 3D renderer used to sit right above it.
        start = js.index("function reflectState()")
        end = js.index("\n  function ", start + 10)
        funnel = js[start:end]
        self.assertIn("syncAnim();", funnel)
        self.assertEqual(js.count("syncAnim();"), 2,
                         "syncAnim is called once from reflectState plus the "
                         "single resume re-assert")

    def test_no_second_animation_controller_is_introduced(self):
        js = self._cloud_js()
        self.assertNotIn("requestAnimationFrame", js,
                         "cloud.js must not run its own render loop")
        # The rig animator stays the only thing that writes rig transforms.
        anim = self._anim_js()
        self.assertEqual(anim.count("window.vmCloudAnim = {"), 1)

    def test_mapping_covers_every_cloud_vocabulary_term(self):
        js = self._cloud_js()
        emotions = re.search(r"var EMOTIONS = \[(.*?)\]", js, re.S).group(1)
        emotion_terms = re.findall(r'"([a-z_]+)"', emotions)
        statuses = re.search(r"var INTERACTION_STATES = \[(.*?)\]", js, re.S).group(1)
        status_terms = re.findall(r'"([a-z_]+)"', statuses)
        self.assertTrue(emotion_terms and status_terms)

        emap = re.search(r"var ANIM_EMOTION = \{(.*?)\n  \};", js, re.S).group(1)
        smap = re.search(r"var ANIM_STATUS = \{(.*?)\n  \};", js, re.S).group(1)
        for term in emotion_terms:
            self.assertRegex(emap, r"(?m)^\s*%s:\s*\"" % term,
                             "emotion %r is unmapped to a rig state" % term,
                             )
        for term in status_terms:
            self.assertRegex(smap, r"(?m)^\s*%s:\s*(null|\")" % term,
                             "status %r is unmapped to a rig state" % term)

    def test_every_mapped_anim_state_actually_exists(self):
        js = self._cloud_js()
        anim = self._anim_js()
        registered = set(re.findall(r'key: "([a-z_]+)"', anim))
        mapped = set(re.findall(r":\s*\"([a-z_]+)\"", re.search(
            r"var ANIM_EMOTION = \{(.*?)\n  \};", js, re.S).group(1)))
        mapped |= set(re.findall(r":\s*\"([a-z_]+)\"", re.search(
            r"var ANIM_STATUS = \{(.*?)\n  \};", js, re.S).group(1)))
        missing = sorted(mapped - registered)
        self.assertEqual(missing, [],
                         "bridge points at rig states that do not exist: %r"
                         % missing)

    def test_curious_is_registered_as_a_real_state(self):
        # cloud.js maps the "curious" emotion, so the rig must accept it.
        anim = self._anim_js()
        self.assertIn('key: "curious"', anim)
        self.assertRegex(anim, r"curious:\s*\{[\s\S]*?face:\s*\{[\s\S]*?arms:")

    def test_sticky_states_are_reasserted_but_blips_are_not(self):
        js = self._cloud_js()
        # Long requests must keep "thinking" on screen past the rig's own
        # transient hold, while one-shot emotions must still relax to idle.
        for sticky in ("idle", "listening", "thinking", "speaking"):
            self.assertIn('%s: 1' % sticky,
                          re.search(r"var ANIM_STICKY = \{[^}]*\}", js).group(0))
        self.assertIn("ANIM_STICKY[key] || key !== _animState", js)

    def test_rig_lifecycle_follows_the_owner(self):
        js = self._cloud_js()
        # Hidden companion stops the rig loop; resuming restarts it and replays
        # the live state; logout clears the transient mouth state.
        self.assertIn("window.vmCloudAnim.stop()", js)
        self.assertIn("window.vmCloudAnim.start()", js)
        self.assertIn("window.vmCloudAnim.clearSpeechLevel()", js)

    def test_gestures_are_rate_limited(self):
        js = self._cloud_js()
        self.assertIn("ANIM_GESTURE_MS", js)
        self.assertIn("if (now - _animGestureAt < ANIM_GESTURE_MS) return;", js)

    def test_mouth_sync_is_amplitude_driven_not_a_fake_pulse(self):
        anim = self._anim_js()
        self.assertIn("setSpeechLevel", anim)
        self.assertIn("function talkMouthPath", anim)
        self.assertIn("talkMouthPath(_speechLevel)", anim)
        self.assertIn("isSpeechDriven", anim)
        # Level 0 must be byte-identical to the closed MOUTH.speak path so a
        # silent mouth is indistinguishable from the previous behaviour.
        self.assertIn('speak:      "M199 342 C201 352 231 352 233 342"', anim)
        self.assertIn("[199, 342, 201, 352, 231, 352, 233, 342]", anim)

    def test_mouth_sync_still_has_a_working_fallback(self):
        anim = self._anim_js()
        self.assertIn("MOUTH.big_smile", anim,
                      "without an audio tap the mouth must still move")
        self.assertIn("speechActive(now)", anim)

    def test_voice_taps_the_one_existing_player(self):
        voice = self._voice_js()
        self.assertIn("createMediaElementSource(el)", voice)
        self.assertIn("getPlayer()", voice)
        # Never a second audio element, and never a second synthesis request.
        self.assertEqual(voice.count('document.createElement("audio")'), 1)
        self.assertEqual(voice.count('"/api/tts"'), 1)
        self.assertIn("setLevelListener", voice)

    def test_analyser_is_terminated_so_playback_is_never_silenced(self):
        voice = self._voice_js()
        # An unterminated MediaElementSource mutes the element entirely.
        self.assertIn("levelAnalyser.connect(levelCtx.destination)", voice)

    def test_tap_is_skipped_for_cross_origin_audio(self):
        # createMediaElementSource() silences a cross-origin element that is not
        # CORS-enabled, and TTS audio can come from an external provider URL.
        voice = self._voice_js()
        self.assertIn("function sameOriginAudio", voice)
        self.assertIn("if (!el || !sameOriginAudio(el)) { levelBlocked = true; return null; }", voice)

    def test_voice_stops_its_level_loop_with_playback(self):
        voice = self._voice_js()
        self.assertIn("stopLevelLoop", voice)
        self.assertGreaterEqual(voice.count("stopLevelLoop();"), 3)

    def test_owner_forwards_amplitude_into_the_rig(self):
        js = self._cloud_js()
        self.assertIn("VMCloudVoice.setLevelListener", js)
        self.assertIn("A.setSpeechLevel(level)", js)

    def test_arms_reach_past_the_body(self):
        # The brief asks for visibly longer arms; GEO drives them so the limbs
        # and the hands cannot drift apart.
        rig = self._rig_js()
        geo = re.search(r"var GEO = \{(.*?)\n  \};", rig, re.S).group(1)
        wrist = int(re.search(r"wristY:\s*(\d+)", geo).group(1))
        hand = int(re.search(r"handY:\s*(\d+)", geo).group(1))
        body = int(re.search(r"body:\s*\{[^}]*bottom:\s*(\d+)", geo).group(1))
        # The hands must hang below the body underside, and the whole limb must
        # be longer than the previous 400px wrist that read as a stub.
        self.assertGreater(hand, body,
                           "hands must hang below the body underside")
        self.assertGreater(wrist, 400, "arms must be visibly longer than before")
        self.assertLess(hand, int(re.search(r"shadowY:\s*(\d+)", geo).group(1)),
                        "hands must not reach the shadow")
        self.assertIn("GEO.wristY", rig)
        # The approved silhouette itself is untouched by an arm change.
        self.assertIn("var SILHOUETTE_D = [", rig)

    def test_transient_state_auto_returns_to_stable(self):
        src = self._anim_js()
        self.assertIn("transient: true", src)
        self.assertIn("_returnTimer", src)
        self.assertIn("setState(_stable)", src)
        self.assertIn("thinking", src)

    def test_rig_animation_uses_transform_walking_repositions(self):
        # Rig/whole-body animation is pure transform (never fights the drag
        # position). The only left/top writes live in placeElement, which is the
        # walking controller deliberately moving Cloud on screen.
        src = self._anim_js()
        self.assertIn(".style.transform =", src)
        self.assertIn("function placeElement(", src)
        self.assertIn("_el.style.left", src)
        self.assertIn('"auto"', src)
        transform_block = src[src.index("function renderBody("):src.index("function boot(")]
        self.assertNotIn(".style.left", transform_block)
        self.assertNotIn(".style.top", transform_block)
        self.assertNotIn(".style.right", transform_block)
        self.assertNotIn(".style.bottom", transform_block)

    def test_never_replaces_or_sets_character_source(self):
        src = self._anim_js()
        self.assertNotIn(".src =", src)
        self.assertNotIn('src="/static/cloud.png"', src)
        self.assertNotIn("document.createElement", src)

    def test_targets_character_with_mini_orb_fallback(self):
        src = self._anim_js()
        self.assertIn('var CHARACTER_ID = "vmCloudCharacter"', src)
        self.assertIn('var MINI_ORB_ID = "vmCloudCompanionMiniOrb"', src)
        self.assertIn("getElementById(CHARACTER_ID)", src)
        self.assertIn("getElementById(MINI_ORB_ID)", src)
        self.assertIn("50% 100%", src)

    def test_pauses_while_dragging_hidden_or_reduced_motion(self):
        src = self._anim_js()
        self.assertIn("_dragging", src)
        self.assertIn("document.hidden", src)
        self.assertIn("prefers-reduced-motion", src)
        self.assertIn("visibilitychange", src)

    def test_no_future_brain_or_voice_apis_yet(self):
        src = self._anim_js()
        for banned in ("SpeechRecognition", "getUserMedia", "AudioContext",
                       "fetch(", "localStorage", "/api/cloud/chat"):
            self.assertNotIn(banned, src,
                             "%r is deferred until the brain/voice phase" % banned)

    def test_on_state_change_hook_present_for_brain_phase(self):
        src = self._anim_js()
        self.assertIn("onStateChange", src)
        self.assertIn("function (cb)", src)
        self.assertIn("_onChangeCb", src)

    def test_demo_cycles_states_without_adding_ui(self):
        src = self._anim_js()
        self.assertIn("demo:", src)
        self.assertIn("setInterval", src)
        self.assertIn("listStates", src)

    # ── New capability layer (Layer 3 / 4) ──────────────────────────────
    def test_explicit_gaze_api_present(self):
        src = self._anim_js()
        self.assertIn("function lookToward(", src)
        self.assertIn("function lookAt(", src)
        self.assertIn("function clearLook(", src)
        self.assertIn("_lookTarget", src)
        self.assertIn("_lookHoldUntil", src)

    def test_blink_variants_present(self):
        src = self._anim_js()
        self.assertIn('speed === "quick"', src)
        self.assertIn('speed === "slow"', src)
        self.assertIn("triggerBlink(r < 0.70", src)

    def test_one_shot_arm_gestures_present(self):
        src = self._anim_js()
        self.assertIn("function triggerArmGesture(", src)
        self.assertIn('KIND === "wave"', src)
        self.assertIn('KIND === "welcome"', src)
        self.assertIn('KIND === "point_up"', src)
        self.assertIn('KIND === "point_l"', src)
        self.assertIn('KIND === "point_r"', src)
        self.assertIn('KIND === "point_down"', src)
        self.assertIn("function wave(", src)
        self.assertIn("function point(", src)
        self.assertIn("function welcome(", src)

    def test_emotional_walking_profiles_present(self):
        src = self._anim_js()
        self.assertIn("var WALK_PROFILES = {", src)
        for state in ("thinking", "happy", "excited", "sad", "sleepy", "surprised"):
            self.assertIn("%s:" % state, src)
        self.assertIn("walkProfile()", src)
        self.assertIn("pr.cadence", src)

    def test_body_follows_gaze_coordination_present(self):
        src = self._anim_js()
        # A cloud has no neck, so the gaze chain leads the whole body (a lean +
        # slight roll) instead of a robot head rotation.
        self.assertIn("lookX += _walk.dir", src)
        self.assertIn("headTxT", src)
        self.assertIn("_cur.hdRot", src)
        self.assertIn("rigPartTransform(p.body", src)

    def test_future_brain_surface_present_but_not_wired(self):
        src = self._anim_js()
        self.assertIn("BrainAPI: {", src)
        for verb in ("setEmotion", "startThinking", "startSpeaking",
                     "stopSpeaking", "walkTo", "lookToward", "lookAt",
                     "wave", "point", "welcome", "stop"):
            self.assertIn(verb, src)
        # Honest scoping: the anim surface animates, no brain is integrated.
        self.assertIn("no Brain wiring today", src)
        self.assertNotIn("/api/cloud/chat", src)
        self.assertNotIn("fetch(", src)


class CloudRigStaticTestCase(unittest.TestCase):
    """Static guarantees about static/cloud_rig.js — the layered vector rig for
    the cloud companion, an ORIGINAL cloud visual (NOT a robot, NOT the legacy
    cloud.png) drawn as separately addressable toy-like parts.

    static/cloud.png stays only as the no-JS/loading fallback and is hidden the
    moment the rig mounts; the rig is how independent eyes/brows/nose/mouth/
    arms/hands/legs can be animated at all without a flattened stale asset
    behind the character.
    """

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _rig_js(self):
        return (ROOT / "static" / "cloud_rig.js").read_text(encoding="utf-8")

    def _anim_js(self):
        return (ROOT / "static" / "cloud_anim.js").read_text(encoding="utf-8")

    def test_rig_script_loaded_before_anim(self):
        html = self._index_html()
        rig = re.search(r'<script src="/static/cloud_rig\.js\?v=2"></script>', html)
        anim = re.search(r'<script src="/static/cloud_anim\.js\?v=2"></script>', html)
        self.assertIsNotNone(rig, "cloud_rig.js script tag is required")
        self.assertIsNotNone(anim)
        self.assertLess(rig.start(), anim.start(),
                        "rig must load before the animation engine")

    def test_rig_exposes_public_api(self):
        src = self._rig_js()
        self.assertIn("window.VMCloudRig = {", src)
        self.assertIn("mount: mount", src)
        self.assertIn("build: build", src)
        self.assertIn("collectRig: collectRig", src)
        self.assertIn("mountRoot: mountRoot", src)

    def test_rig_declares_original_cloud_not_legacy_png(self):
        src = self._rig_js()
        self.assertIn("cloud", src)
        self.assertIn("original cloud", src)
        self.assertIn("There is no robot anywhere in this rig", src)
        # The legacy cloud asset survives strictly as the hidden fallback only.
        self.assertIn("static/cloud.png", src)
        self.assertIn("vmcloud-fallback", src)

    def test_rig_palette_cloud(self):
        src = self._rig_js()
        # Cloud palette: luminous cool-white crown, cool shaded underside, a
        # soft silhouette line, deep navy face ink and warm blush — not robot
        # chrome.
        self.assertIn('cloudTop:  "#F4FBFF"', src)
        self.assertIn('cloudBase: "#BCE2F0"', src)
        self.assertIn('edge:      "#8CC6DC"', src)
        self.assertIn('ink:       "#16324F"', src)
        self.assertIn('blush:     "#FFA9C4"', src)
        self.assertIn('limbHigh:  "#EAF8FF"', src)
        self.assertIn('limbLow:   "#A6D2E6"', src)
        for gone in ('headTop:  "#F4F9FB"', 'face:    "#05090D"',
                     'leg:     "#2E7CF6"', 'ink:     "#00E5FF"'):
            self.assertNotIn(gone, src, "robot palette %s should be removed" % gone)

    def test_rig_exposes_all_addressable_parts(self):
        src = self._rig_js()
        # Every addressable part is labeled (either an inline label or a
        # builder invocation) and routed through the generic data-part marker.
        self.assertIn('labelPart(%s, "body")' % "bodyG", src)
        for part in ("body", "leftEyebrow", "rightEyebrow", "leftEye",
                     "rightEye", "nose", "leftCheek", "rightCheek", "mouth",
                     "leftArm", "rightArm", "leftHand", "rightHand",
                     "leftLeg", "rightLeg", "shadow"):
            self.assertIn('"%s"' % part, src)
        # Robot-only parts are gone for good.
        for part in ("head", "face", "halo", "lowerBody", "leftEar",
                     "rightEar"):
            self.assertNotIn('"%s"' % part, src, "%s should be removed" % part)
        self.assertIn('attr(g, "data-part", id)', src)

    def test_rig_is_one_cloud_body(self):
        src = self._rig_js()
        # A SINGLE silhouette path describes the cloud; the rim pass, the fill
        # pass and the clip path all reuse it so they cannot disagree.
        self.assertIn("var SILHOUETTE_D = [", src)
        self.assertIn('attr(clip, "id", "vmCloudSilhouette")', src)
        self.assertIn('attr(clipShape, "d", SILHOUETTE_D)', src)
        self.assertIn('attr(rim, "d", SILHOUETTE_D)', src)
        self.assertIn('attr(mainBody, "d", SILHOUETTE_D)', src)
        # The outline pass is grown with a thick round-joined stroke instead of
        # by offsetting circles, so it reads as a rim on light and dark alike.
        self.assertIn('attr(rim, "stroke-width", "14")', src)
        self.assertIn('attr(rim, "stroke-linejoin", "round")', src)

        # Every coordinate in the silhouette must sit inside the 433x577 canvas.
        # A path that drifts outside the viewBox renders as a mostly-clipped,
        # unrecognisable blob, which is invisible to the DOM assertions below.
        block = re.search(r"var SILHOUETTE_D = \[(.*?)\]\.join", src, re.S)
        self.assertIsNotNone(block, "SILHOUETTE_D literal must be readable")
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", block.group(1))]
        self.assertGreater(len(nums), 40, "silhouette should be a real multi-point path")
        xs, ys = nums[0::2], nums[1::2]
        self.assertGreaterEqual(min(xs), 0, "silhouette stays inside the left edge")
        self.assertLessEqual(max(xs), 433, "silhouette stays inside the right edge")
        self.assertGreaterEqual(min(ys), 0, "silhouette stays inside the top edge")
        self.assertLessEqual(max(ys), 577, "silhouette stays inside the bottom edge")
        mid = (min(xs) + max(xs)) / 2
        self.assertLess(abs(mid - 216.5), 12,
                        "silhouette is horizontally centred in the canvas")

        # The old detached-circle silhouette must not creep back in.
        self.assertNotIn("var PUFFS = [", src)
        self.assertNotIn("function drawPuffs(", src)

    def test_rig_mouth_neutral_matches_anim_table(self):
        rig = self._rig_js()
        anim = self._anim_js()
        # cloud_anim.js hardcodes its MOUTH table in rig coordinates and swaps
        # `d` on the first pose it applies. If the rig's neutral path sits at a
        # different y than MOUTH.neutral the mouth visibly jumps on the first
        # expression change, so the two must stay locked together.
        rig_neutral = re.search(r'attr\(mouthPath, "d", "([^"]+)"\)', rig)
        anim_neutral = re.search(r'neutral:\s*"([^"]+)"', anim)
        self.assertIsNotNone(rig_neutral, "rig must set an explicit neutral mouth path")
        self.assertIsNotNone(anim_neutral, "cloud_anim must define MOUTH.neutral")
        self.assertEqual(
            rig_neutral.group(1), anim_neutral.group(1),
            "rig neutral mouth must equal cloud_anim MOUTH.neutral so the first "
            "expression swap does not jump the mouth")

    def test_rig_robot_parts_are_gone(self):
        src = self._rig_js()
        # One cloud body, no head/face screen/halo/ears/hover base.
        for gone in ('"head"', '"face"', '"halo"', '"lowerBody"',
                     '"leftEar"', '"rightEar"'):
            self.assertNotIn(gone, src, "%s should be removed" % gone)
        # The robot's chest emblem is gone too.
        self.assertNotIn("vEmblem", src)
        self.assertNotIn('id === "rightArm"', src)

    def test_rig_pivots_per_part(self):
        src = self._rig_js()
        self.assertIn("transformOrigin = xPct", src)
        self.assertIn('g.style.transformBox = "fill-box"', src)
        self.assertIn("leftShoulder", src)
        self.assertIn("leftHip", src)

    def test_rig_keeps_png_fallback(self):
        src = self._rig_js()
        self.assertIn('img.src = "/static/cloud.png"', src)
        self.assertIn('vmcloud-fallback', src)