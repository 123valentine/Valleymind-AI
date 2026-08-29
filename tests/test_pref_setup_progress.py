"""Preference-completion progress (+ navigation regression) tests.

Verifies the 14-step preference-completion model behind the circular progress
indicator and the navigation fix that was breaking Continue/Skip/Back.

Coverage (from the spec):
  * 0/14→0%, 1/14→7%, 2/14→14%, 7/14→50%, 13/14→93%, 14/14→100%, all DERIVED
    (nearest whole number), never a hard-coded page→percent mapping.
  * Live updates as the user interacts; ring number matches the model.
  * Continue/Skip/Back semantics + values preserved; reopen re-runs the flow.
  * Identity/Profile contributes to completion; 100% only when genuinely 14/14.
  * Index/static guarantees: pref_setup_model.js loads BEFORE onboarding.js;
    onboarding.js no longer hard-codes the step list or a linear page bar.
"""

import copy
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "_pref_setup_harness.js"
MODEL_RUNNER = ROOT / "tests" / "_pref_model_test.js"
NODE = shutil.which("node")


def run_harness(mode, state=None):
    env = dict(os.environ)
    if state is not None:
        env["PREF_STATE"] = json.dumps(state)
    proc = subprocess.run(
        [NODE, str(HARNESS), mode],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
        check=True,
    )
    return json.loads(proc.stdout)


FULL = {
    "setup_status": "not_started",
    "language": {"country": "Nigeria", "native_languages": ["Igbo"],
                 "cultural_background": "Igbo culture"},
    "culture": {"cultural_expression": "Natural"},
    "preferences": {
        "use_cases": ["Learning"], "use_case_profile": "Student",
        "communication_style": ["Direct"], "expressive_language": ["Playful"],
        "multilingual_behavior": "Always reply in my language",
        "preferred_characters": ["Marcus"], "voice_style": "Friendly",
        "custom_preference": "Keep it short", "about_me": "I code late at night",
    },
}


class PrefSetUpModelNodeTestCase(unittest.TestCase):
    """Pure-model assertions executed in Node (the single source of truth)."""

    @unittest.skipIf(NODE is None, "node is not available")
    def test_model_runner_passes(self):
        proc = subprocess.run(
            [NODE, str(MODEL_RUNNER)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        self.assertEqual(
            proc.returncode, 0,
            msg="model runner exited non-zero\nstdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr),
        )
        summary = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual(summary["failed"], [], summary)


class ProgressIndicatorHarnessTestCase(unittest.TestCase):
    """Real onboarding.js driven through its own global handlers in Node."""

    @unittest.skipIf(NODE is None, "node is not available")
    def test_navigation_continue_back_skip_reopen(self):
        out = run_harness("nav", {"setup_status": "not_started"})
        nav = out["nav"]
        # Continue → step 1 (both affordances appear).
        self.assertTrue(nav["step1"]["hasBack"], "Back appears after Continue")
        self.assertTrue(nav["step1"]["hasContinue"], "Continue still present")
        # Back → intro again (no Back affordance, Continue present).
        self.assertFalse(nav["back0"]["hasBack"], "Back gone back on intro")
        self.assertTrue(nav["back0"]["hasContinue"], "Continue on intro")
        # Skip closes the wizard…
        self.assertEqual(nav["afterSkipDisplay"], "none", "Skip hides the overlay")
        # …and reopening SHOWS it again (the regression fix).
        self.assertEqual(nav["reopenedDisplay"], "flex", "reopen re-displays the wizard")
        self.assertTrue(nav["reopenedHasRing"], "ring survives reopen")

    @unittest.skipIf(NODE is None, "node is not available")
    def test_ring_live_update_on_toggle(self):
        out = run_harness("live", {"setup_status": "not_started"})
        self.assertEqual(out["live"]["before"]["ringPct"], "0")
        self.assertEqual(out["live"]["after"]["ringPct"], "7",
                         "toggling a use case bumps the ring live (no re-render)")
        self.assertEqual(out["live"]["after"]["ringLabel"], "1 of 14 complete")

    @unittest.skipIf(NODE is None, "node is not available")
    def test_fresh_user_ring_zero(self):
        for status in ("not_started", "skipped"):
            out = run_harness("basic", {"setup_status": status})
            self.assertEqual(out["render"]["ringPct"], "0", "fresh user: 0%")
            self.assertEqual(out["render"]["ringLabel"], "0 of 14 complete")

    @unittest.skipIf(NODE is None, "node is not available")
    def test_ring_derives_from_persisted_state(self):
        cases = (
            ({"language": {"country": "Nigeria"}}, "7"),
            ({"language": {"country": "Nigeria"},
              "preferences": {"communication_style": ["Direct"]}}, "14"),
            (dict(FULL, setup_status="completed"), "100"),
        )
        for state, expected in cases:
            out = run_harness("basic", state)
            self.assertEqual(out["render"]["ringPct"], expected,
                             "state=%s" % json.dumps(state))

    @unittest.skipIf(NODE is None, "node is not available")
    def test_reopen_after_finish_keeps_100(self):
        # Finish persists setup_status=completed via the same endpoint; a fresh
        # open re-derives 100% from persisted sections (no stored percentage).
        out = run_harness("reopen-100", dict(FULL))
        self.assertEqual(out["final"]["ringPct"], "100")
        self.assertEqual(out["final"]["ringLabel"], "14 of 14 complete")

    @unittest.skipIf(NODE is None, "node is not available")
    def test_resume_after_skip_partial_keeps_position(self):
        # Partial progress (2/14), then Skip → reopening must land on the first
        # incomplete step (not intro, not a restart) and stay there after Skip.
        state = {"setup_status": "skipped",
                 "language": {"country": "Nigeria"},
                 "preferences": {"communication_style": ["Direct"]}}
        out = run_harness("resume", state)
        r = out["resume"]
        self.assertEqual(r["initial"]["ringPct"], "14")
        self.assertTrue(r["initial"]["hasBack"], "resume skips intro → first incomplete step")
        self.assertTrue(r["initial"]["hasContinue"], "Continue works on resume")
        self.assertFalse(r["initial"]["hasFinish"], "not at the review/completed page yet")
        self.assertEqual(r["afterSkipDisplay"], "none", "Skip closes the wizard")
        self.assertEqual(r["afterSkipReopen"], r["initial"], "Skip preserves the resume position")

    @unittest.skipIf(NODE is None, "node is not available")
    def test_resume_fresh_user_stays_on_page_one(self):
        out = run_harness("resume", {"setup_status": "skipped"})
        r = out["resume"]
        self.assertFalse(r["initial"]["hasBack"], "nothing completed → Page 1 (intro)")
        self.assertTrue(r["initial"]["hasContinue"], "intro Continue present")
        self.assertEqual(r["afterSkipReopen"], r["initial"], "Skip keeps fresh user at Page 1")

    @unittest.skipIf(NODE is None, "node is not available")
    def test_resume_lands_on_first_incomplete_with_answer_preserved(self):
        # Every data step done EXCEPT voice → resume lands on voice (step 10)
        # with the persisted answers intact (ring still 79% = 11 of 14).
        state = copy.deepcopy(FULL)
        del state["preferences"]["voice_style"]
        out = run_harness("resume", state)
        r = out["resume"]
        self.assertEqual(r["initial"]["ringPct"], "79")
        self.assertTrue(r["initial"]["hasBack"], "resume happened (past intro)")
        self.assertFalse(r["initial"]["hasFinish"], "voice isn't the completed page")
        self.assertEqual(r["afterSkipReopen"], r["initial"], "answers + position preserved after Skip")

    @unittest.skipIf(NODE is None, "node is not available")
    def test_resume_all_complete_shows_completed_state_not_restart(self):
        # All data steps complete (but not finished) → the wizard shows the
        # review/completed state instead of restarting from Page 1.
        out = run_harness("resume", dict(FULL))
        r = out["resume"]
        self.assertEqual(r["initial"]["ringPct"], "86")
        self.assertTrue(r["initial"]["hasFinish"], "completed state (review) shown for all-done user")
        self.assertTrue(r["initial"]["hasBack"], "Back works on the review page")


class ProgressFrontendStaticTestCase(unittest.TestCase):
    """Static guarantees about the frontend that don't need a browser."""

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _js_path(self, name):
        return ROOT / "static" / name

    def test_model_loads_before_onboarding(self):
        html = self._index_html()
        i_settings = html.find('<script src="/static/settings.js"></script>')
        i_model = html.find('<script src="/static/pref_setup_model.js')
        i_onboarding = html.find('<script src="/static/onboarding.js')
        self.assertNotEqual(i_model, -1, "pref_setup_model.js must be loaded")
        self.assertLess(i_settings, i_model, "model loads after settings.js (globals)")
        self.assertLess(i_model, i_onboarding, "model MUST load before onboarding.js")

    def test_no_hard_coded_step_list_in_onboarding(self):
        js = self._js_path("onboarding.js").read_text(encoding="utf-8")
        self.assertIn("window.PREF_SETUP_MODEL", js, "wizard uses the model")
        self.assertIn("MODEL.stepIds()", js, "wizard derives steps from the model")
        self.assertNotIn('STEP_IDS = ["', js, "no hard-coded step array in wizard")

    def test_navigation_root_cause_fixed(self):
        js = self._js_path("onboarding.js").read_text(encoding="utf-8")
        self.assertNotIn("writeSection(", js, "writeSection() gone (was undefined → ReferenceError)")
        self.assertIn("function putSection(", js, "putSection() is defined")

    def test_progress_bar_replaced_by_ring(self):
        js = self._js_path("onboarding.js").read_text(encoding="utf-8")
        self.assertNotIn("progressPct", js, "page-index progress bar removed")
        for token in ("ob-ring-wrap", 'data-progress', "ob-ring-fill",
                      "ob-ring-num", "ob-ring-label", "conic-gradient", "aria-live"):
            self.assertIn(token, js, "ring markup missing: %s" % token)

    def test_percentage_is_derived_not_mapped(self):
        js = self._js_path("onboarding.js").read_text(encoding="utf-8")
        self.assertNotIn("OB.step + 1", js, "no step-index → percent mapping")
        self.assertIn("MODEL.percentage(", js, "percentage comes from the model")
        model = self._js_path("pref_setup_model.js").read_text(encoding="utf-8")
        self.assertIn("completedSteps", model)
        self.assertIn("totalSteps", model)
        self.assertIn("Math.round", model, "nearest-whole-number rounding")

    def test_no_stored_percentage(self):
        # The spec forbids storing completionPercentage; it must be derived.
        # (The word appears in comments, so check code patterns, not prose.)
        js = self._js_path("onboarding.js").read_text(encoding="utf-8")
        self.assertNotIn('"completionPercentage"', js, "no stored JSON key in wizard")
        self.assertNotIn(".completionPercentage =", js, "no stored value in wizard")
        model = self._js_path("pref_setup_model.js").read_text(encoding="utf-8")
        self.assertNotIn('"completionPercentage"', model, "model never persists a percentage")

    def test_resume_landing_step_derived_via_model(self):
        js = self._js_path("onboarding.js").read_text(encoding="utf-8")
        self.assertIn("function resumeStep()", js, "resume logic lives in the wizard")
        self.assertIn("MODEL.isStepComplete(", js, "resume uses the model's completion rules")
        self.assertIn("OB.step = resumeStep()", js, "landing step set from persisted state")
        # Resume must run only AFTER the persisted state has been loaded.
        load = js[js.index("function loadDraft()"):js.index("function saveStep(")]
        self.assertIn("OB.ev = ", load, "state arrives before resume computes")
        self.assertIn("OB.step = resumeStep()", load, "resume happens inside loadDraft, post-load")

    def test_chat_banner_shown_until_preferences_completed(self):
        html = self._index_html()
        self.assertIn('id="prefBanner"', html, "chat banner markup present")
        for fn in ("showPreferenceBanner", "hidePreferenceBanner",
                   "dismissPreferenceBanner", "openPreferenceBannerSetup"):
            self.assertIn("function " + fn + "()", html, "banner fn missing: %s" % fn)
        self.assertIn('setup_status === "completed"', html, "banner reads setup-status")
        self.assertIn("showPreferenceBanner()", html, "banner shown when not completed")
        self.assertIn("hidePreferenceBanner()", html, "banner hidden once completed")


if __name__ == "__main__":
    unittest.main()