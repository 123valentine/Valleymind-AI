// Regression guard for the real-browser freeze root cause:
// prefers-reduced-motion must DAMPEN the loop, never stop it. Browsers (and
// many OS setups) report matches:true; the engine must still boot, draw
// frames, resolve the rig and change expressions.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const failures = [];
let passes = 0;
function check(cond, name, detail) {
  if (cond) { passes++; }
  else { failures.push(name + (detail !== undefined ? ` (${detail})` : "")); }
}

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
FakeEl.prototype.getBoundingClientRect = function () { return { left: 0, top: 0, width: 96, height: 140 }; };
FakeEl.prototype.hasClass = function (c) { return (this.attrs["class"] || "").split(/\s+/).indexOf(c) !== -1; };
FakeEl.prototype.addEventListener = function (ev, cb) { (this.listeners[ev] = this.listeners[ev] || []).push(cb); };
FakeEl.prototype.emit = function (ev) { (this.listeners[ev] || []).forEach(function (cb) { cb(); }); };
FakeEl.prototype.queryAll = function () {
  var out = [];
  (function walk(el) { el.children.forEach(function (c) { out.push(c); walk(c); }); })(this);
  return out;
};
FakeEl.prototype.querySelector = function (sel) {
  var m = sel.match(/^\.([\w-]+)$/), d = sel.match(/^\[data-part="([\w-]+)"\]$/), tc = sel.match(/^([a-zA-Z][\w-]*)\.([\w-]+)$/), t = sel.match(/^([a-zA-Z][\w-]*)$/);
  var kids = this.queryAll();
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (m && k.hasClass(m[1])) return k;
    if (d && k.attrs["data-part"] === d[1]) return k;
    if (tc && k.tagName === tc[1] && k.hasClass(tc[2])) return k;
    if (t && k.tagName === t[1]) return k;
  }
  return null;
};
FakeEl.prototype.querySelectorAll = function (sel) {
  var s = sel.match(/^(\w+)\.([\w-]+)$/), out = [], kids = this.queryAll();
  for (var i = 0; i < kids.length; i++) if (s && kids[i].tagName === s[1] && kids[i].hasClass(s[2])) out.push(kids[i]);
  return out;
};

function FakeDocument() {
  this.readyState = "loading";
  this.listeners = {};
  this.roots = {};
}
FakeDocument.prototype.createElementNS = function (_, name) { return new FakeEl(name, this); };
FakeDocument.prototype.getElementById = function (id) { return this.roots[id] || null; };
FakeDocument.prototype.addEventListener = function (ev, cb) { (this.listeners[ev] = this.listeners[ev] || []).push(cb); };
FakeDocument.prototype.emit = function (ev) { (this.listeners[ev] || []).forEach(function (cb) { cb(); }); };

const doc = new FakeDocument();
doc.body = new FakeEl("body", doc);
const charEl = new FakeEl("div", doc);
charEl.id = "vmCloudCharacter";
const rigMount = new FakeEl("div", doc);
rigMount.attrs["class"] = "vmcloud-rig-mount";
charEl.children.push(rigMount);
doc.roots["vmCloudCharacter"] = charEl;

let clock = 0;
let rafQueue = [];
const windowObj = {
  innerWidth: 1280,
  innerHeight: 720,
  // The divergence: a REAL browser reports matches:true for reduced motion.
  matchMedia: function () { return { matches: true, addEventListener: function () { } }; }
};
const sandbox = {
  document: doc,
  window: windowObj,
  performance: { now: function () { return clock; } },
  requestAnimationFrame: function (cb) { rafQueue.push(cb); return rafQueue.length; },
  cancelAnimationFrame: function () {},
  getComputedStyle: function () { return { display: "block", visibility: "visible", opacity: "1" }; },
  console: console
};
vm.createContext(sandbox);

vm.runInContext(fs.readFileSync(path.join(ROOT, "static", "cloud_rig.js"), "utf8"), sandbox, { filename: "cloud_rig.js" });
doc.readyState = "complete";
doc.emit("DOMContentLoaded"); // auto-boot mounts the rig into #vmCloudCharacter
if (rigMount.children.length >= 1) {
  passes++;
} else {
  failures.push("rig mounted into rig-mount: " + rigMount.children.length + " children");
}
vm.runInContext(fs.readFileSync(path.join(ROOT, "static", "cloud_anim.js"), "utf8"), sandbox, { filename: "cloud_anim.js" });
const A = windowObj.vmCloudAnim;

function pump(n) { for (let i = 0; i < n; i++) { const q = rafQueue; rafQueue = []; clock += 16; q.forEach(function (cb) { cb(clock); }); } }
pump(10);

const dbg = A.debug();
check(dbg.running === true, "engine running under prefers-reduced-motion", JSON.stringify(dbg));
check(dbg.reduced === true, "reduced flag observed");
check(dbg.framesDrawn > 0, "frames actually drawn while reduced", dbg.framesDrawn);
const PART = function (name) { return rigMount.children[0].querySelector('[data-part="' + name + '"]'); };
check((PART("head").style.transform || "").length > 0, "rig parts driven while reduced", PART("head").style.transform);

windowObj.cloudSetState("happy");
pump(60);
const mouthPath = PART("mouth").querySelector("path");
check(mouthPath.getAttribute("data-expression") === "smile", "state change still visible while reduced");

// Movement layer stays alive while reduced: an explicit walk-to still
// repositions the character (target mode always places instead of stalling on
// sub-pixel wander), so calling walk() alone never dead-ends reduced users.
A.walkTo(300, 300);
let sawLeft = false;
let placedAt = -1;
for (let i = 0; i < 8; i++) { pump(1); if (!sawLeft && (charEl.style.left || "") !== "") { sawLeft = true; placedAt = i; } }
check(sawLeft, "walk still repositions even under reduced motion", "first=" + placedAt);
check(A.isMoving(), "walk remains active while reduced");

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);