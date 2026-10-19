/*
 * companion_overlay.js — renderer for the Android floating-companion surface.
 *
 * This is the SAME companion as everywhere else; only the chrome differs.
 * Reuse map (nothing is reimplemented):
 *   body animation -> /vrm-preview/assets/companion.js  (the ONE engine)
 *   face / mouth   -> /vrm-preview/assets/face.js       (via the engine)
 *   AI backend     -> POST /api/cloud/chat              (same brain as the site)
 *   voice          -> window.VMCloudVoice               (/api/tts + microphone)
 *   STT fallback   -> POST /api/editing/transcribe      (Groq Whisper)
 *
 * The overlay never owns conversation state of its own: it talks to the same
 * backend endpoints with the same session cookie as the ValleyMind app, so
 * there is exactly ONE brain and ONE conversation.
 */

import { Companion } from "/vrm-preview/assets/companion.js";

const $ = (id) => document.getElementById(id);
const canvas = $("stage");
const statusEl = $("status");
const logBox = $("log");
const panel = $("panel");
const input = $("input");
const micBtn = $("btnMic");

const engine = new Companion(canvas, logLine, { transparent: true });
const voice = window.VMCloudVoice || null;
// Injected by the Android WebView (also present inside the overlay WebView), so
// the on-screen "Hide" button can put the companion away through the same bridge
// the Settings toggle uses. Absent in a plain browser.
const android = (window.vmCompanionAndroid && typeof window.vmCompanionAndroid.setVisible === "function")
  ? window.vmCompanionAndroid
  : null;

const state = {
  chatId: "",
  busy: false,
  speaking: false,
  listening: false,
  recording: false,
  recorder: null,
  chunks: [],
};

const STATE_LABEL = {
  boot: "Starting…",
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  error: "Error",
};

function logLine(text, bad) {
  if (!text) return;
  const div = document.createElement("div");
  div.className = "bubble" + (bad ? " error" : " assistant");
  div.textContent = String(text).slice(0, 4000);
  logBox.appendChild(div);
  while (logBox.childElementCount > 120) logBox.firstElementChild.remove();
  logBox.scrollTop = logBox.scrollHeight;
}

function bubble(role, text) {
  const div = document.createElement("div");
  div.className = "bubble " + role;
  div.textContent = String(text || "");
  logBox.appendChild(div);
  while (logBox.childElementCount > 120) logBox.firstElementChild.remove();
  logBox.scrollTop = logBox.scrollHeight;
  panel.hidden = false;
}

function setState(name) {
  const s = STATE_LABEL[name] ? name : "idle";
  if (statusEl.dataset.s === s) return;
  statusEl.dataset.s = s;
  statusEl.textContent = STATE_LABEL[s];
  if (s === "listening") engine.trigger("listening");
  else if (s === "thinking") engine.trigger("thinking");
  else if (s === "speaking") engine.trigger("talking");
}

function wireVoice() {
  if (!voice) {
    logLine("Voice module unavailable — typed chat still works.", true);
    return;
  }
  if (!voice.supported()) {
    micBtn.title = "Record and transcribe (server STT)";
    logLine("Browser speech recognition unavailable — using server transcription.", true);
  } else {
    voice.setHooks({
      onListeningChange: (on) => {
        state.listening = !!on;
        micBtn.classList.toggle("on", !!on);
        setState(on ? "listening" : state.busy ? "thinking" : "idle");
      },
      onResult: (text, final) => {
        if (final && text && text.trim().length >= 2) sendMessage(text.trim());
      },
      onSpeakStart: () => { state.speaking = true; setState("speaking"); },
      onSpeakEnd: () => {
        state.speaking = false;
        if (engine.face) engine.face.setTalkLevel(0);
        setState(state.busy ? "thinking" : "idle");
      },
      onError: (err) => { setState("error"); logLine("Voice error: " + err, true); },
    });
    if (typeof voice.setLevelListener === "function") {
      voice.setLevelListener((level) => {
        if (engine.face && state.speaking) engine.face.setTalkLevel(level);
      });
    }
  }
}

async function sendMessage(text) {
  text = String(text || "").trim();
  if (!text) return;
  if (state.busy) { logLine("Still working on the previous message…", true); return; }
  state.busy = true;
  bubble("user", text);
  setState("thinking");
  try {
    const r = await fetch("/api/cloud/chat", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, chat_id: state.chatId }),
    });
    if (r.status === 401 || r.status === 403) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.message || "Please sign in to ValleyMind first.");
    }
    const data = await r.json();
    if (!r.ok || data.status !== "success" || !data.reply) {
      throw new Error(data.message || `Backend error (HTTP ${r.status})`);
    }
    state.chatId = data.chat_id || state.chatId;
    bubble("assistant", data.reply);
    state.busy = false;
    if (voice) {
      try {
        voice.speak(data.reply, { prefs: { voice_preference: data.presentation || "" }, presentation: data.presentation || "neutral" });
      } catch (e) {
        logLine("TTS failed: " + (e && e.message ? e.message : e), true);
        setState("idle");
      }
    } else {
      setState("idle");
    }
  } catch (err) {
    state.busy = false;
    setState("error");
    bubble("error", err && err.message ? err.message : String(err));
  }
}

function toggleMic() {
  if (state.speaking) {
    if (voice) { try { voice.stop(); } catch (e) { /* ignore */ } }
    state.speaking = false;
  }
  if (voice && voice.supported()) {
    if (voice.isListening()) {
      const spoken = voice.takeTranscript();
      voice.stopListening();
      if (spoken && spoken.trim().length >= 2) sendMessage(spoken.trim());
    } else {
      try { voice.startListening(); }
      catch (e) { setState("error"); logLine("Could not start the microphone: " + (e && e.message ? e.message : e), true); }
    }
    return;
  }
  toggleServerRecording();
}

async function toggleServerRecording() {
  if (state.recording && state.recorder) {
    try { state.recorder.stop(); } catch (e) { /* ignore */ }
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.chunks = [];
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (e) => { if (e.data && e.data.size) state.chunks.push(e.data); };
    state.recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      state.recording = false;
      state.recorder = null;
      micBtn.classList.remove("on");
      const blob = new Blob(state.chunks, { type: "audio/webm" });
      if (blob.size) transcribeAndSend(blob);
      else setState("idle");
    };
    state.recorder.start();
    state.recording = true;
    micBtn.classList.add("on");
    setState("listening");
  } catch (err) {
    setState("error");
    logLine("Microphone permission denied or unavailable: " + (err && err.message ? err.message : err), true);
  }
}

async function transcribeAndSend(blob) {
  setState("thinking");
  try {
    const fd = new FormData();
    fd.append("audio", blob, "companion-voice.webm");
    const r = await fetch("/api/editing/transcribe", { method: "POST", credentials: "include", body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.status !== "success" || !data.text) {
      throw new Error(data.message || `Transcription unavailable (HTTP ${r.status})`);
    }
    sendMessage(data.text);
  } catch (err) {
    setState("error");
    logLine("Transcription failed: " + (err && err.message ? err.message : err), true);
  }
}

function wireControls() {
  $("btnChat").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) input.focus();
  });
  $("btnSend").addEventListener("click", () => {
    const t = input.value;
    input.value = "";
    sendMessage(t);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const t = input.value; input.value = ""; sendMessage(t); }
  });
  micBtn.addEventListener("click", toggleMic);
  // Put the companion away through the SAME bridge the Settings toggle uses, so
  // the switch, the service and the overlay can never disagree.
  $("btnHide").addEventListener("click", () => {
    if (android) { try { android.setVisible(false); return; } catch (e) { /* fall through */ } }
    window.close();
  });
}

async function checkAuth() {
  try {
    const r = await fetch("/auth/status", { credentials: "include" });
    const j = await r.json();
    return !!(j && j.authenticated);
  } catch (e) { return false; }
}

async function boot() {
  wireControls();
  wireVoice();
  try {
    await engine.boot();
  } catch (err) {
    setState("error");
    logLine("The character failed to load: " + (err && err.message ? err.message : err), true);
    return;
  }
  setState("idle");
  if (!(await checkAuth())) {
    // The character shows regardless; signing in is only needed for the AI.
    logLine("Sign in to ValleyMind to talk. The character is just showing for now.", true);
  }
  document.addEventListener("visibilitychange", () => engine.setRendering(!document.hidden));
}

boot();
