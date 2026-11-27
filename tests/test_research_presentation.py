"""Research presentation regressions: one cohesive answer, clean evidence,
horizontal media cards, and topic-matched follow-up chips.

Run: env311\\Scripts\\python.exe -m pytest tests/test_research_presentation.py -q
"""
from __future__ import annotations

import unittest
from pathlib import Path

from core.brain import _clean_research_artifacts, _reply_has_closing_followup

ROOT = Path(__file__).resolve().parent.parent
BRAIN = (ROOT / "core" / "brain.py").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
SETTINGS = (ROOT / "static" / "settings.js").read_text(encoding="utf-8")
RICH = (ROOT / "static" / "vm_richrender.js").read_text(encoding="utf-8")


class ResearchArtifactCleanupTests(unittest.TestCase):
    def test_garbled_citation_note_is_removed_and_spacing_repaired(self):
        cleaned = _clean_research_artifacts(
            'The film earned 151 milliongross(thearticlecites"151"and"$60") worldwide.'
        )
        self.assertNotIn("thearticlecites", cleaned)
        self.assertNotIn('and"$60"', cleaned)
        self.assertIn("151 million gross", cleaned)

    def test_bracket_dump_becomes_separate_clean_pills(self):
        self.assertEqual(
            _clean_research_artifacts("Sources [imdb.com; wikipedia.org] agree."),
            "Sources [imdb.com] [wikipedia.org] agree.",
        )

    def test_math_wrapped_currency_is_not_turned_into_math(self):
        self.assertEqual(_clean_research_artifacts("It cost $60$"), "It cost $60")

    def test_ordinary_currency_and_citations_survive(self):
        original = "Tickets cost $60 [imdb.com] and the budget was $151 million [wikipedia.org]."
        self.assertEqual(_clean_research_artifacts(original), original)


class SingleCohesiveAnswerTests(unittest.TestCase):
    def test_closing_followup_detection(self):
        self.assertTrue(_reply_has_closing_followup("Here is what I found. Want to explore the cast?"))
        self.assertTrue(_reply_has_closing_followup("Verdict: worth watching.\nWhich scenes should I unpack?"))
        self.assertFalse(_reply_has_closing_followup(
            "Want to explore the cast? Anyway, here is a second long essay with more findings."
        ))

    def test_auto_continue_stops_when_reply_already_closed(self):
        self.assertIn("not _already_closed", BRAIN)
        self.assertIn("_reply_has_closing_followup(full_reply)", BRAIN)
        self.assertIn("auto-continuing", BRAIN)

    def test_only_one_final_reply_event_per_turn(self):
        self.assertGreaterEqual(APP.count("final_reply_emitted = False"), 3)
        self.assertGreaterEqual(APP.count('if "reply" in token:'), 3)
        self.assertIn('if _reply_corrected:', BRAIN)
        self.assertIn('yield {"reply": full_reply}', BRAIN)

    def test_rich_research_prompt_rules(self):
        for phrase in (
            "RESEARCH CONVERSATION & EVIDENCE",
            "Write ONE single cohesive response",
            "Never invent scenes, props, dialogue, timestamps, dates, figures or sources",
            "Maintain continuity across turns",
            "strictly as the final sentence",
            "Never emit raw bracket dumps",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, BRAIN)


class ResearchMediaLayoutTests(unittest.TestCase):
    def test_broken_strip_and_carousel_are_gone(self):
        self.assertNotIn("Side-by-side comparison", HTML)
        self.assertNotIn("overflowX:auto", HTML)
        self.assertNotIn("flexDirection:row", HTML)

    def test_horizontal_card_dimensions_and_dividers(self):
        self.assertIn("vm-media-card", HTML)
        self.assertIn("flex:0 0 118px", HTML)
        self.assertIn("aspect-ratio:2/3", HTML)
        self.assertIn("aspect-ratio:16/9", HTML)
        self.assertIn("object-fit:cover", HTML)
        self.assertIn("1px solid rgba(255,255,255,0.08)", HTML)
        self.assertIn("gap:22px", HTML)

    def test_overflow_guards(self):
        self.assertIn("#messages, .chat-messages{ overflow-x:hidden; }", HTML)
        self.assertIn("max-width:100%; box-sizing:border-box", HTML)
        self.assertNotIn(".bubble.ai-rich .vm-research-card{ min-width:140px; max-width:none; }", HTML)

    def test_fallback_lightbox_and_original_urls(self):
        self.assertIn('className = "vm-media-fallback"', HTML)
        self.assertIn("openImageViewer(s.image)", HTML)
        self.assertIn("img.src = s.image;", HTML)
        self.assertIn("source.href = s.url;", HTML)


class ContextAwareChipTests(unittest.TestCase):
    def test_no_shopping_boilerplate(self):
        self.assertNotIn('label: "Check current pricing"', HTML)
        self.assertIn('label: "Explore the cast"', HTML)
        self.assertIn('label: "Find the trailer"', HTML)
        self.assertIn('label: "Ask about specific scenes"', HTML)

    def test_citation_dumps_are_split_in_both_renderers(self):
        self.assertIn("raw.split(/\\s*[;,|]\\s+/)", HTML)
        self.assertIn("function citationLabels(label)", SETTINGS)
        self.assertIn("cleanArtifactText", RICH)


if __name__ == "__main__":
    unittest.main()
