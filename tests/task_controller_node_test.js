/*
 * Node-based checks for static/task_controller.js.
 *
 * These exercise the PURE, executable parts of the browser task adapter
 * (state mapping, transitions, consequence/permission rules, selector/URL
 * validation) and a headless lifecycle pass with no DOM. They are NOT a
 * substitute for real end-to-end desktop interaction (which requires a
 * browser + the running app); see the report for that distinction.
 *
 * Run: node tests/task_controller_node_test.js
 */
"use strict";

const path = require("path");
const tc = require(path.join(__dirname, "..", "static", "task_controller.js"));

let failures = 0;
function assert(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error("FAIL: " + msg);
  }
}

// ── pure rules ──────────────────────────────────────────────────────────────
assert(tc.statusFor("planning") === "thinking", "planning maps to thinking");
assert(tc.statusFor("awaiting_confirmation") === "listening", "awaiting maps to listening");
assert(tc.statusFor("working") === "working", "working maps to working");
assert(tc.statusFor("paused") === "idle", "paused maps to idle");
assert(tc.statusFor("completed") === "happy", "completed maps to happy");
assert(tc.statusFor("failed") === "confused", "failed maps to confused");
assert(tc.statusFor("cancelled") === "idle", "cancelled maps to idle");

assert(tc.isConsequential({ type: "click" }) === true, "click is consequential");
assert(tc.isConsequential({ type: "type" }) === true, "type is consequential");
assert(tc.isConsequential({ type: "scroll" }) === false, "scroll is safe");
assert(tc.isConsequential({ type: "respond" }) === false, "respond is safe");

assert(tc.permissionFor({ type: "type" }) === "type", "type permission");
assert(tc.permissionFor({ type: "inspect_view" }) === "vision", "inspect_view permission");
assert(tc.permissionFor({ type: "respond" }) === null, "respond needs no permission");

assert(tc.canTransition("planning", "working"), "planning -> working allowed");
assert(tc.canTransition("working", "paused"), "working -> paused allowed");
assert(tc.canTransition("paused", "working"), "paused -> working allowed");
assert(!tc.canTransition("completed", "working"), "completed is terminal");
assert(!tc.canTransition("failed", "working"), "failed is terminal");
assert(!tc.canTransition("idle", "working"), "idle -> working not allowed");
let threw = false;
try { tc.transition("completed", "working"); } catch (e) { threw = true; }
assert(threw, "illegal transition throws");

assert(tc.selectorOk("#sendBtn"), "id selector allowed");
assert(tc.selectorOk(".card button"), "class selector allowed");
assert(tc.selectorOk("[data-action=x]"), "attribute selector allowed");
assert(!tc.selectorOk("document.body"), "document token blocked");
assert(!tc.selectorOk("body"), "bare tag not targeted");
assert(!tc.selectorOk("a; b"), "semicolon blocked");
assert(!tc.selectorOk("input[onerror=x]"), "handler token blocked");

assert(tc.urlOk("/settings/profile", false), "relative app path allowed");
assert(!tc.urlOk("//evil.com", false), "protocol-relative blocked");
assert(!tc.urlOk("javascript:alert(1)", false), "javascript scheme blocked");
assert(!tc.urlOk("data:text/html,x", false), "data scheme blocked");
assert(!tc.urlOk("http://example.com", false), "http blocked even same form");
assert(!tc.urlOk("https://example.com", false), "external needs permission");
assert(tc.urlOk("https://example.com", true), "https external allowed with permission");
assert(!tc.urlOk("https://user@example.com", true), "credentials in url blocked");

// In-app workspace allowlist (mirrors vmWsGo in index.html).
assert(tc.workspaceOk("studio"), "studio is a workspace");
assert(tc.workspaceOk("MUSIC") === true, "workspace names are case-insensitive");
assert(!tc.workspaceOk("evil.com"), "unknown workspace blocked");
assert(!tc.workspaceOk(""), "empty workspace blocked");
assert(tc.PLANNER_TIMEOUT_MS >= 60000, "planner budget exceeds backend LLM timeout");
assert(tc.permissionFor({ type: "goto_workspace" }) === "navigate", "goto_workspace uses navigate permission");
assert(tc.isConsequential({ type: "goto_workspace" }) === false, "goto_workspace is not consequential");

// ── headless lifecycle: stops at confirmation, then completes ───────────────
const events = [];
const plan = {
  task_id: "t1",
  steps: [
    { step_id: "s1", type: "respond", params: { text: "hello" }, consequential: false, permission: null },
    { step_id: "s2", type: "scroll", params: { direction: "down", amount: 10 }, consequential: false, permission: null },
    { step_id: "s3", type: "click", params: { selector: "#go" }, consequential: true, permission: "click" }
  ]
};

const apiPost = (p, body) => {
  if (p === "/api/task/plan") {
    return Promise.resolve({ json: () => Promise.resolve({ status: "success", plan: plan }) });
  }
  return Promise.resolve({ json: () => Promise.resolve({ status: "success", deduped: false }) });
};

tc.init({
  getPermissions: () => ["scroll", "click"],
  apiPost: apiPost,
  onStatus: (s) => events.push("status:" + s),
  onSpeak: (t) => events.push("speak:" + t)
});

function finish() {
  const state = tc.getState();
  if (state !== "completed") {
    failures += 1;
    console.error("FAIL: expected completed after approve, got " + state);
  }
  if (events.indexOf("status:thinking") === -1) {
    failures += 1; console.error("FAIL: planning did not map to thinking");
  }
  if (events.indexOf("status:listening") === -1) {
    failures += 1; console.error("FAIL: confirmation did not map to listening");
  }
  if (events.indexOf("speak:hello") === -1) {
    failures += 1; console.error("FAIL: respond step did not speak");
  }
}

tc.submit("do the thing");

setTimeout(() => {
  if (tc.getState() !== "awaiting_confirmation") {
    failures += 1;
    console.error("FAIL: did not pause at confirmation, got " + tc.getState());
    process.exit(1);
  }
  tc.approve();
  setTimeout(() => {
    finish();
    // A second lifecycle proves the in-app workspace guidance step executes
    // (onGuide is called, never a bare location change) and completes.
    const guided = [];
    tc.init({
      getPermissions: () => ["navigate"],
      apiPost: apiPost,
      onStatus: (s) => events.push("status:" + s),
      onGuide: (ws) => guided.push(ws),
      onSpeak: (t) => events.push("speak:" + t)
    });
    const guidePlan = {
      task_id: "t2",
      steps: [
        { step_id: "g1", type: "goto_workspace", params: { workspace: "studio" }, consequential: false, permission: "navigate" },
        { step_id: "g2", type: "respond", params: { text: "done" }, consequential: false, permission: null }
      ]
    };
    const guideApi = (p, body) => Promise.resolve({ json: () => Promise.resolve({ status: "success", plan: guidePlan }) });
    tc.init({ getPermissions: () => ["navigate"], apiPost: guideApi, onGuide: (ws) => guided.push(ws) });
    if (tc.submit("open the studio") !== true) { failures += 1; console.error("FAIL: submit returned false"); }
    setTimeout(() => {
      const st = tc.getState();
      if (st !== "completed") { failures += 1; console.error("FAIL: guide task did not complete, got " + st); }
      if (guided.indexOf("studio") === -1) { failures += 1; console.error("FAIL: goto_workspace did not call onGuide"); }
      console.log(failures === 0 ? "task_controller node checks OK" : "task_controller node checks FAILED");
      process.exit(failures === 0 ? 0 : 1);
    }, 120);
  }, 120);
}, 80);
