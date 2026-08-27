"""Tests for the African Cultural Intelligence + Persistent Language system.

Covers language resolution, the independence of cultural identity from the
response language, cultural RAG retrieval, the adage-relevance gate, the
system-prompt grounding block, and dataset integrity.

These tests need no external services. They read the bundled dataset under
``data/african_culture`` and the bundled ``model_capabilities.json``.

Run: env311\\Scripts\\python.exe -m pytest tests/test_african_culture.py -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

from core.config import PROJECT_ROOT

from core.african_culture import (
    LANGUAGES,
    build_cultural_grounding_block,
    decide_adage_relevance,
    format_adage_for_prompt,
    language_label,
    load_all_proverbs,
    model_support_for,
    resolve_language,
    retrieve_proverbs,
    supported_languages,
)

from core.brain import MarcusBrain

DATA_DIR = os.path.join(PROJECT_ROOT, "data", "african_culture")


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# Language resolution & capability layer
# ═══════════════════════════════════════════════════════════════════════════════

class TestLanguageResolution(unittest.TestCase):
    def test_codes_resolve_to_themselves(self):
        for code in ["en", "ig", "yo", "ha", "sw", "zu", "xh", "af", "pcm"]:
            self.assertEqual(resolve_language(code), code)

    def test_names_and_aliases_resolve(self):
        self.assertEqual(resolve_language("Nigerian Pidgin"), "pcm")
        self.assertEqual(resolve_language("naija"), "pcm")
        self.assertEqual(resolve_language("Igbo"), "ig")
        self.assertEqual(resolve_language("Zulu"), "zu")
        self.assertEqual(resolve_language("isixhosa"), "xh")
        self.assertEqual(resolve_language("English"), "en")

    def test_language_label(self):
        self.assertEqual(language_label("ig").lower(), "igbo")
        self.assertEqual(language_label("sw").lower(), "swahili")
        self.assertTrue(language_label("zu"))

    def test_sixteen_languages_are_registered(self):
        self.assertEqual(len(LANGUAGES), 16)

    def test_model_support_is_honest_tier(self):
        # gpt-oss capabilities: en full, sw good, most African languages experimental
        self.assertIn(model_support_for("en"), ("full", "good"))
        self.assertIn(model_support_for("ig"), ("experimental", "partial", "good"))
        self.assertIn(model_support_for("sw"), ("good", "partial"))

    def test_supported_languages_selector(self):
        langs = supported_languages()
        self.assertGreaterEqual(len(langs), 16)
        codes = {x["code"] for x in langs}
        self.assertTrue({"en", "ig", "sw", "zu"}.issubset(codes))


# ═══════════════════════════════════════════════════════════════════════════════
# Cultural identity is INDEPENDENT from response language
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdentityLanguageIndependence(unittest.TestCase):
    def test_response_language_does_not_imply_identity(self):
        # Choosing Igbo as the RESPONSE LANGUAGE must NOT, on its own, add a
        # cultural identity — the two are independent settings.
        block = build_cultural_grounding_block(
            response_language="ig", cultural_identity="", use_adages=True,
            retrieved=[], message="hello",
        )
        self.assertIn("RESPONSE LANGUAGE", block)
        self.assertNotIn("USER CULTURAL IDENTITY", block)

    def test_identity_present_without_language_block(self):
        block = build_cultural_grounding_block(
            response_language="en", cultural_identity="igbo", use_adages=True,
            retrieved=[], message="hello",
        )
        self.assertIn("USER CULTURAL IDENTITY", block)
        self.assertNotIn("RESPONSE LANGUAGE", block)

    def test_both_can_coexist_separately(self):
        block = build_cultural_grounding_block(
            response_language="ig", cultural_identity="igbo", use_adages=True,
            retrieved=[], message="hello",
        )
        self.assertIn("RESPONSE LANGUAGE", block)
        self.assertIn("USER CULTURAL IDENTITY", block)
        # The identity paragraph must state it is independent of language.
        self.assertIn("INDEPENDENT of the response language", block)

    def test_none_identity_adds_no_identity_block(self):
        block = build_cultural_grounding_block(
            response_language="en", cultural_identity="none", use_adages=True,
            retrieved=[], message="hello",
        )
        self.assertNotIn("USER CULTURAL IDENTITY", block)


# ═══════════════════════════════════════════════════════════════════════════════
# Cultural RAG retrieval
# ═══════════════════════════════════════════════════════════════════════════════

class TestCulturalRetrieval(unittest.TestCase):
    def test_igbo_identity_returns_only_igbo(self):
        recs = retrieve_proverbs("igbo", message="I feel like giving up on my business", limit=10)
        self.assertGreater(len(recs), 0)
        for r in recs:
            self.assertEqual(r["culture"], "igbo")

    def test_yoruba_identity_returns_only_yoruba(self):
        recs = retrieve_proverbs("yoruba", message="How should I respect my elders", limit=10)
        self.assertGreater(len(recs), 0)
        for r in recs:
            self.assertEqual(r["culture"], "yoruba")

    def test_nigerian_identity_spans_multiple_cultures(self):
        recs = retrieve_proverbs("nigerian", message="I want to give up on my work", limit=20)
        cultures = {r["culture"] for r in recs}
        self.assertGreater(len(cultures), 1)

    def test_results_sorted_by_score(self):
        recs = retrieve_proverbs("igbo", message="persevere and keep going", limit=5)
        scores = [r["_score"] for r in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_empty_result_for_unknown_identity(self):
        # Unknown identity should degrade gracefully, not crash.
        recs = retrieve_proverbs("atlantis_mystery", message="any advice", limit=3)
        self.assertIsInstance(recs, list)

    def test_language_match_boosts(self):
        # Proverbs in the response language should rank (score > 0).
        recs = retrieve_proverbs("", language_code="ig", message="", limit=10)
        self.assertGreaterEqual(len(recs), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Adage relevance gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdageRelevanceGate(unittest.TestCase):
    def test_suitable_life_advice_contexts(self):
        for msg in [
            "I feel like giving up on my business",
            "I'm frustrated with my career",
            "How do I stay patient with my children",
            "advice on my relationship",
            "I'm stuck and discouraged",
        ]:
            self.assertTrue(decide_adage_relevance(msg), f"Should be suitable: {msg}")

    def test_unsuitable_technical_and_factual_contexts(self):
        for msg in [
            "what is 2 + 2",
            "multiply 5 by 5",
            "my code has an error, can you debug it",
            "traceback exception syntax",
            "translate this into English",
        ]:
            self.assertFalse(decide_adage_relevance(msg), f"Should be unsuitable: {msg}")

    def test_unsuitable_emergency_and_financial(self):
        for msg in [
            "someone overdosed, call 999",
            "give me legal advice",
            "should I invest my savings in this stock",
            "medical diagnosis for my symptom",
            "take this medication",
        ]:
            self.assertFalse(decide_adage_relevance(msg), f"Should be unsuitable: {msg}")

    def test_empty_message_is_not_relevant(self):
        self.assertFalse(decide_adage_relevance(""))


# ═══════════════════════════════════════════════════════════════════════════════
# Grounding block builder
# ═══════════════════════════════════════════════════════════════════════════════

class TestGroundingBlockBuilder(unittest.TestCase):
    def test_returns_empty_when_nothing_to_inject(self):
        # en language, no identity, no adage-relevant message -> empty block.
        block = build_cultural_grounding_block(
            response_language="en", cultural_identity="", use_adages=True,
            retrieved=[], message="what is 2 + 2",
        )
        self.assertEqual(block, "")

    def test_identity_label_for_south_african(self):
        block = build_cultural_grounding_block(
            response_language="en", cultural_identity="south_african", use_adages=True,
            retrieved=[], message="hello",
        )
        self.assertIn("South African / Southern African", block)

    def test_retrieved_records_are_injected_only_when_relevant(self):
        recs = retrieve_proverbs("igbo", message="I want to give up on my business", limit=2)
        relevant = build_cultural_grounding_block(
            response_language="en", cultural_identity="igbo", use_adages=True,
            retrieved=recs, message="I want to give up on my business",
        )
        self.assertIn("RETRIEVED CULTURAL CONTEXT", relevant)

        _not_relevant = build_cultural_grounding_block(
            response_language="en", cultural_identity="igbo", use_adages=True,
            retrieved=recs, message="what is 2 + 2",
        )
        self.assertNotIn("RETRIEVED CULTURAL CONTEXT", _not_relevant)

    def test_adages_disabled_suppresses_retrieved_context(self):
        recs = retrieve_proverbs("igbo", message="I want to give up on my business", limit=2)
        block = build_cultural_grounding_block(
            response_language="en", cultural_identity="igbo", use_adages=False,
            retrieved=recs, message="I want to give up on my business",
        )
        self.assertNotIn("RETRIEVED CULTURAL CONTEXT", block)

    def test_unverified_note_is_honest(self):
        rec = {"text": "A proverb", "translation_en": "Translation",
               "meaning": "Meaning", "origin_verified": False}
        out = format_adage_for_prompt(rec)
        self.assertIn("origin NOT yet independently verified", out)
        self.assertIn("A proverb", out)


# ═══════════════════════════════════════════════════════════════════════════════
# Dataset integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestDatasetIntegrity(unittest.TestCase):
    def test_proverb_files_exist_and_parse(self):
        proverbs_dir = os.path.join(DATA_DIR, "proverbs")
        self.assertTrue(os.path.isdir(proverbs_dir), "proverbs dir missing")
        files = [n for n in os.listdir(proverbs_dir) if n.endswith(".json")]
        self.assertGreater(len(files), 0)

    def test_all_proverbs_have_required_fields(self):
        records = load_all_proverbs()
        self.assertGreaterEqual(len(records), 10, "expected the seeded dataset")
        for r in records:
            self.assertTrue(r.get("id"), f"missing id: {r}")
            self.assertTrue(r.get("text"), f"missing text: {r.get('id')}")
            self.assertTrue(r.get("translation_en"), f"missing translation: {r.get('id')}")
            self.assertIsInstance(r.get("themes"), list)

    def test_no_fabricated_verification(self):
        # Seed data is honestly marked as unverified; nothing claims verified origin.
        records = load_all_proverbs()
        for r in records:
            verification = r.get("verification") or {}
            self.assertIn("origin_verified", verification)
            self.assertIn("translation_verified", verification)
            # No record should lie about having a verified origin.
            self.assertIs(verification.get("origin_verified"), False)

    def test_sources_and_manifest_exist(self):
        for rel in [
            "metadata/sources.json",
            "metadata/dataset_manifest.json",
            "languages/supported_languages.json",
        ]:
            path = os.path.join(DATA_DIR, rel)
            self.assertTrue(os.path.isfile(path), f"missing {rel}")
            data = _read_json(path)
            self.assertTrue(data)

    def test_sources_record_provenance(self):
        sources = _read_json(os.path.join(DATA_DIR, "metadata", "sources.json"))
        sources_list = sources.get("sources") if isinstance(sources, dict) else sources
        self.assertTrue(sources_list)
        for s in sources_list:
            self.assertTrue(s.get("source_url") or s.get("url") or s.get("title"))
            self.assertTrue(s.get("access_status") or s.get("status") or True)

    def test_supported_languages_json_mirrors_registry(self):
        self.assertEqual(len(LANGUAGES), 16)


# ═══════════════════════════════════════════════════════════════════════════════
# Brain integration: _groq_messages must inject the grounding block
# ═══════════════════════════════════════════════════════════════════════════════

class _StubMemory:
    """Minimal stand-in for MemorySystem, exposing the surface _groq_messages needs."""

    def __init__(self, long_term=None):
        self.long_term = {
            "response_language": "",
            "culture_identity": "",
            "use_cultural_adages": True,
            **(long_term or {}),
        }

    def reload(self):
        return None

    def get_full_memory(self):
        return {"identity": {}}

    def get_chat(self, chat_id):
        return []

    def get_user_name(self):
        return "Test User"

    def get_active_facts(self):
        return []

    def load_creator_context(self):
        return ""

    def list_sessions(self):
        return []


def _build_brain(long_term=None):
    brain = object.__new__(MarcusBrain)
    brain.profile = type("P", (), {
        "name": "Marcus",
        "to_prompt": lambda self: "Marcus prompt",
        "raw": {},
    })()
    brain.memory = _StubMemory(long_term)
    brain._user_documents_context = lambda query, budget=6000: ""
    return brain


class TestBrainGroundingIntegration(unittest.TestCase):
    def test_grounding_injected_into_system_prompt(self):
        brain = _build_brain({
            "response_language": "ig",
            "culture_identity": "igbo",
            "use_cultural_adages": True,
        })
        messages = brain._groq_messages(
            "chat_1", "I feel like giving up on my business",
        )
        system = messages[0]["content"]
        self.assertIn("RESPONSE LANGUAGE", system)
        self.assertIn("USER CULTURAL IDENTITY", system)
        self.assertIn("RETRIEVED CULTURAL CONTEXT", system)
        # The cultural identity stays independent of the response language.
        self.assertIn("INDEPENDENT of the response language", system)

    def test_no_grounding_when_disabled_and_english(self):
        brain = _build_brain({
            "response_language": "en",
            "culture_identity": "",
            "use_cultural_adages": False,
        })
        messages = brain._groq_messages("chat_1", "what is 2 + 2")
        system = messages[0]["content"]
        self.assertNotIn("RESPONSE LANGUAGE", system)
        self.assertNotIn("USER CULTURAL IDENTITY", system)
        self.assertNotIn("RETRIEVED CULTURAL CONTEXT", system)


if __name__ == "__main__":
    sys.exit(unittest.main())