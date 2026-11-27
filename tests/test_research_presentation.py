"""Research presentation regressions: one cohesive answer, clean evidence,
horizontal media cards, and topic-matched follow-up chips.

Run: env311\\Scripts\\python.exe -m pytest tests/test_research_presentation.py -q
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from core.brain import (
    CHAT_MAX_COMPLETION_TOKENS,
    FALLBACK_RESPONSE,
    _clean_research_artifacts,
    _fast_model_candidates,
    _filter_query_images,
    _is_conversational_opinion_question,
    _is_reasoning_transient_failure,
    _needs_explicit_live_search,
    _reply_has_closing_followup,
    _resolve_explicit_live_search,
)
from core.router import _active_topic_from_history

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
        self.assertNotIn("flex:0 0 118px", HTML)
        self.assertIn("width:124px", HTML)
        self.assertIn("height:186px", HTML)
        self.assertIn("aspect-ratio:2/3", HTML)
        self.assertIn("vm-media-landscape", HTML)
        self.assertIn("width:208px", HTML)
        self.assertIn("height:117px", HTML)
        self.assertIn("aspect-ratio:16/9", HTML)
        self.assertIn("object-fit:contain", HTML)
        self.assertIn("1px solid rgba(255,255,255,0.08)", HTML)
        self.assertIn("gap:22px", HTML)
        self.assertIn("function VMResearchImageMetrics(", HTML)

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


class MultiItemCompletionTests(unittest.TestCase):
    def test_chat_token_budget_covers_multi_item_answers(self):
        self.assertGreaterEqual(CHAT_MAX_COMPLETION_TOKENS, 2400)
        self.assertGreaterEqual(BRAIN.count("CHAT_MAX_COMPLETION_TOKENS"), 10)

    def test_length_stops_are_continued_until_the_reply_closes(self):
        self.assertIn('if (_finish == "length" and _seg.strip() and not _already_closed', BRAIN)
        self.assertIn('yield {"stream_continue": True', BRAIN)
        self.assertIn("Continue exactly where you left off.", BRAIN)
        self.assertTrue(_reply_has_closing_followup(
            "I compared all four shows. Which cast should I unpack next?"
        ))

    def test_one_final_reply_event_per_assistant_turn(self):
        self.assertGreaterEqual(APP.count("final_reply_emitted = False"), 3)
        self.assertGreaterEqual(APP.count('if "reply" in token:'), 3)


class PerTurnSSEGuardTests(unittest.TestCase):
    def test_backend_tags_every_stream_dispatch(self):
        self.assertIn("def _sse_frame(", APP)
        self.assertIn("def _tag_sse_frames(", APP)
        self.assertGreaterEqual(APP.count("_sse_frame("), 12)
        self.assertIn('request_id = str(data.get("request_id")', APP)
        self.assertIn("attempt_id = str(data.get(\"attempt_id\")", APP)
        self.assertIn("_tag_sse_frames(\n            drain_sse_activities(activity_session)", APP)

    def test_frontend_rejects_stale_frames_and_wrappers(self):
        self.assertIn("window.VMTurnGuard", SETTINGS)
        self.assertIn("body.request_id = _vmTurnGuard.requestId", HTML)
        self.assertIn("body.turn_id = _vmTurnGuard.turnId", HTML)
        self.assertIn("body.attempt_id = _vmTurnGuard.attemptId", HTML)
        self.assertIn("!window.VMTurnGuard.accepts(_vmTurnGuard, evt)", HTML)
        self.assertIn("window.VMTurnGuard.retry", HTML)
        self.assertIn("dataset.vmTurnId", HTML)
        self.assertGreaterEqual(HTML.count("if (!_vmTurnIsActive()) return;"), 5)
        self.assertIn("function VMEnsureLiveAssistantBubble(thinkingEl, regenCtx, turnId)", HTML)


class ExplicitSearchAndOpinionTests(unittest.TestCase):
    def test_bare_search_directives_resolve_against_active_topic(self):
        history = [
            {"role": "user", "content": "Tell me about The Umbrella Academy Season 4 Jennifer"},
            {"role": "assistant", "content": "Here is what I found about the show."},
        ]
        topic = _active_topic_from_history(history)
        self.assertIn("Umbrella Academy", topic)
        for message in ("search online", "check online", "please search the web"):
            with self.subTest(message=message):
                self.assertTrue(_needs_explicit_live_search(message))
                self.assertEqual(_resolve_explicit_live_search(message, topic), topic)

    def test_conversational_opinion_questions_do_not_search(self):
        for message in (
            "what's your favorite part?",
            "Which scene did you like most?",
            "Do you prefer the finale?",
        ):
            with self.subTest(message=message):
                self.assertTrue(_is_conversational_opinion_question(message))
        self.assertFalse(_is_conversational_opinion_question("search online"))

    def test_opinion_prompt_and_routing_bypass_research(self):
        self.assertGreaterEqual(
            BRAIN.count("conversational_opinion = _is_conversational_opinion_question(message)"), 2
        )
        self.assertGreaterEqual(BRAIN.count("if conversational_opinion:"), 3)
        self.assertIn("PERSONAL OPINION MODE", BRAIN)
        self.assertIn("Never say you don't watch shows", BRAIN)
        self.assertIn("Do not introduce dates, freshness checks", BRAIN)


class OffTopicImageFilterTests(unittest.TestCase):
    def test_calendar_site_images_are_dropped_for_tv_research(self):
        active_topic = "The Umbrella Academy Season 4 Jennifer"
        query = "show me posters and images for The Umbrella Academy Season 4 Jennifer"
        items = [
            {
                "title": "The Umbrella Academy Season 4 Jennifer Poster",
                "domain": "imdb.com",
                "url": "https://www.imdb.com/title/tt123/",
                "image": "https://images.example/umbrella-poster.jpg",
                "description": "Poster for the show.",
            },
            {
                "title": "What is today's date?",
                "domain": "almanac.com",
                "url": "https://www.almanac.com/today",
                "image": "https://www.almanac.com/images/date-banner.jpg",
                "description": "Daily calendar facts.",
            },
            {
                "title": "What time is it?",
                "domain": "timeanddate.com",
                "url": "https://www.timeanddate.com/",
                "image": "https://www.timeanddate.com/img/clock.png",
                "description": "Live clock.",
            },
        ]
        filtered = _filter_query_images(items, query, active_topic=active_topic)
        self.assertEqual(filtered[0]["image"], "https://images.example/umbrella-poster.jpg")
        self.assertEqual(filtered[1]["image"], "")
        self.assertEqual(filtered[2]["image"], "")

    def test_all_filter_call_sites_pass_active_topic(self):
        self.assertGreaterEqual(BRAIN.count("active_topic=active_topic"), 5)


class TransientReasoningRetryTests(unittest.TestCase):
    def test_canonical_apology_is_recognized_and_fast_models_are_configurable(self):
        self.assertTrue(_is_reasoning_transient_failure(FALLBACK_RESPONSE))
        self.assertFalse(_is_reasoning_transient_failure("Here is the answer."))
        with patch.dict(os.environ, {"GROQ_FAST_MODELS": "custom-fast,primary-model"}):
            candidates = _fast_model_candidates("primary-model")
        self.assertEqual(candidates[0], "custom-fast")
        self.assertNotIn("primary-model", candidates)

    def test_stream_and_non_stream_paths_retry_before_apology(self):
        self.assertIn("_iter_transient_guarded_stream", BRAIN)
        self.assertIn('status.get("transient")', BRAIN)
        self.assertGreaterEqual(BRAIN.count("_fast_model_candidates("), 4)
        self.assertIn("Recovered transient failure via", BRAIN)


if __name__ == "__main__":
    unittest.main()
