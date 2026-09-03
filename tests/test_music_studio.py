"""Music Studio tests.

Covers:
  1. Static guarantees about the frontend wiring in index.html: the Music
     workspace tab, its panel div, the vmWsGo hook that fires vmMusicOnShow,
     and the music_studio.js script tag.
  2. The /api/music route (the "Let ValleyMind produce it" stage) exercised
     through the real Flask client with an authenticated session and a mocked
     LLM: login gating, input validation, and the honest AI response shape.

Run with: python -m pytest tests/test_music_studio.py -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module


class MusicStudioStaticTestCase(unittest.TestCase):
    """Structural checks on index.html (logic is client-side)."""

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def test_music_workspace_tab_present(self):
        html = self._index_html()
        self.assertIn('data-ws="music"', html)
        self.assertIn("Music Studio", html)
        self.assertIn('onclick="vmWsGo(\'music\')"', html)

    def test_music_workspace_panel_present(self):
        html = self._index_html()
        self.assertIn('data-ws-panel="music"', html)
        self.assertIn('id="vmWsPanelMusic"', html)

    def test_vm_ws_go_hooks_music_onshow(self):
        html = self._index_html()
        block = html[html.index("function vmWsGo("):]
        self.assertIn('ws === "music" && typeof vmMusicOnShow === "function"', block)
        self.assertIn("vmMusicOnShow()", block)

    def test_music_studio_script_loaded(self):
        html = self._index_html()
        self.assertIn('<script src="/static/music_studio.js', html)

    def test_music_studio_has_workspace_layout(self):
        """Verify music_studio.js defines the waveform-editor Studio layout."""
        js_path = ROOT / "static" / "music_studio.js"
        js = js_path.read_text(encoding="utf-8")
        self.assertIn("ms-studio", js)
        self.assertIn("mse-top", js)
        self.assertIn("mse-side", js)
        self.assertIn("mse-center", js)
        self.assertIn("mse-status", js)
        self.assertIn("ms-pnl", js)
        self.assertIn("VMMusic", js)
        # Verify all nav items
        for nav_id in ("record", "tracks", "generate", "tools", "projects"):
            self.assertIn(nav_id, js)
        # Verify all 10 tool sub-panels are defined
        for tid in ("voice", "music", "instruments", "lyrics",
                     "effects", "mix", "ai-edit", "memory",
                     "assets", "ai-tools"):
            self.assertIn(tid, js)


class MusicApiTestCase(unittest.TestCase):
    """Real app + patched isolated storage, exercised via the Flask client."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmpdir.name)
        cls._users_file_patch = patch.object(
            app_module, "_users_file", tmp / "auth_users.json")
        cls._users_coll_patch = patch.object(
            app_module, "users_collection", lambda: None)
        cls._auth_coll_patch = patch.object(
            app_module, "auth_tokens_collection", lambda: None)
        cls._marcus_patch = patch.object(
            app_module, "load_marcus", lambda user_id=None: None)
        for p in (cls._users_file_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._marcus_patch):
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in (cls._users_file_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._marcus_patch):
            p.stop()
        cls.tmpdir.cleanup()

    def setUp(self):
        self._app = app_module.app.test_client()

    def _auth(self, email: str):
        """Create a verified user record and return a session-authenticated client."""
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

    @patch.object(app_module, "_call_llm_cluster")
    def test_requires_login(self, _llm):
        resp = self._app.post("/api/music", json={"brief": "a song"})
        self.assertEqual(resp.status_code, 401)
        _llm.assert_not_called()

    @patch.object(app_module, "_call_llm_cluster")
    def test_requires_brief_or_lyrics(self, _llm):
        client = self._auth("music_gate@example.com")
        resp = client.post("/api/music", json={})
        self.assertEqual(resp.status_code, 400)
        _llm.assert_not_called()

    @patch.object(app_module, "_call_llm_cluster")
    def test_produces_creative_package(self, _llm):
        payload = {
            "brief": "I just sang this melody. Turn it into a romantic Afrobeats song.",
            "genre": "Afrobeats", "mood": "Romantic", "tempo": "Medium",
            "role": "Singer", "voice": "keep", "language": "English",
        }
        llm_json = json.dumps({
            "title": "Midnight in Lagos",
            "lyrics": "Stars above us…",
            "structure": "Intro-Verse-Chorus-Outro",
            "arrangement": "Log drums, shakers, 100 BPM in A minor",
            "note": "Lyrics + arrangement ready now; final audio rendering later.",
        })
        _llm.return_value = (llm_json, {"provider": "mock"})
        client = self._auth("music_ok@example.com")
        resp = client.post("/api/music", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["generated"])
        self.assertEqual(data["title"], "Midnight in Lagos")
        self.assertIn("Stars above", data["lyrics"])
        self.assertIn("rendering", data["note"])

    @patch.object(app_module, "_call_llm_cluster")
    def test_handles_non_json_llm_output(self, _llm):
        _llm.return_value = ("sorry, could not respond", {"provider": "mock"})
        client = self._auth("music_fallback@example.com")
        resp = client.post("/api/music", json={"brief": "a lonely R&B ballad"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["generated"])
        # Even on an empty result we must not fake a finished song.
        self.assertIn("lyrics", data)

    @patch.object(app_module, "_call_llm_cluster")
    def test_llm_failure_returns_502(self, _llm):
        _llm.side_effect = RuntimeError("provider down")
        client = self._auth("music_err@example.com")
        resp = client.post("/api/music", json={"brief": "a track"})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.get_json()["status"], "error")


class MusicAiEditTestCase(unittest.TestCase):
    """Tests for the POST /api/music/ai-edit incremental refinement route."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmpdir.name)
        cls._users_file_patch = patch.object(
            app_module, "_users_file", tmp / "auth_users.json")
        cls._users_coll_patch = patch.object(
            app_module, "users_collection", lambda: None)
        cls._auth_coll_patch = patch.object(
            app_module, "auth_tokens_collection", lambda: None)
        cls._marcus_patch = patch.object(
            app_module, "load_marcus", lambda user_id=None: None)
        for p in (cls._users_file_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._marcus_patch):
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in (cls._users_file_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._marcus_patch):
            p.stop()
        cls.tmpdir.cleanup()

    def _auth(self, email="edit@example.com"):
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

    def test_requires_login(self):
        resp = app_module.app.test_client().post(
            "/api/music/ai-edit", json={"instruction": "make it louder"})
        self.assertEqual(resp.status_code, 401)

    def test_requires_instruction(self):
        client = self._auth("edit_empty@example.com")
        resp = client.post("/api/music/ai-edit", json={})
        self.assertEqual(resp.status_code, 400)

    @patch.object(app_module, "_call_llm_cluster")
    def test_returns_targeted_changes(self, _llm):
        changes_json = json.dumps({
            "lyrics": "Updated lyrics here",
            "changes_summary": "Rewrote chorus",
        })
        _llm.return_value = (changes_json, {"provider": "mock"})
        client = self._auth("edit_ok@example.com")
        resp = client.post("/api/music/ai-edit", json={
            "instruction": "Rewrite the chorus to be more energetic",
            "lyrics": "Old lyrics here",
            "genre": "Afrobeats",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("changes", data)
        self.assertEqual(data["changes"]["lyrics"], "Updated lyrics here")
        self.assertIn("summary", data)

    @patch.object(app_module, "_call_llm_cluster")
    def test_llm_failure_returns_502(self, _llm):
        _llm.side_effect = RuntimeError("provider down")
        client = self._auth("edit_err@example.com")
        resp = client.post("/api/music/ai-edit", json={
            "instruction": "Add more bass"})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.get_json()["status"], "error")


class MusicProjectsSyncTestCase(unittest.TestCase):
    """Cloud-sync /api/music/projects endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmpdir.name)
        cls._users_file_patch = patch.object(
            app_module, "_users_file", tmp / "auth_users.json")
        cls._mp_dir_patch = patch.object(
            app_module, "_MUSIC_PROJECTS_DIR", tmp / "music_projects")
        cls._users_coll_patch = patch.object(
            app_module, "users_collection", lambda: None)
        cls._auth_coll_patch = patch.object(
            app_module, "auth_tokens_collection", lambda: None)
        cls._marcus_patch = patch.object(
            app_module, "load_marcus", lambda user_id=None: None)
        for p in (cls._users_file_patch, cls._mp_dir_patch,
                  cls._users_coll_patch, cls._auth_coll_patch, cls._marcus_patch):
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in (cls._users_file_patch, cls._mp_dir_patch,
                  cls._users_coll_patch, cls._auth_coll_patch, cls._marcus_patch):
            p.stop()
        cls.tmpdir.cleanup()

    def _auth(self, email="sync@example.com"):
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

    def test_requires_login(self):
        resp = app_module.app.test_client().get("/api/music/projects")
        self.assertEqual(resp.status_code, 401)

    def test_post_and_get_roundtrip(self):
        client = self._auth()
        payload = {"projects": [
            {"id": "ms1", "name": "Cloud Song", "mode": "diy", "savedAt": 1000},
            {"id": "ms2", "name": "Another", "mode": "ai", "savedAt": 2000},
        ]}
        resp = client.post("/api/music/projects", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["count"], 2)

        resp = client.get("/api/music/projects")
        self.assertEqual(resp.status_code, 200)
        proj = resp.get_json()["projects"]
        self.assertEqual(len(proj), 2)
        self.assertEqual(proj[0]["name"], "Cloud Song")

    def test_projects_are_user_scoped(self):
        client_a = self._auth("scoped_a@example.com")
        client_a.post("/api/music/projects", json={"projects": [{"id": "x1", "name": "A"}]})
        # A different user must not see user A's projects.
        client_b = self._auth("scoped_b@example.com")
        resp = client_b.get("/api/music/projects")
        self.assertEqual(resp.get_json()["projects"], [])

    def test_delete_single_project(self):
        client = self._auth("del@example.com")
        client.post("/api/music/projects", json={"projects": [
            {"id": "keep", "name": "Keep"}, {"id": "remove", "name": "Remove"}]})
        resp = client.delete("/api/music/projects/remove")
        self.assertEqual(resp.status_code, 200)
        proj = client.get("/api/music/projects").get_json()["projects"]
        self.assertEqual(len(proj), 1)
        self.assertEqual(proj[0]["id"], "keep")

    def test_rejects_non_list_payload(self):
        client = self._auth("bad@example.com")
        resp = client.post("/api/music/projects", json={"projects": "nope"})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
