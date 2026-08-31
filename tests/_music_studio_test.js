// Pure-node test runner for static/music_studio.js.
// Uses minimal DOM/browser stubs (module loaded via require, so stubs live on
// global) to exercise the authoritative Music Studio state: mode switching,
// voice + consent gating, save/load/delete persistence, and AI-result rendering.
// Prints a JSON summary and exits non-zero on any failure.
"use strict";

const path = require("path");
const ROOT = path.resolve(__dirname, "..");
const MODULE_PATH = path.join(ROOT, "static", "music_studio.js");

const failures = [];
let passes = 0;
function check(cond, name, detail) {
  if (cond) { passes++; }
  else { failures.push(`${name}${detail !== undefined ? ` (${detail})` : ""}`); }
}
function eq(got, want, name) { check(got === want, name, `got ${got} expected ${want}`); }
function count(html, re) { return (String(html).match(re) || []).length; }

// ── Minimal browser/DOM stubs (persist across the single module load) ──
const panel = { innerHTML: "" };
const elems = Object.create(null);
const store = Object.create(null);
const elStub = (id) => ({
  tagName: "div", id, textContent: "", className: "", innerHTML: "", value: "",
  style: {}, onclick: null,
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, appendChild() {}, setAttribute() {}
});
const toastEl = () => ({
  textContent: "", style: {}, _t: null,
  classList: { add() {}, remove() {}, contains: () => false }
});

global.window = global;
global.document = {
  readyState: "complete",
  getElementById(id) {
    if (id === "vmWsPanelMusic") return panel;
    if (id === "vmMusicCSS") return null;
    if (id === "vmMusicToast") return toastEl();
    if (!elems[id]) elems[id] = elStub(id);
    return elems[id];
  },
  createElement: () => elStub(""),
  head: { appendChild() {} },
  body: { appendChild() {}, removeChild() {} },
  addEventListener() {}
};
global.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; }
};
global.apiFetch = async () => ({ json: async () => ({}) });
global.authHeaders = (h) => h;
global.fetch = async () => ({ json: async () => ({}) });

delete global.window.vmMusicAPI;
delete global.vmMusicOnShow;
require(MODULE_PATH);
const api = global.vmMusicAPI;
const onShow = global.vmMusicOnShow;

// ── 1. Public API surface ─────────────────────────────────────────────
["onMode", "toggleRecord", "playAudio", "onVoice", "onConsent",
 "runAI", "saveSong", "newSong", "loadSong", "deleteSong", "exportSong"]
  .forEach((k) => check(typeof api[k] === "function", `api.${k} is a function`));
check(typeof onShow === "function", "vmMusicOnShow is a function");

// ── 2. Initial render is balanced and shows both modes ────────────────
onShow();
let html = panel.innerHTML;
eq(count(html, /<div\b/g), count(html, /<\/div>/g), "initial render div balance");
check(html.includes("Do it yourself"), "DIY mode card present");
check(html.includes("Let ValleyMind produce it"), "AI mode card present");
check(html.includes("vmMusicAPI.saveSong"), "save action wired");
check(html.includes('vmMusicRecBtn'), "record button present");

// ── 3. AI mode shows brief + produce action, hides DIY lyrics editor ──
api.onMode("ai");
html = panel.innerHTML;
check(html.includes('id="vmMusicBrief"'), "AI mode shows brief textarea");
check(html.includes("vmMusicAPI.runAI"), "AI mode shows produce button");
check(!html.includes('id="vmMusicLyrics"'), "AI mode hides DIY lyrics editor");
eq(count(html, /<div\b/g), count(html, /<\/div>/g), "AI render div balance");

// ── 4. Voice options + consent gating ─────────────────────────────────
api.onVoice("keep");
html = panel.innerHTML;
check(!html.includes("Authorization to clone my voice"), "clone consent hidden for keep voice");
api.onVoice("clone");
html = panel.innerHTML;
check(html.includes("Authorization to clone my voice"), "clone consent shown for clone voice");
check(html.includes('value="clone"'), "clone voice option present");
api.onVoice("elena");
html = panel.innerHTML;
check(!html.includes("Authorization to clone my voice"), "consent hidden for Elena voice");

// ── 5. Save / load / delete persistence ───────────────────────────────
api.onVoice("keep");
api.onMode("diy");
onShow();
elems["vmMusicName"].value = "Test Song";
elems["vmMusicLyrics"].value = "La la la";
api.saveSong();
let proj = JSON.parse(store["vmMusicProjects"] || "[]");
eq(proj.length, 1, "one project saved");
eq(proj[0].name, "Test Song", "saved project name persists");
const savedId = proj[0].id;
check(typeof savedId === "string" && savedId.length > 0, "saved project has an id");

api.saveSong();
proj = JSON.parse(store["vmMusicProjects"] || "[]");
eq(proj.length, 1, "re-save updates existing project (no duplicate)");

api.newSong();
html = panel.innerHTML;
check(/id="vmMusicName" value="Untitled song"/.test(html), "new song resets the name field");

api.loadSong(savedId);
html = panel.innerHTML;
check(html.includes("Test Song"), "loaded project renders its name");

api.deleteSong(savedId);
proj = JSON.parse(store["vmMusicProjects"] || "[]");
eq(proj.length, 0, "delete removes the project");

// ── 6. AI result renders honestly, never claims finished audio ────────
const aiProject = {
  id: "aiproj1", name: "AI Song", mode: "ai", savedAt: Date.now(),
  role: "Singer", genre: "Afrobeats", mood: "Romantic", tempo: "Medium",
  key: "", language: "English", brief: "romantic", lyrics: "",
  voice: "elena", consent: true,
  take: { name: "", url: "", dur: 0 }, beat: { name: "", url: "", dur: 0 },
  aiResult: {
    generated: true, title: "Midnight", structure: "Intro-Verse-Chorus",
    lyrics: "Stars above", arrangement: "Log drums, shakers, 100 BPM",
    note: "Lyrics + arrangement ready now; final audio rendering is a future step."
  }
};
store["vmMusicProjects"] = JSON.stringify([aiProject]);

// loadProjects() runs only at module init, so reload a fresh module instance
// that will read the seeded store on startup before loading the project.
const resPath = require.resolve(MODULE_PATH);
delete require.cache[resPath];
delete global.vmMusicAPI;
delete global.vmMusicOnShow;
require(resPath);
const api2 = global.vmMusicAPI;
const onShow2 = global.vmMusicOnShow;
onShow2();
api2.loadSong("aiproj1");
html = panel.innerHTML;
check(html.includes("Midnight"), "AI title renders");
check(html.includes("Stars above"), "AI lyrics render");
check(html.includes("future step"), "AI note is honest about pending rendering");

// ── 7. New features: cloud sync, effect chips, beat library ────────────
check(typeof onShow2 === "function", "vmMusicOnShow still a function");
html = panel.innerHTML;
check(html.includes("vmm-chip"), "vocal effect chips render");
check(html.includes("applyEffect"), "effect chip actions wired");
check(html.includes("Beat Library"), "Beat Library panel present");
check(html.includes("vmm-beat"), "beat cards render");
check(html.includes("Lagos Midnight"), "first curated beat renders");
check(html.includes("Bayelsa River"), "twentieth curated beat renders");
check(html.includes("20 African"), "beat library description present");
check(html.includes("Saved songs follow you"), "cloud-sync projects subtitle present");
eq(count(html, /<div\b/g), count(html, /<\/div>/g), "chips+beats render div balance");

["applyEffect", "previewBeat", "selectBeat", "stopPreview"]
  .forEach((k) => check(typeof api2[k] === "function", `api2.${k} is a function`));

// Cloud sync uses apiFetch POST on save (apiFetch stub returns {}).
api2.onMode("diy");
elems["vmMusicName"].value = "Cloud Song";
elems["vmMusicLyrics"].value = "sync me";
api2.saveSong();
proj = JSON.parse(store["vmMusicProjects"] || "[]");
check(proj.length >= 1, "save still persists locally with cloud sync");

// ── summary ────────────────────────────────────────────────────────────
const out = { passed: passes, failed: failures };
console.log(JSON.stringify(out));
process.exit(failures.length ? 1 : 0);
