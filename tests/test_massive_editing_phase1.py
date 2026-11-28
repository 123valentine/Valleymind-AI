"""Massive Editing workspace + upload-routing tests.

Covers the sidebar-led tablet/desktop layout (permanent asset rail, adjacent
slide-over drawers, stacked multi-track timeline, docked speed properties and
the bottom multimedia composer) and the storage gateway that routes reference
photos, custom stickers and reaction clips through the media manager (object
storage when configured, temporary local storage only when it is unset).

Run with: python -m pytest tests/test_massive_editing_phase1.py -v
"""
import io
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

    def test_top_bar_has_projects_mode_and_export(self):
        js = self._read("static/massive_editing.js")
        for label in ("Projects", "Mode", "Export", "meProjectsBtn",
                      "meModeSelect", "meExportBtn"):
            self.assertIn(label, js)
        for mode in ("Auto AI Director", "Skit & Meme Cutaways",
                     "Sports / Beat-Sync", "Tutorial Pop-Ups", "Character Fusion"):
            self.assertIn(mode, js)

    def test_preview_and_compact_timeline_are_present(self):
        js = self._read("static/massive_editing.js")
        html = self._read("index.html")
        for marker in ("me-preview", "meTimeline", "me-timeline-strip",
                       "me-tl-base-track", "me-tl-marker", "16:9", "9:16"):
            self.assertIn(marker, js)
        self.assertIn(".me-timeline-strip", html)
        self.assertIn(".me-preview", html)

    def test_single_composer_has_plus_upload_and_apply(self):
        js = self._read("static/massive_editing.js")
        self.assertIn('placeholder="Describe your AI edit..."', js)
        self.assertIn('id="meFilesInput" accept="video/*,image/*,audio/*" multiple', js)
        self.assertIn("me-composer-plus", js)
        self.assertIn("me-attachment-chips", js)
        self.assertIn(">Apply</button>", js)
        self.assertIn('title="Start AI Edit"', js)
        self.assertIn('postJSON("/api/editing/run"', js)

    def test_sidebar_tools_and_slide_over_drawers(self):
        js = self._read("static/massive_editing.js")
        html = self._read("index.html")
        for label in ("Stickers", "Overlays", "Transitions", "FX"):
            self.assertIn(label, js)
        for drawer in ("stickersTool", "overlaysTool", "transitionsTool",
                       "fxTool", "meDrawer", "openDrawer", "toggleTool"):
            self.assertIn(drawer, js)
        self.assertIn(".me-sidebar", html)
        self.assertIn(".me-flyout.open", html)
        self.assertNotIn(".me-bottom-tabs", html)

    def test_drawer_categories_and_empty_state_presets(self):
        settings = self._read("static/settings.js")
        js = self._read("static/massive_editing.js")
        for label in ("Velocity & Beat", "Color & Vibe", "Framing",
                      "Beat Flash", "Zoom Punch", "Velocity Slow-Mo",
                      "Camera Shake", "Phonk Contrast", "Cinematic Warm", "Vignette"):
            self.assertTrue(label in settings or label in js, label)
        for label in ("My Stickers", "Reactions", "Sports", "Tutorial Pop-Ups",
                      "YES!", "NO WAY", "TOO REAL", "GOAT", "🔥", "😂", "⚽"):
            self.assertTrue(label in settings or label in js, label)
        for label in ("Clean Border", "Neon Frame", "Picture Card", "PIP Window",
                      "Cut", "Whip Pan", "Fade", "Glitch"):
            self.assertTrue(label in settings or label in js, label)
        self.assertIn('<strong>Add</strong>', js)
        self.assertIn("Ready for sync", js)
        for label in ("Word-by-Word Pop", "Clean Subtitle", "Off",
                      "Keep Original Audio", "Add Background Track"):
            self.assertTrue(label in settings or label in js, label)

    def test_no_heavy_asset_bundle_is_wired_into_the_workspace(self):
        js = self._read("static/massive_editing.js")
        self.assertNotIn("reaction-clips", js.lower())
        self.assertNotIn("sticker-pack", js.lower())

    def test_sidebar_feeds_stacked_timeline_and_properties(self):
        js = self._read("static/massive_editing.js")
        settings = self._read("static/settings.js")
        html = self._read("index.html")
        for marker in ("meSidebar", "meDrawer", "meWorkspace", "me-tl-multitrack",
                       "me-tl-track-label", "me-tl-marker", "me-tl-playhead",
                       "meProperties", "Speed Control", "sliceAtPlayhead",
                       "trimAtPlayhead", "alignToBeat", "Beat Sync", "Vocal",
                       "collectTimelineBlocks"):
            self.assertIn(marker, js)
        for label in ("VIDEO", "AUDIO", "FX", "STICKERS"):
            self.assertIn(label, js)
        for marker in ("me-sidebar", "me-flyout.open", "me-tl-multitrack",
                       "me-tl-trim-handle", "me-properties", "me-composer-dock"):
            self.assertIn(marker, html)
        for tool in ("stickers", "overlays", "transitions", "fx"):
            self.assertIn('id: "%s"' % tool, settings)

    def test_apply_payload_carries_sidebar_selections_and_speed_controls(self):
        js = self._read("static/massive_editing.js")
        for field in ('fd.append("instruction", text)', 'fd.append("mode", ME.modeName)',
                      'fd.append("manual", JSON.stringify(manualOverrides()))',
                      'speed_curve:', 'ramp_in:', 'ramp_out:', 'trim_start:', 'trim_end:',
                      'beat_sync:', 'sticker_anim:', 'fd.append("overlay", ME.overlay)',
                      'fd.append("transition", ME.preview.transition)',
                      'fd.append("sticker_text", ME.sticker.text)',
                      'postJSON("/api/editing/run"'):
            self.assertIn(field, js)


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


if __name__ == "__main__":
    unittest.main()
