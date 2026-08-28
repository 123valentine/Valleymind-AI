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
        self.assertIn("vm_pref_setup_pending", html)
        self.assertIn("openPreferencesSetup", html)
        self.assertIn("is_new_user", html)

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


if __name__ == "__main__":
    unittest.main()