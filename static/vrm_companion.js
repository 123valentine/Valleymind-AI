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
  sizePct: 100,      // overlay height as % of viewport height (increased per request)
  skin: null,        // SKIN_PRESETS name, or null = original VRM colors
  dock: "br",        // "br" | "bl" | "tr" | "tl" (used until the avatar is moved)
  x: null,           // manual position (px) once placed (floating overlay)
  y: null,
  // The Studio stage lays out the same node. Its manual placement is a plain
  // transform offset from the centred layout position.
  stageX: null,
  stageY: null,
  userPlaced: false, // true once the user drags the avatar; the manual position is preferred
  roam: false,       // legacy pref kept for compatibility; autonomous idle roaming is disabled
  charName: "ValleyMind", // display name (shown in Settings; keeps CJK glyphs intact)
  voiceKey: "elena", // female companion default; "marcus" remains an explicit opt-in
  visionOn: false,   // user preference: attach screen context to the brain
  taskOpen: false,   // task panel visibility (opened from Settings / auto on confirm)
  taskPermissions: [], // granted task permissions (vision/scroll/navigate/click/type)
  muted: false,      // hands-free voice off (auto-listen + spoken replies suppressed)
  voiceFirst: true,  // resume hands-free listening after each turn (access-permitted)
  // Voice-interaction state prep for a future hands-free wake word. "awake"
  // gates automatic listening exactly like muted does; a real wake-word engine
  // (native Android, see the report) will flip sleep()/wake(). There is no
  // always-on listening loop here -- that is deliberately NOT faked.
  awake: true,
};

// Spoken by BOTH companion surfaces the instant the user switches her on.
const GREETING_TEXT = "Hi! I'm right here—how can I help you today?";

// Every semantic state emitted by core/brain.py has a labelled status here.
// The behaviour values are REAL clip pools from vrm_preview/companion.js and
// vrm_preview/clips.json (idle, talking, listening, thinking, walking, happy,
// waving, nodding, pointing, sad, angry, headshake, unsure, working, sitting,
// gestures) so `[ANIM: ...]` always resolves to an actual animation.
const STATUS_LABEL = {
  idle: "idle",
  listening: "listening…",
  thinking: "thinking…",
  speaking: "speaking",
  working: "working",
  observing: "watching your screen",
  looking: "looking closely",
  waving: "waving hello",
  greeting: "greeting you",
  pointing: "pointing it out",
  guiding: "leading the way",
  walking: "walking with you",
  nodding: "nodding",
  agreeing: "agreeing",
  disagreeing: "shaking my head",
  unsure: "not quite sure",
  happy: "happy",
  celebrating: "celebrating",
  empathetic: "here for you",
  sad: "sad",
  angry: "angry",
  surprised: "surprised",
  blinking: "blinking",
  gesturing: "gesturing",
  sitting: "sitting",
};

const STATUS_BEHAVIOUR = {
  idle: "idle",
  listening: "listening",
  thinking: "thinking",
  speaking: "talking",
  working: "working",
  observing: "listening",
  looking: "pointing",
  waving: "waving",
  greeting: "waving",
  pointing: "pointing",
  guiding: "pointing",
  walking: "walking",
  nodding: "nodding",
  agreeing: "nodding",
  disagreeing: "headshake",
  unsure: "unsure",
  happy: "happy",
  celebrating: "happy",
  empathetic: "listening",
  sad: "sad",
  angry: "angry",
  surprised: "idle",
  blinking: "idle",
  gesturing: "gestures",
  sitting: "sitting",
  // Legacy aliases kept so older callers never break.
  curious: "listening",
  confused: "unsure",
};

// States that must stay locked to one clip until the driver changes them.
// Transient accents just nudge the auto state machine and settle to idle.
const DRIVEN = new Set(["listening", "thinking", "speaking", "working"]);

// The six Studio controls map to REAL engine behaviours. `gesture()` accepts
// both the short button word and the canonical status/behaviour, so a stale
// caller using "wave" still reaches the same waving clip + bone fallback.
const GESTURE_STATUS = {
  wave: "waving", waving: "waving",
  nod: "nodding", nodding: "nodding",
  point: "pointing", pointing: "pointing",
  think: "thinking", thinking: "thinking",
};

// A VRMA fetch is a network round-trip. These bounded, procedurally animated
// bone poses make the gesture visible on the FIRST tap; the real clip takes over
// as soon as it resolves. They only touch raw VRM humanoid bones (never the
// face/viseme channel, never a second mixer).
const GESTURE_FALLBACKS = {
  waving: {
    ms: 2600, oscHz: 2.6,
    bones: {
      rightUpperArm: { x: -0.18, z: -0.62 },
      rightLowerArm: { x: -0.20, z: -0.52 },
      rightHand: { z: 0.18, waveZ: 0.42 },
      head: { y: 0.10 },
    },
  },
  nodding: {
    ms: 1900, oscHz: 1.8,
    bones: {
      neck: { x: 0.08, waveX: 0.10 },
      head: { x: 0.20, waveX: 0.22 },
    },
  },
  pointing: {
    ms: 2400, oscHz: 0.8,
    bones: {
      rightUpperArm: { x: -0.52, z: -0.58 },
      rightLowerArm: { x: -0.30, z: -0.12 },
      rightHand: { x: 0.12, z: 0.10 },
      head: { y: 0.10 },
    },
  },
  thinking: {
    ms: 2800, oscHz: 0.6,
    bones: {
      rightUpperArm: { x: -0.40, z: -0.30 },
      rightLowerArm: { x: -0.92, z: -0.18 },
      rightHand: { x: 0.28, z: 0.20 },
      head: { y: 0.16, z: 0.13 },
    },
  },
};

const EMOTION_STATUS = {
  neutral: "idle",
  relaxed: "idle",
  curious: "looking",
  excited: "celebrating",
  happy: "happy",
  thinking: "thinking",
  focused: "thinking",
  sad: "sad",
  angry: "angry",
  frustrated: "unsure",
  surprised: "surprised",
  confused: "unsure",
  concerned: "empathetic",
};

// brain.py semantic state -> companion status. Covers every tag in the brain's
// SEMANTIC_ANIMATION_STATES contract plus common aliases from older prompts.
const SEMANTIC_STATUS = {
  idle: "idle", listen: "listening", listening: "listening",
  think: "thinking", thinking: "thinking", work: "working", working: "working",
  observe: "observing", look: "looking", curious: "looking",
  greet: "greeting", wave: "waving", welcome: "greeting",
  explain: "speaking", talk: "speaking", speaking: "speaking", ask: "listening",
  nod: "nodding", agree: "agreeing", celebrate: "celebrating",
  happy: "happy", success: "celebrating", point: "pointing", guide: "guiding",
  walk: "walking", lead: "walking", sit: "sitting", gesture: "gesturing",
  empathetic: "empathetic", sympathize: "empathetic", sad: "sad",
  disagree: "disagreeing", headshake: "disagreeing", unsure: "unsure",
  confused: "unsure", surprised: "surprised", blink: "blinking", angry: "angry",
};

// Face expressions from vrm_preview/face.js. Brain emotion tags are validated
// against this set before being handed to the engine.
const FACE_EXPRESSIONS = new Set(["neutral", "happy", "angry", "sad", "relaxed", "surprised", "blink"]);

const STATUS_EXPRESSION = {
  idle: "relaxed", listening: "neutral", thinking: "neutral", speaking: "relaxed",
  working: "neutral", observing: "surprised", looking: "surprised",
  waving: "happy", greeting: "happy", pointing: "neutral", guiding: "neutral",
  walking: "relaxed", nodding: "neutral", agreeing: "happy",
  disagreeing: "neutral", unsure: "surprised", happy: "happy",
  celebrating: "happy", empathetic: "sad", sad: "sad", angry: "angry",
  surprised: "surprised", blinking: "blink", gesturing: "relaxed", sitting: "relaxed",
  curious: "surprised", confused: "surprised",
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
  // Old profiles stored "" (follow Cloud) or "companion"; both could resolve to
  // a male voice. Normalise them to the natural female companion default.
  if (!p.voiceKey || p.voiceKey === "companion") p.voiceKey = DEFAULTS.voiceKey;
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
let nativeSpeechWired = false;
let autoWakeStarted = false;
let autoWakePending = false;
let greetingActive = false;
let uiBuilt = false;
let resumeTimer = null;   // delayed hands-free re-listen (cleared on stop/hide/mute)
let walkRaf = 0;          // explicit guidance walk handle (cancel on hide/stop)
let guidanceActive = false; // one explicit UI-guidance step owns the body clip
let gestureFallback = null; // temporary bone pose while a VRMA clip downloads

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
  // Disable the engine's weighted random state machine BEFORE boot starts its
  // frame loop. Idle is always chosen explicitly below; no shuffle can slip in.
  engine.setAuto(false);
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

// ── idle lock ─────────────────────────────────────────────────────────────
// The engine's weighted random state machine is permanently OFF. While nobody
// is talking to the companion, the ONLY allowed body behaviour is `idle`:
// calm breathing plus the face channel's natural blinking. There is no idle
// shuffle timer and no autonomous roaming timer. Gestures are reserved for a
// real live turn (listening / thinking / speaking), an active task, or an
// explicit UI-guidance step.
const ACTIVE_BODY_STATES = new Set(["listening", "thinking", "speaking", "working"]);

function voiceInputActive() {
  try {
    return !!(window.VMCloudVoice && typeof window.VMCloudVoice.isListening === "function" &&
      window.VMCloudVoice.isListening());
  } catch (e) { return false; }
}

function ttsPlaying() {
  if (stateKey === "speaking") return true;
  try {
    return !!(window.VMCloudVoice && typeof window.VMCloudVoice.isSpeaking === "function" &&
      window.VMCloudVoice.isSpeaking());
  } catch (e) { return false; }
}

function taskActive() {
  const tc = taskController();
  const snap = (tc && tc.current) ? tc.current() : null;
  return !!(snap && (snap.active ||
    ["planning", "working", "awaiting_confirmation"].indexOf(snap.state) !== -1));
}

function isActivelyEngaged() {
  if (busy || guidanceActive || walkRaf || voiceInputActive() || ttsPlaying()) return true;
  return taskActive();
}

// `explicit` is reserved for deliberate UI-guidance gestures. Everything else
// collapses to idle unless a real conversation/task is in progress.
function resolveStatus(status, explicit) {
  if (!STATUS_BEHAVIOUR[status]) return "idle";
  if (status === "idle") return "idle";
  if (explicit || ACTIVE_BODY_STATES.has(status)) return status;
  return isActivelyEngaged() ? status : "idle";
}

function setStatus(status, expression, explicit) {
  const s = resolveStatus(status, !!explicit);
  if (s === stateKey && engine && engine.status.stage === "ready") {
    // A repeated deliberate gesture must replay, not be swallowed as a no-op.
    if (explicit && s !== "idle") {
      clearAccentTimer();
      const repeated = STATUS_BEHAVIOUR[s];
      if (repeated && repeated !== "idle") {
        fireBodyClip(repeated);
        const reaction = s;
        accentTimer = setTimeout(() => {
          accentTimer = null;
          if (stateKey === reaction && !busy) setStatus("idle");
        }, 3200);
      }
    }
    paintStatus();
    syncStageControls();
    return;
  }
  clearAccentTimer();
  if (s === "idle") guidanceActive = false;
  else if (explicit) guidanceActive = true;
  stateKey = s;
  // Any non-speaking state must leave the mouth shut: the amplitude listener
  // only runs while audio plays, so without this the mouth stayed open (or kept
  // wobbling on the simulated envelope) after speech, a stop or a mute.
  if (s !== "speaking") setFaceTalking(false);
  if (engine && engine.status.stage === "ready") {
    const behaviour = STATUS_BEHAVIOUR[s];
    // Idle always uses the relaxed face; a non-idle expression is only applied
    // when its gesture was actually allowed above.
    const mood = (s === "idle")
      ? "relaxed"
      : (FACE_EXPRESSIONS.has(expression) ? expression : (STATUS_EXPRESSION[s] || "relaxed"));
    if (engine.face && typeof engine.face.setMood === "function") {
      try { engine.face.setMood(mood); } catch (e) { }
    }
    engine.setAuto(false);
    syncFraming();
    if (DRIVEN.has(s)) {
      // listening / thinking / speaking / working hold their clip until an
      // event changes the state; no auto-cycling can interrupt them.
      fireBodyClip(behaviour);
    } else if (s === "idle") {
      // A real idle clip (breathing/blinking) held until an event changes it.
      engine.trigger("idle");
    } else if (behaviour && behaviour !== "idle") {
      // A momentary conversational/guidance accent plays once, then settles.
      fireBodyClip(behaviour);
      const reaction = s;
      accentTimer = setTimeout(() => {
        accentTimer = null;
        if (stateKey === reaction && !busy) setStatus("idle");
      }, 3200);
    }
  }
  paintStatus();
  syncStageControls();
}

function paintStatus() {
  // Nothing is drawn on the avatar any more. The only live status readout is
  // the line inside Settings → Companion, updated only while it is on screen.
  const line = $id("vmCompanionStatusLine");
  if (line) line.textContent = STATUS_LABEL[stateKey] || stateKey;
}

// ── explicit gestures: real VRMA first, smooth bone fallback immediately ───
// The engine owns the .vrma clips, but a first tap can happen before a clip has
// been fetched. These helpers animate the actual VRM humanoid bones for that
// short window, then release them to the real animation. No second engine,
// mixer, face channel or timer chain is introduced.
function humanBoneNode(vrm, name) {
  const humanoid = vrm && vrm.humanoid;
  if (!humanoid) return null;
  const getters = ["getRawBoneNode", "getNormalizedBoneNode"];
  for (const getter of getters) {
    try {
      if (typeof humanoid[getter] === "function") {
        const node = humanoid[getter](name);
        if (node) return node;
      }
    } catch (e) { /* try the next accessor */ }
  }
  try {
    for (const b of (humanoid.humanBones || [])) {
      if (b && (b.name === name || b.bone === name)) {
        const node = b.node || b.boneNode || null;
        if (node) return node;
      }
    }
  } catch (e) { /* fall through to a scene lookup */ }
  try {
    if (vrm.scene && typeof vrm.scene.getObjectByName === "function") {
      return vrm.scene.getObjectByName(name) || null;
    }
  } catch (e) { /* no bones available */ }
  return null;
}

function gestureClipReady(eng, behaviour) {
  if (!eng || !eng.clipCache) return false;
  const pool = (eng.manifest && eng.manifest.behaviours && eng.manifest.behaviours[behaviour]) || [];
  return pool.some((name) => eng.clipCache.has(name));
}

function restoreGestureBones(f) {
  for (const item of (f && f.bones) || []) {
    try {
      item.node.rotation.set(item.base.x, item.base.y, item.base.z, item.base.order);
    } catch (e) { /* a bone may have been detached mid-gesture */ }
  }
}

function applyGestureFrame(f, now) {
  const spec = GESTURE_FALLBACKS[f.behaviour];
  if (!spec) return false;
  const elapsed = Math.max(0, now - f.start);
  const enter = Math.min(1, elapsed / 140);
  const leave = Math.min(1, Math.max(0, (spec.ms - elapsed) / 260));
  let amount = Math.max(0, Math.min(enter, leave));
  if (f.releasing) amount *= Math.max(0, 1 - (now - f.releaseAt) / 180);
  if (elapsed >= spec.ms || amount <= 0) {
    restoreGestureBones(f);
    if (gestureFallback === f) gestureFallback = null;
    return false;
  }
  const wave = Math.sin((elapsed / 1000) * Math.PI * 2 * (spec.oscHz || 1));
  for (const item of f.bones) {
    const pose = spec.bones[item.name];
    try {
      item.node.rotation.set(
        item.base.x + ((pose.x || 0) + (pose.waveX || 0) * wave) * amount,
        item.base.y + ((pose.y || 0) + (pose.waveY || 0) * wave) * amount,
        item.base.z + ((pose.z || 0) + (pose.waveZ || 0) * wave) * amount,
        item.base.order
      );
    } catch (e) { /* keep the remaining bones moving */ }
  }
  return true;
}

function runGestureFallbackFrame() {
  const f = gestureFallback;
  if (!f) return;
  f.raf = 0;
  if (applyGestureFrame(f, Date.now())) {
    f.raf = requestAnimationFrame(runGestureFallbackFrame);
  }
}

function cancelGestureFallback(restore) {
  const f = gestureFallback;
  if (!f) return;
  if (f.raf) {
    try { cancelAnimationFrame(f.raf); } catch (e) { }
    f.raf = 0;
  }
  if (restore) restoreGestureBones(f);
  gestureFallback = null;
}

function releaseGestureFallback() {
  const f = gestureFallback;
  if (!f || f.releasing) return;
  f.releasing = true;
  f.releaseAt = Date.now();
}

function startGestureFallback(behaviour) {
  const spec = GESTURE_FALLBACKS[behaviour];
  if (!spec || !engine || !engine.vrm) return false;
  cancelGestureFallback(true);
  const bones = [];
  for (const name of Object.keys(spec.bones)) {
    const node = humanBoneNode(engine.vrm, name);
    if (!node || !node.rotation || typeof node.rotation.set !== "function") continue;
    const r = node.rotation;
    bones.push({
      name: name, node: node,
      base: { x: r.x, y: r.y, z: r.z, order: r.order },
    });
  }
  if (!bones.length) return false;
  const now = Date.now();
  const f = {
    // Backdate by the ramp so the first frame is already a full, visible pose.
    behaviour: behaviour, bones: bones, start: now - 140,
    raf: 0, releasing: false, releaseAt: 0,
  };
  gestureFallback = f;
  // Pose on the tap itself; RAF then smooths the wave/nod over the full clip gap.
  applyGestureFrame(f, now);
  if (gestureFallback === f && typeof requestAnimationFrame === "function") {
    f.raf = requestAnimationFrame(runGestureFallbackFrame);
  }
  return true;
}

function playVrmGesture(behaviour) {
  const spec = GESTURE_FALLBACKS[behaviour];
  if (!spec) {
    if (!engine || typeof engine.trigger !== "function") return false;
    try { return engine.trigger(behaviour); } catch (e) { return false; }
  }
  if (engine && !gestureClipReady(engine, behaviour)) startGestureFallback(behaviour);
  if (!engine || typeof engine.trigger !== "function") {
    startGestureFallback(behaviour);
    return Promise.resolve(false);
  }
  let played;
  try { played = engine.trigger(behaviour); }
  catch (e) { played = false; }
  return Promise.resolve(played).then((ok) => {
    if (ok) releaseGestureFallback();
    else if (!gestureFallback || gestureFallback.behaviour !== behaviour) startGestureFallback(behaviour);
    return !!ok;
  }).catch(() => {
    if (!gestureFallback || gestureFallback.behaviour !== behaviour) startGestureFallback(behaviour);
    return false;
  });
}

function fireBodyClip(behaviour) {
  let p;
  if (!engine || typeof engine.trigger !== "function") {
    p = GESTURE_FALLBACKS[behaviour] ? playVrmGesture(behaviour) : false;
  } else {
    p = GESTURE_FALLBACKS[behaviour] ? playVrmGesture(behaviour) : engine.trigger(behaviour);
  }
  if (p && typeof p.catch === "function") p.catch(function () { });
  return p;
}

// ── overlay geometry (size / position / dock / drag / Home) ───────────────
function applySize() {
  const el = $id("vmVrmCompanion");
  if (!el) return;
  const narrow = (window.innerWidth || 1280) <= 760;
  if (narrow) {
    // Phones: an inline vh height can round down to a cramped box, so enforce a
    // comfortably visible minimum while still honouring the size pref upward.
    const px = Math.max(132, Math.round((window.innerHeight || 800) * prefs.sizePct / 100));
    el.style.height = px + "px";
  } else {
    el.style.height = prefs.sizePct + "vh";
  }
  refit();
}

// Re-fit the renderer AND the camera framing together. Resizing alone changes
// camera.aspect without re-framing, which crops a shot on a phone in
// landscape/portrait. The engine recomputes BOTH presets on resize, then eases
// to the one the current interaction needs (no snap back to full body mid-talk).
const FULL_BODY_BEHAVIOURS = new Set(["walking", "pointing", "guiding"]);

function currentFraming() {
  const s = stateKey || "idle";
  const behaviour = STATUS_BEHAVIOUR[s] || "idle";
  const conversational = s === "listening" || s === "speaking" || ttsPlaying() || voiceInputActive();
  const gesture = FULL_BODY_BEHAVIOURS.has(behaviour) || guidanceActive;
  return conversational && !gesture ? "close" : "full";
}

function syncFraming() {
  if (!engine || typeof engine.setFraming !== "function") return;
  try { engine.setFraming(currentFraming()); } catch (e) { /* older engine: no-op */ }
}

function refit() {
  if (!engine) return;
  try {
    engine.resize();
    // Older engines only know frameFullBody(); the current engine owns a smooth
    // lerp and keeps the active preset across orientation changes.
    if (typeof engine.setFraming !== "function") {
      if (typeof engine.frameFullBody === "function") engine.frameFullBody();
    } else {
      syncFraming();
    }
  } catch (e) { }
}

// ── workspace-scoped geometry ───────────────────────────────────────────────
// The Studio stage reuses this same node as a normal stage member. Drag is a
// transform from its natural centred position (no !important conflict) and is
// clamped to the STAGE, not the viewport.
// The same helpers keep the avatar inside the stage's real padding (including
// the mobile safe-top inset) after resize/rotation.
const STAGE_DRAG_PAD = 8;

function stagedWorkspace() {
  const el = $id("vmVrmCompanion");
  const stage = $id("vmCompanionStage");
  if (!el || !stage || !el.classList || !stage.contains) return null;
  if (!el.classList.contains("vm-companion-staged")) return null;
  if (!stage.contains(el)) return null;
  return { el: el, stage: stage };
}

function axisClamp(value, lo, hi) {
  if (!isFinite(value)) value = lo;
  if (hi < lo) hi = lo;
  return Math.max(lo, Math.min(hi, value));
}

function readStageOffset(el) {
  return {
    x: parseFloat(el.style.getPropertyValue("--vm-stage-x") || "0") || 0,
    y: parseFloat(el.style.getPropertyValue("--vm-stage-y") || "0") || 0,
  };
}

function writeStageOffset(el, x, y) {
  el.style.setProperty("--vm-stage-x", Math.round(x) + "px");
  el.style.setProperty("--vm-stage-y", Math.round(y) + "px");
}

// Bounding rect WITH the current drag transform applied; subtract that offset
// to recover the layout position the transform is measured from.
function stageBaseRect(el) {
  const r = el.getBoundingClientRect();
  const off = readStageOffset(el);
  return {
    left: r.left - off.x,
    top: r.top - off.y,
    width: r.width || el.offsetWidth || 120,
    height: r.height || el.offsetHeight || 120,
  };
}

function stageDragBounds(stage) {
  const r = stage.getBoundingClientRect();
  let cs = null;
  try { cs = window.getComputedStyle ? window.getComputedStyle(stage) : null; } catch (e) { }
  function padding(key) {
    const n = parseFloat(cs && cs[key]);
    return isFinite(n) ? n : STAGE_DRAG_PAD;
  }
  return {
    left: r.left + padding("paddingLeft"),
    top: r.top + padding("paddingTop"),
    right: r.right - padding("paddingRight"),
    bottom: r.bottom - padding("paddingBottom"),
  };
}

function clampStageOffset(x, y) {
  const ctx = stagedWorkspace();
  if (!ctx) return null;
  const base = stageBaseRect(ctx.el);
  const bounds = stageDragBounds(ctx.stage);
  return {
    x: axisClamp(x, bounds.left - base.left, bounds.right - base.width - base.left),
    y: axisClamp(y, bounds.top - base.top, bounds.bottom - base.height - base.top),
  };
}

function clampPosition() {
  const staged = stagedWorkspace();
  if (staged) {
    const off = readStageOffset(staged.el);
    const c = clampStageOffset(off.x, off.y);
    if (c && (c.x !== off.x || c.y !== off.y)) {
      writeStageOffset(staged.el, c.x, c.y);
      prefs.stageX = Math.round(c.x);
      prefs.stageY = Math.round(c.y);
      savePrefs();
    }
    return;
  }
  const el = $id("vmVrmCompanion");
  if (!el) return;
  const w = el.offsetWidth || 120;
  const h = el.offsetHeight || 120;
  const r = el.getBoundingClientRect();
  const x = Math.max(0, Math.min((window.innerWidth || 1280) - w, r.left));
  const y = Math.max(0, Math.min((window.innerHeight || 800) - h, r.top));
  if (x !== r.left || y !== r.top) {
    el.style.left = Math.round(x) + "px";
    el.style.top = Math.round(y) + "px";
    el.style.right = "auto";
    el.style.bottom = "auto";
    if (prefs.x != null) { prefs.x = Math.round(x); prefs.y = Math.round(y); savePrefs(); }
  }
}

function dockOffsets(dock) {
  const gap = 18;
  if (dock === "bl") return { top: "auto", right: "auto", left: gap + "px", bottom: gap + "px" };
  if (dock === "tr") return { top: gap + "px", right: gap + "px", bottom: "auto", left: "auto" };
  if (dock === "tl") return { top: gap + "px", right: "auto", bottom: "auto", left: gap + "px" };
  return { top: "auto", right: gap + "px", bottom: gap + "px", left: "auto" }; // br
}

function applyPosition() {
  const staged = stagedWorkspace();
  if (staged) {
    // The stage owns top/left here; only the transform offset is ours.
    const c = clampStageOffset(
      prefs.stageX != null ? prefs.stageX : 0,
      prefs.stageY != null ? prefs.stageY : 0
    ) || { x: 0, y: 0 };
    writeStageOffset(staged.el, c.x, c.y);
    return;
  }
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
  // Home is an explicit user reset back to the docked rest pose.
  prefs.userPlaced = false;
  prefs.stageX = null;
  prefs.stageY = null;
  savePrefs();
  const staged = stagedWorkspace();
  if (staged) writeStageOffset(staged.el, 0, 0);
  applySize();
  applyPosition();
  syncControls();
}

// ── direct manipulation (drag to reposition, tap to open chat) ─────────────
// The wrapper stays pointer-transparent so it never swallows taps or drags;
// only the avatar canvas opts in (see index.html). Pointer events bubble from
// the canvas through the wrapper, and window-level move/up handlers keep a
// touch drag alive even when the finger leaves the small canvas box.
let dragState = null;
let dragRaf = 0;
let dragTargetX = 0;
let dragTargetY = 0;

// Eases toward the last pointer target. A short, bounded lerp removes touch
// jitter without lag and always settles exactly on the clamped target.
function runDragFrame() {
  dragRaf = 0;
  const el = $id("vmVrmCompanion");
  if (!dragState || !el) return;
  if (dragState.staged || stagedWorkspace()) {
    const off = readStageOffset(el);
    const dx = dragTargetX - off.x;
    const dy = dragTargetY - off.y;
    if (Math.abs(dx) < 0.6 && Math.abs(dy) < 0.6) {
      writeStageOffset(el, dragTargetX, dragTargetY);
      return;
    }
    writeStageOffset(el, off.x + dx * 0.45, off.y + dy * 0.45);
    dragRaf = requestAnimationFrame(runDragFrame);
    return;
  }
  const r = el.getBoundingClientRect();
  const dx = dragTargetX - r.left;
  const dy = dragTargetY - r.top;
  if (Math.abs(dx) < 0.6 && Math.abs(dy) < 0.6) {
    el.style.left = Math.round(dragTargetX) + "px";
    el.style.top = Math.round(dragTargetY) + "px";
    return;
  }
  el.style.left = Math.round(r.left + dx * 0.45) + "px";
  el.style.top = Math.round(r.top + dy * 0.45) + "px";
  dragRaf = requestAnimationFrame(runDragFrame);
}

function stopDragFrame() {
  if (dragRaf) { cancelAnimationFrame(dragRaf); dragRaf = 0; }
}

function wireDrag() {
  const el = $id("vmVrmCompanion");
  if (!el || el._vmDragWired) return;
  el._vmDragWired = true;
  el.style.touchAction = "none";
  const stage = $id("vmCompanionStage");
  // Touch listeners MUST be non-passive: without preventDefault() the mobile
  // browser scrolls/pans the workspace and cancels the gesture before it moves.
  const passiveFalse = { passive: false };

  function touchPoint(list, identifier) {
    if (!list) return null;
    for (let i = 0; i < list.length; i++) {
      if (identifier == null || (list[i] && list[i].identifier === identifier)) return list[i];
    }
    return null;
  }
  function touchIdentifier(id) {
    return Number(String(id).replace(/^touch:/, ""));
  }

  const begin = (e, point, id, touch) => {
    // Whichever event arrives first (touch or pointer) owns this finger. This
    // also prevents a synthetic pointerdown from restarting a live touch drag.
    if (dragState) return;
    if (!touch) {
      if (e.__vmCompanionDragHandled) return;
      e.__vmCompanionDragHandled = true;
      if (e.button != null && e.button !== 0) return;
    }
    const ctx = stagedWorkspace();
    if (e.currentTarget === stage && (!ctx || (!el.contains(e.target) && e.target !== el))) return;
    const p = point || e;
    if (typeof p.clientX !== "number" || typeof p.clientY !== "number") return;
    const r = el.getBoundingClientRect();
    dragState = {
      id: id != null ? id : e.pointerId,
      dx: p.clientX - r.left, dy: p.clientY - r.top,
      sx: p.clientX, sy: p.clientY, t: Date.now(), moved: false,
      staged: !!ctx, touch: !!touch,
    };
    if (!touch) {
      try { el.setPointerCapture(dragState.id); } catch (err) {
        try { if (e.target && e.target.setPointerCapture) e.target.setPointerCapture(dragState.id); } catch (err2) { }
      }
    }
    cancelWalk();
    if (el.classList) el.classList.add("vm-companion-dragging");
    el.style.cursor = "grabbing";
    if (e.cancelable) e.preventDefault();
  };

  const beginPointer = (e) => begin(e, e, e.pointerId, false);
  const beginTouch = (e) => {
    if (dragState) return;
    const t = touchPoint(e.changedTouches, null);
    if (!t) return;
    begin(e, t, "touch:" + t.identifier, true);
  };

  const move = (e, point, id) => {
    const eventId = id != null ? id : e.pointerId;
    if (!dragState || eventId !== dragState.id) return;
    const p = point || e;
    if (typeof p.clientX !== "number" || typeof p.clientY !== "number") return;
    if (!dragState.moved &&
      Math.abs(p.clientX - dragState.sx) + Math.abs(p.clientY - dragState.sy) > 4) {
      dragState.moved = true;
    }
    if (!dragState.moved) return;
    if (dragState.staged || stagedWorkspace()) {
      // Stage coordinates: subtract the natural (transform-free) layout rect,
      // then clamp to the stage's padded bounds so she never leaves the room.
      const ctx = stagedWorkspace();
      if (!ctx) return;
      const base = stageBaseRect(el);
      const c = clampStageOffset(
        p.clientX - dragState.dx - base.left,
        p.clientY - dragState.dy - base.top
      );
      if (!c) return;
      dragTargetX = c.x;
      dragTargetY = c.y;
    } else {
      const w = el.offsetWidth || 120;
      const h = el.offsetHeight || 120;
      dragTargetX = Math.max(0, Math.min((window.innerWidth || 1280) - w, p.clientX - dragState.dx));
      dragTargetY = Math.max(0, Math.min((window.innerHeight || 800) - h, p.clientY - dragState.dy));
      el.style.right = "auto";
      el.style.bottom = "auto";
    }
    if (!dragRaf) dragRaf = requestAnimationFrame(runDragFrame);
  };

  const movePointer = (e) => {
    if (dragState && dragState.touch) return;
    move(e, e, e.pointerId);
  };
  const moveTouch = (e) => {
    if (!dragState || !dragState.touch) return;
    const t = touchPoint(e.changedTouches, touchIdentifier(dragState.id));
    if (!t) return;
    if (e.cancelable) e.preventDefault();
    move(e, t, dragState.id);
  };

  const end = (e, point, id) => {
    const eventId = id != null ? id : e.pointerId;
    if (!dragState || eventId !== dragState.id) return;
    if (e.cancelable && dragState.touch) e.preventDefault();
    if (dragState.moved) {
      // Settle exactly on the final clamped target before persisting so the
      // stored position matches where the avatar actually comes to rest.
      stopDragFrame();
      if (dragState.staged || stagedWorkspace()) {
        const c = clampStageOffset(dragTargetX, dragTargetY);
        if (c) {
          writeStageOffset(el, c.x, c.y);
          prefs.stageX = Math.round(c.x);
          prefs.stageY = Math.round(c.y);
        }
      } else {
        el.style.left = Math.round(dragTargetX) + "px";
        el.style.top = Math.round(dragTargetY) + "px";
        prefs.x = Math.round(dragTargetX);
        prefs.y = Math.round(dragTargetY);
      }
      prefs.userPlaced = true;
      savePrefs();
    }
    // Tapping the character does nothing: the removed inner box must never
    // reappear from a stage tap.
    stopDragFrame();
    dragState = null;
    if (el.classList) el.classList.remove("vm-companion-dragging");
    el.style.cursor = "grab";
  };

  const endPointer = (e) => {
    if (dragState && dragState.touch) return;
    end(e, e, e.pointerId);
  };
  const endTouch = (e) => {
    if (!dragState || !dragState.touch) return;
    const t = touchPoint(e.changedTouches, touchIdentifier(dragState.id));
    if (!t) return;
    end(e, t, dragState.id);
  };

  el.addEventListener("pointerdown", beginPointer);
  el.addEventListener("touchstart", beginTouch, passiveFalse);
  if (stage && stage.addEventListener) {
    stage.addEventListener("pointerdown", beginPointer);
    stage.addEventListener("touchstart", beginTouch, passiveFalse);
  }

  // Window-level listeners keep the gesture alive outside the canvas/wrapper;
  // el-level listeners are kept for engines where window capture is unavailable.
  el.addEventListener("pointermove", movePointer);
  el.addEventListener("pointerup", endPointer);
  el.addEventListener("pointercancel", endPointer);
  el.addEventListener("touchmove", moveTouch, passiveFalse);
  el.addEventListener("touchend", endTouch, passiveFalse);
  el.addEventListener("touchcancel", endTouch, passiveFalse);
  if (window.addEventListener) {
    window.addEventListener("pointermove", movePointer);
    window.addEventListener("pointerup", endPointer);
    window.addEventListener("pointercancel", endPointer);
    window.addEventListener("touchmove", moveTouch, passiveFalse);
    window.addEventListener("touchend", endTouch, passiveFalse);
    window.addEventListener("touchcancel", endTouch, passiveFalse);
  }
}

// Keep the avatar on-screen and correctly framed after a resize, rotation or
// viewport change (mobile URL bar, keyboard, orientation). Debounced so the
// GPU is not re-sized on every intermediate event.
let resizeTimer = null;
function onViewportChange() {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resizeTimer = null;
    applySize();
    clampPosition();
  }, 120);
}
window.addEventListener("resize", onViewportChange);
window.addEventListener("orientationchange", onViewportChange);

// ── in-memory turn log (no on-character chat UI any more) ─────────────────
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function pushChat(role, text) {
  transcript.push({ role: role, text: text, ts: Date.now() });
  if (transcript.length > 40) transcript.shift();
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
// `explicitEmotion` comes from brain.py's [EMO: ...] tag and always wins.
function emotionForReply(userText, reply, explicitEmotion) {
  if (explicitEmotion && EMOTION_STATUS[explicitEmotion]) return EMOTION_STATUS[explicitEmotion];
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

// Parse the brain's silent wire tags out of a reply. They are control data and
// must never appear in the chat log or be read aloud by TTS.
function takeAnimationTags(text) {
  const raw = String(text || "");
  const anim = raw.match(/\[\s*ANIM\s*:\s*([a-zA-Z][\w-]*)\s*\]/i);
  const emo = raw.match(/\[\s*(?:EMO|EMOTION)\s*:\s*([a-zA-Z][\w-]*)\s*\]/i);
  const clean = raw
    .replace(/\[\s*ANIM\s*:\s*[a-zA-Z][\w-]*\s*\]/gi, "")
    .replace(/\[\s*(?:EMO|EMOTION)\s*:\s*[a-zA-Z][\w-]*\s*\]/gi, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
  return {
    state: anim ? anim[1].toLowerCase().replace(/\s+/g, "_") : "",
    emotion: emo ? emo[1].toLowerCase().replace(/\s+/g, "_") : "",
    text: clean,
  };
}

// Apply a structured animation payload (from an SSE `animation` event or from
// inline tags) to BOTH the body clip and the face channel.
function applyBrainAnimation(anim) {
  if (!anim || typeof anim !== "object") return;
  const key = String(anim.state || "").toLowerCase().replace(/\s+/g, "_");
  const rawStatus = SEMANTIC_STATUS[key] || (STATUS_BEHAVIOUR[key] ? key : "idle");
  // Brain tags never arm a gesture while the companion is idle. A pending AI
  // answer keeps `busy` true, so real conversational accents still resolve.
  const status = resolveStatus(rawStatus, false);
  if (status === "idle") return;
  const expression = FACE_EXPRESSIONS.has(anim.emotion) ? anim.emotion : undefined;
  setStatus(status, expression);
  // One-shot accents should be visible immediately even when a driven status
  // (listening/thinking/speaking) was already active.
  if (expression && engine && engine.face && typeof engine.face.setMood === "function") {
    try { engine.face.setMood(expression); } catch (e) { }
  }
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
      // brain.py strips [ANIM: ...] / [EMO: ...] from the reply and returns a
      // structured `animation` field. Older servers may still inline the tags,
      // so strip those defensively too; the structured field always wins.
      const tags = takeAnimationTags(d.reply || "");
      const reply = tags.text;
      pushChat("ai", reply);
      // Emotion is grounded in THIS exchange, never the Cloud section's default.
      const emo = emotionForReply(text, reply, tags.emotion);
      const anim = (d.animation && d.animation.state)
        ? d.animation
        : (tags.state ? { state: tags.state, emotion: tags.emotion } : null);
      if (anim) applyBrainAnimation(anim);
      // Guidance replies also walk the companion to the target and highlight it.
      if (isGuidanceRequest(text) || isGuidanceReply(reply)) {
        try { runStepGuidance(text, reply); } catch (e) { /* motion is a cue, not required */ }
      }
      if (reply && window.VMCloudVoice && !prefs.muted && !usingNativeAsr()) {
        setStatus("speaking");
        speakText(reply, emo);
      } else if (reply && usingNativeAsr() && !prefs.muted) {
        // Native TTS on Android: the bridge reports real start/end events.
        setStatus("speaking");
        speakNativeReply(reply, emo);
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
  cancelGestureFallback(true);
  if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
  stopNativeSpeech();
  setFaceTalking(false);
  setStatus("idle");
}

function clearResumeTimer() {
  if (resumeTimer) { clearTimeout(resumeTimer); resumeTimer = null; }
}

// Hands-free loop: after a spoken reply (or a graceful error) switch the mic
// back on for the next turn -- but ONLY when the user granted first-use access,
// voice is not muted, the companion is the active character, and the overlay is
// still visible. 250 ms is the echo guard after TTS onEnd: long enough for the
// speaker tail to clear, short enough to answer a follow-up naturally.
const VOICE_ECHO_GUARD_MS = 250;
function resumeListening() {
  clearResumeTimer();
  if (!voiceActive() || !accessAllowed() || prefs.muted || prefs.voiceFirst === false) return;
  if (!isAwake()) return;
  const el = $id("vmVrmCompanion");
  if (!el || el.style.display === "none") return;
  if (!window.VMCloudVoice || !window.VMCloudVoice.supported()) return;
  if (busy || window.VMCloudVoice.isListening()) return;
  resumeTimer = setTimeout(() => {
    resumeTimer = null;
    if (!voiceActive() || !accessAllowed() || prefs.muted || busy) return;
    if (!isAwake()) return;
    if (window.VMCloudVoice.isListening()) return;
    try { window.VMCloudVoice.start(); } catch (e) { }
  }, VOICE_ECHO_GUARD_MS);
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

// Local escape phrase for an active call. It is checked BEFORE the brain so a
// spoken "goodbye" or "stop listening" always ends the loop immediately.
const CALL_STOP_PHRASE = /^(?:stop listening|please stop listening|goodbye|good bye|bye bye)\.?$/i;
function isCallStopPhrase(text) {
  return CALL_STOP_PHRASE.test(String(text || "").trim().toLowerCase());
}

// Explicit mouth control. The face channel now requires TWO gates before it can
// open a viseme: TTS playback is active AND a real amplitude sample arrived from
// that playback. There is no simulated wobble to leak into silence. Every end /
// stop / mute / state change therefore closes the gate and the level.
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
      if (isFinal && isCallStopPhrase(t)) {
        if (nativeCallActive) endCall(); else stop();
        return;
      }
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
      if (greetingActive) { finishAutoGreeting(); return; }
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
  // The face channel ignores a level unless setSpeaking(true) is also active,
  // so a late amplitude frame cannot open the mouth after playback has ended.
  window.VMCloudVoice.setLevelListener(function (level) {
    if (!voiceActive() || !engine || !engine.face) return;
    try {
      if (stateKey === "speaking" && window.VMCloudVoice.isSpeaking()) {
        engine.face.setTalkLevel(level);
      } else {
        setFaceTalking(false);
      }
    } catch (e) { }
  });
}

// ── native TTS fallback lip-sync (Android WebView) ─────────────────────────
// The site's server TTS remains the primary path and supplies TRUE amplitude.
// On Android the engine has no speechSynthesis/Web Audio amplitude, so the
// native bridge reports real engine start/end plus a bounded level envelope.
// This adapter drives the SAME face channel; it never claims measured amplitude.
function applyNativeSpeechEvent(event, value) {
  if (!voiceActive() || !engine || !engine.face) return;
  try {
    if (event === "start") {
      setStatus("speaking");
      setFaceTalking(true);
    } else if (event === "level") {
      // setSpeaking(true) fires on the engine's real onStart; the face channel
      // drops this sample if that gate is not open.
      if (stateKey === "speaking") {
        engine.face.setTalkLevel(Math.max(0, Math.min(1, Number(value) || 0)));
      }
    } else if (event === "end") {
      setFaceTalking(false);
      setStatus(voiceEndEmo || "idle");
    }
  } catch (e) { }
}

function wireNativeSpeech() {
  if (nativeSpeechWired) return;
  const a = window.vmCompanionAndroid;
  if (!a || typeof a.onSpeechEvent !== "function") return;
  nativeSpeechWired = true;
  a.onSpeechEvent(function (ev) {
    if (ev && ev.event) applyNativeSpeechEvent(ev.event, ev.value);
  });
}

// ── Live Companion Call (hands-free native loop, in-app WebView) ───────────
// The main WebView uses the SAME platform SpeechRecognizer as the overlay when
// the Android shell provides one. The loop is listen -> brain -> lip-synced TTS
// -> listen again; it only stops on End / Mute / hide.
const nativeBridge = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.setVisible === "function")
  ? window.vmCompanionAndroid : null;

// The native TTS engine is a separate audio owner from VMCloudVoice, so every
// stop/mute/hide path must silence it explicitly as well. One bridge, one call.
function stopNativeSpeech() {
  if (!nativeBridge || typeof nativeBridge.ttsStop !== "function") return;
  try { nativeBridge.ttsStop(); } catch (e) { }
}

let nativeCallActive = false;
let nativeAsrWired = false;
let nativeAsrRetry = null;

function nativeAsrAvailable() {
  try { return !!(nativeBridge && nativeBridge.hasNativeAsr && nativeBridge.hasNativeAsr()); }
  catch (e) { return false; }
}
function usingNativeAsr() { return nativeCallActive && nativeAsrAvailable(); }

function wireInAppNativeAsr() {
  if (nativeAsrWired || !nativeBridge || typeof nativeBridge.onAsrEvent !== "function") return;
  nativeAsrWired = true;
  nativeBridge.onAsrEvent(function (ev) {
    if (!nativeCallActive || !voiceActive() || prefs.muted) return;
    const type = String((ev && ev.type) || "");
    const reason = String((ev && ev.reason) || "");
    if (type === "final") {
      const text = String((ev && ev.text) || "").trim();
      if (isCallStopPhrase(text)) { endCall(); return; }
      if (text.length >= 2) send(text);
      else resumeNativeListening();
      return;
    }
    if (type === "ready" || type === "partial") { setStatus("listening"); return; }
    if (type === "end") { setStatus("thinking"); return; }
    if (type === "error") {
      if (reason === "busy") { resumeNativeListening(700); return; }
      if (reason === "no-match" || reason === "timeout" || reason === "audio") {
        resumeNativeListening(600); return;
      }
      setStatus("unsure");
      logLine("Microphone unavailable — you can still type to the companion.");
      endCall();
    }
  });
}

function clearNativeAsrRetry() {
  if (nativeAsrRetry) { clearTimeout(nativeAsrRetry); nativeAsrRetry = null; }
}

function resumeNativeListening(delay) {
  clearNativeAsrRetry();
  if (!nativeCallActive || prefs.muted || !isAwake() || !nativeAsrAvailable()) return;
  if (busy || stateKey === "speaking") return;
  nativeAsrRetry = setTimeout(function () {
    nativeAsrRetry = null;
    if (!nativeCallActive || prefs.muted || !isAwake() || busy) return;
    setStatus("listening");
    try {
      if (!nativeBridge.asrStart()) resumeNativeListening(700);
    } catch (e) { resumeNativeListening(700); }
  }, Number(delay) || VOICE_ECHO_GUARD_MS);
}

function startCall() {
  if (!accessAllowed()) { maybePromptAccess(); return; }
  if (prefs.muted) toggleMute();
  nativeCallActive = false;
  stop();
  wireInAppNativeAsr();
  if (nativeAsrAvailable()) {
    nativeCallActive = true;
    syncControls();
    resumeNativeListening(0);
    return;
  }
  // Browser/desktop fallback: the existing VMCloudVoice loop.
  startVoiceSession();
  syncControls();
}

function endCall() {
  nativeCallActive = false;
  clearNativeAsrRetry();
  clearResumeTimer();
  if (nativeBridge && nativeBridge.asrCancel) { try { nativeBridge.asrCancel(); } catch (e) { } }
  stop();
  syncControls();
}

// Device voice ids differ, so the native layer resolves semantic gender requests
// against tts.voices (female first, 1.08f pitch fallback on a single-voice engine).
function nativeVoiceRequest() { return prefs.voiceKey === "marcus" ? "male" : "female"; }

// Native TTS for the in-app call: real engine start/end + bounded level drive
// the same face channel used by server TTS. The site's server TTS still wins on
// desktop; this is the Android WebView fallback that makes speech audible.
function speakNativeReply(text, endEmo) {
  if (!nativeBridge || prefs.muted) { resumeNativeListening(); return false; }
  voiceEndEmo = EMOTION_STATUS[endEmo] ? endEmo : "idle";
  const done = function () {
    setFaceTalking(false);
    setStatus(voiceEndEmo || "idle");
    resumeNativeListening();
  };
  try {
    if (nativeBridge.ttsSpeakV2 && typeof nativeBridge.ttsSpeakV2 === "function") {
      return !!nativeBridge.ttsSpeakV2(String(text), nativeVoiceRequest(), {
        onStart: function () { setStatus("speaking"); setFaceTalking(true); },
        onLevel: function (level) {
          if (stateKey === "speaking") applyNativeSpeechEvent("level", level);
        },
        onEnd: done,
      });
    }
    return !!nativeBridge.ttsSpeak(String(text), nativeVoiceRequest(), done);
  } catch (e) { done(); return false; }
}

// Always-available mute for hands-free voice (the Settings-friendly escape
// hatch). Muting silences in-progress speech and stops auto-listening without
// revoking first-use access; unmuting resumes the hands-free loop on access.
function toggleMute() {
  prefs.muted = !prefs.muted;
  savePrefs();
  if (prefs.muted) {
    if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
    stopNativeSpeech();
    clearNativeAsrRetry();
    if (nativeBridge && nativeBridge.asrCancel) { try { nativeBridge.asrCancel(); } catch (e) { } }
    clearResumeTimer();
    cancelGestureFallback(true);
    setFaceTalking(false);
    setStatus("idle");
  } else if (nativeCallActive && nativeAsrAvailable()) {
    resumeNativeListening(0);
  } else {
    if (accessAllowed()) startVoiceSession();
  }
  syncControls();
  paintStatus();
}

function startVoiceSession() {
  if (!voiceActive() || prefs.muted || !accessAllowed()) return;
  if (!isAwake()) return;
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

// The Studio stage's "Voice + lip-sync" control. This is only a thin edge onto
// the ONE existing voice/brain pipeline: wire the hooks and the native speech
// events, then open the platform's real recognizer. Recognized speech goes
// through send() -> speakText()/speakNativeReply(), whose amplitude listener
// drives the SAME engine.face channel (aa/oh/ou) while TTS is playing.
function startStageVoice() {
  wireVoice();
  wireNativeSpeech();
  maybePromptAccess();
  if (!voiceActive() || prefs.muted || !accessAllowed() || !isAwake()) {
    syncControls();
    return false;
  }
  if (nativeAsrAvailable()) {
    wireInAppNativeAsr();
    startCall();
  } else {
    startVoiceSession();
  }
  syncControls();
  return true;
}

// ── one-switch auto-wake (in-app companion) ────────────────────────────────
// Settings → Companion's ON switch (and a programmatic setVisible(true)) arms
// every capability in one step, waves + smiles, speaks first, and opens the mic
// only on the greeting's REAL TTS onEnd. Reuses VMCloudVoice / native TTS V2 /
// CompanionAsr / VMCloudVision — no second voice or brain pipeline.
function enableAutoCapabilities() {
  prefs.awake = true;
  prefs.voiceFirst = true;
  prefs.muted = false;
  savePrefs();
  try {
    if (window.VMNative && window.VMNative.screen &&
        typeof window.VMNative.screen.promptOnce === "function") {
      window.VMNative.screen.promptOnce();
    }
  } catch (e) { /* browser or older shell: no Accessibility prompt */ }
  if (window.VMCloudVision && !prefs.visionOn) setVision(true);
  wireInAppNativeAsr();
  maybePromptAccess();
  syncControls();
}

function armListeningAfterGreeting() {
  if (!accessAllowed()) { maybePromptAccess(); return; }
  if (nativeAsrAvailable()) {
    nativeCallActive = true;
    wireInAppNativeAsr();
    syncControls();
    resumeNativeListening(0);
  } else {
    startVoiceSession();
  }
}

function finishAutoGreeting() {
  if (!greetingActive) return;
  greetingActive = false;
  setFaceTalking(false);
  setStatus("idle");
  armListeningAfterGreeting();
  syncFraming();
}

function speakGreeting() {
  voiceEndEmo = "happy";
  if (nativeBridge && typeof nativeBridge.ttsSpeakV2 === "function") {
    let finished = false;
    const finish = (ok) => {
      if (finished) return;
      finished = true;
      if (ok) { finishAutoGreeting(); return; }
      // A failed native greeting falls back through the SAME hosted voice path.
      greetingActive = true;
      if (window.VMCloudVoice) { speakText(GREETING_TEXT, "happy"); return; }
      finishAutoGreeting();
    };
    try {
      const started = !!nativeBridge.ttsSpeakV2(GREETING_TEXT, nativeVoiceRequest(), {
        onStart: function () { setStatus("speaking", "happy", true); setFaceTalking(true); syncFraming(); },
        onLevel: function (level) {
          if (stateKey === "speaking" && engine && engine.face) {
            try { engine.face.setTalkLevel(Math.max(0, Math.min(1, Number(level) || 0))); } catch (e) { }
          }
        },
        onEnd: function (ok) { finish(ok !== false); },
      });
      if (!started) finish(false);
    } catch (e) { finish(false); }
    return true;
  }
  if (window.VMCloudVoice) {
    greetingActive = true;
    return speakText(GREETING_TEXT, "happy");
  }
  finishAutoGreeting();
  return false;
}

function startAutoWake() {
  if (autoWakeStarted || autoWakePending) return;
  autoWakeStarted = true;
  greetingActive = true;
  enableAutoCapabilities();
  setStatus("greeting", "happy", true);
  // setStatus selected the real waving clip; settle into happy while she speaks.
  window.setTimeout(function () {
    if (!greetingActive) return;
    try { if (engine) engine.trigger("happy"); } catch (e) { }
  }, 900);
  speakGreeting();
}

// ── voice-interaction state (sleep / wake) ────────────────────────────────
// Prepares a hands-free "wake" lifecycle without ever faking an always-on mic.
// "Awake" is the default. sleep() stops listening and silences speech and
// pauses automatic re-listen; wake() re-arms it on the existing consent. A real
// wake-word detector belongs in a native Android service (browser tabs cannot
// listen while backgrounded) and would simply call wake()/sleep() here -- see
// the report's "native Android" section. No wake phrase is claimed to work.
function isAwake() { return prefs.awake !== false; }

function sleep() {
  prefs.awake = false;
  savePrefs();
  clearResumeTimer();
  if (window.VMCloudVoice) {
    try { if (window.VMCloudVoice.isListening()) window.VMCloudVoice.stopListening(); } catch (e) { }
    try { window.VMCloudVoice.stop(); } catch (e) { }
  }
  setFaceTalking(false);
  setStatus("idle");
  syncControls();
  paintStatus();
}

function wake() {
  prefs.awake = true;
  savePrefs();
  syncControls();
  paintStatus();
  if (accessAllowed() && !prefs.muted) startAutoWake();
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
  guidanceActive = false;
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

// ── structured UI-step navigator (in-app DOM guidance) ─────────────────────
// Guidance replies are turned into visible steps: the companion walks toward
// the target control, plays the real `pointing` clip, highlights the element and
// explains what to tap next. It READS the DOM and never clicks on the user's
// behalf, so the human stays in control.
const GUIDANCE_REQUEST_RE = /\b(where do i (?:tap|click|go)|what do i tap|which (?:button|tab|menu)|help me navigate|show me where|how do i (?:get|open|find)|guide me|walk me through|next step|take me to|stuck on (?:this|the) screen)\b/i;
const GUIDANCE_STEP_RE = /^\s*(?:\d+[.)]|step\s*\d+[:.)-]?|[-•*])\s*(.+)$/i;
const GUIDANCE_ACTION_RE = /\b(tap|click|press|select|choose|open|go to|hit)\b/i;
let guideHighlightTimer = null;

function isGuidanceRequest(text) { return GUIDANCE_REQUEST_RE.test(String(text || "")); }
function isGuidanceReply(reply) {
  const t = String(reply || "");
  return GUIDANCE_STEP_RE.test(t.split("\n").filter(Boolean)[0] || "") || GUIDANCE_ACTION_RE.test(t);
}

function parseGuidanceSteps(reply) {
  const lines = String(reply || "").split(/\n+/).map((s) => s.trim()).filter(Boolean);
  const numbered = lines.map((l) => l.match(GUIDANCE_STEP_RE)).filter(Boolean).map((m) => m[1].trim());
  if (numbered.length) return numbered;
  // Prose fallback: split into sentences that contain an actionable verb.
  return lines.join(" ").split(/(?<=[.!?])\s+/)
    .map((s) => s.trim()).filter((s) => GUIDANCE_ACTION_RE.test(s));
}

function findGuidanceElement(step) {
  const raw = String(step || "");
  const quoted = raw.match(/["'`“]([^"'`”]{2,60})["'`”]/);
  let phrase = quoted ? quoted[1] : "";
  if (!phrase) {
    const m = raw.match(/\b(?:tap|click|press|select|choose|open|go to|hit)\s+(?:the\s+)?([^\s.,!?]+(?:\s+[^\s.,!?]+){0,4})/i);
    phrase = m ? m[1].trim() : "";
  }
  if (!phrase) return null;
  const norm = (s) => String(s || "").toLowerCase().replace(/[\s_\-]+/g, " ").trim();
  const needle = norm(phrase);
  const candidates = document.querySelectorAll(
    'button, a[href], input, select, textarea, [role="button"], [role="tab"], [role="menuitem"], [data-vm-mode], [data-ws], [id]'
  );
  let best = null, bestScore = 0;
  for (const el of candidates) {
    if (!el.getClientRects || !el.getClientRects().length) continue;
    const label = norm([
      el.textContent, el.getAttribute("aria-label"), el.getAttribute("title"),
      el.getAttribute("placeholder"), el.getAttribute("id"), el.value,
    ].filter(Boolean).join(" "));
    if (!label) continue;
    const words = needle.split(" ").filter((w) => w.length > 1);
    const score = words.reduce((n, w) => n + (label.includes(w) ? 1 : 0), 0);
    if (score > bestScore) { bestScore = score; best = el; }
  }
  return bestScore >= Math.max(1, Math.ceil(needle.split(" ").filter((w) => w.length > 1).length / 2)) ? best : null;
}

function injectGuideStyles() {
  if ($id("vmvrmGuideStyle")) return;
  const style = document.createElement("style");
  style.id = "vmvrmGuideStyle";
  style.textContent =
    ".vmvrm-guide-highlight{outline:3px solid #38bdf8!important;outline-offset:3px!important;" +
    "box-shadow:0 0 0 6px rgba(56,189,248,.22),0 0 24px rgba(56,189,248,.45)!important;" +
    "border-radius:8px;position:relative;z-index:100001!important}" +
    "#vmvrmGuideTip{position:fixed;z-index:100002;pointer-events:none;background:#0f172a;" +
    "color:#e0f7ff;border:1px solid #38bdf8;border-radius:8px;padding:5px 9px;font:600 12px/1.3 " +
    "Inter,system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.5);white-space:nowrap}";
  (document.head || document.documentElement).appendChild(style);
}

function highlightGuidanceTarget(el, step) {
  if (!el) return;
  injectGuideStyles();
  clearGuidanceHighlight();
  el.classList.add("vmvrm-guide-highlight");
  const tip = document.createElement("div");
  tip.id = "vmvrmGuideTip";
  tip.textContent = "Tap here → " + String(step || "").slice(0, 72);
  document.body.appendChild(tip);
  const place = () => {
    const r = el.getBoundingClientRect();
    tip.style.left = Math.max(6, Math.min((window.innerWidth || 1280) - 220, r.left)) + "px";
    tip.style.top = Math.max(6, r.top - 34) + "px";
  };
  place();
  window.addEventListener("scroll", place, true);
  window.addEventListener("resize", place);
  guideHighlightTimer = setTimeout(clearGuidanceHighlight, 9000);
}

function clearGuidanceHighlight() {
  if (guideHighlightTimer) { clearTimeout(guideHighlightTimer); guideHighlightTimer = null; }
  const old = document.querySelector(".vmvrm-guide-highlight");
  if (old) old.classList.remove("vmvrm-guide-highlight");
  const tip = $id("vmvrmGuideTip");
  if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
}

function runStepGuidance(userText, reply) {
  const steps = parseGuidanceSteps(reply);
  if (!steps.length) return false;
  clearGuidanceHighlight();
  guidanceActive = true; // an explicit guidance step may point even after TTS ends
  const step = steps[0];
  const el = findGuidanceElement(step);
  const explain = "Step 1: " + step;
  if (el) { try { el.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (e) { } }
  const box = $id("vmVrmCompanion");
  // Let the spoken explanation start first; then walk over, point and highlight.
  setTimeout(() => {
    if (el && box) {
      const r = el.getBoundingClientRect();
      const bw = box.offsetWidth || 120;
      walkTo(r.left - bw - 12, r.top, {
        ms: 850,
        done: () => {
          setStatus("pointing", undefined, true);
          highlightGuidanceTarget(el, step);
          if (!prefs.muted && window.VMCloudVoice) speakText(explain, "idle");
        },
      });
    } else {
      setStatus("pointing", undefined, true);
      if (!prefs.muted && window.VMCloudVoice) speakText(explain, "idle");
      logLine(explain);
    }
  }, 240);
  return true;
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
  syncStageControls();
  syncTaskPermControls();
  paintStatus();
}

// The six Studio controls are the only companion controls. The voice button is
// active while the real mic/native recognizer is open or TTS is speaking; the
// mute button mirrors the persisted mute gate. No inner call bar is involved.
function syncStageControls() {
  let voiceOn = nativeCallActive || stateKey === "speaking";
  try {
    voiceOn = voiceOn || !!(window.VMCloudVoice &&
      typeof window.VMCloudVoice.isListening === "function" && window.VMCloudVoice.isListening());
  } catch (e) { /* voice API not loaded yet */ }
  const voiceBtn = $id("vmCompanionStageVoiceBtn");
  if (voiceBtn) {
    voiceBtn.classList.toggle("on", voiceOn);
    voiceBtn.classList.toggle("active", voiceOn);
    voiceBtn.setAttribute("aria-pressed", voiceOn ? "true" : "false");
  }
  const muteBtn = $id("vmCompanionStageMuteBtn");
  if (muteBtn) {
    muteBtn.classList.toggle("on", !!prefs.muted);
    muteBtn.classList.toggle("active", !!prefs.muted);
    muteBtn.setAttribute("aria-pressed", prefs.muted ? "true" : "false");
  }
}

function buildUI() {
  if (uiBuilt) return;
  uiBuilt = true;

  // The six Stage controls live in index.html and call the public API directly
  // (startStageVoice / toggleMute / gesture). There is no inner chat/call box to
  // bind here; the canvas only drags and never opens one.
  wireDrag();

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
// The companion has no attached controls any more. The six Studio stage buttons
// and the main sidebar's Settings → Companion section drive it exclusively
// through this public API (wired in static/settings.js).
function setVisible(on) {
  prefs.visible = !!on;
  savePrefs();
  if (on) {
    // The Settings "Show companion" switch is the in-app one-switch edge.
    // The pending flag stops show()'s normal auto-listen from opening the mic
    // before the greeting has finished.
    autoWakePending = true;
    Promise.resolve(show()).then(function () { autoWakePending = false; startAutoWake(); });
  } else {
    hide();
    autoWakeStarted = false;
    greetingActive = false;
  }
  syncControls();
}

function isVisible() {
  const el = $id("vmVrmCompanion");
  return getMode() === "vrm" && prefs.visible !== false && !!el && el.style.display !== "none";
}

function setSize(pct) {
  const v = Math.max(10, Math.min(100, Number(pct) || DEFAULTS.sizePct));
  prefs.sizePct = v;
  savePrefs();
  applySize();
  syncControls();
}

function setDock(d) {
  prefs.dock = ["br", "bl", "tr", "tl"].indexOf(d) !== -1 ? d : "br";
  prefs.x = null;
  prefs.y = null;
  prefs.userPlaced = false; // an explicit dock clears the manual-placement lock
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
  prefs.voiceKey = (key && ["marcus", "elena", "angelina"].indexOf(key) !== -1)
    ? key : DEFAULTS.voiceKey;
  savePrefs();
  syncControls();
}

// Programmatic screen-context switch (the Settings toggle). All surfaces
// converge on prefs.visionOn; there is no separate screen button on the stage.
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

// Legacy setting kept so older callers do not break. Autonomous idle roaming
// is intentionally disabled; walking only happens during explicit guidance.
function setRoam(on) {
  prefs.roam = false;
  savePrefs();
  syncControls();
}

// Awake toggle: the hands-free readiness gate (see sleep()/wake()).
function setAwake(on) {
  if (!!on) wake(); else sleep();
}

function getProps() {
  return {
    mode: getMode(),
    visible: prefs.visible !== false,
    visibleNow: isVisible(),
    sizePct: prefs.sizePct,
    dock: prefs.dock,
    skin: prefs.skin || "",
    roam: false, // idle roaming is permanently disabled
    userPlaced: prefs.userPlaced === true,
    awake: prefs.awake !== false,
    charName: prefs.charName || DEFAULTS.charName,
    voiceKey: prefs.voiceKey || DEFAULTS.voiceKey,
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
  // No floating default: the body may only render inside its Studio workspace.
  if (!companionWorkspaceActive()) { hide(); return; }
  // Persisted, recoverable hide: once the user hides the companion from
  // Settings it stays hidden across navigations/refreshes until re-enabled
  // there (never an irreversible hide).
  if (prefs.visible === false) { hide(); return; }
  setDataset();
  buildUI();
  wireVoice();
  wireNativeSpeech();
  const el = $id("vmVrmCompanion");
  if (!el) return;
  el.style.display = "block";
  applySize();
  applyPosition();
  // A persisted manual position can fall outside a smaller viewport (e.g. a
  // phone after the window shrank or rotated): pull it back into view.
  clampPosition();
  syncControls();
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
  if (accessAllowed() && !prefs.muted && !autoWakePending) startVoiceSession();
}

function hide() {
  setDataset();
  cancelWalk();
  nativeCallActive = false;
  clearNativeAsrRetry();
  if (nativeBridge && nativeBridge.asrCancel) { try { nativeBridge.asrCancel(); } catch (e) { } }
  clearResumeTimer();
  cancelGestureFallback(true);
  guidanceActive = false;
  greetingActive = false;
  autoWakeStarted = false;
  autoWakePending = false;
  if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
  stopNativeSpeech();
  setFaceTalking(false);
  const el = $id("vmVrmCompanion");
  if (el) {
    el.style.display = "none";
    if (el.classList) el.classList.remove("vm-companion-dragging");
  }
  if (engine) engine.setRendering(false);
}

function cleanup() {
  cancelGestureFallback(true);
  if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
  stopNativeSpeech();
  hide();
}

function setMode(mode, opts) {
  opts = opts || {};
  prefs.mode = mode === "cloud" ? "cloud" : "vrm";
  if (opts.fromUser) prefs.visible = true;
  savePrefs();
  setDataset();
  if (prefs.mode === "vrm") {
    if (opts.fromUser) autoWakePending = true;
    Promise.resolve(show()).then(function () {
      if (!opts.fromUser) return;
      autoWakePending = false;
      startAutoWake();
    });
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

// The VRM body now has one dedicated home: Studio → 3D Companion. Both the
// auth-time auto-show and the shell dispatch must refuse to float her over
// normal chat / general Studio views. The workspace mount calls show() direct.
function companionWorkspaceActive() {
  return typeof window.vmCompanionWorkspaceActive === "function" && window.vmCompanionWorkspaceActive();
}

function maybeAutoShow() {
  if (getMode() !== "vrm") return;
  if (!companionWorkspaceActive()) return;
  if (typeof window.vmIsAuthenticated === "function" && window.vmIsAuthenticated()) {
    show();
  } else {
    maybePromptAccess();
  }
}

// One-shot, real-clip gestures from the six Studio controls. The button words
// (wave/nod/point/think) are accepted as aliases for the canonical statuses,
// which fire the corresponding VRMA behaviour with a bone fallback if needed.
function gesture(name) {
  const raw = String(name || "").toLowerCase();
  const key = GESTURE_STATUS[raw] || raw;
  if (!STATUS_BEHAVIOUR[key]) return false;
  setStatus(key, undefined, true); // explicit caller request, never an idle shuffle
  return true;
}

// ── window hooks exposed to the classic-script app shell ─────────────────
window.vmVrmCompanionShow = show;
window.vmVrmCompanionCleanup = cleanup;
window.vmVrmCompanionDispatch = function () {
  setDataset();
  if (!companionWorkspaceActive()) { hide(); return; }
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
  startStageVoice: startStageVoice,
  accessDecision: accessDecision,
  allowAccess: function () {
    setAccessDecision("allowed");
    hideAccessPrompt();
    if (autoWakeStarted && !greetingActive) armListeningAfterGreeting();
    else startAutoWake();
  },
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
  // ── life / wandering / voice state (Phase-1 companion) ───────────────────
  setRoam: setRoam,
  sleep: sleep,
  wake: wake,
  setAwake: setAwake,
  isAwake: isAwake,
  refit: refit,
  // Discards a manual placement and returns to the docked rest pose.
  resetPlacement: function () { prefs.userPlaced = false; savePrefs(); home(); },
  /**
   * The documented seam between "AI decides the state" and the avatar:
   * persistent driven states (listening / thinking / speaking / working) lock
   * the avatar to a matching .vrma clip; momentary emotional accents nudge the
   * auto state machine. Any other value returns to idle autonomy.
   */
  applyStatus: function (s) { setStatus(s); },
  getStatus: function () { return stateKey; },
  // brain.py [ANIM: ...] / SSE `animation` event seam.
  applyAnimation: applyBrainAnimation,
  // Live Companion Call controls (native SpeechRecognizer when available).
  startCall: startCall,
  endCall: endCall,
  // Structured, read-only DOM guidance used by the UI-step navigator.
  guideToElement: function (step) {
    const el = findGuidanceElement(step);
    if (!el) return false;
    highlightGuidanceTarget(el, step);
    return true;
  },
  clearGuidanceHighlight: clearGuidanceHighlight,
  gesture: gesture,
  applySpeechEvent: applyNativeSpeechEvent,
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

// Hidden tab: stop spending GPU time; resume cleanly when the tab is visible
// again. There are no idle timers to pause any more — the idle clip simply keeps
// its held pose until a real event changes it.
document.addEventListener("visibilitychange", () => {
  if (!engine) return;
  if (document.hidden) engine.setRendering(false);
  else if (isVisible()) engine.setRendering(true);
});

// Cookie-authenticated opens / refreshes / new tabs: show immediately once
// the app shell reports auth (mirrors the Cloud character's showIfAuthed()).
if (document.readyState !== "loading") {
  maybeAutoShow();
} else {
  document.addEventListener("DOMContentLoaded", maybeAutoShow);
}