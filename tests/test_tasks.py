"""ValleyMind task execution layer tests.

Covers the safe, observable action system added on top of the existing
companion:

  1. core.tasks -- the authoritative, PURE validator/planner/lifecycle:
     action schema, URL/selector validation, permission gating, confirmation
     metadata, task state transitions, the companion status mapping, the
     heuristic fallback planner, the LLM planner (mocked), and the idempotent
     outcome/audit store.
  2. The /api/task/* routes via the REAL Flask client with an authenticated
     session: login gating, input validation, planning (LLM + fallback),
     request-id idempotency, outcome recording + de-duplication, status.
  3. Static guarantees: the task adapter is a SEPARATE module, the privacy
     contract in cloud_vision.js is unchanged (still capture-only), the UI is
     wired, and applyStatus() is driven from real task lifecycle events.
  4. A Node-based check of the browser controller's pure rules + a headless
     lifecycle pass (skipped when node is unavailable).

NOTE: the Node pass runs the controller WITHOUT a DOM. It proves the rules and
the lifecycle, not real desktop interaction -- see the module report for which
behaviors were verified on a real browser vs. only here.

Run with: env311\\Scripts\\python.exe -m pytest tests/test_tasks.py -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module
from core import tasks as task_model

NODE = shutil.which("node")


class TaskModelTestCase(unittest.TestCase):
    """Pure core.tasks validation / planning / lifecycle / store."""

    def setUp(self):
        task_model.reset_store()

    # ── action validation ───────────────────────────────────────────────
    def test_respond_requires_text(self):
        with self.assertRaises(task_model.ActionValidationError):
            task_model.normalize_action({"type": "respond", "params": {"text": ""}})
        action = task_model.normalize_action({"type": "respond", "params": {"text": "hi"}})
        self.assertEqual(action["type"], "respond")
        self.assertFalse(action["consequential"])
        self.assertIsNone(action["permission"])

    def test_scroll_direction_and_amount_clamped(self):
        action = task_model.normalize_action(
            {"type": "scroll", "params": {"direction": "DOWN", "amount": 999999}})
        self.assertEqual(action["params"]["direction"], "down")
        self.assertEqual(action["params"]["amount"], 5000)
        with self.assertRaises(task_model.ActionValidationError):
            task_model.normalize_action({"type": "scroll", "params": {"direction": "sideways"}})

    def test_click_and_type_are_consequential_and_need_a_target(self):
        click = task_model.normalize_action({"type": "click", "params": {"selector": "#go"}})
        self.assertTrue(click["consequential"])
        self.assertEqual(click["permission"], "click")
        with self.assertRaises(task_model.ActionValidationError):
            task_model.normalize_action({"type": "click", "params": {"selector": "body"}})
        typed = task_model.normalize_action(
            {"type": "type", "params": {"selector": "[data-field=x]", "text": "hello"}})
        self.assertTrue(typed["consequential"])
        self.assertEqual(typed["params"]["text"], "hello")

    def test_unsupported_action_rejected(self):
        with self.assertRaises(task_model.ActionValidationError):
            task_model.normalize_action({"type": "run_shell", "params": {"cmd": "del /"}})

    def test_selector_rejects_dangerous_tokens(self):
        for bad in ("document.body", "input[onerror=x]", "a; b", "window.x", "eval(x)"):
            with self.assertRaises(task_model.ActionValidationError):
                task_model.validate_selector(bad)

    def test_url_validation(self):
        self.assertEqual(task_model.validate_url("/settings/profile"), "/settings/profile")
        for bad in ("//evil.com", "javascript:alert(1)", "data:text/html,x", "file:///c:/x"):
            with self.assertRaises(task_model.ActionValidationError):
                task_model.validate_url(bad)
        # external requires the flag AND https
        with self.assertRaises(task_model.ActionValidationError):
            task_model.validate_url("https://example.com", allow_external=False)
        with self.assertRaises(task_model.ActionValidationError):
            task_model.validate_url("http://example.com", allow_external=True)
        with self.assertRaises(task_model.ActionValidationError):
            task_model.validate_url("https://user@example.com", allow_external=True)
        self.assertEqual(
            task_model.validate_url("https://example.com/x", allow_external=True),
            "https://example.com/x",
        )

    def test_goto_workspace_is_allowlisted_and_non_consequential(self):
        action = task_model.normalize_action(
            {"type": "goto_workspace", "params": {"workspace": "Music"}})
        self.assertEqual(action["type"], "goto_workspace")
        self.assertEqual(action["params"]["workspace"], "music")
        self.assertFalse(action["consequential"])
        self.assertEqual(action["permission"], "navigate")
        for bad in ("evil.com", "", "admin", "http://x"):
            with self.assertRaises(task_model.ActionValidationError):
                task_model.normalize_action({"type": "goto_workspace", "params": {"workspace": bad}})
        self.assertEqual(task_model.validate_workspace("Studio"), "studio")
        with self.assertRaises(task_model.ActionValidationError):
            task_model.validate_workspace("not-a-workspace")

    def test_goto_workspace_gates_on_navigate_permission(self):
        steps, dropped = task_model.normalize_steps(
            [{"type": "goto_workspace", "params": {"workspace": "music"}}],
            permissions=(),
        )
        self.assertEqual(dropped, 0)
        self.assertTrue(steps[0]["blocked"])
        self.assertEqual(steps[0]["blocked_reason"], "permission required: navigate")

    def test_heuristic_plan_detects_workspace_intent(self):
        plan = task_model.heuristic_plan(
            "take me to the music studio", permissions=("navigate",))
        types = [s["type"] for s in plan["steps"]]
        self.assertIn("goto_workspace", types)
        target = next(s for s in plan["steps"] if s["type"] == "goto_workspace")
        self.assertEqual(target["params"]["workspace"], "music")
        # Stray words do NOT hijack the interface without an explicit intent.
        plan2 = task_model.heuristic_plan("I like music in my free time", permissions=("navigate",))
        self.assertNotIn("goto_workspace", [s["type"] for s in plan2["steps"]])

    def test_normalize_permissions(self):
        self.assertEqual(task_model.normalize_permissions(["scroll", "bogus", "scroll"]), ("scroll",))
        self.assertEqual(task_model.normalize_permissions("click"), ("click",))
        self.assertEqual(task_model.normalize_permissions(None), ())

    def test_normalize_steps_gates_on_permissions(self):
        raw = [
            {"type": "scroll", "params": {"direction": "down"}},
            {"type": "click", "params": {"selector": "#go"}},
        ]
        steps, dropped = task_model.normalize_steps(raw, permissions=("scroll",))
        self.assertEqual(dropped, 0)
        self.assertFalse(steps[0]["blocked"])
        self.assertTrue(steps[1]["blocked"])
        self.assertEqual(steps[1]["blocked_reason"], "permission required: click")

    def test_normalize_steps_drops_invalid_but_requires_one_valid(self):
        steps, dropped = task_model.normalize_steps(
            [{"type": "bogus"}, {"type": "respond", "params": {"text": "hi"}}])
        self.assertEqual(dropped, 1)
        self.assertEqual(len(steps), 1)
        with self.assertRaises(task_model.ActionValidationError):
            task_model.normalize_steps([{"type": "bogus"}])

    # ── lifecycle transitions ───────────────────────────────────────────
    def test_transitions_legal_and_illegal(self):
        self.assertEqual(task_model.apply_transition("idle", "planning"), "planning")
        self.assertEqual(task_model.apply_transition("planning", "working"), "working")
        self.assertEqual(task_model.apply_transition("working", "paused"), "paused")
        self.assertEqual(task_model.apply_transition("paused", "working"), "working")
        with self.assertRaises(ValueError):
            task_model.apply_transition("completed", "working")
        with self.assertRaises(ValueError):
            task_model.apply_transition("idle", "working")
        with self.assertRaises(ValueError):
            task_model.apply_transition("working", "bogus")

    def test_companion_status_mapping(self):
        self.assertEqual(task_model.companion_status_for("planning"), "thinking")
        self.assertEqual(task_model.companion_status_for("awaiting_confirmation"), "listening")
        self.assertEqual(task_model.companion_status_for("working"), "working")
        self.assertEqual(task_model.companion_status_for("paused"), "idle")
        self.assertEqual(task_model.companion_status_for("completed"), "happy")
        self.assertEqual(task_model.companion_status_for("failed"), "confused")
        self.assertEqual(task_model.companion_status_for("cancelled"), "idle")

    # ── planner ─────────────────────────────────────────────────────────
    def test_heuristic_plan_never_emits_consequential_actions(self):
        plan = task_model.heuristic_plan("scroll down 400 and open /settings/profile", permissions=())
        types = [s["type"] for s in plan["steps"]]
        self.assertIn("scroll", types)
        self.assertIn("respond", types)
        self.assertNotIn("click", types)
        self.assertNotIn("type", types)
        self.assertFalse(plan["requires_confirmation"])

    def test_build_plan_uses_llm_when_valid(self):
        raw = json.dumps({
            "goal": "open settings",
            "steps": [
                {"type": "navigate", "params": {"url": "/settings/profile"}, "label": "open"},
                {"type": "click", "params": {"selector": "#save"}, "label": "save"},
                {"type": "respond", "params": {"text": "done"}, "label": "reply"},
            ],
        })
        plan = task_model.build_plan("open settings and save", permissions=("click",), llm=lambda m: raw)
        self.assertEqual(plan["source"], "llm")
        self.assertTrue(plan["requires_confirmation"])
        click = [s for s in plan["steps"] if s["type"] == "click"][0]
        self.assertTrue(click["consequential"])
        self.assertFalse(click["blocked"])

    def test_build_plan_marks_blocked_without_permission(self):
        raw = json.dumps({"steps": [
            {"type": "click", "params": {"selector": "#save"}, "label": "save"},
        ]})
        plan = task_model.build_plan("save", permissions=(), llm=lambda m: raw)
        self.assertTrue(plan["steps"][0]["blocked"])
        self.assertIn("click", plan["blocked_permissions"])

    def test_build_plan_falls_back_on_llm_failure(self):
        def boom(_messages):
            raise RuntimeError("no provider")
        plan = task_model.build_plan("please scroll down", permissions=("scroll",), llm=boom)
        self.assertEqual(plan["source"], "heuristic")

    def test_build_plan_falls_back_on_garbage(self):
        plan = task_model.build_plan("hello", permissions=(), llm=lambda m: "not json at all")
        self.assertEqual(plan["source"], "heuristic")

    def test_build_plan_requires_request(self):
        with self.assertRaises(task_model.ActionValidationError):
            task_model.build_plan("   ", llm=lambda m: "{}")

    # ── idempotency + audit store ──────────────────────────────────────
    def test_outcome_record_deduplicates(self):
        first = task_model.record_outcome("u1", "t1", "s1", "working", "ok")
        second = task_model.record_outcome("u1", "t1", "s1", "working", "ok")
        self.assertFalse(first["deduped"])
        self.assertTrue(second["deduped"])
        self.assertEqual(len(task_model.get_tasks("u1")), 1)

    def test_outcome_is_per_user(self):
        task_model.record_outcome("u1", "t1", "s1", "working", "ok")
        task_model.record_outcome("u2", "t2", "s2", "completed", "ok")
        self.assertEqual(len(task_model.get_tasks("u1")), 1)
        self.assertEqual(len(task_model.get_tasks("u2")), 1)

    def test_outcome_rejects_bad_state_or_outcome(self):
        with self.assertRaises(ValueError):
            task_model.record_outcome("u1", "t1", "s1", "nonsense", "ok")
        with self.assertRaises(ValueError):
            task_model.record_outcome("u1", "t1", "s1", "working", "exploded")

    def test_plan_cache_roundtrip_and_isolation(self):
        plan = {"task_id": "t", "steps": [{"step_id": "s", "type": "respond"}]}
        task_model.cache_plan("req1", plan)
        got = task_model.get_cached_plan("req1")
        self.assertEqual(got["task_id"], "t")
        got["task_id"] = "mutated"
        self.assertEqual(task_model.get_cached_plan("req1")["task_id"], "t")
        self.assertIsNone(task_model.get_cached_plan("missing"))


class TaskApiTestCase(unittest.TestCase):
    """Real Flask client + isolated storage; no network (planner mocked)."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmpdir.name)
        cls._users_file_patch = patch.object(app_module, "_users_file", tmp / "auth_users.json")
        cls._users_coll_patch = patch.object(app_module, "users_collection", lambda: None)
        cls._auth_coll_patch = patch.object(app_module, "auth_tokens_collection", lambda: None)
        cls._settings_dir_patch = patch.object(app_module, "_SETTINGS_DIR", tmp / "settings")
        for p in (cls._users_file_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._settings_dir_patch):
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in (cls._users_file_patch, cls._users_coll_patch,
                  cls._auth_coll_patch, cls._settings_dir_patch):
            p.stop()
        cls.tmpdir.cleanup()

    def setUp(self):
        task_model.reset_store()
        self._app = app_module.app.test_client()

    def _auth(self):
        email = f"task_{uuid.uuid4().hex[:12]}@example.com"
        user_id = app_module._safe_user_id(email)
        users = app_module._load_users()
        users[email] = {"_id": email, "email": email, "user_id": user_id, "email_verified": True}
        app_module._save_users(users)
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["email"] = email
        return client

    # ── login gating ────────────────────────────────────────────────────
    def test_all_task_routes_require_login(self):
        self.assertEqual(self._app.post("/api/task/plan", json={"request": "hi"}).status_code, 401)
        self.assertEqual(self._app.post("/api/task/outcome", json={}).status_code, 401)
        self.assertEqual(self._app.get("/api/task/status").status_code, 401)

    def test_plan_requires_request(self):
        client = self._auth()
        self.assertEqual(client.post("/api/task/plan", json={}).status_code, 400)

    # ── planning ────────────────────────────────────────────────────────
    @patch("core.brain._call_llm_cluster")
    def test_plan_uses_brain_and_validates(self, llm):
        llm.return_value = (json.dumps({
            "goal": "open profile",
            "steps": [
                {"type": "navigate", "params": {"url": "/settings/profile"}, "label": "open"},
                {"type": "click", "params": {"selector": "#save"}, "label": "save"},
                {"type": "respond", "params": {"text": "opened"}, "label": "reply"},
            ],
        }), {})
        client = self._auth()
        resp = client.post("/api/task/plan", json={
            "request": "open my profile and save", "permissions": ["click", "navigate"],
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["source"], "llm")
        self.assertTrue(body["plan"]["requires_confirmation"])
        self.assertTrue(any(s["type"] == "click" for s in body["plan"]["steps"]))

    @patch("core.brain._call_llm_cluster", side_effect=RuntimeError("down"))
    def test_plan_falls_back_when_brain_down(self, _llm):
        client = self._auth()
        resp = client.post("/api/task/plan", json={"request": "scroll down", "permissions": ["scroll"]})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["source"], "heuristic")
        self.assertTrue(len(body["plan"]["steps"]) >= 1)

    @patch("core.brain._call_llm_cluster")
    def test_plan_request_id_is_idempotent(self, llm):
        llm.return_value = (json.dumps({"steps": [
            {"type": "respond", "params": {"text": "hi"}}]}), {})
        client = self._auth()
        payload = {"request": "hi", "request_id": "req-123", "permissions": []}
        first = client.post("/api/task/plan", json=payload).get_json()
        second = client.post("/api/task/plan", json=payload).get_json()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["plan"]["task_id"], second["plan"]["task_id"])

    # ── outcomes + status ───────────────────────────────────────────────
    def test_outcome_records_and_dedupes(self):
        client = self._auth()
        body = {"task_id": "t1", "step_id": "s1", "state": "working", "outcome": "ok"}
        first = client.post("/api/task/outcome", json=body).get_json()
        second = client.post("/api/task/outcome", json=body).get_json()
        self.assertFalse(first["deduped"])
        self.assertTrue(second["deduped"])
        status = client.get("/api/task/status").get_json()
        self.assertEqual(status["status"], "success")
        self.assertIn("awaiting_confirmation", status["states"])
        self.assertEqual(len(status["tasks"]), 1)

    def test_outcome_rejects_unknown_state(self):
        client = self._auth()
        resp = client.post("/api/task/outcome", json={
            "task_id": "t1", "step_id": "s1", "state": "exploded", "outcome": "ok"})
        self.assertEqual(resp.status_code, 400)


class TaskFrontendStaticTestCase(unittest.TestCase):
    """Static guarantees about the frontend wiring."""

    def _index_html(self):
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def _controller_js(self):
        return (ROOT / "static" / "task_controller.js").read_text(encoding="utf-8")

    def _companion_js(self):
        return (ROOT / "static" / "vrm_companion.js").read_text(encoding="utf-8")

    def _settings_js(self):
        return (ROOT / "static" / "settings.js").read_text(encoding="utf-8")

    def _vision_js(self):
        return (ROOT / "static" / "cloud_vision.js").read_text(encoding="utf-8")

    def test_controller_script_loaded(self):
        self.assertIn('<script src="/static/task_controller.js', self._index_html())

    def test_task_panel_markup_present(self):
        # The task dashboard was removed from the main screen and now renders
        # inline in Settings → Companion, using the same element ids.
        html = self._index_html()
        settings = self._settings_js()
        for el in ("vmVrmCompanionTask", "vmVrmCompanionTaskInput", "vmVrmCompanionTaskPlan",
                   "vmVrmCompanionTaskApprove", "vmVrmCompanionTaskDeny",
                   "vmVrmCompanionTaskPause", "vmVrmCompanionTaskResume",
                   "vmVrmCompanionTaskStop", "vmVrmCompanionTaskList"):
            self.assertNotIn('id="' + el + '"', html,
                             f"task dashboard should no longer be on the main view: {el}")
            self.assertIn('id="' + el + '"', settings,
                          f"task control missing from Settings → Companion: {el}")

    def test_controller_exposes_lifecycle_api(self):
        js = self._controller_js()
        self.assertIn("VMTaskController", js)
        for fn in ("submit", "approve", "deny", "pause", "resume", "cancel"):
            self.assertIn(fn + ":", js)
        for state in ("idle", "planning", "awaiting_confirmation", "working",
                      "paused", "completed", "failed", "cancelled"):
            self.assertIn('"' + state + '"', js)

    def test_controller_has_no_code_execution_or_os_control(self):
        js = self._controller_js()
        for banned in ("child_process", "execSync", "require(", "eval(", "new Function",
                       "robotjs", "pyautogui", "applescript", "shell.exec"):
            self.assertNotIn(banned, js)

    def test_controller_confirmation_and_stop_logic_present(self):
        js = self._controller_js()
        self.assertIn("isConsequential", js)
        self.assertIn("awaiting_confirmation", js)
        self.assertIn("function pause", js)
        self.assertIn("function cancel", js)
        self.assertIn("executed[step.step_id]", js)  # retry dedupe

    def test_companion_wires_task_controller_to_applystatus(self):
        js = self._companion_js()
        self.assertIn("initTasks", js)
        self.assertIn("window.VMTaskController", js)
        self.assertIn("onStatus: function (s) { setStatus(s); }", js)
        self.assertIn("submitTask", js)

    def test_companion_wires_in_app_workspace_guidance(self):
        js = self._companion_js()
        # The task adapter's `goto_workspace` action maps to onGuide -> guide().
        self.assertIn("onGuide: function (ws) { guide(ws); }", js)
        self.assertIn("function guide(", js)
        self.assertIn("walkTo(", js)
        self.assertIn("vmWsGo", js)

    def test_controller_supports_goto_workspace_action(self):
        js = self._controller_js()
        self.assertIn('if (type === "goto_workspace")', js)
        self.assertIn("workspaceOk(", js)
        self.assertIn("function workspaceOk(", js)
        self.assertIn('"roundtable"', js)
        self.assertIn("analytics", js)
        self.assertIn("PLANNER_TIMEOUT_MS", js)
        # The goto step is executed client-side through onGuide, never a raw
        # location change, so it cannot reach outside the app.
        self.assertIn("opts.onGuide", js)

    def test_controller_planner_timeout_is_distinct_and_compatible(self):
        js = self._controller_js()
        self.assertIn("planner timed out", js)
        # The 15s client budget that used to misfire on a 30s backend brain is
        # gone; planning now uses a dedicated, larger budget.
        self.assertNotIn("DEFAULT_TIMEOUT_MS = 15000", js)

    def test_vision_contract_is_unchanged_separate_adapter(self):
        """The task layer must NOT weaken the capture-only vision contract."""
        js = self._vision_js()
        self.assertIn("NO COMPUTER CONTROL", js.upper())
        self.assertNotIn("click(", js)
        self.assertIn("getDisplayMedia", js)
        # and the interaction adapter lives in its own file
        self.assertNotIn("VMTaskController", js)


@unittest.skipUnless(NODE, "node executable not available")
class TaskControllerNodeTestCase(unittest.TestCase):
    """Executes the browser controller's pure rules + headless lifecycle."""

    def test_controller_node_logic(self):
        script = ROOT / "tests" / "task_controller_node_test.js"
        proc = subprocess.run(
            [NODE, str(script)], capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
