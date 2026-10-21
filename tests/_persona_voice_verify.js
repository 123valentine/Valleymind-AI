// Focused runtime verification of the per-persona voice selectors in
// static/settings.js. Loads the REAL module in a vm sandbox with minimal DOM
// stubs and drives the actual window handlers (vmPersonaSetVoice /
// vmPersonaPreviewVoice / vmPersonaVoicesCard) the way inline onclick does.
//
// This is RUNTIME verification: it executes the shipped code, not a regex.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "settings.js"), "utf8");

let pass = 0;
const fails = [];
function check(name, cond, detail) {
  if (cond) { pass++; }
  else { fails.push(name + (detail ? " :: " + detail : "")); }
}

function makeSandbox(opts) {
  opts = opts || {};
  const store = Object.assign({}, opts.storage || {});
  const storage = opts.storageUnavailable ? {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
    removeItem() { throw new Error("blocked"); },
  } : {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
  };

  const effects = { apiCalls: [], playedSrcs: [], audioEls: [] };

  function El(id) {
    return {
      id, value: "", textContent: "", innerHTML: "", style: {}, checked: false,
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      appendChild() {}, querySelector() { return null; }, querySelectorAll() { return []; },
      setAttribute() {}, getAttribute() { return null; }, focus() {},
    };
  }
  const els = {};
  function byId(id) { return (els[id] = els[id] || El(id)); }

  // Real timer: schedule asynchronously (like a browser) but fast, so the
  // handler's status-clearing setTimeout cannot pre-empt the promise chain.
  const timers = [];
  const sandbox = {
    console,
    setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
    clearTimeout() {},
    __runTimers: () => { timers.splice(0).forEach(fn => { try { fn(); } catch (e) {} }); },
    localStorage: storage,
    // audio player factory: capture the src set by the preview handler.
    document: {
      getElementById: byId,
      querySelector() { return null; },
      querySelectorAll() { return []; },
      createElement(tag) {
        const el = El("_" + tag + "_" + effects.audioEls.length);
        if (tag === "audio") {
          effects.audioEls.push(el);
          Object.defineProperty(el, "src", {
            get() { return this._src; },
            set(v) { this._src = v; effects.playedSrcs.push(v); },
          });
          el.play = () => { effects.playCalled = (effects.playCalled || 0) + 1; return Promise.resolve(); };
        }
        return el;
      },
      body: { appendChild() {} },
      documentElement: { appendChild() {} },
    },
    // apiFetch stub: records calls; failure mode configurable.
    apiFetch(url, o) {
      effects.apiCalls.push({ url, body: o && o.body });
      if (opts.apiFails) return Promise.reject(new Error("network"));
      let payload = { status: "success" };
      if (url.indexOf("/api/tts") !== -1) {
        payload = opts.ttsNoUrl
          ? { status: "success" }
          : { status: "success", url: "/tts/preview.mp3", engine: opts.ttsEngine || "piper_local" };
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
    },
    authHeaders() { return { "Content-Type": "application/json" }; },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "settings.js" });
  return { sandbox, effects, store, els, byId };
}

// ── 1. Module surfaces exist ──────────────────────────────────────────────
{
  const { sandbox } = makeSandbox();
  check("VM_PERSONA_VOICES defined", !!sandbox.VM_PERSONA_VOICES);
  check("four personas present",
    sandbox.VM_PERSONA_VOICES.personas.length === 4,
    JSON.stringify(sandbox.VM_PERSONA_VOICES.personas.map(p => p.key)));
  const keys = sandbox.VM_PERSONA_VOICES.personas.map(p => p.key).sort();
  check("persona keys = marcus/elena/angelina/companion",
    JSON.stringify(keys) === JSON.stringify(["angelina", "companion", "elena", "marcus"]), JSON.stringify(keys));
  check("six voice options (4 ref + 2 piper)", sandbox.VM_PERSONA_VOICES.voices.length === 6);
  const piper = sandbox.VM_PERSONA_VOICES.voices.filter(v => v.piper);
  check("exactly 2 piper opt-in voices", piper.length === 2, JSON.stringify(piper.map(p => p.id)));
  check("piper voices marked non-default", piper.every(p => p.default === false));
  check("handlers exported", typeof sandbox.vmPersonaSetVoice === "function" &&
    typeof sandbox.vmPersonaPreviewVoice === "function" &&
    typeof sandbox.vmPersonaVoicesCard === "function");
  check("shared voice key handler intact", typeof sandbox.vmCompanionSetVoiceKey === "function");
}

// ── 2. Selector rendering: four independent selects, wired to handlers ────
{
  const { sandbox } = makeSandbox();
  const html = sandbox.vmPersonaVoicesCard();
  ["marcus", "elena", "angelina", "companion"].forEach(k => {
    check("select rendered for " + k, html.indexOf('id="vmPersonaVoice-' + k + '"') !== -1);
    check("select " + k + " wired to vmPersonaSetVoice",
      html.indexOf("vmPersonaSetVoice('" + k + "'") !== -1);
    check("preview btn for " + k, html.indexOf("vmPersonaPreviewVoice('" + k + "')") !== -1);
  });
  check("Piper voices appear as options",
    html.indexOf("Kristin (local, opt-in preview)") !== -1 &&
    html.indexOf("LJSpeech (local, opt-in preview)") !== -1);
}

// ── 3. Independent persistence (localStorage) + settings route ────────────
async function flush() {
  // Let queued promise callbacks (the handler's .then/.catch) run.
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

async function main() {
{
  const h = makeSandbox();
  const sandbox = h.sandbox, effects = h.effects, store = h.store;
  sandbox.vmPersonaSetVoice("elena", "kristin_piper");
  sandbox.vmPersonaSetVoice("marcus", "ljspeech_piper");
  await flush();
  const saved = JSON.parse(store["vmPersonaVoices"]);
  check("elena saved independently", saved.elena === "kristin_piper", JSON.stringify(saved));
  check("marcus saved independently", saved.marcus === "ljspeech_piper");
  check("unset personas absent (not clobbered)",
    !("angelina" in saved) && !("companion" in saved), JSON.stringify(saved));
  const post = effects.apiCalls.filter(c => c.url.indexOf("/api/settings/cloud") !== -1);
  check("persisted via existing /api/settings/cloud route", post.length >= 1);
  // Each save sends the cumulative map; the LAST call must carry both selections.
  const body = JSON.parse(post[post.length - 1].body);
  check("route body carries cumulative persona_voices map",
    body && body.persona_voices && body.persona_voices.marcus === "ljspeech_piper" &&
    body.persona_voices.elena === "kristin_piper",
    post[post.length - 1].body);
  check("no new endpoint introduced",
    effects.apiCalls.every(c => c.url.indexOf("/api/settings/") !== -1));
  check("persona save uses merge flag (non-destructive)",
    post.every(c => c.url.indexOf("merge=1") !== -1), JSON.stringify(post.map(c => c.url)));
  check("shared vmVoiceProfile untouched by persona save",
    !("vmVoiceProfile" in store), JSON.stringify(Object.keys(store)));
  check("unknown voice id rejected",
    (() => { const before = JSON.stringify(JSON.parse(store["vmPersonaVoices"]));
      sandbox.vmPersonaSetVoice("elena", "bogus_voice");
      return JSON.stringify(JSON.parse(store["vmPersonaVoices"])) === before; })());
}

// ── 4. Reload persistence + isolation across personas + shared profile ────
{
  // Pre-seed storage as if a prior session saved one persona.
  const h = makeSandbox({
    storage: { vmPersonaVoices: JSON.stringify({ angelina: "kristin_piper" }) },
  });
  const sandbox = h.sandbox, store = h.store;
  // Reload: a fresh render must reflect the saved selection for angelina only.
  const html = sandbox.vmPersonaVoicesCard();
  const angelinaSel = /id="vmPersonaVoice-angelina"[\s\S]*?<option value="([^"]+)" selected/.exec(html);
  check("reload restores angelina selection", angelinaSel && angelinaSel[1] === "kristin_piper", angelinaSel && angelinaSel[1]);
  const marcusSel = /id="vmPersonaVoice-marcus"[\s\S]*?<option value="([^"]+)" selected/.exec(html);
  check("reload leaves marcus on its default", marcusSel && marcusSel[1] === "guy", marcusSel && marcusSel[1]);
  // Change marcus; angelina must be untouched.
  sandbox.vmPersonaSetVoice("marcus", "jenny");
  await flush();
  const after = JSON.parse(store["vmPersonaVoices"]);
  check("changing marcus does not change angelina", after.angelina === "kristin_piper", JSON.stringify(after));
  check("changing marcus does not create shared profile", !("vmVoiceProfile" in store));
}

// ── 5. Failure modes ──────────────────────────────────────────────────────
{
  // (a) settings endpoint fails -> local mirror still written, no throw.
  const h = makeSandbox({ apiFails: true });
  const sandbox = h.sandbox, store = h.store;
  let threw = false;
  try { sandbox.vmPersonaSetVoice("elena", "kristin_piper"); } catch (e) { threw = true; }
  await flush();
  check("save survives settings-endpoint failure", !threw);
  check("local mirror written despite API failure",
    JSON.parse(store["vmPersonaVoices"]).elena === "kristin_piper");
}
{
  // (b) localStorage unavailable -> still no throw, API still attempted.
  const h = makeSandbox({ storageUnavailable: true });
  const sandbox = h.sandbox, effects = h.effects;
  let threw = false;
  try { sandbox.vmPersonaSetVoice("elena", "kristin_piper"); } catch (e) { threw = true; }
  await flush();
  check("save survives blocked localStorage", !threw);
  check("API still called when storage blocked",
    effects.apiCalls.some(c => c.url.indexOf("/api/settings/cloud") !== -1));
}
{
  // (c) Piper voice selected -> preview goes through /api/tts (server routes to
  // the local Piper engine) and plays the returned url; the status reports the
  // ACTUAL engine, never mislabelling it as a reference/device voice.
  const h = makeSandbox();
  const sandbox = h.sandbox, effects = h.effects;
  sandbox.vmPersonaSetVoice("elena", "kristin_piper");
  effects.apiCalls.length = 0; effects.playedSrcs.length = 0;
  sandbox.vmPersonaPreviewVoice("elena");
  await flush();
  const ttsCalls = effects.apiCalls.filter(c => c.url.indexOf("/api/tts") !== -1);
  check("piper preview calls /api/tts", ttsCalls.length === 1, JSON.stringify(effects.apiCalls.map(c => c.url)));
  check("piper preview sends persona only (server reads saved selection)",
    ttsCalls.length && JSON.parse(ttsCalls[0].body).persona === "elena" &&
    !JSON.parse(ttsCalls[0].body).engine && !JSON.parse(ttsCalls[0].body).profile);
  check("piper preview plays returned audio", effects.playedSrcs.length === 1, JSON.stringify(effects.playedSrcs));
  const st = h.byId("vmPersonaVoiceStatus");
  check("piper preview labels the real engine (local Piper)",
    /local Piper/.test(st.textContent), st.textContent);
}
{
  // (d) reference voice preview -> DOES call /api/tts and DOES play returned url.
  const h = makeSandbox();
  const sandbox = h.sandbox, effects = h.effects;
  sandbox.vmPersonaSetVoice("marcus", "guy");
  effects.apiCalls.length = 0; effects.playedSrcs.length = 0;
  sandbox.vmPersonaPreviewVoice("marcus");
  await flush();
  check("reference preview calls /api/tts",
    effects.apiCalls.some(c => c.url.indexOf("/api/tts") !== -1));
  const ttsCall = effects.apiCalls.find(c => c.url.indexOf("/api/tts") !== -1);
  check("reference preview sends persona only (no engine/profile override)",
    ttsCall && JSON.parse(ttsCall.body).persona === "marcus" &&
    !JSON.parse(ttsCall.body).engine && !JSON.parse(ttsCall.body).profile);
  check("reference preview plays the returned url",
    effects.playedSrcs.indexOf("/tts/preview.mp3") !== -1, JSON.stringify(effects.playedSrcs));
  const st = h.byId("vmPersonaVoiceStatus");
  // Run the status-clearing timer only now (a browser would fire it later).
  check("reference preview reports playing",
    /Playing/.test(st.textContent), st.textContent);
}
{
  // (e) reference voice but server returns no url -> honest status, no audio.
  const h = makeSandbox({ ttsNoUrl: true });
  const sandbox = h.sandbox, effects = h.effects;
  sandbox.vmPersonaSetVoice("marcus", "guy");
  effects.playedSrcs.length = 0;
  sandbox.vmPersonaPreviewVoice("marcus");
  await flush();
  check("no-url preview plays nothing", effects.playedSrcs.length === 0);
  const st = h.byId("vmPersonaVoiceStatus");
  check("no-url preview reports unavailable", /unavailable/i.test(st.textContent), st.textContent);
}
{
  // (f) Voice tab registered + section renders a real catalogue.
  const h = makeSandbox();
  const sandbox = h.sandbox;
  check("Voice tab registered in SETTINGS_SECTIONS",
    sandbox.SETTINGS_SECTIONS.some(s => s.id === "voice" && s.label === "Voice"));
  check("renderVoiceSection exported", typeof sandbox.renderVoiceSection === "function");
  const c = h.byId("settingsContent");
  sandbox.renderVoiceSection(c);
  const html = c.innerHTML;
  check("voice section header", html.indexOf("Choose how each speaker sounds") !== -1);
  check("voice catalogue card present", html.indexOf("Available voices") !== -1);
  check("persona assignment card present", html.indexOf("Persona voices") !== -1);
  check("per-voice preview buttons for all six catalogue voices",
    ["guy", "aria", "jenny", "christopher", "kristin_piper", "ljspeech_piper"]
      .every(id => html.indexOf("vmVoicePreview('" + id + "')") !== -1));
  check("reference voice shows its server id",
    html.indexOf("en-US-GuyNeural") !== -1 && html.indexOf("Online reference voice") !== -1);
  check("piper voice shows on-device detail",
    html.indexOf("en_US-kristin-medium") !== -1 && html.indexOf("On-device model") !== -1);
  check("voice tab status span present", html.indexOf('id="vmVoiceTabStatus"') !== -1);
}
{
  // (g) Catalogue preview sends ONLY the specific voice id (server validates it).
  const h = makeSandbox({ ttsEngine: "edge_tts_free" });
  const sandbox = h.sandbox, effects = h.effects;
  sandbox.vmVoicePreview("aria");
  await flush();
  const ttsCall = effects.apiCalls.find(c => c.url.indexOf("/api/tts") !== -1);
  check("catalogue preview calls /api/tts", !!ttsCall);
  check("catalogue preview sends the specific voice only",
    ttsCall && JSON.parse(ttsCall.body).voice === "aria" &&
    !JSON.parse(ttsCall.body).persona && !JSON.parse(ttsCall.body).engine);
  check("catalogue preview plays returned audio",
    effects.playedSrcs.indexOf("/tts/preview.mp3") !== -1, JSON.stringify(effects.playedSrcs));
  const st = h.byId("vmVoiceTabStatus");
  check("catalogue preview reports the engine", /free Edge voice/.test(st.textContent), st.textContent);
}
{
  // (h) Catalogue preview reports the ACTUAL engine when Piper falls back.
  const h = makeSandbox({ ttsEngine: "edge_tts_free" });
  const sandbox = h.sandbox;
  // Simulate a fallback_from response by overriding the mock for this call.
  sandbox.apiFetch = function () {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(
      { status: "success", url: "/tts/f.mp3", engine: "edge_tts_free", fallback_from: "piper_local" }) });
  };
  sandbox.vmVoicePreview("kristin_piper");
  await flush();
  const st = h.byId("vmVoiceTabStatus");
  check("fallback labelled honestly (unavailable, not the chosen voice)",
    /unavailable/.test(st.textContent) && /free Edge voice/.test(st.textContent), st.textContent);
}
{
  // (i) The Voice tab does NOT collide with the Companion tab's DOM ids:
  // the persona card is no longer rendered inside Companion.
  const h = makeSandbox();
  const sandbox = h.sandbox;
  sandbox.renderCompanionInto(h.byId("settingsContent"));
  const comp = h.byId("settingsContent").innerHTML;
  check("Companion tab no longer embeds the persona voices card",
    comp.indexOf("Persona voices") === -1);
}
}

main().then(() => {
  console.log(JSON.stringify({ passed: pass, failed: fails }, null, 2));
  process.exit(fails.length ? 1 : 0);
});