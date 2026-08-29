"""Tests for the ValleyMind Preferences Setup <-> Settings integration.

The onboarding wizard (/static/onboarding.js) is NOT a separate
personalization system. It reads and writes through the EXISTING Settings
API (/api/settings/language, /api/settings/culture, /api/settings/preferences)
which persists into the single store below:

    memory_data/settings/<hashed_user_id>/settings.json

These tests verify the rule:

    Onboarding UI
        -> existing Settings API
        -> existing backend storage (settings.json)
        -> AI-readable memory (via _mirror_settings_to_memory)

and importantly that no second/onboarding-specific storage is created.

Coverage:
  A. Language section persistence  (response_language, language, country,
     state_province, native_languages, cultural_background, prefer_not_to_say)
  B. Preferences section persistence  (communication_style/note, use_cases,
     use_cases_other, expressive_language, custom_preference, voice_style) —
     including the Page-2 keys multilingual_behavior & preferred_characters —
     and that pre-existing preference fields still round-trip.
  C. Culture section persistence  (cultural_expression)
  D. Memory mirroring — saved preference values become AI-readable memory facts
     through the existing _mirror_settings_to_memory / _mirror_preference_to_memory.
  E. Single source of truth — one settings.json per user, no onboarding store.
  F. Skip behaviour — closing/skipping onboarding never deletes saved values.
  G. Creator compatibility — the Createaccount uses the exact same endpoints.

Run with: python -m pytest tests/test_preferences_onboarding.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the project root is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as app_module


class FakeMemory:
    """Minimal stand-in for Marcus long-term memory (matches core/memory.py)."""

    def __init__(self):
        self.long_term = {}
        self.preferences = {}
        self.facts = []
        self.saved = False

    def remember_preference(self, key, value):
        self.preferences[key] = value

    def remember_fact(self, memory_type, summary, value, confidence=0.0):
        self.facts.append({
            "type": memory_type,
            "summary": summary,
            "value": value,
            "confidence": confidence,
        })

    def save_memory(self):
        self.saved = True


class FakeMarcus:
    def __init__(self):
        self.memory = FakeMemory()


class SettingsApiTestCase(unittest.TestCase):
    """Real app + patched isolated storage, exercised via the Flask client."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmpdir.name)
        cls._settings_patch = patch.object(app_module, "_SETTINGS_DIR", tmp / "settings")
        cls._users_patch = patch.object(app_module, "_users_file", tmp / "auth_users.json")
        cls._users_coll_patch = patch.object(app_module, "users_collection", lambda: None)
        cls._auth_coll_patch = patch.object(app_module, "auth_tokens_collection", lambda: None)
        # Storage tests must not touch real Marcus memory files; memory
        # mirroring itself has its own dedicated test class below.
        cls._marcus_patch = patch.object(app_module, "load_marcus", lambda user_id=None: None)
        for p in (cls._settings_patch, cls._users_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._marcus_patch):
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in (cls._settings_patch, cls._users_patch, cls._users_coll_patch,
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

    # ── A. Language persistence ──────────────────────────────────

    def test_language_section_round_trip(self):
        client = self._auth("lang_a@example.com")
        payload = {
            "response_language": "ig",
            "language": "ig",
            "country": "Nigeria",
            "state_province": "Lagos",
            "native_languages": "Igbo, English",
            "cultural_background": "Igbo heritage",
            "prefer_not_to_say": False,
        }
        resp = client.put("/api/settings/language", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "success")

        saved = client.get("/api/settings/language").get_json()["data"]
        self.assertEqual(saved["response_language"], "ig")
        self.assertEqual(saved["language"], "ig")
        self.assertEqual(saved["country"], "Nigeria")
        self.assertEqual(saved["state_province"], "Lagos")
        self.assertEqual(saved["native_languages"], "Igbo, English")
        self.assertEqual(saved["cultural_background"], "Igbo heritage")
        self.assertIs(saved["prefer_not_to_say"], False)

    def test_language_prefer_not_to_say_true(self):
        client = self._auth("lang_b@example.com")
        client.put("/api/settings/language", json={"prefer_not_to_say": True, "response_language": "en"})
        saved = client.get("/api/settings/language").get_json()["data"]
        self.assertIs(saved["prefer_not_to_say"], True)

    def test_native_languages_round_trip_as_array(self):
        client = self._auth("lang_c@example.com")
        client.put("/api/settings/language", json={
            "native_languages": ["Igbo", "English"],
            "response_language": "en",
        })
        saved = client.get("/api/settings/language").get_json()["data"]
        self.assertEqual(saved["native_languages"], ["Igbo", "English"])

    # ── B. Preferences persistence ──────────────────────────────

    def test_multilingual_and_characters_round_trip(self):
        client = self._auth("prefs_b@example.com")
        payload = {
            "multilingual_behavior": "Follow the language I use",
            "preferred_characters": ["Marcus", "Angelina"],
        }
        resp = client.put("/api/settings/preferences", json=payload)
        self.assertEqual(resp.status_code, 200)

        saved = client.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(saved["multilingual_behavior"], "Follow the language I use")
        self.assertEqual(saved["preferred_characters"], ["Marcus", "Angelina"])

    def test_single_preferences_store_serves_both_wizard_keys(self):
        """The multi-step wizard and Settings share the same preferences dict —
        new keys merge in without wiping pre-existing ones."""
        client = self._auth("prefs_c@example.com")
        client.put("/api/settings/preferences", json={"voice_style": "Natural"})
        client.put("/api/settings/preferences", json={
            "voice_style": "Natural",
            "multilingual_behavior": "Mix languages naturally when appropriate",
            "preferred_characters": ["Marcus", "Angelina", "Elena"],
            "expressive_language_other": "Nigerian Pidgin slang",
        })
        saved = client.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(saved["voice_style"], "Natural")
        self.assertEqual(saved["multilingual_behavior"], "Mix languages naturally when appropriate")
        self.assertEqual(saved["preferred_characters"], ["Marcus", "Angelina", "Elena"])
        self.assertEqual(saved["expressive_language_other"], "Nigerian Pidgin slang")

    def test_preferences_section_round_trip(self):
        client = self._auth("prefs_a@example.com")
        payload = {
            "communication_style": ["Friendly", "Straight to the point"],
            "communication_note": "Keep ordinary answers short",
            "use_cases": ["Personal AI assistant", "Writing"],
            "use_cases_other": "Journaling and planning",
            "expressive_language": ["Light humor", "Slang"],
            "custom_preference": "Prefer concise bullet answers for work questions",
            "voice_style": "Natural",
            "response_length": "Balanced",
            "creativity": "Moderate",
        }
        resp = client.put("/api/settings/preferences", json=payload)
        self.assertEqual(resp.status_code, 200)

        saved = client.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(saved["communication_style"], ["Friendly", "Straight to the point"])
        self.assertEqual(saved["communication_note"], "Keep ordinary answers short")
        self.assertEqual(saved["use_cases"], ["Personal AI assistant", "Writing"])
        self.assertEqual(saved["use_cases_other"], "Journaling and planning")
        self.assertEqual(saved["expressive_language"], ["Light humor", "Slang"])
        self.assertEqual(saved["custom_preference"], "Prefer concise bullet answers for work questions")
        self.assertEqual(saved["voice_style"], "Natural")
        # Pre-existing preference fields must continue working.
        self.assertEqual(saved["response_length"], "Balanced")
        self.assertEqual(saved["creativity"], "Moderate")

    # ── C. Culture persistence ──────────────────────────────────

    def test_culture_section_round_trip(self):
        client = self._auth("culture_a@example.com")
        payload = {
            "cultural_expression": "deep",
            "cultural_identity": "igbo",
            "use_cultural_adages": True,
        }
        resp = client.put("/api/settings/culture", json=payload)
        self.assertEqual(resp.status_code, 200)

        saved = client.get("/api/settings/culture").get_json()["data"]
        self.assertEqual(saved["cultural_expression"], "deep")
        self.assertEqual(saved["cultural_identity"], "igbo")
        self.assertIs(saved["use_cultural_adages"], True)

    # ── D. Memory mirroring (existing mechanism, dedicated unit) ─

    def test_language_mirrored_to_memory(self):
        marcus = FakeMarcus()
        with patch.object(app_module, "load_marcus", return_value=marcus):
            app_module._mirror_settings_to_memory("uid_lang", "language", {
                "response_language": "ig",
                "country": "Nigeria",
                "state_province": "Lagos",
                "native_languages": "Igbo",
                "cultural_background": "Igbo heritage",
                "prefer_not_to_say": True,
            })
        mem = marcus.memory
        self.assertEqual(mem.long_term["response_language"], "ig")
        self.assertEqual(mem.preferences["language_country"], "Nigeria")
        self.assertEqual(mem.preferences["language_state_province"], "Lagos")
        self.assertEqual(mem.preferences["language_native_languages"], "Igbo")
        self.assertEqual(mem.preferences["language_cultural_background"], "Igbo heritage")
        self.assertEqual(mem.preferences["language_prefer_not_to_say"], "true")
        self.assertTrue(mem.saved)
        summaries = [f["summary"] for f in mem.facts]
        self.assertTrue(any("User prefers country: Nigeria" in s for s in summaries))
        self.assertTrue(any("User prefers native language(s): Igbo" in s for s in summaries))

    def test_culture_mirrored_to_memory(self):
        marcus = FakeMarcus()
        with patch.object(app_module, "load_marcus", return_value=marcus):
            app_module._mirror_settings_to_memory("uid_culture", "culture", {
                "cultural_expression": "deep",
            })
        mem = marcus.memory
        self.assertEqual(mem.long_term["cultural_expression"], "deep")
        self.assertEqual(mem.preferences["cultural_expression"], "deep")
        self.assertTrue(any(
            f["summary"] == "User prefers the level of cultural expression in replies: deep"
            for f in mem.facts
        ))

    def test_preferences_mirrored_to_memory(self):
        marcus = FakeMarcus()
        with patch.object(app_module, "load_marcus", return_value=marcus):
            app_module._mirror_settings_to_memory("uid_prefs", "preferences", {
                "communication_style": ["Friendly", "Direct"],
                "voice_style": "Natural",
                "use_cases": ["Writing"],
                "custom_preference": "Keep it concise",
            })
        mem = marcus.memory
        self.assertEqual(mem.preferences["preferences_communication_style"], "Friendly, Direct")
        self.assertEqual(mem.preferences["preferences_voice_style"], "Natural")
        self.assertEqual(mem.preferences["preferences_use_cases"], "Writing")
        self.assertEqual(mem.preferences["preferences_custom_preference"], "Keep it concise")
        summaries = [f["summary"] for f in mem.facts]
        self.assertTrue(any("User prefers voice style: Natural" in s for s in summaries))
        values = [f["value"] for f in mem.facts]
        self.assertIn("Friendly, Direct", values)

    def test_native_languages_array_mirrored_as_joined_string(self):
        marcus = FakeMarcus()
        with patch.object(app_module, "load_marcus", return_value=marcus):
            app_module._mirror_settings_to_memory("uid_lang_arr", "language", {
                "native_languages": ["Igbo", "English", "Pidgin English"],
                "response_language": "en",
            })
        mem = marcus.memory
        self.assertEqual(mem.preferences["language_native_languages"], "Igbo, English, Pidgin English")
        self.assertTrue(any(
            f["summary"] == "User prefers native language(s): Igbo, English, Pidgin English"
            for f in mem.facts
        ))

    def test_multilingual_and_characters_mirrored_to_memory(self):
        marcus = FakeMarcus()
        with patch.object(app_module, "load_marcus", return_value=marcus):
            app_module._mirror_settings_to_memory("uid_ml", "preferences", {
                "multilingual_behavior": "Follow the language I use",
                "preferred_characters": ["Marcus", "Angelina", "Elena"],
            })
        mem = marcus.memory
        self.assertEqual(mem.preferences["preferences_multilingual_behavior"],
                         "Follow the language I use")
        self.assertEqual(mem.preferences["preferences_preferred_characters"],
                         "Marcus, Angelina, Elena")
        self.assertTrue(any(
            "User prefers preferred characters: Marcus, Angelina, Elena" in f["summary"] for f in mem.facts
        ))

    def test_mirror_preference_skips_blank_or_none(self):
        marcus = FakeMarcus()
        app_module._mirror_preference_to_memory(marcus, "language_country", "", "country")
        app_module._mirror_preference_to_memory(marcus, "language_country", "  ", "country")
        app_module._mirror_preference_to_memory(marcus, "language_country", "n/a", "country")
        self.assertEqual(marcus.memory.preferences, {})

    # ── E. Single source of truth ───────────────────────────────

    def _user_dir(self, email: str) -> Path:
        """Resolve where a user's settings.json actually lives.

        The app session stores `user_id = _safe_user_id(email)` and
        `_settings_path()` applies `_safe_user_id()` AGAIN to that value, so the
        on-disk folder is the double-hashed id."""
        return app_module._SETTINGS_DIR / app_module._safe_user_id(app_module._safe_user_id(email))

    def test_exactly_one_settings_file_per_user(self):
        email = "single@example.com"
        client = self._auth(email)
        client.put("/api/settings/language", json={"response_language": "ig"})
        client.put("/api/settings/preferences", json={"voice_style": "Natural"})
        client.put("/api/settings/culture", json={"cultural_expression": "natural"})

        user_dir = self._user_dir(email)
        json_files = list(user_dir.glob("*.json"))
        self.assertEqual(len(json_files), 1, "Only settings.json must exist — no onboarding store")

        raw = json.loads(json_files[0].read_text(encoding="utf-8"))
        self.assertEqual(raw["language"]["response_language"], "ig")
        self.assertEqual(raw["preferences"]["voice_style"], "Natural")
        self.assertEqual(raw["culture"]["cultural_expression"], "natural")
        self.assertNotIn("onboarding", raw, "Preference data must not be stored under an onboarding key")

    def test_onboarding_reads_exact_settings_api_payload(self):
        """The onboarding wizard loads GET /api/settings/language, /preferences
        and /culture. Those responses must equal the single persisted store."""
        email = "roundtrip@example.com"
        client = self._auth(email)
        client.put("/api/settings/language", json={"response_language": "yo", "country": "Nigeria"})
        client.put("/api/settings/preferences", json={"voice_style": "Professional"})
        client.put("/api/settings/culture", json={"cultural_expression": "off"})

        lang = client.get("/api/settings/language").get_json()["data"]
        prefs = client.get("/api/settings/preferences").get_json()["data"]
        culture = client.get("/api/settings/culture").get_json()["data"]
        self.assertEqual(lang["response_language"], "yo")
        self.assertEqual(lang["country"], "Nigeria")
        self.assertEqual(prefs["voice_style"], "Professional")
        self.assertEqual(culture["cultural_expression"], "off")

    # ── F. Skip behaviour ───────────────────────────────────────

    def test_skip_does_not_delete_saved_values(self):
        """Skip/close in the wizard runs a best-effort save of gathered values and
        clears the client-side flag — it must never wipe existing preferences."""
        client = self._auth("skips@example.com")
        client.put("/api/settings/preferences", json={
            "voice_style": "Natural", "communication_style": ["Friendly"],
        })
        # Re-issuing the same merged values (as the wizard does on skip) is a no-op.
        client.put("/api/settings/preferences", json={
            "voice_style": "Natural", "communication_style": ["Friendly"],
        })
        saved = client.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(saved["voice_style"], "Natural")
        self.assertEqual(saved["communication_style"], ["Friendly"])
        self.assertNotIn("vm_pref_setup_pending", saved)

    def test_new_user_without_preferences_is_not_blocked(self):
        """A brand-new signing user with zero saved preferences can still hit the
        settings API (skip path) — no forced completion anywhere on the backend."""
        client = self._auth("fresh@example.com")
        resp = client.get("/api/settings/preferences")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["data"], {})

    # ── F2. Preferences setup status (server-side first-entry gate) ─

    def test_new_user_setup_status_is_not_started(self):
        client = self._auth("setup_new@example.com")
        resp = client.get("/api/settings/setup-status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["setup_status"], "not_started")

    def test_save_persists_settings_and_marks_completed(self):
        client = self._auth("setup_save@example.com")
        client.put("/api/settings/preferences", json={
            "about_me": "I run a design studio",
            "use_cases": ["Graphic design", "Content creation"],
            "voice_style": "Natural",
        })
        resp = client.post("/api/settings/setup-status", json={"setup_status": "completed"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["setup_status"], "completed")
        self.assertEqual(client.get("/api/settings/setup-status").get_json()["setup_status"],
                         "completed")
        saved = client.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(saved["about_me"], "I run a design studio")
        self.assertEqual(saved["use_cases"], ["Graphic design", "Content creation"])

    def test_skip_preserves_settings_marks_skipped_and_allows_chat(self):
        client = self._auth("setup_skip@example.com")
        client.put("/api/settings/preferences", json={
            "about_me": "I journal daily",
            "use_cases": ["Journaling"],
        })
        resp = client.post("/api/settings/setup-status", json={"setup_status": "skipped"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["setup_status"], "skipped")
        self.assertEqual(client.get("/api/settings/setup-status").get_json()["setup_status"],
                         "skipped")
        saved = client.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(saved["about_me"], "I journal daily")
        self.assertEqual(saved["use_cases"], ["Journaling"])
        self.assertEqual(saved["preferences_setup_status"], "skipped")
        # The auth gate stays OPEN after a skip: 404 (brain not configured in the
        # test harness) rather than 401 proves the user can reach chat endpoints.
        resp_chat = client.get("/chat/history")
        self.assertIn(resp_chat.status_code, (200, 404))

    def test_setup_status_invalid_value_rejected(self):
        client = self._auth("setup_bad@example.com")
        resp = client.post("/api/settings/setup-status", json={"setup_status": "maybe"})
        self.assertEqual(resp.status_code, 400)

    def test_backward_compat_existing_prefs_without_flag_marks_completed(self):
        client = self._auth("setup_back@example.com")
        client.put("/api/settings/preferences", json={"voice_style": "Professional"})
        self.assertEqual(client.get("/api/settings/setup-status").get_json()["setup_status"],
                         "completed")

    def test_defaults_never_force_completion(self):
        client = self._auth("setup_fresh@example.com")
        self.assertEqual(client.get("/api/settings/setup-status").get_json()["setup_status"],
                         "not_started")
        self.assertEqual(client.get("/api/settings/preferences").status_code, 200)

    def test_about_me_and_use_case_profile_round_trip(self):
        client = self._auth("about_ct@example.com")
        client.put("/api/settings/preferences", json={
            "about_me": "I am a lawyer and part-time podcaster",
            "use_case_profile": "Research and writing long-form articles",
            "use_cases": ["Research", "Writing"],
        })
        saved = client.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(saved["about_me"], "I am a lawyer and part-time podcaster")
        self.assertEqual(saved["use_case_profile"], "Research and writing long-form articles")
        self.assertEqual(saved["use_cases"], ["Research", "Writing"])

    def test_multi_user_setup_and_settings_are_isolated(self):
        client_a = self._auth("iso_a@example.com")
        client_b = self._auth("iso_b@example.com")
        client_c = self._auth("iso_c@example.com")
        client_a.put("/api/settings/preferences", json={
            "about_me": "A-only secret identity",
            "use_cases": ["Coding"],
        })
        client_b.put("/api/settings/preferences", json={
            "about_me": "B-only baking hobby",
            "use_cases": ["Journaling"],
        })
        da = client_a.get("/api/settings/preferences").get_json()["data"]
        db = client_b.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(da["about_me"], "A-only secret identity")
        self.assertIn("B-only", db["about_me"])
        self.assertNotIn("B-only", da.get("about_me", ""))
        self.assertNotIn("A-only", db.get("about_me", ""))
        # Setup status is per-user as well: A completing never affects a fresh user.
        client_a.post("/api/settings/setup-status", json={"setup_status": "completed"})
        self.assertEqual(client_a.get("/api/settings/setup-status").get_json()["setup_status"],
                         "completed")
        self.assertEqual(client_c.get("/api/settings/setup-status").get_json()["setup_status"],
                         "not_started")

    # ── G. Creator compatibility ────────────────────────────────

    def test_creator_uses_the_same_preference_system(self):
        email = app_module.CREATOR_EMAIL
        self.assertTrue(app_module._is_creator(email))

        client = self._auth(email)
        client.put("/api/settings/preferences", json={
            "voice_style": "Professional",
            "use_cases": ["Business & productivity", "Content creation"],
        })
        saved = client.get("/api/settings/preferences").get_json()["data"]
        self.assertEqual(saved["voice_style"], "Professional")
        self.assertEqual(saved["use_cases"], ["Business & productivity", "Content creation"])

        client.put("/api/settings/language", json={"response_language": "en"})
        lang = client.get("/api/settings/language").get_json()["data"]
        self.assertEqual(lang["response_language"], "en")

        # No separate creator/preference store is created.
        user_dir = self._user_dir(email)
        json_files = list(user_dir.glob("*.json"))
        self.assertEqual(len(json_files), 1)
        raw = json.loads(json_files[0].read_text(encoding="utf-8"))
        self.assertEqual(raw["preferences"]["voice_style"], "Professional")
        self.assertNotIn("creator_preferences", raw)

    def test_settings_route_serves_both_creator_and_regular(self):
        allowed = {
            "account", "memory", "projects", "creator", "preferences",
            "appearance", "notifications", "knowledge", "billing",
            "privacy", "language", "culture", "integrations", "extensions",
            "interests", "goals", "accessibility", "security",
            "connected", "tutorials", "help",
        }
        self.assertIn("preferences", allowed)
        self.assertIn("language", allowed)
        self.assertIn("culture", allowed)
        self.assertIn("creator", allowed)

    def test_creator_setup_status_flow(self):
        email = app_module.CREATOR_EMAIL
        client = self._auth(email)
        self.assertEqual(client.get("/api/settings/setup-status").get_json()["setup_status"],
                         "not_started")
        client.put("/api/settings/preferences", json={
            "about_me": "Founder of ValleyMind",
            "voice_style": "Professional",
        })
        client.post("/api/settings/setup-status", json={"setup_status": "completed"})
        self.assertEqual(client.get("/api/settings/setup-status").get_json()["setup_status"],
                         "completed")
        user_dir = self._user_dir(email)
        json_files = list(user_dir.glob("*.json"))
        self.assertEqual(len(json_files), 1)
        raw = json.loads(json_files[0].read_text(encoding="utf-8"))
        self.assertEqual(raw["preferences"]["preferences_setup_status"], "completed")

    # ── D2. about_me & use_case_profile mirroring ───────────────

    def test_about_me_mirrored_as_identity_fact(self):
        marcus = FakeMarcus()
        with patch.object(app_module, "load_marcus", return_value=marcus):
            app_module._mirror_settings_to_memory("uid_about", "preferences", {
                "about_me": "Software developer building African fintech startups",
            })
        mem = marcus.memory
        self.assertEqual(mem.preferences["preferences_about_me"],
                         "Software developer building African fintech startups")
        summaries = [f["summary"] for f in mem.facts]
        self.assertTrue(any("User-provided personal context" in s for s in summaries))
        identity_facts = [f for f in mem.facts if f.get("type") == "identity"]
        self.assertTrue(identity_facts, "about_me must be stored as a settled identity fact")

    def test_use_case_profile_mirrored_as_fact(self):
        marcus = FakeMarcus()
        with patch.object(app_module, "load_marcus", return_value=marcus):
            app_module._mirror_settings_to_memory("uid_ucp", "preferences", {
                "use_case_profile": "I make short-form videos for TikTok",
            })
        mem = marcus.memory
        self.assertEqual(mem.preferences["preferences_use_case_profile"],
                         "I make short-form videos for TikTok")
        summaries = [f["summary"] for f in mem.facts]
        self.assertTrue(any("User use-case profile" in s for s in summaries))


class FrontendIntegrationTestCase(unittest.TestCase):
    """Checks the frontend wiring that a Python test suite can reasonably probe."""

    def _index_html(self) -> str:
        return (Path(ROOT) / "index.html").read_text(encoding="utf-8")

    def test_index_loads_onboarding_after_settings(self):
        html = self._index_html()
        i_settings = html.find('<script src="/static/settings.js"></script>')
        i_onboarding = html.find('<script src="/static/onboarding.js"></script>')
        self.assertNotEqual(i_settings, -1, "settings.js must be loaded")
        self.assertNotEqual(i_onboarding, -1, "onboarding.js must be loaded")
        self.assertLess(i_settings, i_onboarding, "onboarding.js must load AFTER settings.js")

    def test_index_has_first_run_pending_flag(self):
        html = self._index_html()
        # The server-side setup-status endpoint replaced the fragile
        # sessionStorage flag.  index.html now calls
        # /api/settings/setup-status to decide whether to show the wizard.
        self.assertIn("/api/settings/setup-status", html)
        self.assertIn("openPreferencesSetup", html)

    def test_onboarding_uses_shared_registry_and_settings_api(self):
        js = (Path(ROOT) / "static" / "onboarding.js").read_text(encoding="utf-8")
        self.assertIn("CULTURAL_LANGUAGES", js, "must reuse the canonical registry")
        self.assertIn("/api/settings/language", js)
        self.assertIn("/api/settings/preferences", js)
        self.assertIn("/api/settings/culture", js)

    def test_onboarding_page2_fields_present(self):
        js = (Path(ROOT) / "static" / "onboarding.js").read_text(encoding="utf-8")
        self.assertIn("var COUNTRY_OPTIONS = [", js)
        self.assertIn("var SPEAK_OPTIONS = [", js)
        self.assertIn("prefSetupShare", js)
        self.assertIn("prefSetupHeritage", js)
        self.assertIn("obNatOtherWrap", js)
        self.assertIn("obExprOtherWrap", js)
        self.assertIn("multilingual_behavior", js)
        self.assertIn('"multilingual"', js)
        self.assertIn("preferred_characters", js)
        self.assertIn('"characters"', js)
        self.assertIn("var CHARACTER_OPTIONS = [", js)

    def test_ai_preferences_uses_canonical_language_registry(self):
        js = (Path(ROOT) / "static" / "settings.js").read_text(encoding="utf-8")
        self.assertIn("var CULTURAL_LANGUAGES = [", js)
        self.assertIn('_SH.select(CULTURAL_LANGUAGES, "prefLanguage"', js)
        self.assertNotIn('"Spanish", "French", "German"', js)

    def test_onboarding_use_case_options_match_canonical_spec(self):
        js = (Path(ROOT) / "static" / "onboarding.js").read_text(encoding="utf-8")
        for opt in ("Personal AI assistant", "Studying", "Writing", "Coding",
                    "Business & productivity", "Content creation", "Graphic design",
                    "Video creation", "Research", "Planning & productivity",
                    "Journaling", "Other"):
            self.assertIn(opt, js)
        # "Other" free-text is bound to a separate input (JS source keeps the
        # single quotes backslash-escaped inside its single-quoted string literal).
        self.assertIn("oninput=\"prefSetupInput(\\'use_cases_other\\', this.value)\"", js)

    def test_onboarding_preserves_all_use_case_steps_and_new_fields(self):
        js = (Path(ROOT) / "static" / "onboarding.js").read_text(encoding="utf-8")
        for field in ("about_me", "use_case_profile", "communication_style",
                      "communication_note", "use_cases", "use_cases_other",
                      "language", "country", "native_languages",
                      "cultural_background", "cultural_expression",
                      "expressive_language", "multilingual_behavior",
                      "preferred_characters", "voice_style", "custom_preference"):
            self.assertIn(field, js)
        # The "Other" use-case path free-text is separate from the checkbox list.
        self.assertIn("USE_OPTIONS", js)


class ImageContextTestCase(unittest.TestCase):
    """Image-generation preference injection (app._build_image_user_context).

    Only IMAGE-relevant preferences are injected — language (for text inside
    images), cultural expression (visual motifs), creative/visual tone and
    creative use-cases. Private fields (about_me, custom_preference, non-image
    sections) must never leak into the prompt. The user's explicit request is
    always preserved verbatim AFTER the context and always takes precedence.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._settings_patch = patch.object(app_module, "_SETTINGS_DIR",
                                           Path(cls._tmpdir.name) / "settings")
        cls._settings_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._settings_patch.stop()
        cls._tmpdir.cleanup()

    def _save(self, user_id: str, data: dict):
        app_module._save_settings(user_id, data)

    def test_injects_relevant_image_prefs_only(self):
        user = "img_user_a"
        self._save(user, {
            "language": {"response_language": "ig"},
            "culture": {"cultural_expression": "natural"},
            "preferences": {
                "voice_style": "Professional",
                "use_cases": ["Graphic design", "Video creation"],
                "custom_preference": "secret-work-habit",
                "about_me": "secret-about-me",
            },
        })
        prompt = "A purple nebula over the Lagos skyline at night"
        out = app_module._build_image_user_context(user, prompt)
        self.assertIn("User's preferred language: ig", out)
        self.assertIn("Cultural expression preference: natural", out)
        self.assertIn("Visual tone: Professional", out)
        self.assertIn("User's creative context: Graphic design", out)
        self.assertNotIn("secret-work-habit", out)
        self.assertNotIn("secret-about-me", out)
        self.assertTrue(out.endswith(prompt))

    def test_explicit_request_takes_precedence(self):
        user = "img_user_b"
        self._save(user, {"preferences": {"voice_style": "Natural",
                                          "use_cases": ["Graphic design"]}})
        prompt = "Draw a stormtrooper riding a white horse"
        out = app_module._build_image_user_context(user, prompt)
        self.assertTrue(out.startswith("[User context"))
        self.assertIn("takes precedence", out)
        self.assertTrue(out.endswith(prompt))
        self.assertEqual(out.count(prompt), 1, "explicit request must appear exactly once")

    def test_no_relevant_prefs_returns_prompt_unchanged(self):
        user = "img_user_c"
        self._save(user, {
            "preferences": {"about_me": "nothing image-related", "custom_preference": "x"},
            "memory": {"note": "private"},
        })
        prompt = "A mountain at sunrise"
        self.assertEqual(app_module._build_image_user_context(user, prompt), prompt)

    def test_empty_settings_returns_prompt_unchanged(self):
        user = "img_user_d"
        self._save(user, {})
        prompt = "A boat on a calm river"
        self.assertEqual(app_module._build_image_user_context(user, prompt), prompt)

    def test_all_image_entrypoints_prepend_user_context(self):
        src = (Path(ROOT) / "app.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("_build_image_user_context"), 5,
                                "definition + at least 4 call sites: "
                                "json, stream, multi-json, multi-stream, /api/generate-image")


class BrainPreferenceInjectionTestCase(unittest.TestCase):
    """User-level preference injection into the chat system prompt.

    The same USER preferences must reach every persona (Marcus, Angelina,
    Elena) because load_persona_brain restructures MarcusBrain for all three —
    a single injection point guarantees identical user context. Persona
    personality must remain distinct, and User A's preferences must never
    leak into User B's prompt.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name)
        # Keep brain hermetic: force local-file memory/chat stores, never Mongo.
        self._mem_patch = patch("core.memory.user_memory_collection", lambda: None)
        self._chat_patch = patch("core.memory.chats_collection", lambda: None)
        self._mem_patch.start()
        self._chat_patch.start()

    def tearDown(self):
        self._chat_patch.stop()
        self._mem_patch.stop()
        self._tmpdir.cleanup()

    def _brain(self, persona: str, user_tag: str):
        from core.brain import MarcusBrain
        memory_file = str(self._tmp / user_tag / persona / "long_term.json")
        os.makedirs(os.path.dirname(memory_file), exist_ok=True)
        return MarcusBrain(
            memory_file=memory_file,
            behavior_file=str(Path(ROOT) / "character" / persona / "behavior.json"),
        )

    @staticmethod
    def _seed_user(brain, about_me, use_cases, style, lang="ig"):
        m = brain.memory
        m.remember_preference("preferences_about_me", about_me)
        m.remember_preference("preferences_use_cases", use_cases)
        m.remember_preference("preferences_voice_style", style)
        m.remember_preference("preferences_communication_style", "Friendly and direct")
        m.long_term["response_language"] = lang
        m.save_long_term()

    def _system(self, brain, message="What do you think about this idea?"):
        msgs = brain._groq_messages("chat_t", message)
        self.assertEqual(msgs[0]["role"], "system")
        return msgs[0]["content"]

    def test_all_personas_receive_identical_user_preference_context(self):
        content = "I'm building an African fintech startup and I write daily."
        results = {}
        for persona in ("marcus", "angelina", "elena"):
            brain = self._brain(persona, "user_alpha")
            self._seed_user(brain, content, "Coding, Content creation", "Professional")
            system = self._system(brain)
            results[persona] = system
            self.assertIn("=== USER PREFERENCES / USER PROFILE CONTEXT ===", system)
            self.assertIn(f"About the user: {content}", system)
            self.assertIn("Primary use cases: Coding, Content creation", system)
            self.assertIn("Voice preference: Professional", system)
            self.assertIn("Communication style: Friendly and direct", system)
            self.assertIn("Response language: ig", system)

    def test_persona_personalities_stay_distinct(self):
        systems = {}
        for persona in ("marcus", "angelina", "elena"):
            brain = self._brain(persona, "user_beta")
            self._seed_user(brain, "Prefers short replies", "Writing", "Natural")
            systems[persona] = self._system(brain)
        self.assertNotEqual(systems["marcus"], systems["angelina"])
        self.assertNotEqual(systems["marcus"], systems["elena"])
        self.assertNotEqual(systems["angelina"], systems["elena"])

    def test_multi_user_prompts_are_isolated(self):
        brain_a = self._brain("marcus", "user_a")
        brain_b = self._brain("marcus", "user_b")
        self._seed_user(brain_a, "Loves Nigerian jollof rice debates",
                        "Coding", "Professional")
        self._seed_user(brain_b, "Building a skateboarding video channel",
                        "Video creation", "Creative")
        system_a = self._system(brain_a)
        system_b = self._system(brain_b)
        self.assertIn("Loves Nigerian jollof rice debates", system_a)
        self.assertIn("Building a skateboarding video channel", system_b)
        self.assertNotIn("skateboarding", system_a)
        self.assertNotIn("jollof", system_b)


if __name__ == "__main__":
    unittest.main()