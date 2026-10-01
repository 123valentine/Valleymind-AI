// Pure-node harness for static/cloud_rig.js using a minimal SVG DOM stub.
// Verifies the layered rig builds the expected addressable cloud parts, keeps
// the cloud palette/geometry, sets per-part pivots, and mounts into a
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

// ── Cloud palette ───────────────────────────────────────────────────────
eq(R.COLORS.cloudTop, "#F4FBFF", "cloud crown = luminous cool white");
eq(R.COLORS.cloudBase, "#BCE2F0", "underside = cool shaded blue");
eq(R.COLORS.edge, "#8CC6DC", "silhouette = soft cloud outline");
eq(R.COLORS.ink, "#16324F", "face ink = deep navy");
eq(R.COLORS.blush, "#FFA9C4", "cheeks = warm blush");
eq(R.COLORS.limbLow, "#A6D2E6", "limb underside = shaded cloud tone");
eq(R.COLORS.limbHigh, "#EAF8FF", "limb tops = luminous cloud white");

// ── Geometry anchors ────────────────────────────────────────────────────
check(R.GEO.leftEye.x < R.GEO.rightEye.x, "left eye is image-left of right eye");
eq(R.GEO.leftEye.y, R.GEO.rightEye.y, "eyes sit on the same line");
check(R.GEO.leftShoulder.y < R.GEO.leftHip.y, "shoulder above hip");
check(R.GEO.nose.x === R.GEO.mouth.x, "nose centred above the mouth");
check(R.GEO.browY < R.GEO.leftEye.y, "eyebrows sit above the eyes");

// ── Build produces all addressable parts ────────────────────────────────
const built = R.build();
const wantParts = ["root", "body", "leftEye", "rightEye", "leftEyebrow",
  "rightEyebrow", "nose", "leftCheek", "rightCheek", "mouth",
  "leftArm", "rightArm", "leftHand", "rightHand",
  "leftLeg", "rightLeg", "shadow", "leftPupil", "rightPupil"];
wantParts.forEach(function (p) {
  check(built.parts[p] != null, "part present: " + p);
});
// Robot-only parts must be gone for good.
["head", "face", "halo", "lowerBody", "leftEar", "rightEar"].forEach(function (p) {
  check(built.parts[p] == null, "robot part removed: " + p);
});
eq(built.parts.root.tagName, "svg", "root is an SVG");
// The rig claims the same canvas as the companion container (433x577); the
// cloud is drawn in FULL canvas space (no PNG translate overlay needed).
eq(built.parts.root.attrs["viewBox"], "0 0 433 577", "viewBox equals companion canvas");
const wrapped = built.parts.root.children.filter(function (c) {
  return c.attrs["transform"] === "translate(78 185)";
});
eq(wrapped.length, 0, "content uses full canvas (cloud, no PNG translate)");

// ── One coherent silhouette, and it must stay on the canvas ─────────────
// Regression guard: a single path drives the rim pass, the gradient fill pass
// and the clip path, so they cannot disagree. The coordinate bounds check
// catches shapes that drift outside the 433x577 viewBox (which renders as a
// mostly-clipped, unrecognisable blob).
const bodyPaths = built.parts.body.queryAll().filter(function (p) { return p.tagName === "path"; });
check(bodyPaths.length >= 2, "body draws a rim pass and a fill pass", bodyPaths.length);
const rimD = bodyPaths[0].getAttribute("d");
const fillD = bodyPaths[1].getAttribute("d");
eq(rimD, fillD, "rim and fill reuse the exact same silhouette path");
check(/^M/.test(rimD) && /Z$/.test(rimD.trim()), "silhouette is one closed subpath");
const silNums = (rimD.match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
const silXs = silNums.filter(function (_, i) { return i % 2 === 0; });
const silYs = silNums.filter(function (_, i) { return i % 2 === 1; });
const silX = Math.min.apply(null, silXs) + ".." + Math.max.apply(null, silXs);
const silY = Math.min.apply(null, silYs) + ".." + Math.max.apply(null, silYs);
check(Math.min.apply(null, silXs) >= 0, "silhouette starts inside the left edge", silX);
check(Math.max.apply(null, silXs) <= 433, "silhouette ends inside the right edge", silX);
check(Math.min.apply(null, silYs) >= 0, "silhouette starts inside the top edge", silY);
check(Math.max.apply(null, silYs) <= 577, "silhouette ends inside the bottom edge", silY);
// Horizontally centred so the companion never sits lopsided in its box.
const silMid = (Math.min.apply(null, silXs) + Math.max.apply(null, silXs)) / 2;
check(Math.abs(silMid - 216.5) < 12, "silhouette is horizontally centred", silMid);

// Every independently-animated part must carry a transform-origin pivot so the
// anim engine can rotate/scale around the correct joint.
["body", "leftEye", "rightEye", "leftEyebrow", "rightEyebrow", "nose",
  "leftCheek", "rightCheek", "mouth", "leftArm", "rightArm", "leftHand",
  "rightHand", "leftLeg", "rightLeg"].forEach(function (p) {
  const part = built.parts[p];
  check(part != null && !!part.style.transformOrigin, "pivot set on " + p, part && part.style.transformOrigin);
});

// Eyes hold real pupil sub-nodes (needed for independent gaze).
check(built.parts.leftPupil !== built.parts.leftEye, "left pupil is a distinct node");
eq(built.parts.leftPupil.attrs["class"], "cloud-pupil", "pupil class tag present");

// ── Mouth starts on a known expression and keeps its expression marker ──
const mouthPath = built.parts.mouth.querySelector("path");
eq(mouthPath.getAttribute("data-expression"), "neutral", "mouth defaults to neutral smile");

// Regression guard: static/cloud_anim.js hardcodes its MOUTH table in rig
// coordinates and swaps `d` on the first pose it applies. If the rig's neutral
// path sits at a different y than MOUTH.neutral, the mouth visibly jumps on
// the first expression change. Keep the two locked together.
const animSrc = fs.readFileSync(path.join(ROOT, "static", "cloud_anim.js"), "utf8");
const animNeutral = (animSrc.match(/neutral:\s*"([^"]+)"/) || [])[1];
check(!!animNeutral, "cloud_anim MOUTH.neutral is locatable");
eq(mouthPath.getAttribute("d"), animNeutral,
   "rig neutral mouth matches cloud_anim MOUTH.neutral (no first-pose jump)");

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