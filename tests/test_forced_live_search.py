"""Direct factual questions must always reach the real live-search pipeline.

The screenshots showed Marcus answering "I don't have a confirmed list of 2026
movies" without ever searching. The LLM research classifier alone cannot be
trusted for that; a deterministic gate forces intent="search" for current,
factual and list questions in BOTH respond paths.

Run with:
  env311\\Scripts\\python.exe -m pytest tests/test_forced_live_search.py -q
"""
import unittest
from pathlib import Path

from core.brain import (
    _is_conversational_opinion_question,
    _needs_explicit_live_search,
    _needs_forced_live_search,
    _resolve_explicit_live_search,
    strip_animation_tags_preserve_space,
)
from core.router import _active_topic_from_history

ROOT = Path(__file__).resolve().parent.parent
BRAIN = (ROOT / "core" / "brain.py").read_text(encoding="utf-8")


class ForcedLiveSearchTests(unittest.TestCase):
    def test_current_and_factual_questions_are_forced(self):
        for message in (
            "name 10 movies released in 2026 and where to watch them",
            "what are the latest movie titles in 2026?",
            "list the current top AI models",
            "what news happened today",
            "new anime releases this year",
            "recent phone releases and their prices",
            "what are the best restaurants in Lagos right now",
            "which movies came out in 2025",
        ):
            with self.subTest(message=message):
                self.assertTrue(
                    _needs_forced_live_search(message),
                    f"should force live search: {message}",
                )

    def test_chat_and_non_factual_requests_are_not_forced(self):
        for message in (
            "",
            "how are you?",
            "thanks",
            "good morning",
            "who are you?",
            "write a python function that sorts a list",
            "tell me a story about a lion",
            "remember that I like jazz",
            "what is 2 + 2",
        ):
            with self.subTest(message=message):
                self.assertFalse(
                    _needs_forced_live_search(message),
                    f"should not force live search: {message}",
                )

    def test_both_respond_paths_force_search(self):
        # One gate on the prompt builder, one in each of the two respond paths.
        self.assertGreaterEqual(BRAIN.count("_needs_forced_live_search("), 3)
        self.assertGreaterEqual(BRAIN.count("forced_live_search = _needs_forced_live_search(message)"), 2)
        self.assertGreaterEqual(
            BRAIN.count("if forced_live_search"), 2,
            "both respond paths must force intent='search'",
        )
        self.assertGreaterEqual(BRAIN.count('intent = "search"'), 2)

    def test_reply_mode_is_resolved_before_forced_search_uses_it(self):
        # Regression: the stream path used `reply_mode` before assigning it.
        reply_assign = BRAIN.index("            reply_mode = self._reply_mode\n            forced_live_search =")
        self.assertGreater(reply_assign, 0)

    def test_direct_answer_prompt_directive_exists(self):
        self.assertIn("DIRECT ANSWER MODE: This is a current/factual question", BRAIN)
        # This directive is split across adjacent Python string literals.
        self.assertIn("Never reply 'I don't have a ", BRAIN)
        self.assertIn("confirmed list', 'I don't know'", BRAIN)
        self.assertIn("legal watch/download sources", BRAIN)

    def test_failed_forced_search_softened_in_stream_path(self):
        self.assertIn("Live web search was attempted for this direct factual question", BRAIN)
        self.assertIn("Never open with 'I don't know' or 'I don't have a confirmed list'", BRAIN)

    def test_card_syntax_alias_documented_for_the_model(self):
        self.assertIn("renders identically to [[ENTITY: ...]]", BRAIN)


class SSEDeltaSpacingTests(unittest.TestCase):
    """Wire-tag stripping must never delete a whitespace-only SSE delta."""

    def test_whitespace_deltas_survive_verbatim(self):
        for delta in (" ", "  ", "\t", "\n", " \n ", "to"):
            with self.subTest(delta=delta):
                self.assertEqual(strip_animation_tags_preserve_space(delta), delta)

    def test_only_the_tag_is_removed(self):
        self.assertEqual(
            strip_animation_tags_preserve_space("Hello  [ANIM: wave] world "),
            "Hello   world ",
        )
        self.assertEqual(
            strip_animation_tags_preserve_space("[EMO: happy] ok"),
            " ok",
        )

    def test_stream_of_deltas_keeps_word_boundaries(self):
        deltas = ["to", " ", "illegal", " ", "for", " ", "offline"]
        self.assertEqual("".join(strip_animation_tags_preserve_space(d) for d in deltas),
                         "to illegal for offline")

    def test_stream_loop_uses_the_preserving_stripper(self):
        self.assertIn("anim_tail = strip_animation_tags_preserve_space(anim_tail + yielded)", BRAIN)
        self.assertNotIn("anim_tail = strip_animation_tags(anim_tail + yielded)", BRAIN)


class ExplicitDirectiveAndOpinionTests(unittest.TestCase):
    def test_bare_directives_use_the_active_conversation_entity(self):
        history = [
            {"role": "user", "content": "Tell me about The Umbrella Academy Season 4 Jennifer"},
            {"role": "assistant", "content": "Here is what I found."},
        ]
        active_topic = _active_topic_from_history(history)
        self.assertIn("Umbrella Academy", active_topic)
        for message in ("search online", "check online", "look it up"):
            with self.subTest(message=message):
                self.assertTrue(_needs_explicit_live_search(message))
                self.assertEqual(_resolve_explicit_live_search(message, active_topic), active_topic)

    def test_personal_opinion_asks_are_not_treated_as_fresh_research(self):
        for message in ("what's your favorite part?", "Which scene did you like most?"):
            with self.subTest(message=message):
                self.assertTrue(_is_conversational_opinion_question(message))
                self.assertFalse(_needs_explicit_live_search(message))
        self.assertIn("PERSONAL OPINION MODE", BRAIN)
        self.assertIn("Do not introduce dates, freshness checks", BRAIN)
        self.assertEqual(BRAIN.count("intent = \"none\""), 2)


if __name__ == "__main__":
    unittest.main()
