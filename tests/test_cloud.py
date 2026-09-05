"""ValleyMind Cloud foundation tests.

Covers:
  1. Static guarantees about the frontend wiring in index.html: the Cloud
     workspace tab, its panel div, the vmWsGo hook that fires vmCloudOnShow,
     and the cloud.js script tag.
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

    def test_cloud_workspace_tab_present(self):
        html = self._index_html()
        self.assertIn('data-ws="cloud"', html)
        self.assertIn("Cloud", html)
        self.assertIn('onclick="vmWsGo(\'cloud\')"', html)

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
        self.assertIn("companion_minimized: false", js)
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


class Cloud3DStaticTestCase(unittest.TestCase):
    """Structural checks for the 3D Cloud character (static/cloud3d.js).

    These never execute WebGL/three.js: they assert that the renderer module
    is wired into index.html and cloud.js, keeps the renderer out of cloud.js,
    and centralizes the emotion/status→visual rig so every canonical state has
    a presentation.
    """

    def _cloud3d_js(self):
        return (ROOT / "static" / "cloud3d.js").read_text(encoding="utf-8")

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _cloud_js(self):
        return (ROOT / "static" / "cloud.js").read_text(encoding="utf-8")

    def test_cloud3d_script_loaded(self):
        html = self._index_html()
        self.assertIn('<script src="/static/cloud3d.js', html)

    def test_vm_ws_go_hooks_cloud_hide(self):
        html = self._index_html()
        block = html[html.index("function vmWsGo("):]
        self.assertIn('ws !== "cloud" && typeof vmCloudOnHide === "function"', block)
        self.assertIn("vmCloudOnHide()", block)

    def test_cloud_js_wires_stage_and_3d(self):
        js = self._cloud_js()
        self.assertIn('id="vmCloudStage"', js)
        self.assertIn("window.vmCloudOnHide", js)
        self.assertIn("VMCloud3D.attach", js)
        self.assertIn("VMCloud3D.detach", js)
        self.assertIn("VMCloud3D.notifyState", js)
        # Renderer must NOT leak into the controller module.
        self.assertNotIn("getContext('webgl')", js)
        self.assertNotIn("THREE.", js)
        self.assertNotIn("requestAnimationFrame", js)

    def test_cloud3d_exposes_public_api(self):
        js = self._cloud3d_js()
        self.assertIn("window.VMCloud3D = {", js)
        self.assertIn("attach: attach", js)
        self.assertIn("detach: detach", js)
        self.assertIn("notifyState: notifyState", js)
        self.assertIn("setAttentionTarget", js)
        self.assertIn("clearAttentionTarget", js)
        self.assertIn("isActive", js)
        self.assertIn("getEngineInfo", js)

    def test_cloud3d_lazy_loads_three(self):
        js = self._cloud3d_js()
        self.assertIn(
            'var THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"',
            js)
        self.assertIn('document.createElement("script")', js)
        self.assertIn("requestAnimationFrame", js)
        self.assertIn("cancelAnimationFrame", js)
        # Should be lazy: nothing renders until attach() is called.
        self.assertIn("function attach(", js)

    def test_cloud3d_covers_every_canonical_emotion(self):
        js = self._cloud3d_js()
        for emo in cloud_model.EMOTIONS:
            self.assertIn(emo, js)

    def test_cloud3d_covers_every_canonical_status(self):
        js = self._cloud3d_js()
        for status in cloud_model.INTERACTION_STATES:
            self.assertIn(status, js)

    def test_cloud3d_centralizes_visual_rigs(self):
        js = self._cloud3d_js()
        self.assertIn("var EMOTION_RIG = {", js)
        self.assertIn("var STATUS_OVERRIDE = {", js)
        self.assertIn("var PRESENTATION_ADJ = {", js)
        self.assertIn("var GESTURES = {", js)
        self.assertIn("var BASE_BODY = 0xeaf7ff", js)
        self.assertIn("var BASE_ACCENT = 0x00d4ff", js)

    def test_cloud3d_supports_presentations_and_attention(self):
        js = self._cloud3d_js()
        self.assertIn("var PRESENTATION_ADJ = {", js)
        for p in cloud_model.PRESENTATIONS:
            self.assertIn(p, js)
        self.assertIn("attention_target", js)
        self.assertIn("setAttentionTarget", js)
        self.assertIn("nextBlinkAt", js)

    def test_cloud3d_cleanup_and_fallback(self):
        js = self._cloud3d_js()
        self.assertIn("function detach()", js)
        self.assertIn("dispose", js)
        self.assertIn("removeChild", js)
        self.assertIn("WebGL unavailable", js)
        self.assertIn("showFallback", js)
        self.assertIn("visibilitychange", js)

    def test_cloud3d_uses_existing_stage_ids(self):
        js = self._cloud3d_js()
        self.assertIn('var STAGE_ID = "vmCloudStage"', js)
        self.assertIn('var STATUS_ID = "vmCloud3DStatus"', js)


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

    def _cloud3d_js(self):
        return (ROOT / "static" / "cloud3d.js").read_text(encoding="utf-8")

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

    def test_cloud3d_speech_gating(self):
        js = self._cloud3d_js()
        self.assertIn("speechActive", js)
        self.assertIn("notifySpeech", js)
        self.assertIn('status === "speaking" && engine.speechActive', js)

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
        self.assertIn("startCompanion3D", js)
        self.assertIn("start3D", js)
        # The controller never starts a second renderer; it re-parents the
        # single engine via attach()/suspend()/resume().
        self.assertIn("VMCloud3D.suspend", js)
        self.assertIn("VMCloud3D.resume", js)
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
        self.assertIn(".vmcloud-mini-dot", js)

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