(function () {
  "use strict";

  var END_SILENCE_MS = 1500;
  var VALID_KEYS = ["marcus", "elena", "angelina"];

  var hooks = {
    onListeningChange: null,
    onResult: null,
    onSpeakStart: null,
    onSpeakEnd: null,
    onError: null
  };

  var rec = null;
  var active = false;
  var interim = "";
  var endedText = "";
  var silenceTimer = null;

  var player = null;
  var speaking = false;
  var speechSeq = 0;

  function $id(s) { return document.getElementById(s); }

  function supported() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  function getVoiceKey(prefs, presentation) {
    var vp = String((prefs && prefs.voice_preference) || "").trim().toLowerCase();
    var p = String(presentation || "neutral").toLowerCase();
    if (VALID_KEYS.indexOf(vp) !== -1) return vp;
    if (vp.indexOf("bright") !== -1 || vp.indexOf("cheer") !== -1 ||
        vp.indexOf("upbeat") !== -1 || vp.indexOf("crisp") !== -1 ||
        vp.indexOf("clear") !== -1) return "elena";
    if (vp.indexOf("deep") !== -1 || vp.indexOf("warm") !== -1 ||
        vp.indexOf("soft") !== -1 || vp.indexOf("calm") !== -1 ||
        vp.indexOf("low") !== -1 || vp.indexOf("dark") !== -1) return "marcus";
    if (vp.indexOf("melodic") !== -1 || vp.indexOf("musical") !== -1 ||
        vp.indexOf("smooth") !== -1 || vp.indexOf("rich") !== -1 ||
        vp.indexOf("husky") !== -1) return "angelina";
    if (p === "feminine") return "elena";
    if (p === "masculine") return "marcus";
    return "marcus";
  }

  function getVoiceLabel(key) {
    var k = VALID_KEYS.indexOf(String(key || "").toLowerCase()) !== -1 ? String(key).toLowerCase() : "";
    if (k === "elena") return "Elena";
    if (k === "angelina") return "Angelina";
    return "Marcus";
  }

  function getPlayer() {
    if (player) return player;
    player = $id("vmCloudVoicePlayer");
    if (!player) {
      player = document.createElement("audio");
      player.id = "vmCloudVoicePlayer";
      player.preload = "auto";
      player.style.display = "none";
      (document.body || document.documentElement).appendChild(player);
    }
    return player;
  }

  var unlocked = false;

  // ── Amplitude tap ────────────────────────────────────────────────────────
  // Mouth sync needs the real amplitude of the audio the Cloud is speaking. We
  // analyse the ONE existing player element through an AnalyserNode instead of
  // adding a second audio element, a second <audio>, or a second /api/tts call.
  //
  // Hard safety rule: createMediaElementSource() silences a cross-origin media
  // element that is not CORS-enabled. TTS audio can come from an external
  // provider URL, so the tap is only ever attached to a same-origin source.
  // Anything else simply leaves the mouth on its synthetic pulse.
  var levelCtx = null;
  var levelAnalyser = null;
  var levelSource = null;
  var levelData = null;
  var levelRaf = 0;
  var levelListener = null;
  var levelBlocked = false;

  function sameOriginAudio(el) {
    try {
      var src = el.currentSrc || el.src;
      if (!src || src.indexOf("data:") === 0) return true;
      return new URL(src, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  function ensureLevelTap(el) {
    if (levelAnalyser || levelBlocked) return levelAnalyser;
    if (!el || !sameOriginAudio(el)) { levelBlocked = true; return null; }
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) { levelBlocked = true; return null; }
    try {
      levelCtx = new Ctx();
      levelSource = levelCtx.createMediaElementSource(el);
      levelAnalyser = levelCtx.createAnalyser();
      levelAnalyser.fftSize = 512;
      levelAnalyser.smoothingTimeConstant = 0.6;
      // analyser must terminate at the destination or playback goes silent.
      levelSource.connect(levelAnalyser);
      levelAnalyser.connect(levelCtx.destination);
      levelData = new Uint8Array(levelAnalyser.fftSize);
    } catch (e) {
      levelBlocked = true;
      levelAnalyser = null;
      return null;
    }
    return levelAnalyser;
  }

  function readLevel() {
    if (!levelAnalyser || !levelData) return 0;
    try {
      levelAnalyser.getByteTimeDomainData(levelData);
    } catch (e) {
      return 0;
    }
    var sum = 0;
    for (var i = 0; i < levelData.length; i++) {
      var v = (levelData[i] - 128) / 128;
      sum += v * v;
    }
    var rms = Math.sqrt(sum / levelData.length);
    // TTS speech RMS sits low; expand it and keep a little floor so the mouth
    // still articulates on quiet providers.
    var lvl = Math.max(0, Math.min(1, (rms - 0.004) * 7));
    return lvl < 0.06 ? 0 : Math.min(1, lvl);
  }

  function startLevelLoop() {
    if (levelRaf) return;
    var tick = function () {
      levelRaf = requestAnimationFrame(tick);
      if (!levelListener) return;
      try { levelListener(readLevel()); } catch (e) {}
    };
    if (typeof requestAnimationFrame !== "function") return;
    levelRaf = requestAnimationFrame(tick);
  }

  function stopLevelLoop() {
    if (levelRaf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(levelRaf);
    levelRaf = 0;
  }

  function setLevelListener(fn) {
    levelListener = typeof fn === "function" ? fn : null;
    return !!levelListener;
  }

  function resumeLevelCtx() {
    if (levelCtx && levelCtx.state === "suspended" && levelCtx.resume) {
      try { levelCtx.resume().catch(function () {}); } catch (e) {}
    }
  }

  function unlock() {
    var el = getPlayer();
    if (!el || unlocked) return;
    try {
      el.src = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
      var pr = el.play();
      if (pr && pr.then) pr.then(function () { try { el.pause(); el.currentTime = 0; } catch (e) {} }).catch(function () {});
    } catch (e) {}
    unlocked = true;
  }

  ["pointerdown", "touchstart", "keydown", "click"].forEach(function (evt) {
    document.addEventListener(evt, unlock, { passive: true, capture: true });
  });

  function setHooks(o) {
    if (!o) return;
    hooks.onListeningChange = o.onListeningChange || hooks.onListeningChange;
    hooks.onResult = o.onResult || hooks.onResult;
    hooks.onSpeakStart = o.onSpeakStart || hooks.onSpeakStart;
    hooks.onSpeakEnd = o.onSpeakEnd || hooks.onSpeakEnd;
    hooks.onError = o.onError || hooks.onError;
  }

  function fire(name, arg) {
    if (typeof hooks[name] === "function") { try { hooks[name](arg); } catch (e) {} }
  }

  function startListening() {
    if (active) return;
    if (!supported()) { fire("onError", "unsupported"); return; }
    unlock();
    if (speaking) stopSpeech(true);
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    try { rec = new SR(); } catch (e) { fire("onError", "unsupported"); return; }
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.continuous = true;
    endedText = "";
    interim = "";
    rec.onresult = function (ev) {
      var live = "";
      for (var i = ev.resultIndex; i < ev.results.length; i++) {
        if (ev.results[i].isFinal) endedText += ev.results[i][0].transcript;
        else live += ev.results[i][0].transcript;
      }
      interim = live;
      fire("onResult", (endedText + live).trim(), false);
      clearTimer();
      silenceTimer = setTimeout(endOfSpeech, END_SILENCE_MS);
    };
    rec.onerror = function (ev) {
      if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
        stopListening();
        fire("onError", "microphone blocked");
      }
    };
    rec.onend = function () {
      if (active) { try { rec.start(); } catch (e) {} }
    };
    try { rec.start(); } catch (e) {}
    active = true;
    fire("onListeningChange", true);
  }

  function stopListening() {
    active = false;
    clearTimer();
    if (rec) {
      try { rec.onend = null; rec.stop(); } catch (e) {}
      rec = null;
    }
    fire("onListeningChange", false);
  }

  function endOfSpeech() {
    var text = (endedText + interim).trim();
    stopListening();
    clearTimer();
    endedText = "";
    interim = "";
    if (text.length >= 2) fire("onResult", text, true);
  }

  function takeTranscript() {
    var t = (endedText + interim).trim();
    endedText = "";
    interim = "";
    return t;
  }

  function clearTimer() {
    if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
  }

  function stopSpeech(quiet) {
    var seq = ++speechSeq;
    var wasSpeaking = speaking;
    speaking = false;
    stopLevelLoop();
    var el = getPlayer();
    try {
      el.onplaying = null;
      el.onended = null;
      el.onerror = null;
      el.pause();
      el.currentTime = 0;
    } catch (e) {}
    if (wasSpeaking && !quiet) fire("onSpeakEnd", "interrupted");
    void seq;
  }

  function interrupt() {
    stopSpeech(true);
    if (active) stopListening();
  }

  function stop() {
    stopSpeech(true);
    stopListening();
    clearTimer();
  }

  function speak(text, opts) {
    var o = opts || {};
    stopSpeech(true);
    clearTimer();
    var clean = String(text || "").trim();
    if (!clean) return;
    unlock();
    var key = getVoiceKey(o.prefs || null, o.presentation || "neutral");
    var seq = ++speechSeq;
    apiFetch("/api/tts", {
      method: "POST",
      credentials: "include",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ text: clean.slice(0, 1500), persona: key }),
      timeoutMs: 60000
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (seq !== speechSeq) return;
      if (data && data.engine === "qwen_tts" && data.url) {
        var el = getPlayer();
        try {
          el.onplaying = function () {
            if (seq !== speechSeq) return;
            speaking = true;
            resumeLevelCtx();
            startLevelLoop();
            fire("onSpeakStart");
          };
          el.onended = function () {
            if (seq !== speechSeq) return;
            speaking = false;
            stopLevelLoop();
            fire("onSpeakEnd", "");
          };
          el.onerror = function () {
            if (seq !== speechSeq) return;
            speaking = false;
            stopLevelLoop();
            fire("onSpeakEnd", "tts failed");
          };
          el.src = data.url;
          ensureLevelTap(el);
          var pr = el.play();
          if (pr && pr.catch) pr.catch(function () {
            if (seq !== speechSeq) return;
            speaking = false;
            stopLevelLoop();
            fire("onSpeakEnd", "tts blocked");
          });
        } catch (e) {
          speaking = false;
          stopLevelLoop();
          fire("onSpeakEnd", "tts failed");
        }
      } else {
        fire("onSpeakEnd", "tts unavailable");
      }
    }).catch(function () {
      if (seq !== speechSeq) return;
      fire("onSpeakEnd", "tts unavailable");
    });
  }

  window.VMCloudVoice = {
    supported: supported,
    start: startListening,
    startListening: startListening,
    stopListening: stopListening,
    interrupt: interrupt,
    stop: stop,
    speak: speak,
    getVoiceKey: getVoiceKey,
    getVoiceLabel: getVoiceLabel,
    validKeys: VALID_KEYS.slice(),
    setHooks: setHooks,
    takeTranscript: takeTranscript,
    isListening: function () { return active; },
    isSpeaking: function () { return speaking; },
    getPlayer: getPlayer,
    // Real TTS amplitude for mouth sync (same player element, no extra request).
    setLevelListener: setLevelListener,
    readLevel: readLevel,
    hasLevelTap: function () { return !!levelAnalyser; }
  };
})();