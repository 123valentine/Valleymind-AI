// Pure-node runner for static/companion_presentation.js.
// No DOM: exercises the embedded<->external presentation coordinator that the
// Settings toggle uses. Prints a JSON summary and exits non-zero on failure.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const MODEL_PATH = path.join(ROOT, "static", "companion_presentation.js");

const failures = [];
let passes = 0;
function check(cond, name, detail) {
  if (cond) { passes++; }
  else { failures.push(`${name}${detail !== undefined ? ` (${detail})` : ""}`); }
}
function eq(got, want, name) { check(got === want, name, `got ${got} expected ${want}`); }

const sandbox = { module: { exports: {} }, window: {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(MODEL_PATH, "utf8"), sandbox, { filename: "companion_presentation.js" });
const API = sandbox.module.exports;

check(API && typeof API.createCoordinator === "function", "createCoordinator is exported");

function fakeEmbedded(opts) {
  opts = opts || {};
  return {
    visible: opts.visible !== false,
    mode: opts.mode || "vrm",
    hideCalls: 0,
    showCalls: 0,
    getMode: function () { return this.mode; },
    isVisible: function () { return this.visible; },
    hide: function () { this.hideCalls++; this.visible = false; },
    show: function () { this.showCalls++; this.visible = true; },
  };
}
function coordFor(e) {
  return API.createCoordinator({ getEmbedded: function () { return e; } });
}

// ── Initial OFF: embedded stays visible, nothing hidden/spawned ────────────
{
  const e = fakeEmbedded();
  const c = coordFor(e);
  eq(c.externalHidden(), false, "OFF with nothing hidden is a no-op");
  eq(e.visible, true, "initial OFF keeps the embedded companion visible");
  eq(e.hideCalls, 0, "OFF never hides the embedded companion");
  eq(e.showCalls, 0, "OFF never forces a show when nothing was hidden");
  eq(c.embeddedHiddenByExternal(), false, "OFF reports embedded not hidden by external");
}

// ── Successful ON then OFF: hide on confirm, restore on OFF ────────────────
{
  const e = fakeEmbedded();
  const c = coordFor(e);
  eq(c.externalConfirmedVisible(), true, "ON hides the embedded companion");
  eq(e.visible, false, "embedded is hidden while external is confirmed on");
  eq(e.hideCalls, 1, "embedded hidden exactly once");
  eq(c.embeddedHiddenByExternal(), true, "coordinator remembers it hid the embedded");
  // Idempotent: a repeated confirmation must not hide (or count) again, and the
  // first OFF must fully restore.
  eq(c.externalConfirmedVisible(), true, "second ON confirmation stays hidden");
  eq(e.hideCalls, 1, "repeated ON does not hide twice");
  eq(c.externalHidden(), true, "OFF restores the embedded companion");
  eq(e.visible, true, "embedded is visible again after OFF");
  eq(e.showCalls, 1, "embedded restored exactly once");
  eq(c.embeddedHiddenByExternal(), false, "OFF clears the hidden-by-external flag");
  // A second OFF is a no-op (nothing hidden).
  eq(c.externalHidden(), false, "second OFF is a no-op");
  eq(e.showCalls, 1, "second OFF does not show twice");
}

// ── Failed/timed-out activation: restore embedded, never leave it hidden ───
{
  const e = fakeEmbedded();
  const c = coordFor(e);
  // Activation was requested but never confirmed: reconcile to OFF.
  c.reconcile(false);
  eq(e.visible, true, "failed activation keeps the embedded companion visible");
  eq(e.hideCalls, 0, "failed activation never hides the embedded companion");
}

// ── Reconcile to a KNOWN external state (panel open / IPC broadcast) ───────
{
  const e = fakeEmbedded();
  const c = coordFor(e);
  c.reconcile(true);
  eq(e.visible, false, "reconcile(true) hides embedded (external is really on)");
  c.reconcile(false);
  eq(e.visible, true, "reconcile(false) restores embedded (external is really off)");
}

// ── Never both hidden: ON then OFF always ends with embedded visible ───────
{
  const e = fakeEmbedded();
  const c = coordFor(e);
  c.externalConfirmedVisible();
  check(!e.visible, "during external mode the embedded is off (external is the one showing)");
  c.externalHidden();
  check(e.visible, "after switching back the embedded is on again (never both hidden)");
}

// ── Legacy Cloud character is never hidden by the external toggle ──────────
{
  const e = fakeEmbedded({ mode: "cloud" });
  const c = coordFor(e);
  eq(c.externalConfirmedVisible(), false, "cloud mode: nothing hidden");
  eq(e.visible, true, "cloud character stays visible");
  eq(e.hideCalls, 0, "cloud character is never hidden by the VRM external toggle");
}

// ── No companion engine present: never throws, never lies ──────────────────
{
  const c = API.createCoordinator({ getEmbedded: function () { return null; } });
  eq(c.externalConfirmedVisible(), false, "no engine: ON reports nothing hidden");
  eq(c.externalHidden(), false, "no engine: OFF reports nothing restored");
  eq(c.reconcile(true), false, "no engine: reconcile(true) is safe");
  eq(c.reconcile(false), false, "no engine: reconcile(false) is safe");
}

// ── A missing/partial engine is tolerated (no crash) ──────────────────────
{
  const partial = { getMode: function () { return "vrm"; } }; // no hide/show
  const c = API.createCoordinator({ getEmbedded: function () { return partial; } });
  eq(c.externalConfirmedVisible(), false, "partial engine without hide() is a no-op");
  eq(c.externalHidden(), false, "partial engine without show() is a no-op");
}

// ── The hide is reversible even if the engine throws ──────────────────────
{
  const e = fakeEmbedded();
  e.hide = function () { throw new Error("boom"); };
  const c = coordFor(e);
  eq(c.externalConfirmedVisible(), false, "hide() throwing does not mark hidden");
  eq(e.visible, true, "helper still visible after a throwing hide()");
}

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);
