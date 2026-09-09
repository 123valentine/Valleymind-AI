// Pure-node harness for static/cloud_anim.js using a minimal DOM stub.
// The anim engine is layered on top of the rig (static/cloud_rig.js via
// window.VMCloudRig); this harness loads it in a DOM-lite sandbox and drives
// the ENTIRE public surface: state machine, movement controller, explicit gaze,
// one-shot gestures, and the reserved future-Brain API. The rAF loop is stubbed
// to never tick, so all assertions exercise logic, not frames.
// Prints a JSON summary and exits non-zero on any failure.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const ANIM_PATH = path.join(ROOT, "static", "cloud_anim.js");

const failures = [];
let passes = 0;
function check(cond, name, detail) {
  if (cond) { passes++; }
  else { failures.push(`${name}${detail !== undefined ? ` (${detail})` : ""}`); }
}
function eq(got, want, name) { check(got === want, name, `got ${got} expected ${want}`); }

// ── Minimal DOM/browser stub ────────────────────────────────────────────
const window = { innerWidth: 1280, innerHeight: 720 };
const document = {
  readyState: "complete",
  hidden: false,
  listeners: {},
  getElementById: function () { return null; },
  addEventListener: function (ev, cb) { (this.listeners[ev] = this.listeners[ev] || []).push(cb); }
};
const performance = { now: function () { return Date.now(); } };

const sandbox = {
  window: window,
  document: document,
  performance: performance,
  requestAnimationFrame: function () { return 0; },  // never ticks frames
  cancelAnimationFrame: function () { },
  console: console
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ANIM_PATH, "utf8"), sandbox, { filename: "cloud_anim.js" });

const A = window.vmCloudAnim;
const states = A.listStates();
["idle", "listening", "thinking", "happy", "excited", "surprised", "sad",
  "confused", "concerned", "greeting", "speaking", "sleeping"].forEach(function (s) {
  check(states.indexOf(s) !== -1, "state exists: " + s);
});

// ── Entry points ────────────────────────────────────────────────────────
check(typeof window.cloudSetState === "function", "global cloudSetState installed");
eq(window.cloudSetState("happy"), true, "cloudSetState accepts core state");
eq(A.getState(), "happy", "state reported after set");
eq(window.cloudSetState("does_not_exist"), false, "unknown state rejected");
eq(A.getState(), "happy", "rejected state leaves state untouched");

// Transient states must auto-return to the last stable state (framework flag
// only — the auto-return timer is exercised by the frame loop, which is stubbed).
A.setState("surprised");
A.setState("idle");
eq(A.getState(), "idle", "back to idle after transient run");

// ── Layer 2: movement controller ───────────────────────────────────────
eq(A.setSpeed(1.5), 1.5, "setSpeed clamps/returns");
eq(A.setSpeed(99), 3, "setSpeed max clamp");
eq(A.setSpeed(-9), 0.2, "setSpeed min clamp");
check(A.walkTo(800, 300), "walkTo accepts a destination");
check(A.walk(-1), "walk accepts direction");
check(A.isMoving(), "isMoving true while walking");
check(A.walk(1), "walk(right) accepted");
A.stop();
check(!A.isMoving(), "stop halts movement");
A.turn(-1);
A.stop();

// ── Layer 3: explicit gaze ──────────────────────────────────────────────
check(A.lookToward(0.5, -0.3), "lookToward accepted");
check(A.lookToward(-1, 1, 800), "lookToward accepted with hold");
check(A.lookAt(40, -20), "lookAt accepted");
check(A.clearLook(), "clearLook accepted");
eq(A.lookToward(0, 0), true, "lookToward rejects nothing");
check(typeof A.lookAt === "function", "lookAt is exposed");

// ── Layer 3: one-shot gestures ──────────────────────────────────────────
check(A.wave(), "wave accepted");
check(A.isGesturing(), "wave registers as a gesture");
check(A.point("up"), "point(up) accepted");
check(A.point(-1), "point(left) accepted via -1");
check(A.point("down"), "point(down) accepted");
A.stopGestures();
check(!A.isGesturing(), "stopGestures clears the gesture");
check(A.welcome(), "welcome accepted");

// ── Layer 4: reserved future-Brain surface ──────────────────────────────
check(!!A.BrainAPI, "BrainAPI surface present");
eq(A.BrainAPI.setEmotion("excited"), true, "BrainAPI.setEmotion works");
eq(A.BrainAPI.startThinking(), true, "BrainAPI.startThinking works");
eq(A.BrainAPI.startSpeaking("Hello"), true, "BrainAPI.startSpeaking works");
eq(A.BrainAPI.stopTalking(), true, "BrainAPI.stopTalking returns to stable");
eq(A.BrainAPI.walkTo(100, 100), true, "BrainAPI.walkTo works");
eq(A.BrainAPI.lookToward(0, 0.5), true, "BrainAPI.lookToward works");
eq(A.BrainAPI.wave(), true, "BrainAPI.wave works");
eq(A.BrainAPI.point("right"), true, "BrainAPI.point works");
eq(A.BrainAPI.welcome(), true, "BrainAPI.welcome works");
eq(A.BrainAPI.stop(), true, "BrainAPI.stop cancels everything");

// ── Capabilities are honest about independence ──────────────────────────
const caps = A.getCapabilities();
check(caps.independentEyes && caps.independentEyebrows && caps.independentMouth, "face parts independently animated");
check(caps.independentArms && caps.independentLegs, "limbs independently animated");
check(caps.blinkVariants.indexOf("quick") !== -1 && caps.blinkVariants.indexOf("slow") !== -1, "blink variants exposed");
check(caps.gazeDirections.indexOf("toward-point") !== -1, "explicit gaze direction exposed");
check(caps.emotionalWalking, "emotional walking flagged");
check(caps.brainAPI.indexOf("anim surface only") !== -1, "brain API is honestly scoped");

// ── Top-level movement aliases ──────────────────────────────────────────
check(typeof window.vmCloudWalk === "function", "vmCloudWalk alias");
check(typeof window.vmCloudWalkTo === "function", "vmCloudWalkTo alias");
check(typeof window.vmCloudStop === "function", "vmCloudStop alias");
check(typeof window.vmCloudTurn === "function", "vmCloudTurn alias");

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);