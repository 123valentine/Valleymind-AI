(function () {
  "use strict";

  var EMOTIONS = [
    "neutral", "happy", "excited", "thinking", "curious", "concerned", "sad",
    "frustrated", "angry", "surprised", "confused", "focused", "listening", "speaking"
  ];
  var INTERACTION_STATES = [
    "idle", "listening", "thinking", "speaking", "helping", "learning", "observing", "guiding"
  ];
  var PRESENTATIONS = ["feminine", "masculine", "neutral"];
  var PERSONALITY_STYLES = ["calm", "friendly", "playful", "professional", "energetic", "gentle"];
  var FUTURE_CONTEXT_KEYS = [
    "screen_context", "browser_context", "application_context",
    "task_context", "selected_content", "attention_target"
  ];
  var CLOUD_ORB_ID = "vmCloudOrb";
  var CLOUD_READOUT_ID = "vmCloudStateReadout";
  var CSS_ID = "vmCloudCSS";
  var VOICE_OPTIONS = [
    ["", "Auto"],
    ["marcus", "Marcus"],
    ["elena", "Elena"],
    ["angelina", "Angelina"]
  ];

  var EMOTION_HINTS = {
    happy: ["happy", "great", "amazing", "awesome", "love", "wonderful", "glad", "excited", "fun", "perfect", "fantastic", "yay"],
    concerned: ["sad", "depressed", "upset", "frustrated", "angry", "annoyed", "stuck", "broken", "failed", "error", "worried", "scared", "miss", "problem", "not working", "doesn't work", "hate"],
    curious: ["how", "what", "why", "when", "where", "who", "curious", "wonder", "explain", "tell me more", "question"],
    focused: ["plan", "help me", "organize", "steps", "tutorial", "guide", "schedule", "remember", "write a", "create a"]
  };

  function findHint(text, map) {
    var t = String(text || "").toLowerCase();
    var keys = Object.keys(map);
    for (var i = 0; i < keys.length; i++) {
      var list = map[keys[i]];
      for (var j = 0; j < list.length; j++) {
        if (t.indexOf(list[j]) !== -1) return keys[i];
      }
    }
    return "";
  }

  function pickCloudEmotion(userText, replyText) {
    var emo = findHint(userText, EMOTION_HINTS);
    if (!emo) emo = findHint(replyText, EMOTION_HINTS);
    if (!emo) emo = String(userText || "").indexOf("?") !== -1 ? "curious" : "neutral";
    return emo;
  }

  var EMOTION_VISUALS = {
    neutral:    { color: "#7cc7ff", glow: 32, expression: "calm",         animation: "drift" },
    happy:      { color: "#3ddc84", glow: 48, expression: "warm",         animation: "bounce" },
    excited:    { color: "#ffd166", glow: 64, expression: "bright",       animation: "spring" },
    thinking:   { color: "#b49cff", glow: 44, expression: "focused",      animation: "slow-drift" },
    curious:    { color: "#4cc9f0", glow: 44, expression: "tilt",         animation: "float" },
    concerned:  { color: "#ffb86b", glow: 36, expression: "soft",         animation: "slow" },
    sad:        { color: "#8ea2b5", glow: 24, expression: "low",          animation: "sink" },
    frustrated: { color: "#ff9f43", glow: 40, expression: "tight",        animation: "jitter" },
    angry:      { color: "#f8485e", glow: 48, expression: "sharp",        animation: "shake" },
    surprised:  { color: "#35e0c8", glow: 56, expression: "wide",         animation: "pop" },
    confused:   { color: "#a5b7c4", glow: 32, expression: "askew",        animation: "sway" },
    focused:    { color: "#5ce1ff", glow: 48, expression: "steady",       animation: "hold" },
    listening:  { color: "#4fd1c5", glow: 44, expression: "open",         animation: "pulse-slow" },
    speaking:   { color: "#5ce1ff", glow: 52, expression: "expressive",   animation: "pulse" }
  };

  function defaultState() {
    return {
      emotion: "neutral", mode: "companion", status: "idle",
      presentation: "neutral", accent: "", intensity: 0.5,
      screen_context: null, browser_context: null, application_context: null,
      task_context: null, selected_content: null, attention_target: null
    };
  }

  function defaultPrefs() {
    return {
      presentation: "neutral", personality_style: "calm",
      voice_preference: "", appearance: "", accent: "", animation_intensity: 0.5,
      cloud_name: "Cloud", companion_minimized: false
    };
  }

  var CLOUD = {
    state: defaultState(),
    prefs: defaultPrefs(),
    chatId: "",
    transcript: [],
    busy: false,
    inited: false,
    turnToken: 0,
    lastEmotion: "neutral",
    surface: "hidden",        // "hidden" | "mini" | "panel" | "workspace"
    companionActive: false,
    companionInited: false,
    visionActive: false
  };

  function $id(s) { return document.getElementById(s); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function clamp01(v) {
    var n = Number(v);
    if (!isFinite(n)) return 0.5;
    return Math.max(0, Math.min(1, n));
  }
  function inList(v, list, dflt) { return list.indexOf(v) !== -1 ? v : dflt; }

  function renderConfig() {
    var vis = EMOTION_VISUALS[CLOUD.state.emotion] || EMOTION_VISUALS.neutral;
    var color = (CLOUD.state.accent && CLOUD.state.accent !== "auto") ? CLOUD.state.accent : vis.color;
    var intensity = CLOUD.state.intensity == null ? 0.5 : clamp01(CLOUD.state.intensity);
    return {
      emotion: CLOUD.state.emotion,
      status: CLOUD.state.status,
      mode: CLOUD.state.mode,
      presentation: CLOUD.state.presentation,
      presentation_pref: CLOUD.prefs.presentation,
      accent_pref: CLOUD.prefs.accent,
      accent: color,
      intensity: intensity,
      color: color,
      glow: Math.max(18, Math.round(vis.glow * (0.6 + intensity * 0.9))),
      expression: vis.expression,
      animation: vis.animation,
      attention_target: CLOUD.state.attention_target,
      context: {},
      future_context: {
        screen_context: CLOUD.state.screen_context,
        browser_context: CLOUD.state.browser_context,
        application_context: CLOUD.state.application_context,
        task_context: CLOUD.state.task_context,
        selected_content: CLOUD.state.selected_content
      }
    };
  }

  function applyRuntimePatch(patch) {
    if (!patch || typeof patch !== "object") return CLOUD.state;
    if ("emotion" in patch) CLOUD.state.emotion = inList(patch.emotion, EMOTIONS, CLOUD.state.emotion);
    if ("status" in patch) CLOUD.state.status = inList(patch.status, INTERACTION_STATES, CLOUD.state.status);
    if ("mode" in patch) CLOUD.state.mode = String(patch.mode || "companion");
    if ("presentation" in patch) CLOUD.state.presentation = inList(patch.presentation, PRESENTATIONS, CLOUD.state.presentation);
    if ("accent" in patch) CLOUD.state.accent = String(patch.accent || "").slice(0, 64);
    if ("intensity" in patch) CLOUD.state.intensity = clamp01(patch.intensity);
    if ("attention_target" in patch) CLOUD.state.attention_target = patch.attention_target;
    reflectState();
    return CLOUD.state;
  }

  function applyServerModel(model) {
    if (!model || typeof model !== "object") return CLOUD.state;
    if (inList(model.presentation, PRESENTATIONS, "") !== "") CLOUD.state.presentation = model.presentation;
    if (typeof model.accent === "string") CLOUD.state.accent = model.accent.slice(0, 64);
    if (model.intensity != null) CLOUD.state.intensity = clamp01(model.intensity);
    if (model.mode) CLOUD.state.mode = String(model.mode);
    FUTURE_CONTEXT_KEYS.forEach(function (key) {
      if (key === "attention_target") { if (model.attention_target != null) CLOUD.state.attention_target = model.attention_target; return; }
      if (model[key] != null) CLOUD.state[key] = model[key];
    });
    reflectState();
    return CLOUD.state;
  }

  function reflectState() {
    var orb = $id(CLOUD_ORB_ID);
    if (orb) {
      var cfg = renderConfig();
      orb.style.setProperty("--cloud-color", cfg.color);
      orb.style.setProperty("--cloud-glow", cfg.glow + "px");
      orb.style.setProperty("--cloud-intensity", String(cfg.intensity));
      orb.setAttribute("data-expression", cfg.expression);
      var lbl = $id("vmCloudOrbLabel");
      if (lbl) lbl.textContent = cfg.emotion + " · " + cfg.status + " · " + cfg.animation;
      orb.title = JSON.stringify({ emotion: cfg.emotion, status: cfg.status, animation: cfg.animation, context: cfg.future_context });
    }
    var cOrb = $id("vmCloudCompanionOrb");
    if (cOrb) {
      var ccfg = renderConfig();
      cOrb.style.setProperty("--cloud-color", ccfg.color);
      cOrb.style.setProperty("--cloud-glow", ccfg.glow + "px");
      cOrb.style.setProperty("--cloud-intensity", String(ccfg.intensity));
      cOrb.setAttribute("data-expression", ccfg.expression);
      var cLbl = $id("vmCloudCompanionOrbLabel");
      if (cLbl) cLbl.textContent = ccfg.emotion + " · " + ccfg.status;
    }
    var shell = $id("vmCloudCompanion");
    if (shell) {
      shell.style.setProperty("--cloud-color", renderConfig().color);
      shell.classList.toggle("sharing", CLOUD.visionActive);
    }
    var readout = $id(CLOUD_READOUT_ID);
    if (readout) readout.textContent = JSON.stringify(CLOUD.state, null, 2);
    var chips = document.querySelectorAll(".vmcloud-chip");
    for (var i = 0; i < chips.length; i++) {
      var c = chips[i];
      var v = c.getAttribute("data-v");
      c.classList.toggle("active", v === CLOUD.state.emotion || v === CLOUD.state.status);
    }
    notify3d();
  }

  function notify3d() {
    if (window.VMCloud3D && typeof window.VMCloud3D.notifyState === "function") {
      try { window.VMCloud3D.notifyState(renderConfig()); } catch (e) { }
    }
  }

  function applyPrefs(p) {
    if (!p) return;
    if (inList(p.presentation, PRESENTATIONS, "") !== "") CLOUD.prefs.presentation = p.presentation;
    if (inList(p.personality_style, PERSONALITY_STYLES, "") !== "") CLOUD.prefs.personality_style = p.personality_style;
    if (typeof p.voice_preference === "string") CLOUD.prefs.voice_preference = p.voice_preference;
    if (typeof p.appearance === "string") CLOUD.prefs.appearance = p.appearance;
    if (p.accent) CLOUD.prefs.accent = String(p.accent).slice(0, 64);
    if (p.animation_intensity != null) CLOUD.prefs.animation_intensity = clamp01(p.animation_intensity);
    if (typeof p.cloud_name === "string") CLOUD.prefs.cloud_name = normalizeNameInput(p.cloud_name);
    if (typeof p.companion_minimized === "boolean") CLOUD.prefs.companion_minimized = p.companion_minimized;
    else if (p.companion_minimized === "true" || p.companion_minimized === "false") CLOUD.prefs.companion_minimized = p.companion_minimized === "true";
    CLOUD.state.presentation = CLOUD.prefs.presentation;
    CLOUD.state.intensity = CLOUD.prefs.animation_intensity;
    if (CLOUD.prefs.accent && CLOUD.prefs.accent !== "auto") CLOUD.state.accent = CLOUD.prefs.accent;
  }

  function reflectPrefsUI() {
    var p = CLOUD.prefs, s = CLOUD.state;
    function setVal(idName, val) { var el = $id(idName); if (el) el.value = val == null ? "" : String(val); }
    setVal("vmCloudPresentation", p.presentation);
    setVal("vmCloudPersonality", p.personality_style);
    setVal("vmCloudVoice", p.voice_preference);
    setVal("vmCloudAccent", p.accent);
    setVal("vmCloudIntensity", s.intensity);
    setVal("vmCloudName", p.cloud_name);
    var iv = $id("vmCloudIntensityVal");
    if (iv) iv.textContent = Math.round(s.intensity * 100) + "%";
    var mini = $id("vmCloudMinimized");
    if (mini) mini.checked = !!p.companion_minimized;
  }

  function loadPrefs() {
    return settingsApiGet("cloud").then(function (res) {
      if (res && res.status === "success" && res.data && typeof res.data === "object") applyPrefs(res.data);
      reflectPrefsUI();
      reflectState();
      updateIdentity();
      return CLOUD.prefs;
    }).catch(function () {
      updateIdentity();
      return CLOUD.prefs;
    });
  }

  function readPrefsForm() {
    var p = CLOUD.prefs;
    var pres = $id("vmCloudPresentation"), pers = $id("vmCloudPersonality"),
        acc = $id("vmCloudAccent"), inten = $id("vmCloudIntensity");
    if (pres && inList(pres.value, PRESENTATIONS, "") !== "") p.presentation = pres.value;
    if (pers && inList(pers.value, PERSONALITY_STYLES, "") !== "") p.personality_style = pers.value;
    var voice = $id("vmCloudVoice");
    if (voice) p.voice_preference = String(voice.value || "").trim().slice(0, 40);
    if (acc) p.accent = String(acc.value || "").trim().slice(0, 64);
    if (inten) p.animation_intensity = clamp01(inten.value);
    p.cloud_name = normalizeNameInput(($id("vmCloudName") || {}).value);
    var mini = $id("vmCloudMinimized");
    if (mini) p.companion_minimized = !!mini.checked;
  }

  function normalizeNameInput(value) {
    var text = String(value == null ? "" : value)
      .replace(/[\u0000-\u001f\u007f]/g, "")
      .replace(/\s+/g, " ")
      .trim();
    return text.slice(0, 32) || "Cloud";
  }

  function savePrefs() {
    readPrefsForm();
    var p = CLOUD.prefs;
    CLOUD.state.presentation = p.presentation;
    CLOUD.state.intensity = p.animation_intensity;
    if (p.accent && p.accent !== "auto") CLOUD.state.accent = p.accent;
    reflectState();
    return settingsApiSave("cloud", p).then(function (res) {
      var st = $id("vmCloudPrefsStatus");
      if (st) st.textContent = res && res.status === "success" ? "Saved to ValleyMind settings + memory." : "Save failed.";
      if (st) st.style.color = res && res.status === "success" ? "#3ddc84" : "#f8485e";
      return res;
    }).catch(function () {
      var st = $id("vmCloudPrefsStatus");
      if (st) { st.textContent = "Save failed (connection)."; st.style.color = "#f8485e"; }
    });
  }

  function syncStateFromServer() {
    return apiFetch("/api/cloud/state", {
      credentials: "include",
      headers: authHeaders(),
      timeoutMs: 15000
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.status === "success" && d.state) applyServerModel(d.state);
      var st = $id("vmCloudStateStatus");
      if (st) st.textContent = d && d.status === "success" ? "State synced from server." : "Sync failed.";
      return d;
    }).catch(function (e) {
      var st = $id("vmCloudStateStatus");
      if (st) st.textContent = "Sync failed: " + (e && e.message ? e.message : "connection");
      return null;
    });
  }

  function persistState() {
    var snapshot = JSON.parse(JSON.stringify(CLOUD.state));
    return apiFetch("/api/cloud/state", {
      method: "POST",
      credentials: "include",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(snapshot),
      timeoutMs: 15000
    }).then(function (r) { return r.json(); }).then(function (d) {
      var st = $id("vmCloudStateStatus");
      if (st) st.textContent = d && d.status === "success" ? "State persisted." : "Persist failed.";
      return d;
    }).catch(function () {
      var st = $id("vmCloudStateStatus");
      if (st) st.textContent = "Persist failed (connection).";
      return null;
    });
  }

  function ensureSession() {
    if (CLOUD.chatId) return Promise.resolve(CLOUD.chatId);
    return apiFetch("/chat/sessions", {
      method: "POST",
      credentials: "include",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ title: "Cloud Companion" }),
      timeoutMs: 20000
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.status === "success" && d.session && d.session.chat_id) CLOUD.chatId = d.session.chat_id;
      return CLOUD.chatId || ("cloud_" + Date.now());
    }).catch(function () { return CLOUD.chatId || ("cloud_" + Date.now()); });
  }

  function pushTurn(role, text, meta) {
    CLOUD.transcript.push({ role: role, text: text, ts: Date.now(), meta: meta || null });
    renderTranscript();
    renderCompanionLog();
  }

  function markdownHtml(text) {
    if (window.marked && typeof window.marked.parse === "function") {
      try { return window.marked.parse(text); } catch (e) { }
    }
    return esc(text).replace(/\n/g, "<br>");
  }

  function renderTranscript() {
    var log = $id("vmCloudLog");
    if (!log) return;
    var html = "";
    CLOUD.transcript.forEach(function (t) {
      html += '<div class="vmcloud-msg ' + (t.role === "user" ? "user" : "cloud") + '">' +
        '<div class="vmcloud-msg-role">' + (t.role === "user" ? "You" : "Cloud") + '</div>' +
        '<div class="vmcloud-msg-body">' + markdownHtml(t.text) + '</div>' +
        (t.meta && t.meta.by === "brain" ? '<div class="vmcloud-msg-meta">answered by the existing ValleyMind brain</div>' : '') +
        '</div>';
    });
    if (!CLOUD.transcript.length) {
      html += '<div class="vmcloud-placeholder-msg">Say hello — this chat runs on your existing ValleyMind brain and memory.</div>';
    }
    log.innerHTML = html;
    log.scrollTop = log.scrollHeight;
    var input = $id("vmCloudInput");
    if (input) input.focus();
  }

  function buildFramePayload() {
    if (!CLOUD.visionActive || !window.VMCloudVision) return "";
    try { return window.VMCloudVision.capture(720) || ""; } catch (e) { return ""; }
  }

  function send(text, opts) {
    text = String(text || "").trim();
    if (!text || CLOUD.busy) return;
    opts = opts || {};
    var token = ++CLOUD.turnToken;
    CLOUD.busy = true;
    pushTurn("user", text);
    applyRuntimePatch({ status: "thinking", emotion: "thinking" });
    if (window.VMCloud3D && typeof window.VMCloud3D.notifySpeech === "function") {
      try { window.VMCloud3D.notifySpeech(false); } catch (e) { }
    }
    var frame = buildFramePayload();
    var hasVision = CLOUD.visionActive;
    ensureSession().then(function (chatId) {
      return apiFetch("/api/cloud/chat", {
        method: "POST",
        credentials: "include",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ message: text, chat_id: chatId, image_data: frame || "" }),
        timeoutMs: 120000
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (token !== CLOUD.turnToken) return;
        CLOUD.busy = false;
        if (d && d.status === "success") {
          var emo = pickCloudEmotion(text, d.reply || "");
          CLOUD.lastEmotion = emo;
          pushTurn("cloud", d.reply, { by: "brain" });
          if (d.state) applyServerModel(d.state);
          if (hasVision) {
            applyRuntimePatch({ status: "observing", emotion: emo === "neutral" ? "curious" : emo });
          } else {
            applyRuntimePatch({ status: "idle", emotion: emo });
          }
          if (opts.voice && d.reply) speakCloudReply(d.reply, emo);
        } else {
          pushTurn("cloud", "Cloud hit an error: " + ((d && d.message) || "unknown response"));
          applyRuntimePatch({ status: "idle", emotion: "confused" });
          if (opts.voice) voiceNote("ValleyMind hit an error — text reply shown.", true);
        }
      }).catch(function () {
        if (token !== CLOUD.turnToken) return;
        CLOUD.busy = false;
        pushTurn("cloud", "Cloud could not reach ValleyMind. Check your connection and try again.");
        applyRuntimePatch({ status: "idle", emotion: "concerned" });
        if (opts.voice) voiceNote("ValleyMind unreachable — text reply shown.", true);
      });
    });
  }

  function sendFromInput() {
    var input = $id("vmCloudInput");
    if (!input) return;
    var text = input.value;
    input.value = "";
    send(text);
  }

  var _voiceSeq = 0;
  var _voiceNoteT = null;

  function submitVoiceTurn(text) {
    send(text, { voice: true });
  }

  function speakCloudReply(text, emotion) {
    ++_voiceSeq;
    applyRuntimePatch({ status: "speaking", emotion: emotion });
    setVoiceStatus("Speaking…");
    if (!window.VMCloudVoice) {
      applyRuntimePatch({ status: "idle" });
      return;
    }
    window.VMCloudVoice.speak(text, {
      prefs: CLOUD.prefs,
      presentation: CLOUD.state.presentation
    });
  }

  function interruptCloud() {
    ++_voiceSeq;
    ++CLOUD.turnToken;
    CLOUD.busy = false;
    if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
    if (window.VMCloud3D && typeof window.VMCloud3D.notifySpeech === "function") {
      try { window.VMCloud3D.notifySpeech(false); } catch (e) { }
    }
    applyRuntimePatch({ status: "idle", emotion: CLOUD.lastEmotion || "neutral" });
    setVoiceStatus("Tap the mic to talk");
  }

  function stopVoice() {
    if (window.VMCloudVoice) { try { window.VMCloudVoice.stop(); } catch (e) { } }
    if (window.VMCloud3D && typeof window.VMCloud3D.notifySpeech === "function") {
      try { window.VMCloud3D.notifySpeech(false); } catch (e) { }
    }
    applyRuntimePatch({ status: "idle", emotion: CLOUD.lastEmotion || "neutral" });
    setVoiceStatus("Tap the mic to talk");
  }

  function toggleVoice() {
    if (!window.VMCloudVoice) return;
    if (!window.VMCloudVoice.supported()) {
      voiceNote("Voice input isn't supported in this browser — text chat still works.", true);
      return;
    }
    if (window.VMCloudVoice.isListening()) {
      var spoken = window.VMCloudVoice.takeTranscript();
      window.VMCloudVoice.stopListening();
      if (spoken.trim().length >= 2) submitVoiceTurn(spoken);
      return;
    }
    interruptCloud();
    window.VMCloudVoice.start();
  }

  function setVoiceStatus(txt, cls) {
    var el = $id("vmCloudVoiceStatus");
    if (el) {
      el.textContent = txt;
      el.className = "vmcloud-voice-status" + (cls ? " " + cls : "");
    }
    var ce = $id("vmCloudCompanionVoiceStatus");
    if (ce) {
      ce.textContent = txt;
      ce.className = "vmcloud-voice-status" + (cls ? " " + cls : "");
    }
  }

  function voiceNote(txt, isError) {
    setVoiceStatus(txt, isError ? "error" : "note");
    if (isError) return;
    if (_voiceNoteT) clearTimeout(_voiceNoteT);
    _voiceNoteT = setTimeout(function () { setVoiceStatus("Tap the mic to talk"); }, 6000);
  }

  function updateMicUI() {
    var on = window.VMCloudVoice && window.VMCloudVoice.isListening();
    var b = $id("vmCloudMicBtn");
    if (b) b.classList.toggle("listening", !!on);
    var cb = $id("vmCloudCompMicBtn");
    if (cb) cb.classList.toggle("listening", !!on);
  }

  function wireVoice() {
    if (!window.VMCloudVoice || typeof window.VMCloudVoice.setHooks !== "function") return;
    window.VMCloudVoice.setHooks({
      onListeningChange: function (active) {
        if (active) {
          applyRuntimePatch({ status: "listening", emotion: "listening" });
          setVoiceStatus("Listening…  tap mic when done");
        } else if (CLOUD.state.status === "listening") {
          applyRuntimePatch({ status: "idle", emotion: CLOUD.lastEmotion || "neutral" });
          setVoiceStatus("Tap the mic to talk");
        }
        updateMicUI();
      },
      onResult: function (text, isFinal) {
        if (isFinal) {
          var t = String(text || "").trim();
          if (t.length >= 2) submitVoiceTurn(t);
        }
      },
      onSpeakStart: function () {
        applyRuntimePatch({ status: "speaking" });
        if (window.VMCloud3D && typeof window.VMCloud3D.notifySpeech === "function") {
          try { window.VMCloud3D.notifySpeech(true); } catch (e) { }
        }
      },
      onSpeakEnd: function (reason) {
        if (window.VMCloud3D && typeof window.VMCloud3D.notifySpeech === "function") {
          try { window.VMCloud3D.notifySpeech(false); } catch (e) { }
        }
        applyRuntimePatch({ status: "idle", emotion: CLOUD.lastEmotion || "neutral" });
        setVoiceStatus("Tap the mic to talk");
        if (reason && reason !== "interrupted") {
          voiceNote(reason === "tts unavailable"
            ? "Voice unavailable — text reply shown."
            : "Voice playback failed — text reply shown.", true);
        }
      },
      onError: function (code) {
        applyRuntimePatch({ status: "idle", emotion: "confused" });
        voiceNote(code === "microphone blocked"
          ? "Microphone access is blocked — allow mic access to use voice."
          : "Voice input isn't supported in this browser.", true);
        updateMicUI();
      }
    });
    updateMicUI();
  }

  function chipHtml(kind) {
    var html = "";
    var list = kind === "emotion" ? EMOTIONS : INTERACTION_STATES;
    for (var i = 0; i < list.length; i++) {
      var v = list[i];
      html += '<button type="button" class="vmcloud-chip" data-v="' + v + '" onclick="VMCloud.pick(\'' + kind + '\',\'' + v + '\')">' + v + '</button>';
    }
    return html;
  }

  function renderSelections() {
    var sel = function (idName, options, current) {
      var html = '<select id="' + idName + '">';
      for (var i = 0; i < options.length; i++) {
        var v = Array.isArray(options[i]) ? options[i][0] : options[i];
        var l = Array.isArray(options[i]) ? options[i][1] : options[i];
        html += '<option value="' + v + '"' + (String(v) === String(current) ? ' selected' : '') + '>' + l + '</option>';
      }
      return html + '</select>';
    };
    return sel("vmCloudPresentation", PRESENTATIONS, CLOUD.prefs.presentation) +
      sel("vmCloudPersonality", PERSONALITY_STYLES, CLOUD.prefs.personality_style);
  }

  function renderPrefRows() {
    var sels = renderSelections();
    var presentSel = sels.substring(0, sels.indexOf("</select>") + "</select>".length);
    var rest = sels.substring(sels.indexOf("</select>") + "</select>".length);
    var persSel = rest.substring(0, rest.indexOf("</select>") + "</select>".length);
    var voiceSel = '<select id="vmCloudVoice">';
    for (var i = 0; i < VOICE_OPTIONS.length; i++) {
      var v = VOICE_OPTIONS[i][0], l = VOICE_OPTIONS[i][1];
      voiceSel += '<option value="' + v + '"' + (String(v) === String(CLOUD.prefs.voice_preference) ? ' selected' : '') + '>' + l + '</option>';
    }
    voiceSel += '</select>';
    return '<div class="vmcloud-pref-row"><label>Presentation</label>' + presentSel + '</div>' +
      '<div class="vmcloud-pref-row"><label>Personality style</label>' + persSel + '</div>' +
      '<div class="vmcloud-pref-row"><label>Voice</label>' + voiceSel + '</div>';
  }

  function render() {
    var pan = $id("vmWsPanelCloud");
    if (!pan) return;
    var cfg = renderConfig();
    pan.innerHTML =
      '<div class="vmcloud-shell">' +
        '<div class="vmcloud-header">' +
          '<div class="vmcloud-stage" id="vmCloudStage">' +
            '<div class="vmcloud-backdrop"></div>' +
            '<div class="vmcloud-orb" id="' + CLOUD_ORB_ID + '" style="display:none"></div>' +
            '<div class="vmcloud-stage-status" id="vmCloud3DStatus">Activating Cloud…</div>' +
          '</div>' +
          '<div class="vmcloud-header-text">' +
            '<h2 id="vmCloudTitle">Cloud</h2>' +
            '<p>Companion body · presented around your existing ValleyMind brain and memory</p>' +
            '<div id="vmCloudOrbLabel" class="vmcloud-orb-label">' + cfg.emotion + ' · ' + cfg.status + ' · ' + cfg.animation + '</div>' +
          '</div>' +
          '<div class="vmcloud-voice">' +
            '<button type="button" id="vmCloudMicBtn" class="vmcloud-mic" onclick="VMCloud.toggleVoice()" aria-label="Talk to Cloud">' +
              '<i data-lucide="mic" style="width:24px;height:24px;"></i>' +
            '</button>' +
            '<div id="vmCloudVoiceStatus" class="vmcloud-voice-status">Tap the mic to talk</div>' +
          '</div>' +
        '</div>' +

        '<div class="vmcloud-grid">' +
          '<div class="vmcloud-card">' +
            '<div class="vmcloud-card-title">Connection</div>' +
            '<div class="vmcloud-conn-row"><span>Brain</span><strong>Existing ValleyMind (MarcusBrain)</strong></div>' +
            '<div class="vmcloud-conn-row"><span>Memory</span><strong>Existing long-term + semantic memory</strong></div>' +
            '<div class="vmcloud-conn-row"><span>Sessions</span><strong>Existing chat sessions</strong></div>' +
            '<div class="vmcloud-conn-row"><span>Personality</span><strong>Instructions carried into the existing brain</strong></div>' +
            '<div class="vmcloud-conn-row"><span>New AI / LLM</span><strong style="color:#f8485e;">None</strong></div>' +
          '</div>' +

          '<div class="vmcloud-card">' +
            '<div class="vmcloud-card-title">State — click to change emotion / status</div>' +
            '<div class="vmcloud-chip-set">' + chipHtml("emotion") + '</div>' +
            '<div class="vmcloud-chip-set">' + chipHtml("status") + '</div>' +
            '<div style="display:flex;gap:8px;margin-top:12px;">' +
              '<button type="button" class="vmcloud-btn" onclick="VMCloud.syncStateFromServer()">Sync state</button>' +
              '<button type="button" class="vmcloud-btn" onclick="VMCloud.persistState()">Persist state</button>' +
            '</div>' +
            '<div id="vmCloudStateStatus" class="vmcloud-inline-status"></div>' +
            '<pre id="' + CLOUD_READOUT_ID + '" class="vmcloud-readout">' + esc(JSON.stringify(CLOUD.state, null, 2)) + '</pre>' +
            '<div class="vmcloud-note">renderConfig() for the future renderer is available: window.VMCloud.renderConfig()</div>' +
          '</div>' +

          '<div class="vmcloud-card">' +
            '<div class="vmcloud-card-title">Preferences (saved via the existing settings system)</div>' +
            '<div class="vmcloud-pref-row">' + renderPrefRows() + '</div>' +
            '<div class="vmcloud-pref-row"><label>Accent color</label>' +
              '<input type="text" id="vmCloudAccent" placeholder="auto" value="' + esc(CLOUD.prefs.accent) + '"></div>' +
            '<div class="vmcloud-pref-row"><label>What would you like to call your Cloud?</label>' +
              '<input type="text" id="vmCloudName" maxlength="32" placeholder="Cloud" value="' + esc(CLOUD.prefs.cloud_name) + '"></div>' +
            '<div class="vmcloud-pref-row"><label>Start Cloud minimized</label>' +
              '<input type="checkbox" id="vmCloudMinimized"' + (CLOUD.prefs.companion_minimized ? ' checked' : '') + ' style="flex:0 0 auto;accent-color:#00E5FF;"></div>' +
            '<div class="vmcloud-pref-row"><label>Animation intensity <span id="vmCloudIntensityVal"></span></label>' +
              '<input type="range" id="vmCloudIntensity" min="0" max="1" step="0.05" value="' + CLOUD.state.intensity + '" oninput="VMCloud.updateIntensityUp(this.value)"></div>' +
            '<div style="margin-top:12px;">' +
              '<button type="button" class="vmcloud-btn" onclick="VMCloud.savePrefs()">Save preferences</button>' +
              '<button type="button" class="vmcloud-btn" style="margin-left:8px;" onclick="VMCloud.showCompanion()">Show floating companion</button>' +
            '</div>' +
            '<div id="vmCloudPrefsStatus" class="vmcloud-inline-status"></div>' +
            '<div class="vmcloud-note">Personality &amp; presentation become instructions carried into your existing brain per message. No separate AI is created.</div>' +
          '</div>' +

          '<div class="vmcloud-card">' +
            '<div class="vmcloud-card-title">Screen &amp; context — explicit permission only</div>' +
            '<div class="vmcloud-conn-row"><span>Capture</span><strong>Throttled stills, only while you share</strong></div>' +
            '<div class="vmcloud-conn-row"><span>Storage</span><strong>Never saved to long-term memory</strong></div>' +
            '<div class="vmcloud-conn-row"><span>Control</span><strong style="color:#f8485e;">Seeing only — Cloud never clicks, types or scrolls</strong></div>' +
            '<button type="button" class="vmcloud-btn" id="vmCloudVisionBtn" onclick="VMCloud.toggleVision()">Let Cloud see your screen</button>' +
            '<div id="vmCloudVisionStatus" class="vmcloud-inline-status">Screen &amp; context off — Cloud can only see your screen if you share it.</div>' +
          '</div>' +

          '<div class="vmcloud-card vmcloud-chat-card">' +
            '<div class="vmcloud-card-title">Talk to Cloud <span class="vmcloud-chat-hint">(runs on the existing ValleyMind brain)</span></div>' +
            '<div class="vmcloud-log" id="vmCloudLog"></div>' +
            '<div class="vmcloud-input-row">' +
              '<input type="text" id="vmCloudInput" placeholder="Message Cloud…" onkeydown="if(event.key===\'Enter\')VMCloud.sendFromInput()">' +
              '<button type="button" class="vmcloud-btn" onclick="VMCloud.sendFromInput()">Send</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    renderTranscript();
    reflectState();
    reflectPrefsUI();
    var iv = $id("vmCloudIntensityVal");
    if (iv) iv.textContent = Math.round(CLOUD.state.intensity * 100) + "%";
  }

  function onShow() {
    injectStyles();
    if (!$id("vmWsPanelCloud")) return;
    if (!CLOUD.inited || !$id(CLOUD_ORB_ID)) {
      CLOUD.inited = true;
      render();
    }
    renderTranscript();
    loadPrefs();
    syncStateFromServer();
    wireVoice();
    wireVision();
    updateVisionUI();
    updateMicUI();
    updateIdentity();
    // The full workspace is a presentation surface of the same controller:
    // hide the floating companion shell and relocate the single 3D context.
    CLOUD.companionActive = true;
    surfaceCompanion("workspace");
    start3D();
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (e) { }
    }
  }

  function onHide() {
    stopVoice();
    if (CLOUD.companionActive) {
      // Full workspaces and the companion are two surfaces of the SAME Cloud
      // state/controller. Leaving the Cloud workspace restores the floating
      // companion instead of destroying the engine.
      var isWs = isCloudWorkspaceActive();
      surfaceCompanion(isWs ? "hidden" : (CLOUD.prefs.companion_minimized ? "mini" : "panel"));
      return;
    }
    if (window.VMCloud3D && typeof window.VMCloud3D.detach === "function") {
      try { window.VMCloud3D.detach(); } catch (e) { }
    }
  }

  // ── Persistent companion (single controller over one Cloud state) ──────

  function isAuthActive() {
    return typeof getAuthToken === "function" && !!getAuthToken();
  }

  function isCloudWorkspaceActive() {
    var p = $id("vmWsPanelCloud");
    return !!p && p.classList.contains("active");
  }

  function ensureCompanionShell() {
    var shell = $id("vmCloudCompanion");
    if (shell) return shell;
    if (!document.body) return null;
    var d = document.createElement("div");
    d.id = "vmCloudCompanion";
    d.className = "vmcloud-companion";
    d.setAttribute("role", "complementary");
    d.setAttribute("aria-label", "Cloud companion");
    d.innerHTML =
      '<div class="vmcloud-companion-panel">' +
        '<div class="vmcloud-comp-head">' +
          '<div class="vmcloud-comp-id">' +
            '<div class="vmcloud-comp-avatar" id="vmCloudCompanionOrb" tabindex="0" role="button" aria-label="Toggle Cloud companion panel"></div>' +
            '<div>' +
              '<div class="vmcloud-comp-name" id="vmCloudCompanionName">Cloud</div>' +
              '<div class="vmcloud-comp-orb-label" id="vmCloudCompanionOrbLabel">neutral · idle</div>' +
            '</div>' +
          '</div>' +
          '<div class="vmcloud-comp-head-btns">' +
            '<button type="button" class="vmcloud-comp-btn" id="vmCloudCompOpenWs" title="Open Cloud workspace" aria-label="Open Cloud workspace"><i data-lucide="expand"></i></button>' +
            '<button type="button" class="vmcloud-comp-btn" id="vmCloudCompMinimize" title="Minimize Cloud" aria-label="Minimize Cloud"><i data-lucide="minus"></i></button>' +
          '</div>' +
        '</div>' +
        '<div class="vmcloud-comp-stage" id="vmCloudCompanionStage">' +
          '<div class="vmcloud-comp-stage-status" id="vmCloudCompanionStatus">Cloud idle</div>' +
        '</div>' +
        '<div class="vmcloud-comp-controls">' +
          '<div class="vmcloud-comp-mic-zone">' +
            '<button type="button" class="vmcloud-comp-mic" id="vmCloudCompMicBtn" aria-label="Talk to Cloud"><i data-lucide="mic" style="width:20px;height:20px;"></i></button>' +
            '<div id="vmCloudCompanionVoiceStatus" class="vmcloud-voice-status">Tap the mic to talk</div>' +
          '</div>' +
          '<div class="vmcloud-comp-vision">' +
            '<button type="button" class="vmcloud-btn" id="vmCloudCompVisionBtn">Let Cloud see your screen</button>' +
            '<div class="vmcloud-comp-vision-status" id="vmCloudCompVisionStatus">Screen &amp; context off — explicit permission only</div>' +
          '</div>' +
        '</div>' +
        '<div class="vmcloud-comp-log" id="vmCloudCompanionLog"></div>' +
        '<div class="vmcloud-comp-input-row">' +
          '<input type="text" id="vmCloudCompanionInput" placeholder="Message Cloud…" maxlength="2000" aria-label="Message Cloud" autocomplete="off">' +
          '<button type="button" class="vmcloud-btn" id="vmCloudCompSend">Send</button>' +
        '</div>' +
      '</div>' +
      '<div class="vmcloud-companion-mini" id="vmCloudCompanionMini" role="button" tabindex="0" aria-label="Open Cloud companion">' +
        '<div class="vmcloud-companion-mini-orb" id="vmCloudCompanionMiniOrb" aria-hidden="true"></div>' +
        '<div class="vmcloud-companion-mini-name" id="vmCloudMiniLabel">Cloud</div>' +
      '</div>';
    document.body.appendChild(d);
    var input = $id("vmCloudCompanionInput");
    if (input) input.addEventListener("keydown", function (e) { if (e.key === "Enter") { companionSend(); } });
    var sendBtn = $id("vmCloudCompSend");
    if (sendBtn) sendBtn.addEventListener("click", companionSend);
    var micBtn = $id("vmCloudCompMicBtn");
    if (micBtn) micBtn.addEventListener("click", function () { window.VMCloud && window.VMCloud.toggleVoice(); });
    var miniBtn = $id("vmCloudCompMinimize");
    if (miniBtn) miniBtn.addEventListener("click", minimizeCompanion);
    var openWs = $id("vmCloudCompOpenWs");
    if (openWs) openWs.addEventListener("click", function () { if (typeof vmWsGo === "function") vmWsGo("cloud"); });
    var mini = $id("vmCloudCompanionMini");
    if (mini) mini.addEventListener("click", restoreCompanion);
    if (mini) mini.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); restoreCompanion(); } });
    var orb = $id("vmCloudCompanionOrb");
    if (orb) orb.addEventListener("click", function () { if (CLOUD.surface === "panel") minimizeCompanion(); else restoreCompanion(); });
    if (orb) orb.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (CLOUD.surface === "panel") minimizeCompanion(); else restoreCompanion(); } });
    var visionBtn = $id("vmCloudCompVisionBtn");
    if (visionBtn) visionBtn.addEventListener("click", toggleVision);
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (e) { }
    }
    return d;
  }

  function updateIdentity() {
    var name = normalizeNameInput(CLOUD.prefs.cloud_name);
    var t = $id("vmCloudTitle");
    if (t) t.textContent = name;
    var n = $id("vmCloudCompanionName");
    if (n) n.textContent = name;
    var m = $id("vmCloudMiniLabel");
    if (m) m.textContent = name;
    var shell = $id("vmCloudCompanion");
    if (shell) {
      shell.setAttribute("aria-label", name + " companion");
      var ib = $id("vmCloudCompOpenWs");
      if (ib) ib.title = "Open " + name + " workspace";
    }
  }

  function surfaceCompanion(mode) {
    var shell = ensureCompanionShell();
    if (!shell) return;
    CLOUD.surface = mode;
    updateIdentity();
    shell.classList.toggle("hidden", mode === "hidden" || mode === "workspace");
    shell.classList.toggle("minimized", mode === "mini");
    if (mode === "mini") {
      if (window.VMCloud3D && typeof window.VMCloud3D.suspend === "function") {
        try { window.VMCloud3D.suspend(); } catch (e) { }
      }
    } else if (mode === "panel") {
      startCompanion3D();
      renderCompanionLog();
      updateMicUI();
      updateVisionUI();
    }
  }

  function startCompanion3D() {
    var stage = $id("vmCloudCompanionStage");
    if (!stage) return;
    if (window.VMCloud3D && typeof window.VMCloud3D.attach === "function") {
      try {
        window.VMCloud3D.attach(stage, {
          config: renderConfig(),
          statusId: "vmCloudCompanionStatus",
          fallbackId: "vmCloudCompanionOrb"
        });
        if (typeof window.VMCloud3D.resume === "function") {
          window.VMCloud3D.resume(renderConfig());
        }
      } catch (e) { }
    }
  }

  function savePrefsLight() {
    settingsApiSave("cloud", JSON.parse(JSON.stringify(CLOUD.prefs))).catch(function () { });
  }

  function minimizeCompanion() {
    CLOUD.prefs.companion_minimized = true;
    surfaceCompanion("mini");
    savePrefsLight();
  }

  function restoreCompanion() {
    CLOUD.prefs.companion_minimized = false;
    surfaceCompanion("panel");
    savePrefsLight();
  }

  function companionSend() {
    var input = $id("vmCloudCompanionInput");
    if (!input) return;
    var text = input.value;
    input.value = "";
    send(text);
  }

  function renderCompanionLog() {
    var log = $id("vmCloudCompanionLog");
    if (!log) return;
    var entries = CLOUD.transcript.slice(-6);
    var html = "";
    for (var i = 0; i < entries.length; i++) {
      var t = entries[i];
      html += '<div class="vmcloud-comp-msg ' + (t.role === "user" ? "user" : "cloud") + '">' +
        '<div class="vmcloud-comp-msg-body">' + markdownHtml(t.text) + '</div></div>';
    }
    if (!entries.length) {
      html += '<div class="vmcloud-comp-empty">Your Cloud companion is here — say hello anytime.</div>';
    }
    log.innerHTML = html;
    log.scrollTop = log.scrollHeight;
  }

  // ── Explicit screen context (permission-first, throttled, transient) ───

  function visionStateLabel(s) {
    if (s === "active") return "Screen shared with Cloud — you can stop anytime.";
    if (s === "requesting") return "Waiting for your permission to share your screen…";
    if (s === "stopped") return "Screen sharing stopped — nothing is captured now.";
    if (s === "denied") return "Permission denied — nothing is captured or sent.";
    if (s === "unsupported") return "Screen sharing is not supported in this browser.";
    return "Screen & context off — Cloud can only see your screen if you share it.";
  }

  function onVisionState(s) {
    CLOUD.visionActive = (s === "active" || s === "requesting");
    if (s === "active") {
      applyRuntimePatch({ status: "observing", emotion: "curious" });
    } else if (s === "requesting") {
      applyRuntimePatch({ status: "thinking", emotion: "curious" });
    } else if (s === "denied") {
      applyRuntimePatch({ status: "idle", emotion: "confused" });
    } else {
      applyRuntimePatch({ status: "idle", emotion: "neutral" });
    }
    updateVisionUI();
  }

  function wireVision() {
    if (!window.VMCloudVision || typeof window.VMCloudVision.init !== "function") return;
    window.VMCloudVision.init({ onStateChange: onVisionState });
    updateVisionUI();
  }

  function toggleVision() {
    if (!window.VMCloudVision) return;
    var st = window.VMCloudVision.getState();
    if (st === "active") { window.VMCloudVision.stop(); return; }
    if (st === "requesting") return;
    CLOUD.visionActive = true;
    updateVisionUI();
    var started = window.VMCloudVision.start();
    if (!started && window.VMCloudVision.getState() === "off") {
      CLOUD.visionActive = false;
    }
    updateVisionUI();
  }

  function updateVisionUI() {
    var st = window.VMCloudVision ? window.VMCloudVision.getState() : "off";
    var label = visionStateLabel(st);
    var active = st === "active";
    var busy = st === "requesting";
    var spec =
      active ? "Stop sharing screen" :
      busy ? "Requesting permission…" :
      st === "denied" ? "Try again" :
      st === "unsupported" ? "Unsupported here" :
      "Let Cloud see your screen";
    function setStatus(id) { var s = $id(id); if (s) s.textContent = label; }
    var b1 = $id("vmCloudVisionBtn");
    if (b1) { b1.textContent = spec; b1.disabled = busy; }
    var b2 = $id("vmCloudCompVisionBtn");
    if (b2) { b2.textContent = spec; b2.disabled = busy; }
    setStatus("vmCloudVisionStatus");
    setStatus("vmCloudCompVisionStatus");
  }

  function companionShow() {
    if (!isAuthActive()) return;
    var shell = ensureCompanionShell();
    if (!shell) return;
    CLOUD.companionActive = true;
    wireVoice();
    wireVision();
    updateMicUI();
    loadPrefs().then(function () {
      updateIdentity();
      surfaceCompanion(isCloudWorkspaceActive() ? "workspace" : (CLOUD.prefs.companion_minimized ? "mini" : "panel"));
    });
  }

  function cleanupCompanion() {
    interruptCloud();
    if (window.VMCloudVision && typeof window.VMCloudVision.destroy === "function") {
      try { window.VMCloudVision.destroy(); } catch (e) { }
    }
    if (window.VMCloud3D && typeof window.VMCloud3D.detach === "function") {
      try { window.VMCloud3D.detach(); } catch (e) { }
    }
    CLOUD.visionActive = false;
    CLOUD.companionActive = false;
    CLOUD.surface = "hidden";
    CLOUD.transcript = [];
    CLOUD.chatId = "";
    CLOUD.busy = false;
    CLOUD.inited = false;
    CLOUD.companionInited = false;
    CLOUD.lastEmotion = "neutral";
    CLOUD.state = defaultState();
    CLOUD.prefs = defaultPrefs();
    var shell = $id("vmCloudCompanion");
    if (shell && shell.parentNode) shell.parentNode.removeChild(shell);
  }

  function start3D() {
    var stage = $id("vmCloudStage");
    if (!stage) return;
    if (window.VMCloud3D && typeof window.VMCloud3D.attach === "function") {
      try {
        window.VMCloud3D.attach(stage, {
          config: renderConfig(),
          statusId: "vmCloud3DStatus",
          fallbackId: "vmCloudOrb"
        });
        if (typeof window.VMCloud3D.resume === "function") {
          window.VMCloud3D.resume(renderConfig());
        }
      } catch (e) { }
    }
  }

  function injectStyles() {
    if (document.getElementById(CSS_ID)) return;
    var st = document.createElement("style");
    st.id = CSS_ID;
    st.textContent =
      "#vmWsPanelCloud{margin:0 auto;padding:24px;max-width:1200px;width:100%;}" +
      ".vmcloud-shell{display:flex;flex-direction:column;gap:16px;font-family:'Inter',sans-serif;}" +
      ".vmcloud-header{display:flex;align-items:center;gap:20px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:16px;padding:20px;}" +
      ".vmcloud-header-text h2{color:#f1f5f9;font-size:22px;font-weight:700;font-family:'Space Grotesk',sans-serif;margin:0 0 4px;}" +
      ".vmcloud-header-text p{color:#64748b;font-size:13px;margin:0 0 6px;}" +
      ".vmcloud-orb-label{color:#94a3b8;font-size:12px;letter-spacing:0.04em;text-transform:capitalize;}" +
      ".vmcloud-voice{display:flex;flex-direction:column;align-items:center;gap:8px;flex:0 0 auto;}" +
      ".vmcloud-mic{width:64px;height:64px;border-radius:50%;border:1px solid rgba(0,229,255,0.45);background:radial-gradient(circle at 35% 30%,rgba(0,229,255,0.22),rgba(0,10,18,0.9) 70%);color:#7ff0ff;display:flex;align-items:center;justify-content:center;box-shadow:0 0 18px rgba(0,212,255,0.35);cursor:pointer;transition:all 0.2s;padding:0;}" +
      ".vmcloud-mic:hover{box-shadow:0 0 26px rgba(0,212,255,0.6);transform:scale(1.03);}" +
      ".vmcloud-mic.listening{color:#fff;border-color:#ff5e7a;background:radial-gradient(circle at 35% 30%,rgba(255,94,122,0.3),rgba(30,0,10,0.9) 70%);box-shadow:0 0 26px rgba(255,94,122,0.6);animation:vmcloud-mic-pulse 1.1s ease-in-out infinite;}" +
      "@keyframes vmcloud-mic-pulse{0%,100%{box-shadow:0 0 14px rgba(255,94,122,0.4);}50%{box-shadow:0 0 34px rgba(255,94,122,0.85);}}" +
      ".vmcloud-voice-status{font-size:12px;color:#8fd0ff;letter-spacing:0.05em;text-transform:uppercase;font-family:'Space Grotesk',sans-serif;text-align:center;max-width:230px;line-height:1.4;}" +
      ".vmcloud-voice-status.error{color:#ff8fa6;}" +
      ".vmcloud-voice-status.note{color:#a5b7c4;}" +
      ".vmcloud-stage{position:relative;width:100%;height:clamp(290px,42vh,410px);flex:0 0 auto;border-radius:18px;overflow:hidden;border:1px solid rgba(0,212,255,0.14);background:#071019;isolation:isolate;}" +
      ".vmcloud-backdrop{position:absolute;inset:0;z-index:0;background:radial-gradient(120% 90% at 50% 28%,rgba(0,212,255,0.14),transparent 58%),radial-gradient(140% 100% at 50% 112%,rgba(0,20,36,0.85),transparent 62%);}" +
      ".vmcloud-stage-status{position:absolute;left:14px;bottom:12px;z-index:2;font-size:11px;color:#8fd0ff;letter-spacing:0.08em;text-transform:uppercase;font-family:'Space Grotesk',sans-serif;}" +
      ".vmcloud-orb{position:absolute;left:50%;top:52%;z-index:1;transform:translate(-50%,-50%);width:110px;height:96px;border-radius:60% 50% 55% 45%;background:radial-gradient(circle at 35% 30%,rgba(255,255,255,0.95),var(--cloud-color,#00d4ff) 48%,rgba(0,80,110,0.5) 78%);box-shadow:0 0 34px var(--cloud-color,#00d4ff),inset -8px -10px 22px rgba(0,40,60,0.35);animation:vmcloud-pulse 3.4s ease-in-out infinite;}" +
      ".vmcloud-orb-core{position:absolute;left:22%;top:18%;width:24%;height:26%;border-radius:50%;background:rgba(255,255,255,0.9);filter:blur(3px);opacity:0.8;}" +
      "@keyframes vmcloud-pulse{0%,100%{transform:translate(-50%,-50%) scale(1);}50%{transform:translate(-50%,-50%) scale(1.04);}}" +
      ".vmcloud-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;}" +
      ".vmcloud-card{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:18px;min-width:0;display:flex;flex-direction:column;gap:10px;}" +
      ".vmcloud-card-title{color:#94a3b8;font-size:10px;text-transform:uppercase;letter-spacing:0.15em;font-family:'Space Grotesk',sans-serif;font-weight:600;}" +
      ".vmcloud-conn-row{display:flex;justify-content:space-between;gap:10px;color:#94a3b8;font-size:13px;}" +
      ".vmcloud-conn-row strong{color:#e2e8f0;text-align:right;font-weight:600;}" +
      ".vmcloud-chip-set{display:flex;flex-wrap:wrap;gap:6px;}" +
      ".vmcloud-chip{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);color:#cbd5e1;border-radius:999px;padding:5px 12px;font-size:12px;cursor:pointer;transition:all 0.15s;text-transform:capitalize;}" +
      ".vmcloud-chip:hover{border-color:rgba(0,229,255,0.5);}" +
      ".vmcloud-chip.active{background:rgba(0,229,255,0.16);border-color:#00E5FF;color:#00E5FF;}" +
      ".vmcloud-btn{background:rgba(0,229,255,0.12);border:1px solid rgba(0,229,255,0.4);color:#7ff0ff;border-radius:10px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;transition:background 0.15s;}" +
      ".vmcloud-btn:hover{background:rgba(0,229,255,0.22);}" +
      ".vmcloud-readout{background:#0b1220;border:1px solid rgba(255,255,255,0.05);border-radius:10px;padding:12px;color:#7ee7b8;font-size:12px;line-height:1.5;overflow:auto;max-height:180px;margin:0;}" +
      ".vmcloud-inline-status{font-size:12px;color:#64748b;min-height:14px;}" +
      ".vmcloud-note{font-size:11px;color:#64748b;line-height:1.5;}" +
      ".vmcloud-pref-row{display:flex;align-items:center;justify-content:space-between;gap:10px;color:#94a3b8;font-size:13px;}" +
      ".vmcloud-pref-row label{flex:0 0 150px;color:#94a3b8;}" +
      ".vmcloud-pref-row select,.vmcloud-pref-row input[type=text]{flex:1;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);color:#e2e8f0;border-radius:9px;padding:7px 10px;font-size:13px;min-width:0;}" +
      ".vmcloud-pref-row input[type=range]{flex:1;accent-color:#00E5FF;min-width:0;}" +
      ".vmcloud-log{background:#0b1220;border:1px solid rgba(255,255,255,0.05);border-radius:10px;padding:12px;min-height:140px;max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:10px;}" +
      ".vmcloud-msg{max-width:85%;border-radius:12px;padding:10px 14px;font-size:14px;line-height:1.5;}" +
      ".vmcloud-msg.user{align-self:flex-end;background:rgba(0,229,255,0.14);border:1px solid rgba(0,229,255,0.3);color:#e8fbff;}" +
      ".vmcloud-msg.cloud{align-self:flex-start;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);color:#e2e8f0;}" +
      ".vmcloud-msg-role{font-size:10px;text-transform:uppercase;letter-spacing:0.12em;color:#64748b;font-weight:700;margin-bottom:4px;}" +
      ".vmcloud-msg-meta{font-size:10px;color:#475569;margin-top:6px;}" +
      ".vmcloud-placeholder-msg{color:#475569;font-size:13px;padding:8px;text-align:center;}" +
      ".vmcloud-input-row{display:flex;gap:8px;}" +
      ".vmcloud-input-row input{flex:1;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);color:#e2e8f0;border-radius:10px;padding:10px 14px;font-size:14px;min-width:0;}" +
      ".vmcloud-input-row input:focus{outline:none;border-color:rgba(0,229,255,0.5);}" +
      ".vmcloud-chat-hint{color:#475569;font-size:11px;text-transform:none;letter-spacing:normal;}" +
      ".vmcloud-status-ok{color:#3ddc84;}" +
      "#vmCloudCompanion{position:fixed;right:18px;bottom:18px;z-index:8000;font-family:'Inter',sans-serif;isolation:isolate;}" +
      ".vmcloud-companion-panel{width:310px;max-width:calc(100vw - 24px);max-height:min(72vh,640px);display:flex;flex-direction:column;gap:10px;background:rgba(7,13,24,0.94);border:1px solid rgba(0,212,255,0.22);border-radius:18px;padding:14px;box-shadow:0 18px 50px rgba(0,0,0,0.55),0 0 0 1px rgba(0,212,255,0.05);backdrop-filter:blur(10px);}" +
      ".vmcloud-comp-head{display:flex;align-items:center;justify-content:space-between;gap:8px;}" +
      ".vmcloud-comp-id{display:flex;align-items:center;gap:10px;min-width:0;}" +
      ".vmcloud-comp-avatar{flex:0 0 auto;width:46px;height:42px;border-radius:55% 45% 50% 50%;background:radial-gradient(circle at 35% 30%,rgba(255,255,255,0.95),var(--cloud-color,#00d4ff) 48%,rgba(0,80,110,0.5) 78%);box-shadow:0 0 18px var(--cloud-color,#00d4ff);cursor:pointer;border:none;}" +
      ".vmcloud-comp-name{color:#f1f5f9;font-size:14px;font-weight:700;font-family:'Space Grotesk',sans-serif;line-height:1.2;}" +
      ".vmcloud-comp-orb-label{color:#94a3b8;font-size:11px;letter-spacing:0.04em;text-transform:capitalize;}" +
      ".vmcloud-comp-head-btns{display:flex;gap:6px;}" +
      ".vmcloud-comp-btn{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:#94a3b8;border-radius:9px;width:30px;height:30px;display:flex;align-items:center;justify-content:center;cursor:pointer;padding:0;}" +
      ".vmcloud-comp-btn:hover{color:#7ff0ff;border-color:rgba(0,229,255,0.5);}" +
      ".vmcloud-comp-btn:disabled{opacity:0.5;cursor:not-allowed;}" +
      ".vmcloud-comp-stage{position:relative;width:100%;height:120px;border-radius:12px;overflow:hidden;border:1px solid rgba(0,212,255,0.12);background:#071019;}" +
      ".vmcloud-comp-stage-status{position:absolute;left:10px;bottom:8px;z-index:2;font-size:10px;color:#8fd0ff;letter-spacing:0.08em;text-transform:uppercase;font-family:'Space Grotesk',sans-serif;}" +
      ".vmcloud-comp-controls{display:flex;flex-direction:column;gap:8px;}" +
      ".vmcloud-comp-mic-zone{display:flex;align-items:center;gap:8px;justify-content:flex-start;}" +
      ".vmcloud-comp-mic{flex:0 0 auto;width:44px;height:44px;border-radius:50%;border:1px solid rgba(0,229,255,0.45);background:radial-gradient(circle at 35% 30%,rgba(0,229,255,0.22),rgba(0,10,18,0.9) 70%);color:#7ff0ff;display:flex;align-items:center;justify-content:center;box-shadow:0 0 12px rgba(0,212,255,0.3);cursor:pointer;transition:all 0.2s;padding:0;}" +
      ".vmcloud-comp-mic:hover{box-shadow:0 0 18px rgba(0,212,255,0.55);}" +
      ".vmcloud-comp-mic.listening{color:#fff;border-color:#ff5e7a;background:radial-gradient(circle at 35% 30%,rgba(255,94,122,0.3),rgba(30,0,10,0.9) 70%);box-shadow:0 0 18px rgba(255,94,122,0.6);}" +
      ".vmcloud-comp-vision{display:flex;flex-direction:column;gap:6px;}" +
      ".vmcloud-comp-vision-status{font-size:11px;color:#64748b;line-height:1.4;}" +
      ".vmcloud-comp-log{background:#0b1220;border:1px solid rgba(255,255,255,0.05);border-radius:10px;padding:10px;min-height:72px;max-height:170px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;}" +
      ".vmcloud-comp-msg{max-width:92%;border-radius:10px;padding:8px 11px;font-size:13px;line-height:1.45;}" +
      ".vmcloud-comp-msg.user{align-self:flex-end;background:rgba(0,229,255,0.14);border:1px solid rgba(0,229,255,0.28);color:#e8fbff;}" +
      ".vmcloud-comp-msg.cloud{align-self:flex-start;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);color:#e2e8f0;}" +
      ".vmcloud-comp-empty{color:#475569;font-size:12px;text-align:center;padding:6px;}" +
      ".vmcloud-comp-input-row{display:flex;gap:8px;}" +
      ".vmcloud-comp-input-row input{flex:1;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);color:#e2e8f0;border-radius:10px;padding:9px 12px;font-size:13px;min-width:0;}" +
      ".vmcloud-comp-input-row input:focus{outline:none;border-color:rgba(0,229,255,0.5);}" +
      ".vmcloud-companion-mini{display:none;flex-direction:row;align-items:center;gap:10px;cursor:pointer;padding:10px 12px;border-radius:999px;background:rgba(7,13,24,0.92);border:1px solid rgba(0,212,255,0.25);box-shadow:0 10px 30px rgba(0,0,0,0.45);backdrop-filter:blur(8px);}" +
      ".vmcloud-companion.minimized .vmcloud-companion-panel{display:none;}" +
      ".vmcloud-companion.minimized .vmcloud-companion-mini{display:flex;}" +
      ".vmcloud-companion.hidden{display:none !important;}" +
      ".vmcloud-companion-mini-orb{position:relative;flex:0 0 auto;width:44px;height:40px;border-radius:55% 45% 50% 50%;background:radial-gradient(circle at 35% 30%,rgba(255,255,255,0.95),var(--cloud-color,#00d4ff) 48%,rgba(0,80,110,0.5) 78%);box-shadow:0 0 16px var(--cloud-color,#00d4ff);}" +
      ".vmcloud-mini-dot{position:absolute;right:-2px;bottom:-2px;width:12px;height:12px;border-radius:50%;background:#334155;border:2px solid #0b1220;}" +
      ".vmcloud-companion.sharing .vmcloud-companion-mini-orb{animation:vmcloud-share-pulse 1.6s ease-in-out infinite;}" +
      ".vmcloud-companion.sharing .vmcloud-mini-dot{background:#f8485e;box-shadow:0 0 8px #f8485e;}" +
      "@keyframes vmcloud-share-pulse{0%,100%{box-shadow:0 0 12px var(--cloud-color,#00d4ff);}50%{box-shadow:0 0 26px var(--cloud-color,#00d4ff),0 0 34px rgba(248,72,94,0.5);}}" +
      ".vmcloud-companion-mini-name{color:#e2e8f0;font-size:13px;font-weight:600;font-family:'Space Grotesk',sans-serif;}" +
      "@media(max-width:640px){.vmcloud-header{flex-direction:column;text-align:center;}.vmcloud-stage{height:clamp(240px,48vw,340px);}.vmcloud-voice{margin-top:6px;}.vmcloud-pref-row{flex-direction:column;align-items:stretch;}.vmcloud-pref-row label{flex:none;}}";
    // Responsive companion placement: bottom-right, clear of safe areas and
    // mobile nav, no hardcoded off-canvas offsets.
    st.textContent +=
      "@media(max-width:900px){" +
        "#vmCloudCompanion{right:12px;bottom:calc(12px + env(safe-area-inset-bottom,0px));}" +
        ".vmcloud-companion-panel{width:288px;max-height:min(68vh,600px);padding:12px;}" +
      "}";
    (document.head || document.documentElement).appendChild(st);
  }

  window.VMCloud = {
    getState: function () { return JSON.parse(JSON.stringify(CLOUD.state)); },
    getPrefs: function () { return JSON.parse(JSON.stringify(CLOUD.prefs)); },
    pick: function (kind, value) {
      if (kind === "emotion") applyRuntimePatch({ emotion: value });
      else if (kind === "status") applyRuntimePatch({ status: value });
      else if (kind === "presentation") applyRuntimePatch({ presentation: value });
      else if (kind === "accent") applyRuntimePatch({ accent: value });
    },
    setEmotion: function (v) { if (EMOTIONS.indexOf(v) !== -1) applyRuntimePatch({ emotion: v }); },
    setStatus: function (v) { if (INTERACTION_STATES.indexOf(v) !== -1) applyRuntimePatch({ status: v }); },
    setState: applyRuntimePatch,
    renderConfig: renderConfig,
    syncStateFromServer: syncStateFromServer,
    persistState: persistState,
    savePrefs: savePrefs,
    loadPrefs: loadPrefs,
    updateIntensityUp: function (v) {
      CLOUD.state.intensity = clamp01(v);
      var lbl = $id("vmCloudIntensityVal");
      if (lbl) lbl.textContent = Math.round(CLOUD.state.intensity * 100) + "%";
      reflectState();
    },
    send: send,
    sendFromInput: sendFromInput,
    render: render,
    toggleVoice: toggleVoice,
    stopVoice: stopVoice,
    interruptCloud: interruptCloud,
    pickCloudEmotion: pickCloudEmotion,
    toggleVision: toggleVision,
    minimizeCompanion: minimizeCompanion,
    restoreCompanion: restoreCompanion,
    companionSend: companionSend,
    showCompanion: function () {
      if (!isAuthActive()) return;
      CLOUD.companionActive = true;
      surfaceCompanion(CLOUD.prefs.companion_minimized ? "mini" : "panel");
    },
    onShow: onShow
  };
  window.vmCloudOnShow = onShow;
  window.vmCloudOnHide = onHide;
  window.vmCloudCompanionShow = companionShow;
  window.vmCloudCompanionCleanup = cleanupCompanion;
})();