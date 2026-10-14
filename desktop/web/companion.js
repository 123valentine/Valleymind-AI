/**
 * companion.js -- renderer for the Windows desktop companion surface.
 *
 * Reuse map (nothing is reimplemented):
 *   body animation -> /vrm-preview/assets/companion.js  (the ONE engine)
 *   face / mouth   -> /vrm-preview/assets/face.js       (via the engine)
 *   AI backend     -> POST /api/cloud/chat              (same brain as the site)
 *   voice          -> window.VMCloudVoice               (/api/tts + microphone)
 *   desktop fallback STT -> POST /api/editing/transcribe (Groq Whisper)
 *
 * This file owns ONLY the desktop chrome: state pill, transcript, buttons and
 * the auth handshake with the Electron shell. It never touches the mixer.
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
const bridge = window.vmDesktop || null;

const state = {
  chatId: "",
  busy: false,
  speaking: false,
  listening: false,
  paused: false,
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

// ── small helpers ───────────────────────────────────────────────────────────
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
  // Reflect the state on the body engine where it maps to a real behaviour.
  if (s === "listening") engine.trigger("listening");
  else if (s === "thinking") engine.trigger("thinking");
  else if (s === "speaking") engine.trigger("talking");
}

// ── voice pipeline (reused) ─────────────────────────────────────────────────
function wireVoice() {
  if (!voice) {
    logLine("Voice module unavailable — typed chat still works.", true);
    return;
  }
  if (!voice.supported()) {
    // Electron/Chromium desktop builds usually lack the Web Speech API. We keep
    // the button working by falling back to the server's Groq Whisper endpoint.
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
        // Interim results update nothing visible; only commit on end-of-speech.
        if (final && text && text.trim().length >= 2) {
          if (input && !input.value.trim()) {
            /* keep the composer free for typing */
          }
          sendMessage(text.trim());
        }
      },
      onSpeakStart: () => {
        state.speaking = true;
        setState("speaking");
      },
      onSpeakEnd: () => {
        state.speaking = false;
        if (engine.face) engine.face.setTalkLevel(0);
        setState(state.busy ? "thinking" : "idle");
      },
      onError: (err) => {
        setState("error");
        logLine("Voice error: " + err, true);
      },
    });
    // Real TTS amplitude -> the mouth. The engine's face channel is the ONLY
    // thing driven here; the body keeps its own mixer untouched.
    if (typeof voice.setLevelListener === "function") {
      voice.setLevelListener((level) => {
        if (engine.face && state.speaking) engine.face.setTalkLevel(level);
      });
    }
  }
}

// ── typing→AI→speech round trip (reuses /api/cloud/chat and /api/tts) ────────
async function sendMessage(text) {
  text = String(text || "").trim();
  if (!text) return;
  if (state.busy) {
    logLine("Still working on the previous message…", true);
    return;
  }
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
      // The body is always visible; signing in is only needed for the AI.
      requestLogin();
      throw new Error(body.message || "Please sign in to talk to ValleyMind.");
    }
    const data = await r.json();
    if (!r.ok || data.status !== "success" || !data.reply) {
      throw new Error(data.message || `Backend error (HTTP ${r.status})`);
    }
    state.chatId = data.chat_id || state.chatId;
    bubble("assistant", data.reply);
    state.busy = false;
    if (voice) {
      const prefs = { voice_preference: data.presentation || "" };
      try {
        voice.speak(data.reply, { prefs: prefs, presentation: data.presentation || "neutral" });
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
    // A clear, honest error — never a fabricated reply.
  }
}

// ── microphone ──────────────────────────────────────────────────────────────
function toggleMic() {
  if (state.speaking) {
    if (voice) {
      try {
        voice.stop();
      } catch (e) {
        /* ignore */
      }
    }
    state.speaking = false;
  }
  if (voice && voice.supported()) {
    if (voice.isListening()) {
      const spoken = voice.takeTranscript();
      voice.stopListening();
      if (spoken && spoken.trim().length >= 2) sendMessage(spoken.trim());
    } else {
      try {
        voice.startListening();
      } catch (e) {
        setState("error");
        logLine("Could not start the microphone: " + (e && e.message ? e.message : e), true);
      }
    }
    return;
  }
  toggleServerRecording();
}

async function toggleServerRecording() {
  if (state.recording && state.recorder) {
    try {
      state.recorder.stop();
    } catch (e) {
      /* ignore */
    }
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.chunks = [];
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (e) => {
      if (e.data && e.data.size) state.chunks.push(e.data);
    };
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

// ── controls ────────────────────────────────────────────────────────────────
function wireControls() {
  $("btnSend").addEventListener("click", () => {
    const t = input.value;
    input.value = "";
    sendMessage(t);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const t = input.value;
      input.value = "";
      sendMessage(t);
    }
  });
  micBtn.addEventListener("click", toggleMic);

  $("btnChat").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) input.focus();
  });

  const pauseBtn = $("btnPause");
  pauseBtn.addEventListener("click", () => setPaused(!state.paused));
  function setPaused(on) {
    state.paused = !!on;
    engine.setAuto(!state.paused);
    if (state.paused) engine.trigger("idle");
    pauseBtn.textContent = state.paused ? "Resume" : "Pause";
    pauseBtn.classList.toggle("on", state.paused);
  }

  $("btnTop").addEventListener("click", () => {
    if (bridge && bridge.toggleAlwaysOnTop) bridge.toggleAlwaysOnTop();
    else logLine("Always-on-top is only available in the desktop app.", true);
  });
  $("btnReset").addEventListener("click", () => bridge && bridge.resetPosition && bridge.resetPosition());
  $("btnQuit").addEventListener("click", () => {
    if (bridge && bridge.quit) bridge.quit();
    else window.close();
  });

  if (bridge && bridge.onPauseMovement) {
    bridge.onPauseMovement(() => setPaused(!state.paused));
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") panel.hidden = true;
    if (e.key === "Enter" && panel.hidden) {
      panel.hidden = false;
      input.focus();
    }
  });
}

// ── boot ────────────────────────────────────────────────────────────────────
async function checkAuth() {
  try {
    const r = await fetch("/auth/status", { credentials: "include" });
    const j = await r.json();
    return !!(j && j.authenticated);
  } catch (e) {
    return false;
  }
}

// Only ever called when the AI actually needs a session (HTTP 401/403).
function requestLogin() {
  if (bridge && bridge.openLogin) bridge.openLogin();
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

  if (bridge && bridge.onAuthOk) {
    bridge.onAuthOk(() => {
      setState("idle");
      bubble("assistant", "Signed in. Ask me anything.");
    });
  }

  // The body is shown regardless of auth — signing in is only required to use
  // the AI. This lets the website's "Show companion outside ValleyMind" toggle
  // reveal the character without forcing a login.
  setState("idle");
  if (bridge && bridge.ready) {
    bridge.ready();
  } else if (!(await checkAuth())) {
    // Opened directly in a browser without a desktop bridge and no session.
    setState("error");
    logLine("Not signed in. Open the ValleyMind app and sign in first.", true);
  }

  // Keep the ambient behaviour lively but stop the CPU cost when hidden.
  document.addEventListener("visibilitychange", () => {
    engine.setRendering(!document.hidden);
  });
}

boot();
