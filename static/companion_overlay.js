/*
 * companion_overlay.js — renderer for the Android floating-companion surface.
 *
 * This is the SAME companion as everywhere else; only the chrome differs — and
 * now there is almost none: the character fills a fully transparent window and
 * you talk to it by tapping a small mic attached to its body. No panel, no
 * text box, no status pill. Settings and secondary controls live in the main
 * app (static/settings.js).
 *
 * Reuse map (nothing is reimplemented):
 *   body animation -> /vrm-preview/assets/companion.js  (the ONE engine)
 *   face / mouth   -> engine.face.setTalkLevel()        (real TTS amplitude)
 *   AI backend     -> POST /api/cloud/chat              (same brain as the site)
 *   voice          -> window.VMCloudVoice               (/api/tts + microphone)
 *   STT fallback   -> POST /api/editing/transcribe      (Groq Whisper)
 *   turn logic     -> window.VMTurnSM                   (the turn state machine)
 *
 * The overlay owns no conversation state of its own: it talks to the same
 * backend endpoints with the same session cookie as the ValleyMind app, so
 * there is exactly ONE brain and ONE conversation.
 */

import { Companion } from "/vrm-preview/assets/companion.js";

const $ = (id) => document.getElementById(id);
const canvas = $("stage");
const micBtn = $("btnMic");

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

// The turn state machine is pure logic; the page supplies every side effect.
const sm = (window.VMTurnSM && window.VMTurnSM.create)
  ? window.VMTurnSM.create({ dedupeWindowMs: 15000 })
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
  raf: 0,              // mic-anchor frame loop
  watchdog: 0,
};

// ── voice module hooks (reused, not reimplemented) ────────────────────────
function wireVoice() {
  if (!voice) return;
  voice.setHooks({
    onListeningChange: () => { /* the SM drives the mic indicator, not the module */ },
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

// ── side-effect handlers (the SM returns these; we execute them) ──────────
function execute(effects) {
  for (const eff of effects) {
    switch (eff.type) {
      case "start-listen": startListening(); break;
      case "stop-listen": stopListening(); break;
      case "transcribe": transcribeAndChat(eff.blob); break;
      case "chat": sendChat(eff.text); break;
      case "speak": speakReply(eff.reply); break;
      case "stop-speak": stopSpeaking(); break;
      case "abort": abortInFlight(); break;
      case "feedback": cue(eff.message); break;
    }
  }
}

function dispatch(event) {
  if (!sm) return;
  const res = sm.send(event);
  setMicState(res.state);
  execute(res.effects);
}

// ── mic interaction ───────────────────────────────────────────────────────
function tapMic() {
  if (!sm) return;
  const text = (voice && voice.supported && voice.supported() && voice.isListening)
    ? (voice.takeTranscript ? voice.takeTranscript() : "")
    : "";
  // If the speech recogniser is holding a transcript, stop-and-send wins over
  // a plain "start listening" tap.
  if (voice && voice.supported && voice.supported() && voice.isListening && voice.isListening() && text && text.trim().length >= 2) {
    stopListening();
    dispatch({ type: "TAP", text: text.trim() });
    return;
  }
  dispatch({ type: "TAP" });
}

function startListening() {
  clearWatchdog();
  if (voice && voice.supported && voice.supported()) {
    try { voice.startListening(); } catch (e) { dispatch({ type: "SPEECH_TIMEOUT" }); return; }
    // No-speech watchdog: an open mic that hears nothing must not hang the turn.
    state.watchdog = window.setTimeout(() => dispatch({ type: "SPEECH_TIMEOUT" }), 12000);
    return;
  }
  startVadRecording(); // no in-page recognition -> record + server transcription
}

function stopListening() {
  clearWatchdog();
  if (voice && voice.stopListening) { try { voice.stopListening(); } catch (e) {} }
  stopVadRecording();
}

function abortInFlight() {
  if (state.req) { try { state.req.abort(); } catch (e) {} state.req = null; }
}

function stopSpeaking() {
  if (nativeTts) { try { nativeTts.stop(); } catch (e) {} }
  if (voice && voice.stop) { try { voice.stop(); } catch (e) {} }
  state.speaking = false;
  if (engine.face) engine.face.setTalkLevel(0);
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
    // Keep the companion's own voice profile authoritative for the reply.
    if (data.presentation && !readProfile()) data.presentation = data.presentation;
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

// ── mic indicator + body anchor ───────────────────────────────────────────
function setMicState(s) {
  const map = { listening: "listening", processing: "processing", speaking: "speaking", idle: "" };
  micBtn.dataset.s = map[s] || "";
  state.speaking = s === "speaking";
  engine.trigger(s === "listening" ? "listening" : s === "processing" ? "thinking" : s === "speaking" ? "talking" : "idle");
}

function cue(kind) {
  micBtn.classList.remove("cue");
  void micBtn.offsetWidth;
  micBtn.classList.add("cue");
}

// Anchors the mic to the character's chest every frame using the engine's own
// world->screen projection, so the control is genuinely attached to the body.
function anchorMic() {
  const tick = () => {
    state.raf = requestAnimationFrame(tick);
    const vrm = engine.vrm;
    if (!vrm || !vrm.humanoid || typeof engine.projectWorld !== "function") return;
    const chest = vrm.humanoid.getRawBoneNode("chest") || vrm.humanoid.getRawBoneNode("spine");
    if (!chest) return;
    chest.updateWorldMatrix(true, false);
    const p = chest.matrixWorld.elements;
    const [px, py] = engine.projectWorld(p[12], p[13], p[14]);
    const rect = canvas.getBoundingClientRect();
    micBtn.style.left = (px / canvas.width) * rect.width + "px";
    micBtn.style.top = (py / canvas.height) * rect.height + 26 + "px";
    micBtn.style.margin = "0";
    micBtn.style.transform = "translate(-50%, -50%)";
  };
  tick();
}

function clearWatchdog() {
  if (state.watchdog) { clearTimeout(state.watchdog); state.watchdog = 0; }
}

function wireControls() {
  micBtn.addEventListener("click", tapMic);
  $("btnHide").addEventListener("click", () => {
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
  try {
    await engine.boot();
  } catch (err) {
    return; // the companion cannot load without a character; nothing to show
  }
  setMicState("idle");
  anchorMic();
  document.addEventListener("visibilitychange", () => engine.setRendering(!document.hidden));
}

boot();
