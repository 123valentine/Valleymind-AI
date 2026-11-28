"""Conversational video-edit tests.

Covers:
  1. Static guarantees that the manual Massive Editing workspace is gone and
     that chat owns the composer attachments, sticker picker, clarification flow
     and confirmed /api/editing/run hand-off.
  2. The brain's clarification gate for vague attached-video prompts.
  3. The /api/editing/transcribe and /api/editing/run + /api/editing/stickers
     routes exercised through the real Flask client with an authenticated
     session: login gating, input validation, and that instruction + voice +
     media + sticker + position all reach the created job.
  4. The AI plan engine (build_instruction_plan): an LLM failure must never
     break an edit (default plan), timestamps the LLM invents are clamped to
     the transcript, and a selected sticker's default position survives.

Run with: python -m pytest tests/test_massive_editing.py -v
"""
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module
import core.studio_jobs as sj
import core.video_edit as ve
from core import brain as brain_module


class ChatEditingStaticTestCase(unittest.TestCase):
    """Structural checks on chat-side editing in index.html + massive_editing.js."""

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _module_js(self):
        return (ROOT / "static" / "massive_editing.js").read_text(encoding="utf-8")

    def test_manual_workspace_is_gone(self):
        html = self._index_html()
        js = self._module_js()
        for marker in (
            'data-ws="editing"', 'data-ws-panel="editing"', "vmWsPanelEditing",
            "id=\"meRoot\"", ".me-root", "me-timeline", "me-playhead",
            "Properties - Velocity", "Beat Sync", "VIDEO/AUDIO/FX/STICKERS",
            "me-sidebar", "me-composer-dock", "sliceAtPlayhead",
            "collectTimelineBlocks", "Start AI Edit",
        ):
            self.assertNotIn(marker, html, marker)
            self.assertNotIn(marker, js, marker)

    def test_chat_script_and_composer_inputs(self):
        html = self._index_html()
        self.assertIn('<script src="/static/massive_editing.js?v=7"', html)
        for field_id in ("chatVideoInput", "chatImageInput", "chatAudioInput",
                         "chatStickerInput"):
            self.assertRegex(
                html, r'id="' + field_id + r'"[^>]*class="vm-file-input"')
        for label in ("Video to Edit", "Reference Pictures", "Funny Audio Clips",
                      "Studio Asset Stickers"):
            self.assertIn(label, html)

    def test_composer_persona_avatar_is_locked_to_24px_circle(self):
        html = self._index_html()
        match = re.search(r"#personaBtnAvatar\s*\{([^}]*)\}", html)
        self.assertIsNotNone(match, "missing #personaBtnAvatar CSS lock")
        css = re.sub(r"\s+", " ", match.group(1))
        for declaration in (
            "width: 24px", "height: 24px", "max-width: 24px",
            "max-height: 24px", "border-radius: 9999px", "object-fit: cover",
            "flex-shrink: 0",
        ):
            self.assertIn(declaration, css, declaration)

    def test_confirmed_edit_hands_off_to_the_real_pipeline(self):
        html = self._index_html()
        self.assertIn(
            "_vmEditPlan = window.VMChatEdit.prepare(text, vmChatAttachments)",
            html)
        self.assertIn(r'textToSend += "\n" + _vmEditPlan.marker', html)
        self.assertIn("_vmEditPlan.shouldRun", html)
        self.assertIn("window.VMChatEdit.start().catch(", html)
        self.assertIn('requestJson("/api/editing/run"', self._module_js())
        self.assertIn('.vm-edit-icon-btn{', html)
        self.assertIn("border-radius:9999px", html)

    def test_legacy_entry_points_return_to_chat(self):
        html = self._index_html()
        self.assertRegex(
            html,
            r"function openEditing\(\)\s*\{[^}]*closeStudio\(\);[^}]*focusChatInput\(\);")
        self.assertIn("window.VMChatEdit.reset", html)
        self.assertNotIn('vmWsGo("editing")', html)
        guide = (ROOT / "static" / "tutorials.js").read_text(encoding="utf-8")
        self.assertNotIn("key: \"editing\"", guide)
        self.assertNotIn("Massive Editing", guide)

    def test_module_exposes_chat_api(self):
        js = self._module_js()
        self.assertIn("win().VMChatEdit = API", js)
        for method in (
            "VERSION", "PLACEMENTS", "state", "init", "prepare", "marker",
            "isConfirmation", "isDetailed", "normalizePlacement", "setPlacement",
            "selectSticker", "refreshStickers", "uploadStickers",
            "openStickerPicker", "buildFormData", "resultCardHtml", "start",
            "poll", "download", "regenerate", "reset",
        ):
            self.assertIn(method, js, method)
        self.assertIn('requestJson("/api/editing/run"', js)
        self.assertIn('"/api/studio/job/"', js)
        for label in ("Top Left", "Upper Right", "Lower Left", "Lower Right",
                      "Center"):
            self.assertIn(label, js, label)


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

    def test_picker_placement_label_is_normalized_for_the_engine(self):
        client = self._auth("run_picker_pos@example.com")
        data = self._upload() | {
            "sticker_url": "/static/assets/stickers/sticker_0002.png",
            "sticker_name": "fire", "sticker_pos": "Upper Right",
        }
        resp = client.post("/api/editing/run", data=data,
                           content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        stored = list(sj.studio_jobs_collection().docs.values())[0]
        self.assertEqual(stored["sticker_pos"], "tr")

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


class _FakeBrainMemory:
    """Minimal memory surface used by the deterministic clarification gate."""

    def __init__(self, history=None):
        self.history = list(history or [])
        self.messages = []
        self.long_term = {}
        self.user_id = "brain-test"

    def reload(self):
        return None

    def get_message_count(self, chat_id):
        return 1

    def get_chat(self, chat_id):
        return self.history

    def add_message(self, chat_id, role, content, timestamp=None, **kwargs):
        self.messages.append({"role": role, "content": content})

    def save_memory(self):
        return None

    def get_active_facts(self):
        return []

    def get_full_memory(self):
        return ""


def _clarifying_brain(history=None):
    brain = object.__new__(brain_module.MarcusBrain)
    brain.memory = _FakeBrainMemory(history)
    brain.profile = SimpleNamespace(
        key="marcus", name="Marcus", to_prompt=lambda: "")
    return brain


class VideoEditClarificationTestCase(unittest.TestCase):
    """Vague attached-video prompts get three questions, never a voiceover."""

    VAGUE = "make this viral [VIDEO_ATTACHED: clip.mp4]"

    def test_only_attached_videos_are_gated(self):
        self.assertFalse(brain_module.needs_video_edit_clarification("make this viral"))
        self.assertTrue(brain_module.needs_video_edit_clarification(self.VAGUE))

    def test_detailed_brief_skips_the_gate_before_vision_findings(self):
        detailed = (
            "hype reel, upper right sticker, zoom punches "
            "[VIDEO_ATTACHED: clip.mp4]\n[Visual Analysis Findings]\n"
            + ("Observed frame detail. " * 20)
        )
        self.assertFalse(brain_module.needs_video_edit_clarification(detailed))

    def test_confirmations_and_prior_clarifications_skip_the_gate(self):
        self.assertFalse(brain_module.needs_video_edit_clarification(
            "edit it now [VIDEO_ATTACHED: clip.mp4]"))
        self.assertFalse(brain_module.needs_video_edit_clarification(
            "make it viral now [VIDEO_ATTACHED: clip.mp4]"))
        history = [{"role": "assistant",
                    "content": "Quick questions before I edit your video. 1. Style..."}]
        self.assertFalse(brain_module.needs_video_edit_clarification(
            self.VAGUE, history))

    def test_clarification_copy_is_brief_and_never_adds_a_voiceover(self):
        message = brain_module.video_edit_clarification_message(self.VAGUE)
        self.assertIn("Quick questions before I edit", message)
        self.assertIn("clip.mp4", message)
        for fragment in ("Style/trend", "Stickers", "Top Left", "Upper Right",
                         "Lower Left", "Lower Right", "Center", "Camera treatment"):
            self.assertIn(fragment, message, fragment)
        self.assertIn(
            "I won't add an AI voiceover unless you explicitly ask for one.",
            message)
        self.assertIn("edit it now", message)
        self.assertIn("make it viral now", message)

    def test_respond_and_stream_respond_both_pause_for_questions(self):
        reply = _clarifying_brain().respond(
            self.VAGUE, chat_id="video-clarify-respond")
        self.assertIn("Quick questions before I edit", reply)
        chunks = list(_clarifying_brain().stream_respond(
            self.VAGUE, chat_id="video-clarify-stream"))
        self.assertTrue(chunks)
        self.assertIn("Quick questions before I edit", str(chunks[0]))
        self.assertIn("animation", str(chunks[-1]))


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
    def test_intensity_high_caps_effect_counts(self, _llm):
        words = self._words(60)
        raw = json.dumps({
            "steps": ["Trim"], "captions": True, "music": False, "intensity": "high",
            "reframe": "smart",
            "sticker_windows": [{"at": 1.0 + i, "duration": 3, "position": "br", "fade": 0.5}
                                for i in range(8)],
            "sticker_captions": [{"at": 2.0 + i, "text": f"BOOM{i}"} for i in range(8)],
            "camera_windows": [{"start": 3.0 + i * 2, "end": 4.2 + i * 2, "effect": "punch_in"}
                               for i in range(8)],
            "transitions": [{"at": 5.0 + i, "type": "zoom", "duration": 0.4} for i in range(8)],
            "sfx": [{"at": 6.0 + i, "type": "whoosh"} for i in range(8)],
        })
        # 30 line-gap keep allows transitions before max_t - 0.2 (transitions
        # between 5.0 and 12.0 stay valid; keep ends at 29.5).
        _llm.return_value = (raw, {"provider": "mock"})
        plan = ve.build_instruction_plan(
            instruction="viral short", words=words, keep=[(0.0, 29.5)], media_assets=[])
        self.assertEqual(plan["intensity"], "high")
        self.assertEqual(plan["reframe"], "smart")
        self.assertEqual(len(plan["sticker_windows"]), 6)   # high cap
        self.assertEqual(len(plan["sticker_captions"]), 5)  # high cap
        self.assertEqual(len(plan["camera_windows"]), 4)    # high cap
        self.assertEqual(len(plan["transitions"]), 4)       # high cap
        self.assertEqual(len(plan["sfx"]), 4)               # high cap

    @patch.object(brain_module, "_call_llm_cluster")
    def test_intensity_low_caps_and_forces_center_reframe(self, _llm):
        words = self._words(40)
        raw = json.dumps({
            "steps": ["Trim"], "captions": True, "music": False, "intensity": "low",
            "reframe": "smart",
            "sticker_windows": [{"at": 1.0 + i, "duration": 3} for i in range(5)],
            "sticker_captions": [{"at": 2.0 + i, "text": "X"} for i in range(5)],
            "camera_windows": [{"start": 3.0 + i, "end": 4.0 + i, "effect": "shake"} for i in range(5)],
            "transitions": [{"at": 5.0 + i, "type": "flash"} for i in range(5)],
            "sfx": [{"at": 6.0 + i, "type": "pop"} for i in range(5)],
        })
        _llm.return_value = (raw, {"provider": "mock"})
        plan = ve.build_instruction_plan(
            instruction="clean minimal polish", words=words, keep=[(0.0, 19.5)], media_assets=[])
        self.assertEqual(plan["intensity"], "low")
        self.assertEqual(plan["reframe"], "center")          # low forces center
        self.assertEqual(len(plan["sticker_windows"]), 1)
        self.assertEqual(len(plan["camera_windows"]), 1)
        self.assertEqual(len(plan["transitions"]), 0)
        self.assertEqual(len(plan["sfx"]), 0)
        self.assertEqual(len(plan["sticker_captions"]), 1)

    @patch.object(brain_module, "_call_llm_cluster")
    def test_reframe_defaults_from_subject_focus(self, _llm):
        raw = json.dumps({
            "steps": ["Trim"], "captions": True, "music": False,
            "sticker_windows": [], "sticker_captions": [],
            "camera_windows": [], "transitions": [], "sfx": [],
        })
        _llm.return_value = (raw, {"provider": "mock"})
        # analysis says the subject sits far off-center → smart reframe.
        plan = ve.build_instruction_plan(
            instruction="reframe it", words=self._words(),
            keep=[(0.0, 9.5)], media_assets=[],
            analysis={"subject_focus": 0.12})
        self.assertEqual(plan["reframe"], "smart")

    def test_normalize_manual_new_fields(self):
        m = ve._normalize_manual({
            "canvas": {"aspect": "9:16", "mode": "fill", "reframe": "center"},
            "intensity": "high", "transitions": "off", "camera": True, "sfx": False,
        })
        self.assertEqual(m["intensity"], "high")
        self.assertEqual(m["reframe"], "center")
        self.assertEqual(m["transitions_mode"], "off")
        self.assertTrue(m["camera"])
        self.assertFalse(m["sfx"])
        m2 = ve._normalize_manual({"transitions": "auto", "intensity": "wild"})
        self.assertEqual(m2.get("transitions_mode"), "auto")
        self.assertNotIn("intensity", m2)  # unknown intensity dropped
        m3 = ve._normalize_manual({
            "speed_curve": "S-Curve", "ramp_in": 0.5, "beat_sync": True,
            "sticker_anim": "bounce",
        })
        self.assertEqual(m3["speed_curve"], "S-Curve")
        self.assertEqual(m3["ramp_in"], 0.5)
        self.assertTrue(m3["beat_sync"])
        self.assertEqual(m3["sticker_anim"], "bounce")

    def test_analysis_notice_uses_vision_context(self):
        note = ve._analysis_notice({"vision": {"context": "Street football trick"}})
        self.assertIn("Street football trick", note)
        note2 = ve._analysis_notice({"moments": [{"at": 1.0}], "scenes": [{"start": 0.0}]})
        self.assertIn("1 scenes, 1 high-energy moments", note2)
        self.assertTrue(ve._analysis_notice({}))
        self.assertTrue(ve._analysis_notice(None))

    def test_build_timeline_meta_includes_new_layers(self):
        meta = ve.build_timeline_meta(
            plan={"keep": [(0.0, 4.0)]}, kept_words=[],
            brolls=[], sticker_windows=[], slow_motion={}, music=False, total_out=4.0,
            camera_windows=[{"start": 1.0, "end": 1.8, "effect": "punch_in"}],
            transitions=[{"at": 2.5}], sfx=[{"at": 3.0}],
            emphasis=[{"at": 0.5, "text": "NO WAY"}])
        for key in ("video", "captions", "stickers", "broll", "slow_motion",
                    "camera", "transitions", "sfx", "emphasis", "music"):
            self.assertIn(key, meta)
        self.assertEqual(meta["duration"], 4.0)
        self.assertEqual(meta["camera"][0]["effect"], "punch_in")
        self.assertTrue(any(b["kind"] == "text" for b in meta["emphasis"]))

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