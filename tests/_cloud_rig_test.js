// Pure-node harness for static/cloud_rig.js using a minimal SVG DOM stub.
// Verifies the layered rig builds the expected addressable parts, keeps the
// faithful cloud.png palette/geometry, sets per-part pivots, and mounts into a
// container exactly once (auto-boot + cloud.js can race safely).
// Prints a JSON summary and exits non-zero on any failure.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const RIG_PATH = path.join(ROOT, "static", "cloud_rig.js");

const failures = [];
let passes = 0;
function check(cond, name, detail) {
  if (cond) { passes++; }
  else { failures.push(`${name}${detail !== undefined ? ` (${detail})` : ""}`); }
}
function eq(got, want, name) { check(got === want, name, `got ${got} expected ${want}`); }

// ── Minimal fake DOM element ────────────────────────────────────────────
function FakeEl(tagName, ownerDocument) {
  this.tagName = tagName;
  this.nodeType = 1;
  this.ownerDocument = ownerDocument;
  this.attrs = {};
  this.children = [];
  this.style = {};
  this._class = "";
  this.id = "";
}
FakeEl.prototype.setAttribute = function (n, v) { this.attrs[n] = String(v); };
FakeEl.prototype.getAttribute = function (n) { return this.attrs[n]; };
FakeEl.prototype.appendChild = function (c) { this.children.push(c); c.parentNode = this; return c; };
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

const doc = new FakeDocument();
const sandbox = {
  document: doc,
  window: {},
  console: console
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(RIG_PATH, "utf8"), sandbox, { filename: "cloud_rig.js" });

const R = sandbox.window.VMCloudRig;
check(!!R, "VMCloudRig exported");
check(!!R.build && !!R.mount && !!R.collectRig && !!R.mountRoot, "rig API surface present");

// ── Faithful palette (sampled from static/cloud.png) ────────────────────
eq(R.COLORS.ink, "#010409", "ink is near-black (png ~1,4,9)");
eq(R.COLORS.face, "#788286", "face gray-teal (png 120,130,131)");
eq(R.COLORS.leg, "#40464A", "legs dark gray (png 65,70,74)");
eq(R.COLORS.headTop, "#E5EAE8", "head fluff near-white (png ~229,234,230)");

// ── Geometry anchors ────────────────────────────────────────────────────
check(R.GEO.leftEye.x < R.GEO.rightEye.x, "left eye is image-left of right eye");
eq(R.GEO.leftEye.y, R.GEO.rightEye.y, "eyes sit on the same line");
check(R.GEO.leftShoulder.y < R.GEO.leftHip.y, "shoulder above hip");

// ── Build produces all addressable parts ────────────────────────────────
const built = R.build();
const wantParts = ["root", "body", "head", "leftEye", "rightEye", "leftEyebrow",
  "rightEyebrow", "mouth", "leftArm", "rightArm", "leftLeg", "rightLeg",
  "shadow", "leftPupil", "rightPupil"];
wantParts.forEach(function (p) {
  check(built.parts[p] != null, "part present: " + p);
});
eq(built.parts.root.tagName, "svg", "root is an SVG");
// The rig claims the PNG's own canvas (433x577) so it can overlay static/
// cloud.png pixel-exactly (the character content is offset by translate(78,185)).
eq(built.parts.root.attrs["viewBox"], "0 0 433 577", "viewBox equals PNG canvas");
const wrapped = built.parts.root.children.filter(function (c) {
  return c.attrs["transform"] === "translate(78 185)";
});
eq(wrapped.length, 1, "content wrapped for PNG-canvas alignment");

// Every independently-animated part must carry a transform-origin pivot so the
// anim engine can rotate/scale around the correct joint.
["body", "head", "leftEye", "rightEye", "leftEyebrow", "rightEyebrow",
  "mouth", "leftArm", "rightArm", "leftLeg", "rightLeg"].forEach(function (p) {
  const part = built.parts[p];
  check(!!part.style.transformOrigin, "pivot set on " + p, part.style.transformOrigin);
});

// Eyes hold real pupil sub-nodes (needed for independent gaze).
check(built.parts.leftPupil !== built.parts.leftEye, "left pupil is a distinct node");
eq(built.parts.leftPupil.attrs["class"], "cloud-pupil", "pupil class tag present");

// ── Mouth starts on a known expression and keeps its expression marker ──
const mouthPath = built.parts.mouth.querySelector("path");
eq(mouthPath.getAttribute("data-expression"), "neutral", "mouth defaults to neutral smile");

// ── mount() attaches into the rig-mount host exactly once ───────────────
const container = new FakeEl("div", doc);
const rigMount = new FakeEl("div", doc);
rigMount._class = "vmcloud-rig-mount";
container.children.push(rigMount); rigMount.parentNode = container;
container.querySelector = function (sel) { return sel === ".vmcloud-rig-mount" ? rigMount : null; };

const first = R.mount(container);
check(!!first, "first mount succeeds");
eq(rigMount.attrs["data-vm-rig"], "1", "rig-mount marked as rigged");
eq(rigMount.children.length, 1, "one SVG attached");
eq(rigMount.children[0].hasClass("vmcloud-rig-svg"), true, "SVG tagged vmcloud-rig-svg");
check(rigMount.querySelector("[data-part=\"leftEye\"]") != null, "part findable via querySelector");

const second = R.mount(container);
eq(rigMount.children.length, 1, "second mount does not duplicate the rig");
check(!!second.parts.mouth, "collectRig path returns parts");

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);