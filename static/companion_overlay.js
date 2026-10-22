/*
 * companion_overlay.js — renderer for the Android floating-companion surface.
 *
 * This is the SAME companion as everywhere else; only the chrome differs — and
 * there is almost none: the character fills a fully transparent window, you
 * start a conversation by tapping the character, and the companion then talks
 * on its own (listen -> think -> speak -> listen) until you stop it. No
 * microphone icon, no panel, no text box, no status pill. Settings and
 * secondary controls live in the main app (static/settings.js).
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
const halo = $("vmHalo");
const stopBtn = $("btnStop");

const engine = new Companion(canvas, () => {}, { transparent: true });
const voice = window.VMCloudVoice || null;
const android = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.setVisible === "function")
  ? window.vmCompanionAndroid
  : null;

// Native TTS fallback (Android WebView has no speechSynthesis). Used only when
// the site's own voice module cannot actually produce sound on this platform.
const nativeTts = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.ttsSpeak === "function")
  ? window.vmCompanionAndroid
  : null;

// The platform speech recogniser (same bridge, same process).
const nativeAsr = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.asrStart === "function")
  ? window.vmCompanionAndroid
  : null;

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
const DEFAULT_PROFILE = "companion";

function readProfile() {
  try {
    const v = String(localStorage.getItem(VOICE_PROFILE_KEY) || "").toLowerCase();
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

let profile = readProfile();

// ── transient state ───────────────────────────────────────────────────────
const state = {
  chatId: "",
  recorder: null,
  chunks: [],
  recording: false,
  vad: null,           // MediaRecorder VAD (getUserMedia) fallback
  vadRaf: 0,
  vadHadSpeech: false,
  raf: 0,              // halo-anchor frame loop
  watchdog: 0,
  wakeTimer: 0,        // wakes the turn if the wake animation never reports back
  asrTimer: 0,         // delayed start of the platform recogniser
  errorTimer: 0,       // bounded automatic retry after a hard failure
  behaviour: "",       // the body animation currently playing
  speaking: false,
  session: false,      // a hands-free conversation is running
};

// ── voice module hooks (reused, not reimplemented) ────────────────────────
function wireVoice() {
  if (!voice) return;
  voice.setHooks({
    onListeningChange: () => { /* the SM drives the state, not the module */ },
    onResult: (text, final) => { if (final) dispatch({ type: "SPEECH_FINAL", text }); },
    onSpeakStart: () => { /* SM already put us in SPEAKING */ },
    onSpeakEnd: (reason) => { clearWatchdog(); dispatch({ type: reason === "tts failed" || reason === "tts blocked" || reason === "tts unavailable" ? "SPEAK_ERR" : "SPEAK_END" }); },
    onError: (err) => { dispatch({ type: "SPEAK_ERR", err }); },
  });
  if (typeof voice.setLevelListener === "function") {
    voice.setLevelListener((level) => {
      if (engine.face && state.speaking) engine.face.setTalkLevel(level);
    });
  }
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
      dispatch({ type: "SPEECH_FINAL", text: String(ev.text || "") });
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
function execute(effects) {
  for (const eff of effects) {
    switch (eff.type) {
      case "wake": triggerWake(); break;
      case "start-listen": startListening(); break;
      case "stop-listen": stopListening(); break;
      case "transcribe": transcribeAndChat(eff.blob); break;
      case "chat": sendChat(eff.text); break;
      case "speak": speakReply(eff.reply); break;
      case "stop-speak": stopSpeaking(); break;
      case "abort": abortInFlight(); break;
      case "feedback": cue(eff.message); break;
      case "session-end": endSession(); break;
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
  if (state.asrTimer) { clearTimeout(state.asrTimer); state.asrTimer = 0; }
  if (nativeAsr && nativeAsr.asrStop) { try { nativeAsr.asrStop(); } catch (e) {} }
  if (voice && voice.stopListening) { try { voice.stopListening(); } catch (e) {} }
  stopVadRecording();
}

function abortInFlight() {
  if (state.req) { try { state.req.abort(); } catch (e) {} state.req = null; }
}

function stopSpeaking() {
  // The bridge exposes the native engine as ttsStop() (ttsSpeak/ttsStop).
  if (nativeTts && nativeTts.ttsStop) { try { nativeTts.ttsStop(); } catch (e) {} }
  if (voice && voice.stop) { try { voice.stop(); } catch (e) {} }
  state.speaking = false;
  if (engine.face) engine.face.setTalkLevel(0);
}

// ── the end of a hands-free conversation ──────────────────────────────────
function endSession() {
  if (state.errorTimer) { clearTimeout(state.errorTimer); state.errorTimer = 0; }
  if (state.wakeTimer) { clearTimeout(state.wakeTimer); state.wakeTimer = 0; }
  if (state.asrTimer) { clearTimeout(state.asrTimer); state.asrTimer = 0; }
  if (nativeAsr && nativeAsr.asrCancel) { try { nativeAsr.asrCancel(); } catch (e) {} }
  state.session = false;
  stopBtn.classList.remove("on");
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

// ── the ONE brain ─────────────────────────────────────────────────────────
async function sendChat(text) {
  clearWatchdog();
  state.watchdog = window.setTimeout(() => dispatch({ type: "PROCESSING_TIMEOUT" }), 60000);
  try {
    state.req = new AbortController();
    const r = await fetch("/api/cloud/chat", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, chat_id: state.chatId }),
      signal: state.req.signal,
    });
    if (r.status === 401 || r.status === 403) { clearWatchdog(); dispatch({ type: "CHAT_ERR" }); return; }
    const data = await r.json();
    clearWatchdog();
    if (!r.ok || data.status !== "success" || !data.reply) { dispatch({ type: "CHAT_ERR" }); return; }
    state.chatId = data.chat_id || state.chatId;
    dispatch({ type: "CHAT_OK", reply: data.reply, presentation: data.presentation });
  } catch (err) {
    clearWatchdog();
    if (err && err.name === "AbortError") return; // a TAP superseded this turn
    dispatch({ type: "CHAT_ERR" });
  }
}

// ── speaking: free TTS, with a native fallback ────────────────────────────
// Order: the site's VMCloudVoice (server TTS -> same-origin MP3 -> REAL
// amplitude lip-sync, or the browser synth on desktop). Android's WebView has
// NO speechSynthesis, so when the server voice is unavailable the page must use
// the device's own TTS engine through the bridge — otherwise the reply would be
// silently dropped. The mouth in that fallback is event-driven (open at start,
// close at end), which we disclose rather than fake as amplitude sync.
function canUseBrowserSynth() {
  return !!(window.speechSynthesis && typeof window.SpeechSynthesisUtterance === "function");
}

function speakReply(reply) {
  const text = String(reply || "").trim();
  if (!text) { dispatch({ type: "SPEAK_END" }); return; }
  // In the overlay WebView there is no browser speech engine, and the server
  // voice may be unavailable (no login / no key). Prefer the native engine when
  // the module cannot actually produce sound here.
  const preferNative = nativeTts && !canUseBrowserSynth();
  let handled = false;
  if (!preferNative && voice && voice.speak) {
    try {
      voice.speak(text, { prefs: { voice_preference: profile }, presentation: "neutral" });
      handled = true;
    } catch (e) { handled = false; }
  }
  if (!handled && nativeTts) { speakNative(text); return; }
  if (!handled) { dispatch({ type: "SPEAK_ERR" }); return; }
  // Playback watchdog: a silent TTS failure must still free the turn.
  state.watchdog = window.setTimeout(() => dispatch({ type: "SPEAK_TIMEOUT" }), Math.max(8000, text.length * 90));
}

function speakNative(text) {
  state.speaking = true;
  if (engine.face) engine.face.setSpeaking(true);
  const finish = () => {
    clearWatchdog();
    if (state.speaking) {
      state.speaking = false;
      if (engine.face) { engine.face.setSpeaking(false); engine.face.setTalkLevel(0); }
      dispatch({ type: "SPEAK_END" });
    }
  };
  try {
    nativeTts.ttsSpeak(text, PROFILE_NATIVE_VOICE[profile] || null, finish);
  } catch (e) {
    dispatch({ type: "SPEAK_ERR" });
    return;
  }
  state.watchdog = window.setTimeout(() => dispatch({ type: "SPEAK_TIMEOUT" }), Math.max(8000, text.length * 90));
}

// ── state on screen: a colour halo and the body animation ─────────────────
// No text, no box: the character shows what it is doing.
function applyState(s) {
  if (root) root.dataset.state = s;
  state.speaking = s === "speaking";
  // "error" is still a running conversation: the stop control must stay
  // reachable so the user can end it without waiting for the retry.
  const active = s !== "idle";
  if (active !== state.session) {
    state.session = active;
    stopBtn.classList.toggle("on", active);
  }
  const behaviour = s === "waking" ? "waving"
    : s === "listening" ? "listening"
    : s === "processing" ? "thinking"
    : s === "speaking" ? "talking"
    : "idle";
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
  if (!halo) return;
  halo.classList.remove("cue");
  void halo.offsetWidth;
  halo.classList.add("cue");
}

// Anchors the halo to the character's chest every frame using the engine's own
// world->screen projection, so the state indicator is genuinely attached to
// the body.
function anchorHalo() {
  const tick = () => {
    state.raf = requestAnimationFrame(tick);
    if (!halo) return;
    const vrm = engine.vrm;
    if (!vrm || !vrm.humanoid || typeof engine.projectWorld !== "function") return;
    const chest = vrm.humanoid.getRawBoneNode("chest") || vrm.humanoid.getRawBoneNode("spine");
    if (!chest) return;
    chest.updateWorldMatrix(true, false);
    const p = chest.matrixWorld.elements;
    const [px, py] = engine.projectWorld(p[12], p[13], p[14]);
    const rect = canvas.getBoundingClientRect();
    halo.style.left = (px / canvas.width) * rect.width + "px";
    halo.style.top = (py / canvas.height) * rect.height + "px";
    halo.style.margin = "0";
    halo.style.transform = "translate(-50%, -50%)";
  };
  tick();
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
  // The only way to end the conversation.
  stopBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    dispatch({ type: "STOP" });
  });
  $("btnHide").addEventListener("click", () => {
    dispatch({ type: "STOP" });
    if (android) { try { android.setVisible(false); return; } catch (e) { /* fall through */ } }
    window.close();
  });
  // A profile change made in the main app Settings (same origin, same storage)
  // takes effect here without a reload.
  window.addEventListener("storage", (e) => {
    if (e.key === VOICE_PROFILE_KEY) profile = readProfile();
  });
}

async function boot() {
  wireControls();
  wireVoice();
  wireNativeAsr();
  try {
    await engine.boot();
  } catch (err) {
    return; // the companion cannot load without a character; nothing to show
  }
  applyState("idle");
  anchorHalo();
  document.addEventListener("visibilitychange", () => engine.setRendering(!document.hidden));
}

boot();
