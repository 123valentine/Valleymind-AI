// Headless runtime smoke harness: loads static/cloud_rig.js AND
// static/cloud_anim.js into a DOM-lite sandbox, auto-boots the rig, then
// actually ticks requestAnimationFrame frames through the animation engine.
// This exercises adopt/resolve/render (rig parts, pupils, mouth, body, arms,
// legs, autonomous drift, movement placement) with a real rig, proving the
// engine runs without exceptions and genuinely drives independent parts.
// Prints a JSON summary and exits non-zero on any failure.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const RIG_PATH = path.join(ROOT, "static", "cloud_rig.js");
const ANIM_PATH = path.join(ROOT, "static", "cloud_anim.js");

const failures = [];
let passes = 0;
function check(cond, name, detail) {
  if (cond) { passes++; }
  else { failures.push(`${name}${detail !== undefined ? ` (${detail})` : ""}`); }
}
function eq(got, want, name) { check(got === want, name, `got ${got} expected ${want}`); }

// ── Fake DOM element (superset of the rig harness needs) ────────────────
function FakeEl(tagName, ownerDocument) {
  this.tagName = tagName;
  this.nodeType = 1;
  this.ownerDocument = ownerDocument;
  this.attrs = {};
  this.children = [];
  this.style = {};
  this._class = "";
  this.id = "";
  this.isConnected = true;
  this.listeners = {};
}
FakeEl.prototype.setAttribute = function (n, v) { this.attrs[n] = String(v); };
FakeEl.prototype.getAttribute = function (n) { return this.attrs[n]; };
FakeEl.prototype.appendChild = function (c) { this.children.push(c); c.parentNode = this; return c; };
FakeEl.prototype.addEventListener = function (ev, cb) {
  (this.listeners[ev] = this.listeners[ev] || []).push(cb);
};
// Reflect the inline left/top the movement controller writes, so repeated
// reads advance (a real browser reports the new position once placed).
FakeEl.prototype.getBoundingClientRect = function () {
  var left = parseFloat(this.style.left);
  var top = parseFloat(this.style.top);
  if (isNaN(left)) left = this.attrs["styleLeft"] || 0;
  if (isNaN(top)) top = this.attrs["styleTop"] || 0;
  var w = 132, h = 176;
  return { left: left, top: top, width: w, height: h, right: left + w, bottom: top + h };
};
FakeEl.prototype.hasClass = function (c) {
  return (this.attrs["class"] || this._class || "").split(/\s+/).indexOf(c) !== -1;
};
FakeEl.prototype.queryAll = function () {
  var out = [];
  (function walk(el) {
    el.children.forEach(function (c) { out.push(c); walk(c); });
  })(this);
  return out;
};
FakeEl.prototype.querySelector = function (sel) {
  var m = sel.match(/^\.([\w-]+)$/);
  var d = sel.match(/^\[data-part="([\w-]+)"\]$/);
  var s = sel.match(/^(\w+)\.([\w-]+)$/);
  var t = sel.match(/^([a-zA-Z][\w-]*)$/);
  var kids = this.queryAll();
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (m && k.hasClass(m[1])) return k;
    if (d && k.attrs["data-part"] === d[1]) return k;
    if (s && k.tagName === s[1] && k.hasClass(s[2])) return k;
    if (t && k.tagName === t[1]) return k;
  }
  return null;
};
FakeEl.prototype.querySelectorAll = function (sel) {
  var s = sel.match(/^(\w+)\.([\w-]+)$/);
  var out = [];
  var kids = this.queryAll();
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (s && k.tagName === s[1] && k.hasClass(s[2])) out.push(k);
  }
  return out;
};

function FakeDocument() {
  this.readyState = "loading";
  this.body = new FakeEl("body", this);
  this.listeners = {};
  this.roots = {};
}
FakeDocument.prototype.createElementNS = function (_, name) { return new FakeEl(name, this); };
FakeDocument.prototype.createElement = function (name) { return new FakeEl(name, this); };
FakeDocument.prototype.getElementById = function (id) { return this.roots[id] || null; };
FakeDocument.prototype.addEventListener = function (ev, cb) {
  (this.listeners[ev] = this.listeners[ev] || []).push(cb);
};
FakeDocument.prototype.querySelector = function () { return null; };
FakeDocument.prototype.querySelectorAll = function () { return []; };
FakeDocument.prototype.emit = function (ev) {
  (this.listeners[ev] || []).forEach(function (cb) { cb(); });
};

// ── Browser-ish sandbox with a manual rAF pump ──────────────────────────
const doc = new FakeDocument();
// Pre-populate the exact static markup: #vmCloudCharacter > .vmcloud-rig-mount.
const charEl = new FakeEl("div", doc);
charEl.id = "vmCloudCharacter";
const rigMount = new FakeEl("div", doc);
rigMount._class = "vmcloud-rig-mount";
rigMount.className = "vmcloud-rig-mount";
charEl.children.push(rigMount); rigMount.parentNode = charEl;
doc.roots["vmCloudCharacter"] = charEl;

let clock = 0;
let rafQueue = [];
const windowObj = {
  innerWidth: 1280,
  innerHeight: 720,
  matchMedia: function () {
    return { matches: false, addEventListener: function () { } };
  }
};
const sandbox = {
  document: doc,
  window: windowObj,
  performance: { now: function () { return clock; } },
  requestAnimationFrame: function (cb) { rafQueue.push(cb); return rafQueue.length; },
  cancelAnimationFrame: function () { },
  getComputedStyle: function () {
    return { display: "block", visibility: "visible", opacity: "1" };
  },
  console: console
};
vm.createContext(sandbox);

// Load the rig (it defers auto-boot to DOMContentLoaded while loading).
vm.runInContext(fs.readFileSync(RIG_PATH, "utf8"), sandbox, { filename: "cloud_rig.js" });
check(!!windowObj.VMCloudRig, "rig loaded into sandbox");
doc.emit("DOMContentLoaded"); // auto-boot mounts the rig into #vmCloudCharacter
check(rigMount.attrs["data-vm-rig"] === "1", "rig auto-booted into the static mount");
eq(rigMount.children.length, 1, "one rig SVG mounted");
eq(rigMount.children[0].hasClass("vmcloud-rig-svg"), true, "rig SVG tagged");

// Load the animation engine (readyState now "complete" → boot()).
doc.readyState = "complete";
vm.runInContext(fs.readFileSync(ANIM_PATH, "utf8"), sandbox, { filename: "cloud_anim.js" });
const A = windowObj.vmCloudAnim;
check(!!A, "anim engine exported");
check(typeof windowObj.cloudSetState === "function", "cloudSetState exported");

const PART = function (name) { return rigMount.children[0].querySelector('[data-part="' + name + '"]'); };
function pump(n) {
  for (let i = 0; i < n; i++) {
    const q = rafQueue; rafQueue = [];
    clock += 16;
    q.forEach(function (cb) { cb(clock); });
  }
}

// Let the engine resolve + render a few frames.
pump(10);
check(!!PART("body"), "rig body part reachable from mounted svg");
check((PART("body").style.transform || "").length > 0, "body driven via transform", PART("body").style.transform);
check(!!PART("nose"), "cloud nose part reachable from mounted svg");
check((PART("nose").style.transform || "").length > 0, "nose driven via transform", PART("nose").style.transform);
check((PART("leftLeg").style.transform || "").length > 0, "left leg driven via transform");
check((PART("leftArm").style.transform || "").length > 0, "left arm driven via transform");
["head", "face", "halo", "lowerBody", "leftEar", "rightEar"].forEach(function (gone) {
  check(PART(gone) === null, "robot part gone from the mounted rig: " + gone);
});
const leftPupil = rigMount.children[0].querySelector(".cloud-pupil");
check(!!leftPupil, "pupil node reachable");
check((leftPupil.style.transform || "").length > 0, "pupil translated for gaze", leftPupil.style.transform);
check((charEl.style.transform || "").length > 0, "whole body transform applied");

// State → expression actually propagates to the mouth path.
const mouthPath = rigMount.children[0].querySelector("[data-part=\"mouth\"]").querySelector("path");
eq(mouthPath.getAttribute("data-expression"), "neutral", "starts neutral");
windowObj.cloudSetState("happy");
pump(12);
eq(mouthPath.getAttribute("data-expression"), "smile", "happy → smile mouth expression");
windowObj.cloudSetState("excited");
pump(12);
eq(mouthPath.getAttribute("data-expression"), "big_smile", "excited → big smile mouth expression");

// ── Mouth sync: real amplitude drives the mouth, and the fallback survives ──
// The deepest point of the mouth curve is its open depth. The synthetic
// fallback can only ever reach smile (354) or big_smile (360), so depths below
// 354 or above 360 prove the amplitude tap is genuinely in control.
function mouthDepth(d) {
  const nums = (d || "").match(/-?\d+(\.\d+)?/g) || [];
  return nums.reduce(function (m, n) { return Math.max(m, parseFloat(n)); }, -Infinity);
}
A.setState("speaking");
pump(6);
eq(mouthPath.getAttribute("data-expression"), "speak", "speaking → speak mouth expression");

const syntheticA = mouthPath.getAttribute("d");
pump(3);
check(mouthPath.getAttribute("d") !== syntheticA, "unwired mouth still pulses (fallback alive)");
const synthDepth = mouthDepth(mouthPath.getAttribute("d"));
check(synthDepth >= 354 && synthDepth <= 360, "fallback depth stays in the synthetic pair", synthDepth);

// A live tap feeds a level every frame, exactly like the analyser rAF loop in
// static/cloud_voice.js. Pumping without feeding models a dead tap.
function pumpTTS(n, level) {
  for (let i = 0; i < n; i++) { A.setSpeechLevel(level); pump(1); }
}

// A quiet live tap must reach a CLOSEDER mouth than the synthetic pair allows.
pumpTTS(90, 0);
const quietDepth = mouthDepth(mouthPath.getAttribute("d"));
check(quietDepth < 354, "level 0 opens less than any synthetic shape", quietDepth);
eq(A.isSpeechDriven(), true, "tap keeps the mouth audio-driven through silence");

// A loud tap must open WIDER than the widest synthetic shape.
pumpTTS(90, 1);
const loudDepth = mouthDepth(mouthPath.getAttribute("d"));
check(loudDepth > 360, "level 1 opens wider than the synthetic pulse", loudDepth);
check(loudDepth > quietDepth, "louder audio opens the mouth further");

// Releasing the tap hands control back to the synthetic pulse.
A.clearSpeechLevel();
pump(6);
const released = mouthDepth(mouthPath.getAttribute("d"));
check(released >= 354 && released <= 360, "clearing the tap restores the fallback", released);

// A tap that stops feeding entirely (crashed analyser, backgrounded tab) must
// release back to the synthetic pulse rather than freezing the mouth open.
A.setSpeechLevel(1);
pump(2);
eq(A.isSpeechDriven(), true, "tap is live right after a level");
pump(90);
eq(A.isSpeechDriven(), false, "a dead tap releases to the synthetic pulse");
const starved = mouthDepth(mouthPath.getAttribute("d"));
check(starved >= 354 && starved <= 360, "dead tap leaves the mouth pulsing", starved);

A.setState("idle");

// Explicit gaze actually leans the body (lead → follow chain).
const bodyBefore = PART("body").style.transform;
A.lookToward(0.6, -0.2);
pump(6);
check(PART("body").style.transform !== bodyBefore, "lookToward drives body coordination");

// Autonomous drift moves the cloud on its own (no walk command needed).
A.stop();
const leftBeforeDrift = charEl.style.left;
const topBeforeDrift = charEl.style.top;
for (let i = 0; i < 900; i++) pump(1);
const dbgDrift = A.debug();
check(dbgDrift.driftOn === true, "autonomous drift enabled by default");
check(typeof dbgDrift.driftHasTarget === "boolean", "debug exposes drift target state");
check(
  charEl.style.left !== leftBeforeDrift || charEl.style.top !== topBeforeDrift,
  "cloud drifts across the screen on its own",
  `${leftBeforeDrift},${topBeforeDrift} -> ${charEl.style.left},${charEl.style.top}`
);
A.setDrift(false);
check(A.debug().driftOn === false, "setDrift(false) disables autonomous travel");
A.setDrift(true);

// Walking actually repositions the character (movement layer).
A.walk(-1);
let animated = 0;
for (let i = 0; i < 20; i++) pump(1);
check(typeof charEl.style.left === "string" && charEl.style.left !== "", "walking repositions via left", charEl.style.left);
A.stop();

// One-shot gesture registers and clears.
check(A.wave() && A.isGesturing(), "wave registers");
A.stopGestures();
check(!A.isGesturing(), "gesture cleared");

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);