// Pure-node test runner for static/pref_setup_model.js.
// No DOM — exercises the single source of truth for the 14-step completion.
// Prints a JSON summary and exits non-zero on any failure.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const MODEL_PATH = path.join(ROOT, "static", "pref_setup_model.js");

const failures = [];
let passes = 0;
function check(cond, name, detail) {
  if (cond) { passes++; }
  else { failures.push(`${name}${detail !== undefined ? ` (${detail})` : ""}`); }
}
function eq(got, want, name) { check(got === want, name, `got ${got} expected ${want}`); }

const sandbox = { module: { exports: {} } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(MODEL_PATH, "utf8"), sandbox, { filename: "pref_setup_model.js" });
const M = sandbox.module.exports;

// ── Step vocabulary for building exact-N-complete states ────────────
// 12 non-structural steps in wizard order. Each entry answers one step.
const DATA = [
  { preferences: { use_cases: ["Coding"] } },                        // use
  { preferences: { use_case_profile: "Solo coder" } },              // useprofile
  { language: { country: "Nigeria" } },                             // lang
  { preferences: { communication_style: ["Direct"] } },             // style
  { language: { native_languages: ["Igbo"] } },                     // background
  { culture: { cultural_expression: "Natural" } },                  // expression
  { preferences: { expressive_language: ["Playful"] } },            // expressive
  { preferences: { multilingual_behavior: "Reply in user language" } }, // multilingual
  { preferences: { preferred_characters: ["Marcus"] } },            // characters
  { preferences: { voice_style: "Friendly" } },                     // voice
  { preferences: { custom_preference: "Keep short" } },             // custom
  { preferences: { about_me: "Night coder" } },                     // aboutme
];

function withSteps(k, extra) {
  const st = { setup_status: "not_started", language: {}, preferences: {}, culture: {}, meta: {} };
  for (let i = 0; i < Math.min(k, DATA.length); i++) {
    const d = DATA[i];
    if (d.language) Object.assign(st.language, d.language);
    if (d.preferences) Object.assign(st.preferences, d.preferences);
    if (d.culture) Object.assign(st.culture, d.culture);
  }
  // Reaching the characters page (or interacting) makes the user's selection
  // real; before that, the draft's seeded all-on default stays "unanswered".
  st.meta = { charactersSeeded: k <= 8 };
  if (extra) Object.assign(st, extra);
  return st;
}

// ── Structure ──────────────────────────────────────────────────────
const EXPECTED_IDS = ["intro", "use", "useprofile", "lang", "style", "background",
  "expression", "expressive", "multilingual", "characters", "voice", "custom",
  "aboutme", "review"];
eq(M.totalSteps(), 14, "total steps = 14");
eq(JSON.stringify(M.stepIds()), JSON.stringify(EXPECTED_IDS), "step ids match wizard pages");

// ── Percentage derivation (nearest whole number, no mapping table) ──
eq(M.percentage(withSteps(0)), 0, "0/14 → 0%");
eq(M.percentage(withSteps(1)), 7, "1/14 → 7%");
eq(M.percentage(withSteps(2)), 14, "2/14 → 14%");
eq(M.percentage(withSteps(5)), 36, "5/14 → 36%");
eq(M.percentage(withSteps(11)), 79, "11/14 → 79%");
eq(M.percentage(withSteps(12)), 86, "12/14 → 86% (unfinished caps below 100)");
eq(M.percentage(withSteps(12, { setup_status: "skipped" })), 86, "skipped caps below 100");

// ── Structural steps via genuine finish only ───────────────────────
eq(M.percentage(withSteps(12, { setup_status: "completed" })), 100, "completed + 12 data → 100%");
eq(M.percentage(withSteps(11, { setup_status: "completed" })), 93, "completed + 11 data → 93%");
eq(M.percentage(withSteps(5, { setup_status: "completed" })), 50, "completed + 5 data → 50%");
eq(M.percentage(withSteps(0, { setup_status: "completed" })), 14, "completed but no data → 14% (finish alone is not completion)");

// ── Live derivation on state change (no caching, no second system) ──
{
  const st = { setup_status: "not_started", language: {}, preferences: {}, culture: {}, meta: {} };
  eq(M.percentage(st), 0, "dynamic: empty → 0");
  st.language.country = "Nigeria";
  eq(M.percentage(st), 7, "dynamic: country added → 7");
  st.preferences.communication_style = ["Direct"];
  eq(M.percentage(st), 14, "dynamic: style added → 14");
  st.setup_status = "completed";
  eq(M.percentage(st), 29, "dynamic: finish + lang + style → 4/14 = 29%");
  eq(M.completedSteps(st), st.setup_status === "completed" ? 4 : 0, "completedSteps derives, not stored");
}

// ── Identity / about-me contributes ───────────────────────────────
eq(M.percentage({ setup_status: "not_started", language: {}, preferences: { about_me: "x" }, culture: {}, meta: {} }), 7, "identity alone → 1/14 = 7%");
eq(M.isStepComplete({ setup_status: "not_started", language: {}, preferences: { about_me: "x" }, culture: {}, meta: {} }, 12), true, "aboutme step complete with text");

// ── Characters: seeded default is not the user's completion ────────
{
  const seeded = { setup_status: "not_started", language: {}, preferences: { preferred_characters: ["Marcus", "Angelina", "Elena"] }, culture: {}, meta: { charactersSeeded: true } };
  eq(M.isStepComplete(seeded, 9), false, "seeded all-on default does NOT complete characters");
  const touched = { setup_status: "not_started", language: {}, preferences: { preferred_characters: ["Marcus", "Angelina", "Elena"] }, culture: {}, meta: { charactersSeeded: false } };
  eq(M.isStepComplete(touched, 9), true, "explicit touch completes characters");
  const legacy = { setup_status: "not_started", language: {}, preferences: { preferred_characters: "Marcus,Angelina" }, culture: {}, meta: { charactersSeeded: false } };
  eq(M.isStepComplete(legacy, 9), true, "legacy comma-string characters are real", legacy.preferences.preferred_characters);
}

// ── Country: seeded Nigeria default is state truth, not a fabricated step ──
{
  const seeded = { setup_status: "not_started", language: { country: "Nigeria" }, preferences: {}, culture: {}, meta: { countrySeeded: true } };
  eq(M.isStepComplete(seeded, 3), false, "seeded default country does NOT complete lang");
  eq(M.percentage(seeded), 0, "seeded default keeps fresh user at 0%");
  const accepted = { setup_status: "not_started", language: { country: "Nigeria" }, preferences: {}, culture: {}, meta: { countrySeeded: false } };
  eq(M.isStepComplete(accepted, 3), true, "persisted/accepted country completes lang");
  eq(M.percentage(accepted), 7, "persisted country counts 7%");
  const changed = { setup_status: "not_started", language: { country: "Algeria" }, preferences: {}, culture: {}, meta: { countrySeeded: true } };
  eq(M.isStepComplete(changed, 3), true, "even a stale seed flag never hides a real, different value");
  const regionOnly = { setup_status: "not_started", language: { country: "Nigeria", state_province: "Lagos" }, preferences: {}, culture: {}, meta: { countrySeeded: true } };
  eq(M.isStepComplete(regionOnly, 3), true, "region/state data still completes lang regardless of seed");
}

// ── Navigation (pure clamp, no state mutation) ─────────────────────
eq(M.nextStep(0, -1), 0, "back beyond start clamps to 0");
eq(M.nextStep(0, 1), 1, "continue advances");
eq(M.nextStep(13, 1), 13, "continue beyond end stays");
eq(M.nextStep(7, -1), 6, "back steps");
eq(M.nextStep(7, 0), 7, "zero offset no-op");
{
  const st = withSteps(1);
  const before = JSON.stringify(st);
  M.nextStep(2, 1);
  check(JSON.stringify(st) === before, "navigation does not mutate state");
}

// ── isComplete only at true 14/14 ─────────────────────────────────
check(M.isComplete(withSteps(12, { setup_status: "completed" })), "100% ⇔ genuinely complete");
check(!M.isComplete(withSteps(12)), "unfinished is never 100%");
check(!M.isComplete(withSteps(11, { setup_status: "completed" })), "12/13 data never 100%");

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);