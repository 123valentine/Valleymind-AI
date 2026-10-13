/*
 * vrm_companion.js -- the persistent VRM desktop-companion overlay for the
 * main ValleyMind app shell.
 *
 * It REUSES, and never duplicates:
 *   - the existing body engine  -> /vrm-preview/assets/companion.js (the very
 *     same module the /vrm-preview page runs; its assets are served under
 *     /vrm-preview/... and only the import map is provided here),
 *   - the existing face channel -> face.js (lip-sync via setTalkLevel()),
 *   - the existing brain        -> POST /api/cloud/chat (Cloud's personified
 *     adapter of MarcusBrain + per-user memory) and POST /chat/sessions,
 *   - the existing voice        -> window.VMCloudVoice (TTS + STT + amplitude),
 *   - the existing screen view  -> window.VMCloudVision (permission-first).
 *
 * Cloud and the VRM companion are mutually exclusive: exactly ONE is visible,
 * chosen by the persisted `mode` pref ("vrm" | "cloud"). The app shell's
 * setAppVisible() calls vmVrmCompanionDispatch(); CSS (body[data-vm-companion])
 * hides whichever character is not active.
 */

import { Companion, SKIN_PRESETS } from "/vrm-preview/assets/companion.js";

const PREFS_KEY = "valleymind.vrm_companion";
// Separate, coarse consent for auto-listening. This is the FIRST-USE access
// decision ("Allow ValleyMind Companion access?"), persisted once and kept
// distinct from per-action task permissions ("allow click/type", etc.).
const ACCESS_KEY = "valleymind.companion_access";
const DEFAULTS = {
  mode: "vrm",       // "vrm" (default companion) | "cloud" (legacy character)
  visible: true,     // keep the VRM overlay visible across sessions (recoverable in Settings)
  sizePct: 16,       // overlay height as % of viewport height (12-18 recommended)
  skin: null,        // SKIN_PRESETS name, or null = original VRM colors
  dock: "br",        // "br" | "bl" | "tr" | "tl" (used until the avatar is moved)
  x: null,           // manual position (px) once placed
  y: null,
  charName: "ValleyMind", // display name (shown in Settings; keeps CJK glyphs intact)
  voiceKey: "",      // "marcus" | "elena" | "angelina" ("" = follow the Cloud preference)
  visionOn: false,   // user preference: attach screen context to the brain
  chatOpen: false,
  taskOpen: false,   // task panel visibility (opened from Settings / auto on confirm)
  taskPermissions: [], // granted task permissions (vision/scroll/navigate/click/type)
  muted: false,      // hands-free voice off (auto-listen + spoken replies suppressed)
  voiceFirst: true,  // resume hands-free listening after each turn (access-permitted)
};

const STATUS_LABEL = {
  idle: "idle",
  listening: "listening…",
  thinking: "thinking…",
  speaking: "speaking",
  happy: "happy",
  curious: "curious",
  sad: "sad",
  angry: "angry",
  confused: "confused",
  working: "working",
  observing: "watching your screen",
};

const STATUS_BEHAVIOUR = {
  idle: "idle",
  listening: "listening",
  thinking: "thinking",
  speaking: "talking",
  happy: "happy",
  curious: "listening",
  sad: "sad",
  angry: "angry",
  confused: "unsure",
  working: "working",
  observing: "listening",
};

// States that must stay locked to one clip until the driver changes them.
// Transient accents (happy / sad / ...) just nudge the auto state machine.
const DRIVEN = new Set(["listening", "thinking", "speaking", "working"]);

const EMOTION_STATUS = {
  neutral: "idle",
  curious: "curious",
  excited: "happy",
  happy: "happy",
  thinking: "thinking",
  focused: "thinking",
  sad: "sad",
  angry: "angry",
  frustrated: "confused",
  surprised: "curious",
  confused: "confused",
  concerned: "confused",
};

// ── persisted prefs ───────────────────────────────────────────────────────
let prefs = null;

function readPrefs() {
  let raw = {};
  try { raw = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}"); } catch (e) { raw = {}; }
  const p = Object.assign({}, DEFAULTS);
  for (const k of Object.keys(DEFAULTS)) {
    if (raw[k] !== undefined && raw[k] !== null) p[k] = raw[k];
  }
  return p;
}

function savePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) { /* storage blocked */ }
}

function getMode() { return prefs ? prefs.mode : DEFAULTS.mode; }

// ── first-use access consent ──────────────────────────────────────────────
function accessDecision() {
  let value = null;
  try { value = localStorage.getItem(ACCESS_KEY); } catch (e) { value = null; }
  return value === "allowed" || value === "declined" ? value : null;
}

function setAccessDecision(value) {
  try {
    if (value === "allowed" || value === "declined") localStorage.setItem(ACCESS_KEY, value);
    else localStorage.removeItem(ACCESS_KEY);
  } catch (e) { /* storage blocked */ }
}

function accessAllowed() { return accessDecision() === "allowed"; }

// The app shell force-shows the legacy Cloud character with an INLINE
// `display:block !important` (vmCloudCharacterShow), and an inline !important
// outranks the stylesheet's `body[data-vm-companion="vrm"] #vmCloudCharacter`
// rule. Mutual exclusion is therefore enforced at the same cascade level: the
// active character keeps no inline override and the inactive one is pinned with
// an inline `display:none !important` so exactly ONE companion is ever visible.
function applyCharacterVisibility() {
  const vrmActive = getMode() === "vrm";
  for (const id of ["vmCloudCharacter", "vmCloudCompanion"]) {
    const el = $id(id);
    if (!el) continue;
    if (vrmActive) el.style.setProperty("display", "none", "important");
    else el.style.removeProperty("display");
  }
}

function setDataset() {
  if (document.body) document.body.setAttribute("data-vm-companion", getMode());
  applyCharacterVisibility();
}

// ── tiny DOM helpers (no framework — matches the app shell) ───────────────
function $id(id) { return document.getElementById(id); }
function on(el, type, fn) { if (el) el.addEventListener(type, fn); }

// ── shared state ──────────────────────────────────────────────────────────
let engine = null;      // Companion instance (stays alive across hide/show)
let readyP = null;      // Promise that resolves when the engine is bootable
let booted = false;
let stateKey = "idle";
let busy = false;
let turn = 0;
let voiceTurn = 0;
let voiceEndEmo = "idle";
let chatId = "";
let transcript = [];
let voiceWired = false;
let uiBuilt = false;
let resumeTimer = null;   // delayed hands-free re-listen (cleared on stop/hide/mute)
let walkRaf = 0;          // walking animation handle (cancel on hide/stop)

let lastEngineLog = "";

// The avatar no longer shows a clip/debug label (that chrome was removed from
// the main view); the engine's own log is kept in memory for diagnostics and
// echoed to the console only. Nothing is painted over the character.
function logLine(text) {
  lastEngineLog = String(text == null ? "" : text).slice(0, 200);
  try {
    if (window.console && typeof console.debug === "function") {
      console.debug("[companion]", lastEngineLog);
    }
  } catch (e) { /* non-fatal */ }
}

// ── engine (lazy boot: no GPU until the VRM companion is actually shown) ──
async function getEngine() {
  if (engine) return engine;
  const canvas = $id("vmVrmCompanionCanvas");
  if (!canvas) return null;
  engine = new Companion(canvas, logLine, { transparent: true });
  await engine.boot();
  booted = true;
  if (prefs.skin) { try { engine.setSkinTone(prefs.skin); } catch (e) { } }
  // Event-driven only: the autonomous random state machine stays OFF for the
  // desktop companion so idle never cycles through unrelated clips. Every clip
  // is chosen by setStatus() from a real app event.
  engine.setAuto(false);
  return engine;
}

// ── behaviour / status mapping (event-driven) ───────────────────────────────
// setStatus is the ONE seam between app events and the avatar. It never turns
// the engine's autonomous random loop back on: driven states stay locked to a
// matching clip, idle plays an idle clip, and a momentary reaction plays once
// then settles back to idle. This is the single animation controller for the
// desktop companion (the Cloud character uses its own separate rig).
let accentTimer = null;

function clearAccentTimer() {
  if (accentTimer) { clearTimeout(accentTimer); accentTimer = null; }
}

function setStatus(status) {
  const s = STATUS_BEHAVIOUR[status] ? status : "idle";
  if (s === stateKey && engine && engine.status.stage === "ready") {
    paintStatus();
    return;
  }
  clearAccentTimer();
  stateKey = s;
  // Any non-speaking state must leave the mouth shut: the amplitude listener
  // only runs while audio plays, so without this the mouth stayed open (or kept
  // wobbling on the simulated envelope) after speech, a stop or a mute.
  if (s !== "speaking") setFaceTalking(false);
  if (engine && engine.status.stage === "ready") {
    const behaviour = STATUS_BEHAVIOUR[s];
    engine.setAuto(false);
    if (DRIVEN.has(s)) {
      // listening / thinking / speaking / working hold their clip until an
      // event changes the state; no auto-cycling can interrupt them.
      engine.trigger(behaviour);
    } else if (s === "idle") {
      // Idle is the resting state: a real idle clip (breathing/blinking), never
      // an unrelated animation.
      engine.trigger("idle");
    } else if (behaviour && behaviour !== "idle") {
      // A momentary conversational/app reaction (happy/sad/curious/…) plays
      // once, then the companion returns to idle on its own.
      engine.trigger(behaviour);
      const reaction = s;
      accentTimer = setTimeout(() => {
        accentTimer = null;
        if (stateKey === reaction && !busy) setStatus("idle");
      }, 3200);
    }
  }
  paintStatus();
}

function paintStatus() {
  // Nothing is drawn on the avatar any more. The only live status readout is
  // the line inside Settings → Companion, updated only while it is on screen.
  const line = $id("vmCompanionStatusLine");
  if (line) line.textContent = STATUS_LABEL[stateKey] || stateKey;
}

// ── overlay geometry (size / position / dock / drag / Home) ───────────────
function applySize() {
  const el = $id("vmVrmCompanion");
  if (!el) return;
  el.style.height = prefs.sizePct + "vh";
  if (engine) engine.resize();
}

function dockOffsets(dock) {
  const gap = 18;
  if (dock === "bl") return { top: "auto", right: "auto", left: gap + "px", bottom: gap + "px" };
  if (dock === "tr") return { top: gap + "px", right: gap + "px", bottom: "auto", left: "auto" };
  if (dock === "tl") return { top: gap + "px", right: "auto", bottom: "auto", left: gap + "px" };
  return { top: "auto", right: gap + "px", bottom: gap + "px", left: "auto" }; // br
}

function applyPosition() {
  const el = $id("vmVrmCompanion");
  if (!el) return;
  if (prefs.x != null && prefs.y != null) {
    el.style.left = Math.max(0, prefs.x) + "px";
    el.style.top = Math.max(0, prefs.y) + "px";
    el.style.right = "auto";
    el.style.bottom = "auto";
  } else {
    const o = dockOffsets(prefs.dock || "br");
    el.style.left = o.left;
    el.style.right = o.right;
    el.style.top = o.top;
    el.style.bottom = o.bottom;
    prefs.x = null;
    prefs.y = null;
  }
}

function home() {
  prefs.x = null;
  prefs.y = null;
  prefs.dock = DEFAULTS.dock;
  prefs.sizePct = DEFAULTS.sizePct;
  savePrefs();
  applySize();
  applyPosition();
  syncControls();
}

// The character is a standalone, stateless overlay now: there is no drag
// handle, so it always sits at its docked corner (or where purposeful
// movement left it) and can never be lost. Position is reset from the main
// Settings → Companion section.

// ── chat UI ───────────────────────────────────────────────────────────────
function paintTranscript() {
  const log = $id("vmVrmCompanionLog");
  if (!log) return;
  let html = "";
  for (const t of transcript) {
    html += '<div class="vmvrm-msg ' + t.role + '">' +
      '<div class="vmvrm-msg-role">' + (t.role === "user" ? "You" : "Companion") + "</div>" +
      '<div class="vmvrm-msg-body">' + escapeHtml(t.text) + "</div></div>";
  }
  if (!transcript.length) {
    html += '<div class="vmvrm-msg ai"><div class="vmvrm-msg-body dim">Say hello — this runs on your existing ValleyMind brain and memory.</div></div>';
  }
  log.innerHTML = html;
  log.scrollTop = log.scrollHeight;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function pushChat(role, text) {
  transcript.push({ role: role, text: text, ts: Date.now() });
  if (transcript.length > 40) transcript.shift();
  paintTranscript();
}

// ── brain plumbing (the SAME endpoints the Cloud companion uses) ──────────
// The brain adapter is BLOCKING and walks a provider fallback chain, so its
// worst case is longer than a single provider call. Both the transport timeout
// and the companion's own wall-clock budget use this value so the wrapper can
// never cut a request off BEFORE the transport would have (which reported
// healthy-but-slow replies as "took too long").
const CHAT_BUDGET_MS = 120000;

function apiPost(path, body) {
  const post = window.apiFetch || function (url, opts) { return fetch(url, opts); };
  const headers = window.authHeaders
    ? window.authHeaders({ "Content-Type": "application/json" })
    : { "Content-Type": "application/json" };
  return post(path, {
    method: "POST",
    credentials: "include",
    headers: headers,
    body: JSON.stringify(body),
    timeoutMs: CHAT_BUDGET_MS,
  });
}

async function ensureChat() {
  if (chatId) return chatId;
  try {
    const resp = await apiPost("/chat/sessions", { title: "ValleyMind Companion" });
    const data = await resp.json();
    if (data && data.status === "success" && data.session && data.session.chat_id) {
      chatId = data.session.chat_id;
    }
  } catch (e) { /* fall back to a local id */ }
  if (!chatId) chatId = "vrm_companion_" + Date.now();
  return chatId;
}

function screenFrame() {
  if (!prefs.visionOn || !window.VMCloudVision) return "";
  try { return window.VMCloudVision.capture(720) || ""; } catch (e) { return ""; }
}

// The companion's post-reply emotion is grounded in the ACTUAL exchange -- the
// user's message and the companion's reply -- NEVER in the backend's
// d.state.emotion. That field is the Cloud section's default state (it does not
// describe this turn), so trusting it replayed a stale "angry" after any
// unrelated message such as a plain greeting. Cues must be strong and
// unambiguous; otherwise the companion settles to a neutral idle.
const EMOTION_CUES = {
  angry: /\b(i'?m (?:so |really )?(?:angry|furious|mad|upset|livid)|i (?:hate|can'?t stand) (?:this|that|it)|this is (?:ridiculous|unacceptable|infuriating)|pissed off|fed up)\b/i,
  happy: /\b(congratulations|congrats|great news|awesome|amazing|wonderful|fantastic|so glad|delighted|well done|nice work|that'?s great|love (?:it|this))\b/i,
  sad: /\b(unfortunately|i'?m (?:so )?sorry|condolences|heartbroken|sad news|that'?s (?:sad|unfortunate))\b/i,
  confused: /\b(i'?m not sure|i'?m unsure|i don'?t understand|not sure (?:what|how|why)|could you clarify|i (?:couldn'?t|could not) (?:find|reach)|something went wrong)\b/i,
  curious: /\b(tell me more|that'?s interesting|how (?:does|do|can) (?:that|this|it|you)|what if|would you like to know)\b/i,
};

// Mirror the user's own clear emotion first (anger ONLY when the user is
// genuinely angry), then let the reply's own tone show; default to idle.
function emotionForReply(userText, reply) {
  const user = String(userText || "");
  const ai = String(reply || "");
  for (const key of ["angry", "happy", "sad"]) {
    if (EMOTION_CUES[key].test(user)) return EMOTION_STATUS[key];
  }
  for (const key of ["confused", "sad", "happy", "curious"]) {
    if (EMOTION_CUES[key].test(ai)) return EMOTION_STATUS[key];
  }
  return "idle";
}

// ── voice-first routing ───────────────────────────────────────────────────
// A spoken or typed instruction first checks whether it is a control word for an
// in-flight task (approve / deny / stop / pause / resume), then whether it is a
// clearly actionable request (which runs through the EXISTING task layer), and
// otherwise it is ordinary conversation on the SAME brain. No second brain,
// voice or animation pipeline is introduced.
const TASK_WORKSPACE = /\b(studio|ai builder|builder|website|sketch|3d|threed|three d|round ?table|council|music|editing|analytics|workspace)\b/i;
const TASK_UI_ACTION = /\b(scroll|click|press|type|fill|read the page|look at the screen|inspect the (?:page|screen)|what'?s on (?:the )?screen)\b/i;
const TASK_NAV = /\b(open|go to|navigate to|take me to|switch to|bring up)\b/i;

function looksLikeTask(text) {
  const low = String(text || "").toLowerCase();
  if (TASK_UI_ACTION.test(low)) return true;
  return TASK_NAV.test(low) &&
    (TASK_WORKSPACE.test(low) || /https?:\/\/|\/[A-Za-z]/.test(low) || /\bpage\b|\bworkspace\b|\bscreen\b/.test(low));
}

function taskWords(text) {
  const norm = " " + String(text || "").toLowerCase()
    .replace(/[^a-z0-9'\s]/g, " ").replace(/\s+/g, " ").trim() + " ";
  return {
    stop: /\b(stop|cancel|cancel that|never ?mind|abort|forget it)\b/.test(norm),
    deny: /\b(deny|no|don't|do not|reject|nope)\b/.test(norm),
    approve: /\b(approve|yes|yeah|okay|ok|go ahead|do it|confirm|proceed|sure)\b/.test(norm),
    pause: /\b(pause|hold on|hold that|wait)\b/.test(norm),
    resume: /\b(resume|continue|carry on|go on)\b/.test(norm),
  };
}

// Returns true when the utterance was consumed as a control word for an active
// or awaiting task (so it must not also be sent as a brand-new instruction).
function resolveTaskVoiceCommand(text) {
  const tc = taskController();
  if (!tc || !tc.current) return false;
  const snap = tc.current();
  if (!snap || snap.state === "idle") return false;
  const w = taskWords(text);
  if (snap.state === "awaiting_confirmation") {
    if (w.stop || w.deny) { pushChat("user", text); tc.deny(); acknowledge("Okay — I've dropped that step."); return true; }
    if (w.approve) { pushChat("user", text); tc.approve(); return true; }
  } else if (snap.state === "paused") {
    if (w.stop) { pushChat("user", text); tc.cancel("stopped by voice"); acknowledge("Cancelled."); return true; }
    if (w.resume || w.approve) { pushChat("user", text); tc.resume(); return true; }
  } else if (snap.state === "working" || snap.state === "planning") {
    if (w.stop) { pushChat("user", text); tc.cancel("stopped by voice"); acknowledge("Stopping now."); return true; }
    if (w.pause) { pushChat("user", text); tc.pause(); acknowledge("Paused."); return true; }
  }
  return false;
}

// A short spoken acknowledgement that also animates the avatar. Used only for
// control-word replies and graceful refusals — it never claims work has started
// or finished unless the task layer's own state has confirmed it.
function acknowledge(text) {
  pushChat("ai", text);
  if (!prefs.muted && window.VMCloudVoice) {
    setStatus("speaking");
    speakText(text, "idle");
  } else {
    setStatus("idle");
    resumeListening();
  }
}

// Route a clearly actionable instruction through the existing task layer. The
// controller plans on the server, executes only permissioned/confirmed steps,
// and reports results through onSpeak / onEvent (which animate the avatar).
function runTask(text) {
  pushChat("user", text);
  const tc = taskController();
  if (!tc) { acknowledge("Task execution isn't available right now."); return; }
  pushChat("ai", "Okay — I'll take care of that.");
  tc.submit(text);
  renderTask();
}

async function send(text) {
  text = String(text || "").trim();
  if (!text || busy) return;

  // 1. Control words for an in-flight task (approve / deny / stop / pause).
  if (resolveTaskVoiceCommand(text)) return;

  // 2. A task is already running: acknowledge it rather than silently
  // launching a duplicate. The user can say "stop" to cancel it first.
  const tc = taskController();
  const snap = (tc && tc.current) ? tc.current() : null;
  if (snap && snap.active) {
    pushChat("user", text);
    acknowledge("I'm still on your last request — say \u201cstop\u201d and I'll switch.");
    return;
  }

  // 3. A clearly actionable request goes through the safe task layer;
  // everything else is ordinary conversation on the same brain.
  if (looksLikeTask(text)) { runTask(text); return; }

  await chatTurn(text);
}

async function chatTurn(text) {
  const t = ++turn;
  busy = true;
  clearResumeTimer();
  pushChat("user", text);
  setStatus("thinking");
  let frame = "";
  try { frame = screenFrame(); } catch (e) { }
  try {
    const cid = await ensureChat();
    const resp = await chatWithTimeout(apiPost("/api/cloud/chat", {
      message: text,
      chat_id: cid,
      image_data: frame || "",
    }), CHAT_BUDGET_MS);
    let d = null;
    try { d = await resp.json(); } catch (e2) { d = null; }
    if (t !== turn) { busy = false; return; }
    busy = false;
    if (d && d.status === "success") {
      const reply = d.reply || "";
      pushChat("ai", reply);
      // Emotion is grounded in THIS exchange, never the Cloud section's default.
      const emo = emotionForReply(text, reply);
      if (reply && window.VMCloudVoice && !prefs.muted) {
        setStatus("speaking");
        speakText(reply, emo);
      } else {
        setStatus(emo);
        resumeListening();
      }
    } else {
      // Surface the backend's own message (unconfigured companion, 404/500, or
      // an HTML error page) instead of collapsing every failure to "unreachable".
      const msg = (d && d.message)
        ? String(d.message)
        : (resp.ok
          ? "The companion returned an unexpected response — please try again."
          : "ValleyMind couldn't answer that (" + resp.status + "). Please try again.");
      guardSpeakFallback(t, msg);
    }
  } catch (e) {
    // A hard timeout is a timeout; any other failure keeps the REAL error text
    // so a network/auth problem is not misreported as the companion being slow.
    guardSpeakFallback(t, (e && e.vmTimedOut)
      ? "that request took too long — please try again"
      : ((e && e.message) ? String(e.message) : "ValleyMind unreachable — check your connection and try again."));
  }
}

// Gives a network call a hard wall-clock budget so the companion can never sit
// in "thinking" forever behind a hung request. Aligned with the transport
// timeout (CHAT_BUDGET_MS) so it is a genuine backstop, not an earlier cut-off.
function chatWithTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      const err = new Error("chat timed out");
      err.vmTimedOut = true;
      reject(err);
    }, ms);
    Promise.resolve(promise).then(
      (v) => { clearTimeout(timer); resolve(v); },
      (e) => { clearTimeout(timer); reject(e); }
    );
  });
}

function guardSpeakFallback(t, message) {
  if (t !== turn) { busy = false; return; }
  busy = false;
  pushChat("ai", message);
  if (!prefs.muted && window.VMCloudVoice) {
    setStatus("speaking");
    speakText(message, "confused");
  } else {
    setStatus("confused");
    resumeListening();
  }
}

function speakText(text, endEmo) {
  if (prefs.muted) return false;
  const seq = ++voiceTurn;
  voiceEndEmo = END_EMOTION_OK[endEmo] ? endEmo : "idle";
  const cloud = (window.VMCloud && window.VMCloud.prefs) || {};
  // A companion-specific voice preference (Settings → Companion) wins; otherwise
  // fall back to the account's Cloud voice preference. Never a second voice
  // engine -- this is still the single VMCloudVoice player.
  const prefsRef = {};
  if (prefs.voiceKey) prefsRef.voice_preference = prefs.voiceKey;
  else if (cloud.voice_preference) prefsRef.voice_preference = cloud.voice_preference;
  window.VMCloudVoice.speak(text, { prefs: prefsRef, presentation: cloud.presentation || "neutral" });
  if (seq !== voiceTurn) return false;
  return true;
}

const END_EMOTION_OK = { idle: 1, happy: 1, curious: 1, sad: 1, confused: 1, working: 1, angry: 1 };

function stop() {
  ++turn;
  ++voiceTurn;
  busy = false;
  clearResumeTimer();
  if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
  setFaceTalking(false);
  setStatus("idle");
}

function clearResumeTimer() {
  if (resumeTimer) { clearTimeout(resumeTimer); resumeTimer = null; }
}

// Hands-free loop: after a spoken reply (or a graceful error) switch the mic
// back on for the next turn -- but ONLY when the user granted first-use access,
// voice is not muted, the companion is the active character, and the overlay is
// still visible. The small delay lets the TTS tail clear so we never re-capture
// our own reply.
function resumeListening() {
  clearResumeTimer();
  if (!voiceActive() || !accessAllowed() || prefs.muted || prefs.voiceFirst === false) return;
  const el = $id("vmVrmCompanion");
  if (!el || el.style.display === "none") return;
  if (!window.VMCloudVoice || !window.VMCloudVoice.supported()) return;
  if (busy || window.VMCloudVoice.isListening()) return;
  resumeTimer = setTimeout(() => {
    resumeTimer = null;
    if (!voiceActive() || !accessAllowed() || prefs.muted || busy) return;
    if (window.VMCloudVoice.isListening()) return;
    try { window.VMCloudVoice.start(); } catch (e) { }
  }, 900);
}

function sendFromInput() {
  const input = $id("vmVrmCompanionInput");
  if (!input) return;
  const text = input.value;
  input.value = "";
  send(text);
}

// ── task execution layer (safe, permissioned, observable) ─────────────────
// The controller lives in static/task_controller.js (its own permissions and
// tests). Here we only (a) hand it the EXISTING capabilities -- brain chat via
// apiPost, voice via VMCloudVoice, screen via VMCloudVision -- and (b) render
// its lifecycle. Task states drive window.vmVrmCompanion.applyStatus through
// the SAME setStatus() the rest of the companion uses.
const TASK_LABEL = {
  idle: "No active task",
  planning: "planning…",
  awaiting_confirmation: "waiting for your confirmation",
  working: "working…",
  paused: "paused",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
};

let lastTaskState = "idle";

function taskController() { return window.VMTaskController || null; }

// A concise spoken status for the human-glanceable task lifecycle. Kept short so
// it never overlaps a `respond` step's own speech, and only used for states the
// controller has actually confirmed (a needed confirmation, or a failure).
function speakTaskNotice(text) {
  const msg = String(text || "").trim();
  if (!msg) return;
  pushChat("ai", msg);
  if (!prefs.muted && window.VMCloudVoice) {
    setStatus("speaking");
    speakText(msg, "idle");
  } else if (stateKey === "speaking") {
    setStatus("idle");
  }
}

function getTaskPermissions() {
  return Array.isArray(prefs.taskPermissions) ? prefs.taskPermissions.slice() : [];
}

function initTasks() {
  const tc = taskController();
  if (!tc) return;
  tc.init({
    getPermissions: getTaskPermissions,
    onStatus: function (s) { setStatus(s); },
    onSpeak: function (text) {
      text = String(text || "").trim();
      if (!text) return;
      pushChat("ai", text);
      if (window.VMCloudVoice && !prefs.muted) { setStatus("speaking"); speakText(text, "idle"); }
      else resumeListening();
    },
    captureView: function (maxWidth) {
      if (!prefs.visionOn || !window.VMCloudVision) return "";
      try { return window.VMCloudVision.capture(maxWidth) || ""; } catch (e) { return ""; }
    },
    onStop: function () {
      if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
      clearResumeTimer();
      setStatus("idle");
    },
    onGuide: function (ws) { guide(ws); },
    apiPost: apiPost,
    onEvent: function (kind, snap) {
      // The controller emits (kind, snapshot); taking only one arg would read
      // the event kind as the state and break transition speech entirely.
      renderTask();
      if (!snap || !snap.state) return;
      // Speak only on a genuine state transition, and never claim a result the
      // controller has not confirmed. A completed task's final `respond` step
      // has already spoken, so completion is not re-announced here.
      if (snap.state === lastTaskState) return;
      lastTaskState = snap.state;
      if (snap.state === "awaiting_confirmation") {
        speakTaskNotice("This next step needs your confirmation. Open Settings, Companion, to approve it — or say cancel.");
      } else if (snap.state === "failed") {
        speakTaskNotice(String(snap.message || "I couldn't finish that."));
      }
    },
  });
}

// The task dashboard now lives in Settings → Companion, so "opening tasks"
// simply surfaces that section; the panel is always rendered inline there.
function openTaskPanel() {
  prefs.taskOpen = true;
  savePrefs();
  if (typeof window.openSettingsSection === "function") window.openSettingsSection("companion");
  renderTask();
}

function toggleTaskPanel() {
  openTaskPanel();
}

function syncTaskPermControls() {
  const boxes = document.querySelectorAll("[data-task-perm]");
  const perms = getTaskPermissions();
  for (const b of boxes) {
    b.checked = perms.indexOf(b.getAttribute("data-task-perm")) !== -1;
  }
}

function renderTask() {
  const tc = taskController();
  const snap = (tc && tc.current) ? tc.current()
    : { state: "idle", steps: [], active: false, index: 0, total: 0, message: "", pending: null };
  const statusEl = $id("vmVrmCompanionTaskStatus");
  if (statusEl) {
    const base = TASK_LABEL[snap.state] || snap.state;
    const prog = snap.total ? " · " + Math.min(snap.index, snap.total) + "/" + snap.total : "";
    const goal = snap.goal ? " — " + String(snap.goal).slice(0, 42) : "";
    statusEl.textContent = base + prog + goal;
  }
  const msgEl = $id("vmVrmCompanionTaskMsg");
  if (msgEl) msgEl.textContent = snap.message || "";
  const listEl = $id("vmVrmCompanionTaskList");
  if (listEl) {
    let html = "";
    for (const s of snap.steps) {
      const cls = "vmvrm-step" + (s.done ? " done" : (s.current ? " current" : "")) +
        ((s.consequential || s.blocked) ? " flag" : "");
      const badge = s.blocked ? ("needs " + (s.permission || "permission"))
        : (s.consequential ? "confirm" : "");
      html += '<div class="' + cls + '"><span>' + escapeHtml(s.label || s.type) + "</span>" +
        (badge ? '<span class="vmvrm-step-badge">' + escapeHtml(badge) + "</span>" : "") + "</div>";
    }
    if (!snap.steps.length) html = '<div class="vmvrm-step"><span class="vmvrm-step-badge">No active task.</span></div>';
    listEl.innerHTML = html;
  }
  const setBtn = (id, on) => { const b = $id(id); if (b) b.disabled = !on; };
  const terminal = ["completed", "failed", "cancelled"].indexOf(snap.state) !== -1;
  setBtn("vmVrmCompanionTaskApprove", snap.state === "awaiting_confirmation" && !!snap.pending);
  setBtn("vmVrmCompanionTaskDeny", snap.state === "awaiting_confirmation");
  setBtn("vmVrmCompanionTaskPause", snap.state === "working" || snap.state === "awaiting_confirmation" || snap.state === "planning");
  setBtn("vmVrmCompanionTaskResume", snap.state === "paused");
  setBtn("vmVrmCompanionTaskStop", snap.state !== "idle" && !terminal);
  syncTaskPermControls();
}

function submitTask() {
  const input = $id("vmVrmCompanionTaskInput");
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  prefs.taskOpen = true;
  savePrefs();
  const tc = taskController();
  if (tc) tc.submit(text);
  renderTask();
}

function wireTaskControls() {
  // The on-screen task dashboard is gone. Settings → Companion renders the
  // plan + controls inline (with these same ids) and calls this after every
  // render, so re-binding fresh nodes is safe and never duplicates listeners.
  // The permission checkboxes live in Settings too and are wired through
  // setTaskPermissions(); this only binds the plan + control elements.
  on($id("vmVrmCompanionTaskPlan"), "click", submitTask);
  on($id("vmVrmCompanionTaskInput"), "keydown", (e) => { if (e.key === "Enter") submitTask(); });
  on($id("vmVrmCompanionTaskApprove"), "click", () => { const tc = taskController(); if (tc) tc.approve(); renderTask(); });
  on($id("vmVrmCompanionTaskDeny"), "click", () => { const tc = taskController(); if (tc) tc.deny(); renderTask(); });
  on($id("vmVrmCompanionTaskPause"), "click", () => { const tc = taskController(); if (tc) tc.pause(); renderTask(); });
  on($id("vmVrmCompanionTaskResume"), "click", () => { const tc = taskController(); if (tc) tc.resume(); renderTask(); });
  on($id("vmVrmCompanionTaskStop"), "click", () => { const tc = taskController(); if (tc) tc.cancel("stopped"); renderTask(); });
}

// ── voice glue (same player/amplitude as Cloud, never a second pipeline) ──
// VMCloudVoice is a process-wide singleton with a single hook set and a shared
// amplitude tap, so the VRM bootstraps it here and every callback no-ops while
// the legacy Cloud character is the active one. Without this call the listening
// / speaking status and the amplitude-driven mouth sync were never connected.
function voiceActive() { return getMode() === "vrm"; }

// Explicit mouth control. The face channel keeps a target level and a separate
// "speaking" flag, and its update() falls back to a SIMULATED wobble whenever
// speaking is on with no level. Switching body clips does not clear either, so
// after speech the mouth stayed open (or kept wobbling) forever. Every end /
// stop / mute / state change therefore closes BOTH the flag and the level.
function setFaceTalking(on) {
  if (!engine || !engine.face) return;
  try {
    if (on) {
      engine.face.setSpeaking(true);
    } else {
      engine.face.setSpeaking(false);
      engine.face.setTalkLevel(0);
    }
  } catch (e) { }
}

function wireVoice() {
  if (voiceWired || !window.VMCloudVoice) return;
  voiceWired = true;
  window.VMCloudVoice.setHooks({
    onListeningChange: function (active) {
      if (!voiceActive()) return;
      setStatus(active ? "listening" : (stateKey === "listening" ? "idle" : stateKey));
    },
    onResult: function (text, isFinal) {
      if (!voiceActive()) return;
      // Overlap / duplicate guard: while a turn is in flight (thinking/speaking)
      // we ignore further final results. The busy flag also keeps a second
      // simultaneous mic tap from double-sending the same phrase.
      if (busy) return;
      const t = String(text || "").trim();
      if (isFinal && t.length >= 2) send(t);
    },
    onSpeakStart: function () {
      if (!voiceActive()) return;
      setStatus("speaking");
      setFaceTalking(true);
    },
    onSpeakEnd: function (reason) {
      if (!voiceActive()) return;
      setFaceTalking(false);
      setStatus(voiceEndEmo || "idle");
      // Successful spoken replies (reason "" or "interrupted") return to the
      // hands-free loop; a hard TTS failure surfaces as confused (not silent).
      if (reason === "tts failed" || reason === "tts blocked" || reason === "tts unavailable") {
        setStatus("confused");
        logLine("Voice engine unavailable — replies shown as text.");
      }
      resumeListening();
    },
    onError: function (code) {
      if (!voiceActive()) return;
      clearResumeTimer(); // never auto-retry a denied mic
      setFaceTalking(false);
      setStatus("confused");
      logLine(code === "microphone blocked"
        ? "Microphone denied — use the text box or enable mic in the browser."
        : "Voice input isn't supported in this browser.");
    },
  });
  // Mouth sync: the ONE existing TTS player's amplitude drives setTalkLevel.
  // setTalkLevel() owns the speaking flag (level > 0.01), so a silent gap
  // between words closes the mouth instead of triggering the simulated
  // envelope; while audio is not playing the mouth is held shut.
  window.VMCloudVoice.setLevelListener(function (level) {
    if (!voiceActive() || !engine || !engine.face) return;
    try {
      if (stateKey === "speaking" && window.VMCloudVoice.isSpeaking()) {
        engine.face.setTalkLevel(level);
      } else {
        engine.face.setSpeaking(false);
        engine.face.setTalkLevel(0);
      }
    } catch (e) { }
  });
}

function toggleVoice() {
  if (!window.VMCloudVoice) return;
  if (!window.VMCloudVoice.supported()) {
    setStatus("confused");
    logLine("Voice input isn't supported in this browser.");
    return;
  }
  if (window.VMCloudVoice.isListening()) {
    const spoken = window.VMCloudVoice.takeTranscript();
    window.VMCloudVoice.stopListening();
    clearResumeTimer();
    if (String(spoken || "").trim().length >= 2) send(String(spoken).trim());
    return;
  }
  stop();
  try { window.VMCloudVoice.start(); } catch (e) { }
}

// Always-available mute for hands-free voice (the Settings-friendly escape
// hatch). Muting silences in-progress speech and stops auto-listening without
// revoking first-use access; unmuting resumes the hands-free loop on access.
function toggleMute() {
  prefs.muted = !prefs.muted;
  savePrefs();
  if (prefs.muted) {
    if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
    clearResumeTimer();
    setFaceTalking(false);
    setStatus("idle");
  } else {
    if (accessAllowed()) startVoiceSession();
  }
  syncControls();
  paintStatus();
}

function startVoiceSession() {
  if (!voiceActive() || prefs.muted || !accessAllowed()) return;
  if (!window.VMCloudVoice || !window.VMCloudVoice.supported()) {
    setStatus("confused");
    logLine("Voice input isn't supported in this browser.");
    return;
  }
  // Start immediately when possible (keeps the Allow/Unmute click's gesture
  // context for browsers that need it); resumeListening() handles the slower
  // post-reply re-arm with its own guards.
  if (!window.VMCloudVoice.isListening()) {
    try { window.VMCloudVoice.start(); } catch (e) { }
  }
}

// ── screen awareness (permission-first, VMCloudVision) ────────────────────
function toggleVision() {
  if (!window.VMCloudVision) return;
  const st = window.VMCloudVision.getState ? window.VMCloudVision.getState() : "off";
  if (st === "active" || prefs.visionOn) {
    try { window.VMCloudVision.stop(); } catch (e) { }
    prefs.visionOn = false;
  } else {
    const started = window.VMCloudVision.start();
    prefs.visionOn = !!(started || window.VMCloudVision.getState() === "prompt");
  }
  savePrefs();
  paintStatus();
}

// ── purposeful movement (app-native, visible area only) ───────────────────
// The companion walks with the REAL walking clips (the clips.json "walking"
// set: world-walk / world-walk-slow / world-walk-fast / ...) while the overlay
// box glides inside the viewport. The gait is in-place inside the small canvas,
// so "walking across the app" = the box translation + the limb animation. All
// movement is clamped so the companion can never cover nav controls or leave
// the visible area.
function cancelWalk() {
  if (walkRaf) { cancelAnimationFrame(walkRaf); walkRaf = 0; }
}

function clampToViewport(x, y, w, h, gap) {
  gap = gap || 8;
  // On phones keep clear of the workspace tab bar (top) and the on-screen
  // input row (bottom) so purposeful movement never covers a control.
  const narrow = (window.innerWidth || 1280) <= 760;
  const topPad = narrow ? 96 : gap;
  const bottomPad = narrow ? 88 : gap;
  const maxX = (window.innerWidth || 1280) - w - gap;
  const maxY = (window.innerHeight || 800) - h - bottomPad;
  const minX = gap, minY = topPad;
  const cx = Math.max(minX, Math.min(maxX, x));
  const cy = Math.max(minY, Math.min(maxY, y));
  return { x: Math.floor(cx), y: Math.floor(cy) };
}

function walkTo(x, y, opts) {
  opts = opts || {};
  const el = $id("vmVrmCompanion");
  if (!el) return false;
  const r = el.getBoundingClientRect();
  const target = clampToViewport(x, y, r.width || 120, r.height || 120);
  if (Math.abs(r.left - target.x) < 2 && Math.abs(r.top - target.y) < 2) {
    if (typeof opts.done === "function") { try { opts.done(); } catch (e) { } }
    return true;
  }
  cancelWalk();
  if (engine && engine.status.stage === "ready") {
    // Walk with the REAL walking clips. Set the status label to "working"
    // WITHOUT re-driving the generic working clip, which would immediately
    // replace the gait (the two clips were previously fighting each other).
    engine.setAuto(false);
    engine.trigger("walking");
    clearAccentTimer();
    stateKey = "working";
    paintStatus();
  }
  const from = { x: r.left, y: r.top };
  const spanMs = (opts.ms || 900);
  const t0 = (window.performance && performance.now) ? performance.now() : Date.now();
  const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
  const raf = window.requestAnimationFrame || ((cb) => setTimeout(cb, 16));
  const step = (now) => {
    const k = Math.min(1, ((now || Date.now()) - t0) / spanMs);
    const e = ease(k);
    const cx = Math.round(from.x + (target.x - from.x) * e);
    const cy = Math.round(from.y + (target.y - from.y) * e);
    const box = $id("vmVrmCompanion");
    if (box) {
      box.style.left = cx + "px";
      box.style.top = cy + "px";
      box.style.right = "auto";
      box.style.bottom = "auto";
    }
    if (k < 1) { walkRaf = raf(step); return; }
    walkRaf = 0;
    prefs.x = cx;
    prefs.y = cy;
    savePrefs();
    setStatus("idle");
    if (typeof opts.done === "function") { try { opts.done(); } catch (e) { } }
  };
  walkRaf = raf(step);
  return true;
}

const WORKSPACE_LABEL = {
  studio: "ValleyMind Studio",
  website: "AI Builder",
  sketch: "Sketch",
  threed: "3D Studio",
  roundtable: "Round Table",
  music: "Music Studio",
  editing: "Massive Editing",
  analytics: "Analytics",
};

// In-app guidance: walk toward the top tab bar, then switch the SPA workspace
// via the app shell's own vmWsGo() switcher. This is what the task system's
// `goto_workspace` action maps to (authoritative allowlist lives server-side).
function guide(ws) {
  const display = WORKSPACE_LABEL[ws] || String(ws || "");
  pushChat("ai", display ? ("Opening the " + display + " workspace for you.") : "Navigating inside ValleyMind.");
  const midX = Math.max(18, Math.floor((window.innerWidth || 1280) / 2) - 120);
  walkTo(midX, 10, {
    done: () => {
      if (typeof window.vmWsGo === "function") {
        try { window.vmWsGo(ws); } catch (e) { }
      }
      if (display && !prefs.muted && window.VMCloudVoice) {
        setStatus("speaking");
        speakText("Here's the " + display + " workspace.", "idle");
      } else {
        logLine(display ? ("Here's the " + display + " workspace.") : "Workspace opened.");
      }
    },
  });
}

// ── (formerly the attached settings popover; now the main Settings section) ─
// The companion's own controls were removed from the overlay. The main sidebar
// Settings → Companion section renders elements carrying data-vm-mode /
// data-skin / data-task-perm attributes; syncControls() keeps their selected
// state in sync with the persisted prefs wherever they are in the document.
function syncControls() {
  const btns = document.querySelectorAll("[data-vm-mode]");
  for (const b of btns) {
    b.classList.toggle("on", b.getAttribute("data-vm-mode") === prefs.mode);
  }
  const chips = document.querySelectorAll("[data-skin]");
  for (const c of chips) {
    c.classList.toggle("on", c.getAttribute("data-skin") === (prefs.skin || ""));
  }
  syncTaskPermControls();
  paintStatus();
}

function buildUI() {
  if (uiBuilt) return;
  uiBuilt = true;

  on($id("vmVrmCompanionSend"), "click", sendFromInput);
  on($id("vmVrmCompanionInput"), "keydown", (e) => { if (e.key === "Enter") sendFromInput(); });
  on($id("vmVrmCompanionMicBtn"), "click", toggleVoice);
  on($id("vmVrmCompanionScreenBtn"), "click", toggleVision);

  // First-use access prompt (Allow / Not now). Distinct consent from task
  // permissions: it only gates automatic hands-free listening.
  const allowBtn = $id("vmCompanionAccessAllow");
  const denyBtn = $id("vmCompanionAccessDeny");
  if (allowBtn) {
    on(allowBtn, "click", () => {
      setAccessDecision("allowed");
      hideAccessPrompt();
      startVoiceSession();
    });
  }
  if (denyBtn) {
    on(denyBtn, "click", () => {
      setAccessDecision("declined");
      hideAccessPrompt();
      setStatus("idle");
    });
  }

  // The task layer's status/plan/controls render inline in Settings →
  // Companion; only the controller is initialised here so voice-driven tasks
  // run headlessly even if that section is never opened.
  initTasks();
}

function applySkin(name) {
  prefs.skin = name || null;
  savePrefs();
  if (engine) { try { engine.setSkinTone(prefs.skin); } catch (e) { } }
  syncControls();
}

// ── main-settings surface ─────────────────────────────────────────────────
// The companion has no attached controls any more; the main sidebar's
// Settings → Companion section drives it exclusively through these methods
// (wired in static/settings.js), so nothing about the character can be
// changed only from a hidden popover.
function toggleChat() {
  prefs.chatOpen = !prefs.chatOpen;
  savePrefs();
  const panel = $id("vmVrmCompanionChat");
  if (panel) panel.classList.toggle("open", prefs.chatOpen);
  syncControls();
}

function setVisible(on) {
  prefs.visible = !!on;
  savePrefs();
  if (on) show(); else hide();
  syncControls();
}

function isVisible() {
  const el = $id("vmVrmCompanion");
  return getMode() === "vrm" && prefs.visible !== false && !!el && el.style.display !== "none";
}

function setSize(pct) {
  const v = Math.max(10, Math.min(24, Number(pct) || DEFAULTS.sizePct));
  prefs.sizePct = v;
  savePrefs();
  applySize();
  syncControls();
}

function setDock(d) {
  prefs.dock = ["br", "bl", "tr", "tl"].indexOf(d) !== -1 ? d : "br";
  prefs.x = null;
  prefs.y = null;
  savePrefs();
  applyPosition();
  syncControls();
}

function setCharName(name) {
  const clean = String(name || "").trim().slice(0, 40);
  prefs.charName = clean || DEFAULTS.charName;
  savePrefs();
  syncControls();
}

function setVoiceKey(key) {
  prefs.voiceKey = (key && ["marcus", "elena", "angelina"].indexOf(key) !== -1) ? key : "";
  savePrefs();
  syncControls();
}

// Programmatic screen-context switch (the Settings toggle). The chat "Screen"
// button keeps using toggleVision(); both converge on prefs.visionOn.
function setVision(on) {
  const want = !!on;
  if (want && window.VMCloudVision && window.VMCloudVision.getState() !== "active") {
    let started = false;
    try { started = !!window.VMCloudVision.start(); } catch (e) { started = false; }
    prefs.visionOn = started || window.VMCloudVision.getState() === "prompt";
  } else if (!want && window.VMCloudVision && window.VMCloudVision.getState() === "active") {
    try { window.VMCloudVision.stop(); } catch (e) { }
    prefs.visionOn = false;
  } else {
    prefs.visionOn = want;
  }
  savePrefs();
  syncControls();
}

function setMuted(on) {
  if (!!on === !!prefs.muted) { syncControls(); return; }
  toggleMute();
}

function setVoiceFirst(on) {
  prefs.voiceFirst = !!on;
  savePrefs();
  if (prefs.voiceFirst) resumeListening();
  syncControls();
}

function getProps() {
  return {
    mode: getMode(),
    visible: prefs.visible !== false,
    visibleNow: isVisible(),
    sizePct: prefs.sizePct,
    dock: prefs.dock,
    skin: prefs.skin || "",
    charName: prefs.charName || DEFAULTS.charName,
    voiceKey: prefs.voiceKey || "",
    visionOn: !!prefs.visionOn,
    muted: !!prefs.muted,
    voiceFirst: prefs.voiceFirst !== false,
    access: accessDecision(),
    taskPermissions: getTaskPermissions(),
    statusLabel: STATUS_LABEL[stateKey] || stateKey,
  };
}

// ── first-use access prompt ───────────────────────────────────────────────
function hideAccessPrompt() {
  const m = $id("vmCompanionAccess");
  if (m) m.classList.remove("open");
  document.body.removeAttribute("data-vm-access-open");
}

function maybePromptAccess() {
  // Only ask when the user is signed in and has not answered yet. The prompt
  // is the app's own consent screen (distinct from the browser's mic prompt,
  // which the browser shows itself when SpeechRecognition starts).
  if (typeof window.vmIsAuthenticated === "function" && !window.vmIsAuthenticated()) return;
  if (accessDecision() !== null) return;
  const m = $id("vmCompanionAccess");
  if (!m) return;
  m.classList.add("open");
  document.body.setAttribute("data-vm-access-open", "1");
}

// ── public lifecycle (called by the app shell + the module itself) ───────
async function show() {
  if (getMode() !== "vrm") { hide(); return; }
  // Persisted, recoverable hide: once the user hides the companion from
  // Settings it stays hidden across navigations/refreshes until re-enabled
  // there (never an irreversible hide).
  if (prefs.visible === false) { hide(); return; }
  setDataset();
  buildUI();
  wireVoice();
  const el = $id("vmVrmCompanion");
  if (!el) return;
  el.style.display = "block";
  applySize();
  applyPosition();
  syncControls();
  const chatPanel = $id("vmVrmCompanionChat");
  if (chatPanel) chatPanel.classList.toggle("open", !!prefs.chatOpen);
  initTasks();
  renderTask();
  const eng = await getEngine();
  if (eng) {
    eng.setRendering(true);
    setStatus(stateKey === "idle" ? "idle" : stateKey);
  }
  // VMCloudVoice may load after the first paint; retry the one-time wiring now
  // that the engine exists so amplitude lip-sync is attached before speaking.
  wireVoice();
  // First time here: offer the access prompt. After consent we never auto-listen
  // until the user accepts OR manually presses the mic.
  maybePromptAccess();
  if (accessAllowed() && !prefs.muted) startVoiceSession();
}

function hide() {
  setDataset();
  cancelWalk();
  clearResumeTimer();
  if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
  setFaceTalking(false);
  const el = $id("vmVrmCompanion");
  if (el) el.style.display = "none";
  if (engine) engine.setRendering(false);
}

function cleanup() {
  if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
  hide();
}

function setMode(mode, opts) {
  opts = opts || {};
  prefs.mode = mode === "cloud" ? "cloud" : "vrm";
  if (opts.fromUser) prefs.visible = true;
  savePrefs();
  setDataset();
  if (prefs.mode === "vrm") {
    show();
  } else {
    hide();
    // Roll back to the legacy Cloud character: clear the inline pin above and
    // let Cloud's own lifecycle restore its character + panel.
    if (typeof window.vmCloudCharacterShow === "function") {
      try { window.vmCloudCharacterShow(); } catch (e) { }
    }
    if (typeof window.vmCloudCompanionShow === "function") {
      try { window.vmCloudCompanionShow(); } catch (e) { }
    }
  }
}

function maybeAutoShow() {
  if (getMode() !== "vrm") return;
  if (typeof window.vmIsAuthenticated === "function" && window.vmIsAuthenticated()) {
    show();
  } else {
    maybePromptAccess();
  }
}

// ── window hooks exposed to the classic-script app shell ─────────────────
window.vmVrmCompanionShow = show;
window.vmVrmCompanionCleanup = cleanup;
window.vmVrmCompanionDispatch = function () {
  setDataset();
  if (getMode() === "vrm") show(); else hide();
};
window.vmVrmCompanion = {
  getMode: getMode,
  setMode: setMode,
  setSkin: applySkin,
  home: home,
  stop: stop,
  cleanup: cleanup,
  toggleMute: toggleMute,
  isMuted: function () { return !!prefs.muted; },
  startVoiceSession: startVoiceSession,
  accessDecision: accessDecision,
  allowAccess: function () { setAccessDecision("allowed"); hideAccessPrompt(); startVoiceSession(); },
  declineAccess: function () { setAccessDecision("declined"); hideAccessPrompt(); setStatus("idle"); },
  walkTo: walkTo,
  guide: guide,
  // ── main-settings surface (wired by static/settings.js) ──────────────────
  getProps: getProps,
  syncControls: syncControls,
  show: show,
  hide: hide,
  isVisible: isVisible,
  setVisible: setVisible,
  toggleChat: toggleChat,
  openTaskPanel: openTaskPanel,
  toggleTaskPanel: toggleTaskPanel,
  wireTaskControls: wireTaskControls,
  setSize: setSize,
  setDock: setDock,
  setCharName: setCharName,
  setVoiceKey: setVoiceKey,
  setVision: setVision,
  setMuted: setMuted,
  setVoiceFirst: setVoiceFirst,
  /**
   * The documented seam between "AI decides the state" and the avatar:
   * persistent driven states (listening / thinking / speaking / working) lock
   * the avatar to a matching .vrma clip; momentary emotional accents nudge the
   * auto state machine. Any other value returns to idle autonomy.
   */
  applyStatus: function (s) { setStatus(s); },
  getStatus: function () { return stateKey; },
  setRendering: function (on) { if (engine) engine.setRendering(!!on); },
  // ── task execution layer surface ─────────────────────────────────────────
  submitTask: submitTask,
  renderTask: renderTask,
  approveTask: function () { const tc = taskController(); return tc ? tc.approve() : false; },
  denyTask: function () { const tc = taskController(); return tc ? tc.deny() : false; },
  pauseTask: function () { const tc = taskController(); return tc ? tc.pause() : false; },
  resumeTask: function () { const tc = taskController(); return tc ? tc.resume() : false; },
  cancelTask: function (reason) { const tc = taskController(); return tc ? tc.cancel(reason || "cancelled") : false; },
  taskSnapshot: function () { const tc = taskController(); return tc ? tc.current() : null; },
  setTaskPermissions: function (perms) {
    prefs.taskPermissions = Array.isArray(perms) ? perms.slice() : [];
    savePrefs();
    const tc = taskController();
    if (tc) tc.init({ getPermissions: getTaskPermissions });
    syncTaskPermControls();
    return getTaskPermissions();
  },
  getTaskPermissions: getTaskPermissions,
  get engine() { return engine; },
  snapshot: function () { return engine ? engine.snapshot() : null; },
  get SKIN_PRESETS() { return SKIN_PRESETS; },
};

// Shared state in case the shell needs to read/write it for tests.
prefs = readPrefs();
setDataset();
buildUI();
window.vmVrmCompanion.prefs = prefs;

// Cookie-authenticated opens / refreshes / new tabs: show immediately once
// the app shell reports auth (mirrors the Cloud character's showIfAuthed()).
if (document.readyState !== "loading") {
  maybeAutoShow();
} else {
  document.addEventListener("DOMContentLoaded", maybeAutoShow);
}