// Headless runtime smoke harness: loads static/cloud_rig.js AND
// static/cloud_anim.js into a DOM-lite sandbox, auto-boots the rig, then
// actually ticks requestAnimationFrame frames through the animation engine.
// This exercises adopt/resolve/render (rig parts, pupils, mouth, head, arms,
// legs, body, movement placement) with a real rig, proving the engine runs
// without exceptions and genuinely drives independent parts.
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
FakeEl.prototype.getBoundingClientRect = function () {
  return { left: 0, top: 0, width: 96, height: 140, right: 96, bottom: 140 };
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
check(!!PART("head"), "rig head part reachable from mounted svg");
check((PART("head").style.transform || "").length > 0, "head driven via transform", PART("head").style.transform);
check((PART("leftLeg").style.transform || "").length > 0, "left leg driven via transform");
check((PART("leftArm").style.transform || "").length > 0, "left arm driven via transform");
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

// Explicit gaze actually rotates the head (lead → follow chain).
const headBefore = PART("head").style.transform;
A.lookToward(0.6, -0.2);
pump(6);
check(PART("head").style.transform !== headBefore, "lookToward drives head coordination");

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