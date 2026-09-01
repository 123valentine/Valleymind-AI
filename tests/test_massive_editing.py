"""Massive Editing tests.

Covers:
  1. Static guarantees about the frontend wiring in index.html: the Massive
     Editing workspace tab, its panel div, the vmWsGo hook that fires
     VMEditing.onShow + the first-look guide, the massive_editing.js script
     tag, the legacy entry points forwarding to the module, and that the old
     standalone overlay is gone.
  2. The /api/editing/transcribe and /api/editing/run + /api/editing/stickers
     routes exercised through the real Flask client with an authenticated
     session: login gating, input validation, and that instruction + voice +
     media + sticker + position all reach the created job.
  3. The AI plan engine (build_instruction_plan): an LLM failure must never
     break an edit (default plan), timestamps the LLM invents are clamped to
     the transcript, and a selected sticker's default position survives.

Run with: python -m pytest tests/test_massive_editing.py -v
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module
import core.studio_jobs as sj
import core.video_edit as ve
from core import brain as brain_module


class MassiveEditingStaticTestCase(unittest.TestCase):
    """Structural checks on index.html + massive_editing.js (logic is client-side)."""

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _module_js(self):
        return (ROOT / "static" / "massive_editing.js").read_text(encoding="utf-8")

    def test_editing_workspace_tab_present(self):
        html = self._index_html()
        self.assertIn('data-ws="editing"', html)
        self.assertIn("Massive Editing", html)
        self.assertIn('onclick="vmWsGo(\'editing\')"', html)

    def test_editing_workspace_panel_present(self):
        html = self._index_html()
        self.assertIn('data-ws-panel="editing"', html)
        self.assertIn('id="vmWsPanelEditing"', html)

    def test_editing_panel_lives_inside_studio_overlay(self):
        html = self._index_html()
        start = html.index('<div id="studioOverlay">')
        end = html.index("</div><!-- /Studio overlay -->")
        inside = html[start:end]
        self.assertIn('id="vmWsPanelEditing"', inside)

    def test_vm_ws_go_hooks_editing_onshow(self):
        html = self._index_html()
        block = html[html.index("function vmWsGo("):]
        self.assertIn('ws === "editing" && typeof VMEditing !== "undefined"', block)
        self.assertIn("VMEditing.onShow()", block)
        self.assertIn('vmGuideMaybeShow("editing")', block)

    def test_editing_script_loaded(self):
        html = self._index_html()
        self.assertIn('<script src="/static/massive_editing.js', html)

    def test_legacy_entry_points_forward_to_module(self):
        html = self._index_html()
        self.assertIn("function openEditing()", html)
        self.assertIn("VMEditing.launch", html)
        self.assertIn("function closeEditing()", html)
        self.assertIn("function editReset()", html)
        self.assertIn("VMEditing.reset", html)
        self.assertEqual(html.count("editingOverlay"), 0)

    def test_feature_tiles_launch_editing(self):
        html = self._index_html()
        self.assertIn("action: function () { openEditing(); }", html)

    def test_guide_registered(self):
        guide = (ROOT / "static" / "tutorials.js").read_text(encoding="utf-8")
        self.assertIn("editing", guide)
        self.assertIn("Massive Editing", guide)

    def test_module_exposes_api(self):
        js = self._module_js()
        self.assertIn("window.VMEditing = API", js)
        for method in ("launch", "onShow", "reset", "submit", "toggleRec",
                        "pickSticker", "clearVoice", "toDashboard"):
            self.assertIn(method, js)

    def test_start_is_explicit(self):
        # No auto-start: the "Start AI Edit" tap is the only submit path.
        js = self._module_js()
        self.assertIn("Start AI Edit", js)
        self.assertIn('postJSON("/api/editing/run"', js)


class _AuthHelpersMixin:
    @classmethod
    def _patch_isolated_storage(cls):
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
        cls._storage_patches = (cls._users_file_patch, cls._users_coll_patch,
                                cls._auth_coll_patch, cls._marcus_patch)
        for p in cls._storage_patches:
            p.start()

    @classmethod
    def _unpatch_storage(cls):
        for p in cls._storage_patches:
            p.stop()
        cls.tmpdir.cleanup()

    @staticmethod
    def _auth(email):
        user_id = app_module._safe_user_id(email)
        users = app_module._load_users()
        users[email] = {
            "_id": email, "email": email, "user_id": user_id,
            "email_verified": True,
        }
        app_module._save_users(users)
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["email"] = email
        return client


class FakeMediaManager:
    """Stands in for MediaManager so route tests never touch R2/disk."""

    def __init__(self):
        self.saved = []

    def save_video(self, path, **kwargs):
        self.saved.append(("video", path))
        return {"media_id": "v1", "local_path": "/static/media/users/u/edit/video/v1.mp4"}

    def save_image(self, path, **kwargs):
        self.saved.append(("image", path))
        return {"media_id": "i1", "local_path": "/static/media/users/u/edit/image/i1.png"}

    def save_media(self, path, **kwargs):
        self.saved.append(("audio", path))
        return {"media_id": "a1", "local_path": "/static/media/users/u/edit/audio/a1.mp3"}


class FakeJobsCollection:
    """In-memory stand-in for the studio_jobs Mongo collection."""

    def __init__(self):
        self.docs = {}

    def replace_one(self, query, doc, upsert=False):
        self.docs[query["_id"]] = doc

    def find_one(self, query):
        if isinstance(query, dict) and "_id" in query:
            return self.docs.get(query["_id"])
        return None


class TranscribeApiTestCase(_AuthHelpersMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._patch_isolated_storage()

    @classmethod
    def tearDownClass(cls):
        cls._unpatch_storage()

    def setUp(self):
        self._app = app_module.app.test_client()

    @patch("core.transcription.transcribe")
    def test_requires_login(self, _tr):
        resp = self._app.post("/api/editing/transcribe")
        self.assertEqual(resp.status_code, 401)
        _tr.assert_not_called()

    @patch("core.transcription.transcribe")
    def test_requires_audio_upload(self, _tr):
        client = self._auth("trans_gate@example.com")
        resp = client.post("/api/editing/transcribe")
        self.assertEqual(resp.status_code, 400)
        _tr.assert_not_called()

    @patch("core.transcription.transcribe")
    def test_transcribe_voice_note(self, _tr):
        _tr.return_value = {"text": "slow motion to the goal and add captions",
                            "words": [{"word": "slow", "start": 0.2}]}
        client = self._auth("trans_ok@example.com")
        resp = client.post("/api/editing/transcribe",
                           data={"audio": (io.BytesIO(b"fake-audio"), "voice.webm")},
                           content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["text"], "slow motion to the goal and add captions")
        _tr.assert_called_once()

    @patch("core.transcription.transcribe")
    def test_transcribe_failure_surfaces(self, _tr):
        _tr.return_value = {"error": "groq down"}
        client = self._auth("trans_err@example.com")
        resp = client.post("/api/editing/transcribe",
                           data={"audio": (io.BytesIO(b"x"), "voice.webm")},
                           content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.get_json()["status"], "error")


class RunApiTestCase(_AuthHelpersMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._patch_isolated_storage()
        cls.jobs = FakeJobsCollection()
        cls._media_patch = patch.object(app_module, "get_media_manager",
                                        lambda user_id: FakeMediaManager())
        cls._coll_patch = patch.object(sj, "studio_jobs_collection",
                                       lambda: cls.jobs)
        cls._launch_patch = patch.object(sj, "launch", lambda _job_id: None)
        cls._run_patches = (cls._media_patch, cls._coll_patch, cls._launch_patch)
        for p in cls._run_patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._run_patches:
            p.stop()
        cls._unpatch_storage()

    def setUp(self):
        self._app = app_module.app.test_client()
        self.jobs.docs.clear()

    def _upload(self, extra=None):
        data = {"media": [(io.BytesIO(b"fake-video-bytes"), "clip.mp4")],
                "instruction": "Hype reel with captions and slow motion"}
        if extra:
            data.update(extra)
        return data

    def test_requires_login(self):
        resp = self._app.post("/api/editing/run")
        self.assertEqual(resp.status_code, 401)

    def test_requires_a_video(self):
        client = self._auth("run_no_video@example.com")
        resp = client.post("/api/editing/run",
                           data={"instruction": "add captions"},
                           content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("video", resp.get_json()["message"].lower())

    def test_requires_an_instruction(self):
        client = self._auth("run_no_instr@example.com")
        resp = client.post("/api/editing/run",
                           data=self._upload() | {"instruction": "   "},
                           content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("instruction", resp.get_json()["message"].lower())

    def test_voice_transcript_satisfies_instruction(self):
        client = self._auth("run_voice@example.com")
        data = self._upload() | {"instruction": "", "voice_transcript": "slow motion please"}
        resp = client.post("/api/editing/run", data=data,
                           content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        job = resp.get_json()["job"]
        self.assertEqual(job["voice_transcript"], "slow motion please")

    def test_full_submission_creates_job(self):
        client = self._auth("run_full@example.com")
        data = self._upload() | {
            "instruction": "Hype reel: captions, slow-mo the goal, fire sticker at the goal, use the uploads",
            "voice_transcript": "",
            "media": [(io.BytesIO(b"clip"), "clip.mp4"),
                      (io.BytesIO(b"img"), "still.png"),
                      (io.BytesIO(b"beat"), "beat.mp3")],
            "sticker_url": "/static/assets/stickers/sticker_0001.png",
            "sticker_name": "fire",
            "sticker_pos": "tr",
            "test_mode": "1",
        }
        resp = client.post("/api/editing/run", data=data,
                           content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "success")
        job = body["job"]
        self.assertTrue(job["job_id"])
        self.assertEqual(job["source"], "autoedit")
        self.assertTrue(job["test_mode"])
        self.assertEqual(job["instruction"], data["instruction"])
        self.assertEqual(job["edit_plan"], [])
        self.assertEqual(job["edit_plan_stage"], "")
        kinds = sorted(a["type"] for a in job["media_assets"])
        self.assertEqual(kinds, ["audio", "image"])

    def test_job_persists_sticker_position(self):
        client = self._auth("run_pos@example.com")
        data = self._upload() | {"sticker_url": "/static/assets/stickers/sticker_0001.png",
                                 "sticker_name": "fire", "sticker_pos": "tl"}
        client.post("/api/editing/run", data=data, content_type="multipart/form-data")
        coll = sj.studio_jobs_collection()
        stored = list(coll.docs.values())[0]
        self.assertEqual(stored["sticker_source"], data["sticker_url"])
        self.assertEqual(stored["sticker_name"], "fire")
        self.assertEqual(stored["sticker_pos"], "tl")

    def test_rejects_remote_sticker_url(self):
        client = self._auth("run_ssrf@example.com")
        data = self._upload() | {"sticker_url": "https://evil.example.com/x.png"}
        resp = client.post("/api/editing/run", data=data,
                           content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)  # sticker dropped, not blocked
        stored = list(sj.studio_jobs_collection().docs.values())[0]
        self.assertEqual(stored["sticker_source"], "")


class StickersApiTestCase(_AuthHelpersMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._patch_isolated_storage()

    @classmethod
    def tearDownClass(cls):
        cls._unpatch_storage()

    def test_requires_login(self):
        resp = app_module.app.test_client().get("/api/editing/stickers")
        self.assertEqual(resp.status_code, 401)

    def test_lists_bundled_stickers(self):
        client = self._auth("stickers@example.com")
        resp = client.get("/api/editing/stickers")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "success")
        self.assertIsInstance(body["stickers"], list)
        for s in body["stickers"]:
            self.assertIn("name", s)
            self.assertTrue(str(s.get("url", "")).startswith("/static/"))


class PlanEngineTestCase(unittest.TestCase):
    """build_instruction_plan must never let an LLM problem break an edit."""

    @staticmethod
    def _words(n=20):
        return [{"word": f"word{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(n)]

    @patch.object(brain_module, "_call_llm_cluster", side_effect=RuntimeError("cluster down"))
    def test_llm_failure_falls_back_to_default_plan(self, _llm):
        plan = ve.build_instruction_plan(
            instruction="hype reel", voice_transcript="", words=self._words(),
            keep=[(0.0, 9.5)], media_assets=[],
            sticker={"url": "/static/assets/stickers/fire.png", "name": "fire",
                     "position": "tr"})
        self.assertTrue(plan["steps"])
        self.assertTrue(plan["captions"])
        # A selected sticker is authoritative even when the LLM is down.
        self.assertEqual(len(plan["sticker_windows"]), 1)
        self.assertEqual(plan["sticker_windows"][0]["position"], "tr")
        self.assertEqual(plan["sticker_windows"][0]["fade"], 0.5)

    @patch.object(brain_module, "_call_llm_cluster")
    def test_invented_timestamps_get_clamped_to_transcript(self, _llm):
        raw = json.dumps({
            "steps": ["Trim", "Add captions"],
            "captions": True, "music": False,
            "broll": {"use_uploads": [], "windows": []},
            "slow_motion": {"start": 999, "end": 1000, "factor": 2.0},
            "sticker_use": True,
            "sticker_windows": [{"at": 99999, "duration": 3, "position": "center", "fade": 0}],
        })
        _llm.return_value = (raw, {"provider": "mock"})
        plan = ve.build_instruction_plan(
            instruction="slow motion at the end with a centered sticker",
            words=self._words(), keep=[(0.0, 9.5)], media_assets=[])
        self.assertLessEqual(plan["sticker_windows"][0]["at"], 9.5)
        # end-start < 0.4s ⇒ slow-motion falls back to "off"
        self.assertIsNone(plan["slow_motion"]["start"])

    @patch.object(brain_module, "_call_llm_cluster")
    def test_normal_plan_shape(self, _llm):
        raw = json.dumps({
            "steps": ["Trim", "Add captions", "Fire sticker"],
            "captions": True, "music": True,
            "broll": {"use_uploads": [0], "windows": [{"at": 4.0}]},
            "slow_motion": {"start": 6.0, "end": 7.5, "factor": 2.0},
            "sticker_use": True,
            "sticker_windows": [{"at": 8.0, "duration": 3, "position": "br", "fade": 0.5}],
            "note": "ok",
        })
        _llm.return_value = (raw, {"provider": "mock"})
        plan = ve.build_instruction_plan(
            instruction="use the uploaded image, add music, slider", words=self._words(),
            keep=[(0.0, 9.5)], media_assets=[{"type": "image", "url": "/x.png", "name": "still"}])
        self.assertEqual(plan["broll"]["use_uploads"], [0])
        self.assertTrue(plan["music"])
        self.assertEqual(plan["slow_motion"]["start"], 6.0)
        self.assertEqual(plan["sticker_windows"][0]["position"], "br")


if __name__ == "__main__":
    unittest.main()