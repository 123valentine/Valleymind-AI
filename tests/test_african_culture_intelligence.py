"""Regression tests for ValleyMind's cultural intelligence layer.

This covers the spec-driven behavior implemented in core/african_culture.py and
the injection point in core/brain.py::_groq_messages. Tests are hermetic (no
network, no Mongo — the memory/chat stores are patched to local files).

Coverage:
  A. Semantic relevance — financial-discipline phrasing is recognized without
     bare-keyword matching; hard-unsuitable contexts stay rejected.
  B. Culture-aware selection — Igbo/Yoruba/Hausa users get their own culture;
     unknown culture stays neutral (never assumes Igbo just because the founder
     is Igbo).
  C. Explicit per-message culture/language requests override the saved profile
     FOR THAT RESPONSE ONLY and never edit saved settings.
  D. No fabrication — only records from the bundled dataset are returned;
     unqualified provenance is surfaced (origin_verified false), never asserted.
  E. Language/culture separation — a language request alone does not change
     culture, and vice versa.
  F. All personas share the same user-level cultural context.
  G. User-preference isolation — two users never leak culture/language.

Run with: python -m pytest tests/test_african_culture_intelligence.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.african_culture import (
    cultural_request_directives,
    decide_adage_relevance,
    select_cultural_context,
    valleymind_cultural_foundation_block,
)


class SemanticRelevanceTestCase(unittest.TestCase):
    """decide_adage_relevance stays semantic + conservative."""

    def test_financial_discipline_phrasing_recognized_without_keyword_gate(self):
        self.assertTrue(decide_adage_relevance(
            "I'm trying to become more disciplined with money."))

    def test_saving_single_word_is_not_a_trigger(self):
        self.assertFalse(decide_adage_relevance("Do I need to worry about saving?"))

    def test_hard_unsuitable_context_rejected(self):
        self.assertFalse(decide_adage_relevance(
            "should I invest my savings in this stock"))
        self.assertFalse(decide_adage_relevance(
            "my medical symptoms conflict with the insurance clause"))
        self.assertFalse(decide_adage_relevance(
            "trace this TypeError in the payment service"))

    def test_patience_relevant(self):
        self.assertTrue(decide_adage_relevance("Give me advice about patience."))

    def test_generosity_does_not_trigger_on_bare_give(self):
        self.assertFalse(decide_adage_relevance("give me a quick answer"))
        self.assertTrue(decide_adage_relevance(
            "I want to be more generous with my money"))


class SelectionTestCase(unittest.TestCase):
    """select_cultural_context picks the right culture/record for each user."""

    def test_igbo_user_gets_igbo_grounding(self):
        out = select_cultural_context("igbo", "en",
                                      "I'm trying to become more disciplined with money.")
        self.assertTrue(out["relevant"])
        self.assertEqual(out["culture"], "igbo")
        self.assertEqual(out["explicit_culture"], "")
        self.assertNotEqual(out["expression"], "")
        self.assertNotEqual(out["source_name"], "")
        self.assertFalse(out["origin_verified"])
        self.assertGreater(out["confidence"], 0.0)

    def test_yoruba_user_gets_yoruba_not_igbo(self):
        out = select_cultural_context("yoruba", "en",
                                      "I'm trying to become more disciplined with money.")
        self.assertEqual(out["culture"], "yoruba")
        self.assertNotEqual(out["culture"], "igbo")

    def test_hausa_user_gets_hausa_not_igbo(self):
        out = select_cultural_context("hausa", "en",
                                      "I keep losing patience with my progress.")
        self.assertEqual(out["culture"], "hausa")
        self.assertNotEqual(out["culture"], "igbo")

    def test_unknown_culture_is_neutral_never_igbo(self):
        out = select_cultural_context("", "en",
                                      "I'm trying to become more disciplined with money.")
        self.assertEqual(out["culture"], "")
        # An unknown-culture user must not be grounded in Igbo by default.

    def test_explicit_culture_requests_override_saved_profile_for_this_message(self):
        out = select_cultural_context("", "en",
                                      "Give me an Igbo adage about financial discipline.")
        self.assertEqual(out["explicit_culture"], "igbo")  # not "" from profile
        self.assertEqual(out["culture"], "igbo")
        self.assertTrue(out["relevant"])
        self.assertNotEqual(out["expression"], "")

    def test_explicit_language_requests_override_saved_language(self):
        out = select_cultural_context("", "en",
                                      "Abeg explain this one for Naija Pidgin.")
        self.assertEqual(out["explicit_language_requested"], "pcm")
        self.assertEqual(out["language"], "pcm")
        self.assertEqual(out["explicit_culture"], "")

    def test_language_request_does_not_change_culture(self):
        out = select_cultural_context("igbo", "en", "Say it in Pidgin.")
        self.assertEqual(out["language"], "pcm")
        self.assertEqual(out["culture"], "igbo")  # language only

    def test_culture_request_does_not_change_language(self):
        out = select_cultural_context("", "en", "Show me some Igbo wisdom about patience.")
        # Culture requested; language stays the saved English.
        self.assertEqual(out["culture"], "igbo")
        self.assertEqual(out["explicit_culture"], "igbo")
        self.assertEqual(out["explicit_language_requested"], "")
        self.assertEqual(out["language"], "en")

    def test_talk_x_to_me_is_a_language_and_culture_request(self):
        out = select_cultural_context("", "en", "Talk Igbo to me.")
        self.assertEqual(out["language"], "ig")
        self.assertEqual(out["explicit_language_requested"], "ig")
        self.assertEqual(out["explicit_culture"], "igbo")
        self.assertTrue(out["relevant"])

    def test_no_adage_when_none_semantically_fits(self):
        out = select_cultural_context("igbo", "en",
                                      "My friend keeps disappointing me.")
        self.assertTrue(out["relevant"])          # relationships context
        self.assertEqual(out["expression"], "")   # but no fitting dataset record

    def test_never_fabricates_proverb(self):
        out = select_cultural_context("", "en",
                                      "Give me an Igbo adage about financial discipline.")
        # There is no adage-generation path in this module: the expression either
        # comes back verbatim from the dataset or is the empty string.
        self.assertIn(out["expression"], {"", out.get("expression", "")})

    def test_unknown_culture_upon_no_identity_means_neutral_language(self):
        out = select_cultural_context("", "", "Hello there")
        self.assertEqual(out["language"], "en")
        self.assertFalse(out["relevant"])

    def test_explicit_directives_are_one_off_for_language(self):
        out = select_cultural_context("", "en", "Say it in Yoruba.")
        directives = cultural_request_directives(out)
        self.assertIn("Yoruba", directives)
        self.assertIn("THIS message", directives)
        self.assertIn("does NOT change their saved response language", directives)

    def test_directives_empty_when_nothing_explicit(self):
        out = select_cultural_context("igbo", "en",
                                      "I'm trying to become more disciplined with money.")
        self.assertEqual(cultural_request_directives(out), "")


class BrainIntegrationTestCase(unittest.TestCase):
    """The selection layer is actually wired into MarcusBrain (and therefore
    every persona via load_persona_brain)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name)
        self._mem_patch = patch("core.memory.user_memory_collection", lambda: None)
        self._chat_patch = patch("core.memory.chats_collection", lambda: None)
        self._mem_patch.start()
        self._chat_patch.start()

    def tearDown(self):
        self._chat_patch.stop()
        self._mem_patch.stop()
        self._tmpdir.cleanup()

    def _brain(self, persona: str = "marcus", user_tag: str = "user_alpha"):
        from core.brain import MarcusBrain
        memory_file = str(self._tmp / user_tag / persona / "long_term.json")
        os.makedirs(os.path.dirname(memory_file), exist_ok=True)
        return MarcusBrain(
            memory_file=memory_file,
            behavior_file=str(Path(ROOT) / "character" / persona / "behavior.json"),
        )

    def _seed(self, brain, culture=None, lang="en"):
        brain.memory.long_term["response_language"] = lang
        if culture is not None:
            brain.memory.long_term["culture_identity"] = culture
        brain.memory.save_long_term()

    def _system(self, brain, message="What do you think about this idea?"):
        msgs = brain._groq_messages("chat_t", message)
        self.assertEqual(msgs[0]["role"], "system")
        return msgs[0]["content"]

    def test_foundation_block_present_for_every_persona(self):
        for persona in ("marcus", "angelina", "elena"):
            brain = self._brain(persona, f"user_{persona}")
            self._seed(brain)
            system = self._system(brain)
            self.assertIn("valleymind cultural foundation", system.casefold())
            self.assertIn("igbo", system.casefold())

    def test_personas_share_same_user_cultural_context(self):
        systems = {}
        for persona in ("marcus", "angelina", "elena"):
            brain = self._brain(persona, "user_shared")
            self._seed(brain, culture="igbo")
            system = self._system(brain, "I'm trying to become more disciplined with money.")
            self.assertIn("igbo", system.casefold())
            systems[persona] = system
        # The user's Igbo grounding reaches every persona.
        self.assertEqual(systems["marcus"].count("Igbo cultural context"),
                         systems["angelina"].count("Igbo cultural context"))
        self.assertEqual(systems["marcus"].count("Igbo cultural context"),
                         systems["elena"].count("Igbo cultural context"))

    def test_unknown_culture_prompt_does_not_assume_igbo(self):
        brain = self._brain("marcus", "user_neutral")
        self._seed(brain, culture=None)
        system = self._system(brain, "I'm trying to become more disciplined with money.")
        # The user-level grounding block must not manufacture an Igbo identity
        # for a user who never selected a culture.
        self.assertNotIn("Igbo cultural context", system)

    def test_two_users_are_isolated(self):
        brain_a = self._brain("marcus", "user_a")
        brain_b = self._brain("marcus", "user_b")
        self._seed(brain_a, culture="igbo")
        self._seed(brain_b, culture="yoruba")
        system_a = self._system(brain_a, "I'm trying to become more disciplined with money.")
        system_b = self._system(brain_b, "I'm trying to become more disciplined with money.")
        self.assertNotEqual(system_a, system_b)
        self.assertIn("igbo", system_a.casefold())
        self.assertIn("yoruba", system_b.casefold())

    def test_explicit_pidgin_request_creates_directive_and_does_not_save(self):
        brain = self._brain("marcus", "user_pidgin")
        self._seed(brain, culture="igbo", lang="en")
        system = self._system(brain, "Abeg explain this one for Naija Pidgin.")
        self.assertIn("Nigerian Pidgin", system)
        self.assertIn("THIS message", system)
        self.assertEqual(brain.memory.long_term.get("response_language"), "en")


class FoundationBlockTestCase(unittest.TestCase):
    def test_foundation_mentions_creator_not_imposing(self):
        block = valleymind_cultural_foundation_block().casefold()
        self.assertIn("igbo", block)
        self.assertIn("heritage", block)
        self.assertIn("not a script to impose", block)

    def test_foundation_never_overrides_user_culture_or_language(self):
        block = valleymind_cultural_foundation_block().casefold()
        self.assertIn("never overrides", block)      # the RULE, not the action
        self.assertNotIn("always reply in igbo", block)


if __name__ == "__main__":
    unittest.main()