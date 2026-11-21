/*
 * companion_overlay.js — renderer for the Android floating-companion surface.
 *
 * This is the SAME companion as everywhere else; only the chrome differs — and
 * there is almost none: the character fills a compact transparent stage, with a
 * dark-glass micro-pill directly beneath her feet (Talk/Mute Mic, Camera Eyes,
 * Close). Tapping the character or the mic starts the hands-free conversation
 * (listen -> think -> speak -> listen). No panel, no text box, no status pill,
 * and no ring behind the character. Idle is locked to the engine's breathing /
 * blinking idle pool; every other clip comes from a real turn or guidance step.
 *
 * Reuse map (nothing is reimplemented):
 *   body animation -> /vrm-preview/assets/companion.js  (the ONE engine)
 *   face / mouth   -> engine.face.setTalkLevel()        (real TTS amplitude)
 *   AI backend     -> POST /api/cloud/chat              (same brain as the site)
 *   voice          -> window.VMCloudVoice               (/api/tts + microphone)
 *   STT fallback   -> POST /api/editing/transcribe      (Groq Whisper)
 *   turn logic     -> window.VMTurnSM                   (the turn state machine)
 *
 * LISTENING ORDER (first that actually works wins):
 *   1. the platform recogniser (window.vmCompanionAndroid.asr*) — Android's
 *      WebView has no Web Speech API, so the native SpeechRecognizer is the
 *      only path that both reports the end of speech AND keeps the audio on
 *      the device;
 *   2. window.VMCloudVoice — the browser's own recognition (desktop / a
 *      WebView build that ships it);
 *   3. MediaRecorder + the server's existing transcription endpoint — the last
 *      resort, unchanged from before.
 *
 * The overlay owns no conversation state of its own: it talks to the same
 * backend endpoints with the same session cookie as the ValleyMind app, so
 * there is exactly ONE brain and ONE conversation.
 */

import { Companion } from "/vrm-preview/assets/companion.js";

const $ = (id) => document.getElementById(id);
const canvas = $("stage");
const root = $("vmOverlayRoot");
const pill = $("vmMicroPill");
const talkBtn = $("btnTalk");
const camBtn = $("btnCamera");
const hideBtn = $("btnHide");

const engine = new Companion(canvas, () => {}, { transparent: true });
// No autonomous shuffling: idle is held to the breathing/blinking idle pool and
// every other clip comes from a real turn or an explicit guidance step.
engine.setAuto(false);
const voice = window.VMCloudVoice || null;
const android = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.setVisible === "function")
  ? window.vmCompanionAndroid
  : null;

// Native TTS engine (Android WebView has no speechSynthesis). V2 is preferred
// because it reports the engine's real utterance start/end; the legacy callback
// is the last-resort fallback when even V2 cannot speak.
const nativeTts = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.ttsSpeak === "function")
  ? window.vmCompanionAndroid
  : null;

// Enhanced native TTS: real engine start/end plus a bounded level envelope for
// the mouth. It is the overlay's FIRST choice; server TTS remains the fallback
// and supplies TRUE amplitude when the hosted voice is reachable.
const nativeTtsV2 = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.ttsSpeakV2 === "function")
  ? window.vmCompanionAndroid
  : null;

// The platform speech recogniser (same bridge, same process).
const nativeAsr = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.asrStart === "function")
  ? window.vmCompanionAndroid
  : null;

// Camera eyes. Native code only captures a JPEG frame; this page posts it to
// the SAME authenticated /api/vision/analyze endpoint the rest of the app uses.
const nativeCamera = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.cameraCapture === "function")
  ? window.vmCompanionAndroid
  : null;

// Prompts that should make the companion actually LOOK before it answers.
const VISION_PROMPT = /\b(what\s+am\s+i\s+wearing|look\s+at\s+this|what\s+do\s+you\s+see|describe\s+what\s+you\s+see|what(?:'s|\s+is)\s+this)\b/i;

// Navigation prompts mean "inspect the active screen, then guide me step by
// step". They are answered from the optional Android Accessibility UI-tree (or
// a CameraX frame routed through the SAME /api/vision/analyze endpoint) and the
// normal brain — never from a guess.
const NAV_PROMPT = /\b(where\s+do\s+i\s+(?:tap|click|go)|what\s+do\s+i\s+tap|which\s+(?:button|tab|menu)|help\s+me\s+navigate|show\s+me\s+where|how\s+do\s+i\s+(?:get|open|find|use)|guide\s+me|walk\s+me\s+through|next\s+step|stuck\s+on\s+(?:this|the)\s+screen|look\s+at\s+(?:this|my)\s+screen)\b/i;

// brain.py semantic states -> real engine behaviours + face expressions.
const ANIM_BEHAVIOUR = {
  idle: "idle", listen: "listening", listening: "listening", think: "thinking",
  thinking: "thinking", work: "working", working: "working", observe: "listening",
  look: "pointing", curious: "listening", greet: "waving", wave: "waving",
  welcome: "waving", explain: "talking", talk: "talking", speaking: "talking",
  ask: "listening", nod: "nodding", agree: "nodding", celebrate: "happy",
  happy: "happy", success: "happy", point: "pointing", guide: "pointing",
  walk: "walking", lead: "walking", sit: "sitting", gesture: "gestures",
  empathetic: "listening", sympathize: "listening", sad: "sad",
  disagree: "headshake", headshake: "headshake", unsure: "unsure",
  confused: "unsure", surprised: "idle", blink: "idle", angry: "angry",
};
const ANIM_MOOD = {
  happy: "happy", celebrate: "happy", success: "happy", greet: "happy",
  wave: "happy", welcome: "happy", empathetic: "sad", sad: "sad",
  angry: "angry", surprised: "surprised", curious: "surprised", look: "surprised",
  observe: "surprised", unsure: "surprised", confused: "surprised",
  relaxed: "relaxed", blink: "blink", neutral: "neutral",
};

// The turn state machine is pure logic; the page supplies every side effect.
const sm = (window.VMTurnSM && window.VMTurnSM.create)
  ? window.VMTurnSM.create({ dedupeWindowMs: 15000, maxFailures: 3 })
  : null;

// ── voice profile (Task 4) ────────────────────────────────────────────────
// The four free voices the user chose. The value stored/read here is the
// SHARED preference the main app writes (see static/settings.js) so the two
// same-origin WebViews always agree on who the companion sounds like.
const VOICE_PROFILE_KEY = "vmVoiceProfile";
const VOICE_PROFILES = [
  { id: "marcus", label: "Marcus", gender: "masculine" },
  { id: "angelina", label: "Angelina", gender: "feminine" },
  { id: "elena", label: "Elena", gender: "feminine" },
  { id: "companion", label: "Companion", gender: "neutral" },
];
// The VRM companion speaks with Elena's natural female voice unless the user
// explicitly saved another profile. "companion" remains readable for old saved
// values and is normalised to the same female default below.
const DEFAULT_PROFILE = "elena";
const GREETING_TEXT = "Hi! I'm right here—how can I help you today?";

function readProfile() {
  try {
    const v = String(localStorage.getItem(VOICE_PROFILE_KEY) || "").toLowerCase();
    if (v === "companion") return DEFAULT_PROFILE;
    if (VOICE_PROFILES.some((p) => p.id === v)) return v;
  } catch (e) { /* storage blocked */ }
  return DEFAULT_PROFILE;
}

// System-TTS voice ids for the native fallback (see SystemTts.kt). Kept beside
// the profile list so there is one place that maps a profile to a native voice.
const PROFILE_NATIVE_VOICE = {
  marcus: "en-us-x-iol-local",
  angelina: "en-us-x-sfg-local",
  elena: "en-us-x-tpf-local",
  companion: "en-us-x-tpd-local",
};

// Device voice ids differ across Android builds. The native layer resolves these
// semantic requests against tts.voices (female first, higher-pitch fallback).
function nativeVoiceRequest() {
  return profile === "marcus" ? "male" : "female";
}

let profile = readProfile();
let autoWakeStarted = false;
let screenContextOn = false;
let screenContextCache = null;

// ── transient state ───────────────────────────────────────────────────────
const ECHO_GUARD_MS = 250;
// Spoken escape hatch. Checked before the brain so it always ends the loop.
const CALL_STOP_PHRASE = /^(?:stop listening|please stop listening|goodbye|good bye|bye bye)\.?$/i;

function isCallStopPhrase(text) {
  return CALL_STOP_PHRASE.test(String(text || "").trim().toLowerCase());
}

const state = {
  chatId: "",
  recorder: null,
  chunks: [],
  recording: false,
  vad: null,           // MediaRecorder VAD (getUserMedia) fallback
  vadRaf: 0,
  vadHadSpeech: false,
  watchdog: 0,
  wakeTimer: 0,        // wakes the turn if the wake animation never reports back
  asrTimer: 0,         // delayed start of the platform recogniser
  listenTimer: 0,      // 250 ms echo-guard before re-opening the mic
  errorTimer: 0,       // bounded automatic retry after a hard failure
  behaviour: "",       // the body animation currently playing
  speaking: false,
  session: false,      // a hands-free conversation is running
  muted: false,        // explicit Mute escape hatch (stops auto-resume)
  animation: null,     // last semantic animation resolved from the brain
  lastReply: "",       // used to fall back from a failed server TTS to native TTS
  restartDelayMs: 0,   // set by TTS onEnd, consumed by the next start-listen effect
  ttsSeq: 0,           // invalidates callbacks from a stopped/superseded utterance
  ttsBackend: "",      // which backend may still emit events for this utterance
  ttsFallbackUsed: false, // one bounded fallback per reply, never a retry loop
};

// The face channel requires BOTH the TTS gate and a live amplitude sample.
// Opening the gate alone never moves a viseme; ending playback closes both.
function setFaceSpeaking(on) {
  if (!engine.face) return;
  try {
    if (on) engine.face.setSpeaking(true);
    else { engine.face.setSpeaking(false); engine.face.setTalkLevel(0); }
  } catch (e) { /* a face channel is optional */ }
}

function closeMouth() { setFaceSpeaking(false); }

function speechCurrent(seq, backend) {
  return state.ttsSeq === seq && state.speaking &&
    (!backend || state.ttsBackend === backend);
}

function beginSpeech(text) {
  state.ttsSeq++;
  state.ttsBackend = "";
  state.ttsFallbackUsed = false;
  state.lastReply = String(text || "");
  return state.ttsSeq;
}

// A silent backend must not hold the hands-free turn hostage. The timeout is
// bounded by the length of the reply, exactly like the original TTS watchdog.
function armTtsWatchdog(seq, text) {
  clearWatchdog();
  state.watchdog = window.setTimeout(() => {
    if (!speechCurrent(seq)) return;
    dispatch({ type: "SPEAK_TIMEOUT" });
  }, Math.max(8000, String(text || "").length * 90));
}

// Every successful TTS end (native engine or server audio) enters the next
// listening turn through this one seam, so the 250 ms echo guard cannot be
// bypassed by a future voice backend.
function finishSpeaking(success, seq) {
  if (!speechCurrent(seq)) return;
  clearWatchdog();
  closeMouth();
  state.ttsBackend = "";
  if (!success) { dispatch({ type: "SPEAK_ERR" }); return; }
  state.restartDelayMs = ECHO_GUARD_MS;
  dispatch({ type: "SPEAK_END" });
}

// ── voice module hooks (reused, not reimplemented) ────────────────────────
function wireVoice() {
  if (!voice) return;
  voice.setHooks({
    onListeningChange: () => { /* the SM drives the state, not the module */ },
    onResult: (text, final) => { if (final) handleFinalTranscript(text); },
    onSpeakStart: () => {
      if (state.ttsBackend === "server") setFaceSpeaking(true);
    },
    onSpeakEnd: (reason) => { handleServerSpeechEnd(reason, state.ttsSeq); },
    onError: (err) => {
      if (state.ttsBackend === "server") handleServerSpeechEnd("tts failed", state.ttsSeq);
      else dispatch({ type: "SPEECH_ERR", reason: String(err || "asr") });
    },
  });
  if (typeof voice.setLevelListener === "function") {
    voice.setLevelListener((level) => {
      // setTalkLevel() is ignored unless setSpeaking(true) was called by the
      // matching TTS onStart, so silence and stale frames cannot open the mouth.
      if (engine.face && state.speaking) engine.face.setTalkLevel(level);
    });
  }
}

// One entry point for every final transcript. A spoken stop phrase is handled
// locally and immediately; real speech goes to the one brain as usual.
function handleFinalTranscript(text) {
  const clean = String(text || "").trim();
  if (isCallStopPhrase(clean)) {
    stopListening();
    closeMouth();
    dispatch({ type: "STOP" });
    return;
  }
  dispatch({ type: "SPEECH_FINAL", text: clean });
}

// ── platform speech recognition (native) ───────────────────────────────────
// The recogniser reports: ready / partial / end (the platform says the
// utterance is over) / final (the transcript) / error (a stable reason token).
function wireNativeAsr() {
  if (!nativeAsr || typeof nativeAsr.onAsrEvent !== "function") return;
  nativeAsr.onAsrEvent((ev) => {
    if (!ev || typeof ev !== "object") return;
    const reason = String(ev.reason || "");
    if (ev.type === "final") {
      handleFinalTranscript(String(ev.text || ""));
      return;
    }
    if (ev.type === "ready" || ev.type === "partial") {
      // The utterance is still in progress: the watchdog measures SILENCE, not
      // total speaking time, so a long sentence is never cut off at 12s.
      armSpeechWatchdog(12000);
      return;
    }
    if (ev.type === "end") {
      // The platform detected the end of the utterance; the final transcript
      // follows. Bound the wait for it.
      armSpeechWatchdog(8000);
      return;
    }
    if (ev.type !== "error") return;
    if (reason === "no-match" || reason === "timeout" || reason === "audio") {
      // Nothing was heard: a soft failure, the session survives.
      dispatch({ type: "SPEECH_FINAL", text: "" });
      return;
    }
    if (reason === "busy") {
      // The platform was still busy: try again shortly, without leaving the
      // listening state. The old watchdog must not fire during the retry.
      clearWatchdog();
      if (state.asrTimer) { clearTimeout(state.asrTimer); state.asrTimer = 0; }
      state.asrTimer = window.setTimeout(() => { state.asrTimer = 0; startListening(); }, 700);
      return;
    }
    if (reason === "permission") {
      dispatch({ type: "SPEECH_ERR", reason });
      return;
    }
    dispatch({ type: "SPEECH_ERR", reason });
  });
}

// ── side-effect handlers (the SM returns these; we execute them) ──────────
function cancelPendingListen() {
  if (state.listenTimer) { clearTimeout(state.listenTimer); state.listenTimer = 0; }
}

function startListeningAfterEchoGuard() {
  const delay = state.restartDelayMs;
  state.restartDelayMs = 0;
  cancelPendingListen();
  if (state.muted) return;
  if (!delay) { startListening(); return; }
  // The state machine has already moved to LISTENING; keep that state while the
  // speaker tail clears, then open CompanionAsr for the next follow-up.
  state.listenTimer = window.setTimeout(() => {
    state.listenTimer = 0;
    if (state.muted) return;
    startListening();
  }, delay);
}

function execute(effects) {
  for (const eff of effects) {
    switch (eff.type) {
      case "wake": triggerWake(); break;
      case "greet": playGreetingAnimation(); break;
      case "start-listen": startListeningAfterEchoGuard(); break;
      case "stop-listen": stopListening(); break;
      case "transcribe": transcribeAndChat(eff.blob); break;
      case "chat": sendChat(eff.text); break;
      case "speak": speakReply(eff.reply); break;
      case "stop-speak": stopSpeaking(); break;
      case "abort": abortInFlight(); break;
      case "feedback": cue(eff.message); break;
      case "session-end": endSession(eff.reason); break;
    }
  }
}

function dispatch(event) {
  if (!sm) return;
  const res = sm.send(event);
  applyState(res.state);
  execute(res.effects);
}

// ── the wake-up: a wave, then the microphone opens ────────────────────────
function triggerWake() {
  if (state.wakeTimer) { clearTimeout(state.wakeTimer); state.wakeTimer = 0; }
  // The fallback guarantees the turn advances even if the animation never
  // reports back (missing clip, offline fetch).
  state.wakeTimer = window.setTimeout(() => {
    state.wakeTimer = 0;
    dispatch({ type: "WAKE_DONE" });
  }, 1500);
  // The state may already have started the wave; re-triggering would restart
  // the clip and never settle.
  if (state.behaviour === "waving") return;
  try {
    const p = engine.trigger("waving");
    if (p && typeof p.then === "function") {
      p.then(() => {
        if (!state.wakeTimer) return;
        clearTimeout(state.wakeTimer);
        state.wakeTimer = 0;
        dispatch({ type: "WAKE_DONE" });
      }).catch(() => { /* the fallback timer still advances the turn */ });
    }
  } catch (e) { /* the fallback timer still advances the turn */ }
}

// ── one-switch startup: wave, smile, speak first, then listen ─────────────
// The overlay WebView is created the instant the outside-app switch is turned
// on, so boot() is the switch edge. The greeting is an ordinary spoken turn:
// speakReply() opens the SAME lip-sync gate as every other reply, and its real
// TTS onEnd emits SPEAK_END, which is the only thing that opens CompanionAsr.
function playGreetingAnimation() {
  state.behaviour = "waving";
  try {
    if (engine.face && typeof engine.face.setMood === "function") engine.face.setMood("happy");
  } catch (e) { /* the pose is a cue, not required */ }
  let settled = false;
  const toHappy = () => {
    if (settled) return;
    settled = true;
    if (state.wakeTimer) { clearTimeout(state.wakeTimer); state.wakeTimer = 0; }
    state.behaviour = "happy";
    try { engine.trigger("happy"); } catch (e) { /* keep the wave pose */ }
  };
  state.wakeTimer = window.setTimeout(toHappy, 900);
  try {
    const p = engine.trigger("waving");
    if (p && typeof p.then === "function") p.then(toHappy).catch(toHappy);
    else if (p === false) toHappy();
  } catch (e) { toHappy(); }
}

function startAutoWake() {
  if (autoWakeStarted) return;
  autoWakeStarted = true;
  if (voice && typeof voice.unlock === "function") { try { voice.unlock(); } catch (e) {} }
  // The switch is explicit: unmute and arm the hands-free preference in one step.
  state.muted = false;
  state.keepMuted = false;
  state.restartDelayMs = 0;
  syncCallControls();
  enableScreenAwareness();
  dispatch({ type: "AUTO_START", reply: GREETING_TEXT });
}

// ── listening ─────────────────────────────────────────────────────────────
function nativeAsrAvailable() {
  try { return !!(nativeAsr && nativeAsr.hasNativeAsr && nativeAsr.hasNativeAsr()); }
  catch (e) { return false; }
}

function startListening() {
  clearWatchdog();
  if (nativeAsrAvailable()) {
    // The platform recogniser needs a short gap between sessions; asking it
    // to start immediately after the previous one is answered with
    // ERROR_RECOGNIZER_BUSY.
    if (state.asrTimer) { clearTimeout(state.asrTimer); state.asrTimer = 0; }
    state.asrTimer = window.setTimeout(() => {
      state.asrTimer = 0;
      let started = false;
      try { started = !!nativeAsr.asrStart(); } catch (e) { started = false; }
      if (!started) { listenFallback(); return; }
      armSpeechWatchdog(12000);
    }, 300);
    return;
  }
  listenFallback();
}

// The browser / recording fallbacks, used when the platform has no
// recogniser (or refuses to start one).
function listenFallback() {
  if (voice && voice.supported && voice.supported()) {
    try { voice.startListening(); } catch (e) { dispatch({ type: "SPEECH_TIMEOUT" }); return; }
    state.watchdog = window.setTimeout(() => dispatch({ type: "SPEECH_TIMEOUT" }), 12000);
    return;
  }
  startVadRecording();
}

function stopListening() {
  clearWatchdog();
  cancelPendingListen();
  state.restartDelayMs = 0;
  if (state.asrTimer) { clearTimeout(state.asrTimer); state.asrTimer = 0; }
  if (nativeAsr && nativeAsr.asrStop) { try { nativeAsr.asrStop(); } catch (e) {} }
  if (voice && voice.stopListening) { try { voice.stopListening(); } catch (e) {} }
  stopVadRecording();
}

function abortInFlight() {
  if (state.req) { try { state.req.abort(); } catch (e) {} state.req = null; }
}

function stopSpeaking() {
  // Invalidate every pending backend callback before stopping the engines.
  state.ttsSeq++;
  state.ttsBackend = "";
  clearWatchdog();
  closeMouth();
  // The bridge exposes the native engine as ttsStop() (ttsSpeak/ttsStop).
  if (nativeTts && nativeTts.ttsStop) { try { nativeTts.ttsStop(); } catch (e) {} }
  if (voice && voice.stop) { try { voice.stop(); } catch (e) {} }
  state.speaking = false;
}

// ── the end of a hands-free conversation ──────────────────────────────────
function endSession(reason) {
  if (state.errorTimer) { clearTimeout(state.errorTimer); state.errorTimer = 0; }
  if (state.wakeTimer) { clearTimeout(state.wakeTimer); state.wakeTimer = 0; }
  if (state.asrTimer) { clearTimeout(state.asrTimer); state.asrTimer = 0; }
  if (nativeAsr && nativeAsr.asrCancel) { try { nativeAsr.asrCancel(); } catch (e) {} }
  state.session = false;
  // End resets the mute escape hatch; an explicit Mute keeps it latched until
  // the user taps UNMUTE (which starts a fresh session).
  if (reason !== "muted" && !state.keepMuted) state.muted = false;
  state.keepMuted = false;
  state.animation = null;
  syncCallControls();
}

// The compact pill's Talk/Mute Mic button is a single toggle: tap once to start
// hands-free conversation, tap again to mute/stop it, tap while muted to resume.
// A soft centred glow (CSS, driven by root[data-state]) shows live listening or
// speech without a ring anywhere near the character's legs.
function syncCallControls() {
  if (talkBtn) {
    talkBtn.classList.toggle("on", !!state.session);
    talkBtn.classList.toggle("muted", !!state.muted);
    talkBtn.setAttribute("aria-pressed", state.session ? "true" : "false");
    const label = state.muted
      ? "Unmute microphone and restart conversation"
      : (state.session ? "Mute microphone and end conversation" : "Talk to ValleyMind");
    talkBtn.setAttribute("aria-label", label);
    talkBtn.title = label;
  }
  syncCameraButton();
}

// ── VAD recording fallback (no Web Speech recognition available) ──────────
// Uses getUserMedia + an AnalyserNode to decide when the user stopped talking,
// then hands the blob to the server's existing Groq Whisper endpoint.
async function startVadRecording() {
  if (state.recording) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.chunks = [];
    state.vadHadSpeech = false;
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (e) => { if (e.data && e.data.size) state.chunks.push(e.data); };
    state.recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      stopVadLoop();
      state.recording = false;
      state.recorder = null;
      const blob = new Blob(state.chunks, { type: "audio/webm" });
      dispatch({ type: "VAD_END", hadSpeech: state.vadHadSpeech, blob: blob.size ? blob : null });
    };
    state.recorder.start();
    state.recording = true;
    runVadLoop(stream);
    state.watchdog = window.setTimeout(() => dispatch({ type: "SPEECH_TIMEOUT" }), 15000);
  } catch (err) {
    dispatch({ type: "SPEECH_TIMEOUT" });
  }
}

function runVadLoop(stream) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  let ctx, analyser, data;
  try {
    ctx = new Ctx();
    const src = ctx.createMediaStreamSource(stream);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    src.connect(analyser);
    data = new Uint8Array(analyser.fftSize);
  } catch (e) { return; }
  let quiet = 0;
  const tick = () => {
    state.vadRaf = requestAnimationFrame(tick);
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
    const rms = Math.sqrt(sum / data.length);
    if (rms > 0.02) { state.vadHadSpeech = true; quiet = 0; }
    else if (state.vadHadSpeech) { quiet++; if (quiet > 45) { try { state.recorder.stop(); } catch (e) {} } }
  };
  requestAnimationFrame(tick);
  state.vad = ctx;
}

function stopVadLoop() {
  if (state.vadRaf) cancelAnimationFrame(state.vadRaf);
  state.vadRaf = 0;
  if (state.vad) { try { state.vad.close(); } catch (e) {} state.vad = null; }
}

function stopVadRecording() {
  if (state.recorder && state.recording) { try { state.recorder.stop(); } catch (e) {} }
}

async function transcribeAndChat(blob) {
  if (!blob) { dispatch({ type: "TRANSCRIBE_EMPTY" }); return; }
  state.watchdog = window.setTimeout(() => dispatch({ type: "PROCESSING_TIMEOUT" }), 45000);
  try {
    const fd = new FormData();
    fd.append("audio", blob, "companion-voice.webm");
    state.req = new AbortController();
    const r = await fetch("/api/editing/transcribe", { method: "POST", credentials: "include", body: fd, signal: state.req.signal });
    const data = await r.json().catch(() => ({}));
    clearWatchdog();
    if (!r.ok || data.status !== "success" || !data.text) { dispatch({ type: "TRANSCRIBE_ERR" }); return; }
    dispatch({ type: "TRANSCRIBE_OK", text: data.text });
  } catch (err) {
    clearWatchdog();
    dispatch({ type: "TRANSCRIBE_ERR" });
  }
}

// ── camera eyes: LOOK, then answer with the existing vision service ──────
function cameraEyesAvailable() {
  try {
    return !!(nativeCamera && typeof nativeCamera.hasNativeCamera === "function" &&
      nativeCamera.hasNativeCamera());
  } catch (e) { return false; }
}

// Keeps the floating window as small as the character so transparent canvas
// space cannot swallow taps meant for the app underneath. Android has no
// per-pixel overlay input mask, so the practical, pointer-safe solution is a
// compact window region (validated/clamped natively by OverlayRegionRules).
function requestCompactRegion() {
  // 270dp is the character stage; 46dp below it is the bottom micro-pill.
  try {
    if (window.vmNative && window.vmNative.overlay && typeof window.vmNative.overlay.setRegion === "function") {
      window.vmNative.overlay.setRegion(176, 316);
      return;
    }
    if (window.VMNative && window.VMNative.overlayRegion && typeof window.VMNative.overlayRegion.set === "function") {
      window.VMNative.overlayRegion.set(176, 316);
    }
  } catch (e) { /* older shells keep their current window size */ }
}

function cameraActive() {
  if (nativeCamera && typeof nativeCamera.cameraState === "function") {
    try { return !!nativeCamera.cameraState().active; } catch (e) { return false; }
  }
  return false;
}

function syncCameraButton() {
  if (!camBtn) return;
  const active = cameraActive();
  camBtn.classList.toggle("on", active);
  camBtn.setAttribute("aria-pressed", active ? "true" : "false");
  const label = active ? "Turn camera eyes off" : "Turn camera eyes on";
  camBtn.setAttribute("aria-label", label);
  camBtn.title = label;
}

// The pill's camera icon is a real on/off toggle and switches lens while the
// camera is live. The OS foreground notification is the privacy indicator; the
// overlay itself carries no top badge strip.
function toggleCameraEyes() {
  if (!cameraEyesAvailable()) return;
  try {
    if (cameraActive()) {
      if (typeof nativeCamera.cameraStop === "function") nativeCamera.cameraStop();
      else if (typeof nativeCamera.cameraSwitch === "function") nativeCamera.cameraSwitch();
    } else if (typeof nativeCamera.cameraStart === "function") {
      nativeCamera.cameraStart("back");
    }
  } catch (e) { /* keep the last known camera state */ }
  paintBadges();
}

function paintBadges() {
  // No DOM badge/strip: only the native foreground notification marks an
  // active camera/overlay. The root data attribute is kept for CSS diagnostics.
  if (root) root.dataset.camera = cameraActive() ? "on" : "off";
  syncCameraButton();
}

async function ensureCameraEyes() {
  if (!cameraEyesAvailable()) return false;
  try {
    let st = nativeCamera.cameraState ? nativeCamera.cameraState() : { active: false };
    if (!st.active) {
      if (!nativeCamera.cameraStart("back")) return false;
      paintBadges();
      // CameraX binds asynchronously; give it a short, bounded window to open.
      await new Promise((resolve) => setTimeout(resolve, 900));
      st = nativeCamera.cameraState ? nativeCamera.cameraState() : { active: false };
    }
    paintBadges();
    return !!st.active;
  } catch (e) { return false; }
}

// Returns the vision description, or null when the eyes are unavailable.
async function lookAndAnswer(question) {
  const ready = await ensureCameraEyes();
  if (!ready) return null;
  try {
    // "Look at this" -> point at what it sees; the engine has a real clip.
    try { engine.trigger("pointing"); } catch (e) { /* motion is a cue, not required */ }
    const dataUri = await nativeCamera.cameraCapture();
    const r = await fetch("/api/vision/analyze", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image: dataUri,
        question: question,
        context: "Frame captured by the ValleyMind companion's live camera eyes",
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.status !== "success" || !data.description) return null;
    return String(data.description).trim();
  } catch (e) {
    return null;
  }
}

// ── screen-context capture (optional native eyes) ─────────────────────────
// The AccessibilityService UI-tree is the low-bandwidth "what is on screen"
// signal; CameraX frames are the high-fidelity path. Both feed the SAME brain
// and the SAME /api/vision/analyze endpoint the rest of the app uses.
function screenBridge() {
  const a = window.vmCompanionAndroid;
  if (a && typeof a.screenContextJson === "function") return a;
  const n = window.VMNative;
  if (n && n.screen && typeof n.screen.context === "function") {
    return {
      screenContextSupported: function () { return n.screen.supported(); },
      screenContextJson: function () { return n.screen.context(); },
      screenContextSettings: function () { if (n.screen.settings) n.screen.settings(); },
    };
  }
  return null;
}

function readScreenContext() {
  const b = screenBridge();
  if (!b) return null;
  try {
    if (b.screenContextSupported && !b.screenContextSupported()) return null;
    const raw = b.screenContextJson();
    const data = (typeof raw === "string") ? JSON.parse(raw) : raw;
    if (!data || data.ok === false || !data.summary) return null;
    return data;
  } catch (e) { return null; }
}

// One-switch screen awareness: if the Accessibility service is off, open its
// settings ONCE (never on every boot), then cache the current UI-tree so the
// first spoken turn already has context. Nothing here reads screen pixels — it
// reuses the existing permission-gated bridge and the one /api/cloud/chat brain.
function enableScreenAwareness() {
  screenContextOn = true;
  try {
    if (window.VMNative && window.VMNative.screen &&
        typeof window.VMNative.screen.promptOnce === "function") {
      window.VMNative.screen.promptOnce();
    }
  } catch (e) { /* older shells keep their current permission state */ }
  screenContextCache = readScreenContext();
}

function attachScreenContext(message) {
  if (!screenContextOn || !screenContextCache || !message) return message;
  // Refresh opportunistically so continuous awareness follows the active screen.
  const fresh = readScreenContext();
  if (fresh) screenContextCache = fresh;
  const ctx = screenContextCache;
  const nodes = Array.isArray(ctx.nodes)
    ? ctx.nodes.slice(0, 24).map((n) => (n && (n.label || n.text || n.id)) || "").filter(Boolean).join(" | ")
    : "";
  const parts = ["[Active screen context] package=" + (ctx.package || "unknown")];
  parts.push(String(ctx.summary || "").slice(0, 1200));
  if (nodes) parts.push("Interactive elements: " + nodes);
  return parts.join("\n") + "\nQuestion: " + message;
}

async function screenGuidanceContext(question) {
  const ctx = readScreenContext();
  let visual = "";
  if (cameraEyesAvailable()) {
    visual = (await lookAndAnswer(question + " Focus on which control to tap next.")) || "";
  }
  if (!ctx && !visual) return null;
  try { engine.trigger("pointing"); } catch (e) { /* cue only */ }
  const parts = [];
  if (ctx) {
    parts.push("[Active screen context] package=" + (ctx.package || "unknown"));
    parts.push(String(ctx.summary || "").slice(0, 3000));
    if (Array.isArray(ctx.nodes)) {
      parts.push("Interactive elements: " + ctx.nodes.slice(0, 40)
        .map((n) => (n && (n.label || n.text || n.id)) || "").filter(Boolean).join(" | "));
    }
  }
  if (visual) parts.push("[Vision analysis] " + visual);
  parts.push("Question: " + question);
  parts.push("Answer with numbered, concrete steps naming the exact on-screen control to tap. Never invent controls that are not in the context.");
  return parts.join("\n");
}

// Strip the brain's silent wire tags; they control the avatar, never speech.
function takeAnimTags(reply) {
  const raw = String(reply || "");
  const anim = raw.match(/\[\s*ANIM\s*:\s*([a-zA-Z][\w-]*)\s*\]/i);
  const emo = raw.match(/\[\s*(?:EMO|EMOTION)\s*:\s*([a-zA-Z][\w-]*)\s*\]/i);
  const text = raw
    .replace(/\[\s*ANIM\s*:\s*[a-zA-Z][\w-]*\s*\]/gi, "")
    .replace(/\[\s*(?:EMO|EMOTION)\s*:\s*[a-zA-Z][\w-]*\s*\]/gi, "")
    .replace(/[ \t]{2,}/g, " ").trim();
  return { text: text, state: anim ? anim[1].toLowerCase() : "", emotion: emo ? emo[1].toLowerCase() : "" };
}

function applyBrainAnimation(anim) {
  if (!anim || !anim.state) return;
  const key = String(anim.state).toLowerCase().replace(/\s+/g, "_");
  const behaviour = ANIM_BEHAVIOUR[key] || "idle";
  state.animation = { state: key, behaviour: behaviour, emotion: anim.emotion || "" };
  state.behaviour = behaviour;
  try { engine.trigger(behaviour); } catch (e) { /* pose is a cue, not required */ }
  if (engine.face && typeof engine.face.setMood === "function") {
    const mood = ANIM_MOOD[anim.emotion] || ANIM_MOOD[key] || "neutral";
    try { engine.face.setMood(mood); } catch (e) { }
  }
}

// ── the ONE brain ─────────────────────────────────────────────────────────
async function sendChat(text) {
  clearWatchdog();
  state.animation = null; // each reply brings its own semantic animation
  state.watchdog = window.setTimeout(() => dispatch({ type: "PROCESSING_TIMEOUT" }), 60000);
  try {
    state.req = new AbortController();
    // "What am I wearing?" / "Look at this" must be answered from a REAL frame,
    // not guessed by the language model. The vision description then goes
    // through the same brain/speech path as any other reply.
    if (VISION_PROMPT.test(text) && cameraEyesAvailable()) {
      const description = await lookAndAnswer(text);
      if (description) {
        clearWatchdog();
        applyBrainAnimation({ state: "guide", emotion: "surprised" });
        dispatch({ type: "CHAT_OK", reply: description });
        return;
      }
    }
    // "Where do I tap here?" / "Help me navigate this app": on-demand screen
    // context (Accessibility UI-tree and/or a CameraX frame via the SAME
    // /api/vision/analyze endpoint) is injected into this turn only.
    // Continuous screen awareness rides on the same turn; navigation prompts
    // still use the richer step-by-step context path below.
    let message = NAV_PROMPT.test(text) ? text : attachScreenContext(text);
    if (NAV_PROMPT.test(text)) {
      try {
        const context = await screenGuidanceContext(text);
        if (context) message = context;
      } catch (e) { /* the plain question still reaches the brain */ }
    }
    const r = await fetch("/api/cloud/chat", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message, chat_id: state.chatId }),
      signal: state.req.signal,
    });
    if (r.status === 401 || r.status === 403) { clearWatchdog(); dispatch({ type: "CHAT_ERR" }); return; }
    const data = await r.json();
    clearWatchdog();
    if (!r.ok || data.status !== "success" || !data.reply) { dispatch({ type: "CHAT_ERR" }); return; }
    state.chatId = data.chat_id || state.chatId;
    // The brain's [ANIM: ...] / [EMO: ...] tags are silent control data: never
    // speak or display them. Structured `animation` from /api/cloud/chat wins.
    const tags = takeAnimTags(data.reply);
    const anim = data.animation && data.animation.state ? data.animation : tags;
    if (anim.state) applyBrainAnimation(anim);
    dispatch({ type: "CHAT_OK", reply: tags.text, presentation: data.presentation });
  } catch (err) {
    clearWatchdog();
    if (err && err.name === "AbortError") return; // a TAP superseded this turn
    dispatch({ type: "CHAT_ERR" });
  }
}

// ── speaking: native engine first, bounded server fallback ────────────────
// Android's WebView has no speechSynthesis. Native TTS V2 is preferred in the
// overlay because its onStart/onEnd are the engine's REAL utterance events and
// it works with no login/server key. VMCloudVoice stays as the bounded fallback:
// when the hosted voice is available it supplies true measured amplitude, and a
// failed server TTS falls back to the legacy native engine exactly once.
function canUseBrowserSynth() {
  return !!(window.speechSynthesis && typeof window.SpeechSynthesisUtterance === "function");
}

function isFailedTtsReason(reason) {
  const r = String(reason || "").toLowerCase();
  return r === "tts failed" || r === "tts blocked" || r === "tts unavailable" ||
    r === "tts timeout" || r === "interrupted";
}

function startServerSpeech(text, seq) {
  if (!voice || typeof voice.speak !== "function") return false;
  state.ttsBackend = "server";
  clearWatchdog();
  closeMouth();
  try {
    voice.speak(text, { prefs: { voice_preference: profile }, presentation: "neutral" });
  } catch (e) {
    handleServerSpeechEnd("tts unavailable", seq);
    return true;
  }
  armTtsWatchdog(seq, text);
  return true;
}

function handleServerSpeechEnd(reason, seq) {
  if (!speechCurrent(seq, "server")) return;
  clearWatchdog();
  if (!isFailedTtsReason(reason)) { finishSpeaking(true, seq); return; }
  if (!state.ttsFallbackUsed && nativeTts && typeof nativeTts.ttsSpeak === "function") {
    state.ttsFallbackUsed = true;
    startLegacyNativeSpeech(state.lastReply, seq);
    return;
  }
  finishSpeaking(false, seq);
}

function handleNativeSpeechFailure(seq, text) {
  if (!speechCurrent(seq)) return;
  clearWatchdog();
  if (state.ttsFallbackUsed) { finishSpeaking(false, seq); return; }
  state.ttsFallbackUsed = true;
  if (startServerSpeech(text, seq)) return;
  if (startLegacyNativeSpeech(text, seq)) return;
  finishSpeaking(false, seq);
}

function startNativeV2Speech(text, seq) {
  const voiceId = nativeVoiceRequest();
  state.ttsBackend = "nativeV2";
  clearWatchdog();
  closeMouth();
  try {
    const started = nativeTtsV2.ttsSpeakV2(text, voiceId, {
      // The mouth opens only on the engine's real onStart. Its level here is a
      // bounded speech-shaped envelope because Android TTS exposes no amplitude;
      // the server TTS fallback still uses the player's true amplitude tap.
      onStart: () => { if (speechCurrent(seq, "nativeV2")) setFaceSpeaking(true); },
      onLevel: (level) => {
        if (speechCurrent(seq, "nativeV2") && engine.face) {
          engine.face.setTalkLevel(Math.max(0, Math.min(1, Number(level) || 0)));
        }
      },
      onEnd: (ok) => {
        if (!speechCurrent(seq, "nativeV2")) return;
        if (ok === true || ok === "true") finishSpeaking(true, seq);
        else handleNativeSpeechFailure(seq, text);
      },
    });
    if (!started) {
      if (speechCurrent(seq, "nativeV2")) handleNativeSpeechFailure(seq, text);
      return true;
    }
  } catch (e) {
    if (speechCurrent(seq, "nativeV2")) handleNativeSpeechFailure(seq, text);
    return true;
  }
  armTtsWatchdog(seq, text);
  return true;
}

function startLegacyNativeSpeech(text, seq) {
  if (!nativeTts || typeof nativeTts.ttsSpeak !== "function") return false;
  state.ttsBackend = "native";
  clearWatchdog();
  // The legacy bridge has no start event, so the mouth stays shut instead of
  // pretending to know when audio is playing. The completion callback ends the
  // turn; V2 above carries the real start/end-driven mouth.
  closeMouth();
  try {
    const started = nativeTts.ttsSpeak(text, nativeVoiceRequest(), (ok) => {
      if (!speechCurrent(seq, "native")) return;
      finishSpeaking(!!ok, seq);
    });
    if (!started) {
      if (speechCurrent(seq, "native")) finishSpeaking(false, seq);
      return true;
    }
  } catch (e) {
    if (speechCurrent(seq, "native")) finishSpeaking(false, seq);
    return true;
  }
  armTtsWatchdog(seq, text);
  return true;
}

function speakNative(text, seq) {
  if (nativeTtsV2 && typeof nativeTtsV2.ttsSpeakV2 === "function") {
    return startNativeV2Speech(text, seq);
  }
  return startLegacyNativeSpeech(text, seq);
}

function speakReply(reply) {
  const text = String(reply || "").trim();
  const seq = beginSpeech(text);
  if (!text) { finishSpeaking(true, seq); return; }
  if (nativeTtsV2 && typeof nativeTtsV2.ttsSpeakV2 === "function") {
    speakNative(text, seq);
    return;
  }
  if (voice && typeof voice.speak === "function") {
    startServerSpeech(text, seq);
    return;
  }
  if (speakNative(text, seq)) return;
  finishSpeaking(false, seq);
}

// ── state on screen: body animation + the pill's centred glow ─────────────
// No text, no box and no ring behind the character: the body clip shows what
// she is doing, and root[data-state] lights the Talk/Mute Mic glow while live.
const FULL_BODY_BEHAVIOURS = new Set(["walking", "pointing", "guiding"]);

// Conversation zooms to the face/shoulders so lip-sync and blinks are legible in
// the compact overlay. Walking and pointing/guiding always ease back out so the
// hands and the target stay visible.
function updateFraming(s, behaviour) {
  if (!engine || typeof engine.setFraming !== "function") return;
  const conversational = s === "listening" || s === "speaking";
  const gesture = FULL_BODY_BEHAVIOURS.has(behaviour) ||
    (state.animation && FULL_BODY_BEHAVIOURS.has(state.animation.behaviour));
  try { engine.setFraming(conversational && !gesture ? "close" : "full"); }
  catch (e) { /* an older engine keeps its full-body camera */ }
}

function applyState(s) {
  if (root) root.dataset.state = s;
  state.speaking = s === "speaking";
  // "error" is still a running conversation: the pill stays reachable so the
  // user can end it without waiting for the retry.
  const active = s !== "idle";
  if (active !== state.session) {
    state.session = active;
    syncCallControls();
  }
  let behaviour = s === "greeting" ? "idle" // the greet effect owns the wave clip
    : s === "waking" ? "waving"
    : s === "listening" ? "listening"
    : s === "processing" ? "thinking"
    : s === "speaking" ? "talking"
    : "idle";
  // A semantic brain animation (e.g. guide/pointing, celebrate/happy) owns the
  // body while the reply is spoken; it must not be overwritten by the generic
  // "talking" fallback. The face channel carries the matching expression.
  if (s === "speaking" && state.animation && state.animation.behaviour) {
    behaviour = state.animation.behaviour;
    if (engine.face && typeof engine.face.setMood === "function") {
      const mood = ANIM_MOOD[state.animation.emotion] || ANIM_MOOD[state.animation.state] || "neutral";
      try { engine.face.setMood(mood); } catch (e) { /* keep the current face */ }
    }
  }
  if (s === "greeting" && engine.face && typeof engine.face.setMood === "function") {
    try { engine.face.setMood("happy"); } catch (e) { /* keep the current face */ }
  }
  updateFraming(s, behaviour);
  // Only re-trigger the body when the behaviour actually changes, so the
  // animation is not restarted on every state touch.
  if (behaviour !== state.behaviour) {
    state.behaviour = behaviour;
    try { engine.trigger(behaviour); } catch (e) { /* the character keeps its pose */ }
  }
  if (s === "error" && !state.errorTimer) {
    // A hard failure retries by itself a moment later; the SM bounds how many
    // times that can happen.
    state.errorTimer = window.setTimeout(() => {
      state.errorTimer = 0;
      dispatch({ type: "RETRY" });
    }, 1400);
  } else if (s !== "error" && state.errorTimer) {
    // The user recovered first (a tap): the pending automatic retry is stale.
    clearTimeout(state.errorTimer);
    state.errorTimer = 0;
  }
}

function cue(kind) {
  if (!pill) return;
  pill.classList.remove("cue");
  void pill.offsetWidth;
  pill.classList.add("cue");
}

function clearWatchdog() {
  if (state.watchdog) { clearTimeout(state.watchdog); state.watchdog = 0; }
}

// Arms (or re-arms) the safety timeout for the current listening turn. Speech
// activity re-arms it, so it measures silence rather than total speaking time.
function armSpeechWatchdog(ms) {
  clearWatchdog();
  state.watchdog = window.setTimeout(() => dispatch({ type: "SPEECH_TIMEOUT" }), ms);
}

function wireControls() {
  // Tapping the character starts the hands-free conversation (and, when the
  // browser is holding a transcript, submits it).
  canvas.addEventListener("click", () => {
    if (voice && voice.unlock) { try { voice.unlock(); } catch (e) {} }
    const text = (voice && voice.supported && voice.supported() && voice.isListening && voice.isListening())
      ? (voice.takeTranscript ? voice.takeTranscript() : "")
      : "";
    dispatch({ type: "TAP", text: String(text || "").trim() });
  });
  // One icon button covers Talk / Mute Mic:
  //   idle     -> start the hands-free conversation
  //   active   -> mute + end it immediately
  //   muted    -> unmute and start a fresh conversation
  if (talkBtn) {
    talkBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (voice && voice.unlock) { try { voice.unlock(); } catch (err) {} }
      if (state.muted) {
        state.muted = false;
        state.keepMuted = false;
        syncCallControls();
        dispatch({ type: "TAP", text: "" });
        return;
      }
      if (state.session) {
        stopListening();
        stopSpeaking();
        state.muted = true;
        state.keepMuted = true;
        syncCallControls();
        dispatch({ type: "STOP" });
        return;
      }
      dispatch({ type: "TAP", text: "" });
    });
  }
  if (hideBtn) {
    hideBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      dispatch({ type: "STOP" });
      if (android) { try { android.setVisible(false); return; } catch (err) { /* fall through */ } }
      window.close();
    });
  }
  // A profile change made in the main app Settings (same origin, same storage)
  // takes effect here without a reload.
  window.addEventListener("storage", (e) => {
    if (e.key === VOICE_PROFILE_KEY) profile = readProfile();
  });
}

function wireCameraControls() {
  // Native pushes camera start/stop/lens-switch state here (reserved callback).
  window.__vmCameraState = function () { paintBadges(); };
  if (camBtn) {
    camBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleCameraEyes();
    });
  }
  if (android && typeof android.onCompanionVisibility === "function") {
    android.onCompanionVisibility(() => paintBadges());
  }
}

async function boot() {
  wireControls();
  wireVoice();
  wireNativeAsr();
  wireCameraControls();
  paintBadges();
  requestCompactRegion();
  // Keep autonomous shuffling off across boot as well as after it: some engine
  // versions restart their idle scheduler inside boot().
  engine.setAuto(false);
  try {
    await engine.boot();
  } catch (err) {
    return; // the companion cannot load without a character; nothing to show
  }
  // Belt-and-braces: boot starts the render loop, so keep autonomous shuffling
  // off even if a future engine version re-enables it by default on boot.
  engine.setAuto(false);
  applyState("idle");
  document.addEventListener("visibilitychange", () => engine.setRendering(!document.hidden));
  // This page is created exactly when the outside-app switch turns on, so its
  // boot is the one-switch edge: capabilities + greeting + zero-tap listening.
  startAutoWake();
}

boot();
