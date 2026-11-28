"""Chat editing + upload-routing tests.

Covers the conversational hand-off (no manual workspace or timeline), the
storage gateway that routes reference photos, custom stickers and reaction
clips through the media manager, and the shared sticker-placement vocabulary
used by the chat picker and the real render engine.

Run with: python -m pytest tests/test_massive_editing_phase1.py -v
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

import app as app_module  # noqa: E402
import core.studio_jobs as sj  # noqa: E402
from core import video_editor as massive_storage  # noqa: E402


class Phase1StaticTestCase(unittest.TestCase):
    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_manual_timeline_ui_is_removed(self):
        html = self._read("index.html")
        js = self._read("static/massive_editing.js")
        settings = self._read("static/settings.js")
        markers = (
            "me-timeline", "me-playhead", "me-tl-", "meSidebar", "meDrawer",
            "meWorkspace", "meProperties", "me-composer-dock", "me-flyout",
            "Properties - Velocity", "Beat Sync", "VIDEO/AUDIO/FX/STICKERS",
            "sliceAtPlayhead", "trimAtPlayhead", "alignToBeat",
            "collectTimelineBlocks", "Start AI Edit", "meProjectsBtn",
            "meModeSelect", "meExportBtn",
        )
        for marker in markers:
            for name, source in (("index", html), ("module", js)):
                self.assertNotIn(marker, source, f"{name} still carries {marker}")
        self.assertNotIn("key: \"editing\"", settings)
        self.assertNotIn("Massive Editing", settings)

    def test_chat_picker_replaces_manual_drawers(self):
        html = self._read("index.html")
        js = self._read("static/massive_editing.js")
        settings = self._read("static/settings.js")
        for field_id in ("chatVideoInput", "chatImageInput", "chatAudioInput",
                         "chatStickerInput"):
            self.assertIn('id="' + field_id + '"', html)
        self.assertIn("#vmStickerPicker{", html)
        self.assertIn(".vm-sticker-places{", html)
        self.assertIn("window.VM_MASSIVE_STICKER_PLACEMENTS", settings)
        self.assertIn('requestJson("/api/editing/stickers?', js)
        self.assertIn("uploadStickers(files)", js)
        for label in ("Top Left", "Upper Right", "Lower Left", "Lower Right",
                      "Center"):
            self.assertIn(label, settings)
            self.assertIn(label, js)

    def test_chat_payload_carries_brief_media_sticker_and_canvas(self):
        js = self._read("static/massive_editing.js")
        for field in (
            'fd.append("video", state.video',
            'fd.append("media", m.file',
            'fd.append("instruction", state.instruction',
            'fd.append("sticker_url", state.sticker.url)',
            'fd.append("sticker_name", state.sticker.name',
            'fd.append("sticker_pos", state.placement)',
            'JSON.stringify({ canvas: { aspect: aspect } })',
            'requestJson("/api/editing/run"',
            '"/api/studio/job/"',
        ):
            self.assertIn(field, js, field)
        self.assertNotIn('"tts"', js)
        self.assertNotIn("voice_over", js.lower())
        self.assertNotIn("voiceover", js.lower())

    def test_no_heavy_asset_bundle_is_wired_into_chat(self):
        js = self._read("static/massive_editing.js").lower()
        self.assertNotIn("reaction-clips", js)
        self.assertNotIn("sticker-pack", js)


class BaseFakeManager:
    """Records storage calls without touching disk or the network."""

    def __init__(self):
        self.calls = []

    def save_image(self, path, **kwargs):
        self.calls.append(("image", path, kwargs))
        return {"media_id": "i1", "local_path": "/static/media/users/u/images/i1.png",
                "storage": "r2"}

    def save_video(self, path, **kwargs):
        self.calls.append(("video", path, kwargs))
        return {"media_id": "v1", "local_path": "/static/media/users/u/videos/v1.mp4",
                "storage": "r2"}

    def save_media(self, path, **kwargs):
        self.calls.append((kwargs.get("media_type", "audio"), path, kwargs))
        return {"media_id": "a1", "local_path": "/static/media/users/u/audio/a1.mp3",
                "storage": "r2"}


class FakeManager(BaseFakeManager):
    def save_asset(self, path, **kwargs):
        self.calls.append(("asset", path, kwargs))
        return {"media_id": "s1",
                "local_path": f"/static/media/users/u/{kwargs['kind']}s/s1.bin",
                "storage": "r2"}


class FakeManagerWithoutAssetApi(BaseFakeManager):
    """Older manager shape: has no save_asset method at all."""

    def __init__(self):
        super().__init__()
        self.user_id = "phase1"


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


class VideoEditorStorageTestCase(unittest.TestCase):
    def test_backend_reflects_configuration(self):
        with patch.object(massive_storage.r2_storage, "available", return_value=True):
            self.assertEqual(massive_storage.storage_backend(), "r2")
            self.assertTrue(massive_storage.object_storage_ready())
        with patch.object(massive_storage.r2_storage, "available", return_value=False):
            self.assertEqual(massive_storage.storage_backend(), "temporary")
            self.assertFalse(massive_storage.object_storage_ready())

    def test_store_upload_routes_all_three_kinds(self):
        manager = FakeManager()
        massive_storage.store_upload(manager, "/tmp/photo.png", "image", prompt="still")
        massive_storage.store_upload(manager, "/tmp/clip.mp4", "video", prompt="clip")
        massive_storage.store_upload(manager, "/tmp/beat.mp3", "audio", prompt="beat",
                                     category="music")
        self.assertEqual([c[0] for c in manager.calls], ["image", "video", "audio"])
        self.assertEqual(manager.calls[2][2]["library"], "edit")
        self.assertEqual(manager.calls[2][2]["category"], "music")

    def test_store_upload_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            massive_storage.store_upload(FakeManager(), "/tmp/x.bin", "document")

    def test_library_asset_uses_native_api_when_available(self):
        manager = FakeManager()
        massive_storage.store_library_asset(manager, "/tmp/sticker.png", kind="image",
                                            category="sticker", name="mine")
        self.assertEqual(manager.calls[0][0], "asset")
        self.assertEqual(manager.calls[0][2]["category"], "sticker")

    def test_library_asset_falls_back_to_save_media(self):
        manager = FakeManagerWithoutAssetApi()
        massive_storage.store_library_asset(manager, "/tmp/reaction.mp4", kind="video",
                                            category="reaction", name="hype")
        self.assertEqual(manager.calls[0][0], "video")
        self.assertEqual(manager.calls[0][2]["library"], "edit")
        self.assertEqual(manager.calls[0][2]["category"], "reaction")


class ManualOverrideGatewayTestCase(unittest.TestCase):
    def test_missing_default_and_malformed_payloads_are_noops(self):
        self.assertEqual(massive_storage.parse_manual_overrides(None), {})
        self.assertEqual(massive_storage.parse_manual_overrides(""), {})
        self.assertEqual(massive_storage.parse_manual_overrides("not-json"), {})
        defaults = {
            "speed": 1, "speed_curve": "Linear", "ramp_in": 0, "ramp_out": 0,
            "trim_start": 0, "trim_end": 0, "beat_sync": False,
            "overlay": "", "transition": "", "sticker_text": "",
            "sticker_anim": "", "effects": [], "mode": "Auto AI Director",
        }
        self.assertEqual(massive_storage.parse_manual_overrides(json.dumps(defaults)), {})

    def test_real_overrides_are_mapped_to_engine_keys(self):
        payload = json.dumps({
            "speed": 1.5, "speed_curve": "S-Curve", "ramp_in": 0.5,
            "trim_start": 1.25, "trim_end": 4, "beat_sync": True,
            "sticker_anim": "beat", "overlay": "Clean Border",
            "effects": ["boost", "vignette"],
        })
        manual = massive_storage.parse_manual_overrides(payload)
        self.assertEqual(manual["speed"], 1.5)
        self.assertEqual(manual["speed_curve"], "S-Curve")
        self.assertEqual(manual["ramp_in"], 0.5)
        self.assertEqual(manual["trim"], {"start": 1.25, "end": 4.0})
        self.assertTrue(manual["beat_sync"])
        self.assertEqual(manual["sticker_anim"], "beat")
        self.assertEqual(manual["overlay"], "Clean Border")
        self.assertEqual(manual["effects"], ["boost", "vignette"])


class StickerPlacementTestCase(unittest.TestCase):
    """Chat picker labels and render-engine codes share one mapping."""

    def test_human_labels_normalize_to_render_codes(self):
        self.assertEqual(
            [massive_storage.normalize_sticker_position(v) for v in (
                "Top Left", "upper right", "Lower-Left", "bottom right", "Centre")],
            ["tl", "tr", "bl", "br", "center"])
        self.assertEqual(massive_storage.normalize_sticker_position(""), "br")
        self.assertEqual(massive_storage.normalize_sticker_position(
            "nowhere", default="Top Left"), "tl")

    def test_overlay_expressions_follow_the_normalized_placement(self):
        self.assertEqual(massive_storage.sticker_overlay_expressions("Top Left"),
                         ("24", "24"))
        self.assertEqual(massive_storage.sticker_overlay_expressions("Upper Right"),
                         ("main_w-overlay_w-24", "24"))
        self.assertEqual(massive_storage.sticker_overlay_expressions("Lower Left"),
                         ("24", "main_h-overlay_h-24"))
        self.assertEqual(massive_storage.sticker_overlay_expressions("Lower Right"),
                         ("main_w-overlay_w-24", "main_h-overlay_h-24"))
        self.assertEqual(massive_storage.sticker_overlay_expressions("Center"),
                         ("(main_w-overlay_w)/2", "(main_h-overlay_h)/2"))

    def test_canvas_aspect_override_survives_manual_parsing(self):
        self.assertEqual(
            massive_storage.parse_manual_overrides(
                json.dumps({"canvas": {"aspect": "16:9"}})),
            {"canvas": {"aspect": "16:9"}})
        self.assertEqual(
            massive_storage.parse_manual_overrides(
                json.dumps({"canvas": {"aspect": "4:3"}})), {})


class _AuthMixin:
    @classmethod
    def _start_storage(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patches = (
            patch.object(app_module, "_users_file", Path(cls.tmp.name) / "users.json"),
            patch.object(app_module, "users_collection", lambda: None),
            patch.object(app_module, "auth_tokens_collection", lambda: None),
            patch.object(app_module, "load_marcus", lambda user_id=None: None),
        )
        for p in cls.patches:
            p.start()

    @classmethod
    def _stop_storage(cls):
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    @staticmethod
    def _client(email):
        user_id = app_module._safe_user_id(email)
        users = app_module._load_users()
        users[email] = {"_id": email, "email": email, "user_id": user_id,
                        "email_verified": True}
        app_module._save_users(users)
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["email"] = email
        return client


class EditingStorageRoutesTestCase(_AuthMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._start_storage()
        cls.manager = FakeManager()
        cls.patches += (patch.object(app_module, "get_media_manager",
                                     lambda user_id: cls.manager),)
        cls.patches[-1].start()

    @classmethod
    def tearDownClass(cls):
        cls._stop_storage()

    def setUp(self):
        self.manager.calls.clear()

    def test_storage_route_reports_readiness_without_naming_credentials(self):
        client = self._client("phase1_storage@example.com")
        with patch.object(massive_storage.r2_storage, "available", return_value=True):
            body = client.get("/api/editing/storage").get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["storage"], "r2")
        self.assertTrue(body["ready"])
        self.assertNotIn("account", str(body).lower())

    def test_custom_sticker_and_reaction_clip_use_library_asset_route(self):
        client = self._client("phase1_assets@example.com")
        resp = client.post("/api/editing/assets", data={
            "category": "sticker",
            "files": [(io.BytesIO(b"png"), "mine.png"),
                      (io.BytesIO(b"mp4"), "hype.mp4")],
        }, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["saved"], 2)
        kinds = [c[2]["kind"] for c in self.manager.calls]
        self.assertEqual(kinds, ["image", "video"])
        self.assertTrue(all(c[2]["category"] == "sticker" for c in self.manager.calls))

    def test_reference_photo_goes_through_the_storage_gateway(self):
        client = self._client("phase1_reference@example.com")
        fake_jobs = FakeJobsCollection()
        job_patches = (
            patch.object(sj, "studio_jobs_collection", lambda: fake_jobs),
            patch.object(sj, "launch", lambda job_id: None),
        )
        for p in job_patches:
            p.start()
        try:
            resp = client.post("/api/editing/run", data={
                "instruction": "slow-mo the goal with my still",
                "media": [(io.BytesIO(b"video"), "clip.mp4"),
                          (io.BytesIO(b"image"), "still.png")],
                "test_mode": "1",
            }, content_type="multipart/form-data")
            self.assertEqual(resp.status_code, 200)
            kinds = [c[0] for c in self.manager.calls]
            self.assertEqual(kinds, ["video", "image"])
            body = resp.get_json()
            self.assertEqual([a["type"] for a in body["job"]["media_assets"]], ["image"])
        finally:
            for p in job_patches:
                p.stop()

    def test_manual_overrides_reach_job_only_when_supplied(self):
        client = self._client("phase1_manual@example.com")
        fake_jobs = FakeJobsCollection()
        captured = {}
        real_new = sj.new_autoedit_job

        def capture_new(user_id, source_video_url, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return real_new(user_id, source_video_url, **kwargs)

        def payload(manual):
            return {
                "instruction": "slow-mo the goal",
                "media": [(io.BytesIO(b"video"), "clip.mp4")],
                "test_mode": "1",
                "manual": manual,
            }

        patches = (
            patch.object(sj, "studio_jobs_collection", lambda: fake_jobs),
            patch.object(sj, "launch", lambda job_id: None),
            patch.object(sj, "new_autoedit_job", capture_new),
        )
        for p in patches:
            p.start()
        try:
            defaults = json.dumps({
                "speed": 1, "speed_curve": "Linear", "ramp_in": 0, "ramp_out": 0,
                "trim_start": 0, "trim_end": 0, "beat_sync": False,
                "overlay": "", "transition": "", "sticker_text": "",
                "sticker_anim": "", "effects": [],
            })
            resp = client.post("/api/editing/run", data=payload(defaults),
                               content_type="multipart/form-data")
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("manual", captured)

            real = json.dumps({"speed_curve": "S-Curve", "ramp_in": 0.5,
                               "trim_start": 1.25, "beat_sync": True})
            resp = client.post("/api/editing/run", data=payload(real),
                               content_type="multipart/form-data")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(captured["manual"]["speed_curve"], "S-Curve")
            self.assertEqual(captured["manual"]["trim"], {"start": 1.25, "end": None})
            self.assertTrue(captured["manual"]["beat_sync"])
        finally:
            for p in patches:
                p.stop()


if __name__ == "__main__":
    unittest.main()
